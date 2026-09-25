from __future__ import annotations
import base64
import io
import json
import re
from typing import Any, Dict, List, Optional

from docmate.core.logging_utils import _log_event

_PROMPT_TEMPLATE = """\
You are an expert invoice data extraction engine for UK retail businesses.
Extract all data from the invoice text below and return ONLY valid JSON — no explanation, no markdown, no code fences.

Return this exact schema:
{{
  "invoice_type": "PURCHASE_INVOICE | EXPENSE | CREDIT_NOTE | STATEMENT",
  "header": {{
    "supplierName": "string",
    "customerName": "string",
    "companyName": "string",
    "tradingName": "string",
    "customerNo": "string",
    "customerPONumber": "string",
    "invoiceNumber": "string",
    "invoiceDate": "YYYY-MM-DD",
    "currency": "GBP",
    "totalNet": 0.00,
    "totalVat": 0.00,
    "totalGross": 0.00,
    "deliveryCharge": 0.00
  }},
  "items": [
    {{
      "lineNo": 1,
      "code": "string",
      "description": "string",
      "casePack": 1,
      "unitSize": "string",
      "qty": 0.0,
      "unitPrice": 0.00,
      "lineNet": 0.00,
      "vatCode": "A",
      "vatRate": 20.0,
      "vatAmount": 0.00,
      "lineGross": 0.00
    }}
  ],
  "vatBreakdown": [
    {{
      "vatCode": "A",
      "netAmount": 0.00,
      "vatAmount": 0.00,
      "grossAmount": 0.00
    }}
  ]
}}

Rules:
- vatCode: A = 20% standard rate, Z = 0% zero-rated, R = 5% reduced rate, B = 20% alcohol/tobacco
- All dates must be YYYY-MM-DD (convert UK dd/mm/yyyy if needed)
- All monetary amounts must be plain numbers, not strings
- For EXPENSE invoices (utilities, rent, insurance, services): items will have 1-3 lines with description and total
- If a field is unknown or absent, use null for strings and 0 for numbers
- Do not invent data — only extract what is present in the text

INVOICE TEXT:
{text}"""

_VISION_PROMPT_TEMPLATE = """\
You are an expert invoice data extraction engine for UK retail businesses.
Extract all data from the invoice image and return ONLY valid JSON — no explanation, no markdown, no code fences.

Return this exact schema:
{{
  "invoice_type": "PURCHASE_INVOICE | EXPENSE | CREDIT_NOTE | STATEMENT",
  "header": {{
    "supplierName": "string",
    "customerName": "string",
    "companyName": "string",
    "tradingName": "string",
    "customerNo": "string",
    "customerPONumber": "string",
    "invoiceNumber": "string",
    "invoiceDate": "YYYY-MM-DD",
    "currency": "GBP",
    "totalNet": 0.00,
    "totalVat": 0.00,
    "totalGross": 0.00,
    "deliveryCharge": 0.00
  }},
  "items": [
    {{
      "lineNo": 1,
      "code": "string",
      "description": "string",
      "casePack": 1,
      "unitSize": "string",
      "qty": 0.0,
      "unitPrice": 0.00,
      "lineNet": 0.00,
      "vatCode": "A",
      "vatRate": 20.0,
      "vatAmount": 0.00,
      "lineGross": 0.00
    }}
  ],
  "vatBreakdown": [
    {{
      "vatCode": "A",
      "netAmount": 0.00,
      "vatAmount": 0.00,
      "grossAmount": 0.00
    }}
  ]
}}

Rules:
- Read ALL line items from the invoice image carefully — do not skip any rows
- vatCode: A = 20% standard rate, Z = 0% zero-rated, R = 5% reduced rate, B = 20% alcohol/tobacco
- All dates must be YYYY-MM-DD (convert UK dd/mm/yyyy if needed)
- All monetary amounts must be plain numbers, not strings
- For EXPENSE invoices (utilities, rent, insurance, services): items will have 1-3 lines with description and total
- If a field is unknown or absent, use null for strings and 0 for numbers
- Do not invent data — only extract what is present in the image"""


