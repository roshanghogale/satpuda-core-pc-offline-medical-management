"""License / activation for the Tauri desktop API.

Mirrors widgets.activation_dialog (classic Tk): named local store, then
create/pair that store on Satpuda Core Server when Online is chosen.
The server stores activation_date and sets expiry_date itself.

Two ways in:

  * ``activate_trial`` -- a fresh install. Two answers: the shop name and
    Online or Offline. Goes to the public provisioning endpoint, carries no
    credential, and leaves the PC in the mode that was chosen. See
    core/trial_activation.py.
  * ``activate_license`` -- the long-standing three-factor path, unchanged and
    no longer on the first screen. Still what an existing shop reconnecting, a
    satellite PC restoring from Drive, and a shop the vendor is re-activating
    after an expiry use; the screen keeps it one click away.
"""
from __future__ import annotations

import os
import shutil
from typing import Any

# The trial window is the SERVER's to decide -- DEFAULT_EXPIRY_DAYS in
# server src/services/licenseService.js -- and every licence response now carries
# it as ``trial_days``. This constant is what the screen falls back to when the
# server answered without one, and it must not drift away from the server's
# value: a build that says "10 days" over a licence the server ends in 3 is a
# shop that believes it has a week it does not have.
ONLINE_TRIAL_DAYS = 3


def server_trial_days(default: int = ONLINE_TRIAL_DAYS) -> int:
    """The trial length the server reports, falling back to the constant above."""
    try:
        from core.license_manager import fetch_server_license

        lic = fetch_server_license() or {}
        days = int(lic.get("trial_days") or 0)
        if days > 0:
            return days
    except Exception:
        pass
    return default


def get_license_status() -> dict[str, Any]:
    # The installer's file, if there is one. Runs at most once per process and
    # does nothing at all when the file is absent -- which is every launch after
    # the first, and every install that did not come through the installer. This
    # is what makes a fresh PC open ALREADY ACTIVATED: by the time the screen
    # asks whether activation is needed, it is not.
    provision_note = ""
    provision_name = ""
    provision_mode = ""
    provision_code = ""
    try:
        from core.trial_activation import last_provision_failure, run_pending_provision

        run_pending_provision()
        # Deliberately NOT the return value of the call above.
        #
        # The run happens once per process and answers "ran: False" to every
        # read after it -- and the read that runs it is not the read that shows
        # it. desktop/src/App.tsx checks the licence to decide whether to put
        # the activation screen up at all, and the screen then reads the status
        # again for itself. Reading the outcome out of the reply meant the
        # refusal, and both answers the installer had collected, were consumed
        # by the boot check and the screen came up blank. So ask the module what
        # it knows, which any number of reads may do.
        failure = last_provision_failure()
        provision_note = str(failure.get("error") or "")
        provision_name = str(failure.get("store_name") or "")
        provision_mode = str(failure.get("sync_mode") or "")
        provision_code = str(failure.get("code") or "")
    except Exception as exc:
        provision_note = str(exc)

    from core.license_manager import (
        check_expiry,
        get_device_key_path,
        is_activated,
        needs_activation,
        prepare_device_key,
    )
    from core.store_manager import get_active_display_name, has_registry
    from core.sync_prefs import get_sync_mode, is_online_mode

    # Classic main.py: Online expiry is NOT credential re-activation.
    expired = bool(check_expiry())
    online = is_online_mode()
    needs = needs_activation()

    # WHY ACCESS IS BLOCKED, not merely that it is.
    #
    # "Your licence ran out" and "this computer has lost its licence file and
    # needs the internet for a minute" are different sentences with different
    # next steps, and the second must not send a shopkeeper hunting for a device
    # key he does not need. A missing licence is therefore never treated as an
    # Offline expiry: that path deletes activation.dat to force the vendor's
    # re-activation, and doing that because a file went missing destroys the one
    # record saying this PC was ever activated.
    block_reason = ""
    seal_message = ""
    if expired:
        try:
            from core.license_manager import expiry_block_reason

            block_reason = expiry_block_reason()
        except Exception:
            block_reason = ""
    try:
        from core import license_seal as seal

        _st = seal.seal_state(fetch=False)
        seal_message = str(_st.get("message") or "")
        seal_state = str(_st.get("state") or "")
    except Exception:
        seal_state = ""
    needs_internet = block_reason == "needs_internet"

    access_blocked = bool(expired and (online or needs_internet))
    expiry_reactivation = bool((not online) and expired and not needs_internet)
    device_key = ""
    if needs or expiry_reactivation:
        prepare_device_key()
        path = get_device_key_path()
        if os.path.isfile(path):
            try:
                with open(path, encoding="utf-8-sig") as fh:
                    device_key = fh.read().strip()
            except Exception:
                device_key = ""

    store_name = ""
    try:
        store_name = get_active_display_name() or ""
    except Exception:
        pass

    return {
        "ok": True,
        "needs_activation": needs,
        "access_blocked": access_blocked,
        "activated": is_activated() and not expired,
        "expiry_reactivation": expiry_reactivation,
        "device_key": device_key,
        "device_key_path": get_device_key_path(),
        "has_registry": has_registry(),
        "store_name": store_name,
        "sync_mode": get_sync_mode(),
        # Non-empty when the licence file is gone or does not verify. The screen
        # shows this instead of an expiry message: the shop is not out of time,
        # it is out of a licence file, and one connection puts it back.
        "needs_internet": needs_internet,
        "block_reason": block_reason,
        "seal_state": seal_state,
        "seal_message": seal_message,
        # Non-empty only when the installer left a shop name and the sign-up was
        # refused. The screen shows it above the one name field, so the shopkeeper
        # sees "too many trials from this connection" rather than a blank form.
        "provision_error": provision_note,
        # The name the installer asked for, when the sign-up was refused, so the
        # first question is already answered and Start is the only thing left.
        "provision_store_name": provision_name,
        # And the mode he picked in the installer, so the second question is
        # already answered too.
        "provision_sync_mode": provision_mode,
        # "name_exists": the installer's shop name is already on the server, so
        # the screen asks for the SC- key or a deliberate new shop.
        "provision_code": provision_code,
    }


