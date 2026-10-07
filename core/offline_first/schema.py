"""Bookkeeping tables and change-capture triggers in the local store database.

Every synced table gets AFTER INSERT / UPDATE / DELETE triggers that note the changed record in
``of_dirty``; line tables note their parent document. ``medicines`` notes stock movements in
``of_stock_journal`` (the delta, whatever code path moved it) and notes the record itself only
when something other than stock changed. Capture is switched off (``of_flags.capture = '0'``)
while the PC writes changes it pulled from the server, so other devices' work is never sent
back as this PC's own.
"""
from __future__ import annotations

import sqlite3

# collection -> local table (they share names on the PC)
COLLECTIONS = (
    "customers",
    "suppliers",
    "doctors",
    "medicines",
    "general_products",
    "sales",
    "purchases",
    "customer_payments",
    "supplier_payments",
    "sales_returns",
    "purchase_returns",
    "stock_disposals",
    "pending_orders",
    "racks",
    "sections",
    "boxes",
)

# line table -> (parent collection, parent id column)
LINE_TABLES = {
    "sales_items": ("sales", "sale_id"),
    "purchase_items": ("purchases", "purchase_id"),
    "sales_return_items": ("sales_returns", "return_id"),
    "purchase_return_items": ("purchase_returns", "return_id"),
}

# Settings rows that belong to the shop (all devices), not to one PC.
SHOP_WIDE_SETTING_PREFIXES = ("regular_meds:", "customer_gst:", "gst_filed:")
SHOP_WIDE_SETTING_NAMES = ("billing_layout_prefs",)

# Medicine columns whose change is bookkeeping or stock, not an edit of the medicine itself.
_MEDICINE_NOT_AN_EDIT = {
    "stock_qty", "updated_at", "version", "sync_status", "synced_at", "device_id", "sync_state",
}

_TABLES = """
CREATE TABLE IF NOT EXISTS of_meta (k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS of_flags (k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS of_dirty (
    collection TEXT NOT NULL,
    local_id   INTEGER NOT NULL,
    op         TEXT NOT NULL,
    at         TEXT NOT NULL,
    PRIMARY KEY (collection, local_id)
);
CREATE TABLE IF NOT EXISTS of_stock_journal (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    medicine_id INTEGER NOT NULL,
    delta       INTEGER NOT NULL,
    at          TEXT NOT NULL,
    event_seq   INTEGER
);
CREATE INDEX IF NOT EXISTS idx_of_journal_open ON of_stock_journal (event_seq, medicine_id);
CREATE TABLE IF NOT EXISTS of_events (
    seq          INTEGER PRIMARY KEY,
    event_uuid   TEXT NOT NULL UNIQUE,
    collection   TEXT NOT NULL,
    op           TEXT NOT NULL,
    local_id     INTEGER,
    base_version INTEGER,
    payload      TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'pending',
    outcome      TEXT,
    flag_code    TEXT,
    flag_detail  TEXT,
    acked_at     TEXT
);
CREATE INDEX IF NOT EXISTS idx_of_events_status ON of_events (status, seq);
CREATE TABLE IF NOT EXISTS of_versions (
    collection TEXT NOT NULL,
    local_id   INTEGER NOT NULL,
    version    INTEGER NOT NULL,
    PRIMARY KEY (collection, local_id)
);
CREATE TABLE IF NOT EXISTS of_id_counters (tbl TEXT PRIMARY KEY, next_id INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS of_number_blocks (
    kind          TEXT NOT NULL,
    fy_start_year INTEGER NOT NULL,
    from_serial   INTEGER NOT NULL,
    to_serial     INTEGER NOT NULL,
    next_serial   INTEGER NOT NULL,
    PRIMARY KEY (kind, fy_start_year, from_serial)
);
CREATE TABLE IF NOT EXISTS of_doc_counters (prefix TEXT PRIMARY KEY, next_no INTEGER NOT NULL);
INSERT OR IGNORE INTO of_flags (k, v) VALUES ('capture', '1');
"""

_CAPTURE_ON = "(SELECT v FROM of_flags WHERE k='capture')='1'"
_NOW = "strftime('%Y-%m-%dT%H:%M:%fZ','now')"


def _mark(collection: str, id_expr: str, op: str) -> str:
    return (
        f"INSERT INTO of_dirty (collection, local_id, op, at) VALUES ('{collection}', {id_expr}, '{op}', {_NOW}) "
        f"ON CONFLICT(collection, local_id) DO UPDATE SET op=excluded.op, at=excluded.at;"
    )


