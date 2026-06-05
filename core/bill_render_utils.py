"""Shared helpers for bill HTML templates."""
from __future__ import annotations

from typing import Any


def esc(text: Any) -> str:
    return (
        str(text or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def fmt_expiry_mm_yy(raw: str) -> str:
    if not raw:
        return ""
    text = str(raw).strip()
    if len(text) >= 7 and text[4] == "-":
        parts = text.split("-")
        if len(parts) >= 2:
            return f"{parts[1][:2]}/{parts[0][2:]}"
    return text
