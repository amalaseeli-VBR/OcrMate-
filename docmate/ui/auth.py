from __future__ import annotations

def _sync_from_legacy() -> None:
    # Pull all names from docmate.legacy into this module's globals so extracted
    # functions can run unchanged.
    from docmate import legacy as L
    g = globals()
    for k in dir(L):
        if k.startswith("__"):
            continue
        if k in g:
            continue
        g[k] = getattr(L, k)

def login_gate(settings: Dict[str, Any]) -> None:
    _sync_from_legacy()
    """Dedicated login screen. Stops execution until user is authenticated.

    NOTE: Keep this renderer DOM-safe (avoid unbalanced HTML wrappers) to prevent
    occasional 'blank screen' issues on some Streamlit reruns/browsers.
    """
    import streamlit as st
    # Branding logo (persisted in Admin > Branding). Safe fallback if not configured.
    logo_path = None
    try:
        logo_path = _get_branding_logo_path(settings)
    except Exception:
        logo_path = None


    # Already authenticated (per-session)
    if st.session_state.get("auth_ok", False):
        return

    # ---- Styling (two-panel split, no wrapper divs) ----
    st.markdown(
        """<style>
        /* Split background: left white, right navy */
        .stApp {
            background: linear-gradient(90deg, #ffffff 0%, #ffffff 50%, #2E3A67 50%, #2E3A67 100%) !important;
        }
        /* Remove Streamlit default top padding on the login screen */
        section.main > div.block-container,
        [data-testid="stMainBlockContainer"] {
            padding-top: 0.05rem !important;
            padding-bottom: 0.5rem !important;
            padding-left: 0 !important;
            padding-right: 0 !important;
            max-width: 100% !important;
        }
        /* Hide sidebar, header, footer to keep login clean */
        [data-testid="stSidebar"] { display: none !important; }
        header, [data-testid="stHeader"], footer { display: none !important; height: 0 !important; }

        /* Right panel card */
        .dm-login-card {
            background: transparent;
            border-radius: 0;
            padding: 0;
            box-shadow: none;
            width: 100%;
            max-width: 640px;
            margin-left: auto;
            margin-right: auto;
            text-align: center;
        }
        .dm-login-hero{
            width: 100%;
            display: flex;
            justify-content: center;
            text-align: center;
        }
        .dm-login-title { font-size: 44px; font-weight: 800; color: #ffffff; line-height: 1; text-align: center; margin: 0; }
        .dm-login-brand { font-size: 44px; font-weight: 800; color: #5a2dff; line-height: 1; text-align: center; margin: 0; }
        .dm-login-sub { font-size: 16px; color: rgba(255,255,255,0.65); text-align: center; margin-top: 6px; margin-bottom: 0; }

        /* Inputs */
        /* Labels + placeholders (make readable on dark background) */
        div[data-testid="stTextInput"] label, div[data-testid="stTextInput"] label p {
            color: rgba(255,255,255,0.92) !important;
            font-weight: 700 !important;
        }
        div[data-testid="stTextInput"] > div > div > input {
            color: #1b2449 !important;              /* dark blue typed text */
            background: #efe6b6 !important;         /* pale yellow input background (as design) */
        }
        div[data-testid="stTextInput"] > div > div > input::placeholder {
            color: rgba(27,36,73,0.55) !important;  /* dark blue placeholder */
        }

        div[data-testid="stTextInput"] > div > div > input {
            border-radius: 12px !important;
            border: 2px solid rgba(255,120,170,0.45) !important;
            padding: 14px 16px !important;
            font-size: 18px !important;
        }
        /* Login button */
        div.dm-login-btn button {
            width: 100% !important;
            border-radius: 28px !important;
            padding: 16px 18px !important;
            font-size: 22px !important;
            font-weight: 800 !important;
            background: #E4662E !important;
            color: #ffffff !important;
            border: none !important;
        }
        
        /* Vertical centre wrapper */
        .dm-login-centre {
            width: 100%;
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 0;
        }
        .dm-login-left-panel{
            width: 100%;
            min-height: 5vh;
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: flex-start;
            gap: 10px;
            padding-top: 4px;
            padding-bottom: 10px;
        }
        .dm-login-left-panel [data-testid="stImage"]{
            width: 100%;
            display: flex;
            justify-content: center;
        }
        .dm-login-left-panel [data-testid="stImage"] img{
            display: block;
            margin-left: auto !important;
            margin-right: auto !important;
            width: clamp(320px, 42vw, 560px) !important;
            max-width: 100% !important;
            max-height: 70vh !important;
            height: auto !important;
            transform: none !important;
        }

        .dm-login-right-panel{
            width: 100%;
            min-height: 0vh;
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
        }

        /* Centre inputs/buttons within right login panel */
        .dm-login-right div[data-testid="stTextInput"],
        .dm-login-right div[data-testid="stTextInput"] > div {
            max-width: 640px;
            margin-left: auto !important;
            margin-right: auto !important;
        }
        .dm-login-right div[data-testid="stButton"] {
            max-width: 640px;
            margin-left: auto !important;
            margin-right: auto !important;
        }
</style>""",
        unsafe_allow_html=True,
    )

    # ---- Layout (two equal columns) ----
    left, right = st.columns([1, 1], gap="large")

    # Left: logo (single image - includes 'Powered by' inside the logo), centred
    with left:
        st.markdown("<div class='dm-login-left-panel'>", unsafe_allow_html=True)
        if logo_path:
            try:
                # Single main logo image (user will upload one image that already includes 'Powered by')
                st.image(logo_path)
            except Exception:
                pass
        st.markdown("</div>", unsafe_allow_html=True)

    with right:
        st.markdown("<div class='dm-login-centre dm-login-right'>", unsafe_allow_html=True)

        st.markdown("<div class='dm-login-hero'>", unsafe_allow_html=True)
        st.markdown(
            f"""<div class='dm-login-card'>
                <p class='dm-login-title'>Welcome!</p>
                <p class='dm-login-brand'>DocMate</p>
                <p style='text-align:center; margin:25px 0 0 0; color:rgba(255,255,255,0.55); letter-spacing:0.12em;'>
                    VERSION: {APP_VERSION} | {APP_RELEASE_DATE}
                </p>
                <p class='dm-login-sub'>Login to Your DocMate Register</p>
            </div>""",
            unsafe_allow_html=True,
        )
        st.markdown("</div>", unsafe_allow_html=True)

        st.markdown("<div style='height: 16px'></div>", unsafe_allow_html=True)

        # Credentials
        email = st.text_input("Email Address", value="user@docmate.store", key="dm_login_email")
        password = st.text_input("Password", type="password", key="dm_login_password")
        cms_id_in = st.text_input("CMS Customer ID", placeholder="e.g., C02013", key="dm_login_cms")

        cms_clean = (cms_id_in or "").strip().upper()
        cp_row = None
        cms_ok = False
        if cms_clean:
            try:
                row = _db_fetchone(
                    "SELECT * FROM customer_profiles WHERE UPPER(TRIM(cms_customer_id))=?",
                    (cms_clean,),
                )
                if row:
                    cp_row = dict(row)
                    cms_ok = True
            except Exception:
                cp_row = None
                cms_ok = False

        st.session_state["_login_customer_profile"] = cp_row or {}

        # Support text (white) under CMS Customer ID
        sid_disp = cms_clean if cms_clean else "—"
        ls = (cp_row or {}).get("license_start_date", "") or ""
        le = (cp_row or {}).get("license_end_date", "") or ""
        lic_disp = _licence_period_text(ls, le) if cms_ok else "(licence dates not set)"

        st.markdown(
            "<div style='color:#ffffff; font-weight:700; margin-top:2px;'>"
            "Enter your CMS Customer ID (Support) to enable Login"
            "</div>",
            unsafe_allow_html=True,
        )
        st.markdown(
            f"<div style='color:#ffffff; font-weight:800; margin-top:2px;'>"
            f"Your Support ID {sid_disp} - Licence Period: {lic_disp}"
            "</div>",
            unsafe_allow_html=True,
        )

        # Invalid CMS message (keep visible)
        if cms_clean and (not cms_ok):
            st.error("Invalid CMS Customer ID. Please check the Support ID provided by VBR Support.")

        # Expiry warning badge (only when CMS is valid)
        if cms_ok:
            dl = _licence_days_left(le)
            if dl is not None:
                if dl < 0:
                    st.markdown("<div class='dm-lic-badge dm-lic-expired'>Licence expired</div>", unsafe_allow_html=True)
                elif dl <= 30:
                    st.markdown(f"<div class='dm-lic-badge dm-lic-warn'>Licence expires in {dl} day(s)</div>", unsafe_allow_html=True)

                    st.markdown(f"<div class='dm-lic-badge dm-lic-warn'>Licence expires in {dl} day(s)</div>", unsafe_allow_html=True)

        st.markdown("<div class='dm-login-btn'>", unsafe_allow_html=True)
        do_login = st.button(
            "Login",
            use_container_width=True,
            key="dm_login_btn",
            disabled=(not cms_ok),
        )
        st.markdown("</div>", unsafe_allow_html=True)

        # Auth check
        if do_login:
            ok = _validate_user_login(email, password)
            if ok:
                st.session_state["auth_ok"] = True
                st.session_state["user_email"] = (email or "").strip().lower()
                st.session_state["is_admin"] = _is_admin_user_email(st.session_state["user_email"], settings)
                # Persist CMS profile from login (used for dashboard preselect + register writes)
                cp0 = st.session_state.get("_login_customer_profile") or {}
                if isinstance(cp0, dict) and (cp0.get("cms_customer_id") or ""):
                    st.session_state["login_cms_customer_id"] = str(cp0.get("cms_customer_id") or "").strip().upper()
                    st.session_state["selected_customer_profile"] = cp0
                else:
                    st.session_state["login_cms_customer_id"] = (st.session_state.get("dm_login_cms") or "").strip().upper()
                st.session_state["is_developer"] = _is_developer_user_email(st.session_state["user_email"], settings)
                st.session_state.setdefault("page", "Dashboard")
                st.rerun()
            else:
                st.error("Invalid login. Please check your credentials.")

        st.markdown("</div>", unsafe_allow_html=True)

    # Stop the rest of the app rendering until authenticated
    st.stop()