def _finish_store_setup(store_name: str, *, satellite: bool) -> None:
    from core.store_manager import setup_initial_store_on_activation, setup_satellite_store_from_restore

    store_name = (store_name or "").strip()
    if not store_name:
        raise ValueError("Store name is required.")
    if satellite:
        from core.backup_manager import restore_latest_backup_from_drive

        ok, result = restore_latest_backup_from_drive(store_name)
        if not ok:
            raise RuntimeError(result if isinstance(result, str) else "Drive restore failed.")
        tmp_db = result["db_path"] if isinstance(result, dict) else result
        try:
            setup_satellite_store_from_restore(store_name, tmp_db)
        finally:
            tmp_parent = os.path.dirname(tmp_db)
            if tmp_parent and os.path.isdir(tmp_parent):
                shutil.rmtree(tmp_parent, ignore_errors=True)
    else:
        setup_initial_store_on_activation(store_name)


def _record_server_activation() -> dict[str, Any]:
    """Record activation_date on the server; the server sets the expiry window."""
    from core import server_api as api
    from core.license_manager import save_activation_date_local

    day = save_activation_date_local()
    token = api.store_token_for_active()
    remote = api.put_license(
        token,
        {
            "activation_date": day,
            "expiry_enabled": True,
            "apply_expiry_check": True,
        },
    )
    if not isinstance(remote, dict):
        raise RuntimeError("Server did not save the store licence.")
    act = str(remote.get("activation_date") or day or "")[:10]
    exp = str(remote.get("expiry_date") or "")[:10]
    # The server sets expiry itself, from activation + DEFAULT_EXPIRY_DAYS.
    #
    # This used to push a locally computed date when the response came back
    # without one. It cannot work and must not be relied on: routes/auth.js
    # deletes expiry_date, expiry_enabled and apply_expiry_check from a device's
    # PUT before it reaches the database -- deliberately, so an expired store
    # cannot un-expire itself. Re-reading is the honest way to find out what the
    # server decided; inventing a date only made the screen and the server
    # disagree about when the trial ends.
    if act and not exp:
        try:
            exp = str((api.get_license(token) or {}).get("expiry_date") or "")[:10]
        except Exception:
            exp = ""
    try:
        from core.license_manager import _cache_server_license_locally

        _cache_server_license_locally(remote)
    except Exception:
        pass
    sealed, seal_note = store_signed_license(token)
    return {
        "activation_date": act,
        "expiry_date": exp,
        "access_allowed": remote.get("access_allowed"),
        "is_active": remote.get("is_active"),
        "sealed": sealed,
        "seal_note": seal_note,
    }


