"""
core/purchase_service.py
────────────────────────
All database operations for the purchase flow.
No UI code. No calculation code.
Called by ui/purchase.py and ui/purchase_history.py.
"""
from __future__ import annotations

import re
import sqlite3
import uuid
from datetime import datetime

from core.app_prefs import load_app_mode
from core.layout_config import is_strip_count_type, parse_tablets_per_stripe
from core.master_medicine_service import lookup_master_details, upsert_master_medicine
from core.due_fifo import PURCHASE_ENTRY_PAID_SQL


# ── Helpers ───────────────────────────────────────────────────────────────────

def expiry_to_db(expiry_mmyy: str) -> str:
    """Any expiry text → YYYY-MM-01 for DB storage. Unreadable → ''.

    This used to demand a "/" and return '' without one, which is how stock
    loaded from a phone as "0428" landed with no expiry at all. See
    core/expiry_text.py.
    """
    from core.expiry_text import expiry_to_db as _read

    return _read(expiry_mmyy)


def _expiry_db(value) -> str:
    """Normalise any expiry to the DB's YYYY-MM-DD, whatever form it arrives in.

    The purchase bundle used to send the raw MM/YY straight from the form into
    medicines.expiry_date. Postgres cannot read "07/29" as a date, so it landed
    NULL and the medicine showed a blank expiry. It went unnoticed because the
    uuid-less twin row -- which DID carry a converted date -- was the one the
    inventory screen displayed. Deduplicating the twins exposed it.
    """
    v = str(value or "").strip()
    if not v:
        return ""
    if len(v) >= 10 and v[4] == "-" and v[7] == "-":
        return v[:10]
    try:
        return expiry_to_db(v) or ""
    except Exception:
        return ""


def expiry_to_display(db_expiry: str) -> str:
    """Convert YYYY-MM-01 → MM/YY for display."""
    if db_expiry and '-' in db_expiry:
        parts = db_expiry.split('-')
        if len(parts) >= 2:
            return f"{parts[1]}/{parts[0][2:]}"
    return db_expiry or ''


def _next_id(cur, table: str, id_col: str = 'id') -> int:
    """Return MAX(id)+1 — safe after deletions, never reuses a number."""
    cur.execute(f"SELECT COALESCE(MAX({id_col}),0)+1 FROM {table}")
    return cur.fetchone()[0]


def _purchase_number_int(purchase_no: str | None) -> int | None:
    """Parse stored purchase_no to integer (1, PUR5, APU3 -> 1, 5, 3)."""
    raw = (purchase_no or "").strip()
    if not raw:
        return None
    if raw.isdigit():
        return int(raw)
    upper = raw.upper()
    for prefix in ("PUR", "APU"):
        if upper.startswith(prefix):
            suffix = raw[len(prefix):].lstrip("-_").strip()
            if suffix.isdigit():
                return int(suffix)
    return None


def _allocate_purchase_number(
    conn, prefix: str = "", *, purchase_date=None,
    exclude_purchase_id: int | None = None,
) -> str:
    """Return the next purchase number for the bill's FY (drafts: APU… global)."""
    from core.fy_serial import allocate_purchase_number

    local_no = allocate_purchase_number(
        conn,
        purchase_date,
        prefix=prefix,
        exclude_purchase_id=exclude_purchase_id,
    )
    prefix_u = (prefix or "").upper()
    if prefix_u == "APU":
        return local_no

    try:
        from core.sync_prefs import is_online_mode

        if not is_online_mode():
            return local_no
        from core import server_api as api
        from core.fy_serial import (
            encode_purchase_no,
            fy_start_year_for_date,
        )
        from datetime import date as _date

        token = api.store_token_for_active()
        if not token:
            raise RuntimeError("Online mode requires Satpuda Core Server sign-in for purchase numbers")
        raw = purchase_date or _date.today()
        date_s = raw.isoformat() if hasattr(raw, "isoformat") else str(raw)[:10]
        data = api.allocate_fy(token, "purchases", date_s) or {}
        purchase_no = (data.get("purchase_no") or "").strip()
        if purchase_no:
            return purchase_no
        server_serial = int(data.get("fy_serial") or 0)
        if server_serial > 0:
            fy = int(data.get("fy_start_year") or fy_start_year_for_date(purchase_date))
            return encode_purchase_no(server_serial, fy)
        raise RuntimeError("Server did not return next purchase number")
    except Exception as e:
        from core.sync_prefs import is_online_mode as _online

        if _online():
            raise
        print(f"[PURCHASE] server FY allocate failed, using local: {e}")
        return local_no
    return local_no


def parse_purchase_entry_serial(
    purchase_no: str | None,
    *,
    purchase_id: int | None = None,
) -> str:
    """Display purchase number (1, 2, 3…) — unique per FY, not supplier bill no."""
    from core.fy_serial import display_purchase_no

    n = _purchase_number_int(display_purchase_no(purchase_no))
    if n is not None:
        return str(n)
    raw = (purchase_no or "").strip()
    if raw:
        return raw
    if purchase_id is not None:
        return str(int(purchase_id))
    return ""


def payment_split_from_calc(calc_result: dict) -> tuple[float, float, float]:
    """Return (cash, online, total) from calculator output."""
    cash = round(float(calc_result.get('cash_paid') or 0), 2)
    online = round(float(calc_result.get('online_paid') or 0), 2)
    total = round(float(calc_result.get('amount_paid') or 0), 2)
    if total <= 0:
        total = round(cash + online, 2)
    elif cash <= 0 and online <= 0:
        cash = total
    return cash, online, total


# ── Supplier ──────────────────────────────────────────────────────────────────

def get_or_create_supplier(conn, name, address, phone, gstin, dl_numbers) -> int:
    from core.name_utils import storage_name_from_entry
    from core.sync_prefs import is_online_mode

    name = storage_name_from_entry(name) or (name or '').strip().upper()
    address_s = (address or "").strip()
    phone_s = (phone or "").strip()
    gstin_s = (gstin or "").strip()
    dl_s = (dl_numbers or "").strip()

    if is_online_mode():
        from core.server_crud import upsert_contact_online, allocate_id
        from core.online_catalog import find_supplier_by_name, patch_supplier_cache

        existing = find_supplier_by_name(name)
        if existing and int(existing.get("id") or 0) > 0:
            sid = int(existing["id"])
            need = False
            if address_s and address_s != (existing.get("address") or "").strip():
                need = True
            if phone_s and phone_s != (existing.get("phone") or "").strip():
                need = True
            if gstin_s and gstin_s != (existing.get("gstin") or "").strip():
                need = True
            if dl_s and dl_s != (existing.get("dl_numbers") or "").strip():
                need = True
            merged = {
                "id": sid,
                "local_id": sid,
                "name": existing.get("name") or name,
                "address": address_s or (existing.get("address") or ""),
                "phone": phone_s or (existing.get("phone") or ""),
                "gstin": gstin_s or (existing.get("gstin") or ""),
                "dl_numbers": dl_s or (existing.get("dl_numbers") or ""),
                "total_due": float(existing.get("total_due") or 0),
                "total_credit": float(existing.get("total_credit") or 0),
            }
            if need:
                upsert_contact_online("suppliers", merged)
                patch_supplier_cache(merged)
            return sid
        sid = allocate_id("suppliers")
        created = {
            "id": sid,
            "local_id": sid,
            "name": name,
            "address": address_s,
            "phone": phone_s,
            "gstin": gstin_s,
            "dl_numbers": dl_s,
            "total_due": 0,
            "total_credit": 0,
        }
        upsert_contact_online("suppliers", created)
        patch_supplier_cache(created)
        return sid

    cur = conn.cursor()
    cur.execute("SELECT id FROM suppliers WHERE name=?", (name,))
    row = cur.fetchone()
    if row:
        cur.execute(
            "UPDATE suppliers SET address=?,phone=?,gstin=?,dl_numbers=? WHERE id=?",
            (address_s, phone_s, gstin_s, dl_s, row[0]))
        return row[0]
    cur.execute(
        "INSERT INTO suppliers (name,address,phone,gstin,dl_numbers) VALUES (?,?,?,?,?)",
        (name, address_s, phone_s, gstin_s, dl_s))
    return cur.lastrowid


def compute_paid_via_payments_by_bill(conn, supplier_ids=None) -> dict:
    """
    Map purchase_id -> amount covered by supplier_payments.

    Payments are applied oldest bill first (purchase_date, then id) so credit
    payments clear earlier purchase bills before newer ones.
    """
    cur = conn.cursor()
    supplier_ids = [int(s) for s in (supplier_ids or []) if s]

    if supplier_ids:
        placeholders = ','.join('?' * len(supplier_ids))
        cur.execute(
            f"SELECT supplier_id, COALESCE(SUM(amount),0) FROM supplier_payments "
            f"WHERE COALESCE(deleted,0)=0 "
            f"AND supplier_id IN ({placeholders}) GROUP BY supplier_id",
            supplier_ids,
        )
    else:
        cur.execute(
            "SELECT supplier_id, COALESCE(SUM(amount),0) FROM supplier_payments "
            "WHERE COALESCE(deleted,0)=0 GROUP BY supplier_id"
        )
    pool_remaining = {
        int(sid): round(float(amt or 0), 2) for sid, amt in cur.fetchall()
    }

    if supplier_ids:
        placeholders = ','.join('?' * len(supplier_ids))
        cur.execute(
            f"SELECT id, supplier_id, COALESCE(final_amount,total_amount), "
            f"{PURCHASE_ENTRY_PAID_SQL} "
            f"FROM purchases WHERE supplier_id IN ({placeholders}) "
            f"ORDER BY supplier_id, purchase_date ASC, id ASC",
            supplier_ids,
        )
    else:
        cur.execute(
            "SELECT id, supplier_id, COALESCE(final_amount,total_amount), "
            f"{PURCHASE_ENTRY_PAID_SQL} "
            "FROM purchases "
            "ORDER BY supplier_id, purchase_date ASC, id ASC"
        )

    paid_map = {}
    for bill_id, sid, bill_final, bill_entry_paid in cur.fetchall():
        pool = pool_remaining.get(int(sid), 0.0)
        bill_final = float(bill_final or 0)
        bill_entry_paid = float(bill_entry_paid or 0)
        unpaid = round(max(0.0, bill_final - bill_entry_paid), 2)
        if pool <= 0 or unpaid <= 0:
            paid_map[int(bill_id)] = 0.0
        else:
            applied = min(pool, unpaid)
            paid_map[int(bill_id)] = round(applied, 2)
            pool_remaining[int(sid)] = round(pool - applied, 2)
    return paid_map


def repair_purchase_credit_fields(conn) -> int:
    """
    Recalculate purchase due / credit on every bill.

    Clears false credit when Amount Paid equals Total (leftover previous_credit
    was previously stored as current_credit).
    Returns number of purchases updated.
    """
    from core.calc_engine import calc_purchase_payment

    cur = conn.cursor()
    cur.execute("""
        SELECT id, supplier_id,
               COALESCE(final_amount, total_amount, 0),
               COALESCE(amount_paid_at_entry, amount_paid, 0),
               COALESCE(previous_due, 0), COALESCE(previous_credit, 0)
        FROM purchases
        WHERE COALESCE(deleted,0)=0 AND COALESCE(is_autosave,0)=0
    """)
    supplier_ids = set()
    fixed = 0
    for row in cur.fetchall():
        pid, sid, final_amount, paid, prev_due, prev_credit = row
        pay = calc_purchase_payment(final_amount, paid, prev_due, prev_credit)
        bill_cleared = 1 if pay['due_amount'] < 0.01 else 0
        cur.execute("""
            UPDATE purchases
            SET due=?, due_amount=?, current_credit=?, credit_amount=?,
                total_due=?, bill_cleared=?,
                need_to_pay=?
            WHERE id=?
        """, (
            pay['due_amount'], pay['due_amount'],
            pay['credit_amount'], pay['credit_amount'],
            pay['total_due'], bill_cleared,
            pay['net_amount'], pid,
        ))
        if sid:
            supplier_ids.add(int(sid))
        fixed += 1
    conn.commit()
    for sid in supplier_ids:
        try:
            recalculate_supplier_due(conn, sid)
        except Exception:
            pass
    return fixed


def recalculate_supplier_due(conn, supplier_id: int, *, commit: bool = True) -> tuple:
    """
    Single source of truth for supplier balance.

    net = SUM(purchases.total_amount)
        - SUM(purchases.amount_paid_at_entry)   ← entry-time only, never mutated
        - SUM(supplier_payments.amount)
        - SUM(purchase_returns.refund_amount)

    net > 0  → total_due = net,  total_credit = 0
    net < 0  → total_due = 0,    total_credit = abs(net)

    Updates suppliers.total_due / total_credit.
    Also sets purchases.account_cleared per-bill.
    Returns (total_due, total_credit).
    """
    cur = conn.cursor()

    cur.execute(
        "SELECT COALESCE(SUM(COALESCE(final_amount, total_amount)),0), "
        f"COALESCE(SUM({PURCHASE_ENTRY_PAID_SQL}),0) "
        "FROM purchases WHERE supplier_id=? "
        "AND COALESCE(deleted,0)=0 AND COALESCE(is_autosave,0)=0",
        (supplier_id,))
    row = cur.fetchone()
    total_purchased  = round(float(row[0] or 0), 2)
    total_entry_paid = round(float(row[1] or 0), 2)

    try:
        cur.execute(
            "SELECT COALESCE(SUM(amount),0) FROM supplier_payments "
            "WHERE supplier_id=? AND COALESCE(deleted,0)=0",
            (supplier_id,))
        total_payments = round(float(cur.fetchone()[0] or 0), 2)
    except Exception:
        total_payments = 0.0

    try:
        cur.execute("""
            SELECT COALESCE(SUM(pr.refund_amount),0)
            FROM purchase_returns pr
            LEFT JOIN purchases p ON pr.purchase_id=p.id
            WHERE COALESCE(pr.deleted,0)=0
              AND (
                pr.supplier_id=?
                OR (p.id IS NOT NULL AND p.supplier_id=?
                    AND COALESCE(p.deleted,0)=0)
              )
        """, (supplier_id, supplier_id))
        total_returns = round(float(cur.fetchone()[0] or 0), 2)
    except Exception:
        total_returns = 0.0

    net = round(total_purchased - total_entry_paid - total_payments - total_returns, 2)
    total_due    = round(max(0.0,  net), 2)
    total_credit = round(max(0.0, -net), 2)

    cur.execute(
        "UPDATE suppliers SET total_due=?, total_credit=? WHERE id=?",
        (total_due, total_credit, supplier_id))

    # Oldest-first clear of every purchase for this supplier.
    from core.due_fifo import cascade_purchases_fifo

    cur.execute(
        f"SELECT id, COALESCE(final_amount, total_amount), {PURCHASE_ENTRY_PAID_SQL} FROM purchases "
        "WHERE supplier_id=? AND COALESCE(deleted,0)=0 AND COALESCE(is_autosave,0)=0 "
        "ORDER BY purchase_date ASC, id ASC",
        (supplier_id,))
    bills = [(int(r[0]), float(r[1] or 0), float(r[2] or 0)) for r in cur.fetchall()]
    for bill_id, remaining, cleared in cascade_purchases_fifo(
        bills, total_payments, total_returns,
    ):
        cur.execute(
            "UPDATE purchases SET account_cleared=? WHERE id=?",
            (cleared, bill_id),
        )

    if commit:
        conn.commit()
    print(f"[SUPPLIER] id={supplier_id} purchased={total_purchased:.2f} "
          f"entry_paid={total_entry_paid:.2f} payments={total_payments:.2f} "
          f"returns={total_returns:.2f} => due={total_due:.2f} credit={total_credit:.2f}")
    return total_due, total_credit


def get_supplier_due(
    conn, supplier_name: str, *, force_refresh: bool = False
) -> tuple:
    """
    Return (total_due, total_credit) for a supplier by name.
    Reads from suppliers.total_due / total_credit (maintained by recalculate_supplier_due).
    Falls back to dynamic calculation if columns not yet populated.
    Online: optionally force-refresh the warm catalog (Classic load_supplier_details).
    """
    try:
        from core.sync_prefs import is_online_mode

        if is_online_mode():
            from core.online_catalog import find_supplier_by_name, find_supplier_by_id

            found = find_supplier_by_name(
                supplier_name, force=bool(force_refresh)
            )
            if not found:
                try:
                    sid = int(supplier_name)
                except (TypeError, ValueError):
                    sid = 0
                if sid > 0:
                    found = find_supplier_by_id(sid)
            if found:
                due = round(float(found.get("total_due") or 0), 2)
                credit = round(float(found.get("total_credit") or 0), 2)
                try:
                    from core.desktop_settings_service import online_supplier_remaining_due
                    from core.online_catalog import patch_supplier_cache

                    sid = int(found.get("id") or found.get("local_id") or 0)
                    live = online_supplier_remaining_due(sid, str(found.get("name") or supplier_name))
                    if live is not None:
                        due = round(max(0.0, float(live)), 2)
                        if due > 0.01:
                            credit = 0.0
                        merged = dict(found)
                        merged["total_due"] = due
                        merged["total_credit"] = credit
                        patch_supplier_cache(merged)
                except Exception:
                    pass
                return (due, credit)
            return (0.0, 0.0)
    except Exception:
        pass

    cur = conn.cursor()
    cur.execute(
        "SELECT id, COALESCE(total_due,0), COALESCE(total_credit,0) "
        "FROM suppliers WHERE name=? LIMIT 1",
        (supplier_name,))
    row = cur.fetchone()
    if not row:
        return (0.0, 0.0)

    supplier_id, cached_due, cached_credit = row[0], float(row[1]), float(row[2])

    # If both are 0 and purchases exist, recalculate (first-run / migration case)
    if cached_due == 0.0 and cached_credit == 0.0:
        cur.execute("SELECT COUNT(*) FROM purchases WHERE supplier_id=?", (supplier_id,))
        if cur.fetchone()[0] > 0:
            return recalculate_supplier_due(conn, supplier_id)

    return (cached_due, cached_credit)


def refresh_inventory_if_loaded(parent_widget) -> None:
    """Reload inventory tree and search dropdown after stock changes."""
    try:
        from core.page_refresh import refresh_after_purchase
        refresh_after_purchase(parent_widget)
    except Exception:
        pass


# ── Medicine ──────────────────────────────────────────────────────────────────

def _norm_medicine_batch(batch) -> str:
    import re
    return re.sub(r"\s+", "", str(batch or "").strip().upper())


def _medicine_match_key(name, batch) -> tuple[str, str]:
    from core.name_utils import normalize_medicine_name

    return (
        normalize_medicine_name(name).strip().lower(),
        _norm_medicine_batch(batch),
    )


def _pick_canonical_medicine_row(rows: list) -> dict | None:
    """Prefer stocked row, then lowest id (stable original)."""
    cands = []
    for d in rows or []:
        if not isinstance(d, dict) or d.get("deleted"):
            continue
        try:
            mid = int(d.get("id") or d.get("local_id") or 0)
        except (TypeError, ValueError):
            mid = 0
        if mid <= 0:
            continue
        cands.append(d)
    if not cands:
        return None
    cands.sort(
        key=lambda d: (
            0 if float(d.get("stock_qty") or 0) > 0.01 else 1,
            0 if not d.get("is_hidden") else 1,
            int(d.get("id") or d.get("local_id") or 0),
        )
    )
    return cands[0]


