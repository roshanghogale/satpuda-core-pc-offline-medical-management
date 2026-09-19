"""Durable copy of the store's `settings` table, per store, in AppData.

Online mode runs on sqlite3.connect(":memory:") -- there is no store file behind
it. Everything the `settings` table holds therefore lived only for the life of
the process: low-stock and near-expiry thresholds, due-alert minimums, reorder
quantities, the show-location flag. Closing the app, or switching between Online
and Offline, silently put every one of them back to its default, and the shop had
to set them again.

This module keeps a JSON mirror beside the other per-store files. Writes go to
both the table and the mirror; opening an in-memory shell reloads from the
mirror. Offline keeps using its real table as before -- the mirror simply follows
along, so the two modes agree instead of drifting apart.

Deliberately NOT mirrored: bookkeeping rows the sync engine owns (watermarks,
migration markers, FY flags). Those belong to one database's own state and must
not be carried across a mode switch.
"""
from __future__ import annotations

import json
import os
from typing import Any

# Rows that describe THIS database's sync/migration state, not the shop's
# preferences. Copying these between modes would confuse the sync engine.
#
# bill_logo_image / home_banner_image are the shop's pictures, carried with the
# store as base64 in the same key/value rows (see core.store_images). They are
# preferences, but they are also the only rows here measured in megabytes, and
# store_images already keeps the file itself in AppData -- mirroring them would
# copy the same picture into a JSON file for every store on the PC to no end.
_SKIP_PREFIXES = (
    "sync_",
    "startup_migration",
    "purchase_no_compact",
    "last_pull_",
    "watermark",
    "bootstrap",
    "fy_serial_",
    "bill_logo_image",
    "home_banner_image",
)


def _mirror_path() -> str:
    from core.license_manager import _appdata_dir
    from core.store_manager import get_active_store_key

    folder = os.path.join(_appdata_dir(), "settings_mirror")
    os.makedirs(folder, exist_ok=True)
    key = (get_active_store_key() or "default").strip() or "default"
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in key)
    return os.path.join(folder, f"{safe}.json")


def _is_durable(name: str) -> bool:
    n = str(name or "").strip().lower()
    if not n:
        return False
    return not any(n.startswith(p) for p in _SKIP_PREFIXES)


def load_all() -> dict[str, str]:
    """Every mirrored setting for the active store."""
    try:
        path = _mirror_path()
        if not os.path.isfile(path):
            return {}
        with open(path, encoding="utf-8-sig") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            return {str(k): str(v) for k, v in data.items() if _is_durable(k)}
    except Exception:
        pass
    return {}


def remember(name: str, value: Any) -> None:
    """Record one setting so it survives a restart or a mode switch."""
    if not _is_durable(name):
        return
    try:
        current = load_all()
        current[str(name)] = "" if value is None else str(value)
        path = _mirror_path()
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(current, fh, indent=2, sort_keys=True)
        os.replace(tmp, path)
    except Exception:
        pass


def capture_from(conn) -> int:
    """Merge a real store's settings table into the mirror. Returns rows kept.

    Merged, never replaced. Opening the Offline store used to overwrite the
    whole file, so every setting the shop had changed while Online -- which
    lives only in the mirror -- was silently deleted the first time they went
    back offline.
    """
    try:
        rows = conn.execute("SELECT name, value FROM settings").fetchall()
    except Exception:
        return 0
    keep = {
        str(n): ("" if v is None else str(v))
        for n, v in rows
        if _is_durable(n)
    }
    if not keep:
        return 0
    merged = load_all()
    merged.update(keep)
    try:
        path = _mirror_path()
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(merged, fh, indent=2, sort_keys=True)
        os.replace(tmp, path)
    except Exception:
        return 0
    return len(keep)


def apply_to(conn) -> int:
    """Load the mirror into a connection's settings table. Returns rows applied.

    Existing rows win: this fills gaps, it never overwrites what the database
    already holds, so an Offline store's own table stays authoritative.
    """
    data = load_all()
    if not data:
        return 0
    applied = 0
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS settings (name TEXT PRIMARY KEY, value TEXT)"
        )
        for name, value in data.items():
            try:
                cur = conn.execute(
                    "INSERT OR IGNORE INTO settings (name, value) VALUES (?, ?)",
                    (name, value),
                )
                # Count what was actually inserted; OR IGNORE silently drops a
                # row the database already had, and reporting those as restored
                # overstated what came back.
                applied += int(getattr(cur, "rowcount", 0) or 0)
            except Exception:
                continue
        conn.commit()
    except Exception:
        return applied
    return applied
