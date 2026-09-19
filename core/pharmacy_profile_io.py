"""Pharmacy profile load/save — Online = Satpuda Core Server, Offline = local SQLite.

A JSON copy also lives in %LOCALAPPDATA%\\VeterinaryApp so replacing the
Win10 folder build does not wipe the store name / GST / DL / logo.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import sqlite3
import time
from typing import Any, Optional

log = logging.getLogger(__name__)

_PROFILE_KEYS = (
    "name",
    "address",
    "phone",
    "email",
    "gstin",
    "dl_number",
    "gst_enabled",
    "logo_path",
    "fssai_number",
    "show_fssai_on_bill",
)

_SIDECAR_NAME = "pharmacy_profile.json"
_LOGO_NAME = "pharmacy_logo"


def _empty() -> dict[str, Any]:
    return {
        "name": "",
        "address": "",
        "phone": "",
        "email": "",
        "gstin": "",
        "dl_number": "",
        "gst_enabled": True,
        "logo_path": "",
        "fssai_number": "",
        # Absent means PRINT IT. The bill templates now honour this flag, and
        # the online profile comes from the store server, which has never
        # carried the key -- defaulting it off there would have removed the
        # licence number from every online shop's bills on the day this shipped.
        # An explicit False from a shop that actually unticked the box is kept,
        # because _normalize only overwrites when the key is present.
        "show_fssai_on_bill": True,
    }


def _is_online() -> bool:
    try:
        from core.sync_prefs import is_online_mode

        return bool(is_online_mode())
    except Exception:
        return False


def _appdata_dir() -> str:
    from core.license_manager import _appdata_dir as _dir

    return _dir()


def _active_store_key() -> str:
    try:
        from core.store_manager import get_active_store_key

        return str(get_active_store_key() or "").strip()
    except Exception:
        return ""


def _single_store_install() -> bool:
    """True when this PC has at most one store folder."""
    try:
        from core.store_manager import get_stores_root

        root = get_stores_root()
        if not os.path.isdir(root):
            return True
        return len([d for d in os.listdir(root)
                    if os.path.isdir(os.path.join(root, d))]) <= 1
    except Exception:
        return False


def _sidecar_path() -> str:
    """Per-store sidecar, falling back to the single-store file older builds wrote."""
    base = _appdata_dir()
    key = _active_store_key()
    if key:
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in key)
        per_store = os.path.join(base, f"pharmacy_profile_{safe}.json")
        if os.path.isfile(per_store):
            return per_store
        # First run on this build: adopt the legacy file only if it is not
        # already claimed by a different store, so shop A's name can never end
        # up printed on shop B's bills.
        legacy = os.path.join(base, _SIDECAR_NAME)
        if os.path.isfile(legacy):
            try:
                with open(legacy, encoding="utf-8-sig") as fh:
                    owner = str((json.load(fh) or {}).get("store_key") or "").strip()
            except Exception:
                owner = ""
            if owner == key:
                return legacy
            # An unstamped legacy file was written before stores had keys, so it
            # can only have belonged to a single-store install. With more than
            # one store on this PC there is no way to tell whose it is, and
            # guessing prints one shop's header on another shop's bills.
            if not owner and _single_store_install():
                return legacy
        return per_store
    return os.path.join(base, _SIDECAR_NAME)


_PROFILE_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS pharmacy_profile (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT, address TEXT, phone TEXT, email TEXT,
    gstin TEXT, dl_number TEXT,
    gst_enabled INTEGER DEFAULT 1, logo_path TEXT,
    fssai_number TEXT, show_fssai_on_bill INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
"""

_PROFILE_PRINT_SELECT = """
SELECT id, name, address, phone, email, gstin, dl_number,
       gst_enabled, created_at, COALESCE(logo_path, '') as logo_path,
       COALESCE(fssai_number, '') as fssai_number,
       COALESCE(show_fssai_on_bill, 0) as show_fssai_on_bill
FROM pharmacy_profile LIMIT 1
"""


def _usable(profile: Optional[dict[str, Any]]) -> bool:
    if not isinstance(profile, dict):
        return False
    return bool(str(profile.get("name") or "").strip())


