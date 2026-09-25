# DocMate Cloud — Booker-first Templates + Multi-Registers (VBR)
# Version: v1.07.001_r06 (2026-02-10)
# Software Development Company: Visual Business Retail Ltd
# Purpose:
# - Reliable Purchase/Service/Sales/Commission document processing with:
#   * Booker (BKL) fast extraction (PDF text layer first; OCR fallback)
#   * Supplier + Customer templates (editable + deletable by Admin)
#   * Multi-register saving by Document Type
#   * System Information tree (Version History + PRD preview)
# - Designed to integrate downstream with VBR EPOS, VBR Cloud Reporting, Loyalty, and Online Ordering.
# - Next patch note: Add expander “Cloud AI Raw Output (first 50k)” to view Cloud AI raw text/JSON in compact form.

from __future__ import annotations


def _safe_filename(s: str) -> str:
    """Make a string safe to use as a filename (Windows-friendly)."""
    s = (s or "").strip()
    s = re.sub(r'[\\/:*?\"<>|]+', "_", s)
    s = re.sub(r"\s+", " ", s).strip()
    s = s.replace(" ", "_")
    return s[:120] if len(s) > 120 else s


import base64
import dataclasses
import hashlib
import copy
import io
import json
import os
import pathlib
import traceback
import re
import ast
import math
import sqlite3
import time
import warnings
from dataclasses import dataclass
from datetime import datetime, timezone
import datetime as dt
from typing import Any, Dict, List, Optional, Tuple

import streamlit as st

import io
from datetime import datetime


def _ddmmyyyy_to_iso(s: str) -> str:
    """Convert common UK date formats to ISO yyyy-mm-dd.

    Accepts: dd/mm/yy, dd/mm/yyyy, dd-mm-yy, dd-mm-yyyy
    Returns original string if parsing fails.
    """
    if not s:
        return s
    t = str(s).strip()
    for fmt in ("%d/%m/%y", "%d/%m/%Y", "%d-%m-%y", "%d-%m-%Y"):
        try:
            return datetime.strptime(t, fmt).strftime("%Y-%m-%d")
        except Exception:
            pass
    return t




def _clean_booker_customer_name(nm: str) -> str:
    """Booker 'CUSTOMER <no> <name>' lines sometimes have PO/Customer PO appended.
    This strips common PO suffix patterns without damaging genuine business names.
    """
    if not nm:
        return nm
    s = str(nm).strip()
    # Remove trailing PO suffixes like 'PO 12345', 'P/O NO 12345', 'PO NUMBER: 12345'
    s = re.sub(r"\s+P\s*/\s*O\s*(?:NUMBER|NO\.?|#)?\s*[:\-]?\s*[A-Z0-9\-\/]{3,}.*$", "", s, flags=re.I)
    s = re.sub(r"\s+PO\s*(?:NUMBER|NO\.?|#)?\s*[:\-]?\s*[A-Z0-9\-\/]{3,}.*$", "", s, flags=re.I)
    # If still contains an obvious ' PO ' token near the end followed by digits, trim from there
    m = re.search(r"\s+PO\s+[0-9]{3,}\s*$", s, flags=re.I)
    if m:
        s = s[: m.start()].strip()
    return s.strip(" -:|\t")





def _guess_supplier_from_filename(filename: str) -> str:
    """Lightweight supplier guesser based on filename (used as a fallback)."""
    f = (filename or "").lower()
    if "booker" in f:
        return "BOOKER"
    if "parfett" in f:
        return "PARFETTS"
    if "dwg" in f or "drinks" in f or "wholesale group" in f:
        return "DWG"
    return ""



import streamlit.components.v1 as components
# Reduce noisy warnings (does not affect extraction)
warnings.filterwarnings(
    "ignore",
    category=FutureWarning,
    message=r"The behavior of DataFrame concatenation with empty or all-NA entries is deprecated.*",
)

# Optional deps
try:
    import pandas as pd
except Exception:
    pd = None

try:
    import fitz  # PyMuPDF
except Exception:
    fitz = None

try:
    from PIL import Image, ImageOps
except Exception:
    Image = None
    ImageOps = None

# Lazy OCR imports (pytesseract / easyocr / paddleocr)

try:
    from zoneinfo import ZoneInfo
except Exception:
    ZoneInfo = None

# -------------------------
# App constants
# -------------------------
APP_TITLE = "DocMate"
APP_VERSION = 'v1.07.001_r06'
APP_DATE = "2026-02-10"
APP_RELEASE_DATE = '2026-02-10'

APP_VERSION_NOTE = "v1.07: Login adds CMS Customer ID validation + Support Panel (SVG icons) with licence period and expiry warning badge; customer profile adds licence dates; register stores CMS/SPOS IDs."
DATA_DIR = "DocMate_DATA"

# -------------------------
# v1.07 helpers: Support panel + Licence badge
# -------------------------
import base64
from pathlib import Path

def _read_file_b64(path: str) -> str:
    try:
        p = Path(path)
        if not p.exists():
            p = Path(__file__).parent / path
        data = p.read_bytes()
        return base64.b64encode(data).decode("utf-8")
    except Exception:
        return ""

def _uk_date_from_iso(iso_yyyy_mm_dd: str) -> str:
    try:
        d = datetime.strptime((iso_yyyy_mm_dd or "").strip(), "%Y-%m-%d").date()
        return d.strftime("%d/%m/%Y")
    except Exception:
        return ""

def _licence_period_text(start_iso: str, end_iso: str) -> str:
    s = _uk_date_from_iso(start_iso)
    e = _uk_date_from_iso(end_iso)
    if s and e:
        return f"{s}–{e}"
    if s and not e:
        return f"{s}–(end not set)"
    if (not s) and e:
        return f"(start not set)–{e}"
    return "(licence dates not set)"

def _licence_days_left(end_iso: str):
    try:
        end_dt = datetime.strptime((end_iso or '').strip(), "%Y-%m-%d").date()
        today = datetime.utcnow().date()
        return (end_dt - today).days
    except Exception:
        return None

DB_FILE = os.path.join(DATA_DIR, "docmate.db")
EVENT_LOG_FILE = os.path.join(DATA_DIR, "EventLOG_" + datetime.now().strftime("%Y-%m-%d") + ".txt")
SETTINGS_FILE = os.path.join(DATA_DIR, "docmate_settings.json")

UK_TZ = "Europe/London"

DOC_TYPES = [
    "Purchase Invoice",
    "Service Invoice",
    "Sales Invoice",
    "Commission Invoice",
]

# Default VAT mapping (per supplier; can be overridden in templates/settings)
VAT_MAPPING_DEFAULT = {
    "B": 20.0,
    "A": 0.0,
    "Z": 0.0,
    "Z/A": 0.0,
    "R": 5.0,
    "R5": 5.0,
}

# Registers mapping (for UI labelling)
REGISTER_LABEL = {
    "Purchase Invoice": "Purchase Invoice Register",
    "Service Invoice": "Service Invoice Register",
    "Sales Invoice": "Sales Invoice Register",
    "Commission Invoice": "Commission Invoice Register",
}


# --- Extracted core helpers (moved out of legacy.py)
from docmate.core.logging_utils import _log_event, _read_event_log
from docmate.core.settings import (
    _default_settings, _read_settings, _write_settings,
    _get_google_api_key, _set_google_api_key, _has_env_google_api_key,
    _get_google_api_key_stored, _get_google_api_key_source,
)
from docmate.core.db import (
    _db_connect, _get_db, _db_init, _db_exec, _db_fetchall, _db_fetchone,
    _db_has_booker_supplier_template,
)
from docmate.core.template_match import (
    _template_keywords, _template_score, _template_signature_supplier, _diff_template_fields,
)



# --- Extracted supplier parsers and processing router
from docmate.core.processing import LocalInvoiceParser, CloudGeminiExtractor, _parse_json_from_model
from docmate.parsers.ai_fallback import ClaudeAIFallbackParser
from docmate.core.settings import _get_claude_api_key
from docmate.parsers.booker import BookerCombinedRowParser, _booker_extract_customer_name, _booker_autofill_customer_name
from docmate.parsers.dwg import _dwg_extract_header, _parse_dwg_purchase_invoice
from docmate.parsers.dhamecha import _dhamecha_parse_vat_code_rate_table, _dhamecha_parse_purchase_invoice
from docmate.parsers.parfetts import _extract_parfetts_vat_breakdown, _parfetts_extract_header, _parse_parfetts_purchase_invoice

# --- More extracted components
from docmate.core.ocr import (
    _ensure_import, _easy_reader, _paddle, _prep_img, _bytes_to_image,
    _apply_page_separators, _pdf_text_layer, _pdf_to_images, ocr_text_cached,
)
from docmate.core.exports import (
    _ensure_data_dir, _invoice_export_file_names, _invoice_export_full_csv_bytes,
    _build_dedicated_invoice_export, _write_invoice_exports,
)
from docmate.core.reports_pdf import (
    _footer, _calc_vat_breakdown_from_items, _generate_pdf_report_bytes,
)
from docmate.core.recalc import (
    _is_empty, _safe_float, _normalise_vat_rate, _recalc_items,
)

# -------------------------
# Helpers
# -------------------------


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _uk_now_iso() -> str:
    tz = ZoneInfo(UK_TZ) if ZoneInfo else None
    if tz:
        return datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S %Z")
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S GMT")


def _yyyy_mm_dd_to_dd_mm_yyyy(d: str) -> str:
    try:
        s = (d or "").strip()
        if not s:
            return ""
        if re.fullmatch(r"\d{2}-\d{2}-\d{4}", s):
            return s
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
            y, m, dd = s.split("-")
            return f"{dd}-{m}-{y}"
    except Exception:
        pass
    return (d or "").strip()

def _version_key_tuple(v: str) -> Tuple[int, ...]:
    """Sort key for versions like v1.03.054."""
    s = (v or "").strip().lstrip("vV")
    parts = re.split(r"[^0-9]+", s)
    nums = []
    for p in parts:
        if p.isdigit():
            nums.append(int(p))
    return tuple(nums or [0])

def _to_uk_ts(utc_ts: str) -> str:
    """Convert an ISO UTC timestamp (..Z) to UK local display with GMT/BST."""
    try:
        # Accept both 2026-01-21T23:58:19Z and 2026-01-21 23:58:19 GMT
        s = utc_ts.strip().replace(" GMT", "Z").replace(" UTC", "Z")
        if "T" not in s and s.endswith("Z"):
            # maybe "YYYY-MM-DD HH:MM:SSZ"
            s = s.replace(" ", "T")
        if s.endswith("Z"):
            dt = datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        else:
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        tz = ZoneInfo(UK_TZ) if ZoneInfo else None
        if tz:
            dt2 = dt.astimezone(tz)
            return dt2.strftime("%Y-%m-%d %H:%M:%S %Z")
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S GMT")
    except Exception:
        return utc_ts



def _mask_key(key: Optional[str]) -> str:
    """Return a safe masked representation of an API key for UI display."""
    k = (key or "").strip()
    if not k:
        return "(none)"
    # Keep only last 4 chars
    tail = k[-4:] if len(k) >= 4 else k
    return "****" + tail



def _tesseract_path_ok(tess_path: str) -> bool:
    p = (tess_path or "").strip()
    return bool(p) and os.path.isfile(p)


def _decode_logo_bytes(logo_b64: str) -> Optional[bytes]:
    s = (logo_b64 or "").strip()
    if not s:
        return None
    try:
        return base64.b64decode(s.encode("utf-8"))
    except Exception:
        return None



def _get_branding_logo_path(settings: Dict[str, Any]) -> Optional[bytes]:
    """Return branding logo content suitable for st.image().

    Historically some UI code expected a filesystem path; Streamlit st.image()
    also accepts raw bytes, which is more reliable for packaged/local installs.
    """
    try:
        branding = (settings or {}).get("branding", {}) or {}
        logo_b64 = (branding.get("logo_b64") or "").strip()
        logo_bytes = _decode_logo_bytes(logo_b64)
        return logo_bytes
    except Exception:
        return None




def _get_branding_powered_by_logo_path(settings: dict) -> str | None:
    """Return path to optional 'Powered by Visual AI' logo, if configured."""
    try:
        b = (settings or {}).get('branding') or {}
        # Prefer explicit file path
        fp = b.get('powered_by_logo_path')
        if fp and pathlib.Path(fp).exists():
            return fp
        # Fallback to conventional location
        base = pathlib.Path(_get_docs_configuration_dir()) / 'Branding'
        for name in ['powered_by_visualai.png','powered_by_visualai.jpg','powered_by_logo.png','powered_by_logo.jpg']:
            cand = base / name
            if cand.exists():
                return str(cand)
    except Exception:
        return None
    return None

