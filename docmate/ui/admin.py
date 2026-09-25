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



"""Admin Settings UI.

This module contains the extracted Admin page rendering code from legacy.py.
To preserve behavior while legacy is progressively decomposed, we import legacy
and copy its globals into this module's global namespace.
"""

from typing import Any, Dict

import docmate.legacy as _legacy  # type: ignore
from docmate.core.settings import (
    _get_claude_api_key,
    _set_claude_api_key,
    _get_claude_api_key_source,
    _get_claude_api_key_stored,
)

# Bring legacy globals (helpers, imports, constants) into this module so the
# extracted function can run unchanged.
globals().update({k: v for k, v in _legacy.__dict__.items() if k not in globals()})

def render_admin_settings(settings: Dict[str, Any]) -> None:
    # Admin page header is already shown in the static frame above.

    if not st.session_state.get("is_admin"):
        st.warning("Admin settings are Admin-only.")
        return



    # Quick Admin navigation (Admin area is intentionally hidden behind the ⚙️ icon)
    nav1, nav2, nav3, nav4 = st.columns(4)
    with nav1:
        if st.button("Template Management",  use_container_width=True):
            st.session_state["page"] = "Template Management"
            st.rerun()
    with nav2:
        if st.button("Customer Profiles",  use_container_width=True):
            st.session_state["page"] = "Customer Profiles"
            st.rerun()
    with nav3:
        if st.button("System Information",  use_container_width=True):
            st.session_state["page"] = "System Information"
            st.rerun()
    with nav4:
        if st.button("Database Management",  use_container_width=True):
            st.session_state["page"] = "Database Management"
            st.rerun()

    st.divider()

    st.subheader("Processing Performance")
    col1, col2, col3 = st.columns(3)
    engine_choices = ["auto", "local_doctr", "local_tesseract", "azure_di", "cloud_gemini"]
    # Hide Azure DI option if not configured
    if not bool(settings.get("azure_di_enabled", False)) or not (settings.get("azure_di_endpoint") or "").strip() or not (settings.get("azure_di_key_stored") or "").strip():
        engine_choices = [e for e in engine_choices if e != "azure_di"]
    # Hide cloud option if no API key
    if not (_get_google_api_key(settings) or "").strip():
        engine_choices = [e for e in engine_choices if e != "cloud_gemini"]
        # keep at least auto+local
        if "local_tesseract" not in engine_choices:
            engine_choices.append("local_tesseract")
        if "auto" not in engine_choices:
            engine_choices.insert(0,"auto")
    settings["default_engine"] = col1.selectbox(
        "Default engine",
        options=engine_choices,
        index=engine_choices.index(settings.get("default_engine", "auto")) if settings.get("default_engine", "auto") in engine_choices else 0,
    )
    settings["allow_user_choose_engine"] = col2.checkbox("Allow users to choose engine", value=bool(settings.get("allow_user_choose_engine", True)))
    settings["force_ocr_pdf"] = col3.checkbox(
        "Force OCR for PDFs (slower)",
        value=bool(settings.get("force_ocr_pdf", False)),
        help="Recommended OFF to use embedded PDF text layer when available (Booker often works in seconds).",
    )

    col4, col5, col6 = st.columns(3)


    col4a, col5a, col6a = st.columns(3)
    settings["booker_max_pages"] = int(col4a.number_input("Booker max pages", min_value=1, max_value=10, value=int(settings.get("booker_max_pages", 4) or 4)))
    settings["parfetts_max_pages"] = int(col5a.number_input("Parfetts max pages", min_value=1, max_value=15, value=int(settings.get("parfetts_max_pages", 6) or 6)))
    col6a.markdown("**Tip:** Parfetts invoices are often multi-page; set 6 (or more if needed).")

    settings["ocr_dpi"] = int(col4.number_input("OCR DPI", min_value=120, max_value=450, value=int(settings.get("ocr_dpi", 250))))
    if _is_developer():
        settings["ocr_max_pages"] = int(col5.number_input("Max pages", min_value=1, max_value=20, value=int(settings.get("ocr_max_pages", 10))))
    else:
        settings["ocr_max_pages"] = int(settings.get("ocr_max_pages", 10) or 10)
        col5.markdown(f"**Max pages:** {settings['ocr_max_pages']} (Developer only)")
    settings["auto_install_ocr"] = col6.checkbox(
        "Auto-install OCR packages (NOT recommended on Windows)",
        value=bool(settings.get("auto_install_ocr", False)),
        help="Windows often fails due to Torch/DLL dependencies. Prefer installing dependencies manually or use PDF text layer when available.",
    )

    st.subheader("Local OCR Settings")
    settings["tesseract_cmd"] = st.text_input(
        "Tesseract executable path (Windows)",
        value=(settings.get("tesseract_cmd") or ""),
        help=r'Example: C:\Program Files\Tesseract-OCR\tesseract.exe',
    )
    if settings.get("tesseract_cmd"):
        if _tesseract_path_ok(settings.get("tesseract_cmd")):
            st.success("Tesseract path found ✅")
        else:
            st.error("Tesseract path NOT found ❌ — please check the path or reinstall Tesseract.")
    else:
        st.warning("No Tesseract path set. If you force OCR, extraction will fail.")

    st.subheader("Template Management")
    st.caption("Template behaviour and thresholds (used by Auto mode) are managed here. Use Template Management to view/edit supplier routing rules.")
    col7, col8, col9 = st.columns(3)
    settings["auto_mode_enabled"] = col7.checkbox("Enable Auto mode", value=bool(settings.get("auto_mode_enabled", True)))
    settings["auto_create_booker_template"] = col8.checkbox("Auto-propose Booker template (BKL) (admin)", value=bool(settings.get("auto_create_booker_template", False)))
    settings["supplier_template_match_threshold"] = int(col9.number_input("Supplier template match threshold", min_value=1, max_value=10, value=int(settings.get("supplier_template_match_threshold", 3))))
    if st.button("Open Template Management",  use_container_width=True):
        st.session_state["page"] = "Template Management"
        st.rerun()

    st.subheader("Business API License (1) – Google Cloud Gemini")
    st.write("Used for Cloud extraction and template building (recommended for complex suppliers). Each customer should use their own API key (do not use VBR keys).")
    st.caption("Models: Gemini Flash 3.0 (fast) / Gemini Pro 3.0 (higher accuracy).")

    key_source = _get_google_api_key_source(settings)
    stored_key = _get_google_api_key_stored(settings)
    env_key = (
        os.environ.get("GOOGLE_API_KEY")
        or os.environ.get("GEMINI_API_KEY")
        or os.environ.get("GOOGLE_GEMINI_API_KEY")
        or ""
    ).strip()

    effective_key = _get_google_api_key(settings)
    masked_effective = _mask_key(effective_key)

    if bool(settings.get("ignore_env_google_api_key", False)) and env_key:
        st.info("An environment API key is present on this machine, but this DocMate instance is currently set to ignore it.")

    if key_source == "env":
        st.info(f"Gemini API key active (environment) — {masked_effective}.")
    elif key_source == "stored":
        st.info(f"Gemini API key active (stored on this device) — {masked_effective}.")
    else:
        st.warning("No Gemini API key is active. Add a key below to enable Cloud AI.")

    # model selection (safe defaults)
    settings["gemini_model"] = st.selectbox(
        "Gemini model",
        options=["gemini-3.0-flash", "gemini-3.0-pro"],
        index=0 if (settings.get("gemini_model") or "gemini-3.0-flash") == "gemini-3.0-flash" else 1,
        help="Flash is faster; Pro is more accurate for messy invoices.",
    )

    key_input = st.text_input(
        "Google Gemini API Key",
        value="",
        type="password",
        help="Paste to overwrite; stored obfuscated on this device. This field will remain blank after saving.",
    )

    colk1, colk2 = st.columns([1, 1])
    if colk1.button("Update Gemini API Key"):
        if not (key_input or "").strip():
            st.warning("Please paste a valid API key before updating.")
        else:
            settings["ignore_env_google_api_key"] = False
            _set_google_api_key(settings, key_input)
            _write_settings(settings)
            st.success("Gemini API Key stored (hidden)")
            _log_event("Admin updated Gemini API key")
            st.rerun()

    if colk2.button("Clear Gemini API Key"):
        _set_google_api_key(settings, "")
        settings["ignore_env_google_api_key"] = True
        _write_settings(settings)
        if env_key:
            st.warning("Stored key cleared. Note: an environment API key is still present on this machine; DocMate will ignore it.")
        else:
            st.success("Gemini API Key cleared.")
        _log_event("Admin cleared Gemini API key")
        st.rerun()

    st.divider()
    st.subheader("Business API License (2) – Anthropic Claude AI (Universal Fallback)")
    st.write(
        "AI fallback parser: activates automatically for unrecognised suppliers and expense/service invoices. "
        "Extracts header, line items, and VAT breakdown from any invoice format without a custom parser."
    )
    st.caption("Recommended model: claude-sonnet-4-6. Requires an Anthropic API key from console.anthropic.com.")

    _claude_key_source = _get_claude_api_key_source(settings)
    _claude_effective_key = _get_claude_api_key(settings)
    _claude_masked = _mask_key(_claude_effective_key)
    _os_mod = __import__("os")
    _claude_env_key = (_os_mod.environ.get("ANTHROPIC_API_KEY") or _os_mod.environ.get("CLAUDE_API_KEY") or "").strip()

    if _claude_key_source == "env":
        st.info(f"Claude API key active (environment variable) — {_claude_masked}.")
    elif _claude_key_source == "stored":
        st.info(f"Claude API key active (stored on this device) — {_claude_masked}.")
    else:
        st.warning("No Claude API key configured. AI fallback will not activate for unrecognised suppliers.")

    _CLAUDE_MODELS = ["claude-sonnet-4-6", "claude-haiku-4-5-20251001", "claude-opus-4-7"]
    _cur_claude_model = settings.get("claude_model") or "claude-sonnet-4-6"
    settings["claude_model"] = st.selectbox(
        "Claude model",
        options=_CLAUDE_MODELS,
        index=_CLAUDE_MODELS.index(_cur_claude_model) if _cur_claude_model in _CLAUDE_MODELS else 0,
        help="Sonnet: best speed/accuracy balance. Haiku: fastest/cheapest. Opus: highest accuracy for complex invoices.",
    )

    _claude_key_input = st.text_input(
        "Anthropic API Key",
        value="",
        type="password",
        help="Paste to overwrite; stored obfuscated on this device. Field stays blank after saving.",
    )

    _colck1, _colck2 = st.columns([1, 1])
    if _colck1.button("Update Claude API Key"):
        if not (_claude_key_input or "").strip():
            st.warning("Please paste a valid API key before updating.")
        else:
            settings["ignore_env_claude_api_key"] = False
            _set_claude_api_key(settings, _claude_key_input)
            _write_settings(settings)
            st.success("Claude API Key stored (hidden)")
            _log_event("Admin updated Claude API key")
            st.rerun()

    if _colck2.button("Clear Claude API Key"):
        _set_claude_api_key(settings, "")
        settings["ignore_env_claude_api_key"] = True
        _write_settings(settings)
        if _claude_env_key:
            st.warning("Stored key cleared. Note: an environment API key is still present; DocMate will ignore it.")
        else:
            st.success("Claude API Key cleared.")
        _log_event("Admin cleared Claude API key")
        st.rerun()

    st.divider()
    st.subheader("Business API License (3) – Google Document AI")
    st.caption("Optional structured extraction tier. Configure only if customer uses Google Document AI processors.")
    settings["gdocai_enabled"] = st.checkbox("Enable Google Document AI", value=bool(settings.get("gdocai_enabled", False)))
    settings["gdocai_project_id"] = st.text_input("Google Project ID", value=str(settings.get("gdocai_project_id","") or ""))
    settings["gdocai_location"] = st.text_input("Location (e.g., eu, us)", value=str(settings.get("gdocai_location","") or ""))
    settings["gdocai_processor_id"] = st.text_input("Processor ID", value=str(settings.get("gdocai_processor_id","") or ""))
    settings["gdocai_credentials_json"] = st.text_area("Service Account JSON (optional)", value=str(settings.get("gdocai_credentials_json","") or ""), height=120)
    csave1, csave2 = st.columns([1, 3])
    with csave1:
        if st.button("Save Document AI settings", key="save_gdocai_settings",  use_container_width=True):
            try:
                _write_settings(settings)
                st.success("Google Document AI settings saved.")
                st.rerun()
            except Exception as _e:
                st.error(f"Failed to save settings: {_e}")



    st.divider()
    st.subheader("Business API License (3) – Microsoft Azure Document Intelligence")
    st.write("Structured OCR tier between Local OCR and Cloud LLM. Recommended for Booker/Parfetts/Dhamecha where tables/fields help stabilise extraction for VBR reconciliation.")
    settings["azure_di_enabled"] = st.checkbox("Enable Azure Document Intelligence", value=bool(settings.get("azure_di_enabled", False)))
    settings["azure_di_endpoint"] = st.text_input("Azure DI Endpoint", value=str(settings.get("azure_di_endpoint","") or ""), help="Example: https://<resource-name>.cognitiveservices.azure.com/")
    st.session_state["_tmp_az_key"] = st.text_input("Azure DI Key", value="", type="password", help="Paste to update; stored obfuscated on this device. Field stays blank after saving.")
    if (st.session_state.get("_tmp_az_key") or "").strip():
        settings["azure_di_key_stored"] = _obfuscate_key(st.session_state.get("_tmp_az_key"))
    settings["azure_di_model"] = st.selectbox("Azure DI model", options=["prebuilt-invoice", "prebuilt-document", "layout"], index=["prebuilt-invoice","prebuilt-document","layout"].index(str(settings.get("azure_di_model","prebuilt-invoice") or "prebuilt-invoice")))
    settings["azure_di_api_version"] = st.text_input("API version", value=str(settings.get("azure_di_api_version","2024-11-30") or "2024-11-30"))

    colaz1, colaz2 = st.columns([1,1])
    if colaz1.button("Update Azure DI Key"):
        if not (st.session_state.get("_tmp_az_key") or "").strip() and not (settings.get("azure_di_key_stored") or "").strip():
            # We'll accept update via the field itself below in the save path
            pass
    if colaz2.button("Clear Azure DI Key"):
        settings["azure_di_key_stored"] = ""
        _write_settings(settings)
        st.success("Azure DI Key cleared.")
        _log_event("Admin cleared Azure DI key")
        st.rerun()

    st.subheader("Branding")
    branding = settings.get("branding", {}) if isinstance(settings.get("branding"), dict) else {}
    app_name = st.text_input("App name", value=branding.get("app_name", "DocMate Cloud"))
    tagline = st.text_input("Tagline", value=branding.get("tagline", "Visualising your business"))
    logo_bytes = _decode_logo_bytes(branding.get("logo_b64", ""))
    if logo_bytes:
        st.image(logo_bytes, width=140, caption="Current logo")
    up_logo = st.file_uploader("Upload main logo (PNG/JPG)", type=["png","jpg","jpeg"], help="Upload your main DocMate logo (recommended 800px wide). This logo is shown on the Login screen (left) and in headers where applicable.")
    clear_logo = st.checkbox("Clear logo", value=False)
    if st.button("Save Branding"):
        branding["app_name"] = app_name
        branding["tagline"] = tagline
        if clear_logo:
            branding["logo_b64"] = ""
        elif up_logo is not None:
            branding["logo_b64"] = base64.b64encode(up_logo.getvalue()).decode("utf-8")
        settings["branding"] = branding
        _write_settings(settings)
        st.success("Branding saved")
        _log_event("Admin updated branding")
        st.rerun()