def _normalize(raw: Optional[dict[str, Any]]) -> dict[str, Any]:
    out = _empty()
    if not isinstance(raw, dict):
        return out
    for k in _PROFILE_KEYS:
        if k not in raw or raw.get(k) is None:
            continue
        if k in ("gst_enabled", "show_fssai_on_bill"):
            out[k] = bool(raw.get(k))
        else:
            out[k] = raw.get(k) or ""
    return out


def _fssai_choice_path() -> str:
    """Marker: this store has since made its own choice about printing FSSAI."""
    key = _active_store_key()
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in key) or "default"
    return os.path.join(_appdata_dir(), f"fssai_choice_{safe}.flag")


def _fssai_choice_recorded() -> bool:
    try:
        return os.path.isfile(_fssai_choice_path())
    except Exception:
        return False


def _record_fssai_choice() -> None:
    try:
        os.makedirs(_appdata_dir(), exist_ok=True)
        with open(_fssai_choice_path(), "w", encoding="utf-8") as fh:
            fh.write("1")
    except Exception:
        pass


def _apply_fssai_legacy_default(profile: dict[str, Any]) -> dict[str, Any]:
    """Keep printing FSSAI for shops that were already printing it.

    db_setup._migrate_fssai_print_flag does this once against the local
    pharmacy_profile table. Online there is no local table to migrate -- the
    profile comes from the store server -- so a shop whose server copy carries
    show_fssai_on_bill FALSE (pushed from a build where the checkbox was saved
    but never read) quietly stopped printing its FSSAI number, while the very
    same shop Offline kept printing it. Same rule, same one-shot: once the shop
    saves the profile itself, its choice is recorded and honoured verbatim.

    This only ever reads. It does not push a corrected profile to the server --
    flipping a licence number back on is not worth a silent write to a live
    store from whatever machine happens to load the profile.
    """
    if not isinstance(profile, dict):
        return profile
    if profile.get("show_fssai_on_bill"):
        return profile
    if not str(profile.get("fssai_number") or "").strip():
        return profile
    if _fssai_choice_recorded():
        return profile
    out = dict(profile)
    out["show_fssai_on_bill"] = True
    return out


def _safe_fsync(fh) -> None:
    """fsync is unreliable on mapped/UNC drives (Errno 9). Flush is enough."""
    try:
        fh.flush()
    except Exception:
        return
    try:
        fd = fh.fileno()
        if fd is None or int(fd) < 0:
            return
        os.fsync(fd)
    except OSError:
        pass


def _write_sidecar(profile: dict[str, Any]) -> None:
    if not _usable(profile):
        return
    try:
        os.makedirs(_appdata_dir(), exist_ok=True)
        payload = _normalize(profile)
        key = _active_store_key()
        if key:
            payload["store_key"] = key
        tmp = _sidecar_path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)
            _safe_fsync(fh)
        os.replace(tmp, _sidecar_path())
    except Exception as exc:
        log.warning("pharmacy profile sidecar write: %s", exc)


def _read_sidecar() -> Optional[dict[str, Any]]:
    path = _sidecar_path()
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        owner = str((data or {}).get("store_key") or "").strip()
        key = _active_store_key()
        if owner and key and owner != key:
            return None
        out = _normalize(data)
        return out if _usable(out) else None
    except Exception as exc:
        log.warning("pharmacy profile sidecar read: %s", exc)
        return None


def _keep_logo_in_appdata(logo_path: str, *, share: bool = False) -> str:
    """Keep the logo where a folder replace cannot take it, keyed by store.

    It used to land on one shared AppData\\pharmacy_logo.png for the whole PC,
    so two shops on one machine overwrote each other's letterhead. It is now a
    per-store file under core.store_images, which is also what carries the
    picture to the shop's other machines when `share` is set (an explicit save).
    """
    src = (logo_path or "").strip()
    if not src or not os.path.isfile(src):
        return src
    try:
        from core.store_images import BILL_LOGO, remember_file

        kept = str((remember_file(BILL_LOGO, src, share=share) or {}).get("path") or "")
        if kept:
            return kept
    except Exception as exc:
        log.warning("pharmacy logo keep: %s", exc)
    try:
        appdata = os.path.abspath(_appdata_dir())
        src_abs = os.path.abspath(src)
        if src_abs.lower().startswith(appdata.lower() + os.sep):
            return src_abs
        ext = os.path.splitext(src_abs)[1] or ".png"
        dest = os.path.join(appdata, _LOGO_NAME + ext.lower())
        if os.path.normcase(src_abs) != os.path.normcase(dest):
            shutil.copy2(src_abs, dest)
        return dest
    except Exception as exc:
        log.warning("pharmacy logo copy: %s", exc)
        return src


