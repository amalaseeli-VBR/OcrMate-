from __future__ import annotations
import html

def _sync_from_legacy() -> None:
    # Pull all names from docmate.legacy into this module's globals so extracted
    # functions can run unchanged.
    from docmate import legacy as L
    g = globals()
    for k in dir(L):
        if k.startswith("__"):
            continue
        if k in g:
            continue
        g[k] = getattr(L, k)


def _escape_html(s: str) -> str:
    return html.escape(str(s or ""), quote=True)

def render_live_clock_card(settings: Dict[str, Any]) -> None:
    _sync_from_legacy()
    """Renders a LIVE SYSTEM TIME card with a client-side ticking clock (seconds) without Streamlit reruns.

    Uses server time as the source-of-truth at render time, then increments on the client.
    This is suitable for a UK clock on all UI pages and avoids blank screens due to missing autorefresh deps.
    """
    now = _now_in_configured_tz(settings)
    # send epoch millis to browser
    epoch_ms = int(now.timestamp() * 1000)

    tz_label = _get_time_zone_label(settings) or "GMT"
    # Example: MONDAY, 9 FEBRUARY 2026
    day_line = now.strftime("%A, %-d %B %Y") if os.name != "nt" else now.strftime("%A, %d %B %Y")

    # Client-side ticking clock (updates every second)
    html = f"""
    <div style="text-align:center; padding:12px 16px; border-radius:14px; border:1px solid rgba(255,255,255,0.10);
background: linear-gradient(180deg, rgba(0,0,0,0.92) 0%, rgba(0,0,0,0.72) 100%); box-shadow:0 2px 10px rgba(0,0,0,0.25);">
  <div style="font-weight:900; letter-spacing:0.08em; font-size:12px; color:#7c3aed;">Live System Time</div>
  <div id="dmClockTime" style="font-weight:900; font-size:36px; color:#ffffff; margin-top:2px;">--:--:--</div>
  <div style="font-weight:900; font-size:12px; color:#7c3aed; margin-top:-6px;">{tz_label}</div>
  <div id="dmClockDate" style="font-weight:900; letter-spacing:0.04em; font-size:14px; color:#7c3aed; margin-top:4px;">{day_line}</div>
</div>

    <script>
      (function() {{
        let t = {epoch_ms};
        function pad(n) {{ return String(n).padStart(2, '0'); }}
        function tick() {{
          const d = new Date(t);
          const hh = pad(d.getHours());
          const mm = pad(d.getMinutes());
          const ss = pad(d.getSeconds());
          const el = document.getElementById('dmClockTime');
          if (el) el.textContent = `${{hh}}:${{mm}}:${{ss}}`;
          t += 1000;
        }}
        tick();
        setInterval(tick, 1000);
      }})();
    </script>
    """
    st.components.v1.html(html, height=120, scrolling=False)

