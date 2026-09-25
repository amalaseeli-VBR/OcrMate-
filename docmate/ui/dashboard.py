from __future__ import annotations

def _sync_from_legacy() -> None:
    from docmate import legacy as L
    g = globals()
    for k in dir(L):
        if k.startswith("__"):
            continue
        if k in g:
            continue
        g[k] = getattr(L, k)


from typing import Any, Dict
import importlib
import streamlit as st
from docmate.core.template_config import build_supplier_template_config, infer_layout_variant
from docmate.parsers.tns import looks_like_tns_invoice, parse_tns_invoice


def _tns_authoritative_result(doc_type: str, text: str, header: dict, items: list):
    """Return reconciled native-text TNS data, or preserve the existing result."""
    try:
        if str(doc_type or "") != "Purchase Invoice" or not looks_like_tns_invoice(text or ""):
            return header, items, False
        parsed = parse_tns_invoice(text or "")
        parsed_header = parsed.get("header") or {}
        parsed_items = list(parsed.get("items") or [])
        header_net = round(float(parsed_header.get("totalNet") or 0.0), 2)
        lines_net = round(sum(float(row.get("lineNet") or 0.0) for row in parsed_items), 2)
        if parsed_items and header_net > 0 and abs(lines_net - header_net) <= 0.10:
            return parsed_header, parsed_items, True
    except Exception:
        pass
    return header, items, False

def render_dashboard(settings: Dict[str, Any]) -> None:
    """Render the main Dashboard page (extracted from legacy.py)."""
    # Pull legacy helpers into this module namespace so the extracted code can run unchanged.
    legacy = importlib.import_module("docmate.legacy")
    g = globals()
    for k in dir(legacy):
        if k.startswith("__") or k == "st":
            continue
        if k not in g:
            g[k] = getattr(legacy, k)

    st.header("Dashboard")
    if settings.get("force_ocr_pdf") and not _tesseract_path_ok(settings.get("tesseract_cmd", "")):
        st.error("Force OCR is enabled, but Tesseract path is missing/invalid. Go to Admin Settings → Local OCR Settings and set: C:\Program Files\Tesseract-OCR\tesseract.exe")
    st.caption("Process invoices and documents into DocMate registers. Output can be used for VBR EPOS import, Cloud Reporting checks, and supplier reconciliation.")

    

    # Customer selector (CMSID ↔ SPOS mapping) — feeds exports and downstream posting to VBR SPOS Cloud / local DB.
    cust_rows = _db_fetchall("SELECT cms_customer_id, spos_customer_id, customer_name, trading_name, company_name FROM customer_profiles WHERE TRIM(COALESCE(cms_customer_id,'')) <> '' ORDER BY cms_customer_id")
    cust_opts = ["(Not set)"]
    cust_map = {}
    for r in (cust_rows or []):
        cms = str(r.get("cms_customer_id") or "").strip()
        if not cms:
            # Skip legacy/invalid rows (blank CMSID) so they don't pollute the dashboard dropdown
            continue
        nm = str(r.get("customer_name") or "").strip()
        spos = str(r.get("spos_customer_id") or "").strip() or "N/A"
        label = f"{cms} — {nm}  [SPOS {spos}]".strip()
        if label in cust_map:
            # Ensure uniqueness in the dropdown (defensive)
            label = f"{label} (dup)"
        cust_opts.append(label)
        cust_map[label] = dict(r)

    pre_index = 0
    try:
        cms_login = (st.session_state.get("login_cms_customer_id") or "").strip().upper()
        if cms_login:
            for i, lab in enumerate(cust_opts):
                if str(lab).upper().startswith(cms_login):
                    pre_index = i
                    break
    except Exception:
        pre_index = 0

    sel_cust = st.selectbox("Customer (CMSID ↔ SPOS)", options=cust_opts, index=pre_index, key="dash_customer_sel")
    if sel_cust != "(Not set)":
        st.session_state["selected_customer_profile"] = cust_map.get(sel_cust) or {}
        # display 2-line summary as requested
        cp = st.session_state.get("selected_customer_profile") or {}
        st.caption(f"CMSID: {cp.get('cms_customer_id','')} - {cp.get('customer_name','')}  [SPOS CustomerID { (cp.get('spos_customer_id') or 'N/A') } – { (cp.get('trading_name') or cp.get('customer_name') or '') }]")
    else:
        st.session_state["selected_customer_profile"] = {}

    doc_type = st.selectbox("Document Type", options=DOC_TYPES, index=0)

    # Defaults used on reruns (e.g. after Save to Register / Template) to avoid UnboundLocalError
    doc_text_hint = st.session_state.get('doc_text_hint', '') or ''
    extracted_text = st.session_state.get('extracted_text', '') or ''

    # Engine selection
    is_admin = bool(st.session_state.get("is_admin"))
    # End users: Auto + Local only. Admins: Cloud appears ONLY when a key exists, and is shown first.
    engine_opts = [
        ("Auto (Templates → PDF text layer → OCR fallback)", "auto"),
        ("Local AI OCR (DocTR)", "local_doctr"),
        ("Local OCR (Tesseract)", "local_tesseract"),
    ]
    if is_admin:
        api_key_present = bool((_get_google_api_key(settings) or "").strip())
        if api_key_present:
            engine_opts = [
                ("Cloud AI (Google Gemini) — ACTIVE", "cloud_gemini"),
                ("Auto (Templates → PDF text layer → OCR fallback)", "auto"),
                ("Local AI OCR (DocTR)", "local_doctr"),
                ("Local OCR (Tesseract)", "local_tesseract"),
            ]
        else:
            st.info("Cloud AI is hidden until a Google API key is added in Admin Settings → Business API Licence.")

    default_engine = settings.get("default_engine", "auto")
    keys = [k for _, k in engine_opts]
    if default_engine not in keys:
        default_engine = "auto"

    selected_engine = default_engine
    if settings.get("allow_user_choose_engine", True):
        labels = [l for l, _ in engine_opts]
        label_map = {l: k for l, k in engine_opts}
        default_label = [l for l, k in engine_opts if k == default_engine][0]
        chosen = st.selectbox("Processing Engine", options=labels, index=labels.index(default_label))
        selected_engine = label_map[chosen]
    else:
        st.info(f"Engine locked by Admin: {default_engine}")

    uploaded = []

    uploaded = st.file_uploader(
        "Upload PDF(s) / image(s)",
        type=["pdf", "png", "jpg", "jpeg"],
        accept_multiple_files=True,
    )

    if "_dm_processing_docs" not in st.session_state:
        st.session_state["_dm_processing_docs"] = False

    clicked = st.button(
        "Process Document(s)",
        disabled=(st.session_state.get("_dm_processing_docs", False) or not (uploaded and len(uploaded) > 0)),
        key="dm_process_docs_btn",
    )
    if clicked and not st.session_state.get("_dm_processing_docs", False):
        st.session_state["_dm_processing_docs"] = True
        try:
            results = []
            total_files = len(uploaded or [])
            status = st.empty()
            prog = st.progress(0.0)

            with st.spinner("Processing document(s)…"):
                for idx, up in enumerate(uploaded or []):
                    file_name = up.name
                    def _page_progress(page_no: int, total_pages: int, label: str = file_name):
                        # Live status during processing (Page N/Total) — useful for multi-page Parfetts/Booker PDFs
                        try:
                            status.info(f"Processing {idx+1}/{total_files}: {label} — Page {page_no} of {total_pages}")
                        except Exception:
                            pass

                    file_bytes = up.getvalue()
                    mime = up.type or _guess_mime_type(up.name)
                    pdf_page_count = None
                    if mime == "application/pdf":
                        try:
                            pdf_page_count = _pdf_page_count(file_bytes)
                            if (pdf_page_count or 0) > 10:
                                st.warning("Warning - Doc. more than 10 pages, contact Support Desk")
                                _log_event(f"WARNING: document has {pdf_page_count} pages (>10). Processing will be limited by Max pages setting.", file_name=file_name)
                        except Exception as e:
                            _log_event(f"ERROR reading PDF page count: {e}", file_name=file_name)


                    status.info(f"Processing {idx+1}/{total_files}: {file_name}")
                    engine = selected_engine
                    _log_event(f"START engine={engine} mime={mime} docType={doc_type} pdfPages={pdf_page_count or ''}", file_name=file_name)

                    # Ensure vat_map always defined (used by preview enrichment)
                    vat_map: Dict[str, Any] = {}

                    try:
                        # Auto: match template using fast PDF text layer
                        doc_text_hint = ""
                        effective_pages_hint = None
                        if mime == "application/pdf":
                            # First take a small preview, detect supplier, then re-read with supplier-aware page limit.
                            try:
                                preview_pages = min(2, int(pdf_page_count or 2))
                            except Exception:
                                preview_pages = 2
                            preview_text = _pdf_text_layer(file_bytes, max_pages=preview_pages, progress_cb=_page_progress, progress_label=file_name)
                            effective_pages_hint = _effective_max_pages(
                                settings,
                                supplier_hint=((preview_text or "") + "\n" + (file_name or "")),
                                file_bytes=file_bytes,
                                mime=mime,
                                default_pages=int(settings.get("ocr_max_pages", 10) or 10),
                            )
                            if effective_pages_hint and int(effective_pages_hint) > preview_pages:
                                doc_text_hint = _pdf_text_layer(file_bytes, max_pages=int(effective_pages_hint), progress_cb=_page_progress, progress_label=file_name)
                            else:
                                doc_text_hint = preview_text

                        # Include filename for matching (helps when PDF text layer is weak)
                        doc_text_for_match = ((doc_text_hint or "") + "\n" + (file_name or "")).strip()

                        api_key_present = bool((_get_google_api_key(settings) or "").strip()) if is_admin else False

                        # Auto engine: use stored template if possible; else choose best default
                        template_hit = None
                        if engine == "auto" and doc_text_for_match and settings.get("auto_mode_enabled", True):
                            party_table = _decide_party_table(doc_type)
                            if party_table == "supplier_templates":
                                template_hit = _db_find_best_template(doc_text_for_match, table="supplier_templates")
                                if not template_hit:
                                    template_hit = _auto_template_for_booker_if_needed(settings, doc_text_for_match)
                                if (not template_hit) and _looks_like_booker_invoice(doc_text_for_match):
                                    template_hit = _db_find_booker_template_latest()
                                # Extra defensive fallback: pick latest BOOKER parser template
                                if not template_hit:
                                    template_hit = _db_find_template_latest_by_parser_type('BOOKER', table='supplier_templates')

                            if template_hit:
                                cfg_t = template_hit.get("config") or {}
                                requires_cloud = bool(cfg_t.get("requires_cloud", False)) or bool(int(template_hit.get("requires_cloud") or 0)) or bool(cfg_t.get("preferred_cloud_engine"))
                                # Supplier preference: DHAMECHA is Cloud-first in AUTO when Cloud is available
                                try:
                                    if selected_engine == "auto" and str(template_hit.get("parser_type") or "").upper().strip() == "DHAMECHA":
                                        requires_cloud = True
                                except Exception:
                                    pass
                                # Booker default: do NOT force Cloud in AUTO unless explicitly configured
                                try:
                                    if str(template_hit.get("parser_type") or "").upper() == "BOOKER" and not bool(cfg_t.get("force_cloud", False)):
                                        requires_cloud = False
                                except Exception:
                                    pass

                                pref_local = cfg_t.get("preferred_local_engine", "tesseract")
                                if requires_cloud and api_key_present and is_admin:
                                    engine = "cloud_gemini"
                                else:
                                    engine = f"local_{pref_local}" if pref_local else "local_tesseract"
                            else:
                                # No supplier template.
                                # If a Cloud API key is configured (Admin), prefer Cloud for first-time suppliers,
                                # but keep Booker-like invoices local (Booker templates/parsers are stable).
                                if api_key_present and is_admin:
                                    try:
                                        if _looks_like_booker_invoice(doc_text_for_match):
                                            engine = "local_tesseract"
                                        else:
                                            engine = "cloud_gemini"
                                    except Exception:
                                        engine = "cloud_gemini"
                                else:
                                    engine = "local_tesseract"

                            # VAT mapping for preview enrichment (template-specific if available)
                            vat_map = {}
                            try:
                                if template_hit:
                                    _vm = (cfg_t or {}).get("vat_map") or {}
                                    if isinstance(_vm, str):
                                        try:
                                            _vm = json.loads(_vm)
                                        except Exception:
                                            _vm = {}
                                    if isinstance(_vm, dict):
                                        vat_map = {str(k).strip().upper(): float(v) for k, v in _vm.items() if str(k).strip()}
                            except Exception:
                                vat_map = {}
                            if not vat_map:
                                try:
                                    vat_map = dict(BookerCombinedRowParser.DEFAULT_VAT_MAP)
                                except Exception:
                                    vat_map = {"B": 20.0, "A": 0.0, "Z": 0.0, "R": 5.0, "S": 20.0, "S/A": 20.0, "Z/A": 0.0}
                        # If a Booker template exists but keyword matching did not hit (or legacy DB),
                        # attach latest BOOKER template for enrichment/logging when the document clearly looks like Booker.
                        if (template_hit is None) and _looks_like_booker_invoice(doc_text_for_match):
                            template_hit = _db_find_booker_template_latest() or _db_find_template_latest_by_parser_type("BOOKER", table="supplier_templates")


                        # Record resolved engine after AUTO/template routing (for audit)
                        try:
                            _tpl = template_hit or {}
                            _tpl_id = (_tpl.get("display_id") or _tpl.get("template_id") or "").strip()
                            if engine == "auto":
                                _log_event(f"RESOLVED engine={engine} template={_tpl_id or 'none'}", file_name=file_name)
                            else:
                                _log_event(f"RESOLVED engine={engine} template={_tpl_id or 'none'}", file_name=file_name)
                        except Exception:
                            pass

