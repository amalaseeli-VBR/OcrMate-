from __future__ import annotations

def _sync_from_legacy() -> None:
    # Pull all names from docmate.legacy into this module's globals so extracted
    # functions can run unchanged.
    from docmate import legacy as L
    g = globals()
    for k in dir(L):
        if k.startswith("__"):
            continue
        # Do not clobber app_main's own symbols (especially `main`), or we can
        # create recursion via legacy.main -> app_main.main wrappers.
        if k in g:
            continue
        g[k] = getattr(L, k)

def main() -> None:
    _sync_from_legacy()
    st.set_page_config(page_title=f"{APP_TITLE} {APP_VERSION}", layout="wide", initial_sidebar_state="collapsed")

    # --- Global UI styling (navigation buttons, etc.) ---
    st.markdown("""<style>
    /* Dashboard / Registers / Reports buttons */
    div.dm-nav-text .stButton > button{
        background:#efe4b8 !important;
        border:1px solid #d9cfa1 !important;
        color:#2b2b2b !important;
        font-weight:700 !important;
        border-radius:10px !important;
        padding:0.55rem 1.15rem !important;
        min-height:44px !important;
    }
    div.dm-nav-text .stButton > button:hover{
        filter:brightness(0.98);
        border-color:#c9bd8f !important;
    }
    /* Admin / Logoff icon buttons */
    div.dm-nav-btns .stButton > button{
        background:#ff3b30 !important;
        border:0 !important;
        border-radius:14px !important;
        min-height:48px !important;
    }
    </style>""", unsafe_allow_html=True)

    _db_init()
    settings = _read_settings()

    # Dedicated login screen (stops until authenticated)
    _login_gate(settings)

    # Session page state
    if "page" not in st.session_state:
        st.session_state["page"] = "Dashboard"

    is_admin = bool(st.session_state.get("is_admin"))
    email = (st.session_state.get("email_address") or "").strip().lower()
    role = "ADMIN" if is_admin else "USER"

    # ---------- Top Bar (Static Header Cards) ----------
    st.markdown(
        """<style>

        /* App scroll container: keep header sticky and scroll content inside main area */
        html, body {height:100%; overflow:hidden;}
        [data-testid="stAppViewContainer"]{height:100vh; overflow:hidden;}
        [data-testid="stMain"], section.main{height:100vh; overflow:auto;}
        /* Make scrollbar visible like your reference UI */
        [data-testid="stMain"]::-webkit-scrollbar {width:14px;}
        [data-testid="stMain"]::-webkit-scrollbar-track {background:#e5e7eb;}
        [data-testid="stMain"]::-webkit-scrollbar-thumb {background:#9ca3af; border-radius:10px; border:3px solid #e5e7eb;}

        /* Static header cards (matches VBR DocMate Extractor style) */
        .dm-static-wrap {position:sticky; top:0; z-index:999; background:#ffffff; padding-top:4px;}
        .dm-card-black {background:#0b0f16; border-radius:14px; padding:14px 16px; border:1px solid rgba(255,255,255,0.10);}
        .dm-title {font-size:30px; font-weight:900; line-height:1; letter-spacing:0.01em;}
        .dm-title .w {color:#ffffff;}
        .dm-title .p {color:#7c3aed;}
        .dm-ver {margin-top:6px; font-size:12px; font-weight:800; color:rgba(255,255,255,0.60); letter-spacing:0.08em;}
        .dm-action-row {display:flex; gap:12px; justify-content:flex-end; align-items:center; flex-wrap:nowrap;}
        button[data-testid="baseButton-primary"]{background:#ef4444 !important;color:#ffffff !important;border:0 !important;border-radius:12px !important;height:56px !important;font-weight:900 !important;font-size:26px !important;box-shadow:0 2px 6px rgba(0,0,0,0.15) !important;}
        button[data-testid="baseButton-secondary"]{background:#efe6c8 !important;color:#1f2a64 !important;border:0 !important;border-radius:12px !important;height:48px !important;font-weight:900 !important;font-size:18px !important;box-shadow:0 1px 4px rgba(0,0,0,0.10) !important;}
        .dm-action-btn {background:#ef4444; border-radius:10px; padding:12px 14px; border:0; color:#ffffff; font-weight:900; font-size:18px; width:100%;}
        .dm-nav-row {display:flex; gap:14px; justify-content:flex-end; margin-top:10px;}
        .dm-nav-btn {background:#efe6c8; border-radius:10px; padding:10px 16px; border:1px solid rgba(0,0,0,0.08); color:#1f2a64; font-weight:900;}
        
        /* Ensure header buttons match reference (red icon tiles + beige nav tiles) */
        .dm-actions .stButton > button {background:#ef4444 !important; color:#ffffff !important; border:0 !important; border-radius:12px !important; height:56px !important; font-weight:900 !important; font-size:26px !important; box-shadow:0 2px 6px rgba(0,0,0,0.15) !important;}
        .dm-nav .stButton > button {background:#efe6c8 !important; color:#1f2a64 !important; border:0 !important; border-radius:12px !important; height:50px !important; font-weight:900 !important; font-size:18px !important; box-shadow:0 1px 4px rgba(0,0,0,0.10) !important;}
</style>""",
        unsafe_allow_html=True,
    )

    with st.container():
        st.markdown("<div class='dm-static-wrap'>", unsafe_allow_html=True)
        c1, c2, c3 = st.columns([2.2, 2.2, 1.8])

        with c1:
            # Header title changes depending on context (Dashboard vs Admin area)
            _p = (st.session_state.get("page") or "Dashboard")
            if _p in ("Admin Settings", "Template Management", "Customer Profiles", "System Information", "Database Management"):
                hdr_w, hdr_p = "Admin", "Settings"
            else:
                hdr_w, hdr_p = "DocMate", "Extractor"
            st.markdown(
                f"""<div class='dm-card-black'>
                    <div class='dm-title'><span class='w'>{hdr_w}</span> <span class='p'>{hdr_p}</span></div>
                    <div class='dm-ver'>VERSION: {APP_VERSION}  |  {_yyyy_mm_dd_to_dd_mm_yyyy(APP_DATE)}</div>
                </div>""",
                unsafe_allow_html=True,
            )
        with c2:
            st.markdown("<div class='dm-card-black'>", unsafe_allow_html=True)
            _render_live_clock_card(settings)
            st.markdown("</div>", unsafe_allow_html=True)
        with c3:
            # Action buttons + nav buttons (kept Streamlit buttons for routing)
            st.markdown("<div class='dm-actions'>", unsafe_allow_html=True)
            ab1, ab2 = st.columns([1,1])
            with ab1:
                if is_admin:
                    if st.button("⚙️", help="Admin settings", use_container_width=True, type="primary", key="btn_admin_settings"):
                        st.session_state["page"] = "Admin Settings"
                        st.rerun()
            with ab2:
                if st.button("⎋", help="Log out", use_container_width=True, type="primary", key="btn_logout"):
                    for k in list(st.session_state.keys()):
                        del st.session_state[k]
                    st.rerun()

            st.markdown("</div>", unsafe_allow_html=True)
            st.markdown("<div class='dm-nav'>", unsafe_allow_html=True)
            nb1, nb2, nb3 = st.columns([1,1,1])
            with nb1:
                if st.button("Dashboard",  use_container_width=True, type="secondary"):
                    st.session_state["page"] = "Dashboard"
                    st.rerun()
            with nb2:
                if st.button("Registers",  use_container_width=True, type="secondary"):
                    st.session_state["page"] = "Registers"
                    st.rerun()
            with nb3:
                if st.button("Reports",  use_container_width=True, type="secondary"):
                    st.session_state["page"] = "Reports"
                    st.rerun()

            st.markdown("</div>", unsafe_allow_html=True)
        st.markdown("</div>", unsafe_allow_html=True)

    st.divider()

    page = st.session_state.get("page", "Dashboard")

    if page == "Dashboard":
        _render_dashboard(settings)
    elif page == "Registers":
        _render_registers()
    elif page == "Reports":
        _render_reports()
    elif page == "Admin Settings":
        _render_admin_settings(settings)
    elif page == "Template Management":
        _render_templates(settings)
    elif page == "Customer Profiles":
        _render_customer_profiles(settings)
    elif page == "System Information":
        _render_system_info()
    elif page == "Database Management":
        _render_database_management()
    else:
        _render_dashboard(settings)


if __name__ == "__main__":

    try:
        main()
    except Exception as exc:
        # Render a safe error screen instead of a blank page
        try:
            import streamlit as st
            st.error("DocMate failed to start. Please see the error details below (also written to EventLOG).")
            st.code(traceback.format_exc())
        except Exception:
            pass
        try:
            _log_event("Startup failure: " + str(exc) + "\n" + traceback.format_exc())
        except Exception:
            pass

