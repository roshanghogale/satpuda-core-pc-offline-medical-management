"""The two pictures a shop puts on its own screens: the bill logo and the home banner.

Both were stored the same wrong way. The shop picked a file, the app copied it
into %LOCALAPPDATA%\\VeterinaryApp, and then saved the ABSOLUTE PATH of that copy
-- the logo path into pharmacy_profile.logo_path, the banner path into
layout_config.txt. A path is not a picture. It only means anything on the one
machine that has the file.

Online that breaks twice over:

  * pharmacy_profile lives on the store server, so logo_path travels between
    machines while the file does not. Every store row on the server carries a
    NULL logo_path today, and load_pharmacy_profile returns the server copy
    whole -- so the empty server value REPLACED a perfectly good local one and
    the bill printed with no logo even on the machine that had the file.
  * layout_config.txt never travels at all, so a banner picked on the counter PC
    is invisible on any other machine, and get_home_banner_path falls back to the
    built-in image without a word. The shop changes the banner and nothing
    happens.

So the picture itself has to travel with the store. It goes into the generic
per-store key/value settings channel that already exists on the server --
PUT/GET /api/sync/settings/kv, backed by the store_settings table -- as a
data: URI under the names below. NO NEW SERVER ENDPOINT AND NO SCHEMA CHANGE:
that route, that table and the client helper (server_api.push_settings_kv) all
ship today and billing_layout_prefs already keeps a JSON blob there.

Locally the bytes are cached in AppData KEYED BY STORE, because the old files
were not: two shops on one PC shared one bill_logo.png, so shop A's logo could
print on shop B's bill. AppData is also what makes Offline work and what keeps
bill printing off the network -- the server copy is only consulted when this
machine has no file of its own.

Nothing here ever deletes or rewrites a shop's picture on the server as a side
effect of reading. Pushes happen only when the shop saves a picture itself.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import threading
import time

log = logging.getLogger(__name__)

BILL_LOGO = "bill_logo"
HOME_BANNER = "home_banner"

_KV_NAMES = {
    BILL_LOGO: "bill_logo_image",
    HOME_BANNER: "home_banner_image",
}

IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")

_MIME_BY_EXT = {
    ".png": "png",
    ".jpg": "jpeg",
    ".jpeg": "jpeg",
    ".gif": "gif",
    ".webp": "webp",
    ".bmp": "bmp",
}

# Files older builds wrote with no store in the name. Adopted on first read so a
# shop that already picked a logo does not have to pick it again.
_LEGACY_BASENAMES = {
    BILL_LOGO: ("pharmacy_logo", "bill_logo"),
    HOME_BANNER: (),
}

# What we are willing to carry with the store. A logo is a letterhead, not a
# photograph; anything past this is shrunk, and if it still will not fit it
# stays on this machine and the shop is told so instead of the sync quietly
# dragging megabytes around on every settings pull.
_MAX_SHARED_BYTES = 1_100_000
_SHRINK_TO_WIDTH = {BILL_LOGO: 600, HOME_BANNER: 1600}

_SERVER_TTL = 120.0
_lock = threading.Lock()
# Keyed by (store, settings row name). The row NAME is the same string for every
# shop there is -- "home_banner_image" -- so keying by it alone meant the answer
# fetched for one store was handed to the next store to ask, for the whole TTL.
# Worse than a stale read: resolve_path ADOPTS what this returns, writing those
# bytes into the asking store's own folder, where they outlive the cache and
# become that shop's picture for good.
_server_cache: dict[tuple[str, str], tuple[float, str]] = {}

# A save that could not reach the store server leaves the OLD picture standing
# in the settings row. The kind is remembered here so the next explicit save
# replaces it instead of the shop's other machines printing last month's logo
# for ever. Only a save ever flushes this -- printing a bill must not write.
_PENDING_FILE = "pending_share.json"
_flushing = threading.local()


# ── local, per store ────────────────────────────────────────────────────────
def _appdata_dir() -> str:
    from core.license_manager import _appdata_dir as _dir

    return _dir()


def _store_key() -> str:
    try:
        from core.store_manager import get_active_store_key

        return str(get_active_store_key() or "").strip()
    except Exception:
        return ""


def _safe_key() -> str:
    """A folder name for this store that no OTHER store can also produce.

    This has to be injective, and it is the reason a delete here is safe. The
    plain sanitiser is not: "ZZ Medical" and "ZZ_Medical" both come out
    "ZZ_Medical", the two shops share one folder, and replacing one shop's logo
    would then remove the other's. Canonical keys (store_manager.display_name_key
    already yields only letters, digits, '_' and '-') pass through unchanged and
    keep the folder they have today; only a key the sanitiser had to alter gets
    the disambiguating tail, and it is derived from the untouched key so the same
    store always lands in the same place.
    """
    key = _store_key()
    if not key:
        return "default"
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in key)
    if safe != key:
        tail = hashlib.sha1(key.encode("utf-8", "replace")).hexdigest()[:8]
        safe = f"{safe}-{tail}"
    return safe or "default"


def images_dir() -> str:
    """Per-store folder for this shop's own pictures."""
    return os.path.join(_appdata_dir(), "store_images", _safe_key())


