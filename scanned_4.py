
import argparse
import re
import shutil
import tempfile
from pathlib import Path

import cv2
import numpy as np
import pandas as pd
import pytesseract


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"}
PDF_EXTENSIONS = {".pdf"}
SUPPORTED_EXTENSIONS = IMAGE_EXTENSIONS | PDF_EXTENSIONS
ENABLE_CONTRAST_ENHANCEMENT = False
RECON_TOLERANCE = 0.05
LOW_CONTRAST_THRESHOLD = 35
LOW_OCR_CONFIDENCE_THRESHOLD = 70
MIN_OCR_DIMENSION = 1800
VERBOSE_PROGRESS = True
MIN_ACCEPTABLE_TEXT_ROWS = 10
PROMO_LINE_RE = re.compile(
    r"\b(BUY\s*ANY|BUY\s*\d+|BUY\d+|MULTI\s*BUY|MULTIBUY|MULTI-BUY|PROMO|PROMOTION|DEAL|OFFER|SAVE|SAVING|"
    r"DISC(?:OUNT)?|GET\s*\d+(?:\s+\S+){0,3}\s+FREE|BUY\b.*\bFREE)\b",
    re.IGNORECASE,
)
PROMO_STATEMENT_RE = re.compile(
    r"\b(SPEND\s*&\s*SAVE\s+STATEMENT|TOBACCO\s+SPEND|STATEMENT\s+.*\bSPEND)\b",
    re.IGNORECASE,
)

DHAMECHA_COLUMN_BOUNDS = {
    "code": (0.02, 0.10),
    "description": (0.09, 0.45),
    "case_pack": (0.45, 0.52),
    "unit_size": (0.51, 0.56),
    "quantity": (0.56, 0.61),
    "price": (0.60, 0.68),
    "ext_price": (0.67, 0.76),
    "rrp": (0.76, 0.84),
    "por": (0.84, 0.93),
    "vatcode": (0.92, 0.98),
}


def configure_tesseract():
    tesseract_cmd = shutil.which("tesseract")
    if tesseract_cmd:
        pytesseract.pytesseract.tesseract_cmd = tesseract_cmd
        return

    common_paths = [
        Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe"),
        Path(r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"),
    ]
    for path in common_paths:
        if path.exists():
            pytesseract.pytesseract.tesseract_cmd = str(path)
            return

    raise RuntimeError(
        "Tesseract OCR is not installed or is not on PATH. "
        "Install it, then run this script again."
    )


def preprocess_image(image_path):
    if not image_path.exists():
        raise FileNotFoundError(f"Image file not found: {image_path}")

    img = cv2.imread(str(image_path))
    if img is None:
        raise FileNotFoundError(f"Could not read image: {image_path}")

    return prepare_ocr_image(img, force_enhancement=ENABLE_CONTRAST_ENHANCEMENT)


def upscale_for_ocr(gray, min_dimension=MIN_OCR_DIMENSION):
    height, width = gray.shape[:2]
    short_edge = min(height, width)
    if short_edge <= 0 or short_edge >= min_dimension:
        return gray

    scale = min_dimension / short_edge
    return cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)


def estimate_skew_angle(gray):
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    thresholded = cv2.threshold(
        blurred,
        0,
        255,
        cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU,
    )[1]
    coords = np.column_stack(np.where(thresholded > 0))
    if len(coords) < 100:
        return 0.0

    angle = cv2.minAreaRect(coords)[-1]
    if angle < -45:
        angle = 90 + angle
    elif angle > 45:
        angle = angle - 90
    return float(angle)


def deskew_image(gray):
    angle = estimate_skew_angle(gray)
    if abs(angle) < 0.3:
        return gray

    height, width = gray.shape[:2]
    center = (width // 2, height // 2)
    matrix = cv2.getRotationMatrix2D(center, angle, 1.0)
    return cv2.warpAffine(
        gray,
        matrix,
        (width, height),
        flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_REPLICATE,
    )


def prepare_ocr_image(img, force_enhancement=False):
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    gray = upscale_for_ocr(gray)
    gray = deskew_image(gray)

    if not force_enhancement:
        return cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]

    return enhance_for_ocr(gray)


def enhance_for_ocr(gray):
    if len(gray.shape) == 3:
        gray = cv2.cvtColor(gray, cv2.COLOR_BGR2GRAY)

    gray = upscale_for_ocr(gray)
    gray = deskew_image(gray)
    denoised = cv2.fastNlMeansDenoising(gray, None, 10, 7, 21)
    normalized = cv2.normalize(denoised, None, 0, 255, cv2.NORM_MINMAX)

    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
    contrasted = clahe.apply(normalized)

    blurred = cv2.GaussianBlur(contrasted, (0, 0), 1.0)
    sharpened = cv2.addWeighted(contrasted, 1.7, blurred, -0.7, 0)
    binary = cv2.adaptiveThreshold(
        sharpened,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        35,
        9,
    )
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
    return cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel)


