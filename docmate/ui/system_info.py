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

def render_system_info() -> None:
    _sync_from_legacy()
    st.header("System Information")

    node = st.selectbox(
        "Tree",
        options=["System Overview", "Version History", "Technical PRD", "Event Log"],
        index=0,
    )

    if node == "System Overview":
        st.markdown(
            f"""**App:** {APP_TITLE}  
**Version:** {APP_VERSION} ({APP_DATE})  
**Release note:** {APP_VERSION_NOTE}  

**Built for:** Visual Business Retail Ltd (VBR) — works with VBR EPOS, Cloud Reporting, Loyalty & Online Ordering.
"""
        )
        st.write("Local time:", _uk_now_iso())

    elif node == "Version History":
        versions = _db_get_versions()
        if pd is not None and versions:
            st.dataframe(pd.DataFrame(versions),  use_container_width=True, hide_index=True)
        else:
            st.write(versions)

    elif node == "Technical PRD":
        prd = _db_get_system_doc("PRD")
        if not prd:
            st.info("PRD not found")
            return
        st.subheader(prd.get("title") or "PRD")
        st.markdown(prd.get("content_md") or "")
        if st.session_state.get("is_admin"):
            with st.expander("Edit PRD (Admin)"):
                title = st.text_input("Title", value=prd.get("title") or "")
                content = st.text_area("Content (Markdown)", value=prd.get("content_md") or "", height=300)
                if st.button("Save PRD"):
                    _db_update_system_doc("PRD", title, content)
                    st.success("PRD updated")
                    _log_event("Admin updated PRD")

    elif node == "Event Log":
        st.subheader("Event Log (Today)")
        # Show current UK time at the top so you don’t have to scroll to find “latest”
        try:
            now_uk = _uk_now_iso()
        except Exception:
            now_uk = ""
        lines = _read_event_log(6000)
        if not lines:
            st.info("Event log is empty.")
            return

        # Convert timestamps to UK display and group by processing runs.
        # A "run" starts when we see: "<file> START engine=..."
        entries = []
        for ln in lines:
            m = re.match(r"^\[(.*?)\]\s*(.*)$", ln)
            if m:
                entries.append((_to_uk_ts(m.group(1)), m.group(2)))
            else:
                entries.append(("", ln))

        runs = []
        current = []
        for (uk_ts, rest) in entries:
            if " START engine=" in rest and current:
                runs.append(current)
                current = []
            current.append((uk_ts, rest))
        if current:
            runs.append(current)

        # Newest runs first
        runs = list(reversed(runs))

        st.caption(f"Now (UK): {now_uk}   •   Runs shown: {min(len(runs), 25)} of {len(runs)}   •   Lines: {len(entries)}")

        max_runs = 25
        for idx, run in enumerate(runs[:max_runs], start=1):
            # Divider per run
            st.markdown("---")
            # Use the START line (if present) as the title
            start_line = next((r for r in run if " START engine=" in r[1]), run[0])
            title = start_line[1]
            st.markdown(f"**Run {idx}:** {title}")

            block = "\n".join([f"[{t}] {msg}".rstrip() if t else msg for (t, msg) in run])
            st.code(block, language="text")


# -------------------------
# Main
# -------------------------
