"""DocMate package.

This repo is mid-refactor: legacy.py contains the original monolith.
New packages (ui/, core/, parsers/) provide stable import locations.
"""

__all__ = [
    "app",
    "legacy",
    "ui",
    "core",
    "parsers",
]