def _cache_key(name: str) -> tuple[str, str]:
    """Which store a cached server answer belongs to. Read at every use.

    _store_key() is re-read rather than remembered, so a store switch takes
    effect on the very next call: the new store simply does not find the old
    store's entry.
    """
    return (_store_key(), str(name or ""))


def _cached_paths(kind: str) -> list[str]:
    folder = images_dir()
    return [os.path.join(folder, f"{kind}{ext}") for ext in IMAGE_EXTS]


def local_path(kind: str) -> str:
    """This store's cached copy on this machine, or ''."""
    for p in _cached_paths(kind):
        if os.path.isfile(p):
            return p
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


def _legacy_path(kind: str) -> str:
    """A picture an older build left in AppData with no store in its name.

    Same rule pharmacy_profile_io._sidecar_path already applies to the profile
    JSON: an unkeyed file was written before pictures were per store, so it can
    only have belonged to a single-store install. With two shops on this PC
    there is no way to tell whose it is, and guessing puts one pharmacy's
    letterhead on another pharmacy's bill.
    """
    names = _LEGACY_BASENAMES.get(kind, ())
    if not names or not _single_store_install():
        return ""
    base = _appdata_dir()
    for name in names:
        for ext in IMAGE_EXTS:
            p = os.path.join(base, name + ext)
            if os.path.isfile(p):
                return p
    return ""


def _write_cache(kind: str, ext: str, raw: bytes) -> str:
    ext = (ext or ".png").lower()
    if ext not in IMAGE_EXTS:
        ext = ".png"
    folder = images_dir()
    os.makedirs(folder, exist_ok=True)
    dest = os.path.join(folder, f"{kind}{ext}")
    tmp = dest + ".tmp"
    with open(tmp, "wb") as fh:
        fh.write(raw)
    os.replace(tmp, dest)
    # One picture per kind. A leftover PNG must not win after the shop switches
    # to a JPG.
    for other in IMAGE_EXTS:
        if other == ext:
            continue
        stale = os.path.join(folder, f"{kind}{other}")
        try:
            if os.path.isfile(stale):
                os.remove(stale)
        except OSError:
            pass
    return dest


def _read_file(path: str) -> tuple[str, bytes]:
    ext = os.path.splitext(path)[1].lower()
    with open(path, "rb") as fh:
        return ext, fh.read()


# ── removing the one it replaces ────────────────────────────────────────────
def _same_file(a: str, b: str) -> bool:
    if not a or not b:
        return False
    try:
        if os.path.samefile(a, b):
            return True
    except OSError:
        pass
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def _is_under(path: str, folder: str) -> bool:
    try:
        p = os.path.normcase(os.path.abspath(path))
        f = os.path.normcase(os.path.abspath(folder))
    except Exception:
        return False
    return p.startswith(f + os.sep)


def _is_legacy_copy(kind: str, path: str) -> bool:
    """AppData\\pharmacy_logo.png and friends, written before pictures had a store.

    Ours to remove only where _legacy_path is willing to adopt it -- a single
    store on this PC. With two shops here nobody knows whose that file is, and
    deleting another pharmacy's letterhead is worse than leaving a stale one.
    """
    names = _LEGACY_BASENAMES.get(kind, ())
    if not names or not _single_store_install():
        return False
    stem, ext = os.path.splitext(os.path.basename(path))
    if ext.lower() not in IMAGE_EXTS or stem not in names:
        return False
    try:
        here = os.path.normcase(os.path.dirname(os.path.abspath(path)))
        base = os.path.normcase(os.path.abspath(_appdata_dir()))
    except Exception:
        return False
    return here == base


