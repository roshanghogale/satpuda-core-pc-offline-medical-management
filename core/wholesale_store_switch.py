"""The Retail | Wholesale switch OPENS THE OTHER STORE (owner, 18 Oct 2026).

Wholesale and retail are two different stores on the server -- different medicines, rates,
purchases, sales and stock (D:\\projects\\WHOLESALE_DESIGN.md, "Wholesale is a SEPARATE
store"). The open store's licence / pairing answer names the other one (core/license_manager:
a retail store's ``wholesale_store``, a wholesale store's ``retail_store``:
{store_id, store_key, store_name, android_key}). ``switch_to(mode)``:

1. finds that store on this PC -- the adoption record of its server store id, or a local
   store paired with its SC- key -- or ADDS it: a new local store under its own name, paired
   with its SC- key, linked the way ``server_live.add_server_store`` links one (registry,
   per-store key file, adoption record). No administrator password: the key came from the
   server in the open store's own answer, so it can only ever reach that one store;
2. makes sure it has a server session (``server_api.ensure_store_session`` with its key);
3. offline-first: gives it ITS OWN offline-first copy (core/offline_first/runtime
   ``activate_from_online``, run for that store in a background thread with
   ``store_manager.acting_for_store``) BEFORE the registry is pointed at it -- a failed copy
   leaves the PC on the store it was on. The progress is the offline-first activation
   progress (GET /api/offline-first/activate/progress);
4. opens it as the Settings "Switch store" does: ``store_manager.set_active_store`` (store
   caches dropped) and ``desktop_api.reopen_active_store`` (the store file / Online runtime,
   the sync worker), then the UI restarts.

Nothing is ever copied from one store into the other.
"""
from __future__ import annotations

import logging
import os
import sqlite3
import threading
from typing import Any, Callable, Optional

from core.wholesale_mode import NotAllowed
from core.wholesale_schema import RETAIL, WHOLESALE

log = logging.getLogger(__name__)

PROGRESS_PATH = "/api/offline-first/activate/progress"
JOB_KIND = "wholesale_switch"

_switch_lock = threading.Lock()

Progress = Callable[[str], None]


class NoCompanion(NotAllowed):
    """The open store has no other store to switch to."""


class SwitchBusy(RuntimeError):
    """A copy is already running."""


def _say(progress: Optional[Progress], msg: str) -> None:
    if progress:
        try:
            progress(msg)
        except Exception:
            pass


def _app_version() -> str:
    try:
        from core.app_version import APP_VERSION

        return str(APP_VERSION)
    except Exception:
        return ""


# ── which store is which ─────────────────────────────────────────────────────

def _display_name(store_key: str) -> str:
    from core.store_manager import list_stores

    for s in list_stores():
        if s.get("store_key") == store_key:
            return str(s.get("display_name") or store_key)
    return store_key


def own_identity(store_key: str) -> dict | None:
    """This PC's store as the server knows it: {store_id, store_key, store_name, android_key}
    (the link back a newly added companion is given until its own answer arrives)."""
    from core import server_api as api
    from core.store_link import get_local_android_key

    session = {}
    try:
        session = api.load_session(store_key) or {}
    except Exception:
        pass
    android_key = str(get_local_android_key(store_key) or session.get("android_key") or "").strip()
    if not android_key:
        return None
    return {
        "store_id": str(session.get("store_id") or "").strip(),
        "store_key": str(session.get("store_key") or "").strip(),
        "store_name": str(session.get("store_name") or _display_name(store_key)).strip(),
        "android_key": android_key,
    }


def find_local_store(target: dict) -> str:
    """The local store key of that server store, or "" when it is not on this PC."""
    from core.server_live import _load_adoptions
    from core.store_link import get_local_android_key
    from core.store_manager import list_stores

    keys = [str(s.get("store_key") or "") for s in list_stores() if s.get("store_key")]
    want_id = str(target.get("store_id") or "").strip()
    if want_id:
        for key, entry in _load_adoptions().items():
            if key in keys and isinstance(entry, dict) and str(entry.get("store_id") or "").strip() == want_id:
                return key
    want_key = str(target.get("android_key") or "").strip()
    if want_key:
        for key in keys:
            if str(get_local_android_key(key) or "").strip() == want_key:
                return key
    return ""