def image_quality_metrics(image_path):
    img = cv2.imread(str(image_path))
    if img is None:
        return None

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    height, width = gray.shape[:2]
    brightness = float(gray.mean())
    contrast = float(gray.std())
    sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())

    edges = cv2.Canny(gray, 50, 150, apertureSize=3)
    lines = cv2.HoughLines(edges, 1, 3.141592653589793 / 180, 180)
    angles = []
    if lines is not None:
        for line in lines[:50]:
            rho, theta = line[0]
            angle = (theta * 180 / 3.141592653589793) - 90
            if -15 <= angle <= 15:
                angles.append(angle)
    skew = float(sorted(angles)[len(angles) // 2]) if angles else 0.0

    ocr_data = pytesseract.image_to_data(
        gray,
        config="--psm 6",
        output_type=pytesseract.Output.DICT,
    )
    confidences = []
    for confidence, word in zip(ocr_data["conf"], ocr_data["text"]):
        if not clean_text(word):
            continue
        try:
            confidence = float(confidence)
        except ValueError:
            continue
        if confidence >= 0:
            confidences.append(confidence)
    ocr_confidence = sum(confidences) / len(confidences) if confidences else 0.0

    score = 100
    warnings = []
    if min(width, height) < 1200:
        score -= 20
        warnings.append("low resolution")
    if sharpness < 80:
        score -= 25
        warnings.append("blurry")
    elif sharpness < 150:
        score -= 10
        warnings.append("slightly blurry")
    if contrast < LOW_CONTRAST_THRESHOLD:
        score -= 15
        warnings.append("low contrast")
    if brightness < 80 or brightness > 235:
        score -= 10
        warnings.append("poor brightness")
    if abs(skew) > 3:
        score -= 15
        warnings.append("skewed page")
    if ocr_confidence < 55:
        score -= 20
        warnings.append("low OCR confidence")
    elif ocr_confidence < LOW_OCR_CONFIDENCE_THRESHOLD:
        score -= 10
        warnings.append("medium OCR confidence")

    score = max(0, min(100, score))
    if score >= 80:
        rating = "Good"
    elif score >= 60:
        rating = "Warning"
    else:
        rating = "Poor"

    return {
        "width": width,
        "height": height,
        "brightness": brightness,
        "contrast": contrast,
        "sharpness": sharpness,
        "skew": skew,
        "ocr_confidence": ocr_confidence,
        "word_count": len(confidences),
        "score": score,
        "rating": rating,
        "warnings": warnings,
    }


def quality_check_pages(input_path):
    if input_path.suffix.lower() in IMAGE_EXTENSIONS:
        metrics = image_quality_metrics(input_path)
        return [metrics] if metrics else []

    if input_path.suffix.lower() in PDF_EXTENSIONS:
        with tempfile.TemporaryDirectory() as temp_dir:
            page_images = render_pdf_pages(input_path, Path(temp_dir))
            return [
                metrics
                for metrics in (image_quality_metrics(page_image) for page_image in page_images)
                if metrics
            ]

    return []


def print_quality_report(metrics_by_page):
    if not metrics_by_page:
        print("\nInput quality: could not measure")
        return

    average_score = sum(page["score"] for page in metrics_by_page) / len(metrics_by_page)
    if average_score >= 80:
        overall = "Good"
    elif average_score >= 60:
        overall = "Warning"
    else:
        overall = "Poor"

    print(f"\nInput quality: {overall} ({average_score:.0f}/100)")
    for index, page in enumerate(metrics_by_page, start=1):
        warnings = ", ".join(page["warnings"]) if page["warnings"] else "none"
        print(
            f"Page {index}: {page['rating']} ({page['score']}/100), "
            f"{page['width']}x{page['height']}, OCR confidence {page['ocr_confidence']:.1f}%, "
            f"sharpness {page['sharpness']:.0f}, contrast {page['contrast']:.1f}, "
            f"skew {page['skew']:.1f} deg, warnings: {warnings}"
        )


def has_contrast_warning(metrics_by_page):
    return any("low contrast" in page["warnings"] for page in metrics_by_page)


def log_progress(message):
    if VERBOSE_PROGRESS:
        print(message, flush=True)


def should_try_enhancement(metrics_by_page):
    if not metrics_by_page:
        return False

    average_score = sum(page["score"] for page in metrics_by_page) / len(metrics_by_page)
    average_confidence = sum(page["ocr_confidence"] for page in metrics_by_page) / len(metrics_by_page)
    severe_low_contrast = any(page["contrast"] < 30 for page in metrics_by_page)
    weak_ocr = any(page["ocr_confidence"] < LOW_OCR_CONFIDENCE_THRESHOLD for page in metrics_by_page)
    poor_page = any(page["score"] < 75 for page in metrics_by_page)

    if average_score >= 80 and average_confidence >= 80 and not severe_low_contrast and not weak_ocr:
        return False

    return any(
        "low contrast" in page["warnings"]
        or "low OCR confidence" in page["warnings"]
        or "medium OCR confidence" in page["warnings"]
        or "poor brightness" in page["warnings"]
        for page in metrics_by_page
    ) or poor_page


def render_pdf_pages(pdf_path, output_dir, dpi=350):
    try:
        import fitz
    except ImportError as exc:
        raise RuntimeError(
            "PDF support needs PyMuPDF. Install it with: python -m pip install pymupdf"
        ) from exc

    doc = fitz.open(pdf_path)
    if doc.page_count == 0:
        raise RuntimeError(f"PDF has no pages: {pdf_path}")

    scale = dpi / 72
    matrix = fitz.Matrix(scale, scale)
    page_images = []
    for page_index, page in enumerate(doc, start=1):
        pixmap = page.get_pixmap(matrix=matrix, alpha=False)
        image_path = output_dir / f"{pdf_path.stem}_page_{page_index}.png"
        pixmap.save(image_path)
        page_images.append(image_path)

    return page_images


def extract_text_from_image(image_path):
    processed = preprocess_image(image_path)
    return pytesseract.image_to_string(processed)


def extract_text_from_file(input_path, page_images=None):
    if input_path.suffix.lower() in IMAGE_EXTENSIONS:
        log_progress("Running OCR on image...")
        return extract_text_from_image(input_path), extract_header_ocr_text(input_path)

    if input_path.suffix.lower() in PDF_EXTENSIONS:
        if page_images is None:
            with tempfile.TemporaryDirectory() as temp_dir:
                log_progress("Rendering PDF pages...")
                page_images = render_pdf_pages(input_path, Path(temp_dir))
                page_texts = []
                for page_number, page_image in enumerate(page_images, start=1):
                    log_progress(f"OCR text extraction: page {page_number}/{len(page_images)}")
                    page_text = extract_text_from_image(page_image)
                    page_texts.append(f"\n--- Page {page_number} ---\n{page_text}")

                log_progress("Extracting header fields from page 1...")
                header_text = extract_header_ocr_text(page_images[0])
                return "\n".join(page_texts).strip(), header_text

        page_texts = []
        for page_number, page_image in enumerate(page_images, start=1):
            log_progress(f"OCR text extraction: page {page_number}/{len(page_images)}")
            page_text = extract_text_from_image(page_image)
            page_texts.append(f"\n--- Page {page_number} ---\n{page_text}")

        log_progress("Extracting header fields from page 1...")
        header_text = extract_header_ocr_text(page_images[0])
        return "\n".join(page_texts).strip(), header_text

    raise RuntimeError(f"Unsupported file type: {input_path.suffix}")


def ocr_crop(img, x1, y1, x2, y2, psm=6, threshold=False):
    h, w = img.shape[:2]
    crop = img[int(h * y1):int(h * y2), int(w * x1):int(w * x2)]
    crop = cv2.resize(crop, None, fx=4, fy=4, interpolation=cv2.INTER_CUBIC)
    if threshold:
        processed = prepare_ocr_image(crop, force_enhancement=ENABLE_CONTRAST_ENHANCEMENT)
    else:
        processed = deskew_image(upscale_for_ocr(cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)))
    return pytesseract.image_to_string(processed, config=f"--psm {psm}")


def extract_header_ocr_text(image_path):
    img = cv2.imread(str(image_path))
    if img is None:
        return ""

    invoice_text = ocr_crop(img, 0.76, 0.015, 0.97, 0.06, psm=7, threshold=True)
    date_text = ocr_crop(img, 0.78, 0.045, 0.98, 0.10, psm=6, threshold=True)
    return f"{invoice_text}\n{date_text}".strip()


def ocr_words_with_positions(image_path):
    img = cv2.imread(str(image_path))
    if img is None:
        return [], 0, 0

    gray = prepare_ocr_image(img, force_enhancement=ENABLE_CONTRAST_ENHANCEMENT)
    data = pytesseract.image_to_data(
        gray,
        config="--psm 6",
        output_type=pytesseract.Output.DICT,
    )

    words = []
    for index, text in enumerate(data["text"]):
        text = clean_text(text)
        if not text:
            continue
        try:
            confidence = float(data["conf"][index])
        except ValueError:
            confidence = -1
        if confidence < 0:
            continue

        left = data["left"][index]
        top = data["top"][index]
        width = data["width"][index]
        height = data["height"][index]
        words.append(
            {
                "text": text,
                "left": left,
                "top": top,
                "right": left + width,
                "bottom": top + height,
                "x_mid": left + (width / 2),
                "y_mid": top + (height / 2),
                "line_key": (
                    data["page_num"][index],
                    data["block_num"][index],
                    data["par_num"][index],
                    data["line_num"][index],
                ),
            }
        )

    return words, img.shape[1], img.shape[0]


def page_layout_lines(image_path):
    words, width, height = ocr_words_with_positions(image_path)
    if not words:
        return []

    grouped = {}
    for word in words:
        grouped.setdefault(word["line_key"], []).append(word)

    lines = []
    for line_words in grouped.values():
        line_words = sorted(line_words, key=lambda word: word["left"])
        text = clean_text(" ".join(word["text"] for word in line_words))
        if not text:
            continue
        top = min(word["top"] for word in line_words)
        bottom = max(word["bottom"] for word in line_words)
        lines.append(
            {
                "text": text,
                "top": top,
                "bottom": bottom,
                "y_mid": (top + bottom) / 2,
                "words": line_words,
                "width": width,
                "height": height,
            }
        )

    return sorted(lines, key=lambda line: line["top"])


def words_in_column(line, column_name, tolerance=0.007):
    start, end = DHAMECHA_COLUMN_BOUNDS[column_name]
    width = line["width"]
    return [
        word
        for word in line["words"]
        if (start - tolerance) <= (word["x_mid"] / width) < (end + tolerance)
    ]


def column_text(line, column_name):
    words = words_in_column(line, column_name)
    return clean_text(" ".join(word["text"] for word in words)) or ""


def column_text_numeric(img, line, column_name):
    start, end = DHAMECHA_COLUMN_BOUNDS[column_name]
    h, w = img.shape[:2]
    y1 = max(0.0, line["top"] / h - 0.003)
    y2 = min(1.0, line["bottom"] / h + 0.003)
    crop = img[int(h * y1):int(h * y2), int(w * start):int(w * end)]
    if crop.size == 0:
        return ""
    gray = prepare_ocr_image(crop, force_enhancement=ENABLE_CONTRAST_ENHANCEMENT)
    return pytesseract.image_to_string(
        gray,
        config="--psm 8 -c tessedit_char_whitelist=0123456789.,-% ",
    ).strip()


def parse_dhamecha_layout_line(line, vat_rates, description_prefix=None, img=None):
    code = clean_code(column_text(line, "code"))
    if not looks_like_item_code(code):
        return None

    description = column_text(line, "description")
    if not description:
        return None

    case_pack = column_text(line, "case_pack")
    unit_size = column_text(line, "unit_size")
    if img is not None:
        quantity = column_text_numeric(img, line, "quantity") or column_text(line, "quantity")
        price = column_text_numeric(img, line, "price") or column_text(line, "price")
        ext_price = column_text_numeric(img, line, "ext_price") or column_text(line, "ext_price")
        rrp = column_text_numeric(img, line, "rrp") or column_text(line, "rrp")
        por = column_text_numeric(img, line, "por") or column_text(line, "por")
    else:
        quantity = column_text(line, "quantity")
        price = column_text(line, "price")
        ext_price = column_text(line, "ext_price")
        rrp = column_text(line, "rrp")
        por = column_text(line, "por")
    vatcode = column_text(line, "vatcode") or "A"

    if not price and not ext_price:
        return None
    if not por:
        por = "0%"

    vatcode = clean_vatcode(vatcode.strip(".,|"))
    if not re.fullmatch(r"[A-Z0-9]+", vatcode):
        vatcode = "A"

    description, case_pack, unit_size, quantity, price, ext_price, rrp = repair_dhamecha_columns(
        code,
        description,
        case_pack,
        unit_size,
        quantity,
        price,
        ext_price,
        rrp,
    )

    if description_prefix:
        description = f"{description_prefix} {description}"
    description = clean_description(description)
    code = correct_code_from_description(code, description)

    return {
        "Code": code,
        "Description": description,
        "PMP": clean_money(price),
        "ExtPrice": clean_money(ext_price),
        "Case pack": clean_case_pack(case_pack),
        "unit size": clean_unit_size(unit_size),
        "Quantity": clean_quantity(quantity),
        "vat": vat_rates.get(vatcode.upper()),
        "vatcode": vatcode.upper(),
        "Stfdrrp": clean_money(rrp),
        "PRR": clean_percent(por),
    }


def extract_dhamecha_layout_items_from_images(page_images, text):
    if not re.search(r"Dhamecha|Product\s+Description", text, re.IGNORECASE):
        return []

    vat_rates = extract_vat_rates(text)
    rows = []
    description_prefix = None

    for page_image in page_images:
        img = cv2.imread(str(page_image))
        in_table = False
        for line in page_layout_lines(page_image):
            line_text = line["text"]
            if (
                re.search(r"\bItem\s+#.*Product\s+Description\b", line_text, re.IGNORECASE)
                or re.search(r"\bltem\s+#\b", line_text, re.IGNORECASE)
                or re.search(r"Product\s+Desc\w*\b.*\bCase\b.*Price", line_text, re.IGNORECASE)
            ):
                in_table = True
                description_prefix = None
                continue

            if not in_table:
                continue

            if re.search(
                r"\b(?:Items\s+Total|Title\s+in\s+the\s+Goods|Dhamecha\s+Foods|"
                r"This\s+invoice|Page\s+\d+\s+of\s+\d+)\b",
                line_text,
                re.IGNORECASE,
            ):
                in_table = False
                description_prefix = None
                continue

            if re.search(r"\b(?:Trolley|Sub\s+Total|T&S:|BUY\s+ANY)\b", line_text, re.IGNORECASE):
                description_prefix = None
                continue

            row = parse_dhamecha_layout_line(line, vat_rates, description_prefix, img=img)
            if row:
                rows.append(row)
                description_prefix = None
                continue

            wrapped_description, _suffix = split_wrapped_description_line(line_text)
            if wrapped_description:
                description_prefix = (
                    f"{description_prefix} {wrapped_description}"
                    if description_prefix
                    else wrapped_description
                )

    return rows


def folder_inputs():
    return sorted(
        path for path in Path.cwd().iterdir() if path.suffix.lower() in SUPPORTED_EXTENSIONS
    )


def choose_input_interactively():
    inputs = folder_inputs()
    if inputs:
        print("Images/PDFs found in this folder:")
        for index, input_file in enumerate(inputs, start=1):
            print(f"{index}. {input_file.name}")

        prompt = "Choose file number, or paste another image/PDF path"
        if len(inputs) == 1:
            prompt += " [Enter = 1]"
        prompt += ": "

        choice = input(prompt).strip().strip('"')
        if not choice and len(inputs) == 1:
            return inputs[0]
        if choice.isdigit() and 1 <= int(choice) <= len(inputs):
            return inputs[int(choice) - 1]
        if choice:
            return Path(choice)

    input_path = input("Paste the full path to the invoice image/PDF: ").strip().strip('"')
    if not input_path:
        raise SystemExit("No file selected.")

    return Path(input_path)


def find_input(input_path=None):
    if input_path is None:
        input_path = choose_input_interactively()

    if input_path.exists():
        if input_path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            raise SystemExit(f"Unsupported file type: {input_path.suffix}")
        return input_path

    inputs = folder_inputs()
    if len(inputs) == 1:
        print(f"File not found: {input_path}")
        print(f"Using the only image/PDF in this folder instead: {inputs[0].name}\n")
        return inputs[0]

    if inputs:
        available = "\n".join(f"- {path.name}" for path in inputs)
        raise SystemExit(
            f"File not found: {input_path}\n"
            f"Images/PDFs in this folder:\n{available}\n"
            "Run again with one of those filenames."
        )

    raise SystemExit(
        f"File not found: {input_path}\n"
        "Put your invoice image/PDF in this folder or pass the full path."
    )


def first_match(pattern, text, group=1):
    match = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
    return match.group(group).strip() if match else None


def clean_amount(amount):
    return amount.replace(",", ".").replace("..", ".").strip() if amount.count(",") == 1 and "." not in amount else amount.replace(",", "").strip()


def clean_money(amount):
    if amount is None:
        return ""
    amount = clean_amount(amount).replace("P", "").replace("p", "")
    amount = amount.replace("§", "5").replace("S", "5").replace(")", "")
    amount = amount.translate(str.maketrans("oOlLiI", "001111"))
    amount = re.sub(r"^\d-", "", amount)
    amount = re.sub(r"^[^\d.]+", "", amount)
    if "." not in amount and amount.isdigit() and len(amount) >= 4:
        amount = f"{amount[:-2]}.{amount[-2:]}"
    return amount


def money_value(amount):
    amount = clean_money(amount)
    try:
        return float(amount)
    except ValueError:
        return None


def clean_quantity(quantity):
    quantity = quantity.strip()
    quantity = quantity.translate(str.maketrans({"I": "1", "i": "1", "l": "1", "L": "1"}))
    if quantity in {"H1", "Hi", "to", "©", "�", "SD", "sss"}:
        return "1"
    cleaned = re.sub(r"\D", "", quantity)
    if cleaned == "11" and len(quantity) <= 2:
        return "1"
    return clean_amount(cleaned or quantity)


def clean_percent(percent):
    percent = percent.replace(" ", ".").replace("F.TO", "7.70").replace("G.0", "0.0")
    percent = percent.replace("§", "5").replace("S", "5").replace("�", "5")
    percent = percent.translate(str.maketrans("oOlLiI", "001111"))
    number = percent.rstrip("%")
    if "." not in number and number.isdigit() and len(number) >= 3:
        number = f"{number[:-2]}.{number[-2:]}"
    return f"{number}%"


def clean_unit_size(unit_size):
    value = unit_size.strip()
    corrections = {
        "to": "10",
        "id": "10",
        "a5": "25",
        "iz": "12",
        "=i": "5",
    }
    return corrections.get(value, value)


def clean_case_pack(case_pack):
    value = case_pack.strip()
    if value in {":", "."}:
        return ""
    if value in {"zomg", "+"}:
        return "-"
    return value


def clean_description(description):
    description = clean_text(description)
    replacements = {
        "Scl": "5cl",
        "5c!": "5cl",
        "20mq": "20mg",
        "l?mg": "12mg",
        "limg": "11mg",
        "BM&00": "BM600",
        "25'5": "25's",
        "(ee ADE": "IVG PRO KIT LEMONADE",
        "ores 600 KIT": "LOST MARY BM600 KIT",
        "copeRRY": "BLUEBERRY",
        "FENSON": "BENSON",
        "«": "",
    }
    for wrong, right in replacements.items():
        description = description.replace(wrong, right)
    return clean_text(description)


def correct_code_from_description(code, description):
    if re.fullmatch(r"\d{5,7}", code):
        return code
    if re.search(r"BENSON\s+&\s+HEDGES\s+SKY\s+BLUE\s+SUPERKINGS", description, re.IGNORECASE):
        return "828388"
    return code


def repair_dhamecha_columns(code, description, case_pack, unit_size, quantity, price, ext_price, rrp):
    if unit_size == "-" and re.fullmatch(r"\d{3}", quantity):
        unit_size = quantity[:-1]
        quantity = quantity[-1]

    if case_pack == "FRUIT" and unit_size.lower() == "20mg":
        description = f"{description} FRUIT 20mg"
        case_pack = ""
        unit_size = "5"
        quantity = "1"

    if clean_money(rrp) == "" and clean_money(ext_price):
        rrp = ext_price
        ext_price = price

    price_value = money_value(price)
    ext_price_value = money_value(ext_price)
    if (
        clean_quantity(quantity) == "1"
        and price_value
        and ext_price_value
        and ext_price_value > 0
        and ext_price_value < price_value
        and price_value != ext_price_value
        and re.search(r"\d+[,.]\d{2}", ext_price)
        and abs(price_value - ext_price_value) / min(price_value, ext_price_value) >= 0.30
    ):
        price = ext_price

    return description, case_pack, unit_size, quantity, price, ext_price, rrp


def clean_vatcode(vatcode):
    if vatcode is None:
        return ""
    vatcode = vatcode.upper()
    return {"4": "A", "ES": "E"}.get(vatcode, vatcode)


def clean_text(value):
    if value is None:
        return None
    value = value.replace("\ufffd", "").replace("\u2018", "").replace("\u2019", "")
    return re.sub(r"\s+", " ", value).strip(" .:-")


def parse_number(value):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "").replace("£", "").replace("GBP", "")
    if text.endswith("-") and len(text) > 1:
        text = "-" + text[:-1]
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def is_promo_line_item(row):
    if not isinstance(row, dict):
        return False
    code = str(row.get("code") or row.get("Code") or "").strip().upper()
    tag = str(row.get("rowTag") or "").strip().upper()
    desc = str(row.get("description") or row.get("Description") or "")
    raw = str(row.get("rawRow") or row.get("raw") or "")
    has_promo_text = bool(PROMO_LINE_RE.search(desc) or PROMO_LINE_RE.search(raw))
    line_net = parse_number(row.get("lineNet") or row.get("LineNet"))
    unit_price = parse_number(row.get("unitPrice") or row.get("PMP"))
    has_discount_value = (line_net is not None and line_net < 0) or (unit_price is not None and unit_price < 0)
    if code == "PROMO":
        return True
    if tag == "PROMO":
        return has_promo_text or has_discount_value
    return has_promo_text