def _get_powered_by_logo_path(settings: dict) -> str:
    """Backward-compatible alias used by older UI code."""
    return _get_branding_powered_by_logo_path(settings)
def _sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _cache_key_for_ocr(
    file_bytes: bytes,
    mime: str,
    engine: str,
    dpi: int,
    max_pages: int,
    force_pdf: bool,
) -> str:
    """Stable cache key for OCR results (used by st.cache_data)."""
    h = _sha256_bytes(file_bytes)
    return f"{h}|{mime}|{(engine or '').lower()}|{int(dpi)}|{int(max_pages)}|{1 if force_pdf else 0}"


def _safe_eval_arith(expr: str) -> float | None:
    """Safely evaluate a simple arithmetic expression like '=57.98+11.60'.
    Allowed: digits, spaces, + - * / ( ) and decimal points.
    """
    expr = (expr or "").strip()
    if not expr:
        return None
    if expr.startswith("="):
        expr = expr[1:].strip()
    if not expr:
        return None
    if not re.fullmatch(r"[0-9\.\+\-\*\/\(\)\s]+", expr):
        return None

    try:
        node = ast.parse(expr, mode="eval")
    except Exception:
        return None

    def _eval(n):
        if isinstance(n, ast.Expression):
            return _eval(n.body)
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)):
            return float(n.value)
        if isinstance(n, ast.Num):
            return float(n.n)
        if isinstance(n, ast.UnaryOp) and isinstance(n.op, (ast.UAdd, ast.USub)):
            v = _eval(n.operand)
            return v if isinstance(n.op, ast.UAdd) else -v
        if isinstance(n, ast.BinOp) and isinstance(n.op, (ast.Add, ast.Sub, ast.Mult, ast.Div)):
            a = _eval(n.left)
            b = _eval(n.right)
            if isinstance(n.op, ast.Add):
                return a + b
            if isinstance(n.op, ast.Sub):
                return a - b
            if isinstance(n.op, ast.Mult):
                return a * b
            if isinstance(n.op, ast.Div):
                return float("nan") if b == 0 else (a / b)
        raise ValueError("disallowed")

    try:
        v = float(_eval(node))
        if math.isnan(v) or math.isinf(v):
            return None
        return v
    except Exception:
        return None




def _escape_html(s: str) -> str:
    """HTML-escape for safe embedding in st.markdown(unsafe_allow_html=True)."""
    return html.escape(s, quote=True)


def _fmt_money(v, places: int = 2) -> str:
    """Format a numeric value as money with fixed decimals. Returns "0.00" if blank."""
    x = _safe_float(v)
    if x is None:
        x = 0.0
    try:
        return f"{x:.{places}f}"
    except Exception:
        return f"{float(x):.{places}f}"
def _safe_eval_arith(expr: str) -> float | None:
    """Safely evaluate simple arithmetic expressions like '57.98+11.60'.

    Allowed: numbers, + - * /, parentheses, whitespace.
    Returns None if expression is not safe/valid.
    """
    import ast, operator as _op
    if expr is None:
        return None
    s = str(expr).strip()
    if not s:
        return None
    # Strip leading '=' and common currency symbols/commas
    if s.startswith("="):
        s = s[1:].strip()
    s = s.replace("£", "").replace(",", "").strip()
    # Quick allowlist of characters
    if not re.fullmatch(r"[0-9\.\+\-\*\/\(\)\s]+", s):
        return None

    # AST safety
    try:
        node = ast.parse(s, mode="eval")
    except Exception:
        return None

    allowed_nodes = (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Num, ast.Constant,
                     ast.Add, ast.Sub, ast.Mult, ast.Div, ast.USub, ast.UAdd, ast.Load,
                     ast.Pow, ast.Mod, ast.FloorDiv, ast.Call, ast.Name)
    # Disallow Call/Name explicitly (keep allowlist simple)
    for n in ast.walk(node):
        if isinstance(n, (ast.Call, ast.Name, ast.Attribute, ast.Subscript, ast.Lambda, ast.Dict, ast.List, ast.Tuple)):
            return None
        if not isinstance(n, allowed_nodes):
            return None
        # Disallow operators other than + - * /
        if isinstance(n, (ast.Pow, ast.Mod, ast.FloorDiv)):
            return None

    ops = {ast.Add: _op.add, ast.Sub: _op.sub, ast.Mult: _op.mul, ast.Div: _op.truediv,
           ast.UAdd: lambda a: a, ast.USub: _op.neg}

    def _eval(n):
        if isinstance(n, ast.Expression):
            return _eval(n.body)
        if isinstance(n, ast.Constant):
            if isinstance(n.value, (int, float)):
                return float(n.value)
            return None
        if isinstance(n, ast.Num):  # py<3.8
            return float(n.n)
        if isinstance(n, ast.UnaryOp):
            fn = ops.get(type(n.op))
            val = _eval(n.operand)
            if fn is None or val is None:
                return None
            return fn(val)
        if isinstance(n, ast.BinOp):
            fn = ops.get(type(n.op))
            left = _eval(n.left)
            right = _eval(n.right)
            if fn is None or left is None or right is None:
                return None
            try:
                return fn(left, right)
            except Exception:
                return None
        return None

    return _eval(node)

def _maybe_number_from_editor(val):
    """Convert editor values to float, supporting simple '=a+b' formulas."""
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, str):
        s = val.strip()
        if not s:
            return None
        # Try arithmetic evaluation if it looks like a formula
        if s.startswith("=") or any(ch in s for ch in "+-*/()"):
            v = _safe_eval_arith(s)
            if v is not None:
                return v
        # Fallback: regular float parsing
        try:
            return float(s.replace("£","").replace(",",""))
        except Exception:
            return None
    return None



def _effective_line_gross(it: dict) -> float:
    """Return a robust gross amount even if lineGross is missing/incorrect."""
    net = _safe_float(it.get("lineNet"))
    vat = _safe_float(it.get("vatAmount"))
    gross = _safe_float(it.get("lineGross"))
    # Treat "gross == net" as missing gross if VAT exists (common supplier print/AI quirk).
    if (gross == 0 and net != 0) or (abs(gross - net) < 0.01 and abs(vat) > 0.01):
        return round(net + vat, 2)
    return gross


def _effective_line_vat(it: dict) -> float:
    net = _safe_float(it.get("lineNet"))
    vat = _safe_float(it.get("vatAmount"))
    rate = _safe_float(it.get("vatRate"))
    if vat == 0 and net != 0 and rate != 0:
        return round(net * (rate / 100.0), 2)
    return vat

def _derive_vat_map_from_items(items: List[Dict[str, Any]]) -> Dict[str, float]:
    """Derive a VAT code -> rate (%) mapping from line items.

    Uses vatCode+vatRate if present; otherwise falls back to vatAmount/lineNet.
    """
    vat_map: Dict[str, float] = {}
    if not items:
        return vat_map
    for it in items:
        code = str(it.get("vatCode") or "").strip()
        if not code:
            continue
        rate = _safe_float(it.get("vatRate"))
        # Fallback: derive % from amounts if rate missing/zero
        if (rate is None) or (rate == 0.0 and it.get("vatRate") in (None, "", 0, 0.0)):
            net = _safe_float(it.get("lineNet"))
            vat = _safe_float(it.get("vatAmount"))
            if net and net > 0 and vat is not None:
                rate = round((vat / net) * 100.0, 2)
        if rate is None:
            continue
        # Prefer a non-zero rate if we already captured 0
        if code not in vat_map or (vat_map.get(code, 0.0) == 0.0 and rate > 0.0):
            vat_map[code] = float(rate)
    return vat_map



