"""Push local Mac2 SQLite store data to Satpuda Core Server (not Server)."""
from __future__ import annotations

import logging
import os
import re
import sqlite3
from typing import Any, Callable, Optional

log = logging.getLogger(__name__)

ProgressCb = Optional[Callable[[str], None]]

# Everything a store must hand over when it moves Offline -> Online.
#
# stock_disposals, general_products and pending_orders were missing, so a shop
# that had been working offline uploaded its bills but silently left its
# write-offs, its general (non-medicine) products and its pending reorders
# behind. The guard in online_migrate refuses to wipe the local DB when it finds
# rows in a table that is not listed here, so nothing was lost -- but the switch
# could not complete either. The server already accepts all three.
_PUSH_ORDER = [
    "customers",
    "suppliers",
    "medicines",
    "doctors",
    "sales",
    "purchases",
    "customer_payments",
    "supplier_payments",
    "sales_returns",
    "purchase_returns",
    "stock_disposals",
    "general_products",
    "pending_orders",
]

# Large chunks cut HTTP round-trips. Heavy collections stay smaller because each
# doc carries nested line items (still far above the old 20/40 defaults).
_CHUNK = 400
_CHUNK_HEAVY = 80
_HEAVY_COLS = frozenset({"sales", "purchases", "sales_returns", "purchase_returns"})
_FLAT_COLS = frozenset({
    "customers",
    "suppliers",
    "medicines",
    "doctors",
    "customer_payments",
    "supplier_payments",
    # Plain one-row-per-record tables: the generic SELECT * builder handles them.
    "stock_disposals",
    "general_products",
    "pending_orders",
})


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


def _display_name(store: dict) -> str:
    return (
        store.get("display_name")
        or store.get("store_name")
        or (store.get("store_key") or "").replace("Store_", "").replace("_", " ")
        or "Store"
    ).strip()


def _open_store_db(store_key: str) -> sqlite3.Connection:
    from core.store_manager import get_store_db_path

    path = get_store_db_path(store_key)
    if not os.path.isfile(path):
        raise FileNotFoundError(f"Store DB not found: {path}")
    conn = sqlite3.connect(path, timeout=60)
    conn.row_factory = sqlite3.Row
    return conn


def _remote_store_for(store: dict, *, admin_token: str = "") -> dict:
    """The server row for one local store, WITHOUT an administrator where possible.

    A push needs three things off the remote row: its store_id, its name and its
    pairing key. When the PC already holds that store's SC- key, ``/api/auth/pair``
    hands all three back for that one store and nothing else -- no credential, no
    list of the account's other shops. Only when there is no key (a store that has
    never been published) does this need the administrator, and then only a token
    a person signed in for.
    """
    from core import server_api as api
    from core.store_link import get_local_android_key

    store_key = str(store.get("store_key") or "")
    key = ""
    try:
        key = (get_local_android_key(store_key) or "").strip()
    except Exception:
        key = ""
    if key:
        try:
            data = api.pair_store(
                android_key=key,
                store_name=_display_name(store),
                device_id=api._pc_device_id() or store_key or key,
                device_type="pc",
                device_name=f"Mac2:{store_key}",
            ) or {}
            remote = dict(data.get("store") or {})
            if remote:
                remote.setdefault("android_key", key)
                return remote
        except Exception:
            # A key that the server will not accept is not a reason to fall
            # through to the administrator; say so instead.
            raise
    token = str(admin_token or "").strip()
    if not token:
        from core import admin_session

        token = admin_session.token()
    return _ensure_remote_store(token, store)


def _ensure_remote_store(admin_token: str, store: dict) -> dict:
    """Create store on server if missing; return remote store row with android_key.

    ADMINISTRATOR ONLY: it reads and may add to the whole account's store list.
    ``admin_token`` has to come from a person who typed the vendor's username
    and password; there is no compiled-in one any more.
    """
    from core import server_api as api
    from core.server_api import _pc_device_id

    store_key = store.get("store_key") or ""
    store_id = _slug_store_id(store_key)
    name = _display_name(store)
    remote = api.list_remote_stores(admin_token)
    for s in remote:
        if (
            s.get("store_id") == store_id
            or s.get("store_key") == store_key
            or (s.get("store_name") or "").strip().lower() == name.lower()
        ):
            return s
    created = api.create_store(
        admin_token,
        store_name=name,
        store_id=store_id,
        store_key=store_key or None,
    )
    return created


