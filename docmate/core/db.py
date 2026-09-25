from __future__ import annotations
import os, sqlite3
from typing import Any, Dict, List, Optional, Tuple

DATA_DIR='DocMate_DATA'
DB_FILE=os.path.join(DATA_DIR,'docmate.db')

def _ensure_data_dir()->None:
    os.makedirs(DATA_DIR, exist_ok=True)

def _db_connect() -> sqlite3.Connection:
    _ensure_data_dir()
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn




# Backward-compatible alias used by older renderers
def _get_db():
    return _db_connect()
def _db_init() -> None:
    """Initialise DB and run safe migrations (keeps existing v1.02 tables)."""
    conn = _db_connect()
    cur = conn.cursor()

    # Existing register tables from v1.02 (keep names for backwards compatibility)
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS purchase_invoices (
            inv_key TEXT PRIMARY KEY,
            supplier_name TEXT,
            customer_name TEXT,
            invoice_date TEXT,
            invoice_number TEXT,
            total_lines INTEGER,
            total_case_qty REAL,
            total_units REAL,
            net_amount REAL,
            vat_amount REAL,
            gross_amount REAL,
            engine_used TEXT,
            file_name TEXT,
            processed_at TEXT,
            updated_at TEXT
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS purchase_invoice_items (
            inv_key TEXT,
            line_no INTEGER,
            code TEXT,
            description TEXT,
            case_pack REAL,
            unit_size TEXT,
            qty_cases REAL,
            unit_price REAL,
            line_net REAL,
            vat_code TEXT,
            vat_rate REAL,
            vat_amount REAL,
            line_gross REAL,
            std_rrp REAL,
            por REAL,
            is_void INTEGER,
            void_note TEXT,
            PRIMARY KEY (inv_key, line_no)
        )
        """
    )

    # Migration: multi-register support
    try:
        cur.execute("ALTER TABLE purchase_invoices ADD COLUMN doc_type TEXT")
    except Exception:
        pass
    try:
        cur.execute("UPDATE purchase_invoices SET doc_type='Purchase Invoice' WHERE doc_type IS NULL OR doc_type='' ")
    except Exception:
        pass

    # Migration: preserve original casePack text
    try:
        cur.execute("ALTER TABLE purchase_invoice_items ADD COLUMN case_pack_text TEXT")
    except Exception:
        pass

    # Migration: preserve the full extracted model alongside stable reporting columns.
    # This allows new supplier/header/line fields to be retained without adding a
    # physical column for every template variation.
    try:
        cur.execute("ALTER TABLE purchase_invoices ADD COLUMN header_json TEXT")
    except Exception:
        pass
    try:
        cur.execute("ALTER TABLE purchase_invoice_items ADD COLUMN line_json TEXT")
    except Exception:
        pass

    # Supplier templates (existing)
    cur.execute(
        """CREATE TABLE IF NOT EXISTS supplier_templates (
            template_id TEXT PRIMARY KEY,
            display_id TEXT,
            supplier_name TEXT,
            vat_number TEXT,
            awrs_number TEXT,
            match_keywords_json TEXT,
            parser_type TEXT,
            requires_cloud INTEGER DEFAULT 0,
            config_json TEXT,
            created_at TEXT,
            updated_at TEXT
        )"""
    )

    # Customer templates (new)
    cur.execute(
        """CREATE TABLE IF NOT EXISTS customer_templates (
            template_id TEXT PRIMARY KEY,
            display_id TEXT,
            customer_name TEXT,
            match_keywords_json TEXT,
            parser_type TEXT,
            requires_cloud INTEGER DEFAULT 0,
            config_json TEXT,
            created_at TEXT,
            updated_at TEXT
        )"""
    )

    
    # Customer profiles (Admin managed; used for exception reporting and customer communications)
    cur.execute(
        """CREATE TABLE IF NOT EXISTS customer_profiles (
            cms_customer_id        TEXT PRIMARY KEY,
            customer_name        TEXT NOT NULL DEFAULT '',
            spos_customer_id     TEXT DEFAULT '',
            trading_name         TEXT DEFAULT '',
            company_name         TEXT DEFAULT '',
            vat_registration_no  TEXT DEFAULT '',
            contact_no           TEXT DEFAULT '',
            email_address        TEXT DEFAULT '',
            address              TEXT DEFAULT '',
            notes                TEXT DEFAULT '',
            created_at           TEXT,
            updated_at           TEXT
        )"""
    )

    # Safe migration: customer_profiles may evolve (CMSID/SPOS mapping).
    try:
        cols = [r["name"] for r in cur.execute("PRAGMA table_info(customer_profiles)").fetchall()]

        # Legacy: customer_code -> cms_customer_id
        if "customer_code" in cols and "cms_customer_id" not in cols:
            try:
                cur.execute("ALTER TABLE customer_profiles RENAME COLUMN customer_code TO cms_customer_id")
            except Exception:
                # If rename not supported, add new column and backfill
                cur.execute("ALTER TABLE customer_profiles ADD COLUMN cms_customer_id TEXT DEFAULT ''")
                cur.execute("UPDATE customer_profiles SET cms_customer_id = COALESCE(cms_customer_id, customer_code)")
            cols = [r["name"] for r in cur.execute("PRAGMA table_info(customer_profiles)").fetchall()]

        # Legacy: email -> email_address (keep old email if present)
        if "email" in cols and "email_address" not in cols:
            try:
                cur.execute("ALTER TABLE customer_profiles ADD COLUMN email_address TEXT DEFAULT ''")
            except Exception:
                pass
            try:
                cur.execute("UPDATE customer_profiles SET email_address = COALESCE(email_address, email)")
            except Exception:
                pass
            cols = [r["name"] for r in cur.execute("PRAGMA table_info(customer_profiles)").fetchall()]

        # Add missing columns (older installs)
        def _add_col(col: str, ddl: str) -> None:
            nonlocal cols
            if col not in cols:
                try:
                    cur.execute(f"ALTER TABLE customer_profiles ADD COLUMN {ddl}")
                    cols.append(col)
                except Exception:
                    pass

        _add_col("cms_customer_id", "cms_customer_id TEXT DEFAULT ''")
        _add_col("spos_customer_id", "spos_customer_id TEXT DEFAULT ''")
        _add_col("customer_name", "customer_name TEXT NOT NULL DEFAULT ''")
        _add_col("trading_name", "trading_name TEXT DEFAULT ''")
        _add_col("company_name", "company_name TEXT DEFAULT ''")
        _add_col("vat_registration_no", "vat_registration_no TEXT DEFAULT ''")
        _add_col("contact_no", "contact_no TEXT DEFAULT ''")
        _add_col("email_address", "email_address TEXT DEFAULT ''")
        # Extended fields (for CMSID ↔ SPOS profile management)
        _add_col("contact_name", "contact_name TEXT DEFAULT ''")
        _add_col("contact_email", "contact_email TEXT DEFAULT ''")
        _add_col("contact_phone", "contact_phone TEXT DEFAULT ''")
        _add_col("address_line1", "address_line1 TEXT DEFAULT ''")
        _add_col("address_line2", "address_line2 TEXT DEFAULT ''")
        _add_col("city", "city TEXT DEFAULT ''")
        _add_col("postcode", "postcode TEXT DEFAULT ''")
        _add_col("country", "country TEXT DEFAULT ''")

        _add_col("address", "address TEXT DEFAULT ''")
        # Backfill new fields from legacy columns (safe no-op if already populated)
        try:
            cur = conn.cursor()
            cur.execute("UPDATE customer_profiles SET contact_name = COALESCE(NULLIF(contact_name,''), NULLIF(customer_name,''))")
            cur.execute("UPDATE customer_profiles SET contact_email = COALESCE(NULLIF(contact_email,''), NULLIF(email_address,''))")
            cur.execute("UPDATE customer_profiles SET contact_phone = COALESCE(NULLIF(contact_phone,''), NULLIF(contact_no,''))")
            cur.execute("UPDATE customer_profiles SET address_line1 = COALESCE(NULLIF(address_line1,''), NULLIF(address,''))")
            conn.commit()
        except Exception:
            pass
        _add_col("notes", "notes TEXT DEFAULT ''")
        _add_col("created_at", "created_at TEXT")
        _add_col("updated_at", "updated_at TEXT")
    except Exception:
        # Never block app start due to migrations
        pass

# System docs and version history
    # NOTE: We migrate defensively because earlier builds used slightly different column names.
    cur.execute(
        """CREATE TABLE IF NOT EXISTS version_history (
            version TEXT PRIMARY KEY,
            release_date TEXT,
            release_note TEXT,
            created_at TEXT
        )"""
    )

    # Safe migration: tolerate legacy schemas that used columns: 'date' and/or 'notes'.
    try:
        cols = [r["name"] for r in cur.execute("PRAGMA table_info(version_history)").fetchall()]

        # Add missing columns (SQLite allows ADD COLUMN)
        if "release_date" not in cols:
            cur.execute("ALTER TABLE version_history ADD COLUMN release_date TEXT")
            cols.append("release_date")
        if "release_note" not in cols:
            cur.execute("ALTER TABLE version_history ADD COLUMN release_note TEXT")
            cols.append("release_note")
        if "created_at" not in cols:
            cur.execute("ALTER TABLE version_history ADD COLUMN created_at TEXT")
            cols.append("created_at")

        # Backfill from legacy columns if present
        if "date" in cols:
            cur.execute("UPDATE version_history SET release_date = COALESCE(release_date, date)")
        if "notes" in cols:
            cur.execute("UPDATE version_history SET release_note = COALESCE(release_note, notes)")
    except Exception:
        # Never block app start due to migrations
        pass

    cur.execute(
        """CREATE TABLE IF NOT EXISTS system_docs (
            doc_key TEXT PRIMARY KEY,
            title TEXT,
            content_md TEXT,
            updated_at TEXT
        )"""
    )

    # Insert version record once
    try:
        cur.execute(
            "INSERT INTO version_history(version, release_date, release_note, created_at) VALUES(?,?,?,?) "
            "ON CONFLICT(version) DO UPDATE SET "
            "release_date=excluded.release_date, release_note=excluded.release_note, created_at=excluded.created_at",
            (APP_VERSION, APP_DATE, APP_VERSION_NOTE, _uk_now_iso()),
        )
    except Exception:
        pass

    # Products master (for customer product imports / margin analysis)
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS products_master (
            site_id            TEXT NOT NULL DEFAULT '',
            plu_no             TEXT NOT NULL,
            product_name       TEXT NOT NULL DEFAULT '',
            dept_no            TEXT,
            dept_name          TEXT,
            unit_size          TEXT,
            pvat_code          TEXT,
            pvat_rate          REAL,
            selling_price      REAL,
            supplier_no        TEXT,
            supplier_name      TEXT,
            supplier_code      TEXT,
            case_pack          REAL,
            case_cost          REAL,
            vat_code           TEXT,
            vat_rate           REAL,
            unit_cost          REAL,
            std_rrp_inc_vat    REAL,
            std_rrp_exc_vat    REAL,
            ppor               REAL,
            trading_name       TEXT,
            operating_company  TEXT,
            updated_at         TEXT,
            PRIMARY KEY (site_id, plu_no)
        )
        """
    )

    # Insert PRD placeholder once
    try:
        cur.execute(
            "INSERT OR IGNORE INTO system_docs(doc_key, title, content_md, updated_at) VALUES(?,?,?,?)",
            (
                "PRD",
                "DocMate Technical PRD (Draft)",
                _default_prd_markdown(),
                _uk_now_iso(),
            ),
        )
    except Exception:
        pass

    
    # Seed full v1.04 history (v1.04.001+) so System Information always shows a complete timeline.
    # We only fill missing rows/fields (never overwrite user-edited notes).
    seed_history = [
        ("v1.04.001", "2026-02-01", "Start of v1.04 line: dashboard refresh + supplier parsers (Booker/Parfetts/DWG) groundwork."),
        ("v1.04.009", "2026-02-01", "UI iterations + reconciliation + VAT breakdown layout improvements."),
        ("v1.04.010", "2026-02-01", "Bug fixes + dashboard rendering tweaks."),
        ("v1.04.011", "2026-02-01", "Fixed missing helper functions; stabilised dashboard render."),
        ("v1.04.012", "2026-02-01", "Improved metrics (cases/stock units/products) and VAT breakdown box."),
        ("v1.04.013", "2026-02-01", "UI refinements: doc icon, frames, better layout; initial DWG Auto handling."),
        ("v1.04.014", "2026-02-01", "Reasons/tips panel tidy; reconciliation box alignment."),
        ("v1.04.015", "2026-02-01", "Removed duplicate reasons lines; simplified recon notes."),
        ("v1.04.016", "2026-02-01", "Dashboard polish + bug fixes (items_for_metrics)."),
        ("v1.04.019", "2026-02-01", "Auto vs Cloud routing improvements; supplier parser stability work."),
        ("v1.04.020", "2026-02-01", "DWG Auto routing fix attempt + header/line calculations."),
        ("v1.04.022", "2026-02-01", "Navigation update scaffold for Database Management; DWG parsing improvements."),
        ("v1.04.023", "2026-02-01", "Database Management groundwork; CSV import planning; DWG auto/description fixes."),
        ("v1.04.024", "2026-02-02", "DWG footer line capture improvements; report/export scaffolding."),
        ("v1.04.025", "2026-02-02", "Results frames split; collapsible line items section; recalculation prep."),
        ("v1.04.026", "2026-02-02", "Default DWG VAT mapping; VAT breakdown table rows increased."),
        ("v1.04.027", "2026-02-02", "Version history + DB schema migration attempt."),
        ("v1.04.028", "2026-02-02", "Bug fixes: supplier detection helper + VAT breakdown max rows."),
        ("v1.04.029", "2026-02-02", "Bug fixes: _fmt_money and VAT breakdown rendering."),
        ("v1.04.030", "2026-02-02", "Dhamecha supplier parser initial support + template VAT mapping field."),
        ("v1.04.031", "2026-02-03", "Template VAT mapping auto-populates from extracted items (incl. Dhamecha A/B/Z). VAT map detection helper added; items auto-recalculated before UI/VAT breakdown."),
        ("v1.04.032", "2026-02-03", "Bug fixes: VAT breakdown helper alignment + supplier-specific VAT map routing refinements."),
        ("v1.04.033", "2026-02-03", "Hotfix: Dhamecha helper + VAT breakdown max_rows + DWG S/A VAT/gross calc fixes."),
        ("v1.04.034", "2026-02-03", "Fix: Dhamecha line VATRate/VATAmount population and VAT Breakdown box uses correct row keys (netAmount/vatAmount/grossAmount)."),
        ("v1.04.036", "2026-02-03", "Fix: Prevent NaN propagation in VAT breakdown/reconciliation after manual edits; Dhamecha VAT code/rate enforcement improved (item_map override + header-rate correction)."),
    ]

    for v, d, n in seed_history:
        # Create row if missing
        cur.execute(
            "INSERT OR IGNORE INTO version_history(version, release_date, release_note) VALUES(?,?,?)",
            (v, d, n),
        )
        # Fill missing fields only
        cur.execute("SELECT release_date, release_note FROM version_history WHERE version=?", (v,))
        row = cur.fetchone()
        if row:
            rd, rn = row[0], row[1]
            if (rd is None) or (str(rd).strip() == ""):
                cur.execute("UPDATE version_history SET release_date=? WHERE version=?", (d, v))
            if (rn is None) or (str(rn).strip() == ""):
                cur.execute("UPDATE version_history SET release_note=? WHERE version=?", (n, v))

    # -------------------------
    # v1.07 migrations
    # -------------------------
    # Customer profiles: add licence dates (displayed on login support panel + expiry badge)
    try:
        cols = [r["name"] for r in cur.execute("PRAGMA table_info(customer_profiles)").fetchall()]
        if "license_start_date" not in cols:
            cur.execute("ALTER TABLE customer_profiles ADD COLUMN license_start_date TEXT DEFAULT ''")
        if "license_end_date" not in cols:
            cur.execute("ALTER TABLE customer_profiles ADD COLUMN license_end_date TEXT DEFAULT ''")
    except Exception:
        pass

    # Purchase register: store CMS/SPOS IDs with each saved invoice row
    for _ddl in [
        "cms_customer_id TEXT DEFAULT ''",
        "spos_customer_id TEXT DEFAULT ''",
        "spos_site_id TEXT DEFAULT ''",
    ]:
        try:
            cur.execute(f"ALTER TABLE purchase_invoices ADD COLUMN {_ddl}")
        except Exception:
            pass

    # Dedicated local index table (requested)
    try:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS invoice_register_index (
                inv_key           TEXT PRIMARY KEY,
                doc_type          TEXT DEFAULT '',
                cms_customer_id   TEXT DEFAULT '',
                spos_customer_id  TEXT DEFAULT '',
                spos_site_id      TEXT DEFAULT '',
                supplier_name     TEXT DEFAULT '',
                invoice_number    TEXT DEFAULT '',
                invoice_date      TEXT DEFAULT '',
                engine_used       TEXT DEFAULT '',
                source_file_name  TEXT DEFAULT '',
                created_at        TEXT,
                updated_at        TEXT
            )
            """
        )
    except Exception:
        pass



    conn.commit()
    conn.close()
def _db_exec(query: str, params: Tuple[Any, ...] = ()) -> None:
    conn = _db_connect()
    cur = conn.cursor()
    cur.execute(query, params)
    conn.commit()
    conn.close()

# Backwards-compat alias (older UI blocks)
_db_execute = _db_exec
def _db_fetchall(query: str, params: Tuple[Any, ...] = ()) -> List[Dict[str, Any]]:
    conn = _db_connect()
    cur = conn.cursor()
    rows = cur.execute(query, params).fetchall()
    conn.close()
    # sqlite3.Row does not implement dict.get(); many UI blocks expect .get().
    # Convert rows to plain dicts once at the boundary to keep the rest of the app consistent.
    return [dict(r) for r in rows] if rows else []
def _db_fetchone(query: str, params: Tuple[Any, ...] = ()) -> Optional[Dict[str, Any]]:
    conn = _db_connect()
    cur = conn.cursor()
    row = cur.execute(query, params).fetchone()
    conn.close()
    return dict(row) if row is not None else None


# -------------------------
# Templates
# -------------------------

STOP_WORDS = {"LTD", "LIMITED", "PLC", "LLP", "CO", "COMPANY", "INC", "INCORPORATED", "THE"}
def _db_has_booker_supplier_template() -> bool:
    try:
        row = _db_fetchone("SELECT template_id FROM supplier_templates WHERE UPPER(TRIM(parser_type))='BOOKER' LIMIT 1")
        return bool(row)
    except Exception:
        return False
