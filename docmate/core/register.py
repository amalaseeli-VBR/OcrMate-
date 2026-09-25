from __future__ import annotations

import json
import math

def _sync_from_legacy() -> None:
    from docmate import legacy as L
    g = globals()
    for k in dir(L):
        if k.startswith("__"):
            continue
        # Keep local implementations (e.g., _upsert_register) to avoid wrapper recursion.
        if k in g:
            continue
        g[k] = getattr(L, k)


def _json_safe_model(value):
    if isinstance(value, dict):
        return {str(k): _json_safe_model(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe_model(v) for v in value]
    if isinstance(value, tuple):
        return [_json_safe_model(v) for v in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _json_model_text(value) -> str:
    """Serialise extracted model data for flexible template-specific fields."""
    try:
        return json.dumps(_json_safe_model(value or {}), ensure_ascii=False, default=str, sort_keys=True, allow_nan=False)
    except Exception:
        try:
            return json.dumps({"value": str(value)}, ensure_ascii=False, sort_keys=True, allow_nan=False)
        except Exception:
            return "{}"


def _ensure_register_json_columns() -> None:
    for sql in (
        "ALTER TABLE purchase_invoices ADD COLUMN header_json TEXT",
        "ALTER TABLE purchase_invoice_items ADD COLUMN line_json TEXT",
    ):
        try:
            _db_exec(sql)
        except Exception:
            pass



def _upsert_register(inv_key: str, header: Dict[str, Any], items: List[Dict[str, Any]], doc_type: str, engine_used: str, file_name: str) -> str:
    """Upsert into the Purchase register with de-duplication.

    Problem observed in testing:
    - Same invoice saved multiple times can create duplicates because inv_key includes a file-hash/engine variance.

    Fix:
    - Use a *natural key* (doc_type + supplier + invoice_number + invoice_date) to detect existing entries.
    - If a match exists, reuse the existing inv_key and update that entry (re-save).
    - If multiple duplicates exist for the same natural key, keep the most-recent and remove the rest.
    """
    _sync_from_legacy()
    _ensure_register_json_columns()
    items = _ensure_line_numbers(items)
    metrics = _calc_metrics(header, items)
    now = _uk_now_iso()
    processed_at = now

    supplier = str(header.get("supplierName") or "").strip()
    customer = str(header.get("customerName") or "").strip()
    inv_no = str(header.get("invoiceNumber") or "").strip()
    inv_dt = (_normalise_invoice_date_str(header.get("invoiceDate") or "") or str(header.get("invoiceDate") or "").strip())

    # Normalise for matching
    supplier_norm = re.sub(r"\s+", " ", supplier).strip().upper()
    inv_no_norm = re.sub(r"\s+", "", inv_no).strip().upper()
    inv_dt_norm = re.sub(r"\s+", "", inv_dt).strip()

    # 1) Detect existing rows by natural key (prevents duplicates across engines/files)
    match_rows: List[Dict[str, Any]] = []
    try:
        if supplier_norm and inv_no_norm and inv_dt_norm:
            rows = _db_fetchall(
                """SELECT inv_key, processed_at, updated_at
                   FROM purchase_invoices
                   WHERE doc_type=?
                     AND UPPER(TRIM(supplier_name))=?
                     AND UPPER(REPLACE(TRIM(invoice_number),' ',''))=?
                     AND TRIM(invoice_date)=?
                   ORDER BY COALESCE(updated_at, processed_at) DESC""",
                (doc_type, supplier_norm, inv_no_norm, inv_dt_norm),
            )
            match_rows = [dict(r) for r in (rows or [])]
        elif supplier_norm and inv_no_norm:
            rows = _db_fetchall(
                """SELECT inv_key, processed_at, updated_at
                   FROM purchase_invoices
                   WHERE doc_type=?
                     AND UPPER(TRIM(supplier_name))=?
                     AND UPPER(REPLACE(TRIM(invoice_number),' ',''))=?
                   ORDER BY COALESCE(updated_at, processed_at) DESC""",
                (doc_type, supplier_norm, inv_no_norm),
            )
            match_rows = [dict(r) for r in (rows or [])]
    except Exception as e:
        _log_event(f"WARNING register natural-key lookup failed: {e}", file_name=file_name)

    # Determine target inv_key to write to
    target_inv_key = inv_key
    mode = "created"

    if match_rows:
        # Reuse latest existing key
        target_inv_key = str(match_rows[0].get("inv_key") or inv_key)
        mode = "updated"
        if target_inv_key != inv_key:
            _log_event(
                f"Register de-dup: matched existing invoice (supplier={supplier_norm} inv={inv_no_norm} date={inv_dt_norm}); reusing inv_key={target_inv_key} (discarding new inv_key={inv_key})",
                file_name=file_name,
            )

        # If multiple duplicates exist, remove the extras
        dup_keys = [str(r.get("inv_key")) for r in match_rows[1:] if r.get("inv_key")]
        if dup_keys:
            try:
                for k in dup_keys:
                    _db_exec("DELETE FROM purchase_invoice_items WHERE inv_key=?", (k,))
                    _db_exec("DELETE FROM purchase_invoices WHERE inv_key=?", (k,))
                _log_event(f"Register de-dup cleanup: removed duplicate inv_key(s)={', '.join(dup_keys)}", file_name=file_name)
            except Exception as e:
                _log_event(f"WARNING register de-dup cleanup failed: {e}", file_name=file_name)

    else:
        # Fallback: inv_key exact match
        existed = _db_fetchone("SELECT 1 FROM purchase_invoices WHERE inv_key=?", (inv_key,))
        mode = "updated" if existed else "created"
        target_inv_key = inv_key

    # 2) Upsert header row
    header_json = _json_model_text(header)

    _db_exec(
        """INSERT OR REPLACE INTO purchase_invoices(
            inv_key, doc_type, supplier_name, customer_name, invoice_date, invoice_number,
            total_lines, total_case_qty, total_units, net_amount, vat_amount, gross_amount,
            engine_used, file_name, processed_at, updated_at,
            cms_customer_id, spos_customer_id, spos_site_id, header_json
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            target_inv_key,
            doc_type,
            supplier,
            customer,
            inv_dt,
            inv_no,
            int(metrics["total_lines"]),
            float(metrics["total_case_qty"]),
            float(metrics["total_units"]),
            float(metrics["net_header"]),
            float(metrics["vat_header"]),
            float(metrics["gross_header"]),
            engine_used,
            file_name,
            processed_at,
            now,
            str((st.session_state.get("selected_customer_profile") or {}).get("cms_customer_id") or ""),
            str((st.session_state.get("selected_customer_profile") or {}).get("spos_customer_id") or ""),
            str((st.session_state.get("selected_customer_profile") or {}).get("spos_site_id") or ""),
            header_json,
        ),
    )

    
    # 2b) Update local invoice_register_index (v1.07)
    try:
        cp = st.session_state.get("selected_customer_profile") or {}
        _db_exec(
            """INSERT OR REPLACE INTO invoice_register_index(
                inv_key, doc_type, cms_customer_id, spos_customer_id, spos_site_id,
                supplier_name, invoice_number, invoice_date, engine_used, source_file_name,
                created_at, updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                target_inv_key,
                doc_type,
                str(cp.get("cms_customer_id") or ""),
                str(cp.get("spos_customer_id") or ""),
                str(cp.get("spos_site_id") or ""),
                supplier,
                inv_no,
                inv_dt,
                engine_used,
                file_name,
                processed_at,
                now,
            ),
        )
    except Exception:
        pass

# 3) Replace items for this invoice
    _db_exec("DELETE FROM purchase_invoice_items WHERE inv_key=?", (target_inv_key,))

    for it in items:
        line_no = int(_safe_float(it.get("lineNo")) or 0) or 0
        if line_no <= 0:
            line_no = items.index(it) + 1
        case_pack_text = str(it.get("casePack") or "").strip()
        case_pack_num = _safe_float(case_pack_text)

        _db_exec(
            """INSERT OR REPLACE INTO purchase_invoice_items(
                inv_key, line_no, code, description, case_pack, case_pack_text, unit_size,
                qty_cases, unit_price, line_net, vat_code, vat_rate, vat_amount, line_gross,
                std_rrp, por, is_void, void_note, line_json
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                target_inv_key,
                line_no,
                str(it.get("code") or "").strip(),
                str(it.get("description") or "").strip(),
                float(case_pack_num),
                case_pack_text,
                str(it.get("unitSize") or "").strip(),
                float(_safe_float(it.get("qty"))),
                float(_safe_float(it.get("unitPrice"))),
                float(_safe_float(it.get("lineNet"))),
                str(it.get("vatCode") or "").strip(),
                float(_safe_float(it.get("vatRate"))),
                float(_safe_float(it.get("vatAmount"))),
                float(_safe_float(it.get("lineGross"))),
                float(_safe_float(it.get("stdRrp"))),
                float(_safe_float(it.get("por"))),
                int(_safe_float(it.get("isVoid")) or 0),
                str(it.get("voidNote") or "").strip(),
                _json_model_text(it),
            ),
        )

    # Record action in EventLOG for transparency
    if mode == "updated":
        _log_event(f"Register update: Entry already exists — updated & re-saved (No duplicate entry enforced). register={REGISTER_LABEL.get(doc_type, 'Register')} inv_key={target_inv_key}", file_name=file_name)
    else:
        _log_event(f"Register insert: New entry created. register={REGISTER_LABEL.get(doc_type, 'Register')} inv_key={target_inv_key}", file_name=file_name)

    return mode