def _merged_stock_for_batch_group(rows: list) -> float:
    """Clone rows (same stock twice) → keep once; split stocks → sum."""
    stocks = []
    for d in rows or []:
        if not isinstance(d, dict) or d.get("deleted"):
            continue
        try:
            s = float(d.get("stock_qty") or 0)
        except (TypeError, ValueError):
            s = 0.0
        if s > 0.01:
            stocks.append(round(s, 2))
    if not stocks:
        return 0.0
    uniq = set(stocks)
    if len(uniq) <= 1:
        return float(max(stocks))
    return float(sum(stocks))


def find_medicine_id(conn, name, batch, expiry_mmyy):
    """Read-only lookup — never locks for write. Used by bill import matching.
    Returns medicine id or None.

    Identity is name + batch (expiry is an attribute, not part of the key).
    """
    from core.name_utils import normalize_medicine_name

    name = normalize_medicine_name(name)
    db_expiry = expiry_to_db(expiry_mmyy)
    batch_key = _norm_medicine_batch(batch)
    try:
        from core.sync_prefs import is_online_mode
        if is_online_mode():
            from core.online_catalog import medicines_for_name_match

            name_l = name.lower()
            hits = []
            for d in medicines_for_name_match(name_l) or []:
                if _norm_medicine_batch(d.get("batch_no") or d.get("batch")) != batch_key:
                    continue
                hits.append(d)
            picked = _pick_canonical_medicine_row(hits)
            if picked:
                mid = int(picked.get("id") or picked.get("local_id") or 0)
                if mid > 0:
                    return mid
            return None
    except Exception:
        pass

    if conn is None:
        return None
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT id FROM medicines
            WHERE LOWER(TRIM(name)) = LOWER(TRIM(?))
              AND UPPER(REPLACE(TRIM(COALESCE(batch_no,'')), ' ', '')) = ?
              AND COALESCE(deleted, 0) = 0
            ORDER BY CASE WHEN COALESCE(stock_qty,0) > 0 THEN 0 ELSE 1 END,
                     CASE WHEN COALESCE(is_hidden,0) = 0 THEN 0 ELSE 1 END,
                     id ASC
            LIMIT 1
            """,
            (name, batch_key),
        )
        row = cur.fetchone()
        return int(row[0]) if row else None
    except Exception:
        return None


def get_or_create_medicine(conn, name, med_type, batch, expiry_mmyy,
                            gst_pct, mrp, rate, manufacturer,
                            hsn_code, schedule, content_drug) -> int:
    from core.db_utils import db_retry
    from core.name_utils import normalize_medicine_name
    from core.sync_prefs import is_online_mode

    name = normalize_medicine_name(name)
    db_expiry = expiry_to_db(expiry_mmyy)
    batch_key = _norm_medicine_batch(batch)

    if is_online_mode():
        from core.server_crud import allocate_id
        from core.online_catalog import medicines_for_name_match, patch_docs

        name_l = name.lower()
        hsn_in = (hsn_code or "").strip()
        hits = []
        for d in medicines_for_name_match(name) or []:
            if (
                str(d.get("name") or "").strip().lower() == name_l
                and _norm_medicine_batch(d.get("batch_no") or d.get("batch")) == batch_key
            ):
                hits.append(d)
        picked = _pick_canonical_medicine_row(hits)
        if picked:
            mid = int(picked.get("id") or picked.get("local_id") or 0)
            if mid:
                # Unhide / refresh expiry on restock path — purchase flush owns stock.
                if picked.get("is_hidden") or (
                    db_expiry and str(picked.get("expiry_date") or "")[:10] != str(db_expiry)[:10]
                ):
                    try:
                        doc = dict(picked)
                        doc["id"] = mid
                        doc["local_id"] = mid
                        doc["is_hidden"] = False
                        if db_expiry:
                            doc["expiry_date"] = db_expiry
                        doc["type"] = med_type or doc.get("type") or ""
                        doc["mrp"] = mrp
                        doc["rate"] = rate
                        doc["gst_percent"] = gst_pct
                        doc["manufacturer"] = manufacturer or doc.get("manufacturer") or ""
                        doc["hsn_code"] = hsn_in or doc.get("hsn_code") or ""
                        doc["schedule"] = schedule or doc.get("schedule") or ""
                        doc["content_drug"] = content_drug or doc.get("content_drug") or ""
                        from core.server_crud import upsert_medicine_online, bump_meta
                        upsert_medicine_online(bump_meta(doc))
                        patch_docs("medicines", [doc])
                    except Exception:
                        pass
                return mid
        mid = allocate_id("medicines")
        doc = {
            "id": mid,
            "local_id": mid,
            "name": name,
            "type": med_type or "",
            "batch_no": batch or "",
            "expiry_date": db_expiry or "",
            "gst_percent": gst_pct,
            "mrp": mrp,
            "rate": rate,
            "manufacturer": manufacturer or "",
            "hsn_code": hsn_in,
            "schedule": schedule or "",
            "content_drug": content_drug or "",
            "stock_qty": 0,
            "unit": "1",
            "is_hidden": False,
        }
        # Must exist on the server before purchase save — allocate+local cache alone
        # caused HTTP 404 medicines/<id> not found on imported Online bills.
        try:
            from core.server_crud import upsert_medicine_online

            upsert_medicine_online(doc)
        except Exception:
            pass
        try:
            patch_docs("medicines", [doc])
        except Exception:
            pass
        return mid

    def _work():
        cur = conn.cursor()
        # Schema includes content_drug at create time — do not ALTER on every
        # import row (that takes an exclusive lock and causes "database is locked").
        cur.execute(
            """
            SELECT id, name FROM medicines
            WHERE LOWER(TRIM(name)) = LOWER(TRIM(?))
              AND UPPER(REPLACE(TRIM(COALESCE(batch_no,'')), ' ', '')) = ?
              AND COALESCE(deleted, 0) = 0
            ORDER BY CASE WHEN COALESCE(stock_qty,0) > 0 THEN 0 ELSE 1 END,
                     CASE WHEN COALESCE(is_hidden,0) = 0 THEN 0 ELSE 1 END,
                     id ASC
            LIMIT 1
            """,
            (name, batch_key),
        )
        row = cur.fetchone()
        if row:
            med_id, stored_name = row[0], row[1]
            use_name = stored_name.strip() if stored_name and stored_name.strip() else name
            content_to_save = (content_drug or '').strip()
            if not content_to_save:
                try:
                    cur.execute(
                        "SELECT COALESCE(content_drug, '') FROM medicines WHERE id=?",
                        (med_id,),
                    )
                    existing_content = (cur.fetchone() or ('',))[0] or ''
                    if existing_content.strip():
                        content_to_save = existing_content.strip()
                except sqlite3.OperationalError:
                    content_to_save = ''
            cur.execute(
                """
                UPDATE medicines
                SET type=?,
                    manufacturer=?,
                    hsn_code=?,
                    schedule=?,
                    content_drug=?,
                    rate=?,
                    mrp=?,
                    gst_percent=?,
                    expiry_date=COALESCE(NULLIF(?, ''), expiry_date),
                    is_hidden=0
                WHERE id=?
                """,
                (
                    med_type, manufacturer, hsn_code, schedule, content_to_save,
                    rate, mrp, gst_pct, db_expiry or '', med_id,
                ),
            )
            if load_app_mode() == 'medical':
                try:
                    upsert_master_medicine(
                        name=use_name,
                        manufacturer=manufacturer,
                        mrp=mrp,
                        content_drug=content_to_save,
                        med_type=med_type,
                        schedule=schedule or '',
                        hsn_code=hsn_code or '',
                        gst_percent=float(gst_pct or 0),
                    )
                except Exception:
                    pass
            return med_id

        cur.execute("""
            INSERT INTO medicines
                (name,type,batch_no,expiry_date,gst_percent,mrp,rate,
                 manufacturer,hsn_code,schedule,content_drug,location,stock_qty)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,'',0)
        """, (name, med_type, batch, db_expiry, gst_pct, mrp, rate,
              manufacturer, hsn_code, schedule, content_drug))
        med_id = cur.lastrowid
        if load_app_mode() == 'medical':
            try:
                upsert_master_medicine(
                    name=name,
                    manufacturer=manufacturer,
                    mrp=mrp,
                    content_drug=content_drug,
                    med_type=med_type,
                    schedule=schedule or '',
                    hsn_code=hsn_code or '',
                    gst_percent=float(gst_pct or 0),
                )
            except Exception:
                pass
        return med_id

    return db_retry(_work)


def hide_online_zero_stock_duplicates() -> int:
    """Merges nothing by itself any more; returns 0.

    It ran after every Online purchase save and edit, and Inventory ran it every 15 minutes,
    merging each name+batch group into whichever row the code liked best. A merge moves
    stock, deletes a row and pushes every bill on that row again, and a sale pushed again has
    the server recompute that customer's balance. The store audit asked for the owner to pick
    the row to keep first, so only merge_online_medicine_batch_duplicates(keep_ids=...) merges.
    """
    return 0




def cleanup_orphan_import_medicines_online(*, max_check: int = 80, force: bool = False) -> int:
    """Safe no-op for auto inventory refresh.

    Earlier versions soft-deleted medicines that lacked a supplier label on the
    inventory payload even when purchase bills existed (supplier map miss / name
    mismatch). That hid real purchased stock (e.g. GLIZID) from Inventory.

    Import stubs are prevented by find-only import; use
    repair_purchased_medicines_online() to restore ledger stock instead.
    """
    return 0


def repair_purchased_medicines_online(*, limit_purchases: int = 5000) -> dict:
    """Restore medicines referenced by Online purchases: un-delete, unhide, rebuild stock.

    Stock = sum(purchase tablet/qty increases) - sum(sales qty) for each medicine_id.
    Fixes bills that appear in Purchase History but not Inventory.
    """
    from collections import defaultdict

    from core.layout_config import is_strip_count_type, parse_tablets_per_stripe
    from core.online_catalog import invalidate, medicine_by_id
    from core.server_crud import upsert_medicine_online, bump_meta, get_doc
    from core import store_query_client as sq

    purch_stock: dict[int, float] = defaultdict(float)
    sale_stock: dict[int, float] = defaultdict(float)
    meta_by_mid: dict[int, dict] = {}

    def _tps_from_item(it: dict) -> int:
        med_type = str(it.get("type") or "")
        if not is_strip_count_type(med_type):
            return 1
        for key in ("tablets_per_stripe", "tps", "unit", "pack", "quantity_value"):
            raw = it.get(key)
            if raw in (None, ""):
                continue
            try:
                if key in ("tablets_per_stripe", "tps"):
                    n = int(float(raw))
                else:
                    try:
                        from core.bill_import_normalize import tablets_per_strip_from_pack
                        n = int(tablets_per_strip_from_pack(str(raw)))
                    except Exception:
                        n = int(parse_tablets_per_stripe(str(raw)))
                if n > 0:
                    return n
            except (TypeError, ValueError):
                continue
        return 1

    def _purchase_units(it: dict) -> float:
        it2 = dict(it)
        if is_strip_count_type(str(it2.get("type") or "")):
            it2["tablets_per_stripe"] = _tps_from_item(it2)
        return float(_get_stock_increase(it2) or 0)

    # --- Purchases ---
    offset = 0
    scanned_purchases = 0
    while scanned_purchases < limit_purchases:
        data = sq.list_purchases(
            from_date="2000-04-01",
            to_date="2099-03-31",
            limit=200,
            include_total=False,
        ) if offset == 0 else None
        # list_purchases has no offset in client — paginate via repeated calls is limited.
        # Pull once with high limit.
        break
    try:
        data = sq.list_purchases(
            from_date="2000-04-01",
            to_date="2099-03-31",
            limit=min(limit_purchases, 5000),
            include_total=False,
        ) or {}
    except Exception:
        data = {}
    bills = list(data.get("rows") or [])
    for b in bills:
        try:
            pid = int(b.get("id") or 0)
        except (TypeError, ValueError):
            continue
        if pid <= 0:
            continue
        try:
            detail = sq.get_purchase(pid) or {}
        except Exception:
            continue
        scanned_purchases += 1
        for it in detail.get("items") or []:
            if not isinstance(it, dict) or it.get("deleted"):
                continue
            try:
                mid = int(it.get("medicine_id") or it.get("local_id") or 0)
            except (TypeError, ValueError):
                mid = 0
            if mid <= 0:
                continue
            units = _purchase_units(it)
            purch_stock[mid] += units
            cur = meta_by_mid.get(mid) or {}
            name = str(it.get("name") or it.get("medicine_name") or cur.get("name") or "")
            batch = str(it.get("batch_no") or it.get("batch") or cur.get("batch_no") or "")
            med_type = str(it.get("type") or cur.get("type") or "")
            tps = _tps_from_item(it)
            unit = str(it.get("unit") or "")
            if is_strip_count_type(med_type) and tps > 1:
                unit = str(tps)
            elif not unit:
                unit = str(tps) if is_strip_count_type(med_type) else "1"
            meta_by_mid[mid] = {
                "name": name or cur.get("name") or "",
                "batch_no": batch or cur.get("batch_no") or "",
                "type": med_type or cur.get("type") or "",
                "unit": unit or cur.get("unit") or "1",
                "expiry_date": str(
                    it.get("expiry_date") or it.get("expiry") or cur.get("expiry_date") or ""
                ),
                "mrp": float(it.get("mrp") or cur.get("mrp") or 0),
                "rate": float(it.get("rate") or cur.get("rate") or 0),
                "manufacturer": str(it.get("manufacturer") or cur.get("manufacturer") or ""),
                "schedule": str(it.get("schedule") or cur.get("schedule") or ""),
                "hsn_code": str(it.get("hsn_code") or cur.get("hsn_code") or ""),
            }

    # --- Sales (subtract) ---
    try:
        sdata = sq.list_sales(
            from_date="2000-04-01",
            to_date="2099-03-31",
            limit=5000,
            include_total=False,
        ) or {}
    except Exception:
        sdata = {}
    for b in list(sdata.get("rows") or [])[:2000]:
        try:
            sid = int(b.get("id") or 0)
        except (TypeError, ValueError):
            continue
        if sid <= 0:
            continue
        try:
            detail = sq.get_sale(sid) or {}
        except Exception:
            continue
        if detail.get("is_autosave") or detail.get("deleted"):
            continue
        for it in detail.get("items") or []:
            if not isinstance(it, dict) or it.get("deleted"):
                continue
            try:
                mid = int(it.get("medicine_id") or it.get("local_id") or 0)
            except (TypeError, ValueError):
                mid = 0
            if mid <= 0:
                continue
            sale_stock[mid] += float(it.get("qty") or 0)

    restored = 0
    touched = sorted(set(purch_stock) | set(sale_stock) | set(meta_by_mid))
    for mid in touched:
        if mid not in purch_stock and mid not in meta_by_mid:
            continue
        existing = medicine_by_id(mid) or get_doc("medicines", mid) or {}
        meta = meta_by_mid.get(mid) or {}
        stock = max(0.0, float(purch_stock.get(mid, 0.0)) - float(sale_stock.get(mid, 0.0)))
        doc = bump_meta(dict(existing) if existing else {})
        doc["id"] = mid
        doc["local_id"] = mid
        doc["name"] = meta.get("name") or doc.get("name") or f"Medicine {mid}"
        doc["batch_no"] = meta.get("batch_no") or doc.get("batch_no") or ""
        doc["type"] = meta.get("type") or doc.get("type") or ""
        doc["unit"] = meta.get("unit") or doc.get("unit") or "1"
        if meta.get("expiry_date"):
            doc["expiry_date"] = meta["expiry_date"]
        if meta.get("mrp"):
            doc["mrp"] = meta["mrp"]
        if meta.get("rate"):
            doc["rate"] = meta["rate"]
        if meta.get("manufacturer"):
            doc["manufacturer"] = meta["manufacturer"]
        if meta.get("schedule"):
            doc["schedule"] = meta["schedule"]
        if meta.get("hsn_code"):
            doc["hsn_code"] = meta["hsn_code"]
        doc["stock_qty"] = stock
        doc["is_hidden"] = False
        doc["deleted"] = False
        try:
            upsert_medicine_online(doc)
            restored += 1
        except Exception:
            continue

    try:
        invalidate("medicines")
    except Exception:
        pass
    return {
        "ok": True,
        "purchases_scanned": scanned_purchases,
        "medicines_repaired": restored,
        "medicine_ids": touched[:50],
    }



def _is_deleted_row(row) -> bool:
    raw = (row or {}).get("deleted")
    if isinstance(raw, str):
        return raw.strip().lower() in ("1", "t", "true", "yes")
    return bool(raw)


def _repoint_medicine_lines(from_id: int, to_id: int, batch: str) -> bool:
    """Point every sale and purchase line on ``from_id`` at ``to_id``; False if any could not be.

    Each bill goes back whole, from its stored document, with only the medicine id of those
    lines changed -- no stock movement and no header field is touched. Bills are found by the
    batch (the server filters lines by it); a line with no batch cannot be found, and a group
    without a batch is never merged.
    """
    from core import store_query_client as sq
    from core.server_crud import bump_meta, get_doc, push_bundle

    for collection, lister in (("sales", sq.list_sales), ("purchases", sq.list_purchases)):
        try:
            found = lister(
                from_date="2000-01-01",
                to_date="2099-12-31",
                batch=batch,
                limit=5000,
                include_total=False,
            ) or {}
        except Exception as exc:
            print(f"[merge] could not list {collection} on medicine {from_id}: {exc}")
            return False
        for row in found.get("rows") or []:
            try:
                rid = int((row or {}).get("id") or (row or {}).get("local_id") or 0)
            except (TypeError, ValueError):
                continue
            if rid <= 0:
                continue
            doc = get_doc(collection, rid)
            if not doc or not isinstance(doc.get("items"), list):
                return False
            items = [dict(it) for it in doc["items"] if isinstance(it, dict)]
            hits = 0
            for it in items:
                try:
                    if int(it.get("medicine_id") or 0) == int(from_id):
                        it["medicine_id"] = int(to_id)
                        hits += 1
                except (TypeError, ValueError):
                    continue
            if not hits:
                continue
            moved = bump_meta(dict(doc))
            moved["id"] = rid
            moved["local_id"] = rid
            moved["items"] = items
            try:
                push_bundle({collection: [moved]})
            except Exception as exc:
                print(f"[merge] could not move {collection}/{rid} to medicine {to_id}: {exc}")
                return False
    return True


def merge_online_medicine_batch_duplicates(keep_ids=()) -> int:
    """Merge duplicate Online medicines that share the same name + batch, as the owner chose.

    ``keep_ids`` are the rows the owner picked to keep. Only a group in which exactly one row
    was picked is merged, into that row; every other group is left as it is, and with no pick
    nothing is read or written. The merge changes store data -- stock, a deleted row, every
    bill on that row pushed again, and a sale pushed again has the server recompute that
    customer's balance -- so it never runs by itself and never chooses the row itself.

    - Identical stock on two rows (dual-device clone) → keep stock once.
    - Different stocks (split purchases) → sum into the row that stays.
    - The row that goes: its sale and purchase lines are pointed at the row that stays,
      then it is hidden and soft-deleted.

    Stock moves by a pair of stock movements -- off the row that goes, onto the row that
    stays -- never by writing figures with no record: store 127 lost 2,144 units on 36 rows
    that way. A row whose lines cannot all be moved is left as it is. Each row is written
    under its own id and its own stored client_uuid, never the name+batch one: the server
    landed such a write on the DELETED row that owns that uuid, so four deleted rows were
    written 472 times while their live twins were never merged.
    """
    from core.online_catalog import invalidate as catalog_invalidate
    from core.server_crud import bump_meta, get_doc, upsert_docs, _device_id
    from core import store_query_client as sq
    from collections import defaultdict

    wanted: set[int] = set()
    for raw in keep_ids or ():
        try:
            if int(raw or 0) > 0:
                wanted.add(int(raw))
        except (TypeError, ValueError):
            continue
    if not wanted:
        return 0

    rows: list[dict] = []
    try:
        offset = 0
        while offset < 50000:
            data = sq.list_inventory(
                q="", limit=500, offset=offset, include_total=False, hidden="all",
            ) or {}
            raw = data.get("rows") or []
            if not raw:
                break
            rows.extend([r for r in raw if isinstance(r, dict)])
            if len(raw) < 500:
                break
            offset += 500
    except Exception:
        try:
            from core.online_catalog import _pull_sync_pages
            rows = [r for r in _pull_sync_pages("medicines") if isinstance(r, dict)]
        except Exception:
            rows = []

    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for m in rows:
        if _is_deleted_row(m):
            continue
        name_l, batch_k = _medicine_match_key(m.get("name"), m.get("batch_no") or m.get("batch"))
        if not name_l or not batch_k:
            continue
        groups[(name_l, batch_k)].append(m)

    def _row_id(row) -> int:
        try:
            return int((row or {}).get("id") or (row or {}).get("local_id") or 0)
        except (TypeError, ValueError):
            return 0

    fixed = 0
    for _key, group in groups.items():
        if len(group) < 2:
            continue
        if not any(_row_id(g) in wanted for g in group):
            continue  # the owner has not picked a row to keep in this group
        # The server's own copies, not the list rows: a row the list still shows may already
        # be deleted there, and a deleted row is never written again.
        held_rows = []
        for g in group:
            try:
                gid = int(g.get("id") or g.get("local_id") or 0)
            except (TypeError, ValueError):
                continue
            held = get_doc("medicines", gid) if gid > 0 else None
            if held and not _is_deleted_row(held):
                held = dict(held)
                held["id"] = gid
                held_rows.append(held)
        if len(held_rows) < 2:
            continue
        picked = [h for h in held_rows if _row_id(h) in wanted]
        if len(picked) != 1:
            # Two picks in one group, or a pick the server no longer holds live: leave it.
            continue
        keep = picked[0]
        keep_id = int(keep.get("id") or 0)
        if keep_id <= 0:
            continue
        keep_stock = float(keep.get("stock_qty") or 0)
        still_to_move = max(0.0, _merged_stock_for_batch_group(held_rows) - keep_stock)
        batch = str(keep.get("batch_no") or keep.get("batch") or "").strip()
        for loser in held_rows:
            lid = int(loser.get("id") or 0)
            if lid <= 0 or lid == keep_id:
                continue
            if not _repoint_medicine_lines(lid, keep_id, batch):
                # Its lines could not all be moved: leave it, stock and all, for a later pass.
                continue
            gone = float(loser.get("stock_qty") or 0)
            arriving = min(max(0.0, gone), still_to_move)
            out_doc = bump_meta(dict(loser))
            out_doc.update({"id": lid, "local_id": lid, "stock_qty": 0.0,
                            "is_hidden": True, "deleted": True})
            out_doc["client_uuid"] = str(loser.get("client_uuid") or "")
            keep_doc = bump_meta(dict(keep))
            keep_doc.update({"id": keep_id, "local_id": keep_id,
                             "stock_qty": keep_stock + arriving, "is_hidden": False,
                             "deleted": False})
            keep_doc["client_uuid"] = str(keep.get("client_uuid") or "")
            if round(gone):
                out_doc["stock_ops"] = [{
                    "op_uuid": f"merge:{lid}:into:{keep_id}:out:v1",
                    "op": "merge_out",
                    "qty_delta": -int(round(gone)),
                    "medicine_id": lid,
                    "ref_collection": "medicines",
                    "ref_id": keep_id,
                    "device_id": _device_id(),
                }]
            if round(arriving):
                keep_doc["stock_ops"] = [{
                    "op_uuid": f"merge:{lid}:into:{keep_id}:in:v1",
                    "op": "merge_in",
                    "qty_delta": int(round(arriving)),
                    "medicine_id": keep_id,
                    "ref_collection": "medicines",
                    "ref_id": lid,
                    "device_id": _device_id(),
                }]
            docs = [out_doc] + ([keep_doc] if keep_doc.get("stock_ops") else [])
            try:
                upsert_docs("medicines", docs)
            except Exception as exc:
                print(f"[merge] medicine {lid} into {keep_id} not written: {exc}")
                continue
            fixed += 1
            still_to_move -= arriving
            keep_stock += arriving
            keep["stock_qty"] = keep_stock
            keep["version"] = int(keep_doc.get("version") or 0)
    try:
        catalog_invalidate()
    except Exception:
        pass
    return fixed


def lookup_last_purchase_details(conn, medicine_name: str) -> dict:
    """Return medicine fields from the most recent saved purchase line for this name."""
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT pi.rate, pi.mrp,
                   COALESCE(pi.gst_pct, pi.gst_value, 0),
                   COALESCE(pi.manufacturer, ''), COALESCE(pi.hsn_code, ''),
                   COALESCE(pi.type, ''), COALESCE(pi.schedule, ''),
                   COALESCE(m.content_drug, ''),
                   COALESCE(pi.batch_no, ''), pi.expiry_date,
                   COALESCE(pi.discount_pct, 0), COALESCE(m.unit, ''),
                   COALESCE(p.bill_number, ''), p.purchase_date
            FROM purchase_items pi
            JOIN medicines m ON pi.medicine_id = m.id
            JOIN purchases p ON pi.purchase_id = p.id
            WHERE LOWER(TRIM(m.name)) = LOWER(TRIM(?))
            ORDER BY p.purchase_date DESC, p.id DESC, pi.id DESC
            LIMIT 1
        """, (medicine_name,))
    except sqlite3.Error:
        return {}
    row = cur.fetchone()
    if not row:
        return {}
    return {
        'type': row[5] or '',
        'manufacturer': row[3] or '',
        'hsn_code': row[4] or '',
        'gst_percent': row[2] or 0,
        'mrp': row[1] or 0,
        'rate': row[0] if row[0] is not None else 0,
        'schedule': row[6] or '',
        'content_drug': row[7] or '',
        'batch_no': row[8] or '',
        'expiry_date': row[9] or '',
        'discount_pct': row[10] or 0,
        'unit': row[11] or '',
        'bill_number': row[12] or '',
        'purchase_date': row[13] or '',
    }


