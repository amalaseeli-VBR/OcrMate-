from __future__ import annotations
from typing import Any, Dict

from docmate.core.logging_utils import _log_event

# NOTE: extracted from legacy.py

def _ensure_data_dir() -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    # Keep only today's EventLOG (daily rotation)
    try:
        today_name = os.path.basename(EVENT_LOG_FILE)
        for fn in os.listdir(DATA_DIR):
            if fn == today_name:
                continue
            if fn == 'EventLOG.txt' or (fn.startswith('EventLOG_') and fn.endswith('.txt')):
                try:
                    os.remove(os.path.join(DATA_DIR, fn))
                except Exception:
                    pass
    except Exception:
        pass
def _invoice_export_file_names(inv_key: str, payload: Dict[str, Any]) -> Dict[str, str]:
    """Return consistent filenames for export artifacts."""
    cust = payload.get("customer") or {}
    invh = payload.get("invoiceHeader") or {}

    cms = _safe_filename(str(cust.get("CMS_CustomerID") or "CMSNA"))
    spos = _safe_filename(str(cust.get("SPOS_CustomerID") or "N/A"))
    inv_no = _safe_filename(str(invh.get("invoice_number") or invh.get("invoiceNumber") or inv_key))
    inv_dt = _safe_filename(str(invh.get("invoice_date") or invh.get("invoiceDate") or ""))

    stem = f"{cms}_{spos}_{inv_no}" + (f"_{inv_dt}" if inv_dt else "")
    return {
        "single_json": f"{stem}_DocMate_Invoice.json",
        "dedicated_json": f"{stem}_VBR_InvoiceExport.json",
        "lines_csv": f"{stem}_LineItems.csv",
        "full_csv": f"{stem}_InvoiceExport_Full.csv",
    }
