"""Streamlit entrypoint (modular wrapper).

Run:
  streamlit run app.py
"""
from .legacy import main

# Streamlit runs the script top-to-bottom, so just call main().
main()
