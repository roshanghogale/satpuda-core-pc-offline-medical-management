"""Server-only Online CRUD — no store SQLite.

Builds sync docs in memory and POST/DELETE via /api/sync.
"""
from __future__ import annotations

import logging
import uuid
from datetime import date, datetime
from typing import Any, Optional

log = logging.getLogger(__name__)


def _token() -> str:
    from core import server_api as api

    return api.store_token_for_active()


def _device_id() -> str:
    try:
        from core.store_manager import get_active_store_key

        return (get_active_store_key() or "desktop").strip() or "desktop"
    except Exception:
        return "desktop"


def _now() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _line_field(line: dict, master: dict, key: str) -> str:
    """Descriptive line field: what the cart says, else the medicines master.

    Same precedence the purchase push uses. Returns "" rather than None so a
    blank never reads as "the caller had no opinion" further down.
    """
    for src in (line, master):
        try:
            val = str((src or {}).get(key) or "").strip()
        except Exception:
            val = ""
        if val:
            return val
    return ""


def _line_gst_percent(line: dict, master: dict) -> Optional[float]:
    """GST % of a sale line: the line's own rate -- 0 included -- else the medicines master.

    Not _line_field, which reads 0 as blank: an exempt line would take the
    medicine's rate. None when neither has one; the server stores that as NULL.
    """
    for src in (line, master):
        val = (src or {}).get("gst_percent")
        if val in (None, ""):
            continue
        try:
            return float(val)
        except (TypeError, ValueError):
            continue
    return None


class BundleDocumentRejected(RuntimeError):
    """The server refused one document while the rest of the bundle committed.

    Distinct from a transport failure: after this, part of the write IS on the
    server, so the operation must not simply be re-queued as if nothing had
    happened.

    ``failures`` carries the server's answer for each refused document
    (collection, id, error, constraint), so a caller can tell a bill number that
    another device took first from any other refusal.
    """

    def __init__(self, message: str, failures: Optional[list[dict]] = None):
        super().__init__(message)
        self.failures = list(failures or [])
        # The server's whole answer for the bundle, when there was one (push_bundle): what the
        # documents that were not refused became.
        self.answer: Any = None

    def only_number_taken(self, collection: str) -> bool:
        """True when every refused document is a ``collection`` row whose number was taken."""
        if not self.failures:
            return False
        for f in self.failures:
            if f.get("collection") != collection:
                return False
            text = f"{f.get('constraint') or ''} {f.get('error') or ''}".lower()
            if "bill_no" not in text and "purchase_no" not in text:
                return False
            if "duplicate" not in text and "unique" not in text:
                return False
        return True

    def only_rows_of(self, collection: str) -> bool:
        """True when every refused document is a ``collection`` row -- all the rest committed."""
        return bool(self.failures) and all(
            f.get("collection") == collection for f in self.failures
        )


class PurchaseEditNotSaved(BundleDocumentRejected):
    """An Online purchase edit the store did not take, while the rest of its bundle committed.

    The store skips a purchase that is not past the version it holds, but applies the stock
    movements that rode in the same bundle. Such an edit is either put over the store's copy
    (purchase_service.land_skipped_purchase_edit) or ends here: another device changed or
    deleted the bill first, or the store kept moving on under it. The message says which, and
    what became of the stock this edit moved.

    A BundleDocumentRejected, so a queued edit is parked for the shop (Settings -> Sync) and
    not replayed: a replay works the edit out again from the store's copy and moves its stock
    a second time.

    ``saved`` is True when the edit itself IS on the store and only a stock movement of it was
    refused (purchase_service.settle_refused_purchase_edit): the save is done, and the message
    names the medicine to check. ``purchase_no`` is the number the store holds for it then.
    """

    def __init__(self, message: str, purchase_id: int, *, saved: bool = False,
                 purchase_no: str = ""):
        super().__init__(
            message,
            [{"collection": "purchases", "id": int(purchase_id), "error": message}],
        )
        self.purchase_id = int(purchase_id)
        self.saved = bool(saved)
        self.purchase_no = str(purchase_no or "")


class SaleEditNotSaved(BundleDocumentRejected):
    """An Online sale edit the store did not take, while the rest of its bundle committed.

    The sale twin of PurchaseEditNotSaved. A customer receipt's cascade writes every bill whose
    due it changes at version + 1 with updated_at NOW(), and an edit stamped before the receipt
    but arriving after it was skipped whole while its stock movements were applied (staging,
    14 Sep, d5race OPP0: sale 36478 kept its lines, stock 0 -> 1). Such an edit is put over the
    store's copy (sale_edit_landing.land_skipped_sale_edit) or ends here: another device changed
    the bill first, or the store kept moving on under it. The message says which, and what
    became of the stock this edit moved.

    A BundleDocumentRejected, so the queue parks it for the shop (Settings -> Sync) and never
    replays it: a replay works the edit out again from the store's copy at the next version and
    moves its stock a second time.
    """

    def __init__(self, message: str, sale_id: int):
        super().__init__(message, [{"collection": "sales", "id": int(sale_id), "error": message}])
        self.sale_id = int(sale_id)


