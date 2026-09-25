from __future__ import annotations

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

def render_database_management() -> None:
    _sync_from_legacy()
    """
    Admin-only: Product Master database (SQLite) + CSV import/export.

    This is the foundation for future margin intelligence:
      - Compare invoice costs vs current selling prices (POR / margin)
      - Alert when POR is below target, selling below Std RRP, etc.
      - Publish insights into VBR Cloud Reporting / SPOS Cloud-style dashboards
    """
    st.subheader("Database Management")

    # ---- Table schema (Products Master) ----
    # NOTE: SQLite is used locally for speed and portability. This can later be mirrored to SQL Server / Azure SQL
    # for centralised reporting (aligns with VBR Cloud Reporting / multi-site deployments).
    st.info(
        "Products Master lives in the local SQLite database (docmate.db). "
        "You can import a customer product file (CSV), review records, and export. "
        "This enables future profit-margin and pricing exception reports."
    )


    # ---- Helpers ----
    def _norm_col(name: str) -> str:
        s = (name or "").strip().lower()
        s = re.sub(r"[\s\-\/\(\)\.]+", "", s)
        s = s.replace("%", "pct")
        return s

    # Accepted columns (user-friendly CSV headers) -> internal column name
    col_map = {
        "siteid": "site_id",
        "site": "site_id",
        "tradingname": "trading_name",
        "operatingcompany": "operating_company",
        "pluno": "plu_no",
        "plu": "plu_no",
        "productname": "product_name",
        "deptno": "dept_no",
        "deptname": "dept_name",
        "unitsize": "unit_size",
        "pvatcode": "pvat_code",
        "pvatrate": "pvat_rate",
        "sellingprice": "selling_price",
        "supplierno": "supplier_no",
        "suppliername": "supplier_name",
        "suppliercode": "supplier_code",
        "casepack": "case_pack",
        "casecost": "case_cost",
        "vatcode": "vat_code",
        "vatrate": "vat_rate",
        "unitcost": "unit_cost",
        "stdrrpincvat": "std_rrp_inc_vat",
        "stdrrpexcvat": "std_rrp_exc_vat",
        "ppor": "ppor",
    }

    required = ["plu_no", "product_name"]

    with st.expander("Import Products CSV", expanded=True):
        st.caption(
            "Upload a CSV with columns such as: PLUNo, ProductName, DeptNo, DeptName, UnitSize, "
            "PVATCode, PVATRate, SellingPrice, SupplierNo, SupplierName, SupplierCode, CasePack, "
            "CaseCost, VatCode, VatRate, UnitCost, STDRRP(incVAT), STDRRP(exc.VAT), PPOR. "
            "Optional: SiteID, Trading Name, Operating Company."
        )
        up = st.file_uploader("Choose CSV file", type=["csv"], key="products_csv_uploader")
        if up is not None:
            try:
                df = pd.read_csv(up)
            except Exception:
                # Try with alternate encodings/delimiters
                up.seek(0)
                df = pd.read_csv(up, encoding_errors="ignore")

            if df is None or df.empty:
                st.warning("CSV appears empty.")
            else:
                # Build rename mapping
                rename = {}
                for c in df.columns:
                    k = _norm_col(str(c))
                    if k in col_map:
                        rename[c] = col_map[k]
                df = df.rename(columns=rename)

                missing = [c for c in required if c not in df.columns]
                if missing:
                    st.error(f"Missing required columns: {', '.join(missing)}")
                else:
                    # Ensure all internal columns exist
                    internal_cols = [
                        "site_id","plu_no","product_name","dept_no","dept_name","unit_size",
                        "pvat_code","pvat_rate","selling_price","supplier_no","supplier_name","supplier_code",
                        "case_pack","case_cost","vat_code","vat_rate","unit_cost","std_rrp_inc_vat","std_rrp_exc_vat","ppor",
                        "trading_name","operating_company"
                    ]
                    for c in internal_cols:
                        if c not in df.columns:
                            df[c] = None

                    # Normalise values
                    df["site_id"] = df["site_id"].fillna("").astype(str).str.strip()
                    df["plu_no"] = df["plu_no"].astype(str).str.strip()
                    df["product_name"] = df["product_name"].fillna("").astype(str).str.strip()

                    # Numeric conversions
                    num_cols = [
                        "pvat_rate","selling_price","case_pack","case_cost","vat_rate","unit_cost","std_rrp_inc_vat","std_rrp_exc_vat","ppor"
                    ]
                    for c in num_cols:
                        try:
                            df[c] = df[c].apply(_safe_float)
                        except Exception:
                            pass

                    df["updated_at"] = _uk_now_iso()

                    st.write("Preview (first 50 rows):")
                    st.dataframe(df[internal_cols + ["updated_at"]].head(50),  use_container_width=True)

                    # Import action
                    if st.button("Import / Upsert into Products Master", type="primary"):
                        try:
                            conn = _db_connect()
                            cur = conn.cursor()
                            cur.execute("BEGIN")
                            sql = """
                                INSERT INTO products_master(
                                    site_id, plu_no, product_name, dept_no, dept_name, unit_size,
                                    pvat_code, pvat_rate, selling_price, supplier_no, supplier_name, supplier_code,
                                    case_pack, case_cost, vat_code, vat_rate, unit_cost,
                                    std_rrp_inc_vat, std_rrp_exc_vat, ppor, trading_name, operating_company, updated_at
                                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                                ON CONFLICT(site_id, plu_no) DO UPDATE SET
                                    product_name=excluded.product_name,
                                    dept_no=excluded.dept_no,
                                    dept_name=excluded.dept_name,
                                    unit_size=excluded.unit_size,
                                    pvat_code=excluded.pvat_code,
                                    pvat_rate=excluded.pvat_rate,
                                    selling_price=excluded.selling_price,
                                    supplier_no=excluded.supplier_no,
                                    supplier_name=excluded.supplier_name,
                                    supplier_code=excluded.supplier_code,
                                    case_pack=excluded.case_pack,
                                    case_cost=excluded.case_cost,
                                    vat_code=excluded.vat_code,
                                    vat_rate=excluded.vat_rate,
                                    unit_cost=excluded.unit_cost,
                                    std_rrp_inc_vat=excluded.std_rrp_inc_vat,
                                    std_rrp_exc_vat=excluded.std_rrp_exc_vat,
                                    ppor=excluded.ppor,
                                    trading_name=excluded.trading_name,
                                    operating_company=excluded.operating_company,
                                    updated_at=excluded.updated_at
                            """
                            rows = df[
                                [
                                    "site_id","plu_no","product_name","dept_no","dept_name","unit_size",
                                    "pvat_code","pvat_rate","selling_price","supplier_no","supplier_name","supplier_code",
                                    "case_pack","case_cost","vat_code","vat_rate","unit_cost","std_rrp_inc_vat","std_rrp_exc_vat","ppor",
                                    "trading_name","operating_company","updated_at"
                                ]
                            ].values.tolist()
                            cur.executemany(sql, rows)
                            conn.commit()
                            conn.close()
                            st.success(f"Imported/updated {len(rows)} rows into products_master.")
                        except Exception as e:
                            try:
                                conn.rollback()
                                conn.close()
                            except Exception:
                                pass
                            st.error(f"Import failed: {e}")

    st.divider()

    # ---- Browse / Export ----
    try:
        conn = _db_connect()
        cur = conn.cursor()
        cur.execute("SELECT COUNT(1) FROM products_master")
        total = int(cur.fetchone()[0] or 0)
        cur.execute("SELECT COUNT(DISTINCT site_id) FROM products_master")
        sites_n = int(cur.fetchone()[0] or 0)
        conn.close()
    except Exception:
        total, sites_n = 0, 0

    c1, c2, c3 = st.columns([1,1,2])
    c1.metric("Total products", total)
    c2.metric("Sites", sites_n)
    c3.caption("Tip: Import product files from your VBR EPOS/SPOS export, then we can build margin and pricing exception reports in the Reports section.")

    # Site filter
    site_filter = ""
    try:
        conn = _db_connect()
        cur = conn.cursor()
        cur.execute("SELECT DISTINCT site_id FROM products_master ORDER BY site_id")
        site_list = [r[0] for r in cur.fetchall()]
        conn.close()
    except Exception:
        site_list = []

    if site_list:
        site_filter = st.selectbox("Filter by SiteID", options=["(All)"] + site_list, index=0)
        if site_filter == "(All)":
            site_filter = ""

    # Load data
    try:
        conn = _db_connect()
        cur = conn.cursor()
        if site_filter:
            cur.execute("SELECT * FROM products_master WHERE site_id=? ORDER BY plu_no", (site_filter,))
        else:
            cur.execute("SELECT * FROM products_master ORDER BY site_id, plu_no")
        rows = cur.fetchall()
        cols = [d[0] for d in cur.description]
        conn.close()
        dfv = pd.DataFrame(rows, columns=cols)
    except Exception as e:
        st.error(f"Read error: {e}")
        return

    if dfv.empty:
        st.warning("No products found. Import a CSV above.")
        return

    st.write("Products Master (preview):")
    st.dataframe(dfv.head(200),  use_container_width=True)

    # Export
    csv_bytes = dfv.to_csv(index=False).encode("utf-8")
    st.download_button(
        "Download Products CSV",
        data=csv_bytes,
        file_name=f"DocMate_ProductsMaster_{_uk_now_iso()[:10]}.csv",
        mime="text/csv",
    )

    # Clear per site (danger)
    with st.expander("Danger Zone (Admin)", expanded=False):
        st.warning("These actions remove data from the local SQLite database (docmate.db). Use carefully.")
        if site_list:
            del_site = st.selectbox("Select SiteID to clear", options=["(Select)"] + site_list, index=0, key="del_site_products")
        else:
            del_site = "(Select)"
        confirm = st.checkbox("I understand this will permanently delete product records.", key="confirm_delete_products")
        if st.button("Clear products for selected site", disabled=(del_site in ("(Select)", "") or not confirm)):
            try:
                conn = _db_connect()
                cur = conn.cursor()
                cur.execute("DELETE FROM products_master WHERE site_id=?", (del_site,))
                conn.commit()
                conn.close()
                st.success(f"Cleared products for SiteID={del_site}.")
                st.rerun()
            except Exception as e:
                st.error(f"Delete failed: {e}")