# Cloud (admin-only, key-required)
                        if engine == "cloud_gemini":
                            if not is_admin:
                                raise RuntimeError("Cloud AI is admin-only")
                            api_key = _get_google_api_key(settings)
                            if not api_key:
                                raise RuntimeError("Google API key not set in Admin settings")
                            extractor = CloudGeminiExtractor(api_key=api_key, model_name=settings.get("gemini_model", "gemini-2.0-flash"))

                            extracted_text = doc_text_hint or ""
                            doc_text_up = (extracted_text or "").upper()
                            is_booker = (doc_type == "Purchase Invoice") and (("BOOKER" in doc_text_up) or ("BOOKER" in (file_name or "").upper()))
                            is_dwg = (doc_type == "Purchase Invoice") and (("DRINKS WHOLESALE" in doc_text_up) or ("DRINKS WHOLESALE GROUP" in doc_text_up) or ("DWG" in doc_text_up) or ("DWG" in (file_name or "").upper()))
                            is_parfetts = (doc_type == "Purchase Invoice") and (("PARFETTS" in doc_text_up) or ("PARFETTS" in (file_name or "").upper()))

                            is_dwg = (doc_type == "Purchase Invoice") and (("DWG" in doc_text_up) or ("DRINKS WHOLESALE" in doc_text_up) or ("DWG" in (file_name or "").upper()) or ("DRINKS" in (file_name or "").upper()))

                            # For DWG (and other scanned PDFs), ensure we have OCR text available for debug + header completion.
                            # DWG invoices often have no PDF text layer; without this, debug text shows blank pages.
                            if is_dwg and mime == "application/pdf":
                                need_hdr = (len(extracted_text or "") < 200)
                                if need_hdr:
                                    cfg = OcrConfig(
                                        engine="tesseract",
                                        tesseract_cmd=str(settings.get("tesseract_cmd", "") or ""),
                                        dpi=int(settings.get("ocr_dpi", 300) or 300),
                                        max_pages=_effective_max_pages(
                                            settings,
                                            supplier_hint=((file_name or "") + "\nDWG"),
                                            file_bytes=file_bytes,
                                            mime=mime,
                                            default_pages=int(settings.get("ocr_max_pages", 4) or 4),
                                        ),
                                        force_pdf=True,
                                        auto_install=bool(settings.get("auto_install_ocr", True)),
                                    )
                                    cache_key = _sha256_bytes(file_bytes) + f"|{cfg.engine}|{cfg.dpi}|{cfg.max_pages}|{cfg.force_pdf}"
                                    extracted_text = ocr_text_cached(
                                        cache_key, mime, dataclasses.asdict(cfg), file_bytes,
                                        progress_cb=_page_progress, progress_label=file_name
                                    )

                            # For Parfetts, we must have header text (Customer + Date of Invoice) available for deterministic header fill.
                            # On some Windows installs, the PDF text layer may be unavailable (PyMuPDF not installed), so doc_text_hint can be empty.
                            # In that case, trigger OCR (supplier-aware max pages) before calling Cloud AI, so the Parfetts header/footer extractor can fill blanks.
                            if is_parfetts and mime == "application/pdf":
                                need_hdr = (len(extracted_text or "") < 500) or (re.search(r"\(CR\)", extracted_text or "", flags=re.I) is None) or (
                                    re.search(r"DATE\s+OF\s+INVOICE|DATE\s+PRINTED", extracted_text or "", flags=re.I) is None
                                )
                                if need_hdr:
                                    cfg = OcrConfig(
                                        engine="tesseract",
                                        tesseract_cmd=str(settings.get("tesseract_cmd", "") or ""),
                                        dpi=int(settings.get("ocr_dpi", 300) or 300),
                                        max_pages=_effective_max_pages(
                                            settings,
                                            supplier_hint=((file_name or "") + "\nPARFETTS"),
                                            file_bytes=file_bytes,
                                            mime=mime,
                                            default_pages=int(settings.get("parfetts_max_pages", 6) or 6),
                                        ),
                                        force_pdf=True,
                                        auto_install=bool(settings.get("auto_install_ocr", True)),
                                    )
                                    cache_key = _sha256_bytes(file_bytes) + f"|{cfg.engine}|{cfg.dpi}|{cfg.max_pages}|{cfg.force_pdf}"
                                    extracted_text = ocr_text_cached(
                                        cache_key, mime, dataclasses.asdict(cfg), file_bytes,
                                        progress_cb=_page_progress, progress_label=file_name
                                    )

                            # For Booker, ensure we have strong evidence text (force OCR when needed)
                            if is_booker and mime == "application/pdf":
                                local_try = LocalInvoiceParser.parse(extracted_text, doc_type=doc_type)
                                local_try_items = local_try.get("items", []) or []
                                hint_code_lines = len(re.findall(r"(?m)^\s*\d{5,7}\b", extracted_text or ""))
                                m_total = re.search(r"TOTAL\s+ITEMS\s*:\s*(\d+)", extracted_text or "", flags=re.I)
                                expected_total = int(m_total.group(1)) if m_total else 0
                                incomplete = (expected_total > 0 and len(local_try_items) < expected_total) or (expected_total == 0 and hint_code_lines < 10)

                                if incomplete or (len(extracted_text) < 2500) or (not local_try_items):
                                    cfg = OcrConfig(
                                        engine="tesseract",
                                        tesseract_cmd=str(settings.get("tesseract_cmd", "") or ""),
                                        dpi=int(settings.get("ocr_dpi", 300) or 300),
                                        max_pages=_effective_max_pages(settings, supplier_hint=doc_text_for_match, file_bytes=file_bytes, mime=mime, default_pages=int(settings.get("ocr_max_pages", 10) or 10)),
                                        force_pdf=True,
                                        auto_install=bool(settings.get("auto_install_ocr", True)),
                                    )
                                    cache_key = _sha256_bytes(file_bytes) + f"|{cfg.engine}|{cfg.dpi}|{cfg.max_pages}|{cfg.force_pdf}"
                                    extracted_text = ocr_text_cached(cache_key, mime, dataclasses.asdict(cfg), file_bytes, progress_cb=_page_progress, progress_label=file_name)

                            data = extractor.extract_purchase_invoice(file_bytes, mime, file_name, doc_text=extracted_text)
                            cloud_header = data.get("header", {}) or {}
                            cloud_items = data.get("items", []) or []

                            # Deterministic Booker parse for parity
                            if (doc_type == "Purchase Invoice") and ("BOOKER" in str(cloud_header.get("supplierName", "")).upper() or "BOOKER" in (doc_text_hint or "").upper() or "BOOKER" in (file_name or "").upper()):
                                local_data = LocalInvoiceParser.parse(extracted_text, doc_type=doc_type)
                                header = local_data.get("header", {}) or {}
                                items = local_data.get("items", []) or []

                                # Fill missing header from Cloud (treat OCR page markers as blank supplier)
                                for k in ("supplierName", "customerName", "invoiceNumber", "invoiceDate", "currency"):
                                    hv = str(header.get(k) or "").strip()
                                    cv = str(cloud_header.get(k) or "").strip()
                                    if k == "supplierName" and _looks_like_page_marker(hv):
                                        hv = ""
                                    if (not hv) and cv:
                                        header[k] = cloud_header.get(k)
                                for k in ("totalNet", "totalVat", "totalGross"):
                                    if float(header.get(k) or 0.0) <= 0.0 and float(cloud_header.get(k) or 0.0) > 0.0:
                                        header[k] = float(cloud_header.get(k) or 0.0)
                                if float(header.get("totalGross") or 0.0) <= 0.0 and float(header.get("totalNet") or 0.0) > 0.0:
                                    header["totalGross"] = round(float(header["totalNet"]) + float(header.get("totalVat") or 0.0), 2)

                                if not items and cloud_items:
                                    header = cloud_header or header
                                    items = cloud_items
                            # Deterministic Parfetts parse for parity (and to avoid LLM column drift)
                            elif (doc_type == "Purchase Invoice") and ("PARFETTS" in str(cloud_header.get("supplierName", "")).upper() or is_parfetts):
                                local_data = LocalInvoiceParser.parse(extracted_text, doc_type)
                                local_header = local_data.get("header") or {}
                                local_items = local_data.get("items") or []

                                # If local parse found items, prefer it; otherwise fall back to cloud output.
                                if local_items:
                                    header = local_header
                                    items = local_items
                                else:
                                    header = cloud_header
                                    items = cloud_items

                                # Fill missing header fields from local OCR parse when cloud is blank
                                # (Parfetts often has customer + invoice date in the OCR header block, but Cloud may omit it.)
                                for key in ["customerName", "invoiceDate"]:
                                    if not str(header.get(key, "")).strip() and str(local_header.get(key, "")).strip():
                                        header[key] = local_header.get(key)

                                # VAT breakdown: prefer extracted-from-invoice breakdown (Parfetts A/Z), even in Cloud mode
                                if (not header.get("vatBreakdown")) and local_header.get("vatBreakdown"):
                                    header["vatBreakdown"] = local_header.get("vatBreakdown")

                                # Fill missing header fields from cloud when local is blank
                                for key in ["supplierName", "customerName", "invoiceNumber", "invoiceDate", "currency"]:
                                    if not str(header.get(key, "")).strip() and str(cloud_header.get(key, "")).strip():
                                        header[key] = cloud_header.get(key)
                                # Fill totals if missing (keep Parfetts footer totals when present)
                                try:
                                    net_items = round(sum(_safe_float(it.get("lineNet")) for it in (items or [])), 2)
                                    vat_items = round(sum(_safe_float(it.get("vatAmount")) for it in (items or [])), 2)
                                    gross_items = round(net_items + vat_items, 2)
                                    if _safe_float(header.get("totalNet")) <= 0 and net_items > 0:
                                        header["totalNet"] = net_items
                                    if _safe_float(header.get("totalVat")) <= 0 and vat_items > 0:
                                        header["totalVat"] = vat_items
                                    if _safe_float(header.get("totalGross")) <= 0 and gross_items > 0:
                                        header["totalGross"] = gross_items

                                    # Enforce Parfetts footer totals (prevents rounding drift vs invoice)
                                    foot = _parfetts_extract_header(extracted_text or "")
                                    for k in ("totalNet", "totalVat", "totalGross"):
                                        if _safe_float(foot.get(k)) > 0:
                                            header[k] = _safe_float(foot.get(k))

                                    # Fill Parfetts header fields (keeps totals aligned to invoice footer)
                                    try:
                                        for kk in ("supplierName", "customerName", "invoiceNumber", "invoiceDate", "currency", "deliveryCharge"):
                                            if (not (header.get(kk) or "").strip()) and (foot.get(kk) is not None):
                                                header[kk] = foot.get(kk)
                                        if foot.get("vatBreakdown"):
                                            header["vatBreakdown"] = foot.get("vatBreakdown")
                                    except Exception:
                                        pass

                                    # Parfetts: last-chance fill of customer & invoice date (OCR/PDF + filename)
                                    try:
                                        if not (header.get("invoiceDate") or ""):
                                            fn_dt = _date_from_filename(file_name or "")
                                            if fn_dt:
                                                header["invoiceDate"] = fn_dt
                                        if not (header.get("customerName") or "") or not (header.get("invoiceDate") or ""):
                                            cust2, dt2 = _parfetts_autofill_customer_and_date(extracted_text or "")
                                            if cust2 and not (header.get("customerName") or ""):
                                                header["customerName"] = cust2
                                            if dt2 and not (header.get("invoiceDate") or ""):
                                                header["invoiceDate"] = dt2
                                    except Exception:
                                        pass

                                except Exception:
                                    pass

                            else:
                                header = cloud_header
                                items = cloud_items
                                _dwg_enrich_header_items(header, items, supplier_hint=doc_text_hint)
                                # Dhamecha enrichment
                                if _is_supplier_like(header.get('supplierName') or '', ['dhamecha']):
                                    _dhamecha_enrich_header_items(header, items, doc_text_hint=doc_text_hint)

                            extracted_text = extracted_text or doc_text_hint

                        else:
                            # Local path
                            extracted_text = None
                            is_booker = (doc_type == "Purchase Invoice") and (("booker" in (file_name or "").lower()) or ("BOOKER" in (doc_text_hint or "").upper()))
                            is_parfetts = (doc_type == "Purchase Invoice") and (("parfetts" in (file_name or "").lower()) or ("PARFETTS" in (doc_text_hint or "").upper()))
                            is_dwg = (doc_type == "Purchase Invoice") and (("DWG" in (doc_text_hint or "").upper()) or ("DRINKS WHOLESALE" in (doc_text_hint or "").upper()) or ("DRINKS WHOLESALE GROUP" in (doc_text_hint or "").upper()) or ("DWG" in (file_name or "").upper()) or ("DRINKS WHOLESALE" in (file_name or "").upper()))
                            explicit_local = (selected_engine == "local_tesseract")
                            local_engine = engine.replace("local_", "")
                            force_pdf = bool(settings.get("force_ocr_pdf", False))

                            # Booker PDFs are often scans → OCR helps. Parfetts PDFs usually have a strong text layer.
                            if mime == "application/pdf":
                                # Prefer PDF text layer when it looks strong, but force OCR for known scan-heavy suppliers.
                                hint = (doc_text_hint or "")

                                is_dhamecha = ("DHAMECHA" in hint.upper()) or ("DHAMECHA" in (file_name or "").upper()) or ("927135230" in hint) or ("XRAW00000102826" in hint) or ("TOTAL GOODS" in hint.upper())

                                if is_booker:
                                    # Booker PDFs are often scans (barcode/line layouts) → OCR is safer.
                                    force_pdf = True

                                elif is_dhamecha:
                                    # Dhamecha PDFs often have a usable text layer; OCR can latch onto the legal disclaimer.
                                    force_pdf = False

                                elif is_parfetts:
                                    # Parfetts PDFs usually have a strong text layer; only force OCR if it looks weak.
                                    hint_code_lines = len(re.findall(r"(?m)^\s*\d{5,7}\b", hint))
                                    if len(hint.strip()) < 500 or hint_code_lines < 10:
                                        force_pdf = True
                                    else:
                                        force_pdf = False

                                elif is_dhamecha:
                                    # Dhamecha PDFs have a strong text layer; prefer text layer to avoid OCR disclaimer noise.
                                    # Only force OCR if the text layer is extremely short.
                                    if len(hint.strip()) < 300:
                                        force_pdf = True
                                    else:
                                        force_pdf = False

                                else:
                                    # Generic: only force OCR when the PDF text layer is weak (short or lacks item-code structure).
                                    hint_code_lines = len(re.findall(r"(?m)^\s*\d{5,7}\b", hint))
                                    if len(hint.strip()) < 350 or hint_code_lines < 3:
                                        force_pdf = True
                                    else:
                                        force_pdf = False

                            cfg = OcrConfig(
                                engine=local_engine,
                                tesseract_cmd=str(settings.get("tesseract_cmd", "") or ""),
                                dpi=int(settings.get("ocr_dpi", 250)),
                                max_pages=_effective_max_pages(settings, supplier_hint=(doc_text_hint or "") + "\n" + (file_name or ""), file_bytes=file_bytes, mime=mime, default_pages=int(settings.get("ocr_max_pages", 10) or 10)),
                                force_pdf=force_pdf,
                                auto_install=bool(settings.get("auto_install_ocr", True)),
                            )

                            if mime == "application/pdf" and not cfg.force_pdf and (doc_text_hint or "").strip():
                                extracted_text = doc_text_hint

                            cache_key = _cache_key_for_ocr(file_bytes, mime=mime, engine=cfg.engine, dpi=cfg.dpi, max_pages=cfg.max_pages, force_pdf=cfg.force_pdf)
                            if extracted_text is None or len((extracted_text or "").strip()) < 120:
                                ocr_txt = ocr_text_cached(cache_key, mime, dataclasses.asdict(cfg), file_bytes, progress_cb=_page_progress, progress_label=file_name)
                                # If we already have any PDF text-layer content, keep it alongside OCR to improve header/footer detection (Parfetts VAT box, etc.)
                                if (doc_text_hint or "").strip():
                                    extracted_text = (doc_text_hint.strip() + "\n\n" + (ocr_txt or "").strip())
                                else:
                                    extracted_text = ocr_txt

                            data = LocalInvoiceParser.parse(extracted_text, doc_type)
                            header = data.get("header") or {}
                            items = data.get("items") or []

                            # Claude Vision fallback: for image files where OCR produces poor/garbled descriptions,
                            # send the raw image bytes to Claude Vision for accurate extraction (bypasses OCR entirely).
                            try:
                                _is_image_file = mime.startswith("image/")
                                _desc_poor = (
                                    not items or
                                    sum(1 for _it in items if len(str(_it.get("description") or "").strip()) < 4) > len(items) * 0.5
                                )
                                if _is_image_file and _desc_poor:
                                    _claude_key = _get_claude_api_key(settings)
                                    if _claude_key:
                                        _vision = ClaudeAIFallbackParser(_claude_key, settings.get("claude_model", "claude-sonnet-4-6"))
                                        _vresult = _vision.extract_from_image(file_bytes, mime, doc_type)
                                        if _vresult and (_vresult.get("items") or []):
                                            items = _vresult.get("items") or items
                                            _vh = _vresult.get("header") or {}
                                            for _k in ("supplierName", "customerName", "invoiceNumber", "invoiceDate", "currency"):
                                                if _vh.get(_k):
                                                    header[_k] = _vh[_k]
                                            for _k in ("totalNet", "totalVat", "totalGross"):
                                                if float(_vh.get(_k) or 0.0) > 0.0 and float(header.get(_k) or 0.0) <= 0.0:
                                                    header[_k] = float(_vh[_k])
                                            _log_event("Claude Vision used for image invoice extraction", file_name=file_name)
                            except Exception as _ve:
                                _log_event(f"Claude Vision fallback error: {_ve}", file_name=file_name)

                            # AUTO safety: if a template routed to Local for a supplier but Local parsing produced no items,
                            # retry with Cloud AI when available (prevents Dhamecha/DWG from returning empty item lists).
                            try:
                                if (selected_engine == "auto") and (template_hit is not None) and is_admin and api_key_present:
                                    ptype = str((template_hit or {}).get("parser_type") or "").upper().strip()
                                    if ptype in ("DHAMECHA", "DWG") and (len(items) == 0 or not str(header.get("invoiceNumber") or "").strip()):
                                        _log_event(f"AUTO fallback: Local parse incomplete for {ptype}; retrying with Cloud AI.", file_name=file_name)
                                        api_key = _get_google_api_key(settings)
                                        if api_key:
                                            cext = CloudGeminiExtractor(api_key=api_key, model_name=settings.get("gemini_model", "gemini-2.0-flash"))
                                            cdata = cext.extract_purchase_invoice(file_bytes, mime, file_name=file_name, doc_text=doc_text_hint or "")
                                            cheader = (cdata or {}).get("header") or {}
                                            citems = (cdata or {}).get("items") or []
                                            if citems:
                                                header, items = cheader, citems
                                                engine = "cloud_gemini"
                            except Exception:
                                pass
                            # Booker customer autofill (AUTO/Local/Cloud): ensure customerName is captured, cleaned, and (if needed) learned from template
                            try:
                                if doc_type == "Purchase Invoice" and (("BOOKER" in (extracted_text or "").upper()) or ("BOOKER" in (file_name or "").upper())):
                                    _booker_autofill_customer_name(header, extracted_text, template_hit)
                            except Exception:
                                pass

