"""Indexes a big store needs (64,000+ batches, 13,000+ bills; planned for 5 lakh batches).

The store tables had no index at all besides their primary keys, so every lookup by medicine,
bill or customer read the whole table. Two lists, made together by ensure_perf_indexes()
wherever a store file is opened or made (the engine's connection, db_setup.initialise):

BILLING_INDEXES (sales billing, 26 Oct 2026). The worst was the Sales medicine list: for each
batch it asks for that batch's first purchase (core/batch_visibility.first_purchase_date_sql),
a read of every purchase line, so opening the list on Shrihari Medical (64,032 batches) took
25 s. The medicine search reads the name indexes IN ORDER (name COLLATE NOCASE, the in-stock
partial index), so these are plain indexes the query planner may use any way it likes.

LOOKUP_INDEXES (the other pages, 26 Oct 2026): the startup alerts asked the medicines table by
name thousands of times (20 s on Shrihari), and bill lines, returns and payments by their bill
or party. Each is on ONE column looked up with ``=`` / ``IN``, and each is marked
``unordered`` in sqlite_stat1, so SQLite uses it for those lookups only -- never to walk a
table in order or for a range. A whole list read in name order through an index is three
times slower than the scan and sort it had, and a page whose query has no ORDER BY would get
its rows in another order; with ``unordered`` such a query keeps the plan it had. On equal
keys an index gives rowid order, the order a full read gives, so the lookups answer the same
rows in the same order (tests/test_big_store_pages_answer_the_same_with_the_speed_fixes.py).
A lookup the billing list already has an index for (bill lines by bill, ...) is not made twice.

The statistics are written here, not by ANALYZE: "1048576 10" is what SQLite assumes for an
index it has no figures for, so the planner weighs these indexes as it always would have, on
every store and at every size, and nothing goes stale as the shop grows. Indexes without a
statistics row (the billing list) are weighed by the same defaults.

Every index is CREATE INDEX IF NOT EXISTS and only built when its table and columns exist, so
running this twice is harmless; once they are there it costs two small reads.
"""
from __future__ import annotations

import sqlite3

# (index name, table, columns needed, CREATE statement)
BILLING_INDEXES: tuple[tuple[str, str, tuple[str, ...], str], ...] = (
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

# (index name, table, column) -- equality lookups only, marked "unordered" (see above).
LOOKUP_INDEXES: tuple[tuple[str, str, str], ...] = (
    # medicines by exact name / batch (alert rows, reorder, purchase and return line matching;
    # the billing name indexes are NOCASE / UPPER(TRIM()), which "name = ?" cannot use)
    ("ix_perf_medicines_name", "medicines", "name"),
    ("ix_perf_medicines_batch", "medicines", "batch_no"),
    # purchase-side lines, bills, returns and payments by bill / party
    ("ix_perf_purchase_return_items_return", "purchase_return_items", "return_id"),
    ("ix_perf_purchase_return_items_medicine", "purchase_return_items", "medicine_id"),
    ("ix_perf_purchases_supplier", "purchases", "supplier_id"),
    ("ix_perf_purchase_returns_purchase", "purchase_returns", "purchase_id"),
    ("ix_perf_purchase_returns_supplier", "purchase_returns", "supplier_id"),
    ("ix_perf_supplier_payments_supplier", "supplier_payments", "supplier_id"),
    # customers by exact name (ledgers, payments; the billing index is NOCASE)
    ("ix_perf_customers_name", "customers", "name"),
)

# SQLite's own assumption for an index without statistics (2^20 rows, ~10 per key), and
# "unordered": use it for = / IN only, never for ORDER BY / GROUP BY / ranges.
STAT = "1048576 10 unordered"


def _all_specs() -> list[tuple[str, str, tuple[str, ...], str]]:
    out = list(BILLING_INDEXES)
    for name, table, col in LOOKUP_INDEXES:
        out.append((name, table, (col,), f"CREATE INDEX IF NOT EXISTS {name} ON {table} ({col})"))
    return out


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    try:
        return {str(r[1]) for r in conn.execute(f"PRAGMA table_info({table})")}
    except sqlite3.Error:
        return set()


def missing_indexes(conn: sqlite3.Connection) -> list[str]:
    """Names of the indexes above that this file could have and does not."""
    try:
        have = {str(r[0]) for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    except sqlite3.Error:
        return []
    cols_by_table: dict[str, set[str]] = {}
    out: list[str] = []
    for name, table, needs, _sql in _all_specs():
        if name in have:
            continue
        cols = cols_by_table.setdefault(table, _columns(conn, table))
        if cols and set(needs) <= cols:
            out.append(name)
    return out


def ensure_perf_indexes(conn: sqlite3.Connection) -> list[str]:
    """Create the missing indexes and give the lookup ones their statistics; returns the names
    created. Never raises."""
    made: list[str] = []
    try:
        spec = {name: sql for name, _t, _n, sql in _all_specs()}
        for name in missing_indexes(conn):
            try:
                conn.execute(spec[name])
                made.append(name)
            except sqlite3.Error as exc:
                print(f"[perf_indexes] {name}: {exc}", flush=True)
        lookup = {name for name, _t, _c in LOOKUP_INDEXES}
        present = [str(r[0]) for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'ix_perf_%'")
            if str(r[0]) in lookup]
        if present and (any(n in lookup for n in made) or not _stats_in_place(conn, present)):
            _write_stats(conn, present)
        if made:
            conn.commit()
    except sqlite3.Error as exc:
        print(f"[perf_indexes] {exc}", flush=True)
    return made


def _stats_in_place(conn: sqlite3.Connection, names: list[str]) -> bool:
    try:
        rows = conn.execute(
            "SELECT idx, stat FROM sqlite_stat1 WHERE idx LIKE 'ix_perf_%'").fetchall()
    except sqlite3.Error:
        return False
    have = {str(r[0]): str(r[1]) for r in rows}
    return all(have.get(n) == STAT for n in names)


def _write_stats(conn: sqlite3.Connection, names: list[str]) -> None:
    tables = {name: table for name, table, _col in LOOKUP_INDEXES}
    try:
        # sqlite_stat1 is made by ANALYZE; analyzing sqlite_master alone makes it (no table read).
        conn.execute("ANALYZE sqlite_master")
        for n in names:
            conn.execute("DELETE FROM sqlite_stat1 WHERE idx=?", (n,))
            conn.execute("INSERT INTO sqlite_stat1 (tbl, idx, stat) VALUES (?, ?, ?)",
                         (tables[n], n, STAT))
        conn.commit()
        # Load the statistics into this connection (others read them when they open).
        conn.execute("ANALYZE sqlite_master")
    except sqlite3.Error as exc:
        print(f"[perf_indexes] statistics: {exc}", flush=True)