def _generate_html_report(
    file_name: str,
    header: Dict[str, Any],
    items: List[Dict[str, Any]],
    reconciliation: Dict[str, Any],
    recon_notes: List[str],
) -> str:
    """
    Generate a customer-ready HTML report (printable). This is a robust fallback when PDF libs
    are not available. Users can open the HTML and use the browser Print → Save as PDF.
    """
    import html

    calc_items = _recalc_items(items)
    net_calc = round(sum(_safe_float(i.get("lineNet")) for i in calc_items), 2)
    vat_calc = round(sum(_safe_float(i.get("vatAmount")) for i in calc_items), 2)
    gross_calc = round(sum(_safe_float(i.get("lineGross")) for i in calc_items), 2)

    net_hdr = round(_safe_float(header.get("totalNet")), 2)
    vat_hdr = round(_safe_float(header.get("totalVat")), 2)
    gross_hdr = round(_safe_float(header.get("totalGross")), 2)

    release_date_disp = APP_DATE
    try:
        release_date_disp = datetime.strptime(str(APP_DATE), "%Y-%m-%d").strftime("%d/%m/%Y")
    except Exception:
        pass


    def esc(x): return html.escape(str(x) if x is not None else "")

    vat_rows = _calc_vat_breakdown_from_items(calc_items)

    css = """
    <style>
      @page { size: A4 landscape; margin: 12mm; }
      body { font-family: Arial, Helvetica, sans-serif; font-size: 12px; color: #111; }
      h1 { font-size: 18px; margin: 0 0 6px 0; }
      h2 { font-size: 14px; margin: 14px 0 6px 0; }
      .meta { margin-bottom: 10px; padding-left: 6px; }
      .center { text-align: center; }
      table { border-collapse: collapse; width: 100%; }
      th, td { border: 1px solid #bbb; padding: 6px 6px; vertical-align: top; }
      th { background: #f0f0f0; font-weight: 600; }
      .small { font-size: 11px; color: #333; }
      .right { text-align: right; }
      .nowrap { white-space: nowrap; }
      .notes { margin-top: 8px; }
      .footer { margin-top: 12px; font-size: 10px; color: #444; }
    
.dm-login-flex{min-height:92vh;display:flex;flex-direction:column;justify-content:center;align-items:center;}
.dm-login-flex .stForm{width:100%;max-width:560px;}

        /* v1.07 Licence badge + Support panel */
        .dm-lic-badge { text-align:center; font-weight:900; border-radius: 12px; padding: 8px 10px; margin-top: 8px; }
        .dm-lic-expired { background:#ff2a2a; color:#000; }
        .dm-lic-warn { background:#f3d27a; color:#111; }

        .dm-support-panel{
            margin-top: 12px;
            background: rgba(255,255,255,0.92);
            border-radius: 18px;
            padding: 12px 14px;
            border: 2px solid rgba(0,0,0,0.25);
            box-shadow: 0 10px 22px rgba(0,0,0,0.18);
            display:flex;
            gap: 12px;
            align-items:center;
        }
        .dm-support-svg{ width: 86px; display:flex; align-items:center; justify-content:center; }
        .dm-support-main{ flex:1; }
        .dm-support-title{ font-weight: 900; font-size: 22px; color:#111; margin:0 0 6px 0; }
        .dm-support-lines{ display:flex; gap: 16px; flex-wrap: wrap; color:#1b2a52; font-weight: 800; font-size: 16px; }
        .dm-support-idrow{ margin-top: 10px; display:flex; gap: 10px; flex-wrap: wrap; align-items:center; }
        .dm-pill{ display:inline-flex; align-items:center; border-radius: 12px; padding: 6px 10px; font-weight: 900; }
        .dm-pill-gold{ background:#f6d27a; color:#111; }
        .dm-pill-white{ background:#fff; border:2px solid #2d2a73; color:#2d2a73; }
        .dm-pill-lic{ background:#fff; border:2px solid #e14b3b; color:#111; }

        </style>
    """

    summary_rows = f"""
    <tr><th>Supplier</th><td>{esc(header.get('supplierName',''))}</td><th>Customer</th><td>{esc(header.get('customerName',''))}</td></tr>
    <tr><th>Invoice Date</th><td>{esc(header.get('invoiceDate',''))}</td><th>Invoice Number</th><td>{esc(header.get('invoiceNumber',''))}</td></tr>
    <tr><th>Header Net</th><td class="right">{net_hdr:,.2f}</td><th>Line Net</th><td class="right">{net_calc:,.2f}</td></tr>
    <tr><th>Header VAT</th><td class="right">{vat_hdr:,.2f}</td><th>Line VAT</th><td class="right">{vat_calc:,.2f}</td></tr>
    <tr><th>Header Gross</th><td class="right">{gross_hdr:,.2f}</td><th>Line Gross</th><td class="right">{gross_calc:,.2f}</td></tr>
    """

    recon_rows = f"""
    <tr><th>Check</th><th class="right">Line Items</th><th class="right">Header</th><th class="right">Diff</th><th>Status</th></tr>
    <tr><td>Net</td><td class="right">{net_calc:,.2f}</td><td class="right">{net_hdr:,.2f}</td><td class="right">{(net_calc-net_hdr):,.2f}</td><td>{esc(reconciliation.get('netStatus',''))}</td></tr>
    <tr><td>VAT</td><td class="right">{vat_calc:,.2f}</td><td class="right">{vat_hdr:,.2f}</td><td class="right">{(vat_calc-vat_hdr):,.2f}</td><td>{esc(reconciliation.get('vatStatus',''))}</td></tr>
    <tr><td>Gross</td><td class="right">{gross_calc:,.2f}</td><td class="right">{gross_hdr:,.2f}</td><td class="right">{(gross_calc-gross_hdr):,.2f}</td><td>{esc(reconciliation.get('grossStatus',''))}</td></tr>
    """

    vat_table_rows = "<tr><th>VAT Code</th><th class='right'>Net</th><th class='right'>VAT</th><th class='right'>Gross</th></tr>"
    for r in vat_rows:
        vat_table_rows += f"<tr><td class='nowrap'>{esc(r.get('vatCode',''))}</td><td class='right'>{_safe_float(r.get('netAmount')):,.2f}</td><td class='right'>{_safe_float(r.get('vatAmount')):,.2f}</td><td class='right'>{_safe_float(r.get('grossAmount')):,.2f}</td></tr>"

    # Line items
    cols = ["lineNo","code","description","casePack","unitSize","qty","totalUnits","unitPrice","lineNet","vatCode","vatRate","vatAmount","lineGross"]
    li_head = "<tr>" + "".join([f"<th>{esc(c)}</th>" for c in cols]) + "</tr>"
    li_rows = ""
    for it in calc_items:
        li_rows += "<tr>" + "".join([
            f"<td>{esc(it.get('lineNo',''))}</td>",
            f"<td class='nowrap'>{esc(it.get('code',''))}</td>",
            f"<td>{esc(it.get('description',''))}</td>",
            f"<td>{esc(it.get('casePack',''))}</td>",
            f"<td>{esc(it.get('unitSize',''))}</td>",
            f"<td class='right'>{_safe_float(it.get('qty')):g}</td>",
            f"<td class='right'>{_safe_float(it.get('totalUnits')):g}</td>",
            f"<td class='right'>{_safe_float(it.get('unitPrice')):,.2f}</td>",
            f"<td class='right'>{_safe_float(it.get('lineNet')):,.2f}</td>",
            f"<td class='nowrap'>{esc(it.get('vatCode',''))}</td>",
            f"<td class='right'>{_safe_float(it.get('vatRate')):g}</td>",
            f"<td class='right'>{_safe_float(it.get('vatAmount')):,.2f}</td>",
            f"<td class='right'>{_safe_float(it.get('lineGross')):,.2f}</td>",
        ]) + "</tr>"

    # Totals row (calculated fields)
    tot_qty = round(sum(_safe_float(i.get("qty")) for i in calc_items), 3)
    tot_units = round(sum(_safe_float(i.get("totalUnits")) for i in calc_items), 3)
    tot_net = round(sum(_safe_float(i.get("lineNet")) for i in calc_items), 2)
    tot_vat = round(sum(_safe_float(i.get("vatAmount")) for i in calc_items), 2)
    tot_gross = round(sum(_safe_float(i.get("lineGross")) for i in calc_items), 2)

    li_rows += "<tr>" + "".join([
        "<td></td>",
        "<td></td>",
        "<td><b>TOTAL</b></td>",
        "<td></td>",
        "<td></td>",
        f"<td class='right'><b>{tot_qty:g}</b></td>",
        f"<td class='right'><b>{tot_units:g}</b></td>",
        "<td></td>",
        f"<td class='right'><b>{tot_net:,.2f}</b></td>",
        "<td></td>",
        "<td></td>",
        f"<td class='right'><b>{tot_vat:,.2f}</b></td>",
        f"<td class='right'><b>{tot_gross:,.2f}</b></td>",
    ]) + "</tr>"

    notes_html = ""
    if recon_notes:
        notes_html = "<div class='notes'><b>Notes</b><br/>" + "<br/>".join([esc(n) for n in recon_notes]) + "</div>"

    html_doc = f"""
    <!doctype html>
    <html><head><meta charset="utf-8"/>{css}</head>
    <body>
      <h1 class="center">DocMate Invoice Audit Report</h1>
      <div class="center small"><b>Version:</b> {APP_VERSION} | <b>Release Date:</b> {release_date_disp}</div>
      <div class="meta"><b>DocMate Invoice Report for Supplier:</b> {esc(header.get("supplierName",""))} &nbsp;—&nbsp; <b>Doc. File Name:</b> {esc(file_name)}</div>
      <div class="meta small">Generated by DocMate (Visual Business Retail Ltd) — suitable for DocMate Registers and VBR Cloud Reporting.</div>

      <h2>Header Summary</h2>
      <table>{summary_rows}</table>

      <h2>Reconciliation</h2>
      <table>{recon_rows}</table>
      {notes_html}

      <h2>VAT Breakdown (calculated from line items)</h2>
      <table>{vat_table_rows}</table>

      <h2>Line Items</h2>
      <table>{li_head}{li_rows}</table>

      <div class="footer">
        Visual Business Retail Ltd — DocMate Invoice Audit Report.
      </div>
    </body></html>
    """
    return html_doc



def _strip_page_markers(text: str) -> str:
    """Remove DocMate OCR page markers (===== Page X/Y =====) that can pollute supplier detection."""
    if not text:
        return ""
    t = str(text)
    t = re.sub(r"(?im)^\s*=+\s*page\s*\d+\s*/\s*\d+\s*=+\s*$", "", t)
    # Also remove duplicated blank lines introduced by stripping
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()

def _looks_like_booker_invoice(text: str) -> bool:
    if not text:
        return False
    up = str(text).upper()
    # Booker invoices consistently contain these anchors (even when BOOKER word is OCR-corrupted)
    if ("INVOICE NUMBER" in up) and ("TOTAL ITEMS" in up) and (("RRP" in up) and ("POR" in up)):
        return True
    # Alternative: Booker VAT breakdown block (RATE / GOODS / NETT / VAT / INVOICE TOTAL)
    if ("INVOICE NUMBER" in up) and ("INVOICE TOTAL" in up) and ("RATE" in up) and ("NETT" in up) and ("VAT" in up):
        return True
    return False

def _looks_like_page_marker(val: str) -> bool:
    if not val:
        return False
    v = str(val).strip()
    return bool(re.match(r"^=+\s*page\s*\d+\s*/\s*\d+\s*=+$", v, flags=re.I))







def _is_supplier_like(name: str, needles: list[str]) -> bool:
    """Case-insensitive supplier matcher used by supplier-specific enrichers."""
    try:
        s = (name or "").strip().lower()
        if not s:
            return False
        for n in (needles or []):
            if n and str(n).strip().lower() in s:
                return True
        return False
    except Exception:
        return False


def _title_if_all_caps(s: str) -> str:
    try:
        s2 = (s or "").strip()
        if not s2:
            return ""
        # Heuristic: if it's mostly uppercase letters, convert to Title Case
        letters = [c for c in s2 if c.isalpha()]
        if letters and (sum(1 for c in letters if c.isupper()) / len(letters)) > 0.9:
            return s2.title()
        return s2
    except Exception:
        return (s or "").strip()


def _uk_date_to_iso(d: str) -> str:
    """Convert UK date 'DD/MM/YYYY' to 'YYYY-MM-DD'. Returns '' on failure."""
    try:
        d = (d or "").strip()
        m = re.match(r"^(\d{2})/(\d{2})/(\d{4})$", d)
        if not m:
            return ""
        dd, mm, yyyy = m.group(1), m.group(2), m.group(3)
        return f"{yyyy}-{mm}-{dd}"
    except Exception:
        return ""


def _money(x: Any) -> str:
    """Format number as money-like string with thousands separator (no currency symbol).
    Used for dashboard metrics (Net/VAT/Gross) and reconciliation lines.
    """
    v = _safe_float(x)
    # Avoid showing -0.00
    if abs(v) < 0.0005:
        v = 0.0
    return f"{v:,.2f}"


def _normalise_invoice_date_str(s: str) -> str:
    """Normalise date-like strings to ISO YYYY-MM-DD (best-effort).

    Handles formats commonly seen on supplier invoices, e.g.
    - 2026-01-30
    - 2026-01-30 19:49:00
    - 30/01/2026, 19:49
    - 30 Jan 2026, 19:49
    - 01-Feb-26 11:00:37
    """
    s = (s or '').strip()
    if not s:
        return ''

    # If an ISO date appears anywhere, trust it.
    m = re.search(r'(\d{4}-\d{2}-\d{2})', s)
    if m:
        return m.group(1)

    # dd/mm/yyyy or dd-mm-yyyy (optionally followed by time)
    m = re.match(r'^([0-3]?\d)[/\-]([01]?\d)[/\-](\d{4})(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?$', s)
    if m:
        dd, mm, yy = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            return dt.date(yy, mm, dd).isoformat()
        except Exception:
            return s

    # Try a set of common textual month formats
    cand = s
    # Strip trailing timezone-ish tokens
    cand = re.sub(r'\s+(GMT|UTC|BST).*$', '', cand, flags=re.I).strip()

    fmts = [
        '%d %b %Y',
        '%d %B %Y',
        '%d %b %Y, %H:%M',
        '%d %B %Y, %H:%M',
        '%d %b %Y %H:%M',
        '%d %B %Y %H:%M',
        '%d %b %Y %H:%M:%S',
        '%d %B %Y %H:%M:%S',
        '%d-%b-%Y',
        '%d-%b-%y',
        '%d-%b-%Y %H:%M:%S',
        '%d-%b-%y %H:%M:%S',
        '%d-%b-%Y %H:%M',
        '%d-%b-%y %H:%M',
    ]

    for f in fmts:
        try:
            d = dt.datetime.strptime(cand, f).date()
            return d.isoformat()
        except Exception:
            pass

    # Last resort: if we see a dd Mon yyyy inside the string, parse just that part
    m = re.search(r'([0-3]?\d\s+[A-Za-z]{3,9}\s+\d{2,4})', cand)
    if m:
        token = m.group(1)
        for f in ('%d %b %Y', '%d %B %Y', '%d %b %y', '%d %B %y'):
            try:
                return dt.datetime.strptime(token, f).date().isoformat()
            except Exception:
                pass

    return s

def _date_from_filename(file_name: str) -> str:
    """Extract YYYY-MM-DD from filenames like INV_Parfetts_30226797_2026-01-13.pdf."""
    fn = (file_name or "").strip()
    m = re.search(r"(\d{4}-\d{2}-\d{2})", fn)
    if m:
        return _normalise_invoice_date_str(m.group(1))
    return ""

def _parfetts_autofill_customer_and_date(extracted_text: str) -> Tuple[str, str]:
    """Try to find Parfetts customer name and invoice date from extracted text (PDF layer or OCR).

    Used as a fallback when cloud output misses these header fields.
    """
    txt = extracted_text or ""
    head = txt[:15000]
    lines = [ln.strip() for ln in head.splitlines() if ln.strip()]

    customer = ""
    inv_date = ""

    # Customer line typically contains (CR) but OCR may drop '('
    for i, ln in enumerate(lines[:800]):
        if "(CR)" in ln or "CR)" in ln or "(CR" in ln or "[CR]" in ln:
            m = re.search(r"(?:\(|\[)?\s*CR\s*(?:\)|\])?\s*([^\r\n]{3,})", ln, flags=re.I)
            if m:
                customer = m.group(1).strip()
            if customer:
                customer = re.split(r"\s*\(", customer)[0].strip()
            if not customer and i + 1 < len(lines):
                nxt = lines[i + 1]
                if re.match(r"^[A-Z0-9 &\-\'\.]{4,}$", nxt):
                    customer = nxt.strip()
            break

    # Invoice date: prefer explicit label variants (OCR can omit 'of')
    m = re.search(r"Date\s*(?:of\s*)?Invoice\s*[:\-]?\s*([0-3]?\d[/\-][01]?\d[/\-]\d{4})", head, flags=re.I)
    if m:
        inv_date = _normalise_invoice_date_str(m.group(1))
    else:
        m = re.search(r"Date\s*Printed\s*[:\-]?\s*([0-3]?\d[/\-][01]?\d[/\-]\d{4})", head, flags=re.I)
        if m:
            inv_date = _normalise_invoice_date_str(m.group(1))
        else:
            for ln in lines[:800]:
                if "Date Printed" in ln:
                    continue
                m2 = re.search(r"([0-3]?\d[/\-][01]?\d[/\-]\d{4})", ln)
                if m2:
                    inv_date = _normalise_invoice_date_str(m2.group(1))
                    break

    return customer, inv_date