# Default VAT mapping for Booker when no template map exists
                            try:
                                if (not vat_map) and doc_type == "Purchase Invoice" and ("BOOKER" in (extracted_text or "").upper() or "BOOKER" in (file_name or "").upper()):
                                    vat_map = dict(BookerCombinedRowParser.DEFAULT_VAT_MAP)
                            except Exception:
                                pass
                            items = _preview_enrich_items(items, vat_map)

                            # Auto fallback guidance (non-admin): suggest Cloud key if extraction incomplete
                            if selected_engine == "auto" and (not items or float(header.get("totalGross") or 0.0) <= 0.0):
                                if not is_admin or not api_key_present:
                                    st.warning("Auto extraction looks incomplete. Try Local OCR, or ask an admin to add a Cloud API key for difficult invoices.")
                                else:
                                    # admin: attempt a single Cloud assist if Booker and clearly incomplete
                                    if is_booker or is_dwg:
                                        try:
                                            extractor2 = CloudGeminiExtractor(api_key=_get_google_api_key(settings), model_name=settings.get("gemini_model", "gemini-2.0-flash"))
                                            cloud = extractor2.extract_purchase_invoice(file_bytes, mime, file_name, doc_text=extracted_text)
                                            cloud_header = cloud.get("header", {}) or {}
                                            cloud_items = cloud.get("items", []) or []
                                            if "BOOKER" in str(cloud_header.get("supplierName", "")).upper():
                                                local_data = LocalInvoiceParser.parse(extracted_text, doc_type=doc_type)
                                                header = local_data.get("header", {}) or cloud_header
                                                items = local_data.get("items", []) or cloud_items
                                                for k in ("totalNet", "totalVat", "totalGross"):
                                                    if float(header.get(k) or 0.0) <= 0.0 and float(cloud_header.get(k) or 0.0) > 0.0:
                                                        header[k] = float(cloud_header.get(k) or 0.0)
                                                if float(header.get("totalGross") or 0.0) <= 0.0 and float(header.get("totalNet") or 0.0) > 0.0:
                                                    header["totalGross"] = round(float(header["totalNet"]) + float(header.get("totalVat") or 0.0), 2)
                                            else:
                                                header = cloud_header or header
                                                items = cloud_items or items
                                            engine = "cloud_gemini"
                                        except Exception:
                                            pass
                    
                        
                        # Booker customer autofill (all engines): if missing, derive from text or template default
                        try:
                            if doc_type == "Purchase Invoice":
                                sup_u = str((header or {}).get("supplierName") or "").upper()
                                if ("BOOKER" in sup_u) or _looks_like_booker_invoice(extracted_text or doc_text_for_match or ""):
                                    _booker_autofill_customer_name(header, extracted_text or doc_text_for_match or "", template_hit)
                        except Exception:
                            pass

                        # TNS native-text parsing is authoritative when its rows
                        # reconcile to the printed net total.
                        header, items, _tns_used = _tns_authoritative_result(
                            doc_type, doc_text_hint or extracted_text or "", header, items
                        )
                        if _tns_used:
                            engine = "local_tns"
                            _log_event(
                                f"TNS authoritative parser applied: items={len(items)} "
                                f"net={sum(float(row.get('lineNet') or 0.0) for row in items):.2f}",
                                file_name=file_name,
                            )

# Normalise invoice date (store/display as date only)
                        try:
                            header['invoiceDate'] = _normalise_invoice_date_str(header.get('invoiceDate') or '')
                        except Exception:
                            pass
                        _log_event(f"DONE engine={engine} supplier={header.get('supplierName','')} inv={header.get('invoiceNumber','')} items={len(items)} net={header.get('totalNet',0)} vat={header.get('totalVat',0)} gross={header.get('totalGross',0)}", file_name=file_name)
                        inv_key = _make_inv_key(header, file_bytes, doc_type)
                        results.append(
                            {
                                "file": file_name,
                                "mime": mime,
                                "engine": engine,
                                "doc_type": doc_type,
                                "header": header,
                                "header_raw": copy.deepcopy(header),
                                "items": items,
                                "inv_key": inv_key,
                                "text": extracted_text,
                                "debug_text": extracted_text,
                                "template_used": (template_hit.get("display_id") or template_hit.get("template_id")) if template_hit else "",
                                "template_parser_type": (template_hit.get("parser_type") if template_hit else ""),
                            }
                        )

                    except Exception as e:
                        import traceback
                        tb = traceback.format_exc()
                        _log_event(f"Processing error: {e}\n{tb}", file_name=file_name)
                        results.append({"file": file_name, "error": str(e), "engine": engine, "doc_type": doc_type})

                    prog.progress(min(1.0, float(idx + 1) / float(max(total_files, 1))))

            status.success("Processing complete.")
            prog.progress(1.0)
            st.session_state["last_batch"] = results

            st.session_state["last_batch_ts"] = time.time()

        finally:
            st.session_state["_dm_processing_docs"] = False

    batch = st.session_state.get("last_batch") or []
    if not batch:
        st.info("Upload document(s) and click **Process Document(s)**.")
        return

    st.divider()
    st.subheader("Results")

    for res in batch:
        file_name = res.get("file")
        if res.get("error"):
            st.error(f"{file_name} — {res.get('error')}")
            continue

        header = res.get("header") or {}
        items = res.get("items") or []
        # Ensure derived/calculated fields are present for UI + VAT breakdown
        items = _recalc_items(items)
        res["items"] = items
        # Parfetts: ensure key header fields + VAT breakdown are present even when OCR/text-layer is imperfect.
        try:
            tag = ((header.get("supplierName") or "") + " " + (file_name or "")).lower()
            if "parfetts" in tag:
                dbg = (res.get("debug_text") or res.get("text") or "") or ""
                # Fill missing header fields from the full captured text
                if dbg and (not header.get("customerName") or not header.get("invoiceDate") or not header.get("invoiceNumber") or not header.get("vatBreakdown")):
                    foot = _parfetts_extract_header(dbg) or {}
                    for kk in ("supplierName", "customerName", "invoiceDate", "invoiceNumber", "deliveryCharge", "vatBreakdown", "totalNet", "totalVat", "totalGross"):
                        if (not header.get(kk)) and foot.get(kk) not in (None, ""):
                            header[kk] = foot.get(kk)

                # If VAT breakdown still missing, calculate it from line items (vatCode + vatRate) and reconcile totals
                if (not header.get("vatBreakdown")) and items:
                    calc = _parfetts_vat_breakdown_from_items(items) or {}
                    rows = calc.get("rows") or []
                    if rows:
                        header["vatBreakdown"] = rows
                        dc = _safe_float(header.get("deliveryCharge"))
                        if abs(_safe_float(header.get("totalNet")) - _safe_float(calc.get("totalNet"))) > 0.05:
                            header["totalNet"] = _safe_float(calc.get("totalNet"))
                        if abs(_safe_float(header.get("totalVat")) - _safe_float(calc.get("totalVat"))) > 0.05:
                            header["totalVat"] = _safe_float(calc.get("totalVat"))
                        header["totalGross"] = round(_safe_float(calc.get("totalNet")) + _safe_float(calc.get("totalVat")) + dc, 2)

                res["header"] = header
        except Exception:
            pass

        inv_key = res.get("inv_key")
        doc_type = res.get("doc_type")

        with st.expander(f"{file_name} — {doc_type}", expanded=True):
            # Header summary + VAT breakdown + Template tools (Admin)
            top_left, top_right = st.columns([3, 1])

            with top_left:
                # Compact KPI header (better for quick validation before posting into VBR EPOS / Cloud Reporting)
                metrics = _calc_metrics(header, items)
                supplier_v = header.get("supplierName") or "—"
                customer_v = header.get("customerName") or "—"
                inv_date_v = header.get("invoiceDate") or "—"
                inv_no_v = header.get("invoiceNumber") or "—"

                # CSS once (safe to call repeatedly inside expander)
                st.markdown("""<style>
                .dm-row{display:flex;gap:10px;flex-wrap:wrap;margin:6px 0;}
                .dm-card{flex:1;min-width:160px;border:1px solid var(--dm-border);border-radius:14px;padding:10px 12px;background:var(--dm-card);color:var(--dm-text);}
                .dm-card h4{margin:0;font-size:12px;color:var(--dm-muted);font-weight:700;}
                .dm-card .v{margin-top:2px;font-size:var(--dm-kpi-font);color:var(--dm-text);font-weight:900;line-height:1.15;}
                .dm-card.yellow{background:#fff7cc;}
                .dm-card.yellow .v{font-size:var(--dm-header-font);}
                .dm-card.blue{background:#e8f2ff;}
                .dm-card.green{background:#eaf7ea;}
                </style>""", unsafe_allow_html=True)

                st.markdown(
                    f"""<div class='dm-row'>
                        <div class='dm-card'><h4>Supplier</h4><div class='v'>{supplier_v}</div></div>
                        <div class='dm-card'><h4>Customer</h4><div class='v'>{customer_v}</div></div>
                        <div class='dm-card'><h4>Invoice Date</h4><div class='v'>{inv_date_v}</div></div>
                        <div class='dm-card'><h4>Invoice Number</h4><div class='v'>{inv_no_v}</div></div>
                    </div>
                    <div class='dm-row'>
                        <div class='dm-card blue'><h4>No. of Cases (Σ Qty)</h4><div class='v'>{_fmt_int(metrics.get('total_case_qty',0))}</div></div>
                        <div class='dm-card blue'><h4>No. of Stock Units</h4><div class='v'>{_fmt_int(metrics.get('total_units',0))}</div></div>
                        <div class='dm-card blue'><h4>No. of Products</h4><div class='v'>{int(metrics.get('products_count',0) or 0)}</div></div>
                        <div class='dm-card blue'><h4>Average POR (%)</h4><div class='v'>{_safe_float(metrics.get('avg_por',0) or 0):,.1f}</div></div>
                    </div>
                    <div class='dm-row'>
                        <div class='dm-card yellow'><h4>Net</h4><div class='v'>{_money(header.get('totalNet'))}</div></div>
                        <div class='dm-card yellow'><h4>VAT</h4><div class='v'>{_money(header.get('totalVat'))}</div></div>
                        <div class='dm-card yellow'><h4>Gross</h4><div class='v'>{_money(header.get('totalGross'))}</div></div>
                    </div>""",
                    unsafe_allow_html=True,
                )