def _invoice_export_full_csv_bytes(payload: Dict[str, Any]) -> bytes:
    """Build the dedicated FULL export CSV (customer + header + VAT/metrics + line items).
    One row per line item; customer + header repeated for SQL import into VBR local/SPOS Cloud DBs.
    """
    cust = payload.get("customer") or {}
    invh = payload.get("invoiceHeader") or {}
    lines = payload.get("lineItems") or []

    # Header totals (as stored)
    def _f(x: Any) -> float:
        try:
            return float(x)
        except Exception:
            return 0.0

    header_net = _f(invh.get("net_amount") or invh.get("totalNet") or invh.get("net") or 0.0)
    header_vat = _f(invh.get("vat_amount") or invh.get("totalVat") or invh.get("vat") or 0.0)
    header_gross = _f(invh.get("gross_amount") or invh.get("totalGross") or invh.get("gross") or 0.0)

    # Sums from lines
    line_sum_net = round(sum(_f(x.get("line_net")) for x in (lines or [])), 2)
    line_sum_vat = round(sum(_f(x.get("vat_amount")) for x in (lines or [])), 2)
    line_sum_gross = round(sum(_f(x.get("line_gross")) for x in (lines or [])), 2)

    # VAT breakdown
    vat_rows = payload.get("vatBreakdown")
    if isinstance(vat_rows, str):
        try:
            vat_rows = json.loads(vat_rows)
        except Exception:
            vat_rows = None
    if not isinstance(vat_rows, list):
        vat_rows = _compute_vat_breakdown_from_lines(lines)

    vat_sum_net = round(sum(_f(v.get("net")) for v in vat_rows), 2)
    vat_sum_vat = round(sum(_f(v.get("vat")) for v in vat_rows), 2)
    vat_sum_gross = round(sum(_f(v.get("gross")) for v in vat_rows), 2)

    totals_match_flag = "Y" if (round(header_net,2)==vat_sum_net and round(header_vat,2)==vat_sum_vat and round(header_gross,2)==vat_sum_gross) else "N"
    lines_match_flag = "Y" if (round(header_net,2)==line_sum_net and round(header_vat,2)==line_sum_vat and round(header_gross,2)==line_sum_gross) else "N"

    # Metrics
    count_total_case_qty = _f(invh.get("total_case_qty") or invh.get("totalCaseQty") or 0.0)
    count_total_units = _f(invh.get("total_units") or invh.get("totalUnits") or 0.0)
    count_products = int(_f(invh.get("total_lines") or invh.get("totalLines") or len(lines)))

    avg_por = 0.0
    try:
        pors = [float(x.get("por") or 0.0) for x in lines]
        pors = [p for p in pors if p > 0]
        avg_por = round(sum(pors)/len(pors), 2) if pors else 0.0
    except Exception:
        avg_por = 0.0

    vat_breakdown_json = json.dumps(vat_rows, ensure_ascii=False)

    # Build dataframe rows
    rows: List[Dict[str, Any]] = []
    base = {
        # A) Customer identity (must be first)
        "CMS_CustomerID": cust.get("CMS_CustomerID") or "",
        "CMS_CustomerName": cust.get("Customer_Name") or "",
        "SPOS_CustomerID": cust.get("SPOS_CustomerID") or "N/A",
        "Customer_Name": cust.get("Customer_Name") or "",
        "Trading_Name": cust.get("Trading_Name") or "",
        "Company_Name": cust.get("Company_Name") or "",
        "VAT_Registration_No": cust.get("VAT_Registration_No") or "",
        "Contact_No": cust.get("Contact_No") or "",
        "Email_Address": cust.get("Email_Address") or "",

        # B) Invoice header
        "inv_key": invh.get("inv_key") or payload.get("inv_key") or "",
        "doc_type": "Purchase Invoice",
        "source_file_name": invh.get("file_name") or payload.get("source_file_name") or "",
        "engine_used": invh.get("engine_used") or payload.get("engine_used") or "",
        "template_used": payload.get("template_used") or "",
        "supplier_name": invh.get("supplier_name") or "",
        "supplier_code": invh.get("supplier_code") or "",
        "invoice_number": invh.get("invoice_number") or "",
        "invoice_date": invh.get("invoice_date") or "",
        "currency": invh.get("currency") or "GBP",
        "header_total_net": round(header_net, 2),
        "header_total_vat": round(header_vat, 2),
        "header_total_gross": round(header_gross, 2),

        # C) VAT breakdown + checks
        "vat_breakdown_json": vat_breakdown_json,
        "vat_sum_net": vat_sum_net,
        "vat_sum_vat": vat_sum_vat,
        "vat_sum_gross": vat_sum_gross,
        "totals_match_flag": totals_match_flag,
        "line_sum_net": line_sum_net,
        "line_sum_vat": line_sum_vat,
        "line_sum_gross": line_sum_gross,
        "lines_match_flag": lines_match_flag,

        # D) Metrics
        "count_total_case_qty": round(count_total_case_qty, 2),
        "count_total_units": round(count_total_units, 2),
        "count_products": count_products,
        "avg_por_pct": avg_por,
    }

    for it in (lines or []):
        case_pack = _f(it.get("case_pack") or 0.0)
        qty_cases = _f(it.get("qty_cases") or 0.0)
        total_units = round(case_pack * qty_cases, 2) if case_pack and qty_cases else ""
        row = dict(base)
        row.update({
            "line_no": it.get("line_no"),
            "code": it.get("code") or "",
            "barcode": it.get("barcode") or "",
            "description": it.get("description") or "",
            "case_pack": it.get("case_pack"),
            "case_pack_text": it.get("case_pack_text") or "",
            "unit_size": it.get("unit_size") or "",
            "qty_cases": it.get("qty_cases"),
            "total_units": total_units,
            "unit_price": it.get("unit_price"),
            "line_net": it.get("line_net"),
            "vat_code": it.get("vat_code") or "",
            "vat_rate": it.get("vat_rate"),
            "vat_amount": it.get("vat_amount"),
            "line_gross": it.get("line_gross"),
            "std_rrp": it.get("std_rrp"),
            "por": it.get("por"),
            "is_void": it.get("is_void"),
            "void_note": it.get("void_note") or "",
        })
        rows.append(row)

    if not rows:
        rows = [base]

    df = pd.DataFrame(rows)
    # Ensure stable column order (customer first, then header, then VAT/metrics, then lines)
    desired = [
        "CMS_CustomerID","CMS_CustomerName","SPOS_CustomerID","Customer_Name","Trading_Name","Company_Name","VAT_Registration_No","Contact_No","Email_Address",
        "inv_key","doc_type","source_file_name","engine_used","template_used","supplier_name","supplier_code","invoice_number","invoice_date","currency","header_total_net","header_total_vat","header_total_gross",
        "vat_breakdown_json","vat_sum_net","vat_sum_vat","vat_sum_gross","totals_match_flag","line_sum_net","line_sum_vat","line_sum_gross","lines_match_flag",
        "count_total_case_qty","count_total_units","count_products","avg_por_pct",
        "line_no","code","barcode","description","case_pack","case_pack_text","unit_size","qty_cases","total_units","unit_price","line_net","vat_code","vat_rate","vat_amount","line_gross","std_rrp","por","is_void","void_note"
    ]
    cols = [c for c in desired if c in df.columns] + [c for c in df.columns if c not in desired]
    df = df[cols]
    return df.to_csv(index=False).encode("utf-8")