def _to_yyyy_mm_dd(dmy: str) -> str:
    """Convert a UK date dd/mm/yyyy to yyyy-mm-dd. Returns '' if not parseable."""
    try:
        s = (dmy or '').strip()
        mm = re.match(r'^(\d{1,2})/(\d{1,2})/(\d{4})$', s)
        if not mm:
            return ''
        dd = mm.group(1).zfill(2)
        mo = mm.group(2).zfill(2)
        yy = mm.group(3)
        return f"{yy}-{mo}-{dd}"
    except Exception:
        return ''


def _guess_mime_type(filename: str, file_bytes=None) -> str:
    """Best-effort MIME detection.

    Backwards compatible:
    - older callers pass only filename
    - newer callers may pass (filename, file_bytes)

    We prefer filename extension; if missing/unknown and bytes are provided,
    we sniff a few common magic headers (PDF/PNG/JPEG).
    """
    fn = (filename or "").lower().strip()

    # 1) Extension first (fast + reliable when present)
    if fn.endswith(".pdf"):
        return "application/pdf"
    if fn.endswith(".png"):
        return "image/png"
    if fn.endswith(".jpg") or fn.endswith(".jpeg"):
        return "image/jpeg"

    # 2) Sniff magic bytes when extension is absent/unknown
    if isinstance(file_bytes, (bytes, bytearray)) and len(file_bytes) >= 4:
        b = bytes(file_bytes[:16])
        if b.startswith(b"%PDF"):
            return "application/pdf"
        if b.startswith(b"\x89PNG\r\n\x1a\n"):
            return "image/png"
        if b[:3] == b"\xff\xd8\xff":
            return "image/jpeg"

    return "application/octet-stream"




def _collapse_spaces(s: str) -> str:
    """Collapse repeated whitespace to a single space."""
    try:
        return re.sub(r"\s+", " ", (s or "")).strip()
    except Exception:
        return (s or "").strip()


















def _supplier_acronym(supplier_name: str) -> str:
    s = (supplier_name or "").upper()
    if "BOOKER" in s:
        return "BKL"
    if "DRINKS WHOLESALE" in s or "DWG" in s:
        return "DWG"
    words = re.findall(r"[A-Z0-9]+", s)
    core = [w for w in words if w and w not in STOP_WORDS]
    if core:
        acr = "".join([w[0] for w in core])[:3]
    else:
        acr = re.sub(r"[^A-Z0-9]", "", s)[:3] or "SUP"
    return acr.ljust(3, "X")


def _customer_acronym(customer_name: str) -> str:
    s = (customer_name or "").upper()
    # Common: Fresher Kingston (for purchase) — but for customer templates, create from name
    words = re.findall(r"[A-Z0-9]+", s)
    core = [w for w in words if w and w not in STOP_WORDS]
    if core:
        acr = "".join([w[0] for w in core])[:3]
    else:
        acr = re.sub(r"[^A-Z0-9]", "", s)[:3] or "CST"
    return acr.ljust(3, "X")


def _next_display_id(prefix: str, yyyymmdd: str, table: str) -> str:
    # Format: PREFIXYYYYMMDD-01
    patt = f"{prefix}{yyyymmdd}-"
    rows = _db_fetchall(f"SELECT display_id FROM {table} WHERE display_id LIKE ?", (patt + "%",))
    used = set([r[0] for r in rows if r[0]])
    i = 1
    while True:
        cand = f"{patt}{i:02d}"
        if cand not in used:
            return cand
        i += 1






def _db_upsert_supplier_template_checked(payload: Dict[str, Any]) -> Tuple[str, bool, List[str]]:
    """Upsert supplier template but skip if identical. Returns (template_id, saved, notes)."""
    supplier_name = (payload.get("supplier_name") or "").strip()
    parser_type = (payload.get("parser_type") or "GENERIC").strip().upper()

    existing = _db_fetchone(
        "SELECT * FROM supplier_templates WHERE UPPER(TRIM(supplier_name))=? AND UPPER(TRIM(parser_type))=?",
        (supplier_name.upper(), parser_type),
    )
    existing_d = dict(existing) if existing else None

    # Build comparable normalised payloads
    def _norm_from_row(row: Dict[str, Any]) -> Dict[str, Any]:
        cfg = {}
        try:
            cfg = json.loads(row.get("config_json") or "{}")
        except Exception:
            cfg = {}
        kws = []
        try:
            kws = json.loads(row.get("match_keywords_json") or "[]")
        except Exception:
            kws = []
        return {
            "vat_number": (row.get("vat_number") or "").strip(),
            "awrs_number": (row.get("awrs_number") or "").strip(),
            "requires_cloud": 1 if int(row.get("requires_cloud") or 0) == 1 else 0,
            "match_keywords": sorted(list(dict.fromkeys([str(x).strip().upper() for x in (kws or []) if str(x).strip()]))),
            "config": cfg if isinstance(cfg, dict) else {},
        }

    new_norm = {
        "vat_number": (payload.get("vat_number") or "").strip(),
        "awrs_number": (payload.get("awrs_number") or "").strip(),
        "requires_cloud": 1 if int(payload.get("requires_cloud") or 0) == 1 else 0,
        "match_keywords": sorted(list(dict.fromkeys([str(x).strip().upper() for x in (payload.get("match_keywords") or []) if str(x).strip()]))),
        "config": (payload.get("config") or {}) if isinstance(payload.get("config") or {}, dict) else {},
    }

    if existing_d:
        old_norm = _norm_from_row(existing_d)
        if json.dumps(old_norm, sort_keys=True, ensure_ascii=False) == json.dumps(new_norm, sort_keys=True, ensure_ascii=False):
            tpl_id = (existing_d.get("template_id") or "").strip()
            disp = (existing_d.get("display_id") or "").strip()
            notes = [f"Template unchanged — not saved (existing: {disp or tpl_id})."]
            return (tpl_id, False, notes)

    tpl_id = _db_upsert_supplier_template(payload)
    return (tpl_id, True, [])

