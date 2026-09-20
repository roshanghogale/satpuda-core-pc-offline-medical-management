"""Apply Android mobile JSON imports without Tk (desktop API)."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any


def _parse_expiry_to_db(raw: str) -> str:
    """The loader's expiry, however the phone spelled it. See core/expiry_text.py.

    The old copy returned the raw text unchanged when it held no "/", so "0428"
    travelled on as "0428" and Postgres stored nothing for it.
    """
    from core.expiry_text import expiry_to_db

    return expiry_to_db(raw)


def detect_export_type(data: dict[str, Any]) -> str:
    export_type = str(data.get("export_type") or "").lower()
    if export_type in ("medicines", "purchases"):
        return export_type
    if data.get("medicines"):
        return "medicines"
    if data.get("purchases") or data.get("bills"):
        return "purchases"
    return ""


def preview_mobile_data(data: dict[str, Any]) -> dict[str, Any]:
    kind = detect_export_type(data)
    if not kind:
        return {
            "ok": False,
            "error": "Unknown format. Use export_type medicines or purchases.",
        }
    device = str(data.get("device_name") or "Unknown")
    export_date = str(data.get("export_date") or "")
    if kind == "medicines":
        meds = data.get("medicines") or []
        preview = [
            {
                "name": m.get("name", ""),
                "batch": m.get("batch_no", ""),
                "stock_qty": m.get("stock_qty", 0),
            }
            for m in meds[:12]
        ]
        return {
            "ok": True,
            "export_type": kind,
            "device_name": device,
            "export_date": export_date,
            "count": len(meds),
            "preview": preview,
        }
    purchases = data.get("purchases") or data.get("bills") or []
    preview = [
        {
            "bill_number": p.get("bill_number", ""),
            "supplier": (
                p.get("supplier_name")
                or (p.get("supplier") or {}).get("name", "")
            ),
            "items": len(p.get("items") or []),
        }
        for p in purchases[:12]
    ]
    return {
        "ok": True,
        "export_type": kind,
        "device_name": device,
        "export_date": export_date,
        "count": len(purchases),
        "preview": preview,
    }


def apply_mobile_data(conn, data: dict[str, Any]) -> dict[str, Any]:
    kind = detect_export_type(data)
    if kind == "medicines":
        return _apply_medicines(conn, data)
    if kind == "purchases":
        return _apply_purchases(conn, data)
    return {"ok": False, "error": "Unknown mobile import format."}


def _medicine_cols(cur) -> set[str]:
    cur.execute("PRAGMA table_info(medicines)")
    return {str(r[1]) for r in cur.fetchall()}


def _find_existing_medicine(cur, name: str, batch: str, db_expiry: str):
    """Match name+batch+expiry, then fall back to name+batch (expiry drift from phone)."""
    cur.execute(
        "SELECT id, stock_qty, name FROM medicines "
        "WHERE LOWER(TRIM(name)) = LOWER(TRIM(?)) AND TRIM(COALESCE(batch_no,''))=TRIM(?) "
        "AND TRIM(COALESCE(expiry_date,''))=TRIM(?) "
        "ORDER BY id DESC LIMIT 1",
        (name, batch, db_expiry),
    )
    row = cur.fetchone()
    if row:
        return row
    cur.execute(
        "SELECT id, stock_qty, name FROM medicines "
        "WHERE LOWER(TRIM(name)) = LOWER(TRIM(?)) AND TRIM(COALESCE(batch_no,''))=TRIM(?) "
        "ORDER BY id DESC LIMIT 1",
        (name, batch),
    )
    return cur.fetchone()


def _supplier_note(med: dict[str, Any]) -> str:
    """The supplier a loader row names, as a note on the medicine.

    Only a plain name counts. The purchase-shaped ``supplier`` dict some
    exports send belongs to a bill, and opening stock has no bill.
    """
    raw = med.get("supplier_name")
    if raw is None:
        raw = med.get("supplier")
    if isinstance(raw, dict):
        raw = raw.get("name")
    return str(raw or "").strip()


def _row_values(med: dict[str, Any]) -> dict[str, Any]:
    """One loader row -> the fields a medicine document holds, for either mode."""
    from core.name_utils import normalize_medicine_name
    from core.stock_utils import android_import_stock

    name = normalize_medicine_name(str(med.get("name") or "").strip())
    # The loader sometimes sends a blank batch -- still import, so the stock
    # shows up in Inventory instead of being silently dropped.
    batch = str(med.get("batch_no") or med.get("batch") or "").strip() or "-"
    med_type = str(med.get("type") or "")
    unit = str(med.get("unit") or "")
    return {
        "name": name,
        "batch": batch,
        "type": med_type,
        "unit": unit,
        "expiry": _parse_expiry_to_db(med.get("expiry_date", "")),
        "stock": android_import_stock(
            med_type, int(med.get("stock_qty", 0) or 0), unit,
            int(med.get("extra_medicine", 0) or 0),
        ),
        "mrp": float(med.get("mrp", 0) or 0),
        "rate": float(med.get("rate", 0) or 0),
        "gst": float(med.get("gst_percent", med.get("gst_pct", 0)) or 0),
        "hsn_code": med.get("hsn_code", ""),
        "manufacturer": med.get("manufacturer", ""),
        "schedule": med.get("schedule", ""),
        "content_drug": med.get("content_drug", ""),
        # Reference only. Nothing reads this into the supplier ledger --
        # opening stock owes nobody anything.
        "supplier_name": _supplier_note(med),
    }


def _apply_medicines_online(data: dict[str, Any]) -> dict[str, Any]:
    """The same import, for a shop whose medicines live on the server.

    Online mode keeps NOTHING in the engine's SQLite (it is :memory:), so the
    offline path below matched every row against an empty table: each one was
    inserted as new and pushed, which is how a loader import could double a
    shop's catalogue instead of filling in its stock. Here the shop's own
    server catalogue is what a row is matched against, and the write is the
    product's own medicine write (upsert_medicine_online), so an existing
    medicine is UPDATED under its own id.
    """
    from core import online_catalog
    from core.server_crud import upsert_medicine_online

    medicines = data.get("medicines") or []
    if not medicines:
        return {"ok": False, "error": "No medicines in JSON."}

    def _key(name: str, batch: str, expiry: str = "") -> tuple:
        return (str(name or "").strip().lower(), str(batch or "").strip(), str(expiry or "").strip())

    by_name_batch_expiry: dict[tuple, dict] = {}
    by_name_batch: dict[tuple, dict] = {}
    try:
        catalogue = online_catalog.medicines(force=True) or []
    except Exception as exc:
        return {"ok": False, "error": f"Could not read this shop's medicines from the server: {exc}"}
    for doc in catalogue:
        if doc.get("deleted"):
            continue
        nm, bt = doc.get("name") or "", doc.get("batch_no") or ""
        ex = str(doc.get("expiry_date") or "")
        # Highest id wins, exactly as the offline lookup's ORDER BY id DESC does.
        for index, key in ((by_name_batch_expiry, _key(nm, bt, ex)), (by_name_batch, _key(nm, bt))):
            seen = index.get(key)
            if not seen or int(doc.get("id") or 0) > int(seen.get("id") or 0):
                index[key] = doc

    inserted = updated = skipped = 0
    errors: list[str] = []
    for med in medicines:
        try:
            row = _row_values(med)
            if not row["name"]:
                skipped += 1
                errors.append("Skipped row: missing name")
                continue
            existing = (by_name_batch_expiry.get(_key(row["name"], row["batch"], row["expiry"]))
                        or by_name_batch.get(_key(row["name"], row["batch"])))
            doc = {
                "name": (str(existing.get("name")).strip()
                         if existing and str(existing.get("name") or "").strip() else row["name"]),
                "type": row["type"],
                "batch_no": row["batch"],
                "expiry_date": row["expiry"],
                "stock_qty": row["stock"],
                "unit": row["unit"],
                "mrp": row["mrp"],
                "rate": row["rate"],
                "gst_percent": row["gst"],
                "hsn_code": row["hsn_code"],
                "manufacturer": row["manufacturer"],
                "schedule": row["schedule"],
                "content_drug": row["content_drug"],
                "supplier_name": row["supplier_name"],
                # A row the shop is filling stock into belongs on the shelf again.
                "is_hidden": 0,
                "deleted": 0,
            }
            if existing:
                doc["id"] = int(existing.get("id") or existing.get("local_id") or 0)
                doc["local_id"] = doc["id"]
            upsert_medicine_online(doc)
            if existing:
                updated += 1
            else:
                inserted += 1
        except Exception as exc:
            errors.append(str(exc))
    return {
        "ok": True,
        "export_type": "medicines",
        "inserted": inserted,
        "updated": updated,
        "skipped": skipped,
        "errors": errors[:20],
        "message": (
            f"Imported medicines: {inserted} new, {updated} updated"
            + (f", {skipped} skipped" if skipped else "")
            + ". Inventory shows non-hidden stock rows — refresh Inventory."
        ),
    }


def _apply_medicines(conn, data: dict[str, Any]) -> dict[str, Any]:
    try:
        from core.sync_prefs import is_online_mode

        online = is_online_mode()
    except Exception:
        online = False
    if online:
        return _apply_medicines_online(data)
    from core.name_utils import normalize_medicine_name
    from core.purchase_service import get_or_create_supplier
    from core.stock_utils import android_import_stock

    medicines = data.get("medicines") or []
    if not medicines:
        return {"ok": False, "error": "No medicines in JSON."}
    cur = conn.cursor()
    cols = _medicine_cols(cur)
    has_hidden = "is_hidden" in cols
    has_deleted = "deleted" in cols
    inserted = updated = skipped = 0
    errors: list[str] = []
    touched_ids: list[int] = []
    for med in medicines:
        try:
            name = normalize_medicine_name(str(med.get("name") or "").strip())
            # Android sometimes sends blank batch — still import so stock appears in inventory.
            batch = str(med.get("batch_no") or med.get("batch") or "").strip() or "-"
            if not name:
                skipped += 1
                errors.append("Skipped row: missing name")
                continue
            sup = med.get("supplier")
            if isinstance(sup, dict) and sup.get("name", "").strip():
                get_or_create_supplier(
                    conn,
                    sup.get("name", "").strip(),
                    sup.get("address", ""),
                    sup.get("phone", ""),
                    sup.get("gstin", ""),
                    sup.get("dl_numbers", ""),
                )
            db_expiry = _parse_expiry_to_db(med.get("expiry_date", ""))
            med_type = str(med.get("type") or "")
            unit = str(med.get("unit") or "")
            extra = int(med.get("extra_medicine", 0) or 0)
            stock = android_import_stock(
                med_type,
                int(med.get("stock_qty", 0) or 0),
                unit,
                extra,
            )
            existing = _find_existing_medicine(cur, name, batch, db_expiry)
            mrp = float(med.get("mrp", 0) or 0)
            rate = float(med.get("rate", 0) or 0)
            gst = float(
                med.get("gst_percent", med.get("gst_pct", 0)) or 0
            )
            if existing:
                med_id, _, stored_name = existing
                if stored_name and str(stored_name).strip():
                    name = str(stored_name).strip()
                sets = [
                    "stock_qty=?",
                    "unit=?",
                    "mrp=?",
                    "rate=?",
                    "gst_percent=?",
                    "hsn_code=?",
                    "manufacturer=?",
                    "schedule=?",
                    "content_drug=?",
                    "type=?",
                    "expiry_date=?",
                    "batch_no=?",
                ]
                vals: list[Any] = [
                    stock,
                    unit,
                    mrp,
                    rate,
                    gst,
                    med.get("hsn_code", ""),
                    med.get("manufacturer", ""),
                    med.get("schedule", ""),
                    med.get("content_drug", ""),
                    med_type,
                    db_expiry,
                    batch,
                ]
                supplier_note = _supplier_note(med)
                if supplier_note:
                    sets.append("supplier_name=?")
                    vals.append(supplier_note)
                if has_hidden:
                    sets.append("is_hidden=0")
                if has_deleted:
                    sets.append("deleted=0")
                vals.append(int(med_id))
                cur.execute(
                    f"UPDATE medicines SET {', '.join(sets)} WHERE id=?",
                    vals,
                )
                touched_ids.append(int(med_id))
                updated += 1
            else:
                fields = [
                    "name",
                    "type",
                    "batch_no",
                    "expiry_date",
                    "stock_qty",
                    "unit",
                    "mrp",
                    "rate",
                    "gst_percent",
                    "hsn_code",
                    "manufacturer",
                    "schedule",
                    "content_drug",
                    "supplier_name",
                ]
                vals = [
                    name,
                    med_type,
                    batch,
                    db_expiry,
                    stock,
                    unit,
                    mrp,
                    rate,
                    gst,
                    med.get("hsn_code", ""),
                    med.get("manufacturer", ""),
                    med.get("schedule", ""),
                    med.get("content_drug", ""),
                    _supplier_note(med),
                ]
                if "location" in cols:
                    fields.append("location")
                    vals.append("")
                if has_hidden:
                    fields.append("is_hidden")
                    vals.append(0)
                if has_deleted:
                    fields.append("deleted")
                    vals.append(0)
                placeholders = ",".join("?" * len(fields))
                cur.execute(
                    f"INSERT INTO medicines ({', '.join(fields)}) VALUES ({placeholders})",
                    vals,
                )
                touched_ids.append(int(cur.lastrowid))
                inserted += 1
        except Exception as exc:
            errors.append(str(exc))

    # Stock import must surface in inventory even if rows were soft-hidden earlier.
    if has_hidden:
        cur.execute(
            "UPDATE medicines SET is_hidden=0 "
            "WHERE COALESCE(stock_qty,0)>0 AND COALESCE(is_hidden,0)=1"
        )
    conn.commit()

    sync_error = ""
    if touched_ids:
        try:
            from core.sync_prefs import is_online_mode

            if is_online_mode():
                from core import server_live as live
                from core.sync_meta_bump import bump_row_meta

                for mid in touched_ids:
                    bump_row_meta(conn, "medicines", int(mid), commit=False)
                conn.commit()
                if not live.push_entities(conn, "medicines", touched_ids):
                    raise RuntimeError("Server push medicines failed")
        except Exception as exc:
            sync_error = str(exc)

    out = {
        "ok": True,
        "export_type": "medicines",
        "inserted": inserted,
        "updated": updated,
        "skipped": skipped,
        "errors": errors[:20],
        "message": (
            f"Imported medicines: {inserted} new, {updated} updated"
            + (f", {skipped} skipped" if skipped else "")
            + ". Inventory shows non-hidden stock rows — refresh Inventory."
        ),
    }
    if sync_error:
        out["sync_warning"] = sync_error
    return out


def _apply_purchases(conn, data: dict[str, Any]) -> dict[str, Any]:
    from core.expiry_text import expiry_display
    from core.layout_config import is_strip_count_type
    from core.purchase_calculator import PurchaseCalculator
    from core.purchase_service import (
        get_or_create_medicine,
        get_or_create_supplier,
        get_supplier_due,
        save_purchase as svc_save_purchase,
    )

    purchases = data.get("purchases") or data.get("bills") or []
    if not purchases:
        return {"ok": False, "error": "No purchases in JSON."}
    supplier_lookup = {
        str(s.get("name") or "").strip(): s for s in (data.get("suppliers") or [])
    }
    saved = 0
    errors: list[str] = []
    for i, purchase in enumerate(purchases):
        try:
            supplier_name = (
                purchase.get("supplier_name")
                or (purchase.get("supplier") or {}).get("name", "")
            )
            supplier_name = str(supplier_name or "").strip()
            if not supplier_name:
                raise ValueError("Missing supplier name")
            sup_data = supplier_lookup.get(supplier_name, {})
            if not sup_data and isinstance(purchase.get("supplier"), dict):
                sup_data = purchase["supplier"]
            supplier_id = get_or_create_supplier(
                conn,
                supplier_name,
                sup_data.get("address", ""),
                sup_data.get("phone", ""),
                sup_data.get("gstin", ""),
                sup_data.get("dl_numbers", ""),
            )
            raw_items = purchase.get("items") or []
            if not raw_items:
                raise ValueError("No items in purchase")
            items = []
            for it in raw_items:
                med_type = it.get("type", "")
                is_tb = is_strip_count_type(med_type)
                qty = float(it.get("qty", 0) or 0)
                free_qty = float(it.get("free_qty", 0) or 0)
                # MM/YY is what the purchase path downstream expects. A phone
                # row that spelled it "0428" used to travel on unchanged and be
                # dropped when it was finally converted for the database.
                expiry = expiry_display(it.get("expiry_date", ""))
                item = {
                    # Android exports "name" / "gst_pct"; older web export used medicine_name / gst_percent.
                    "name": str(
                        it.get("name") or it.get("medicine_name") or ""
                    ).strip(),
                    "type": med_type,
                    "batch": str(it.get("batch_no") or it.get("batch") or "").strip()
                    or "-",
                    "expiry": expiry,
                    "qty": qty,
                    "free_qty": free_qty,
                    "rate": float(it.get("rate", 0) or 0),
                    "mrp": float(it.get("mrp", 0) or 0),
                    "discount_pct": float(
                        it.get("item_discount", it.get("discount_pct", 0)) or 0
                    ),
                    "gst_pct": float(
                        it.get("gst_pct", it.get("gst_percent", 0)) or 0
                    ),
                    "hsn_code": it.get("hsn_code", ""),
                    "manufacturer": it.get("manufacturer", ""),
                    "schedule": it.get("schedule", ""),
                    "content_drug": it.get("content_drug", ""),
                    "auto_unit": "",
                }
                if not item["name"]:
                    raise ValueError("Purchase item missing medicine name")
                item["medicine_id"] = get_or_create_medicine(
                    conn,
                    item["name"],
                    item["type"],
                    item["batch"],
                    item["expiry"],
                    item["gst_pct"],
                    item["mrp"],
                    item["rate"],
                    item["manufacturer"],
                    item["hsn_code"],
                    item["schedule"],
                    item["content_drug"],
                )
                # The line's own pack when the export carries one, else the catalogue's. The
                # phone's export carries none, and the tablets_per_stripe 1 / quantity_value "1"
                # filled in here became the line's own pack (X3): 3 strips of 10 stocked as 3.
                item.update(import_line_pack(conn, it, item["medicine_id"], med_type))
                tps = int(item.get("tablets_per_stripe") or 1)
                item["total_tablets"] = qty * tps if is_tb else 0
                item["free_tablets"] = free_qty * tps if is_tb else 0
                items.append(item)
            prev_due, prev_credit = get_supplier_due(conn, supplier_name)
            cash = float(purchase.get("cash_paid", purchase.get("amount_paid", 0)) or 0)
            online = float(purchase.get("online_paid", 0) or 0)
            if cash <= 0 and online <= 0 and float(purchase.get("amount_paid", 0) or 0) > 0:
                cash = float(purchase.get("amount_paid", 0) or 0)
            result = PurchaseCalculator(
                items=items,
                overall_discount=float(purchase.get("overall_discount", 0) or 0),
                # The bill's own rounding and delivery charge were thrown away
                # here, so an imported bill could not add up to what the shop
                # was actually asked to pay.
                rounding=float(purchase.get("rounding", 0) or 0),
                expenditure=float(
                    purchase.get("expenditure", purchase.get("delivery", 0)) or 0
                ),
                previous_due=prev_due,
                previous_credit=prev_credit,
                cash_paid=cash,
                online_paid=online,
                gst_calc_method=(
                    purchase.get("gst_calc_method") or "discount_after_gst"
                ).strip(),
            ).calculate()
            svc_save_purchase(
                conn,
                supplier_id,
                purchase.get(
                    "purchase_date", datetime.now().strftime("%Y-%m-%d")
                ),
                purchase.get("bill_number", ""),
                result,
                items,
            )
            saved += 1
        except Exception as exc:
            bill_no = purchase.get("bill_number", f"#{i + 1}")
            errors.append(f"Bill {bill_no}: {exc}")
    conn.commit()
    return {
        "ok": True,
        "export_type": "purchases",
        "saved": saved,
        "total": len(purchases),
        "errors": errors[:20],
    }


def _exported_pack(it: dict[str, Any], med_type: str) -> dict[str, Any]:
    """The pack an exported purchase line carries itself; {} when it carries none.

    The phone's purchase export (MobileExportService.buildPurchasesPayload) writes no
    tablets_per_stripe, unit or quantity_value for a line. A pack another export does write is
    the line's own and is taken as typed, a loose 1 included.
    """
    from core.layout_config import is_strip_count_type, parse_tablets_per_stripe

    try:
        tps = int(float(it.get("tablets_per_stripe") or 0))
    except (TypeError, ValueError):
        tps = 0
    text = ""
    for key in ("quantity_value", "unit", "pack"):
        text = str(it.get(key) or "").strip()
        if text:
            break
    if tps <= 0 and not text:
        return {}
    if tps <= 0:
        try:
            tps = int(parse_tablets_per_stripe(text)) if is_strip_count_type(med_type) else 1
        except Exception:
            tps = 1
    tps = max(1, tps)
    return {"tablets_per_stripe": tps, "quantity_value": text or str(tps)}


def _catalogue_pack(conn, medicine_id) -> str:
    """The pack the store's medicine row holds (its unit); "" when there is none to read."""
    try:
        mid = int(medicine_id or 0)
    except (TypeError, ValueError):
        return ""
    if mid <= 0:
        return ""
    try:
        from core.sync_prefs import is_online_mode

        if is_online_mode():
            from core.online_catalog import medicine_by_id
            from core.server_crud import get_doc

            held = medicine_by_id(mid) or get_doc("medicines", mid) or {}
            return str(held.get("unit") or "").strip()
        if conn is None:
            return ""
        row = conn.execute("SELECT unit FROM medicines WHERE id=?", (mid,)).fetchone()
        return str(row[0] or "").strip() if row else ""
    except Exception:
        return ""