class SaleRowNotSaved(BundleDocumentRejected):
    """A sale's bill row is not on the server, while the rest of its bundle is.

    The customer's balance and the stock of that bundle committed under their own
    savepoints; only ``sale`` -- the row, with its id and client_uuid -- is still to go.
    Whoever holds this must send that row ALONE. Sending the whole sale again, as a queued
    sale or a Retry did, posted the balance and the stock a second time.

    ``refused`` is True when the server refused the row itself and will refuse it again
    until the cause is fixed. A network error, or a run of numbers other devices took
    first, is worth simply trying again. ``renumber`` says whether the row may take the
    next free number (a new bill, or an edit moved into another year) or must keep its
    own. ``tried_serial`` is the serial of ``sale["bill_no"]`` when that number was taken.
    ``local_id`` is the queue's temp id for the row once it has been queued, else 0.
    """

    def __init__(self, sale: dict, cause: Exception, *, renumber: bool, tried_serial: int = 0):
        refused = isinstance(cause, BundleDocumentRejected) and not (
            renumber and cause.only_number_taken("sales")
        )
        failures = list(getattr(cause, "failures", None) or [])
        # The server's own words (the rule or constraint), which is what the shop needs.
        reason = "; ".join(
            str(f.get("error") or f.get("constraint"))
            for f in failures
            if f.get("error") or f.get("constraint")
        ) or str(cause)
        what = sale.get("bill_no") or f"id {sale.get('id')}"
        if refused:
            message = (
                f"Server refused bill {what}: {reason}. The customer's balance and the stock "
                "were saved; the bill waits in Sales History as refused (Settings → Sync "
                "to retry). Do not bill it again."
            )
        else:
            message = f"Bill {what} did not reach the server ({reason}); only the bill will be sent again."
        super().__init__(message, failures)
        self.sale = dict(sale)
        self.cause = cause
        self.renumber = bool(renumber)
        self.tried_serial = int(tried_serial or 0)
        self.refused = refused
        self.local_id = 0


class SaleNotConfirmed(RuntimeError):
    """A new sale's send failed with no answer, so nobody knows whether the server stored it.

    ``sale`` is the document that was sent: its id, number and client_uuid. Whoever queues
    the sale again keeps that id, so the replay can ask the server for this very sale first.
    Asking for a fresh number instead stored an already-saved sale a second time under the
    next number, and the first number was never used (SCB1251/FY2026-27, store 4).
    """

    def __init__(self, sale: dict, cause: Exception):
        super().__init__(f"Bill {sale.get('bill_no') or sale.get('id')} was sent with no answer ({cause})")
        self.sale = dict(sale)
        self.cause = cause


def _stored_sale_number(answer, sale_id) -> str:
    """The bill number the server's push answer says it stored for ``sale_id``, else ""."""
    try:
        data = (
            answer.get("data")
            if isinstance(answer, dict) and isinstance(answer.get("data"), dict)
            else answer
        )
        results = ((data or {}).get("sales") or {}).get("results") or []
        for r in results:
            if isinstance(r, dict) and str(r.get("id")) == str(sale_id):
                return str(r.get("bill_no") or "").strip()
    except Exception:
        pass
    return ""


def _sale_already_stored(sale_id, client_uuid: str) -> dict | None:
    """The server's copy of this very sale (same id and client_uuid), when an earlier send landed."""
    try:
        sid = int(sale_id or 0)
    except (TypeError, ValueError):
        return None
    if sid <= 0 or not str(client_uuid or "").strip():
        return None
    try:
        held = get_doc("sales", sid)
    except Exception:
        return None
    if (
        held
        and not held.get("deleted")
        and str(held.get("client_uuid") or "") == str(client_uuid).strip()
    ):
        return held
    return None


def _meta(doc: dict) -> dict:
    out = dict(doc)
    out.setdefault("version", int(out.get("version") or 1))
    out.setdefault("updated_at", _now())
    out.setdefault("device_id", _device_id())
    out.setdefault("deleted", False)
    return out


def bump_meta(doc: dict) -> dict:
    """Force a newer version + timestamp so server LWW accepts the write."""
    out = _meta(doc)
    out["version"] = int(out.get("version") or 1) + 1
    out["updated_at"] = _now()
    out["device_id"] = _device_id()
    out["deleted"] = bool(out.get("deleted", False))
    return out


def allocate_id(collection: str) -> int:
    from core import server_api as api

    data = api.allocate_ids(_token(), collection=collection, count=1)
    ids = (data or {}).get("ids") or {}
    val = ids.get(collection)
    if isinstance(val, list):
        return int(val[0])
    return int(val)


def allocate_ids_map(requests: list[dict]) -> dict[str, Any]:
    from core import server_api as api

    data = api.allocate_ids(_token(), requests=requests)
    return (data or {}).get("ids") or {}


# Ids the server has already said it does not have.
#
# A PC on the shop floor asked /api/sync/medicines/<id> 423 times in one run and
# was answered 404 every time -- the same handful of ids, over and over, each a
# round trip to the VPS before the caller fell back to its stub. A row that is
# not there does not appear because it was asked for again a second later, so
# the answer is remembered briefly instead. Deliberately SHORT, and thrown away
# whenever anything is written or the catalogue is refreshed: another device can
# create that id at any moment, and the shop must see it.
_MISSING: dict[tuple[str, int], float] = {}
_MISSING_TTL = 60.0


def forget_missing(collection: str | None = None) -> None:
    """Stop remembering 404s -- for one collection, or for everything."""
    if collection is None:
        _MISSING.clear()
        return
    for key in [k for k in _MISSING if k[0] == collection]:
        _MISSING.pop(key, None)