def recorded_logo_paths(conn=None) -> list[str]:
    """Every place THIS machine has the current logo path written down.

    Two of them, and they disagree often enough to matter: the pharmacy_profile
    row in this store's database, and the AppData sidecar that survives a folder
    replace. When the shop picks a new logo, whichever of these still names a
    real file is the one that would go on printing -- resolve_path takes an
    existing path before anything else -- so both are what "the previous one"
    means.
    """
    out: list[str] = []
    try:
        side = _read_sidecar() or {}
        text = str(side.get("logo_path") or "").strip()
        if text:
            out.append(text)
    except Exception as exc:
        log.debug("recorded logo (sidecar): %s", exc)
    if conn is not None:
        try:
            row = conn.execute(
                "SELECT COALESCE(logo_path,'') FROM pharmacy_profile "
                "ORDER BY id LIMIT 1"
            ).fetchone()
            if row and str(row[0] or "").strip():
                out.append(str(row[0]).strip())
        except Exception as exc:
            log.debug("recorded logo (row): %s", exc)
    return out


def _cached_logo() -> str:
    """This store's cached picture, whether or not anything records a path.

    An upload leaves the bytes in the per-store cache and _repair_logo_path
    hands them back on the next read. So "no path written down" is not "no
    picture" -- without this, Remove Logo on a shop whose only copy is the cache
    removed nothing at all.
    """
    try:
        from core.store_images import BILL_LOGO, local_path

        return local_path(BILL_LOGO)
    except Exception:
        return ""


def discard_replaced_logo(previous, keep: str) -> list[str]:
    """Remove the copy this app made of the logo that has just been replaced.

    Only ever called once the new picture is written AND everything points at
    it. core.store_images decides what is ours to delete: its own per-store
    folder and the unkeyed file older builds left in AppData. The picture the
    shop picked out of its own folders is never touched -- we only made a copy
    of it.
    """
    if not keep:
        return []
    try:
        from core.store_images import BILL_LOGO, discard_previous

        return discard_previous(BILL_LOGO, previous, keep=keep)
    except Exception as exc:
        log.warning("logo discard: %s", exc)
        return []


def _repair_logo_path(profile: dict[str, Any]) -> dict[str, Any]:
    """Point logo_path at a file that exists on THIS machine, or leave it alone.

    logo_path is the only field in the profile that is a machine path rather
    than shop data, and it is the one field the server cannot be right about:
    every store row on the server carries a NULL logo_path today, so the online
    profile arrived with logo_path '' and REPLACED the good local value -- the
    bill printed with no logo even on the PC holding the picture. When the
    server does carry a path it is some other machine's C:\\Users\\..., and the
    file is not here either.

    A path that resolves to a real file on this machine is kept verbatim, so a
    shop with its own local logo prints exactly what it printed before.
    """
    if not isinstance(profile, dict):
        return profile
    current = str(profile.get("logo_path") or "").strip()
    if current and os.path.isfile(current):
        return profile
    try:
        from core.store_images import BILL_LOGO, resolve_path

        found = resolve_path(BILL_LOGO, current)
    except Exception as exc:
        log.debug("logo path repair: %s", exc)
        return profile
    if not found or found == current:
        return profile
    out = dict(profile)
    out["logo_path"] = found
    return out