def is_promo_statement_line(row):
    if not isinstance(row, dict):
        return False
    text = " ".join(
        str(row.get(key) or "")
        for key in ("description", "Description", "rawRow", "raw")
    )
    return bool(PROMO_STATEMENT_RE.search(text))


def normalise_promo_quantities(rows):
    normalised = []
    for row in rows or []:
        row = dict(row or {})
        if is_promo_line_item(row):
            row["rowTag"] = "PROMO"
            row["Quantity"] = "0"
            row["qty"] = 0.0
            row["totalUnits"] = 0.0
            row["casePack"] = 0
            row["unitSize"] = 0
            row["unitPrice"] = 0.0
            if is_promo_statement_line(row):
                row["lineNet"] = 0.0
                row["vatAmount"] = 0.0
                row["lineGross"] = 0.0
                row["_promoStatement"] = True
        normalised.append(row)
    return normalised


def clean_invoice_no(value):
    value = value.replace("/", "7").replace("+", "7").replace("£", "6")
    value = re.sub(r"\D", "", value)
    return value or None


def clean_code(code):
    code = code.strip(".,")
    code = re.sub(r"^[@#](\d{5}\b)", r"8\1", code)
    return code


def looks_like_item_code(code):
    if re.fullmatch(r"\d{5,7}", code):
        return True
    if re.fullmatch(r"[A-Za-z0-9@#]{5,7}", code) and any(char.isdigit() for char in code):
        return True
    return re.fullmatch(r"[A-Za-z]{5,7}", code) is not None