def get_doc(collection: str, local_id: int) -> dict | None:
    """Fetch one server doc. Missing IDs return None (never raise 404)."""
    import time as _time

    from core import server_api as api

    key = (str(collection), int(local_id))
    seen_at = _MISSING.get(key)
    now = _time.time()
    if seen_at is not None:
        if now - seen_at < _MISSING_TTL:
            return None
        _MISSING.pop(key, None)

    try:
        doc = api.pull_doc(_token(), collection, int(local_id))
        # pull_doc itself turns a 404 into None, so THIS is where a missing row is
        # seen -- the except below only catches a 404 raised past it. The first
        # version remembered nothing because it only looked there.
        if doc is None:
            _MISSING[key] = now
            if len(_MISSING) > 4000:
                for k, t in list(_MISSING.items()):
                    if now - t >= _MISSING_TTL:
                        _MISSING.pop(k, None)
        return doc
    except api.ServerHttpError as exc:
        if int(getattr(exc, "status", 0) or 0) == 404:
            _MISSING[key] = now
            if len(_MISSING) > 4000:
                for k, t in list(_MISSING.items()):
                    if now - t >= _MISSING_TTL:
                        _MISSING.pop(k, None)
            return None
        raise
    except Exception as exc:
        # Network / parse errors — callers usually fall back to a stub. Say WHY,
        # though: swallowing this silently made an unreachable server, an expired
        # token and a genuinely missing row all look identical, and the bill
        # printer could only report "Sale id N not found".
        print(
            f"[server_crud] get_doc({collection}/{local_id}) failed: "
            f"{type(exc).__name__}: {exc}",
            flush=True,
        )
        return None


def push_bundle(bundle: dict) -> dict:
    from core import server_api as api

    result = api.push_bundle(_token(), bundle)
    # Bundles are applied per-document with a savepoint, so one bad row is
    # rejected while the rest of the batch commits -- which is what we want,
    # but nothing here ever looked at the per-document results. A return whose
    # row was refused (duplicate return_no) still had its stock and customer
    # effects applied, and the client marked it saved. The document was gone
    # and nobody was told. upsert_docs() has always checked; bundles must too.
    # Only FAILED documents are raised here, never merely skipped ones: a
    # bundle routinely carries a party the server already has at a higher
    # version, and treating that normal skip as an error is what made returns
    # retry forever.
    refused: list[dict] = []
    try:
        # api.push_bundle already unwraps and returns res["data"], so reading
        # result["data"] here always gave None and this whole scan never ran once
        # -- every rejected document went unnoticed, which is exactly what the
        # comment above says must not happen. Accept either shape.
        inner = result.get("data") if isinstance(result, dict) else None
        data = inner if isinstance(inner, dict) else result
        for collection, res in (data if isinstance(data, dict) else {}).items():
            if not isinstance(res, dict):
                continue
            for r in res.get("results") or []:
                if isinstance(r, dict) and r.get("status") == "failed":
                    refused.append(
                        {
                            "collection": collection,
                            "id": r.get("id"),
                            "error": r.get("error"),
                            "constraint": r.get("constraint"),
                        }
                    )
    except Exception:
        refused = []
    if refused:
        # A distinct type, because the two failure modes need opposite handling.
        # A transport error means nothing was written and the caller may safely
        # re-queue the whole operation. A REJECTED DOCUMENT means the rest of the
        # bundle committed under its own savepoint -- re-queueing would apply the
        # customer balance a second time and overcharge them.
        rejected = BundleDocumentRejected(
            "Server rejected "
            + "; ".join(
                f"{f['collection']}/{f['id']}: {f['error'] or f['constraint'] or 'rejected'}"
                for f in refused[:3]
            ),
            refused,
        )
        rejected.answer = result
        raise rejected
    return result


_NUMBER_RETRIES = 3


def push_sale_under_free_number(
    sale: dict,
    date_s: str,
    tried_serial: int,
    refused: Optional[BundleDocumentRejected] = None,
) -> dict:
    """Send a sale row whose bill number another device took first; returns the row sent.

    The server hands out "highest live number + 1" without holding it, so two devices
    saving at the same moment both get it and the second bill row is refused. Everything
    else in that bundle (the customer's balance, the stock) already committed under its
    own savepoint, so only the bill row goes again, under the next free number. The
    allocator counts live bills only -- the number that just clashed, or one a deleted
    row still holds, can come back -- so a retry never reuses a number already tried.

    Every failure here raises SaleRowNotSaved with the row still to send. A network error
    used to escape as a plain exception, which the direct save reads as "nothing reached
    the server" -- so it queued the whole sale, and the replay posted the customer's
    balance and the stock a second time.
    """
    from core import server_api as api
    from core.fy_serial import encode_sales_bill_no, fy_start_year_for_date, fy_start_year_in_code

    last = refused
    row = dict(sale)  # the row whose number was last refused as taken
    tried = int(tried_serial or 0)
    for _ in range(_NUMBER_RETRIES):
        try:
            fy = api.allocate_fy(_token(), "sales", date_s) or {}
        except Exception as exc:
            raise SaleRowNotSaved(row, exc, renumber=True, tried_serial=tried) from exc
        start = int(
            fy.get("fy_start_year") or row.get("fy_start_year") or fy_start_year_for_date(date_s)
        )
        # Step past the refused number only when it is a number of THIS year's series. An
        # untagged legacy number or another year's serial says nothing about this year, and
        # flooring on it jumped the series (SCB77 dated today came back as SCB78/FY2026-27,
        # and every later bill carried on from there).
        floor = tried + 1 if tried and fy_start_year_in_code(row.get("bill_no")) == start else 0
        serial = max(int(fy.get("fy_serial") or 0), floor)
        if serial <= 0:
            raise SaleRowNotSaved(
                row,
                RuntimeError("Server did not allocate next sales bill number for this store/FY"),
                renumber=True,
                tried_serial=tried,
            )
        retry = dict(row)
        retry.update(
            {
                "bill_no": encode_sales_bill_no(serial, start),
                "fy_start_year": start,
                "fy_serial": serial,
            }
        )
        try:
            push_bundle({"sales": [retry]})
        except BundleDocumentRejected as exc:
            if not exc.only_number_taken("sales"):
                raise SaleRowNotSaved(retry, exc, renumber=True, tried_serial=tried) from exc
            last = exc
            row, tried = retry, serial
            continue
        except Exception as exc:
            raise SaleRowNotSaved(retry, exc, renumber=True, tried_serial=tried) from exc
        log.warning(
            "[BILL-ONLINE] %s was taken by another device; saved as %s",
            sale.get("bill_no"),
            retry["bill_no"],
        )
        return retry
    raise SaleRowNotSaved(
        row, last or RuntimeError("bill number taken"), renumber=True, tried_serial=tried
    )


