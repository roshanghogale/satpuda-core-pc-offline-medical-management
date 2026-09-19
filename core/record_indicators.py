"""
Record row status indicators for list screens (sales, inventory, etc.).

Settings: Layout & Lists → Record Indicators
"""
from __future__ import annotations

import json
import os
from copy import deepcopy
from typing import Any

# ── Column helpers (prepended / appended when style needs them) ─────────────

INDICATOR_COL = "__ind__"
STATUS_COL = "__status__"
INDICATOR_HEADING = ""
STATUS_HEADING = "Status"

DISPLAY_STYLES = (
    "none",
    "status_badge",
    "left_border",
    "right_border",
    "full_row",
    "full_row_text",
    "badge_border",
    "auto",
)

DISPLAY_STYLE_LABELS = {
    "none": "None",
    "status_badge": "Status Badge (● Due, ● Paid)",
    "left_border": "Left Border",
    "right_border": "Right Border",
    "full_row": "Full Row Background",
    "full_row_text": "Full Row Text Color",
    "badge_border": "Badge + Left Border",
    "auto": "Auto (Recommended)",
}

STATUSES = (
    "due",
    "partial",
    "cleared",
    "credit",
    "low_stock",
    "out_of_stock",
    "near_expiry",
    "expired",
    "purchase_return",
    "sales_return",
)

STATUS_LABELS = {
    "due": "Due",
    "partial": "Partial Payment",
    "cleared": "Cleared / Paid",
    "credit": "Credit Balance",
    "low_stock": "Low Stock",
    "out_of_stock": "Out of Stock",
    "near_expiry": "Near Expiry",
    "expired": "Expired",
    "purchase_return": "Purchase Return",
    "sales_return": "Sales Return",
    # Not in STATUSES on purpose: it is not a preference, it is a warning. An
    # autosaved bill whose form was never finished is real money on a real
    # customer, and it has to be told apart from a completed sale in the one
    # list a shop actually audits.
    "unfinished": "Unfinished Sale",
}

# Bold, dark status colors (fixed — used in lists and settings previews).
STATUS_COLORS = {
    "due": "#9B0000",
    "partial": "#9A5500",
    "cleared": "#006B1A",
    "credit": "#003D8F",
    "low_stock": "#7A4F00",
    "out_of_stock": "#7A0000",
    "near_expiry": "#8A6500",
    "expired": "#5A0070",
    "purchase_return": "#005A60",
    "sales_return": "#9A0048",
    "unfinished": "#7A3B00",
}

_AUTO_EFFECT = {
    "due": {"border": "left", "badge": False, "full_row": False, "full_row_text": False},
    "partial": {"border": "left", "badge": False, "full_row": False, "full_row_text": False},
    "cleared": {"border": None, "badge": True, "full_row": False, "full_row_text": False},
    "credit": {"border": None, "badge": True, "full_row": False, "full_row_text": False},
    "low_stock": {"border": None, "badge": True, "full_row": False, "full_row_text": False},
    "out_of_stock": {"border": "left", "badge": True, "full_row": False, "full_row_text": False},
    "near_expiry": {"border": None, "badge": True, "full_row": False, "full_row_text": False},
    "expired": {"border": "left", "badge": True, "full_row": False, "full_row_text": False},
    "purchase_return": {"border": None, "badge": True, "full_row": False, "full_row_text": False},
    "sales_return": {"border": None, "badge": True, "full_row": False, "full_row_text": False},
    "unfinished": {"border": "left", "badge": True, "full_row": False, "full_row_text": False},
}

_BADGE_LABEL = {
    "due": "Due",
    "partial": "Partial",
    "cleared": "Paid",
    "credit": "Credit",
    "low_stock": "Low Stock",
    "out_of_stock": "Out of Stock",
    "near_expiry": "Near Expiry",
    "expired": "Expired",
    "purchase_return": "Purchase Return",
    "sales_return": "Sales Return",
    "unfinished": "Unfinished",
}

_BORDER_CHAR = {
    "left": "\u258c",
    "right": "\u258c",
}

_DEFAULT_PREFS = {
    "display_style": "auto",
}

_cached_prefs: dict | None = None

# Backward compatibility alias
DEFAULT_COLORS = STATUS_COLORS


def _config_path() -> str:
    from core.layout_config import _get_config_dir
    return os.path.join(_get_config_dir(), "record_indicators.json")


def _normalize_style(style: str) -> str:
    text = (style or "auto").strip().lower()
    if text == "bottom_border":
        return "auto"
    return text if text in DISPLAY_STYLES else "auto"