def render_vat_breakdown_box(vat_rows: List[Dict[str, Any]], header_net: float, header_vat: float, header_gross: float, delivery_charge: float = 0.0) -> str:
    _sync_from_legacy()
    """Compatibility wrapper (legacy). Prefer _render_vat_breakdown_box_calculated(items)."""
    # Backward compatibility: try to use provided vat_rows; if it looks empty, return a minimal box.
    rows_html = []
    goods_total = 0.0
    vat_total = 0.0
    for r in (vat_rows or []):
        code = str(r.get("vatCode") or "").strip().upper()
        goods = _safe_float(r.get("goodsAmount") or r.get("netAmount"))
        vat = _safe_float(r.get("vatAmount"))
        goods_total += goods
        vat_total += vat
        rows_html.append(
            f"<tr><td style='padding:4px 8px;'>{code}</td><td style='padding:4px 8px; text-align:right;'>{goods:,.2f}</td><td style='padding:4px 8px; text-align:right;'>{vat:,.2f}</td></tr>"
        )
    goods_total = round(goods_total, 2)
    vat_total = round(vat_total, 2)
    return f"""
<div style='border:1px solid rgba(120,120,120,0.35); border-radius:10px; padding:10px; max-width:520px;'>
  <div style='font-weight:700; margin-bottom:8px;'>VAT Breakdown</div>
  <table style='width:100%; border-collapse:collapse;'>
    <thead>
      <tr>
        <th style='text-align:left; padding:4px 8px; border-bottom:1px solid rgba(120,120,120,0.35);'>VAT Code</th>
        <th style='text-align:right; padding:4px 8px; border-bottom:1px solid rgba(120,120,120,0.35);'>Net</th>
        <th style='text-align:right; padding:4px 8px; border-bottom:1px solid rgba(120,120,120,0.35);'>VAT</th>
      </tr>
    </thead>
    <tbody>
      {''.join(rows_html)}
    </tbody>
  </table>
  <div style='margin-top:10px; padding-top:8px; border-top:1px solid rgba(120,120,120,0.35);'>
    <div><b>Total Net:</b> {goods_total:,.2f}</div>
    <div><b>Total VAT:</b> {vat_total:,.2f}</div>
    <div><b>Total Gross (header):</b> {_safe_float(header_gross):,.2f}</div>
  </div>
</div>
"""

def render_vat_breakdown_box_calculated(items: list[dict], max_rows: int = 5) -> str:
    _sync_from_legacy()
    """Render VAT Breakdown based on current (possibly edited) line items.

    - Always shows a fixed number of VAT-code rows (max_rows)
    - Calculates Net/VAT/Gross from line items (not header totals)
    - Includes a Total row inside the table (no extra totals lines below)

    NOTE: _calc_vat_breakdown_from_items() returns rows with keys:
      vatCode, netAmount, vatAmount, grossAmount
    """
    # Normalise max_rows
    try:
        max_rows = int(max_rows)
    except Exception:
        max_rows = 5
    if max_rows < 1:
        max_rows = 1
    if max_rows > 8:
        max_rows = 8

    rows = _calc_vat_breakdown_from_items(items)

    # Prepare display rows (cap + pad)
    display_rows = rows[:max_rows]
    while len(display_rows) < max_rows:
        display_rows.append({"vatCode": "", "netAmount": 0.0, "vatAmount": 0.0, "grossAmount": 0.0, "_source": "calculated"})

    net_total = round(sum(_safe_float(r.get("netAmount")) for r in rows), 2)
    vat_total = round(sum(_safe_float(r.get("vatAmount")) for r in rows), 2)
    gross_total = round(sum(_safe_float(r.get("grossAmount")) for r in rows), 2)

    # Styling
    box_style = (
        "border:1px solid #e6e6e6;border-radius:10px;padding:12px 12px 10px 12px;"
        "background:#ffffff;box-shadow:0 1px 2px rgba(0,0,0,0.03);"
    )
    table_style = "width:100%;border-collapse:collapse;font-size:13px;"
    th_style = "text-align:left;border-bottom:1px solid #ddd;padding:6px 8px;font-weight:600;background:#fafafa;"
    td_style = "border-bottom:1px solid #eee;padding:6px 8px;vertical-align:top;"
    td_num_style = td_style + "text-align:right;font-variant-numeric:tabular-nums;"
    tr_total_style = "background:#fbfbfb;font-weight:700;"

    html = f"<div style='{box_style}'>"
    html += "<div style='font-weight:700;margin-bottom:8px;'>VAT Breakdown (Calculated from line items)</div>"
    html += f"<table style='{table_style}'>"
    html += "<tr>"
    html += f"<th style='{th_style}'>VAT Code</th>"
    html += f"<th style='{th_style}'>Net Amount</th>"
    html += f"<th style='{th_style}'>VAT Amount</th>"
    html += f"<th style='{th_style}'>Gross Amount</th>"
    html += "</tr>"

    for r in display_rows:
        code = (r.get("vatCode") or "").strip()
        net = _fmt_money(_safe_float(r.get("netAmount")))
        vat = _fmt_money(_safe_float(r.get("vatAmount")))
        gross = _fmt_money(_safe_float(r.get("grossAmount")))
        html += "<tr>"
        html += f"<td style='{td_style}'>{_escape_html(code)}</td>"
        html += f"<td style='{td_num_style}'>{net}</td>"
        html += f"<td style='{td_num_style}'>{vat}</td>"
        html += f"<td style='{td_num_style}'>{gross}</td>"
        html += "</tr>"

    # Total row
    html += f"<tr style='{tr_total_style}'>"
    html += f"<td style='{td_style}'>TOTAL</td>"
    html += f"<td style='{td_num_style}'>{_fmt_money(net_total)}</td>"
    html += f"<td style='{td_num_style}'>{_fmt_money(vat_total)}</td>"
    html += f"<td style='{td_num_style}'>{_fmt_money(gross_total)}</td>"
    html += "</tr>"

    html += "</table></div>"
    return html

