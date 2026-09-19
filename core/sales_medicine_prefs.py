"""Sales medicine dropdown preferences (batch order, zero-stock visibility)."""
from __future__ import annotations

import os
import sys

BATCH_NEWEST_FIRST = "newest_first"
BATCH_OLDEST_FIRST = "oldest_first"
BATCH_VALUES = (BATCH_NEWEST_FIRST, BATCH_OLDEST_FIRST)


def _config_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.join(
            os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
            "VeterinaryApp",
        )
    return os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "config",
    )


def load_batch_sort_order() -> str:
    path = os.path.join(_config_dir(), "sales_batch_sort_order.txt")
    try:
        if os.path.exists(path):
            raw = open(path, encoding="utf-8").read().strip().lower()
            if raw in BATCH_VALUES:
                return raw
    except Exception:
        pass
    return BATCH_OLDEST_FIRST


def save_batch_sort_order(order: str) -> None:
    val = (order or "").strip().lower()
    if val not in BATCH_VALUES:
        val = BATCH_OLDEST_FIRST
    os.makedirs(_config_dir(), exist_ok=True)
    with open(os.path.join(_config_dir(), "sales_batch_sort_order.txt"), "w", encoding="utf-8") as f:
        f.write(val)


def batch_sort_label(order: str) -> str:
    if order == BATCH_NEWEST_FIRST:
        return "Recent batch first (newest purchase on top)"
    return "Oldest batch first (earliest expiry on top)"


def batch_sort_from_label(label: str) -> str:
    text = (label or "").strip().lower()
    if "recent" in text or "newest" in text:
        return BATCH_NEWEST_FIRST
    return BATCH_OLDEST_FIRST


def load_show_zero_stock_in_sales() -> bool:
    path = os.path.join(_config_dir(), "sales_show_zero_stock.txt")
    try:
        if os.path.exists(path):
            return open(path, encoding="utf-8").read().strip().lower() in ("1", "true", "yes", "on")
    except Exception:
        pass
    return False


def save_show_zero_stock_in_sales(enabled: bool) -> None:
    os.makedirs(_config_dir(), exist_ok=True)
    with open(os.path.join(_config_dir(), "sales_show_zero_stock.txt"), "w", encoding="utf-8") as f:
        f.write("1" if enabled else "0")


def batch_order_sql_clause(alias: str = "m") -> str:
    if load_batch_sort_order() == BATCH_NEWEST_FIRST:
        return (
            f"COALESCE({alias}.last_purchase_date, '') DESC, "
            f"{alias}.expiry_date DESC, {alias}.id DESC"
        )
    return f"{alias}.expiry_date ASC, {alias}.id ASC"


def batch_purchase_join_sql(medicine_alias: str = "m") -> str:
    return f"""
        LEFT JOIN (
            SELECT pi.medicine_id, MAX(p.purchase_date) AS last_purchase_date
            FROM purchase_items pi
            JOIN purchases p ON p.id = pi.purchase_id
            GROUP BY pi.medicine_id
        ) lp ON lp.medicine_id = {medicine_alias}.id
    """