class ClaudeAIFallbackParser:
    """
    AI-powered fallback parser using Claude API.
    Activates for unrecognised suppliers and expense/service invoices.
    Requires the anthropic package: pip install anthropic
    """

    def __init__(self, api_key: str, model: str = "claude-sonnet-4-6"):
        self.api_key = (api_key or "").strip()
        self.model = model or "claude-sonnet-4-6"

    def is_available(self) -> bool:
        return bool(self.api_key)

    def extract(self, doc_text: str, doc_type: str = "Purchase Invoice") -> Optional[Dict[str, Any]]:
        """
        Extract invoice data using Claude. Returns normalised {header, items} dict,
        or None if unavailable or on unrecoverable error.
        """
        if not self.is_available():
            return None
        try:
            import anthropic  # noqa: F401
        except ImportError:
            _log_event("ClaudeAIFallback: anthropic package not installed. Run: pip install anthropic")
            return None

        text_excerpt = (doc_text or "")[:8000]
        prompt = _PROMPT_TEMPLATE.format(text=text_excerpt)

        try:
            import anthropic as _anthropic
            client = _anthropic.Anthropic(api_key=self.api_key)
            message = client.messages.create(
                model=self.model,
                max_tokens=4096,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = message.content[0].text if message.content else ""
            _log_event(f"ClaudeAIFallback: response received ({len(raw)} chars)")
            return _parse_claude_response(raw)
        except Exception as exc:
            _log_event(f"ClaudeAIFallback API error: {exc}")
            return None

    def extract_from_image(self, file_bytes: bytes, mime_type: str, doc_type: str = "Purchase Invoice") -> Optional[Dict[str, Any]]:
        """Extract invoice data directly from an image using Claude Vision API.
        Bypasses OCR entirely — much more accurate for scanned/photo invoices.
        Supports PNG, JPEG, and scanned PDFs (converted to images via PyMuPDF).
        """
        if not self.is_available():
            return None
        try:
            import anthropic as _anthropic
        except ImportError:
            _log_event("ClaudeAIFallback Vision: anthropic package not installed")
            return None

        try:
            image_blocks = []

            if mime_type == "application/pdf":
                try:
                    from docmate.core.ocr import _pdf_to_images
                    imgs = _pdf_to_images(file_bytes, dpi=200, max_pages=3)
                    for img in imgs:
                        buf = io.BytesIO()
                        img.save(buf, format="PNG")
                        b64 = base64.standard_b64encode(buf.getvalue()).decode()
                        image_blocks.append({
                            "type": "image",
                            "source": {"type": "base64", "media_type": "image/png", "data": b64},
                        })
                except Exception as e:
                    _log_event(f"ClaudeAIFallback Vision: PDF to image failed: {e}")
                    return None
            else:
                allowed = {"image/png", "image/jpeg", "image/gif", "image/webp"}
                media_type = mime_type if mime_type in allowed else "image/jpeg"
                b64 = base64.standard_b64encode(file_bytes).decode()
                image_blocks.append({
                    "type": "image",
                    "source": {"type": "base64", "media_type": media_type, "data": b64},
                })

            if not image_blocks:
                return None

            content = image_blocks + [{"type": "text", "text": _VISION_PROMPT_TEMPLATE}]
            client = _anthropic.Anthropic(api_key=self.api_key)
            message = client.messages.create(
                model=self.model,
                max_tokens=4096,
                messages=[{"role": "user", "content": content}],
            )
            raw = message.content[0].text if message.content else ""
            _log_event(f"ClaudeAIFallback Vision: response received ({len(raw)} chars)")
            return _parse_claude_response(raw)
        except Exception as exc:
            _log_event(f"ClaudeAIFallback Vision API error: {exc}")
            return None


def _parse_claude_response(raw: str) -> Optional[Dict[str, Any]]:
    """Parse Claude's JSON response into the standard {header, items} dict."""
    if not raw:
        return None

    text = raw.strip()
    # Strip markdown code fences if model added them
    text = re.sub(r"^```(?:json)?\s*\n?", "", text)
    text = re.sub(r"\n?```\s*$", "", text)
    text = text.strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{[\s\S]*\}", text)
        if not m:
            _log_event("ClaudeAIFallback: could not parse JSON from response")
            return None
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError:
            _log_event("ClaudeAIFallback: JSON extraction from response failed")
            return None

    if not isinstance(data, dict):
        return None

    hdr_raw = data.get("header") or {}
    items_raw = data.get("items") or []
    vat_raw = data.get("vatBreakdown") or []
    invoice_type = str(data.get("invoice_type") or "PURCHASE_INVOICE")

    header: Dict[str, Any] = {
        "supplierName": _s(hdr_raw.get("supplierName")),
        "customerName": _s(hdr_raw.get("customerName")),
        "companyName": _s(hdr_raw.get("companyName")) or "N/A",
        "tradingName": _s(hdr_raw.get("tradingName")),
        "customerNo": _s(hdr_raw.get("customerNo")),
        "customerPONumber": _s(hdr_raw.get("customerPONumber")),
        "invoiceNumber": _s(hdr_raw.get("invoiceNumber")),
        "invoiceDate": _s(hdr_raw.get("invoiceDate")),
        "currency": _s(hdr_raw.get("currency")) or "GBP",
        "totalNet": _f(hdr_raw.get("totalNet")),
        "totalVat": _f(hdr_raw.get("totalVat")),
        "totalGross": _f(hdr_raw.get("totalGross")),
        "deliveryCharge": _f(hdr_raw.get("deliveryCharge")),
        "_aiExtracted": True,
        "_invoiceType": invoice_type,
    }

    if vat_raw:
        header["vatBreakdown"] = [
            {
                "vatCode": _s(r.get("vatCode")),
                "netAmount": _f(r.get("netAmount")),
                "vatAmount": _f(r.get("vatAmount")),
                "grossAmount": _f(r.get("grossAmount")),
                "_source": "ai_extracted",
            }
            for r in vat_raw
            if isinstance(r, dict)
        ]

    items: List[Dict[str, Any]] = []
    for i, it in enumerate(items_raw):
        if not isinstance(it, dict):
            continue
        items.append({
            "lineNo": int(it.get("lineNo") or i + 1),
            "code": _s(it.get("code")),
            "description": _s(it.get("description")),
            "casePack": int(it.get("casePack") or 1),
            "unitSize": _s(it.get("unitSize")),
            "qty": _f(it.get("qty")),
            "unitPrice": _f(it.get("unitPrice")),
            "lineNet": _f(it.get("lineNet")),
            "vatCode": _s(it.get("vatCode")) or "A",
            "vatRate": _f(it.get("vatRate")),
            "vatAmount": _f(it.get("vatAmount")),
            "lineGross": _f(it.get("lineGross")),
        })

    return {"header": header, "items": items}


def _s(val: Any) -> str:
    if val is None:
        return ""
    return str(val).strip()


def _f(val: Any) -> float:
    try:
        if val is None:
            return 0.0
        return round(float(str(val).replace(",", "")), 4)
    except (ValueError, TypeError):
        return 0.0