def render_reconciliation_box_html(metrics: dict) -> str:
    _sync_from_legacy()
    """Render a compact reconciliation summary as HTML (Streamlit markdown w/ unsafe_allow_html=True)."""
    try:
        eps = float(metrics.get("eps", 0.02) or 0.02)
    except Exception:
        eps = 0.02

    def _status(diff: float) -> str:
        try:
            return "OK" if abs(float(diff or 0.0)) <= eps else "CHECK"
        except Exception:
            return "CHECK"

    def _chip(status: str) -> str:
        if status == "OK":
            return (
                '<span style="padding:2px 10px; border-radius:999px; background:#eaf7ea; color:#0f5132;'
                'border:1px solid #cfe9d5; font-weight:800; font-size:12px;">OK</span>'
            )
        return (
            '<span style="padding:2px 10px; border-radius:999px; background:#fdecea; color:#842029;'
            'border:1px solid #f5c2c7; font-weight:800; font-size:12px;">CHECK</span>'
        )

    sum_net = metrics.get("sum_net", 0.0) or 0.0
    hdr_net = metrics.get("hdr_net", 0.0) or 0.0
    diff_net = metrics.get("diff_net", (sum_net - hdr_net)) or 0.0

    sum_vat = metrics.get("sum_vat", 0.0) or 0.0
    hdr_vat = metrics.get("hdr_vat", 0.0) or 0.0
    diff_vat = metrics.get("diff_vat", (sum_vat - hdr_vat)) or 0.0

    sum_gross = metrics.get("sum_gross", 0.0) or 0.0
    hdr_gross = metrics.get("hdr_gross", 0.0) or 0.0
    diff_gross = metrics.get("diff_gross", (sum_gross - hdr_gross)) or 0.0

    s_net, s_vat, s_gross = _status(diff_net), _status(diff_vat), _status(diff_gross)
    any_check = (s_net != "OK") or (s_vat != "OK") or (s_gross != "OK")

    def _row(label: str, s: str, line_val: float, hdr_val: float, diff_val: float) -> str:
        return (
            '<div style="display:flex; align-items:center; gap:10px; margin:6px 0;">'
            f'<div style="width:120px; font-weight:700;">{label}</div>'
            f'{_chip(s)}'
            '<div style="font-size:13px; color:#374151;">'
            f'Line Items = <b>{_money(line_val)}</b>&nbsp;|&nbsp; Header = <b>{_money(hdr_val)}</b>'
            f'&nbsp;|&nbsp; Diff = <b>{_money(diff_val)}</b>'
            '</div>'
            '</div>'
        )

    box_start = (
        '<div style="border:1px solid #e5e7eb; border-radius:14px; padding:10px 12px;'
        'background:#f9fafb; margin-top:8px;">'
        '<div style="font-weight:800; font-size:14px; margin-bottom:6px;">Reconciliation (Header vs Line Items)</div>'
    )
    box_end = "</div>"

    reasons = ""
    if any_check:
        reasons = (
            '<div style="margin-top:10px; font-size:12px; color:#374151; line-height:1.4;">'
            '<b>If any line shows “CHECK”, possible reasons:</b>'
            '<ul style="margin:6px 0 0 18px;">'
            '<li>One or more invoice lines may be missing from the printed/PDF copy (common supplier print issue).</li>'
            '<li>Delivery/surcharge/discount lines included in totals but not captured as line items.</li>'
            '<li>VAT rounding differences (VAT rounded per-line vs invoice-level rounding).</li>'
            '<li>OCR capture gap on a line (rare) – please cross-check against the supplier copy.</li>'
            '</ul>'
            '<div style="margin-top:6px;">If you believe the invoice totals are incorrect, please query the supplier and request a corrected invoice or missing line detail.</div>'
            '</div>'
        )

    html = (
        box_start
        + _row("Net Check", s_net, sum_net, hdr_net, diff_net)
        + _row("VAT Check", s_vat, sum_vat, hdr_vat, diff_vat)
        + _row("Gross Check", s_gross, sum_gross, hdr_gross, diff_gross)
        + reasons
        + box_end
    )
    return html