# -------------------------
# UI: System Information
# -------------------------


    st.divider()
    st.subheader("Time & Zone")
    st.caption("Configure the live system clock shown on the header (Region + Time Zone). Optional NTP improves accuracy across servers. Default is UK time.")
    tz_cfg = _get_time_zone_settings(settings)
    region = st.selectbox("Region", options=list(_REGION_TIMEZONES.keys()), index=list(_REGION_TIMEZONES.keys()).index(tz_cfg["region"]) if tz_cfg["region"] in _REGION_TIMEZONES else 0, key="tz_region")
    tz_opts = _REGION_TIMEZONES.get(region) or _REGION_TIMEZONES["Europe"]
    tz_labels = [x[0] for x in tz_opts]
    tz_map = {x[0]: x[1] for x in tz_opts}
    # pick current label if possible
    current_label = None
    for lbl, tzname in tz_opts:
        if tzname == tz_cfg["tz_name"]:
            current_label = lbl
            break
    if current_label is None:
        current_label = tz_labels[0]
    sel_label = st.selectbox("Time Zone", options=tz_labels, index=tz_labels.index(current_label), key="tz_name_label")
    use_ntp = st.checkbox("Use NTP (recommended for consistent UK time across deployments)", value=bool(tz_cfg.get("use_ntp")), key="tz_use_ntp")
    ntp_server = st.text_input("NTP server", value=str(tz_cfg.get("ntp_server") or "pool.ntp.org"), disabled=not use_ntp, key="tz_ntp_server")
    if st.button("Save Time & Zone"):
        settings["time_zone"] = {
            "region": region,
            "tz_name": tz_map.get(sel_label, UK_TZ),
            "use_ntp": bool(use_ntp),
            "ntp_server": (ntp_server or "pool.ntp.org").strip(),
        }
        _write_settings(settings)
        st.success("Time & Zone saved. The header clock will update automatically.")

