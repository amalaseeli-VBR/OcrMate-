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


"""Customer Profiles page extracted from legacy.py.

This module keeps behavior identical by reusing legacy helpers via import.
"""

from typing import Any, Dict

# Import legacy symbols used by the extracted function body.
from docmate.legacy import *  # noqa: F401,F403

def render_customer_profiles(settings: Dict[str, Any]) -> None:
    st.title("Customer Profiles")
    st.caption(
        "Map CMS Customer ID ↔ SPOS Customer ID and maintain customer profile metadata for DocMate deployments. "
        "This mapping drives Dashboard customer selection and downstream VBR EPOS / Cloud Reporting exports."
    )

    conn = _get_db()

    # Load current profiles
    try:
        df = pd.read_sql_query(
            """
            SELECT
                rowid AS rowid,
                COALESCE(cms_customer_id,'') AS cms_customer_id,
                COALESCE(spos_customer_id,'') AS spos_customer_id,
                COALESCE(company_name,'') AS company_name,
                COALESCE(trading_name,'') AS trading_name,
                COALESCE(contact_name,'') AS contact_name,
                COALESCE(contact_email,'') AS contact_email,
                COALESCE(contact_phone,'') AS contact_phone,
                COALESCE(address_line1,'') AS address_line1,
                COALESCE(address_line2,'') AS address_line2,
                COALESCE(city,'') AS city,
                COALESCE(postcode,'') AS postcode,
                COALESCE(country,'') AS country,
                COALESCE(license_start_date,'') AS license_start_date,
                COALESCE(license_end_date,'') AS license_end_date,
                COALESCE(notes,'') AS notes
            FROM customer_profiles
            ORDER BY cms_customer_id
            """,
            conn,
        )
    except Exception as e:
        st.error(f"Failed to load customer profiles: {e}")
        df = pd.DataFrame(
            columns=[
                "rowid",
                "cms_customer_id",
                "spos_customer_id",
                "company_name",
                "trading_name",
                "contact_name",
                "contact_email",
                "contact_phone",
                "address_line1",
                "address_line2",
                "city",
                "postcode",
                "country",
                "license_start_date",
                "license_end_date",
                "notes",
            ]
        )

    st.subheader("Existing customer profiles")
    st.dataframe(df, width="stretch", hide_index=True)

    st.divider()

    # Edit selector
    st.subheader("Edit customer profile")
    edit_options = ["(New profile)"] + sorted(
        [x for x in df["cms_customer_id"].tolist() if str(x).strip()]
    )
    selected = st.selectbox(
        "Select CMS Customer ID to edit", options=edit_options, index=0, width="stretch"
    )

    current: Dict[str, Any] = {}
    if selected != "(New profile)":
        try:
            row = conn.execute(
                "SELECT * FROM customer_profiles WHERE UPPER(TRIM(cms_customer_id))=?",
                (str(selected).strip().upper(),),
            ).fetchone()
            current = dict(row) if row else {}
        except Exception:
            current = {}

    st.subheader("Add / update customer profile")

    def _v(k: str, default: str = "") -> str:
        v = current.get(k, default)
        return "" if v is None else str(v)

    with st.form("customer_profile_form", clear_on_submit=False):
        col_a, col_b = st.columns(2)
        with col_a:
            cms_id = st.text_input(
                "CMS Customer ID", value=_v("cms_customer_id"), placeholder="e.g., C02013"
            )
        with col_b:
            spos_id = st.text_input(
                "SPOS Customer ID", value=_v("spos_customer_id"), placeholder="e.g., S1529"
            )

        col1, col2 = st.columns(2)
        with col1:
            company_name = st.text_input(
                "Company name", value=_v("company_name"), placeholder="Legal entity name"
            )
            trading_name = st.text_input(
                "Trading name",
                value=_v("trading_name"),
                placeholder="Store / brand trading name",
            )
            contact_name = st.text_input("Contact name", value=_v("contact_name"))
            contact_email = st.text_input("Contact email", value=_v("contact_email"))
            contact_phone = st.text_input("Contact phone", value=_v("contact_phone"))
        with col2:
            address_line1 = st.text_input("Address line 1", value=_v("address_line1"))
            address_line2 = st.text_input("Address line 2", value=_v("address_line2"))
            city = st.text_input("Town/City", value=_v("city"))
            postcode = st.text_input("Postcode", value=_v("postcode"))
            country = st.text_input("Country", value=_v("country", "United Kingdom"))
            license_start_date = st.text_input(
                "Licence Start Date",
                value=_v("license_start_date"),
                placeholder="YYYY-MM-DD (e.g., 2026-02-01)",
            )
            license_end_date = st.text_input(
                "Licence End Date",
                value=_v("license_end_date"),
                placeholder="YYYY-MM-DD (e.g., 2026-12-31)",
            )

        notes = st.text_area("Notes", value=_v("notes"), height=90)

        submitted = st.form_submit_button("Save profile", width="stretch")

        if submitted:
            cms_id_clean = (cms_id or "").strip().upper()
            spos_id_clean = (spos_id or "").strip()

            if not cms_id_clean or not spos_id_clean:
                st.error("Please enter both CMS Customer ID and SPOS Customer ID.")
                return

            try:
                exists = conn.execute(
                    "SELECT 1 FROM customer_profiles WHERE UPPER(TRIM(cms_customer_id))=?",
                    (cms_id_clean,),
                ).fetchone()

                if exists:
                    conn.execute(
                        """
                        UPDATE customer_profiles
                        SET
                            spos_customer_id=?,
                            company_name=?,
                            trading_name=?,
                            contact_name=?,
                            contact_email=?,
                            contact_phone=?,
                            address_line1=?,
                            address_line2=?,
                            city=?,
                            postcode=?,
                            country=?,
                            license_start_date=?,
                            license_end_date=?,
                            notes=?
                        WHERE UPPER(TRIM(cms_customer_id))=?
                        """,
                        (
                            spos_id_clean,
                            (company_name or "").strip(),
                            (trading_name or "").strip(),
                            (contact_name or "").strip(),
                            (contact_email or "").strip(),
                            (contact_phone or "").strip(),
                            (address_line1 or "").strip(),
                            (address_line2 or "").strip(),
                            (city or "").strip(),
                            (postcode or "").strip(),
                            (country or "").strip(),
                            (license_start_date or "").strip(),
                            (license_end_date or "").strip(),
                            (notes or "").strip(),
                            cms_id_clean,
                        ),
                    )
                else:
                    conn.execute(
                        """
                        INSERT INTO customer_profiles (
                            cms_customer_id, spos_customer_id,
                            company_name, trading_name,
                            contact_name, contact_email, contact_phone,
                            address_line1, address_line2, city, postcode, country,
                            license_start_date, license_end_date,
                            notes
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            cms_id_clean,
                            spos_id_clean,
                            (company_name or "").strip(),
                            (trading_name or "").strip(),
                            (contact_name or "").strip(),
                            (contact_email or "").strip(),
                            (contact_phone or "").strip(),
                            (address_line1 or "").strip(),
                            (address_line2 or "").strip(),
                            (city or "").strip(),
                            (postcode or "").strip(),
                            (country or "").strip(),
                            (license_start_date or "").strip(),
                            (license_end_date or "").strip(),
                            (notes or "").strip(),
                        ),
                    )

                conn.commit()
                st.success(f"Saved customer profile: {cms_id_clean} ↔ {spos_id_clean}")
                st.rerun()

            except Exception as e:
                st.error(f"Failed to save customer profile: {e}")

    st.info(
        "Tip: This mapping is used in Dashboard for Customer (CMSID ↔ SPOS). "
        "Keep it accurate for VBR EPOS and Cloud Reporting exports."
    )

def render_customer_profiles(settings: Dict[str, Any]) -> None:
    _sync_from_legacy()
    st.title("Customer Profiles")
    st.caption(
        "Map CMS Customer ID ↔ SPOS Customer ID and maintain customer profile metadata for DocMate deployments. "
        "This mapping drives Dashboard customer selection and downstream VBR EPOS / Cloud Reporting exports."
    )

    conn = _get_db()

    # Load current profiles
    try:
        df = pd.read_sql_query(
            """
            SELECT
                rowid AS rowid,
                COALESCE(cms_customer_id,'') AS cms_customer_id,
                COALESCE(spos_customer_id,'') AS spos_customer_id,
                COALESCE(company_name,'') AS company_name,
                COALESCE(trading_name,'') AS trading_name,
                COALESCE(contact_name,'') AS contact_name,
                COALESCE(contact_email,'') AS contact_email,
                COALESCE(contact_phone,'') AS contact_phone,
                COALESCE(address_line1,'') AS address_line1,
                COALESCE(address_line2,'') AS address_line2,
                COALESCE(city,'') AS city,
                COALESCE(postcode,'') AS postcode,
                COALESCE(country,'') AS country,
                COALESCE(license_start_date,'') AS license_start_date,
                COALESCE(license_end_date,'') AS license_end_date,
                COALESCE(notes,'') AS notes
            FROM customer_profiles
            ORDER BY cms_customer_id
            """,
            conn,
        )
    except Exception as e:
        st.error(f"Failed to load customer profiles: {e}")
        df = pd.DataFrame(
            columns=[
                "rowid",
                "cms_customer_id",
                "spos_customer_id",
                "company_name",
                "trading_name",
                "contact_name",
                "contact_email",
                "contact_phone",
                "address_line1",
                "address_line2",
                "city",
                "postcode",
                "country",
                "license_start_date",
                "license_end_date",
                "notes",
            ]
        )

    st.subheader("Existing customer profiles")
    st.dataframe(df, width="stretch", hide_index=True)

    st.divider()

    # Edit selector
    st.subheader("Edit customer profile")
    edit_options = ["(New profile)"] + sorted(
        [x for x in df["cms_customer_id"].tolist() if str(x).strip()]
    )
    selected = st.selectbox(
        "Select CMS Customer ID to edit", options=edit_options, index=0, width="stretch"
    )

    current: Dict[str, Any] = {}
    if selected != "(New profile)":
        try:
            row = conn.execute(
                "SELECT * FROM customer_profiles WHERE UPPER(TRIM(cms_customer_id))=?",
                (str(selected).strip().upper(),),
            ).fetchone()
            current = dict(row) if row else {}
        except Exception:
            current = {}

    st.subheader("Add / update customer profile")

    def _v(k: str, default: str = "") -> str:
        v = current.get(k, default)
        return "" if v is None else str(v)

    with st.form("customer_profile_form", clear_on_submit=False):
        col_a, col_b = st.columns(2)
        with col_a:
            cms_id = st.text_input(
                "CMS Customer ID", value=_v("cms_customer_id"), placeholder="e.g., C02013"
            )
        with col_b:
            spos_id = st.text_input(
                "SPOS Customer ID", value=_v("spos_customer_id"), placeholder="e.g., S1529"
            )

        col1, col2 = st.columns(2)
        with col1:
            company_name = st.text_input(
                "Company name", value=_v("company_name"), placeholder="Legal entity name"
            )
            trading_name = st.text_input(
                "Trading name",
                value=_v("trading_name"),
                placeholder="Store / brand trading name",
            )
            contact_name = st.text_input("Contact name", value=_v("contact_name"))
            contact_email = st.text_input("Contact email", value=_v("contact_email"))
            contact_phone = st.text_input("Contact phone", value=_v("contact_phone"))
        with col2:
            address_line1 = st.text_input("Address line 1", value=_v("address_line1"))
            address_line2 = st.text_input("Address line 2", value=_v("address_line2"))
            city = st.text_input("Town/City", value=_v("city"))
            postcode = st.text_input("Postcode", value=_v("postcode"))
            country = st.text_input("Country", value=_v("country", "United Kingdom"))
            license_start_date = st.text_input(
                "Licence Start Date",
                value=_v("license_start_date"),
                placeholder="YYYY-MM-DD (e.g., 2026-02-01)",
            )
            license_end_date = st.text_input(
                "Licence End Date",
                value=_v("license_end_date"),
                placeholder="YYYY-MM-DD (e.g., 2026-12-31)",
            )

        notes = st.text_area("Notes", value=_v("notes"), height=90)

        submitted = st.form_submit_button("Save profile", width="stretch")

        if submitted:
            cms_id_clean = (cms_id or "").strip().upper()
            spos_id_clean = (spos_id or "").strip()

            if not cms_id_clean or not spos_id_clean:
                st.error("Please enter both CMS Customer ID and SPOS Customer ID.")
                return

            try:
                exists = conn.execute(
                    "SELECT 1 FROM customer_profiles WHERE UPPER(TRIM(cms_customer_id))=?",
                    (cms_id_clean,),
                ).fetchone()

                if exists:
                    conn.execute(
                        """
                        UPDATE customer_profiles
                        SET
                            spos_customer_id=?,
                            company_name=?,
                            trading_name=?,
                            contact_name=?,
                            contact_email=?,
                            contact_phone=?,
                            address_line1=?,
                            address_line2=?,
                            city=?,
                            postcode=?,
                            country=?,
                            license_start_date=?,
                            license_end_date=?,
                            notes=?
                        WHERE UPPER(TRIM(cms_customer_id))=?
                        """,
                        (
                            spos_id_clean,
                            (company_name or "").strip(),
                            (trading_name or "").strip(),
                            (contact_name or "").strip(),
                            (contact_email or "").strip(),
                            (contact_phone or "").strip(),
                            (address_line1 or "").strip(),
                            (address_line2 or "").strip(),
                            (city or "").strip(),
                            (postcode or "").strip(),
                            (country or "").strip(),
                            (license_start_date or "").strip(),
                            (license_end_date or "").strip(),
                            (notes or "").strip(),
                            cms_id_clean,
                        ),
                    )
                else:
                    conn.execute(
                        """
                        INSERT INTO customer_profiles (
                            cms_customer_id, spos_customer_id,
                            company_name, trading_name,
                            contact_name, contact_email, contact_phone,
                            address_line1, address_line2, city, postcode, country,
                            license_start_date, license_end_date,
                            notes
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            cms_id_clean,
                            spos_id_clean,
                            (company_name or "").strip(),
                            (trading_name or "").strip(),
                            (contact_name or "").strip(),
                            (contact_email or "").strip(),
                            (contact_phone or "").strip(),
                            (address_line1 or "").strip(),
                            (address_line2 or "").strip(),
                            (city or "").strip(),
                            (postcode or "").strip(),
                            (country or "").strip(),
                            (license_start_date or "").strip(),
                            (license_end_date or "").strip(),
                            (notes or "").strip(),
                        ),
                    )

                conn.commit()
                st.success(f"Saved customer profile: {cms_id_clean} ↔ {spos_id_clean}")
                st.rerun()

            except Exception as e:
                st.error(f"Failed to save customer profile: {e}")

    st.info(
        "Tip: This mapping is used in Dashboard for Customer (CMSID ↔ SPOS). "
        "Keep it accurate for VBR EPOS and Cloud Reporting exports."
    )

