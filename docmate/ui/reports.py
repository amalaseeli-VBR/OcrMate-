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



"""Reports UI (extracted from legacy.py)."""

import docmate.legacy as _legacy  # type: ignore

# Reuse legacy imports/helpers
globals().update({k: v for k, v in _legacy.__dict__.items() if k not in globals()})

def render_reports() -> None:
    st.subheader("Reports")
    st.info("Reports module is planned: profitability KPIs, POR exceptions, selling vs RRP, and export to VBR Cloud Reporting. For now, use Registers export while we stabilise supplier parsing.")
    st.markdown("- Upcoming: **Low POR alerts**, **Selling below RRP**, **VAT code anomalies**, **supplier price drift**.")

def render_reports() -> None:
    _sync_from_legacy()
    st.subheader("Reports")
    st.info("Reports module is planned: profitability KPIs, POR exceptions, selling vs RRP, and export to VBR Cloud Reporting. For now, use Registers export while we stabilise supplier parsing.")
    st.markdown("- Upcoming: **Low POR alerts**, **Selling below RRP**, **VAT code anomalies**, **supplier price drift**.")






# --- Thin wrappers to extracted modules

