"""Normalize customer/supplier names and document tab labels."""
from __future__ import annotations

from core.customer_service import COUNTER_SALE, is_counter_sale_name


def storage_name_from_entry(text: str) -> str:
    """Full customer/supplier name for storage (trimmed, not truncated)."""
    return (text or '').strip().upper()


def normalize_medicine_name(text: str) -> str:
    """Trim and uppercase medicine name for storage/lookup."""
    return (text or '').strip().upper()


def normalize_medicine_names_in_db(conn) -> int:
    """Uppercase all medicine names once (fixes legacy mixed-case rows)."""
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE medicines
        SET name = UPPER(TRIM(name))
        WHERE name != UPPER(TRIM(name))
        """
    )
    count = int(cur.rowcount or 0)
    if count:
        conn.commit()
    return count


def _tab_short_name(text: str) -> str:
    """First word only — for compact sale/purchase tab titles."""
    raw = (text or '').strip()
    if not raw:
        return ''
    return raw.split()[0].upper()


def sale_tab_label(customer_name: str, default: str) -> str:
    word = _tab_short_name(customer_name)
    if not word or is_counter_sale_name(word):
        return default
    return word


def purchase_tab_label(supplier_name: str, default: str) -> str:
    word = _tab_short_name(supplier_name)
    if not word:
        return default
    return word
