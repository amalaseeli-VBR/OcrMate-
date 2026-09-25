from __future__ import annotations

import hashlib
import re
from typing import Any, Dict, List


COMMON_ANCHOR_STOPWORDS = {
    "INVOICE",
    "TOTAL",
    "VAT",
    "NET",
    "GROSS",
    "AMOUNT",
    "DATE",
    "NUMBER",
    "DESCRIPTION",
    "QTY",
    "QUANTITY",
    "PRICE",
    "VALUE",
    "PAGE",
}

KNOWN_TEMPLATE_CURRENCIES = (
    "EUR", "GBP", "USD", "CHF", "CAD", "AUD", "NZD",
    "SEK", "NOK", "DKK", "PLN", "CZK", "HUF", "RON",
    "BGN", "JPY", "CNY", "INR",
)


def normalise_template_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def normalise_supplier_key(supplier_name: str) -> str:
    text = normalise_template_text(supplier_name).upper()
    text = re.sub(r"[^A-Z0-9]+", " ", text)
    parts = [
        p
        for p in text.split()
        if p
        and p
        not in {
            "THE",
            "LTD",
            "LIMITED",
            "PLC",
            "LLP",
            "CO",
            "COMPANY",
            "UK",
        }
    ]
    return " ".join(parts[:8])