def import_line_pack(conn, it: dict[str, Any], medicine_id, med_type: str) -> dict[str, Any]:
    """The pack fields of one purchase line imported from a phone export.

    A pack the export carries is the line's own (the owner's rule: stock per line pack). A line
    without one takes the pack of the medicine row it was matched to -- the catalogue pack, the
    one the Online save also fills in for a stored line that says nothing. The importers filled
    in tablets_per_stripe 1 / quantity_value "1" instead, and once a line was stocked by its own
    pack (X3) that invented 1 was the pack: 3 strips of a strip-of-10 medicine went on the shelf
    as 3, and a syrup's "100ML" pack was written over with "1". {} when neither is known: the
    save then counts the line as it comes, as before.

    Used by the engine's import (_apply_purchases) and the Classic Import from Mobile page.
    """
    from core.layout_config import is_strip_count_type, parse_tablets_per_stripe

    own = _exported_pack(it, med_type)
    if own:
        return own
    unit = _catalogue_pack(conn, medicine_id)
    if not unit:
        return {}
    tps = 1
    if is_strip_count_type(med_type):
        try:
            tps = max(1, int(parse_tablets_per_stripe(unit)))
        except Exception:
            tps = 1
    return {"tablets_per_stripe": tps, "quantity_value": unit, "unit": unit, "pack": unit}


def parse_mobile_json(raw: str) -> dict[str, Any]:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        return {"ok": False, "error": f"Invalid JSON: {exc}"}
    if not isinstance(data, dict):
        return {"ok": False, "error": "JSON root must be an object."}
    return preview_mobile_data(data)
