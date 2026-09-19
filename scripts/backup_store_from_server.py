"""Build a restorable veterinary.db backup for one store, straight from the server.

The desktop's own backup can only copy the LOCAL veterinary.db. For a store that
is not paired on this machine there is no local file and no store key, so the
normal path cannot run at all. This reads the store's rows out of PostgreSQL and
writes them into a fresh SQLite created by the app's own schema builder, so the
result is byte-compatible with what the app produces and restores the same way.

The output filename and layout deliberately match the existing convention
(SatpudaCore_YYYY-MM-DD_HH-MM.db.gz under SatpudaCore_Backup/<Store Name>/) so
existing tooling and older builds keep working.

Usage:
    python3 scripts/backup_store_from_server.py "Shivkrupa Medical & General Store" [outdir]
"""
import gzip
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import db_setup  # noqa: E402
from core.server_live import _REPLACE_PULL_TABLES  # noqa: E402
from core.store_manager import display_name_key  # noqa: E402

# Tables the replace-pull deliberately leaves alone are still worth backing up.
EXTRA_TABLES = ("pharmacy_profile", "racks", "shelves", "sections", "boxes", "shelf_settings")

# Postgres does not always use the same table name as local SQLite. Getting this
# wrong is silent and costly: "pharmacy_profile" (local) is "pharmacy_profiles"
# (server), so the pull found no columns, skipped the table, and every backup
# kept whatever profile db_setup seeded -- one Shivkrupa backup carried the name
# "ZZ Test Pharmacy" and would have printed that on the restored store's bills.
SERVER_TABLE_ALIASES = {"pharmacy_profile": "pharmacy_profiles"}
SSH_HOST = "satpuda"
REMOTE = "cd /opt/Satpuda-Core-Server && set -a && . ./.env && set +a && psql \"$DATABASE_URL\" -t -A -c"


def psql(sql: str) -> str:
    out = subprocess.run(
        ["ssh", SSH_HOST, f"{REMOTE} {json.dumps(sql)}"],
        capture_output=True, text=True, timeout=600,
    )
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip()[:400])
    return out.stdout


def server_columns(table: str) -> list[str]:
    raw = psql(
        "SELECT column_name FROM information_schema.columns "
        f"WHERE table_name='{table}' ORDER BY ordinal_position"
    )
    return [c.strip() for c in raw.splitlines() if c.strip()]


def fetch_rows(table: str, cols: list[str], store_pk: int) -> list[dict]:
    sel = ", ".join(f'"{c}"' for c in cols)
    raw = psql(
        f"SELECT COALESCE(json_agg(t), '[]') FROM "
        f"(SELECT {sel} FROM \"{table}\" WHERE store_pk={store_pk}) t"
    )
    return json.loads(raw.strip() or "[]")