def store_signed_license(token: str) -> tuple[bool, str]:
    """Fetch the SIGNED licence for this store and keep it. Returns (stored, note).

    A STEP OF ACTIVATION, not a side effect of one.

    This used to happen only by luck: ``license_manager.get_expiry_state`` fires
    an opportunistic background refresh whenever the server happens to answer an
    Online licence read, and that was the only thing that ever wrote
    license.seal on this path. A PC closed before the worker finished, or a
    server slow to answer, left a shop ACTIVATED with no signed licence -- and
    the first launch without internet then showed "Licence not found", because a
    missing blob is not "no restriction", it is no licence.

    The trial path has always stored it deliberately (trial_activation). This
    makes the long form and the SC- key path do the same, on the round trip they
    have already earned.
    """
    from core import license_seal as seal
    from core import server_api as api

    try:
        data = api.get_signed_license(token, seal._binding_body()) or {}
    except Exception as exc:
        return False, (
            "The shop is activated, but this computer could not collect its "
            f"signed licence from the server ({exc}). Connect the internet and "
            "open the software again."
        )
    blob = str(data.get("signed") or "")
    if not blob:
        return False, (
            "The shop is activated, but the server did not send a signed "
            "licence. Connect the internet and open the software again."
        )
    if not seal.store_seal(blob, fresh=True):
        # The row moved but the blob will not verify here -- the wrong public
        # key in this build, or a machine binding that does not match. Said out
        # loud rather than left to fail silently on the next offline launch,
        # exactly as license_manager.save_expiry_settings does.
        return False, (
            "The shop is activated, but this computer could not verify the "
            "signed licence the server sent back. Contact Satpuda."
        )
    return True, ""


def _link_online_store() -> tuple[str, str]:
    """Create/pair the active store on Satpuda Core Server. Returns (android_key, note)."""
    from core.store_link import get_local_android_key

    note = ""
    key = ""
    try:
        from core import server_live as live

        session = live.ensure_active_store_on_server(create_if_missing=True)
        key = (session.get("android_key") or "").strip()
    except Exception as exc:
        note = str(exc)
    if not key:
        try:
            key = (get_local_android_key() or "").strip()
        except Exception:
            key = ""
    return key, note