def push_sale_row_alone(sale: dict, *, renumber: bool, tried_serial: int = 0) -> dict:
    """Send the bill row of a sale whose balance and stock are already on the server.

    The queue's replay of SaleRowNotSaved: the row goes by itself, with its own id and
    client_uuid, never with the documents that already committed. Returns the row as the
    server now holds it.
    """
    try:
        held = get_doc("sales", int(sale.get("id") or sale.get("local_id") or 0))
    except Exception:
        held = None
    if (
        held
        and not held.get("deleted")
        and str(held.get("client_uuid") or "") == str(sale.get("client_uuid") or "")
        and int(held.get("version") or 0) >= int(sale.get("version") or 0)
    ):
        # An earlier send landed and only its answer was lost: nothing left to do.
        return held
    row = dict(sale)
    row["updated_at"] = _now()
    date_s = str(row.get("bill_date") or "")[:10] or date.today().isoformat()
    if renumber:
        return push_sale_under_free_number(row, date_s, tried_serial)
    try:
        push_bundle({"sales": [row]})
    except Exception as exc:
        raise SaleRowNotSaved(row, exc, renumber=False) from exc
    return row


def delete_entity(collection: str, local_id: int) -> dict:
    from core import server_api as api

    return api.delete_doc(_token(), collection, int(local_id))


class StaleWriteRejected(RuntimeError):
    """The server skipped a write because our copy was out of date."""


def _raise_if_skipped(collection: str, result: dict, docs: list[dict]) -> None:
    """
    Turn a silently-skipped write into a real error.

    The server resolves conflicts on version alone:
        if (incoming.version < existing.version) return 'skip'
    A skipped document still comes back inside an HTTP 200 bundle, marked
    {"status": "skipped"}. Nothing here inspected that, so a rejected edit was
    reported to the user as success — you would delete a medicine, see it vanish
    from the list, refresh, and find it back, because the write never landed.

    Raising lets callers refresh from the server and retry with a current version
    instead of quietly losing the change.
    """
    if not isinstance(result, dict):
        return
    data = result.get("data") if isinstance(result.get("data"), dict) else result
    results = data.get("results") if isinstance(data, dict) else None
    if not isinstance(results, list):
        return
    skipped = [r for r in results if isinstance(r, dict) and r.get("status") == "skipped"]
    failed = [r for r in results if isinstance(r, dict) and r.get("status") == "failed"]
    if failed:
        first = failed[0]
        raise RuntimeError(
            f"Server rejected {collection} #{first.get('id')}: {first.get('error') or 'unknown error'}"
        )
    if skipped and len(skipped) == len(results):
        ids = ", ".join(str(r.get("id")) for r in skipped[:5])
        raise StaleWriteRejected(
            f"{collection} ({ids}) was not saved — this device's copy is out of date. "
            f"Refresh and try again."
        )


def upsert_docs(collection: str, docs: list[dict]) -> dict:
    from core import server_api as api

    payload = [_meta(d) for d in docs if d]
    # Anything written may be exactly the row a 404 was remembered for.
    forget_missing(collection)
    result = api.push_collection(_token(), collection, payload)
    _raise_if_skipped(collection, result, payload)
    return result


def push_pharmacy_profile(profile: dict) -> None:
    from core import server_api as api

    # Stamp sync metadata like every other collection does. Without it the
    # profile went up carrying its OLD version and updated_at, so the server
    # row kept a stale timestamp and any incremental settings pull skipped it.
    # A pharmacy name, GSTIN or DL number changed on the PC therefore never
    # reached the phone -- Android kept printing bills with the old details.
    api.push_settings_profile(_token(), bump_meta(dict(profile or {})))


