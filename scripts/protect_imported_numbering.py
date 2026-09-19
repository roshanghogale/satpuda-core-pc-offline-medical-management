"""Stop a shop's imported bill numbers from being renumbered on first open.

WHY THIS EXISTS
---------------
A shop that came from another system arrives with its OWN book: bills numbered
the way its customers already hold them on paper, holes and all. The desktop app
has two one-time migrations that renumber a book it has not seen before:

  core/fy_serial.migrate_fy_serial_numbers()   renumbers sales AND purchases per
      financial year, in DATE order, unless settings.fy_serial_migrated_v1 = '1'
  core/db_setup._migrate_purchase_numbers_compact()   renumbers purchases to a
      compact 1..N, unless settings.purchase_no_compact_migrated = '1'

Both are right for a book this app wrote itself. Both are wrong for an imported
book: on Swami Samarth's 703 bills the FY migration would move 485 of them onto
a different number, and the purchase migration would renumber all 148 of this
year's purchases and move the next purchase from 149 to 174. The customer's copy
would then say one thing and the shop's screen another.

So: before the app opens an imported file, close both gates.

THE ORDER MATTERS. migrate_fy_serial_numbers() returns at its gate check BEFORE
it calls _ensure_fy_columns(), and those two calls are the only place in the
product that adds sales/purchases.fy_start_year and .fy_serial. Setting the key
on a file that has no fy columns would therefore leave it without them for good.
This adds the columns first and sets the keys second, in one transaction.

Nothing here touches a bill number, a date or an amount. It adds two empty
columns per table and two settings rows.

USAGE
    python3 protect_imported_numbering.py <path to veterinary.db>          # look only
    python3 protect_imported_numbering.py <path to veterinary.db> --apply  # do it

On a shop PC the file is usually:
    %LOCALAPPDATA%\\VeterinaryApp\\stores\\<Store_Name>\\veterinary.db
Close the app first. Take a copy of the file before --apply if you want one.
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import sys

KEYS = ("fy_serial_migrated_v1", "purchase_no_compact_migrated")
FY_COLUMNS = ("fy_start_year", "fy_serial")


def columns(cur: sqlite3.Cursor, table: str) -> set[str]:
    try:
        return {str(r[1]) for r in cur.execute(f"PRAGMA table_info({table})")}
    except sqlite3.Error:
        return set()


def table_exists(cur: sqlite3.Cursor, table: str) -> bool:
    row = cur.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return bool(row)


def report(cur: sqlite3.Cursor) -> dict:
    state = {}
    for table in ("sales", "purchases"):
        if not table_exists(cur, table):
            state[table] = None
            continue
        cols = columns(cur, table)
        n = cur.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        state[table] = {
            "rows": int(n),
            "missing_columns": [c for c in FY_COLUMNS if c not in cols],
        }
    state["settings"] = {}
    if table_exists(cur, "settings"):
        for key in KEYS:
            row = cur.execute("SELECT value FROM settings WHERE name=?", (key,)).fetchone()
            state["settings"][key] = None if row is None else str(row[0])
    return state


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    path = sys.argv[1]
    apply_it = "--apply" in sys.argv[2:]
    if not os.path.isfile(path):
        print(f"REFUSING: no such file: {path}")
        return 1

    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    cur = con.cursor()
    if not table_exists(cur, "settings") or not table_exists(cur, "sales"):
        con.close()
        print(f"REFUSING: {path} does not look like a Satpuda store (no settings/sales table)")
        return 1
    before = report(cur)
    con.close()

    print(f"file: {path}")
    for table in ("sales", "purchases"):
        info = before.get(table)
        if info is None:
            print(f"  {table:10} table not present")
        else:
            missing = ", ".join(info["missing_columns"]) or "none"
            print(f"  {table:10} {info['rows']:6} rows   columns missing: {missing}")
    for key in KEYS:
        value = before["settings"].get(key)
        mark = "already closed" if value == "1" else "OPEN -- the app would renumber on first open"
        print(f"  gate {key:30} = {value!r}   {mark}")

    already = all(before["settings"].get(k) == "1" for k in KEYS)
    nothing_to_add = all(
        (before.get(t) is None or not before[t]["missing_columns"]) for t in ("sales", "purchases")
    )
    if already and nothing_to_add:
        print("\nNothing to do: both gates are closed and the columns are there.")
        return 0

    if not apply_it:
        print("\nLOOK ONLY. Re-run with --apply to close the gates (and take a copy first if you want one).")
        return 0

    backup = path + ".before_numbering_guard"
    if not os.path.exists(backup):
        shutil.copy2(path, backup)
        print(f"\ncopy kept: {backup}")

    con = sqlite3.connect(path)
    cur = con.cursor()
    try:
        cur.execute("BEGIN IMMEDIATE")
        added = []
        for table in ("sales", "purchases"):
            if not table_exists(cur, table):
                continue
            have = columns(cur, table)
            for col in FY_COLUMNS:
                if col not in have:
                    cur.execute(f"ALTER TABLE {table} ADD COLUMN {col} INTEGER")
                    added.append(f"{table}.{col}")
        for key in KEYS:
            cur.execute(
                "INSERT OR REPLACE INTO settings (name, value) VALUES (?, '1')", (key,)
            )
        con.commit()
    except Exception as exc:
        con.rollback()
        con.close()
        print(f"FAILED, nothing changed: {type(exc).__name__}: {exc}")
        return 1

    after = report(con.cursor())
    con.close()
    print(f"columns added: {', '.join(added) if added else 'none needed'}")
    for key in KEYS:
        print(f"  gate {key:30} = {after['settings'].get(key)!r}")
    ok = all(after["settings"].get(k) == "1" for k in KEYS)
    print("\nDONE -- the imported numbers will be left alone." if ok else "\nSOMETHING IS STILL OPEN -- look above.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