def lookup_supplier_last_purchase_rate(
    conn, medicine_name: str, supplier_name: str,
) -> dict:
    """Last purchase rate for this medicine from a specific supplier."""
    if not (medicine_name or '').strip() or not (supplier_name or '').strip():
        return {}
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT pi.rate, COALESCE(p.bill_number, ''), p.purchase_date
            FROM purchase_items pi
            JOIN medicines m ON pi.medicine_id = m.id
            JOIN purchases p ON pi.purchase_id = p.id
            JOIN suppliers s ON p.supplier_id = s.id
            WHERE m.name = ? AND s.name = ?
            ORDER BY p.purchase_date DESC, p.id DESC, pi.id DESC
            LIMIT 1
        """, (medicine_name.strip(), supplier_name.strip()))
    except sqlite3.Error:
        return {}
    row = cur.fetchone()
    if not row or row[0] is None:
        return {}
    return {
        'rate': float(row[0] or 0),
        'bill_number': row[1] or '',
        'purchase_date': str(row[2] or ''),
    }


def lookup_medicine_details(conn, medicine_name: str) -> dict:
    try:
        from core.sync_prefs import is_online_mode
        if is_online_mode():
            from core.online_catalog import medicines_for_name
            from core.name_utils import normalize_medicine_name

            want = normalize_medicine_name(medicine_name)
            for d in medicines_for_name(want) or []:
                return {
                    "type": d.get("type") or "",
                    "manufacturer": d.get("manufacturer") or "",
                    "hsn_code": d.get("hsn_code") or "",
                    "gst_percent": float(d.get("gst_percent") or 0),
                    "mrp": float(d.get("mrp") or 0),
                    "rate": float(d.get("rate") or 0),
                    "schedule": d.get("schedule") or "",
                    "content_drug": d.get("content_drug") or "",
                    "batch_no": d.get("batch_no") or "",
                    "expiry_date": d.get("expiry_date") or "",
                    "unit": d.get("unit") or "",
                }
            if load_app_mode() == "medical":
                try:
                    m = lookup_master_details(medicine_name)
                    if m:
                        return {
                            "type": m.get("type", "") or "",
                            "manufacturer": m.get("manufacturer", "") or "",
                            "hsn_code": m.get("hsn_code", "") or "",
                            "gst_percent": float(m.get("gst_percent", 0) or 0),
                            "mrp": float(m.get("mrp", 0) or 0),
                            "rate": 0,
                            "schedule": m.get("schedule", "") or "",
                            "content_drug": m.get("content_drug", "") or "",
                            "unit": m.get("pack_size", "") or "",
                        }
                except Exception:
                    pass
            return {}
    except Exception:
        pass

    cur = conn.cursor()
    cur.execute("PRAGMA table_info(medicines)")
    cols = [c[1] for c in cur.fetchall()]
    has_cd = 'content_drug' in cols

    result = {}

    if has_cd:
        cur.execute("""
            SELECT type,manufacturer,hsn_code,gst_percent,mrp,rate,schedule,content_drug,
                   batch_no, expiry_date, unit
            FROM medicines
            WHERE LOWER(TRIM(name)) = LOWER(TRIM(?))
            ORDER BY id DESC LIMIT 1
        """, (medicine_name,))
    else:
        cur.execute("""
            SELECT type,manufacturer,hsn_code,gst_percent,mrp,rate,schedule,
                   batch_no, expiry_date, unit
            FROM medicines
            WHERE LOWER(TRIM(name)) = LOWER(TRIM(?))
            ORDER BY id DESC LIMIT 1
        """, (medicine_name,))
    row = cur.fetchone()
    if row:
        if has_cd:
            result = {
                'type': row[0] or '', 'manufacturer': row[1] or '',
                'hsn_code': row[2] or '', 'gst_percent': row[3] or 0,
                'mrp': row[4] or 0, 'rate': row[5] or 0,
                'schedule': row[6] or '',
                'content_drug': row[7] or '',
                'batch_no': row[8] or '',
                'expiry_date': row[9] or '',
                'unit': row[10] or '',
            }
        else:
            result = {
                'type': row[0] or '', 'manufacturer': row[1] or '',
                'hsn_code': row[2] or '', 'gst_percent': row[3] or 0,
                'mrp': row[4] or 0, 'rate': row[5] or 0,
                'schedule': row[6] or '',
                'content_drug': '',
                'batch_no': row[7] or '',
                'expiry_date': row[8] or '',
                'unit': row[9] or '',
            }

    if not result and load_app_mode() == 'medical':
        try:
            m = lookup_master_details(medicine_name)
            if m:
                result = {
                    'type': m.get('type', '') or '',
                    'manufacturer': m.get('manufacturer', '') or '',
                    'hsn_code': m.get('hsn_code', '') or '',
                    'gst_percent': float(m.get('gst_percent', 0) or 0),
                    'mrp': float(m.get('mrp', 0) or 0),
                    'rate': 0,
                    'schedule': m.get('schedule', '') or '',
                    'content_drug': m.get('content_drug', '') or '',
                    'unit': m.get('pack_size', '') or '',
                }
        except Exception:
            pass

    last = lookup_last_purchase_details(conn, medicine_name)
    if last:
        if not result:
            result = dict(last)
        else:
            if last.get('rate') not in (None, '', 0):
                result['rate'] = last['rate']
            for key in (
                'type', 'manufacturer', 'hsn_code', 'gst_percent', 'mrp', 'schedule',
                'content_drug', 'batch_no', 'expiry_date', 'unit', 'discount_pct',
            ):
                if last.get(key) not in (None, '', 0):
                    result[key] = last[key]

    return result


# ── Stock helpers ─────────────────────────────────────────────────────────────

# Two ceilings, split by where the number came from.
#
# A single ceiling of 60 meant a genuine 100-tablet pack was silently rewritten to
# 1, so 5 x 100 was booked as 5 tablets and the stock was 100x short. But the
# ceiling also protects against a bare "200" that really meant 200ML being read as
# a strip size. So: trust a number the operator typed into Tabs/Strip (or a pack
# written as a bare integer) up to 1000, and keep the tight bound for a number
# inferred from free-text pack wording. The ML/GM/KG/LTR/DOSE guards below are
# what actually stop volume packs, and they apply either way.
_MAX_STRIP_TPS_EXPLICIT = 1000
_MAX_STRIP_TPS_INFERRED = 60


def _sanitize_strip_tps(
    med_type: str, tps: int, pack_hint: str = "", *, explicit: bool = False
) -> int:
    """Reject volume/weight packs and absurd strip sizes for tablet/capsule lines."""
    try:
        from core.bill_import_normalize import pack_is_volume_or_weight

        if pack_is_volume_or_weight(pack_hint):
            return 1
    except Exception:
        pass
    try:
        n = int(tps)
    except (TypeError, ValueError):
        n = 1
    ceiling = _MAX_STRIP_TPS_EXPLICIT if explicit else _MAX_STRIP_TPS_INFERRED
    if n <= 0 or n > ceiling:
        return 1
    if is_strip_count_type(med_type or "") and pack_hint:
        hint = str(pack_hint).strip().upper()
        if any(x in hint for x in ("ML", "GM", "MG", "KG", "LTR", " DOSE")):
            return 1
    return n


def _get_unit_value(item) -> str:
    t = item['type']
    if is_strip_count_type(t):
        pack_hint = str(
            item.get("pack")
            or item.get("quantity_value")
            or item.get("unit")
            or item.get("tablets_per_stripe")
            or ""
        )
        # Stored field -- the operator's own number, so the wide ceiling.
        tps = _sanitize_strip_tps(
            t, item.get("tablets_per_stripe", 1), pack_hint, explicit=True
        )
        return str(tps)
    if t == 'injection - vial':
        return 'Vial'
    qty_raw = str(item.get('quantity_value', '1')).strip()
    unit_suffix = item.get('auto_unit', '')
    if any(sep in qty_raw.lower() for sep in ('*', 'x', '×')):
        return qty_raw
    # Anything that already names its own size is left alone. This list used to
    # stop at 'ml'/'gm', so a bill that said "1 LTR" or "500GMS" came out as
    # "1LTRml" and "500GMSgm" on the shelf.
    if any(qty_raw.lower().endswith(s) for s in (
        'ml', 'ltr', 'lt', 'ltrs', 'lit', 'litre', 'liter', 'l', 'gms', 'gm', 'gr', 'g', 'kg', 'mg', 'mcg',
        'doses', 'dose', 'vial', 'tab', 'tabs', 'cap', 'caps', 'pcs', 'btl',
    )):
        return qty_raw
    return f"{qty_raw}{unit_suffix}" if unit_suffix else qty_raw


def _get_stock_increase(item) -> float:
    """Stock added when a purchase is saved (tablets stored as individual tablets)."""
    med_type = str(item.get('type') or '')
    if is_strip_count_type(med_type):
        total = float(item.get('total_tablets', 0) or 0)
        free = float(item.get('free_tablets', 0) or 0)
        if total <= 0 and free <= 0:
            tps = item.get('tablets_per_stripe')
            try:
                tps = int(float(tps or 0))
            except (ValueError, TypeError):
                tps = 0
            pack_hint = str(
                item.get("pack")
                or item.get("quantity_value")
                or item.get("unit")
                or ""
            )
            # Provenance decides the ceiling, and _get_stock_decrease must land
            # on the same number or a delete would reverse a different quantity
            # than the save added.
            from_stored = tps > 0
            if tps <= 0:
                pack = item.get('pack') or item.get('quantity_value') or ''
                if pack:
                    try:
                        from core.bill_import_normalize import tablets_per_strip_from_pack
                        tps = tablets_per_strip_from_pack(pack)
                    except Exception:
                        tps = parse_tablets_per_stripe(str(pack))
                else:
                    tps = 1
            tps = _sanitize_strip_tps(
                med_type, tps, pack_hint, explicit=from_stored
            )
            if tps <= 0:
                tps = 1
            qty = float(item.get('qty', 0) or 0)
            free_qty = float(item.get('free_qty', 0) or 0)
            total = qty * tps
            free = free_qty * tps
        return total + free
    return float(item.get('qty', 0) or 0) + float(item.get('free_qty', 0) or 0)


def _get_stock_decrease(item) -> float:
    """
    Stock to subtract when a purchase is deleted or reversed.
    Mirrors _get_stock_increase exactly so the net is always zero.
    """
    if is_strip_count_type(item['type']):
        # Derive total_tablets from qty × tablets_per_strip stored in medicines.unit
        tps = item.get('tablets_per_stripe') or item.get('tps') or 0
        try:
            tps = int(float(tps))
        except (ValueError, TypeError):
            tps = 0
        pack_hint = str(
            item.get("pack")
            or item.get("quantity_value")
            or item.get("unit")
            or ""
        )
        # The docstring above promises this mirrors _get_stock_increase, but it
        # used to stop at the stored field and fall back to 1. A line with no
        # stored tablets_per_stripe and a pack reading "10 TAB" therefore ADDED
        # qty x 10 on save and took back only qty x 1 on delete, leaving nine
        # tenths of the batch as phantom stock. Same fallback, same ceiling rule.
        from_stored = tps > 0
        if tps <= 0:
            pack = item.get('pack') or item.get('quantity_value') or ''
            if pack:
                try:
                    from core.bill_import_normalize import tablets_per_strip_from_pack
                    tps = tablets_per_strip_from_pack(pack)
                except Exception:
                    tps = parse_tablets_per_stripe(str(pack))
            else:
                tps = 1
        tps = _sanitize_strip_tps(
            str(item.get("type") or ""), tps, pack_hint, explicit=from_stored
        )
        if tps <= 0:
            tps = 1
        qty      = float(item.get('qty', 0))
        free_qty = float(item.get('free_qty', 0))
        return qty * tps + free_qty * tps
    return float(item.get('qty', 0)) + float(item.get('free_qty', 0))


def _purchase_item_columns(cur) -> set[str]:
    try:
        cur.execute("PRAGMA table_info(purchase_items)")
        return {str(row[1]) for row in cur.fetchall()}
    except Exception:
        return set()


def _reverse_stock_for_purchase(cur, purchase_id: int):
    """
    Fetch purchase_items for purchase_id and subtract the correct stock amount.
    Used by delete_purchase and update_purchase (before re-inserting new items).
    """
    # Each line takes back what IT added when it was saved. Worked out again from the
    # medicine's unit, every line used the pack of whichever line was saved last: two strips
    # of 10 and one of 15 on one medicine went on as 35 tablets and came off as 45.
    logged_col = (
        "pi.stock_units" if "stock_units" in _purchase_item_columns(cur) else "NULL"
    )
    cur.execute(f"""
        SELECT pi.medicine_id, pi.qty, pi.free_qty,
               COALESCE(NULLIF(TRIM(pi.type), ''), m.type),
               COALESCE(m.unit, '1'), {logged_col}
        FROM purchase_items pi
        JOIN medicines m ON pi.medicine_id = m.id
        WHERE pi.purchase_id = ?
    """, (purchase_id,))
    rows = cur.fetchall()
    for med_id, qty, free_qty, med_type, unit_str, logged in rows:
        item = {
            'type':     med_type or '',
            'qty':      float(qty or 0),
            'free_qty': float(free_qty or 0),
        }
        if is_strip_count_type(med_type or ''):
            item['tablets_per_stripe'] = parse_tablets_per_stripe(unit_str)
        decrease = float(logged) if logged is not None else _get_stock_decrease(item)
        # No MAX(0, ...) here: this must be the exact inverse of _insert_items,
        # which adds without a floor. Clamping made an EDIT inflate stock -- the
        # reversal stopped at zero while the re-insert put the full quantity back,
        # so every edit of a partly-sold purchase quietly created stock that was
        # never delivered. A negative row is the honest answer and is what the
        # Add-No-Stock path already relies on.
        cur.execute(
            "UPDATE medicines SET stock_qty = stock_qty - ? WHERE id=?",
            (decrease, med_id))


def _apply_stock_for_purchase(cur, purchase_id: int):
    """Add stock for an existing purchase's line items (Server pull / restore)."""
    cur.execute("""
        SELECT pi.medicine_id, pi.qty, pi.free_qty,
               COALESCE(NULLIF(TRIM(pi.type), ''), m.type),
               COALESCE(m.unit, '1')
        FROM purchase_items pi
        JOIN medicines m ON pi.medicine_id = m.id
        WHERE pi.purchase_id = ?
    """, (purchase_id,))
    for med_id, qty, free_qty, med_type, unit_str in cur.fetchall():
        item = {
            'type':     med_type or '',
            'qty':      float(qty or 0),
            'free_qty': float(free_qty or 0),
        }
        if is_strip_count_type(med_type or ''):
            item['tablets_per_stripe'] = parse_tablets_per_stripe(unit_str)
        increase = _get_stock_decrease(item)  # same qty math as reverse
        if increase <= 0:
            continue
        cur.execute(
            "UPDATE medicines SET stock_qty=stock_qty+?, is_hidden=0 WHERE id=?",
            (increase, med_id),
        )