def add_companion_store(target: dict) -> str:
    """Put the server store ``target`` on this PC as a new local store (not opened). Pairs
    FIRST: when the server refuses, nothing on this PC has changed. Returns its store key."""
    from core import server_api as api
    from core.server_api import _pc_device_id
    from core.server_live import _mark_joined_existing, _record_store_adoption
    from core.store_link import _save_local
    from core.store_manager import _find_store_by_display_name, create_store, display_name_key

    name = str(target.get("store_name") or "").strip()
    android_key = str(target.get("android_key") or "").strip()
    if not name or not android_key:
        raise NoCompanion("The server did not say which store to open.")
    if _find_store_by_display_name(name):
        raise RuntimeError(
            f'This PC already has a store called "{name}" that is not linked to it. '
            "Rename or remove that store in Settings > Stores first.")
    key = display_name_key(name)
    try:
        session = api.ensure_store_session(store_key=key, android_key=android_key, store_name=name,
                                           device_id=_pc_device_id(), force_pair=True)
    except Exception as exc:
        raise RuntimeError(f'Could not reach the server to open "{name}": {exc}') from exc
    if not (session or {}).get("token"):
        raise RuntimeError(f'The server did not accept this PC for "{name}".')
    entry = create_store(name, activate=False, allow_existing_remote=True)
    new_key = str(entry.get("store_key") or key)
    if new_key != key:
        try:
            api.save_session(new_key, session)
        except Exception:
            pass
    _save_local(android_key, new_key)
    _record_store_adoption(new_key, {"store_id": target.get("store_id") or "", "store_name": name})
    # Its books are the server's; nothing made on this PC before is ever merged into them.
    _mark_joined_existing(new_key)
    return new_key


def _link_key(store_key: str, target: dict) -> None:
    """A store found by its server id keeps the SC- key the server gives now."""
    from core.store_link import _save_local, get_local_android_key

    want = str(target.get("android_key") or "").strip()
    if want and str(get_local_android_key(store_key) or "").strip() != want:
        _save_local(want, store_key)


def _ensure_session(store_key: str, target: dict) -> None:
    from core import server_api as api
    from core.server_api import _pc_device_id
    from core.store_link import get_local_android_key

    api.ensure_store_session(
        store_key=store_key,
        android_key=str(get_local_android_key(store_key) or target.get("android_key") or ""),
        store_name=_display_name(store_key),
        device_id=_pc_device_id(),
    )


# ── offline-first: each store has its own copy ───────────────────────────────

def has_offline_first_copy(store_key: str) -> bool:
    """That store's file is an offline-first copy (registered with the server)."""
    from core.offline_first.runtime import registered
    from core.store_manager import get_store_db_path

    path = get_store_db_path(store_key)
    if not path or not os.path.isfile(path):
        return False
    try:
        conn = sqlite3.connect(f"file:{os.path.abspath(path)}?mode=ro", uri=True, timeout=10)
    except sqlite3.Error:
        return False
    try:
        return registered(conn)
    finally:
        conn.close()


def needs_copy(store_key: str) -> bool:
    from core.offline_first.runtime import is_active

    return bool(is_active()) and not has_offline_first_copy(store_key)


def copy_for_offline_first(store_key: str, progress: Optional[Progress] = None) -> dict:
    """The server's copy of THAT store into ITS file, in this thread, while the registry and
    the rest of the app stay on the open store."""
    from core.offline_first.runtime import activate_from_online
    from core.store_manager import _forget_store_scoped_caches, acting_for_store

    try:
        with acting_for_store(store_key):
            return activate_from_online(progress_cb=progress, app_version=_app_version())
    finally:
        # anything cached while working for the other store goes
        _forget_store_scoped_caches()


# ── the switch ───────────────────────────────────────────────────────────────

def _open(store_key: str, progress: Optional[Progress] = None) -> dict:
    """What Settings > Stores > Switch does: registry, caches, the store file / Online runtime
    and the sync worker. The UI restarts after it."""
    from core.store_manager import set_active_store

    if not set_active_store(store_key):
        raise RuntimeError(f'Store "{store_key}" is not on this PC.')
    _say(progress, f'Opening "{_display_name(store_key)}"…')
    try:
        from core.desktop_api import reopen_active_store

        reopen = reopen_active_store() or {}
    except Exception as exc:
        reopen = {"ok": False, "error": str(exc)}
    return reopen