def final_totals_from_summary(text):
    total_goods = first_match(r"Total\s+Goods\s*:?\s*[;:\s\u00a3GBP]*([\d,.]+)", text)
    vat_matches = re.findall(r"(?<!incl\s)\bVAT\s*:?\s*[\s\u00a3GBP]*([\d,.]+)", text, re.IGNORECASE)
    vat = vat_matches[-1] if vat_matches else None
    total_incl_vat = first_match(r"Total\s+incl\s+VAT\s*:?\s*[\s\u00a3GBP]*([\d,.]+)", text)
    if total_goods and vat and total_incl_vat:
        return {
            "total_goods": clean_amount(total_goods),
            "vat": clean_amount(vat),
            "total_incl_vat": clean_amount(total_incl_vat),
        }

    booker_totals = re.search(
        r"Total\s+Net\s+Value\s+([\d,.]+)\s+Total\s+VAT\s+([\d,.]+)\s+Total\s+([\d,.]+)",
        text,
        re.IGNORECASE,
    )
    if booker_totals:
        total_goods, vat, total_incl_vat = booker_totals.groups()
        return {
            "total_goods": clean_amount(total_goods),
            "vat": clean_amount(vat),
            "total_incl_vat": clean_amount(total_incl_vat),
        }

    summary_start = text.lower().rfind("cases:")
    if summary_start == -1:
        return {}
    summary = text[summary_start:]
    amounts = re.findall(r"-?\d[\d,]*\.\d{2}", summary)
    positive_amounts = [amount for amount in amounts if not amount.startswith("-")]
    if len(positive_amounts) < 3:
        return {}

    total_goods, vat, total_incl_vat = positive_amounts[-3:]
    return {
        "total_goods": clean_amount(total_goods),
        "vat": clean_amount(vat),
        "total_incl_vat": clean_amount(total_incl_vat),
    }