def _db_upsert_supplier_template(payload: Dict[str, Any]) -> str:
    """Create/update supplier template and return template_id.

    Rules:
    - One template per (supplier_name, parser_type) unless template_id explicitly provided.
    - If display_id is empty and a matching template exists, keep existing display_id (prevents duplicates).
    """
    supplier_name = (payload.get("supplier_name") or "").strip()
    parser_type = (payload.get("parser_type") or "GENERIC").strip().upper()
    now = _uk_now_iso()

    template_id = (payload.get("template_id") or "").strip()
    existing: Optional[Dict[str, Any]] = None

    if template_id:
        row = _db_fetchone("SELECT * FROM supplier_templates WHERE template_id=?", (template_id,))
        existing = dict(row) if row else None
    else:
        row = _db_fetchone(
            "SELECT * FROM supplier_templates WHERE UPPER(TRIM(supplier_name))=? AND UPPER(TRIM(parser_type))=?",
            (supplier_name.upper(), parser_type),
        )
        existing = dict(row) if row else None
        if existing:
            template_id = (existing.get("template_id") or "").strip()
        # Dedupe: keep one template per supplier+parser_type
        if template_id:
            _db_exec(
                "DELETE FROM supplier_templates WHERE UPPER(TRIM(supplier_name))=? AND UPPER(TRIM(parser_type))=? AND template_id<>?",
                (supplier_name.upper(), parser_type, template_id),
            )

    if not template_id:
        template_id = _sha256_bytes(f"SUP|{supplier_name}|{time.time()}".encode("utf-8"))[:24]

    created = (payload.get("created_at") or (existing.get("created_at") if existing else "") or now)

    vat = (payload.get("vat_number") or (existing.get("vat_number") if existing else "") or "").strip()
    awrs = (payload.get("awrs_number") or (existing.get("awrs_number") if existing else "") or "").strip()
    requires_cloud = 1 if int(payload.get("requires_cloud") or (existing.get("requires_cloud") if existing else 0) or 0) == 1 else 0

    cfg = payload.get("config")
    if cfg is None:
        cfg = {}
        if existing and existing.get("config_json"):
            try:
                cfg = json.loads(existing.get("config_json") or "{}")
            except Exception:
                cfg = {}
    if not isinstance(cfg, dict):
        cfg = {}

    keywords = payload.get("match_keywords")
    if not isinstance(keywords, list):
        if existing and existing.get("match_keywords_json"):
            try:
                keywords = json.loads(existing.get("match_keywords_json") or "[]")
            except Exception:
                keywords = None
        if not isinstance(keywords, list):
            keywords = _template_keywords(supplier_name, [vat, awrs])

    display_id = (payload.get("display_id") or "").strip()
    if not display_id and existing:
        display_id = (existing.get("display_id") or "").strip()

    if not display_id:
        yyyymmdd = re.sub(r"[^0-9]", "", now[:10])
        if len(yyyymmdd) != 8:
            yyyymmdd = datetime.utcnow().strftime("%Y%m%d")
        prefix = _supplier_acronym(supplier_name)
        display_id = _next_display_id(prefix, yyyymmdd, "supplier_templates")

    row2 = _db_fetchone("SELECT template_id FROM supplier_templates WHERE display_id=?", (display_id,))
    if row2 and row2[0] != template_id:
        raise ValueError(f"Display ID already exists: {display_id}")

    _db_exec(
        """INSERT OR REPLACE INTO supplier_templates(
            template_id, display_id, supplier_name, vat_number, awrs_number,
            match_keywords_json, parser_type, requires_cloud, config_json,
            created_at, updated_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
        (
            template_id,
            display_id,
            supplier_name,
            vat,
            awrs,
            json.dumps(keywords),
            parser_type,
            requires_cloud,
            json.dumps(cfg),
            created,
            now,
        ),
    )
    return template_id




def _db_patch_supplier_template_config(template_id: str, patch: Dict[str, Any]) -> None:
    """Patch (merge) config_json for a supplier template and bump updated_at.

    Used to 'learn' default values such as Booker default_customer_name from Admin header edits.
    """
    if not template_id:
        return
    try:
        row = _db_fetchone("SELECT config_json FROM supplier_templates WHERE template_id=?", (template_id,))
        cfg = {}
        if row:
            try:
                cfg = json.loads((dict(row).get("config_json") or "{}"))
            except Exception:
                cfg = {}
        if not isinstance(cfg, dict):
            cfg = {}
        for k, v in (patch or {}).items():
            cfg[k] = v
        _db_exec(
            "UPDATE supplier_templates SET config_json=?, updated_at=? WHERE template_id=?",
            (json.dumps(cfg, ensure_ascii=False), _uk_now_iso(), template_id),
        )
    except Exception:
        return
def _db_upsert_customer_template(payload: Dict[str, Any]) -> str:
    """Create/update customer template and return template_id.

    Rules:
    - One template per (customer_name, parser_type) unless template_id explicitly provided.
    - If display_id is empty and a matching template exists, keep existing display_id (prevents duplicates).
    """
    customer_name = (payload.get("customer_name") or "").strip()
    parser_type = (payload.get("parser_type") or "GENERIC").strip().upper()
    now = _uk_now_iso()

    template_id = (payload.get("template_id") or "").strip()
    existing: Optional[Dict[str, Any]] = None

    if template_id:
        row = _db_fetchone("SELECT * FROM customer_templates WHERE template_id=?", (template_id,))
        existing = dict(row) if row else None
    else:
        row = _db_fetchone(
            "SELECT * FROM customer_templates WHERE UPPER(TRIM(customer_name))=? AND UPPER(TRIM(parser_type))=?",
            (customer_name.upper(), parser_type),
        )
        existing = dict(row) if row else None
        if existing:
            template_id = (existing.get("template_id") or "").strip()

        # Dedupe: keep one template per customer+parser_type
        if template_id:
            _db_exec(
                "DELETE FROM customer_templates WHERE UPPER(TRIM(customer_name))=? AND UPPER(TRIM(parser_type))=? AND template_id<>?",
                (customer_name.upper(), parser_type, template_id),
            )

    if not template_id:
        template_id = _sha256_bytes(f"CST|{customer_name}|{time.time()}".encode("utf-8"))[:24]

    created = (payload.get("created_at") or (existing.get("created_at") if existing else "") or now)
    requires_cloud = 1 if int(payload.get("requires_cloud") or (existing.get("requires_cloud") if existing else 0) or 0) == 1 else 0

    cfg = payload.get("config")
    if cfg is None:
        cfg = {}
        if existing and existing.get("config_json"):
            try:
                cfg = json.loads(existing.get("config_json") or "{}")
            except Exception:
                cfg = {}
    if not isinstance(cfg, dict):
        cfg = {}

    keywords = payload.get("match_keywords")
    if not isinstance(keywords, list):
        if existing and existing.get("match_keywords_json"):
            try:
                keywords = json.loads(existing.get("match_keywords_json") or "[]")
            except Exception:
                keywords = None
        if not isinstance(keywords, list):
            keywords = _template_keywords(customer_name)

    display_id = (payload.get("display_id") or "").strip()
    if not display_id and existing:
        display_id = (existing.get("display_id") or "").strip()

    if not display_id:
        yyyymmdd = re.sub(r"[^0-9]", "", now[:10])
        if len(yyyymmdd) != 8:
            yyyymmdd = datetime.utcnow().strftime("%Y%m%d")
        prefix = _customer_acronym(customer_name)
        display_id = _next_display_id(prefix, yyyymmdd, "customer_templates")

    row2 = _db_fetchone("SELECT template_id FROM customer_templates WHERE display_id=?", (display_id,))
    if row2 and row2[0] != template_id:
        raise ValueError(f"Display ID already exists: {display_id}")

    _db_exec(
        """INSERT OR REPLACE INTO customer_templates(
            template_id, display_id, customer_name,
            match_keywords_json, parser_type, requires_cloud, config_json,
            created_at, updated_at
        ) VALUES(?,?,?,?,?,?,?,?,?)""",
        (
            template_id,
            display_id,
            customer_name,
            json.dumps(keywords),
            parser_type,
            requires_cloud,
            json.dumps(cfg),
            created,
            now,
        ),
    )
    return template_id


def _db_delete_template(table: str, identifier: str) -> int:
    """Delete a template by template_id OR display_id. Returns rows deleted."""
    if table not in ("supplier_templates", "customer_templates"):
        return 0
    ident = (identifier or "").strip()
    if not ident:
        return 0
    conn = _db_connect()
    cur = conn.cursor()
    cur.execute(f"DELETE FROM {table} WHERE template_id=? OR display_id=?", (ident, ident))
    conn.commit()
    n = cur.rowcount or 0
    conn.close()
    return n

def _db_get_template(table: str, template_id: str) -> Optional[Dict[str, Any]]:
    if table not in ("supplier_templates", "customer_templates"):
        return None
    row = _db_fetchone(f"SELECT * FROM {table} WHERE template_id=?", (template_id,))
    if not row:
        return None
    d = dict(row)
    try:
        d["match_keywords"] = json.loads(d.get("match_keywords_json") or "[]")
    except Exception:
        d["match_keywords"] = []
    try:
        d["config"] = json.loads(d.get("config_json") or "{}")
    except Exception:
        d["config"] = {}
    return d


def _db_find_best_template(doc_text: str, table: str, threshold: int | None = None) -> Optional[Dict[str, Any]]:
    if table not in ("supplier_templates", "customer_templates"):
        return None
    rows = _db_fetchall(f"SELECT * FROM {table}")
    # Adaptive threshold: templates should still match when invoice numbers change.
    # If not provided, require 2 strong keyword hits when possible, otherwise 1.
    effective_threshold = threshold
    best: Optional[Tuple[int, Dict[str, Any]]] = None
    for r in rows:
        d = dict(r)
        try:
            kws = json.loads(d.get("match_keywords_json") or "[]")
        except Exception:
            kws = []
        try:
            cfg_match = json.loads(d.get("config_json") or "{}")
        except Exception:
            cfg_match = {}
        if not isinstance(cfg_match, dict):
            cfg_match = {}
        alias_kws = cfg_match.get("supplier_aliases") or []
        anchor_kws = cfg_match.get("layout_anchors") or []
        if not isinstance(alias_kws, list):
            alias_kws = []
        if not isinstance(anchor_kws, list):
            anchor_kws = []
        score = _template_score(doc_text, list(kws or []) + list(alias_kws or []))
        # Layout anchors are weaker than supplier identity, but help separate
        # multiple layouts for the same vendor without needing new DB columns.
        anchor_score = _template_score(doc_text, list(anchor_kws or []))
        if anchor_score:
            score += min(anchor_score, 3)
        if best is None or score > best[0]:
            best = (score, d)
        # Determine threshold after we see best candidate keyword count
    if effective_threshold is None:
        # best[1] is the dict for the best-scoring template; count its usable keywords
        try:
            kws_best = json.loads(best[1].get("match_keywords_json") or "[]")
        except Exception:
            kws_best = []
        kws_best = [k for k in (kws_best or []) if isinstance(k, str) and len(k.strip()) >= 4]
        effective_threshold = 2 if len(kws_best) >= 2 else 1

    if best and best[0] >= int(effective_threshold):
        d = best[1]
        try:
            d["match_keywords"] = json.loads(d.get("match_keywords_json") or "[]")
        except Exception:
            d["match_keywords"] = []
        try:
            d["config"] = json.loads(d.get("config_json") or "{}")
        except Exception:
            d["config"] = {}
        return d
    return None


# -------------------------
# OCR
# -------------------------

@dataclass
class OcrConfig:
    engine: str = "tesseract"  # tesseract/easyocr/paddleocr
    tesseract_cmd: str = ""
    dpi: int = 250
    max_pages: int = 2
    force_pdf: bool = False
    auto_install: bool = False

    @classmethod
    def from_dict(cls, d: Any) -> "OcrConfig":
        """Safe constructor from a dict-like object.

        Allows older keys and ignores unknown keys.
        """
        if isinstance(d, cls):
            return d
        if d is None:
            return cls()
        if not isinstance(d, dict):
            try:
                # e.g. dataclasses.asdict output already dict; otherwise best-effort
                d = dict(d)
            except Exception:
                return cls()

        # normalise possible legacy keys
        d2 = dict(d)
        if "max_pages_pdf" in d2 and "max_pages" not in d2:
            d2["max_pages"] = d2.get("max_pages_pdf")

        kwargs = {}
        for f in dataclasses.fields(cls):
            if f.name in d2 and d2[f.name] is not None:
                kwargs[f.name] = d2[f.name]
        return cls(**kwargs)



def _db_find_template_latest_by_parser_type(parser_type: str, table: str = "supplier_templates") -> Optional[Dict[str, Any]]:
    """Return the latest template row for a given parser_type from the chosen table."""
    pt = (parser_type or "").strip().upper()
    if not pt:
        return None
    try:
        row = _db_fetchone(
            f"""SELECT * FROM {table}
                  WHERE UPPER(TRIM(parser_type))=?
                  ORDER BY COALESCE(NULLIF(updated_at,''), NULLIF(created_at,'')) DESC
                  LIMIT 1""",
            (pt,)
        )
        if row:
            r = dict(row)
            try:
                r["config"] = json.loads(r.get("config_json") or "{}")
            except Exception:
                r["config"] = {}
            return r
    except Exception:
        return None
    return None


def _db_find_booker_template_latest() -> Optional[Dict[str, Any]]:
    """Fallback: if keyword matching fails but the document looks like Booker, use latest Booker supplier template.

    NOTE: Some deployments may have NULL updated_at values or legacy rows. This function is intentionally defensive.
    """
    queries = [
        # Preferred: most recently updated (or created) Booker template
        """SELECT * FROM supplier_templates
               WHERE (UPPER(TRIM(supplier_name)) LIKE '%BOOKER%' OR UPPER(TRIM(parser_type))='BOOKER')
               ORDER BY COALESCE(NULLIF(updated_at,''), NULLIF(created_at,'')) DESC
               LIMIT 1""",
        # Fallback: order by created_at
        """SELECT * FROM supplier_templates
               WHERE (UPPER(TRIM(supplier_name)) LIKE '%BOOKER%' OR UPPER(TRIM(parser_type))='BOOKER')
               ORDER BY COALESCE(NULLIF(created_at,''), template_id) DESC
               LIMIT 1""",
        # Last resort: any Booker template
        """SELECT * FROM supplier_templates
               WHERE (UPPER(TRIM(supplier_name)) LIKE '%BOOKER%' OR UPPER(TRIM(parser_type))='BOOKER')
               LIMIT 1""",
    ]
    for q in queries:
        try:
            row = _db_fetchone(q)
            if row:
                r = dict(row)
                try:
                    r["config"] = json.loads(r.get("config_json") or "{}")
                except Exception:
                    r["config"] = {}
                return r
        except Exception:
            continue
    return None




def _pdf_page_count(pdf_bytes: bytes) -> int:

    if not fitz:
        return 0
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        n = doc.page_count
        doc.close()
        return int(n or 0)
    except Exception:
        return 0


def _effective_max_pages(settings: Dict[str, Any], supplier_hint: str, file_bytes: bytes, mime: str, default_pages: int) -> int:
    """Supplier-aware max pages for OCR/PDF text layer.
    - Booker recommended: 4 pages
    - Parfetts (multi-page invoice): 6 pages
    Falls back to global ocr_max_pages or provided default.
    Caps to actual PDF page count when possible.
    """
    try:
        hint = (supplier_hint or "").upper()
    except Exception:
        hint = ""
    pages = int(default_pages or 1)

    # Template override (supplier_templates.config.max_pages_pdf)
    try:
        if bool(settings.get("supplier_templates_enabled", True)) and hint:
            tpl = _db_find_best_template(hint, table="supplier_templates", threshold=int(settings.get("supplier_template_match_threshold", 3) or 3))
            if tpl:
                cfg = tpl.get("config") or {}
                if isinstance(cfg, dict):
                    mp = cfg.get("max_pages_pdf")
                    if mp is not None and str(mp).strip().isdigit():
                        pages = int(mp)
    except Exception:
        pass
    try:
        if "BOOKER" in hint:
            pages = int(settings.get("booker_max_pages", 4) or 4)
        elif "PARFETTS" in hint:
            pages = int(settings.get("parfetts_max_pages", 6) or 6)
        else:
            pages = int(settings.get("ocr_max_pages", pages) or pages)
    except Exception:
        pass
    pages = max(1, min(int(pages), 50))
    if mime == "application/pdf":
        try:
            n = _pdf_page_count(file_bytes)
            if n and n > 0:
                pages = min(pages, int(n))
        except Exception:
            pass
    return max(1, int(pages))





def _parfetts_vat_breakdown_from_items(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Compute Parfetts-style VAT breakdown from line items.

    Returns:
      {
        "rows": [{"vatCode":"A","goodsAmount":2666.88,"vatAmount":533.38}, ...],
        "totalNet": 3033.74,
        "totalVat": 533.38,
        "totalGross": 3567.12
      }
    VAT is calculated at *code-group* level to match Parfetts invoice summary (reduces per-line rounding drift).
    """
    if not items:
        return {"rows": [], "totalNet": 0.0, "totalVat": 0.0, "totalGross": 0.0}

    # Aggregate net by VAT code
    by_code: Dict[str, float] = {}
    rates_by_code: Dict[str, List[float]] = {}
    for it in items_for_metrics:
        vc = str(it.get("vatCode") or "").strip().upper()
        if not vc:
            continue
        net = _safe_float(it.get("lineNet"))
        by_code[vc] = by_code.get(vc, 0.0) + net
        r = _safe_float(it.get("vatRate"))
        if r > 0:
            rates_by_code.setdefault(vc, []).append(r)

    rows: List[Dict[str, Any]] = []
    for vc, net_sum in by_code.items():
        # Prefer the most common vatRate observed for this code; else fall back to default map
        rate = 0.0
        try:
            lst = rates_by_code.get(vc) or []
            if lst:
                from collections import Counter
                rate = float(Counter([round(x, 2) for x in lst]).most_common(1)[0][0])
            else:
                rate = _safe_float(PARFETTS_VAT_MAP_DEFAULT.get(vc))
        except Exception:
            rate = _safe_float(PARFETTS_VAT_MAP_DEFAULT.get(vc))
        vat_sum = round(float(net_sum) * float(rate) / 100.0, 2)
        rows.append({
            "vatCode": vc,
            "goodsAmount": round(float(net_sum), 2),
            "vatAmount": float(vat_sum),
        })

    # Sort A then Z then others for a familiar Parfetts look
    def _vk(x: str) -> Tuple[int, str]:
        if x == "A":
            return (0, x)
        if x == "Z":
            return (1, x)
        return (2, x)

    rows = sorted(rows, key=lambda r: _vk(str(r.get("vatCode") or "").upper()))

    tot_net = round(sum(_safe_float(r.get("goodsAmount")) for r in rows), 2)
    tot_vat = round(sum(_safe_float(r.get("vatAmount")) for r in rows), 2)
    tot_gross = round(tot_net + tot_vat, 2)
    return {"rows": rows, "totalNet": tot_net, "totalVat": tot_vat, "totalGross": tot_gross}


# ---------------------------
# Generic VAT Breakdown helpers (Booker, DWG, others)
# ---------------------------

def _vat_code_rate_map_for_supplier(supplier_hint: str) -> Dict[str, float]:
    """Supplier-specific VAT code → rate mapping (UK, percent).
    Used to infer vatRate when vatCode exists but vatRate is missing/blank/non-numeric.
    """
    s = (supplier_hint or "").lower()

    # Dhamecha: A=20%, B=5%, Z=0%
    if "dhamecha" in s:
        return {"A": 20.0, "B": 5.0, "Z": 0.0}

    # Booker known: B=20%, R=5%, Z=0%, A=0% (per your template guidance).
    if "booker" in s:
        return {"B": 20.0, "R": 5.0, "Z": 0.0, "A": 0.0}

    # DWG common: S/A or S => 20%
    if ("drinks wholesale" in s) or ("dwg" in s):
        return {"S/A": 20.0, "S": 20.0}

    # Default common UK codes (best-effort)
    return {"S": 20.0, "T": 20.0, "A": 20.0, "B": 20.0, "R": 5.0, "Z": 0.0, "E": 0.0, "X": 0.0}



def _nearest_uk_vat_rate(x: float) -> float:
    # Snap an inferred VAT rate to common UK rates (0/5/20).
    try:
        x = float(x)
    except Exception:
        return 0.0
    candidates = [0.0, 5.0, 20.0]
    return min(candidates, key=lambda c: abs(c - x))


def _infer_vat_rate(it: Dict[str, Any], supplier_hint: str) -> float:
    # Infer VAT rate for an item: prefer explicit vatRate, else code mapping, else gross-net.
    vr = _safe_float(it.get("vatRate"))
    # Some Cloud models return VAT as a fraction (e.g. 0.2 for 20%). Normalise to percent.
    if 0 < vr < 1.0:
        vr = vr * 100.0
    if vr > 0:
        return vr
    vc = str(it.get("vatCode") or "").strip().upper()
    m = _vat_code_rate_map_for_supplier(supplier_hint)
    if vc and vc in m:
        return float(m[vc])
    net = _safe_float(it.get("lineNet"))
    gross = _safe_float(it.get("lineGross"))
    if net > 0 and gross > net:
        approx = ((gross - net) / net) * 100.0
        return float(_nearest_uk_vat_rate(approx))
    return 0.0






def _vat_breakdown_from_items(items: List[Dict[str, Any]], supplier_hint: str, header_vat: float = 0.0) -> Dict[str, Any]:
    # Compute VAT breakdown rows (vatCode, goodsAmount, vatAmount) from line items.
    # VAT method is chosen to best match header_vat where available.
    if not items:
        return {"rows": [], "totalNet": 0.0, "totalVat": 0.0, "totalGross": 0.0}

    by_code_net: Dict[str, float] = {}
    by_code_vat_sum: Dict[str, float] = {}
    by_code_rate: Dict[str, float] = {}

    for it in items:
        if not isinstance(it, dict):
            continue
        vc = str(it.get("vatCode") or "").strip().upper() or "NA"
        net = _safe_float(it.get("lineNet"))
        vat_line = _safe_float(it.get("vatAmount"))
        by_code_net[vc] = by_code_net.get(vc, 0.0) + net
        by_code_vat_sum[vc] = by_code_vat_sum.get(vc, 0.0) + vat_line
        r = _infer_vat_rate(it, supplier_hint)
        if vc not in by_code_rate or _safe_float(by_code_rate.get(vc)) == 0.0:
            by_code_rate[vc] = r
        else:
            by_code_rate[vc] = max(_safe_float(by_code_rate.get(vc)), r)

    total_vat_sum = round(sum(by_code_vat_sum.values()), 2)
    total_vat_calc = 0.0
    vat_calc_by_code: Dict[str, float] = {}
    for vc, net in by_code_net.items():
        r = _safe_float(by_code_rate.get(vc))
        vat_c = round(net * r / 100.0, 2)
        vat_calc_by_code[vc] = vat_c
        total_vat_calc += vat_c
    total_vat_calc = round(total_vat_calc, 2)

    # Choose best method (calc vs sum)
    hv = round(_safe_float(header_vat), 2)
    if hv > 0:
        use_calc = abs(hv - total_vat_calc) <= abs(hv - total_vat_sum)
    else:
        use_calc = (total_vat_sum == 0.0)

    rows: List[Dict[str, Any]] = []
    total_net = 0.0
    total_vat = 0.0
    for vc, net in sorted(by_code_net.items(), key=lambda kv: kv[0]):
        net = round(net, 2)
        vat_amt = round(vat_calc_by_code.get(vc, 0.0), 2) if use_calc else round(by_code_vat_sum.get(vc, 0.0), 2)
        total_net += net
        total_vat += vat_amt
        rows.append({"vatCode": vc, "goodsAmount": net, "vatAmount": vat_amt, "_source": "calculated"})

    total_net = round(total_net, 2)
    total_vat = round(total_vat, 2)
    total_gross = round(total_net + total_vat, 2)
    return {"rows": rows, "totalNet": total_net, "totalVat": total_vat, "totalGross": total_gross}


def _ensure_vat_breakdown(header: Dict[str, Any], items: List[Dict[str, Any]], supplier_hint: str) -> None:
    # Ensure header.vatBreakdown exists for consistent Dashboard UI across suppliers.
    try:
        rows = header.get("vatBreakdown")
        if isinstance(rows, list) and rows:
            for r in rows:
                if isinstance(r, dict) and "_source" not in r:
                    r["_source"] = "invoice"
            header["vatBreakdown"] = rows
            return
        if items:
            calc = _vat_breakdown_from_items(items, supplier_hint, header_vat=_safe_float(header.get("totalVat")))
            rows2 = calc.get("rows") or []
            if rows2:
                header["vatBreakdown"] = rows2
    except Exception:
        return




def _title_case(s: str) -> str:
    """Lightweight title-casing for names (keeps short acronyms upper-case)."""
    s = (s or "").strip()
    if not s:
        return ""
    words = re.split(r"\s+", s)
    out = []
    for w in words:
        w = w.strip()
        if not w:
            continue
        # Keep acronyms/codes such as UK, VAT, LTD
        if w.isupper() and len(w) <= 4:
            out.append(w)
        else:
            out.append(w[:1].upper() + w[1:].lower() if w.isalpha() else w.title())
    return " ".join(out)


def _parfetts_calc_por(std_rrp: float, vat_rate: float, unit_price_case: float, case_pack: int) -> float:
    try:
        if std_rrp <= 0 or unit_price_case <= 0 or case_pack <= 0:
            return 0.0
        sell_ex_vat = float(std_rrp)
        if vat_rate and vat_rate > 0:
            sell_ex_vat = sell_ex_vat / (1.0 + (float(vat_rate) / 100.0))
        cost_unit = float(unit_price_case) / float(case_pack)
        if sell_ex_vat <= 0:
            return 0.0
        por = ((sell_ex_vat - cost_unit) / sell_ex_vat) * 100.0
        return round(float(por), 2)
    except Exception:
        return 0.0



def _parfetts_unit_size_before(lines: List[str], idx: int, lookback: int = 6) -> str:
    """
    Best-effort unit-size extraction for Parfetts invoices.

    Parfetts PDFs often place a unit-size token (e.g., 440ML, 75CL, 130G, 8PK) a few lines
    *before* the item code. This helper scans upwards a small window to capture it.

    Returns an uppercase token with spaces removed (e.g., "75CL"). If not found, returns "".
    """
    import re
    for k in range(1, lookback + 1):
        j = idx - k
        if j < 0:
            break
        s = (lines[j] or "").strip()
        if not s:
            continue

        # Common liquid/weight sizes: 440ML, 75CL, 1L, 130G, 2KG, 12OZ
        m = re.search(r"\b(\d+(?:\.\d+)?)(?:\s*)(ML|CL|L|G|KG|OZ)\b", s, flags=re.IGNORECASE)
        if m:
            return f"{m.group(1)}{m.group(2).upper()}".replace(" ", "")

        # Packs: 4PK, 8PK, 6PACK
        m = re.search(r"\b(\d+)\s*(PK|PACK)\b", s, flags=re.IGNORECASE)
        if m:
            return f"{m.group(1)}PK"

        # Occasionally appears like "5'S" for gum; keep as-is normalised
        m = re.search(r"\b(\d+)\s*'S\b", s, flags=re.IGNORECASE)
        if m:
            return f"{m.group(1)}S"

    return ""







def _strip_models_prefix(model_name: str) -> str:
    mn = (model_name or "").strip()
    if mn.startswith("models/"):
        return mn.split("/", 1)[1].strip()
    return mn

def _is_model_not_found_error(err: Exception) -> bool:
    msg = str(err).lower()
    return ("not_found" in msg) or ("not found" in msg) or ("model" in msg and "not" in msg and "found" in msg)

def _pick_best_gemini_model(model_names, preferred: str = "") -> str:
    preferred = _strip_models_prefix(preferred or "")
    norm = [_strip_models_prefix(x) for x in (model_names or []) if str(x).strip()]
    if not norm:
        return preferred or ""
    # Exact match
    if preferred and preferred in norm:
        return preferred
    # Suffix match (handles models/ prefix)
    if preferred:
        for m in norm:
            if m.endswith(preferred):
                return m
    # Prefer flash models first (speed + cost), then any gemini
    flash = [m for m in norm if "flash" in m]
    if flash:
        # Prefer the newest (often 2.0 > 1.5) by string sort heuristic
        flash_sorted = sorted(flash, reverse=True)
        return flash_sorted[0]
    gem = [m for m in norm if "gemini" in m]
    if gem:
        return sorted(gem, reverse=True)[0]
    return norm[0]

def _resolve_gemini_model_google_genai(client, preferred: str) -> str:
    """Resolve to an available model name for google-genai SDK."""
    preferred = _strip_models_prefix(preferred or "")
    if not client:
        return preferred
    # Quick validate via models.get (if available)
    try:
        if preferred:
            client.models.get(model=preferred)
            return preferred
    except Exception as e:
        if not _is_model_not_found_error(e):
            return preferred
    # List and pick
    model_names = []
    try:
        for m in client.models.list():
            try:
                supported = getattr(m, "supported_actions", None) or []
            except Exception:
                supported = []
            name = getattr(m, "name", None) or getattr(m, "model", None) or str(m)
            name = str(name)
            if "generatecontent" in "".join([str(x).lower() for x in supported]) or (not supported):
                model_names.append(name)
    except Exception:
        model_names = []
    pick = _pick_best_gemini_model(model_names, preferred=preferred)
    return pick or preferred

def _resolve_gemini_model_google_generativeai(genai_module, preferred: str) -> str:
    """Resolve to an available model name for google-generativeai SDK."""
    preferred = _strip_models_prefix(preferred or "")
    if genai_module is None:
        return preferred
    try:
        if preferred:
            # Not all versions support get_model; safe-try
            try:
                genai_module.get_model("models/" + preferred)
                return preferred
            except Exception:
                pass
    except Exception:
        pass
    model_names = []
    try:
        for m in genai_module.list_models():
            name = getattr(m, "name", "") or ""
            supported = getattr(m, "supported_generation_methods", None) or getattr(m, "supported_generation_methods", []) or []
            # Prefer models that can generate content
            if supported:
                if any(str(x).lower() == "generatecontent" for x in supported):
                    model_names.append(name)
            else:
                model_names.append(name)
    except Exception:
        model_names = []
    pick = _pick_best_gemini_model(model_names, preferred=preferred)
    return pick or preferred



def _invoice_json_schema_purchase() -> Dict[str, Any]:
    """JSON Schema used to force strict model output for Purchase Invoices (Cloud Gemini)."""
    return {
        "type": "object",
        "required": ["header", "items"],
        "properties": {
            "header": {
                "type": "object",
                "properties": {
                    "supplierName": {"type": "string"},
                    "customerName": {"type": "string"},
                    "invoiceNumber": {"type": "string"},
                    "invoiceDate": {"type": "string"},
                    "currency": {"type": "string"},
                    "totalNet": {"type": "number"},
                    "totalVat": {"type": "number"},
                    "totalGross": {"type": "number"},
                },
                "additionalProperties": True,
            },
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "lineNo": {"type": "integer"},
                        "code": {"type": "string"},
                        "description": {"type": "string"},
                        "casePack": {"type": "string"},
                        "unitSize": {"type": "string"},
                        "qty": {"type": "number"},
                        "totalUnits": {"type": "number"},
                        "unitPrice": {"type": "number"},
                        "lineNet": {"type": "number"},
                        "vatRate": {"type": "number"},
                        "vatAmount": {"type": "number"},
                        "lineGross": {"type": "number"},
                        "stdRrp": {"type": "number"},
                        "por": {"type": "number"},
                    },
                    "additionalProperties": True,
                },
            },
        },
        "additionalProperties": True,
    }