def _pair_for_store(remote_store: dict, store_key: str) -> str:
    from core import server_api as api
    # _pc_device_id is used below. It was only ever imported inside
    # _ensure_remote_store, so this function raised NameError the moment it was
    # reached -- which is why "Push to server" never uploaded anything.
    from core.server_api import _pc_device_id

    key = remote_store.get("android_key")
    if not key:
        raise RuntimeError(f"Remote store {remote_store.get('store_id')} has no pairing key")
    data = api.pair_store(
        android_key=key,
        store_name=remote_store.get("store_name") or "",
        device_id=_pc_device_id() or store_key or key,
        device_type="pc",
        device_name=f"Mac2:{store_key}",
    )
    token = data.get("token")
    if not token:
        raise RuntimeError("Pairing returned no store token")
    # Persist local android key for this PC (shared file — last store wins; OK for single active)
    try:
        from core.store_link import _save_local

        _save_local(key)
    except Exception:
        pass
    return token


def _build_docs(conn, collection: str) -> list[dict]:
    """Build server docs (with id). Flat tables and sales/purchases use bulk SQL."""
    if collection in _FLAT_COLS:
        return _build_flat_docs(conn, collection)
    if collection == "sales":
        return _build_sales_docs(conn)
    if collection == "purchases":
        return _build_purchases_docs(conn)
    return _build_docs_one_by_one(conn, collection)


def _build_docs_one_by_one(conn, collection: str) -> list[dict]:
    """Fallback per-id builders (returns and any unknown collection)."""
    from core import server_entity_sync as fb

    cur = conn.cursor()
    cur.execute(f"SELECT id FROM {collection}")
    ids = [int(r[0]) for r in cur.fetchall()]
    docs: list[dict] = []
    builders = {
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
    }
    build = builders.get(collection)
    if not build:
        return docs
    for rid in ids:
        try:
            payload = build(conn, rid)
        except Exception as exc:
            log.warning("build %s/%s failed: %s", collection, rid, exc)
            continue
        if not payload:
            continue
        payload = dict(payload)
        payload["id"] = rid
        if collection in ("sales", "purchases"):
            try:
                cur.execute(f"SELECT is_autosave FROM {collection} WHERE id=?", (rid,))
                row = cur.fetchone()
                if row is not None:
                    payload["is_autosave"] = bool(int(row[0] or 0))
            except Exception:
                pass
        _sanitize_dates_in_doc(payload)
        docs.append(payload)
    return docs


def _rows_as_dicts(cur) -> list[dict]:
    from core.server_entity_sync import _row_dict

    return [_row_dict(cur, r) for r in cur.fetchall()]