def _prepare(mode: str) -> tuple[str, dict, str, str]:
    """(mode, target, local store key, open store key); adds the target to this PC if needed."""
    from core import wholesale_mode as wm
    from core.store_manager import get_active_store_key, is_satellite_device

    here = str(get_active_store_key() or "")
    info = wm.store_info(refresh=True)
    target = wm.companion(info)
    if not target:
        if mode == WHOLESALE:
            raise NoCompanion("This store has no wholesale store. Ask Satpuda to switch "
                              "wholesale on for it; the server then makes one.")
        raise NoCompanion("The server has not said which retail store this wholesale store "
                          "belongs to. Open it from Settings > Stores.")
    if is_satellite_device():
        raise RuntimeError("This PC is linked to one store only, so it cannot open "
                           f'"{target.get("store_name")}". Unlock store switching in Settings > Stores.')
    local = find_local_store(target)
    if local:
        if local == here:
            raise RuntimeError(f'"{target.get("store_name")}" is the store that is open now.')
        _link_key(local, target)
    else:
        local = add_companion_store(target)
    # Until its own answer arrives, the store just added knows what it is and where it
    # links back to (its own licence answer replaces this).
    try:
        from core import license_manager as lm

        back = own_identity(here)
        lm.note_store_wholesale_info(local, {
            "enabled": True,
            "store_kind": mode,
            "wholesale_store": back if mode == RETAIL else None,
            "retail_store": back if mode == WHOLESALE else None,
        })
    except Exception as exc:
        log.debug("wholesale switch: link back: %s", exc)
    return mode, target, local, here


def switch_to(mode: Any) -> dict[str, Any]:
    """POST /api/wholesale/mode {mode}. Answers {ok, reload, mode, store_name} -- with
    ``running`` and ``progress`` while the offline-first copy of the store runs in the
    background (then the UI waits for the job before it reloads)."""
    from core import wholesale_mode as wm

    m = str(mode or "").strip().lower()
    if m not in (RETAIL, WHOLESALE):
        raise ValueError("mode must be 'retail' or 'wholesale'")
    if _copy_running():
        raise SwitchBusy("A copy from the server is running. Wait for it to finish.")
    if not _switch_lock.acquire(blocking=False):
        raise SwitchBusy("The store is already being switched. Wait a moment.")
    try:
        if wm.current_mode(refresh=False) == m:
            return {"ok": True, "reload": False, "unchanged": True, "mode": m,
                    "store_name": wm._store_name()}
        _m, target, local, here = _prepare(m)
        name = _display_name(local)
        if needs_copy(local):
            from core.offline_first import api as of_api

            def work(progress: Progress) -> dict:
                _say(progress, f'Making this PC\'s copy of "{name}"…')
                copied = copy_for_offline_first(local, progress)
                from core.store_manager import get_active_store_key

                if str(get_active_store_key() or "") != here:
                    raise RuntimeError("The open store was changed while the copy ran. "
                                       f'Press the switch again to open "{name}".')
                reopen = _open(local, progress)
                _say(progress, f'"{name}" is open.')
                return {"ok": True, "store_key": local, "store_name": name, "mode": m,
                        "copied": copied, "reopen_ok": bool(reopen.get("ok", True))}

            if not of_api.start_job(JOB_KIND, work):
                raise SwitchBusy("A copy from the server is already running. Wait for it to finish.")
            return {"ok": True, "reload": True, "running": True, "progress": PROGRESS_PATH,
                    "mode": m, "store_name": name, "store_key": local}
        # Not offline-first, or the store already has its copy: open it now.
        try:
            _ensure_session(local, target)
        except Exception as exc:
            # A shop with no internet still opens a store that is already on this PC.
            log.info("wholesale switch: session for %s: %s", local, exc)
        reopen = _open(local)
        return {"ok": True, "reload": True, "mode": m, "store_name": name, "store_key": local,
                "previous_store_key": here, "reopen_ok": bool(reopen.get("ok", True))}
    finally:
        _switch_lock.release()


def _copy_running() -> bool:
    try:
        from core.offline_first import api as of_api

        with of_api._job_lock:
            return bool(of_api._job.get("running"))
    except Exception:
        return False
