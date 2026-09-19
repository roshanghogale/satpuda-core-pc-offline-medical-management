"""Live-safe probe for the Online -> Offline fallback download.

`download_store_for_offline()` takes no path argument: it resolves the ACTIVE
store database and, via `replace_local=True`, DELETEs every business row before
refilling from the server. Run unpatched on a real machine it would destroy the
active store, so this probe redirects the three path resolvers it depends on to
a throwaway directory and asserts afterwards that the real database was not
touched.

Run as its own short-lived process while the app is ONLINE:

    python3 scripts/probe_offline_download.py

It is read-only against the server (paged GETs) and writes only under a temp dir.
"""
import os
import shutil
import sqlite3
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from core.store_manager import get_active_db_path, get_active_store_key  # noqa: E402
from core.sync_prefs import is_online_mode  # noqa: E402


def main() -> int:
    real_db = get_active_db_path()
    print(f"  active store : {get_active_store_key()}")
    print(f"  real db      : {real_db}")

    # The download only pulls while Online; offline it returns skipped_offline
    # and would leave an empty mirror, which is the bug this feature fixes.
    assert is_online_mode(), "must stay ONLINE for the download to pull anything"

    before = None
    if os.path.isfile(real_db):
        st = os.stat(real_db)
        before = (st.st_mtime_ns, st.st_size)
        print(f"  real db size : {st.st_size} bytes (must be unchanged at the end)")

    tmp = tempfile.mkdtemp(prefix="satpuda_offline_probe_")
    throwaway = os.path.join(tmp, "store", "veterinary.db")
    os.makedirs(os.path.dirname(throwaway), exist_ok=True)
    assert os.path.abspath(throwaway) != os.path.abspath(real_db)

    from core import online_migrate as om
    from core import sync_bootstrap as boot
    from core import sync_watermarks as wm

    # 1. the DB the download writes (online_migrate resolves this at call time)
    om.store_db_path = lambda *a, **k: throwaway
    # 2. machine-wide sync bookkeeping that lives in config/, not in the db --
    #    a replace-pull clears these, and leaking that into the live app would
    #    misdirect its own sync state.
    wm._path = lambda: os.path.join(tmp, "watermarks.json")
    boot._done_path = lambda *a, **k: os.path.join(tmp, "bootstrap_done.txt")

    print("\n  downloading store for offline use (into the throwaway path)...")
    steps: list[str] = []
    res = om.download_store_for_offline(progress_cb=lambda m: steps.append(str(m)))
    print(f"  result       : {res}")
    for s in steps[:8]:
        print(f"    - {s}")

    ok = True

    def check(label: str, cond: bool, detail: str = "") -> None:
        nonlocal ok
        ok = ok and bool(cond)
        print(f"  {'PASS' if cond else 'FAIL'}  {label}{(' -- ' + detail) if detail else ''}")

    check("download reported ok", bool(res and res.get("ok")), str(res))
    check("throwaway db created", os.path.isfile(throwaway))

    if os.path.isfile(throwaway):
        conn = sqlite3.connect(throwaway)
        counts = {}
        for t in ("medicines", "sales", "purchases", "customers", "suppliers"):
            try:
                counts[t] = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            except Exception as exc:  # table missing -> schema not built
                counts[t] = f"ERR {exc}"
        print(f"  mirror rows  : {counts}")
        check("schema created and rows pulled", isinstance(counts.get("medicines"), int)
              and counts["medicines"] > 0, str(counts.get("medicines")))

        # The replay double-counted stock (a purchase of 200 landed as 400);
        # _restore_server_stock overwrites it with the server's figure.
        from core import store_query_client as sq
        server = {
            (str(m.get("name") or "").strip().upper(), str(m.get("batch_no") or "").strip().upper()):
                float(m.get("stock_qty") or 0)
            for m in sq.list_inventory(limit=5000).get("rows", [])
        }
        local = {
            (str(n or "").strip().upper(), str(b or "").strip().upper()): float(s or 0)
            for n, b, s in conn.execute(
                "SELECT name, batch_no, stock_qty FROM medicines WHERE COALESCE(deleted,0)=0"
            )
        }
        shared = set(server) & set(local)
        mismatched = {k: (server[k], local[k]) for k in shared if abs(server[k] - local[k]) > 0.001}
        print(f"  compared {len(shared)} batches against the server")
        check("mirror stock equals server stock", not mismatched, str(list(mismatched.items())[:4]))

        conn.close()

        # A freshly downloaded mirror must not trigger the migrate prompt.
        check("is_clean_prepared_mirror(fresh) is True",
              om.is_clean_prepared_mirror(throwaway) is True)

        # ...and must stop being clean once real offline work lands in it.
        # The marker check looks at the document tables (sales, purchases,
        # returns, payments), so dirty one of those -- a pending medicine row
        # is deliberately not treated as offline work.
        conn = sqlite3.connect(throwaway)
        try:
            conn.execute(
                "UPDATE sales SET sync_status='pending' "
                "WHERE id=(SELECT id FROM sales LIMIT 1)"
            )
            conn.commit()
            dirtied = conn.execute(
                "SELECT COUNT(*) FROM sales WHERE COALESCE(sync_status,'synced')<>'synced'"
            ).fetchone()[0]
        finally:
            conn.close()
        check("a pending sale was actually written", int(dirtied) > 0, str(dirtied))
        check("is_clean_prepared_mirror(dirty) is False",
              om.is_clean_prepared_mirror(throwaway) is False)

    if before is not None:
        st = os.stat(real_db)
        check("REAL store database untouched",
              (st.st_mtime_ns, st.st_size) == before,
              f"{before} -> {(st.st_mtime_ns, st.st_size)}")

    shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n  {'ALL CHECKS PASSED' if ok else 'SOME CHECKS FAILED'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
