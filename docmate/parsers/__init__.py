from __future__ import annotations
from typing import Callable, Dict, Any, Optional

from .booker import BookerCombinedRowParser, _booker_extract_customer_name, _booker_autofill_customer_name
from .dwg import _dwg_extract_header, _parse_dwg_purchase_invoice
from .dhamecha import _dhamecha_parse_vat_code_rate_table, _dhamecha_parse_purchase_invoice
from .parfetts import _extract_parfetts_vat_breakdown, _parfetts_extract_header, _parse_parfetts_purchase_invoice

# Simple registry. Keys are supplier/parser labels used by routing logic.
PARSER_REGISTRY: Dict[str, Dict[str, Any]] = {
    'BOOKER': {
        'row_parser_cls': BookerCombinedRowParser,
        'extract_customer_name': _booker_extract_customer_name,
        'autofill_customer_name': _booker_autofill_customer_name,
    },
    'DWG': {
        'extract_header': _dwg_extract_header,
        'parse_purchase': _parse_dwg_purchase_invoice,
    },
    'DHAMECHA': {
        'parse_vat_table': _dhamecha_parse_vat_code_rate_table,
        'parse_purchase': _dhamecha_parse_purchase_invoice,
    },
    'PARFETTS': {
        'extract_header': _parfetts_extract_header,
        'extract_vat': _extract_parfetts_vat_breakdown,
        'parse_purchase': _parse_parfetts_purchase_invoice,
    },
}

def get_parser_entry(key: str) -> Optional[Dict[str, Any]]:
    return PARSER_REGISTRY.get((key or '').strip().upper())