# Line totals reconciliation notes (helps validate posting into VBR EPOS / Cloud Reporting)
                items = _ensure_line_numbers(items)
                _r_text = (res.get("text") or res.get("debug_text") or "")
                _hint = ((file_name or "") + " " + _r_text + " " + str(header.get("supplierName") or "")).lower()
                if ("dwg" in _hint) or ("drinks wholesale" in _hint):
                    dwg_out = _dwg_enrich_header_items(
                        header, items,
                        supplier_hint=(file_name or "") + " " + _r_text
                    )
                    if isinstance(dwg_out, tuple) and len(dwg_out) == 2:
                        header, items = dwg_out
                _ensure_vat_breakdown(header, items, f"{header.get('supplierName','')} {file_name}")
                metrics = _calc_metrics(header, items)
                net_note = f"[Line Items Net = {_money(metrics['sum_net'])} | Header Net = {_money(metrics['hdr_net'])} | Diff = ({_money(metrics['diff_net'])})]"
                vat_note = f"[Line Items VAT = {_money(metrics['sum_vat'])} | Header VAT = {_money(metrics['hdr_vat'])} | Diff = ({_money(metrics['diff_vat'])})]"
                gross_note = f"[Line Items Gross = {_money(metrics['sum_gross'])} | Header Gross = {_money(metrics['hdr_gross'])} | Diff = ({_money(metrics['diff_gross'])})]"
                # Reconciliation (compact)
                recon_col, reason_col = st.columns([1, 1])
                with recon_col:
                    st.markdown(_render_reconciliation_box_html(metrics), unsafe_allow_html=True)
                with reason_col:
                    st.markdown(_render_reconciliation_reasons_html(), unsafe_allow_html=True)
            with top_right:

                # Quick document download (top-right)
                tpl_used = (res.get('template_used') or '').strip() or '—'
                tpl_type = (res.get('template_parser_type') or '').strip()

                reconciliation = _build_reconciliation_status(metrics)
                recon_notes = _build_reconciliation_notes(metrics)

                # Engine badge (visible beside print icon)
                eng_used = (res.get("engine") or "").strip() or "—"
                c1, c2, c3 = st.columns([2.2, 2.2, 1.8])
                try:
                    badge_col, btn_col = st.columns([1, 1])
                except Exception:
                    badge_col = st.container()
                    btn_col = st.container()

                with badge_col:
                    st.markdown(
                        f"""<div style='display:inline-block; padding:8px 12px; border-radius:10px; background:#fff7cc; font-weight:800; border:1px solid #e5e7eb;'>
                        Engine Used: {eng_used}
                        </div>""",
                        unsafe_allow_html=True,
                    )
                with btn_col:
                    try:
                        pdf_bytes = _generate_pdf_report_bytes(file_name, header, items, reconciliation, recon_notes)
                        st.download_button(
                            '🖨️',
                            data=pdf_bytes,
                            file_name=(os.path.splitext(file_name)[0] + '_DocMate_Report.pdf'),
                            mime='application/pdf',
                            help=f"Download DocMate PDF report (A4 landscape). Template: {tpl_used}{(' (' + tpl_type + ')') if tpl_type else ''}",
                        )
                    except Exception as e:
                        # Robust fallback: HTML report (users can Print → Save as PDF in the browser)
                        html_report = _generate_html_report(file_name, header, items, reconciliation, recon_notes)
                        st.download_button(
                            '🖨️',
                            data=html_report,
                            file_name=(os.path.splitext(file_name)[0] + '_DocMate_Report.html'),
                            mime='text/html',
                            help=f"PDF not available on this machine. Download HTML report and print to PDF. Details: {e}",
                        )
                        st.warning("PDF report requires the Python package 'reportlab'. Install it with: pip install reportlab. Until then, the 🖨️ button downloads an HTML report you can print to PDF.")
                if (res.get('engine') or '') == 'auto' and tpl_used == '—':
                    st.warning('AUTO ran without a matched template. Best practice: Cloud AI → Save Supplier Template → AUTO.')
                st.caption('DocMate Invoice Admin')

                # VAT Breakdown must ALWAYS be calculated from (possibly edited) line items (not captured header totals)
                st.markdown(_render_vat_breakdown_box_calculated(items, max_rows=5), unsafe_allow_html=True)

                # Legacy fallback message removed: calculated breakdown is always available.

                
                # Admin-only: edit header fields (quick fix for Customer/Invoice Date etc. before posting)
                if st.session_state.get("is_admin", False):
                    with st.expander("Header (Admin)", expanded=False):
                        st.caption("Edit header fields for this invoice before saving to Register (audit logged).")
                        _sup_in = st.text_input("Supplier Name", value=(header.get("supplierName") or ""), key=f"hdr_sup_{inv_key}")
                        header["supplierName"] = _collapse_spaces(_sup_in) if isinstance(_sup_in, str) else _sup_in
                        _cust_in = st.text_input("Customer Name", value=(header.get("customerName") or ""), key=f"hdr_cust_{inv_key}")
                        _date_in = st.text_input("Invoice Date (YYYY-MM-DD)", value=(header.get("invoiceDate") or ""), key=f"hdr_date_{inv_key}")
                        _inv_in = st.text_input("Invoice Number", value=(header.get("invoiceNumber") or ""), key=f"hdr_inv_{inv_key}")

                        colH1, colH2 = st.columns(2)
                        if colH1.button("Apply Header Changes", key=f"hdr_apply_{inv_key}"):
                            new_cust = _collapse_spaces((_cust_in or "").strip())
                            new_date = _normalise_invoice_date_str(_date_in)
                            new_inv  = (_inv_in or "").strip()

                            if new_cust:
                                header["customerName"] = new_cust
                            if new_date:
                                header["invoiceDate"] = new_date
                            if new_inv:
                                header["invoiceNumber"] = new_inv

                            # persist into batch so Save to Register uses these values
                            try:
                                batch_now = st.session_state.get("last_batch") or []
                                for _ix in range(len(batch_now)):
                                    if (batch_now[_ix].get("inv_key") == inv_key) and (batch_now[_ix].get("file") == file_name):
                                        batch_now[_ix]["header"] = header
                                        st.session_state["last_batch"] = batch_now
                                        break
                            except Exception:
                                pass
                            # If Booker invoice and Admin provided Customer Name, learn it into the latest BOOKER supplier template
                            try:
                                sup_u = str(header.get("supplierName") or "").upper()
                                if new_cust and ("BOOKER" in sup_u):
                                    tpl_b = _db_find_booker_template_latest() or _db_find_template_latest_by_parser_type("BOOKER", table="supplier_templates")
                                    if tpl_b and (tpl_b.get("template_id") or ""):
                                        _db_patch_supplier_template_config(str(tpl_b.get("template_id") or ""), {"default_customer_name": new_cust})
                                        _log_event(f"Booker template learned default_customer_name={new_cust}", file_name=file_name)
                            except Exception:
                                pass


                            _log_event(f"Admin header edit: customer={header.get('customerName','')} date={header.get('invoiceDate','')} inv={header.get('invoiceNumber','')}", file_name=file_name)
                            st.success("Header updated for this session. Click 'Save to Register' to persist.")
                            st.rerun()

                        if colH2.button("Reset to Extracted Header", key=f"hdr_reset_{inv_key}"):
                            try:
                                raw = res.get("header_raw") or {}
                                if isinstance(raw, dict) and raw:
                                    header.clear()
                                    header.update(copy.deepcopy(raw))

                                    batch_now = st.session_state.get("last_batch") or []
                                    for _ix in range(len(batch_now)):
                                        if (batch_now[_ix].get("inv_key") == inv_key) and (batch_now[_ix].get("file") == file_name):
                                            batch_now[_ix]["header"] = header
                                            st.session_state["last_batch"] = batch_now
                                            break

                                    _log_event("Admin header reset to extracted values", file_name=file_name)
                                    st.success("Header reset.")
                                    st.rerun()
                            except Exception:
                                st.warning("Could not reset header for this invoice.")
# Admin-only: create/update template from this invoice (kept here to make the process clearer)
                if st.session_state.get("is_admin", False):
                    with st.expander("Template (Admin)", expanded=False):
                        settings_now = st.session_state.get("settings", {}) or {}

                        supplier_nm = (header.get("supplierName") or "").strip()
                        if not supplier_nm:
                            supplier_nm = _guess_supplier_from_filename(file_name) or ""
                        # Detect supplier parser type for sensible defaults
                        supplier_nm_l = supplier_nm.lower()
                        parser_type = "GENERIC"
                        if "parfetts" in supplier_nm_l:
                            parser_type = "PARFETTS"
                        elif "booker" in supplier_nm_l:
                            parser_type = "BOOKER"
                        elif "dhamecha" in supplier_nm_l:
                            parser_type = "DHAMECHA"
                        elif ("drinks wholesale group" in supplier_nm_l) or ("dwg" in supplier_nm_l):
                            parser_type = "DWG"


                        default_keywords = [k for k in [supplier_nm, header.get("invoiceNumber"), header.get("customerName")] if k]
                        default_keywords = [str(x).strip() for x in default_keywords if str(x).strip()]
                        kw_str_default = ", ".join(default_keywords[:6])

                        inv_key = (res.get("inv_key") or "").strip() or hashlib.sha256((file_name or "").encode("utf-8", errors="ignore")).hexdigest()[:10]
                        default_display_id = f"TPL{_uk_now_iso().replace('-','').replace(':','').replace(' ','')}-{inv_key[:4]}"

                        display_id = st.text_input("Template Display ID", value=default_display_id, key=f"tpl_disp_{inv_key}")
                        kw_str = st.text_input("Match keywords (comma-separated)", value=kw_str_default, key=f"tpl_kw_{inv_key}")
                        layout_variant = st.text_input(
                            "Layout variant",
                            value=infer_layout_variant(parser_type, (extracted_text or doc_text_hint or ""), items),
                            key=f"tpl_layout_{inv_key}",
                            help="Use one variant per supplier layout, e.g. BOOKER_INVOICE_V1 or DHAMECHA_SCAN_V1.",
                        )

                        pdf_pages_override = st.number_input(
                            "PDF max pages override",
                            min_value=1,
                            max_value=30,
                            value=int(settings_now.get("ocr_max_pages", 4) or 4),
                            step=1,
                            key=f"tpl_pages_{inv_key}",
                        )

                        preferred_local = st.selectbox(
                            "Preferred local engine",
                            options=["tesseract", "pdf_text_layer"],
                            index=0,
                            key=f"tpl_local_{inv_key}",
                        )

                        requires_cloud = st.checkbox("Requires Cloud extraction", value=False, key=f"tpl_cloud_{inv_key}")

                        vat_map_default: Dict[str, Any] = {}
                        if parser_type == "PARFETTS":
                            vat_map_default = {"A": 20.0, "Z": 0.0}
                        elif parser_type == "BOOKER":
                            vat_map_default = {"B": 20.0, "R": 5.0, "Z": 0.0, "A": 0.0}
                        elif parser_type == "DWG":
                            vat_map_default = {"S/A": 20.0, "S": 20.0}
                        elif parser_type == "DHAMECHA":
                            vat_map_default = {"A": 20.0, "B": 5.0, "Z": 0.0}

                        # If we can infer mapping from extracted lines, merge it in (helps new suppliers)
                        vat_map_detected = _derive_vat_map_from_items(items)
                        if vat_map_detected:
                            vat_map_default = {**vat_map_default, **vat_map_detected}

                        vat_map_key = f"tpl_vatmap_{inv_key}"
                        if (vat_map_key not in st.session_state) or (str(st.session_state.get(vat_map_key) or "").strip() in ("", "{}", "null")):
                            st.session_state[vat_map_key] = json.dumps(vat_map_default, indent=2)
                        vat_map_json = st.text_area(
                            "VAT mapping (JSON)",
                            key=vat_map_key,
                            height=110,
                        )
                        if st.button("Save template for this supplier", key=f"tpl_save_{inv_key}"):
                            try:
                                vat_map = json.loads(vat_map_json or "{}")
                                if not isinstance(vat_map, dict):
                                    raise ValueError("VAT mapping must be a JSON object.")
                            except Exception as ex:
                                st.error(f"VAT mapping JSON invalid: {ex}")
                                vat_map = {}

                            # Booker templates: keep AUTO local by default (do not force Cloud unless explicitly set in the template)
                            try:
                                if str(parser_type).upper() == "BOOKER":
                                    # only force Cloud if Admin explicitly marks it in config
                                    requires_cloud = bool(requires_cloud) and bool((st.session_state.get("booker_force_cloud_checkbox") or False))
                            except Exception:
                                pass

                            match_keywords = [k.strip() for k in (kw_str or "").split(",") if k.strip()]
                            template_config = build_supplier_template_config(
                                supplier_name=(supplier_nm or "").strip(),
                                parser_type=parser_type,
                                doc_text=(extracted_text or doc_text_hint or ""),
                                header=header,
                                items=items,
                                document_type=doc_type,
                                layout_variant=layout_variant,
                                supplier_alias_values=match_keywords + [header.get("customerName"), header.get("invoiceNumber")],
                                base_config={
                                    "max_pages_pdf": int(pdf_pages_override),
                                    "preferred_local_engine": preferred_local,
                                    "vat_map": vat_map,
                                },
                            )

                            payload = {
                                "template_id": "",
                                "display_id": (display_id or "").strip(),
                                "supplier_name": (supplier_nm or "").strip(),
                                "parser_type": parser_type,
                                "vat_number": "",
                                "awrs_number": "",
                                "requires_cloud": 1 if requires_cloud else 0,
                                "match_keywords": match_keywords,
                                "config": template_config,
                            }

                            tpl_id, saved, notes = _db_upsert_supplier_template_checked(payload)
                            if saved:
                                _log_event(f"Admin created/updated supplier template {display_id or tpl_id} (from invoice)", file_name=file_name)
                                st.success(f"Template saved/updated: {display_id or tpl_id}")
                            else:
                                _log_event(f"Admin template save skipped (unchanged): {display_id or tpl_id}", file_name=file_name)
                                for n in (notes or ["Template unchanged — not saved."]):
                                    st.info(n)

            tab_line_items, tab_debug = st.tabs(["Line Items", "Debug"])
            with tab_line_items:

                # Prepare editable table
                if pd is not None:
                    df = pd.DataFrame(items)
                    # stable column order
                    cols = [
                        "lineNo",
                        "code",
                        "description",
                        "casePack",
                        "unitSize",
                        "qty",
                        "totalUnits",
                        "unitPrice",
                        "lineNet",
                        "vatCode",
                        "vatRate",
                        "vatAmount",
                        "lineGross",
                        "stdRrp",
                        "por",
                        "isVoid",
                    ]
                    for c in cols:
                        if c not in df.columns:
                            df[c] = ""
                    df = df[cols]

                    _edit_ver = int(st.session_state.get(f"edit_ver_{inv_key}", 0))
                    edited = st.data_editor(
                        df,
                         use_container_width=True,
                        num_rows="dynamic",
                        key=f"edit_{inv_key}_{_edit_ver}",
                    )

                    # Convert back to list of dicts
                    edited_items = edited.to_dict(orient="records")
                else:
                    st.info("pandas not available; showing read-only items")
                    edited_items = items
                    for it in items:
                        st.write(it)

                colA, colB, colC = st.columns([1, 1, 1])
                if colA.button("Apply edits", key=f"apply_{inv_key}"):
                    # Supplier-specific enforcement after manual edits
                    try:
                        _supp = str(header.get("supplierName") or "")
                        if "dhamecha" in _supp.lower():
                            # After user edits, ALWAYS respect the user's vatCode and force vatRate + vatAmount + gross.
                            _map = {"A": 20.0, "B": 5.0, "Z": 0.0}
                            for it in (edited_items or []):
                                if not isinstance(it, dict):
                                    continue
                                try:
                                    vc = str(it.get("vatCode") or "").strip().upper()
                                    if vc in _map:
                                        it["vatRate"] = float(_map[vc])
                                    else:
                                        it["vatRate"] = float(_normalise_vat_rate(it.get("vatRate")) or 0.0)

                                    line_net = _safe_float(it.get("lineNet"))
                                    vrn = _safe_float(it.get("vatRate"))
                                    vat_amt = 0.0 if abs(vrn) < 0.0001 else round(line_net * (vrn / 100.0), 2)
                                    it["vatAmount"] = vat_amt
                                    it["lineGross"] = round(line_net + vat_amt, 2)
                                except Exception:
                                    pass
                    except Exception as e:
                        _log_event(f"WARNING VAT enforcement after Apply edits failed: {e}", file_name=res.get("file_name",""))


                    edited_items = _recalc_items(edited_items)
                    res["items"] = edited_items
                    st.session_state["docmate_current_items"] = edited_items
                    try:
                        res["metrics"] = _calc_metrics(header, edited_items)
                    except Exception as e:
                        _log_event(f"ERROR recalculating metrics after Apply edits: {e}", file_name=res.get("file_name",""))
                    _log_event("Apply edits: items updated and recalculated", file_name=res.get("file_name",""))
                    st.success("Edits applied")
                    try:
                        st.session_state[f"edit_ver_{inv_key}"] = int(st.session_state.get(f"edit_ver_{inv_key}", 0)) + 1
                    except Exception:
                        pass
                    st.rerun()

                if colB.button("Save to Register", key=f"save_{inv_key}"):
                    try:
                        mode = _upsert_register(
                            inv_key=inv_key,
                            header=header,
                            items=edited_items,
                            doc_type=doc_type,
                            engine_used=res.get("engine") or "",
                            file_name=file_name,
                        )
                        if mode == "updated":
                            st.info(f"Entry already exists! Updated & re-saved — No duplicate entry enforced.")
                        else:
                            st.success(f"Saved to {REGISTER_LABEL.get(doc_type, 'Register')}")
                    except Exception as e:
                        st.error(f"Save failed: {e}")

                # Download CSV
                if pd is not None:
                    csv_bytes = pd.DataFrame(edited_items).to_csv(index=False).encode("utf-8")
                    colC.download_button(
                        "Download Items CSV",
                        data=csv_bytes,
                        file_name=f"{inv_key}_items.csv",
                        mime="text/csv",
                        key=f"csv_{inv_key}",
                    )
                    with tab_debug:
                        dbg = (res.get("debug_text") or res.get("text") or "")
                        st.code(dbg)
                    st.download_button("Download debug text", data=dbg.encode("utf-8"), file_name=f"{inv_key}_debug.txt", mime="text/plain", key=f"dbg_{inv_key}")