def _build_flat_docs(conn, collection: str) -> list[dict]:
    """One SELECT * for flat masters/payments — avoid N+1 payload builds."""
    from core.server_entity_sync import _sync_meta_fields_from_row

    cur = conn.cursor()
    cur.execute(f"SELECT * FROM {collection}")
    rows = _rows_as_dicts(cur)
    if not rows:
        return []

    name_by_id: dict[int, str] = {}
    if collection == "customer_payments":
        cur.execute("SELECT id, name FROM customers")
        name_by_id = {int(r[0]): r[1] for r in cur.fetchall()}
    elif collection == "supplier_payments":
        cur.execute("SELECT id, name FROM suppliers")
        name_by_id = {int(r[0]): r[1] for r in cur.fetchall()}

    docs: list[dict] = []
    for row in rows:
        rid = int(row["id"])
        try:
            if collection == "customers":
                payload = {
                    "name": row.get("name"),
                    "phone": row.get("phone"),
                    "address": row.get("address"),
                    "document_name": row.get("document_name"),
                    "total_due": row.get("total_due", 0),
                    "total_credit": row.get("total_credit", 0),
                    "created_at": row.get("created_at"),
                }
            elif collection == "suppliers":
                payload = {
                    "name": row.get("name"),
                    "phone": row.get("phone"),
                    "address": row.get("address"),
                    "gstin": row.get("gstin"),
                    "dl_numbers": row.get("dl_numbers"),
                    "total_due": row.get("total_due", 0),
                    "total_credit": row.get("total_credit", 0),
                    "created_at": row.get("created_at"),
                }
            elif collection == "medicines":
                payload = {
                    "name": row.get("name"),
                    "type": row.get("type"),
                    "stock_qty": row.get("stock_qty", 0),
                    "unit": row.get("unit"),
                    "gst_percent": row.get("gst_percent"),
                    "mrp": row.get("mrp"),
                    "rate": row.get("rate"),
                    "manufacturer": row.get("manufacturer"),
                    "batch_no": row.get("batch_no"),
                    "expiry_date": row.get("expiry_date"),
                    "hsn_code": row.get("hsn_code"),
                    "schedule": row.get("schedule"),
                    "location": row.get("location"),
                    "content_drug": row.get("content_drug"),
                    "is_hidden": bool(int(row.get("is_hidden") or 0)),
                    "synced_at": row.get("synced_at"),
                    "created_at": row.get("created_at"),
                }
            elif collection == "doctors":
                payload = {
                    "name": row.get("name"),
                    "phone": row.get("phone"),
                    "registration_number": row.get("registration_number"),
                    "created_at": row.get("created_at"),
                }
            elif collection == "customer_payments":
                cid = row.get("customer_id")
                payload = {
                    "customer_id": cid,
                    "customer_name": name_by_id.get(int(cid)) if cid not in (None, "") else None,
                    "payment_date": row.get("payment_date"),
                    "amount": row.get("amount", 0),
                    "payment_mode": row.get("payment_mode"),
                    "cash_amount": row.get("cash_amount", 0),
                    "online_amount": row.get("online_amount", 0),
                    "reference_no": row.get("reference_no"),
                    "note": row.get("note"),
                    "created_at": row.get("created_at"),
                }
            elif collection == "supplier_payments":
                sid = row.get("supplier_id")
                payload = {
                    "payment_no": row.get("payment_no"),
                    "supplier_id": sid,
                    "supplier_name": name_by_id.get(int(sid)) if sid not in (None, "") else None,
                    "payment_date": row.get("payment_date"),
                    "amount": row.get("amount", 0),
                    "mode": row.get("mode"),
                    "reference": row.get("reference"),
                    "due_before": row.get("due_before", 0),
                    "due_after": row.get("due_after", 0),
                    "created_at": row.get("created_at"),
                }
            else:
                # Plain tables with no hand-written mapping (stock_disposals,
                # general_products, pending_orders): send the row as it stands,
                # minus the local-only sync bookkeeping that
                # _sync_meta_fields_from_row re-adds below. Falling through to
                # `continue` here silently dropped the whole collection, which is
                # how write-offs and general products were left behind on an
                # Offline -> Online switch.
                payload = {
                    k: v
                    for k, v in row.items()
                    if k
                    not in (
                        "id",
                        "version",
                        "updated_at",
                        "device_id",
                        "deleted",
                        "sync_status",
                        "synced_at",
                    )
                }
            payload.update(_sync_meta_fields_from_row(row))
            payload["id"] = rid
            _sanitize_dates_in_doc(payload)
            docs.append(payload)
        except Exception as exc:
            log.warning("bulk build %s/%s failed: %s", collection, rid, exc)
    return docs