def _row_to_profile(row, cols: Optional[list[str]] = None) -> dict[str, Any]:
    out = _empty()
    if not row:
        return out
    if cols:
        data = dict(zip(cols, row))
        return _normalize(data)
    # name, address, phone, email, gstin, dl_number, gst_enabled, logo, fssai, show_fssai
    return {
        "name": row[0] or "",
        "address": row[1] or "",
        "phone": row[2] or "",
        "email": row[3] or "",
        "gstin": row[4] or "",
        "dl_number": row[5] or "",
        "gst_enabled": bool(row[6]),
        "logo_path": row[7] or "",
        "fssai_number": row[8] or "",
        "show_fssai_on_bill": bool(row[9]),
    }


def _load_from_conn(conn) -> Optional[dict[str, Any]]:
    if conn is None:
        return None
    try:
        row = conn.execute(
            "SELECT name, address, phone, email, gstin, dl_number, "
            "COALESCE(gst_enabled,1), COALESCE(logo_path,''), "
            "COALESCE(fssai_number,''), COALESCE(show_fssai_on_bill,0) "
            "FROM pharmacy_profile LIMIT 1"
        ).fetchone()
        out = _row_to_profile(row)
        return out if _usable(out) else None
    except Exception:
        return None


def _load_from_db_file(path: str) -> Optional[dict[str, Any]]:
    db_path = (path or "").strip()
    if not db_path or not os.path.isfile(db_path):
        return None
    conn = None
    try:
        from core.online_migrate import allow_local_store_db

        with allow_local_store_db():
            conn = sqlite3.connect(
                f"file:{os.path.abspath(db_path)}?mode=ro",
                uri=True,
                timeout=8.0,
            )
            return _load_from_conn(conn)
    except Exception as exc:
        log.debug("pharmacy profile from %s: %s", db_path, exc)
        return None
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def _local_db_candidates() -> list[str]:
    paths: list[str] = []
    try:
        from core.store_manager import get_active_db_path, get_legacy_db_path, get_stores_root

        for p in (get_active_db_path(), get_legacy_db_path()):
            if p and p not in paths:
                paths.append(p)
        # Deliberately NOT every store folder. This feeds the shop header that
        # is printed on bills: reading a neighbouring store's database here put
        # one pharmacy's name, address, GST and DL number on another's bills.
        # Only the active store and the pre-multi-store legacy file qualify.
    except Exception:
        pass
    return [p for p in paths if p]


def _load_local_fallback() -> Optional[dict[str, Any]]:
    sidecar = _read_sidecar()
    if sidecar:
        return sidecar
    for path in _local_db_candidates():
        found = _load_from_db_file(path)
        if found:
            _write_sidecar(found)
            return found
    return None


def salvage_profile_from_db_path(db_path: str) -> Optional[dict[str, Any]]:
    """Keep profile when Online wipes an 'empty' local DB (profile is not business data)."""
    found = _load_from_db_file(db_path)
    if not found:
        return None
    found["logo_path"] = _keep_logo_in_appdata(str(found.get("logo_path") or ""))
    _write_sidecar(found)
    if _is_online():
        try:
            from core.server_crud import push_pharmacy_profile

            push_pharmacy_profile(found)
        except Exception as exc:
            log.warning("salvage pharmacy profile push: %s", exc)
    return found


def remember_local_profile(profile: dict[str, Any]) -> None:
    """Write AppData copy so folder-replace / Online :memory: still has the profile."""
    payload = _normalize(profile)
    payload["logo_path"] = _keep_logo_in_appdata(str(payload.get("logo_path") or ""))
    _write_sidecar(payload)


# (when, which store, the profile). The store key is not decoration: this holds
# the shop's whole header -- name, address, GST, DL and logo_path -- and it used
# to be (when, profile) with nothing saying whose. Switch stores inside the
# minute and the next bill printed the shop you had just left, letterhead and
# all. Kept for a minute because the online profile is read on every bill.
_empty_profile_cache: tuple[float, str, dict] | None = None
_PROFILE_TTL = 60.0


def forget_cached_profile() -> None:
    """Drop the remembered profile (the active store is changing)."""
    global _empty_profile_cache
    _empty_profile_cache = None


