"""Day-to-day Online sync against Satpuda Core Server (push / pull / bootstrap / poll).

Uses server_entity_sync payload builders and sync_down_doc apply helpers without
calling the cloud SDK.
"""
from __future__ import annotations

import logging
import os
import re
import threading
import time
from typing import Callable, Optional

log = logging.getLogger(__name__)

ProgressCb = Optional[Callable[[str], None]]
OnChangeCb = Optional[Callable[[str], None]]

# Near-instant cross-device refresh after the writer push-on-save.
_POLL_INTERVAL = 1.0
_poll_stop = threading.Event()
_poll_thread: Optional[threading.Thread] = None
_poll_lock = threading.Lock()
_on_change: OnChangeCb = None
_global_since: Optional[str] = None
_last_pull_changed_cols: set[str] = set()
# While True, ConflictResolver KEEP_LOCAL must NOT spawn one HTTP push per doc
# (that storm caused 429 / WinError 10054 during Pull from Server).
_suppress_entity_repush = False
_repush_lock = threading.Lock()
_repush_queue: set[tuple[str, str]] = set()
_repush_flush_scheduled = False

# Page size for collection pulls (Node hard-caps at 5000). Use full page so
# stores that share one bulk updated_at still finish in one or few requests.
_PULL_PAGE = 5000

# Sales/medicines first so Android bills show in Sales History within one poll tick.
_COLLECTIONS = (
    "sales",
    "medicines",
    "customers",
    "purchases",
    "suppliers",
    "doctors",
    "customer_payments",
    "supplier_payments",
    "sales_returns",
    "purchase_returns",
    "racks",
    "sections",
    "boxes",
)


def _progress(cb: ProgressCb, msg: str) -> None:
    if cb:
        try:
            cb(msg)
        except Exception:
            pass


def _slug_store_id(store_key: str) -> str:
    sid = (store_key or "Store_Default").lower()
    sid = re.sub(r"[^a-z0-9_]+", "_", sid)
    return sid[:60] or "store_default"


_link_error: dict[str, str] = {}


def last_link_error() -> str:
    """Why this PC could not link to its store on the server, or "".

    Modelled on core.online_catalog.last_error. Every caller of
    ensure_active_store_on_server that runs unattended swallows the exception --
    the poller, the launch path, the 401 retry -- so the sentence it raised with
    reached nobody. This is where it is kept until a screen asks.
    """
    return _link_error.get("last", "")


def _note_link_error(message: str) -> None:
    """Never raises: this runs on the raise path of the busiest call in here."""
    try:
        _link_error["last"] = str(message or "").strip()
    except Exception:
        pass


def _clear_link_error() -> None:
    try:
        _link_error.pop("last", None)
    except Exception:
        pass


def _mark_joined_existing(store_key: str) -> None:
    """Flag a store this PC JOINED rather than published.

    The local database still holds whatever this PC was billing before, and the
    server store holds another shop's ledger. Merging the first into the second
    is exactly the accident this whole area exists to prevent, so the fact has
    to survive on disk -- the dialog that offers the merge opens by itself on a
    later launch, with nobody around who remembers the join.
    """
    import json
    import os

    try:
        data = _load_adoptions()
        entry = data.get(str(store_key or ""))
        if not isinstance(entry, dict):
            return
        entry["joined_existing"] = True
        p = _adoption_path()
        os.makedirs(os.path.dirname(p), exist_ok=True)
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, sort_keys=True)
        os.replace(tmp, p)
    except Exception:
        pass


def active_store_was_joined() -> bool:
    """True when the active store was joined to an existing server store."""
    try:
        from core.store_manager import get_active_store_key

        entry = _load_adoptions().get(str(get_active_store_key() or ""))
        return bool(isinstance(entry, dict) and entry.get("joined_existing"))
    except Exception:
        return False


def forget_store_adoption(store_key: str = "") -> dict:
    """Erase this PC's record of which server store a local store belongs to.

    The way out. Without it a wrong join is permanent: the record decides which
    store the PC resolves to, and it decides which store a rebuilt registry
    activates, with no screen able to change either.
    """
    import json
    import os

    from core.store_manager import get_active_store_key

    key = str(store_key or get_active_store_key() or "").strip()
    if not key:
        raise ValueError("No active store.")
    data = _load_adoptions()
    if key not in data:
        return {"ok": True, "store_key": key, "message": "Nothing was recorded for this store."}
    data.pop(key, None)
    p = _adoption_path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)
    os.replace(tmp, p)
    _clear_link_error()
    return {
        "ok": True,
        "store_key": key,
        "message": (
            "This PC no longer records which server store this is. It will "
            "match by store key again on the next connection."
        ),
    }


def _remote_store_id(remote: dict) -> str:
    return str((remote or {}).get("store_id") or (remote or {}).get("id") or "").strip()


def _adoption_path() -> str:
    """Where we record which server store a local store was joined to."""
    import os

    from core.license_manager import _appdata_dir

    return os.path.join(_appdata_dir(), "store_adoptions.json")


def _load_adoptions() -> dict:
    import json
    import os

    try:
        p = _adoption_path()
        if not os.path.isfile(p):
            return {}
        with open(p, encoding="utf-8-sig") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _store_adoption_recorded(store_key: str, remote: dict) -> bool:
    """True when this local store was already deliberately joined to that store."""
    entry = _load_adoptions().get(str(store_key or ""))
    if not isinstance(entry, dict):
        return False
    rid = str(remote.get("store_id") or remote.get("id") or "")
    return bool(rid) and str(entry.get("store_id") or "") == rid


def _record_store_adoption(store_key: str, remote: dict) -> None:
    """Remember the join, so the shop is asked once and not on every launch."""
    import json
    import os

    try:
        data = _load_adoptions()
        key = str(store_key or "")
        previous = data.get(key) if isinstance(data.get(key), dict) else {}
        entry = {
            "store_id": str(remote.get("store_id") or remote.get("id") or ""),
            "store_name": str(remote.get("store_name") or ""),
        }
        # Carry the flags forward. This function runs on every successful pair,
        # so rebuilding the entry from scratch would quietly forget that this
        # PC had been JOINED to a store it did not create -- and that fact is
        # what stops its local records being merged into that shop's books.
        if previous.get("joined_existing") and entry["store_id"] == str(
            previous.get("store_id") or ""
        ):
            entry["joined_existing"] = True
        data[key] = entry
        p = _adoption_path()
        os.makedirs(os.path.dirname(p), exist_ok=True)
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, sort_keys=True)
        os.replace(tmp, p)
    except Exception:
        pass


class StoreNotLinkedOnServer(RuntimeError):
    """This PC's active store has no matching store on the server.

    Raised instead of silently creating one. The only paths allowed to create a
    store are the ones a person actually asked for -- activation, "connect this
    PC to a store", and the Settings sync actions -- which pass
    create_if_missing=True. Everything else (startup, the poller, the 401/403
    re-pair retry) now fails here rather than pairing the shop into a fresh
    empty store and overwriting its saved pairing key on the way.
    """

    def __init__(self, store_key: str, name: str):
        self.store_key = store_key
        self.name = name
        super().__init__(
            f'This PC is not linked to any store on the server '
            f'(local store "{name}", key {store_key}). '
            "Open Settings and connect it to the existing store."
        )


class StoreNameTakenOnServer(RuntimeError):
    """A DIFFERENT store on the server already answers to this name."""

    def __init__(self, name: str, remote: dict):
        self.name = name
        self.remote = remote or {}
        super().__init__(
            f'A store named "{name}" already exists on the server. '
            "Rename this store, or connect to the existing one deliberately."
        )


def _require_admin_token(admin_token: str = "") -> str:
    """An administrator token for this call, or a refusal. Never a compiled one.

    Takes the token the caller was handed, or the one from a sign-in a person
    made minutes ago; with neither it raises. There is no third source -- that
    third source used to be a password inside the build.
    """
    token = str(admin_token or "").strip()
    if token:
        return token
    from core import admin_session

    return admin_session.token()