# -------------------------
# UI: Registers
# -------------------------

def render_dashboard(settings: Dict[str, Any]) -> None:
    _sync_from_legacy()
    st.header("Dashboard")
    if settings.get("force_ocr_pdf") and not _tesseract_path_ok(settings.get("tesseract_cmd", "")):
        st.error("Force OCR is enabled, but Tesseract path is missing/invalid. Go to Admin Settings → Local OCR Settings and set: C:\Program Files\Tesseract-OCR\tesseract.exe")
    st.caption("Process invoices and documents into DocMate registers. Output can be used for VBR EPOS import, Cloud Reporting checks, and supplier reconciliation.")

    

    # Customer selector (CMSID ↔ SPOS mapping) — feeds exports and downstream posting to VBR SPOS Cloud / local DB.
    cust_rows = _db_fetchall("SELECT cms_customer_id, spos_customer_id, customer_name, trading_name, company_name FROM customer_profiles WHERE TRIM(COALESCE(cms_customer_id,'')) <> '' ORDER BY cms_customer_id")
    cust_opts = ["(Not set)"]
    cust_map = {}
    for r in (cust_rows or []):
        cms = str(r.get("cms_customer_id") or "").strip()
        if not cms:
            # Skip legacy/invalid rows (blank CMSID) so they don't pollute the dashboard dropdown
            continue
        nm = str(r.get("customer_name") or "").strip()
        spos = str(r.get("spos_customer_id") or "").strip() or "N/A"
        label = f"{cms} — {nm}  [SPOS {spos}]".strip()
        if label in cust_map:
            # Ensure uniqueness in the dropdown (defensive)
            label = f"{label} (dup)"
        cust_opts.append(label)
        cust_map[label] = dict(r)

    pre_index = 0
    try:
        cms_login = (st.session_state.get("login_cms_customer_id") or "").strip().upper()
        if cms_login:
            for i, lab in enumerate(cust_opts):
                if str(lab).upper().startswith(cms_login):
                    pre_index = i
                    break
    except Exception:
        pre_index = 0

    sel_cust = st.selectbox("Customer (CMSID ↔ SPOS)", options=cust_opts, index=pre_index, key="dash_customer_sel")
    if sel_cust != "(Not set)":
        st.session_state["selected_customer_profile"] = cust_map.get(sel_cust) or {}
        # display 2-line summary as requested
        cp = st.session_state.get("selected_customer_profile") or {}
        st.caption(f"CMSID: {cp.get('cms_customer_id','')} - {cp.get('customer_name','')}  [SPOS CustomerID { (cp.get('spos_customer_id') or 'N/A') } – { (cp.get('trading_name') or cp.get('customer_name') or '') }]")
    else:
        st.session_state["selected_customer_profile"] = {}

    doc_type = st.selectbox("Document Type", options=DOC_TYPES, index=0)

    # Defaults used on reruns (e.g. after Save to Register / Template) to avoid UnboundLocalError
    doc_text_hint = st.session_state.get('doc_text_hint', '') or ''
    extracted_text = st.session_state.get('extracted_text', '') or ''

    # Engine selection
    is_admin = bool(st.session_state.get("is_admin"))
    # End users: Auto + Local only. Admins: Cloud appears ONLY when a key exists, and is shown first.
    engine_opts = [
        ("Auto (Templates → PDF text layer → OCR fallback)", "auto"),
        ("Local AI OCR (DocTR)", "local_doctr"),
        ("Local OCR (Tesseract)", "local_tesseract"),
    ]
    if is_admin:
        api_key_present = bool((_get_google_api_key(settings) or "").strip())
        if api_key_present:
            engine_opts = [
                ("Cloud AI (Google Gemini) — ACTIVE", "cloud_gemini"),
                ("Auto (Templates → PDF text layer → OCR fallback)", "auto"),
                ("Local AI OCR (DocTR)", "local_doctr"),
                ("Local OCR (Tesseract)", "local_tesseract"),
            ]
        else:
            st.info("Cloud AI is hidden until a Google API key is added in Admin Settings → Business API Licence.")

    default_engine = settings.get("default_engine", "auto")
    keys = [k for _, k in engine_opts]
    if default_engine not in keys:
        default_engine = "auto"

    selected_engine = default_engine
    if settings.get("allow_user_choose_engine", True):
        labels = [l for l, _ in engine_opts]
        label_map = {l: k for l, k in engine_opts}
        default_label = [l for l, k in engine_opts if k == default_engine][0]
        chosen = st.selectbox("Processing Engine", options=labels, index=labels.index(default_label))
        selected_engine = label_map[chosen]
    else:
        st.info(f"Engine locked by Admin: {default_engine}")

    uploaded = []

    uploaded = st.file_uploader(
        "Upload PDF(s) / image(s)",
        type=["pdf", "png", "jpg", "jpeg"],
        accept_multiple_files=True,
    )

    if "_dm_processing_docs" not in st.session_state:
        st.session_state["_dm_processing_docs"] = False

    clicked = st.button(
        "Process Document(s)",
        disabled=(st.session_state.get("_dm_processing_docs", False) or not (uploaded and len(uploaded) > 0)),
        key="dm_process_docs_btn",
    )
    if clicked and not st.session_state.get("_dm_processing_docs", False):
        st.session_state["_dm_processing_docs"] = True
        try:
            results = []
            total_files = len(uploaded or [])
            status = st.empty()
            prog = st.progress(0.0)

            with st.spinner("Processing document(s)…"):
                for idx, up in enumerate(uploaded or []):
                    file_name = up.name
                    def _page_progress(page_no: int, total_pages: int, label: str = file_name):
                        # Live status during processing (Page N/Total) — useful for multi-page Parfetts/Booker PDFs
                        try:
                            status.info(f"Processing {idx+1}/{total_files}: {label} — Page {page_no} of {total_pages}")
                        except Exception:
                            pass

                    file_bytes = up.getvalue()
                    mime = up.type or _guess_mime_type(up.name)
                    pdf_page_count = None
                    if mime == "application/pdf":
                        try:
                            pdf_page_count = _pdf_page_count(file_bytes)
                            if (pdf_page_count or 0) > 10:
                                st.warning("Warning - Doc. more than 10 pages, contact Support Desk")
                                _log_event(f"WARNING: document has {pdf_page_count} pages (>10). Processing will be limited by Max pages setting.", file_name=file_name)
                        except Exception as e:
                            _log_event(f"ERROR reading PDF page count: {e}", file_name=file_name)


                    status.info(f"Processing {idx+1}/{total_files}: {file_name}")
                    engine = selected_engine
                    _log_event(f"START engine={engine} mime={mime} docType={doc_type} pdfPages={pdf_page_count or ''}", file_name=file_name)

                    # Ensure vat_map always defined (used by preview enrichment)
                    vat_map: Dict[str, Any] = {}

                    try:
                        # Auto: match template using fast PDF text layer
                        doc_text_hint = ""
                        effective_pages_hint = None
                        if mime == "application/pdf":
                            # First take a small preview, detect supplier, then re-read with supplier-aware page limit.
                            try:
                                preview_pages = min(2, int(pdf_page_count or 2))
                            except Exception:
                                preview_pages = 2
                            preview_text = _pdf_text_layer(file_bytes, max_pages=preview_pages, progress_cb=_page_progress, progress_label=file_name)
                            effective_pages_hint = _effective_max_pages(
                                settings,
                                supplier_hint=((preview_text or "") + "\n" + (file_name or "")),
                                file_bytes=file_bytes,
                                mime=mime,
                                default_pages=int(settings.get("ocr_max_pages", 10) or 10),
                            )
                            if effective_pages_hint and int(effective_pages_hint) > preview_pages:
                                doc_text_hint = _pdf_text_layer(file_bytes, max_pages=int(effective_pages_hint), progress_cb=_page_progress, progress_label=file_name)
                            else:
                                doc_text_hint = preview_text

                        # Include filename for matching (helps when PDF text layer is weak)
                        doc_text_for_match = ((doc_text_hint or "") + "\n" + (file_name or "")).strip()

                        api_key_present = bool((_get_google_api_key(settings) or "").strip()) if is_admin else False

                        # Auto engine: use stored template if possible; else choose best default
                        template_hit = None
                        if engine == "auto" and doc_text_for_match and settings.get("auto_mode_enabled", True):
                            party_table = _decide_party_table(doc_type)
                            if party_table == "supplier_templates":
                                template_hit = _db_find_best_template(doc_text_for_match, table="supplier_templates")
                                if not template_hit:
                                    template_hit = _auto_template_for_booker_if_needed(settings, doc_text_for_match)
                                if (not template_hit) and _looks_like_booker_invoice(doc_text_for_match):
                                    template_hit = _db_find_booker_template_latest()
                                # Extra defensive fallback: pick latest BOOKER parser template
                                if not template_hit:
                                    template_hit = _db_find_template_latest_by_parser_type('BOOKER', table='supplier_templates')

                            if template_hit:
                                cfg_t = template_hit.get("config") or {}
                                requires_cloud = bool(cfg_t.get("requires_cloud", False)) or bool(int(template_hit.get("requires_cloud") or 0)) or bool(cfg_t.get("preferred_cloud_engine"))
                                # Supplier preference: DHAMECHA is Cloud-first in AUTO when Cloud is available
                                try:
                                    if selected_engine == "auto" and str(template_hit.get("parser_type") or "").upper().strip() == "DHAMECHA":
                                        requires_cloud = True
                                except Exception:
                                    pass
                                # Booker default: do NOT force Cloud in AUTO unless explicitly configured
                                try:
                                    if str(template_hit.get("parser_type") or "").upper() == "BOOKER" and not bool(cfg_t.get("force_cloud", False)):
                                        requires_cloud = False
                                except Exception:
                                    pass

                                pref_local = cfg_t.get("preferred_local_engine", "tesseract")
                                if requires_cloud and api_key_present and is_admin:
                                    engine = "cloud_gemini"
                                else:
                                    engine = f"local_{pref_local}" if pref_local else "local_tesseract"
                            else:
                                # No supplier template.
                                # If a Cloud API key is configured (Admin), prefer Cloud for first-time suppliers,
                                # but keep Booker-like invoices local (Booker templates/parsers are stable).
                                if api_key_present and is_admin:
                                    try:
                                        if _looks_like_booker_invoice(doc_text_for_match):
                                            engine = "local_tesseract"
                                        else:
                                            engine = "cloud_gemini"
                                    except Exception:
                                        engine = "cloud_gemini"
                                else:
                                    engine = "local_tesseract"

                            # VAT mapping for preview enrichment (template-specific if available)
                            vat_map = {}
                            try:
                                if template_hit:
                                    _vm = (cfg_t or {}).get("vat_map") or {}
                                    if isinstance(_vm, str):
                                        try:
                                            _vm = json.loads(_vm)
                                        except Exception:
                                            _vm = {}
                                    if isinstance(_vm, dict):
                                        vat_map = {str(k).strip().upper(): float(v) for k, v in _vm.items() if str(k).strip()}
                            except Exception:
                                vat_map = {}
                            if not vat_map:
                                try:
                                    vat_map = dict(BookerCombinedRowParser.DEFAULT_VAT_MAP)
                                except Exception:
                                    vat_map = {"B": 20.0, "A": 0.0, "Z": 0.0, "R": 5.0, "S": 20.0, "S/A": 20.0, "Z/A": 0.0}
                        # If a Booker template exists but keyword matching did not hit (or legacy DB),
                        # attach latest BOOKER template for enrichment/logging when the document clearly looks like Booker.
                        if (template_hit is None) and _looks_like_booker_invoice(doc_text_for_match):
                            template_hit = _db_find_booker_template_latest() or _db_find_template_latest_by_parser_type("BOOKER", table="supplier_templates")


                        # Record resolved engine after AUTO/template routing (for audit)
                        try:
                            _tpl = template_hit or {}
                            _tpl_id = (_tpl.get("display_id") or _tpl.get("template_id") or "").strip()
                            if engine == "auto":
                                _log_event(f"RESOLVED engine={engine} template={_tpl_id or 'none'}", file_name=file_name)
                            else:
                                _log_event(f"RESOLVED engine={engine} template={_tpl_id or 'none'}", file_name=file_name)
                        except Exception:
                            pass