def _build_sales_docs(conn) -> list[dict]:
    """Bulk sales + items + customers — one pass instead of N+1 queries."""
    from core.server_entity_sync import _line_medicine_id, _sanitize_server_doc, _sync_meta_fields_from_row

    cur = conn.cursor()
    cur.execute("SELECT * FROM sales")
    sales = _rows_as_dicts(cur)
    if not sales:
        return []

    cur.execute("SELECT id, name, phone, address FROM customers")
    customers = {
        int(r[0]): {"name": r[1], "phone": r[2], "address": r[3]} for r in cur.fetchall()
    }

    sale_ids = [int(s["id"]) for s in sales]
    items_by_sale: dict[int, list] = {sid: [] for sid in sale_ids}
    # Chunk IN lists for SQLite variable limits
    for i in range(0, len(sale_ids), 800):
        chunk = sale_ids[i : i + 800]
        placeholders = ",".join("?" * len(chunk))
        cur.execute(
            f"""
            SELECT
                si.sale_id,
                si.medicine_id,
                si.qty, si.rate, si.gst_percent, si.amount,
                si.item_discount, si.cost_price,
                m.name AS med_name,
                m.type AS med_type,
                m.batch_no AS med_batch_no,
                m.expiry_date AS med_expiry_date,
                m.hsn_code AS med_hsn_code,
                m.schedule AS med_schedule,
                m.manufacturer AS med_manufacturer
            FROM sales_items si
            LEFT JOIN medicines m ON si.medicine_id = m.id
            WHERE si.sale_id IN ({placeholders})
            """,
            chunk,
        )
        for ir in _rows_as_dicts(cur):
            sid = int(ir["sale_id"])
            items_by_sale.setdefault(sid, []).append(
                {
                    "medicine_id": _line_medicine_id(ir),
                    "name": ir.get("med_name") or ir.get("name"),
                    "type": ir.get("med_type") or ir.get("type"),
                    "batch_no": ir.get("med_batch_no") or ir.get("batch_no"),
                    "expiry_date": ir.get("med_expiry_date") or ir.get("expiry_date"),
                    "hsn_code": ir.get("med_hsn_code") or ir.get("hsn_code"),
                    "schedule": ir.get("med_schedule") or ir.get("schedule"),
                    "manufacturer": ir.get("med_manufacturer") or ir.get("manufacturer"),
                    "qty": ir.get("qty", 0),
                    "rate": ir.get("rate", 0),
                    "gst_percent": ir.get("gst_percent"),
                    "amount": ir.get("amount", 0),
                    "item_discount": ir.get("item_discount", 0),
                    "cost_price": ir.get("cost_price", 0),
                }
            )

    docs: list[dict] = []
    for sale in sales:
        rid = int(sale["id"])
        try:
            customer = customers.get(int(sale["customer_id"] or 0), {})
            items = items_by_sale.get(rid, [])
            payload = {
                "bill_no": sale.get("bill_no"),
                "bill_date": sale.get("bill_date"),
                "total_amount": sale.get("total_amount"),
                "discount": sale.get("discount", 0),
                "discount_pct": sale.get("discount_pct", 0),
                "rounding": sale.get("rounding", 0),
                "amount_paid": sale.get("amount_paid", 0),
                "cash_paid": sale.get("cash_paid", 0),
                "online_paid": sale.get("online_paid", 0),
                "previous_due": sale.get("previous_due", 0),
                "previous_credit": sale.get("previous_credit", 0),
                "due_amount": sale.get("due_amount", 0),
                "credit_amount": sale.get("credit_amount", 0),
                "total_due": sale.get("total_due", 0),
                "paid_due": sale.get("paid_due", 0),
                "bill_cleared": bool(sale.get("bill_cleared")),
                "account_cleared": bool(sale.get("account_cleared")),
                "doctor_name": sale.get("doctor_name"),
                "created_at": sale.get("created_at"),
                "customer_id": sale.get("customer_id"),
                "customer_name": customer.get("name"),
                "customer_phone": customer.get("phone"),
                "customer_address": customer.get("address"),
                "item_count": len(items),
                "items": items,
                "is_autosave": bool(int(sale.get("is_autosave") or 0)),
            }
            try:
                from core.fy_serial import display_sales_bill_no, fy_label

                payload["display_bill_no"] = display_sales_bill_no(sale.get("bill_no"))
                if sale.get("fy_start_year") not in (None, ""):
                    fy = int(sale["fy_start_year"])
                    payload["fy_start_year"] = fy
                    payload["fy_label"] = fy_label(fy)
                if sale.get("fy_serial") not in (None, ""):
                    payload["fy_serial"] = int(sale["fy_serial"])
            except Exception:
                pass
            payload.update(_sync_meta_fields_from_row(sale))
            payload = _sanitize_server_doc(payload)
            payload["id"] = rid
            _sanitize_dates_in_doc(payload)
            docs.append(payload)
        except Exception as exc:
            log.warning("bulk build sales/%s failed: %s", rid, exc)
    return docs