def save_new_sale_online(
    *,
    customer_id: int,
    medicines: list[dict],
    discount_pct,
    rounding,
    cash_paid,
    online_paid,
    doctor_name: str,
    doctor_phone: str,
    previous_due,
    discount_rs=None,
    bill_date=None,
    client_uuid: str = "",
    created_at: str = "",
    known_sale_id: int = 0,
) -> tuple[str, int]:
    """Create sale directly on server. Returns (display_bill_no, sale_id).

    ``known_sale_id`` is the id an earlier send of this same sale (``client_uuid``) was given
    before that send failed with no answer; ``created_at`` is when the sale was made.
    """
    from core.calc_engine import calc_bill_summary, calc_payment_result
    from core.fy_serial import display_sales_bill_no, encode_sales_bill_no, fy_start_year_for_date
    from core import server_api as api
    from core.online_guard import ensure_can_mutate

    ensure_can_mutate()
    cu = (client_uuid or "").strip() or str(uuid.uuid4())
    held = _sale_already_stored(known_sale_id, cu)
    if held:
        # An earlier send of this very sale reached the server and only its answer was lost.
        # It keeps the number the server stored, and nothing goes again: its customer balance
        # and stock went with it. Asking for a fresh number here stored the sale a second time
        # under the next number and left the first one unused (SCB1251/FY2026-27, store 4).
        log.warning(
            "[BILL-ONLINE] sale %s is already on the server as %s; not sent again",
            known_sale_id,
            held.get("bill_no"),
        )
        try:
            from core.online_catalog import patch_docs

            patch_docs("sales", [held])
        except Exception:
            pass
        return display_sales_bill_no(str(held.get("bill_no") or "")), int(known_sale_id)
    token = _token()
    bill_date_val = bill_date or date.today()
    date_s = (
        bill_date_val.isoformat()
        if hasattr(bill_date_val, "isoformat")
        else str(bill_date_val)[:10]
    )

    # Server: max existing FY serial for this store + 1, encoded with /FY…
    fy = api.allocate_fy(token, "sales", date_s) or {}
    start = int(fy.get("fy_start_year") or fy_start_year_for_date(bill_date_val))
    serial = int(fy.get("fy_serial") or 0)
    bill_no = (fy.get("bill_no") or "").strip()
    if not bill_no:
        if serial <= 0:
            raise RuntimeError(
                "Server did not allocate next sales bill number for this store/FY"
            )
        bill_no = encode_sales_bill_no(serial, start)
    if serial <= 0:
        # Parse SCB{n}/FY… if allocate only returned bill_no
        from core.fy_serial import display_sales_bill_no

        disp = display_sales_bill_no(bill_no)
        if disp.upper().startswith("SCB"):
            try:
                serial = int(disp[3:])
            except ValueError:
                serial = 0
    if serial <= 0:
        raise RuntimeError("Server FY allocate returned no sales serial")

    summary = calc_bill_summary(medicines, discount_pct, rounding, discount_rs=discount_rs)
    total = summary["total_amount"]
    disc_amt = summary["discount_amount"]
    disc_pct = summary["discount_pct"]

    cust = None
    try:
        from core.online_catalog import find_customer_by_id, medicine_by_id

        cust = find_customer_by_id(customer_id)
    except Exception:
        cust = None
    if not cust:
        cust = get_doc("customers", int(customer_id)) or {}
    prev_due = round(float(cust.get("total_due") or previous_due or 0), 2)
    prev_credit = round(float(cust.get("total_credit") or 0), 2)
    pay = calc_payment_result(total, cash_paid, online_paid, prev_due, prev_credit)

    if int(known_sale_id or 0) > 0:
        # Sent before and not on the server: the same sale keeps the id it was given.
        sale_id = int(known_sale_id)
    else:
        ids = allocate_ids_map([{"collection": "sales", "count": 1}])
        sale_id = int(ids.get("sales") or allocate_id("sales"))

    doc_upper = (doctor_name or "").strip().upper()

    from core.stock_utils import snapshot_sale_cost_price

    items = []
    med_docs = []
    # Sold quantity summed per medicine, plus the cached row for its display and
    # costing fields. Stock itself is read from the server after the loop.
    qty_by_med: dict[int, float] = {}
    base_by_med: dict[int, dict] = {}
    for m in medicines or []:
        mid = int(m.get("id") or 0)
        if mid <= 0:
            # Refuse rather than skip. Dropping the line here still let the bill
            # total include it, so the printed bill's items did not sum to its
            # own total. Raising sends the sale to the queue, which resolves
            # quick-add lines into real medicine rows first.
            raise ValueError(
                f"Sale line '{m.get('medicine_name') or m.get('name') or '?'}' "
                "has no medicine id"
            )
        qty = float(m.get("qty") or 0)
        rate = float(m.get("rate") or 0)
        amount = float(m.get("amount") or (qty * rate))
        try:
            from core.online_catalog import medicine_by_id

            mp = medicine_by_id(mid)
        except Exception:
            mp = None
        if not mp:
            mp = get_doc("medicines", mid) or {
                "id": mid,
                "local_id": mid,
                "name": m.get("name") or "",
                "stock_qty": 0,
            }
        mtype = str(mp.get("type") or m.get("type") or "")
        unit = mp.get("unit") if mp.get("unit") is not None else m.get("unit")
        purchase_rate = float(mp.get("rate") or 0)
        cost_price = snapshot_sale_cost_price(purchase_rate, mtype, unit)
        items.append(
            {
                "medicine_id": mid,
                "qty": qty,
                "rate": rate,
                "amount": amount,
                "item_discount": float(m.get("medicine_discount") or 0),
                "medicine_name": m.get("name") or mp.get("name") or "",
                "type": mtype,
                "batch_no": m.get("batch") or m.get("batch_no") or "",
                "expiry_date": m.get("expiry") or m.get("expiry_date") or "",
                # The line's own value first, the medicines master second --
                # the rule the purchase side already uses. These three were
                # simply absent, so every bill this store wrote on Tauri
                # reached sales_items with schedule NULL, and the server's
                # Schedule filter -- the one behind Print All -- matched none
                # of them. An H1 register that cannot be printed is not a
                # cosmetic bug.
                "schedule": _line_field(m, mp, "schedule"),
                "hsn_code": _line_field(m, mp, "hsn_code"),
                "manufacturer": _line_field(m, mp, "manufacturer"),
                # Absent too: the server inserts `it.gst_percent ?? null`, so no
                # bill made here kept the rate it was sold at, and every reprint
                # or edit fell back to the medicine's rate on that day.
                "gst_percent": _line_gst_percent(m, mp),
                "cost_price": cost_price,
            }
        )
        # Stock is settled after the loop, once per medicine -- see below.
        qty_by_med[mid] = qty_by_med.get(mid, 0.0) + qty
        base_by_med[mid] = mp

    # One stock document per medicine, built from the SERVER's copy.
    #
    # This used to run inside the loop above, off the local catalog cache and
    # stamped with _meta (which does not raise the version). The server resolves
    # conflicts on version alone, so a cache even slightly behind produced a doc
    # the server silently SKIPPED -- HTTP 200, nothing written -- while
    # patch_docs still wrote the decrement into the local cache. The counter saw
    # the right number all session and the old one came back after a restart.
    # Two lines of the same medicine on one bill also overwrote each other,
    # because each rebuilt the doc from the same unchanged starting value.
    for mid, sold_qty in qty_by_med.items():
        cached = base_by_med.get(mid) or {}
        try:
            authoritative = get_doc("medicines", mid) or {}
        except Exception:
            authoritative = {}
        source = authoritative or cached
        mp = bump_meta(dict(source))
        mp["id"] = mid
        mp["local_id"] = mid
        # Allow negative stock for Add No Stock (quick sale). Clamping to 0
        # then restoring on sale delete left phantom positive stock that stacked
        # on top of a later purchase.
        try:
            mp["stock_qty"] = float(source.get("stock_qty") or 0) - sold_qty
        except Exception:
            pass
        if mp.get("from_quick_sale"):
            # After first sale against provisional row, keep the flag until purchase.
            pass
        mp["stock_ops"] = [
            {
                "op_uuid": f"sale:{cu}:med:{mid}:v1",
                "op": "sale",
                "qty_delta": -int(round(sold_qty)),
                "medicine_id": mid,
                "ref_collection": "sales",
                "ref_id": sale_id,
                "device_id": _device_id(),
            }
        ]
        med_docs.append(mp)

    sale = _meta(
        {
            "id": sale_id,
            "local_id": sale_id,
            "bill_no": bill_no,
            "fy_start_year": start,
            "fy_serial": serial,
            "customer_id": int(customer_id),
            "customer_name": cust.get("name") or "",
            "bill_date": date_s,
            "total_amount": total,
            "discount": disc_amt,
            "discount_pct": disc_pct,
            "rounding": float(rounding or 0),
            "amount_paid": pay["amount_paid"],
            "cash_paid": float(cash_paid or 0),
            "online_paid": float(online_paid or 0),
            "doctor_name": doc_upper,
            "previous_due": prev_due,
            "previous_credit": prev_credit,
            "due_amount": pay["due_amount"],
            "credit_amount": pay["credit_amount"],
            "total_due": pay["total_due"],
            "bill_cleared": 1 if pay["due_amount"] == 0 else 0,
            "account_cleared": 0,
            "items": [
                {
                    **it,
                    "name": it.get("medicine_name") or it.get("name") or "",
                }
                for it in items
            ],
            "client_uuid": cu,
            # When the sale was made. The PC never sent it, so every PC sale on the server
            # had an empty created_at; the server keeps the first value on later edits.
            "created_at": str(created_at or "").strip() or _now(),
        }
    )

    # Update customer balances on server.
    #
    # This used to read pay["total_due"] and pay.get("new_credit", <old credit>).
    # calc_payment_result has never returned a "new_credit" key, so the fallback
    # always won and the customer's OLD credit was written straight back: a
    # customer holding 100 credit who took a 40 bill ended up with due=40 AND
    # credit=100 -- the credit was never spent and a receivable was invented.
    # pay["total_due"] cannot fix it either, because it is built on
    # max(0, prev_due - prev_credit) and so has already thrown the surplus away.
    # Carry the balance as one signed number and split it at the end.
    net = round(prev_due - prev_credit + total - pay["amount_paid"], 2)
    if abs(net) < 0.01:
        net = 0.0
    cust_out = bump_meta(dict(cust))
    cust_out["id"] = int(customer_id)
    cust_out["local_id"] = int(customer_id)
    cust_out["total_due"] = max(0.0, net)
    cust_out["total_credit"] = max(0.0, -net)

    bundle: dict[str, Any] = {"sales": [sale], "customers": [cust_out]}
    if med_docs:
        bundle["medicines"] = med_docs
    answer = None
    try:
        answer = push_bundle(bundle)
    except BundleDocumentRejected as exc:
        if not exc.only_rows_of("sales"):
            raise
        # Only the bill row was refused: the customer and stock documents of this bundle
        # are already on the server. From here on only that row may go again.
        if not exc.only_number_taken("sales"):
            raise SaleRowNotSaved(sale, exc, renumber=True) from exc
        # Another device took this number between our allocate and our push -- the next
        # free number, before it prints.
        sale = push_sale_under_free_number(sale, date_s, serial, exc)
        bill_no = sale["bill_no"]
    except Exception as exc:
        # No answer: the server may or may not hold the sale. Whoever queues it again gets
        # this sale's id with it, so the replay asks for the sale before taking a number.
        raise SaleNotConfirmed(sale, exc) from exc
    stored = _stored_sale_number(answer, sale_id)
    if stored and stored != bill_no:
        # The server already held this sale under another number (a retried send of a
        # sale it had stored): that number is the bill's. Printing the one asked for would
        # show a number no other device has, and leave the stored one looking unused.
        log.warning("[BILL-ONLINE] %s is stored on the server as %s", bill_no, stored)
        from core.fy_serial import fy_start_year_in_code, _parse_serial

        bill_no = stored
        sale["bill_no"] = stored
        sale["fy_start_year"] = fy_start_year_in_code(stored) or sale.get("fy_start_year")
        sale["fy_serial"] = _parse_serial(stored, "SCB") or sale.get("fy_serial")

    display = display_sales_bill_no(bill_no)
    log.info("[BILL-ONLINE] %s id=%s total=%.2f", display, sale_id, total)
    try:
        from core.online_catalog import patch_docs

        patch_docs("sales", [sale])
        if med_docs:
            patch_docs("medicines", med_docs)
        patch_docs("customers", [cust_out])
    except Exception:
        pass
    return display, sale_id