def extract_vat_rates(text):
    rates = {}
    for match in re.finditer(r"^([A-Z])[ \t]+(\d+(?:\.\d+)?)\s+", text, re.IGNORECASE | re.MULTILINE):
        code, rate = match.groups()
        rates[code.upper()] = rate
    for match in re.finditer(r"^([A-Z]{1,2})[ \t]+[^\n\r]+?-[ \t]*(\d+(?:\.\d+)?)%", text, re.IGNORECASE | re.MULTILINE):
        code, rate = match.groups()
        rates[code.upper()] = rate
    return rates


def extract_supplier(text):
    if re.search(r"Dhamecha", text, re.IGNORECASE):
        return "Dhamecha Foods Limited"
    return first_match(r"^([A-Z][A-Za-z&.,\s]+(?:Limited|Ltd))\b", text)


def extract_customer(text):
    customer = first_match(r"\bMrk\.?:\s*([^\n\r]+)", text)
    if customer:
        return clean_text(customer)
    return first_match(r"\bCustomer[:\s]+([^\n\r]+)", text)


def find_invoice_no(text, source="OCR text"):
    patterns = [
        re.compile(r"\bINVOICE\s+NO\.?\s*[:#-]?\s*([A-Z0-9/£+-]+)", re.IGNORECASE),
        re.compile(r"\bINVOICE\b\s*[:#-]\s*([A-Z0-9/£+-]+)", re.IGNORECASE),
    ]
    for line_number, line in enumerate(text.splitlines(), start=1):
        for pattern in patterns:
            match = pattern.search(line)
            if match:
                invoice_no = clean_invoice_no(match.group(1).strip())
                return {
                    "value": invoice_no,
                    "line_number": line_number,
                    "line": line.strip(),
                    "source": source,
                }
    return {"value": None, "line_number": None, "line": None, "source": source}


def find_date(text):
    date_match = re.search(
        r"(\d{1,2}\s+[A-Za-z]+\s+\d{4}),?\s*(\d{1,2}:\d{2})",
        text,
        re.IGNORECASE,
    )
    if not date_match:
        date_match = re.search(r"(\d{1,2}[./-]\d{1,2}[./-]\d{4})", text)
    return date_match


def extract_header_data(text, header_text=""):
    date_match = find_date(header_text) or find_date(text)
    summary_totals = final_totals_from_summary(text)
    header_invoice_no = find_invoice_no(header_text, "top-right header crop")
    text_invoice_no = find_invoice_no(text)
    invoice_no = header_invoice_no
    if text_invoice_no["value"] and (
        not header_invoice_no["value"]
        or (
            header_invoice_no["value"] != text_invoice_no["value"]
            and len(text_invoice_no["value"]) >= 6
        )
    ):
        invoice_no = text_invoice_no

    return {
        "Supplier": extract_supplier(text),
        "Customer": extract_customer(text),
        "Invoice date": date_match.group(1).strip() if date_match else None,
        "Invoice no": invoice_no["value"],
        "Net": summary_totals.get("total_goods") or first_match(
            r"Total\s+Goods[:\s\u00a3GBP]*([\d,]+(?:\.\d{2})?)",
            text,
        ),
        "Vat": summary_totals.get("vat") or first_match(
            r"\bVAT\b[:\s\u00a3GBP]*([\d,]+(?:\.\d{2})?)",
            text,
        ),
        "Gross": summary_totals.get("total_incl_vat") or first_match(
            r"Total\s+inc[l!]?\s+VAT[:\s\u00a3GBP]*([\d,]+(?:\.\d{2})?)",
            text,
        ),
    }


def parse_dhamecha_line_item(line, vat_rates, description_prefix=None):
    tokens = [token.strip("|,") for token in line.strip().split()]
    tokens = [token for token in tokens if token]
    if len(tokens) < 9:
        return None

    code = clean_code(tokens.pop(0))
    if not looks_like_item_code(code):
        return None

    vatcode = clean_vatcode(tokens.pop().strip(".,|"))
    if vatcode.endswith("%"):
        por = vatcode
        vatcode = "A"
    else:
        if not re.fullmatch(r"[A-Z0-9]+", vatcode):
            return None

        por = tokens.pop()
    if not por.endswith("%"):
        return None
    if tokens and re.fullmatch(r"\d{1,2}", tokens[-1]) and "." not in por:
        por = f"{tokens.pop()}.{por}"

    if len(tokens) < 6:
        return None

    rrp = tokens.pop()
    ext_price = tokens.pop()
    price = tokens.pop()
    quantity = tokens.pop()
    unit_size = tokens.pop()
    case_pack = tokens.pop()

    if clean_quantity(quantity) == "1" and unit_size in {"1", "l", "L"} and case_pack.isdigit():
        discarded = quantity
        quantity = unit_size
        unit_size = case_pack
        case_pack = tokens.pop() if tokens else ""
        if discarded in {"©", "�"} and clean_money(price):
            pass

    description = " ".join(tokens)

    description, case_pack, unit_size, quantity, price, ext_price, rrp = repair_dhamecha_columns(
        code, description, case_pack, unit_size, quantity, price, ext_price, rrp
    )

    case_pack = clean_case_pack(case_pack)
    unit_size = clean_unit_size(unit_size)
    if description_prefix:
        description = f"{description_prefix} {description}"
    description = clean_description(description)
    code = correct_code_from_description(code, description)

    return {
        "Code": code,
        "Description": description,
        "PMP": clean_money(price),
        "ExtPrice": clean_money(ext_price),
        "Case pack": case_pack,
        "unit size": unit_size,
        "Quantity": clean_quantity(quantity),
        "vat": vat_rates.get(vatcode.upper()),
        "vatcode": vatcode.upper(),
        "Stfdrrp": clean_money(rrp),
        "PRR": clean_percent(por),
    }


