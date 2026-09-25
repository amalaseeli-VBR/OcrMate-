from __future__ import annotations
from typing import Any, Dict, List


def validate_invoice_result(result: Dict[str, Any]) -> Dict[str, Any]:
    """Add _validation dict to any parse result. Never raises; always returns the result."""
    if not isinstance(result, dict):
        return result

    errors: List[str] = []
    warnings: List[str] = []

    header = result.get("header") or {}
    items = result.get("items") or []

    # Required header fields
    for field in ("invoiceNumber", "invoiceDate", "supplierName"):
        if not str(header.get(field) or "").strip():
            warnings.append(f"Missing header field: {field}")

    total_gross = _n(header.get("totalGross"))
    total_net = _n(header.get("totalNet"))
    total_vat = _n(header.get("totalVat"))

    if total_gross <= 0.0:
        warnings.append("totalGross is zero or not extracted")

    # Net + VAT must reconcile to Gross (within £0.03 rounding)
    if total_gross > 0.0:
        implied = round(total_net + total_vat, 2)
        if abs(implied - total_gross) > 0.03:
            errors.append(
                f"Totals mismatch: net {total_net:.2f} + vat {total_vat:.2f} = {implied:.2f} "
                f"but totalGross is {total_gross:.2f}"
            )

    # Line items sum should be close to header net (warn only — line items may be partial)
    if items and total_net > 0.0:
        items_net = round(sum(_n((it or {}).get("lineNet")) for it in items), 2)
        if abs(items_net - total_net) > 0.05:
            warnings.append(
                f"Line items net {items_net:.2f} differs from header totalNet {total_net:.2f}"
            )

    # Date format sanity (must be YYYY-MM-DD if present)
    inv_date = str(header.get("invoiceDate") or "")
    if inv_date and not _is_iso_date(inv_date):
        warnings.append(f"invoiceDate '{inv_date}' is not in YYYY-MM-DD format")

    status = "ERROR" if errors else "WARNING" if warnings else "OK"
    result["_validation"] = {"status": status, "errors": errors, "warnings": warnings}
    return result


def _n(val: Any) -> float:
    try:
        return float(val or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _is_iso_date(s: str) -> bool:
    import re
    return bool(re.match(r"^\d{4}-\d{2}-\d{2}$", s.strip()))
