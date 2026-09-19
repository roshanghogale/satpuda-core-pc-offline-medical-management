"""Local sync head revision cursor (Option B / Phase B1)."""
from __future__ import annotations

import json
import os
import threading
from typing import Optional

_FILENAME = "sync_head_revision.json"
_lock = threading.Lock()


def _appdata_dir() -> str:
    from core.license_manager import _appdata_dir as d

    return d()


def _path() -> str:
    return os.path.join(_appdata_dir(), _FILENAME)


def _store_key() -> str:
    try:
        from core.store_manager import get_active_store_key

        return (get_active_store_key() or "default").strip() or "default"
    except Exception:
        return "default"


def _load_all() -> dict:
    p = _path()
    try:
        if os.path.isfile(p):
            with open(p, encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {}


def _save_all(data: dict) -> None:
    os.makedirs(_appdata_dir(), exist_ok=True)
    tmp = _path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
    os.replace(tmp, _path())


def get_head_revision(store_key: Optional[str] = None) -> int:
    key = (store_key or _store_key()).strip() or "default"
    with _lock:
        data = _load_all()
        try:
            return max(0, int(data.get(key) or 0))
        except (TypeError, ValueError):
            return 0


def set_head_revision(revision: int, store_key: Optional[str] = None) -> int:
    key = (store_key or _store_key()).strip() or "default"
    rev = max(0, int(revision or 0))
    with _lock:
        data = _load_all()
        data[key] = rev
        _save_all(data)
    _mirror_to_sqlite(rev)
    return rev


def bump_head_revision(revision: int, store_key: Optional[str] = None) -> int:
    """Advance local head if revision is greater; never move backward."""
    key = (store_key or _store_key()).strip() or "default"
    rev = max(0, int(revision or 0))
    with _lock:
        data = _load_all()
        cur = 0
        try:
            cur = max(0, int(data.get(key) or 0))
        except (TypeError, ValueError):
            cur = 0
        if rev > cur:
            data[key] = rev
            _save_all(data)
            cur = rev
    if rev >= cur:
        _mirror_to_sqlite(cur)
    return cur


def _mirror_to_sqlite(revision: int) -> None:
    """Best-effort mirror into settings table for DB-local visibility."""
    try:
        from core.db_utils import open_store_db
        from core.store_manager import get_active_db_path

        path = get_active_db_path()
        if not path or not os.path.isfile(path):
            return
        conn = open_store_db(path, timeout=5.0)
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS settings (
                    name TEXT PRIMARY KEY,
                    value TEXT
                )
                """
            )
            conn.execute(
                "INSERT INTO settings (name, value) VALUES (?, ?) "
                "ON CONFLICT(name) DO UPDATE SET value=excluded.value",
                ("sync_head_revision", str(int(revision))),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        pass