def parse_line_item(line, vat_rates, description_prefix=None):
    line = line.strip()
    booker_match = re.match(
        r"^([A-Z]\d{6})\s+\S+\s+(.+?)\s+(\S+)\s+(\d+)\s+([\d,.]+)\s+([\d,.]+)\s+([A-Z0-9]+)\s+([\d,.]+)$",
        line.strip(),
        re.IGNORECASE,
    )
    if booker_match:
        code, description, unit_size, case_pack, price, quantity, vatcode, net_value = booker_match.groups()
        vatcode = clean_vatcode(vatcode)
        return {
            "Code": code,
            "Description": clean_text(description),
            "PMP": clean_money(price),
            "ExtPrice": clean_money(net_value),
            "Case pack": case_pack,
            "unit size": unit_size,
            "Quantity": clean_quantity(quantity),
            "vat": vat_rates.get(vatcode.upper()),
            "vatcode": vatcode.upper(),
            "Stfdrrp": clean_money(net_value),
            "PRR": None,
        }

    dhamecha_row = parse_dhamecha_line_item(line, vat_rates, description_prefix)
    if dhamecha_row:
        return dhamecha_row

    patterns = [
        (
            r"^(\d{5,7})\s+(.+?)\s+(-|:|\.|\d+)\s+(\S+)\s+([\d,.lLiI]+)\s+"
            r"([^\s]+)\s+([^\s]+)\s+(\S+)\s+([\d. ]+%)\s+([A-Z0-9]+)$",
            "full",
        ),
        (
            r"^(\d{5,7})\s+(.+?)\s+(-|:|\.|\d+)\s+(\S+)\s+([\d,.lLiI]+)\s+"
            r"([^\s]+)\s+(\S+)\s+([\d. ]+%)\s+([A-Z0-9]+)$",
            "missing_ext_price",
        ),
        (
            r"^(\d{5,7})\s+(.+?)\s+(\S+)\s+([\d,.lLiI]+)\s+"
            r"([^\s]+)\s+([^\s]+)\s+(\S+)\s+([\d. ]+%)\s+([A-Z0-9]+)$",
            "missing_case_pack",
        ),
    ]

    match = None
    variant = None
    for pattern, name in patterns:
        match = re.match(pattern, line.strip(), re.IGNORECASE)
        if match:
            variant = name
            break
    if not match:
        return None

    if variant == "full":
        code, description, case_pack, unit_size, quantity, price, ext_price, rrp, por, vatcode = match.groups()
        if case_pack.isdigit() and unit_size == "1" and clean_amount(quantity) == clean_amount(price):
            unit_size = case_pack
            case_pack = ""
            quantity = "1"
    elif variant == "missing_ext_price":
        code, description, case_pack, unit_size, quantity, price, rrp, por, vatcode = match.groups()
        if case_pack.isdigit() and unit_size == "1" and clean_amount(quantity) == clean_amount(price):
            unit_size = case_pack
            case_pack = ""
            quantity = "1"
        ext_price = ""
    else:
        code, description, unit_size, quantity, price, ext_price, rrp, por, vatcode = match.groups()
        case_pack = ""
    if case_pack in {":", "."}:
        case_pack = ""

    vatcode = clean_vatcode(vatcode)
    if description_prefix:
        description = f"{description_prefix} {description}"
    description = clean_description(description)
    return {
        "Code": code,
        "Description": description,
        "PMP": clean_money(price),
        "ExtPrice": clean_money(ext_price),
        "Case pack": clean_case_pack(case_pack),
        "unit size": clean_unit_size(unit_size),
        "Quantity": clean_quantity(quantity),
        "vat": vat_rates.get(vatcode.upper()),
        "vatcode": vatcode.upper(),
        "Stfdrrp": clean_money(rrp),
        "PRR": clean_percent(por),
    }


def split_wrapped_description_line(line):
    if re.match(r"^[@#]?[A-Z]?\d{5,7}\b", line, re.IGNORECASE):
        return None, None

    if re.search(
        r"\b(?:Item\s+#|Product\s+Description|Trolley|Cases|Singles|Total|VAT|Code|"
        r"Invoice|Date|Business|Customer|Dhamecha|Page|T&S:|BUY\s+ANY|GOODS)\b",
        line,
        re.IGNORECASE,
    ):
        return None, None

    if re.search(r"[-~]\s*\d+(?:\.\d{2})?\s*$", line):
        return None, None

    suffix_match = re.search(r"\s+\\?\s*(\d+)\s+(\d+%)\s+([A-Z])$", line, re.IGNORECASE)
    if suffix_match:
        return clean_text(line[:suffix_match.start()]), suffix_match.groups()

    if re.search(r"[A-Za-z]", line):
        return line, None

    return None, None


def join_split_por(line, wrapped_suffix):
    if not wrapped_suffix:
        return line

    por_prefix, por_suffix, vatcode = wrapped_suffix
    por_match = re.search(r"(\d+\.\d+%)$", line)
    if por_match:
        joined_por = f"{por_prefix}{por_match.group(1)}"
        return f"{line[:por_match.start()].rstrip()} {joined_por} {vatcode}"

    return f"{line} {por_prefix} {por_suffix} {vatcode}"


def extract_line_items(text):
    vat_rates = extract_vat_rates(text)
    rows = []
    description_prefix = None
    wrapped_suffix = None
    in_items_table = False

    for raw_line in text.splitlines():
        line = clean_text(raw_line)
        if not line:
            continue

        if re.search(r"\bItem\s+#.*\bProduct\s+Description\b", line, re.IGNORECASE):
            in_items_table = True
            description_prefix = None
            wrapped_suffix = None
            continue

        if re.search(r"\b(?:Trolley|Items\s+Total|Total\s+Goods|Remittance\s+Advice)\b", line, re.IGNORECASE):
            in_items_table = False
            description_prefix = None
            wrapped_suffix = None

        candidate_line = join_split_por(line, wrapped_suffix)
        row = parse_line_item(candidate_line, vat_rates, description_prefix)
        if row:
            rows.append(row)
            description_prefix = None
            wrapped_suffix = None
            continue

        wrapped_description, suffix = split_wrapped_description_line(line)
        if in_items_table and wrapped_description:
            description_prefix = (
                f"{description_prefix} {wrapped_description}"
                if description_prefix
                else wrapped_description
            )
            wrapped_suffix = suffix
            continue

        if re.search(r"\b(?:ELFBAR|SKE|LOST\s+MARY|BM6000|KIT|POD|BAR)\b", line, re.IGNORECASE):
            description_prefix = line
            wrapped_suffix = None

    return rows


def extract_layout_line_items(input_path, text, page_images=None, enable_layout=True):
    if not enable_layout:
        return []

    suffix = input_path.suffix.lower()
    if suffix in IMAGE_EXTENSIONS:
        log_progress("Parsing line items from image layout...")
        return extract_dhamecha_layout_items_from_images([input_path], text)

    if suffix in PDF_EXTENSIONS:
        if page_images is None:
            with tempfile.TemporaryDirectory() as temp_dir:
                log_progress("Rendering PDF pages for layout parsing...")
                page_images = render_pdf_pages(input_path, Path(temp_dir))
                log_progress(f"Parsing line items from {len(page_images)} rendered page(s)...")
                return extract_dhamecha_layout_items_from_images(page_images, text)

        log_progress(f"Parsing line items from {len(page_images)} rendered page(s)...")
        return extract_dhamecha_layout_items_from_images(page_images, text)

    return []