# ── Purchase record ───────────────────────────────────────────────────────────

def save_purchase_online_now(
    supplier_id: int,
    purchase_date_str: str,
    bill_number: str,
    calc_result: dict,
    items: list,
    client_uuid: str = "",
) -> str:
    """Flush worker: allocate FY + push a new purchase to the server."""
    from datetime import datetime as _dt

    from core import server_api as api
    from core.fy_serial import display_purchase_no, encode_purchase_no, fy_start_year_for_date
    from core.online_catalog import find_supplier_by_id, medicine_by_id
    from core.server_crud import (
        _device_id,
        _meta,
        _now,
        allocate_id,
        bump_meta,
        get_doc,
        save_new_purchase_online,
    )

    try:
        purchase_date = _dt.strptime(purchase_date_str, '%Y-%m-%d').date()
    except ValueError:
        purchase_date = _dt.now().date()
    date_s = purchase_date.isoformat()
    token = api.store_token_for_active()
    fy = api.allocate_fy(token, "purchases", date_s) or {}
    start = int(fy.get("fy_start_year") or fy_start_year_for_date(purchase_date))
    serial = int(fy.get("fy_serial") or 0)
    purchase_no = (fy.get("purchase_no") or "").strip()
    # The year in the number must be the year of the bill's own date. A bill
    # dated 2026-08-31 once came back numbered 1/FY2020-21, and the shop's
    # purchase numbering appeared to restart at 1 half way through the year.
    # The date is what the shop typed and can see, so it decides.
    want_fy = fy_start_year_for_date(purchase_date)
    if start != want_fy:
        print(
            f"[PURCHASE] server allocated FY {start} for a bill dated {date_s}; "
            f"asking again for FY {want_fy}"
        )
        fy = api.allocate_fy(token, "purchases", date_s) or {}
        start = int(fy.get("fy_start_year") or want_fy)
        serial = int(fy.get("fy_serial") or 0)
        purchase_no = (fy.get("purchase_no") or "").strip()
        if start != want_fy:
            raise RuntimeError(
                f"Purchase number came back for FY {start} but this bill is "
                f"dated {date_s} (FY {want_fy}). Check the bill date and try again."
            )
    if not purchase_no:
        if serial <= 0:
            raise RuntimeError(
                "Server did not allocate next purchase number for this store/FY"
            )
        purchase_no = encode_purchase_no(serial, start)
    if serial <= 0:
        disp = display_purchase_no(purchase_no)
        try:
            serial = int(disp)
        except ValueError:
            serial = 0
    if serial <= 0:
        raise RuntimeError("Server FY allocate returned no purchase serial")

    cash_paid, online_paid, entry_paid = payment_split_from_calc(calc_result)
    _ensure_purchase_line_medicine_ids(None, items)
    pid = allocate_id("purchases")
    cu = (client_uuid or "").strip() or str(uuid.uuid4())
    item_docs = []
    med_docs = []
    for it in items or []:
        it = dict(it)
        mid = int(it.get("medicine_id") or it.get("id") or 0)
        if mid <= 0:
            raise ValueError(
                "Purchase line "
                f"{it.get('name') or it.get('medicine_name') or '?'} "
                "has no medicine id after resolve."
            )
        _normalize_purchase_item_stock_fields(it)
        qty = float(it.get("qty") or it.get("quantity") or 0)
        free_qty = float(it.get("free_qty") or 0)
        stock_delta = float(_get_stock_increase(it) or 0)
        unit_val = _get_unit_value(it)
        name = it.get("name") or it.get("medicine_name") or ""
        item_docs.append({
            "medicine_id": mid,
            "name": name,
            "medicine_name": name,
            "qty": qty,
            "free_qty": free_qty,
            "rate": float(it.get("rate") or 0),
            "mrp": float(it.get("mrp") or 0),
            "amount": float(it.get("amount") or it.get("item_amount") or 0),
            "item_amount": float(it.get("item_amount") or it.get("amount") or 0),
            "gst_percent": float(
                it.get("gst_percent") or it.get("gst_pct") or it.get("gst_value") or 0
            ),
            "gst_pct": float(
                it.get("gst_pct") or it.get("gst_percent") or it.get("gst_value") or 0
            ),
            "batch_no": it.get("batch_no") or it.get("batch") or "",
            "expiry_date": _expiry_db(it.get("expiry_date") or it.get("expiry")),
            "type": it.get("type") or "",
            "manufacturer": it.get("manufacturer") or "",
            "schedule": it.get("schedule") or "",
            "hsn_code": it.get("hsn_code") or "",
            "discount_pct": float(it.get("discount_pct") or 0),
            "taxable": float(it.get("taxable") or 0),
            "gst_amt": float(it.get("gst_amt") or 0),
            "unit": unit_val,
            "tablets_per_stripe": it.get("tablets_per_stripe"),
            "quantity_value": it.get("quantity_value") or unit_val,
            "pack": it.get("pack") or unit_val,
            "_stock_delta": stock_delta,
            "_unit_val": unit_val,
        })

    # One medicine doc per id (sum stock). Duplicate lines sharing an id used to
    # overwrite each other and collide on stock_ops op_uuid.
    by_med: dict[int, dict] = {}
    stock_by_med: dict[int, float] = {}
    pack_by_med: dict[int, str] = {}
    for it in item_docs:
        mid = int(it["medicine_id"])
        stock_by_med[mid] = stock_by_med.get(mid, 0.0) + float(it.get("_stock_delta") or 0)
        by_med[mid] = it  # last line supplies master fields for that id
        line_pack = str(it.get("_unit_val") or it.get("unit") or "")
        if line_pack and (mid not in pack_by_med or _batch_row_pack(
            pack_by_med[mid], line_pack, it.get("type")
        ) != pack_by_med[mid]):
            pack_by_med[mid] = line_pack
    supplier_note = _supplier_name_for(None, supplier_id)
    for mid, it in by_med.items():
        stock_delta = float(stock_by_med.get(mid, 0.0) or 0)
        unit_val = pack_by_med.get(mid) or it.get("_unit_val") or it.get("unit") or ""
        name = it.get("name") or ""
        mp = medicine_by_id(mid) or get_doc("medicines", mid) or {
            "id": mid, "local_id": mid, "name": name, "stock_qty": 0,
        }
        mp = bump_meta(dict(mp)) if stock_delta else _meta(mp)
        mp["id"] = mid
        mp["local_id"] = mid
        if name:
            mp["name"] = name
        if it.get("hsn_code"):
            mp["hsn_code"] = it.get("hsn_code")
        # Where this medicine came from, for reference. Only on a line that
        # actually stocked something, so an edit that takes stock back does not
        # rewrite the note.
        if supplier_note and stock_delta > 0:
            mp["supplier_name"] = supplier_note
        # Each line was stocked by its own pack above; the batch row keeps its own.
        kept_pack = _batch_row_pack(mp.get("unit"), unit_val, it.get("type") or mp.get("type"))
        if kept_pack:
            mp["unit"] = kept_pack
        batch_s = str(it.get("batch_no") or it.get("batch") or "")
        if batch_s:
            mp["batch_no"] = batch_s
        exp_s = _expiry_db(it.get("expiry_date") or it.get("expiry"))
        if exp_s:
            mp["expiry_date"] = exp_s
        if it.get("type"):
            mp["type"] = it.get("type")
        # First real purchase replaces Add-No-Stock leftover instead of stacking.
        if stock_delta and (
            mp.get("from_quick_sale") or mp.get("provisional_stock")
        ):
            try:
                if float(mp.get("stock_qty") or 0) > 0:
                    mp["stock_qty"] = 0.0
            except Exception:
                mp["stock_qty"] = 0.0
            mp["from_quick_sale"] = False
            mp["provisional_stock"] = False
        try:
            mp["stock_qty"] = float(mp.get("stock_qty") or 0) + stock_delta
        except Exception:
            pass
        mp["is_hidden"] = False
        mp["deleted"] = False
        if stock_delta:
            mp["stock_ops"] = [{
                "op_uuid": f"purchase:{cu}:med:{mid}:v1",
                "op": "purchase",
                "qty_delta": int(round(stock_delta)),
                "medicine_id": mid,
                "ref_collection": "purchases",
                "ref_id": pid,
                "device_id": _device_id(),
            }]
        med_docs.append(mp)

    for it in item_docs:
        it.pop("_stock_delta", None)
        it.pop("_unit_val", None)

    supplier = find_supplier_by_id(int(supplier_id)) or get_doc("suppliers", int(supplier_id)) or {
        "id": int(supplier_id), "local_id": int(supplier_id),
    }
    supplier = _meta(supplier)
    supplier["id"] = int(supplier_id)
    supplier["local_id"] = int(supplier_id)
    supplier["total_due"] = calc_result.get("total_due", supplier.get("total_due"))

    doc = {
        "id": pid,
        "local_id": pid,
        "client_uuid": cu,
        "purchase_no": purchase_no,
        "fy_start_year": start,
        "fy_serial": serial,
        "supplier_id": int(supplier_id),
        "supplier_name": supplier.get("name") or "",
        "purchase_date": date_s,
        "bill_number": bill_number or "",
        "subtotal": calc_result.get("subtotal"),
        "total_gst": calc_result.get("total_gst"),
        "cgst": calc_result.get("cgst"),
        "sgst": calc_result.get("sgst"),
        "total_amount": calc_result.get("total_amount"),
        "overall_discount": calc_result.get("overall_discount"),
        "rounding": calc_result.get("rounding"),
        "need_to_pay": calc_result.get("need_to_pay"),
        "final_amount": calc_result.get("final_amount"),
        "amount_paid": entry_paid,
        "amount_paid_at_entry": entry_paid,
        "cash_paid_at_entry": cash_paid,
        "online_paid_at_entry": online_paid,
        "previous_due": calc_result.get("previous_due"),
        "previous_credit": calc_result.get("previous_credit"),
        "due": calc_result.get("due"),
        "current_credit": calc_result.get("current_credit"),
        "total_due": calc_result.get("total_due"),
        "bill_cleared": calc_result.get("bill_cleared"),
        "account_cleared": calc_result.get("account_cleared"),
        "gst_calc_method": calc_result.get("gst_calc_method") or "discount_after_gst",
        "is_autosave": False,
        # When the purchase was made: the PC never sent it, and every PC purchase on the
        # server had an empty created_at. The server keeps the first value on later edits.
        "created_at": _now(),
        "items": item_docs,
        "_suppliers": [supplier],
        "_medicines": med_docs,
    }
    save_new_purchase_online(doc)
    try:
        hide_online_zero_stock_duplicates()
    except Exception:
        pass
    return display_purchase_no(purchase_no)


_LEDGER_PAGE = 5000
_LEDGER_PAGES = 40
_LEDGER_BUDGET_SECONDS = 20.0


def _one_ms_before(stamp: str) -> str:
    """An ISO time one millisecond earlier, so a page that ends inside one instant loses nothing."""
    from datetime import timedelta

    text = str(stamp or "").strip()
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text
    return (moment - timedelta(milliseconds=1)).isoformat()


def _purchase_op_bill_key(op_uuid) -> str | None:
    """The bill key in a purchase movement's op_uuid, "purchase:<key>:med:<mid>:...", else None.

    The key is the bill's client_uuid -- or its local id, for a bill that had none -- on the PC
    (save_purchase_online_now, update_purchase_online_now, _flush_purchase_delete) and on the
    phone (SaveStockOps, DeleteStockOps) alike.
    """
    text = str(op_uuid or "")
    if not text.startswith("purchase:"):
        return None
    key, sep, _rest = text[len("purchase:"):].partition(":med:")
    return key if sep else None


def _logged_purchase_units(
    purchase_id: int, dates, medicine_ids, client_uuid: str = ""
) -> dict[int, float] | None:
    """What the stock ledger holds for one purchase, per medicine: its save plus its edits.

    Only a medicine whose SAVE movement is found is answered; one saved before the ledger
    began is left out and read from its lines by the caller. The ledger is read from the day
    before the earliest of ``dates`` -- a purchase is entered on or after its own date, and a
    purchase entered before it is simply not found here. None when it could not be read.

    Only THIS bill's movements count: those whose op_uuid carries its ``client_uuid``. A
    deleted purchase row is removed outright and the next purchase takes the same local id,
    so matching on ref_id alone added the deleted bill's save and edits -- never its delete --
    to this one (store 4, MOLICOLD: +5, +3, -8 under 3026, then the next bill's +35 there, and
    its edit to 40 took off 3 where 5 was due).

    A bill saved before it had a client_uuid logged under its local id (or ""), and the row
    may have got a client_uuid since: 98 live bill-medicines hold their save that way (store 1
    bill 83, key "83"). Those keys count as well -- but a deleted bill may have used this local
    id and those keys too, so a medicine with a purchase_delete at this id under any key but
    this bill's client_uuid has its local-id movements left out, and one whose only save is
    among them is left to its lines. A purchase_delete under the bill's own client_uuid leaves
    the medicine to its lines, as before.
    """
    import time as _time
    from datetime import timedelta

    from core import server_api as api
    from core.online_catalog import _token

    wanted: set[int] = set()
    for raw in medicine_ids or ():
        try:
            if int(raw or 0) > 0:
                wanted.add(int(raw))
        except (TypeError, ValueError):
            continue
    if not wanted:
        return {}
    days = []
    for raw in dates or ():
        try:
            days.append(datetime.strptime(str(raw or "")[:10], "%Y-%m-%d").date())
        except ValueError:
            continue
    cursor = (min(days) - timedelta(days=1)).isoformat() if days else None
    # Movements by whose key they carry: this bill's client_uuid ("own"), or the keys a bill
    # saved before it had one logged under -- "" or its local id ("legacy").
    saved: dict[str, dict[int, float]] = {"own": {}, "legacy": {}}
    edited: dict[str, dict[int, float]] = {"own": {}, "legacy": {}}
    seen: set[str] = set()
    own = str(client_uuid or "").strip()
    legacy_keys = {"", str(int(purchase_id))}
    # Medicines this bill's own key deleted from: whose movement is whose is lost.
    doubtful_own: set[int] = set()
    # Medicines a bill was deleted from at this id under any other key: that bill may have
    # logged under the local id too, so the local-id movements are not this bill's for certain.
    doubtful_legacy: set[int] = set()
    deadline = _time.monotonic() + _LEDGER_BUDGET_SECONDS
    try:
        token = _token()
        for _ in range(_LEDGER_PAGES):
            with api.request_deadline(deadline):
                docs, _info = api.pull_collection(
                    token,
                    "stock_operations",
                    since=cursor,
                    include_deleted=False,
                    limit=_LEDGER_PAGE,
                    timeout=15.0,
                )
            docs = [d for d in (docs or []) if isinstance(d, dict)]
            fresh = 0
            last = ""
            for d in docs:
                key = str(d.get("op_uuid") or d.get("id") or "")
                if key in seen:
                    continue
                seen.add(key)
                fresh += 1
                last = str(d.get("created_at") or last)
                if str(d.get("ref_collection") or "") != "purchases":
                    continue
                try:
                    if int(d.get("ref_id") or 0) != int(purchase_id):
                        continue
                    mid = int(d.get("medicine_id") or 0)
                    qty = float(d.get("qty_delta") or 0)
                except (TypeError, ValueError):
                    continue
                if mid not in wanted:
                    continue
                key = _purchase_op_bill_key(d.get("op_uuid"))
                if own and key == own:
                    side = "own"
                elif key in legacy_keys:
                    side = "legacy"
                else:
                    # Another bill's movement under this id: one that was deleted, say.
                    side = ""
                op = str(d.get("op") or "").strip().lower()
                if op == "purchase_delete":
                    (doubtful_own if side == "own" else doubtful_legacy).add(mid)
                elif side and op == "purchase":
                    saved[side][mid] = saved[side].get(mid, 0.0) + qty
                elif side and op == "purchase_edit":
                    edited[side][mid] = edited[side].get(mid, 0.0) + qty
            if len(docs) < _LEDGER_PAGE:
                break
            if not fresh or not last:
                raise RuntimeError("the stock ledger stopped moving")
            cursor = _one_ms_before(last)
        else:
            raise RuntimeError("the stock ledger is longer than the pages read")
    except Exception as exc:
        print(f"[PURCHASE] stock ledger for purchase {purchase_id} not read ({exc}); "
              "the edit is worked out from its lines")
        return None
    logged: dict[int, float] = {}
    for mid in wanted:
        if mid in doubtful_own:
            continue
        sides = (["own"] if own else []) + ([] if mid in doubtful_legacy else ["legacy"])
        if not any(mid in saved[side] for side in sides):
            # Its save is not in the ledger, or not this bill's for certain: read from its lines.
            continue
        logged[mid] = sum(
            saved[side].get(mid, 0.0) + edited[side].get(mid, 0.0) for side in sides
        )
    return logged