def _make_inv_key(header: Dict[str, Any], file_bytes: bytes, doc_type: str) -> str:
    sup = (header.get("supplierName") or "").strip().upper()
    cus = (header.get("customerName") or "").strip().upper()
    inv = (header.get("invoiceNumber") or "").strip().upper()
    dt = (_normalise_invoice_date_str(header.get("invoiceDate") or "") or (header.get("invoiceDate") or "").strip())
    base = f"{doc_type}|{sup}|{cus}|{inv}|{dt}|{_sha256_bytes(file_bytes)[:12]}"
    return hashlib.sha256(base.encode("utf-8")).hexdigest()



def _ensure_line_numbers(items: Any) -> List[Dict[str, Any]]:
    """
    Ensure every line item has a valid sequential lineNo starting at 1.
    Applies to *all* suppliers so CSV exports and UI editing are consistent.
    """
    if not isinstance(items, list):
        return []
    out: List[Dict[str, Any]] = []
    for idx, it in enumerate(items, start=1):
        if not isinstance(it, dict):
            continue
        it["lineNo"] = idx
        out.append(it)
    return out



def _build_reconciliation_status(metrics: dict, eps: float = 0.02) -> dict:
    """Build a compact reconciliation dict for UI + PDF reports."""
    sum_net = _safe_float(metrics.get('net_items'))
    sum_vat = _safe_float(metrics.get('vat_items'))
    sum_gross = _safe_float(metrics.get('gross_items'))
    hdr_net = _safe_float(metrics.get('net_header'))
    hdr_vat = _safe_float(metrics.get('vat_header'))
    hdr_gross = _safe_float(metrics.get('gross_header'))

    def _ok(a: float, b: float) -> bool:
        return abs(_safe_float(a) - _safe_float(b)) <= float(eps)

    return {
        'netStatus': 'OK' if _ok(sum_net, hdr_net) else 'CHECK',
        'vatStatus': 'OK' if _ok(sum_vat, hdr_vat) else 'CHECK',
        'grossStatus': 'OK' if _ok(sum_gross, hdr_gross) else 'CHECK',
    }


