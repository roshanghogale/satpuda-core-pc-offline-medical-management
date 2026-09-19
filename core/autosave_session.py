"""Device-local record of an in-progress autosave bill.

Autosave now writes a REAL bill instead of an ASV draft, so every tick has to
know two things that a bill row cannot tell it:

  * which bill this form is already writing into, and
  * exactly which lines and payments THIS form put there.

The second one is what stops the counter-sale day bill from growing by the
whole form on every tick: the tick rewrites the bill as
``(what is in it now) - (what I put there last time) + (what is on screen)``.

The record is device-local on purpose -- the in-progress form is device-local
too -- and it is a file, not a table, so it survives the app restarting and
works identically in Online mode, where the engine's sqlite connection is
``:memory:`` and holds nothing.

``counter_pointer`` is the day's counter bill id as this device knows it. It
is what makes a second tab (or a queued Online create that the server has not
seen yet) land in the SAME bill instead of starting a second one.

Each record also carries ``form``: the whole form as the last tick saw it --
lines, customer, doctor, discounts, payment. That is what a reopened tab is
handed back, and it is why a restart RECLAIMS the bill it already made instead
of typing a second one next to it. It has to live here rather than be read back
off the bill, because a counter bill holds the whole day's sales and only this
record knows which slice of it belongs to this form.
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from datetime import date, datetime, timedelta

from core.autosave_prefs import _config_dir

_LOCK = threading.RLock()
_MAX_SESSIONS = 60
_KEEP_DAYS = 7


def _path() -> str:
    # Overridable so the test suite never writes a live shop's config folder.
    override = os.environ.get("SATPUDA_AUTOSAVE_SESSION_FILE")
    if override:
        return override
    return os.path.join(_config_dir(), "autosave_sessions.json")


def _store_key() -> str:
    try:
        from core.store_manager import get_active_db_path

        return str(get_active_db_path() or "")
    except Exception:
        return ""


def _today_str() -> str:
    return date.today().strftime("%Y-%m-%d")


def normalize_date(value) -> str:
    """Local calendar date as YYYY-MM-DD. Empty / unparsable -> today."""
    if value is None or value == "":
        return _today_str()
    if hasattr(value, "strftime"):
        return value.strftime("%Y-%m-%d")
    text = str(value).strip().replace("T", " ").split(" ")[0]
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(text, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return _today_str()


def _read() -> dict:
    try:
        with open(_path(), encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            data.setdefault("sessions", {})
            data.setdefault("counter", {})
            return data
    except Exception:
        pass
    return {"sessions": {}, "counter": {}}


def _write(data: dict) -> None:
    """Atomic replace -- a half-written file would lose a live bill pointer."""
    path = _path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.{os.getpid()}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp, path)
    except Exception:
        # json.dump writes as it goes, so a refused value leaves a half file
        # behind. The real file is untouched (os.replace never ran); clear the
        # scrap so a retry is not fighting it.
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def _num(value, default: float = 0.0) -> float:
    """Never raise. A bad number must not cost us the record of a real bill."""
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return default if out != out else out  # NaN


def _plain(value):
    """Reduce any value to something json can write.

    The form snapshot stores line dicts exactly as the caller handed them in,
    which means arbitrary objects from whichever UI is on top -- a ``date`` in
    ``expiry``, a ``Decimal`` rate, a widget variable. One of those used to make
    ``json.dump`` raise AFTER the bill had already been created and the stock
    already moved, so nothing was left pointing at that bill: it vanished from
    recovery, it read as a completed sale in History, and the next autosave tick
    made ANOTHER real bill. Every tick. Nothing in a snapshot is worth that.
    """
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if value == value and value not in (float("inf"), float("-inf")) else 0.0
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except Exception:
            return str(value)
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    try:
        return float(value)  # Decimal and friends
    except (TypeError, ValueError):
        pass
    try:
        return str(value)
    except Exception:
        return ""


def is_open(rec) -> bool:
    """True while this record still owns a real bill nobody has finished.

    ``closed`` is the F7/F8 tombstone -- the bill was saved and the record only
    exists to stop a late tick opening a second one. Anything else with a bill
    id is an in-progress form: real money, real stock, and nothing on screen.
    """
    if not isinstance(rec, dict):
        return False
    if rec.get("closed"):
        return False
    if rec.get("discarded"):
        # Its refused bill row was discarded: there is no bill to hand back, and offering it
        # on every start blamed the connection for a bill that will never exist.
        return False
    if rec.get("held") and (rec.get("form") or {}).get("medicines"):
        # A form held back from writing (its Bill Date refused a line): no bill yet, but
        # typed work that a restart must be able to hand back.
        return True
    try:
        return int(rec.get("sale_id") or 0) != 0
    except (TypeError, ValueError):
        return False


def _prune(data: dict) -> dict:
    """Age out finished sessions. NEVER age out an open one.

    An open record is the only thing that can hand a real, unfinished bill back
    to a form, or take its lines out again. Dropping it after seven days (which
    is what this did to every record alike) leaves that bill on the customer's
    account with nothing left anywhere that knows it was never completed -- the
    silent orphan. Tombstones are safe to forget: their bill is saved and done.
    """
    cutoff = time.time() - _KEEP_DAYS * 86400
    old_day = (date.today() - timedelta(days=_KEEP_DAYS)).strftime("%Y-%m-%d")
    rows = {
        k: v for k, v in (data.get("sessions") or {}).items() if isinstance(v, dict)
    }
    live = {k: v for k, v in rows.items() if is_open(v)}
    # The record of a bill whose refused row was discarded is kept whatever the count: its
    # form may still be open, and without the record that form's next tick saves the sale
    # again. Every tick of that form refreshes it, so it ages out only once no form has
    # touched it for _KEEP_DAYS.
    discarded = {
        k: v
        for k, v in rows.items()
        if not is_open(v)
        and v.get("discarded")
        and float(v.get("updated_at") or 0) >= cutoff
    }
    done = {
        k: v
        for k, v in rows.items()
        if not is_open(v)
        and not v.get("discarded")
        and float(v.get("updated_at") or 0) >= cutoff
    }
    if len(done) > _MAX_SESSIONS:
        keep = sorted(
            done.items(), key=lambda kv: float(kv[1].get("updated_at") or 0)
        )[-_MAX_SESSIONS:]
        done = dict(keep)
    sessions = {**done, **discarded, **live}
    counter = {
        k: v
        for k, v in (data.get("counter") or {}).items()
        if str(k).split("|")[-1] >= old_day
    }
    data["sessions"] = sessions
    data["counter"] = counter
    return data


def new_token() -> str:
    return uuid.uuid4().hex


def load_session(token) -> dict | None:
    tok = str(token or "").strip()
    if not tok:
        return None
    with _LOCK:
        rec = (_read().get("sessions") or {}).get(tok)
    if not isinstance(rec, dict):
        return None
    if rec.get("store") and rec.get("store") != _store_key():
        # Another shop's in-progress bill. Never touch it.
        return None
    return rec


def find_session_for_sale(sale_id) -> dict | None:
    """Recover the LIVE session for a bill id when the caller lost its token.

    Finished sessions are deliberately not returned. A counter day bill is
    shared, so its tombstones belong to other forms -- handing one back here
    would let this form subtract somebody else's lines out of the day's bill.
    A tombstone is only ever reachable through the token of the form that
    made it.
    """
    try:
        sid = int(sale_id or 0)
    except (TypeError, ValueError):
        return None
    if sid == 0:
        return None
    store = _store_key()
    with _LOCK:
        rows = list((_read().get("sessions") or {}).values())
    best = None
    for rec in rows:
        if not isinstance(rec, dict):
            continue
        if rec.get("store") and rec.get("store") != store:
            continue
        if int(rec.get("sale_id") or 0) != sid:
            continue
        if rec.get("counter"):
            # The day's counter bill. Every open record on it is ANOTHER tab's
            # share of the day -- handing one to a caller that asked by bill id
            # lets it subtract that tab's paid lines out of the bill, put the
            # stock back and drop its record: a lost sale.
            return None
        if rec.get("closed"):
            continue
        if best is None or float(rec.get("updated_at") or 0) > float(
            best.get("updated_at") or 0
        ):
            best = rec
    return best


def own_session(token, sale_id=0) -> dict | None:
    """The session THIS form owns. Every caller resolves through here.

    A form that holds a token owns exactly that token's record. When that
    record is gone (its session was released and dropped by a switch whose
    next write then failed) the form owns NOTHING -- it is never handed a
    record by bill id, because the only live records left on that bill are
    other tabs'. Lookup by bill id is only for a caller with no token at all
    (a legacy handle), and ``find_session_for_sale`` never answers it for a
    counter bill.
    """
    if str(token or "").strip():
        return load_session(token)
    return find_session_for_sale(sale_id)


def list_open_sessions() -> list[dict]:
    """Every in-progress bill this device owns, newest first.

    The engine is the source of truth for "what was this shop in the middle
    of": the record is on disk, so it survives the app being killed, and it is
    the same answer for the React tabs and the Tk page. A reopened form asks
    this instead of guessing from something it kept in memory.
    """
    store = _store_key()
    with _LOCK:
        rows = list((_read().get("sessions") or {}).values())
    live = [
        rec
        for rec in rows
        if is_open(rec) and (not rec.get("store") or rec.get("store") == store)
    ]
    live.sort(key=lambda r: float(r.get("updated_at") or 0), reverse=True)
    return live


def open_sale_ids() -> set[int]:
    """Bill ids that an unfinished form still owns -- history marks these."""
    out: set[int] = set()
    try:
        for rec in list_open_sessions():
            sid = int(rec.get("sale_id") or 0)
            if sid:
                out.add(sid)
    except Exception as exc:
        print(f"[autosave] open session scan failed: {exc}")
    return out


def snapshot_form(**fields) -> dict:
    """The whole form, as a reopened tab needs it back.

    Line dicts are stored exactly as the caller passed them -- each UI hands in
    its own shape and gets its own shape back, so neither has to rebuild a line
    from a bill row (and for a counter sale, the bill row is the whole day).
    """
    meds = []
    for med in fields.get("medicines") or []:
        if isinstance(med, dict):
            meds.append({str(k): _plain(v) for k, v in med.items()})
    return {
        "customer_name": str(fields.get("customer_name") or ""),
        "customer_phone": str(fields.get("customer_phone") or ""),
        "customer_address": str(fields.get("customer_address") or ""),
        "doctor_name": str(fields.get("doctor_name") or ""),
        "doctor_phone": str(fields.get("doctor_phone") or ""),
        "bill_date": normalize_date(fields.get("bill_date")),
        "discount_pct": _num(fields.get("discount_pct")),
        "discount_rs": _num(fields.get("discount_rs")),
        "rounding": _num(fields.get("rounding")),
        "cash_paid": _num(fields.get("cash_paid")),
        "online_paid": _num(fields.get("online_paid")),
        "previous_due": _num(fields.get("previous_due")),
        "medicines": meds,
    }


def save_session(token, **fields) -> dict:
    tok = str(token or "").strip() or new_token()
    with _LOCK:
        data = _prune(_read())
        rec = dict((data.get("sessions") or {}).get(tok) or {})
        rec.update(fields)
        rec["token"] = tok
        rec["store"] = _store_key()
        rec["updated_at"] = time.time()
        data.setdefault("sessions", {})[tok] = rec
        try:
            _write(data)
        except (TypeError, ValueError):
            # Last line of defence. `form` is the only free-form part of this
            # record and it is a convenience; `sale_id` is a real bill with real
            # stock and a real charge behind it. Losing the pointer means the
            # next tick opens a second bill, so drop the snapshot and keep the
            # pointer rather than the other way round.
            rec.pop("form", None)
            data["sessions"][tok] = rec
            _write(data)
    return rec


def drop_session(token) -> None:
    tok = str(token or "").strip()
    if not tok:
        return
    with _LOCK:
        data = _prune(_read())
        if tok in (data.get("sessions") or {}):
            data["sessions"].pop(tok, None)
            _write(data)


def _as_int(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def repoint_sale(from_id, to_id, bill_no: str = "") -> int:
    """Move this store's records (and day pointers) from one bill id to another.

    A bill row that waited in the queue is known here by the queue's temp id. Once it lands
    only the server's id reaches the bill, so the records follow it there. Returns how many
    records moved.
    """
    src, dst = _as_int(from_id), _as_int(to_id)
    if not src or not dst or src == dst:
        return 0
    store = _store_key()
    moved = 0
    with _LOCK:
        data = _prune(_read())
        for rec in (data.get("sessions") or {}).values():
            if not isinstance(rec, dict) or _as_int(rec.get("sale_id")) != src:
                continue
            if rec.get("store") and rec.get("store") != store:
                continue
            rec["sale_id"] = dst
            if bill_no:
                rec["bill_no"] = bill_no
            rec["updated_at"] = time.time()
            moved += 1
        counter = data.get("counter") or {}
        for key in list(counter):
            if str(key).startswith(f"{store}|") and _as_int(counter[key]) == src:
                counter[key] = dst
                moved += 1
        if moved:
            _write(data)
    return moved


def mark_sale_discarded(sale_id) -> int:
    """Mark every record of a queued bill that will never exist (its row was discarded).

    The form that owned the record may still be open with its token: a Tauri tab stays dirty
    after a refused tick, so its timer keeps ticking. Forgetting the record left that token
    owning nothing, and its next tick or F7 saved the sale as a new bill -- the customer's
    balance and the stock a second time. The record stays instead, marked discarded: it is
    never offered on a restart, writes nothing, and goes when its form is cleared. The day
    pointer to that bill is dropped; there is no bill for another counter form to join.

    Only a queue temp id (negative) is accepted: a real bill id must never be marked this
    way. A temp id comes from a random uuid, so it names that one bill in any store.
    """
    sid = _as_int(sale_id)
    if sid >= 0:
        return 0
    with _LOCK:
        data = _prune(_read())
        sessions = data.get("sessions") or {}
        marked = 0
        for rec in sessions.values():
            if isinstance(rec, dict) and _as_int(rec.get("sale_id")) == sid:
                rec["discarded"] = True
                rec["updated_at"] = time.time()
                marked += 1
        counter = data.get("counter") or {}
        pointers = [key for key, val in counter.items() if _as_int(val) == sid]
        for key in pointers:
            counter.pop(key, None)
        if marked or pointers:
            _write(data)
    return marked


def discarded_record_for(sale_id) -> dict | None:
    """The discarded record of a queued bill id, for a form that no longer holds its token."""
    sid = _as_int(sale_id)
    if sid >= 0:
        return None
    store = _store_key()
    with _LOCK:
        rows = list((_read().get("sessions") or {}).values())
    for rec in rows:
        if not isinstance(rec, dict) or not rec.get("discarded"):
            continue
        if rec.get("store") and rec.get("store") != store:
            continue
        if _as_int(rec.get("sale_id")) == sid:
            return rec
    return None


def _counter_key(bill_date) -> str:
    return f"{_store_key()}|{normalize_date(bill_date)}"


def get_counter_pointer(bill_date) -> int:
    """This device's id for that day's counter bill (0 when unknown)."""
    with _LOCK:
        raw = (_read().get("counter") or {}).get(_counter_key(bill_date))
    try:
        return int(raw or 0)
    except (TypeError, ValueError):
        return 0


def set_counter_pointer(bill_date, sale_id) -> None:
    try:
        sid = int(sale_id or 0)
    except (TypeError, ValueError):
        return
    with _LOCK:
        data = _prune(_read())
        key = _counter_key(bill_date)
        if sid:
            data.setdefault("counter", {})[key] = sid
        else:
            data.setdefault("counter", {}).pop(key, None)
        _write(data)


def clear_counter_pointer(bill_date, sale_id=None) -> None:
    """Forget the day pointer -- only when it still points at `sale_id`."""
    if sale_id is not None and get_counter_pointer(bill_date) != int(sale_id or 0):
        return
    set_counter_pointer(bill_date, 0)


def snapshot_lines(medicines) -> list[dict]:
    """The part of a form line that a later tick has to be able to subtract."""
    out = []
    for med in medicines or []:
        if not isinstance(med, dict):
            continue
        try:
            mid = int(med.get("id") or 0)
        except (TypeError, ValueError):
            mid = 0
        if mid <= 0:
            continue
        out.append(
            {
                "id": mid,
                "qty": _num(med.get("qty")),
                "rate": _num(med.get("rate")),
                "medicine_discount": _num(med.get("medicine_discount")),
            }
        )
    return out