def _build_purchases_docs(conn) -> list[dict]:
    """Bulk purchases + items + suppliers."""
    from core.server_entity_sync import _line_medicine_id, _sanitize_server_doc, _sync_meta_fields_from_row

    cur = conn.cursor()
    cur.execute("SELECT * FROM purchases")
    purchases = _rows_as_dicts(cur)
    if not purchases:
        return []

    cur.execute("SELECT id, name, phone FROM suppliers")
    suppliers = {
        int(r[0]): {"name": r[1], "phone": r[2]} for r in cur.fetchall()
    }

    purchase_ids = [int(p["id"]) for p in purchases]
    items_by_purchase: dict[int, list] = {pid: [] for pid in purchase_ids}
    # An old store file predates schedule / hsn_code / manufacturer on the line,
    # and one unknown column would abort the WHOLE migration push, not just the
    # purchases. Ask the table what it actually has.
    try:
        _pi_cols = {r[1] for r in cur.execute("PRAGMA table_info(purchase_items)")}
    except Exception:
        _pi_cols = set()
    _extra = [f"pi.{c}" for c in ("schedule", "hsn_code", "manufacturer") if c in _pi_cols]
    _extra_sql = ("," + ", ".join(_extra)) if _extra else ""

    for i in range(0, len(purchase_ids), 800):
        chunk = purchase_ids[i : i + 800]
        placeholders = ",".join("?" * len(chunk))
        cur.execute(
            f"""
            SELECT
                pi.purchase_id,
                pi.medicine_id,
                pi.qty, pi.free_qty, pi.type, pi.rate, pi.mrp, pi.gst_pct,
                pi.batch_no, pi.expiry_date, pi.item_amount{_extra_sql},
                m.name AS med_name,
                m.schedule AS med_schedule,
                m.hsn_code AS med_hsn_code,
                m.manufacturer AS med_manufacturer
            FROM purchase_items pi
            LEFT JOIN medicines m ON pi.medicine_id = m.id
            WHERE pi.purchase_id IN ({placeholders})
            """,
            chunk,
        )
        for ir in _rows_as_dicts(cur):
            pid = int(ir["purchase_id"])
            items_by_purchase.setdefault(pid, []).append(
                {
                    "medicine_id": _line_medicine_id(ir),
                    "name": ir.get("med_name") or ir.get("name"),
                    "type": ir.get("type"),
                    "qty": ir.get("qty", 0),
                    "free_qty": ir.get("free_qty", 0),
                    "rate": ir.get("rate", 0),
                    "mrp": ir.get("mrp", 0),
                    "gst_pct": ir.get("gst_pct", 0),
                    "batch_no": ir.get("batch_no"),
                    "expiry_date": ir.get("expiry_date"),
                    # The line's own value first, the medicines master second.
                    # Purchases migrated up from an old offline store carried no
                    # schedule at all, so the server's Schedule filter matched
                    # none of them and the shop saw an empty list.
                    "schedule": ir.get("schedule") or ir.get("med_schedule"),
                    "hsn_code": ir.get("hsn_code") or ir.get("med_hsn_code"),
                    "manufacturer": ir.get("manufacturer") or ir.get("med_manufacturer"),
                    "item_amount": ir.get("item_amount", 0),
                }
            )

    docs: list[dict] = []
    for p in purchases:
        rid = int(p["id"])
        try:
            supplier = suppliers.get(int(p.get("supplier_id") or 0), {})
            items = items_by_purchase.get(rid, [])
            payload = {
                "purchase_no": p.get("purchase_no"),
                "supplier_id": p.get("supplier_id"),
                "purchase_date": p.get("purchase_date"),
                "bill_number": p.get("bill_number"),
                "subtotal": p.get("subtotal", 0),
                "total_gst": p.get("total_gst", 0),
                "cgst": p.get("cgst", 0),
                "sgst": p.get("sgst", 0),
                "total_amount": p.get("total_amount", 0),
                "overall_discount": p.get("overall_discount", 0),
                "rounding": p.get("rounding", 0),
                "need_to_pay": p.get("need_to_pay", 0),
                "final_amount": p.get("final_amount", 0),
                "amount_paid": p.get("amount_paid", 0),
                "amount_paid_at_entry": p.get("amount_paid_at_entry", 0),
                "cash_paid_at_entry": p.get("cash_paid_at_entry", 0),
                "online_paid_at_entry": p.get("online_paid_at_entry", 0),
                "previous_due": p.get("previous_due", 0),
                "previous_credit": p.get("previous_credit", 0),
                "due": p.get("due", 0),
                "current_credit": p.get("current_credit", 0),
                "total_due": p.get("total_due", 0),
                "due_amount": p.get("due_amount", 0),
                "credit_amount": p.get("credit_amount", 0),
                "paid_due": p.get("paid_due", 0),
                "bill_cleared": bool(p.get("bill_cleared")),
                "account_cleared": bool(p.get("account_cleared")),
                "gst_calc_method": p.get("gst_calc_method"),
                "expenditure": p.get("expenditure", 0),
                "is_autosave": bool(int(p.get("is_autosave") or 0)),
                "supplier_name": supplier.get("name") or p.get("supplier_name"),
                "supplier_phone": supplier.get("phone") or p.get("supplier_phone"),
                "item_count": len(items),
                "items": items,
                "created_at": p.get("created_at"),
            }
            try:
                from core.fy_serial import display_purchase_no, fy_label

                payload["display_purchase_no"] = display_purchase_no(p.get("purchase_no"))
                if p.get("fy_start_year") not in (None, ""):
                    fy = int(p["fy_start_year"])
                    payload["fy_start_year"] = fy
                    payload["fy_label"] = fy_label(fy)
                if p.get("fy_serial") not in (None, ""):
                    payload["fy_serial"] = int(p["fy_serial"])
            except Exception:
                pass
            payload.update(_sync_meta_fields_from_row(p))
            payload = _sanitize_server_doc(payload)
            payload["id"] = rid
            _sanitize_dates_in_doc(payload)
            docs.append(payload)
        except Exception as exc:
            log.warning("bulk build purchases/%s failed: %s", rid, exc)
    return docs


