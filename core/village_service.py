"""Customer village list for sales address dropdown."""
from __future__ import annotations

import json
import os


_VILLAGES_KEY = "customer_villages"
_DEFAULT_KEY = "default_customer_village"


def _ensure_settings(conn):
    conn.cursor().execute("""
        CREATE TABLE IF NOT EXISTS settings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE,
            value TEXT
        )
    """)


def _get_setting(conn, name: str, default: str = "") -> str:
    _ensure_settings(conn)
    cur = conn.cursor()
    cur.execute("SELECT value FROM settings WHERE name=?", (name,))
    row = cur.fetchone()
    return row[0] if row and row[0] is not None else default


def _set_setting(conn, name: str, value: str) -> None:
    _ensure_settings(conn)
    conn.cursor().execute(
        "INSERT OR REPLACE INTO settings (name, value) VALUES (?, ?)",
        (name, value),
    )
    conn.commit()


def _config_dir() -> str:
    try:
        from core.layout_config import _get_config_dir

        return _get_config_dir()
    except Exception:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        return os.path.join(root, "config")


def villages_mirror_path() -> str:
    """Durable file so villages survive Online (in-memory DB) restarts."""
    return os.path.join(_config_dir(), "customer_villages.json")


def _normalize_list(values) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()
    if isinstance(values, str):
        try:
            values = json.loads(values)
        except Exception:
            values = [values]
    if not isinstance(values, list):
        return cleaned
    for v in values:
        text = str(v).strip()
        if not text:
            continue
        key = text.upper()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(text)
    return cleaned


def read_villages_mirror() -> tuple[list[str], str]:
    path = villages_mirror_path()
    if not os.path.isfile(path):
        return [], ""
    try:
        data = json.loads(open(path, encoding="utf-8").read())
        if isinstance(data, list):
            return _normalize_list(data), ""
        if isinstance(data, dict):
            return (
                _normalize_list(data.get("villages")),
                str(data.get("default_village") or "").strip(),
            )
    except Exception:
        pass
    return [], ""


def write_villages_mirror(villages: list[str], default_village: str = "") -> None:
    path = villages_mirror_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        payload = {
            "villages": _normalize_list(villages),
            "default_village": str(default_village or "").strip(),
        }
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
    except Exception:
        pass


def load_villages(conn) -> list[str]:
    raw = _get_setting(conn, _VILLAGES_KEY, "[]")
    try:
        data = json.loads(raw)
        if isinstance(data, list):
            villages = [str(v).strip() for v in data if str(v).strip()]
            if villages:
                return villages
    except Exception:
        pass
    # Online memory DB is empty after restart — fall back to durable mirror.
    mirrored, _ = read_villages_mirror()
    return mirrored


def get_default_village(conn) -> str:
    current = _get_setting(conn, _DEFAULT_KEY, "").strip()
    if current:
        return current
    _, default = read_villages_mirror()
    return default


def save_villages(
    conn,
    villages: list[str],
    default_village: str = "",
    *,
    sync: bool = True,
) -> None:
    cleaned = _normalize_list(villages)
    default = (default_village or "").strip()
    if default and default.upper() not in {x.upper() for x in cleaned}:
        cleaned.insert(0, default)
    if default and default.upper() not in {x.upper() for x in cleaned}:
        default = cleaned[0] if cleaned else ""
    elif default:
        for v in cleaned:
            if v.upper() == default.upper():
                default = v
                break
    _set_setting(conn, _VILLAGES_KEY, json.dumps(cleaned, ensure_ascii=False))
    _set_setting(conn, _DEFAULT_KEY, default)
    write_villages_mirror(cleaned, default)
    if not sync:
        return
    from core.sync_coordinator import after_villages_saved

    after_villages_saved(conn)


def add_village(conn, name: str) -> list[str]:
    villages = load_villages(conn)
    text = str(name).strip()
    if not text:
        return villages
    if any(v.upper() == text.upper() for v in villages):
        return villages
    villages.append(text)
    default = get_default_village(conn)
    save_villages(conn, villages, default)
    return villages


def ensure_village_from_address(conn, address: str, *, silent: bool = False) -> list[str]:
    """Add a typed customer address as a Settings village when it is new.

    Used by Contacts and Sales so the Villages list and dropdowns stay in sync.
    """
    text = str(address or "").strip()
    if not text:
        return load_villages(conn)
    try:
        return add_village(conn, text)
    except Exception:
        if silent:
            return load_villages(conn)
        raise


def village_names_for_ui(conn) -> list[str]:
    return list(load_villages(conn))


def merge_village_lists(*lists: list[str]) -> list[str]:
    return _normalize_list([v for lst in lists for v in (lst or [])])


def hydrate_villages_for_conn(conn) -> dict:
    """Load villages into SQLite from durable mirror + Online server (no wipe).

    Online mode uses an in-memory DB, so this must run after every engine start.
    """
    mirrored, mirror_default = read_villages_mirror()
    server_villages: list[str] = []
    server_default = ""
    server_ok = False
    server_err = ""
    try:
        from core.sync_prefs import is_online_mode

        if is_online_mode():
            from core import server_api as api
            from core.server_live import _token

            doc = api.get_settings_dropdowns(_token()) or {}
            if isinstance(doc, dict):
                server_villages = _normalize_list(doc.get("villages"))
                server_default = str(doc.get("default_village") or "").strip()
                server_ok = True
    except Exception as exc:
        server_ok = False
        server_err = str(exc)

    local = []
    try:
        raw = _get_setting(conn, _VILLAGES_KEY, "[]")
        data = json.loads(raw)
        if isinstance(data, list):
            local = [str(v).strip() for v in data if str(v).strip()]
    except Exception:
        local = []

    merged = merge_village_lists(local, mirrored, server_villages)
    default = (
        _get_setting(conn, _DEFAULT_KEY, "").strip()
        or server_default
        or mirror_default
        or (merged[0] if merged else "")
    )
    save_villages(conn, merged, default, sync=False)

    pushed = False
    if server_ok:
        server_keys = {v.upper() for v in server_villages}
        if any(v.upper() not in server_keys for v in merged):
            try:
                from core.sync_coordinator import after_villages_saved

                after_villages_saved(conn)
                pushed = True
            except Exception:
                pushed = False

    return {
        "ok": True,
        "count": len(merged),
        "server_ok": server_ok,
        "server_error": server_err,
        "pushed": pushed,
    }