def render_admin_settings(settings: Dict[str, Any]) -> None:
    _sync_from_legacy()
    # Admin page header is already shown in the static frame above.

    if not st.session_state.get("is_admin"):
        st.warning("Admin settings are Admin-only.")
        return



    # Quick Admin navigation (Admin area is intentionally hidden behind the ⚙️ icon)
    nav1, nav2, nav3, nav4 = st.columns(4)
    with nav1:
        if st.button("Template Management",  use_container_width=True):
            st.session_state["page"] = "Template Management"
            st.rerun()
    with nav2:
        if st.button("Customer Profiles",  use_container_width=True):
            st.session_state["page"] = "Customer Profiles"
            st.rerun()
    with nav3:
        if st.button("System Information",  use_container_width=True):
            st.session_state["page"] = "System Information"
            st.rerun()
    with nav4:
        if st.button("Database Management",  use_container_width=True):
            st.session_state["page"] = "Database Management"
            st.rerun()

    st.divider()

    st.subheader("Processing Performance")
    col1, col2, col3 = st.columns(3)
    engine_choices = ["auto", "local_doctr", "local_tesseract", "azure_di", "cloud_gemini"]
    # Hide Azure DI option if not configured
    if not bool(settings.get("azure_di_enabled", False)) or not (settings.get("azure_di_endpoint") or "").strip() or not (settings.get("azure_di_key_stored") or "").strip():
        engine_choices = [e for e in engine_choices if e != "azure_di"]
    # Hide cloud option if no API key
    if not (_get_google_api_key(settings) or "").strip():
        engine_choices = [e for e in engine_choices if e != "cloud_gemini"]
        # keep at least auto+local
        if "local_tesseract" not in engine_choices:
            engine_choices.append("local_tesseract")
        if "auto" not in engine_choices:
            engine_choices.insert(0,"auto")
    settings["default_engine"] = col1.selectbox(
        "Default engine",
        options=engine_choices,
        index=engine_choices.index(settings.get("default_engine", "auto")) if settings.get("default_engine", "auto") in engine_choices else 0,
    )
    settings["allow_user_choose_engine"] = col2.checkbox("Allow users to choose engine", value=bool(settings.get("allow_user_choose_engine", True)))
    settings["force_ocr_pdf"] = col3.checkbox(
        "Force OCR for PDFs (slower)",
        value=bool(settings.get("force_ocr_pdf", False)),
        help="Recommended OFF to use embedded PDF text layer when available (Booker often works in seconds).",
    )

    col4, col5, col6 = st.columns(3)


    col4a, col5a, col6a = st.columns(3)
    settings["booker_max_pages"] = int(col4a.number_input("Booker max pages", min_value=1, max_value=10, value=int(settings.get("booker_max_pages", 4) or 4)))
    settings["parfetts_max_pages"] = int(col5a.number_input("Parfetts max pages", min_value=1, max_value=15, value=int(settings.get("parfetts_max_pages", 6) or 6)))
    col6a.markdown("**Tip:** Parfetts invoices are often multi-page; set 6 (or more if needed).")

    settings["ocr_dpi"] = int(col4.number_input("OCR DPI", min_value=120, max_value=450, value=int(settings.get("ocr_dpi", 250))))
    if _is_developer():
        settings["ocr_max_pages"] = int(col5.number_input("Max pages", min_value=1, max_value=20, value=int(settings.get("ocr_max_pages", 10))))
    else:
        settings["ocr_max_pages"] = int(settings.get("ocr_max_pages", 10) or 10)
        col5.markdown(f"**Max pages:** {settings['ocr_max_pages']} (Developer only)")
    settings["auto_install_ocr"] = col6.checkbox(
        "Auto-install OCR packages (NOT recommended on Windows)",
        value=bool(settings.get("auto_install_ocr", False)),
        help="Windows often fails due to Torch/DLL dependencies. Prefer installing dependencies manually or use PDF text layer when available.",
    )

    st.subheader("Local OCR Settings")
    settings["tesseract_cmd"] = st.text_input(
        "Tesseract executable path (Windows)",
        value=(settings.get("tesseract_cmd") or ""),
        help=r'Example: C:\Program Files\Tesseract-OCR\tesseract.exe',
    )
    if settings.get("tesseract_cmd"):
        if _tesseract_path_ok(settings.get("tesseract_cmd")):
            st.success("Tesseract path found ✅")
        else:
            st.error("Tesseract path NOT found ❌ — please check the path or reinstall Tesseract.")
    else:
        st.warning("No Tesseract path set. If you force OCR, extraction will fail.")

    st.subheader("Template Management")
    st.caption("Template behaviour and thresholds (used by Auto mode) are managed here. Use Template Management to view/edit supplier routing rules.")
    col7, col8, col9 = st.columns(3)
    settings["auto_mode_enabled"] = col7.checkbox("Enable Auto mode", value=bool(settings.get("auto_mode_enabled", True)))
    settings["auto_create_booker_template"] = col8.checkbox("Auto-propose Booker template (BKL) (admin)", value=bool(settings.get("auto_create_booker_template", False)))
    settings["supplier_template_match_threshold"] = int(col9.number_input("Supplier template match threshold", min_value=1, max_value=10, value=int(settings.get("supplier_template_match_threshold", 3))))
    if st.button("Open Template Management",  use_container_width=True):
        st.session_state["page"] = "Template Management"
        st.rerun()

    st.subheader("Business API License (1) – Google Cloud Gemini")
    st.write("Used for Cloud extraction and template building (recommended for complex suppliers). Each customer should use their own API key (do not use VBR keys).")
    st.caption("Models: Gemini Flash 3.0 (fast) / Gemini Pro 3.0 (higher accuracy).")

    key_source = _get_google_api_key_source(settings)
    stored_key = _get_google_api_key_stored(settings)
    env_key = (
        os.environ.get("GOOGLE_API_KEY")
        or os.environ.get("GEMINI_API_KEY")
        or os.environ.get("GOOGLE_GEMINI_API_KEY")
        or ""
    ).strip()

    effective_key = _get_google_api_key(settings)
    masked_effective = _mask_key(effective_key)

    if bool(settings.get("ignore_env_google_api_key", False)) and env_key:
        st.info("An environment API key is present on this machine, but this DocMate instance is currently set to ignore it.")

    if key_source == "env":
        st.info(f"Gemini API key active (environment) — {masked_effective}.")
    elif key_source == "stored":
        st.info(f"Gemini API key active (stored on this device) — {masked_effective}.")
    else:
        st.warning("No Gemini API key is active. Add a key below to enable Cloud AI.")

    # model selection (safe defaults)
    settings["gemini_model"] = st.selectbox(
        "Gemini model",
        options=["gemini-3.0-flash", "gemini-3.0-pro"],
        index=0 if (settings.get("gemini_model") or "gemini-3.0-flash") == "gemini-3.0-flash" else 1,
        help="Flash is faster; Pro is more accurate for messy invoices.",
    )

    key_input = st.text_input(
        "Google Gemini API Key",
        value="",
        type="password",
        help="Paste to overwrite; stored obfuscated on this device. This field will remain blank after saving.",
    )

    colk1, colk2 = st.columns([1, 1])
    if colk1.button("Update Gemini API Key"):
        if not (key_input or "").strip():
            st.warning("Please paste a valid API key before updating.")
        else:
            settings["ignore_env_google_api_key"] = False
            _set_google_api_key(settings, key_input)
            _write_settings(settings)
            st.success("Gemini API Key stored (hidden)")
            _log_event("Admin updated Gemini API key")
            st.rerun()

    if colk2.button("Clear Gemini API Key"):
        _set_google_api_key(settings, "")
        settings["ignore_env_google_api_key"] = True
        _write_settings(settings)
        if env_key:
            st.warning("Stored key cleared. Note: an environment API key is still present on this machine; DocMate will ignore it.")
        else:
            st.success("Gemini API Key cleared.")
        _log_event("Admin cleared Gemini API key")
        st.rerun()

    st.divider()
    st.subheader("Business API License (2) – Anthropic Claude AI (Universal Fallback)")
    st.write(
        "AI fallback parser: activates automatically for unrecognised suppliers and expense/service invoices. "
        "Extracts header, line items, and VAT breakdown from any invoice format without a custom parser."
    )
    st.caption("Recommended model: claude-sonnet-4-6. Requires an Anthropic API key from console.anthropic.com.")

    _claude_key_source = _get_claude_api_key_source(settings)
    _claude_effective_key = _get_claude_api_key(settings)
    _claude_masked = _mask_key(_claude_effective_key)
    _os_mod = __import__("os")
    _claude_env_key = (_os_mod.environ.get("ANTHROPIC_API_KEY") or _os_mod.environ.get("CLAUDE_API_KEY") or "").strip()

    if _claude_key_source == "env":
        st.info(f"Claude API key active (environment variable) — {_claude_masked}.")
    elif _claude_key_source == "stored":
        st.info(f"Claude API key active (stored on this device) — {_claude_masked}.")
    else:
        st.warning("No Claude API key configured. AI fallback will not activate for unrecognised suppliers.")

    _CLAUDE_MODELS = ["claude-sonnet-4-6", "claude-haiku-4-5-20251001", "claude-opus-4-7"]
    _cur_claude_model = settings.get("claude_model") or "claude-sonnet-4-6"
    settings["claude_model"] = st.selectbox(
        "Claude model",
        options=_CLAUDE_MODELS,
        index=_CLAUDE_MODELS.index(_cur_claude_model) if _cur_claude_model in _CLAUDE_MODELS else 0,
        help="Sonnet: best speed/accuracy balance. Haiku: fastest/cheapest. Opus: highest accuracy for complex invoices.",
    )

    _claude_key_input = st.text_input(
        "Anthropic API Key",
        value="",
        type="password",
        help="Paste to overwrite; stored obfuscated on this device. Field stays blank after saving.",
    )

    _colck1, _colck2 = st.columns([1, 1])
    if _colck1.button("Update Claude API Key"):
        if not (_claude_key_input or "").strip():
            st.warning("Please paste a valid API key before updating.")
        else:
            settings["ignore_env_claude_api_key"] = False
            _set_claude_api_key(settings, _claude_key_input)
            _write_settings(settings)
            st.success("Claude API Key stored (hidden)")
            _log_event("Admin updated Claude API key")
            st.rerun()

    if _colck2.button("Clear Claude API Key"):
        _set_claude_api_key(settings, "")
        settings["ignore_env_claude_api_key"] = True
        _write_settings(settings)
        if _claude_env_key:
            st.warning("Stored key cleared. Note: an environment API key is still present; DocMate will ignore it.")
        else:
            st.success("Claude API Key cleared.")
        _log_event("Admin cleared Claude API key")
        st.rerun()

    st.divider()
    st.subheader("Business API License (3) – Google Document AI")
    st.caption("Optional structured extraction tier. Configure only if customer uses Google Document AI processors.")
    settings["gdocai_enabled"] = st.checkbox("Enable Google Document AI", value=bool(settings.get("gdocai_enabled", False)))
    settings["gdocai_project_id"] = st.text_input("Google Project ID", value=str(settings.get("gdocai_project_id","") or ""))
    settings["gdocai_location"] = st.text_input("Location (e.g., eu, us)", value=str(settings.get("gdocai_location","") or ""))
    settings["gdocai_processor_id"] = st.text_input("Processor ID", value=str(settings.get("gdocai_processor_id","") or ""))
    settings["gdocai_credentials_json"] = st.text_area("Service Account JSON (optional)", value=str(settings.get("gdocai_credentials_json","") or ""), height=120)
    csave1, csave2 = st.columns([1, 3])
    with csave1:
        if st.button("Save Document AI settings", key="save_gdocai_settings",  use_container_width=True):
            try:
                _save_settings(settings)
                st.success("Google Document AI settings saved.")
                st.rerun()
            except Exception as _e:
                st.error(f"Failed to save settings: {_e}")



    st.divider()
    st.subheader("Business API License (3) – Microsoft Azure Document Intelligence")
    st.write("Structured OCR tier between Local OCR and Cloud LLM. Recommended for Booker/Parfetts/Dhamecha where tables/fields help stabilise extraction for VBR reconciliation.")
    settings["azure_di_enabled"] = st.checkbox("Enable Azure Document Intelligence", value=bool(settings.get("azure_di_enabled", False)))
    settings["azure_di_endpoint"] = st.text_input("Azure DI Endpoint", value=str(settings.get("azure_di_endpoint","") or ""), help="Example: https://<resource-name>.cognitiveservices.azure.com/")
    st.session_state["_tmp_az_key"] = st.text_input("Azure DI Key", value="", type="password", help="Paste to update; stored obfuscated on this device. Field stays blank after saving.")
    if (st.session_state.get("_tmp_az_key") or "").strip():
        settings["azure_di_key_stored"] = _obfuscate_key(st.session_state.get("_tmp_az_key"))
    settings["azure_di_model"] = st.selectbox("Azure DI model", options=["prebuilt-invoice", "prebuilt-document", "layout"], index=["prebuilt-invoice","prebuilt-document","layout"].index(str(settings.get("azure_di_model","prebuilt-invoice") or "prebuilt-invoice")))
    settings["azure_di_api_version"] = st.text_input("API version", value=str(settings.get("azure_di_api_version","2024-11-30") or "2024-11-30"))

    colaz1, colaz2 = st.columns([1,1])
    if colaz1.button("Update Azure DI Key"):
        if not (st.session_state.get("_tmp_az_key") or "").strip() and not (settings.get("azure_di_key_stored") or "").strip():
            # We'll accept update via the field itself below in the save path
            pass
    if colaz2.button("Clear Azure DI Key"):
        settings["azure_di_key_stored"] = ""
        _write_settings(settings)
        st.success("Azure DI Key cleared.")
        _log_event("Admin cleared Azure DI key")
        st.rerun()

    st.subheader("Branding")
    branding = settings.get("branding", {}) if isinstance(settings.get("branding"), dict) else {}
    app_name = st.text_input("App name", value=branding.get("app_name", "DocMate Cloud"))
    tagline = st.text_input("Tagline", value=branding.get("tagline", "Visualising your business"))
    logo_bytes = _decode_logo_bytes(branding.get("logo_b64", ""))
    if logo_bytes:
        st.image(logo_bytes, width=140, caption="Current logo")
    up_logo = st.file_uploader("Upload main logo (PNG/JPG)", type=["png","jpg","jpeg"], help="Upload your main DocMate logo (recommended 800px wide). This logo is shown on the Login screen (left) and in headers where applicable.")
    clear_logo = st.checkbox("Clear logo", value=False)
    if st.button("Save Branding"):
        branding["app_name"] = app_name
        branding["tagline"] = tagline
        if clear_logo:
            branding["logo_b64"] = ""
        elif up_logo is not None:
            branding["logo_b64"] = base64.b64encode(up_logo.getvalue()).decode("utf-8")
        settings["branding"] = branding
        _write_settings(settings)
        st.success("Branding saved")
        _log_event("Admin updated branding")
        st.rerun()

