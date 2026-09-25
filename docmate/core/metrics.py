from __future__ import annotations

import re
from typing import Any, Dict, List

from docmate.core.recalc import (
    _is_promo_line_item,
    _normalise_promo_quantities,
    _normalise_vat_rate,
    _safe_float,
)

def _sync_from_legacy() -> None:
    from docmate import legacy as L
    g = globals()
    for k in dir(L):
        if k.startswith("__"):
            continue
        # Keep local implementations (e.g., _calc_metrics) to avoid wrapper recursion.
        if k in g:
            continue
        g[k] = getattr(L, k)



def _effective_line_gross(it: dict) -> float:
    net = _safe_float(it.get("lineNet"))
    vat = _safe_float(it.get("vatAmount"))
    gross = _safe_float(it.get("lineGross"))
    if (gross == 0 and net != 0) or (abs(gross - net) < 0.01 and abs(vat) > 0.01):
        return round(net + vat, 2)
    return gross


def _effective_line_vat(it: dict) -> float:
    net = _safe_float(it.get("lineNet"))
    vat = _safe_float(it.get("vatAmount"))
    rate = _normalise_vat_rate(it.get("vatRate"))
    if vat == 0 and net != 0 and rate != 0:
        return round(net * (rate / 100.0), 2)
    return vat


def _booker_duplicate_key(item: Dict[str, Any]):
    code = str(item.get("code") or item.get("Code") or "").strip().upper()
    if not code or code == "PROMO":
        return None
    desc = re.sub(r"\s+", " ", str(item.get("description") or item.get("Description") or "").upper()).strip()
    desc = re.sub(r"\bM1\b", "ML", desc)
    return (
        code,
        desc,
        round(_safe_float(item.get("qty")), 4),
        round(_safe_float(item.get("totalUnits")), 4),
        round(_safe_float(item.get("lineNet")), 2),
        round(_effective_line_vat(item), 2),
        round(_effective_line_gross(item), 2),
    )


