"""Sync mode preference — offline (local SQLite) vs online (server-only).

Only administrators may change sync mode (see database_tab Administrator panel).
Default: offline.
"""
from __future__ import annotations

import os

MODE_OFFLINE = 'offline'   # Local SQLite + Google Drive backup/restore
MODE_ONLINE = 'online'     # Server-only business data (no persistent store DB)

_VALID = frozenset({MODE_OFFLINE, MODE_ONLINE})
_DEFAULT = MODE_OFFLINE
_FILENAME = 'sync_mode.txt'


def _appdata_dir() -> str:
    from core.license_manager import _appdata_dir as _dir
    return _dir()


def _path() -> str:
    return os.path.join(_appdata_dir(), _FILENAME)


def get_sync_mode() -> str:
    try:
        p = _path()
        if os.path.isfile(p):
            mode = open(p, encoding='utf-8').read().strip().lower()
            if mode in _VALID:
                return mode
    except Exception:
        pass
    return _DEFAULT


def set_sync_mode(mode: str) -> None:
    mode = (mode or '').strip().lower()
    if mode not in _VALID:
        raise ValueError(f'Invalid sync mode: {mode}')
    previous = get_sync_mode()
    os.makedirs(os.path.dirname(_path()), exist_ok=True)
    with open(_path(), 'w', encoding='utf-8') as fh:
        fh.write(mode)
    if previous == MODE_OFFLINE and mode == MODE_ONLINE:
        # Server-only Online: no bootstrap download / watermark seed.
        try:
            from core.sync_bootstrap import clear_pending_bootstrap, mark_bootstrap_done
            clear_pending_bootstrap()
            mark_bootstrap_done()
        except Exception:
            pass
    if previous == MODE_ONLINE and mode == MODE_OFFLINE:
        # Leaving Online: ensure a local store DB exists for Offline work.
        try:
            from core.store_manager import ensure_active_store_db_exists
            ensure_active_store_db_exists()
        except Exception:
            try:
                from core.master_medicine_cloud import on_switch_to_offline_download
                on_switch_to_offline_download()
            except Exception as exc:
                print(f'[sync_prefs] Offline switch prep: {exc}')


def is_offline_mode() -> bool:
    return get_sync_mode() == MODE_OFFLINE


def is_online_mode() -> bool:
    return get_sync_mode() == MODE_ONLINE


def mode_label(mode: str | None = None) -> str:
    m = mode or get_sync_mode()
    return {
        MODE_OFFLINE: 'Offline — local SQLite + Google Drive',
        MODE_ONLINE: (
            'Online — server-only (no local store DB; '
            'migrate via Push or Drive restore→push→delete)'
        ),
    }.get(m, m)