def _build_reconciliation_notes(metrics: dict, eps: float = 0.02) -> list[str]:
    """Human-readable notes explaining mismatches."""
    notes: list[str] = []
    sum_net = _safe_float(metrics.get('net_items'))
    sum_vat = _safe_float(metrics.get('vat_items'))
    sum_gross = _safe_float(metrics.get('gross_items'))
    hdr_net = _safe_float(metrics.get('net_header'))
    hdr_vat = _safe_float(metrics.get('vat_header'))
    hdr_gross = _safe_float(metrics.get('gross_header'))

    if abs(sum_net - hdr_net) > eps:
        notes.append('Net mismatch: check for missing delivery/discount lines or OCR gaps.')
    if abs(sum_vat - hdr_vat) > eps:
        notes.append('VAT mismatch: check VAT codes/rates per line and rounding (per-line vs invoice-level).')
    if abs(sum_gross - hdr_gross) > eps:
        notes.append('Gross mismatch: check net+VAT consistency across all lines.')
    if not notes:
        notes.append('Header totals reconcile with line items (within tolerance).')
    return notes


def _get_register_rows(doc_type: str, limit: int = 200) -> List[Dict[str, Any]]:
    rows = _db_fetchall(
        """SELECT * FROM purchase_invoices
           WHERE doc_type=?
           ORDER BY processed_at DESC
           LIMIT ?""",
        (doc_type, int(limit)),
    )
    return [dict(r) for r in rows]


def _export_payload_purchase_invoice(inv_key: str) -> Dict[str, Any]:
    """Build combined export payload including customer profile + invoice header + line items.
    This is the 'single JSON' per invoice used for reconciliation and future SPOS Cloud mapping.
    """
    header_row = _db_fetchone("SELECT * FROM purchase_invoices WHERE inv_key=?", (inv_key,))
    if not header_row:
        return {}
    items_rows = _db_fetchall(
        "SELECT * FROM purchase_invoice_items WHERE inv_key=? ORDER BY line_no",
        (inv_key,),
    )

    # Selected customer profile comes from Dashboard customer selector (CMS-first)
    cp = st.session_state.get("selected_customer_profile") or {}

    payload: Dict[str, Any] = {
        "exportedAt": _uk_now_iso(),
        "customer": {
            "CMS_CustomerID": cp.get("cms_customer_id", "") or "",
            "SPOS_CustomerID": (cp.get("spos_customer_id") or "N/A"),
            "Customer_Name": cp.get("customer_name", "") or "",
            "Trading_Name": cp.get("trading_name", "") or "",
            "Company_Name": cp.get("company_name", "") or "",
            "VAT_Registration_No": cp.get("vat_registration_no", "") or "",
            "Contact_No": cp.get("contact_no", "") or "",
            "Email_Address": cp.get("email_address", "") or "",
        },
        "invoiceHeader": dict(header_row),
        "lineItems": [dict(r) for r in (items_rows or [])],
    }

    # If we already calculate/store VAT breakdown elsewhere, keep it when present
    try:
        if "vatBreakdown" in dict(header_row):
            payload["vatBreakdown"] = dict(header_row).get("vatBreakdown")
    except Exception:
        pass

    return payload