def _was_paired_before(store_key: str) -> bool:
    """Has this PC ever been linked to this local store?

    The difference between the two things that both look like "no matching
    store on the server":

      * a store made here five minutes ago that has never been published --
        creating it is the whole point of pressing the button;
      * a store this PC has been billing into for a year whose link just
        stopped matching -- creating it manufactures an empty duplicate, moves
        the pairing key onto it, and the shop opens tomorrow with no data.

    Session file, adoption ledger, saved pairing key: any one of them means
    this store has been paired before, so the second case applies.
    """
    key = str(store_key or "").strip()
    if not key:
        return False
    try:
        from core import server_api as api

        if api.load_session(key):
            return True
    except Exception:
        pass
    try:
        if isinstance(_load_adoptions().get(key), dict):
            return True
    except Exception:
        pass
    try:
        import os

        from core.store_link import _local_path

        if os.path.isfile(_local_path(key)):
            return True
    except Exception:
        pass
    return False


def _link_by_pairing_key(*, store_key: str, name: str) -> dict:
    """This PC's store on the server, resolved with the store's OWN SC- key.

    THE PATH A SHOP PC TAKES WHEN NOBODY IS WATCHING, and the reason this
    function exists at all. The old code opened with ``api.admin_login()`` --
    the vendor ADMINISTRATOR, with a username and password compiled into the
    build -- and this function runs on every launch, from the poller, and again
    on every 401/403 re-pair. So every till in the fleet signed itself in as the
    administrator of every shop on the account, several times a day. The live
    access log shows it.

    The SC- pairing key does the same job and nothing more: it is already on
    this PC, ``/api/auth/pair`` answers with exactly one store -- the one that
    owns the key -- and it cannot read, create or edit anybody else's. Identity
    by key is also STRONGER than what the admin path did, which fell back to
    matching a display NAME and is the one accident that can put a PC inside a
    stranger's ledger.

    No key means this PC has not been connected to a shop yet. That is a
    sentence for a person, not something to fix by inventing a store.
    """
    from core import server_api as api
    from core.server_api import _pc_device_id
    from core.store_link import get_local_android_key

    key = (get_local_android_key(store_key) or "").strip()
    if not key:
        exc = StoreNotLinkedOnServer(store_key, name)
        _note_link_error(
            f'This computer is not connected to a shop on the Satpuda server '
            f'(local store "{name}"). Open Set Up and enter the shop\'s SC- key, '
            "or start a new shop. Publishing this store to the server instead "
            "needs the Satpuda administrator username and password."
        )
        raise exc

    session = api.ensure_store_session(
        store_key=store_key,
        android_key=key,
        store_name=name,
        device_id=_pc_device_id(),
    )
    sid = str(session.get("store_id") or "").strip()
    if sid:
        adopted = _load_adoptions().get(store_key)
        if isinstance(adopted, dict):
            pinned = str(adopted.get("store_id") or "").strip()
            if pinned and pinned != sid:
                # The key on this PC belongs to a different store than the one
                # a person recorded. Not refused -- the key is what the server
                # actually authenticated -- but said out loud, because the only
                # innocent way to get here is somebody pasting a key.
                _note_link_error(
                    f'This PC was connected to the store "'
                    f'{str(adopted.get("store_name") or pinned)}" (id {pinned}), '
                    f"but its SC- key belongs to "
                    f'"{session.get("store_name") or sid}" (id {sid}). '
                    "Open Settings and check which shop this PC belongs to."
                )
        _record_store_adoption(store_key, {
            "store_id": sid,
            "store_key": session.get("store_key") or store_key,
            "store_name": session.get("store_name") or name,
        })
        _clear_link_error()
    return session


def ensure_active_store_on_server(
    *,
    adopt_by_name: bool = False,
    create_if_missing: bool = False,
    create_if_new: bool = False,
    admin_token: str = "",
) -> dict:
    """Ensure active local store exists on server; return pair session.

    TWO WAYS IN, and the default one carries no credential.

    Without ``admin_token`` this resolves the store from its own SC- pairing key
    and never touches the administrator API -- see ``_link_by_pairing_key``.
    That is what every automatic caller gets: the launch-time link, the poller,
    the re-pair after a 401.

    With ``admin_token`` -- which only exists while a PERSON has typed the
    vendor's administrator username and password (``core/admin_session.py``) --
    the store list is searched and a store may be created or adopted. That is
    the operator's path, unchanged below.

    A store on the server is THIS store when its store_key or store_id matches.
    Matching on the display name as well used to be silent and automatic, so
    creating a local store that happened to share a name with another shop's
    store handed over that shop's entire ledger. Adopting by name is now
    something the caller has to ask for, which is what "connect this PC to an
    existing store" does.
    """
    from core import server_api as api
    from core.server_api import _pc_device_id
    from core.store_link import get_local_android_key, _save_local
    from core.store_manager import get_active_display_name, get_active_store_key

    store_key = get_active_store_key() or "Store_Default"
    name = (get_active_display_name() or store_key.replace("Store_", "").replace("_", " ")).strip()
    store_id = _slug_store_id(store_key)

    admin = str(admin_token or "").strip()
    if not admin:
        return _link_by_pairing_key(store_key=store_key, name=name)
    remote_list = api.list_remote_stores(admin)
    remote = None
    name_clash = None

    # An adoption is a person's statement of identity: "this PC belongs to THAT
    # store on the server." A local folder name is not. So when one is on
    # record, it decides -- and a local rename, or a registry rebuilt under a
    # different key, stops mattering.
    #
    # Fails open by construction. No adoption recorded (every install today,
    # every first activation, every fresh machine) leaves the loop below exactly
    # as it was.
    adopted = _load_adoptions().get(store_key)
    adopted_id = ""
    if isinstance(adopted, dict):
        adopted_id = str(adopted.get("store_id") or "").strip()
    pin_unresolved = False
    if adopted_id:
        for s in remote_list:
            if _remote_store_id(s) == adopted_id:
                remote = s
                break
        if remote is None:
            # Remember it, but FALL THROUGH. Refusing here would be a
            # fleet-wide outage one server-side id change away: a PC whose
            # store_key still matches perfectly well would stop billing because
            # a recorded id no longer resolves. What the record DOES buy is
            # below -- with a pin on file, this PC will not adopt a store just
            # because it answers to the same name.
            pin_unresolved = True
            _note_link_error(
                f'This PC was connected to the store "'
                f'{str(adopted.get("store_name") or adopted_id)}" '
                f"(id {adopted_id}) on the server, and that store is not in the "
                "list any more. Open Settings and check which store this PC "
                "belongs to."
            )

    if remote is None:
        for s in remote_list:
            if s.get("store_key") == store_key or s.get("store_id") == store_id:
                remote = s
                break
            if (s.get("store_name") or "").strip().lower() == name.lower():
                name_clash = s
    if remote is None and name_clash is not None:
        if pin_unresolved:
            # A shared NAME is not an identity, and this PC has already told us
            # which store it belongs to. Handing it a different shop that
            # happens to be called the same thing is the whole accident.
            # The sentence already noted above names the missing store, which is
            # more use to the shop than "not linked to any store" -- keep it.
            raise StoreNotLinkedOnServer(store_key, name)
        if not adopt_by_name and not _store_adoption_recorded(store_key, name_clash):
            exc = StoreNameTakenOnServer(name, name_clash)
            _note_link_error(str(exc))
            raise exc
        remote = name_clash
        _record_store_adoption(store_key, name_clash)
    if not remote:
        # Creating a store here used to be unconditional, and this function runs
        # unattended on every launch (sync_coordinator.ensure_online_store_link)
        # and again on every 401/403 push retry below. So anything that made the
        # local store_key stop matching -- a rename, a rebuilt registry, an
        # unreadable one -- silently produced a NEW empty store on the server,
        # repointed this PC at it, and overwrote the saved pairing key. In
        # Online mode the desktop keeps no local data, so the shop opened to a
        # clean, working, completely empty app while its real ledger sat on the
        # server. Only the paths a person asked for may create.
        # create_if_new is the setting the recovery buttons pass: publish a
        # store that has never been on the server, but refuse to "recover" a
        # store that HAS been paired before by inventing an empty replacement.
        # That was the same call, and pressing "Verify server sync" -- the
        # button an owner reaches for when the figures look wrong -- caused the
        # very blackout it was pressed to diagnose.
        may_create = create_if_missing or (
            create_if_new and not _was_paired_before(store_key)
        )
        if not may_create:
            exc = StoreNotLinkedOnServer(store_key, name)
            # A store that has NEVER been published is not a broken link, and
            # must not be answered with "pick your shop from this list" -- that
            # would make the recovery screen itself the way a brand-new shop
            # reaches somebody else's books.
            if _was_paired_before(store_key):
                _note_link_error(str(exc))
            else:
                _note_link_error(
                    f'The store "{name}" has not been published to the server '
                    "yet. Open Settings and use Push to Server, or connect this "
                    "PC to the store it belongs to."
                )
            raise exc
        remote = api.create_store(
            admin,
            store_name=name,
            store_id=store_id,
            store_key=store_key,
        )

    android_key = (remote.get("android_key") or "").strip()
    if not android_key:
        remote = api.regenerate_android_key(admin, remote.get("store_id") or remote.get("id"))
        android_key = (remote.get("android_key") or "").strip()
    if not android_key:
        raise RuntimeError("Server store has no pairing key")

    local_key = get_local_android_key()
    if local_key != android_key:
        _save_local(android_key)

    session = api.ensure_store_session(
        store_key=store_key,
        android_key=android_key,
        store_name=remote.get("store_name") or name,
        device_id=_pc_device_id(),
        force_pair=True,
    )
    # Remember which server store this PC is actually on. Identity used to be
    # derived entirely from a local FOLDER NAME, which a rename, a rebuilt
    # registry or an unreadable one can change; the server's store_id cannot.
    # Overwrite rather than write-once: this records what the server has just
    # confirmed, so a correction fixes the record instead of being blocked by
    # it. Nothing refuses on the strength of this alone -- see the fall-through
    # above.
    if _remote_store_id(remote):
        _record_store_adoption(store_key, remote)
    _clear_link_error()
    return session


