"""One real desktop engine for the LAN tests (tests/test_lan_main_counter.py), in its own process.

    python tests/lan_engine_child.py --db STORE.db --port 18765 --lan-dir DIR --install pc-test-main

It serves the real engine handler (core.desktop_api._DesktopApiHandler) on 127.0.0.1:<port> with
the store file open as offline-first, and starts the LAN role written in <lan-dir>/lan_config.json.
No server is contacted (the offline-first sync loop is not started, and every server call fails),
and port 8765 is never touched. It stops when its stdin closes.
"""
from __future__ import annotations

import argparse
import os
import sys
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--lan-dir", required=True)
    ap.add_argument("--install", required=True)
    ap.add_argument("--saves", required=True)
    args = ap.parse_args()
    if args.port == 8765:
        raise SystemExit("never on 8765")

    os.environ["SATPUDA_LAN_DIR"] = args.lan_dir
    os.environ["SATPUDA_AUTOSAVE_SESSION_FILE"] = os.path.join(args.lan_dir, "autosave_sessions.json")

    from core import sync_prefs

    sync_prefs.get_sync_mode = lambda: sync_prefs.MODE_OFFLINE_FIRST

    from core.offline_first import client, worker

    def no_server(*_a, **_k):
        raise RuntimeError("no server in this test")

    client._call = no_server
    client.changes = no_server
    client.head_revision = no_server
    client.install_id = lambda: args.install
    worker.start_for = lambda path: None

    from core import autosave_session, desktop_sales_service

    autosave_session._store_key = lambda: "lan-test-store"
    desktop_sales_service._has_pharmacy_profile = lambda *a, **k: True
    desktop_sales_service._try_save_sale_pdf = lambda *a, **k: None
    import core.bill_save_prefs as bsp

    os.makedirs(args.saves, exist_ok=True)
    bsp.resolve_sales_bill_save_dir = lambda *a, **k: args.saves
    bsp.resolve_exports_save_dir = lambda *a, **k: os.path.join(args.saves, "Exports")
    # Printing: the bill is "printed" by writing which PC printed which bill.
    import core.bill_output as bo

    def fake_print(conn, sale_id, slot, *a, **k):
        row = conn.execute("SELECT bill_no FROM sales WHERE id=?", (int(sale_id),)).fetchone()
        with open(os.path.join(args.lan_dir, "printed.txt"), "a", encoding="utf-8") as fh:
            fh.write(f"{sale_id}\t{row[0] if row else ''}\n")
        return "", None

    bo.print_bill_with_slot = fake_print
    bo.print_bill_silent_with_slot = fake_print

    import core.desktop_api as d

    conn = d._open_conn(args.db)
    d._db.update({"conn": conn, "path": args.db, "server_only": False, "migrate_gate": {}})
    d._port = args.port
    srv = d._ReusableHTTPServer(("127.0.0.1", args.port), d._DesktopApiHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    d.start_lan()
    print("READY", flush=True)
    try:
        sys.stdin.read()
    finally:
        from core.lan import service

        service.stop()
        srv.shutdown()


if __name__ == "__main__":
    main()
