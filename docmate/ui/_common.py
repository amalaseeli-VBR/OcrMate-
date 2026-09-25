"""UI module wrappers.

These wrappers call into legacy.py to keep behaviour identical while the codebase
is gradually decomposed.
"""

from __future__ import annotations

from typing import Any, Callable


def _call_legacy(func_name: str, *args: Any, **kwargs: Any) -> Any:
    from docmate import legacy

    fn: Callable[..., Any] | None = getattr(legacy, func_name, None)
    if fn is None:
        raise AttributeError(
            f"legacy.py has no function '{func_name}'. "
            "If you renamed it, update the wrapper in docmate/ui/*.py"
        )
    return fn(*args, **kwargs)