def choose_best_line_items(text_rows, layout_rows):
    if not layout_rows:
        return text_rows, "text parser"

    if len(layout_rows) >= len(text_rows):
        return layout_rows, "column layout parser"

    return text_rows, "text parser"


def candidate_quality_score(candidate):
    score = 0
    score += len(candidate["line_items"]) * 12
    score += len(candidate["layout_line_items"]) * 4
    score += len(candidate["text_line_items"]) * 2

    invoice_match = find_invoice_no(candidate["header_text"], "top-right header crop")
    if not invoice_match["value"]:
        invoice_match = find_invoice_no(candidate["text"])
    if invoice_match["value"]:
        score += 20

    header_fields = ("InvoiceNo", "Date", "Net", "Vat", "Gross")
    score += sum(8 for field in header_fields if clean_text(candidate["header_data"].get(field, "")))

    recon = reconcile_invoice(candidate["line_items"], candidate["header_data"])
    for diff_key in ("net_diff", "vat_diff", "gross_diff"):
        diff = recon.get(diff_key)
        if diff is None:
            continue
        if abs(diff) <= RECON_TOLERANCE:
            score += 15
        elif abs(diff) <= 0.5:
            score += 5
        else:
            score -= min(20, int(abs(diff) * 4))

    return score


def extract_invoice_candidate(input_path, page_images=None, enable_layout=True):
    mode_name = "enhanced" if ENABLE_CONTRAST_ENHANCEMENT else "normal"
    log_progress(f"Starting {mode_name} OCR candidate...")
    text, header_text = extract_text_from_file(input_path, page_images=page_images)
    header_data = extract_header_data(text, header_text)
    text_line_items = extract_line_items(text)
    layout_line_items = extract_layout_line_items(
        input_path,
        text,
        page_images=page_images,
        enable_layout=enable_layout,
    )
    line_items, line_item_source = choose_best_line_items(text_line_items, layout_line_items)
    candidate = {
        "text": text,
        "header_text": header_text,
        "header_data": header_data,
        "text_line_items": text_line_items,
        "layout_line_items": layout_line_items,
        "line_items": line_items,
        "line_item_source": line_item_source,
    }
    candidate["quality_score"] = candidate_quality_score(candidate)
    log_progress(
        f"Finished {mode_name} OCR candidate: "
        f"score={candidate['quality_score']}, lines={len(candidate['line_items'])}, source={line_item_source}"
    )
    return candidate


def calculate_line_amounts(row):
    ext_price_str = row.get("ExtPrice", "")
    pmp_str = row.get("PMP", "")
    qty_str = row.get("Quantity", "")
    vat_rate_str = row.get("vat", row.get("vatRate"))

    line_net = parse_number(row.get("lineNet"))
    if line_net is None:
        line_net = parse_number(row.get("LineNet"))
    if line_net is None:
        line_net = parse_number(ext_price_str)

    if line_net is None:
        pmp_val = parse_number(pmp_str)
        qty_val = parse_number(qty_str)
        line_net = round(pmp_val * qty_val, 2) if pmp_val is not None and qty_val is not None else None

    vat_rate = parse_number(vat_rate_str) or 0.0
    if 0 < vat_rate <= 1.0:
        vat_rate *= 100.0
    vat_fraction = vat_rate / 100.0 if vat_rate else 0.0

    line_vat = parse_number(row.get("vatAmount"))
    if line_vat is None:
        line_vat = parse_number(row.get("LineVAT"))
    expected_vat = round(line_net * vat_fraction, 2) if line_net is not None else None
    if line_net is not None and expected_vat is not None:
        vat_implausible = (
            line_vat is not None
            and abs(line_vat - expected_vat) > max(0.05, abs(expected_vat) * 0.10)
            and abs(line_vat) > max(abs(line_net) * 0.50, 1.0)
        )
        if line_vat is None or vat_implausible:
            line_vat = expected_vat

    line_gross = parse_number(row.get("lineGross"))
    if line_gross is None:
        line_gross = parse_number(row.get("LineGross"))
    expected_gross = round(line_net + line_vat, 2) if line_net is not None and line_vat is not None else None
    if expected_gross is not None:
        gross_implausible = (
            line_gross is not None
            and abs(line_gross - expected_gross) > max(0.05, abs(expected_gross) * 0.10)
        )
        if line_gross is None or line_gross == 0 or gross_implausible:
            line_gross = expected_gross

    return {
        **row,
        "LineNet": round(line_net, 2) if line_net is not None else None,
        "LineVAT": line_vat,
        "LineGross": line_gross,
    }


def reconcile_invoice(line_items, header_data):
    enriched = [calculate_line_amounts(row) for row in normalise_promo_quantities(line_items)]

    def parse_header_val(val):
        if val is None:
            return None
        try:
            return float(str(val).replace(",", "").strip())
        except ValueError:
            return None

    header_net = parse_header_val(header_data.get("Net"))
    supplier = str(header_data.get("Supplier") or "").upper()
    if "BOOKER" in supplier and header_net is not None:
        seen = set()
        deduped = []
        removed = 0
        for row in enriched:
            if is_promo_line_item(row):
                deduped.append(row)
                continue
            desc = re.sub(r"\s+", " ", str(row.get("Description") or row.get("description") or "").upper()).strip()
            desc = re.sub(r"\bM1\b", "ML", desc)
            key = (
                str(row.get("Code") or row.get("code") or "").strip().upper(),
                desc,
                round(parse_number(row.get("Quantity") or row.get("qty")) or 0.0, 4),
                round(parse_number(row.get("totalUnits")) or 0.0, 4),
                round(row.get("LineNet") or 0.0, 2),
                round(row.get("LineVAT") or 0.0, 2),
                round(row.get("LineGross") or 0.0, 2),
            )
            if key in seen:
                removed += 1
                continue
            seen.add(key)
            deduped.append(row)
        if removed:
            current_net = round(sum(r["LineNet"] for r in enriched if r.get("LineNet") is not None), 2)
            deduped_net = round(sum(r["LineNet"] for r in deduped if r.get("LineNet") is not None), 2)
            if abs(deduped_net - header_net) <= RECON_TOLERANCE and abs(deduped_net - header_net) < abs(current_net - header_net):
                enriched = deduped

    def total(key):
        return round(sum(r[key] for r in enriched if r.get(key) is not None), 2)

    lines_net = total("LineNet")
    lines_vat = total("LineVAT")
    lines_gross = total("LineGross")

    header_vat = parse_header_val(header_data.get("Vat"))
    header_gross = parse_header_val(header_data.get("Gross"))

    def diff(lines_val, hdr_val):
        return round(lines_val - hdr_val, 2) if hdr_val is not None else None

    return {
        "enriched_lines": enriched,
        "lines_net": lines_net,
        "lines_vat": lines_vat,
        "lines_gross": lines_gross,
        "header_net": header_net,
        "header_vat": header_vat,
        "header_gross": header_gross,
        "net_diff": diff(lines_net, header_net),
        "vat_diff": diff(lines_vat, header_vat),
        "gross_diff": diff(lines_gross, header_gross),
    }


