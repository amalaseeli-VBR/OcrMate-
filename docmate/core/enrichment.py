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



def _preview_enrich_items(items: List[Dict[str, Any]], vat_map: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Preview-only enrichment so the Template Test grid and VAT summary reconcile.

    - Applies vat_map (code -> rate) if vatCode present
    - Derives vatCode from vatRate if missing (best-effort, using inverse of vat_map)
    - Derives vatAmount and lineGross if missing
    """
    _sync_from_legacy()
    if not items:
        return items

    # Build inverse mapping: rate -> preferred code
    inverse: Dict[float, str] = {}
    if isinstance(vat_map, dict) and vat_map:
        # Preference order for UK suppliers
        pref = ["A", "B", "R", "Z", "S", "E"]
        # Normalise vat_map keys/values
        norm = {}
        for k, v in vat_map.items():
            try:
                kk = str(k).strip().upper()
                vv = float(v)
                norm[kk] = vv
            except Exception:
                continue
        for code in pref:
            if code in norm:
                inverse.setdefault(norm[code], code)
        # Add any remaining codes deterministically
        for code, rate in sorted(norm.items(), key=lambda x: x[0]):
            inverse.setdefault(rate, code)

        vat_map = norm  # use normalised map

    for it in items:
        try:
            code = (it.get("vatCode") or it.get("vat_code") or "")
            code = str(code).strip().upper() if code is not None else ""
            rate = _safe_float(it.get("vatRate") if "vatRate" in it else it.get("vat_rate"), default=0.0)
            net = _safe_float(it.get("lineNet") if "lineNet" in it else it.get("line_net"), default=0.0)

            # If we have a code but no/zero rate, apply template map
            if code and (rate == 0.0) and isinstance(vat_map, dict) and code in vat_map:
                rate = float(vat_map.get(code, 0.0))

            # If no code but rate present, derive best-effort code from inverse map
            if (not code) and rate and inverse:
                code = inverse.get(float(rate), "")

            it["vatCode"] = code if code else (it.get("vatCode") or None)
            it["vatRate"] = rate if rate else (it.get("vatRate") or 0.0)

            vat_amount_raw = it.get("vatAmount")
            vat_amount = None if vat_amount_raw is None else _safe_float(vat_amount_raw, default=0.0)
            if vat_amount_raw is None:
                vat_amount = round(net * rate / 100.0, 2) if (net and rate) else 0.0
            it["vatAmount"] = vat_amount

            gross_raw = it.get("lineGross")
            gross = None if gross_raw is None else _safe_float(gross_raw, default=0.0)
            if gross_raw is None:
                gross = round(net + vat_amount, 2)
            it["lineGross"] = gross

        except Exception:
            # Never break preview rendering because of one row
            continue

    return items