# Cloud (admin-only, key-required)
                        if engine == "cloud_gemini":
                            if not is_admin:
                                raise RuntimeError("Cloud AI is admin-only")
                            api_key = _get_google_api_key(settings)
                            if not api_key:
                                raise RuntimeError("Google API key not set in Admin settings")
                            extractor = CloudGeminiExtractor(api_key=api_key, model_name=settings.get("gemini_model", "gemini-2.0-flash"))

                            extracted_text = doc_text_hint or ""
                            doc_text_up = (extracted_text or "").upper()
                            is_booker = (doc_type == "Purchase Invoice") and (("BOOKER" in doc_text_up) or ("BOOKER" in (file_name or "").upper()))
                            is_dwg = (doc_type == "Purchase Invoice") and (("DRINKS WHOLESALE" in doc_text_up) or ("DRINKS WHOLESALE GROUP" in doc_text_up) or ("DWG" in doc_text_up) or ("DWG" in (file_name or "").upper()))
                            is_parfetts = (doc_type == "Purchase Invoice") and (("PARFETTS" in doc_text_up) or ("PARFETTS" in (file_name or "").upper()))

                            is_dwg = (doc_type == "Purchase Invoice") and (("DWG" in doc_text_up) or ("DRINKS WHOLESALE" in doc_text_up) or ("DWG" in (file_name or "").upper()) or ("DRINKS" in (file_name or "").upper()))

                            # For DWG (and other scanned PDFs), ensure we have OCR text available for debug + header completion.
                            # DWG invoices often have no PDF text layer; without this, debug text shows blank pages.
                            if is_dwg and mime == "application/pdf":
                                need_hdr = (len(extracted_text or "") < 200)
                                if need_hdr:
                                    cfg = OcrConfig(
                                        engine="tesseract",
                                        tesseract_cmd=str(settings.get("tesseract_cmd", "") or ""),
                                        dpi=int(settings.get("ocr_dpi", 300) or 300),
                                        max_pages=_effective_max_pages(
                                            settings,
                                            supplier_hint=((file_name or "") + "\nDWG"),
                                            file_bytes=file_bytes,
                                            mime=mime,
                                            default_pages=int(settings.get("ocr_max_pages", 4) or 4),
                                        ),
                                        force_pdf=True,
                                        auto_install=bool(settings.get("auto_install_ocr", True)),
                                    )
                                    cache_key = _sha256_bytes(file_bytes) + f"|{cfg.engine}|{cfg.dpi}|{cfg.max_pages}|{cfg.force_pdf}"
                                    extracted_text = ocr_text_cached(
                                        cache_key, mime, dataclasses.asdict(cfg), file_bytes,
                                        progress_cb=_page_progress, progress_label=file_name
                                    )

                            # For Parfetts, we must have header text (Customer + Date of Invoice) available for deterministic header fill.
                            # On some Windows installs, the PDF text layer may be unavailable (PyMuPDF not installed), so doc_text_hint can be empty.
                            # In that case, trigger OCR (supplier-aware max pages) before calling Cloud AI, so the Parfetts header/footer extractor can fill blanks.
                            if is_parfetts and mime == "application/pdf":
                                need_hdr = (len(extracted_text or "") < 500) or (re.search(r"\(CR\)", extracted_text or "", flags=re.I) is None) or (
                                    re.search(r"DATE\s+OF\s+INVOICE|DATE\s+PRINTED", extracted_text or "", flags=re.I) is None
                                )
                                if need_hdr:
                                    cfg = OcrConfig(
                                        engine="tesseract",
                                        tesseract_cmd=str(settings.get("tesseract_cmd", "") or ""),
                                        dpi=int(settings.get("ocr_dpi", 300) or 300),
                                        max_pages=_effective_max_pages(
                                            settings,
                                            supplier_hint=((file_name or "") + "\nPARFETTS"),
                                            file_bytes=file_bytes,
                                            mime=mime,
                                            default_pages=int(settings.get("parfetts_max_pages", 6) or 6),
                                        ),
                                        force_pdf=True,
                                        auto_install=bool(settings.get("auto_install_ocr", True)),
                                    )
                                    cache_key = _sha256_bytes(file_bytes) + f"|{cfg.engine}|{cfg.dpi}|{cfg.max_pages}|{cfg.force_pdf}"
                                    extracted_text = ocr_text_cached(
                                        cache_key, mime, dataclasses.asdict(cfg), file_bytes,
                                        progress_cb=_page_progress, progress_label=file_name
                                    )

                            # For Booker, ensure we have strong evidence text (force OCR when needed)
                            if is_booker and mime == "application/pdf":
                                local_try = LocalInvoiceParser.parse(extracted_text, doc_type=doc_type)
                                local_try_items = local_try.get("items", []) or []
                                hint_code_lines = len(re.findall(r"(?m)^\s*\d{5,7}\b", extracted_text or ""))
                                m_total = re.search(r"TOTAL\s+ITEMS\s*:\s*(\d+)", extracted_text or "", flags=re.I)
                                expected_total = int(m_total.group(1)) if m_total else 0
                                incomplete = (expected_total > 0 and len(local_try_items) < expected_total) or (expected_total == 0 and hint_code_lines < 10)

                                if incomplete or (len(extracted_text) < 2500) or (not local_try_items):
                                    cfg = OcrConfig(
                                        engine="tesseract",
                                        tesseract_cmd=str(settings.get("tesseract_cmd", "") or ""),
                                        dpi=int(settings.get("ocr_dpi", 300) or 300),
                                        max_pages=_effective_max_pages(settings, supplier_hint=doc_text_for_match, file_bytes=file_bytes, mime=mime, default_pages=int(settings.get("ocr_max_pages", 10) or 10)),
                                        force_pdf=True,
                                        auto_install=bool(settings.get("auto_install_ocr", True)),
                                    )
                                    cache_key = _sha256_bytes(file_bytes) + f"|{cfg.engine}|{cfg.dpi}|{cfg.max_pages}|{cfg.force_pdf}"
                                    extracted_text = ocr_text_cached(cache_key, mime, dataclasses.asdict(cfg), file_bytes, progress_cb=_page_progress, progress_label=file_name)

                            data = extractor.extract_purchase_invoice(file_bytes, mime, file_name, doc_text=extracted_text)
                            cloud_header = data.get("header", {}) or {}
                            cloud_items = data.get("items", []) or []

                            # Deterministic Booker parse for parity
                            if (doc_type == "Purchase Invoice") and ("BOOKER" in str(cloud_header.get("supplierName", "")).upper() or "BOOKER" in (doc_text_hint or "").upper() or "BOOKER" in (file_name or "").upper()):
                                local_data = LocalInvoiceParser.parse(extracted_text, doc_type=doc_type)
                                header = local_data.get("header", {}) or {}
                                items = local_data.get("items", []) or []

                                # Fill missing header from Cloud (treat OCR page markers as blank supplier)
                                for k in ("supplierName", "customerName", "invoiceNumber", "invoiceDate", "currency"):
                                    hv = str(header.get(k) or "").strip()
                                    cv = str(cloud_header.get(k) or "").strip()
                                    if k == "supplierName" and _looks_like_page_marker(hv):
                                        hv = ""
                                    if (not hv) and cv:
                                        header[k] = cloud_header.get(k)
                                for k in ("totalNet", "totalVat", "totalGross"):
                                    if float(header.get(k) or 0.0) <= 0.0 and float(cloud_header.get(k) or 0.0) > 0.0:
                                        header[k] = float(cloud_header.get(k) or 0.0)
                                if float(header.get("totalGross") or 0.0) <= 0.0 and float(header.get("totalNet") or 0.0) > 0.0:
                                    header["totalGross"] = round(float(header["totalNet"]) + float(header.get("totalVat") or 0.0), 2)

                                if not items and cloud_items:
                                    header = cloud_header or header
                                    items = cloud_items
                            # Deterministic Parfetts parse for parity (and to avoid LLM column drift)
                            elif (doc_type == "Purchase Invoice") and ("PARFETTS" in str(cloud_header.get("supplierName", "")).upper() or is_parfetts):
                                local_data = LocalInvoiceParser.parse(extracted_text, doc_type)
                                local_header = local_data.get("header") or {}
                                local_items = local_data.get("items") or []

                                # If local parse found items, prefer it; otherwise fall back to cloud output.
                                if local_items:
                                    header = local_header
                                    items = local_items
                                else:
                                    header = cloud_header
                                    items = cloud_items

                                # Fill missing header fields from local OCR parse when cloud is blank
                                # (Parfetts often has customer + invoice date in the OCR header block, but Cloud may omit it.)
                                for key in ["customerName", "invoiceDate"]:
                                    if not str(header.get(key, "")).strip() and str(local_header.get(key, "")).strip():
                                        header[key] = local_header.get(key)

                                # VAT breakdown: prefer extracted-from-invoice breakdown (Parfetts A/Z), even in Cloud mode
                                if (not header.get("vatBreakdown")) and local_header.get("vatBreakdown"):
                                    header["vatBreakdown"] = local_header.get("vatBreakdown")

                                # Fill missing header fields from cloud when local is blank
                                for key in ["supplierName", "customerName", "invoiceNumber", "invoiceDate", "currency"]:
                                    if not str(header.get(key, "")).strip() and str(cloud_header.get(key, "")).strip():
                                        header[key] = cloud_header.get(key)
                                # Fill totals if missing (keep Parfetts footer totals when present)
                                try:
                                    net_items = round(sum(_safe_float(it.get("lineNet")) for it in (items or [])), 2)
                                    vat_items = round(sum(_safe_float(it.get("vatAmount")) for it in (items or [])), 2)
                                    gross_items = round(net_items + vat_items, 2)
                                    if _safe_float(header.get("totalNet")) <= 0 and net_items > 0:
                                        header["totalNet"] = net_items
                                    if _safe_float(header.get("totalVat")) <= 0 and vat_items > 0:
                                        header["totalVat"] = vat_items
                                    if _safe_float(header.get("totalGross")) <= 0 and gross_items > 0:
                                        header["totalGross"] = gross_items

                                    # Enforce Parfetts footer totals (prevents rounding drift vs invoice)
                                    foot = _parfetts_extract_header(extracted_text or "")
                                    for k in ("totalNet", "totalVat", "totalGross"):
                                        if _safe_float(foot.get(k)) > 0:
                                            header[k] = _safe_float(foot.get(k))

                                    # Fill Parfetts header fields (keeps totals aligned to invoice footer)
                                    try:
                                        for kk in ("supplierName", "customerName", "invoiceNumber", "invoiceDate", "currency", "deliveryCharge"):
                                            if (not (header.get(kk) or "").strip()) and (foot.get(kk) is not None):
                                                header[kk] = foot.get(kk)
                                        if foot.get("vatBreakdown"):
                                            header["vatBreakdown"] = foot.get("vatBreakdown")
                                    except Exception:
                                        pass

                                    # Parfetts: last-chance fill of customer & invoice date (OCR/PDF + filename)
                                    try:
                                        if not (header.get("invoiceDate") or ""):
                                            fn_dt = _date_from_filename(file_name or "")
                                            if fn_dt:
                                                header["invoiceDate"] = fn_dt
                                        if not (header.get("customerName") or "") or not (header.get("invoiceDate") or ""):
                                            cust2, dt2 = _parfetts_autofill_customer_and_date(extracted_text or "")
                                            if cust2 and not (header.get("customerName") or ""):
                                                header["customerName"] = cust2
                                            if dt2 and not (header.get("invoiceDate") or ""):
                                                header["invoiceDate"] = dt2
                                    except Exception:
                                        pass

                                except Exception:
                                    pass

                            else:
                                header = cloud_header
                                items = cloud_items
                                _dwg_enrich_header_items(header, items, supplier_hint=doc_text_hint)
                                # Dhamecha enrichment
                                if _is_supplier_like(header.get('supplierName') or '', ['dhamecha']):
                                    _dhamecha_enrich_header_items(header, items, doc_text_hint=doc_text_hint)

                            extracted_text = extracted_text or doc_text_hint

                        else:
                            # Local path
                            extracted_text = None
                            is_booker = (doc_type == "Purchase Invoice") and (("booker" in (file_name or "").lower()) or ("BOOKER" in (doc_text_hint or "").upper()))
                            is_parfetts = (doc_type == "Purchase Invoice") and (("parfetts" in (file_name or "").lower()) or ("PARFETTS" in (doc_text_hint or "").upper()))
                            is_dwg = (doc_type == "Purchase Invoice") and (("DWG" in (doc_text_hint or "").upper()) or ("DRINKS WHOLESALE" in (doc_text_hint or "").upper()) or ("DRINKS WHOLESALE GROUP" in (doc_text_hint or "").upper()) or ("DWG" in (file_name or "").upper()) or ("DRINKS WHOLESALE" in (file_name or "").upper()))
                            explicit_local = (selected_engine == "local_tesseract")
                            local_engine = engine.replace("local_", "")
                            force_pdf = bool(settings.get("force_ocr_pdf", False))

                            # Booker PDFs are often scans → OCR helps. Parfetts PDFs usually have a strong text layer.
                            if mime == "application/pdf":
                                # Prefer PDF text layer when it looks strong, but force OCR for known scan-heavy suppliers.
                                hint = (doc_text_hint or "")

                                is_dhamecha = ("DHAMECHA" in hint.upper()) or ("DHAMECHA" in (file_name or "").upper()) or ("927135230" in hint) or ("XRAW00000102826" in hint) or ("TOTAL GOODS" in hint.upper())

                                if is_booker:
                                    # Booker PDFs are often scans (barcode/line layouts) → OCR is safer.
                                    force_pdf = True

                                elif is_dhamecha:
                                    # Dhamecha PDFs often have a usable text layer; OCR can latch onto the legal disclaimer.
                                    force_pdf = False

                                elif is_parfetts:
                                    # Parfetts PDFs usually have a strong text layer; only force OCR if it looks weak.
                                    hint_code_lines = len(re.findall(r"(?m)^\s*\d{5,7}\b", hint))
                                    if len(hint.strip()) < 500 or hint_code_lines < 10:
                                        force_pdf = True
                                    else:
                                        force_pdf = False

                                elif is_dhamecha:
                                    # Dhamecha PDFs have a strong text layer; prefer text layer to avoid OCR disclaimer noise.
                                    # Only force OCR if the text layer is extremely short.
                                    if len(hint.strip()) < 300:
                                        force_pdf = True
                                    else:
                                        force_pdf = False

                                else:
                                    # Generic: only force OCR when the PDF text layer is weak (short or lacks item-code structure).
                                    hint_code_lines = len(re.findall(r"(?m)^\s*\d{5,7}\b", hint))
                                    if len(hint.strip()) < 350 or hint_code_lines < 3:
                                        force_pdf = True
                                    else:
                                        force_pdf = False

                            cfg = OcrConfig(
                                engine=local_engine,
                                tesseract_cmd=str(settings.get("tesseract_cmd", "") or ""),
                                dpi=int(settings.get("ocr_dpi", 250)),
                                max_pages=_effective_max_pages(settings, supplier_hint=(doc_text_hint or "") + "\n" + (file_name or ""), file_bytes=file_bytes, mime=mime, default_pages=int(settings.get("ocr_max_pages", 10) or 10)),
                                force_pdf=force_pdf,
                                auto_install=bool(settings.get("auto_install_ocr", True)),
                            )

                            if mime == "application/pdf" and not cfg.force_pdf and (doc_text_hint or "").strip():
                                extracted_text = doc_text_hint

                            cache_key = _cache_key_for_ocr(file_bytes, mime=mime, engine=cfg.engine, dpi=cfg.dpi, max_pages=cfg.max_pages, force_pdf=cfg.force_pdf)
                            if extracted_text is None or len((extracted_text or "").strip()) < 120:
                                ocr_txt = ocr_text_cached(cache_key, mime, dataclasses.asdict(cfg), file_bytes, progress_cb=_page_progress, progress_label=file_name)
                                # If we already have any PDF text-layer content, keep it alongside OCR to improve header/footer detection (Parfetts VAT box, etc.)
                                if (doc_text_hint or "").strip():
                                    extracted_text = (doc_text_hint.strip() + "\n\n" + (ocr_txt or "").strip())
                                else:
                                    extracted_text = ocr_txt

                            data = LocalInvoiceParser.parse(extracted_text, doc_type)
                            header = data.get("header") or {}
                            items = data.get("items") or []

                            # Claude Vision fallback: for image files where OCR produces poor/garbled descriptions,
                            # send the raw image bytes to Claude Vision for accurate extraction (bypasses OCR entirely).
                            try:
                                _is_image_file = mime.startswith("image/")
                                _desc_poor = (
                                    not items or
                                    sum(1 for _it in items if len(str(_it.get("description") or "").strip()) < 4) > len(items) * 0.5
                                )
                                if _is_image_file and _desc_poor:
                                    _claude_key = _get_claude_api_key(settings)
                                    if _claude_key:
                                        _vision = ClaudeAIFallbackParser(_claude_key, settings.get("claude_model", "claude-sonnet-4-6"))
                                        _vresult = _vision.extract_from_image(file_bytes, mime, doc_type)
                                        if _vresult and (_vresult.get("items") or []):
                                            items = _vresult.get("items") or items
                                            _vh = _vresult.get("header") or {}
                                            for _k in ("supplierName", "customerName", "invoiceNumber", "invoiceDate", "currency"):
                                                if _vh.get(_k):
                                                    header[_k] = _vh[_k]
                                            for _k in ("totalNet", "totalVat", "totalGross"):
                                                if float(_vh.get(_k) or 0.0) > 0.0 and float(header.get(_k) or 0.0) <= 0.0:
                                                    header[_k] = float(_vh[_k])
                                            _log_event("Claude Vision used for image invoice extraction", file_name=file_name)
                            except Exception as _ve:
                                _log_event(f"Claude Vision fallback error: {_ve}", file_name=file_name)

                            # AUTO safety: if a template routed to Local for a supplier but Local parsing produced no items,
                            # retry with Cloud AI when available (prevents Dhamecha/DWG from returning empty item lists).
                            try:
                                if (selected_engine == "auto") and (template_hit is not None) and is_admin and api_key_present:
                                    ptype = str((template_hit or {}).get("parser_type") or "").upper().strip()
                                    if ptype in ("DHAMECHA", "DWG") and (len(items) == 0 or not str(header.get("invoiceNumber") or "").strip()):
                                        _log_event(f"AUTO fallback: Local parse incomplete for {ptype}; retrying with Cloud AI.", file_name=file_name)
                                        api_key = _get_google_api_key(settings)
                                        if api_key:
                                            cext = CloudGeminiExtractor(api_key=api_key, model_name=settings.get("gemini_model", "gemini-2.0-flash"))
                                            cdata = cext.extract_purchase_invoice(file_bytes, mime, file_name=file_name, doc_text=doc_text_hint or "")
                                            cheader = (cdata or {}).get("header") or {}
                                            citems = (cdata or {}).get("items") or []
                                            if citems:
                                                header, items = cheader, citems
                                                engine = "cloud_gemini"
                            except Exception:
                                pass
                            # Booker customer autofill (AUTO/Local/Cloud): ensure customerName is captured, cleaned, and (if needed) learned from template
                            try:
                                if doc_type == "Purchase Invoice" and (("BOOKER" in (extracted_text or "").upper()) or ("BOOKER" in (file_name or "").upper())):
                                    _booker_autofill_customer_name(header, extracted_text, template_hit)
                            except Exception:
                                pass