def owned_copy(kind: str, path: str) -> bool:
    """True only when `path` is a copy THIS app made for THIS store.

    The whole safety of replacing rests here. The shop's ORIGINAL picture -- the
    one it picked out of Pictures, off a pen drive, out of a WhatsApp folder --
    is not ours to delete, ever; we only made a copy of it. And another shop's
    copy is not ours either: images_dir() is keyed by store and _safe_key() is
    injective, so a file under this store's folder cannot also be under
    another's.
    """
    p = str(path or "").strip()
    if not p or not os.path.isfile(p):
        return False
    if _is_under(p, images_dir()):
        return True
    return _is_legacy_copy(kind, p)


def _as_paths(previous) -> list[str]:
    """One path or several. The logo is recorded in two places at once."""
    if not previous:
        return []
    if isinstance(previous, (str, bytes, os.PathLike)):
        previous = [previous]
    out = []
    for item in previous:
        text = str(item or "").strip()
        if text:
            out.append(text)
    return out


def _previous_candidates(kind: str, previous) -> list[str]:
    out: list[str] = []
    out.extend(_as_paths(previous))
    # Our own cache under a different extension: the shop switched from a PNG
    # to a JPG and the PNG would otherwise sit there and win on the next read.
    out.extend(_cached_paths(kind))
    base = _appdata_dir()
    for name in _LEGACY_BASENAMES.get(kind, ()):
        for ext in IMAGE_EXTS:
            out.append(os.path.join(base, name + ext))
    return out


def discard_previous(kind: str, previous="", keep: str = "") -> list[str]:
    """Remove the copy this store used to point at, now that the new one is here.

    The order is the point and it is never the other way round: the new picture
    is written, everything is pointed at it, and only then does the old copy go.
    If the new one is not on disk this returns without touching anything, so a
    failed save can never leave the shop with no logo at all.
    """
    keep_path = str(keep or "").strip() or local_path(kind)
    if not keep_path or not os.path.isfile(keep_path):
        return []
    removed: list[str] = []
    seen: set[str] = set()
    for cand in _previous_candidates(kind, previous):
        try:
            marker = os.path.normcase(os.path.abspath(cand))
        except Exception:
            continue
        if marker in seen:
            continue
        seen.add(marker)
        if _same_file(cand, keep_path) or not owned_copy(kind, cand):
            continue
        try:
            os.remove(cand)
            removed.append(cand)
        except OSError as exc:
            log.debug("store image discard (%s): %s", kind, exc)
    return removed


# ── data: URIs ──────────────────────────────────────────────────────────────
def to_data_uri(ext: str, raw: bytes) -> str:
    if not raw:
        return ""
    mime = _MIME_BY_EXT.get((ext or "").lower(), "png")
    return f"data:image/{mime};base64,{base64.b64encode(raw).decode('ascii')}"


def from_data_uri(uri: str) -> tuple[str, bytes]:
    """('.png', b'...') from a data: URI, or ('', b'')."""
    text = str(uri or "").strip()
    if not text.startswith("data:image/"):
        return "", b""
    try:
        header, payload = text.split(",", 1)
        if ";base64" not in header:
            return "", b""
        mime = header[len("data:") :].split(";", 1)[0]
        subtype = mime.split("/", 1)[1].lower()
        ext = {v: k for k, v in _MIME_BY_EXT.items()}.get(subtype, ".png")
        if subtype == "jpeg":
            ext = ".jpg"
        return ext, base64.b64decode(payload, validate=False)
    except Exception:
        return "", b""


