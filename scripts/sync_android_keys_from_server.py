"""Sync server android keys + JWT sessions for local PC stores."""
from __future__ import annotations

from core import server_api as api
from core.store_link import _save_local
from core.store_manager import (
    get_active_display_name,
    get_active_store_key,
    list_stores,
)


def _admin_token() -> str:
    """The administrator credentials, typed now -- there is no compiled default."""
    import getpass

    user = input("Satpuda administrator username: ").strip()
    pw = getpass.getpass("Satpuda administrator password: ")
    return api.admin_login(user, pw)


def main() -> None:
    admin = _admin_token()
    remotes = api.list_remote_stores(admin)
    by_key = {(s.get("store_key") or ""): s for s in remotes}
    print(f"server_stores={len(remotes)} local_stores={len(list_stores())}")
    print()
    print("ANDROID LINK KEYS (use exact store name + SC- key on phone)")
    print("=" * 64)
    for st in list_stores():
        sk = st.get("store_key") or ""
        name = (st.get("display_name") or sk).strip()
        remote = by_key.get(sk)
        if not remote:
            for cand in remotes:
                if (cand.get("store_name") or "").strip().lower() == name.lower():
                    remote = cand
                    break
        if not remote:
            print(f"MISSING on server: {name} ({sk})")
            continue
        key = (remote.get("android_key") or "").strip()
        session = api.ensure_store_session(
            store_key=sk or remote.get("store_key"),
            android_key=key,
            store_name=remote.get("store_name") or name,
            device_id=sk or remote.get("store_key"),
            force_pair=True,
        )
        print(f"Store name : {remote.get('store_name') or name}")
        print(f"PC key     : {sk}")
        print(f"SC- key    : {key}")
        print(f"Server id  : {session.get('store_id')}")
        print("-" * 64)

    ask = get_active_store_key() or ""
    aname = (get_active_display_name() or "").strip()
    active_remote = by_key.get(ask)
    if not active_remote:
        for cand in remotes:
            if (cand.get("store_name") or "").strip().lower() == aname.lower():
                active_remote = cand
                break
    if active_remote and active_remote.get("android_key"):
        _save_local(active_remote["android_key"])
        print(
            f"Active store local key file updated: {active_remote['android_key']} "
            f"({aname or ask})"
        )


if __name__ == "__main__":
    main()
