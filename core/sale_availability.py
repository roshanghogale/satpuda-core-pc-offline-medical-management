"""Which medicine batches a sale bill may use on its own date.

A bill dated in the past (a missed paper bill entered today) may only use batches the
shop already had on that day, and only ones that had not expired by then. Expiry has
always been checked against the bill date. "Already had" was checked offline only, and
never Online: the engine's SQLite is empty there and the catalogue rows carry no
created_at, so every batch passed. Nothing re-checked the lines at save either, so a line
picked before the Bill Date was changed went through.

A batch existed on day D when the earliest date known for it -- its first purchase or the
day its row was created, whichever is earlier -- is on or before D. A batch with no known
date passes (old data). Online both halves come from the store:

  * created_at from the medicine documents (/api/sync/medicines);
  * a purchase dated on or before D but ENTERED after D (a back-dated purchase) is found
    among the purchases updated after D (/api/sync/purchases?since=D), items included.
    A purchase entered on or before D created its batch on or before D, so nothing older
    is needed.

Both pulls happen only for a date before today. The check runs while the counter waits,
so it must never hold billing up: one question per date goes to the store at a time and
every other caller waits for that answer; the store gets a few seconds; the answer is kept
for a few minutes, and a failure for a minute. When the store cannot be asked, nothing is
hidden: a counter that cannot bill at all is worse than a batch that came in a few days
after the bill. The answer is forgotten only when a purchase changes or a medicine is
added -- a stock movement, which every sale on every device makes, cannot change when a
batch came in.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import date
from typing import Any, Iterable, Optional

log = logging.getLogger(__name__)

_TTL_SECONDS = 300.0
# A store that could not answer is not asked again for this long. Unremembered, every
# line added to a back-dated bill waited for the same failure again.
_FAILED_TTL_SECONDS = 60.0
# One page may take this long, and the whole question this long (it was 120 s a page).
_PULL_TIMEOUT_SECONDS = 12.0
_CHECK_BUDGET_SECONDS = 15.0
_PAGE = 5000
_MAX_PAGES = 200
_lock = threading.Lock()
# date -> (when it was answered, how long that holds, ids missing that day, answered ok)
_missing_cache: dict[str, tuple[float, float, frozenset, bool]] = {}
# date -> set once the question under way for that date has its answer
_asking: dict[str, threading.Event] = {}
# every medicine id the kept answers were worked out from
_known_medicines: set[int] = set()
# moved on by invalidate(), so an answer worked out before a change is not kept after it
_generation = 0


def invalidate() -> None:
    """Forget the Online answers (a purchase changed or a medicine was added)."""
    global _generation
    with _lock:
        _missing_cache.clear()
        _known_medicines.clear()
        _generation += 1


def medicines_changed(ids: Iterable[Any]) -> None:
    """Forget the answers when a medicine they were not worked out from appears.

    "medicines" changes on every stock movement too -- each sale on each device -- and
    forgetting on those threw the answer away so often that nearly every line pulled the
    store again. Only a medicine the answers have never seen (a new batch) matters.
    """
    with _lock:
        if not any(hit[3] for hit in _missing_cache.values()):
            return
        fresh = False
        for raw in ids or ():
            try:
                mid = int(raw or 0)
            except (TypeError, ValueError):
                continue
            if mid > 0 and mid not in _known_medicines:
                fresh = True
                break
    if fresh:
        invalidate()


def _as_date(raw: Any) -> date:
    from core.batch_visibility import parse_bill_as_of

    return parse_bill_as_of(raw)


def _is_online() -> bool:
    try:
        from core.sync_prefs import is_online_mode

        return bool(is_online_mode())
    except Exception:
        return False


def _pull_all(
    collection: str, *, since: Optional[str] = None, deadline: Optional[float] = None
) -> list[dict]:
    """Every live document of a store collection, keyset-paged on (updated_at, id).

    Raises rather than hand back part of the store: when a page does not move the keyset,
    or the store is still sending at ``deadline`` (a time.monotonic() value).
    """
    from core import server_api as api
    from core.online_catalog import _token

    token = _token()
    out: list[dict] = []
    seen: set[str] = set()
    cursor_since, after_id = since, None
    for _ in range(_MAX_PAGES):
        timeout = _PULL_TIMEOUT_SECONDS
        if deadline is not None:
            left = deadline - time.monotonic()
            if left <= 0:
                raise TimeoutError(f"the store did not send {collection} in time")
            timeout = min(timeout, left)
        # The deadline reaches the retry inside the request too: a timed-out page used to be
        # tried again with the whole page timeout, well past this check's budget.
        with api.request_deadline(deadline):
            docs, _meta = api.pull_collection(
                token,
                collection,
                since=cursor_since,
                after_id=after_id,
                include_deleted=False,
                limit=_PAGE,
                timeout=timeout,
            )
        if not docs:
            return out
        fresh = 0
        last_ts, last_id = cursor_since, after_id
        for d in docs:
            if not isinstance(d, dict):
                continue
            raw = d.get("id") if d.get("id") is not None else d.get("local_id")
            key = str(raw) if raw is not None else ""
            if key and key in seen:
                continue
            if key:
                seen.add(key)
            fresh += 1
            if not d.get("deleted"):
                out.append(d)
            ts = d.get("updated_at") or d.get("synced_at")
            if ts:
                last_ts = str(ts)
            try:
                last_id = int(raw)
            except (TypeError, ValueError):
                pass
        if len(docs) < _PAGE:
            return out
        # A full page that did not move the keyset. The server pages on updated_at to the
        # microsecond but sends it to the millisecond, so 5000 rows written by one bulk
        # statement come back as the same page again. Stopping here returned part of the
        # store as if it were all of it, and a batch past that page was never checked.
        if fresh == 0 or (
            (last_id is None or last_id == after_id)
            and (not last_ts or last_ts == cursor_since)
        ):
            raise RuntimeError(f"{collection}: paging stopped moving after {len(out)} rows")
        if last_id is None or last_id == after_id:
            cursor_since, after_id = last_ts, None
        else:
            cursor_since, after_id = last_ts, last_id
    raise RuntimeError(f"{collection}: more than {_MAX_PAGES} pages")


def _compute_missing_online(
    day: date, *, deadline: Optional[float] = None
) -> tuple[frozenset, set[int]]:
    """(ids missing on ``day``, every medicine id the store sent)."""
    from core.batch_visibility import _parse_created_date

    bought_by_then: set[int] = set()
    for p in _pull_all("purchases", since=day.isoformat(), deadline=deadline):
        if p.get("is_autosave"):
            continue
        purchased = _parse_created_date(p.get("purchase_date"))
        if purchased is None or purchased > day:
            continue
        for it in p.get("items") or []:
            try:
                mid = int((it or {}).get("medicine_id") or 0)
            except (TypeError, ValueError):
                continue
            if mid > 0:
                bought_by_then.add(mid)

    missing: set[int] = set()
    known: set[int] = set()
    for m in _pull_all("medicines", deadline=deadline):
        try:
            mid = int(m.get("id") or m.get("local_id") or 0)
        except (TypeError, ValueError):
            continue
        if mid <= 0:
            continue
        known.add(mid)
        created = _parse_created_date(m.get("created_at"))
        if created is None or created <= day:
            continue
        if mid not in bought_by_then:
            missing.add(mid)
    return frozenset(missing), known


def _answer(day: date, asking: threading.Event, generation: int) -> frozenset:
    """Put the question for ``day`` to the store once, keep the answer, release the waiters."""
    key = day.isoformat()
    missing: frozenset = frozenset()
    known: set[int] = set()
    ok = False
    try:
        missing, known = _compute_missing_online(
            day, deadline=time.monotonic() + _CHECK_BUDGET_SECONDS
        )
        ok = True
    except Exception as exc:
        log.warning("sale availability for %s could not be checked: %s", key, exc)
    finally:
        with _lock:
            if generation == _generation:
                ttl = _TTL_SECONDS if ok else _FAILED_TTL_SECONDS
                _missing_cache[key] = (time.monotonic(), ttl, missing, ok)
                _known_medicines.update(known)
            if _asking.get(key) is asking:
                del _asking[key]
        asking.set()
    return missing


def batches_missing_on_online(as_of: Any, *, wait: bool = True) -> frozenset:
    """Ids of the store's batches that had not come in yet on ``as_of``.

    Empty for today or a later date, and whenever the store could not be asked. With
    ``wait=False`` nothing waits on the store: with no answer yet this returns an empty set
    at once and the answer is worked out behind it (for a Tk thread).
    """
    day = _as_date(as_of)
    if day >= date.today():
        return frozenset()
    key = day.isoformat()
    while True:
        with _lock:
            hit = _missing_cache.get(key)
            if hit and time.monotonic() - hit[0] < hit[1]:
                return hit[2]
            asking = _asking.get(key)
            if asking is None:
                asking = _asking[key] = threading.Event()
                generation = _generation
                break
        if not wait:
            return frozenset()
        if not asking.wait(_CHECK_BUDGET_SECONDS + 5.0):
            return frozenset()  # longer than any question may take: hide nothing
    if not wait:
        threading.Thread(
            target=_answer,
            args=(day, asking, generation),
            daemon=True,
            name="SaleAvailability",
        ).start()
        return frozenset()
    return _answer(day, asking, generation)


def answer_ready(as_of: Any) -> bool:
    """True when checking ``as_of`` needs no store call right now.

    Today, a later date, Offline, or an Online answer still kept. A caller that must not wait
    (a Tk thread) holds its write until this is True rather than write on a guess.
    """
    day = _as_date(as_of)
    if day >= date.today() or not _is_online():
        return True
    with _lock:
        hit = _missing_cache.get(day.isoformat())
        return bool(hit and time.monotonic() - hit[0] < hit[1])


def batch_existed_on(
    conn,
    medicine_id: Any,
    as_of: Any,
    *,
    created_at: Any = None,
    first_purchase: Any = None,
    wait: bool = True,
) -> bool:
    """True when this batch was already in the shop on the bill date (see module doc).

    ``wait=False`` (Online only) never waits for the store; see batches_missing_on_online.
    """
    try:
        mid = int(medicine_id or 0)
    except (TypeError, ValueError):
        return True
    if mid <= 0:
        return True  # a quick-add line: the save itself creates its row
    day = _as_date(as_of)
    if day >= date.today():
        return True
    if _is_online():
        return mid not in batches_missing_on_online(day, wait=wait)

    from core.batch_visibility import fetch_first_purchase_date, medicine_existed_as_of

    if created_at is None:
        try:
            row = conn.execute(
                "SELECT created_at FROM medicines WHERE id=?", (mid,)
            ).fetchone()
            created_at = row[0] if row else None
        except Exception:
            created_at = None
    if first_purchase is None:
        first_purchase = fetch_first_purchase_date(conn, mid)
    return medicine_existed_as_of(created_at, day, first_purchase_raw=first_purchase)


def lines_unavailable_on(
    conn, medicines: Iterable[dict], bill_date: Any, *, wait: bool = True
) -> list[str]:
    """Why these bill lines cannot be sold on ``bill_date``: expired by then, or not in yet.

    Checked at save because the picker only checks a line when it is added -- the Bill
    Date can be changed afterwards. ``wait=False`` never waits on the store (see
    batches_missing_on_online); pair it with answer_ready.
    """
    from core.batch_visibility import is_expired_as_of

    day = _as_date(bill_date)
    problems: list[str] = []
    seen: set[int] = set()
    for m in medicines or []:
        if not isinstance(m, dict) or m.get("quick_add"):
            continue
        try:
            mid = int(m.get("id") or m.get("medicine_id") or 0)
        except (TypeError, ValueError):
            continue
        if mid <= 0 or mid in seen:
            continue
        seen.add(mid)
        name = str(m.get("name") or m.get("medicine_name") or f"Medicine {mid}").strip()
        batch = str(m.get("batch") or m.get("batch_no") or "").strip()
        label = f"{name} (batch {batch})" if batch else name
        expiry = str(m.get("expiry") or m.get("expiry_date") or "").strip()
        try:
            if expiry and is_expired_as_of(expiry, day):
                problems.append(f"{label} expired on {expiry}, before the bill date {day.isoformat()}.")
                continue
        except Exception:
            pass
        if not batch_existed_on(conn, mid, day, wait=wait):
            problems.append(
                f"{label} was added after {day.isoformat()}. It cannot be sold on this bill date."
            )
    return problems
