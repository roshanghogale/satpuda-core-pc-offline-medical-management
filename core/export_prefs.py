"""Default export file format — configured in Settings → Export Data."""

from __future__ import annotations

import json
import os
import sys

VALID_EXPORT_FORMATS = frozenset({"csv", "xlsx", "pdf"})
DEFAULT_EXPORT_FORMAT = "csv"
SCHEDULE_REPORT_LAYOUTS = frozenset({"portrait", "landscape", "styled"})
SCHEDULE_LAYOUT_PORTRAIT = "portrait"
SCHEDULE_LAYOUT_LANDSCAPE = "landscape"
SCHEDULE_LAYOUT_STYLED = "styled"
DEFAULT_SCHEDULE_REPORT_LAYOUT = SCHEDULE_LAYOUT_PORTRAIT

# Dot-matrix schedule print presets (also applied to styled vertical PDF).
SCHEDULE_DM_STYLE_CLASSIC = "classic"
SCHEDULE_DM_STYLE_SIGN = "sign"
SCHEDULE_DM_STYLES = frozenset({SCHEDULE_DM_STYLE_CLASSIC, SCHEDULE_DM_STYLE_SIGN})
DEFAULT_SCHEDULE_DM_STYLE = SCHEDULE_DM_STYLE_CLASSIC
DEFAULT_SCHEDULE_DM_BORDERS = True

SCHEDULE_DM_STYLE_LABELS = {
    SCHEDULE_DM_STYLE_CLASSIC: "Classic (Batch + Expiry separate)",
    SCHEDULE_DM_STYLE_SIGN: "Sign style (Batch/Expiry + Sign column)",
}

SCHEDULE_LAYOUT_LABELS = {
    SCHEDULE_LAYOUT_PORTRAIT: "A4 Portrait (separate columns, no rate/amount)",
    SCHEDULE_LAYOUT_LANDSCAPE: "A4 Landscape (wide — all columns)",
    SCHEDULE_LAYOUT_STYLED: "Styled Vertical (combined columns — both Classic & Sign)",
}


def _config_path() -> str:
    if getattr(sys, "frozen", False):
        base = os.path.join(
            os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
            "VeterinaryApp",
        )
        os.makedirs(base, exist_ok=True)
        return os.path.join(base, "export_format.txt")
    return os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "config",
        "export_format.txt",
    )


def load_default_export_format() -> str:
    try:
        path = _config_path()
        if os.path.exists(path):
            fmt = open(path, encoding="utf-8").read().strip().lower()
            if fmt in VALID_EXPORT_FORMATS:
                return fmt
    except Exception:
        pass
    return DEFAULT_EXPORT_FORMAT


def save_default_export_format(fmt: str) -> None:
    fmt = (fmt or "").strip().lower()
    if fmt not in VALID_EXPORT_FORMATS:
        fmt = DEFAULT_EXPORT_FORMAT
    path = _config_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(fmt)


def _schedule_layout_path() -> str:
    base = os.path.dirname(_config_path())
    return os.path.join(base, "schedule_report_layout.txt")


def load_schedule_report_layout() -> str:
    try:
        path = _schedule_layout_path()
        if os.path.exists(path):
            val = open(path, encoding="utf-8").read().strip().lower()
            if val in SCHEDULE_REPORT_LAYOUTS:
                return val
    except Exception:
        pass
    return DEFAULT_SCHEDULE_REPORT_LAYOUT


def save_schedule_report_layout(layout: str) -> None:
    val = (layout or "").strip().lower()
    if val not in SCHEDULE_REPORT_LAYOUTS:
        val = DEFAULT_SCHEDULE_REPORT_LAYOUT
    path = _schedule_layout_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(val)


def _schedule_dm_prefs_path() -> str:
    base = os.path.dirname(_config_path())
    return os.path.join(base, "schedule_dm_prefs.json")


def load_schedule_dm_prefs() -> dict:
    """Dot-matrix schedule print prefs: {style, borders}."""
    out = {
        "style": DEFAULT_SCHEDULE_DM_STYLE,
        "borders": DEFAULT_SCHEDULE_DM_BORDERS,
    }
    try:
        path = _schedule_dm_prefs_path()
        if not os.path.exists(path):
            return out
        raw = json.loads(open(path, encoding="utf-8").read() or "{}")
        style = str(raw.get("style") or "").strip().lower()
        if style in SCHEDULE_DM_STYLES:
            out["style"] = style
        if "borders" in raw:
            out["borders"] = bool(raw.get("borders"))
    except Exception:
        pass
    return out


def save_schedule_dm_prefs(
    style: str | None = None,
    borders: bool | None = None,
) -> dict:
    cur = load_schedule_dm_prefs()
    if style is not None:
        val = (style or "").strip().lower()
        cur["style"] = val if val in SCHEDULE_DM_STYLES else DEFAULT_SCHEDULE_DM_STYLE
    if borders is not None:
        cur["borders"] = bool(borders)
    path = _schedule_dm_prefs_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cur, f, indent=2)
    return cur