def _shrink(kind: str, ext: str, raw: bytes) -> tuple[str, bytes]:
    """The copy that travels, at letterhead size.

    The local file stays exactly as the shop picked it -- this only trims what
    goes into the store's settings rows, which every device pulls. A 4000px
    phone photo of a signboard is a banner nobody needs 6 MB of.
    """
    target = _SHRINK_TO_WIDTH.get(kind, 1200)
    try:
        import io

        from PIL import Image

        img = Image.open(io.BytesIO(raw))
        if img.width <= target and len(raw) <= _MAX_SHARED_BYTES:
            return ext, raw
        if img.width > target:
            height = max(1, int(img.height * target / max(img.width, 1)))
            img = img.resize((target, height), Image.LANCZOS)
        buf = io.BytesIO()
        img.convert("RGBA").save(buf, format="PNG", optimize=True)
        out = buf.getvalue()
        if len(out) < len(raw):
            return ".png", out
    except Exception as exc:
        log.debug("store image shrink (%s): %s", kind, exc)
    return ext, raw


# ── the store server ────────────────────────────────────────────────────────
def _is_online() -> bool:
    try:
        from core.sync_prefs import is_online_mode

        return bool(is_online_mode())
    except Exception:
        return False


def _pending_path() -> str:
    return os.path.join(images_dir(), _PENDING_FILE)


def _load_pending() -> dict:
    try:
        with open(_pending_path(), encoding="utf-8-sig") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_pending(data: dict) -> None:
    path = _pending_path()
    try:
        if not data:
            if os.path.isfile(path):
                os.remove(path)
            return
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        os.replace(tmp, path)
    except OSError as exc:
        log.debug("store image pending write: %s", exc)


def _mark_pending(kind: str) -> None:
    data = _load_pending()
    data[kind] = True
    _write_pending(data)


def _clear_pending(kind: str) -> None:
    data = _load_pending()
    if data.pop(kind, None) is not None:
        _write_pending(data)


def flush_pending(skip: str = "") -> list[str]:
    """Re-send a picture whose save could not reach the store, and only then.

    A shop that changed its logo while the internet was down has a NEW picture
    here and the OLD one still sitting in the store's settings row -- the other
    counters keep printing the one it replaced. This puts that right at the next
    save. It is never called from a read: a bill print must not write to a live
    store, and there is a test that says so.
    """
    if getattr(_flushing, "busy", False) or not _is_online():
        return []
    pending = _load_pending()
    if not pending:
        return []
    _flushing.busy = True
    done: list[str] = []
    try:
        for kind in list(pending):
            if skip and kind == skip:
                # The caller has just tried this one. Trying again in the same
                # breath is a second failed round trip, not a second chance.
                continue
            path = local_path(kind)
            if not path:
                _clear_pending(kind)
                continue
            try:
                ext, raw = _read_file(path)
            except OSError:
                continue
            shared, _note = push_to_server(kind, ext, raw)
            if shared:
                done.append(kind)
    finally:
        _flushing.busy = False
    return done


def push_to_server(kind: str, ext: str, raw: bytes) -> tuple[bool, str]:
    """Carry this picture with the store. Returns (pushed, message).

    A successful push REPLACES the previous value in that one settings row, so
    the picture the shop just replaced stops existing on the server the moment
    the new one lands. Every path that does not land records the kind, because
    "we did not send it" and "the old one is still up there" are the same thing.
    """
    if kind not in _KV_NAMES or not raw:
        return False, ""
    if not _is_online():
        _mark_pending(kind)
        return False, ""
    ext, raw = _shrink(kind, ext, raw)
    if len(raw) > _MAX_SHARED_BYTES:
        # It will never fit, so there is nothing to retry -- but the picture it
        # REPLACED must not go on standing in that row, or the shop's other
        # counters keep printing the logo this one just threw away. Better no
        # picture there than the wrong one.
        _clear_pending(kind)
        cleared = clear_on_server(kind)
        note = (
            "That picture is too big to share with your other computers. "
            "It is saved on this one. Use a smaller image to have it "
            "everywhere."
        )
        if cleared:
            # Say it, because it is a real change on their screens: they were
            # showing the picture this shop has just replaced.
            note += (
                " Your other computers now show no picture instead of the old "
                "one."
            )
        return False, note
    uri = to_data_uri(ext, raw)
    try:
        from core.online_guard import ensure_can_mutate
        from core import server_live as live

        ensure_can_mutate()
        live.push_settings_kv(_KV_NAMES[kind], uri)
    except Exception as exc:
        log.warning("store image push (%s): %s", kind, exc)
        _mark_pending(kind)
        return False, (
            "Saved on this computer. It could not be sent to the server yet, "
            "so your other computers will not show it until the next save."
        )
    _clear_pending(kind)
    # Worked out before the lock is taken: _cache_key reads the store registry
    # off disk, and _lock is not re-entrant.
    entry = _cache_key(_KV_NAMES[kind])
    with _lock:
        _server_cache[entry] = (time.time(), uri)
    return True, ""


