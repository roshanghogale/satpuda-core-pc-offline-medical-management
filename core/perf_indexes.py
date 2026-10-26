"""Indexes a big store needs (64,000+ batches, 13,000+ bills; planned for 5 lakh batches).

The store tables had no index at all besides their primary keys, so every lookup by medicine,
bill or customer read the whole table. The worst was the Sales medicine list: for each batch it
asks for that batch's first purchase (core/batch_visibility.first_purchase_date_sql), a read of
every purchase line, so opening the list on Shrihari Medical (64,032 batches) took 25 s.

Every index is CREATE INDEX IF NOT EXISTS and only built when its table and columns exist, so
running this twice (or alongside another branch's copy of the same list) is harmless. An index
changes no result: SQLite answers the same query, only without the full read.
"""
from __future__ import annotations

import sqlite3

# (index name, table, columns needed, CREATE statement)
_INDEXES: tuple[tuple[str, str, tuple[str, ...], str], ...] = (
    # Sales picker: first purchase of a batch, purchase lines of a purchase.
    ("idx_purchase_items_medicine", "purchase_items", ("medicine_id",),
     "CREATE INDEX IF NOT EXISTS idx_purchase_items_medicine ON purchase_items (medicine_id)"),
    ("idx_purchase_items_purchase", "purchase_items", ("purchase_id",),
     "CREATE INDEX IF NOT EXISTS idx_purchase_items_purchase ON purchase_items (purchase_id)"),
    # A bill's lines; a batch's sales (stock checks, returns, edits).
    ("idx_sales_items_sale", "sales_items", ("sale_id",),
     "CREATE INDEX IF NOT EXISTS idx_sales_items_sale ON sales_items (sale_id)"),
    ("idx_sales_items_medicine", "sales_items", ("medicine_id",),
     "CREATE INDEX IF NOT EXISTS idx_sales_items_medicine ON sales_items (medicine_id)"),
    # A customer's bills (dues, the day's counter bill), a day's bills, a bill number.
    ("idx_sales_customer", "sales", ("customer_id",),
     "CREATE INDEX IF NOT EXISTS idx_sales_customer ON sales (customer_id)"),
    ("idx_sales_bill_date", "sales", ("bill_date",),
     "CREATE INDEX IF NOT EXISTS idx_sales_bill_date ON sales (bill_date)"),
    ("idx_sales_bill_no", "sales", ("bill_no",),
     "CREATE INDEX IF NOT EXISTS idx_sales_bill_no ON sales (bill_no)"),
    # Medicine search by name (the picker sorts and matches without case), and the batch list
    # of one name (list_batches_for_name matches UPPER(TRIM(name))).
    ("idx_medicines_name_nocase", "medicines", ("name",),
     "CREATE INDEX IF NOT EXISTS idx_medicines_name_nocase ON medicines (name COLLATE NOCASE)"),
    # Only the batches with stock: the Sales list never offers an empty one, and on a big store
    # most batches are empty (Shrihari Medical: 3,214 of 64,032 have stock).
    ("idx_medicines_instock_name", "medicines", ("name", "stock_qty"),
     "CREATE INDEX IF NOT EXISTS idx_medicines_instock_name ON medicines (name COLLATE NOCASE) "
     "WHERE stock_qty > 0"),
    ("idx_medicines_name_upper", "medicines", ("name",),
     "CREATE INDEX IF NOT EXISTS idx_medicines_name_upper ON medicines (UPPER(TRIM(name)))"),
    # Customer by name / phone (every save looks the customer up).
    ("idx_customers_name_nocase", "customers", ("name",),
     "CREATE INDEX IF NOT EXISTS idx_customers_name_nocase ON customers (name COLLATE NOCASE)"),
    ("idx_customers_phone", "customers", ("phone",),
     "CREATE INDEX IF NOT EXISTS idx_customers_phone ON customers (phone)"),
    # Returns and payments of a bill / customer.
    ("idx_sales_returns_sale", "sales_returns", ("sale_id",),
     "CREATE INDEX IF NOT EXISTS idx_sales_returns_sale ON sales_returns (sale_id)"),
    ("idx_sales_returns_customer", "sales_returns", ("customer_id",),
     "CREATE INDEX IF NOT EXISTS idx_sales_returns_customer ON sales_returns (customer_id)"),
    ("idx_sales_return_items_return", "sales_return_items", ("return_id",),
     "CREATE INDEX IF NOT EXISTS idx_sales_return_items_return ON sales_return_items (return_id)"),
    ("idx_sales_return_items_medicine", "sales_return_items", ("medicine_id",),
     "CREATE INDEX IF NOT EXISTS idx_sales_return_items_medicine ON sales_return_items (medicine_id)"),
    ("idx_customer_payments_customer", "customer_payments", ("customer_id",),
     "CREATE INDEX IF NOT EXISTS idx_customer_payments_customer ON customer_payments (customer_id)"),
)


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    try:
        return {str(r[1]) for r in conn.execute(f"PRAGMA table_info({table})")}
    except sqlite3.Error:
        return set()


def ensure_perf_indexes(conn: sqlite3.Connection) -> list[str]:
    """Create the missing indexes; returns the names created. Never raises."""
    made: list[str] = []
    try:
        have = {str(r[0]) for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    except sqlite3.Error:
        return made
    cols_by_table: dict[str, set[str]] = {}
    for name, table, needs, sql in _INDEXES:
        if name in have:
            continue
        cols = cols_by_table.setdefault(table, _columns(conn, table))
        if not cols or not set(needs) <= cols:
            continue
        try:
            conn.execute(sql)
            made.append(name)
        except sqlite3.Error as exc:
            print(f"[perf_indexes] {name}: {exc}", flush=True)
    if made:
        try:
            conn.commit()
        except sqlite3.Error:
            pass
    return made