# Default VAT mapping for Booker when no template map exists
                            try:
                                if (not vat_map) and doc_type == "Purchase Invoice" and ("BOOKER" in (extracted_text or "").upper() or "BOOKER" in (file_name or "").upper()):
                                    vat_map = dict(BookerCombinedRowParser.DEFAULT_VAT_MAP)
                            except Exception:
                                pass
                            items = _preview_enrich_items(items, vat_map)

                            # Auto fallback guidance (non-admin): suggest Cloud key if extraction incomplete
                            if selected_engine == "auto" and (not items or float(header.get("totalGross") or 0.0) <= 0.0):
                                if not is_admin or not api_key_present:
                                    st.warning("Auto extraction looks incomplete. Try Local OCR, or ask an admin to add a Cloud API key for difficult invoices.")
                                else:
                                    # admin: attempt a single Cloud assist if Booker and clearly incomplete
                                    if is_booker or is_dwg:
                                        try:
                                            extractor2 = CloudGeminiExtractor(api_key=_get_google_api_key(settings), model_name=settings.get("gemini_model", "gemini-2.0-flash"))
                                            cloud = extractor2.extract_purchase_invoice(file_bytes, mime, file_name, doc_text=extracted_text)
                                            cloud_header = cloud.get("header", {}) or {}
                                            cloud_items = cloud.get("items", []) or []
                                            if "BOOKER" in str(cloud_header.get("supplierName", "")).upper():
                                                local_data = LocalInvoiceParser.parse(extracted_text, doc_type=doc_type)
                                                header = local_data.get("header", {}) or cloud_header
                                                items = local_data.get("items", []) or cloud_items
                                                for k in ("totalNet", "totalVat", "totalGross"):
                                                    if float(header.get(k) or 0.0) <= 0.0 and float(cloud_header.get(k) or 0.0) > 0.0:
                                                        header[k] = float(cloud_header.get(k) or 0.0)
                                                if float(header.get("totalGross") or 0.0) <= 0.0 and float(header.get("totalNet") or 0.0) > 0.0:
                                                    header["totalGross"] = round(float(header["totalNet"]) + float(header.get("totalVat") or 0.0), 2)
                                            else:
                                                header = cloud_header or header
                                                items = cloud_items or items
                                            engine = "cloud_gemini"
                                        except Exception:
                                            pass
                    
                        
                        # Booker customer autofill (all engines): if missing, derive from text or template default
                        try:
                            if doc_type == "Purchase Invoice":
                                sup_u = str((header or {}).get("supplierName") or "").upper()
                                if ("BOOKER" in sup_u) or _looks_like_booker_invoice(extracted_text or doc_text_for_match or ""):
                                    _booker_autofill_customer_name(header, extracted_text or doc_text_for_match or "", template_hit)
                        except Exception:
                            pass

                        # Make the reconciled native PDF extraction the final
                        # source of truth for TNS in the active dashboard path.
                        header, items, _tns_used = _tns_authoritative_result(
                            doc_type, doc_text_hint or extracted_text or "", header, items
                        )
                        if _tns_used:
                            engine = "local_tns"
                            _log_event(
                                f"TNS authoritative parser applied: items={len(items)} "
                                f"net={sum(float(row.get('lineNet') or 0.0) for row in items):.2f}",
                                file_name=file_name,
                            )

# Normalise invoice date (store/display as date only)
                        try:
                            header['invoiceDate'] = _normalise_invoice_date_str(header.get('invoiceDate') or '')
                        except Exception:
                            pass
                        _log_event(f"DONE engine={engine} supplier={header.get('supplierName','')} inv={header.get('invoiceNumber','')} items={len(items)} net={header.get('totalNet',0)} vat={header.get('totalVat',0)} gross={header.get('totalGross',0)}", file_name=file_name)
                        inv_key = _make_inv_key(header, file_bytes, doc_type)
                        results.append(
                            {
                                "file": file_name,
                                "mime": mime,
                                "engine": engine,
                                "doc_type": doc_type,
                                "header": header,
                                "header_raw": copy.deepcopy(header),
                                "items": items,
                                "inv_key": inv_key,
                                "text": extracted_text,
                                "debug_text": extracted_text,
                                "template_used": (template_hit.get("display_id") or template_hit.get("template_id")) if template_hit else "",
                                "template_parser_type": (template_hit.get("parser_type") if template_hit else ""),
                            }
                        )

                    except Exception as e:
                        import traceback
                        tb = traceback.format_exc()
                        _log_event(f"Processing error: {e}\n{tb}", file_name=file_name)
                        results.append({"file": file_name, "error": str(e), "engine": engine, "doc_type": doc_type})

                    prog.progress(min(1.0, float(idx + 1) / float(max(total_files, 1))))

            status.success("Processing complete.")
            prog.progress(1.0)
            st.session_state["last_batch"] = results

            st.session_state["last_batch_ts"] = time.time()

        finally:
            st.session_state["_dm_processing_docs"] = False

    batch = st.session_state.get("last_batch") or []
    if not batch:
        st.info("Upload document(s) and click **Process Document(s)**.")
        return

    st.divider()
    st.subheader("Results")

    for res in batch:
        file_name = res.get("file")
        if res.get("error"):
            st.error(f"{file_name} — {res.get('error')}")
            continue

        header = res.get("header") or {}
        items = res.get("items") or []
        # Ensure derived/calculated fields are present for UI + VAT breakdown
        items = _recalc_items(items)
        res["items"] = items
        # Parfetts: ensure key header fields + VAT breakdown are present even when OCR/text-layer is imperfect.
        try:
            tag = ((header.get("supplierName") or "") + " " + (file_name or "")).lower()
            if "parfetts" in tag:
                dbg = (res.get("debug_text") or res.get("text") or "") or ""
                # Fill missing header fields from the full captured text
                if dbg and (not header.get("customerName") or not header.get("invoiceDate") or not header.get("invoiceNumber") or not header.get("vatBreakdown")):
                    foot = _parfetts_extract_header(dbg) or {}
                    for kk in ("supplierName", "customerName", "invoiceDate", "invoiceNumber", "deliveryCharge", "vatBreakdown", "totalNet", "totalVat", "totalGross"):
                        if (not header.get(kk)) and foot.get(kk) not in (None, ""):
                            header[kk] = foot.get(kk)

                # If VAT breakdown still missing, calculate it from line items (vatCode + vatRate) and reconcile totals
                if (not header.get("vatBreakdown")) and items:
                    calc = _parfetts_vat_breakdown_from_items(items) or {}
                    rows = calc.get("rows") or []
                    if rows:
                        header["vatBreakdown"] = rows
                        dc = _safe_float(header.get("deliveryCharge"))
                        if abs(_safe_float(header.get("totalNet")) - _safe_float(calc.get("totalNet"))) > 0.05:
                            header["totalNet"] = _safe_float(calc.get("totalNet"))
                        if abs(_safe_float(header.get("totalVat")) - _safe_float(calc.get("totalVat"))) > 0.05:
                            header["totalVat"] = _safe_float(calc.get("totalVat"))
                        header["totalGross"] = round(_safe_float(calc.get("totalNet")) + _safe_float(calc.get("totalVat")) + dc, 2)

                res["header"] = header
        except Exception:
            pass

        inv_key = res.get("inv_key")
        doc_type = res.get("doc_type")

        with st.expander(f"{file_name} — {doc_type}", expanded=True):
            # Header summary + VAT breakdown + Template tools (Admin)
            top_left, top_right = st.columns([3, 1])

            with top_left:
                # Compact KPI header (better for quick validation before posting into VBR EPOS / Cloud Reporting)
                metrics = _calc_metrics(header, items)
                supplier_v = header.get("supplierName") or "—"
                customer_v = header.get("customerName") or "—"
                inv_date_v = header.get("invoiceDate") or "—"
                inv_no_v = header.get("invoiceNumber") or "—"

                # CSS once (safe to call repeatedly inside expander)
                st.markdown("""<style>
                .dm-row{display:flex;gap:10px;flex-wrap:wrap;margin:6px 0;}
                .dm-card{flex:1;min-width:160px;border:1px solid var(--dm-border);border-radius:14px;padding:10px 12px;background:var(--dm-card);color:var(--dm-text);}
                .dm-card h4{margin:0;font-size:12px;color:var(--dm-muted);font-weight:700;}
                .dm-card .v{margin-top:2px;font-size:var(--dm-kpi-font);color:var(--dm-text);font-weight:900;line-height:1.15;}
                .dm-card.yellow{background:#fff7cc;}
                .dm-card.yellow .v{font-size:var(--dm-header-font);}
                .dm-card.blue{background:#e8f2ff;}
                .dm-card.green{background:#eaf7ea;}
                </style>""", unsafe_allow_html=True)

                st.markdown(
                    f"""<div class='dm-row'>
                        <div class='dm-card'><h4>Supplier</h4><div class='v'>{supplier_v}</div></div>
                        <div class='dm-card'><h4>Customer</h4><div class='v'>{customer_v}</div></div>
                        <div class='dm-card'><h4>Invoice Date</h4><div class='v'>{inv_date_v}</div></div>
                        <div class='dm-card'><h4>Invoice Number</h4><div class='v'>{inv_no_v}</div></div>
                    </div>
                    <div class='dm-row'>
                        <div class='dm-card blue'><h4>No. of Cases (Σ Qty)</h4><div class='v'>{_fmt_int(metrics.get('total_case_qty',0))}</div></div>
                        <div class='dm-card blue'><h4>No. of Stock Units</h4><div class='v'>{_fmt_int(metrics.get('total_units',0))}</div></div>
                        <div class='dm-card blue'><h4>No. of Products</h4><div class='v'>{int(metrics.get('products_count',0) or 0)}</div></div>
                        <div class='dm-card blue'><h4>Average POR (%)</h4><div class='v'>{_safe_float(metrics.get('avg_por',0) or 0):,.1f}</div></div>
                    </div>
                    <div class='dm-row'>
                        <div class='dm-card yellow'><h4>Net</h4><div class='v'>{_money(header.get('totalNet'))}</div></div>
                        <div class='dm-card yellow'><h4>VAT</h4><div class='v'>{_money(header.get('totalVat'))}</div></div>
                        <div class='dm-card yellow'><h4>Gross</h4><div class='v'>{_money(header.get('totalGross'))}</div></div>
                    </div>""",
                    unsafe_allow_html=True,
                )