# A purchase edit the store skipped (land_skipped_purchase_edit).
#
# Everything on a purchase row but what the supplier cascade writes -- due, due_amount,
# total_due and the two cleared flags (partyDueCascade cascadeSupplierAfterLedgerChange). A
# store copy that differs from an edit's base in none of these was moved on by a payment, a
# return or another bill of the supplier, and never edited.
_EDIT_HEADER_FIELDS = (
    "supplier_id", "purchase_date", "bill_number", "purchase_no", "subtotal", "total_gst",
    "cgst", "sgst", "total_amount", "overall_discount", "rounding", "need_to_pay",
    "final_amount", "amount_paid", "amount_paid_at_entry", "cash_paid_at_entry",
    "online_paid_at_entry", "previous_due", "previous_credit", "current_credit",
    "credit_amount", "paid_due", "expenditure", "gst_calc_method", "is_autosave", "deleted",
)
_EDIT_LINE_FIELDS = (
    "medicine_id", "name", "type", "qty", "free_qty", "rate", "mrp", "gst_pct", "discount_pct",
    "taxable", "gst_amt", "item_amount", "batch_no", "expiry_date", "unit", "tablets_per_stripe",
)
# "Is this edit already what the store holds?" The store files the number itself and works a
# line's tablets_per_stripe out of its unit when the line sends none, so those are not asked.
_LANDED_HEADER_FIELDS = tuple(f for f in _EDIT_HEADER_FIELDS if f != "purchase_no")
_LANDED_LINE_FIELDS = tuple(f for f in _EDIT_LINE_FIELDS if f != "tablets_per_stripe")
_EDIT_FLAG_FIELDS = frozenset({"is_autosave", "deleted"})
# How often a skipped edit is put again over a store copy that supplier cascades keep moving.
_EDIT_LANDINGS = 4


def _edit_field(key: str, value):
    """One field as two readings can agree on it: numbers to the paisa, dates to the day."""
    if key in _EDIT_FLAG_FIELDS:
        return str(value).strip().lower() not in ("", "0", "0.0", "false", "f", "none")
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, (bool, int, float)):
        return round(float(value), 2)
    text = str(value).strip()
    if key in ("purchase_date", "expiry_date"):
        return text[:10]
    try:
        return round(float(text), 2)
    except ValueError:
        return text


def _purchase_content(doc, header_fields=_EDIT_HEADER_FIELDS, line_fields=_EDIT_LINE_FIELDS):
    """A purchase's header and lines, in a form two readings of it can be compared in."""
    doc = doc if isinstance(doc, dict) else {}
    header = tuple(_edit_field(k, doc.get(k)) for k in header_fields)
    lines = sorted(
        (
            tuple(_edit_field(k, it.get(k)) for k in line_fields)
            for it in doc.get("items") or []
            if isinstance(it, dict)
        ),
        key=repr,
    )
    return header, tuple(lines)


def _units_by_medicine(items) -> dict[int, float]:
    """What each medicine's lines put on the shelf, by the rule the save and the edit use."""
    out: dict[int, float] = {}
    for it in items or []:
        if not isinstance(it, dict):
            continue
        try:
            mid = int(it.get("medicine_id") or 0)
        except (TypeError, ValueError):
            continue
        if mid <= 0:
            continue
        line = dict(it)
        try:
            _normalize_purchase_item_stock_fields(line)
            units = float(_get_stock_increase(line) or 0)
        except Exception:
            units = float("nan")  # not known, so never "unchanged"
        out[mid] = out.get(mid, 0.0) + units
    return out


def _same_units(a, b) -> bool:
    try:
        return a is not None and b is not None and abs(float(a) - float(b)) < 1e-6
    except (TypeError, ValueError):
        return False


def _edit_moves(medicines, purchase_id: int) -> list[dict]:
    """The stock movements an edit's bundle carried for its own bill."""
    moves: list[dict] = []
    for med in medicines or []:
        for op in (med or {}).get("stock_ops") or []:
            if not isinstance(op, dict) or not str(op.get("op_uuid") or ""):
                continue
            try:
                if int(op.get("ref_id") or 0) != int(purchase_id):
                    continue
                if not int(op.get("qty_delta") or 0) or int(op.get("medicine_id") or 0) <= 0:
                    continue
            except (TypeError, ValueError):
                continue
            moves.append(dict(op))
    return moves


def _put_back_edit_moves(moves: list[dict]) -> str:
    """Reverse ``moves`` on the store; "" when it took every one back, else what went wrong.

    Each goes as a stock_operations entry of its own, under the movement's op_uuid + ":undo"
    (so a second try is applied once) and the same op, so the ledger nets this edit to 0. A
    medicine document would also carry this PC's copy of the medicine's other fields.
    """
    from core.server_crud import _device_id, push_bundle

    if not moves:
        return ""
    ops = [
        {
            "op_uuid": f"{m['op_uuid']}:undo",
            "op": str(m.get("op") or "purchase_edit"),
            "qty_delta": -int(m["qty_delta"]),
            "medicine_id": int(m["medicine_id"]),
            "ref_collection": m.get("ref_collection") or "purchases",
            "ref_id": m.get("ref_id"),
            "device_id": _device_id(),
        }
        for m in moves
    ]
    try:
        answer = push_bundle({"stock_operations": ops})
    except Exception as exc:
        return f"{type(exc).__name__}: {exc}"
    finally:
        try:
            from core.online_catalog import invalidate

            invalidate("medicines")
        except Exception:
            pass
    data = answer.get("data") if isinstance(answer, dict) and isinstance(answer.get("data"), dict) else answer
    results = ((data or {}).get("stock_operations") or {}).get("results") if isinstance(data, dict) else None
    if not isinstance(results, list) or len(results) < len(ops) or any(
        not isinstance(r, dict) or str(r.get("status") or "") not in ("applied", "skipped")
        for r in results
    ):
        return "the server did not confirm it"
    return ""


def _edit_medicine_names(doc: dict, base: dict) -> dict[int, str]:
    """Each medicine's name, from the edit's lines and the lines it was worked out from."""
    names: dict[int, str] = {}
    for it in list((base or {}).get("items") or []) + list((doc or {}).get("items") or []):
        if not isinstance(it, dict):
            continue
        try:
            mid = int(it.get("medicine_id") or 0)
        except (TypeError, ValueError):
            continue
        label = str(it.get("name") or it.get("medicine_name") or "").strip()
        if mid > 0 and label:
            names[mid] = label
    return names


def _names_of(moves: list[dict], names: dict[int, str]) -> str:
    out: list[str] = []
    for m in moves:
        label = names.get(int(m["medicine_id"])) or f"medicine {int(m['medicine_id'])}"
        if label not in out:
            out.append(label)
    return ", ".join(out)


def _certainly_this_edits(base: dict, held, moves: list[dict]) -> list[dict]:
    """The movements of ``moves`` the store certainly holds for THIS edit, against its copy ``held``.

    Two edits worked out from one version write their movements under one op_uuid: the store
    applied the other device's and skipped this one. Only a medicine whose lines the other change
    left as they were certainly carries THIS edit's movement. Lines not read: nothing is certain.
    """
    if not (isinstance(held, dict) and isinstance(held.get("items"), list)):
        return []
    before = _units_by_medicine((base or {}).get("items"))
    now = _units_by_medicine(held.get("items"))
    return [
        m for m in moves
        if _same_units(before.get(int(m["medicine_id"])), now.get(int(m["medicine_id"])))
    ]


def _refusal_reason(failures, names: dict[int, str]) -> str:
    """The server's words for each document it refused, naming the medicine where there is one.

    The stock ledger's refusal ends "Nothing was changed.", which is true of that one medicine
    document only -- the rest of the bundle committed -- so that sentence is left out.
    """
    parts: list[str] = []
    for f in failures or []:
        if not isinstance(f, dict):
            continue
        words = str(f.get("error") or f.get("constraint") or "refused")
        words = words.replace("Nothing was changed.", "").strip().rstrip(".").strip() or "refused"
        collection = str(f.get("collection") or "")
        try:
            fid = int(f.get("id") or 0)
        except (TypeError, ValueError):
            fid = 0
        who = (names.get(fid) or f"medicine {fid}") if collection == "medicines" else (
            f"{collection} {f.get('id')}".strip()
        )
        part = f"{who}: {words}"
        if part not in parts:
            parts.append(part)
    return "; ".join(parts)


def _refuse_skipped_edit(doc: dict, base: dict, held, moves: list[dict], why: str, detail: str = ""):
    """Raise PurchaseEditNotSaved, after putting back the stock this edit certainly moved."""
    from core.fy_serial import display_purchase_no
    from core.server_crud import PurchaseEditNotSaved

    pid = int(doc.get("id") or doc.get("local_id") or 0)
    number = display_purchase_no(str(doc.get("purchase_no") or "")) or str(pid)
    names = _edit_medicine_names(doc, base)

    if why in ("unread", "unanswered"):
        # Whether the edit is on the store cannot be told: nothing is taken back on a guess.
        certain: list[dict] = []
    elif why in ("busy", "refused") or (why == "deleted" and not isinstance(held, dict)):
        # Only supplier cascades moved the bill on (the store refused or skipped the edit over a
        # copy that was its base in all else), or it is gone from the store: no other edit of it
        # can have spent this edit's op_uuids.
        certain = list(moves)
    else:
        certain = _certainly_this_edits(base, held, moves)
    unsure = [m for m in moves if m not in certain]
    failed = _put_back_edit_moves(certain)

    if why == "deleted":
        text = (f"Purchase {number} was deleted on another device while this edit was being "
                "saved, so the edit was not saved.")
    elif why == "changed":
        text = (f"Purchase {number} was changed on another device while this edit was being "
                "saved, so this edit was not saved over that change. Open the purchase again, "
                "check it and make this edit again.")
    elif why == "busy":
        text = (f"Purchase {number} kept changing on the server (payments or returns for this "
                "supplier) while this edit was being saved, so the edit was not saved. Save it "
                "again.")
    elif why == "refused":
        text = f"Purchase {number} was not saved: the server refused the edit ({detail})."
    elif why == "unanswered":
        text = (f"Purchase {number} may not be saved: the server did not answer while the edit "
                f"was being saved ({detail}), and the purchase read back does not show the edit. "
                "Open the purchase again and check it.")
    else:
        text = (f"Purchase {number} may not be saved: the server did not take the edit and the "
                f"purchase could not be read back ({detail}). Open the purchase again and check it.")
    if certain and not failed:
        text += " The stock this edit moved has been put back."
    elif certain:
        text += (f" The stock this edit moved could not be put back ({failed}): check the stock "
                 f"of {_names_of(certain, names)}.")
    if unsure:
        text += f" Check the stock of {_names_of(unsure, names)}."
    print(f"[PURCHASE] edit of purchase {pid} not saved ({why}): {text}")
    raise PurchaseEditNotSaved(text, pid)


def land_skipped_purchase_edit(doc: dict, base: dict, medicines: list) -> None:
    """An Online purchase edit the store skipped: put it over the store's copy, or say why not.

    The store takes a purchase only past the version it holds (at the same version, only with a
    strictly newer updated_at), and a supplier payment's cascade writes each bill whose due it
    changes at version + 1 with updated_at NOW(). An edit read before a payment and arriving
    after it was skipped whole while the stock movements in its bundle were applied (staging,
    store 4, purchase 2997: lines as before, stock 900 -> 890, the save answering ok).

    ``doc`` is the purchase as pushed, ``base`` the store's copy the edit was worked out from,
    ``medicines`` the bundle's medicine documents, whose stock_ops are this edit's movements.

    Every attempt starts by reading the store's copy, and that copy decides:

    * The store already holds this edit (the same change arrived from elsewhere, or a push of
      it landed without an answer): done.
    * The store's copy differs from ``base`` only in what the supplier cascade writes: the edit
      goes again over that copy's version, and the server's cascade after an accepted purchase
      sets its dues from the ledger. ``doc`` takes the version it landed under.
    * Anything else -- another device's edit, a delete, a store that keeps moving on, a push the
      store refuses, a store that cannot be read -- raises server_crud.PurchaseEditNotSaved: the
      edit is not forced over a change it never saw, and the stock it moved is put back wherever
      it certainly moved and named for a check wherever it may have.

    A push that gets no answer is never taken for landed or for lost: the next attempt's read
    decides. Its error used to escape raw, and the online queue replayed the edit, which is
    worked out again from the store's copy at the next version under new op_uuids and moved the
    stock a second time. Whatever ends here unsettled is a PurchaseEditNotSaved, which the queue
    parks for the shop instead of replaying.
    """
    from core import server_api as api
    from core.server_crud import (
        BundleDocumentRejected,
        _device_id,
        _now,
        _token,
        push_bundle,
        pushed_status,
    )

    pid = int(doc.get("id") or doc.get("local_id") or 0)
    moves = _edit_moves(medicines, pid)
    mine = _purchase_content(doc, _LANDED_HEADER_FIELDS, _LANDED_LINE_FIELDS)
    before = _purchase_content(base)
    held = None
    unanswered = ""  # the error of the last push, when it got no answer
    for attempt in range(_EDIT_LANDINGS + 1):
        try:
            # Straight from the store: server_crud.get_doc answers None for a network error too.
            held = api.pull_doc(_token(), "purchases", pid)
        except Exception as exc:
            _refuse_skipped_edit(doc, base, None, moves, "unread", f"{type(exc).__name__}: {exc}")
        if isinstance(held, dict) and (
            _purchase_content(held, _LANDED_HEADER_FIELDS, _LANDED_LINE_FIELDS) == mine
        ):
            for key in ("version", "updated_at"):
                if held.get(key) is not None:
                    doc[key] = held[key]
            return
        if not isinstance(held, dict) or _edit_field("deleted", held.get("deleted")):
            _refuse_skipped_edit(doc, base, held, moves, "deleted")
        if _purchase_content(held) != before:
            _refuse_skipped_edit(doc, base, held, moves, "changed")
        if attempt >= _EDIT_LANDINGS:
            break
        again = dict(doc)
        again["version"] = max(int(held.get("version") or 1), int(doc.get("version") or 1)) + 1
        again["updated_at"] = _now()
        again["device_id"] = _device_id()
        try:
            answer = push_bundle({"purchases": [again]})
        except BundleDocumentRejected as exc:
            _refuse_skipped_edit(
                doc, base, held, moves, "refused",
                _refusal_reason(exc.failures, _edit_medicine_names(doc, base)) or str(exc),
            )
        except Exception as exc:
            unanswered = f"{type(exc).__name__}: {exc}"
            print(f"[PURCHASE] edit of purchase {pid}: no answer to the push over the store's "
                  f"version {held.get('version')} ({unanswered}); reading the store again")
            continue
        unanswered = ""
        if pushed_status(answer, "purchases", pid) != "skipped":
            print(f"[PURCHASE] edit of purchase {pid} was skipped behind the store's version "
                  f"{held.get('version')} (a supplier cascade); saved over it as version "
                  f"{again['version']}")
            for key in ("version", "updated_at", "device_id"):
                doc[key] = again[key]
            return
    _refuse_skipped_edit(doc, base, held, moves, "unanswered" if unanswered else "busy", unanswered)


def settle_refused_purchase_edit(doc: dict, base: dict, medicines: list, refusal) -> None:
    """An Online purchase edit part of whose bundle the store refused: say what became of it.

    Two devices that edit one purchase from the same version write their movements under the
    same op_uuids ("purchase:<uuid>:med:<mid>:edit:v<n>"). When they moved one medicine by
    different amounts the store refuses that medicine's document ("... was already applied with
    qty_delta -20; this push carries -10 under the same op_uuid. Nothing was changed."), while
    every other document of the bundle commits under its own savepoint: this edit's movements
    on the other medicines stayed on the shelf, and the save showed "Nothing was changed." for
    the whole edit (staging dev2 BOTH_DIFF, store 4 purchase 2997).

    ``refusal`` is the server_crud.BundleDocumentRejected; its ``answer`` says what became of the
    purchase itself, and the store's copy is read as well. Always raises PurchaseEditNotSaved:

    * The edit is on the store (its stamp was the newer one): ``saved`` is True, nothing is put
      back, and each medicine whose movement was refused is named for a stock check.
    * It is not: a movement that is certainly this edit's (_certainly_this_edits) is put back,
      any other it may have made is named, and a refused medicine is named only when the store's
      lines do not show the movement already held under its op_uuid.
    """
    from core import server_api as api
    from core.fy_serial import display_purchase_no
    from core.server_crud import PurchaseEditNotSaved, _token, pushed_status

    pid = int(doc.get("id") or doc.get("local_id") or 0)
    number = display_purchase_no(str(doc.get("purchase_no") or "")) or str(pid)
    names = _edit_medicine_names(doc, base)
    failures = [f for f in (getattr(refusal, "failures", None) or []) if isinstance(f, dict)]
    reason = _refusal_reason(failures, names) or str(refusal)
    refused_ids: set[int] = set()
    for f in failures:
        if f.get("collection") == "medicines":
            try:
                refused_ids.add(int(f.get("id") or 0))
            except (TypeError, ValueError):
                continue
    moves = _edit_moves(medicines, pid)
    refused = [m for m in moves if int(m["medicine_id"]) in refused_ids]
    applied = [m for m in moves if int(m["medicine_id"]) not in refused_ids]
    refused_names = ", ".join(
        dict.fromkeys(names.get(mid) or f"medicine {mid}" for mid in sorted(refused_ids))
    )

    status = pushed_status(getattr(refusal, "answer", None), "purchases", pid)
    held = None
    unread = ""
    try:
        held = api.pull_doc(_token(), "purchases", pid)
    except Exception as exc:
        unread = f"{type(exc).__name__}: {exc}"
    landed = status not in ("", "skipped", "failed") or (
        isinstance(held, dict)
        and _purchase_content(held, _LANDED_HEADER_FIELDS, _LANDED_LINE_FIELDS)
        == _purchase_content(doc, _LANDED_HEADER_FIELDS, _LANDED_LINE_FIELDS)
    )

    if landed:
        if refused_names:
            text = (f"Purchase {number} was saved, but the server refused this edit's stock "
                    f"movement for {refused_names} ({reason}). Check the stock of "
                    f"{refused_names}.")
        else:
            text = f"Purchase {number} was saved, but the server refused part of it ({reason})."
        stored = str((held or {}).get("purchase_no") or doc.get("purchase_no") or "")
        print(f"[PURCHASE] edit of purchase {pid} saved with a refused document: {text}")
        raise PurchaseEditNotSaved(text, pid, saved=True, purchase_no=stored)

    readable = not unread and isinstance(held, dict) and isinstance(held.get("items"), list)
    certain = _certainly_this_edits(base, held, applied) if readable else []
    unsure = [m for m in applied if m not in certain]
    # A refused medicine moved nothing now. The movement already held under its op_uuid is the
    # other change's and shows in that change's lines -- unless its lines are as they were.
    if readable:
        was = _units_by_medicine((base or {}).get("items"))
        now = _units_by_medicine(held.get("items"))
        unshown = [
            m for m in refused
            if _same_units(was.get(int(m["medicine_id"])), now.get(int(m["medicine_id"])))
        ]
    else:
        unshown = list(refused)
    failed = _put_back_edit_moves(certain)

    if unread:
        text = (f"Purchase {number} may not be saved: the server refused part of this edit "
                f"({reason}) and the purchase could not be read back ({unread}). Open the "
                "purchase again and check it.")
    elif not isinstance(held, dict) or _edit_field("deleted", held.get("deleted")):
        text = (f"Purchase {number} was deleted on another device while this edit was being "
                f"saved, so the edit was not saved ({reason}).")
    elif _purchase_content(held) != _purchase_content(base):
        text = (f"Purchase {number} was changed on another device while this edit was being "
                f"saved, so this edit was not saved over that change ({reason}). Open the "
                "purchase again, check it and make this edit again.")
    else:
        text = (f"Purchase {number} was not saved: the server refused part of this edit "
                f"({reason}). Open the purchase again and check it.")
    if certain and not failed:
        text += " The stock this edit moved has been put back."
    elif certain:
        text += (f" The stock this edit moved could not be put back ({failed}): check the stock "
                 f"of {_names_of(certain, names)}.")
    check = unsure + unshown
    if check:
        text += f" Check the stock of {_names_of(check, names)}."
    print(f"[PURCHASE] edit of purchase {pid} not saved (refused document): {text}")
    raise PurchaseEditNotSaved(text, pid)