def fetch_profile_from_server() -> Optional[dict[str, Any]]:
    """Return store pharmacy_profile from server, or None if missing/unreachable."""
    global _empty_profile_cache
    try:
        from core import server_api as api
        from core.server_live import _token as live_token

        # Pair/retry so a folder-replace still gets a store JWT.
        token = live_token()
        res = api._request(
            "GET",
            "/api/sync/settings/pharmacy_profile",
            token=token,
            timeout=30,
        )
        if not isinstance(res, dict) or not res.get("ok"):
            log.warning("fetch pharmacy_profile: %s", (res or {}).get("error") if isinstance(res, dict) else res)
            return None
        data = res.get("data")
        if isinstance(data, list) and data:
            data = data[0]
        if not isinstance(data, dict):
            return None
        out = _normalize(data)
        if _usable(out):
            _empty_profile_cache = (time.time(), _active_store_key(), out)
        return out
    except Exception as exc:
        log.warning("fetch pharmacy_profile: %s", exc)
        return None


def load_pharmacy_profile(conn=None) -> dict[str, Any]:
    """Online: server (required). Offline: local SQLite."""
    return _repair_logo_path(_apply_fssai_legacy_default(_load_pharmacy_profile(conn)))


def _load_pharmacy_profile(conn=None) -> dict[str, Any]:
    global _empty_profile_cache
    if _is_online():
        now = time.time()
        cached = _empty_profile_cache
        if (
            cached
            and (now - cached[0]) < _PROFILE_TTL
            # Whose header this is. Without it a store switch kept printing the
            # shop we just left for the rest of the minute.
            and cached[1] == _active_store_key()
            and _usable(cached[2])
        ):
            return dict(cached[2])
        remote = fetch_profile_from_server()
        if _usable(remote):
            return remote
        # Either the server was unreachable (None) or it answered with a store
        # that has no profile on it yet -- which is what a freshly paired folder
        # sees. Neither is a reason to forget the shop's own header: fall back to
        # the AppData copy, then to any local store file, and only then to blank.
        # Returning the empty server answer here is what put the default name,
        # address and GST number back on the bills after a folder replace.
        sidecar = _read_sidecar()
        if _usable(sidecar):
            return dict(sidecar)
        local = _load_local_fallback()
        if _usable(local):
            return dict(local)
        return dict(remote) if isinstance(remote, dict) else _empty()
    found = _load_from_conn(conn)
    if found:
        return found
    sidecar = _read_sidecar()
    return dict(sidecar) if sidecar else _empty()


def has_pharmacy_profile(conn=None) -> bool:
    """True when a usable profile name exists (Online checks server then local copy)."""
    p = load_pharmacy_profile(conn)
    return _usable(p)


def _fallback_store_name() -> str:
    try:
        from core.store_manager import get_active_display_name

        return str(get_active_display_name() or "").strip()
    except Exception:
        return ""


def create_pharmacy_profile_table(conn) -> None:
    """Create pharmacy_profile (and missing columns) on this connection."""
    if conn is None:
        raise RuntimeError("No database connection for pharmacy_profile")
    conn.execute(_PROFILE_CREATE_SQL)
    for col, spec in (
        ("gst_enabled", "INTEGER DEFAULT 1"),
        ("logo_path", "TEXT"),
        ("fssai_number", "TEXT"),
        ("show_fssai_on_bill", "INTEGER DEFAULT 0"),
    ):
        try:
            conn.execute(f"ALTER TABLE pharmacy_profile ADD COLUMN {col} {spec}")
        except sqlite3.OperationalError:
            pass
    try:
        conn.commit()
    except Exception:
        pass


def _write_sqlite_row(conn, payload: dict[str, Any], *, commit: bool = True) -> None:
    create_pharmacy_profile_table(conn)
    vals = (
        payload["name"],
        payload["address"],
        payload["phone"],
        payload["email"],
        payload["gstin"],
        payload["dl_number"],
        1 if payload["gst_enabled"] else 0,
        payload["logo_path"],
        payload["fssai_number"],
        1 if payload["show_fssai_on_bill"] else 0,
    )
    exists = conn.execute("SELECT id FROM pharmacy_profile LIMIT 1").fetchone()
    if exists:
        conn.execute(
            "UPDATE pharmacy_profile SET name=?, address=?, phone=?, email=?, "
            "gstin=?, dl_number=?, gst_enabled=?, logo_path=?, fssai_number=?, "
            "show_fssai_on_bill=? WHERE id=?",
            vals + (exists[0],),
        )
    else:
        conn.execute(
            "INSERT INTO pharmacy_profile "
            "(name, address, phone, email, gstin, dl_number, gst_enabled, "
            "logo_path, fssai_number, show_fssai_on_bill) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            vals,
        )
    if commit:
        try:
            conn.commit()
        except (OSError, sqlite3.Error):
            pass


