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


import importlib
import streamlit as st

def render_registers() -> None:
    """Render the Registers page (extracted from legacy.py)."""
    legacy = importlib.import_module("docmate.legacy")
    g = globals()
    for k in dir(legacy):
        if k.startswith("__") or k == "st":
            continue
        if k not in g:
            g[k] = getattr(legacy, k)

    st.header("Registers")
    st.caption("Stored documents for validation and posting. Outputs can feed VBR EPOS stock/invoice workflows and Cloud Reporting.")

    # Export: Header + Line Items → SPOS Cloud / Local DB
    st.markdown(
        """<div style="background:#fff7cc;border:1px solid #e6d37a;padding:12px;border-radius:10px">
        <b>Export Header + Line Items to SPOS Cloud / Local DB</b><br/>
        Use this to export validated register data (including StdRRP, POR, VAT breakdown) for reconciliation and for posting into VBR EPOS / SPOS Cloud.
        </div>""",
        unsafe_allow_html=True,
    )


    tabs = st.tabs(DOC_TYPES)
    for i, doc_type in enumerate(DOC_TYPES):
        with tabs[i]:
            if doc_type == "Purchase Invoice":
                with st.expander("Admin actions", expanded=False):
                    st.caption("Reset clears stored Purchase Invoices from the local register so you can re-process the same PDFs using Cloud / Auto / Local engines for comparison before importing into VBR EPOS.")
                    confirm_reset = st.checkbox("I understand this will permanently delete stored Purchase Invoice register entries", key="confirm_reset_pi")
                    if st.button("Reset Purchase Invoice Register", disabled=not confirm_reset):
                        deleted = _reset_register("Purchase Invoice")
                        _log_event(f"Admin reset register: Purchase Invoice | deleted={deleted}")
                        st.success(f"Purchase Invoice register cleared (deleted {deleted} document(s)).")
                        st.rerun()

        
                # Export controls (Purchase Invoice)
                try:
                    export_cfg = _spos_export_config(_load_settings())
                except Exception:
                    export_cfg = {"enabled": False, "base_url": "", "auth_method": "", "auth_token": "", "endpoint_pattern": ""}

                st.markdown("### Results")
                tab_summary, tab_line_items, tab_debug = st.tabs(["Header / Summary", "Line Items", "Debug"])

                with tab_summary:
                    st.caption("Select a stored invoice, then export full header + line items for pricing comparison against existing VBR EPOS/SPOS Cloud records.")
                    rows_pi = _get_register_rows("Purchase Invoice", limit=500) or []
                    # Build friendly labels: InvoiceDate — Supplier — InvoiceNo (newest first)
                    opts = []  # list of (label, inv_key)
                    for r in rows_pi:
                        try:
                            inv_key = str(r.get("inv_key") or "").strip()
                            if not inv_key:
                                continue
                            inv_no = str(r.get("invoice_number") or "").strip() or "(No Invoice No)"
                            supp = str(r.get("supplier_name") or "").strip() or "(No Supplier)"
                            inv_dt = _normalise_invoice_date_str(str(r.get("invoice_date") or "")) or str(r.get("invoice_date") or "").strip() or "(No Date)"
                            label = f"{inv_dt} — {supp} — {inv_no}"
                            opts.append((label, inv_key, inv_dt))
                        except Exception:
                            continue
                    # Sort by invoice date (desc), then label
                    opts = sorted(opts, key=lambda x: (x[2] or "", x[0]), reverse=True)
                    label_to_key = {o[0]: o[1] for o in opts}
                    labels = [o[0] for o in opts]
                    inv_sel_label = st.selectbox("Select Invoice (Date — Supplier — Invoice No)", options=["(Select)"] + labels, index=0, key="pi_export_invlabel")
                    if inv_sel_label and inv_sel_label != "(Select)":
                        inv_sel = label_to_key.get(inv_sel_label)
                        payload = _export_payload_purchase_invoice(inv_sel)
                        if payload:
                            st.write("Export destination (current configuration):")
                            st.json(export_cfg)
                            # Single JSON (combined)
                            names = _invoice_export_file_names(inv_sel, payload)
                            jb = json.dumps(payload, indent=2, ensure_ascii=False).encode("utf-8")
                            clicked_json = st.download_button(
                                "Download Single Invoice JSON",
                                data=jb,
                                file_name=names["single_json"],
                                mime="application/json",
                                key=f"dl_json_{inv_sel}",
                            )
                            if clicked_json:
                                _log_event(f"Export download: {names['single_json']}", inv_sel)


                            # Dedicated export format (customer first, stable for import)
                            try:
                                dedicated = _build_dedicated_invoice_export(payload)
                                djb = json.dumps(dedicated, indent=2, ensure_ascii=False).encode("utf-8")
                                clicked_ded_json = st.download_button(
                                    "Download Dedicated Export JSON",
                                    data=djb,
                                    file_name=names["dedicated_json"],
                                    mime="application/json",
                                    key=f"dl_ded_json_{inv_sel}",
                                )
                                if clicked_ded_json:
                                    _log_event(f"Export download: {names['dedicated_json']}", inv_sel)

                            except Exception:
                                pass

                            # Optional: write files to disk (DocsEXPORT/JSON) for audit/reprocessing
                            if st.button("Write Export Files to Disk (DocsEXPORT/JSON)", key=f"btn_write_exports_{inv_sel}"):
                                paths = _write_invoice_exports(inv_sel, payload)
                                _log_event("Export write-to-disk triggered.", inv_sel)
                                st.success("Export files written.")
                                st.code("\n".join([f"{k}: {v}" for k, v in paths.items()]))

                            # Line items CSV (for quick price comparison)
                            if pd is not None:
                                try:
                                    # Compact line-items CSV (quick check)
                                    df_items = pd.DataFrame(payload.get("lineItems") or [])
                                    clicked_lines = st.download_button(
                                        "Download Line Items CSV (Compact)",
                                        data=df_items.to_csv(index=False).encode("utf-8"),
                                        file_name=names["lines_csv"],
                                        mime="text/csv",
                                        key=f"dl_csv_{inv_sel}",
                                    )
                                    if clicked_lines:
                                        _log_event(f"Export download: {names['lines_csv']}", inv_sel)

                                    # FULL Invoice Export CSV (customer + header + VAT/metrics + lines)
                                    clicked_full = st.download_button(
                                        "Download Invoice Export CSV (Full)",
                                        data=_invoice_export_full_csv_bytes(payload),
                                        file_name=names["full_csv"],
                                        mime="text/csv",
                                        key=f"dl_fullcsv_{inv_sel}",
                                    )
                                    if clicked_full:
                                        _log_event(f"Export download: {names['full_csv']}", inv_sel)
                                except Exception as e:
                                    _log_event(f"ERROR building CSV downloads: {e}", inv_sel)

                        else:
                            st.warning("Invoice not found in register.")
                    else:
                        st.info("Select an invoice to enable export.")
            rows = _get_register_rows(doc_type, limit=300)
            if not rows:
                st.info("No documents yet.")
                continue

            # search
            q = st.text_input("Search", key=f"search_{doc_type}")
            filt = []
            for r in rows:
                if not q:
                    filt.append(r)
                    continue
                s = " ".join([
                    str(r.get("supplier_name") or ""),
                    str(r.get("customer_name") or ""),
                    str(r.get("invoice_number") or ""),
                    str(r.get("invoice_date") or ""),
                    str(r.get("file_name") or ""),
                ]).lower()
                if q.lower() in s:
                    filt.append(r)

            if pd is not None:
                df = pd.DataFrame(filt)
                try:
                    if 'invoice_date' in df.columns:
                        df['invoice_date'] = df['invoice_date'].apply(lambda x: _normalise_invoice_date_str(str(x or '')))
                except Exception:
                    pass
                show_cols = [
                    "processed_at",
                    "supplier_name",
                    "customer_name",
                    "invoice_date",
                    "invoice_number",
                    "net_amount",
                    "vat_amount",
                    "gross_amount",
                    "total_lines",
                    "engine_used",
                    "file_name",
                    "inv_key",
                ]
                for c in show_cols:
                    if c not in df.columns:
                        df[c] = ""
                df = df[show_cols]
                st.dataframe(df,  use_container_width=True, hide_index=True)
            else:
                for r in filt:
                    st.write(r)

            # open details
            inv_key = st.text_input("Open invoice by inv_key", key=f"open_{doc_type}")
            if inv_key:
                inv = _get_invoice(inv_key.strip())
                if not inv:
                    st.error("Not found")
                else:
                    st.subheader("Invoice Details")
                    st.write({k: v for k, v in inv.items() if k != "items"})
                    st.subheader("Items")
                    if pd is not None:
                        st.dataframe(pd.DataFrame(inv["items"]),  use_container_width=True, hide_index=True)
                    else:
                        for it in inv["items"]:
                            st.write(it)


    # -------------------------
    # UI: Templates
    # -------------------------

