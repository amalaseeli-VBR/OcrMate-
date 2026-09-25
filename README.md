# DocMate (Modular Folder)

This folder is a **workable, runnable** modular wrapper around your original `docmate_app_v1.07.001_r06.py`.

## What changed
- The original app is preserved as `docmate/legacy.py` (unchanged logic).
- A Streamlit entrypoint is provided at the repo root: `app.py`
- Module stubs/wrappers exist under `docmate/` (`settings.py`, `db.py`, `ocr.py`, etc.) that currently **re-export**
  legacy functions. You can now refactor safely by moving implementations from `legacy.py` into these modules step-by-step.

## Run
```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt

streamlit run app.py
```

## Refactor plan (incremental)
1. Move a small cohesive set of functions (e.g., logging) from `legacy.py` into `docmate/logging_utils.py`.
2. Update imports inside `legacy.py` (or in new code) to import from the new module.
3. Repeat per area: settings → db → exports → reports → ocr → templates → parsers → UI pages.

Tip: Keep `legacy.py` working until the very end; then replace it with thin imports.