def clear_on_server(kind: str) -> bool:
    """Take the store's picture out of the settings row it lives in.

    The replacement path does not need this -- a push overwrites that one row,
    which is the removal. This is for the two cases where nothing overwrites it:
    the shop CLEARS its picture, and a new picture too big to share would
    otherwise leave the old one up.
    """
    name = _KV_NAMES.get(kind)
    if not name or not _is_online():
        return False
    try:
        from core.online_guard import ensure_can_mutate
        from core import server_live as live

        ensure_can_mutate()
        live.push_settings_kv(name, "")
    except Exception as exc:
        log.warning("store image clear (%s): %s", kind, exc)
        return False
    entry = _cache_key(name)
    with _lock:
        _server_cache[entry] = (time.time(), "")
    return True


def _kv_rows(raw) -> list[dict]:
    """Both shapes the settings pull has ever answered with."""
    rows: list[dict] = []
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, list):
        return rows
    for item in raw:
        if not isinstance(item, dict):
            continue
        inner = item.get("settings")
        if isinstance(inner, list):
            rows.extend(r for r in inner if isinstance(r, dict))
        elif "name" in item:
            rows.append(item)
    return rows


def fetch_from_server(kind: str) -> str:
    """The store's picture as a data: URI, or ''. Never raises."""
    name = _KV_NAMES.get(kind)
    if not name or not _is_online():
        return ""
    now = time.time()
    key = _cache_key(name)
    with _lock:
        hit = _server_cache.get(key)
        if hit and (now - hit[0]) < _SERVER_TTL:
            return hit[1]
    try:
        from core import server_api as api
        from core.server_live import _token as live_token

        res = api._request(
            "GET", "/api/sync/settings/kv", token=live_token(), timeout=30
        )
        if not isinstance(res, dict) or not res.get("ok"):
            return ""
        found = ""
        for row in _kv_rows(res.get("data")):
            if str(row.get("name") or "") != name:
                continue
            value = row.get("value")
            if isinstance(value, (dict, list)):
                value = json.dumps(value)
            found = str(value or "")
            break
    except Exception as exc:
        log.debug("store image fetch (%s): %s", kind, exc)
        return ""
    with _lock:
        _server_cache[key] = (time.time(), found)
    return found


def _adopt(kind: str, ext: str, raw: bytes) -> str:
    try:
        return _write_cache(kind, ext, raw)
    except Exception as exc:
        log.warning("store image cache (%s): %s", kind, exc)
        return ""


# ── what everything else calls ──────────────────────────────────────────────
def resolve_path(kind: str, hint_path: str = "") -> str:
    """A real file on THIS machine for this store's picture, or ''.

    The saved path is only a hint. A shop that has the file keeps using it
    exactly as before -- that is the first rung, so Offline behaviour and a
    single-PC shop are untouched. Everything below it is for the machine that
    did not pick the picture.
    """
    hint = str(hint_path or "").strip()
    if hint and os.path.isfile(hint):
        return hint

    cached = local_path(kind)
    if cached:
        return cached

    legacy = _legacy_path(kind)
    if legacy:
        try:
            ext, raw = _read_file(legacy)
            adopted = _adopt(kind, ext, raw)
            if adopted:
                return adopted
        except Exception as exc:
            log.debug("store image legacy (%s): %s", kind, exc)
        return legacy

    # A path recorded on another machine still names the file. If a picture with
    # that name is sitting in this machine's AppData, it is this shop's -- but
    # only where there is one shop here. AppData's root is shared by every store
    # on the PC and the file this matches carries no store in its name, so on a
    # two-shop machine a name match is a coin toss with one pharmacy's letterhead
    # on the other's bills. Same rule as _legacy_path, which this branch sits
    # directly under and used to sidestep.
    if hint and _single_store_install():
        base = os.path.basename(hint)
        if base:
            candidate = os.path.join(_appdata_dir(), base)
            if os.path.isfile(candidate):
                return candidate

    ext, raw = from_data_uri(fetch_from_server(kind))
    if raw:
        return _adopt(kind, ext, raw)
    return ""