def _with_medicine_uuid(doc: dict) -> dict:
    """Stamp the deterministic client_uuid so Android resolves to the SAME row."""
    try:
        if not (doc.get("client_uuid") or "").strip():
            from core.client_uuid import deterministic_medicine_uuid

            name = doc.get("name") or doc.get("medicine_name") or ""
            batch = doc.get("batch_no") or doc.get("batch") or ""
            if str(name).strip():
                doc["client_uuid"] = deterministic_medicine_uuid(name, batch)
    except Exception:
        pass
    return doc


def pushed_status(answer: Any, collection: str, doc_id: Any) -> str:
    """The status a bundle answer gives document ``doc_id`` of ``collection``; "" when none."""
    try:
        data = (
            answer.get("data")
            if isinstance(answer, dict) and isinstance(answer.get("data"), dict)
            else answer
        )
        for r in ((data or {}).get(collection) or {}).get("results") or []:
            if isinstance(r, dict) and str(r.get("id")) == str(doc_id):
                return str(r.get("status") or "")
    except Exception:
        return ""
    return ""


def save_new_purchase_online(payload: dict) -> int:
    """Upsert a purchase doc built by caller (must include items + stock effects).

    An edit sets ``payload["_edit_base"]``: the store's copy it was worked out from. A
    purchase the store skips is then handed to purchase_service.land_skipped_purchase_edit,
    and the version it lands under is written back into ``payload``.
    """
    from core.online_guard import ensure_can_mutate

    ensure_can_mutate()
    doc = _meta(dict(payload))
    doc.pop("_suppliers", None)
    doc.pop("_medicines", None)
    doc.pop("_fifo", None)
    doc.pop("calc_result", None)
    edit_base = doc.pop("_edit_base", None)
    if not doc.get("client_uuid"):
        doc["client_uuid"] = str(uuid.uuid4())
    lid = int(doc.get("id") or doc.get("local_id") or 0)
    if lid <= 0:
        pid = allocate_id("purchases")
        doc["id"] = pid
        doc["local_id"] = pid
    else:
        doc["id"] = lid
        doc["local_id"] = lid
    doc.setdefault("is_autosave", False)
    bundle: dict[str, Any] = {"purchases": [doc]}
    if payload.get("_suppliers"):
        bundle["suppliers"] = [_meta(s) for s in payload["_suppliers"]]
    if payload.get("_medicines"):
        bundle["medicines"] = [_with_medicine_uuid(_meta(m)) for m in payload["_medicines"]]
    try:
        answer = push_bundle(bundle)
    except BundleDocumentRejected as exc:
        if (
            isinstance(edit_base, dict)
            and edit_base
            and not isinstance(exc, PurchaseEditNotSaved)
            and any(f.get("collection") in ("medicines", "purchases") for f in exc.failures)
        ):
            # Part of the edit's bundle was refused -- a medicine another device moved under the
            # same op_uuid first -- while the rest committed. The server's "Nothing was changed."
            # is true of that one document: say what became of the whole edit instead, and put
            # back what this edit certainly moved (always raises PurchaseEditNotSaved).
            from core.purchase_service import settle_refused_purchase_edit

            settle_refused_purchase_edit(doc, edit_base, payload.get("_medicines") or [], exc)
        raise
    if isinstance(edit_base, dict) and edit_base and (
        pushed_status(answer, "purchases", doc["id"]) == "skipped"
    ):
        # The store skipped the purchase and applied this bundle's stock movements: a supplier
        # payment's cascade, or another device, moved the bill past this edit's version. The
        # edit goes over the store's copy, or the save says why it cannot.
        from core.purchase_service import land_skipped_purchase_edit

        land_skipped_purchase_edit(doc, edit_base, payload.get("_medicines") or [])
        for key in ("version", "updated_at", "device_id"):
            payload[key] = doc.get(key)
    # Two devices can be handed the same purchase number. The server gives the second
    # one the next free number when it inserts it and says so in its answer -- which
    # nothing read, so this PC kept listing and printing the number it had asked for.
    try:
        data = (
            answer.get("data")
            if isinstance(answer, dict) and isinstance(answer.get("data"), dict)
            else answer
        )
        results = ((data or {}).get("purchases") or {}).get("results") or []
        mine = [r for r in results if isinstance(r, dict) and str(r.get("id")) == str(doc["id"])]
        stored = str((mine[0] if mine else {}).get("purchase_no") or "").strip()
        sent = str(doc.get("purchase_no") or "").strip()
        from core.fy_serial import _parse_serial, fy_start_year_in_code

        # Same year only: that is the taken-number case. An EDIT is answered with the
        # number the server already held, because its update never writes purchase_no
        # -- a purchase moved across 1 April keeps its old number there (server side).
        if (
            stored
            and stored != sent
            and fy_start_year_in_code(stored) == fy_start_year_in_code(sent)
        ):
            log.warning(
                "[PURCHASE] %s was taken; the server stored it as %s",
                doc.get("purchase_no"),
                stored,
            )
            doc["purchase_no"] = stored
            doc["fy_serial"] = _parse_serial(stored, "") or doc.get("fy_serial")
            doc["fy_start_year"] = fy_start_year_in_code(stored) or doc.get("fy_start_year")
    except Exception:
        pass
    try:
        from core.online_catalog import patch_docs

        patch_docs("purchases", [doc])
        if payload.get("_medicines"):
            patch_docs("medicines", payload["_medicines"])
        if payload.get("_suppliers"):
            patch_docs("suppliers", payload["_suppliers"])
    except Exception:
        pass
    try:
        from core.sync_status import note_collection_change, note_last_sync

        note_collection_change("purchases")
        note_collection_change("medicines")
        note_collection_change("suppliers")
        note_last_sync("push")
    except Exception:
        pass
    try:
        from core.sync_v3.data_change_bus import emit

        emit("purchases", local=True)
        emit("medicines", local=True)
    except Exception:
        pass
    try:
        from core import store_live_refresh

        store_live_refresh.emit(
            {
                "head_revision": 0,
                "source_device_id": _device_id(),
                "changes": [
                    {
                        "collection": "purchases",
                        "local_id": int(doc["id"]),
                        "operation": "upsert",
                    },
                    {
                        "collection": "medicines",
                        "local_id": 0,
                        "operation": "upsert",
                    },
                ],
                "full_refresh": False,
            }
        )
    except Exception:
        pass
    return int(doc["id"])