def _looks_like_date(value: str) -> bool:
    if not isinstance(value, str):
        return False
    s = value.strip()
    if len(s) < 8:
        return False
    return bool(re.match(r"^\d{4}-\d{2}-\d{2}", s))


def _sanitize_dates_in_doc(doc: dict) -> None:
    """Drop junk date strings (e.g. '-12-01') so Postgres DATE columns accept the row."""
    date_keys = {
        "expiry_date",
        "bill_date",
        "purchase_date",
        "payment_date",
        "return_date",
        "created_at",
        "updated_at",
        "synced_at",
        "last_updated",
    }
    for key in list(doc.keys()):
        val = doc.get(key)
        if key in date_keys or key.endswith("_date") or key.endswith("_at"):
            if isinstance(val, str) and val.strip() and not _looks_like_date(val):
                doc[key] = None
            elif isinstance(val, str) and not val.strip():
                doc[key] = None
        elif isinstance(val, list):
            for item in val:
                if isinstance(item, dict):
                    _sanitize_dates_in_doc(item)
        elif isinstance(val, dict):
            _sanitize_dates_in_doc(val)


def _push_profile_and_dropdowns(conn, store_token: str) -> int:
    import json

    from core import server_api as api

    n = 0
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM pharmacy_profile LIMIT 1")
        row = cur.fetchone()
        if row:
            cols = [d[0] for d in cur.description]
            p = dict(zip(cols, row))
            # This builder used to omit logo_path and all sync metadata. The
            # server writes every column unconditionally, so a "push store to
            # server" nulled the stored logo, reset version to 1 and cleared
            # device_id -- the desktop ratcheting against itself with no other
            # device involved. Send the full column set, stamped like every
            # other push.
            from core.server_crud import bump_meta

            api.push_settings_profile(
                store_token,
                bump_meta(
                    {
                        "name": p.get("name"),
                        "address": p.get("address"),
                        "phone": p.get("phone"),
                        "email": p.get("email"),
                        "gstin": p.get("gstin"),
                        "dl_number": p.get("dl_number"),
                        "gst_enabled": bool(int(p.get("gst_enabled") or 1)),
                        "fssai_number": p.get("fssai_number"),
                        "show_fssai_on_bill": bool(int(p.get("show_fssai_on_bill") or 0)),
                        "logo_path": p.get("logo_path"),
                    }
                ),
            )
            n += 1
    except Exception as exc:
        log.warning("pharmacy_profile push: %s", exc)

    try:
        villages: list = []
        default_village = None
        cur = conn.cursor()
        try:
            cur.execute("SELECT value FROM settings WHERE name='customer_villages'")
            r = cur.fetchone()
            if r and r[0]:
                villages = json.loads(r[0]) if isinstance(r[0], str) else (r[0] or [])
        except Exception:
            pass
        try:
            cur.execute("SELECT value FROM settings WHERE name='default_customer_village'")
            r = cur.fetchone()
            if r:
                default_village = r[0]
        except Exception:
            pass
        try:
            from core import layout_config

            med_types = list(getattr(layout_config, "MED_TYPES", []) or [])
            schedules = list(getattr(layout_config, "SCHEDULES", []) or [])
        except Exception:
            med_types = ["TAB", "SYRUP", "INJ", "CAP", "OINT"]
            schedules = ["H", "H1", "X"]
        api.push_settings_dropdowns(
            store_token,
            {
                "villages": villages or [],
                "default_village": default_village,
                "med_types": med_types,
                "schedules": schedules,
            },
        )
        n += 1
    except Exception as exc:
        log.warning("dropdowns push: %s", exc)
    return n


