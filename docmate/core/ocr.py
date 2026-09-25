from __future__ import annotations
import dataclasses
import io
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

# NOTE: extracted from legacy.py

try:
    import fitz  # PyMuPDF
except Exception:
    fitz = None

try:
    from PIL import Image, ImageOps
except Exception:
    Image = None
    ImageOps = None

# Backward-compatible module globals expected by legacy imports.
_easy_reader = None
_paddle = None


@dataclass
class OcrConfig:
    engine: str = "tesseract"  # tesseract/easyocr/paddleocr
    tesseract_cmd: str = ""
    dpi: int = 250
    max_pages: int = 2
    force_pdf: bool = False
    auto_install: bool = False

    @classmethod
    def from_dict(cls, d: Any) -> "OcrConfig":
        if isinstance(d, cls):
            return d
        if d is None:
            return cls()
        if not isinstance(d, dict):
            try:
                d = dict(d)
            except Exception:
                return cls()

        d2 = dict(d)
        if "max_pages_pdf" in d2 and "max_pages" not in d2:
            d2["max_pages"] = d2.get("max_pages_pdf")

        kwargs: Dict[str, Any] = {}
        for f in dataclasses.fields(cls):
            if f.name in d2 and d2[f.name] is not None:
                kwargs[f.name] = d2[f.name]
        return cls(**kwargs)


def _apply_page_separators(mime_type: str, page_texts: Optional[List[str]] = None) -> List[str]:
    if page_texts is None:
        page_texts = []
    if mime_type == "application/pdf" and page_texts:
        total = len(page_texts)
        return [f"===== Page {i+1}/{total} =====\n{(t or '').strip()}" for i, t in enumerate(page_texts)]
    return [(t or "").strip() for t in page_texts]


def _ensure_import(module_name: str, pip_name: str, auto_install: bool) -> Tuple[bool, str]:
    try:
        __import__(module_name)
        return True, ""
    except Exception as e:
        if not auto_install:
            return False, f"Missing dependency: {module_name}. Install: pip install {pip_name}"
        # best-effort pip install
        try:
            import subprocess, sys

            subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", pip_name])
            __import__(module_name)
            return True, ""
        except Exception as e2:
            return False, f"Failed to install {pip_name}: {e2}"
def _prep_img(img):
    """Lightweight image pre-processing to improve OCR stability.

    Safe for all suppliers (Booker/Parfetts etc.). Keeps behaviour minimal:
    - Correct orientation (EXIF) when available
    - Convert to grayscale
    - Auto-contrast
    - Upscale small pages for better character recognition
    """
    try:
        if img is None:
            return img
        # PIL may be optional; if missing, return as-is
        if Image is None:
            return img
        if ImageOps is not None:
            try:
                img = ImageOps.exif_transpose(img)
            except Exception:
                pass
        # Normalise mode
        try:
            if img.mode not in ("RGB", "L"):
                img = img.convert("RGB")
        except Exception:
            pass
        # Grayscale + contrast
        try:
            img = img.convert("L")
        except Exception:
            pass
        if ImageOps is not None:
            try:
                img = ImageOps.autocontrast(img)
            except Exception:
                pass
        # Upscale small images (helps OCR engines)
        try:
            w, h = img.size
            if max(w, h) < 1600:
                img = img.resize((w * 2, h * 2))
        except Exception:
            pass
        return img
    except Exception:
        return img
def _bytes_to_image(data: bytes):
    """Convert raw bytes to a PIL Image (used for previews)."""
    try:
        from PIL import Image  # pillow is a dependency of pdf2image/image handling
        return Image.open(io.BytesIO(data))
    except Exception:
        return None
def _pdf_text_layer(pdf_bytes: bytes, max_pages: int = 2, progress_cb=None, progress_label: str = "") -> str:
    """Extract embedded PDF text layer with clear page separators.

    Used for fast supplier detection and for the Local OCR / PDF debug text panel.
    """
    if not fitz:
        return ""
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        try:
            total = int(doc.page_count or 0)
        except Exception:
            total = 0
        cap = max(1, int(max_pages or 1))
        captured = min(cap, total) if total > 0 else cap

        parts: List[str] = []
        for i, page in enumerate(doc):
            if i >= cap:
                break
            try:
                page_txt = (page.get_text("text") or "").strip()
            except Exception:
                page_txt = ""

            if callable(progress_cb):
                try:
                    progress_cb(i+1, captured, progress_label)
                except Exception:
                    pass
            parts.append(f"""===== Page {i+1}/{captured} =====
{page_txt}""")
        doc.close()
        return "\n\n".join(parts).strip()
    except Exception:
        return ""
