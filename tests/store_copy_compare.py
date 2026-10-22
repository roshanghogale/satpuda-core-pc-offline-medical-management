"""Compare two store files made by the Online -> Offline copy (old path vs fast path).

Every table: the same rows (count) and the same values (a checksum of every column, rows in
id order). The only values allowed to differ are the "just now" stamps written while the copy
runs -- the medicine triggers' synced_at / updated_at, a column's DEFAULT CURRENT_TIMESTAMP:
for those the checksum only asks WHICH rows got such a stamp, and that set must be the same.

usage (any two copies of one store):  python tests/store_copy_compare.py a.db b.db
"""
from __future__ import annotations

import hashlib
import sqlite3
import sys

# Columns the copy itself may stamp with the time it ran.
_RUN_STAMPED = {"synced_at", "updated_at", "created_at"}
# Bookkeeping of the copy itself (when it started / finished), not the store's data.
_SKIP_TABLES = {"app_meta", "sqlite_stat1", "sqlite_stat4"}


def _tables(conn) -> list[str]:
    return [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]


def table_digest(conn, table: str, run_started: str) -> tuple[int, str]:
    cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
    order = "id" if "id" in cols else ", ".join(cols)
    h = hashlib.sha256()
    n = 0
    for row in conn.execute(f"SELECT {', '.join(cols)} FROM {table} ORDER BY {order}"):
        n += 1
        vals = []
        for c, v in zip(cols, row):
            if c in _RUN_STAMPED and isinstance(v, str) and v.replace(" ", "T") >= run_started:
                v = "<stamped during the copy>"
            vals.append(f"{type(v).__name__}:{v!r}")
        h.update("\x1f".join(vals).encode("utf-8"))
        h.update(b"\x1e")
    return n, h.hexdigest()


def compare(path_a: str, path_b: str, *, run_started: str = "2000") -> list[str]:
    """Differences between the two files ([] when the copies are the same).

    ``run_started``: an ISO time before both copies began; medicine stamps at or after it
    count as "written by the copy"."""
    a = sqlite3.connect(f"file:{path_a}?mode=ro", uri=True)
    b = sqlite3.connect(f"file:{path_b}?mode=ro", uri=True)
    try:
        diffs = []
        ta, tb = set(_tables(a)), set(_tables(b))
        if ta != tb:
            diffs.append(f"tables differ: only in A {sorted(ta - tb)}, only in B {sorted(tb - ta)}")
        for t in sorted((ta & tb) - _SKIP_TABLES):
            da, db_ = table_digest(a, t, run_started), table_digest(b, t, run_started)
            if da != db_:
                diffs.append(f"{t}: A {da[0]} rows {da[1][:12]} / B {db_[0]} rows {db_[1][:12]}")
        return diffs
    finally:
        a.close()
        b.close()


def summary(path: str) -> dict[str, int]:
    c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return {t: c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in _tables(c)}
    finally:
        c.close()


if __name__ == "__main__":
    a, b = sys.argv[1], sys.argv[2]
    since = sys.argv[3] if len(sys.argv) > 3 else "2000"
    found = compare(a, b, run_started=since)
    counts = summary(a)
    for t in ("medicines", "sales", "sales_items", "purchases", "purchase_items", "customers",
              "suppliers", "sales_returns", "purchase_returns", "customer_payments", "supplier_payments"):
        if t in counts:
            print(f"  {t:20} {counts[t]:>9,}")
    print("SAME" if not found else "DIFFERENT:\n  " + "\n  ".join(found))
    sys.exit(1 if found else 0)
