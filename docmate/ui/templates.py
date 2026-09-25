"""Templates page extracted from legacy.py.

This module keeps behavior identical by reusing legacy helpers via import.
"""
from __future__ import annotations

from typing import Any, Dict

# Import legacy symbols used by the extracted function body.
from docmate.legacy import *  # noqa: F401,F403

def render_templates(settings: Dict[str, Any]) -> None:
    st.title("Template Management")
    st.caption(
        "Manage supplier and customer templates used by DocMate Auto processing. "
        "Templates help DocMate extract purchase invoices accurately for VBR deployments."
    )

    conn = _get_db()
    tab_sup, tab_cust, tab_builder = st.tabs(["Supplier Templates", "Customer Templates", "Template Builder / Test"])

    with tab_sup:
        st.subheader("Supplier Templates")
        st.caption("Templates matched by supplier name/keywords. Used in Auto mode before OCR fallback.")
        _render_template_table("supplier_templates", "Supplier Templates")

    with tab_cust:
        st.subheader("Customer Templates")
        st.caption("Customer-specific templates/overrides (optional).")
        _render_template_table("customer_templates", "Customer Templates")

    with tab_builder:
        st.subheader("Template Builder / Test")
        st.caption("Create, edit and test templates. Use carefully so invoice parsing remains stable.")
        try:
            _render_template_builder(settings)
        except Exception as e:
            st.error(f"Template builder error: {e}")