def list_server_stores(*, admin_token: str = "") -> list[dict]:
    """The stores on the server, for an operator choosing which one is theirs.

    NEEDS THE VENDOR ADMINISTRATOR, typed at that moment. Reading the whole
    account's store list is an administrator's act, not a shop's, and the token
    used to come from a password compiled into the build -- so this screen was
    a list of every shop on the account available to anyone who opened the app.
    ``admin_token`` comes from ``core/admin_session.py``; without it this
    raises rather than signing itself in.

    Deliberately NOT the raw rows. api.list_remote_stores answers with the
    android_key on every store, and the desktop engine listens on 127.0.0.1
    with Access-Control-Allow-Origin: *  -- so any page open in a browser on
    the counter PC could have asked for this list and walked away with the
    pairing key of every shop on the account. Only what a person needs to
    recognise their own shop leaves this function.
    """
    from core import server_api as api
    from core.store_manager import get_active_store_key, list_stores

    active_key = str(get_active_store_key() or "")
    active_slug = _slug_store_id(active_key) if active_key else ""
    adoptions = _load_adoptions()
    active_pin = ""
    if isinstance(adoptions.get(active_key), dict):
        active_pin = str(adoptions[active_key].get("store_id") or "").strip()

    # Which server stores some OTHER local store on this PC already answers to.
    # Two local stores pointing at one server store means both push into it and
    # both render it: the same books under two names.
    taken: dict[str, str] = {}
    for key, entry in adoptions.items():
        if key == active_key or not isinstance(entry, dict):
            continue
        sid = str(entry.get("store_id") or "").strip()
        if sid:
            taken[sid] = key
    try:
        for row in list_stores() or []:
            key = str(row.get("store_key") or "")
            if not key or key == active_key:
                continue
            taken.setdefault(_slug_store_id(key), key)
    except Exception:
        pass

    admin = _require_admin_token(admin_token)
    out: list[dict] = []
    for s in api.list_remote_stores(admin):
        sid = _remote_store_id(s)
        skey = str(s.get("store_key") or "")
        out.append(
            {
                "store_id": sid,
                "store_key": skey,
                "store_name": str(s.get("store_name") or ""),
                "is_this_pc": bool(
                    (active_pin and sid == active_pin)
                    or (not active_pin and (skey == active_key or sid == active_slug))
                ),
                "used_by_local_store": taken.get(sid, ""),
            }
        )
    return out


def join_server_store(
    *, store_key: str, store_id: str, confirm_name: str, admin_token: str = ""
) -> dict:
    """Connect this PC to a store that already exists on the server.

    This is the screen StoreNotLinkedOnServer has always told people to open.
    It is also the single most dangerous action in the app -- getting it wrong
    hands one shop another shop's entire ledger -- so it refuses before it
    writes anything, and it writes in an order where a failure leaves the PC
    where it was rather than half-moved.

    It picks a store out of the whole account's list, so like ``list_server_stores``
    it needs the vendor administrator typed at that moment. A shop that simply
    knows its own SC- key does not come here at all: it pairs with the key (see
    ``desktop_license_service.pair_with_store_key``), which can only ever reach
    the one store that key belongs to.
    """
    from core import server_api as api
    from core.server_api import _pc_device_id
    from core.store_link import _save_local, get_local_android_key
    from core.store_manager import get_active_store_key

    key = str(store_key or "").strip()
    want_id = str(store_id or "").strip()
    typed = str(confirm_name or "").strip()
    if not key or not want_id:
        raise ValueError("Store to connect not given.")

    # The operator may have switched store in another tab since the list was
    # drawn. Filing the adoption under the wrong local key repoints the wrong
    # shop, so the caller has to say which store it meant.
    active = str(get_active_store_key() or "")
    if key != active:
        raise RuntimeError(
            f'The active store changed to "{active}" while this screen was open. '
            "Close it, reopen Stores and try again."
        )

    # Re-fetch. The list the operator was looking at is a snapshot; a store can
    # be created, renamed or deleted between the render and the click, and an
    # index-based choice would then land on a different shop.
    admin = _require_admin_token(admin_token)
    remote = None
    for s in api.list_remote_stores(admin):
        if _remote_store_id(s) == want_id:
            remote = s
            break
    if remote is None:
        raise RuntimeError(
            "That store is no longer on the server. Show the list again."
        )
    real_name = str(remote.get("store_name") or "").strip()
    if typed.lower() != real_name.lower():
        raise RuntimeError(
            f'To connect to "{real_name}" you have to type its name exactly.'
        )

    used_by = ""
    for other_key, entry in _load_adoptions().items():
        if other_key != key and isinstance(entry, dict):
            if str(entry.get("store_id") or "").strip() == want_id:
                used_by = other_key
                break
    if used_by:
        raise RuntimeError(
            f'The store "{real_name}" is already connected to "{used_by}" on this PC. '
            "One server store belongs to one store here."
        )

    android_key = str(remote.get("android_key") or "").strip()
    if not android_key:
        # Only when the store has no key at all. Rotating an existing one would
        # unpair every Android phone and second PC already on that store.
        remote = api.regenerate_android_key(admin, want_id)
        android_key = str(remote.get("android_key") or "").strip()
    if not android_key:
        raise RuntimeError("That store has no pairing key on the server.")

    # Pair FIRST. Everything below this line changes what this PC is; if the
    # server refuses, nothing local has moved.
    previous_key = get_local_android_key()
    session = api.ensure_store_session(
        store_key=key,
        android_key=android_key,
        store_name=real_name,
        device_id=_pc_device_id(),
        force_pair=True,
    )
    if not (session or {}).get("token"):
        raise RuntimeError("The server did not accept this PC for that store.")

    if previous_key != android_key:
        _save_local(android_key)
    _record_store_adoption(key, remote)
    _mark_joined_existing(key)
    _clear_link_error()

    # The cursors and the cached lists are filed under the LOCAL store key, and
    # they now describe a different shop. Left in place, the poller would ask
    # the new store for everything since the old store's high-water mark and
    # quietly skip the history before it, and the dropdowns would show the
    # previous shop's customers under the new shop's name.
    cleared = []
    try:
        from core import sync_watermarks

        sync_watermarks.clear_all()
        cleared.append("watermarks")
    except Exception:
        pass
    try:
        from core import sync_revision

        sync_revision.set_head_revision(0, store_key=key)
        cleared.append("revision")
    except Exception:
        pass
    try:
        import os

        from core import online_catalog

        online_catalog.invalidate()
        snap = online_catalog._snapshot_path()
        if os.path.isfile(snap):
            os.remove(snap)
        cleared.append("catalog")
    except Exception:
        pass

    return {
        "ok": True,
        "store_key": key,
        "store_id": want_id,
        "store_name": real_name,
        "cleared": cleared,
        "message": (
            f'This PC is now connected to "{real_name}" on the server. '
            "Restart the app to load that store. Anything this PC had stored "
            "locally has NOT been uploaded into it and is not shown in Online "
            "mode; it is still on the disk."
        ),
    }


def _token() -> str:
    from core import server_api as api

    try:
        return api.store_token_for_active()
    except Exception:
        session = ensure_active_store_on_server()
        return session["token"]