def resolve_data_uri(kind: str, hint_path: str = "") -> str:
    """This store's picture ready to drop into HTML, or ''. Never raises."""
    try:
        path = resolve_path(kind, hint_path)
        if path and os.path.isfile(path):
            ext, raw = _read_file(path)
            return to_data_uri(ext, raw)
    except Exception as exc:
        log.debug("store image data uri (%s): %s", kind, exc)
    try:
        return fetch_from_server(kind)
    except Exception:
        return ""


def save_image(
    kind: str,
    ext: str,
    raw: bytes,
    *,
    share: bool = True,
    previous="",
) -> dict:
    """Keep a picture for this store: AppData copy + carried with the store.

    Replacing is one ordered move, never a delete followed by a hopeful write:

      1. write the new picture into this store's folder (os.replace, so a
         reader never sees half a file),
      2. put it in the store's settings row, which REPLACES the picture it
         succeeds -- that is the old server value gone,
      3. and only now remove the copy this app made of the old one.

    `previous` is the path the shop used to point at. Passing it is how the file
    that is not in our folder any more -- an older build's AppData copy, a
    config-folder banner -- gets cleaned up too. Anything that is not ours is
    left exactly where it is.

    share=False is the "we are only tidying up" path -- copying a file the shop
    picked long ago into the per-store folder must not push anything to a live
    store on its own. Only an explicit save does that.
    """
    path = _write_cache(kind, ext, raw)
    shared, note = (False, "")
    if share:
        shared, note = push_to_server(kind, ext, raw)
        # A save is the one moment we are allowed to talk to the store, so it is
        # also the moment to finish a save the internet interrupted earlier.
        try:
            flush_pending(skip="" if shared else kind)
        except Exception as exc:  # pragma: no cover - never break a save
            log.debug("store image flush (%s): %s", kind, exc)
    removed = discard_previous(kind, previous, keep=path)
    return {"path": path, "shared": shared, "note": note, "removed": removed}


def remember_file(kind: str, path: str, *, share: bool = True, previous="") -> dict:
    """Same as save_image for a file already on disk. '' path is a no-op."""
    src = str(path or "").strip()
    if not src or not os.path.isfile(src):
        return {"path": "", "shared": False, "note": "", "removed": []}
    try:
        ext, raw = _read_file(src)
    except OSError as exc:
        log.warning("store image read (%s): %s", kind, exc)
        return {"path": "", "shared": False, "note": "", "removed": []}
    # Re-saving the file we already hold is not a replacement of anything.
    keepers = [p for p in _as_paths(previous) if not _same_file(p, src)]
    return save_image(kind, ext, raw, share=share, previous=keepers)


def forget(kind: str, *, clear_server: bool = True) -> None:
    """Drop this store's picture (used when the shop clears it).

    The cached file used to go and the settings row was left alone, so the next
    read pulled the very picture the shop had just cleared back down and cached
    it again. Clearing has to reach the row too.
    """
    for p in _cached_paths(kind):
        try:
            if os.path.isfile(p):
                os.remove(p)
        except OSError:
            pass
    # The unkeyed file an older build left in AppData counts as this store's
    # picture wherever _legacy_path is willing to adopt it -- leave it and the
    # very next read adopts the logo the shop just removed straight back.
    base = _appdata_dir()
    for name in _LEGACY_BASENAMES.get(kind, ()):
        for ext in IMAGE_EXTS:
            cand = os.path.join(base, name + ext)
            if not owned_copy(kind, cand):
                continue
            try:
                os.remove(cand)
            except OSError:
                pass
    _clear_pending(kind)
    if clear_server:
        clear_on_server(kind)
    entry = _cache_key(_KV_NAMES.get(kind, ""))
    with _lock:
        _server_cache.pop(entry, None)


def _reset_server_cache() -> None:
    with _lock:
        _server_cache.clear()
