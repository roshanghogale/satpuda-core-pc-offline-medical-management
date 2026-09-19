"""Two-question activation: the shop name, and Online or Offline. Nothing else.

The old activation asked for a username, a password, a device key copied
out of a file, a store name and a mode — "khup lamb process" — and it reached the
server as the vendor ADMINISTRATOR: ``ensure_active_store_on_server`` calls
``api.admin_login()`` with credentials compiled into the build. Two consequences
worth saying out loud:

  * every copy of the desktop carries the password to every shop on the account;
  * that same path matches a store by NAME when the key does not match, which is
    the one accident that can put a stranger inside a real shop's ledger.

This module is the replacement for a FRESH install. It calls one public endpoint,
carries no credential at all, and cannot land on an existing store, because the
server side of it only ever INSERTs (see the server's
``src/services/provisionService.js``). Everything a shop already running depends
on is untouched — ``ensure_active_store_on_server`` and its adoption/recovery
rules still handle every store that has been paired before.

The trial length is the SERVER's number. Nothing here computes an expiry date.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
import threading
from typing import Any

# Only a display fallback for a server that answered without a licence. The
# authoritative window is DEFAULT_EXPIRY_DAYS in the server's licenseService.js;
# every response carries it as ``trial_days``.
FALLBACK_TRIAL_DAYS = 3

_PROVISION_FILE = "provision.json"
_PROVISION_DONE = "provision.done"

# The shop-name rule, in Python, for the one answer no human is watching: the
# installer's file. The activation screen has its own copy
# (desktop/src/storeName.ts) and the installer wizard a third
# (installer/SatpudaCore.iss); all three exist so a name the SERVER would refuse
# -- cleanStoreName() in its src/services/provisionService.js -- is refused
# where it is typed.
#
# This fourth copy has a job the other three do not. They guard a person typing;
# this one guards a FILE, which can arrive truncated, half-written or left over
# from an older install, and an Offline activation never reaches the server at
# all -- so on that path there is nothing else between a broken file and a store
# folder named after it.
STORE_NAME_MIN = 2
STORE_NAME_MAX = 60

_CONTROL_CHARS = re.compile(r"[\u0000-\u001f\u007f]+")
# The Python spelling of the server's /[\p{L}\p{N}]/u: \w minus the underscore
# is exactly letters and digits, in any script, once re is in unicode mode.
_LETTER_OR_DIGIT = re.compile(r"[^\W_]", re.UNICODE)


def clean_store_name(raw: str) -> str:
    """Collapse a name to the label the server would store."""
    return " ".join(_CONTROL_CHARS.sub(" ", str(raw or "")).split())


def usable_store_name(raw: str) -> str:
    """The cleaned name, or "" when the server would refuse it anyway."""
    name = clean_store_name(raw)
    if not (STORE_NAME_MIN <= len(name) <= STORE_NAME_MAX):
        return ""
    if not _LETTER_OR_DIGIT.search(name):
        return ""
    return name


def _appdata_dir() -> str:
    from core.license_manager import _appdata_dir as d

    return d()


def machine_id() -> str:
    """The hardware fingerprint, or "" when this PC could not be read.

    Sent so that "one trial per computer" survives a wiped AppData. Blank rather
    than a hash of nothing: a machine with no readable CPU, board, system UUID or
    disk serial produces the SAME digest as every other such machine, and sending
    that would let the first one of them consume the only trial the rest could
    ever get.
    """
    try:
        from core.license_manager import (
            _get_hardware_hash,
            _hardware_parts,
            _identity_readable,
        )

        if not _identity_readable(_hardware_parts()):
            return ""
        return (_get_hardware_hash() or "").strip()
    except Exception:
        return ""


def device_id() -> str:
    """This installation's id — the same one ``store_devices`` records."""
    try:
        from core.server_api import _pc_device_id

        did = (_pc_device_id() or "").strip()
        if len(did) >= 8:
            return did
    except Exception:
        pass
    hw = machine_id()
    if len(hw) >= 8:
        return f"pc-{hw[:32]}"
    # Last resort: a persisted random id. Weaker than either of the above (it is
    # gone with the folder), but the server still has the per-address and
    # server-wide ceilings, and refusing to activate at all would be worse.
    import uuid

    path = os.path.join(_appdata_dir(), "install_id.txt")
    try:
        if os.path.isfile(path):
            with open(path, encoding="utf-8-sig") as fh:
                val = fh.read().strip()
            if len(val) >= 8:
                return val
    except Exception:
        pass
    val = f"pc-{uuid.uuid4().hex}"
    try:
        os.makedirs(_appdata_dir(), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(val)
    except Exception:
        pass
    return val


def _app_version() -> str:
    try:
        from core.app_version import APP_VERSION  # type: ignore

        return str(APP_VERSION or "")[:32]
    except Exception:
        return ""


def offline_licence_lapsed() -> bool:
    """True when this is an Offline install whose licence date has passed.

    Read straight out of expiry.dat, and deliberately NOT by calling
    ``check_expiry``: that function answers the question by DELETING
    activation.dat (``_invalidate_activation_for_reauth``), which is the very
    state this is here to recognise. Asking it would do the damage again, from a
    code path that has not even been asked for a licence check.
    """
    from datetime import date

    try:
        from core.license_manager import (
            _coerce_enabled,
            _is_online_mode,
            _read_expiry,
            is_expiry_check_applied,
        )

        if _is_online_mode():
            return False
        # The SIGNED licence first, because that is the one being enforced. A
        # shop whose seal has lapsed usually has an empty expiry.dat -- the two
        # were never the same file -- and reading only the old one would say
        # "not expired" about a PC that cannot open its till.
        try:
            from core import license_seal as seal

            st = seal.seal_state(fetch=False)
            if st.get("state") in (seal.STATE_SIGNED, seal.STATE_FRESH):
                return bool(st.get("blocked"))
        except Exception:
            pass
        if not is_expiry_check_applied():
            return False
        data = _read_expiry()
        if not data or not _coerce_enabled(data.get("enabled", False)):
            return False
        return date.today() >= date.fromisoformat(str(data["expiry_date"]).strip())
    except Exception:
        return False


def already_set_up() -> bool:
    """True when this PC is already somebody's till — do not provision again.

    Three ways a computer can already belong to a shop, and all three have to
    count:

      * it is PAIRED to a server store (an SC- key on disk). That is the Online
        case, and it was the only case this function used to check.
      * it is ACTIVATED OFFLINE and has a store registry. No SC- key exists in
        that shop, so the pairing test alone answers "fresh install" about a PC
        with a year of bills on it -- and the caller that believes it would run
        ``setup_initial_store_on_activation`` and switch the active store out
        from under the till. A stale ``provision.json`` (a re-run installer, a
        restored profile) is all it takes to get there.
      * it is an Offline shop whose licence has LAPSED, and has a store
        registry. On the expiry day ``check_expiry`` deletes activation.dat on
        purpose, to force the vendor's re-activation -- so from the moment the
        licence runs out, ``is_activated()`` is False on a PC with a year of
        bills, and the test above stops recognising it. An /ALLUSERS install
        asks its questions in the administrator's empty profile and leaves the
        answers in ProgramData for every user to read, so a shop can meet a
        handoff file on exactly the morning it is least able to survive one:
        the store would be switched to a new empty one, and the till would open
        blank once the vendor extended the date.

    The first two are still deliberately AND, not OR: ``has_registry`` alone is
    true on a machine that created a store folder and never finished activating,
    and refusing that PC would leave it with no way forward at all. The third
    adds only machines that HAVE been activated and have since expired, which is
    the opposite of a half-finished install.
    """
    try:
        from core.store_link import get_local_android_key

        if (get_local_android_key() or "").strip():
            return True
    except Exception:
        pass
    try:
        from core.license_manager import is_activated
        from core.store_manager import has_registry

        if not has_registry():
            return False
        return bool(is_activated() or offline_licence_lapsed())
    except Exception:
        return False


def normalize_sync_mode(value: str) -> str:
    """"offline" or "online" -- anything unreadable means Online.

    The two-question screen always sends one of the two. This is for the
    installer's file, which a shopkeeper's antivirus, a text editor or a partial
    write can hand back as anything at all.
    """
    from core.sync_prefs import MODE_OFFLINE, MODE_ONLINE

    text = str(value or "").strip().lower()
    return MODE_OFFLINE if text.startswith("off") else MODE_ONLINE


# One activation at a time, per process.
#
# core/desktop_api.py serves on a ThreadingHTTPServer, and get_license_status --
# which runs the installer's pending provision -- is polled every few seconds
# while the activation screen is up. The sign-up itself takes up to 60 seconds,
# so a second caller (the next poll, or the shopkeeper pressing Start on the
# form the first call has not finished answering yet) can arrive while the first
# is still in flight. Both would pass the "already activated?" check, both would
# be granted a store, and the second would burn this computer's one trial on a
# store nothing is pinned to. Re-entrant because run_pending_provision holds it
# across its own call to activate_trial.
_PROVISION_LOCK = threading.RLock()

def activate_trial(store_name: str, sync_mode: str = "") -> dict[str, Any]:
    """Create a trial store on the server and leave this PC running.

    The whole of activation, and it takes exactly the two things the screen
    asks: the shop name, and Online or Offline. Returns the same shape
    ``activate_license`` returns so the Tauri screen can render either.
    """
    from core.store_manager import normalize_display_name

    name = normalize_display_name(store_name or "")
    if not name:
        return {"ok": False, "error": "Enter the shop name."}
    mode = normalize_sync_mode(sync_mode)

    with _PROVISION_LOCK:
        return _activate_trial_locked(name, mode)


def _activate_trial_locked(name: str, mode: str) -> dict[str, Any]:
    """The body of activate_trial, with the single-flight lock already held.

    The "already connected" test has to be INSIDE the lock. Outside it, two
    callers both read "not connected" before either has finished, and the shop
    ends up with two stores and no trial left.
    """
    from core import server_api as api
    from core import server_live as live
    from core.license_manager import (
        _cache_server_license_locally,
        _get_hardware_hash,
        _write_activation,
        _write_hw_cache,
        save_activation_date_local,
    )
    from core.store_link import _save_local
    from core.store_manager import (
        get_active_store_key,
        setup_initial_store_on_activation,
    )
    from core.sync_prefs import MODE_OFFLINE, MODE_ONLINE, set_sync_mode

    if already_set_up():
        return {
            "ok": False,
            "error": (
                "This computer is already connected to a store. Use Settings to "
                "change which store it belongs to."
            ),
        }

    # ASK FIRST, CHANGE NOTHING UNTIL THE ANSWER IS YES.
    #
    # This used to switch the PC to Online and create the local store before
    # calling. On a refusal -- the per-address limit, a flat network -- that left
    # a fresh install in Online mode with no store on the server: needs_activation
    # was true, has_registry had become true, and the screen therefore offered the
    # LONG three-factor form to a shopkeeper who had done nothing wrong and only
    # needed to press Start again. Nothing local moves until the trial exists.
    dev = device_id()
    try:
        data = api.provision_trial(
            store_name=name,
            device_id=dev,
            machine_id=machine_id(),
            app_version=_app_version(),
            device_name="Mac2 PC",
            # Online is waiting for the thing it asked for and can afford the
            # full minute. Offline is not: the sign-up there only puts the shop
            # on the owner's Trials page, and this runs on the launch path -- a
            # shop with no internet must not watch a splash screen for a minute
            # before the till it was promised opens.
            timeout=20.0 if mode == MODE_OFFLINE else 60.0,
        )
    except Exception as exc:
        note = _friendly(exc, offline=mode == MODE_OFFLINE)
        if mode == MODE_OFFLINE:
            # An Offline shop was never promised a server. Refusing here would
            # mean a PC with no internet -- the exact PC that chooses Offline --
            # cannot be activated at all, and the three factors that used to
            # stand in for this are gone. So the sign-up is best effort: it is
            # what puts the shop on the owner's Trials page, not what lets the
            # till open. The server's own sentence is carried through, so the
            # shopkeeper is told his shop is not on the server yet.
            return _activate_offline_locally(name, note)
        return {"ok": False, "error": note}

    remote = dict(data.get("store") or {})
    android_key = str(remote.get("android_key") or "").strip()
    if not android_key:
        if mode == MODE_OFFLINE:
            return _activate_offline_locally(
                name, "The server did not return a pairing key."
            )
        return {"ok": False, "error": "The server did not return a pairing key."}

    try:
        set_sync_mode(mode)
    except Exception as exc:
        return {"ok": False, "error": f"Could not save the {mode} setting: {exc}"}

    try:
        setup_initial_store_on_activation(name)
    except Exception as exc:
        return {"ok": False, "error": str(exc)}

    store_key = get_active_store_key() or "Store_Default"
    _save_local(android_key, store_key)

    # Pin this PC to the store the server just made. Without the pin, a later
    # ensure_active_store_on_server matches on the display NAME, and a trial
    # called "Medical Store" would try to adopt whichever real shop is called
    # that. With it, identity is the server's random store_id and the name stops
    # deciding anything.
    try:
        live._record_store_adoption(store_key, remote)
    except Exception:
        pass

    session = {
        "token": data.get("token"),
        "store_id": remote.get("store_id"),
        "store_key": remote.get("store_key") or store_key,
        "store_name": remote.get("store_name") or name,
        "android_key": android_key,
        "device_id": dev,
        "paired_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
    }
    if session["token"]:
        # The endpoint already signed a token for the store it created, so the
        # app is Online without a second round trip. Pairing is still attempted
        # below; this only means a slow or flaky /auth/pair cannot leave a
        # freshly installed shop staring at an activation screen.
        try:
            api.save_session(store_key, session)
        except Exception:
            pass
    if mode == MODE_ONLINE:
        try:
            api.ensure_store_session(
                store_key=store_key,
                android_key=android_key,
                store_name=remote.get("store_name") or name,
                device_id=dev,
                force_pair=not session.get("token"),
            )
        except Exception:
            pass

    lic = dict(data.get("license") or {})
    activation_date = str(lic.get("activation_date") or "")[:10]
    expiry_date = str(lic.get("expiry_date") or "")[:10]
    if activation_date:
        save_activation_date_local(activation_date)

    # THE ACTIVATION RECORD FIRST, and the licence after it.
    #
    # So the app opens ALREADY ACTIVATED rather than showing the activation
    # screen again. Online mode does not gate on activation.dat -- needs_activation
    # only asks whether an SC- key exists -- but get_license_status reports
    # `activated` from it, and a shop that has just activated should be told so.
    #
    # THE ORDER MATTERS, and it did not used to. _write_activation REWRITES
    # activation.dat, and two marks that the licence sets live inside it: the
    # rollback ratchet, and the record that this computer has held a signed
    # licence. Storing the blob first and writing the record afterwards wiped
    # both -- so a PC that had completed a real Online sign-up and then deleted
    # license.seal looked exactly like a fresh install and would have been
    # handed a starter window as the reward. _activate_offline_locally has
    # ordered these correctly all along, for the same reason.
    try:
        hw = _get_hardware_hash()
        _write_activation(hw)
        _write_hw_cache(hw)
    except Exception:
        pass

    # THE SIGNED LICENCE, stored here and in BOTH modes.
    #
    # This is the one moment an Offline shop is known to have internet -- the
    # sign-up it has just completed -- and it is therefore the moment it must
    # come away with a blob it can be held to. Storing it is what stops the
    # expiry from being a local file the shop can delete: from here on the date
    # is a signed statement by the server, and losing the file means fetching
    # the same statement again rather than starting over with none.
    try:
        from core import license_seal as seal

        seal.store_seal(str(data.get("signed") or ""), fresh=True)
    except Exception:
        pass
    # The unsigned mirror, in BOTH modes now.
    #
    # This used to be Online only, and the comment here said why: Offline mode
    # treated expiry.dat as the authority, so importing the server's trial date
    # would put a hard stop on the shop three days later with no way past it
    # except the vendor. That reasoning has expired with the file's authority.
    # The signed blob above is what decides an Offline licence now, it already
    # carries this same date, and the mirror is only what older screens read --
    # so leaving it blank would make the Settings page disagree with the licence
    # actually being enforced.
    try:
        _cache_server_license_locally(lic)
    except Exception:
        pass

    return {
        "ok": True,
        "activated": True,
        "sync_mode": mode,
        "store_name": remote.get("store_name") or name,
        "android_key": android_key,
        # The SC- key is how an Android phone joins an ONLINE store. An Offline
        # shop has nothing to do with it yet, so it is kept (a later switch to
        # Online finds it) but not put on screen.
        "show_key": mode == MODE_ONLINE,
        "server_note": "",
        "activation_date": activation_date,
        "expiry_date": expiry_date,
        "access_allowed": lic.get("access_allowed"),
        "trial_days": int(lic.get("trial_days") or data.get("trial_days") or FALLBACK_TRIAL_DAYS),
    }


def _activate_offline_locally(name: str, note: str) -> dict[str, Any]:
    """Activate an Offline install without the server.

    Everything Offline mode actually needs is on this computer: the store folder,
    the SQLite file and ``activation.dat``. This writes those three and nothing
    else -- no expiry is invented, no key is stored, no second call is made.

    ``note`` is why the server was not used. It is carried back rather than
    swallowed, so the shopkeeper is told his shop has not reached the owner's
    Trials page yet instead of being told nothing at all.
    """
    from core.license_manager import (
        _cache_server_license_locally,
        _get_hardware_hash,
        _write_activation,
        _write_hw_cache,
        save_activation_date_local,
    )
    from core.store_manager import setup_initial_store_on_activation
    from core.sync_prefs import MODE_OFFLINE, set_sync_mode

    try:
        set_sync_mode(MODE_OFFLINE)
    except Exception as exc:
        return {"ok": False, "error": f"Could not save the Offline setting: {exc}"}

    try:
        setup_initial_store_on_activation(name)
    except Exception as exc:
        return {"ok": False, "error": str(exc)}

    activation_date = ""
    try:
        activation_date = str(save_activation_date_local() or "")[:10]
    except Exception:
        activation_date = ""

    # Offline mode gates on this file and nothing else (``needs_activation`` ->
    # ``is_activated``). Without it the shop is sent straight back to the
    # activation screen it has just finished, so a failure here is fatal and
    # must be reported rather than shrugged off.
    try:
        hw = _get_hardware_hash()
        _write_activation(hw)
        _write_hw_cache(hw)
    except Exception as exc:
        return {"ok": False, "error": f"Could not save the activation record: {exc}"}

    # AFTER the activation record, and never before it: does this computer
    # already have a licence on the server?
    #
    # This is the exact path a shopkeeper reaches by deleting AppData and running
    # the installer again. The sign-up is refused -- the server remembers this
    # computer has had its trial -- and the old code read that refusal as
    # "activate locally, no expiry", which handed out an unlimited licence as the
    # reward for deleting a folder. The recovery call asks the server for the
    # licence this MACHINE already has, keyed on the hardware fingerprint, which
    # is the one thing a wipe cannot take away because it is read off the machine
    # rather than stored on it. The answer is usually an expiry in the past,
    # which is the correct answer.
    #
    # It runs after ``_write_activation`` because that call rewrites
    # activation.dat, and the rollback ratchet the licence sets lives inside it.
    # Recovering first would have the activation record wipe the mark a moment
    # later.
    recovered = ""
    try:
        from core import license_seal as seal

        # Eight seconds, not twenty. The case where this matters most -- the
        # sign-up refused because this computer has already had its trial -- is
        # a case where the network is fine and the answer is immediate. The slow
        # case is no network at all, and that one has already spent the
        # provision call's own timeout finding out.
        payload = seal.fetch_seal(timeout=8.0, force=True)
        if payload:
            recovered = str(payload.get("exp") or "")
            if payload.get("act"):
                save_activation_date_local(str(payload.get("act"))[:10])
                activation_date = str(payload.get("act"))[:10]
    except Exception:
        pass

    # NO LICENCE CAME BACK, and the till still has to open.
    #
    # This is the state that produced the "Licence not found" screen: activated,
    # told it had succeeded, and holding nothing a licence read could believe.
    # license_seal.fresh_install_window gives such a PC the same window the
    # server would have -- FRESH_INSTALL_DAYS from today -- and the dates are
    # mirrored into expiry.dat here so the Settings page shows what is actually
    # being enforced instead of a blank. Nothing is invented: the mirror is not
    # an authority anywhere, and the moment a signed licence arrives it decides.
    starter_until = ""
    if not recovered:
        try:
            from core import license_seal as seal

            window = seal.fresh_install_window() or {}
            starter_until = str(window.get("expiry_date") or "")
            if starter_until:
                _cache_server_license_locally({
                    "activation_date": activation_date or window.get("activation_date"),
                    "expiry_date": starter_until,
                    "expiry_enabled": True,
                    "apply_expiry_check": True,
                })
        except Exception:
            starter_until = ""

    return {
        "ok": True,
        "activated": True,
        "sync_mode": MODE_OFFLINE,
        "store_name": name,
        "android_key": "",
        "show_key": False,
        "server_note": note or "",
        "activation_date": activation_date,
        # The recovered date when this computer already had a licence, so the
        # screen says "your licence ended on the 12th" rather than nothing at
        # all; otherwise the starter window this PC is now running on.
        "expiry_date": recovered or starter_until,
        # True only for the starter window, so the screen can say which of the
        # two it is looking at.
        "starter_licence": bool(starter_until and not recovered),
        "trial_days": FALLBACK_TRIAL_DAYS,
        "access_allowed": None,
    }


_HTML_TAG = re.compile(r"<[^>]{0,400}>")


def _sentence(text: Any) -> str:
    """One readable line out of whatever came back, or "".

    The layer below hands up an error body verbatim, and a body is not always a
    sentence: an Express server answers an unknown route with a whole HTML page.
    Tags out, whitespace collapsed, and a length a screen can hold.
    """
    body = " ".join(_HTML_TAG.sub(" ", str(text or "")).split())
    return body[:300]


# The endpoint is new. Until the owner deploys the server patch that adds it,
# /api/provision/trial does not exist -- and Express answers a route it does not
# have with an HTML page, which used to arrive on the activation screen as a
# page of markup. That told the shopkeeper nothing, and told whoever he
# telephoned nothing either. These say what is actually wrong, and the Online
# one says the thing he can do about it this minute, since Offline activates
# without the server.
_NO_ENDPOINT_ONLINE = (
    "This Satpuda Core server has not been updated for the new sign-up yet, so "
    "a new shop cannot be created on it. Choose Offline to start using the "
    "software on this computer now, or ask Satpuda to update the server."
)
_NO_ENDPOINT_OFFLINE = (
    "This Satpuda Core server has not been updated for the new sign-up yet, so "
    "the shop is not on Satpuda's list. The software is set up on this computer "
    "and can be used; ask Satpuda to update the server."
)
_NO_ENDPOINT_STATUS = (404, 405, 501)

# THE SERVER BROKE, which is not the same as "the server said no".
#
# A 5xx from /api/provision/trial reaches the client as the error middleware's
# own sentence -- "Something went wrong on the server. Quote reference E… to
# support." -- and that was the whole of what a shopkeeper saw, because only
# 404/405/501 were special-cased. It is a support reference and nothing he can
# act on, and on live it is EVERY sign-up: assertWithinLimits sends an untyped
# parameter and Postgres refuses the statement (42P08), so the endpoint has
# answered 500 since the day it shipped.
#
# So say what happened, in the order a person needs it: what failed, what still
# works, and the reference last for whoever he telephones.
_SERVER_BROKEN_ONLINE = (
    "The Satpuda server could not create the shop just now — this is a problem "
    "on the server, not on this computer, and nothing has been changed here. "
    "Choose Offline to start using the software on this computer right away "
    "(you can switch to Online from Settings once Satpuda has fixed it), or try "
    "again in a few minutes."
)
_SERVER_BROKEN_OFFLINE = (
    "The Satpuda server could not register the shop just now, so it is not on "
    "Satpuda's list yet — that is a problem on the server, not on this "
    "computer. The software is set up here and can be used; connect the "
    "internet again later and it will be registered."
)
_SERVER_BROKEN_STATUS = (500, 502, 504)


def _friendly(exc: Exception, *, offline: bool = False) -> str:
    """A sentence a shopkeeper can act on, not a stack trace."""
    from core.server_api import ServerHttpError

    if isinstance(exc, ServerHttpError):
        if exc.status in _NO_ENDPOINT_STATUS:
            return _NO_ENDPOINT_OFFLINE if offline else _NO_ENDPOINT_ONLINE
        if exc.status in _SERVER_BROKEN_STATUS:
            base = _SERVER_BROKEN_OFFLINE if offline else _SERVER_BROKEN_ONLINE
            ref = _support_reference(exc)
            return f"{base} {ref}" if ref else base
        msg = _sentence(exc.message)
        if msg:
            return msg
        if exc.status in (429, 503):
            return "Too many sign-ups just now. Please wait a little and try again."
        return f"The server refused the request (HTTP {exc.status})."
    text = _sentence(exc)
    return text or "Could not reach the Satpuda server. Check the internet connection."


# Anchored on the word the server itself uses ("Quote reference E… to support"),
# not on the shape of the code: a bare "starts with E" pattern matches the word
# "Error" and would put that on the screen as a support reference.
_SUPPORT_REF = re.compile(r"reference\s+([A-Za-z0-9][A-Za-z0-9_-]{3,40})", re.I)


def _support_reference(exc: Exception) -> str:
    """The server's own reference code, kept at the end for whoever he rings.

    The body is "…Quote reference E1a2b3c4 to support." -- useless as the whole
    message, useful as the last sentence of one.
    """
    body = _sentence(getattr(exc, "message", "") or "")
    match = _SUPPORT_REF.search(body)
    if not match:
        return ""
    return f"Satpuda support reference: {match.group(1)}."


# ── The installer's seam ──────────────────────────────────────────────────────
#
# The installer writes ONE small file and nothing else:
#
#     %LOCALAPPDATA%\VeterinaryApp\provision.json
#     {"store_name": "Roshan Medical", "sync_mode": "online"}
#
# Both answers the wizard asked, and nothing else. A plain text file works too:
# the name on the first line, "online" or "offline" on the second. On the first
# launch the app reads it, activates, and renames it to provision.done -- so it
# runs exactly once, and a second launch (or a crash between the two) cannot
# start a second trial.
#
# WHY A FILE.
#   * It lands in %LOCALAPPDATA%\VeterinaryApp, which is the app's own data
#     folder -- the same folder it already keeps the store registry, the licence
#     and the databases in. No new location, and no permission question: the app
#     writes there constantly, so it can certainly retire the file.
#   * It is CONSUMABLE. Retiring it is a rename inside a folder the app owns.
#     A registry value would have to be deleted instead, and an HKLM value
#     written by an elevated installer is exactly what a standard user cannot
#     delete -- which would mean a fresh trial attempt on every single launch.
#   * A command-line flag dies with the shortcut that carries it. The app is
#     started from the desktop shortcut, the Start Menu, the .exe itself and
#     after every update; a flag would be missing on most of those launches and
#     repeated on the rest -- silently asking for a new trial each time.
#   * Nothing secret lives in it, which is the point: there is nothing the
#     installer could hold that would still be a secret after the first download.
#
# THE OTHER WINDOWS USER. %LOCALAPPDATA% is per-user, and so is the app's whole
# data directory -- a second Windows user has no store, no licence and no books
# of the first user's, so a second user is a genuinely fresh install and gets
# the two-question screen. That is correct rather than a gap. The one case where
# the file could land in the WRONG profile is an all-users install
# (``/ALLUSERS``), where Setup is elevated and {localappdata} is the
# administrator's folder, not the shopkeeper's. For that case the installer also
# drops a copy in %PROGRAMDATA%\SatpudaCore, which the code below reads when the
# per-user file is absent -- once per Windows user, recorded per user, because
# ProgramData is shared and a standard user may not be able to delete it.

_SHARED_DIR = "SatpudaCore"


def _shared_provision_path() -> str:
    """The all-users copy, or "" where there is no such place (not Windows)."""
    base = (os.environ.get("PROGRAMDATA") or "").strip()
    if not base or not os.path.isdir(base):
        return ""
    return os.path.join(base, _SHARED_DIR, _PROVISION_FILE)


def _parse_provision(raw: str) -> dict[str, str]:
    """The two answers out of whatever the installer actually wrote.

    Nothing, rather than a guess, whenever the file is not clearly readable. It
    is written by an installer that can be killed mid-write, copied by an
    imaging tool, or left behind by an older build, and the caller acts on what
    comes out of here without a human ever seeing it.

    A file that BEGINS as JSON is read as JSON or not at all. Falling through to
    the line reader on a parse error is exactly what made a half-written file
    dangerous: ``{"store_name": "Roshan Medical", "sync_mo`` is not JSON, but as
    a first line it is a perfectly acceptable 41-character shop name -- and the
    mode, being unreadable, defaulted to Online, which is not what the
    shopkeeper chose either. Offline never reaches the server, so nothing
    downstream would have caught it: the store folder would simply have been
    named after the fragment.
    """
    text = (raw or "").strip()
    if not text:
        return {}
    if text[0] in "{[":
        try:
            data = json.loads(text)
        except Exception:
            return {}
        if not isinstance(data, dict):
            return {}
        name = usable_store_name(data.get("store_name") or data.get("name") or "")
        if not name:
            return {}
        return {
            "store_name": name,
            "sync_mode": normalize_sync_mode(
                str(data.get("sync_mode") or data.get("mode") or "")
            ),
        }
    # Plain text: the name on the first line, the mode on the second.
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    name = usable_store_name(lines[0] if lines else "")
    if not name:
        return {}
    return {
        "store_name": name,
        "sync_mode": normalize_sync_mode(lines[1] if len(lines) > 1 else ""),
    }


def _read_provision_file(path: str) -> dict[str, str]:
    try:
        if not path or not os.path.isfile(path):
            return {}
        with open(path, encoding="utf-8-sig") as fh:
            raw = fh.read()
    except Exception:
        return {}
    found = _parse_provision(raw)
    if found:
        found["path"] = path
    return found


def pending_provision() -> dict[str, str]:
    """The installer's answers: store_name, sync_mode, and which file they came from.

    The per-user file first. The shared one only when this user has neither the
    file nor the ``provision.done`` marker that says he has already been through
    this once -- otherwise every launch by every user on a shared PC would try
    to start a trial off the same undeleteable ProgramData file.
    """
    mine = _read_provision_file(os.path.join(_appdata_dir(), _PROVISION_FILE))
    if mine:
        mine["shared"] = ""
        return mine
    if os.path.exists(os.path.join(_appdata_dir(), _PROVISION_DONE)):
        return {}
    shared = _read_provision_file(_shared_provision_path())
    if shared:
        shared["shared"] = "1"
    return shared


def pending_provision_name() -> str:
    """The shop name the installer left behind, or ""."""
    return pending_provision().get("store_name", "")


def _consume_provision_file(outcome: str, pending: dict[str, str] | None = None) -> None:
    """Retire the answers so this can never run twice."""
    dst = os.path.join(_appdata_dir(), _PROVISION_DONE)
    src = str((pending or {}).get("path") or "") or os.path.join(
        _appdata_dir(), _PROVISION_FILE
    )
    shared = bool((pending or {}).get("shared"))
    try:
        if os.path.isfile(src):
            if shared:
                # ProgramData belongs to everybody, so this user's record of
                # having used it goes in HIS folder FIRST -- if the delete below
                # is refused (a standard user cannot always remove a file an
                # administrator created), the marker is still what stops the
                # next launch. Then remove the shared copy on a best-effort
                # basis so the other users on this PC are not handed the first
                # shopkeeper's shop name.
                try:
                    with open(dst, "w", encoding="utf-8") as fh:
                        fh.write("")
                except Exception:
                    pass
                try:
                    os.remove(src)
                except Exception:
                    pass
            else:
                try:
                    os.replace(src, dst)
                except Exception:
                    os.remove(src)
    except Exception:
        return
    try:
        with open(dst + ".txt", "w", encoding="utf-8") as fh:
            fh.write(outcome)
    except Exception:
        pass


_RAN_THIS_PROCESS = False

# The refused sign-up, kept for the rest of the process.
#
# It has to live here rather than in the reply, because the caller that SPENDS
# the reply is not the caller that shows it. desktop/src/App.tsx reads
# /api/license/status once, to decide whether to show the activation screen at
# all; only then does the screen mount and read the status a second time. The
# run happens on the first read and answers ``ran: False`` to every read after
# it -- so the sentence, and both answers the installer collected, reached a
# boot check that looks at ``needs_activation`` and nothing else, and the screen
# that came up a moment later got a blank form and no explanation. The file is
# retired either way (retrying it every launch would walk a shop into the
# per-address limit), so if the answers are not held here they are simply gone.
_LAST_FAILURE: dict[str, str] = {}


def last_provision_failure() -> dict[str, str]:
    """The installer's refused sign-up: error, store_name, sync_mode. {} if none."""
    return dict(_LAST_FAILURE)


def run_pending_provision() -> dict[str, Any]:
    """Activate from the installer's file, at most once per process.

    Called from the licence status read, which the desktop polls. The guards
    matter more than the work: a poll runs several times a second, and an
    unguarded network call there would hammer the endpoint into its own rate
    limit and then tell the shop it had been refused.
    """
    global _RAN_THIS_PROCESS, _LAST_FAILURE
    # Reading and setting the flag under the lock is the whole point: a bare
    # `if _RAN_THIS_PROCESS` is checked and set with file reads in between, and
    # two polling threads fit through that gap comfortably.
    with _PROVISION_LOCK:
        if _RAN_THIS_PROCESS:
            return {"ok": False, "ran": False}
        pending = pending_provision()
        name = str(pending.get("store_name") or "")
        if not name:
            _RAN_THIS_PROCESS = True
            return {"ok": False, "ran": False}
        mode = normalize_sync_mode(pending.get("sync_mode") or "")
        if already_set_up():
            # Already somebody's till; the file is stale. Retire it without
            # calling out, and without touching the store that is already here.
            _RAN_THIS_PROCESS = True
            _consume_provision_file(
                "skipped: this PC already belongs to a store", pending
            )
            return {"ok": False, "ran": False}
        _RAN_THIS_PROCESS = True
        result = activate_trial(name, sync_mode=mode)
        _consume_provision_file(
            "activated" if result.get("ok") else f"failed: {result.get('error') or 'unknown'}",
            pending,
        )
        result["ran"] = True
        # Carried back so a refused sign-up does not ask the shopkeeper to type
        # the name a second time, or to pick the mode again. The file is retired
        # either way -- retrying it on every launch would walk a shop straight
        # into the per-address limit -- so the answers have to travel in the
        # reply or they are gone.
        result.setdefault("store_name", name)
        result.setdefault("sync_mode", mode)
        if result.get("ok"):
            _LAST_FAILURE = {}
        else:
            # Kept for every later status read, not just this one. See
            # _LAST_FAILURE above.
            _LAST_FAILURE = {
                "error": str(result.get("error") or "")
                or "The sign-up could not be completed.",
                "store_name": str(result.get("store_name") or name),
                "sync_mode": str(result.get("sync_mode") or mode),
            }
        return result
