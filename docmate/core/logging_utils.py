from __future__ import annotations
import os
from datetime import datetime, timezone
from typing import List

DATA_DIR='DocMate_DATA'
EVENT_LOG_FILE=os.path.join(DATA_DIR,'EventLOG_' + datetime.now().strftime('%Y-%m-%d') + '.txt')

def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def _ensure_data_dir()->None:
    os.makedirs(DATA_DIR, exist_ok=True)

def _log_event(message: str, file_name: str = "") -> None:
    _ensure_data_dir()
    ts = _utc_now_iso()
    ver = str(globals().get("APP_VERSION") or "DocMate")
    # Include version in every line to simplify support/audit across VBR deployments
    if file_name:
        line = f"[{ts}] {ver} {file_name} {message}"
    else:
        line = f"[{ts}] {ver} {message}"

    # Primary log location (DocMate data folder)
    try:
        with open(EVENT_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line.rstrip() + "\n")
        return
    except Exception:
        pass

    # Fallback: current working directory (helps when users look next to the script)
    try:
        with open("EventLOG.txt", "a", encoding="utf-8") as f:
            f.write(line.rstrip() + "\n")
    except Exception:
        pass
def _read_event_log(max_lines: int = 3000) -> List[str]:
    try:
        if not os.path.exists(EVENT_LOG_FILE):
            return []
        with open(EVENT_LOG_FILE, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.read().splitlines()
        if len(lines) > max_lines:
            lines = lines[-max_lines:]
        return lines
    except Exception:
        return []


# -------------------------
# Settings
# -------------------------