def _with_id(payload: Optional[dict], rid: int) -> Optional[dict]:
    if not payload:
        return None
    out = dict(payload)
    out["id"] = int(rid)
    return out


def _sanitize_dates(doc: dict) -> dict:
    from core.server_sync import _sanitize_dates_in_doc

    _sanitize_dates_in_doc(doc)
    return doc


def _log_push_response(kind: str, data: dict) -> None:
    """Log server push outcome; warn when all docs were skipped."""
    import logging

    log = logging.getLogger(__name__)
    if not isinstance(data, dict):
        return
    upserted = int(data.get("upserted") or 0)
    skipped = int(data.get("skipped") or 0)
    revisions = data.get("revisions") or []
    log.info(
        "[SYNC][PUSH] %s upserted=%s skipped=%s revisions=%s",
        kind,
        upserted,
        skipped,
        revisions,
    )
    if skipped > 0 and upserted == 0:
        log.warning(
            "[SYNC][PUSH] %s all docs skipped (stale version/timestamp?)",
            kind,
        )


def push_docs(collection: str, docs: list) -> bool:
    from core import server_api as api

    cleaned = []
    for d in docs:
        if not d:
            continue
        cleaned.append(_sanitize_dates(dict(d)))
    if not cleaned:
        return False
    token = _token()
    try:
        data = api.push_collection(token, collection, cleaned)
        _log_push_response(f"collection:{collection}", data)
        return True
    except api.ServerHttpError as exc:
        if exc.status in (401, 403):
            session = ensure_active_store_on_server()
            data = api.push_collection(session["token"], collection, cleaned)
            _log_push_response(f"collection:{collection}", data)
            return True
        raise


def push_bundle(bundle: dict) -> bool:
    from core import server_api as api

    out = {}
    for k, v in (bundle or {}).items():
        if isinstance(v, list):
            out[k] = [_sanitize_dates(dict(d)) for d in v if d]
        elif isinstance(v, dict):
            out[k] = _sanitize_dates(dict(v))
        else:
            out[k] = v
    if not out:
        return False
    token = _token()
    try:
        data = api.push_bundle(token, out)
        _log_push_response("bundle", data)
        return True
    except api.ServerHttpError as exc:
        if exc.status in (401, 403):
            session = ensure_active_store_on_server()
            data = api.push_bundle(session["token"], out)
            _log_push_response("bundle", data)
            return True
        raise


def delete_remote(collection: str, local_id: int) -> bool:
    from core import server_api as api

    token = _token()
    try:
        api.delete_doc(token, collection, int(local_id))
        return True
    except api.ServerHttpError as exc:
        if exc.status == 404:
            return True
        if exc.status in (401, 403):
            session = ensure_active_store_on_server()
            api.delete_doc(session["token"], collection, int(local_id))
            return True
        raise


def push_sale_bundle(conn, sale_id: int, medicine_ids: Optional[list] = None) -> bool:
    from core import server_entity_sync as fb

    sale = _with_id(fb.build_sale_payload(conn, int(sale_id)), int(sale_id))
    if not sale:
        return False
    bundle: dict = {"sales": [sale]}
    cid = sale.get("customer_id")
    if cid:
        cust = _with_id(fb.build_customer_payload(conn, int(cid)), int(cid))
        if cust:
            bundle["customers"] = [cust]
    ids = []
    seen = set()
    for mid in medicine_ids or []:
        try:
            i = int(mid)
        except (TypeError, ValueError):
            continue
        if i > 0 and i not in seen:
            seen.add(i)
            ids.append(i)
    if not ids:
        cur = conn.cursor()
        cur.execute("SELECT medicine_id FROM sales_items WHERE sale_id=?", (sale_id,))
        for (mid,) in cur.fetchall():
            i = int(mid)
            if i not in seen:
                seen.add(i)
                ids.append(i)
    meds = []
    for mid in ids:
        mp = _with_id(fb.build_medicine_payload(conn, mid), mid)
        if mp:
            meds.append(mp)
    # B4.2 — attach idempotent stock deltas for this sale (preferred over absolute LWW)
    if meds:
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT medicine_id, COALESCE(qty,0) FROM sales_items WHERE sale_id=?",
                (int(sale_id),),
            )
            qty_by_med: dict[int, int] = {}
            for mid, qty in cur.fetchall():
                try:
                    i = int(mid)
                    qty_by_med[i] = qty_by_med.get(i, 0) + int(qty or 0)
                except (TypeError, ValueError):
                    continue
            for mp in meds:
                try:
                    mid = int(mp.get("id") or 0)
                except (TypeError, ValueError):
                    continue
                q = qty_by_med.get(mid, 0)
                if q <= 0:
                    continue
                ver = int(mp.get("version") or 1)
                mp["stock_ops"] = [
                    {
                        "op_uuid": f"sale:{int(sale_id)}:med:{mid}:v{ver}",
                        "op": "sale",
                        "qty_delta": -q,
                        "medicine_id": mid,
                        "ref_collection": "sales",
                        "ref_id": int(sale_id),
                        "device_id": mp.get("device_id"),
                    }
                ]
        except Exception as exc:
            log.debug("sale stock_ops attach: %s", exc)
        bundle["medicines"] = meds
    return push_bundle(bundle)


def push_purchase_bundle(
    conn, purchase_id: int, *, extra_supplier_ids: Optional[list] = None,
) -> bool:
    from core import server_entity_sync as fb

    purchase = _with_id(fb.build_purchase_payload(conn, int(purchase_id)), int(purchase_id))
    if not purchase:
        return False
    bundle: dict = {"purchases": [purchase]}
    supplier_ids = []
    seen = set()
    sid = purchase.get("supplier_id")
    if sid:
        try:
            i = int(sid)
            if i > 0:
                supplier_ids.append(i)
                seen.add(i)
        except (TypeError, ValueError):
            pass
    for raw in extra_supplier_ids or []:
        try:
            i = int(raw)
        except (TypeError, ValueError):
            continue
        if i > 0 and i not in seen:
            seen.add(i)
            supplier_ids.append(i)
    suppliers = []
    for i in supplier_ids:
        sp = _with_id(fb.build_supplier_payload(conn, i), i)
        if sp:
            suppliers.append(sp)
    if suppliers:
        bundle["suppliers"] = suppliers
    cur = conn.cursor()
    cur.execute(
        "SELECT DISTINCT medicine_id FROM purchase_items WHERE purchase_id=?",
        (purchase_id,),
    )
    meds = []
    for (mid,) in cur.fetchall():
        mp = _with_id(fb.build_medicine_payload(conn, int(mid)), int(mid))
        if mp:
            meds.append(mp)
    if meds:
        try:
            cur.execute(
                "SELECT medicine_id, COALESCE(qty,0), COALESCE(free_qty,0) "
                "FROM purchase_items WHERE purchase_id=?",
                (int(purchase_id),),
            )
            qty_by_med: dict[int, int] = {}
            for mid, qty, free_qty in cur.fetchall():
                try:
                    i = int(mid)
                    qty_by_med[i] = qty_by_med.get(i, 0) + int(qty or 0) + int(free_qty or 0)
                except (TypeError, ValueError):
                    continue
            for mp in meds:
                try:
                    mid = int(mp.get("id") or 0)
                except (TypeError, ValueError):
                    continue
                q = qty_by_med.get(mid, 0)
                if q <= 0:
                    continue
                ver = int(mp.get("version") or 1)
                mp["stock_ops"] = [
                    {
                        "op_uuid": f"purchase:{int(purchase_id)}:med:{mid}:v{ver}",
                        "op": "purchase",
                        "qty_delta": q,
                        "medicine_id": mid,
                        "ref_collection": "purchases",
                        "ref_id": int(purchase_id),
                        "device_id": mp.get("device_id"),
                    }
                ]
        except Exception as exc:
            log.debug("purchase stock_ops attach: %s", exc)
        bundle["medicines"] = meds
    return push_bundle(bundle)


_ENTITY_BUILDERS = None


def _entity_builders():
    global _ENTITY_BUILDERS
    if _ENTITY_BUILDERS is None:
        from core import server_entity_sync as fb

        _ENTITY_BUILDERS = {
            "customers": fb.build_customer_payload,
            "suppliers": fb.build_supplier_payload,
            "medicines": fb.build_medicine_payload,
            "doctors": fb.build_doctor_payload,
            "sales": fb.build_sale_payload,
            "purchases": fb.build_purchase_payload,
            "customer_payments": fb.build_customer_payment_payload,
            "supplier_payments": fb.build_supplier_payment_payload,
            "sales_returns": fb.build_sales_return_payload,
            "purchase_returns": fb.build_purchase_return_payload,
            "racks": fb.build_rack_payload,
            "sections": fb.build_section_payload,
            "boxes": fb.build_box_payload,
        }
    return _ENTITY_BUILDERS