def _push_chunk_with_retry(api, store_token: str, col: str, chunk: list) -> dict:
    """Push one collection chunk; backoff on 429 / connection resets."""
    import time

    last_exc: Exception | None = None
    for attempt in range(1, 5):
        try:
            return api.push_collection(store_token, col, chunk, timeout=180.0)
        except Exception as exc:
            last_exc = exc
            msg = str(exc).lower()
            retryable = (
                "429" in msg
                or "too many" in msg
                or "10054" in msg
                or "timed out" in msg
                or "timeout" in msg
                or "forcibly closed" in msg
                or "connection reset" in msg
            )
            if not retryable or attempt >= 4:
                raise
            wait = min(8.0, 1.2 * attempt * attempt)
            log.warning(
                "push %s chunk retry %s/4 after %s (wait %.1fs)",
                col,
                attempt,
                exc,
                wait,
            )
            time.sleep(wait)
    if last_exc:
        raise last_exc
    return {}


def push_store_conn_detailed(
    conn, store_token: str, *, progress_cb: ProgressCb = None, label: str = ""
) -> dict[str, Any]:
    """Push every collection and REPORT what failed.

    The server isolates each document in its own SAVEPOINT and reports per-row
    failures inside an HTTP 200, so a push can lose rows while looking successful.
    Summing only ``upserted`` hid that -- and push_local_then_wipe then deleted the
    local database on the strength of it. Callers that are about to destroy data
    must use this and check ``failed``.
    """
    from core import server_api as api

    total = 0
    failed = 0
    failures: list[str] = []
    per_collection: dict[str, dict[str, int]] = {}
    prefix = f"{label}: " if label else ""
    for col in _PUSH_ORDER:
        _progress(progress_cb, f"{prefix}Preparing {col}…")
        docs = _build_docs(conn, col)
        if not docs:
            continue
        stats = per_collection.setdefault(col, {"sent": 0, "upserted": 0, "failed": 0})
        stats["sent"] += len(docs)
        chunk_size = _CHUNK_HEAVY if col in _HEAVY_COLS else _CHUNK
        for i in range(0, len(docs), chunk_size):
            chunk = docs[i : i + chunk_size]
            _progress(
                progress_cb,
                f"{prefix}Uploading {col}… {min(i + len(chunk), len(docs)):,}/{len(docs):,}",
            )
            result = _push_chunk_with_retry(api, store_token, col, chunk)
            up = int(result.get("upserted") or 0)
            total += up
            stats["upserted"] += up
            bad = int(result.get("failed") or 0)
            if bad:
                failed += bad
                stats["failed"] += bad
                for row in (result.get("results") or []):
                    if isinstance(row, dict) and row.get("status") == "failed":
                        failures.append(
                            f"{col}: {row.get('error') or row.get('message') or 'failed'}"
                        )
            # No artificial sleep — sync limiter is 3000 req/min; retries handle 429.
    _progress(progress_cb, f"{prefix}Uploading settings…")
    total += _push_profile_and_dropdowns(conn, store_token)
    return {
        "upserted": total,
        "failed": failed,
        "failures": failures[:50],
        "per_collection": per_collection,
    }


