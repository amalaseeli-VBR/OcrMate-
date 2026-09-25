from __future__ import annotations
import json
import re
from typing import Any, Dict, List, Optional

from docmate.core.logging_utils import _log_event
from docmate.core.recalc import _safe_float
from docmate.core.validation import validate_invoice_result
from docmate.core.settings import _read_settings as _load_settings, _get_claude_api_key
from docmate.parsers.booker import BookerCombinedRowParser
from docmate.parsers.dhamecha import _dhamecha_parse_purchase_invoice
from docmate.parsers.dwg import _dwg_extract_header, _parse_dwg_purchase_invoice
from docmate.parsers.parfetts import _parfetts_extract_header, _parse_parfetts_purchase_invoice
from docmate.parsers.ai_fallback import ClaudeAIFallbackParser

_LEGACY_CACHE: Dict[str, Any] = {}


def _legacy(name: str, default: Any = None) -> Any:
    if name in _LEGACY_CACHE:
        return _LEGACY_CACHE[name]
    try:
        from docmate import legacy as L

        val = getattr(L, name)
        _LEGACY_CACHE[name] = val
        return val
    except Exception:
        return default


def _strip_page_markers(text: str) -> str:
    """Remove DocMate OCR page markers (===== Page X/Y =====)."""
    t = text or ""
    return re.sub(r"^\s*=+\s*Page\s+\d+\s*/\s*\d+\s*=+\s*$", "", t, flags=re.I | re.M).strip()


def _looks_like_booker_invoice(text: str) -> bool:
    if not text:
        return False
    up = str(text).upper()
    if ("INVOICE NUMBER" in up) and ("TOTAL ITEMS" in up) and ("RRP" in up) and ("POR" in up):
        return True
    if ("INVOICE NUMBER" in up) and ("INVOICE TOTAL" in up) and ("RATE" in up) and ("NETT" in up) and ("VAT" in up):
        return True
    return False


def _clean_booker_customer_name(name: str) -> str:
    s = str(name or "").strip()
    if not s:
        return ""
    s = re.sub(r"\s+P\s*/\s*O\s*(?:NUMBER|NO\.?|#)?\s*[:\-]?\s*[A-Z0-9\-\/]{3,}.*$", "", s, flags=re.I)
    s = re.sub(r"\s+PO\s*(?:NUMBER|NO\.?|#)?\s*[:\-]?\s*[A-Z0-9\-\/]{3,}.*$", "", s, flags=re.I)
    m = re.search(r"\s+PO\s+[0-9]{3,}\s*$", s, flags=re.I)
    if m:
        s = s[: m.start()].strip()
    return s.strip(" -:|\t")


def _find_booker_vat_amount(text: str, fallback_find_amount) -> float:
    """Extract Booker footer VAT without confusing it with footer net/goods blocks."""
    try:
        ms = re.findall(
            r"(?im)^\s*[A-Z]\s*:\s*.*?\bVAT\b\s*([0-9][0-9,]*\.[0-9]{2})\b",
            text or "",
        )
        if ms:
            return float(ms[-1].replace(",", ""))
    except Exception:
        pass
    try:
        msb = re.findall(
            r"(?im)^\s*[A-Z]\s*:\s*[0-9][0-9,]*\.[0-9]{2}\s+"
            r"[0-9][0-9,]*\.[0-9]{2}\s+"
            r"[0-9][0-9,]*\.[0-9]{2}\s+"
            r"[0-9][0-9,]*\.[0-9]{2}\s+"
            r"([0-9][0-9,]*\.[0-9]{2})\b",
            text or "",
        )
        if msb:
            return float(msb[-1].replace(",", ""))
    except Exception:
        pass
    try:
        lines = (text or "").splitlines()
        idxs = [i for i, ln in enumerate(lines) if re.search(r"\bINVOICE\s+TOTAL\b|\bTOTALS?:\b", ln, flags=re.I)]
        if idxs:
            i0 = max(0, idxs[-1] - 4)
            i1 = min(len(lines), idxs[-1] + 4)
            block = "\n".join(lines[i0:i1])
            ms2 = re.findall(r"\bVAT\b[^0-9]{0,10}([0-9][0-9,]*\.[0-9]{2})", block, flags=re.I)
            if ms2:
                return float(ms2[-1].replace(",", ""))
    except Exception:
        pass
    return fallback_find_amount(r"VAT")