# Line totals reconciliation notes (helps validate posting into VBR EPOS / Cloud Reporting)
                items = _ensure_line_numbers(items)
                _r_text = (res.get("text") or res.get("debug_text") or "")
                _hint = ((file_name or "") + " " + _r_text + " " + str(header.get("supplierName") or "")).lower()
                if ("dwg" in _hint) or ("drinks wholesale" in _hint):
                    dwg_out = _dwg_enrich_header_items(
                        header, items,
                        supplier_hint=(file_name or "") + " " + _r_text
                    )
                    if isinstance(dwg_out, tuple) and len(dwg_out) == 2:
                        header, items = dwg_out
                _ensure_vat_breakdown(header, items, f"{header.get('supplierName','')} {file_name}")
                metrics = _calc_metrics(header, items)
                net_note = f"[Line Items Net = {_money(metrics['sum_net'])} | Header Net = {_money(metrics['hdr_net'])} | Diff = ({_money(metrics['diff_net'])})]"
                vat_note = f"[Line Items VAT = {_money(metrics['sum_vat'])} | Header VAT = {_money(metrics['hdr_vat'])} | Diff = ({_money(metrics['diff_vat'])})]"
                gross_note = f"[Line Items Gross = {_money(metrics['sum_gross'])} | Header Gross = {_money(metrics['hdr_gross'])} | Diff = ({_money(metrics['diff_gross'])})]"
                # Reconciliation (compact)
                recon_col, reason_col = st.columns([1, 1])
                with recon_col:
                    st.markdown(_render_reconciliation_box_html(metrics), unsafe_allow_html=True)
                with reason_col:
                    st.markdown(_render_reconciliation_reasons_html(), unsafe_allow_html=True)
            with top_right:

                # Quick document download (top-right)
                tpl_used = (res.get('template_used') or '').strip() or '—'
                tpl_type = (res.get('template_parser_type') or '').strip()

                reconciliation = _build_reconciliation_status(metrics)
                recon_notes = _build_reconciliation_notes(metrics)

                # Engine badge (visible beside print icon)
                eng_used = (res.get("engine") or "").strip() or "—"
                c1, c2, c3 = st.columns([2.2, 2.2, 1.8])
                try:
                    badge_col, btn_col = st.columns([1, 1])
                except Exception:
                    badge_col = st.container()
                    btn_col = st.container()

                with badge_col:
                    st.markdown(
                        f"""<div style='display:inline-block; padding:8px 12px; border-radius:10px; background:#fff7cc; font-weight:800; border:1px solid #e5e7eb;'>
                        Engine Used: {eng_used}
                        </div>""",
                        unsafe_allow_html=True,
                    )
                with btn_col:
                    try:
                        pdf_bytes = _generate_pdf_report_bytes(file_name, header, items, reconciliation, recon_notes)
                        st.download_button(
                            '🖨️',
                            data=pdf_bytes,
                            file_name=(os.path.splitext(file_name)[0] + '_DocMate_Report.pdf'),
                            mime='application/pdf',
                            help=f"Download DocMate PDF report (A4 landscape). Template: {tpl_used}{(' (' + tpl_type + ')') if tpl_type else ''}",
                        )
                    except Exception as e:
                        # Robust fallback: HTML report (users can Print → Save as PDF in the browser)
                        html_report = _generate_html_report(file_name, header, items, reconciliation, recon_notes)
                        st.download_button(
                            '🖨️',
                            data=html_report,
                            file_name=(os.path.splitext(file_name)[0] + '_DocMate_Report.html'),
                            mime='text/html',
                            help=f"PDF not available on this machine. Download HTML report and print to PDF. Details: {e}",
                        )
                        st.warning("PDF report requires the Python package 'reportlab'. Install it with: pip install reportlab. Until then, the 🖨️ button downloads an HTML report you can print to PDF.")
                if (res.get('engine') or '') == 'auto' and tpl_used == '—':
                    st.warning('AUTO ran without a matched template. Best practice: Cloud AI → Save Supplier Template → AUTO.')
                st.caption('DocMate Invoice Admin')

                # VAT Breakdown must ALWAYS be calculated from (possibly edited) line items (not captured header totals)
                st.markdown(_render_vat_breakdown_box_calculated(items, max_rows=5), unsafe_allow_html=True)

                # Legacy fallback message removed: calculated breakdown is always available.

                
                # Admin-only: edit header fields (quick fix for Customer/Invoice Date etc. before posting)
                if st.session_state.get("is_admin", False):
                    with st.expander("Header (Admin)", expanded=False):
                        st.caption("Edit header fields for this invoice before saving to Register (audit logged).")
                        _sup_in = st.text_input("Supplier Name", value=(header.get("supplierName") or ""), key=f"hdr_sup_{inv_key}")
                        header["supplierName"] = _collapse_spaces(_sup_in) if isinstance(_sup_in, str) else _sup_in
                        _cust_in = st.text_input("Customer Name", value=(header.get("customerName") or ""), key=f"hdr_cust_{inv_key}")
                        _date_in = st.text_input("Invoice Date (YYYY-MM-DD)", value=(header.get("invoiceDate") or ""), key=f"hdr_date_{inv_key}")
                        _inv_in = st.text_input("Invoice Number", value=(header.get("invoiceNumber") or ""), key=f"hdr_inv_{inv_key}")

                        colH1, colH2 = st.columns(2)
                        if colH1.button("Apply Header Changes", key=f"hdr_apply_{inv_key}"):
                            new_cust = _collapse_spaces((_cust_in or "").strip())
                            new_date = _normalise_invoice_date_str(_date_in)
                            new_inv  = (_inv_in or "").strip()

                            if new_cust:
                                header["customerName"] = new_cust
                            if new_date:
                                header["invoiceDate"] = new_date
                            if new_inv:
                                header["invoiceNumber"] = new_inv

                            # persist into batch so Save to Register uses these values
                            try:
                                batch_now = st.session_state.get("last_batch") or []
                                for _ix in range(len(batch_now)):
                                    if (batch_now[_ix].get("inv_key") == inv_key) and (batch_now[_ix].get("file") == file_name):
                                        batch_now[_ix]["header"] = header
                                        st.session_state["last_batch"] = batch_now
                                        break
                            except Exception:
                                pass
                            # If Booker invoice and Admin provided Customer Name, learn it into the latest BOOKER supplier template
                            try:
                                sup_u = str(header.get("supplierName") or "").upper()
                                if new_cust and ("BOOKER" in sup_u):
                                    tpl_b = _db_find_booker_template_latest() or _db_find_template_latest_by_parser_type("BOOKER", table="supplier_templates")
                                    if tpl_b and (tpl_b.get("template_id") or ""):
                                        _db_patch_supplier_template_config(str(tpl_b.get("template_id") or ""), {"default_customer_name": new_cust})
                                        _log_event(f"Booker template learned default_customer_name={new_cust}", file_name=file_name)
                            except Exception:
                                pass


                            _log_event(f"Admin header edit: customer={header.get('customerName','')} date={header.get('invoiceDate','')} inv={header.get('invoiceNumber','')}", file_name=file_name)
                            st.success("Header updated for this session. Click 'Save to Register' to persist.")
                            st.rerun()

                        if colH2.button("Reset to Extracted Header", key=f"hdr_reset_{inv_key}"):
                            try:
                                raw = res.get("header_raw") or {}
                                if isinstance(raw, dict) and raw:
                                    header.clear()
                                    header.update(copy.deepcopy(raw))

                                    batch_now = st.session_state.get("last_batch") or []
                                    for _ix in range(len(batch_now)):
                                        if (batch_now[_ix].get("inv_key") == inv_key) and (batch_now[_ix].get("file") == file_name):
                                            batch_now[_ix]["header"] = header
                                            st.session_state["last_batch"] = batch_now
                                            break

                                    _log_event("Admin header reset to extracted values", file_name=file_name)
                                    st.success("Header reset.")
                                    st.rerun()
                            except Exception:
                                st.warning("Could not reset header for this invoice.")
# Admin-only: create/update template from this invoice (kept here to make the process clearer)
                if st.session_state.get("is_admin", False):
                    with st.expander("Template (Admin)", expanded=False):
                        settings_now = st.session_state.get("settings", {}) or {}

                        supplier_nm = (header.get("supplierName") or "").strip()
                        if not supplier_nm:
                            supplier_nm = _guess_supplier_from_filename(file_name) or ""
                        # Detect supplier parser type for sensible defaults
                        supplier_nm_l = supplier_nm.lower()
                        parser_type = "GENERIC"
                        if "parfetts" in supplier_nm_l:
                            parser_type = "PARFETTS"
                        elif "booker" in supplier_nm_l:
                            parser_type = "BOOKER"
                        elif "dhamecha" in supplier_nm_l:
                            parser_type = "DHAMECHA"
                        elif ("drinks wholesale group" in supplier_nm_l) or ("dwg" in supplier_nm_l):
                            parser_type = "DWG"


                        default_keywords = [k for k in [supplier_nm, header.get("invoiceNumber"), header.get("customerName")] if k]
                        default_keywords = [str(x).strip() for x in default_keywords if str(x).strip()]
                        kw_str_default = ", ".join(default_keywords[:6])

                        inv_key = (res.get("inv_key") or "").strip() or hashlib.sha256((file_name or "").encode("utf-8", errors="ignore")).hexdigest()[:10]
                        default_display_id = f"TPL{_uk_now_iso().replace('-','').replace(':','').replace(' ','')}-{inv_key[:4]}"

                        display_id = st.text_input("Template Display ID", value=default_display_id, key=f"tpl_disp_{inv_key}")
                        kw_str = st.text_input("Match keywords (comma-separated)", value=kw_str_default, key=f"tpl_kw_{inv_key}")
                        layout_variant = st.text_input(
                            "Layout variant",
                            value=infer_layout_variant(parser_type, (extracted_text or doc_text_hint or ""), items),
                            key=f"tpl_layout_{inv_key}",
                            help="Use one variant per supplier layout, e.g. BOOKER_INVOICE_V1 or DHAMECHA_SCAN_V1.",
                        )

                        pdf_pages_override = st.number_input(
                            "PDF max pages override",
                            min_value=1,
                            max_value=30,
                            value=int(settings_now.get("ocr_max_pages", 4) or 4),
                            step=1,
                            key=f"tpl_pages_{inv_key}",
                        )

                        preferred_local = st.selectbox(
                            "Preferred local engine",
                            options=["tesseract", "pdf_text_layer"],
                            index=0,
                            key=f"tpl_local_{inv_key}",
                        )

                        requires_cloud = st.checkbox("Requires Cloud extraction", value=False, key=f"tpl_cloud_{inv_key}")

                        vat_map_default: Dict[str, Any] = {}
                        if parser_type == "PARFETTS":
                            vat_map_default = {"A": 20.0, "Z": 0.0}
                        elif parser_type == "BOOKER":
                            vat_map_default = {"B": 20.0, "R": 5.0, "Z": 0.0, "A": 0.0}
                        elif parser_type == "DWG":
                            vat_map_default = {"S/A": 20.0, "S": 20.0}
                        elif parser_type == "DHAMECHA":
                            vat_map_default = {"A": 20.0, "B": 5.0, "Z": 0.0}

                        # If we can infer mapping from extracted lines, merge it in (helps new suppliers)
                        vat_map_detected = _derive_vat_map_from_items(items)
                        if vat_map_detected:
                            vat_map_default = {**vat_map_default, **vat_map_detected}

                        vat_map_key = f"tpl_vatmap_{inv_key}"
                        if (vat_map_key not in st.session_state) or (str(st.session_state.get(vat_map_key) or "").strip() in ("", "{}", "null")):
                            st.session_state[vat_map_key] = json.dumps(vat_map_default, indent=2)
                        vat_map_json = st.text_area(
                            "VAT mapping (JSON)",
                            key=vat_map_key,
                            height=110,
                        )
                        if st.button("Save template for this supplier", key=f"tpl_save_{inv_key}"):
                            try:
                                vat_map = json.loads(vat_map_json or "{}")
                                if not isinstance(vat_map, dict):
                                    raise ValueError("VAT mapping must be a JSON object.")
                            except Exception as ex:
                                st.error(f"VAT mapping JSON invalid: {ex}")
                                vat_map = {}

                            # Booker templates: keep AUTO local by default (do not force Cloud unless explicitly set in the template)
                            try:
                                if str(parser_type).upper() == "BOOKER":
                                    # only force Cloud if Admin explicitly marks it in config
                                    requires_cloud = bool(requires_cloud) and bool((st.session_state.get("booker_force_cloud_checkbox") or False))
                            except Exception:
                                pass

                            match_keywords = [k.strip() for k in (kw_str or "").split(",") if k.strip()]
                            template_config = build_supplier_template_config(
                                supplier_name=(supplier_nm or "").strip(),
                                parser_type=parser_type,
                                doc_text=(extracted_text or doc_text_hint or ""),
                                header=header,
                                items=items,
                                document_type=doc_type,
                                layout_variant=layout_variant,
                                supplier_alias_values=match_keywords + [header.get("customerName"), header.get("invoiceNumber")],
                                base_config={
                                    "max_pages_pdf": int(pdf_pages_override),
                                    "preferred_local_engine": preferred_local,
                                    "vat_map": vat_map,
                                },
                            )

                            payload = {
                                "template_id": "",
                                "display_id": (display_id or "").strip(),
                                "supplier_name": (supplier_nm or "").strip(),
                                "parser_type": parser_type,
                                "vat_number": "",
                                "awrs_number": "",
                                "requires_cloud": 1 if requires_cloud else 0,
                                "match_keywords": match_keywords,
                                "config": template_config,
                            }

                            tpl_id, saved, notes = _db_upsert_supplier_template_checked(payload)
                            if saved:
                                _log_event(f"Admin created/updated supplier template {display_id or tpl_id} (from invoice)", file_name=file_name)
                                st.success(f"Template saved/updated: {display_id or tpl_id}")
                            else:
                                _log_event(f"Admin template save skipped (unchanged): {display_id or tpl_id}", file_name=file_name)
                                for n in (notes or ["Template unchanged — not saved."]):
                                    st.info(n)

            tab_line_items, tab_debug = st.tabs(["Line Items", "Debug"])
            with tab_line_items:

                # Prepare editable table
                if pd is not None:
                    df = pd.DataFrame(items)
                    # stable column order
                    cols = [
                        "lineNo",
                        "code",
                        "description",
                        "casePack",
                        "unitSize",
                        "qty",
                        "totalUnits",
                        "unitPrice",
                        "lineNet",
                        "vatCode",
                        "vatRate",
                        "vatAmount",
                        "lineGross",
                        "stdRrp",
                        "por",
                        "isVoid",
                    ]
                    for c in cols:
                        if c not in df.columns:
                            df[c] = ""
                    df = df[cols]

                    _edit_ver = int(st.session_state.get(f"edit_ver_{inv_key}", 0))
                    edited = st.data_editor(
                        df,
                         use_container_width=True,
                        num_rows="dynamic",
                        key=f"edit_{inv_key}_{_edit_ver}",
                    )

                    # Convert back to list of dicts
                    edited_items = edited.to_dict(orient="records")
                else:
                    st.info("pandas not available; showing read-only items")
                    edited_items = items
                    for it in items:
                        st.write(it)

                colA, colB, colC = st.columns([1, 1, 1])
                if colA.button("Apply edits", key=f"apply_{inv_key}"):
                    # Supplier-specific enforcement after manual edits
                    try:
                        _supp = str(header.get("supplierName") or "")
                        if "dhamecha" in _supp.lower():
                            # After user edits, ALWAYS respect the user's vatCode and force vatRate + vatAmount + gross.
                            _map = {"A": 20.0, "B": 5.0, "Z": 0.0}
                            for it in (edited_items or []):
                                if not isinstance(it, dict):
                                    continue
                                try:
                                    vc = str(it.get("vatCode") or "").strip().upper()
                                    if vc in _map:
                                        it["vatRate"] = float(_map[vc])
                                    else:
                                        it["vatRate"] = float(_normalise_vat_rate(it.get("vatRate")) or 0.0)

                                    line_net = _safe_float(it.get("lineNet"))
                                    vrn = _safe_float(it.get("vatRate"))
                                    vat_amt = 0.0 if abs(vrn) < 0.0001 else round(line_net * (vrn / 100.0), 2)
                                    it["vatAmount"] = vat_amt
                                    it["lineGross"] = round(line_net + vat_amt, 2)
                                except Exception:
                                    pass
                    except Exception as e:
                        _log_event(f"WARNING VAT enforcement after Apply edits failed: {e}", file_name=res.get("file_name",""))


                    edited_items = _recalc_items(edited_items)
                    res["items"] = edited_items
                    st.session_state["docmate_current_items"] = edited_items
                    try:
                        res["metrics"] = _calc_metrics(header, edited_items)
                    except Exception as e:
                        _log_event(f"ERROR recalculating metrics after Apply edits: {e}", file_name=res.get("file_name",""))
                    _log_event("Apply edits: items updated and recalculated", file_name=res.get("file_name",""))
                    st.success("Edits applied")
                    try:
                        st.session_state[f"edit_ver_{inv_key}"] = int(st.session_state.get(f"edit_ver_{inv_key}", 0)) + 1
                    except Exception:
                        pass
                    st.rerun()

                if colB.button("Save to Register", key=f"save_{inv_key}"):
                    try:
                        mode = _upsert_register(
                            inv_key=inv_key,
                            header=header,
                            items=edited_items,
                            doc_type=doc_type,
                            engine_used=res.get("engine") or "",
                            file_name=file_name,
                        )
                        if mode == "updated":
                            st.info(f"Entry already exists! Updated & re-saved — No duplicate entry enforced.")
                        else:
                            st.success(f"Saved to {REGISTER_LABEL.get(doc_type, 'Register')}")
                    except Exception as e:
                        st.error(f"Save failed: {e}")

                # Download CSV
                if pd is not None:
                    csv_bytes = pd.DataFrame(edited_items).to_csv(index=False).encode("utf-8")
                    colC.download_button(
                        "Download Items CSV",
                        data=csv_bytes,
                        file_name=f"{inv_key}_items.csv",
                        mime="text/csv",
                        key=f"csv_{inv_key}",
                    )
                    with tab_debug:
                        dbg = (res.get("debug_text") or res.get("text") or "")
                        st.code(dbg)
                    st.download_button("Download debug text", data=dbg.encode("utf-8"), file_name=f"{inv_key}_debug.txt", mime="text/plain", key=f"dbg_{inv_key}")



# -------------------------
# UI: Registers
# -------------------------