def push_store_conn(conn, store_token: str, *, progress_cb: ProgressCb = None, label: str = "") -> int:
    res = push_store_conn_detailed(
        conn, store_token, progress_cb=progress_cb, label=label
    )
    return int(res.get("upserted") or 0)


def push_active_store_to_server(
    conn, *, progress_cb: ProgressCb = None, admin_token: str = ""
) -> int:
    """Push the currently open SQLite connection (active store) to the server."""
    from core import server_api as api
    from core.store_manager import get_active_store_key, list_stores

    _progress(progress_cb, "Checking server…")
    api.health()
    active = get_active_store_key()
    store = next((s for s in list_stores() if s.get("store_key") == active), None)
    if not store:
        store = {"store_key": active or "Store_Default", "store_name": active or "Default"}
    _progress(progress_cb, f"Ensuring store {_display_name(store)} on server…")
    remote = _remote_store_for(store, admin_token=admin_token)
    _progress(progress_cb, "Pairing device…")
    token = _pair_for_store(remote, store.get("store_key") or "")
    return push_store_conn(conn, token, progress_cb=progress_cb, label=_display_name(store))


def push_active_store_to_server_detailed(
    conn, *, progress_cb: ProgressCb = None, admin_token: str = ""
) -> dict[str, Any]:
    """Same as push_active_store_to_server but reports per-row failures."""
    from core import server_api as api
    from core.store_manager import get_active_store_key, list_stores

    _progress(progress_cb, "Checking server…")
    api.health()
    active = get_active_store_key()
    store = next((s for s in list_stores() if s.get("store_key") == active), None)
    if not store:
        store = {"store_key": active or "Store_Default", "store_name": active or "Default"}
    _progress(progress_cb, f"Ensuring store {_display_name(store)} on server…")
    remote = _remote_store_for(store, admin_token=admin_token)
    _progress(progress_cb, "Pairing device…")
    token = _pair_for_store(remote, store.get("store_key") or "")
    res = push_store_conn_detailed(
        conn, token, progress_cb=progress_cb, label=_display_name(store)
    )
    res["store_token"] = token
    return res


def push_all_local_stores_to_server(
    *, progress_cb: ProgressCb = None, admin_token: str = ""
) -> tuple[int, list[str]]:
    """Push every local store DB on this PC to the server. Returns (total_upserted, messages)."""
    from core import server_api as api
    from core.store_manager import list_stores, reconcile_registry_with_disk

    try:
        reconcile_registry_with_disk()
    except Exception:
        pass

    stores = list_stores()
    if not stores:
        raise RuntimeError("No local stores found on this PC.")

    _progress(progress_cb, "Checking server…")
    api.health()

    total = 0
    messages: list[str] = []
    for store in stores:
        key = store.get("store_key") or ""
        name = _display_name(store)
        try:
            _progress(progress_cb, f"[{name}] Ensuring on server…")
            remote = _remote_store_for(store, admin_token=admin_token)
            _progress(progress_cb, f"[{name}] Pairing…")
            token = _pair_for_store(remote, key)
            conn = _open_store_db(key)
            try:
                n = push_store_conn(conn, token, progress_cb=progress_cb, label=name)
                total += n
                messages.append(f"{name}: uploaded {n:,} record(s) (key {remote.get('android_key')})")
            finally:
                conn.close()
        except Exception as exc:
            log.exception("push store %s failed", key)
            messages.append(f"{name}: FAILED — {exc}")
    return total, messages