def ensure_pharmacy_profile_table(conn) -> dict[str, Any]:
    """Make sure pharmacy_profile exists and has the store header used on bills."""
    if conn is None:
        return load_pharmacy_profile(None)
    create_pharmacy_profile_table(conn)
    profile = load_pharmacy_profile(conn)
    if not _usable(profile):
        name = _fallback_store_name()
        if name:
            profile = _empty()
            profile["name"] = name
    if _usable(profile):
        try:
            _write_sqlite_row(conn, _normalize(profile))
        except Exception as exc:
            log.warning("pharmacy_profile seed: %s", exc)
    return profile


def fetch_pharmacy_profile_row(conn):
    """Return the pharmacy_profile print row after ensuring the table exists."""
    ensure_pharmacy_profile_table(conn)
    return conn.execute(_PROFILE_PRINT_SELECT).fetchone()


def save_pharmacy_profile(conn, profile: dict[str, Any]) -> None:
    """Online: push to server. Always keep local pharmacy_profile for bill print."""
    global _empty_profile_cache
    p = profile or {}
    payload = _normalize(p)
    if not str(payload.get("name") or "").strip():
        raise RuntimeError("Pharmacy name is required")
    # What the shop pointed at BEFORE this save. Read first, deleted last: a
    # picked-a-new-logo save replaces the old copy, and the only safe order is
    # new file written, everything repointed, old copy removed.
    previous_logo = recorded_logo_paths(conn)
    # An explicit save is the one moment the shop is telling us "this picture is
    # mine" -- so this is where the bytes are carried to the store, not just the
    # path. Without it the other machines have nothing to show.
    logo = _keep_logo_in_appdata(str(payload.get("logo_path") or ""), share=True)
    payload["logo_path"] = logo
    # Remove Logo, saved. The KEY has to be there: a caller that simply does not
    # mention logo_path is not clearing anything, and an empty logo_path from the
    # SERVER is exactly the value that used to wipe a good local picture. Only a
    # save that carries the field and carries it empty means the shop said no.
    cleared = (
        "logo_path" in p
        and not str(p.get("logo_path") or "").strip()
        and not logo
        and bool(previous_logo or _cached_logo())
    )
    # From here on the FSSAI checkbox is the shop's; the legacy default above
    # must never second-guess it again.
    _record_fssai_choice()
    _write_sidecar(payload)
    if conn is not None:
        _write_sqlite_row(conn, payload)
    if cleared:
        # Cache AND the store's settings row. Dropping only the file left the
        # picture on the server, and the next read pulled the very logo the shop
        # had just removed back down and cached it again.
        try:
            from core.store_images import BILL_LOGO, forget

            forget(BILL_LOGO)
        except Exception as exc:
            log.warning("logo clear: %s", exc)
    else:
        # Both records now name the new picture, so the old copy can go.
        discard_replaced_logo(previous_logo, logo)
    if _is_online():
        from core.online_guard import ensure_can_mutate
        from core.server_crud import push_pharmacy_profile

        ensure_can_mutate()
        push_pharmacy_profile(payload)
        _empty_profile_cache = (time.time(), _active_store_key(), dict(payload))
        try:
            from core.sync_status import note_collection_change, note_last_sync

            note_collection_change("pharmacy_profile")
            note_last_sync("push")
        except Exception:
            pass
        return

    if conn is None:
        raise RuntimeError("No database connection for pharmacy profile")
    try:
        conn.commit()
    except (OSError, sqlite3.Error):
        pass
    try:
        from core.sync_coordinator import after_profile_saved

        after_profile_saved(conn)
    except Exception:
        pass