def load_record_indicator_prefs(*, reload: bool = False) -> dict:
    global _cached_prefs
    if _cached_prefs is not None and not reload:
        return deepcopy(_cached_prefs)
    prefs = deepcopy(_DEFAULT_PREFS)
    path = _config_path()
    try:
        if os.path.isfile(path):
            with open(path, encoding="utf-8-sig") as f:
                raw = json.load(f)
            if isinstance(raw, dict):
                prefs["display_style"] = _normalize_style(raw.get("display_style"))
    except Exception:
        pass
    _cached_prefs = deepcopy(prefs)
    return deepcopy(prefs)


def save_record_indicator_prefs(prefs: dict) -> None:
    global _cached_prefs
    out = {
        "display_style": _normalize_style(prefs.get("display_style")),
    }
    os.makedirs(os.path.dirname(_config_path()), exist_ok=True)
    with open(_config_path(), "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)
    _cached_prefs = deepcopy(out)


def get_status_color(status: str, prefs: dict | None = None) -> str:
    return STATUS_COLORS.get(status, "#1a1a1a")


def _indicator_tag_font() -> tuple:
    from core.font_config import FONT_FAMILY, FONT_SIZE_TABLES
    return (FONT_FAMILY, FONT_SIZE_TABLES, "bold")


def _effective_for_status(status: str, prefs: dict) -> dict:
    style = prefs.get("display_style", "auto")
    if style == "auto":
        return dict(_AUTO_EFFECT.get(status, _empty_effect()))
    return _global_components(style)


def badge_cell_text(status: str) -> str:
    label = _BADGE_LABEL.get(status, STATUS_LABELS.get(status, status))
    return f"\u2b24 {label}"


def _empty_effect() -> dict:
    return {
        "border": None,
        "badge": False,
        "full_row": False,
        "full_row_text": False,
    }


def _global_components(style: str) -> dict:
    if style == "none":
        return _empty_effect()
    if style == "status_badge":
        return {**_empty_effect(), "badge": True}
    if style == "left_border":
        return {**_empty_effect(), "border": "left"}
    if style == "right_border":
        return {**_empty_effect(), "border": "right"}
    if style == "full_row":
        return {**_empty_effect(), "full_row": True}
    if style == "full_row_text":
        return {**_empty_effect(), "full_row_text": True}
    if style == "badge_border":
        return {**_empty_effect(), "border": "left", "badge": True}
    return _empty_effect()


def resolve_effective_display(status: str, prefs: dict | None = None) -> dict:
    p = prefs or load_record_indicator_prefs()
    style = p.get("display_style", "auto")
    if style == "none" or not status:
        return _empty_effect()
    if style == "auto":
        return dict(_AUTO_EFFECT.get(status, _empty_effect()))
    return _global_components(style)


def needs_indicator_column(prefs: dict | None = None) -> bool:
    p = prefs or load_record_indicator_prefs()
    style = p.get("display_style", "auto")
    if style == "none":
        return False
    if style in ("left_border", "badge_border"):
        return True
    if style == "auto":
        return any(
            _AUTO_EFFECT[s].get("border") == "left"
            for s in STATUSES
        )
    return False


def needs_status_column(prefs: dict | None = None) -> bool:
    p = prefs or load_record_indicator_prefs()
    style = p.get("display_style", "auto")
    if style == "none":
        return False
    if style in ("status_badge", "badge_border", "right_border"):
        return True
    if style == "auto":
        return any(
            _AUTO_EFFECT[s].get("badge")
            or _AUTO_EFFECT[s].get("border") == "right"
            for s in STATUSES
        )
    return False


def column_heading(col: str) -> str:
    if col == INDICATOR_COL:
        return INDICATOR_HEADING
    if col == STATUS_COL:
        return STATUS_HEADING
    return col


def extend_columns(base_columns: tuple | list, prefs: dict | None = None) -> tuple:
    p = prefs or load_record_indicator_prefs()
    cols = list(base_columns)
    if needs_indicator_column(p) and INDICATOR_COL not in cols:
        cols.insert(0, INDICATOR_COL)
    if needs_status_column(p) and STATUS_COL not in cols:
        cols.append(STATUS_COL)
    return tuple(cols)


def indicator_column_widths() -> dict:
    return {INDICATOR_COL: 28, STATUS_COL: 150}


def _append_status_cell(out: list, eff: dict, status: str) -> None:
    if eff.get("badge"):
        out.append(badge_cell_text(status))
    elif eff.get("border") == "right":
        out.append(_BORDER_CHAR["right"])
    else:
        out.append("")


def prepare_tree_row(
    values: tuple | list,
    status: str | None,
    *,
    prefs: dict | None = None,
    badge_text: str | None = None,
) -> tuple[tuple, tuple]:
    """Return (decorated_values, tags) for tree.insert()."""
    p = prefs or load_record_indicator_prefs()
    out = list(values)
    style = p.get("display_style", "auto")
    if style == "none":
        return tuple(out), ()

    use_ind = needs_indicator_column(p)
    use_status = needs_status_column(p)
    tag: tuple = ()

    if status:
        eff = resolve_effective_display(status, p)
        styled = any((
            eff.get("border"),
            eff.get("badge"),
            eff.get("full_row"),
            eff.get("full_row_text"),
        ))
        if styled:
            tag = (f"ri_{status}",)
            if use_ind:
                bar = _BORDER_CHAR["left"] if eff.get("border") == "left" else ""
                out.insert(0, bar)
            if use_status:
                _append_status_cell(out, eff, status)
    else:
        if use_ind:
            out.insert(0, "")
        if use_status:
            out.append("")

    return tuple(out), tag


def register_tree_tags(tree, prefs: dict | None = None) -> None:
    """Register per-status tags on a Treeview from current prefs."""
    p = prefs or load_record_indicator_prefs()
    if p.get("display_style", "auto") == "none":
        return

    bold_font = _indicator_tag_font()
    for status in STATUSES:
        tag = f"ri_{status}"
        color = get_status_color(status, p)
        eff = _effective_for_status(status, p)
        kw: dict[str, Any] = {"font": bold_font}
        if eff.get("full_row"):
            kw["background"] = color
            kw["foreground"] = "#ffffff"
        else:
            kw["foreground"] = color
        try:
            tree.tag_configure(tag, **kw)
        except Exception:
            pass


def preview_sample_rows() -> list[tuple[str, str, tuple]]:
    return [
        ("Due bill", "due", ("SCB101", "Customer A", "₹500")),
        ("Partial pay", "partial", ("SCB102", "Customer B", "₹300")),
        ("Paid / cleared", "cleared", ("SCB103", "Customer C", "₹0")),
        ("Low stock", "low_stock", ("Paracetamol 650", "Tablet", "2")),
        ("Near expiry", "near_expiry", ("Dolo 650", "05/26", "12")),
    ]


def prepare_preview_row(
    status: str,
    base_values: tuple,
    *,
    style_key: str,
    colors: dict | None = None,
) -> tuple[tuple, tuple]:
    prefs = {"display_style": _normalize_style(style_key)}
    return prepare_tree_row(base_values, status, prefs=prefs)


def preview_columns(style_key: str, prefs: dict | None = None) -> tuple:
    p = dict(prefs or load_record_indicator_prefs())
    p["display_style"] = _normalize_style(style_key)
    return extend_columns(("Sample", "Detail", "Amount"), p)


def register_preview_tags(tree, style_key: str, colors: dict | None = None) -> None:
    prefs = {"display_style": _normalize_style(style_key)}
    register_tree_tags(tree, prefs)


# ── Status resolvers per screen ───────────────────────────────────────────────

def sales_history_status(sale) -> str | None:
    """Color by cascaded account balance (total_due / account_cleared).

    previous_due and due_amount are entry-time snapshots and must not alone
    mark a bill Partial after later payments cleared the account.
    """
    credit = float(sale[10] or 0)
    amount_paid = float(sale[5] or 0)
    total_due = float(sale[11] or 0)
    try:
        account_cleared = int(sale[13] or 0)
    except Exception:
        account_cleared = 0
    if account_cleared == 1 or total_due <= 0.01:
        if credit > 0.01:
            return "credit"
        return "cleared"
    return "partial" if amount_paid > 0 else "due"


def sales_history_display_due(sale) -> float:
    total_due = float(sale[11] or 0)
    try:
        account_cleared = int(sale[13] or 0)
    except Exception:
        account_cleared = 0
    if account_cleared == 1:
        return 0.0
    if total_due > 0.01:
        return total_due
    return 0.0


def purchase_history_status(entry_due: float, entry_paid: float, via_payment: float) -> str | None:
    if entry_due <= 0.01:
        return "cleared"
    if entry_paid + via_payment > 0:
        return "partial"
    return "due"


def inventory_status(stock, med_type: str, expiry_raw, *, is_low_stock, is_expired, is_near_expiry) -> str | None:
    if stock == 0:
        return "out_of_stock"
    if is_low_stock(stock, med_type):
        return "low_stock"
    if is_expired(expiry_raw):
        return "expired"
    if is_near_expiry(expiry_raw, med_type):
        return "near_expiry"
    return None


def customer_status(total_due: float, total_credit: float) -> str | None:
    if total_due > 0:
        return "due"
    if total_credit > 0:
        return "credit"
    return "cleared"


def supplier_status(total_due: float, total_credit: float) -> str | None:
    return customer_status(total_due, total_credit)


def ledger_status(tag: str) -> str | None:
    mapping = {
        "due": "due",
        "credit": "credit",
        "clear": "cleared",
        "payment": "cleared",
        "return": "sales_return",
    }
    return mapping.get(tag)
