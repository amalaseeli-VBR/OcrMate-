from __future__ import annotations
from typing import Any
import math
import re

# NOTE: extracted from legacy.py

def _is_empty(v: object) -> bool:
    if v is None:
        return True
    if isinstance(v, str) and v.strip().lower() in ("", "none", "nan", "null"):
        return True
    return False
def _safe_float(x: object, default: float = 0.0) -> float:
    """Parse a value to float safely.

    - Treat None/blank as default
    - Treat NaN/Inf as default (prevents NaN propagation into totals/breakdowns)
    - Supports simple arithmetic formulas from the UI editor (e.g. '=57.98+11.60')
    - Extracts the first numeric token from strings (handles '£', ',', '%' and OCR noise)
    """
    if x is None:
        return default

    # Fast path for numeric types (including numpy floats)
    if isinstance(x, (int, float)):
        try:
            v = float(x)
            if math.isnan(v) or math.isinf(v):
                return default
            return v
        except Exception:
            return default

    # Allow simple formula entry from the UI editor, e.g. '=57.98+11.60'
    if isinstance(x, str):
        s = x.strip()
        if not s or s.lower() in ("none", "null", "nan"):
            return default

        if s.startswith("=") or (re.search(r"[\+\*\/\(\)]", s) and re.search(r"\d", s)):
            v = _safe_eval_arith(s)
            if v is not None:
                # _safe_eval_arith already guards NaN/Inf
                return v

        # Normalise common symbols then extract first numeric token
        s = s.replace(",", "").replace("£", "").replace("%", "")
        # Handle trailing-dash accounting notation: "22.29-" → "-22.29", "1-" → "-1"
        if s.endswith("-") and len(s) > 1 and re.search(r"\d", s):
            s = "-" + s[:-1]
        m = re.search(r"-?\d+(?:\.\d+)?", s)
        if not m:
            return default
        try:
            v = float(m.group(0))
            if math.isnan(v) or math.isinf(v):
                return default
            return v
        except Exception:
            return default

    try:
        v = float(str(x))
        if math.isnan(v) or math.isinf(v):
            return default
        return v
    except Exception:
        return default




# -------------------------
# Formatting / HTML helpers
# -------------------------

import html
_PROMO_LINE_RE = re.compile(
    r"\b(BUY\s*ANY|BUY\s*\d+|BUY\d+|MULTI\s*BUY|MULTIBUY|MULTI-BUY|PROMO|PROMOTION|DEAL|OFFER|SAVE|SAVING|"
    r"DISC(?:OUNT)?|GET\s*\d+(?:\s+\S+){0,3}\s+FREE|BUY\b.*\bFREE)\b",
    re.IGNORECASE,
)
_PROMO_STATEMENT_RE = re.compile(
    r"\b(SPEND\s*&\s*SAVE\s+STATEMENT|SPEND\s+AT\s+THIS\s+BRANCH|"
    r"TOBACCO\s+SPEND|NON[-\s]?TOBACCO\s+SPEND|TOTAL\s+SPEND|"
    r"TERMS\s*&\s*CONDITIONS\s+APPLY|STATEMENT\s+.*\bSPEND)\b",
    re.IGNORECASE,
)


def _is_promo_line_item(item: dict) -> bool:
    if not isinstance(item, dict):
        return False
    code = str(item.get("code") or item.get("Code") or "").strip().upper()
    row_tag = str(item.get("rowTag") or "").strip().upper()
    desc = str(item.get("description") or item.get("Description") or "")
    raw = str(item.get("rawRow") or item.get("raw") or "")
    has_promo_text = bool(_PROMO_LINE_RE.search(desc) or _PROMO_LINE_RE.search(raw))
    has_discount_value = _safe_float(item.get("lineNet"), 0.0) < 0 or _safe_float(item.get("unitPrice"), 0.0) < 0
    if code == "PROMO":
        return True
    if row_tag == "PROMO":
        return has_promo_text or has_discount_value
    return has_promo_text


def _is_promo_statement_line(item: dict) -> bool:
    if not isinstance(item, dict):
        return False
    text = " ".join(
        str(item.get(key) or "")
        for key in ("code", "description", "Description", "rawRow", "raw")
    )
    return bool(_PROMO_STATEMENT_RE.search(text))


def _normalise_promo_quantities(items: list[dict]) -> list[dict]:
    out: list[dict] = []
    for it in items or []:
        if not isinstance(it, dict):
            continue
        if _is_promo_statement_line(it):
            continue
        if not _is_promo_line_item(it):
            out.append(it)
            continue
        it["casePack"] = 0
        it["unitSize"] = 0
        it["qty"] = 0.0
        it["totalUnits"] = 0.0
        it["unitPrice"] = 0.0
        it["stdRrp"] = 0.0
        it["por"] = 0.0
        if not str(it.get("rowTag") or "").strip():
            it["rowTag"] = "PROMO"
        out.append(it)
    return out


def _normalise_vat_rate(v: Any) -> float:
    """Normalise VAT rate so 0.2 becomes 20.0, keep 20 as 20."""
    r = _safe_float(v)
    if 0 < r < 1.0:
        r = r * 100.0
    return round(r, 4)