def capture_store_config(conn, store_pk: int) -> dict:
    """Copy the server-only config tables into the local `settings` key/value table.

    store_settings and store_dropdowns exist ONLY on the server -- the local schema
    has no such tables, so a straight table-for-table pull silently dropped them.
    That lost the shop's village list (their customer-address autocomplete), the
    medicine type and schedule lists, and every billing-layout preference. Locally
    all of that lives in `settings`: villages under "customer_villages", the rest
    inside the "billing_layout_prefs" JSON blob.
    """
    written = {}

    raw = psql(
        "SELECT COALESCE(json_agg(t), '[]') FROM (SELECT name, value FROM store_settings "
        f"WHERE store_pk={store_pk}) t"
    )
    layout = {}
    for row in json.loads(raw.strip() or "[]"):
        if row.get("name") == "billing_layout_prefs":
            val = row.get("value")
            layout = val if isinstance(val, dict) else json.loads(val or "{}")

    raw = psql(
        "SELECT COALESCE(json_agg(t), '[]') FROM (SELECT villages, default_village, "
        f"med_types, schedules FROM store_dropdowns WHERE store_pk={store_pk}) t"
    )
    drops = (json.loads(raw.strip() or "[]") or [{}])[0]

    def _list(v):
        if isinstance(v, list):
            return [str(x) for x in v]
        try:
            out = json.loads(v or "[]")
            return [str(x) for x in out] if isinstance(out, list) else []
        except Exception:
            return []

    villages = _list(drops.get("villages"))
    med_types = _list(drops.get("med_types"))
    schedules = _list(drops.get("schedules"))
    if med_types:
        layout["med_types"] = med_types
    if schedules:
        layout["schedules"] = schedules

    rows = []
    if villages:
        rows.append(("customer_villages", json.dumps(villages, ensure_ascii=False)))
        written["customer_villages"] = len(villages)
    default_village = str(drops.get("default_village") or "").strip()
    if default_village:
        rows.append(("default_customer_village", default_village))
        written["default_customer_village"] = 1
    if layout:
        rows.append(("billing_layout_prefs", json.dumps(layout, ensure_ascii=False)))
        written["billing_layout_prefs"] = len(layout)

    if rows:
        conn.executemany(
            "INSERT OR REPLACE INTO settings (name, value) VALUES (?, ?)", rows
        )
    return written


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    store_name = sys.argv[1]
    outdir = sys.argv[2] if len(sys.argv) > 2 else os.path.join(
        os.path.expanduser("~"), "satpuda-backups"
    )

    row = psql(
        "SELECT id FROM stores WHERE store_name = "
        f"{json.dumps(store_name).replace(chr(34), chr(39))} LIMIT 1"
    ).strip()
    if not row:
        print(f"  store not found on server: {store_name!r}")
        return 1
    store_pk = int(row)
    print(f"  store: {store_name}  (store_pk={store_pk})")

    work = tempfile.mkdtemp(prefix="satpuda_store_backup_")
    db_path = os.path.join(work, "veterinary.db")
    conn = sqlite3.connect(db_path)
    db_setup.initialise(conn)          # the app's own schema, so restore works
    print("  schema created by the app's own initialiser")

    local_tables = {
        r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }

    counts: dict[str, int] = {}
    skipped: list[str] = []
    for table in list(_REPLACE_PULL_TABLES) + list(EXTRA_TABLES):
        if table not in local_tables:
            skipped.append(f"{table} (no such local table)")
            continue
        remote = SERVER_TABLE_ALIASES.get(table, table)
        try:
            scols = set(server_columns(remote))
        except Exception as exc:
            skipped.append(f"{table} (server read failed: {str(exc)[:60]})")
            continue
        if not scols:
            skipped.append(f"{table} (server table {remote!r} has no columns)")
            continue
        lcols = [c[1] for c in conn.execute(f"PRAGMA table_info({table})")]
        shared = [c for c in lcols if c in scols]
        if not shared:
            skipped.append(f"{table} (no columns in common with {remote!r})")
            continue
        try:
            rows = fetch_rows(remote, shared, store_pk)
        except Exception as exc:
            skipped.append(f"{table} (fetch failed: {str(exc)[:60]})")
            continue
        # Wipe before insert: this file must be a PURE mirror of the server.
        # db_setup.initialise() seeds pharmacy_profile from a sidecar belonging to
        # whatever store THIS machine is paired to, so a Shivkrupa backup built on
        # a ZZ-Test-paired Mac ended up with "ZZ Test Pharmacy" at rowid 1 -- and
        # every profile read is "SELECT ... LIMIT 1", so the restored shop would
        # have printed the wrong name, address and phone on its bills.
        conn.execute(f"DELETE FROM {table}")
        if rows:
            ph = ",".join("?" * len(shared))
            conn.executemany(
                f"INSERT OR REPLACE INTO {table} ({','.join(shared)}) VALUES ({ph})",
                [tuple(r.get(c) for c in shared) for r in rows],
            )
        counts[table] = len(rows)
        note = f" <- {remote}" if remote != table else ""
        print(f"    {table:<22} {len(rows):>6} rows{note}")

    # Never skip silently again -- a skipped table is data missing from the backup.
    if skipped:
        print("\n  SKIPPED (not captured):")
        for line in skipped:
            print(f"    ! {line}")
    cfg = capture_store_config(conn, store_pk)
    if cfg:
        print("\n  store config -> local settings table:")
        for k, v in cfg.items():
            print(f"    {k:<26} {v}")
    else:
        print("\n  ! no store_settings / store_dropdowns found on the server")

    conn.commit()
    conn.close()

    ts = datetime.now().strftime("%Y-%m-%d_%H-%M")
    filename = f"SatpudaCore_{ts}.db.gz"          # unchanged convention
    # The folder name MUST come from the app's own display_name_key(): both the
    # Drive restore (_find_store_subfolder_id) and the pendrive layout look for
    # "Store_<sanitised>", NOT the raw display name. Hand-rolling the sanitiser
    # here produced "Shivkrupa Medical & General Store" instead of
    # "Store_Shivkrupa_Medical_General_Store", so the app could not find the file.
    safe = display_name_key(store_name)
    dest_dir = os.path.join(outdir, "SatpudaCore_Backup", safe)
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, filename)
    with open(db_path, "rb") as f_in, gzip.open(dest, "wb") as f_out:
        shutil.copyfileobj(f_in, f_out)

    size = os.path.getsize(dest)
    print(f"\n  wrote {dest}  ({size:,} bytes gzipped)")
    shutil.rmtree(work, ignore_errors=True)
    print(json.dumps(counts, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