def _build_dedicated_invoice_export(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Dedicated export format (customer first, then invoice header, then line items).
    Designed to be stable for VBR reconciliation/import pipelines.
    """
    customer = payload.get("customer") or {}
    invh = payload.get("invoiceHeader") or {}
    items = payload.get("lineItems") or []
    vat = payload.get("vatBreakdown") or invh.get("vat_breakdown") or invh.get("vatBreakdown") or None

    dedicated: Dict[str, Any] = {
        "Customer": {
            "CMS_CustomerID": customer.get("CMS_CustomerID", ""),
            "CMS_CustomerName": customer.get("Customer_Name", ""),
            "SPOS_CustomerID": customer.get("SPOS_CustomerID", "N/A") or "N/A",
            "Trading_Name": customer.get("Trading_Name", ""),
            "Company_Name": customer.get("Company_Name", ""),
            "VAT_Registration_No": customer.get("VAT_Registration_No", ""),
            "Contact_No": customer.get("Contact_No", ""),
            "Email_Address": customer.get("Email_Address", ""),
        },
        "Invoice": invh,
        "LineItems": items,
    }
    if vat is not None:
        dedicated["VAT_Breakdown"] = vat
    return dedicated
def _write_invoice_exports(inv_key: str, payload: Dict[str, Any]) -> Dict[str, str]:
    """Write per-invoice export files to DocsEXPORT/JSON and return paths."""
    _ensure_data_dir()
    out_dir = os.path.join(DATA_DIR, "DocsEXPORT", "JSON")
    os.makedirs(out_dir, exist_ok=True)

    names = _invoice_export_file_names(inv_key, payload)

    p_single = os.path.join(out_dir, names["single_json"])
    p_ded = os.path.join(out_dir, names["dedicated_json"])

    try:
        with open(p_single, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
    except Exception as e:
        _log_event(f"ERROR writing single invoice JSON: {e}", inv_key)

    try:
        dedicated = _build_dedicated_invoice_export(payload)
        with open(p_ded, "w", encoding="utf-8") as f:
            json.dump(dedicated, f, indent=2, ensure_ascii=False)
    except Exception as e:
        _log_event(f"ERROR writing dedicated invoice JSON: {e}", inv_key)

    paths = {
        "single_json": p_single,
        "dedicated_json": p_ded,
    }


    # CSV exports
    try:
        if pd is not None:
            # Compact line-items-only CSV (kept for quick checking)
            df_lines = pd.DataFrame(payload.get("lineItems") or [])
            p_lines = os.path.join(out_dir, names["lines_csv"])
            df_lines.to_csv(p_lines, index=False)
            paths["lines_csv"] = p_lines
            _log_event(f"Export file written: {os.path.basename(p_lines)}", inv_key)

            # FULL Invoice Export CSV (customer + header + VAT/metrics + lines) for VBR SQL import
            p_full = os.path.join(out_dir, names["full_csv"])
            with open(p_full, "wb") as f:
                f.write(_invoice_export_full_csv_bytes(payload))
            paths["full_csv"] = p_full
            _log_event(f"Export file written: {os.path.basename(p_full)}", inv_key)
    except Exception as e:
        _log_event(f"ERROR writing CSV exports: {e}", inv_key)

    _log_event("Export write-to-disk completed (single_json, dedicated_json, csv).", inv_key)
    return paths