def render_registers() -> None:
    _sync_from_legacy()
    st.header("Registers")
    st.caption("Stored documents for validation and posting. Outputs can feed VBR EPOS stock/invoice workflows and Cloud Reporting.")

    # Export: Header + Line Items → SPOS Cloud / Local DB
    st.markdown(
        """<div style="background:#fff7cc;border:1px solid #e6d37a;padding:12px;border-radius:10px">
        <b>Export Header + Line Items to SPOS Cloud / Local DB</b><br/>
        Use this to export validated register data (including StdRRP, POR, VAT breakdown) for reconciliation and for posting into VBR EPOS / SPOS Cloud.
        </div>""",
        unsafe_allow_html=True,
    )


    tabs = st.tabs(DOC_TYPES)
    for i, doc_type in enumerate(DOC_TYPES):
        with tabs[i]:
            if doc_type == "Purchase Invoice":
                with st.expander("Admin actions", expanded=False):
                    st.caption("Reset clears stored Purchase Invoices from the local register so you can re-process the same PDFs using Cloud / Auto / Local engines for comparison before importing into VBR EPOS.")
                    confirm_reset = st.checkbox("I understand this will permanently delete stored Purchase Invoice register entries", key="confirm_reset_pi")
                    if st.button("Reset Purchase Invoice Register", disabled=not confirm_reset):
                        deleted = _reset_register("Purchase Invoice")
                        _log_event(f"Admin reset register: Purchase Invoice | deleted={deleted}")
                        st.success(f"Purchase Invoice register cleared (deleted {deleted} document(s)).")
                        st.rerun()

            
                # Export controls (Purchase Invoice)
                try:
                    export_cfg = _spos_export_config(_load_settings())
                except Exception:
                    export_cfg = {"enabled": False, "base_url": "", "auth_method": "", "auth_token": "", "endpoint_pattern": ""}

                st.markdown("### Results")
                tab_summary, tab_line_items, tab_debug = st.tabs(["Header / Summary", "Line Items", "Debug"])

                with tab_summary:
                    st.caption("Select a stored invoice, then export full header + line items for pricing comparison against existing VBR EPOS/SPOS Cloud records.")
                    rows_pi = _get_register_rows("Purchase Invoice", limit=500) or []
                    # Build friendly labels: InvoiceDate — Supplier — InvoiceNo (newest first)
                    opts = []  # list of (label, inv_key)
                    for r in rows_pi:
                        try:
                            inv_key = str(r.get("inv_key") or "").strip()
                            if not inv_key:
                                continue
                            inv_no = str(r.get("invoice_number") or "").strip() or "(No Invoice No)"
                            supp = str(r.get("supplier_name") or "").strip() or "(No Supplier)"
                            inv_dt = _normalise_invoice_date_str(str(r.get("invoice_date") or "")) or str(r.get("invoice_date") or "").strip() or "(No Date)"
                            label = f"{inv_dt} — {supp} — {inv_no}"
                            opts.append((label, inv_key, inv_dt))
                        except Exception:
                            continue
                    # Sort by invoice date (desc), then label
                    opts = sorted(opts, key=lambda x: (x[2] or "", x[0]), reverse=True)
                    label_to_key = {o[0]: o[1] for o in opts}
                    labels = [o[0] for o in opts]
                    inv_sel_label = st.selectbox("Select Invoice (Date — Supplier — Invoice No)", options=["(Select)"] + labels, index=0, key="pi_export_invlabel")
                    if inv_sel_label and inv_sel_label != "(Select)":
                        inv_sel = label_to_key.get(inv_sel_label)
                        payload = _export_payload_purchase_invoice(inv_sel)
                        if payload:
                            st.write("Export destination (current configuration):")
                            st.json(export_cfg)
                            # Single JSON (combined)
                            names = _invoice_export_file_names(inv_sel, payload)
                            jb = json.dumps(payload, indent=2, ensure_ascii=False).encode("utf-8")
                            clicked_json = st.download_button(
                                "Download Single Invoice JSON",
                                data=jb,
                                file_name=names["single_json"],
                                mime="application/json",
                                key=f"dl_json_{inv_sel}",
                            )
                            if clicked_json:
                                _log_event(f"Export download: {names['single_json']}", inv_sel)


                            # Dedicated export format (customer first, stable for import)
                            try:
                                dedicated = _build_dedicated_invoice_export(payload)
                                djb = json.dumps(dedicated, indent=2, ensure_ascii=False).encode("utf-8")
                                clicked_ded_json = st.download_button(
                                    "Download Dedicated Export JSON",
                                    data=djb,
                                    file_name=names["dedicated_json"],
                                    mime="application/json",
                                    key=f"dl_ded_json_{inv_sel}",
                                )
                                if clicked_ded_json:
                                    _log_event(f"Export download: {names['dedicated_json']}", inv_sel)

                            except Exception:
                                pass

                            # Optional: write files to disk (DocsEXPORT/JSON) for audit/reprocessing
                            if st.button("Write Export Files to Disk (DocsEXPORT/JSON)", key=f"btn_write_exports_{inv_sel}"):
                                paths = _write_invoice_exports(inv_sel, payload)
                                _log_event("Export write-to-disk triggered.", inv_sel)
                                st.success("Export files written.")
                                st.code("\n".join([f"{k}: {v}" for k, v in paths.items()]))

                            # Line items CSV (for quick price comparison)
                            if pd is not None:
                                try:
                                    # Compact line-items CSV (quick check)
                                    df_items = pd.DataFrame(payload.get("lineItems") or [])
                                    clicked_lines = st.download_button(
                                        "Download Line Items CSV (Compact)",
                                        data=df_items.to_csv(index=False).encode("utf-8"),
                                        file_name=names["lines_csv"],
                                        mime="text/csv",
                                        key=f"dl_csv_{inv_sel}",
                                    )
                                    if clicked_lines:
                                        _log_event(f"Export download: {names['lines_csv']}", inv_sel)

                                    # FULL Invoice Export CSV (customer + header + VAT/metrics + lines)
                                    clicked_full = st.download_button(
                                        "Download Invoice Export CSV (Full)",
                                        data=_invoice_export_full_csv_bytes(payload),
                                        file_name=names["full_csv"],
                                        mime="text/csv",
                                        key=f"dl_fullcsv_{inv_sel}",
                                    )
                                    if clicked_full:
                                        _log_event(f"Export download: {names['full_csv']}", inv_sel)
                                except Exception as e:
                                    _log_event(f"ERROR building CSV downloads: {e}", inv_sel)

                        else:
                            st.warning("Invoice not found in register.")
                    else:
                        st.info("Select an invoice to enable export.")
            rows = _get_register_rows(doc_type, limit=300)
            if not rows:
                st.info("No documents yet.")
                continue

            # search
            q = st.text_input("Search", key=f"search_{doc_type}")
            filt = []
            for r in rows:
                if not q:
                    filt.append(r)
                    continue
                s = " ".join([
                    str(r.get("supplier_name") or ""),
                    str(r.get("customer_name") or ""),
                    str(r.get("invoice_number") or ""),
                    str(r.get("invoice_date") or ""),
                    str(r.get("file_name") or ""),
                ]).lower()
                if q.lower() in s:
                    filt.append(r)

            if pd is not None:
                df = pd.DataFrame(filt)
                try:
                    if 'invoice_date' in df.columns:
                        df['invoice_date'] = df['invoice_date'].apply(lambda x: _normalise_invoice_date_str(str(x or '')))
                except Exception:
                    pass
                show_cols = [
                    "processed_at",
                    "supplier_name",
                    "customer_name",
                    "invoice_date",
                    "invoice_number",
                    "net_amount",
                    "vat_amount",
                    "gross_amount",
                    "total_lines",
                    "engine_used",
                    "file_name",
                    "inv_key",
                ]
                for c in show_cols:
                    if c not in df.columns:
                        df[c] = ""
                df = df[show_cols]
                st.dataframe(df,  use_container_width=True, hide_index=True)
            else:
                for r in filt:
                    st.write(r)

            # open details
            inv_key = st.text_input("Open invoice by inv_key", key=f"open_{doc_type}")
            if inv_key:
                inv = _get_invoice(inv_key.strip())
                if not inv:
                    st.error("Not found")
                else:
                    st.subheader("Invoice Details")
                    st.write({k: v for k, v in inv.items() if k != "items"})
                    st.subheader("Items")
                    if pd is not None:
                        st.dataframe(pd.DataFrame(inv["items"]),  use_container_width=True, hide_index=True)
                    else:
                        for it in inv["items"]:
                            st.write(it)


# -------------------------
# UI: Templates
# -------------------------