def _columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [str(r[1]) for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return bool(conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone())


def trigger_sql(conn: sqlite3.Connection) -> list[str]:
    """CREATE TRIGGER statements for the tables this store database has."""
    out: list[str] = []
    for table in COLLECTIONS:
        if not _table_exists(conn, table):
            continue
        if table == "medicines":
            cols = [c for c in _columns(conn, table) if c not in _MEDICINE_NOT_AN_EDIT and c != "id"]
            changed = " OR ".join(f"OLD.{c} IS NOT NEW.{c}" for c in cols) or "0"
            out += [
                f"CREATE TRIGGER IF NOT EXISTS of_ins_{table} AFTER INSERT ON {table} "
                f"WHEN {_CAPTURE_ON} BEGIN {_mark(table, 'NEW.id', 'upsert')} "
                f"INSERT INTO of_stock_journal (medicine_id, delta, at) "
                f"SELECT NEW.id, CAST(COALESCE(NEW.stock_qty,0) AS INTEGER), {_NOW} "
                f"WHERE COALESCE(NEW.stock_qty,0) <> 0; END;",
                f"CREATE TRIGGER IF NOT EXISTS of_upd_{table} AFTER UPDATE ON {table} "
                f"WHEN {_CAPTURE_ON} AND ({changed}) BEGIN {_mark(table, 'NEW.id', 'upsert')} END;",
                f"CREATE TRIGGER IF NOT EXISTS of_stock_{table} AFTER UPDATE OF stock_qty ON {table} "
                f"WHEN {_CAPTURE_ON} AND COALESCE(NEW.stock_qty,0) <> COALESCE(OLD.stock_qty,0) BEGIN "
                f"INSERT INTO of_stock_journal (medicine_id, delta, at) VALUES "
                f"(NEW.id, CAST(COALESCE(NEW.stock_qty,0) - COALESCE(OLD.stock_qty,0) AS INTEGER), {_NOW}); END;",
                f"CREATE TRIGGER IF NOT EXISTS of_del_{table} AFTER DELETE ON {table} "
                f"WHEN {_CAPTURE_ON} BEGIN {_mark(table, 'OLD.id', 'delete')} END;",
            ]
            continue
        out += [
            f"CREATE TRIGGER IF NOT EXISTS of_ins_{table} AFTER INSERT ON {table} "
            f"WHEN {_CAPTURE_ON} BEGIN {_mark(table, 'NEW.id', 'upsert')} END;",
            f"CREATE TRIGGER IF NOT EXISTS of_upd_{table} AFTER UPDATE ON {table} "
            f"WHEN {_CAPTURE_ON} BEGIN {_mark(table, 'NEW.id', 'upsert')} END;",
            f"CREATE TRIGGER IF NOT EXISTS of_del_{table} AFTER DELETE ON {table} "
            f"WHEN {_CAPTURE_ON} BEGIN {_mark(table, 'OLD.id', 'delete')} END;",
        ]
    for line, (parent, col) in LINE_TABLES.items():
        if not _table_exists(conn, line):
            continue
        out += [
            f"CREATE TRIGGER IF NOT EXISTS of_ins_{line} AFTER INSERT ON {line} "
            f"WHEN {_CAPTURE_ON} AND NEW.{col} IS NOT NULL BEGIN {_mark(parent, f'NEW.{col}', 'upsert')} END;",
            f"CREATE TRIGGER IF NOT EXISTS of_upd_{line} AFTER UPDATE ON {line} "
            f"WHEN {_CAPTURE_ON} AND NEW.{col} IS NOT NULL BEGIN {_mark(parent, f'NEW.{col}', 'upsert')} END;",
            f"CREATE TRIGGER IF NOT EXISTS of_del_{line} AFTER DELETE ON {line} "
            f"WHEN {_CAPTURE_ON} AND OLD.{col} IS NOT NULL BEGIN {_mark(parent, f'OLD.{col}', 'upsert')} END;",
        ]
    if _table_exists(conn, "pharmacy_profile"):
        for ev in ("INSERT", "UPDATE"):
            out.append(
                f"CREATE TRIGGER IF NOT EXISTS of_{ev.lower()}_pharmacy_profile AFTER {ev} ON pharmacy_profile "
                f"WHEN {_CAPTURE_ON} BEGIN {_mark('pharmacy_profile', '0', 'upsert')} END;"
            )
    if _table_exists(conn, "settings"):
        # Shop-wide keys only (the same ones store_kv_carry moves between modes). The table
        # also holds this PC's own one-time migration markers, which must never reach
        # another device -- it would skip its own migration.
        shop_wide = " OR ".join(
            [f"NEW.name LIKE '{p}%'" for p in SHOP_WIDE_SETTING_PREFIXES]
            + [f"NEW.name = '{n}'" for n in SHOP_WIDE_SETTING_NAMES]
        )
        for ev in ("INSERT", "UPDATE"):
            out.append(
                f"CREATE TRIGGER IF NOT EXISTS of_{ev.lower()}_settings_v2 AFTER {ev} ON settings "
                f"WHEN {_CAPTURE_ON} AND ({shop_wide}) BEGIN {_mark('settings', 'NEW.id', 'upsert')} END;"
            )
    return out


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Create the bookkeeping tables and triggers (idempotent)."""
    conn.executescript(_TABLES)
    # Retired triggers (replaced by a narrower version).
    for old in ("of_insert_settings", "of_update_settings"):
        conn.execute(f"DROP TRIGGER IF EXISTS {old}")
    for stmt in trigger_sql(conn):
        conn.execute(stmt)
    conn.commit()


def drop_triggers(conn: sqlite3.Connection) -> None:
    """Remove the capture triggers (leaving offline-first)."""
    names = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE 'of\\_%' ESCAPE '\\'"
    ).fetchall()]
    for n in names:
        conn.execute(f"DROP TRIGGER IF EXISTS {n}")
    conn.commit()


class capture_paused:
    """Context manager: write pulled changes without noting them as this PC's own."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def __enter__(self):
        self.conn.execute("UPDATE of_flags SET v='0' WHERE k='capture'")
        return self

    def __exit__(self, *exc):
        self.conn.execute("UPDATE of_flags SET v='1' WHERE k='capture'")
        return False


def meta_get(conn: sqlite3.Connection, key: str, default=None):
    row = conn.execute("SELECT v FROM of_meta WHERE k=?", (key,)).fetchone()
    return row[0] if row else default


def meta_set(conn: sqlite3.Connection, key: str, value) -> None:
    conn.execute(
        "INSERT INTO of_meta (k, v) VALUES (?, ?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
        (key, None if value is None else str(value)),
    )