def _compute_vat_breakdown_from_lines(lines: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Compute VAT breakdown rows from line items (vat_code + vat_rate)."""
    buckets: Dict[Tuple[str, float], Dict[str, float]] = {}
    for it in (lines or []):
        code = str(it.get("vat_code") or "").strip() or "NA"
        try:
            rate = float(it.get("vat_rate") or 0.0)
        except Exception:
            rate = 0.0
        key = (code, rate)
        b = buckets.setdefault(key, {"net": 0.0, "vat": 0.0, "gross": 0.0})
        try:
            b["net"] += float(it.get("line_net") or 0.0)
        except Exception:
            pass
        try:
            b["vat"] += float(it.get("vat_amount") or 0.0)
        except Exception:
            pass
        try:
            b["gross"] += float(it.get("line_gross") or 0.0)
        except Exception:
            pass

    out: List[Dict[str, Any]] = []
    for (code, rate), sums in sorted(buckets.items(), key=lambda x: (x[0][0], x[0][1])):
        out.append({
            "vat_code": code,
            "vat_rate": rate,
            "net": round(sums["net"], 2),
            "vat": round(sums["vat"], 2),
            "gross": round(sums["gross"], 2),
        })
    return out




def _spos_export_config(settings: Dict[str, Any]) -> Dict[str, Any]:
    """Return SPOS export configuration (placeholder until API endpoint/auth is finalised)."""
    cfg = settings.get("spos_export") or {}
    return {
        "enabled": bool(cfg.get("enabled", False)),
        "base_url": str(cfg.get("base_url", "") or ""),
        "auth_method": str(cfg.get("auth_method", "") or ""),
        "auth_token": str(cfg.get("auth_token", "") or ""),
        "endpoint_pattern": str(cfg.get("endpoint_pattern", "") or ""),
    }


def _reset_register(doc_type: str) -> int:
    """
    Admin action: deletes stored documents from the register for the given doc_type.
    Returns number of invoices deleted.
    """
    inv_keys = _db_fetchall("SELECT inv_key FROM purchase_invoices WHERE doc_type=?", (doc_type,))
    inv_keys = [r["inv_key"] for r in inv_keys] if inv_keys else []
    if not inv_keys:
        return 0

    # Delete child rows first
    for k in inv_keys:
        _db_exec("DELETE FROM purchase_invoice_items WHERE inv_key=?", (k,))
    _db_exec("DELETE FROM purchase_invoices WHERE doc_type=?", (doc_type,))
    return len(inv_keys)


def _get_invoice(inv_key: str) -> Optional[Dict[str, Any]]:
    row = _db_fetchone("SELECT * FROM purchase_invoices WHERE inv_key=?", (inv_key,))
    if not row:
        return None
    inv = dict(row)
    items = _db_fetchall("SELECT * FROM purchase_invoice_items WHERE inv_key=? ORDER BY line_no", (inv_key,))
    inv["items"] = [dict(i) for i in items]
    return inv


# -------------------------
# PRD + System Info
# -------------------------


def _default_prd_markdown() -> str:
    return """# DocMate Technical PRD (Draft)

## 1. Purpose
DocMate is a web application by **Visual Business Retail Ltd (VBR)** that captures invoices and documents, extracts key header values and (where required) line items, and saves validated data into registers to support downstream workflows such as **VBR EPOS**, **VBR Cloud Reporting**, **Loyalty**, and **Online Ordering**.

## 2. Key Concepts
- **Document Types**
  - Purchase Invoice (payable)
  - Service Invoice (payable)
  - Sales Invoice (receivable)
  - Commission Invoice (receivable)

- **Templates**
  - Supplier Templates: matched for Purchase/Service documents using supplier identity.
  - Customer Templates: matched for Sales/Commission documents using customer identity.

## 3. Processing Engines
- **Auto**: Template match → PDF text layer → OCR fallback → (optional) Cloud AI.
- **Local OCR**: Tesseract / EasyOCR / PaddleOCR.
- **Cloud AI**: Customer provides their own Google API key (stored securely) for template building/extraction.

## 4. Registers
- Each saved document is routed to its register based on **Document Type**.
- Purchase invoices can capture full line items; Service invoices can be configured to capture header + simplified items.

## 5. Admin / System Information
- System tree: version history, PRD, event logs, and configuration.
- Customer profile: Site ID, trading name/address, buyer aliases.

## 6. Booker (BKL) Priority
- Booker purchase invoices are supported with a fast path (PDF text layer first), and a supplier template ID prefix **BKL**.
"""


def _db_get_system_doc(doc_key: str) -> Optional[Dict[str, Any]]:
    row = _db_fetchone("SELECT * FROM system_docs WHERE doc_key=?", (doc_key,))
    return dict(row) if row else None


def _db_update_system_doc(doc_key: str, title: str, content_md: str) -> None:
    _db_exec(
        "INSERT OR REPLACE INTO system_docs(doc_key, title, content_md, updated_at) VALUES(?,?,?,?)",
        (doc_key, title, content_md, _uk_now_iso()),
    )


def _db_get_versions() -> List[Dict[str, Any]]:
        # Backwards compatible: older DBs may have 'notes' column; current schema uses 'release_note'.
    try:
        cols = [r["name"] for r in _db_fetchall("PRAGMA table_info(version_history)")]
    except Exception:
        cols = []
    note_col = "notes" if "notes" in cols else ("release_note" if "release_note" in cols else None)
    if note_col:
        rows = _db_fetchall(f"SELECT version, release_date, {note_col} AS notes FROM version_history")
    else:
        rows = _db_fetchall("SELECT version, release_date, '' AS notes FROM version_history")

    versions = [dict(r) for r in rows]

    def _vkey(v: str) -> Tuple[int, int, int]:
        m = re.search(r"(\d+)\.(\d+)\.(\d+)", v or "")
        if not m:
            return (0, 0, 0)
        return (int(m.group(1)), int(m.group(2)), int(m.group(3)))

    versions.sort(key=lambda d: (_vkey(d.get("version", "")), d.get("release_date", "")), reverse=True)
    return versions


# -------------------------
# Auth
# -------------------------

def _hash_password(pw: str) -> str:
    return hashlib.sha256((pw or "").encode("utf-8")).hexdigest()


def _is_admin_user(email: str) -> bool:
    # Default admin: krishna@docmate.store (as per earlier builds)
    # Also allow VBR internal email(s) by config later.
    email = (email or "").strip().lower()
    return email in {"krishna@docmate.store", "krishna@visualbusinessretail.com", "tillsupport@visualbusinessretail.com"}


def _is_developer_user(email: str) -> bool:
    email = (email or "").strip().lower()
    return email in {"krishna@docmate.store"}



def _is_admin_user_email(email: str, settings: dict) -> bool:
    email = (email or "").strip().lower()
    if not email:
        return False
    # Built-in list + optional configurable list
    extra = settings.get("admin_emails") or settings.get("adminEmails") or []
    if isinstance(extra, str):
        extra = [e.strip() for e in extra.split(",") if e.strip()]
    extra = [str(e).strip().lower() for e in extra]
    return _is_admin_user(email) or (email in extra)

def _is_developer_user_email(email: str, settings: dict) -> bool:
    email = (email or "").strip().lower()
    if not email:
        return False
    extra = settings.get("developer_emails") or settings.get("developerEmails") or []
    if isinstance(extra, str):
        extra = [e.strip() for e in extra.split(",") if e.strip()]
    extra = [str(e).strip().lower() for e in extra]
    return _is_developer_user(email) or (email in extra)

def _is_developer() -> bool:
    """Return True when current session is a developer user."""
    if st.session_state.get("is_developer") is None:
        st.session_state["is_developer"] = False
    if not st.session_state.get("is_developer"):
        email = (st.session_state.get("user_email") or "").strip().lower()
        if email:
            st.session_state["is_developer"] = _is_developer_user_email(email, st.session_state.get("settings", {}))
    return bool(st.session_state.get("is_developer"))




def _decide_party_table(doc_type: str) -> str:
    """Return the template table name for the given document type.

    DocMate currently stores supplier templates in `supplier_templates`. If/when
    customer-side templates are added, this function is the single switch point.
    """
    # Future: add customer_templates for Sales invoices, statements, etc.
    return "supplier_templates"

def _is_admin() -> bool:
    """Return True when the current session is an admin user."""
    if st.session_state.get("is_admin") is None:
        # normalise None to False
        st.session_state["is_admin"] = False
    if not st.session_state.get("is_admin"):
        email = (st.session_state.get("user_email") or "").strip().lower()
        if email:
            st.session_state["is_admin"] = _is_admin_user_email(email, st.session_state.get("settings", {}))
    return bool(st.session_state.get("is_admin"))




# -------------------------
# Auth
# -------------------------

def _hash_password(pw: str) -> str:
    return hashlib.sha256((pw or "").encode("utf-8")).hexdigest()


def _is_admin_user(email: str) -> bool:
    # Default admin: krishna@docmate.store (as per earlier builds)
    # Also allow VBR internal email(s) by config later.
    email = (email or "").strip().lower()
    return email in {"krishna@docmate.store", "krishna@visualbusinessretail.com", "tillsupport@visualbusinessretail.com"}


def _is_developer_user(email: str) -> bool:
    email = (email or "").strip().lower()
    return email in {"krishna@docmate.store"}


def _is_developer() -> bool:
    return _is_developer_user(st.session_state.get("email_address", ""))



def _is_admin() -> bool:
    """Return True when the current session is an admin user."""
    return bool(st.session_state.get("is_admin"))


# -------------------------
# Time / Zone helpers (UI clock + Admin configuration)
# -------------------------

_REGION_TIMEZONES = {
    "Europe": [
        ("London (GMT +00:00)", "Europe/London"),
        ("Paris (CET +01:00)", "Europe/Paris"),
    ],
    "America": [
        ("New York (EST -05:00)", "America/New_York"),
        ("Chicago (CST -06:00)", "America/Chicago"),
        ("Los Angeles (PST -08:00)", "America/Los_Angeles"),
    ],
    "Asia": [
        ("Colombo (IST +05:30)", "Asia/Colombo"),
        ("Dubai (GST +04:00)", "Asia/Dubai"),
        ("Singapore (SGT +08:00)", "Asia/Singapore"),
    ],
}



def _validate_user_login(email: str, password: str) -> bool:
    # Basic credential check for DocMate (Streamlit) login gate.
    # Default accounts (as per deployment SOP):
    #   - user@docmate.store / user
    #   - admin@docmate.store / admin
    email = (email or "").strip().lower()
    password = (password or "").strip()
    if not email or not password:
        return False

    # NOTE: This list is intentionally small and intended for local/POC deployments.
    # For production, replace with a proper identity provider (e.g., Microsoft Entra ID)
    # and enforce MFA.
    default_users = {
        'user@docmate.store': 'user',
        'admin@docmate.store': 'admin',
        # Developer/Admin convenience (requested):
        'krishna@docmate.store': 'Vbr2025#',
    }

    # Allow internal VBR admin logins to use the admin password by default.
    if email in ('krishna@visualbusinessretail.com', 'tillsupport@visualbusinessretail.com'):
        return password == 'admin'

    # Allow a small amount of flexibility for the requested Krishna dev login
    # (common copy/paste / case variations).
    if email == 'krishna@docmate.store':
        return (password or '').strip() in {'Vbr2025#', 'vbr2025#', 'VBR2025#'}

    expected = default_users.get(email)
    if expected is None:
        return False
    return password == expected

def _get_time_zone_settings(settings: Dict[str, Any]) -> Dict[str, Any]:
    tzs = settings.get("time_zone") if isinstance(settings.get("time_zone"), dict) else {}
    region = (tzs.get("region") or "Europe").strip() or "Europe"
    tz_name = (tzs.get("tz_name") or UK_TZ).strip() or UK_TZ
    use_ntp = bool(tzs.get("use_ntp", False))
    ntp_server = (tzs.get("ntp_server") or "pool.ntp.org").strip() or "pool.ntp.org"
    return {"region": region, "tz_name": tz_name, "use_ntp": use_ntp, "ntp_server": ntp_server}


@st.cache_data(ttl=60)
def _ntp_time_utc_cached(server: str) -> Optional[datetime]:
    """Fetch NTP time (UTC) with cache. If ntplib isn't available or request fails, returns None."""
    try:
        import ntplib  # type: ignore
    except Exception:
        return None
    try:
        c = ntplib.NTPClient()
        r = c.request(server, version=3)
        return datetime.fromtimestamp(r.tx_time, tz=timezone.utc)
    except Exception:
        return None


def _now_in_configured_tz(settings: Dict[str, Any]) -> datetime:
    cfg = _get_time_zone_settings(settings)
    tz_name = cfg["tz_name"]
    # Prefer NTP if enabled and available
    if cfg.get("use_ntp"):
        ntp_dt = _ntp_time_utc_cached(cfg.get("ntp_server") or "pool.ntp.org")
        if ntp_dt is not None:
            try:
                if ZoneInfo:
                    return ntp_dt.astimezone(ZoneInfo(tz_name))
            except Exception:
                pass
    # Fallback: system clock
    try:
        if ZoneInfo:
            return datetime.now(tz=ZoneInfo(tz_name))
    except Exception:
        pass
    return datetime.now()



def _get_time_zone_label(settings: Dict[str, Any]) -> str:
    'Return a short label for the configured time zone (e.g., GMT, CET, EST).'
    cfg = _get_time_zone_settings(settings)
    tz_name = (cfg.get('tz_name') or UK_TZ).strip() or UK_TZ
    if tz_name in (UK_TZ, 'Europe/London'):
        return 'GMT'
    try:
        now = _now_in_configured_tz(settings)
        if ZoneInfo:
            return (now.astimezone(ZoneInfo(tz_name)).tzname() or tz_name)
    except Exception:
        pass
    return tz_name

def _maybe_prepare_booker_template_proposal(settings: Dict[str, Any], doc_text: str) -> None:
    """Prepare (but DO NOT create) a Booker supplier template proposal for manual approval."""
    if "BOOKER" not in (doc_text or "").upper():
        return
    # If a Booker template already exists, do nothing
    try:
        existing = _db_find_best_template(doc_text, "supplier_templates", threshold=3)
        if existing and (existing.get("parser_type") or "").upper() == "BOOKER":
            return
    except Exception:
        pass

    # Default VAT mapping (can be edited before confirming)
    vat_map = {"B": 20.0, "Z/A": 0.0, "A": 0.0, "R": 5.0, "Z": 0.0}

    proposal = {
        "supplier_name": "Booker Ltd",
        "parser_type": "BOOKER",
        "requires_cloud": 0,
        "match_keywords": ["BOOKER", "BOOKER LTD", "BOOKER LIMITED", "BKL"],
        "config": {"preferred_local_engine": "tesseract", "vat_mapping": vat_map},
    }
    st.session_state["booker_template_proposal"] = proposal

def _auto_template_for_booker_if_needed(settings: Dict[str, Any], doc_text: str) -> Optional[Dict[str, Any]]:
    """Legacy hook kept for compatibility — now *proposes* a template for manual approval."""
    if not settings.get("auto_create_booker_template", True):
        return None
    _maybe_prepare_booker_template_proposal(settings, doc_text)
    return None




def _fmt_int(value) -> str:
    """Format KPI counts as whole numbers."""
    try:
        if value is None:
            return "0"
        if isinstance(value, str):
            v = value.strip()
            if v == "":
                return "0"
            value = float(v)
        return str(int(round(float(value))))
    except Exception:
        return "0"






def _render_templates(settings: Dict[str, Any]) -> None:
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

def _render_system_info(*args, **kwargs):
    from docmate.ui.system_info import render_system_info as _fn
    return _fn(*args, **kwargs)

def _render_database_management(*args, **kwargs):
    from docmate.ui.database_management import render_database_management as _fn
    return _fn(*args, **kwargs)

def _login_gate(*args, **kwargs):
    from docmate.ui.auth import login_gate as _fn
    return _fn(*args, **kwargs)

def main(*args, **kwargs):
    from docmate.app_main import main as _m
    return _m(*args, **kwargs)

def _render_live_clock_card(*args, **kwargs):
    from docmate.ui.widgets import render_live_clock_card as _fn
    return _fn(*args, **kwargs)

def _render_vat_breakdown_box(*args, **kwargs):
    from docmate.ui.widgets import render_vat_breakdown_box as _fn
    return _fn(*args, **kwargs)

def _render_vat_breakdown_box_calculated(*args, **kwargs):
    from docmate.ui.widgets import render_vat_breakdown_box_calculated as _fn
    return _fn(*args, **kwargs)

def _render_reconciliation_box_html(*args, **kwargs):
    from docmate.ui.widgets import render_reconciliation_box_html as _fn
    return _fn(*args, **kwargs)

def _render_reconciliation_reasons_html(*args, **kwargs):
    from docmate.ui.widgets import render_reconciliation_reasons_html as _fn
    return _fn(*args, **kwargs)

def _render_template_table(*args, **kwargs):
    from docmate.ui.widgets import render_template_table as _fn
    return _fn(*args, **kwargs)



# --- Auto-generated thin wrappers (extracted blocks)

def _render_dashboard(*args, **kwargs):
    from docmate.ui.dashboard import render_dashboard as _fn
    return _fn(*args, **kwargs)

def _render_registers(*args, **kwargs):
    from docmate.ui.registers import render_registers as _fn
    return _fn(*args, **kwargs)

def _render_reports(*args, **kwargs):
    from docmate.ui.reports import render_reports as _fn
    return _fn(*args, **kwargs)

def _render_admin_settings(*args, **kwargs):
    from docmate.ui.admin import render_admin_settings as _fn
    return _fn(*args, **kwargs)

def _render_customer_profiles(*args, **kwargs):
    from docmate.ui.customer_profiles import render_customer_profiles as _fn
    return _fn(*args, **kwargs)

def _dwg_enrich_header_items(*args, **kwargs):
    from docmate.parsers.dwg import _dwg_enrich_header_items as _fn
    return _fn(*args, **kwargs)

def _dhamecha_build_item_map(*args, **kwargs):
    from docmate.parsers.dhamecha import _dhamecha_build_item_map as _fn
    return _fn(*args, **kwargs)

def _dhamecha_enrich_header_items(*args, **kwargs):
    from docmate.parsers.dhamecha import _dhamecha_enrich_header_items as _fn
    return _fn(*args, **kwargs)

def _preview_enrich_items(*args, **kwargs):
    from docmate.core.enrichment import _preview_enrich_items as _fn
    return _fn(*args, **kwargs)

def _calc_metrics(*args, **kwargs):
    from docmate.core.metrics import _calc_metrics as _fn
    return _fn(*args, **kwargs)

def _upsert_register(*args, **kwargs):
    from docmate.core.register import _upsert_register as _fn
    return _fn(*args, **kwargs)

def _generate_html_report(*args, **kwargs):
    from docmate.core.reports_html import _generate_html_report as _fn
    return _fn(*args, **kwargs)