def delete_sale_online(sale_id: int) -> None:
    from core.online_guard import ensure_can_mutate

    ensure_can_mutate()
    delete_entity("sales", int(sale_id))


def delete_purchase_online(purchase_id: int) -> None:
    from core.online_guard import ensure_can_mutate

    ensure_can_mutate()
    delete_entity("purchases", int(purchase_id))


def upsert_payment_online(collection: str, doc: dict) -> int:
    """customer_payments | supplier_payments"""
    from core.online_guard import ensure_can_mutate

    ensure_can_mutate()
    d = _meta(dict(doc))
    try:
        nid = int(d.get("id") or d.get("local_id") or 0)
    except (TypeError, ValueError):
        nid = 0
    if nid <= 0:
        nid = allocate_id(collection)
    d["id"] = nid
    d["local_id"] = nid
    upsert_docs(collection, [d])
    return int(d["id"])


def upsert_return_online(collection: str, doc: dict) -> int:
    from core.online_guard import ensure_can_mutate

    ensure_can_mutate()
    d = _meta(dict(doc))
    if not d.get("id") and not d.get("local_id"):
        nid = allocate_id(collection)
        d["id"] = nid
        d["local_id"] = nid
    else:
        d["id"] = int(d.get("id") or d.get("local_id"))
        d["local_id"] = d["id"]
    bundle: dict[str, Any] = {collection: [d]}
    if doc.get("_medicines"):
        bundle["medicines"] = [_meta(m) for m in doc["_medicines"]]
    push_bundle(bundle)
    return int(d["id"])


