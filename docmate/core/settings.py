from __future__ import annotations
import os, json, base64
from typing import Any, Dict

DATA_DIR='DocMate_DATA'
SETTINGS_FILE=os.path.join(DATA_DIR,'docmate_settings.json')

def _ensure_data_dir()->None:
    os.makedirs(DATA_DIR, exist_ok=True)

def _default_settings() -> Dict[str, Any]:
    # Windows-friendly default Tesseract path (can be overridden in Admin settings)
    default_tess = r"C:\Program Files\Tesseract-OCR\tesseract.exe" if os.name == "nt" else ""
    return {
        "default_engine": "auto",
        "allow_user_choose_engine": True,
        "force_ocr_pdf": False,
        "ocr_dpi": 250,
        "ocr_max_pages": 10,
        "booker_max_pages": 4,
        "parfetts_max_pages": 6,
        "auto_mode_enabled": True,
        "supplier_template_match_threshold": 3,
        "customer_template_match_threshold": 3,
        "supplier_templates_enabled": True,
        "customer_templates_enabled": True,
        "auto_create_booker_template": True,
        # IMPORTANT (Windows): do NOT auto-install OCR engines at runtime by default.
        "auto_install_ocr": False,
        "tesseract_cmd": default_tess,
        # Cloud API key can be stored (optional). For production, store encrypted.
        "google_api_key_b64": "",
        "gemini_model": "gemini-2.0-flash",
        # Anthropic Claude AI fallback parser
        "claude_api_key_b64": "",
        "claude_model": "claude-sonnet-4-6",
        # Branding (stored locally)
        "branding": {
            "app_name": "DocMate Cloud",
            "tagline": "Visualising your business",
            "logo_b64": "",  # base64 PNG (no prefix)
        },
        # Site profile defaults (editable in Admin)
        "site_profile": {
            "site_id": "S1529",
            "trading_name": "Fresher Kingston Food & News",
            "trading_address": "23 Richmond Road, Kingston Upon Thames, KT2 5BW",
            "buyer_aliases": ["FRESHERS KINGSTON"],
            "operating_company": "VBR Fresh Limited",
            "software_company": "Visual Business Retail Ltd",
        },
        "spos_export": {
            "enabled": False,
            "base_url": "",
            "endpoint_pattern": "",
            "auth_method": "",
            "auth_token": "",
        },

    }
def _read_settings() -> Dict[str, Any]:
    _ensure_data_dir()
    cfg = _default_settings()
    try:
        if os.path.exists(SETTINGS_FILE):
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                # shallow merge
                cfg.update(loaded)

                # deep merge for site_profile
                sp = cfg.get("site_profile", {})
                if isinstance(sp, dict):
                    merged = _default_settings()["site_profile"].copy()
                    merged.update(sp)
                    cfg["site_profile"] = merged

                # deep merge for branding
                br = cfg.get("branding", {})
                if isinstance(br, dict):
                    mergedb = _default_settings()["branding"].copy()
                    mergedb.update(br)
                    cfg["branding"] = mergedb
    except Exception:
        pass
    # If user saved empty tesseract_cmd previously, restore a sensible Windows default
    if os.name == "nt" and not (cfg.get("tesseract_cmd") or "").strip():
        cfg["tesseract_cmd"] = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

    return cfg
def _write_settings(cfg: Dict[str, Any]) -> None:
    _ensure_data_dir()
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
    except Exception:
        pass
def _get_google_api_key(settings: Dict[str, Any]) -> str:
    # Prefer environment variable unless explicitly ignored (useful when clearing before handing to a new user).
    if not bool(settings.get('ignore_env_google_api_key', False)):
        k_env = os.environ.get('GOOGLE_API_KEY') or os.environ.get('GEMINI_API_KEY') or os.environ.get('GOOGLE_GEMINI_API_KEY')
        if (k_env or '').strip():
            return str(k_env).strip()
    stored = _get_google_api_key_stored(settings)
    if stored:
        return stored
    return str(settings.get('google_api_key') or '').strip()
def _set_google_api_key(settings: Dict[str, Any], raw_key: str) -> None:
    raw_key = (raw_key or "").strip()
    if not raw_key:
        settings["google_api_key_b64"] = ""
        return
    settings["google_api_key_b64"] = base64.b64encode(raw_key.encode("utf-8")).decode("utf-8")


# -------------------------
# DB
# -------------------------
def _has_env_google_api_key() -> bool:
    k = (os.getenv("GOOGLE_API_KEY") or os.getenv("GOOGLE_API") or "").strip()
    return bool(k)
def _get_google_api_key_stored(settings: Dict[str, Any]) -> str:
    b64 = (settings.get("google_api_key_b64") or "").strip()
    if not b64:
        return ""
    try:
        return base64.b64decode(b64.encode("utf-8")).decode("utf-8").strip()
    except Exception:
        return ""
def _get_claude_api_key(settings: Dict[str, Any]) -> str:
    """Return active Claude API key: env var takes priority unless explicitly ignored."""
    if not bool(settings.get("ignore_env_claude_api_key", False)):
        k_env = os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("CLAUDE_API_KEY") or ""
        if k_env.strip():
            return k_env.strip()
    return _get_claude_api_key_stored(settings)

def _set_claude_api_key(settings: Dict[str, Any], raw_key: str) -> None:
    raw_key = (raw_key or "").strip()
    if not raw_key:
        settings["claude_api_key_b64"] = ""
        return
    settings["claude_api_key_b64"] = base64.b64encode(raw_key.encode("utf-8")).decode("utf-8")

def _get_claude_api_key_stored(settings: Dict[str, Any]) -> str:
    b64 = (settings.get("claude_api_key_b64") or "").strip()
    if not b64:
        return ""
    try:
        return base64.b64decode(b64.encode("utf-8")).decode("utf-8").strip()
    except Exception:
        return ""

def _get_claude_api_key_source(settings: Dict[str, Any]) -> str:
    if not bool(settings.get("ignore_env_claude_api_key", False)):
        k = (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("CLAUDE_API_KEY") or "").strip()
        if k:
            return "env"
    if (settings.get("claude_api_key_b64") or "").strip():
        return "stored"
    return "none"

def _get_google_api_key_source(settings: Dict[str, Any]) -> str:
    if not bool(settings.get('ignore_env_google_api_key', False)) and _has_env_google_api_key():
        return 'env'
    if _get_google_api_key_stored(settings) or (settings.get('google_api_key') or '').strip():
        return 'stored'
    return 'none'