def _find_booker_footer_totals(text: str) -> Tuple[float, float]:
    """Extract Booker footer net/vat from the final TOTALS block."""
    src = str(text or "")
    try:
        m = re.search(
            r"(?is)TOTALS?\s*:\s*GOODS\s*VAT\s*([0-9][0-9,]*\.[0-9]{2})\s*([0-9][0-9,]*\.[0-9]{2})",
            src,
        )
        if m:
            return float(m.group(1).replace(",", "")), float(m.group(2).replace(",", ""))
    except Exception:
        pass
    try:
        lines = [str(ln or "").strip() for ln in src.splitlines()]
        for idx, ln in enumerate(lines):
            if not re.search(r"\bTOTALS?\b", ln, flags=re.I):
                continue
            window = lines[idx : min(len(lines), idx + 8)]
            nums = [float(x.replace(",", "")) for x in window if re.fullmatch(r"[0-9][0-9,]*\.[0-9]{2}", x)]
            if len(nums) >= 2:
                return nums[0], nums[1]
    except Exception:
        pass
    return 0.0, 0.0


def _parfetts_vat_breakdown_from_items(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    fn = _legacy("_parfetts_vat_breakdown_from_items")
    if callable(fn):
        try:
            return fn(items) or {}
        except Exception:
            pass
    rows_map: Dict[str, Dict[str, float]] = {}
    for it in items or []:
        code = str((it or {}).get("vatCode") or "UNK").strip().upper() or "UNK"
        net = float(_safe_float((it or {}).get("lineNet")))
        vat = float(_safe_float((it or {}).get("vatAmount")))
        gross = float(_safe_float((it or {}).get("lineGross")))
        agg = rows_map.setdefault(code, {"netAmount": 0.0, "vatAmount": 0.0, "grossAmount": 0.0})
        agg["netAmount"] += net
        agg["vatAmount"] += vat
        agg["grossAmount"] += gross
    rows = [{"vatCode": c, **v} for c, v in rows_map.items()]
    return {
        "rows": rows,
        "totalNet": round(sum(r["netAmount"] for r in rows), 2),
        "totalVat": round(sum(r["vatAmount"] for r in rows), 2),
        "totalGross": round(sum(r["grossAmount"] for r in rows), 2),
    }

class LocalInvoiceParser:
    @staticmethod
    def parse(doc_text: str, doc_type: str, settings: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        txt = _strip_page_markers(doc_text or "")
        up = txt.upper()

        header: Dict[str, Any] = {
            "supplierName": "",
            "customerName": "",
            "companyName": "N/A",
            "tradingName": "",
            "customerNo": "",
            "customerPONumber": "",
            "invoiceNumber": "",
            "invoiceDate": "",
            "currency": "GBP",
            "totalNet": 0.0,
            "totalVat": 0.0,
            "totalGross": 0.0,
        }
        items: List[Dict[str, Any]] = []

        # Supplier hints (Local OCR): improve supplierName stability for template matching.
        if "DHAMECHA" in up or "DHAMECHA FOODS" in up:
            header["supplierName"] = "Dhamecha Foods Limited"


        # Dhamecha purchase invoice: parse lines + totals reliably from text layer / OCR tokens
        if doc_type == "Purchase Invoice" and ("DHAMECHA" in up or "DHAMECHA FOODS" in up):
            try:
                return validate_invoice_result(_dhamecha_parse_purchase_invoice(txt))
            except Exception:
                pass


        # Parfetts purchase invoice fast path (multi-page)
        if doc_type == "Purchase Invoice" and "PARFETTS" in up:
            hdr = _parfetts_extract_header(txt) or {}
            if isinstance(hdr, dict):
                for k, v in hdr.items():
                    header[k] = v
            items = _parse_parfetts_purchase_invoice(txt)
            # If header totals missing, infer from items
            try:
                if float(header.get("totalNet") or 0.0) <= 0.0 and items:
                    header["totalNet"] = round(sum(_safe_float(it.get("lineNet")) for it in items), 2)
                if float(header.get("totalVat") or 0.0) <= 0.0 and items:
                    header["totalVat"] = round(sum(_safe_float(it.get("vatAmount")) for it in items), 2)
                if float(header.get("totalGross") or 0.0) <= 0.0 and float(header.get("totalNet") or 0.0) > 0.0:
                    header["totalGross"] = round(float(header["totalNet"]) + float(header.get("totalVat") or 0.0), 2)
            except Exception:
                pass

            # VAT Breakdown: if invoice VAT box (A/Z) not detected, calculate from line items (vatCode + vatRate).
            # This is useful for audit and avoids rounding drift when VAT is calculated at invoice level.
            try:
                if not header.get("vatBreakdown") and items:
                    calc = _parfetts_vat_breakdown_from_items(items) or {}
                    rows = calc.get("rows") or []
                    if rows:
                        try:
                            for r in rows:
                                if isinstance(r, dict) and "_source" not in r:
                                    r["_source"] = "calculated"
                        except Exception:
                            pass
                        header["vatBreakdown"] = rows
                        dc = _safe_float(header.get("deliveryCharge"))
                        # Reconcile totals when missing or materially different
                        if float(header.get("totalNet") or 0.0) <= 0.0 or abs(_safe_float(header.get("totalNet")) - _safe_float(calc.get("totalNet"))) > 0.05:
                            header["totalNet"] = _safe_float(calc.get("totalNet"))
                        if float(header.get("totalVat") or 0.0) <= 0.0 or abs(_safe_float(header.get("totalVat")) - _safe_float(calc.get("totalVat"))) > 0.05:
                            header["totalVat"] = _safe_float(calc.get("totalVat"))
                        new_gross = round(_safe_float(calc.get("totalNet")) + _safe_float(calc.get("totalVat")) + dc, 2)
                        if float(header.get("totalGross") or 0.0) <= 0.0 or abs(_safe_float(header.get("totalGross")) - new_gross) > 0.05:
                            header["totalGross"] = float(new_gross)
            except Exception:
                pass

            return validate_invoice_result({"header": header, "items": items})


        # DWG purchase invoice fast path (local/auto)
        if doc_type == "Purchase Invoice" and ("DRINKS WHOLESALE GROUP" in up or "DRINKS WHOLESALE" in up or "DWG" in up):
            hdr = _dwg_extract_header(txt) or {}
            if isinstance(hdr, dict):
                for k, v in hdr.items():
                    header[k] = v
            items = _parse_dwg_purchase_invoice(txt)

            # If totals missing, infer from items (best-effort)
            try:
                if float(header.get("totalNet") or 0.0) <= 0.0 and items:
                    header["totalNet"] = round(sum(_safe_float(it.get("lineNet")) for it in items), 2)
                if float(header.get("totalVat") or 0.0) <= 0.0 and items:
                    header["totalVat"] = round(sum(_safe_float(it.get("vatAmount")) for it in items), 2)
                if float(header.get("totalGross") or 0.0) <= 0.0 and items:
                    header["totalGross"] = round(sum(_safe_float(it.get("lineGross")) for it in items), 2)
            except Exception:
                pass

            return validate_invoice_result({"header": header, "items": items})

# Booker purchase invoice fast path (robust)
        if doc_type == "Purchase Invoice" and ("BOOKER" in up or _looks_like_booker_invoice(txt)):
            header["supplierName"] = "Booker Ltd"

            # Customer name (best-effort)
            for ln in txt.splitlines():
                if ln.upper().startswith("CUSTOMER "):
                    m = re.match(r"^CUSTOMER\s+\d+\s+(.*)$", ln.strip(), flags=re.IGNORECASE)
                    if m and m.group(1).strip():
                        nm = _clean_booker_customer_name(m.group(1).strip())
                        if nm:
                            header["customerName"] = nm.title() if nm.isupper() else nm
                    break

            # Invoice number (supports: Invoice No / Invoice Number)
            m_inv = re.search(r"(INVOICE\s*(?:NO|NUMBER)\s*[:#\-]?\s*)([0-9]{5,})", txt, flags=re.I)
            if not m_inv:
                m_inv = re.search(r"\bINVOICE\b[^\n\r]{0,40}\b([0-9]{5,})\b", txt, flags=re.I)
            if m_inv:
                header["invoiceNumber"] = (m_inv.group(2) if m_inv.lastindex and m_inv.lastindex >= 2 else m_inv.group(1)).strip()

            # Invoice date (Booker: supports dd/mm/yy, dd/mm/yyyy, yyyy-mm-dd, and spaced slashes)
            m_date = re.search(r"\b([0-9]{4}-[0-9]{2}-[0-9]{2})\b", txt)
            if m_date:
                header["invoiceDate"] = m_date.group(1)
            else:
                # Prefer 'DATE' labelled token first (e.g., 'DATE 16/11/25' or 'DATE 16 / 11 / 25')
                m_lbl = re.search(r"\bDATE\b[^0-9]{0,20}([0-9]{1,2}\s*/\s*[0-9]{1,2}\s*/\s*[0-9]{2,4})", txt, flags=re.I)
                if not m_lbl:
                    m_lbl = re.search(r"DATE\s*[:\-]?\s*([0-9]{1,2}\s*/\s*[0-9]{1,2}\s*/\s*[0-9]{2,4})", txt, flags=re.I)
                date_str = m_lbl.group(1) if m_lbl else None

                if not date_str:
                    # Fallback: any dd/mm/yy or dd/mm/yyyy with optional spaces
                    m_any = re.search(r"\b([0-9]{1,2}\s*/\s*[0-9]{1,2}\s*/\s*[0-9]{2,4})\b", txt)
                    date_str = m_any.group(1) if m_any else None

                if date_str:
                    ds = re.sub(r"\s+", "", date_str)  # remove spaces around separators
                    m_dmy4 = re.match(r"^([0-9]{1,2})/([0-9]{1,2})/([0-9]{4})$", ds)
                    m_dmy2 = re.match(r"^([0-9]{1,2})/([0-9]{1,2})/([0-9]{2})$", ds) if not m_dmy4 else None
                    if m_dmy4:
                        dd, mm, yyyy = m_dmy4.group(1).zfill(2), m_dmy4.group(2).zfill(2), m_dmy4.group(3)
                        header["invoiceDate"] = f"{yyyy}-{mm}-{dd}"
                    elif m_dmy2:
                        dd, mm, yy = m_dmy2.group(1).zfill(2), m_dmy2.group(2).zfill(2), m_dmy2.group(3)
                        yyyy = "20" + yy if int(yy) <= 50 else "19" + yy
                        header["invoiceDate"] = f"{yyyy}-{mm}-{dd}"

            # Totals (Booker): prefer INVOICE TOTAL + VAT, else sum SUB-TOTAL GOODS, else sum items
            txt_fix = re.sub(r"(\d)\s*\.\s*(\d{2})", r"\1.\2", txt)

            def _find_amount(label: str) -> float:
                ms = re.findall(label + r"[^0-9]{0,40}([0-9][0-9,]*\.[0-9]{2})", txt_fix, flags=re.I)
                if ms:
                    try:
                        return float(ms[-1].replace(",", ""))
                    except Exception:
                        return 0.0
                return 0.0

            # Booker often shows GOODS per department; sum SUB-TOTAL goods where possible
            sub_goods = 0.0
            try:
                subs = re.findall(r"SUB-TOTAL[^\n]*?GOODS\s*:\s*([0-9][0-9,]*\.[0-9]{2})", txt_fix, flags=re.I)
                sub_goods = sum(float(x.replace(",", "")) for x in subs) if subs else 0.0
            except Exception:
                sub_goods = 0.0

            footer_net, footer_vat = _find_booker_footer_totals(txt_fix)
            vat = footer_vat or _find_booker_vat_amount(txt_fix, _find_amount)
            gross = _find_amount(r"INVOICE\s+TOTAL")
            # Fallback label variants
            if gross <= 0.0:
                gross = _find_amount(r"TOTAL\s+INVOICE")

            items = BookerCombinedRowParser().parse(txt_fix)
            # Fallback: some scanned Booker PDFs split the table into separate blocks (CODE/DESC/PACK, then QTY/PRICE, then VALUE/VAT/RRP/POR).
            # If the main combined-row parser returns no items (or fewer than TOTAL ITEMS), use the split-layout parser and merge by index.
            try:
                m_total2 = re.search(r"TOTAL\s+ITEMS\s*:\s*(\d+)", txt_fix, flags=re.I)
                expected_total2 = int(m_total2.group(1)) if m_total2 else 0
            except Exception:
                expected_total2 = 0

            if (not items) or (expected_total2 > 0 and len(items) < expected_total2):
                try:
                    split_items = BookerCombinedRowParser().parse_split_layout(txt_fix)
                except Exception:
                    split_items = []
                if split_items:
                    items = split_items
            if not items:
                try:
                    stacked_items = BookerCombinedRowParser().parse_stacked_layout(txt_fix)
                except Exception:
                    stacked_items = []
                if stacked_items:
                    items = stacked_items
            if not items:
                try:
                    stacked_ocr_items = BookerCombinedRowParser().parse_stacked_ocr_text(txt_fix)
                except Exception:
                    stacked_ocr_items = []
                if stacked_ocr_items:
                    items = stacked_ocr_items
            if not items:
                try:
                    row_text = BookerCombinedRowParser().build_rowified_text(txt_fix)
                except Exception:
                    row_text = ""
                if row_text:
                    try:
                        row_items = BookerCombinedRowParser().parse(row_text)
                    except Exception:
                        row_items = []
                    if row_items:
                        items = row_items
            items_net = round(sum((x.get("lineNet") or 0.0) for x in items), 2) if items else 0.0
            items_vat = round(sum((x.get("vatAmount") or 0.0) for x in items), 2) if items else 0.0

            # Best net estimate: gross - vat, else subtotal goods sum, else items sum, else any GOODS match
            net = 0.0
            if footer_net > 0.0:
                net = float(footer_net)
            elif gross > 0.0 and vat >= 0.0:
                net = round(float(gross) - float(vat), 2)
            if net <= 0.0 and sub_goods > 0.0:
                net = round(float(sub_goods), 2)
            if net <= 0.0 and items_net > 0.0:
                net = float(items_net)
            if net <= 0.0:
                net = _find_amount(r"GOODS")

            # If we accidentally picked up a section subtotal as net, reconcile against gross-vat.
            if gross > 0.0 and vat >= 0.0:
                implied_net = round(float(gross) - float(vat), 2)
                if implied_net > 0.0 and (net <= 0.0 or abs(float(net) - implied_net) > 0.75):
                    net = implied_net

            header["totalNet"] = float(net or 0.0)
            header["totalVat"] = float(vat or items_vat or 0.0)
            header["totalGross"] = float(gross or 0.0)
            if header["totalGross"] <= 0.0 and header["totalNet"] > 0.0:
                header["totalGross"] = round(header["totalNet"] + header["totalVat"], 2)

            # If items exist but header net looks like a single department subtotal, reconcile to invoice gross/vat
            if items:
                if abs(items_net - float(header.get("totalNet") or 0.0)) > 0.25 and header.get("totalGross", 0.0) > 0.0:
                    # Prefer invoice totals (gross/vat) as source of truth
                    if header.get("totalVat", 0.0) >= 0.0:
                        header["totalNet"] = round(float(header["totalGross"]) - float(header["totalVat"]), 2)
                # If header totals still missing, use items sums
                if float(header.get("totalNet") or 0.0) <= 0.0 and items_net > 0.0:
                    header["totalNet"] = float(items_net)
                if float(header.get("totalVat") or 0.0) <= 0.0 and items_vat > 0.0:
                    header["totalVat"] = float(items_vat)
                if float(header.get("totalGross") or 0.0) <= 0.0 and float(header.get("totalNet") or 0.0) > 0.0:
                    header["totalGross"] = round(float(header["totalNet"]) + float(header["totalVat"] or 0.0), 2)

            return validate_invoice_result({"header": header, "items": items})
# Generic header-only parsing
        # invoice no
        m_inv2 = re.search(r"\b(INV[- ]?\d{5,}|INVOICE\s*(?:NO|NUMBER)\s*[:\-]?\s*[A-Z0-9\-]{4,})\b", up)
        if m_inv2:
            s = m_inv2.group(1)
            s = re.sub(r"INVOICE\s*(?:NO|NUMBER)\s*[:\-]?\s*", "", s, flags=re.I)
            header["invoiceNumber"] = s.replace(" ", "").strip()

        m_date2 = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", txt)
        if m_date2:
            header["invoiceDate"] = m_date2.group(1)

        # totals
        m_net2 = re.search(r"\bTOTAL\s+NET\b\s*[:\-]?\s*£?\s*([0-9][0-9,]*\.?[0-9]{0,2})", up)
        m_vat2 = re.search(r"\bTOTAL\s+VAT\b\s*[:\-]?\s*£?\s*([0-9][0-9,]*\.?[0-9]{0,2})", up)
        m_gross2 = re.search(r"\bTOTAL\s+(?:GROSS|AMOUNT\s+DUE)\b\s*[:\-]?\s*£?\s*([0-9][0-9,]*\.?[0-9]{0,2})", up)
        if m_net2:
            header["totalNet"] = _safe_float(m_net2.group(1))
        if m_vat2:
            header["totalVat"] = _safe_float(m_vat2.group(1))
        if m_gross2:
            header["totalGross"] = _safe_float(m_gross2.group(1))

        # supplier/customer names (best-effort)
        if doc_type in ("Purchase Invoice", "Service Invoice"):
            # supplier is whoever issued
            # use first non-empty line as placeholder
            lines = [ln.strip() for ln in txt.splitlines() if ln.strip()]
            if lines:
                header["supplierName"] = lines[0][:80]
        else:
            # customer template matching will use customerName; keep placeholder
            m_c = re.search(r"\bCUSTOMER\b\s*[:\-]?\s*(.+)", txt, flags=re.I)
            if m_c:
                header["customerName"] = m_c.group(1).strip()[:80]

        # AI fallback: supplier not recognised by keyword detection — try Claude
        try:
            _cfg = settings if isinstance(settings, dict) else _load_settings()
            _claude_key = _get_claude_api_key(_cfg)
            if (_claude_key or "").strip():
                _claude_model = str(_cfg.get("claude_model") or "claude-sonnet-4-6")
                _ai_result = ClaudeAIFallbackParser(_claude_key, _claude_model).extract(txt, doc_type)
                if _ai_result and isinstance(_ai_result, dict):
                    _log_event("Claude AI fallback used for unrecognised supplier/document type")
                    return validate_invoice_result(_ai_result)
        except Exception as _ai_err:
            _log_event(f"Claude AI fallback error: {_ai_err}")

        return validate_invoice_result({"header": header, "items": items})


# -------------------------
# Cloud (optional) — kept for later expansion
# -------------------------

class CloudGeminiExtractor:
    def __init__(self, api_key: str, model_name: str = "gemini-2.0-flash"):
        self.api_key = api_key
        self.model_name = model_name

    def is_available(self) -> bool:
        return bool((self.api_key or "").strip())

    def extract_purchase_invoice(self, file_bytes: bytes, mime_type: str, file_name: str, doc_text: str = "") -> Dict[str, Any]:
        """Best-effort call. If libraries are missing or API changes, return empty dict but log error."""
        _strip_models_prefix = _legacy("_strip_models_prefix", lambda s: str(s or "").replace("models/", "").strip())
        _resolve_gemini_model_google_genai = _legacy("_resolve_gemini_model_google_genai", lambda client, preferred: _strip_models_prefix(preferred))
        try:
            # Prefer google-genai (new) if installed
            try:
                from google import genai  # type: ignore
                from google.genai import types  # type: ignore

                try:
                    client = genai.Client(api_key=self.api_key, http_options=types.HttpOptions(api_version='v1'))
                except Exception:
                    client = genai.Client(api_key=self.api_key)
                resolved_model = _resolve_gemini_model_google_genai(client, self.model_name)
                if resolved_model and resolved_model != _strip_models_prefix(self.model_name):
                    _log_event(f"{file_name} CloudGemini model resolved: {self.model_name} -> {resolved_model}")
                parts = []
                # text hint
                if doc_text:
                    parts.append(types.Part(text=f"Text layer (may be incomplete):\n{doc_text[:4000]}") )
                # file part
                if mime_type == "application/pdf":
                    parts.append(types.Part.from_bytes(data=file_bytes, mime_type="application/pdf"))
                else:
                    parts.append(types.Part.from_bytes(data=file_bytes, mime_type=mime_type))

                prompt = (
                    "Return STRICT JSON only. Schema: {header:{supplierName,customerName,invoiceNumber,invoiceDate,currency,deliveryCharge,totalNet,totalVat,totalGross},"
                    "items:[{lineNo,code,description,casePack,unitSize,qty,unitPrice,lineNet,vatRate,vatAmount,lineGross}]}."
                    "Include invoice-level charge rows such as delivery, freight, transport, shipping, carriage, handling, service charge, "
                    "or French labels like Frais de port HT / Livraison / Transport when they are printed with an amount and contribute to invoice totals."
                )
                # Build contents in the most compatible way (text + file parts)
                contents = parts + [prompt]
                # Request JSON via prompt instructions (avoid SDK-specific response_mime_type fields)

                cfg_primary = {
                    "temperature": 0,
                }
                cfg_alt = {
                    "temperature": 0,
                }
                cfg_min = {
                    "temperature": 0,
                }

                # Try config forms in order for compatibility across SDK versions
                resp = None
                last_err: Optional[Exception] = None
                for cfg_try in (cfg_primary, cfg_alt, cfg_min):
                    try:
                        resp = client.models.generate_content(
                            model=resolved_model,
                            contents=contents,
                            config=cfg_try,
                        )
                        break
                    except Exception as e:
                        last_err = e
                        resp = None

                if resp is None:
                    # Fallback: older installs used different parameter names
                    try:
                        resp = client.models.generate_content(
                            model=resolved_model,
                            contents=contents,
                            generation_config={"temperature": 0},
                        )
                    except Exception as e:
                        raise last_err or e
                parsed = getattr(resp, "parsed", None)
                if isinstance(parsed, dict):
                    # Attach raw response text where available (useful for Template Test / Preview debug)
                    try:
                        _rt = getattr(resp, "text", "") or ""
                    except Exception:
                        _rt = ""
                    parsed = dict(parsed)
                    if _rt and not parsed.get("raw_text"):
                        parsed["raw_text"] = _rt
                    if _rt and not parsed.get("debug_text"):
                        parsed["debug_text"] = _rt
                    return parsed

                text = getattr(resp, "text", "") or ""
                out = _parse_json_from_model(text)
                if isinstance(out, dict):
                    if text and not out.get("raw_text"):
                        out["raw_text"] = text
                    if text and not out.get("debug_text"):
                        out["debug_text"] = text
                return out
            except Exception as e_new:
                _log_event(f"CloudGeminiExtractor unavailable (google-genai): {e_new}", file_name=file_name)

            # Fallback: google-generativeai (older)
            try:
                import google.generativeai as genai  # type: ignore

                genai.configure(api_key=self.api_key)
                model = genai.GenerativeModel(self.model_name)
                prompt = (
                    "Return STRICT JSON only. Schema: {header:{supplierName,customerName,invoiceNumber,invoiceDate,currency,deliveryCharge,totalNet,totalVat,totalGross},"
                    "items:[{lineNo,code,description,casePack,unitSize,qty,unitPrice,lineNet,vatRate,vatAmount,lineGross}]}."
                    "Include invoice-level charge rows such as delivery, freight, transport, shipping, carriage, handling, service charge, "
                    "or French labels like Frais de port HT / Livraison / Transport when they are printed with an amount and contribute to invoice totals."
                )
                if mime_type == "application/pdf":
                    resp = model.generate_content([prompt, {"mime_type": "application/pdf", "data": file_bytes}])
                else:
                    resp = model.generate_content([prompt, {"mime_type": mime_type, "data": file_bytes}])
                text = getattr(resp, "text", "") or ""
                return _parse_json_from_model(text)
            except Exception as e_old:
                _log_event(f"CloudGeminiExtractor unavailable (google-generativeai): {e_old}", file_name=file_name)

        except Exception as e:
            _log_event(f"CloudGeminiExtractor error: {e}", file_name=file_name)
        return {"header": {}, "items": []}

def _parse_json_from_model(raw: str) -> Dict[str, Any]:
    if not raw:
        return {"header": {}, "items": []}
    s = raw.strip()
    # Remove markdown fences if present
    s = re.sub(r"^```(?:json)?", "", s, flags=re.I).strip()
    s = re.sub(r"```$", "", s).strip()
    # Try direct
    try:
        d = json.loads(s)
        if isinstance(d, dict):
            return d
    except Exception:
        pass
    # Try to extract first {...} block
    try:
        m = re.search(r"\{.*\}", s, flags=re.S)
        if m:
            d = json.loads(m.group(0))
            if isinstance(d, dict):
                return d
    except Exception:
        pass
    return {"header": {}, "items": []}


# -------------------------
# Register save / load
# -------------------------
