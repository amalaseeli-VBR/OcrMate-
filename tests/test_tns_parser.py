from docmate.parsers.tns import parse_tns_invoice


TNS_TEXT = """TNS WHOLESALE INVOICE
Inv No: 5954962096325
Invoice Issue Date
August 15, 2025
Order No
SO1755
BILL TO:
Company: T/A Halfway Minimart - 10169
SHIP TO:
Description Qty Pack Price Net VAT% RRP POR%
SKE600KIT006
SKE Pro 600 Prefilled Kit Puffs 2% Nic - 10 Packs (Cola Ice)
6 - £20.45 £122.70 20% - -
SKE600POD009
10
-
£19.85
£198.50
20%
-
-
SKE Pro 600 Prefilled Pod Puffs 2% Nic - 10 Packs (Lemon Lime)
TNS_V_0132_15
ELUX LIQ, 20MG, 2% Nic - 10 Packs (Cherry Ice)
3 - £11.95 £35.85 20% - -
Total No Items: 19
VAT SUMMARY
Subtotal: £357.05
VAT (20%): £71.41
Total: £428.46
"""


def test_tns_native_and_ocr_row_layouts_are_reconciled():
    result = parse_tns_invoice(TNS_TEXT)

    assert result["header"] == {
        "supplierName": "TNS Retail Limited",
        "customerName": "T/A Halfway Minimart - 10169",
        "invoiceNumber": "5954962096325",
        "invoiceDate": "2025-08-15",
        "orderNumber": "SO1755",
        "currency": "GBP",
        "totalNet": 357.05,
        "totalVat": 71.41,
        "totalGross": 428.46,
    }
    assert [item["code"] for item in result["items"]] == [
        "SKE600KIT006",
        "SKE600POD009",
        "TNS_V_0132_15",
    ]
    assert [item["qty"] for item in result["items"]] == [6.0, 10.0, 3.0]
    assert [item["lineNet"] for item in result["items"]] == [122.70, 198.50, 35.85]
    assert sum(item["lineNet"] for item in result["items"]) == 357.05


def test_tns_merged_doctr_header_columns():
    merged = TNS_TEXT.replace(
        "Invoice Issue Date\nAugust 15, 2025",
        "Invoice Issue Date August 15, 2025 Order Date July 9, 2025",
    ).replace(
        "BILL TO:\nCompany: T/A Halfway Minimart - 10169\nSHIP TO:",
        "BILL TO: SHIP TO:\n"
        "Name: Amudha Vigithkumar Name: Amudha Vigithkumar\n"
        "Company: T/A Halfway Minimart - 10169 Company: T/A Halfway Minimart - 10169\n"
        "Email: bill@example.com Email: ship@example.com",
    )

    result = parse_tns_invoice(merged)

    assert result["header"]["invoiceDate"] == "2025-08-15"
    assert result["header"]["customerName"] == "T/A Halfway Minimart - 10169"


def test_tns_rows_without_product_codes_are_not_dropped():
    text = TNS_TEXT.replace(
        "TNS_V_0132_15\nELUX LIQ, 20MG, 2% Nic - 10 Packs (Cherry Ice)\n"
        "3 - £11.95 £35.85 20% - -",
        "ELUX LIQ, 20MG, 2% Nic - 10 Packs (Cherry Ice)\n"
        "3 - £11.95 £35.85 20% - -",
    )

    result = parse_tns_invoice(text)

    assert len(result["items"]) == 3
    assert result["items"][2]["code"] == ""
    assert result["items"][2]["lineNet"] == 35.85


def test_tns_wrapped_negative_por_and_footer_discount_reconcile():
    text = """TNS WHOLESALE INVOICE
Inv No: INV-GB-7344
Description Qty Pack Price Net VAT% RRP POR%
IP2KBO003
Ivg Pro 2 Battery Only Kit (Silver Device)
2
1
£17.50
£35.00
20%
£5.49
-282.51
%
Total No Items: 2
VAT SUMMARY
Total Discounts:
-£5.00
Subtotal (Ex Vat):
£30.00
VAT:
£6.00
Total:
£36.00
"""

    result = parse_tns_invoice(text)

    assert len(result["items"]) == 2
    assert result["items"][0]["code"] == "IP2KBO003"
    assert result["items"][0]["por"] == -282.51
    assert result["items"][1]["lineNet"] == -5.0
    assert result["items"][1]["vatAmount"] == -1.0
    assert result["items"][1]["_reconOnly"] is True
    assert sum(item["lineNet"] for item in result["items"]) == 30.0
    assert sum(item["vatAmount"] for item in result["items"]) == 6.0


def test_tns_layout_text_with_description_and_wrapped_por():
    text = """TNS WHOLESALE INVOICE
Description Qty Pack Price Net VAT% RRP POR%
IP2KBO003
Ivg Pro 2 Battery Only Kit (Silver 2 1 £17.50 £35.00 20% £5.49 -282.51
Device)                                                        %
Total No Items: 2
Subtotal (Ex Vat): £35.00
VAT: £7.00
Total: £42.00
"""

    result = parse_tns_invoice(text)

    assert len(result["items"]) == 1
    assert result["items"][0]["code"] == "IP2KBO003"
    assert result["items"][0]["lineNet"] == 35.0
    assert result["items"][0]["por"] == -282.51


def test_tns_description_continuation_after_numeric_tail_is_preserved():
    text = """TNS WHOLESALE INVOICE
Description Qty Pack Price Net VAT% RRP POR%
IVG10KPOD685037
IVG Pro12 Refill Pods - 10K Puffs 5 5 £14.95 £74.75 20% £7.95 54.87%
(Spearmint)
===== Page 2/4 =====
Description Qty Pack Price Net VAT% RRP POR%
IVG10KPOD685038
IVG Pro12 Refill Pods - 10K Puffs 5 5 £14.95 £74.75 20% £7.95 54.87%
(Rainbow Burst)
Total No Items: 10
Subtotal (Ex Vat): £149.50
VAT: £29.90
Total: £179.40
"""

    result = parse_tns_invoice(text)

    assert [item["description"] for item in result["items"]] == [
        "IVG Pro12 Refill Pods - 10K Puffs (Spearmint)",
        "IVG Pro12 Refill Pods - 10K Puffs (Rainbow Burst)",
    ]