def upsert_medicine_online(doc: dict) -> int:
    from core.online_guard import ensure_can_mutate

    ensure_can_mutate()
    # Both PC medicine write paths must carry the SAME deterministic uuid.
    # The purchase bundle stamps it; this path did not, so the uuid-less write
    # claimed the local_id first and the server pushed the real one to MAX+1.
    d = _with_medicine_uuid(_meta(dict(doc)))
    added = not d.get("id") and not d.get("local_id")
    if added:
        nid = allocate_id("medicines")
        d["id"] = nid
        d["local_id"] = nid
    else:
        d["id"] = int(d.get("id") or d.get("local_id"))
        d["local_id"] = d["id"]
    upsert_docs("medicines", [d])
    if added:
        try:
            # A new batch came in today: a back-dated bill's answer must count it.
            from core.sale_availability import medicines_changed

            medicines_changed([d["id"]])
        except Exception:
            pass
    try:
        from core.online_catalog import patch_docs

        patch_docs("medicines", [d])
    except Exception:
        pass
    return int(d["id"])


def upsert_contact_online(collection: str, doc: dict) -> int:
    """customers | suppliers | doctors"""
    from core.online_guard import ensure_can_mutate

    ensure_can_mutate()
    d = _meta(dict(doc))
    if not d.get("id") and not d.get("local_id"):
        nid = allocate_id(collection)
        d["id"] = nid
        d["local_id"] = nid
    else:
        d["id"] = int(d.get("id") or d.get("local_id"))
        d["local_id"] = d["id"]
    # Never overwrite a filled phone/address with blanks (folder-replace / empty form).
    if collection in ("customers", "suppliers") and int(d["id"]) > 0:
        try:
            existing = get_doc(collection, int(d["id"])) or {}
        except Exception:
            existing = {}
        if isinstance(existing, dict) and existing:
            if not str(d.get("phone") or "").strip():
                d["phone"] = existing.get("phone") or ""
            if not str(d.get("address") or "").strip():
                d["address"] = existing.get("address") or ""
            for key in ("total_due", "total_credit"):
                if d.get(key) in (None, "") and existing.get(key) is not None:
                    d[key] = existing.get(key)
            # Base the version on the server's own copy and move past it. The
            # server resolves by version alone, so a document carrying a stale
            # version is answered with HTTP 200 and written NOWHERE. That is how
            # a deleted receipt could leave the customer's due unrestored, and
            # how a balance recomputed after a return quietly failed to save.
            try:
                d["version"] = int(existing.get("version") or 0) + 1
                d["updated_at"] = _now()
                d["device_id"] = _device_id()
            except Exception:
                pass
    upsert_docs(collection, [d])
    return int(d["id"])


def delete_contact_online(collection: str, local_id: int) -> None:
    from core.online_guard import ensure_can_mutate

    ensure_can_mutate()
    delete_entity(collection, int(local_id))