def activate_license(body: dict[str, Any]) -> dict[str, Any]:
    from core.license_manager import attempt_activation, is_activated
    from core.store_manager import get_active_display_name, has_registry
    from core.sync_prefs import MODE_OFFLINE, MODE_ONLINE, set_sync_mode

    username = str(body.get("username") or "").strip()
    password = str(body.get("password") or "").strip()
    device_key = str(body.get("device_key") or "").strip()
    store_name = str(body.get("store_name") or "").strip()
    mode = str(body.get("mode") or "activate").strip().lower()
    satellite = mode == "add_store"
    sync_mode = str(body.get("sync_mode") or MODE_OFFLINE).strip().lower()
    if sync_mode not in (MODE_ONLINE, MODE_OFFLINE):
        sync_mode = MODE_OFFLINE

    if not username or not password or not device_key:
        return {"ok": False, "error": "Username, password, and device key are required."}

    ok, msg = attempt_activation(username, password, device_key)
    if not ok:
        return {"ok": False, "error": msg or "Activation failed."}

    try:
        set_sync_mode(sync_mode)
    except Exception as exc:
        return {"ok": False, "error": f"Could not save sync mode: {exc}"}

    if satellite and not store_name:
        return {"ok": False, "error": "Store name is required."}
    if not satellite and not store_name and not has_registry():
        return {"ok": False, "error": "Store name is required."}

    name = store_name
    try:
        if satellite:
            _finish_store_setup(name, satellite=True)
        elif name:
            # Same name → load that store; new name → empty store (classic Tk).
            _finish_store_setup(name, satellite=False)
        else:
            try:
                name = get_active_display_name() or name
            except Exception:
                pass
    except Exception as exc:
        return {"ok": False, "error": str(exc)}

    android_key = ""
    server_note = ""
    activation_date = ""
    expiry_date = ""
    access_allowed = None
    if sync_mode == MODE_ONLINE:
        android_key, server_note = _link_online_store()
        if not name:
            try:
                name = get_active_display_name() or name
            except Exception:
                pass
        if name and not android_key:
            android_key, extra = _link_online_store()
            if extra and not server_note:
                server_note = extra
        if android_key:
            try:
                lic = _record_server_activation()
                activation_date = str(lic.get("activation_date") or "")[:10]
                expiry_date = str(lic.get("expiry_date") or "")[:10]
                access_allowed = lic.get("access_allowed")
                seal_note = str(lic.get("seal_note") or "")
                if seal_note and not server_note:
                    server_note = seal_note
                if access_allowed is False:
                    server_note = (
                        "Store is on the server but the licence is expired or disabled. "
                        "Contact the administrator to extend the expiry date. "
                        "You do not need to re-activate."
                    )
                elif not expiry_date:
                    server_note = (
                        server_note
                        or "Store paired, but the server did not return an expiry date."
                    )
            except Exception as exc:
                server_note = f"Could not save the licence on the server: {exc}"
        try:
            set_sync_mode(MODE_ONLINE)
        except Exception:
            pass

    return {
        "ok": True,
        "activated": is_activated(),
        "sync_mode": sync_mode,
        "store_name": name or "",
        "android_key": android_key or "",
        "show_key": bool(sync_mode == MODE_ONLINE and name and android_key),
        "server_note": server_note or "",
        "activation_date": activation_date,
        "expiry_date": expiry_date,
        "access_allowed": access_allowed,
        "trial_days": ONLINE_TRIAL_DAYS,
    }


def recheck_license() -> dict[str, Any]:
    """"Try again now" from the blocked screen. One forced look for a licence.

    The screen that says "Licence not found" had no controls at all, and the
    desktop's own self-healing re-check was wired to the OTHER screen -- so a
    shop whose licence arrived a minute later still had to be told to quit and
    reopen the app, and a shop that plugged the network in saw nothing change.

    ``force=True`` and a real wait, because a person has just pressed a button
    and is watching: the ordinary once-a-minute rate limit is there to stop a
    polled status endpoint hammering the server, not to ignore a shopkeeper.
    """
    from core import license_seal as seal

    note = ""
    try:
        seal.fetch_seal(timeout=15.0, force=True, wait=15.0)
    except Exception as exc:
        note = str(exc)
    status = get_license_status()
    status["rechecked"] = True
    if note and not status.get("seal_message"):
        status["seal_message"] = note
    return status


