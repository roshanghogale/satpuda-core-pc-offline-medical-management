"""Lookup indexes every store database needs once it is big (26 Oct 2026).

A shop moved over from Medivision / Marg brings everything at once: Shrihari Medical Jalna
64,032 medicine batches, 8,539 customers, 13,115 bills with 66,478 lines; Jagadamba about
65,000 medicines. The store file had no index on any business table, so every "the lines of
this bill", "the batches of this medicine", "this party's payments" read the whole table:
the startup alerts asked the medicines table 2,681 times by name (20 s on Shrihari).

Each index is on ONE column the app looks records up by with ``=`` / ``IN``, and each is
marked ``unordered`` in sqlite_stat1, so SQLite uses it for those lookups only -- never to
walk a table in name order or for a range. Everything else keeps the plan it had (a whole
medicine list in name order read through an index is three times slower than the scan and
sort it had), and on equal keys an index gives rowid order, the order a full read gives: a
query answers the same rows in the same order as before; only the time changes
(tests/perf/test_perf_indexes.py compares every page's answers).

The statistics are written here, not by ANALYZE: "1048576 10" is what SQLite assumes for an
index it has no figures for, so the planner weighs these indexes as it always would have,
on every store and at every size, and nothing goes stale as the shop grows.

ensure_perf_indexes() is called wherever a store file is opened or made (the engine's
connection, db_setup.initialise). Once the indexes are there it costs two small reads; the
first time, a 64,000-medicine store takes well under a second, a 5-lakh one a few seconds.
"""
from __future__ import annotations

import sqlite3

# (index name, table, column)
PERF_INDEXES: tuple[tuple[str, str, str], ...] = (
    # medicines: the batches of a medicine (alerts, reorder, purchase / return line matching)
    ("ix_perf_medicines_name", "medicines", "name"),
    ("ix_perf_medicines_batch", "medicines", "batch_no"),
    # bill lines by bill and by medicine
    ("ix_perf_sales_items_sale", "sales_items", "sale_id"),
    ("ix_perf_sales_items_medicine", "sales_items", "medicine_id"),
    ("ix_perf_purchase_items_purchase", "purchase_items", "purchase_id"),
    ("ix_perf_purchase_items_medicine", "purchase_items", "medicine_id"),
    ("ix_perf_sales_return_items_return", "sales_return_items", "return_id"),
    ("ix_perf_sales_return_items_medicine", "sales_return_items", "medicine_id"),
    ("ix_perf_purchase_return_items_return", "purchase_return_items", "return_id"),
    ("ix_perf_purchase_return_items_medicine", "purchase_return_items", "medicine_id"),
    # bills by party and number
    ("ix_perf_sales_customer", "sales", "customer_id"),
    ("ix_perf_sales_bill_no", "sales", "bill_no"),
    ("ix_perf_purchases_supplier", "purchases", "supplier_id"),
    # returns by bill and party
    ("ix_perf_sales_returns_sale", "sales_returns", "sale_id"),
    ("ix_perf_sales_returns_customer", "sales_returns", "customer_id"),
    ("ix_perf_purchase_returns_purchase", "purchase_returns", "purchase_id"),
    ("ix_perf_purchase_returns_supplier", "purchase_returns", "supplier_id"),
    # payments by party
    ("ix_perf_customer_payments_customer", "customer_payments", "customer_id"),
    ("ix_perf_supplier_payments_supplier", "supplier_payments", "supplier_id"),
    # parties by name / phone
    ("ix_perf_customers_name", "customers", "name"),
    ("ix_perf_customers_phone", "customers", "phone"),
)

# SQLite's own assumption for an index without statistics (2^20 rows, ~10 per key), and
# "unordered": use it for = / IN only, never for ORDER BY / GROUP BY / ranges.
STAT = "1048576 10 unordered"


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    try:
        return {str(r[1]) for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    except sqlite3.Error:
        return set()


def missing_indexes(conn: sqlite3.Connection) -> list[str]:
    """Names of the indexes above that this file could have and does not."""
    have = {str(r[0]) for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index'").fetchall()}
    cols_cache: dict[str, set[str]] = {}
    out: list[str] = []
    for name, table, col in PERF_INDEXES:
        if name in have:
            continue
        if col in cols_cache.setdefault(table, _columns(conn, table)):
            out.append(name)
    return out


def _stats_in_place(conn: sqlite3.Connection, names: list[str]) -> bool:
    try:
        rows = conn.execute(
            "SELECT idx, stat FROM sqlite_stat1 WHERE idx LIKE 'ix_perf_%'").fetchall()
    except sqlite3.Error:
        return False
    have = {str(r[0]): str(r[1]) for r in rows}
    return all(have.get(n) == STAT for n in names)


def ensure_perf_indexes(conn: sqlite3.Connection) -> list[str]:
    """Create the indexes this file lacks and give them their statistics (idempotent; a table
    or column an older store does not have is skipped). Returns the names created."""
    made: list[str] = []
    spec = {name: (table, col) for name, table, col in PERF_INDEXES}
    for name in missing_indexes(conn):
        table, col = spec[name]
        try:
            conn.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {table} ({col})")
            made.append(name)
        except sqlite3.Error as exc:
            print(f"[perf_indexes] {name}: {exc}", flush=True)
    present = [str(r[0]) for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'ix_perf_%'").fetchall()]
    if present and (made or not _stats_in_place(conn, present)):
        _write_stats(conn, present)
    try:
        conn.commit()
    except sqlite3.Error:
        pass
    return made


def _write_stats(conn: sqlite3.Connection, names: list[str]) -> None:
    tables = {name: table for name, table, _col in PERF_INDEXES}
    try:
        # sqlite_stat1 is made by ANALYZE; analyzing sqlite_master alone makes it (no table read).
        conn.execute("ANALYZE sqlite_master")
        for n in names:
            if n not in tables:
                continue
            conn.execute("DELETE FROM sqlite_stat1 WHERE idx=?", (n,))
            conn.execute("INSERT INTO sqlite_stat1 (tbl, idx, stat) VALUES (?, ?, ?)",
                         (tables[n], n, STAT))
        conn.commit()
        # Load the statistics into this connection (others read them when they open).
        conn.execute("ANALYZE sqlite_master")
    except sqlite3.Error as exc:
        print(f"[perf_indexes] statistics: {exc}", flush=True)