def _build_entity_doc(conn, collection: str, local_id: int) -> Optional[dict]:
    build = _entity_builders().get(collection)
    if not build:
        return None
    payload = _with_id(build(conn, int(local_id)), int(local_id))
    if not payload:
        return None
    if collection in ("sales", "purchases"):
        try:
            cur = conn.cursor()
            cur.execute(
                f"SELECT is_autosave FROM {collection} WHERE id=?",
                (int(local_id),),
            )
            row = cur.fetchone()
            if row is not None:
                payload["is_autosave"] = bool(int(row[0] or 0))
        except Exception:
            pass
    return payload


def push_entity(conn, collection: str, local_id: int) -> bool:
    """Push one row via Node collection API (body is still a docs array)."""
    payload = _build_entity_doc(conn, collection, int(local_id))
    if not payload:
        return False
    return push_docs(collection, [payload])


def push_entities(conn, collection: str, local_ids: list) -> bool:
    """One Node POST /api/sync/:collection with many docs — never one HTTP per row."""
    ids: list[int] = []
    seen: set[int] = set()
    for raw in local_ids or []:
        try:
            i = int(raw)
        except (TypeError, ValueError):
            continue
        if i > 0 and i not in seen:
            seen.add(i)
            ids.append(i)
    if not ids:
        return True
    docs = []
    for i in ids:
        doc = _build_entity_doc(conn, collection, i)
        if doc:
            docs.append(doc)
    if not docs:
        return False
    # Chunk large lists but still far fewer requests than 1-per-row.
    ok = True
    chunk = 300
    for start in range(0, len(docs), chunk):
        if not push_docs(collection, docs[start : start + chunk]):
            ok = False
    return ok


def push_related_bundle(conn, parts: dict) -> bool:
    """One Node POST /api/sync/bundle for related collections.

    parts: {collection: [id, id, ...], ...}
    """
    bundle: dict = {}
    for collection, raw_ids in (parts or {}).items():
        if not raw_ids:
            continue
        docs = []
        seen: set[int] = set()
        for raw in raw_ids:
            try:
                i = int(raw)
            except (TypeError, ValueError):
                continue
            if i <= 0 or i in seen:
                continue
            seen.add(i)
            doc = _build_entity_doc(conn, collection, i)
            if doc:
                docs.append(doc)
        if docs:
            bundle[collection] = docs
    if not bundle:
        return False
    return push_bundle(bundle)


def push_sales_return_bundle(
    conn, return_id: int, customer_id: Optional[int] = None, medicine_ids: Optional[list] = None,
) -> bool:
    parts: dict = {"sales_returns": [int(return_id)]}
    if customer_id:
        parts["customers"] = [int(customer_id)]
    if medicine_ids:
        parts["medicines"] = list(medicine_ids)
    return push_related_bundle(conn, parts)


def push_purchase_return_bundle(
    conn, return_id: int, supplier_id: Optional[int] = None, medicine_ids: Optional[list] = None,
) -> bool:
    parts: dict = {"purchase_returns": [int(return_id)]}
    if supplier_id:
        parts["suppliers"] = [int(supplier_id)]
    if medicine_ids:
        parts["medicines"] = list(medicine_ids)
    return push_related_bundle(conn, parts)


def push_customer_payment_bundle(
    conn, payment_id: int, customer_id: Optional[int] = None,
) -> bool:
    parts: dict = {"customer_payments": [int(payment_id)]}
    if customer_id:
        parts["customers"] = [int(customer_id)]
    return push_related_bundle(conn, parts)


def push_supplier_payment_bundle(
    conn, payment_id: int, supplier_id: Optional[int] = None,
) -> bool:
    parts: dict = {"supplier_payments": [int(payment_id)]}
    if supplier_id:
        parts["suppliers"] = [int(supplier_id)]
    return push_related_bundle(conn, parts)


def push_pharmacy_profile(conn) -> bool:
    from core import server_api as api

    cur = conn.cursor()
    try:
        cur.execute("SELECT * FROM pharmacy_profile LIMIT 1")
    except Exception:
        return True
    row = cur.fetchone()
    if not row:
        return True
    cols = [d[0] for d in cur.description]
    p = dict(zip(cols, row))
    profile = {
        "name": p.get("name"),
        "address": p.get("address"),
        "phone": p.get("phone"),
        "email": p.get("email"),
        "gstin": p.get("gstin"),
        "dl_number": p.get("dl_number"),
        "gst_enabled": bool(int(p.get("gst_enabled") or 1)),
        "fssai_number": p.get("fssai_number"),
        "show_fssai_on_bill": bool(int(p.get("show_fssai_on_bill") or 0)),
    }
    api.push_settings_profile(_token(), profile)
    return True


def push_dropdowns(conn) -> bool:
    from core import server_api as api

    villages = []
    default_village = None
    try:
        from core.village_service import get_default_village, load_villages

        villages = load_villages(conn) or []
        default_village = get_default_village(conn)
    except Exception:
        pass
    try:
        from core.layout_config import (
            _DEFAULT_MED_TYPES,
            _DEFAULT_SCHEDULES,
            get_configured_schedules,
            get_med_types,
        )

        med_types = list(get_med_types() or []) or list(_DEFAULT_MED_TYPES)
        schedules = list(get_configured_schedules() or []) or [s for s in _DEFAULT_SCHEDULES if s]
    except Exception:
        med_types = ["TAB", "SYRUP", "INJ", "CAP", "OINT"]
        schedules = ["H", "H1", "X"]
    api.push_settings_dropdowns(
        _token(),
        {
            "villages": villages,
            "default_village": default_village,
            "med_types": med_types,
            "schedules": schedules,
        },
    )
    return True


def push_settings_kv(name: str, value) -> bool:
    import json

    from core import server_api as api

    if isinstance(value, (dict, list)):
        raw = json.dumps(value)
    else:
        raw = value
    api.push_settings_kv(_token(), {"name": name, "value": raw})
    return True


def push_shelf_settings(conn) -> bool:
    from core import server_api as api

    show_location = False
    try:
        cur = conn.cursor()
        cur.execute("SELECT show_location FROM shelf_settings LIMIT 1")
        row = cur.fetchone()
        if row:
            show_location = bool(int(row[0] or 0))
    except Exception:
        pass
    api.push_settings_shelf(_token(), {"show_location": show_location})
    return True


def push_stock_disposal(conn, disposal_id: int) -> bool:
    cur = conn.cursor()
    cur.execute("SELECT * FROM stock_disposals WHERE id=?", (int(disposal_id),))
    row = cur.fetchone()
    if not row:
        return False
    cols = [d[0] for d in cur.description]
    d = dict(zip(cols, row))
    qty = d.get("qty")
    if qty is None:
        qty = d.get("quantity", 0)
    payload = {
        "id": int(disposal_id),
        "disposal_no": d.get("disposal_no"),
        "medicine_id": d.get("medicine_id"),
        "batch_no": d.get("batch_no"),
        "supplier_id": d.get("supplier_id"),
        "purchase_id": d.get("purchase_id"),
        "bill_number": d.get("bill_number"),
        "qty": qty,
        "quantity": qty,
        "original_purchase_qty": d.get("original_purchase_qty"),
        "disposal_type": d.get("disposal_type"),
        "reason": d.get("reason"),
        "expected_credit_note": bool(int(d.get("expected_credit_note") or 0)),
        "notes": d.get("notes"),
        "disposal_date": d.get("disposal_date"),
        "created_at": d.get("created_at"),
    }
    bundle: dict = {"stock_disposals": [payload]}
    mid = d.get("medicine_id")
    if mid:
        try:
            med = _build_entity_doc(conn, "medicines", int(mid))
            if med:
                bundle["medicines"] = [med]
        except Exception:
            pass
    return push_bundle(bundle)


def push_general_product(conn, product_id: int) -> bool:
    cur = conn.cursor()
    cur.execute("SELECT * FROM general_products WHERE id=?", (int(product_id),))
    row = cur.fetchone()
    if not row:
        return False
    cols = [d[0] for d in cur.description]
    p = dict(zip(cols, row))
    payload = {
        "id": int(product_id),
        "name": p.get("name"),
        "rate": p.get("rate", 0),
        "mrp": p.get("mrp", 0),
        "created_at": p.get("created_at"),
    }
    return push_docs("general_products", [payload])