def normalise_template_currency(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    upper = normalise_template_text(raw).upper()
    replacements = {
        "EURO": "EUR",
        "EUROS": "EUR",
        "POUND": "GBP",
        "POUNDS": "GBP",
        "POUND STERLING": "GBP",
        "STERLING": "GBP",
        "US DOLLAR": "USD",
        "US DOLLARS": "USD",
        "DOLLAR": "USD",
        "DOLLARS": "USD",
    }
    if upper in replacements:
        return replacements[upper]
    symbol_map = {"€": "EUR", "£": "GBP"}
    for symbol, code in symbol_map.items():
        if symbol in raw:
            return code
    if upper in KNOWN_TEMPLATE_CURRENCIES:
        return upper
    match = re.search(r"\b(" + "|".join(KNOWN_TEMPLATE_CURRENCIES) + r")\b", upper)
    return match.group(1) if match else ""


def infer_template_currency(doc_text: str, header: Dict[str, Any] | None = None) -> str:
    header_currency = normalise_template_currency((header or {}).get("currency"))
    if header_currency:
        return header_currency
    text = str(doc_text or "")
    upper = text.upper()
    candidates: List[str] = []
    if "€" in text or re.search(r"\b(EUR|EURO|EUROS)\b", upper):
        candidates.append("EUR")
    if "£" in text or re.search(r"\b(GBP|POUND|POUNDS|STERLING)\b", upper):
        candidates.append("GBP")
    if re.search(r"\b(USD|US\s+DOLLARS?)\b", upper):
        candidates.append("USD")
    for code in KNOWN_TEMPLATE_CURRENCIES:
        if code in ("EUR", "GBP", "USD"):
            continue
        if re.search(rf"\b{code}\b", upper):
            candidates.append(code)
    return candidates[0] if candidates else ""


def infer_template_locale_profile(doc_text: str, currency: str = "") -> Dict[str, Any]:
    text = str(doc_text or "")
    upper = text.upper()
    profile: Dict[str, Any] = {}
    if currency:
        profile["currency"] = currency
        profile["default_currency"] = currency
    if re.search(r"\b(TVA|FACTURE|MONTANT\s+HT|TOTAL\s+TTC|NET\s+A\s+PAYER)\b", upper):
        profile["language"] = "fr"
        profile["decimal_separator"] = ","
    elif re.search(r"\b(RECHNUNG|MWST|UST|NETTO|BRUTTO|GESAMTBETRAG)\b", upper):
        profile["language"] = "de"
        profile["decimal_separator"] = ","
    elif re.search(r"\b(IVA|FACTURA|IMPONIBLE|TOTAL\s+FACTURA)\b", upper):
        profile["language"] = "es"
        profile["decimal_separator"] = ","
    elif currency == "EUR":
        profile["decimal_separator"] = "," if re.search(r"\d{1,3}(?:\.\d{3})+,\d{2}\b", text) else "."
    return profile


def supplier_cluster_id(supplier_name: str) -> str:
    key = normalise_supplier_key(supplier_name)
    digest = hashlib.sha1(key.encode("utf-8", errors="ignore")).hexdigest()[:10] if key else "unknown"
    prefix = re.sub(r"[^A-Z0-9]", "", key)[:8] or "SUPPLIER"
    return f"SUP-{prefix}-{digest}"


def supplier_aliases(supplier_name: str, extras: List[Any] | None = None) -> List[str]:
    values: List[str] = []
    base = normalise_template_text(supplier_name)
    if base:
        values.extend(
            [
                base,
                base.upper(),
                re.sub(r"\b(LTD|LIMITED|PLC|LLP)\b\.?", "", base, flags=re.I).strip(),
                normalise_supplier_key(base),
            ]
        )
    for value in extras or []:
        text = normalise_template_text(value)
        if text:
            values.append(text)
    out: List[str] = []
    seen = set()
    for value in values:
        text = normalise_template_text(value)
        key = text.casefold()
        if text and key not in seen:
            seen.add(key)
            out.append(text)
    return out


def infer_layout_variant(parser_type: str, doc_text: str, items: List[Dict[str, Any]] | None = None) -> str:
    parser = normalise_template_text(parser_type).upper() or "GENERIC"
    text = (doc_text or "").upper()
    item_count = len(items or [])
    if "CREDIT NOTE" in text or "CREDIT MEMO" in text:
        suffix = "CREDIT"
    elif "DELIVERY NOTE" in text:
        suffix = "DELIVERY"
    elif item_count <= 1 and any(k in text for k in ("SERVICE", "MONTHLY", "SUBSCRIPTION", "CHARGE")):
        suffix = "SERVICE"
    elif "SCANNED" in text or len(text) < 700:
        suffix = "SCAN"
    else:
        suffix = "INVOICE"
    return f"{parser}_{suffix}_V1"


def detected_model_fields(header: Dict[str, Any] | None, items: List[Dict[str, Any]] | None) -> Dict[str, List[str]]:
    header_fields = sorted(
        str(k)
        for k, v in (header or {}).items()
        if str(k).strip() and v not in (None, "", [], {})
    )
    line_keys = set()
    for item in items or []:
        if not isinstance(item, dict):
            continue
        for key, value in item.items():
            if str(key).strip() and value not in (None, "", [], {}):
                line_keys.add(str(key))
    return {
        "header_fields": header_fields,
        "line_fields": sorted(line_keys),
    }


def extract_layout_anchors(doc_text: str, supplier_name: str = "", limit: int = 20) -> List[str]:
    text = doc_text or ""
    supplier_tokens = set(normalise_supplier_key(supplier_name).split())
    anchors: List[str] = []
    seen = set()
    for raw in text.splitlines():
        line = normalise_template_text(raw)
        if not line:
            continue
        upper = line.upper()
        tokens = re.findall(r"[A-Z0-9]{3,}", upper)
        if not tokens:
            continue
        useful = [
            token
            for token in tokens
            if token not in COMMON_ANCHOR_STOPWORDS
            and token not in supplier_tokens
            and not token.isdigit()
        ]
        has_label = any(
            marker in upper
            for marker in (
                "ACCOUNT",
                "CUSTOMER",
                "ORDER",
                "DELIVERY",
                "INVOICE",
                "REFERENCE",
                "ITEM",
                "CODE",
                "PACK",
                "UNIT",
                "VAT",
                "TOTAL",
            )
        )
        if not useful and not has_label:
            continue
        anchor = line[:120]
        key = anchor.casefold()
        if key not in seen:
            seen.add(key)
            anchors.append(anchor)
        if len(anchors) >= limit:
            break
    return anchors


def build_supplier_template_config(
    *,
    supplier_name: str,
    parser_type: str,
    doc_text: str = "",
    header: Dict[str, Any] | None = None,
    items: List[Dict[str, Any]] | None = None,
    document_type: str = "Purchase Invoice",
    layout_variant: str = "",
    supplier_alias_values: List[Any] | None = None,
    base_config: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    config = dict(base_config or {})
    fields = detected_model_fields(header or {}, items or [])
    variant = normalise_template_text(layout_variant) or infer_layout_variant(parser_type, doc_text, items)
    currency = (
        infer_template_currency(doc_text, header)
        or normalise_template_currency(config.get("default_currency"))
        or normalise_template_currency((config.get("rules") or {}).get("currency") if isinstance(config.get("rules"), dict) else "")
        or normalise_template_currency(
            (config.get("trusted_header_values") or {}).get("currency")
            if isinstance(config.get("trusted_header_values"), dict)
            else ""
        )
        or normalise_template_currency(
            (config.get("locale_profile") or {}).get("currency")
            if isinstance(config.get("locale_profile"), dict)
            else ""
        )
    )
    if currency:
        config["default_currency"] = currency
        rules = config.get("rules") if isinstance(config.get("rules"), dict) else {}
        rules["currency"] = currency
        config["rules"] = rules
        trusted = config.get("trusted_header_values") if isinstance(config.get("trusted_header_values"), dict) else {}
        trusted["currency"] = currency
        config["trusted_header_values"] = trusted
        locale_profile = config.get("locale_profile") if isinstance(config.get("locale_profile"), dict) else {}
        locale_profile.update(infer_template_locale_profile(doc_text, currency))
        config["locale_profile"] = locale_profile
    config.update(
        {
            "supplier_cluster_id": supplier_cluster_id(supplier_name),
            "supplier_key": normalise_supplier_key(supplier_name),
            "supplier_aliases": supplier_aliases(supplier_name, supplier_alias_values),
            "document_type": normalise_template_text(document_type) or "Purchase Invoice",
            "layout_variant": variant,
            "layout_anchors": extract_layout_anchors(doc_text, supplier_name=supplier_name),
            "json_model": {
                "header_json_column": "raw_header_json",
                "line_json_column": "raw_line_json",
                **fields,
            },
        }
    )
    return config