def pair_with_store_key(body: dict[str, Any]) -> dict[str, Any]:
    """"I already have a shop" — connect this PC with the shop's SC- key.

    THE PATH THAT WAS MISSING. A store the owner made by hand in the admin panel
    could be reached from the desktop in exactly one way: the long three-factor
    form, whose Online step opens with ``api.admin_login()`` -- the vendor
    administrator, from a password compiled into the build -- and then matches a
    store by DISPLAY NAME when the key does not match. So "activate against my
    existing shop" depended on a credential that must not be in the build, and
    on a name-matching rule that can hand one shop another shop's ledger.

    The SC- key is the answer to both. ``/api/auth/pair`` takes the key and can
    only ever return the one store that owns it -- no credential, no name, no
    list of anybody else's shops. It is also the thing the owner already has in
    front of him: the admin panel shows it on the store he just created.

    Deliberate about the dangerous parts:

      * PAIRED BY KEY, NEVER BY NAME. A typo in a key is a refusal, not a
        different shop.
      * TWO STEPS. The first call resolves the key and returns the shop's name
        for the person to look at; nothing local changes until ``confirm`` comes
        back with the same key. A PC is not moved on the strength of a paste.
      * REFUSED when this computer already holds a pairing key -- otherwise a
        stale installer file or a second press could re-point a shop that has a
        year of bills on it.
      * A SHOP ALREADY ON THIS PC IS LINKED, NOT MOVED. Online mode is
        server-only, so switching an existing Offline shop over here would hide
        its own books; it gets the link and the licence and stays where it is.
      * THE SIGNED LICENCE IS FETCHED HERE, on the round trip already made, so
        the PC does not walk away activated with nothing to be held to.
    """
    from core import server_api as api
    from core.store_link import get_local_android_key
    from core.store_manager import has_registry
    from core.sync_prefs import MODE_ONLINE, set_sync_mode
    from core.trial_activation import device_id, usable_store_name

    payload = body or {}
    key = str(payload.get("android_key") or payload.get("store_key_code") or "").strip()
    confirm = bool(payload.get("confirm"))
    if not key:
        return {"ok": False, "error": "Enter the shop's SC- key."}
    # ALREADY SOMEBODY'S TILL? The test is the pairing key, not the registry.
    #
    # A PC that already holds an SC- key belongs to a shop, and re-pointing it
    # is the one accident that hands one shop another's ledger -- refused here,
    # and done deliberately from Settings instead. A PC with a store folder but
    # NO key has never been connected to anything, which is exactly the shop
    # this path exists for: activated Offline, no licence, stuck. Using
    # already_set_up() here would refuse precisely that shop, since it counts a
    # local activation as being set up.
    if (get_local_android_key() or "").strip():
        return {
            "ok": False,
            "error": (
                "This computer is already connected to a shop on the server. "
                "Use Settings to change which shop it belongs to."
            ),
        }
    existing_shop = bool(has_registry())

    try:
        data = api.pair_store(
            android_key=key,
            device_id=device_id(),
            device_type="pc",
            device_name="Mac2 PC",
        ) or {}
    except Exception as exc:
        from core.trial_activation import _friendly

        return {"ok": False, "error": _friendly(exc)}

    remote = dict(data.get("store") or {})
    token = str(data.get("token") or "")
    server_name = str(remote.get("store_name") or "").strip()
    if not server_name or not token:
        return {
            "ok": False,
            "error": "That key did not match a shop on the Satpuda server.",
        }

    if not confirm:
        # Nothing has been written. The answer is a question: is this your shop?
        return {
            "ok": True,
            "confirm_required": True,
            "store_name": server_name,
            "store_id": str(remote.get("store_id") or ""),
        }

    name = usable_store_name(server_name) or server_name

    from core import server_live as live
    from core.license_manager import _get_hardware_hash, _write_activation, _write_hw_cache
    from core.store_link import _save_local
    from core.store_manager import get_active_store_key, setup_initial_store_on_activation

    # A SHOP ALREADY HERE IS NOT MOVED.
    #
    # Online mode is server-only -- the engine reads nothing from local SQLite --
    # so flipping an existing Offline shop to Online, or creating a store folder
    # named after the server's store, would leave a year of bills on disk and an
    # empty till on screen. When this PC already has a store, all that happens is
    # the LINK: the key is saved, the shop is recorded, and the licence is
    # fetched. Moving its books to the server is a separate, deliberate act
    # (Settings -> switch to Online, which pushes first).
    if not existing_shop:
        try:
            set_sync_mode(MODE_ONLINE)
        except Exception as exc:
            return {"ok": False, "error": f"Could not save the Online setting: {exc}"}
        try:
            setup_initial_store_on_activation(name)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    store_key = get_active_store_key() or "Store_Default"
    _save_local(key, store_key)
    try:
        api.save_session(store_key, {
            "token": token,
            "store_id": remote.get("store_id"),
            "store_key": remote.get("store_key") or store_key,
            "store_name": server_name,
            "android_key": remote.get("android_key") or key,
            "device_id": device_id(),
        })
    except Exception:
        pass
    # Pin this PC to the server's own store_id, so a later link never falls back
    # to matching the display name.
    try:
        live._record_store_adoption(store_key, remote)
    except Exception:
        pass

    # THE ACTIVATION RECORD BEFORE THE LICENCE, never after.
    #
    # _write_activation REWRITES activation.dat, and two marks the licence sets
    # live inside it: the rollback ratchet, and the record that this computer
    # has held a signed licence. Fetching the blob first and writing the record
    # afterwards would wipe both, and a PC that then lost license.seal would
    # look like a fresh install entitled to a starter window.
    try:
        hw = _get_hardware_hash()
        _write_activation(hw)
        _write_hw_cache(hw)
    except Exception:
        pass

    server_note = ""
    activation_date = ""
    expiry_date = ""
    access_allowed = None
    try:
        # DO NOT RESTAMP A LICENCE THAT ALREADY EXISTS.
        #
        # _record_server_activation PUTs activation_date = today, and the server
        # derives expiry from it -- so calling it on a shop that has been
        # running for months would move its trial window to today and end it in
        # three days. It is only right for a store that has never been given an
        # activation date, which is exactly what a store created by hand in the
        # admin panel looks like (activation_date NULL, expiry NULL,
        # expiry_enabled false).
        current = {}
        try:
            current = api.get_license(token) or {}
        except Exception:
            current = {}
        if str(current.get("activation_date") or "").strip():
            activation_date = str(current.get("activation_date") or "")[:10]
            expiry_date = str(current.get("expiry_date") or "")[:10]
            access_allowed = current.get("access_allowed")
            _sealed, seal_note = store_signed_license(token)
            server_note = seal_note
            try:
                from core.license_manager import _cache_server_license_locally

                _cache_server_license_locally(current)
            except Exception:
                pass
        else:
            lic = _record_server_activation()
            activation_date = str(lic.get("activation_date") or "")[:10]
            expiry_date = str(lic.get("expiry_date") or "")[:10]
            access_allowed = lic.get("access_allowed")
            server_note = str(lic.get("seal_note") or "")
    except Exception as exc:
        server_note = f"Connected, but the licence could not be read: {exc}"

    from core.sync_prefs import get_sync_mode

    return {
        "ok": True,
        "activated": True,
        "sync_mode": get_sync_mode(),
        "store_name": server_name,
        "android_key": remote.get("android_key") or key,
        # Already in his hand -- he just typed it.
        "show_key": False,
        "server_note": server_note,
        "activation_date": activation_date,
        "expiry_date": expiry_date,
        "access_allowed": access_allowed,
    }


def activate_trial(body: dict[str, Any]) -> dict[str, Any]:
    """Two answers: the shop name and the mode. The machine supplies the rest.

    The rest being: the device id, the hardware fingerprint, the app version and
    the machine's own name -- every one of which the PC can read about itself,
    and none of which a shopkeeper could have typed correctly anyway.
    """
    from core.trial_activation import activate_trial as _run

    payload = body or {}
    return _run(
        str(payload.get("store_name") or ""),
        sync_mode=str(payload.get("sync_mode") or ""),
        confirm_new=payload.get("confirm_new") is True,
    )