def _doc_id(doc: dict, fallback_key: str = "id") -> Optional[str]:
    for key in (fallback_key, "id", "local_id"):
        if doc.get(key) is not None and doc.get(key) != "":
            return str(doc[key])
    return None


def apply_server_doc(conn, collection: str, doc: dict) -> str:
    """Apply one server document into local SQLite via server_entity_sync helpers."""
    from core import server_entity_sync as fb

    if collection in ("pharmacy_profile", "dropdowns", "shelf_settings", "settings"):
        # Settings specials — best-effort local apply for profile/dropdowns
        if collection == "pharmacy_profile" and isinstance(doc, dict):
            try:
                cur = conn.cursor()
                exists = cur.execute("SELECT id FROM pharmacy_profile LIMIT 1").fetchone()
                incoming_blank = not str(doc.get("name") or "").strip()
                if exists and incoming_blank:
                    local = cur.execute(
                        "SELECT name FROM pharmacy_profile WHERE id=?",
                        (exists[0],),
                    ).fetchone()
                    if local and str(local[0] or "").strip():
                        return "skipped"
                dl = doc.get("dl_number") or doc.get("dl_numbers") or ""
                gst_enabled = doc.get("gst_enabled")
                if isinstance(gst_enabled, bool):
                    gst_enabled = 1 if gst_enabled else 0
                elif gst_enabled is None:
                    gst_enabled = 1
                else:
                    gst_enabled = 1 if int(gst_enabled or 0) else 0
                vals = (
                    doc.get("name") or "",
                    doc.get("address") or "",
                    doc.get("phone") or "",
                    doc.get("email") or "",
                    doc.get("gstin") or "",
                    str(dl),
                    int(gst_enabled),
                    doc.get("fssai_number") or "",
                    1 if doc.get("show_fssai_on_bill") in (True, 1, "1", "true") else 0,
                )
                if exists:
                    cur.execute(
                        "UPDATE pharmacy_profile SET name=?, address=?, phone=?, "
                        "email=?, gstin=?, dl_number=?, gst_enabled=?, "
                        "fssai_number=?, show_fssai_on_bill=? WHERE id=?",
                        (*vals, exists[0]),
                    )
                else:
                    cur.execute(
                        "INSERT INTO pharmacy_profile "
                        "(name, address, phone, email, gstin, dl_number, "
                        "gst_enabled, fssai_number, show_fssai_on_bill) "
                        "VALUES (?,?,?,?,?,?,?,?,?)",
                        vals,
                    )
                conn.commit()
                try:
                    from core.pharmacy_profile_io import remember_local_profile

                    if str(doc.get("name") or "").strip():
                        remember_local_profile(doc)
                except Exception:
                    pass
                return "applied"
            except Exception as exc:
                log.warning("apply pharmacy_profile failed: %s", exc)
                return "skipped"
        if collection == "dropdowns" and isinstance(doc, dict):
            try:
                from core.village_service import save_villages

                villages = doc.get("villages") or []
                if isinstance(villages, list):
                    save_villages(
                        conn,
                        [str(v).strip() for v in villages if str(v).strip()],
                        default_village=doc.get("default_village") or "",
                        sync=False,
                    )
            except Exception as exc:
                log.warning("apply dropdowns villages failed: %s", exc)
            try:
                from core.layout_config import load_layout, save_layout

                layout = load_layout()
                med_types = doc.get("med_types")
                schedules = doc.get("schedules")
                changed = False
                if isinstance(med_types, list) and med_types:
                    layout["med_types"] = [str(t).strip() for t in med_types if str(t).strip()]
                    changed = True
                if isinstance(schedules, list):
                    layout["schedules"] = [str(s) for s in schedules]
                    changed = True
                if changed:
                    save_layout(layout)
            except Exception as exc:
                log.warning("apply dropdowns layout failed: %s", exc)
            return "applied"
        if collection == "shelf_settings" and isinstance(doc, dict):
            try:
                cur = conn.cursor()
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS shelf_settings (
                        id INTEGER PRIMARY KEY CHECK (id = 1),
                        show_location INTEGER NOT NULL DEFAULT 0
                    )
                    """
                )
                show = doc.get("show_location")
                show_i = 1 if show in (True, 1, "1", "true") else 0
                cur.execute(
                    "INSERT OR REPLACE INTO shelf_settings (id, show_location) VALUES (1, ?)",
                    (show_i,),
                )
                conn.commit()
                return "applied"
            except Exception as exc:
                log.warning("apply shelf_settings failed: %s", exc)
                return "skipped"
        if collection == "settings" and isinstance(doc, dict):
            name = str(doc.get("name") or doc.get("key") or doc.get("id") or "")
            if name == "billing_layout_prefs" or doc.get("item_discount_mode") is not None:
                try:
                    from core.billing_layout_prefs import apply_server_billing_layout_prefs

                    return apply_server_billing_layout_prefs(doc, conn)
                except Exception as exc:
                    log.debug("apply billing_layout_prefs: %s", exc)
                    return "skipped"
            # Generic KV → local settings table (keeps admin/device prefs in SQLite)
            if name:
                try:
                    cur = conn.cursor()
                    cur.execute(
                        """
                        CREATE TABLE IF NOT EXISTS settings (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            name TEXT UNIQUE,
                            value TEXT
                        )
                        """
                    )
                    raw = doc.get("value")
                    if isinstance(raw, (dict, list)):
                        import json as _json

                        raw = _json.dumps(raw)
                    elif raw is None:
                        raw = ""
                    else:
                        raw = str(raw)
                    cur.execute(
                        "INSERT INTO settings (name, value) VALUES (?, ?) "
                        "ON CONFLICT(name) DO UPDATE SET value=excluded.value",
                        (name, raw),
                    )
                    conn.commit()
                    return "applied"
                except Exception as exc:
                    log.debug("apply settings kv %s: %s", name, exc)
                    return "skipped"
        return "ignored"

    # B4.2 — apply stock delta once (idempotent via sync_inbox revision)
    if collection == "stock_operations":
        return _apply_stock_operation_doc(conn, doc)

    doc_id = _doc_id(doc)
    if not doc_id:
        return "ignored"
    data = dict(doc)
    # Explicit deleted=true must go through sync_down_doc → APPLY_SOFT_DELETE.
    # delete_down_doc is for cloud *absence* only and never_auto_delete skips
    # sales/purchases (then wrongly re-pushes the local live bill).
    try:
        status = fb.sync_down_doc(conn, collection, doc_id, data)
        # B4.1 — persist inbound client_uuid when present
        if collection in ("sales", "purchases", "medicines") and data.get("client_uuid"):
            try:
                from core.client_uuid import ensure_client_uuid_schema

                ensure_client_uuid_schema(conn)
                cur = conn.cursor()
                cur.execute(
                    f"UPDATE {collection} SET client_uuid=COALESCE(NULLIF(client_uuid,''), ?) "
                    f"WHERE id=?",
                    (str(data["client_uuid"]).strip(), int(doc_id)),
                )
            except Exception:
                pass
        return status
    except Exception as exc:
        log.warning("server apply %s/%s: %s", collection, doc_id, exc)
        return "error"


def _apply_stock_operation_doc(conn, doc: dict) -> str:
    """Apply server stock_operations delta to local medicines.stock_qty."""
    if not isinstance(doc, dict):
        return "ignored"
    # Audit-only absolute patches must not re-apply (medicine doc already has stock).
    op = str(doc.get("op") or "").strip().lower()
    if op == "set":
        return "skipped"
    try:
        medicine_id = int(doc.get("medicine_id") or 0)
        qty_delta = int(doc.get("qty_delta") or 0)
    except (TypeError, ValueError):
        return "ignored"
    if medicine_id <= 0 or qty_delta == 0:
        return "ignored"
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE medicines SET stock_qty=MAX(0, COALESCE(stock_qty,0)+?) WHERE id=?",
            (qty_delta, medicine_id),
        )
        conn.commit()
        return "applied" if cur.rowcount else "skipped"
    except Exception as exc:
        log.warning("apply stock_operations: %s", exc)
        return "error"


def set_suppress_entity_repush(enabled: bool) -> None:
    global _suppress_entity_repush
    _suppress_entity_repush = bool(enabled)


def is_entity_repush_suppressed() -> bool:
    return bool(_suppress_entity_repush)


def queue_entity_repush(collection: str, doc_id: str) -> None:
    """No-op: Online must not upload local-newer rows from poller/pull."""
    log.debug("skip queued entity re-push %s/%s", collection, doc_id)


def schedule_entity_repush_flush(conn) -> None:
    """Debounce: at most one background flush shortly after KEEP_LOCAL hits."""
    global _repush_flush_scheduled
    with _repush_lock:
        if _repush_flush_scheduled:
            return
        _repush_flush_scheduled = True

    def _run() -> None:
        global _repush_flush_scheduled
        try:
            time.sleep(0.9)
            flush_entity_repush_queue(conn)
        except Exception as exc:
            log.debug("scheduled entity repush flush: %s", exc)
        finally:
            with _repush_lock:
                _repush_flush_scheduled = False
                still = bool(_repush_queue)
            if still:
                schedule_entity_repush_flush(conn)

    threading.Thread(target=_run, daemon=True, name="EntityRepushBatch").start()


def flush_entity_repush_queue(conn, progress_cb: ProgressCb = None) -> int:
    """Drop any leftover KEEP_LOCAL queue — never upload from poller/pull."""
    with _repush_lock:
        skipped = len(_repush_queue)
        _repush_queue.clear()
    if skipped:
        log.info(
            "Dropped %s queued local-newer row(s) without uploading "
            "(server is source of truth; save/edit/delete still upload)",
            skipped,
        )
    return 0


def _pull_collection_pages(
    token: str,
    collection: str,
    *,
    since: Optional[str],
    progress_cb: ProgressCb,
    page_size: int = _PULL_PAGE,
) -> tuple[list, dict]:
    """Paginate Node GET /api/sync/:collection via (since, after_id) keyset."""
    from core import server_api as api

    all_docs: list = []
    meta: dict = {}
    cursor_since = since
    after_id: Optional[int] = None
    page = 0
    seen_ids: set[str] = set()
    while True:
        page += 1
        _progress(
            progress_cb,
            f"Downloading {collection}… page {page}"
            + (f" ({len(all_docs):,} so far)" if all_docs else ""),
        )
        try:
            docs, m = api.pull_collection(
                token,
                collection,
                since=cursor_since,
                after_id=after_id,
                include_deleted=True,
                limit=page_size,
                timeout=120.0,
            )
        except Exception as exc:
            log.warning("pull %s page %s failed: %s — retrying", collection, page, exc)
            time.sleep(1.2)
            docs, m = api.pull_collection(
                token,
                collection,
                since=cursor_since,
                after_id=after_id,
                include_deleted=True,
                limit=page_size,
                timeout=180.0,
            )
        if m:
            meta.update(m)
        if not docs:
            break
        new_in_page = 0
        last_ts = cursor_since
        last_id = after_id
        for doc in docs:
            if not isinstance(doc, dict):
                continue
            raw_id = doc.get("id") if doc.get("id") is not None else doc.get("local_id")
            did = str(raw_id) if raw_id is not None else ""
            if did and did in seen_ids:
                continue
            if did:
                seen_ids.add(did)
            all_docs.append(doc)
            new_in_page += 1
            ts = doc.get("updated_at") or doc.get("synced_at")
            if isinstance(ts, str) and ts:
                last_ts = ts
            try:
                last_id = int(raw_id)
            except (TypeError, ValueError):
                pass
        if len(docs) < page_size or new_in_page == 0:
            break
        # Advance keyset cursor. Prefer after_id so identical updated_at still pages.
        if last_id is None or last_id == after_id:
            if last_ts is None or last_ts == cursor_since:
                break
            cursor_since = last_ts
            after_id = None
        else:
            cursor_since = last_ts or cursor_since
            after_id = last_id
        # No artificial page delay — keep-alive + rate limit handle pacing.
    return all_docs, meta


_REPLACE_PULL_TABLES = (
    "sales_items",
    "sales",
    "purchase_items",
    "purchases",
    "sales_return_items",
    "sales_returns",
    "purchase_return_items",
    "purchase_returns",
    "customer_payments",
    "supplier_payments",
    "medicine_shelf",
    "medicines",
    "customers",
    "suppliers",
    "doctors",
    # Keep pharmacy_profile / shelf layout / racks — Pull replaces business DB only.
    "medicine_suppliers",
    "pending_orders",
    "stock_disposals",
    "general_products",
)


def clear_business_data_for_replace_pull(conn) -> None:
    """Wipe local business rows before a Replace Pull.

    Keeps pharmacy_profile, dropdowns, shelf layout, and app settings/sync_mode.
    """
    from core import sync_watermarks as wm
    from core import sync_bootstrap as boot

    cur = conn.cursor()
    for table in _REPLACE_PULL_TABLES:
        try:
            cur.execute(f"DELETE FROM {table}")
        except Exception as exc:
            log.debug("replace-pull clear %s: %s", table, exc)
    try:
        conn.commit()
    except Exception:
        pass
    try:
        wm.clear_all()
    except Exception:
        pass
    try:
        # Force next full download path; mark done again after pull succeeds.
        done = boot._done_path()
        pending = boot._pending_path()
        for path in (done, pending):
            try:
                os.remove(path)
            except OSError:
                pass
    except Exception:
        pass


def sync_down_all(
    conn,
    *,
    since: Optional[str] = None,
    progress_cb: ProgressCb = None,
    incremental: bool = False,
    replace_local: bool = False,
) -> int:
    """Download from Satpuda Core Server (Node). Download-only — no bulk upload.

    Full pulls use per-collection pagination (never the giant GET /api/sync).
    Incremental poller uses a lighter per-collection pull with watermarks.
    When replace_local=True, clears local business tables first then pulls cleanly.
    """
    from core import server_api as api
    from core import sync_watermarks as wm
    from core import sync_bootstrap as boot

    token = _token()
    count = 0
    global _global_since
    paused_poller = False

    # Pause background poller during a full download so it cannot compete for
    # rate-limit budget / the same SQLite connection.
    if not incremental:
        try:
            stop_poller()
            paused_poller = True
        except Exception:
            pass

    set_suppress_entity_repush(True)
    meta: dict = {}
    try:
        if replace_local and not incremental:
            _progress(progress_cb, "Clearing local store before server pull…")
            clear_business_data_for_replace_pull(conn)

        total_cols = len(_COLLECTIONS)
        changed_cols: set[str] = set()
        for idx, col in enumerate(_COLLECTIONS, 1):
            col_since = None
            if incremental:
                col_since = since or wm.query_since(col)
            _progress(
                progress_cb,
                f"Downloading {col} ({idx}/{total_cols})…",
            )
            docs, m = _pull_collection_pages(
                token,
                col,
                since=col_since,
                progress_cb=progress_cb,
                page_size=_PULL_PAGE if not incremental else 1000,
            )
            if m.get("server_time"):
                meta["server_time"] = m["server_time"]
            if not docs:
                if not incremental and col in wm.WATERMARK_COLLECTIONS:
                    wm.seed_from_local(conn, col)
                continue
            _progress(progress_cb, f"Merging {col}… ({len(docs):,} records)")
            max_ts = None
            for i, doc in enumerate(docs, 1):
                if not isinstance(doc, dict):
                    continue
                status = apply_server_doc(conn, col, doc)
                # Advance watermark only when the remote doc was handled (applied,
                # already-equal skip, or keep-local). Never advance on apply errors
                # or offline skips — otherwise Android bills can be missed forever.
                if status in ("applied", "soft_deleted"):
                    count += 1
                    changed_cols.add(col)
                if status in ("applied", "soft_deleted", "kept_local", "skipped"):
                    ts = doc.get("updated_at") or doc.get("synced_at")
                    if isinstance(ts, str) and ts and (max_ts is None or ts > max_ts):
                        max_ts = ts
                if i % 250 == 0:
                    _progress(
                        progress_cb,
                        f"Merging {col}… {i:,}/{len(docs):,}",
                    )
            if col in wm.WATERMARK_COLLECTIONS:
                if max_ts:
                    wm.bump_watermark(col, max_ts)
                elif not incremental:
                    wm.seed_from_local(conn, col)
            try:
                conn.commit()
            except Exception:
                pass

        # Settings stay local on Replace Pull (pharmacy header / shelves / KV prefs).
        # Incremental poller may still merge settings when replace_local is False.
        if not replace_local:
            for special in ("pharmacy_profile", "dropdowns", "shelf_settings"):
                try:
                    _progress(progress_cb, f"Downloading {special}…")
                    docs, m = api.pull_collection(
                        token, special, since=None, include_deleted=True, limit=50, timeout=60.0
                    )
                    if m.get("server_time"):
                        meta["server_time"] = m["server_time"]
                    for doc in docs or []:
                        if isinstance(doc, dict) and doc:
                            apply_server_doc(conn, special, doc)
                except Exception as exc:
                    log.warning("pull %s failed: %s", special, exc)

            try:
                _progress(progress_cb, "Downloading settings…")
                settings_docs, m = api.pull_collection(
                    token,
                    "settings",
                    since=(since if incremental else None),
                    include_deleted=True,
                    limit=5000,
                    timeout=60.0,
                )
                if m.get("server_time"):
                    meta["server_time"] = m["server_time"]
                for doc in settings_docs or []:
                    if isinstance(doc, dict) and doc:
                        status = apply_server_doc(conn, "settings", doc)
                        if status in ("applied", "soft_deleted"):
                            count += 1
            except Exception as exc:
                log.warning("pull settings failed: %s", exc)

        server_time = meta.get("server_time")
        # Only stamp the global cursor after a full pull. Incremental polls must
        # keep using per-collection watermarks — a wall-clock server_time here
        # previously skipped Android bills that landed between ticks.
        if not incremental and isinstance(server_time, str) and server_time:
            _global_since = server_time

        # Pull is download-only: drop any KEEP_LOCAL winners queued during merge
        # (do not flood the server with per-sale uploads — use Push to Server).
        if not incremental:
            with _repush_lock:
                skipped = len(_repush_queue)
                _repush_queue.clear()
            if skipped:
                log.info(
                    "Pull kept %s local-newer row(s) without uploading "
                    "(use Push to Server if you want them on the server)",
                    skipped,
                )

        if replace_local and not incremental:
            try:
                boot.mark_bootstrap_done()
            except Exception:
                pass
            _progress(
                progress_cb,
                f"Download complete — replaced local store with {count:,} record(s).",
            )
        else:
            _progress(
                progress_cb,
                f"Download complete — merged {count:,} record(s).",
            )
        global _last_pull_changed_cols
        _last_pull_changed_cols = changed_cols
        return count
    finally:
        set_suppress_entity_repush(False)
        if paused_poller:
            try:
                start_poller(conn, on_change=_on_change)
            except Exception:
                pass


def run_bootstrap(conn, progress_cb: ProgressCb = None) -> tuple[bool, str, int]:
    """First Online connect: download/merge from server only (no bulk upload).

    Bulk uploads are intentional only via Settings → Sync to Server, so offline
    work or stale local stock cannot silently overwrite the server.
    Day-to-day Online CRUD still pushes each save immediately.
    """
    try:
        ensure_active_store_on_server()
    except Exception as exc:
        return False, f"Server pairing failed: {exc}", 0

    _progress(progress_cb, "Downloading server data…")
    try:
        pulled = sync_down_all(conn, progress_cb=progress_cb, incremental=False)
    except Exception as exc:
        return False, f"Bootstrap download failed: {exc}", 0

    _progress(progress_cb, "First sync complete (download only).")
    return (
        True,
        (
            f"Merged {pulled:,} record(s) from server (no bulk upload). "
            "Use Settings → Sync to Server only when you intentionally want "
            "to push all local data."
        ),
        0,
    )


def _notify(collection: str) -> None:
    """Tell desktop UI (Tauri + Tk) that server data changed."""
    col = str(collection or "").strip()
    if col:
        try:
            from core.sync_status import note_collection_change, note_last_sync

            note_collection_change(col)
            note_last_sync("pull")
        except Exception:
            pass
    cb = _on_change
    if not cb:
        return
    try:
        cb(collection)
    except Exception:
        pass


def _poll_once(conn) -> int:
    """DEPRECATED (B3): watermark incremental poll.

    Kept only for USE_REVISION_SYNC=false rollback. Live sync uses SyncEngine.
    Manual Pull from Server still uses sync_down_all(replace_local=True).
    """
    # Always watermark-based (since=None). Do not pass wall-clock _global_since.
    changed = sync_down_all(conn, since=None, incremental=True)
    cols = set(_last_pull_changed_cols)
    if changed and cols:
        # Sales first; pull medicines when sales changed (stock lines often follow).
        notify_order = list(_COLLECTIONS)
        for col in notify_order:
            if col in cols:
                _notify(col)
        if "sales" in cols and "medicines" not in cols:
            _notify("medicines")
    # Global master catalog (separate from business sync)
    try:
        from core.app_prefs import load_app_mode
        from core.master_medicine_cloud import pull_master_incremental

        if str(load_app_mode() or "").lower() == "medical":
            ok, _msg, n = pull_master_incremental()
            if ok and n:
                changed += int(n)
    except Exception as exc:
        log.debug("master medicine poll: %s", exc)
    return changed


def start_poller(
    conn,
    on_change: OnChangeCb = None,
    interval_sec: float = _POLL_INTERVAL,
    db_path: Optional[str] = None,
) -> bool:
    """DEPRECATED (B3): watermark live poller.

    Prefer SyncEngine via start_online_sync when USE_REVISION_SYNC=true (default).
    push_* helpers on this module remain fully supported.
    """
    global _poll_thread, _on_change
    from core.sync_prefs import is_online_mode

    if not is_online_mode():
        return False
    try:
        from core.revision_sync_flags import is_revision_sync_enabled

        if is_revision_sync_enabled():
            log.warning(
                "start_poller ignored — USE_REVISION_SYNC is on; "
                "use SyncEngine (start_online_sync)"
            )
            return False
    except Exception:
        pass
    log.warning("Starting DEPRECATED watermark poller (USE_REVISION_SYNC=false)")
    with _poll_lock:
        _on_change = on_change
        if _poll_thread and _poll_thread.is_alive():
            return True
        _poll_stop.clear()
        path = (db_path or "").strip() or None

        def _loop():
            poll_conn = conn
            own_conn = False
            if path:
                try:
                    from core.db_utils import open_store_db

                    poll_conn = open_store_db(path, timeout=60.0)
                    own_conn = True
                except Exception as exc:
                    log.warning("poller open_store_db failed, using shared conn: %s", exc)
                    poll_conn = conn
                    own_conn = False
            try:
                # Catch up immediately (bills saved while Mac2 was closed / idle).
                try:
                    from core.sync_prefs import is_online_mode as _online

                    if _online():
                        n = _poll_once(poll_conn)
                        if n:
                            log.info("server poller initial catch-up: %s change(s)", n)
                except Exception as exc:
                    log.warning("server poller initial pull failed: %s", exc)
                while not _poll_stop.is_set():
                    try:
                        from core.sync_prefs import is_online_mode as _online

                        if _online():
                            _poll_once(poll_conn)
                    except Exception as exc:
                        log.warning("server poller: %s", exc)
                    _poll_stop.wait(interval_sec)
            finally:
                if own_conn:
                    try:
                        poll_conn.close()
                    except Exception:
                        pass

        _poll_thread = threading.Thread(target=_loop, daemon=True, name="server-poller")
        _poll_thread.start()
    return True


def stop_poller() -> None:
    global _poll_thread
    _poll_stop.set()
    with _poll_lock:
        thread = _poll_thread
        _poll_thread = None
    if thread is not None and thread.is_alive() and thread is not threading.current_thread():
        try:
            thread.join(timeout=2.0)
        except Exception:
            pass


def is_poller_alive() -> bool:
    """True when the background server-poller thread is running."""
    with _poll_lock:
        return _poll_thread is not None and _poll_thread.is_alive()


def ensure_poller_running(
    conn,
    on_change: OnChangeCb = None,
    db_path: Optional[str] = None,
) -> bool:
    """Start the poller if Online mode is on but the thread died."""
    from core.sync_prefs import is_online_mode

    if not is_online_mode():
        return False
    if is_poller_alive():
        return True
    try:
        return start_poller(conn, on_change=on_change, db_path=db_path)
    except Exception as exc:
        log.warning("ensure_poller_running failed: %s", exc)
        return False


def start_background(conn, on_change: OnChangeCb = None, db_path: Optional[str] = None) -> None:
    """DEPRECATED (B3): alias for watermark start_poller. Prefer SyncEngine."""
    start_poller(conn, on_change=on_change, db_path=db_path)