def render_reconciliation_reasons_html() -> str:
    _sync_from_legacy()
    """Compact legend/notes block shown next to Reconciliation results."""
    return """<div class='dm-card' style='max-width:100%; overflow-wrap:anywhere; min-height:160px;'>
        <div style='font-weight:800; font-size:14px; margin-bottom:6px;'>Reconciliation notes</div>
        <div style='color:#111827;'>• <b>OK</b>: header totals match line totals within tolerance.</div>
        <div style='margin-top:3px; color:#111827;'>• <b>CHECK</b>: mismatch exists. Common reasons:</div>
        <div style='margin-left:14px; margin-top:3px; color:#374151;'>– VAT rate returned as fraction (e.g. <i>0.2</i> for 20%)</div>
        <div style='margin-left:14px; color:#374151;'>– invoice-level rounding vs line-level rounding</div>
        <div style='margin-left:14px; color:#374151;'>– missing VAT/Gross on some lines (recomputed in Auto)</div>
    </div>"""

def render_template_table(table: str, title: str) -> None:
    _sync_from_legacy()
    st.subheader(title)
    rows = _db_fetchall(f"SELECT * FROM {table} ORDER BY updated_at DESC")
    if not rows:
        st.info("No templates yet.")
        return

    templates = [dict(r) for r in rows]

    if pd is not None:
        df = pd.DataFrame(templates)
        name_col = "supplier_name" if table == "supplier_templates" else "customer_name"
        cols = ["display_id", "template_id", name_col, "parser_type", "requires_cloud", "created_at", "updated_at"]
        for c in cols:
            if c not in df.columns:
                df[c] = ""
        st.dataframe(df[cols],  use_container_width=True, hide_index=True)
    else:
        for t in templates:
            st.write(t)

    st.markdown("#### View / Edit template")
    name_col = "supplier_name" if table == "supplier_templates" else "customer_name"
    options = []
    label_to_tid = {}
    for t in templates:
        disp = (t.get("display_id") or "").strip() or (t.get("template_id") or "")
        nm = (t.get(name_col) or "").strip()
        label = f"{disp} — {nm}".strip(" —")
        options.append(label)
        label_to_tid[label] = t.get("template_id")

    sel = st.selectbox("Select template", options=options, key=f"sel_{table}")
    tid = label_to_tid.get(sel)
    tpl = _db_get_template(table, tid) if tid else None
    if not tpl:
        st.warning("Template not found.")
        return

    try:
        kws = json.loads(tpl.get("match_keywords_json") or "[]")
        if not isinstance(kws, list):
            kws = []
    except Exception:
        kws = []
    try:
        cfg = json.loads(tpl.get("config_json") or "{}")
        if not isinstance(cfg, dict):
            cfg = {}
    except Exception:
        cfg = {}

    with st.form(f"form_edit_{table}_{tpl.get('template_id')}"):
        c1, c2 = st.columns(2)
        display_id = c1.text_input("Display ID", value=tpl.get("display_id") or "")
        parser_type = c2.text_input("Parser type", value=tpl.get("parser_type") or "GENERIC")

        if table == "supplier_templates":
            n1, n2 = st.columns(2)
            supplier_name = n1.text_input("Supplier name", value=tpl.get("supplier_name") or "")
            vat_number = n2.text_input("VAT number (optional)", value=tpl.get("vat_number") or "")
            awrs_number = st.text_input("AWRS number (optional)", value=tpl.get("awrs_number") or "")
            party_value = supplier_name
        else:
            customer_name = st.text_input("Customer name", value=tpl.get("customer_name") or "")
            party_value = customer_name
            vat_number = ""
            awrs_number = ""

        requires_cloud = st.checkbox("Requires Cloud AI", value=bool(int(tpl.get("requires_cloud") or 0)))
        keywords_text = st.text_area("Match keywords (one per line)", value="\n".join([str(k) for k in kws if str(k).strip()]), height=140)
        config_text = st.text_area("Config JSON (advanced)", value=json.dumps(cfg, indent=2), height=160)
        save = st.form_submit_button("Save changes")

    if save:
        display_id_clean = (display_id or "").strip()
        if not display_id_clean:
            st.error("Display ID cannot be blank.")
        else:
            try:
                cfg2 = json.loads(config_text or "{}")
                if not isinstance(cfg2, dict):
                    raise ValueError("Config must be a JSON object")
            except Exception as e:
                st.error(f"Invalid Config JSON: {e}")
                cfg2 = None

            if cfg2 is not None:
                kw_list = [k.strip() for k in (keywords_text or "").splitlines() if k.strip()]
                try:
                    if table == "supplier_templates":
                        _db_upsert_supplier_template(
                            {
                                "template_id": tpl.get("template_id"),
                                "display_id": display_id_clean,
                                "supplier_name": party_value,
                                "vat_number": vat_number,
                                "awrs_number": awrs_number,
                                "parser_type": parser_type,
                                "requires_cloud": 1 if requires_cloud else 0,
                                "match_keywords": kw_list,
                                "config": cfg2,
                                "created_at": tpl.get("created_at") or _uk_now_iso(),
                            }
                        )
                    else:
                        _db_upsert_customer_template(
                            {
                                "template_id": tpl.get("template_id"),
                                "display_id": display_id_clean,
                                "customer_name": party_value,
                                "parser_type": parser_type,
                                "requires_cloud": 1 if requires_cloud else 0,
                                "match_keywords": kw_list,
                                "config": cfg2,
                                "created_at": tpl.get("created_at") or _uk_now_iso(),
                            }
                        )
                    st.success("Template saved")
                    _log_event(f"Admin updated template {display_id_clean} in {table}")
                    st.rerun()
                except Exception as e:
                    st.error(str(e))

    st.markdown("#### Delete template")
    del_ident = st.text_input("Template ID or Display ID", value=tpl.get("display_id") or tpl.get("template_id") or "", key=f"del_{table}_ident")
    confirm = st.checkbox("I understand this cannot be undone", key=f"conf_{table}")
    if st.button("Delete", disabled=(not del_ident or not confirm), key=f"btn_del_{table}"):
        n = _db_delete_template(table, del_ident.strip())
        if n <= 0:
            st.error("Nothing deleted — please check the ID.")
        else:
            st.success("Template deleted")
            _log_event(f"Admin deleted template {del_ident.strip()} from {table}")
            st.rerun()
