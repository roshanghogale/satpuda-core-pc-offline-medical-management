"""Sales / purchase history default date scope (current FY vs all data)."""

from __future__ import annotations

import os
import sys
from datetime import date

HISTORY_SCOPE_CURRENT_FY = "current_fy"
HISTORY_SCOPE_ALL = "all"
VALID_HISTORY_SCOPES = frozenset({HISTORY_SCOPE_CURRENT_FY, HISTORY_SCOPE_ALL})
DEFAULT_HISTORY_SCOPE = HISTORY_SCOPE_CURRENT_FY


def _config_path() -> str:
    if getattr(sys, "frozen", False):
        base = os.path.join(
            os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
            "VeterinaryApp",
        )
        os.makedirs(base, exist_ok=True)
        return os.path.join(base, "history_scope.txt")
    return os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "config",
        "history_scope.txt",
    )


def load_history_scope() -> str:
    try:
        from core.billing_layout_prefs import load_billing_layout_prefs

        scope = str(load_billing_layout_prefs().get("history_scope") or "").strip().lower()
        if scope in VALID_HISTORY_SCOPES:
            return scope
    except Exception:
        pass
    try:
        path = _config_path()
        if os.path.exists(path):
            scope = open(path, encoding="utf-8").read().strip().lower()
            if scope in VALID_HISTORY_SCOPES:
                return scope
    except Exception:
        pass
    return DEFAULT_HISTORY_SCOPE


def save_history_scope(scope: str) -> None:
    scope = (scope or "").strip().lower()
    if scope not in VALID_HISTORY_SCOPES:
        scope = DEFAULT_HISTORY_SCOPE
    try:
        from core.billing_layout_prefs import save_billing_layout_prefs

        save_billing_layout_prefs({"history_scope": scope})
        return
    except Exception:
        pass
    path = _config_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(scope)


def history_scope_label(scope: str | None = None) -> str:
    scope = (scope or load_history_scope()).strip().lower()
    if scope == HISTORY_SCOPE_ALL:
        return "All dates"
    return "Current financial year only"


def current_fy_start_year(for_day: date | None = None) -> int:
    from core.fy_serial import fy_start_year_for_date

    return fy_start_year_for_date(for_day or date.today())


def current_fy_label(for_day: date | None = None) -> str:
    from core.fy_serial import fy_label

    return fy_label(current_fy_start_year(for_day))


def current_fy_bounds(for_day: date | None = None) -> tuple[str, str]:
    from core.fy_serial import fy_date_bounds

    return fy_date_bounds(current_fy_start_year(for_day))


def resolve_history_dates(
    from_date: str = "",
    to_date: str = "",
) -> tuple[str, str, bool]:
    """If dates are empty and scope is current FY, return FY bounds."""
    fd = (from_date or "").strip()
    td = (to_date or "").strip()
    if fd or td:
        return fd, td, False
    if load_history_scope() == HISTORY_SCOPE_ALL:
        return "", "", False
    fd, td = current_fy_bounds()
    return fd, td, True