def _recalc_items(items: list[dict]) -> list[dict]:
    """Recalculate derived fields after edits (and normalise gross/net/vat consistency)."""
    out = []
    for it in _normalise_promo_quantities(list(items or [])):
        it = dict(it or {})
        # Normalise key fields
        qty = _safe_float(it.get("qty"), 0.0)
        case_pack = _safe_float(it.get("casePack"), 0.0)
        unit_price = _safe_float(it.get("unitPrice"), 0.0)

        # VAT rate normalisation
        vat_rate = _normalise_vat_rate(it.get("vatRate"))
        if vat_rate is None:
            vat_rate = 0.0
        it["vatRate"] = vat_rate

        # totalUnits
        total_units = _safe_float(it.get("totalUnits"), 0.0)
        if total_units == 0 and case_pack > 0 and qty > 0:
            total_units = case_pack * qty
        it["totalUnits"] = total_units

        # Amounts (capture what user may have entered)
        has_line_net = not _is_empty(it.get("lineNet"))
        has_vat_amt = not _is_empty(it.get("vatAmount"))
        has_line_gross = not _is_empty(it.get("lineGross"))

        line_net = _safe_float(it.get("lineNet"), 0.0)
        vat_amount = _safe_float(it.get("vatAmount"), 0.0)
        line_gross = _safe_float(it.get("lineGross"), 0.0)

        # If lineNet missing, try compute from qty & unitPrice
        if (not has_line_net or line_net == 0) and qty > 0 and unit_price > 0:
            line_net = round(qty * unit_price, 2)
            it["lineNet"] = line_net
            has_line_net = True

        # Infer missing amount from the other two where possible
        expected_vat = round(line_net * (vat_rate / 100.0), 2) if line_net != 0 and vat_rate != 0 else vat_amount
        vat_implausible = (
            has_vat_amt
            and vat_rate != 0
            and abs(vat_amount - expected_vat) > max(0.05, abs(expected_vat) * 0.10)
            and abs(vat_amount) > max(abs(line_net) * 0.50, 1.0)
        )
        if vat_implausible:
            vat_amount = expected_vat
            it["vatAmount"] = vat_amount

        if has_line_net and has_vat_amt:
            line_gross = round(line_net + vat_amount, 2)
            it["lineGross"] = line_gross
            has_line_gross = True
        elif has_line_net and has_line_gross and not has_vat_amt and line_gross != 0:
            vat_amount = round(line_gross - line_net, 2)
            it["vatAmount"] = vat_amount
            has_vat_amt = True
        elif has_vat_amt and has_line_gross and not has_line_net and line_gross != 0:
            line_net = round(line_gross - vat_amount, 2)
            it["lineNet"] = line_net
            has_line_net = True

        # If VAT still missing, compute from rate
        if (not has_vat_amt or vat_amount == 0) and line_net != 0 and vat_rate != 0:
            vat_amount = round(line_net * (vat_rate / 100.0), 2)
            it["vatAmount"] = vat_amount
            has_vat_amt = True

        # If gross still missing or equals net but VAT exists, compute
        line_gross = _safe_float(it.get("lineGross"), 0.0)
        if (not has_line_gross or line_gross == 0) and line_net != 0:
            line_gross = round(line_net + vat_amount, 2)
            it["lineGross"] = line_gross
            has_line_gross = True
        else:
            # Fix common issue: gross captured as net (missing VAT)
            if abs(line_gross - line_net) < 0.01 and abs(vat_amount) > 0.01:
                it["lineGross"] = round(line_net + vat_amount, 2)

        # unitCost (per stock unit)
        unit_cost = _safe_float(it.get("unitCost"), 0.0)
        if unit_cost == 0 and total_units > 0 and line_net > 0:
            unit_cost = round(line_net / total_units, 4)
        it["unitCost"] = unit_cost

        # POR (%) from stdRrp (inc VAT) when available
        std_rrp_inc = _safe_float(it.get("stdRrp"), 0.0)
        # DWG safeguard: convert pence-based RRP (e.g. 70 from "PM 70P") into pounds.
        try:
            desc_txt = str(it.get("description") or "")
            if std_rrp_inc >= 10 and std_rrp_inc <= 99 and abs(std_rrp_inc - round(std_rrp_inc)) < 0.001:
                if re.search(r"\bPM\b|\bPMP\b", desc_txt, flags=re.IGNORECASE) or re.search(r"\b\d{2}\s*P\b", desc_txt, flags=re.IGNORECASE):
                    std_rrp_inc = round(std_rrp_inc / 100.0, 2)
                    it["stdRrp"] = std_rrp_inc
        except Exception:
            pass

        por = _safe_float(it.get("por"), 0.0)
        if std_rrp_inc > 0 and unit_cost > 0 and vat_rate >= 0:
            sell_ex = std_rrp_inc / (1.0 + (vat_rate / 100.0)) if vat_rate else std_rrp_inc
            if sell_ex > 0:
                por = (sell_ex - unit_cost) / sell_ex * 100.0
                por = round(por, 1)
            it["por"] = por
        else:
            it["por"] = por

        out.append(it)
    return out