def update_purchase_online_now(
    purchase_id: int,
    supplier_id: int,
    bill_number: str,
    purchase_date_str: str,
    calc_result: dict,
    items: list,
    client_uuid: str = "",
    purchase_no: str = "",
    fy_start_year=None,
    fy_serial=None,
) -> None:
    """Flush worker: apply an Online purchase edit to the server now."""
    from core.online_catalog import find_supplier_by_id, medicine_by_id, patch_docs
    import copy

    from core.server_crud import save_new_purchase_online, get_doc, _meta, bump_meta, _device_id

    existing = get_doc("purchases", int(purchase_id)) or {}
    # The store's copy this edit is worked out from, as read. A purchase the store then skips
    # is compared with it (land_skipped_purchase_edit), so nothing below may change it.
    edit_base = copy.deepcopy(existing) if existing else None
    cu = (client_uuid or existing.get("client_uuid") or "").strip()
    if not cu and not existing:
        cu = str(uuid.uuid4())
    if purchase_no and not existing.get("purchase_no"):
        existing["purchase_no"] = purchase_no
    if fy_start_year is not None and not existing.get("fy_start_year"):
        existing["fy_start_year"] = fy_start_year
    if fy_serial is not None and not existing.get("fy_serial"):
        existing["fy_serial"] = fy_serial
    try:
        purchase_date = datetime.strptime(purchase_date_str, '%Y-%m-%d').date()
    except ValueError:
        purchase_date = datetime.now().date()
    date_s = purchase_date.isoformat()

    # An edit keeps the bill's number -- that is what the shop expects, and the
    # number is already written in their register. Offline, a date moved into a
    # DIFFERENT financial year takes that year's next number
    # (resync_purchase_fy_number), because the year is part of the number.
    #
    # Online it cannot yet. The server's purchase UPDATE re-files the year and
    # serial but never writes purchase_no (server-live syncService.upsertPurchase),
    # so the renumbered edit this used to send made this PC list and print
    # 8/FY2026-27 while the store -- and every other device -- kept 5/FY2025-26.
    # Keep the number the server keeps until its UPDATE stores the new one.
    from core.fy_serial import fy_start_year_for_date as _fy_for
    from core.fy_serial import fy_start_year_in_code as _fy_in_code

    # Read the year off the NUMBER the shop can see, and fall back to the
    # stored field only when the number carries no year.
    _held_fy = _fy_in_code(existing.get("purchase_no"))
    if _held_fy is None:
        _raw = existing.get("fy_start_year")
        try:
            _held_fy = int(_raw) if _raw not in (None, "") else None
        except (TypeError, ValueError):
            _held_fy = None
    if _held_fy is not None and _held_fy != _fy_for(purchase_date):
        print(
            f"[PURCHASE] bill {purchase_id} moved from FY {_held_fy} to FY "
            f"{_fy_for(purchase_date)}; keeps {existing.get('purchase_no')} (the server "
            f"does not store a new number on an edit)"
        )

    cash_paid, online_paid, entry_paid = payment_split_from_calc(calc_result)
    _ensure_purchase_line_medicine_ids(None, items)

    old_items = existing.get("items") or []
    old_by_med = {}
    old_lines_by_med: dict[int, int] = {}
    for it in old_items:
        if not isinstance(it, dict):
            continue
        mid = int(it.get("medicine_id") or 0)
        if mid <= 0:
            continue
        old_it = dict(it)
        _normalize_purchase_item_stock_fields(old_it)
        old_by_med[mid] = old_by_med.get(mid, 0.0) + float(_get_stock_increase(old_it) or 0)
        old_lines_by_med[mid] = old_lines_by_med.get(mid, 0) + 1
    # Never treat "items missing from GET" as old stock = 0 (that doubles inventory).
    skip_stock_ops = bool(existing) and not old_items

    item_docs = []
    med_docs = []
    new_by_med = {}
    for it in items or []:
        it = dict(it)
        mid = int(it.get("medicine_id") or it.get("id") or 0)
        if mid <= 0:
            raise ValueError(
                "Purchase line "
                f"{it.get('name') or it.get('medicine_name') or '?'} "
                "has no medicine id after resolve."
            )
        _normalize_purchase_item_stock_fields(it)
        qty = float(it.get("qty") or it.get("quantity") or 0)
        free_qty = float(it.get("free_qty") or 0)
        stock_units = float(_get_stock_increase(it) or 0)
        unit_val = _get_unit_value(it)
        new_by_med[mid] = new_by_med.get(mid, 0.0) + stock_units
        name = it.get("name") or it.get("medicine_name") or ""
        item_docs.append({
            "medicine_id": mid,
            "name": name,
            "medicine_name": name,
            "qty": qty,
            "free_qty": free_qty,
            "rate": float(it.get("rate") or 0),
            "mrp": float(it.get("mrp") or 0),
            "amount": float(it.get("amount") or it.get("item_amount") or 0),
            "item_amount": float(it.get("item_amount") or it.get("amount") or 0),
            "gst_percent": float(it.get("gst_percent") or it.get("gst_pct") or it.get("gst_value") or 0),
            "gst_pct": float(it.get("gst_pct") or it.get("gst_percent") or it.get("gst_value") or 0),
            "batch_no": it.get("batch_no") or it.get("batch") or "",
            "expiry_date": _expiry_db(it.get("expiry_date") or it.get("expiry")),
            "type": it.get("type") or "",
            "manufacturer": it.get("manufacturer") or "",
            "schedule": it.get("schedule") or "",
            "hsn_code": it.get("hsn_code") or "",
            "discount_pct": float(it.get("discount_pct") or 0),
            "taxable": float(it.get("taxable") or 0),
            "gst_amt": float(it.get("gst_amt") or 0),
            "unit": unit_val,
            "tablets_per_stripe": it.get("tablets_per_stripe"),
            "quantity_value": it.get("quantity_value") or unit_val,
            "_unit_val": unit_val,
        })

    # The old side is what this bill actually put on the shelf, as the stock ledger logged
    # it. Worked out again from the lines, it was not: a build that logged one of several
    # lines on one medicine id had its edit take off all of them (store 4, 17585: +3 saved,
    # -4,905 edited). A medicine whose save is not in the ledger (older than it) is still
    # read from its lines.
    #
    # Only a medicine with several lines on the stored bill is asked about. A single line is
    # exactly what every build logged for it, so its lines answer the same; and the ledger has
    # no per-bill query, so asking pages the store's movements from the purchase date on --
    # up to 20 s on a slow store, on the Tk thread for a Classic Purchase History edit. An
    # ordinary edit never waits on it.
    several_lines = {mid for mid, count in old_lines_by_med.items() if count > 1}
    if existing and several_lines:
        logged = _logged_purchase_units(
            int(purchase_id),
            [existing.get("purchase_date"), date_s],
            several_lines,
            client_uuid=cu,
        )
        for mid, units in (logged or {}).items():
            old_by_med[mid] = units

    ver = int(existing.get("version") or 1) + 1
    touched = set(old_by_med) | set(new_by_med)
    # The pack the lines offer each medicine row: a strip pack before a loose one, so a bill
    # with a strip line and a loose line on one batch never offers the loose "1".
    unit_by_med: dict[int, str] = {}
    type_by_med: dict[int, str] = {}
    for it in item_docs:
        if not it.get("_unit_val"):
            continue
        mid_u = int(it["medicine_id"])
        type_by_med.setdefault(mid_u, str(it.get("type") or ""))
        held_u = unit_by_med.get(mid_u)
        if held_u is None or _batch_row_pack(held_u, it["_unit_val"], it.get("type")) != held_u:
            unit_by_med[mid_u] = str(it["_unit_val"])
    # Editing a bill corrected the BILL but never the medicine: this path only
    # ever adjusted stock, so fixing a mistyped rate, MRP, batch or expiry left
    # the inventory row holding the wrong value forever. A corrected expiry is
    # the one that matters most -- the medicine would keep the wrong date and
    # never flag as expiring. Only fields the edited line actually supplies are
    # applied, so an omitted field is left alone rather than blanked.
    attrs_by_med: dict[int, dict[str, Any]] = {}
    for it in item_docs:
        try:
            mid_a = int(it.get("medicine_id") or 0)
        except (TypeError, ValueError):
            continue
        if mid_a <= 0:
            continue
        keep: dict[str, Any] = {}
        for num_key in ("rate", "mrp", "gst_percent"):
            try:
                val = float(it.get(num_key) or 0)
            except (TypeError, ValueError):
                val = 0.0
            if val > 0:
                keep[num_key] = val
        for txt_key in ("batch_no", "expiry_date", "hsn_code", "type",
                        "manufacturer", "schedule"):
            val_s = str(it.get(txt_key) or "").strip()
            if val_s:
                keep[txt_key] = val_s
        if keep:
            attrs_by_med[mid_a] = keep
    supplier_note = _supplier_name_for(None, supplier_id)
    for mid in touched:
        mp = medicine_by_id(mid) or get_doc("medicines", mid) or {
            "id": mid, "local_id": mid, "name": "", "stock_qty": 0,
        }
        mp = dict(mp)
        mp["id"] = mid
        mp["local_id"] = mid
        delta = float(new_by_med.get(mid, 0.0)) - float(old_by_med.get(mid, 0.0))
        # An edited bill still says where the stock came from. Only when the
        # line is putting stock on the shelf: taking it back must not rewrite
        # the note.
        if supplier_note and delta > 0:
            mp["supplier_name"] = supplier_note
        if skip_stock_ops:
            delta = 0.0
        for _k, _v in (attrs_by_med.get(mid) or {}).items():
            mp[_k] = _v
        prev_unit = str(mp.get("unit") or "")
        if unit_by_med.get(mid):
            # Each line is counted by its own pack; the batch row keeps its own.
            kept_pack = _batch_row_pack(
                prev_unit, unit_by_med[mid], type_by_med.get(mid) or mp.get("type")
            )
            if kept_pack:
                mp["unit"] = kept_pack
        unit_changed = str(mp.get("unit") or "") != prev_unit
        if delta > 0 and (mp.get("from_quick_sale") or mp.get("provisional_stock")):
            try:
                if float(mp.get("stock_qty") or 0) > 0:
                    # Replace Add-No-Stock leftover with purchase stock.
                    mp["stock_qty"] = 0.0
            except Exception:
                mp["stock_qty"] = 0.0
            mp["from_quick_sale"] = False
            mp["provisional_stock"] = False
        try:
            mp["stock_qty"] = float(mp.get("stock_qty") or 0) + delta
        except Exception:
            pass
        mp["is_hidden"] = False
        mp["deleted"] = False
        if abs(delta) > 1e-9:
            mp["stock_ops"] = [{
                "op_uuid": f"purchase:{cu}:med:{mid}:edit:v{ver}",
                "op": "purchase_edit",
                "qty_delta": int(round(delta)),
                "medicine_id": mid,
                "ref_collection": "purchases",
                "ref_id": int(purchase_id),
                "device_id": _device_id(),
            }]
            med_docs.append(bump_meta(mp))
        elif unit_changed:
            med_docs.append(bump_meta(mp))
        else:
            med_docs.append(_meta(mp))

    for it in item_docs:
        it.pop("_unit_val", None)

    supplier = find_supplier_by_id(int(supplier_id)) or get_doc("suppliers", int(supplier_id)) or {
        "id": int(supplier_id), "local_id": int(supplier_id),
    }
    supplier = bump_meta(supplier)
    supplier["id"] = int(supplier_id)
    supplier["local_id"] = int(supplier_id)
    supplier["total_due"] = calc_result.get("total_due", supplier.get("total_due"))

    preserved_no = str(existing.get("purchase_no") or "").strip()
    doc = dict(existing)
    doc.update({
        "id": int(purchase_id),
        "local_id": int(purchase_id),
        "client_uuid": cu,
        "purchase_no": preserved_no,
        "fy_start_year": existing.get("fy_start_year") or doc.get("fy_start_year"),
        "fy_serial": existing.get("fy_serial") or doc.get("fy_serial"),
        "supplier_id": int(supplier_id),
        "supplier_name": supplier.get("name") or existing.get("supplier_name") or "",
        "purchase_date": date_s,
        "bill_number": bill_number or "",
        "subtotal": calc_result.get("subtotal"),
        "total_gst": calc_result.get("total_gst"),
        "cgst": calc_result.get("cgst"),
        "sgst": calc_result.get("sgst"),
        "total_amount": calc_result.get("total_amount"),
        "overall_discount": calc_result.get("overall_discount"),
        "rounding": calc_result.get("rounding"),
        "need_to_pay": calc_result.get("need_to_pay"),
        "final_amount": calc_result.get("final_amount"),
        "amount_paid": entry_paid,
        "amount_paid_at_entry": entry_paid,
        "cash_paid_at_entry": cash_paid,
        "online_paid_at_entry": online_paid,
        "expenditure": round(float(calc_result.get("expenditure", 0) or 0), 2),
        "previous_due": calc_result.get("previous_due"),
        "previous_credit": calc_result.get("previous_credit"),
        "due": calc_result.get("due"),
        "current_credit": calc_result.get("current_credit"),
        "total_due": calc_result.get("total_due"),
        "bill_cleared": calc_result.get("bill_cleared"),
        "account_cleared": calc_result.get("account_cleared"),
        "due_amount": calc_result.get("due"),
        "credit_amount": calc_result.get("current_credit"),
        "gst_calc_method": (
            calc_result.get("gst_calc_method")
            or existing.get("gst_calc_method")
            or "discount_after_gst"
        ),
        "items": item_docs,
        "_suppliers": [supplier],
        "_medicines": med_docs,
    })
    if not doc.get("purchase_no"):
        doc["purchase_no"] = preserved_no
    if not str(doc.get("purchase_no") or "").strip():
        # Still nothing -- the bill we were editing came back without its number
        # (a catalogue that had not caught up, say). Sending it out blank lets
        # the server fall back to the row's internal id, and a bill came out
        # numbered "3019/FY2026-27" instead of 104. Ask for a real number.
        from core import server_api as _api
        from core.fy_serial import encode_purchase_no as _encode
        from core.fy_serial import fy_start_year_for_date as _fy_for

        _alloc = _api.allocate_fy(_api.store_token_for_active(), "purchases", date_s) or {}
        _ser = int(_alloc.get("fy_serial") or 0)
        _fy = int(_alloc.get("fy_start_year") or _fy_for(purchase_date))
        if _ser <= 0:
            raise RuntimeError(
                f"Purchase {purchase_id} has no bill number and the server did "
                f"not allocate one. Nothing was saved -- try again."
            )
        doc["purchase_no"] = (_alloc.get("purchase_no") or "").strip() or _encode(_ser, _fy)
        doc["fy_start_year"] = _fy
        doc["fy_serial"] = _ser
        print(f"[PURCHASE] bill {purchase_id} had no number; allocated {doc['purchase_no']}")
    doc = bump_meta(doc)
    if edit_base:
        # A purchase the store skips is put over the store's copy, or refused with the reason
        # (server_crud.save_new_purchase_online -> land_skipped_purchase_edit).
        doc["_edit_base"] = edit_base
    save_new_purchase_online(doc)
    doc.pop("_edit_base", None)
    try:
        patch_docs("purchases", [doc])
        if med_docs:
            patch_docs("medicines", med_docs)
        patch_docs("suppliers", [supplier])
    except Exception:
        pass
    try:
        hide_online_zero_stock_duplicates()
    except Exception:
        pass
    # The number the purchase is filed under, for the save's answer. Online the engine's
    # SQLite is empty, so the caller cannot read it back there.
    return str(doc.get("purchase_no") or "")