# -------------------------
# UI: System Information
# -------------------------


    st.divider()
    st.subheader("Time & Zone")
    st.caption("Configure the live system clock shown on the header (Region + Time Zone). Optional NTP improves accuracy across servers. Default is UK time.")
    tz_cfg = _get_time_zone_settings(settings)
    region = st.selectbox("Region", options=list(_REGION_TIMEZONES.keys()), index=list(_REGION_TIMEZONES.keys()).index(tz_cfg["region"]) if tz_cfg["region"] in _REGION_TIMEZONES else 0, key="tz_region")
    tz_opts = _REGION_TIMEZONES.get(region) or _REGION_TIMEZONES["Europe"]
    tz_labels = [x[0] for x in tz_opts]
    tz_map = {x[0]: x[1] for x in tz_opts}
    # pick current label if possible
    current_label = None
    for lbl, tzname in tz_opts:
        if tzname == tz_cfg["tz_name"]:
            current_label = lbl
            break
    if current_label is None:
        current_label = tz_labels[0]
    sel_label = st.selectbox("Time Zone", options=tz_labels, index=tz_labels.index(current_label), key="tz_name_label")
    use_ntp = st.checkbox("Use NTP (recommended for consistent UK time across deployments)", value=bool(tz_cfg.get("use_ntp")), key="tz_use_ntp")
    ntp_server = st.text_input("NTP server", value=str(tz_cfg.get("ntp_server") or "pool.ntp.org"), disabled=not use_ntp, key="tz_ntp_server")
    if st.button("Save Time & Zone"):
        settings["time_zone"] = {
            "region": region,
            "tz_name": tz_map.get(sel_label, UK_TZ),
            "use_ntp": bool(use_ntp),
            "ntp_server": (ntp_server or "pool.ntp.org").strip(),
        }
        _write_settings(settings)
        st.success("Time & Zone saved. The header clock will update automatically.")