def _pdf_to_images(pdf_bytes: bytes, dpi: int = 250, max_pages: int = 2, progress_cb=None, progress_label: str = "") -> List["Image.Image"]:
    """Render PDF pages to PIL images (for OCR), with optional per-page progress callback."""
    if not fitz or Image is None:
        return []
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        try:
            total_pages = int(doc.page_count or 0)
        except Exception:
            total_pages = 0
        cap = max(1, int(max_pages or 1))
        captured = min(cap, total_pages) if total_pages > 0 else cap

        imgs: List["Image.Image"] = []
        zoom = float(dpi) / 72.0
        mat = fitz.Matrix(zoom, zoom)

        for i, page in enumerate(doc):
            if i >= cap:
                break
            if callable(progress_cb):
                try:
                    progress_cb(i + 1, captured, progress_label)
                except Exception:
                    pass

            pix = page.get_pixmap(matrix=mat, alpha=False)
            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            imgs.append(img)

        doc.close()
        return imgs
    except Exception:
        return []
def ocr_text_cached(cache_key: str, mime_type: str, cfg_dict: Dict[str, Any], file_bytes: bytes, progress_cb=None, progress_label: str = "") -> str:
    """OCR / PDF text extraction.

    Behaviour:
    - For PDFs, always extracts a text-layer hint (for fallback and for fast-path when not forcing raster OCR).
    - When returning multi-page text (PDF text layer or OCR), inserts clear page separators: "Page N/Total".
    """
    cfg = OcrConfig.from_dict(cfg_dict)
    engine = (cfg.engine or "tesseract").lower().strip()

    # Always pre-load PDF text layer for fallback
    txt_hint = ""
    if mime_type == "application/pdf":
        txt_hint = _pdf_text_layer(file_bytes, max_pages=cfg.max_pages, progress_cb=progress_cb, progress_label=progress_label)
        if (not cfg.force_pdf) and txt_hint and len(txt_hint) >= 120:
            return txt_hint

    # Build image list for OCR engines
    if mime_type == "application/pdf":
        try:
            imgs = _pdf_to_images(file_bytes, dpi=cfg.dpi, max_pages=cfg.max_pages, progress_cb=progress_cb, progress_label=progress_label)
        except Exception:
            if txt_hint:
                return txt_hint
            raise
    else:
        imgs = [_bytes_to_image(file_bytes)]

    if engine == "tesseract":
        ok, err = _ensure_import("pytesseract", "pytesseract", cfg.auto_install)
        if not ok:
            raise RuntimeError(err)
        import pytesseract  # type: ignore

        if cfg.tesseract_cmd:
            pytesseract.pytesseract.tesseract_cmd = cfg.tesseract_cmd
        config = "--oem 3 --psm 6 -c preserve_interword_spaces=1"
        parts: List[str] = []
        for img in imgs:
            img = _prep_img(img)
            try:
                parts.append(pytesseract.image_to_string(img, lang="eng", config=config))
            except Exception as e:
                # If the PDF has a text layer, fall back to it rather than hard-failing
                if txt_hint:
                    return txt_hint
                raise RuntimeError(
                    "Tesseract OCR is not available on this machine. Install the Tesseract engine and/or set the full path in Admin → System Information → OCR Settings."
                ) from e
        parts = _apply_page_separators(mime_type, parts)
        return "\n\n".join(parts).strip()

    if engine == "easyocr":
        ok, err = _ensure_import("easyocr", "easyocr", cfg.auto_install)
        if not ok:
            raise RuntimeError(err)
        ok2, err2 = _ensure_import("numpy", "numpy", cfg.auto_install)
        if not ok2:
            raise RuntimeError(err2)
        import easyocr  # type: ignore
        import numpy as np  # type: ignore

        # cache the reader
        if not hasattr(ocr_text_cached, "_easy_reader"):
            ocr_text_cached._easy_reader = easyocr.Reader(["en"], gpu=False)  # type: ignore
        global _easy_reader
        _easy_reader = ocr_text_cached._easy_reader  # type: ignore
        reader = ocr_text_cached._easy_reader  # type: ignore
        parts: List[str] = []
        for img in imgs:
            img = _prep_img(img)
            lines = reader.readtext(np.array(img), detail=0)
            parts.append("\n".join(lines))
        parts = _apply_page_separators(mime_type, parts)
        return "\n\n".join(parts).strip()

    if engine == "paddleocr":
        ok, err = _ensure_import("paddleocr", "paddleocr", cfg.auto_install)
        if not ok:
            raise RuntimeError(err)
        from paddleocr import PaddleOCR  # type: ignore

        if not hasattr(ocr_text_cached, "_paddle"):
            ocr_text_cached._paddle = PaddleOCR(use_angle_cls=True, lang="en", show_log=False)  # type: ignore
        global _paddle
        _paddle = ocr_text_cached._paddle  # type: ignore
        ocr = ocr_text_cached._paddle  # type: ignore
        parts: List[str] = []
        for img in imgs:
            img = _prep_img(img)
            import numpy as np  # type: ignore

            res = ocr.ocr(np.array(img), cls=True)
            lines = []
            for block in res:
                for item in block:
                    lines.append(item[1][0])
            parts.append("\n".join(lines))
        parts = _apply_page_separators(mime_type, parts)
        return "\n\n".join(parts).strip()

    raise RuntimeError(f"Unknown OCR engine: {engine}")


# -------------------------
# Booker parsing (fast / robust)
# -------------------------