def save_purchase(conn, supplier_id: int, purchase_date_str: str,
                  bill_number: str, calc_result: dict,
                  items: list) -> str:
    """
    Insert purchase header + items, update stock, recalculate supplier due.
    calc_result must be the dict returned by PurchaseCalculator.calculate().
    Returns the generated purchase_no (display form).

    Online: enqueue mutation and return immediately (background push).
    """
    from core.online_guard import ensure_can_mutate, commit_local_then_push
    from core.sync_prefs import is_online_mode

    if is_online_mode():
        from core.online_guard import ensure_can_mutate as _ensure_online

        _ensure_online()
        return save_purchase_online_now(
            int(supplier_id),
            purchase_date_str,
            bill_number or "",
            calc_result,
            items,
        )

    ensure_can_mutate()

    cur = conn.cursor()

    try:
        purchase_date = datetime.strptime(purchase_date_str, '%Y-%m-%d').date()
    except ValueError:
        purchase_date = datetime.now().date()

    purchase_no = _allocate_purchase_number(conn, purchase_date=purchase_date)
    cash_paid, online_paid, entry_paid = payment_split_from_calc(calc_result)

    gst_calc_method = (
        calc_result.get('gst_calc_method')
        or 'discount_after_gst'
    )

    cur.execute("""
        INSERT INTO purchases (
            purchase_no, supplier_id, purchase_date, bill_number,
            subtotal, total_gst, cgst, sgst, total_amount,
            overall_discount, rounding, need_to_pay, final_amount,
            amount_paid, amount_paid_at_entry, cash_paid_at_entry, online_paid_at_entry,
            expenditure,
            previous_due, previous_credit, due, current_credit, total_due,
            bill_cleared, account_cleared,
            due_amount, credit_amount, gst_calc_method
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        purchase_no, supplier_id, purchase_date, bill_number,
        calc_result['subtotal'], calc_result['total_gst'],
        calc_result['cgst'], calc_result['sgst'], calc_result['total_amount'],
        calc_result['overall_discount'], calc_result['rounding'],
        calc_result['need_to_pay'], calc_result['final_amount'],
        entry_paid, entry_paid, cash_paid, online_paid,
        round(float(calc_result.get('expenditure', 0) or 0), 2),
        calc_result['previous_due'], calc_result['previous_credit'],
        calc_result['due'], calc_result['current_credit'], calc_result['total_due'],
        calc_result['bill_cleared'], calc_result['account_cleared'],
        calc_result['due'], calc_result['current_credit'],
        gst_calc_method,
    ))
    purchase_id = cur.lastrowid
    from core.fy_serial import patch_purchase_fy_fields

    patch_purchase_fy_fields(cur, purchase_id, purchase_no, purchase_date)

    _insert_items(cur, purchase_id, items, conn=conn, supplier_id=supplier_id)

    if is_online_mode():
        recalculate_supplier_due(conn, supplier_id, commit=False)

        def _push():
            from core.sync_coordinator import push_purchase_now
            return push_purchase_now(conn, purchase_id, commit_meta=False)

        pushed = commit_local_then_push(conn, _push)
        try:
            from core.sync_coordinator import after_purchase_saved
            after_purchase_saved(conn, purchase_id, already_pushed=bool(pushed))
        except Exception:
            pass
        # Mark pending sync when push failed (row still kept).
        if not pushed:
            try:
                from core.sync_v3.schema import set_sync_state
                set_sync_state(conn, "purchases", purchase_id, "pending_sync")
            except Exception:
                pass
    else:
        from core.sync_coordinator import stamp_purchase_meta
        stamp_purchase_meta(conn, purchase_id, commit=False)
        conn.commit()
        recalculate_supplier_due(conn, supplier_id)
        try:
            from core.sync_coordinator import after_purchase_saved
            after_purchase_saved(conn, purchase_id)
        except Exception:
            pass

    from core.fy_serial import display_purchase_no

    try:
        from core.sync_v3.data_change_bus import emit

        emit("purchases", local=True)
    except Exception:
        pass

    return display_purchase_no(purchase_no)


def update_purchase(conn, purchase_id: int, supplier_id: int,
                    bill_number: str, purchase_date_str: str,
                    calc_result: dict, items: list):
    """
    Update an existing purchase.
    Correctly reverses old stock BEFORE inserting new items.

    Online: enqueue mutation and return immediately (background push).
    """
    from core.online_guard import ensure_can_mutate, commit_local_then_push
    from core.sync_prefs import is_online_mode

    if is_online_mode():
        from core.online_guard import ensure_can_mutate as _ensure_online
        from core.online_mutation_queue import pending_by_local_id

        _ensure_online()
        pid = int(purchase_id or 0)
        cu = ""
        existing = {}
        try:
            from core.server_crud import get_doc
            if pid > 0:
                existing = get_doc("purchases", pid) or {}
                cu = str(existing.get("client_uuid") or "")
        except Exception:
            existing = {}
        if pid < 0:
            pending = pending_by_local_id("purchases", pid)
            if pending:
                cu = str(pending.get("client_uuid") or "")
        # The number the store holds for this purchase: the caller's answer to the screen.
        return update_purchase_online_now(
            pid,
            int(supplier_id),
            bill_number or "",
            purchase_date_str,
            calc_result,
            items,
            client_uuid=cu or "",
            purchase_no=str(existing.get("purchase_no") or ""),
            fy_start_year=existing.get("fy_start_year"),
            fy_serial=existing.get("fy_serial"),
        )
        return

    ensure_can_mutate()

    cur = conn.cursor()

    cur.execute('SELECT supplier_id FROM purchases WHERE id=?', (purchase_id,))
    old_supplier_row = cur.fetchone()
    old_supplier_id = int(old_supplier_row[0]) if old_supplier_row and old_supplier_row[0] else 0

    try:
        purchase_date = datetime.strptime(purchase_date_str, '%Y-%m-%d').date()
    except ValueError:
        purchase_date = datetime.now().date()

    cash_paid, online_paid, entry_paid = payment_split_from_calc(calc_result)

    _reverse_stock_for_purchase(cur, purchase_id)

    gst_calc_method = (
        calc_result.get('gst_calc_method')
        or 'discount_after_gst'
    )

    cur.execute("""
        UPDATE purchases SET
            supplier_id=?, purchase_date=?, bill_number=?,
            subtotal=?, total_gst=?, cgst=?, sgst=?, total_amount=?,
            overall_discount=?, rounding=?, need_to_pay=?, final_amount=?,
            amount_paid=?, amount_paid_at_entry=?, cash_paid_at_entry=?, online_paid_at_entry=?,
            expenditure=?,
            previous_due=?, previous_credit=?, due=?, current_credit=?, total_due=?,
            bill_cleared=?, account_cleared=?,
            due_amount=?, credit_amount=?, gst_calc_method=?
        WHERE id=?
    """, (
        supplier_id, purchase_date, bill_number,
        calc_result['subtotal'], calc_result['total_gst'],
        calc_result['cgst'], calc_result['sgst'], calc_result['total_amount'],
        calc_result['overall_discount'], calc_result['rounding'],
        calc_result['need_to_pay'], calc_result['final_amount'],
        entry_paid, entry_paid, cash_paid, online_paid,
        round(float(calc_result.get('expenditure', 0) or 0), 2),
        calc_result['previous_due'], calc_result['previous_credit'],
        calc_result['due'], calc_result['current_credit'], calc_result['total_due'],
        calc_result['bill_cleared'], calc_result['account_cleared'],
        calc_result['due'], calc_result['current_credit'],
        gst_calc_method,
        purchase_id,
    ))

    # Keep the financial-year stamp in step with the date that was just saved.
    # This was missing entirely: a purchase edited into the next FY kept the old
    # fy_start_year while its date fell in the new year's window, so the new
    # year's numbering picked up the old serial and jumped.
    try:
        from core.fy_serial import patch_purchase_fy_fields, resync_purchase_fy_number

        row = cur.execute(
            "SELECT COALESCE(purchase_no,'') FROM purchases WHERE id=?", (purchase_id,)
        ).fetchone()
        purchase_no = str(row[0]) if row else ""
        if purchase_no:
            purchase_no = resync_purchase_fy_number(
                cur, conn, purchase_id, purchase_no, purchase_date_str
            )
            patch_purchase_fy_fields(cur, purchase_id, purchase_no, purchase_date_str)
    except Exception:
        pass

    cur.execute("DELETE FROM purchase_items WHERE purchase_id=?", (purchase_id,))
    _insert_items(cur, purchase_id, items, conn=conn, supplier_id=supplier_id)

    recalculate_supplier_due(conn, supplier_id, commit=False)
    if old_supplier_id and old_supplier_id != supplier_id:
        recalculate_supplier_due(conn, old_supplier_id, commit=False)
    cur.execute(
        "UPDATE purchases SET bill_cleared=COALESCE(account_cleared,0) WHERE id=?",
        (purchase_id,),
    )

    from core.sync_coordinator import stamp_purchase_meta
    stamp_purchase_meta(conn, purchase_id, commit=False)
    conn.commit()
    try:
        from core.sync_coordinator import after_purchase_saved
        after_purchase_saved(conn, purchase_id)
    except Exception:
        pass



# ── Items + stock ─────────────────────────────────────────────────────────────

def _line_has_own_pack(item: dict) -> bool:
    """True when a purchase line says its own pack: a Tabs/Strip figure or pack text.

    The Purchase screen sends unit, quantity_value and tablets_per_stripe on every line; the
    Classic form, the phone import and web entry send tablets_per_stripe. Only a stored line
    the server holds without unit and tablets_per_stripe says nothing.
    """
    try:
        if float(item.get("tablets_per_stripe") or 0) > 0:
            return True
    except (TypeError, ValueError):
        pass
    return any(str(item.get(k) or "").strip() for k in ("unit", "quantity_value", "pack"))


def _supplier_name_for(conn, supplier_id) -> str:
    """The supplier's name, for the note a medicine keeps about where it came from.

    Opening stock writes medicines.supplier_name because it has no bill behind
    it. A purchase has one, and used to leave the field empty -- so a shop that
    bought a medicine through the product still could not see who sold it
    without opening the purchase history. Reference only: the due is the
    purchase's business, not this column's.
    """
    try:
        sid = int(supplier_id or 0)
    except (TypeError, ValueError):
        return ""
    if sid <= 0:
        return ""
    try:
        from core.sync_prefs import is_online_mode

        if is_online_mode():
            from core.online_catalog import find_supplier_by_id
            from core.server_crud import get_doc

            doc = find_supplier_by_id(sid) or get_doc("suppliers", sid) or {}
            return str(doc.get("name") or "").strip()
        if conn is None:
            return ""
        row = conn.execute(
            "SELECT name FROM suppliers WHERE id=?", (sid,)
        ).fetchone()
        return str(row[0]).strip() if row and row[0] else ""
    except Exception:
        return ""


def _batch_row_pack(row_unit, line_pack, med_type) -> str:
    """The pack a medicine (batch) row keeps after a purchase line with ``line_pack``.

    Each purchase line is stocked by its own pack; the batch row keeps its own. A strip row that
    already has a real pack (more than 1 per strip) keeps it -- 5 loose tablets of a strip-of-10
    batch must not turn the batch into strips of 1 for every later sale. A row with no pack yet
    (a new batch is created with unit "1") takes the line's strip pack. Non-strip types are
    left as they were: the line's pack text is written.
    """
    line = str(line_pack or "").strip()
    row = str(row_unit or "").strip()
    if not is_strip_count_type(str(med_type or "")):
        return line or row
    if not row:
        return line

    def _tps(text: str) -> int:
        try:
            return int(parse_tablets_per_stripe(text)) if text else 0
        except Exception:
            return 0

    if _tps(row) > 1:
        return row
    return line if _tps(line) > 1 else (row or line)


def _enrich_purchase_item_for_stock(item: dict) -> None:
    """Fill type/pack from the medicine catalog when the purchase line omits them.

    Server purchase items often have qty but no unit / tablets_per_stripe. Treating
    pack as 1 then made edits add tablet stock on top of the original strip stock.

    Only a line with NO pack of its own is given the catalogue's. A strip medicine bought
    loose (Tabs/Strip 1) arrives with unit / quantity_value "1" and tablets_per_stripe 1 but
    no ``pack`` key; filling that one key with the catalogue's "10" let the strip-size rule
    pick 10 over the typed 1, and 5 loose tablets went on the shelf as 50 (store 4 MOLICOLD,
    store 127 ORAFAST). The same happened when an edit or a delete read a stored loose line
    back, so it was taken off as 50 too.
    """
    mid = 0
    try:
        mid = int(item.get('medicine_id') or item.get('id') or 0)
    except (TypeError, ValueError):
        mid = 0
    mp = None
    if mid > 0:
        try:
            from core.online_catalog import medicine_by_id
            from core.server_crud import get_doc

            mp = medicine_by_id(mid) or get_doc('medicines', mid)
        except Exception:
            mp = None
    if isinstance(mp, dict):
        if not str(item.get('type') or '').strip():
            item['type'] = mp.get('type') or ''
        unit = str(mp.get('unit') or '').strip()
        if unit and not _line_has_own_pack(item):
            item['unit'] = unit
            item['quantity_value'] = unit
            item['pack'] = unit


def _normalize_purchase_item_stock_fields(item: dict) -> None:
    """Ensure strip/tablet lines have total_tablets before stock update."""
    _enrich_purchase_item_for_stock(item)
    med_type = str(item.get('type') or '')
    if not is_strip_count_type(med_type):
        return

    from core.bill_import_normalize import (
        pack_is_volume_or_weight,
        tablets_per_strip_from_pack,
    )

    tps = 0
    chosen_pack = ""
    for raw in (
        item.get("tablets_per_stripe"),
        item.get("pack"),
        item.get("quantity_value"),
        item.get("unit"),
    ):
        if raw in (None, ""):
            continue
        hint = str(raw).strip()
        if pack_is_volume_or_weight(hint):
            continue  # e.g. 200ML from a syrup row — never use as strip size
        # A bare number the operator typed (or Tabs/Strip itself) is explicit and
        # may be large -- a 100-tablet pack is real. A number dug out of free-text
        # pack wording is a guess and keeps the tight ceiling. Without this flag
        # the value was normalised to 1 here, BEFORE the stock arithmetic ever saw
        # it, so 5 x 100 was still booked as 5 tablets.
        explicit = False
        try:
            if isinstance(raw, (int, float)) or re.match(r"^\d+(\.\d+)?$", hint):
                n = int(float(hint))
                explicit = True
            else:
                n = int(tablets_per_strip_from_pack(hint))
        except (TypeError, ValueError):
            try:
                n = int(parse_tablets_per_stripe(hint))
            except Exception:
                n = 0
        n = _sanitize_strip_tps(med_type, n, hint, explicit=explicit)
        if n > 1:
            tps = n
            chosen_pack = hint
            break
        if n == 1 and tps <= 0:
            tps = 1
            chosen_pack = hint
    if tps <= 0:
        tps = 1
    item["tablets_per_stripe"] = tps
    item["unit"] = str(tps)
    if chosen_pack and not pack_is_volume_or_weight(chosen_pack):
        item["pack"] = chosen_pack if not str(chosen_pack).isdigit() else str(tps)

    qty = float(item.get("qty", 0) or 0)
    free_qty = float(item.get("free_qty", 0) or 0)
    item["total_tablets"] = qty * tps
    if free_qty:
        item["free_tablets"] = free_qty * tps
    elif "free_tablets" in item:
        item["free_tablets"] = 0


def _ensure_purchase_line_medicine_ids(conn, items: list) -> None:
    """Create/match medicines for every purchase line before Online enqueue/flush.

    Desktop PurchasePage sends medicine_id=null on new lines. Offline save
    resolves IDs in _insert_items; Online enqueue previously skipped that, so
    the bill reached Purchase History but inventory never received stock.
    """
    for item in items or []:
        if not isinstance(item, dict):
            continue
        if not str(item.get("name") or "").strip():
            item["name"] = str(
                item.get("medicine") or item.get("medicine_name") or ""
            ).strip()
        if not str(item.get("batch") or "").strip():
            item["batch"] = str(item.get("batch_no") or "").strip()
        if not str(item.get("expiry") or "").strip():
            item["expiry"] = str(item.get("expiry_date") or "").strip()
        if not str(item.get("name") or "").strip():
            continue
        _normalize_purchase_item_stock_fields(item)
        _resolve_purchase_item_medicine_id(conn, item)


def _medicine_doc_matches_line(doc: dict | None, name: str, batch: str) -> bool:
    """True when an existing medicines row is the same product+batch as this line."""
    if not isinstance(doc, dict):
        return False
    want = _medicine_match_key(name, batch)
    got = _medicine_match_key(
        doc.get("name") or "",
        doc.get("batch_no") or doc.get("batch") or "",
    )
    return bool(want[0] and want[1] and want == got)


def _adopt_quick_sale_placeholder(name: str, batch: str) -> int:
    """Reuse the Add-No-Stock row for this medicine instead of creating a second one.

    "Add No Stock" sells something that is not in inventory yet. There is no
    supplier batch to record at that moment, so the row is filed under whatever
    the counter typed and its stock goes NEGATIVE. When the delivery arrives it
    carries the SUPPLIER's batch, which does not match, and get_or_create_medicine
    below therefore made a second row: one carrying the debt for ever, one
    carrying the new stock, and nothing to reconcile them. That is the "two
    separate rows for one medicine" the counter keeps reporting.

    Adopting the existing row keeps one medicine and one id -- the sale bill still
    points at it, so nothing about that bill changes -- and the purchase settles
    the debt by plain addition (-6 + 100 = 94). The caller's medicine document
    then rewrites batch_no/expiry_date to the supplier's, which is what the row
    should have carried all along.

    Only a STRICTLY NEGATIVE row qualifies: nothing but an Add-No-Stock sale can
    drive stock below zero, so this cannot swallow a real batch that merely ran
    out. Returns 0 when there is nothing to adopt.
    """
    try:
        from core.sync_prefs import is_online_mode

        if not is_online_mode():
            return 0
    except Exception:
        return 0
    nm = (name or "").strip()
    if not nm:
        return 0
    try:
        from core.online_catalog import medicines_for_name_match

        rows = medicines_for_name_match(nm) or []
    except Exception:
        return 0
    want = _medicine_match_key(nm, batch)
    best = 0
    for row in rows:
        if not isinstance(row, dict) or row.get("deleted"):
            continue
        if float(row.get("stock_qty") or 0) >= 0:
            continue
        # A row already matching this exact batch is handled by the normal path.
        if _medicine_match_key(
            row.get("name") or "", row.get("batch_no") or row.get("batch") or ""
        ) == want:
            return 0
        try:
            rid = int(row.get("id") or row.get("local_id") or 0)
        except (TypeError, ValueError):
            continue
        if rid > 0 and (best == 0 or rid < best):
            best = rid  # oldest shortage first
    return best


def _resolve_purchase_item_medicine_id(conn, item: dict) -> int:
    """
    Match purchase line to medicines row (name + batch + expiry).
    Re-resolves at save time so stock and purchase history use the correct batch.
    """
    name = (item.get('name') or item.get('medicine') or item.get('medicine_name') or '').strip()
    batch = (item.get('batch') or item.get('batch_no') or '').strip()
    expiry = (item.get('expiry') or item.get('expiry_date') or '').strip()
    if not name or not batch:
        raise ValueError("Each purchase line needs a medicine name and batch before saving.")
    item['name'] = name
    item['batch'] = batch
    item['expiry'] = expiry

    try:
        existing_id = int(item.get("medicine_id") or item.get("id") or 0)
    except (TypeError, ValueError):
        existing_id = 0
    if existing_id > 0:
        # Never trust a client/import medicine_id unless name+batch match.
        # INV 17205 linked GLIZID to ZINCOVIT's id because we only checked "id exists".
        try:
            from core.sync_prefs import is_online_mode

            if is_online_mode():
                from core.online_catalog import medicine_by_id
                from core.server_crud import get_doc

                doc = medicine_by_id(existing_id) or get_doc("medicines", existing_id)
                if doc and _medicine_doc_matches_line(doc, name, batch):
                    item["medicine_id"] = existing_id
                    return existing_id
                # Wrong product, stale id, or missing — resolve by name+batch below.
                existing_id = 0
                item.pop("medicine_id", None)
                item.pop("id", None)
            else:
                cur = conn.cursor() if conn is not None else None
                if cur is not None:
                    cur.execute(
                        """
                        SELECT name, batch_no FROM medicines
                        WHERE id=? AND COALESCE(deleted, 0) = 0
                        """,
                        (existing_id,),
                    )
                    row = cur.fetchone()
                    if row and _medicine_doc_matches_line(
                        {"name": row[0], "batch_no": row[1]}, name, batch
                    ):
                        item["medicine_id"] = existing_id
                        return existing_id
                existing_id = 0
                item.pop("medicine_id", None)
                item.pop("id", None)
        except Exception:
            existing_id = 0
            item.pop("medicine_id", None)
            item.pop("id", None)

    adopted = _adopt_quick_sale_placeholder(name, batch)
    if adopted > 0:
        item['medicine_id'] = adopted
        return adopted

    old_id = item.get('medicine_id')
    med_id = get_or_create_medicine(
        conn,
        name,
        item.get('type') or '',
        batch,
        expiry,
        float(item.get('gst_pct', item.get('gst_value', item.get('gst_percent', 0))) or 0),
        float(item.get('mrp', 0) or 0),
        float(item.get('rate', 0) or 0),
        (item.get('manufacturer') or '').strip(),
        (item.get('hsn_code') or '').strip(),
        (item.get('schedule') or '').strip(),
        (item.get('content_drug') or '').strip(),
    )
    item['medicine_id'] = med_id
    if old_id and int(old_id) != int(med_id) and conn is not None:
        _remove_orphan_medicine_if_unused(conn, int(old_id))
    return med_id


def _remove_orphan_medicine_if_unused(conn, medicine_id: int) -> None:
    """Drop pre-import placeholder rows that never received stock or transactions."""
    if not medicine_id or conn is None:
        return
    cur = conn.cursor()
    cur.execute("SELECT COALESCE(stock_qty, 0) FROM medicines WHERE id=?", (medicine_id,))
    row = cur.fetchone()
    if not row or float(row[0] or 0) != 0:
        return
    cur.execute("SELECT COUNT(*) FROM purchase_items WHERE medicine_id=?", (medicine_id,))
    if int(cur.fetchone()[0] or 0) > 0:
        return
    cur.execute("SELECT COUNT(*) FROM sales_items WHERE medicine_id=?", (medicine_id,))
    if int(cur.fetchone()[0] or 0) > 0:
        return
    cur.execute("DELETE FROM medicines WHERE id=?", (medicine_id,))


def _insert_items(cur, purchase_id: int, items: list, conn=None, supplier_id=None):
    item_rows  = []
    stock_rows = []

    for item in items:
        if conn is not None:
            _normalize_purchase_item_stock_fields(item)
            _resolve_purchase_item_medicine_id(conn, item)
        else:
            _normalize_purchase_item_stock_fields(item)

        db_expiry = expiry_to_db(item['expiry'])
        med_id = item['medicine_id']
        item_rows.append((
            purchase_id, med_id,
            item['qty'], item['free_qty'], item['type'],
            item.get('hsn_code', ''), item.get('gst_pct', item.get('gst_value', 0)),
            item.get('mrp', 0), item['rate'],
            item.get('manufacturer', ''), item['batch'], db_expiry,
            item.get('schedule', ''),
            item.get('discount_pct', item.get('item_discount', 0)),
            item.get('taxable', 0),
            item.get('gst_amt', 0),
            item.get('item_amount', item.get('amount', 0)),
            # legacy aliases
            item.get('discount_pct', item.get('item_discount', 0)),
            item.get('gst_pct', item.get('gst_value', 0)),
            item.get('item_amount', item.get('amount', 0)),
        ))
        stock_increase = _get_stock_increase(item)
        stock_rows.append((_get_unit_value(item), stock_increase, med_id, item.get('type')))

    # Each line keeps the stock it added, so an edit or a delete takes back exactly that.
    if "stock_units" in _purchase_item_columns(cur):
        item_rows = [
            row + (max(0.0, float(stock_rows[i][1] or 0)),) for i, row in enumerate(item_rows)
        ]
        cur.executemany("""
            INSERT INTO purchase_items (
                purchase_id, medicine_id, qty, free_qty, type,
                hsn_code, gst_pct, mrp, rate, manufacturer,
                batch_no, expiry_date, schedule,
                discount_pct, taxable, gst_amt, item_amount,
                discount_percent, gst_value, amount, stock_units
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, item_rows)
    else:
        cur.executemany("""
            INSERT INTO purchase_items (
                purchase_id, medicine_id, qty, free_qty, type,
                hsn_code, gst_pct, mrp, rate, manufacturer,
                batch_no, expiry_date, schedule,
                discount_pct, taxable, gst_amt, item_amount,
                discount_percent, gst_value, amount
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, item_rows)

    supplier_note = _supplier_name_for(conn, supplier_id)
    for unit_val, stock_delta, med_id, line_type in stock_rows:
        if stock_delta <= 0:
            continue
        # The line was stocked by its own pack; the batch row keeps its own (a loose line of
        # a strip-of-10 batch used to turn the batch into strips of 1).
        held = cur.execute(
            "SELECT unit, type FROM medicines WHERE id=?", (med_id,)
        ).fetchone()
        kept_pack = _batch_row_pack(
            held[0] if held else None, unit_val, line_type or (held[1] if held else "")
        )
        if supplier_note:
            cur.execute(
                "UPDATE medicines SET unit=?, stock_qty=stock_qty+?, is_hidden=0, "
                "supplier_name=? WHERE id=?",
                (kept_pack or unit_val, stock_delta, supplier_note, med_id),
            )
        else:
            cur.execute(
                "UPDATE medicines SET unit=?, stock_qty=stock_qty+?, is_hidden=0 WHERE id=?",
                (kept_pack or unit_val, stock_delta, med_id),
            )
        cur.execute("SELECT name FROM medicines WHERE id=?", (med_id,))
        name_row = cur.fetchone()
        if name_row and name_row[0]:
            cur.execute(
                "UPDATE medicines SET is_hidden=0 "
                "WHERE UPPER(TRIM(name))=UPPER(TRIM(?)) AND COALESCE(is_hidden, 0)=1",
                (name_row[0],),
            )


def _insert_items_no_stock(cur, purchase_id: int, items: list, conn=None):
    """Insert purchase_items without updating stock (autosave drafts)."""
    item_rows = []
    for item in items:
        if conn is not None:
            _normalize_purchase_item_stock_fields(item)
            _resolve_purchase_item_medicine_id(conn, item)
        else:
            _normalize_purchase_item_stock_fields(item)

        db_expiry = expiry_to_db(item['expiry'])
        med_id = item['medicine_id']
        item_rows.append((
            purchase_id, med_id,
            item['qty'], item['free_qty'], item['type'],
            item.get('hsn_code', ''), item.get('gst_pct', item.get('gst_value', 0)),
            item.get('mrp', 0), item['rate'],
            item.get('manufacturer', ''), item['batch'], db_expiry,
            item.get('schedule', ''),
            item.get('discount_pct', item.get('item_discount', 0)),
            item.get('taxable', 0),
            item.get('gst_amt', 0),
            item.get('item_amount', item.get('amount', 0)),
            item.get('discount_pct', item.get('item_discount', 0)),
            item.get('gst_pct', item.get('gst_value', 0)),
            item.get('item_amount', item.get('amount', 0)),
        ))

    cur.executemany("""
        INSERT INTO purchase_items (
            purchase_id, medicine_id, qty, free_qty, type,
            hsn_code, gst_pct, mrp, rate, manufacturer,
            batch_no, expiry_date, schedule,
            discount_pct, taxable, gst_amt, item_amount,
            discount_percent, gst_value, amount
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, item_rows)


def save_autosave_purchase(conn, supplier_id: int, purchase_date_str: str,
                           bill_number: str, calc_result: dict, items: list) -> tuple:
    """Insert autosave purchase draft — no stock change. Returns (purchase_no, purchase_id).

    Online: drafts stay device-local only (crash recovery); never pushed to server.
    If local write fails Online, no-op gracefully (finalize uses form data).
    """
    try:
        cur = conn.cursor()
        try:
            purchase_date = datetime.strptime(purchase_date_str, '%Y-%m-%d').date()
        except ValueError:
            purchase_date = datetime.now().date()

        purchase_no = _allocate_purchase_number(conn, "APU")
        cash_paid, online_paid, entry_paid = payment_split_from_calc(calc_result)
        gst_calc_method = calc_result.get('gst_calc_method') or 'discount_after_gst'

        cur.execute("""
            INSERT INTO purchases (
                purchase_no, supplier_id, purchase_date, bill_number,
                subtotal, total_gst, cgst, sgst, total_amount,
                overall_discount, rounding, need_to_pay, final_amount,
                amount_paid, amount_paid_at_entry, cash_paid_at_entry, online_paid_at_entry,
                expenditure,
                previous_due, previous_credit, due, current_credit, total_due,
                bill_cleared, account_cleared,
                due_amount, credit_amount, gst_calc_method, is_autosave
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)
        """, (
            purchase_no, supplier_id, purchase_date, bill_number,
            calc_result['subtotal'], calc_result['total_gst'],
            calc_result['cgst'], calc_result['sgst'], calc_result['total_amount'],
            calc_result['overall_discount'], calc_result['rounding'],
            calc_result['need_to_pay'], calc_result['final_amount'],
            entry_paid, entry_paid, cash_paid, online_paid,
            round(float(calc_result.get('expenditure', 0) or 0), 2),
            calc_result['previous_due'], calc_result['previous_credit'],
            calc_result['due'], calc_result['current_credit'], calc_result['total_due'],
            calc_result['bill_cleared'], calc_result['account_cleared'],
            calc_result['due'], calc_result['current_credit'],
            gst_calc_method,
        ))
        purchase_id = cur.lastrowid
        _insert_items_no_stock(cur, purchase_id, items, conn=conn)
        conn.commit()
        return purchase_no, purchase_id
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        try:
            from core.sync_prefs import is_online_mode
            if is_online_mode():
                return "", 0
        except Exception:
            pass
        raise


def update_autosave_purchase(conn, purchase_id: int, supplier_id: int,
                             bill_number: str, purchase_date_str: str,
                             calc_result: dict, items: list):
    """Update autosave purchase draft without stock changes.

    Online: drafts stay device-local only; missing row / wipe → no-op.
    """
    try:
        cur = conn.cursor()
        try:
            purchase_date = datetime.strptime(purchase_date_str, '%Y-%m-%d').date()
        except ValueError:
            purchase_date = datetime.now().date()

        cash_paid, online_paid, entry_paid = payment_split_from_calc(calc_result)
        gst_calc_method = calc_result.get('gst_calc_method') or 'discount_after_gst'

        cur.execute("""
            UPDATE purchases SET
                supplier_id=?, purchase_date=?, bill_number=?,
                subtotal=?, total_gst=?, cgst=?, sgst=?, total_amount=?,
                overall_discount=?, rounding=?, need_to_pay=?, final_amount=?,
                amount_paid=?, amount_paid_at_entry=?, cash_paid_at_entry=?, online_paid_at_entry=?,
                expenditure=?,
                previous_due=?, previous_credit=?, due=?, current_credit=?, total_due=?,
                bill_cleared=?, account_cleared=?,
                due_amount=?, credit_amount=?, gst_calc_method=?
            WHERE id=? AND COALESCE(is_autosave,0)=1
        """, (
            supplier_id, purchase_date, bill_number,
            calc_result['subtotal'], calc_result['total_gst'],
            calc_result['cgst'], calc_result['sgst'], calc_result['total_amount'],
            calc_result['overall_discount'], calc_result['rounding'],
            calc_result['need_to_pay'], calc_result['final_amount'],
            entry_paid, entry_paid, cash_paid, online_paid,
            round(float(calc_result.get('expenditure', 0) or 0), 2),
            calc_result['previous_due'], calc_result['previous_credit'],
            calc_result['due'], calc_result['current_credit'], calc_result['total_due'],
            calc_result['bill_cleared'], calc_result['account_cleared'],
            calc_result['due'], calc_result['current_credit'],
            gst_calc_method,
            purchase_id,
        ))
        cur.execute("DELETE FROM purchase_items WHERE purchase_id=?", (purchase_id,))
        _insert_items_no_stock(cur, purchase_id, items, conn=conn)
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        try:
            from core.sync_prefs import is_online_mode
            if is_online_mode():
                return
        except Exception:
            pass
        raise


def finalize_autosave_purchase(conn, purchase_id: int, supplier_id: int,
                               bill_number: str, purchase_date_str: str,
                               calc_result: dict, items: list) -> str:
    """Convert autosave draft to real purchase — apply stock, clear is_autosave.

    Online: drafts are local-only — always call save_purchase with form data
    when the draft is missing or convert would fail.
    """
    from core.online_guard import ensure_can_mutate, commit_local_then_push
    from core.sync_prefs import is_online_mode

    if is_online_mode():
        if purchase_id:
            try:
                cur = conn.cursor()
                cur.execute("DELETE FROM purchase_items WHERE purchase_id=?", (purchase_id,))
                cur.execute(
                    "DELETE FROM purchases WHERE id=? AND COALESCE(is_autosave,0)=1",
                    (purchase_id,),
                )
                conn.commit()
            except Exception:
                try:
                    conn.rollback()
                except Exception:
                    pass
        return save_purchase(
            conn, supplier_id, purchase_date_str, bill_number, calc_result, items,
        )

    ensure_can_mutate()

    cur = conn.cursor()
    cur.execute(
        "SELECT purchase_no, COALESCE(is_autosave,0) FROM purchases WHERE id=?",
        (purchase_id,),
    )
    row = cur.fetchone()
    if not row:
        return save_purchase(
            conn, supplier_id, purchase_date_str, bill_number, calc_result, items,
        )
    old_no = row[0] if row else ''

    try:
        purchase_date = datetime.strptime(purchase_date_str, '%Y-%m-%d').date()
    except ValueError:
        purchase_date = datetime.now().date()

    from core.fy_serial import display_purchase_no, encode_purchase_no, fy_start_year_for_date

    disp = display_purchase_no(old_no)
    if (old_no or '').upper().startswith('APU') or not disp.isdigit():
        purchase_no = _allocate_purchase_number(
            conn,
            purchase_date=purchase_date,
            exclude_purchase_id=purchase_id,
        )
    elif '/FY' in (old_no or ''):
        purchase_no = old_no
    else:
        purchase_no = encode_purchase_no(int(disp), fy_start_year_for_date(purchase_date))

    cash_paid, online_paid, entry_paid = payment_split_from_calc(calc_result)
    gst_calc_method = calc_result.get('gst_calc_method') or 'discount_after_gst'

    cur.execute("""
        UPDATE purchases SET
            purchase_no=?, supplier_id=?, purchase_date=?, bill_number=?,
            subtotal=?, total_gst=?, cgst=?, sgst=?, total_amount=?,
            overall_discount=?, rounding=?, need_to_pay=?, final_amount=?,
            amount_paid=?, amount_paid_at_entry=?, cash_paid_at_entry=?, online_paid_at_entry=?,
            expenditure=?,
            previous_due=?, previous_credit=?, due=?, current_credit=?, total_due=?,
            bill_cleared=?, account_cleared=?,
            due_amount=?, credit_amount=?, gst_calc_method=?, is_autosave=0
        WHERE id=?
    """, (
        purchase_no, supplier_id, purchase_date, bill_number,
        calc_result['subtotal'], calc_result['total_gst'],
        calc_result['cgst'], calc_result['sgst'], calc_result['total_amount'],
        calc_result['overall_discount'], calc_result['rounding'],
        calc_result['need_to_pay'], calc_result['final_amount'],
        entry_paid, entry_paid, cash_paid, online_paid,
        round(float(calc_result.get('expenditure', 0) or 0), 2),
        calc_result['previous_due'], calc_result['previous_credit'],
        calc_result['due'], calc_result['current_credit'], calc_result['total_due'],
        calc_result['bill_cleared'], calc_result['account_cleared'],
        calc_result['due'], calc_result['current_credit'],
        gst_calc_method,
        purchase_id,
    ))

    from core.fy_serial import patch_purchase_fy_fields

    patch_purchase_fy_fields(cur, purchase_id, purchase_no, purchase_date)

    cur.execute("DELETE FROM purchase_items WHERE purchase_id=?", (purchase_id,))
    _insert_items(cur, purchase_id, items, conn=conn, supplier_id=supplier_id)

    if is_online_mode():
        recalculate_supplier_due(conn, supplier_id, commit=False)

        def _push():
            from core.sync_coordinator import push_purchase_now
            return push_purchase_now(conn, purchase_id, commit_meta=False)

        pushed = commit_local_then_push(conn, _push)
        try:
            from core.sync_coordinator import after_purchase_saved
            after_purchase_saved(conn, purchase_id, already_pushed=bool(pushed))
        except Exception:
            pass
        if not pushed:
            try:
                from core.sync_v3.schema import set_sync_state
                set_sync_state(conn, "purchases", purchase_id, "pending_sync")
            except Exception:
                pass
    else:
        from core.sync_coordinator import stamp_purchase_meta
        stamp_purchase_meta(conn, purchase_id, commit=False)
        conn.commit()
        recalculate_supplier_due(conn, supplier_id)
        try:
            from core.sync_coordinator import after_purchase_saved
            after_purchase_saved(conn, purchase_id)
        except Exception:
            pass

    from core.fy_serial import display_purchase_no

    return display_purchase_no(purchase_no)


def fetch_recent_purchases(conn, limit=5):
    """Return list of (id, purchase_no, purchase_date, supplier_name, total_amount) — saved only."""
    cur = conn.cursor()
    cur.execute("""
        SELECT p.id, p.purchase_no, p.purchase_date, s.name, p.total_amount
        FROM purchases p JOIN suppliers s ON p.supplier_id=s.id
        WHERE COALESCE(p.is_autosave, 0) = 0
        ORDER BY p.purchase_date DESC, p.id DESC
        LIMIT ?
    """, (limit,))
    return cur.fetchall()


def fetch_last_purchase_id(conn):
    """Most recent saved (non-autosave) purchase — same order as Purchase History."""
    cur = conn.cursor()
    cur.execute("""
        SELECT p.id FROM purchases p
        WHERE COALESCE(p.is_autosave, 0) = 0
        ORDER BY p.purchase_date DESC, p.id DESC
        LIMIT 1
    """)
    row = cur.fetchone()
    return row[0] if row else None


def delete_autosave_purchase(conn, purchase_id: int) -> bool:
    """Remove an autosave purchase draft and its lines. Returns True if deleted."""
    cur = conn.cursor()
    cur.execute(
        "SELECT COALESCE(is_autosave, 0) FROM purchases WHERE id=?",
        (purchase_id,),
    )
    row = cur.fetchone()
    if not row or not int(row[0] or 0):
        return False
    cur.execute("DELETE FROM purchase_items WHERE purchase_id=?", (purchase_id,))
    cur.execute("DELETE FROM purchases WHERE id=?", (purchase_id,))
    conn.commit()
    return True 