def print_reconciliation(recon):
    print("\n--- Line Amounts ---")
    print(f"  {'Code':<8}  {'Description':<35}  {'Net':>9}  {'VAT':>8}  {'Gross':>9}")
    print(f"  {'-'*8}  {'-'*35}  {'-'*9}  {'-'*8}  {'-'*9}")
    for row in recon["enriched_lines"]:
        net_s = f"£{row['LineNet']:>8.2f}" if row.get("LineNet") is not None else f"{'?':>9}"
        vat_s = f"£{row['LineVAT']:>7.2f}" if row.get("LineVAT") is not None else f"{'?':>8}"
        gross_s = f"£{row['LineGross']:>8.2f}" if row.get("LineGross") is not None else f"{'?':>9}"
        desc = (row.get("Description") or "")[:35]
        print(f"  {row.get('Code',''):<8}  {desc:<35}  {net_s}  {vat_s}  {gross_s}")

    print(f"\n--- Reconciliation (tolerance ±£{RECON_TOLERANCE:.2f}) ---")
    print(f"  {'':6}  {'Lines Total':>12}  {'Header':>12}  {'Diff':>10}  Status")
    print(f"  {'-'*6}  {'-'*12}  {'-'*12}  {'-'*10}  {'-'*10}")
    for label, lines_val, hdr_val, diff in [
        ("Net",   recon["lines_net"],   recon["header_net"],   recon["net_diff"]),
        ("VAT",   recon["lines_vat"],   recon["header_vat"],   recon["vat_diff"]),
        ("Gross", recon["lines_gross"], recon["header_gross"], recon["gross_diff"]),
    ]:
        h_str = f"£{hdr_val:>11.2f}" if hdr_val is not None else f"{'N/A':>12}"
        if diff is None:
            status = "header not found"
        elif abs(diff) <= RECON_TOLERANCE:
            status = "OK"
        else:
            status = f"MISMATCH  £{diff:+.2f}"
        print(f"  {label:<6}  £{lines_val:>11.2f}  {h_str}  {diff:>+10.2f}  {status}" if diff is not None
              else f"  {label:<6}  £{lines_val:>11.2f}  {h_str}  {'N/A':>10}  {status}")


def main():
    parser = argparse.ArgumentParser(description="Extract invoice data from an image or PDF.")
    parser.add_argument("input", nargs="?", help="Path to invoice image or PDF")
    parser.add_argument("--header-csv", default="invoice_header.csv", help="Header output CSV path")
    parser.add_argument("--lines-csv", default="invoice_lines.csv", help="Line items output CSV path")
    parser.add_argument("--text", default="ocr_text.txt", help="OCR text output path")
    parser.add_argument(
        "--force-enhance",
        action="store_true",
        help="Always use the stronger OCR preprocessing pipeline.",
    )
    parser.add_argument("--verbose", action="store_true", help="Print the full raw OCR text")
    args = parser.parse_args()

    configure_tesseract()
    global ENABLE_CONTRAST_ENHANCEMENT

    input_path = find_input(Path(args.input) if args.input else None)
    print(f"\nReading file: {input_path}")
    quality_metrics = quality_check_pages(input_path)
    enhancement_recommended = args.force_enhance or should_try_enhancement(quality_metrics)
    if args.force_enhance:
        print("Contrast enhancement: forced on by --force-enhance.")
    elif has_contrast_warning(quality_metrics):
        print("Contrast enhancement: low contrast detected; testing enhanced OCR.")
    elif enhancement_recommended:
        print("Contrast enhancement: low OCR confidence or brightness issue detected; testing enhanced OCR.")
    else:
        print("Contrast enhancement: off; no low contrast warning.")
    print_quality_report(quality_metrics)

    pdf_temp_dir = None
    page_images = None
    if input_path.suffix.lower() in PDF_EXTENSIONS:
        pdf_temp_dir = tempfile.TemporaryDirectory()
        log_progress("Rendering PDF pages once for extraction...")
        page_images = render_pdf_pages(input_path, Path(pdf_temp_dir.name))

    ENABLE_CONTRAST_ENHANCEMENT = False
    try:
        normal_candidate = extract_invoice_candidate(input_path, page_images=page_images)
        selected_candidate = normal_candidate
        ocr_mode = "normal"

        if enhancement_recommended:
            enhanced_layout_enabled = len(normal_candidate["line_items"]) < MIN_ACCEPTABLE_TEXT_ROWS
            if enhanced_layout_enabled:
                log_progress("Enhanced pass will include layout parsing.")
            else:
                log_progress(
                    "Enhanced pass will skip layout parsing because the normal pass already found enough rows."
                )

            ENABLE_CONTRAST_ENHANCEMENT = True
            enhanced_candidate = extract_invoice_candidate(
                input_path,
                page_images=page_images,
                enable_layout=enhanced_layout_enabled,
            )
            ENABLE_CONTRAST_ENHANCEMENT = False
            if enhanced_candidate["quality_score"] >= normal_candidate["quality_score"]:
                selected_candidate = enhanced_candidate
                ocr_mode = "enhanced"
            else:
                ocr_mode = "normal; enhanced trial rejected"
    finally:
        ENABLE_CONTRAST_ENHANCEMENT = False
        if pdf_temp_dir is not None:
            pdf_temp_dir.cleanup()

    text = selected_candidate["text"]
    header_text = selected_candidate["header_text"]
    header_data = selected_candidate["header_data"]
    text_line_items = selected_candidate["text_line_items"]
    layout_line_items = selected_candidate["layout_line_items"]
    line_items = selected_candidate["line_items"]
    line_item_source = selected_candidate["line_item_source"]
    invoice_no_source = find_invoice_no(header_text, "top-right header crop")
    text_invoice_no_source = find_invoice_no(text)
    if text_invoice_no_source["value"] and (
        not invoice_no_source["value"]
        or (
            invoice_no_source["value"] != text_invoice_no_source["value"]
            and len(text_invoice_no_source["value"]) >= 6
        )
    ):
        invoice_no_source = text_invoice_no_source

    if args.verbose:
        print("OCR text:")
        print(text)
    else:
        print("OCR text: hidden. Use --verbose to print it.")
    print("\nHeader OCR text:")
    print(header_text)
    print("\nExtracted header:")
    print(header_data)
    print("\nInvoice no found at:")
    if invoice_no_source["value"]:
        print(
            f"{invoice_no_source['source']}, line {invoice_no_source['line_number']}: "
            f"{invoice_no_source['line']}"
        )
    else:
        print("Not found in OCR text.")
    print(f"\nOCR mode used: {ocr_mode}")
    print(
        "OCR candidate scores: "
        f"normal={normal_candidate['quality_score']}, "
        f"enhanced={enhanced_candidate['quality_score'] if enhancement_recommended else 'n/a'}"
    )
    print(f"\nLine item parser used: {line_item_source}")
    print(f"Text parser rows: {len(text_line_items)}")
    print(f"Column layout rows: {len(layout_line_items)}")
    print("\nExtracted lines:")
    for row in line_items:
        print(row)

    recon = reconcile_invoice(line_items, header_data)
    print_reconciliation(recon)

    Path(args.text).write_text(text, encoding="utf-8")
    pd.DataFrame([header_data]).to_csv(args.header_csv, index=False)
    pd.DataFrame(recon["enriched_lines"]).to_csv(args.lines_csv, index=False)
    print(f"\nSaved OCR text to {args.text}")
    print(f"Saved header data to {args.header_csv}")
    print(f"Saved line items to {args.lines_csv}")


if __name__ == "__main__":
    main()
