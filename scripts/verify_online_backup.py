"""Prove an Online store is actually captured by the backup.

Before this fix the backup copied the local veterinary.db. An Online store keeps
its data on the server, so that file was either missing (backup skipped
silently) or frozen at whatever it held before the store went online -- one real
store's newest backup contained 4,142 sales while the server had 4,410.

This runs the real _do_backup with only the two OUTBOUND steps stubbed (Google
Drive and pendrive), so the capture, snapshot and gzip all execute for real, and
then inspects what was about to be uploaded.
"""
import gzip
import os
import shutil
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import backup_manager as bm  # noqa: E402
from core import store_query_client as sq  # noqa: E402
from core.sync_prefs import is_online_mode  # noqa: E402


def rows(res):
    return res.get("rows", []) if isinstance(res, dict) else (res or [])


def main() -> int:
    assert is_online_mode(), "this check is about Online stores"
    print(f"  active store db path: {bm._db_path()}")
    print(f"  exists locally: {os.path.exists(bm._db_path())}")

    captured = {}

    def fake_drive(tmp_gz, filename, store_name, protected):
        # Inspect the gzip that WOULD have been uploaded.
        work = tempfile.mkdtemp(prefix="verify_backup_")
        out = os.path.join(work, "veterinary.db")
        with gzip.open(tmp_gz, "rb") as f_in, open(out, "wb") as f_out:
            shutil.copyfileobj(f_in, f_out)
        conn = sqlite3.connect(out)
        for t in ("sales", "purchases", "medicines", "customers"):
            try:
                captured[t] = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            except Exception as exc:
                captured[t] = f"ERR {exc}"
        conn.close()
        shutil.rmtree(work, ignore_errors=True)
        captured["_uploaded_as"] = filename

    # Stub only the outbound steps; everything before them runs for real.
    bm._do_pendrive_backup = lambda *a, **k: None
    bm._upload_to_drive = fake_drive if hasattr(bm, "_upload_to_drive") else fake_drive
    bm._is_internet_available = lambda *a, **k: False   # stop before Drive

    # _do_pendrive_backup receives the same gzip, so inspect there instead.
    bm._do_pendrive_backup = fake_drive

    print("\n  running the real backup path...")
    bm._do_backup(force=True, trigger="manual")

    if not captured:
        print("  NOTHING WAS PREPARED FOR UPLOAD -- the backup skipped.")
        return 1

    print(f"\n  captured for upload: {captured.get('_uploaded_as')}")
    server = {
        "sales": len(rows(sq.list_sales(limit=5000))),
        "purchases": len(rows(sq.list_purchases(limit=5000))),
        "medicines": len(rows(sq.list_inventory(limit=5000))),
        "customers": len(rows(sq.list_customers(limit=5000))),
    }
    ok = True
    for t, want in server.items():
        got = captured.get(t)
        match = got == want
        ok = ok and match
        print(f"    {t:<12} backup={got:<6} server={want:<6} {'MATCH' if match else 'MISMATCH'}")
    print(f"\n  {'PASS - the backup contains the live server data' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
