from __future__ import annotations
from typing import Any, Dict, List, Optional

def _template_keywords(name: str, extra: Optional[List[str]] = None) -> List[str]:
    out = []
    if name:
        out.append(name.strip())
        out.append(re.sub(r"\s+", " ", name.strip().upper()))
        # add tokenised bits
        toks = re.findall(r"[A-Z0-9]+", name.upper())
        out.extend(list(dict.fromkeys(toks))[:8])
    if extra:
        for e in extra:
            e = (e or "").strip()
            if e:
                out.append(e)
    # de-dup, keep order
    dedup = []
    for k in out:
        k2 = k.strip()
        if not k2:
            continue
        if k2.lower() in seen:
            continue
        seen.add(k2.lower())
        dedup.append(k2)
    return dedup
def _template_score(doc_text: str, keywords: List[str]) -> int:
    """Score a template match against document text.

    We deliberately:
    - ignore very short keywords (<4 chars) to reduce false matches
    - ignore common invoice stop-words
    - keep simple 'contains' matching for speed (AUTO mode)

    This makes templates resilient when invoice numbers change (typical case),
    while still matching reliably on supplier/customer identifiers.
    """
    txt = (doc_text or '').upper()
    score = 0
    for kw in (keywords or []):
        if not kw:
            continue
        k = str(kw).strip().upper()
        if len(k) < 4:
            continue
        if k in {'LTD.', 'LIMITED.'}:
            k = k.replace('.', '')
        if k in {
            'LTD', 'LIMITED', 'INVOICE', 'VAT', 'TOTAL', 'NET', 'GROSS', 'AMOUNT',
            'DATE', 'NUMBER', 'SUPPLIER', 'CUSTOMER', 'TAX', 'GOODS', 'DISCOUNT', 'DELIVERY'
        }:
            continue
        if k and (k in txt):
            score += 1
    return score
def _template_signature_supplier(payload: Dict[str, Any]) -> str:
    """Stable signature for supplier template payload to detect no-op saves."""
    try:
        supplier_name = (payload.get("supplier_name") or "").strip()
        parser_type = (payload.get("parser_type") or "GENERIC").strip().upper()
        vat = (payload.get("vat_number") or "").strip()
        awrs = (payload.get("awrs_number") or "").strip()
        display_id = (payload.get("display_id") or "").strip()
        requires_cloud = 1 if int(payload.get("requires_cloud") or 0) == 1 else 0
        cfg = payload.get("config") or {}
        if not isinstance(cfg, dict):
            cfg = {}
        # normalise keywords
        kws = payload.get("match_keywords") or []
        if not isinstance(kws, list):
            kws = []
        kws_n = [str(x).strip().upper() for x in kws if str(x).strip()]
        kws_n = sorted(list(dict.fromkeys(kws_n)))
        norm = {
            "supplier_name": supplier_name.strip().upper(),
            "parser_type": parser_type,
            "vat_number": vat,
            "awrs_number": awrs,
            "display_id": display_id,
            "requires_cloud": requires_cloud,
            "match_keywords": kws_n,
            "config": cfg,
        }
        blob = json.dumps(norm, sort_keys=True, ensure_ascii=False)
        return _sha256_bytes(blob.encode("utf-8"))
    except Exception:
        return ""
def _diff_template_fields(old_sig_payload: Dict[str, Any], new_sig_payload: Dict[str, Any]) -> List[str]:
    diffs = []
    try:
        for k in ("vat_number", "awrs_number", "requires_cloud", "match_keywords", "config"):
            if old_sig_payload.get(k) != new_sig_payload.get(k):
                diffs.append(k)
    except Exception:
        pass
    return diffs