def _maybe_dedupe_booker_rows(header: Dict[str, Any], items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    supplier = str(header.get("supplierName") or header.get("Supplier") or "").upper()
    if "BOOKER" not in supplier:
        return items
    header_net = _safe_float(header.get("totalNet"))
    if abs(header_net) <= 0.005:
        return items

    deduped: List[Dict[str, Any]] = []
    seen = set()
    removed = 0
    for item in items:
        if _is_promo_line_item(item) or bool(item.get("isVoid")) or bool(item.get("_reconOnly")):
            deduped.append(item)
            continue
        key = _booker_duplicate_key(item)
        if key and key in seen:
            removed += 1
            continue
        if key:
            seen.add(key)
        deduped.append(item)

    if not removed:
        return items

    current_net = round(sum(_safe_float(i.get("lineNet")) for i in items), 2)
    deduped_net = round(sum(_safe_float(i.get("lineNet")) for i in deduped), 2)
    if abs(deduped_net - header_net) <= 0.05 and abs(deduped_net - header_net) < abs(current_net - header_net):
        return deduped
    return items


def _calc_metrics(header: Dict[str, Any], items: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Compute reconciliation + KPI metrics from extracted header and line items.

    Notes:
    - Reconciliation (Net/VAT/Gross) uses all lines, including voids.
    - KPI totals ignore lines marked isVoid=True (delivered items).
    - 'products_count' is aligned to delivered line count (as requested), not unique SKU count.
    """
    net_header = _safe_float(header.get("totalNet"))
    vat_header = _safe_float(header.get("totalVat"))
    gross_header = _safe_float(header.get("totalGross"))

    items_for_recon = _normalise_promo_quantities([dict(i) for i in (items or []) if isinstance(i, dict)])
    # Normalise VAT rate: some cloud models return VAT rate as a fraction (e.g. 0.2 for 20%).
    # If detected, convert to percent and recompute vatAmount/lineGross so reconciliation stays correct.
    for _it in items_for_recon:
        vr = _safe_float(_it.get("vatRate"))
        ln = _safe_float(_it.get("lineNet"))
        if vr is not None and 0 < vr <= 1.0:
            vr_pct = vr * 100.0
            _it["vatRate"] = vr_pct
            if ln is not None:
                _it["vatAmount"] = round(ln * vr, 2)  # ln * 0.2 = VAT
                _it["lineGross"] = round(ln + _safe_float(_it.get("vatAmount")), 2)
        elif vr is not None and vr > 1.0:
            expected_vat = round(ln * (vr / 100.0), 2) if ln is not None else 0.0
            vat_amount = _safe_float(_it.get("vatAmount"))
            gross_amount = _safe_float(_it.get("lineGross"))
            vat_missing = _it.get("vatAmount") in (None, "", 0, 0.0)
            vat_implausible = (
                abs(vat_amount - expected_vat) > max(0.05, abs(expected_vat) * 0.10)
                and abs(vat_amount) > max(abs(ln) * 0.50, 1.0)
            )
            if ln is not None and (vat_missing or vat_implausible):
                _it["vatAmount"] = expected_vat
                vat_amount = expected_vat
            expected_gross = round(ln + vat_amount, 2) if ln is not None else gross_amount
            if ln is not None and (
                _it.get("lineGross") in (None, "", 0, 0.0)
                or abs(gross_amount - expected_gross) > max(0.05, abs(expected_gross) * 0.10)
            ):
                _it["lineGross"] = expected_gross


    items_for_recon = _maybe_dedupe_booker_rows(header, items_for_recon)

    net_items = sum(_safe_float(i.get("lineNet")) for i in items_for_recon)
    vat_items = sum(_effective_line_vat(i) for i in items_for_recon)
    gross_items = sum(_effective_line_gross(i) for i in items_for_recon)

    items_for_metrics = [
        i for i in items_for_recon
        if not bool(i.get("isVoid")) and not bool(i.get("_reconOnly")) and not _is_promo_line_item(i)
    ]
    total_lines = len(items_for_metrics)
    total_case_qty = sum(_safe_float(i.get("qty")) for i in items_for_metrics)

    # 'No. of Products' = delivered line count (matches Booker expectation)
    products_count = total_lines

    # Optional unique count (kept for later analytics / Cloud Reporting)
    def _product_key(it: Dict[str, Any]):
        for k in ("code","itemCode","productCode","sku","barcode","item_no","itemNo","plu","ean"):
            v = it.get(k)
            if v is not None and str(v).strip() != "":
                return ("code", str(v).strip())
        for k in ("description","desc","itemDescription","label","name","product","item"):
            v = it.get(k)
            if v is not None and str(v).strip() != "":
                return ("desc", str(v).strip().lower())
        return None

    seen = set()
    for it in items_for_metrics:
        k = _product_key(it)
        if k:
            seen.add(k)
    unique_products_count = len(seen)

    # Total stock units: prefer totalUnits, else qty * casePack (if numeric)
    units = 0.0
    for it in items_for_metrics:
        tu = it.get("totalUnits")
        if tu is not None and str(tu).strip() != "":
            units += _safe_float(tu)
            continue
        cp = str(it.get("casePack") or "").strip()
        cp_n = _safe_float(cp)
        if cp_n > 0:
            units += _safe_float(it.get("qty")) * cp_n

    # Average POR (%)
    por_vals = []
    for it in items_for_metrics:
        p = it.get("por")
        if p is None:
            p = it.get("POR")
        if p is None:
            continue
        try:
            ps = str(p).strip().replace("%", "")
            pv = float(ps)
            if pv > 0:
                por_vals.append(pv)
        except Exception:
            continue
    avg_por = (sum(por_vals) / len(por_vals)) if por_vals else 0.0

    return {
        # canonical keys used by dashboard reconciliation UI
        "hdr_net": net_header,
        "hdr_vat": vat_header,
        "hdr_gross": gross_header,
        "sum_net": net_items,
        "sum_vat": vat_items,
        "sum_gross": gross_items,
        "diff_net": round(net_items - net_header, 2),
        "diff_vat": round(vat_items - vat_header, 2),
        "diff_gross": round(gross_items - gross_header, 2),

        # legacy keys (keep for backward compatibility)
        "net_header": net_header,
        "vat_header": vat_header,
        "gross_header": gross_header,
        "net_items": net_items,
        "vat_items": vat_items,
        "gross_items": gross_items,

        "total_lines": total_lines,
        "total_case_qty": total_case_qty,
        "total_units": round(units, 2),
        "products_count": int(products_count or 0),
        "unique_products_count": int(unique_products_count or 0),
        "avg_por": round(avg_por, 2) if avg_por else 0.0,
    }
