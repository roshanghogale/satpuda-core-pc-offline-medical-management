"""Re-push a single local store by store_key or display name substring."""
from __future__ import annotations

import os
import sys


def _admin_token() -> str:
    """Ask for the Satpuda administrator credentials, here and now.

    This script used to call ``api.admin_login()`` with no arguments, which
    used the username and password compiled into the build. There is no such
    default any more -- see core/server_api.admin_login. Nothing is echoed and
    nothing is written anywhere.
    """
    import getpass

    user = input("Satpuda administrator username: ").strip()
    pw = getpass.getpass("Satpuda administrator password: ")
    from core import admin_session

    return admin_session.sign_in(user, pw)


def main() -> int:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if root not in sys.path:
        sys.path.insert(0, root)

    needle = (sys.argv[1] if len(sys.argv) > 1 else "Roshan").strip().lower()
    from core import server_api as api
    from core import server_sync
    from core.store_manager import list_stores, reconcile_registry_with_disk

    reconcile_registry_with_disk()
    stores = list_stores()
    match = None
    for s in stores:
        key = (s.get("store_key") or "").lower()
        name = server_sync._display_name(s).lower()
        if needle in key or needle in name:
            match = s
            break
    if not match:
        print("Stores:", [server_sync._display_name(s) for s in stores])
        print(f"No match for {needle!r}")
        return 1

    print(f"API: {api.api_base()}")
    print("Pushing:", server_sync._display_name(match), match.get("store_key"))
    remote = server_sync._remote_store_for(match, admin_token=_admin_token())
    token = server_sync._pair_for_store(remote, match.get("store_key") or "")
    conn = server_sync._open_store_db(match.get("store_key") or "")
    try:
        n = server_sync.push_store_conn(
            conn, token, progress_cb=print, label=server_sync._display_name(match)
        )
    finally:
        conn.close()
    print(f"Done: {n:,} upserted; key={remote.get('android_key')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
