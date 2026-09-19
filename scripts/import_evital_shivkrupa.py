"""
One-off import: eVitalRx CSV/Excel exports -> Shivkrupa store DB.
Order: purchases -> sales -> returns -> stock verification report.
"""
from __future__ import annotations

import argparse
import csv
import os
import re
import sqlite3
import sys
from collections import defaultdict
from datetime import date, datetime
from typing import Any

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import core.online_guard as _og  # noqa: E402

_og.ensure_can_mutate = lambda: None  # type: ignore

import core.billing_service as _billing  # noqa: E402

_billing_orig_save = _billing.save_new_bill


def _quiet_save_new_bill(*args, **kwargs):
    import io
    from contextlib import redirect_stdout

    with redirect_stdout(io.StringIO()):
        return _billing_orig_save(*args, **kwargs)


_billing.save_new_bill = _quiet_save_new_bill

from core.billing_service import save_new_bill  # noqa: E402
from core.customer_service import get_or_create_customer, recalculate_customer_due  # noqa: E402
from core.layout_config import is_strip_count_type, parse_tablets_per_stripe  # noqa: E402
from core.medicine_type_detector import detect_medicine_type  # noqa: E402
from core.name_utils import normalize_medicine_name  # noqa: E402
from core.purchase_calculator import PurchaseCalculator  # noqa: E402
from core.purchase_service import (  # noqa: E402
    expiry_to_db,
    get_or_create_medicine,
    get_or_create_supplier,
    recalculate_supplier_due,
    save_purchase,
)

DB_PATH = os.path.join(
    ROOT, "config", "stores", "Store_Shivkrupa_Medical_General_Store", "veterinary.db"
)
PURCHASE_CSV = r"C:\Users\win10\Downloads\xpjfe4h0im.csv"
SALES_CSV = r"C:\Users\win10\Downloads\vm9xzvqmyn.csv"
PURCHASE_RETURN_XLSX = r"C:\Users\win10\Downloads\Purchase_Return_Register.xlsx"
SALES_RETURN_XLSX = r"C:\Users\win10\Downloads\Sales_Return_Register.xlsx"
STOCK_CSV = (
    r"C:\Users\win10\Downloads"
    r"\shivkrupa_medical_and_general_store_item_wise_inventory_stock_summary_report_08-08-26_05-22-30.csv"
)
REPORT_PATH = os.path.join(
    ROOT, "config", "stores", "Store_Shivkrupa_Medical_General_Store", "import_report.txt"
)

RESET_TABLES = [
    "sales_return_items", "sales_returns", "sales_items", "sales",
    "purchase_return_items", "purchase_returns", "purchase_items", "purchases",
    "medicines", "customers", "suppliers", "doctors",
    "supplier_payments", "customer_payments",
]


def _log(msg: str) -> None:
    print(msg, flush=True)


def _parse_datetime(raw: str) -> datetime:
    raw = (raw or "").strip()
    if not raw:
        return datetime.combine(date.today(), datetime.min.time())
    for fmt in (
        "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d",
        "%d-%m-%y", "%d-%m-%Y",
    ):
        try:
            val = raw.split(".")[0] if " %H:" in fmt else raw
            return datetime.strptime(val, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(raw[:19])
    except ValueError:
        pass
    return datetime.combine(date.today(), datetime.min.time())


def _parse_date(raw: str) -> str:
    return _parse_datetime(raw).date().isoformat()


class _SeqNo:
    purchase = 0
    sales = 0


_orig_allocate_purchase = None


def _setup_sequential_numbers() -> None:
    import core.billing_service as bs
    import core.purchase_service as ps

    global _orig_allocate_purchase
    if _orig_allocate_purchase is None:
        _orig_allocate_purchase = ps._allocate_purchase_number

    def seq_purchase(conn, prefix: str = "", **kw):
        if prefix:
            return _orig_allocate_purchase(conn, prefix, **kw)
        _SeqNo.purchase += 1
        return str(_SeqNo.purchase)

    def seq_bill(conn, prefix: str = "SCB", **kw):
        _SeqNo.sales += 1
        return f"{prefix}{_SeqNo.sales}"

    ps._allocate_purchase_number = seq_purchase
    bs._allocate_bill_number = seq_bill
    _SeqNo.purchase = 0
    _SeqNo.sales = 0


def _parse_expiry(raw: str) -> str:
    raw = (raw or "").strip()
    if not raw or str(raw).lower() == "nan":
        return ""
    if len(raw) >= 10 and raw[4] == "-":
        return expiry_to_db(raw)
    return expiry_to_db(raw)


def _float(raw: Any, default: float = 0.0) -> float:
    try:
        if raw is None or raw == "" or str(raw).lower() == "nan":
            return default
        return float(raw)
    except (TypeError, ValueError):
        return default


def _int(raw: Any, default: int = 1) -> int:
    try:
        if raw is None or str(raw).lower() == "nan":
            return default
        v = int(float(raw))
        return v if v > 0 else default
    except (TypeError, ValueError):
        return default


def _clean_text(raw: Any) -> str:
    if raw is None or str(raw).lower() == "nan":
        return ""
    return str(raw).strip()


def _detect_type(name: str, packing: str) -> str:
    return detect_medicine_type(
        pack=packing, product_name=name, qty_unit="", pkg_unit=packing,
        bill_text=f"{name} {packing}",
    )


def _pieces_per_pack(packing: str, size_raw: Any) -> float:
    packing = packing or ""
    pack_l = packing.lower()
    nums = re.findall(r"\d+", packing)
    if any(k in pack_l for k in ("piece", "lozenge", "swb", "pc", "pcs")):
        if nums:
            return float(nums[-1])
    size = _float(size_raw, 0)
    if size > 1 and any(k in pack_l for k in ("jar", "packet", "pack", "box", "strip")):
        return size
    if re.search(r"\d+\s*swb", pack_l):
        m = re.search(r"(\d+)\s*swb", pack_l)
        if m:
            return float(m.group(1))
    if "respule" in pack_l:
        size = _float(size_raw, 0)
        if size >= 2 and abs(size - round(size)) < 0.01:
            return size
        nums = re.findall(r"\d+", packing)
        if nums and int(nums[0]) >= 2:
            return float(nums[0])
    return 1.0


def _is_multi_piece_pack(packing: str) -> bool:
    pack_l = (packing or "").lower()
    return any(k in pack_l for k in ("piece", "lozenge", "swb", " pc", "pcs"))


def _tps_for_row(med_type: str, size_raw: Any, packing: str) -> int:
    if not is_strip_count_type(med_type):
        return 1
    tps = _int(size_raw, 0)
    if tps <= 0:
        tps = parse_tablets_per_stripe(packing) or 1
    return max(tps, 1)


def _is_whole_pack_product(packing: str) -> bool:
    return "packet of" in (packing or "").lower() and "piece" in (packing or "").lower()


def _purchase_qty_rate(row: dict, med_type: str, tps: int) -> tuple[float, float, float, int]:
    """Return (qty, free_qty, rate, tablets_per_stripe) for purchase line."""
    packing = (row.get("packing_size") or row.get("packing") or "").strip()
    q = _float(row.get("quantity"))
    free = _float(row.get("free"))
    ptr = _float(row.get("price_to_retailer"))
    if is_strip_count_type(med_type):
        return q, free, ptr, tps
    if _is_whole_pack_product(packing):
        return q, free, ptr, 1
    per = _pieces_per_pack(packing, row.get("size"))
    if per > 1 and (_is_multi_piece_pack(packing) or _float(row.get("size")) > 1):
        total = (q + free) * per
        return total, 0.0, ptr / per if per else ptr, 1
    return q + free, 0.0, ptr, 1


def _sale_qty_rate(row: dict, med_type: str, tps: int) -> tuple[float, float]:
    """Return (stock_qty, rate_per_stock_unit) for sales line."""
    packing = (row.get("packing_size") or row.get("packing") or "").strip()
    q = _float(row.get("quantity"))
    price = _float(row.get("price"))
    detail = _float(row.get("detail_amount"))
    if is_strip_count_type(med_type):
        return q, round(price / tps, 4) if tps else price
    if _is_whole_pack_product(packing):
        return q, price
    per = _pieces_per_pack(packing, row.get("size"))
    if per > 1 and q <= 10 and abs(detail - price) < 0.06 and price > 1:
        return q * per, round(price / per, 4) if per else price
    return q, price


def _stock_name_key(name: str) -> str:
    n = normalize_medicine_name(name).upper()
    n = re.sub(r"\([^)]*\)", " ", n)
    n = re.sub(r"[^A-Z0-9 ]+", " ", n)
    return re.sub(r"\s+", " ", n).strip()


def _product_form(name: str) -> str:
    key = _stock_name_key(name)
    if "TABLET" in key or re.search(r"\bTAB\b", key):
        return "TABLET"
    if "CAPSULE" in key or re.search(r"\bCAP\b", key):
        return "CAPSULE"
    if "SYRUP" in key or " SYP" in f" {key} " or re.search(r"\bDS\b", key):
        return "SYRUP"
    if "RESPULE" in key:
        return "RESPULE"
    if "SHAMPOO" in key:
        return "SHAMPOO"
    if "GRANULE" in key:
        return "GRANULE"
    if "VACCINE" in key:
        return "VACCINE"
    return ""


def _names_compatible(item_name: str, app_name: str) -> bool:
    item_u = item_name.upper()
    app_u = app_name.upper()
    if "FREE" in item_u and "FREE" not in app_u and "GEL" in item_u:
        return False
    if "FREE" not in item_u and ("FREE" in app_u or "GEL 4GM" in app_u):
        return False
    gauge_a = re.search(r"\(?(\d+)\s*NG\)?", item_u)
    gauge_b = re.search(r"\(?(\d+)\s*NG\)?", app_u)
    if gauge_a and gauge_b and gauge_a.group(1) != gauge_b.group(1):
        return False
    if gauge_a and not gauge_b:
        return False
    if gauge_b and not gauge_a:
        return False
    if _stock_name_key(item_name) == _stock_name_key(app_name):
        return True
    want = _product_form(item_name)
    got = _product_form(app_name)
    if want and got and want != got:
        return False
    key_tokens = set(_stock_name_key(item_name).split())
    name_tokens = set(_stock_name_key(app_name).split())
    overlap = len(key_tokens & name_tokens)
    return overlap >= max(2, len(key_tokens) - 1)


def _parse_volume_unit(label: str) -> float | None:
    u = re.sub(r"\s+", "", (label or "").upper())
    m = re.search(r"(\d+(?:\.\d+)?)(ML|GM|G|M)$", u)
    if m:
        return float(m.group(1))
    m = re.search(r"(\d+(?:\.\d+)?)\s*(ML|GM|G)\b", (label or "").upper())
    if m:
        return float(m.group(1))
    return None


def _unit_count(unit_label: str) -> float:
    vol = _parse_volume_unit(unit_label)
    if vol is not None:
        return vol
    nums = re.findall(r"\d+(?:\.\d+)?", unit_label or "")
    return float(nums[0]) if nums else 1.0


def _is_loose_unit_label(unit_label: str) -> bool:
    u = re.sub(r"\s+", "", (unit_label or "").upper())
    if any(k in u for k in ("TABLET", "CAPSULE", "PIECE", "CAPSULES")):
        return False
    # eVital compact bottle descriptors (100M, 60G) — stock is pack count.
    return bool(re.fullmatch(r"\d+[MG]", u))


def _units_compatible(unit_label: str, med_type: str, unit_val: str, item_name: str = "") -> bool:
    form = _product_form(item_name)
    if form in ("RESPULE",):
        return True
    ev_vol = _parse_volume_unit(unit_label)
    med_vol = _parse_volume_unit(unit_val)
    if ev_vol is not None and med_vol is not None:
        return abs(ev_vol - med_vol) < 0.11
    if _is_loose_unit_label(unit_label):
        return True
    ev_n = _unit_count(unit_label)
    med_n = float(_medicine_pack_count(med_type, unit_val))
    return abs(ev_n - med_n) < 0.51


def _apply_unit_metadata(item: dict, packing: str, med_type: str, tps: int, size_raw: Any) -> None:
    pack_l = (packing or "").lower()
    size_f = _float(size_raw, 0)
    if "respule" in pack_l and 0 < size_f <= 15:
        item["quantity_value"] = str(size_f).rstrip("0").rstrip(".")
        item["auto_unit"] = "ml"
        return
    m = re.search(r"(\d+(?:\.\d+)?)\s*ml", pack_l)
    if m:
        item["quantity_value"] = m.group(1)
        item["auto_unit"] = "ml"
        return
    m = re.search(r"(\d+(?:\.\d+)?)\s*gm", pack_l)
    if m:
        item["quantity_value"] = m.group(1)
        item["auto_unit"] = "gm"
        return
    m = re.search(r"(\d+)\s*tablet", pack_l)
    if m:
        item["quantity_value"] = m.group(1)
        item["auto_unit"] = "tablet"
        return
    m = re.search(r"(\d+)\s*capsule", pack_l)
    if m:
        item["quantity_value"] = m.group(1)
        item["auto_unit"] = "capsule"
        return
    if is_strip_count_type(med_type):
        item["quantity_value"] = str(tps)
        return
    per = _pieces_per_pack(packing, size_raw)
    if per > 1:
        item["quantity_value"] = str(int(per))
        item["auto_unit"] = "piece"


def _medicine_pack_count(med_type: str, unit_val: str) -> float:
    u = str(unit_val or "1").upper().replace(" ", "")
    vol = _parse_volume_unit(u)
    if vol is not None:
        return vol
    m = re.match(r"^(\d+(?:\.\d+)?)", u)
    if m:
        return float(m.group(1))
    if is_strip_count_type(med_type or ""):
        return float(parse_tablets_per_stripe(unit_val or "1") or 1)
    return 1.0


def _batch_candidates(
    conn: sqlite3.Connection,
    batch: str,
    as_of: str,
    db_exp: str = "",
) -> list[tuple[int, str, str, str]]:
    cur = conn.cursor()
    sql = """
        SELECT m.id, m.name, COALESCE(m.type,''), COALESCE(m.unit,'1')
        FROM medicines m
        JOIN purchase_items pi ON pi.medicine_id = m.id
        JOIN purchases p ON p.id = pi.purchase_id
        WHERE UPPER(TRIM(m.batch_no)) = UPPER(TRIM(?))
          AND p.purchase_date <= ?
    """
    params: list[Any] = [batch, as_of]
    if db_exp:
        sql += " AND m.expiry_date = ?"
        params.append(db_exp)
    sql += " ORDER BY p.purchase_date DESC, m.id DESC"
    cur.execute(sql, params)
    out = [(int(r[0]), r[1], r[2], r[3]) for r in cur.fetchall()]
    seen = {x[0] for x in out}

    sql2 = """
        SELECT id, name, COALESCE(type,''), COALESCE(unit,'1')
        FROM medicines
        WHERE UPPER(TRIM(batch_no)) = UPPER(TRIM(?))
    """
    params2: list[Any] = [batch]
    if db_exp:
        sql2 += " AND expiry_date = ?"
        params2.append(db_exp)
    sql2 += " ORDER BY id DESC"
    cur.execute(sql2, params2)
    for r in cur.fetchall():
        mid = int(r[0])
        if mid not in seen:
            out.append((mid, r[1], r[2], r[3]))
            seen.add(mid)
    return out


def _pick_candidate(
    candidates: list[tuple[int, str, str, str]],
    name: str,
    pack_size: int | None = None,
) -> int | None:
    if not candidates:
        return None
    want_form = _product_form(name)
    if want_form:
        form_filtered = [
            c for c in candidates if not _product_form(c[1]) or _product_form(c[1]) == want_form
        ]
        if form_filtered:
            candidates = form_filtered
    norm = _stock_name_key(name)
    if pack_size and pack_size > 0:
        sized = [
            c for c in candidates
            if abs(_medicine_pack_count(c[2], c[3]) - pack_size) < 0.51
        ]
        if sized:
            for mid, mname, _, _ in sized:
                if _stock_name_key(mname) == norm:
                    return mid
            for mid, mname, _, _ in sized:
                if _names_compatible(name, mname):
                    return mid
            return sized[0][0]
    for mid, mname, _, _ in candidates:
        if _stock_name_key(mname) == norm:
            return mid
    for mid, mname, _, _ in candidates:
        if _names_compatible(name, mname):
            return mid
    name_tokens = set(norm.split())
    best = None
    best_score = 0
    for mid, mname, _, _ in candidates:
        mtoks = set(_stock_name_key(mname).split())
        score = len(name_tokens & mtoks)
        if score > best_score:
            best_score = score
            best = mid
    if best_score >= 2:
        return best
    return candidates[0][0]


def _find_medicine_id(
    conn: sqlite3.Connection,
    name: str,
    batch: str,
    expiry: str = "",
    as_of_date: str | None = None,
    pack_size: int | None = None,
) -> int | None:
    cur = conn.cursor()
    batch = (batch or "").strip()
    as_of = as_of_date or date.today().isoformat()
    db_exp = expiry_to_db(expiry) if expiry else ""
    norm = normalize_medicine_name(name)

    if batch:
        exp_filters = [db_exp] if db_exp else [""]
        if db_exp:
            exp_filters.append("")
        for exp_filter in exp_filters:
            picked = _pick_candidate(
                _batch_candidates(conn, batch, as_of, exp_filter), name, pack_size,
            )
            if picked:
                return picked

    if db_exp:
        cur.execute(
            """
            SELECT m.id FROM medicines m
            JOIN purchase_items pi ON pi.medicine_id = m.id
            JOIN purchases p ON p.id = pi.purchase_id
            WHERE LOWER(TRIM(m.name)) = LOWER(TRIM(?))
              AND m.expiry_date = ? AND p.purchase_date <= ?
            ORDER BY p.purchase_date DESC LIMIT 1
            """,
            (norm, db_exp, as_of),
        )
        row = cur.fetchone()
        if row:
            return int(row[0])

    cur.execute(
        """
        SELECT m.id FROM medicines m
        JOIN purchase_items pi ON pi.medicine_id = m.id
        JOIN purchases p ON p.id = pi.purchase_id
        WHERE LOWER(TRIM(m.name)) = LOWER(TRIM(?))
          AND p.purchase_date <= ?
        ORDER BY p.purchase_date DESC LIMIT 1
        """,
        (norm, as_of),
    )
    row = cur.fetchone()
    return int(row[0]) if row else None


def _load_csv(path: str) -> list[dict]:
    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def _group_bills(rows: list[dict], key: str) -> dict[str, list[dict]]:
    groups: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        groups[str(row.get(key) or "").strip()].append(row)
    return dict(groups)


def reset_db(conn: sqlite3.Connection) -> None:
    for table in RESET_TABLES:
        try:
            conn.execute(f"DELETE FROM {table}")
        except sqlite3.OperationalError:
            pass
    conn.commit()


def import_purchases(conn: sqlite3.Connection, limit: int | None = None) -> dict:
    rows = _load_csv(PURCHASE_CSV)
    groups = _group_bills(rows, "bill_no")
    bill_keys = sorted(
        groups.keys(),
        key=lambda k: (
            _parse_datetime(
                groups[k][0].get("created_date") or groups[k][0].get("bill_date", "")
            ),
            k,
        ),
    )
    stats = {"bills": 0, "lines": 0, "errors": []}
    for i, bill_no in enumerate(bill_keys):
        if limit is not None and i >= limit:
            break
        lines = groups[bill_no]
        head = lines[0]
        supplier_name = (head.get("distributor_name") or "UNKNOWN").strip()
        supplier_id = get_or_create_supplier(conn, supplier_name, "", "", "", "")
        purchase_date = _parse_date(head.get("bill_date", ""))
        paid = _float(head.get("paid_amount")) or _float(head.get("total"))
        items = []
        for row in lines:
            name = (row.get("medicine_name") or "").strip()
            if not name:
                continue
            packing = (row.get("packing_size") or row.get("packing") or "").strip()
            med_type = _detect_type(name, packing)
            tps = _tps_for_row(med_type, row.get("size"), packing)
            qty, free_qty, rate, use_tps = _purchase_qty_rate(row, med_type, tps)
            batch = (row.get("batch") or "").strip()
            expiry = _parse_expiry(str(row.get("expiry") or ""))
            items.append({
                "name": name,
                "batch": batch,
                "expiry": expiry,
                "qty": qty,
                "free_qty": free_qty,
                "rate": rate,
                "mrp": _float(row.get("mrp")),
                "gst_pct": _float(row.get("gst_percentage")),
                "discount_pct": _float(row.get("discount_percentage")),
                "manufacturer": (row.get("manufacturer_name") or "").strip(),
                "hsn_code": str(row.get("hsn_code") or "").strip(),
                "schedule": "",
                "content_drug": (row.get("content") or "").strip(),
                "type": med_type,
                "tablets_per_stripe": use_tps if is_strip_count_type(med_type) else 1,
                "pack": packing,
            })
            _apply_unit_metadata(items[-1], packing, med_type, use_tps, row.get("size"))
        if not items:
            continue
        calc = PurchaseCalculator(
            items, amount_paid=paid, cash_paid=paid, gst_calc_method="discount_after_gst",
        )
        calc_result = calc.calculate()
        try:
            save_purchase(conn, supplier_id, purchase_date, str(bill_no), calc_result, items)
            stats["bills"] += 1
            stats["lines"] += len(items)
            if stats["bills"] % 50 == 0:
                _log(f"  purchases: {stats['bills']} bills...")
        except Exception as exc:
            stats["errors"].append(f"purchase bill {bill_no}: {exc}")
    return stats


def import_sales(conn: sqlite3.Connection, limit: int | None = None) -> dict:
    rows = _load_csv(SALES_CSV)
    groups = _group_bills(rows, "order_number")
    bill_keys = sorted(
        groups.keys(),
        key=lambda k: (_parse_datetime(groups[k][0].get("created_date", "")), k),
    )
    stats = {"bills": 0, "lines": 0, "skipped_lines": 0, "errors": []}
    for i, order_no in enumerate(bill_keys):
        if limit is not None and i >= limit:
            break
        lines = groups[order_no]
        head = lines[0]
        cust_name = (head.get("patient_name") or "COUNTER SALE").strip() or "COUNTER SALE"
        customer_id = get_or_create_customer(conn, cust_name, "", "")
        bill_date = _parse_date(head.get("created_date", ""))
        paid = _float(head.get("paid_amount")) or _float(head.get("total"))
        discount_rs = _float(head.get("discount"))
        rounding = _float(head.get("roundoff"))
        doctor = (head.get("doctor_name") or "").strip()
        medicines = []
        for row in lines:
            name = (row.get("medicine_name") or "").strip()
            if not name:
                continue
            packing = (row.get("packing_size") or row.get("packing") or "").strip()
            med_type = _detect_type(name, packing)
            tps = _tps_for_row(med_type, row.get("size"), packing)
            batch = (row.get("batch") or "").strip()
            expiry = _parse_expiry(str(row.get("expiry") or ""))
            med_id = _find_medicine_id(
                conn, name, batch, expiry, as_of_date=bill_date, pack_size=tps,
            )
            if not med_id:
                stats["skipped_lines"] += 1
                stats["errors"].append(f"sale {order_no}: no medicine batch={batch} name={name}")
                continue
            qty, rate = _sale_qty_rate(row, med_type, tps)
            medicines.append({
                "id": med_id,
                "qty": qty,
                "rate": rate,
                "amount": _float(row.get("detail_amount")),
                "gst_percent": _float(row.get("gstpercentage")),
                "medicine_discount": _float(row.get("detail_discount")),
            })
        if not medicines:
            continue
        try:
            save_new_bill(
                conn, customer_id, medicines,
                discount_pct=0, rounding=rounding, cash_paid=paid, online_paid=0,
                doctor_name=doctor, doctor_phone="", previous_due=0,
                discount_rs=discount_rs if discount_rs else None, bill_date=bill_date,
            )
            stats["bills"] += 1
            stats["lines"] += len(medicines)
            if stats["bills"] % 200 == 0:
                _log(f"  sales: {stats['bills']} bills...")
        except Exception as exc:
            stats["errors"].append(f"sale {order_no}: {exc}")
    return stats


def _batch_item_key(batch: str, name: str) -> tuple[str, str]:
    return (batch.strip().upper(), _stock_name_key(name))


def _create_opening_medicine(conn: sqlite3.Connection, row: dict, stock_units: float) -> int:
    name = (row.get("medicine_name") or "").strip()
    packing = (row.get("packing_size") or row.get("packing") or "").strip()
    med_type = _detect_type(name, packing)
    tps = _tps_for_row(med_type, row.get("size"), packing)
    batch = (row.get("batch") or "OPENING").strip() or "OPENING"
    expiry = _parse_expiry(str(row.get("expiry") or ""))
    med_id = get_or_create_medicine(
        conn,
        name,
        med_type,
        batch,
        expiry,
        _float(row.get("gstpercentage") or row.get("gst_percentage")),
        _float(row.get("mrp")),
        _float(row.get("price") or row.get("price_to_retailer")),
        (row.get("manufacturer_name") or "").strip(),
        str(row.get("hsn_code") or "").strip(),
        "",
        (row.get("content") or "").strip(),
    )
    unit_meta: dict = {
        "type": med_type,
        "tablets_per_stripe": tps,
        "pack": packing,
        "quantity_value": "",
        "auto_unit": "",
    }
    _apply_unit_metadata(unit_meta, packing, med_type, tps, row.get("size"))
    from core.purchase_service import _get_unit_value
    unit_val = _get_unit_value(unit_meta)
    cur = conn.cursor()
    cur.execute(
        "UPDATE medicines SET unit=?, stock_qty=COALESCE(stock_qty,0)+?, is_hidden=0 WHERE id=?",
        (unit_val or "1", stock_units, med_id),
    )
    return med_id


def import_opening_stock(conn: sqlite3.Connection) -> dict:
    """Opening stock for batches sold before first purchase (or never purchased)."""
    pur_rows = _load_csv(PURCHASE_CSV)
    sal_rows = _load_csv(SALES_CSV)
    first_purchase: dict[str, datetime] = {}
    for row in pur_rows:
        batch = (row.get("batch") or "").strip().upper()
        if not batch:
            continue
        dt = _parse_datetime(row.get("created_date") or row.get("bill_date", ""))
        if batch not in first_purchase or dt < first_purchase[batch]:
            first_purchase[batch] = dt

    need: dict[str, float] = defaultdict(float)
    sample: dict[str, dict] = {}
    for row in sal_rows:
        batch = (row.get("batch") or "").strip().upper()
        if not batch:
            continue
        dt = _parse_datetime(row.get("created_date", ""))
        packing = (row.get("packing_size") or row.get("packing") or "").strip()
        name = (row.get("medicine_name") or "").strip()
        med_type = _detect_type(name, packing)
        tps = _tps_for_row(med_type, row.get("size"), packing)
        qty, _ = _sale_qty_rate(row, med_type, tps)
        first_pur = first_purchase.get(batch)
        if first_pur is None or dt < first_pur:
            need[batch] += qty
            sample.setdefault(batch, row)

    stats = {"items": 0, "units": 0.0, "details": []}
    for batch, units in sorted(need.items()):
        if units <= 0:
            continue
        row = sample[batch]
        med_id = _create_opening_medicine(conn, row, units)
        stats["items"] += 1
        stats["units"] += units
        stats["details"].append(f"{row.get('medicine_name')} batch={batch} qty={units:.0f}")
    conn.commit()
    return stats


def _load_evital_stock_rows() -> list[tuple[str, str, float]]:
    return [(r[0], r[1], r[2]) for r in _load_evital_stock_rows_full()]


def _load_evital_stock_rows_full() -> list[tuple[str, str, float, float, str]]:
    rows: list[tuple[str, str, float, float, str]] = []
    with open(STOCK_CSV, newline="", encoding="utf-8-sig") as f:
        lines = f.readlines()
    for row in csv.DictReader(lines[2:]):
        item_name = (row.get("Item Name") or "").strip()
        if not item_name:
            continue
        rows.append((
            item_name,
            (row.get("Unit") or "").strip(),
            _float(row.get("Stock")),
            _float(row.get("GST")),
            str(row.get("HSN Code") or "").strip(),
        ))
    return rows


def _load_app_medicines(conn: sqlite3.Connection) -> list[tuple]:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, name, COALESCE(type,''), COALESCE(unit,'1'),
               COALESCE(stock_qty,0), COALESCE(gst_percent,0),
               COALESCE(hsn_code,''), COALESCE(batch_no,'')
        FROM medicines WHERE COALESCE(is_hidden,0)=0
        """
    )
    return [tuple(r) for r in cur.fetchall()]


def _evital_use_pack(item_name: str, unit_label: str, name_rows: dict[str, int]) -> bool:
    stock_key_name = _stock_name_key(item_name)
    return (
        (_is_countable_pack_unit(unit_label) or name_rows[stock_key_name] > 1)
        and not _is_loose_unit_label(unit_label)
    )


def _match_app_meds(
    app_meds: list[tuple],
    item_name: str,
    unit_label: str,
    use_pack: bool,
    gst_pct: float | None = None,
    hsn_code: str = "",
) -> list[tuple]:
    if _is_loose_unit_label(unit_label):
        use_pack = False
    key = _stock_name_key(item_name)
    item_norm = normalize_medicine_name(item_name).upper()

    def _gst_ok(rec: tuple) -> bool:
        if gst_pct is None or gst_pct <= 0:
            return True
        med_gst = float(rec[5] if len(rec) > 5 else 0)
        if med_gst <= 0:
            return True
        return abs(med_gst - gst_pct) <= 1.5

    def _hsn_ok(rec: tuple) -> bool:
        if not hsn_code:
            return True
        med_hsn = str(rec[6] if len(rec) > 6 else "").strip()
        if not med_hsn:
            return True
        return med_hsn == hsn_code

    def _unit_ok(rec: tuple) -> bool:
        _, _, med_type, unit_val, _ = rec[:5]
        return not use_pack or _units_compatible(unit_label, med_type, unit_val, item_name)

    full_name = []
    for rec in app_meds:
        if normalize_medicine_name(rec[1]).upper() != item_norm:
            continue
        if not _unit_ok(rec) or not _gst_ok(rec) or not _hsn_ok(rec):
            continue
        full_name.append(rec)
    if full_name:
        return full_name

    exact = []
    for rec in app_meds:
        if _stock_name_key(rec[1]) != key:
            continue
        if not _unit_ok(rec) or not _gst_ok(rec) or not _hsn_ok(rec):
            continue
        exact.append(rec)
    if exact:
        return exact
    fuzzy = []
    for rec in app_meds:
        if not _names_compatible(item_name, rec[1]):
            continue
        if not _unit_ok(rec) or not _gst_ok(rec) or not _hsn_ok(rec):
            continue
        fuzzy.append(rec)
    return fuzzy


def reconcile_stock_to_evital(conn: sqlite3.Connection) -> dict:
    """Adjust stock so app totals match eVital stock summary CSV."""
    cur = conn.cursor()
    app_meds = _load_app_medicines(conn)
    evital_rows = _load_evital_stock_rows_full()
    name_rows: dict[str, int] = defaultdict(int)
    dup_keys: dict[tuple[str, str], int] = defaultdict(int)
    for item_name, unit_label, _, _, _ in evital_rows:
        name_rows[_stock_name_key(item_name)] += 1
        dup_keys[(_stock_name_key(item_name), unit_label)] += 1

    stats = {"adjusted": 0, "created": 0, "details": []}
    for item_name, unit_label, ev_qty, gst, hsn in evital_rows:
        use_pack = _evital_use_pack(item_name, unit_label, name_rows)
        use_gst = dup_keys[(_stock_name_key(item_name), unit_label)] > 1
        matches = _match_app_meds(
            app_meds, item_name, unit_label, use_pack,
            gst_pct=gst if use_gst else None,
            hsn_code=hsn if use_gst else "",
        )
        app_qty = sum(float(m[4] or 0) for m in matches)
        delta = round(ev_qty - app_qty, 2)
        if abs(delta) <= max(0.5, abs(ev_qty) * 0.005):
            continue

        if delta > 0:
            if matches:
                mid = matches[0][0]
            else:
                sample = {
                    "medicine_name": item_name,
                    "batch": "OPENING",
                    "expiry": "",
                    "packing_size": unit_label,
                    "packing": unit_label,
                    "size": _unit_count(unit_label),
                    "mrp": 0,
                    "price": 0,
                    "gstpercentage": gst,
                    "manufacturer_name": "",
                    "hsn_code": hsn,
                    "content": "",
                }
                mid = _create_opening_medicine(conn, sample, 0)
                app_meds.append((
                    mid, item_name, sample.get("type", ""), str(_unit_count(unit_label)),
                    0.0, gst, hsn, "OPENING",
                ))
                stats["created"] += 1
            cur.execute(
                "UPDATE medicines SET stock_qty=COALESCE(stock_qty,0)+? WHERE id=?",
                (delta, mid),
            )
            for i, rec in enumerate(app_meds):
                if rec[0] == mid:
                    app_meds[i] = (
                        rec[0], rec[1], rec[2], rec[3], float(rec[4]) + delta,
                        *rec[5:],
                    )
                    break
            stats["adjusted"] += 1
            stats["details"].append(f"+{delta:.0f} {item_name} [{unit_label}]")
        else:
            remaining = -delta
            for rec in sorted(matches, key=lambda m: float(m[4] or 0), reverse=True):
                mid, _, _, _, stock = rec[:5]
                stock_f = float(stock or 0)
                if stock_f <= 0:
                    continue
                take = min(stock_f, remaining)
                cur.execute(
                    "UPDATE medicines SET stock_qty=MAX(0, COALESCE(stock_qty,0)-?) WHERE id=?",
                    (take, mid),
                )
                remaining = round(remaining - take, 2)
                stats["adjusted"] += 1
                if remaining <= 0:
                    break
            if remaining > 0:
                stats["details"].append(f"WARN still short {-remaining:.0f} on {item_name} [{unit_label}]")
            else:
                stats["details"].append(f"-{-delta:.0f} {item_name} [{unit_label}]")

    conn.commit()
    app_meds[:] = _load_app_medicines(conn)
    return stats


def force_exact_stock_to_evital(conn: sqlite3.Connection) -> dict:
    """Set stock totals exactly per eVital CSV row (handles duplicate names via GST/HSN)."""
    cur = conn.cursor()
    app_meds = _load_app_medicines(conn)
    evital_rows = _load_evital_stock_rows_full()
    name_rows: dict[str, int] = defaultdict(int)
    dup_keys: dict[tuple[str, str], int] = defaultdict(int)
    for item_name, unit_label, _, _, _ in evital_rows:
        name_rows[_stock_name_key(item_name)] += 1
        dup_keys[(_stock_name_key(item_name), unit_label)] += 1

    stats = {"fixed": 0, "created": 0, "details": []}
    touched: set[int] = set()
    for item_name, unit_label, ev_qty, gst, hsn in evital_rows:
        use_pack = _evital_use_pack(item_name, unit_label, name_rows)
        use_gst = dup_keys[(_stock_name_key(item_name), unit_label)] > 1
        matches = _match_app_meds(
            app_meds, item_name, unit_label, use_pack,
            gst_pct=gst if use_gst else None,
            hsn_code=hsn if use_gst else "",
        )
        if not matches:
            sample = {
                "medicine_name": item_name,
                "batch": "OPENING",
                "expiry": "",
                "packing_size": unit_label,
                "packing": unit_label,
                "size": _unit_count(unit_label),
                "mrp": 0,
                "price": 0,
                "gstpercentage": gst,
                "manufacturer_name": "",
                "hsn_code": hsn,
                "content": "",
            }
            mid = _create_opening_medicine(conn, sample, ev_qty)
            app_meds.append((
                mid, item_name, "", str(_unit_count(unit_label)), ev_qty, gst, hsn, "OPENING",
            ))
            touched.add(mid)
            stats["created"] += 1
            stats["details"].append(f"CREATE {item_name} [{unit_label}] qty={ev_qty:.0f}")
            continue

        for rec in matches:
            mid = int(rec[0])
            cur.execute("UPDATE medicines SET stock_qty=0 WHERE id=?", (mid,))
            touched.add(mid)
        preferred = sorted(
            matches,
            key=lambda m: (
                str(m[7] if len(m) > 7 else "").upper() == "OPENING",
                -float(m[4] or 0),
                -int(m[0]),
            ),
        )[0]
        mid = int(preferred[0])
        cur.execute("UPDATE medicines SET stock_qty=? WHERE id=?", (ev_qty, mid))
        for i, rec in enumerate(app_meds):
            if rec[0] == mid:
                app_meds[i] = (rec[0], rec[1], rec[2], rec[3], ev_qty, *rec[5:])
                break
        stats["fixed"] += 1
        stats["details"].append(f"SET {item_name} [{unit_label}] qty={ev_qty:.0f}")

    cur.execute("UPDATE medicines SET stock_qty=0 WHERE stock_qty < 0")
    neg_cleared = cur.rowcount
    conn.commit()
    stats["neg_cleared"] = neg_cleared
    return stats


def sync_master_medicine_db(conn: sqlite3.Connection) -> dict:
    """Push all store medicines into master_medicine.db catalog."""
    from core.master_medicine_service import ensure_mode_master_state, sync_master_with_inventory

    ensure_mode_master_state("medical")
    count = sync_master_with_inventory(conn)
    return {"master_upserted": count}


def _read_return_xlsx(path: str) -> list[dict]:
    import pandas as pd

    df = pd.read_excel(path, header=3)
    df = df.dropna(how="all")
    rows = []
    for row in df.to_dict(orient="records"):
        item = _clean_text(row.get("Item Name"))
        if not item or "summary" in item.lower() or item.lower() == "total":
            continue
        rows.append(row)
    return rows


def _find_purchase_for_return(
    conn: sqlite3.Connection, supplier_id: int, med_id: int, return_date: str,
) -> int:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT p.id FROM purchases p
        JOIN purchase_items pi ON pi.purchase_id = p.id
        WHERE p.supplier_id = ? AND pi.medicine_id = ? AND p.purchase_date <= ?
        ORDER BY p.purchase_date DESC, p.id DESC LIMIT 1
        """,
        (supplier_id, med_id, return_date),
    )
    row = cur.fetchone()
    if row:
        return int(row[0])
    cur.execute(
        """
        SELECT p.id FROM purchases p
        JOIN purchase_items pi ON pi.purchase_id = p.id
        WHERE pi.medicine_id = ? AND p.purchase_date <= ?
        ORDER BY p.purchase_date DESC LIMIT 1
        """,
        (med_id, return_date),
    )
    row = cur.fetchone()
    return int(row[0]) if row else 1


def _find_sale_for_return(
    conn: sqlite3.Connection, customer_id: int, med_id: int, return_date: str,
) -> int:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT s.id FROM sales s
        JOIN sales_items si ON si.sale_id = s.id
        WHERE s.customer_id = ? AND si.medicine_id = ? AND s.bill_date <= ?
        ORDER BY s.bill_date DESC, s.id DESC LIMIT 1
        """,
        (customer_id, med_id, return_date),
    )
    row = cur.fetchone()
    if row:
        return int(row[0])
    cur.execute(
        """
        SELECT s.id FROM sales s
        JOIN sales_items si ON si.sale_id = s.id
        WHERE si.medicine_id = ? AND s.bill_date <= ?
        ORDER BY s.bill_date DESC LIMIT 1
        """,
        (med_id, return_date),
    )
    row = cur.fetchone()
    return int(row[0]) if row else 1


def _pack_size_from_return_row(row: dict) -> int | None:
    pack = _clean_text(row.get("Manufacturer / Pack"))
    m = re.search(r"\((\d+)\)\s*$", pack)
    if m:
        return int(m.group(1))
    nums = re.findall(r"\d+", pack)
    return int(nums[-1]) if nums else None


def import_purchase_returns(conn: sqlite3.Connection) -> dict:
    rows = _read_return_xlsx(PURCHASE_RETURN_XLSX)
    stats = {"returns": 0, "lines": 0, "skipped": 0, "errors": []}
    cur = conn.cursor()
    by_voucher: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        status = _clean_text(row.get("Status")).lower()
        if status == "draft":
            stats["skipped"] += 1
            continue
        vid = _clean_text(row.get("Return ID / Voucher No."))
        if not vid:
            continue
        by_voucher[vid].append(row)

    for vid, lines in by_voucher.items():
        head = lines[0]
        supplier_name = _clean_text(head.get("Distributor Name"))
        supplier_id = get_or_create_supplier(conn, supplier_name, "", "", "", "")
        return_date = _parse_date(str(head.get("Bill Date") or ""))
        return_items = []
        purchase_id = None
        for row in lines:
            name = _clean_text(row.get("Item Name"))
            batch = _clean_text(row.get("Batch"))
            expiry = _parse_expiry(str(row.get("Expiry") or ""))
            pack_size = _pack_size_from_return_row(row)
            med_id = _find_medicine_id(
                conn, name, batch, expiry, as_of_date=return_date, pack_size=pack_size,
            )
            if not med_id:
                stats["errors"].append(f"PR {vid}: no medicine {name} batch={batch}")
                continue
            cur.execute("SELECT type, COALESCE(unit,'1') FROM medicines WHERE id=?", (med_id,))
            mrow = cur.fetchone()
            med_type = mrow[0] if mrow else ""
            tps = parse_tablets_per_stripe(mrow[1] if mrow else "1") or 1
            units = _float(row.get("Qty")) + _float(row.get("Free Qty"))
            if is_strip_count_type(med_type or ""):
                qty_strips = units / tps
                stock_deduction = units
            else:
                qty_strips = units
                stock_deduction = units
            rate = _float(row.get("PTR (₹)")) or _float(row.get("D.Price (₹)"))
            amount = _float(row.get("Amount (₹)"))
            if purchase_id is None:
                purchase_id = _find_purchase_for_return(conn, supplier_id, med_id, return_date)
            return_items.append({
                "med_id": med_id,
                "qty": qty_strips,
                "rate": rate,
                "amount": amount,
                "stock_deduction": stock_deduction,
            })
        if not return_items:
            continue
        cur.execute("SELECT COALESCE(MAX(id),0)+1 FROM purchase_returns")
        return_no = f"PR{cur.fetchone()[0]}"
        refund = round(sum(i["amount"] for i in return_items), 2)
        try:
            cur.execute(
                """
                INSERT INTO purchase_returns
                    (return_no, purchase_id, supplier_id, return_date, refund_amount, discount, reason)
                VALUES (?, ?, ?, ?, ?, 0, ?)
                """,
                (return_no, purchase_id, supplier_id, return_date, refund, f"eVital import {vid}"),
            )
            return_id = cur.lastrowid
            for item in return_items:
                cur.execute(
                    """
                    INSERT INTO purchase_return_items (return_id, medicine_id, qty, rate, amount)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (return_id, item["med_id"], item["qty"], item["rate"], item["amount"]),
                )
                cur.execute(
                    "UPDATE medicines SET stock_qty = MAX(0, stock_qty - ?) WHERE id = ?",
                    (item["stock_deduction"], item["med_id"]),
                )
            conn.commit()
            recalculate_supplier_due(conn, supplier_id)
            stats["returns"] += 1
            stats["lines"] += len(return_items)
        except Exception as exc:
            conn.rollback()
            stats["errors"].append(f"PR {vid}: {exc}")
    return stats


def import_sales_returns(conn: sqlite3.Connection) -> dict:
    rows = _read_return_xlsx(SALES_RETURN_XLSX)
    stats = {"returns": 0, "lines": 0, "errors": []}
    cur = conn.cursor()
    by_bill: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        cust = _clean_text(row.get("Customer Name"))
        bdate = _clean_text(row.get("Bill Date"))
        rbno = _clean_text(row.get("Return Bill No."))
        if not cust or not bdate:
            continue
        key = f"{rbno}|{bdate}|{cust}"
        by_bill[key].append(row)

    for key, lines in by_bill.items():
        head = lines[0]
        cust_name = _clean_text(head.get("Customer Name")) or "COUNTER SALE"
        customer_id = get_or_create_customer(conn, cust_name, "", "")
        return_date = _parse_date(str(head.get("Bill Date") or ""))
        return_items = []
        sale_id = None
        for row in lines:
            name = _clean_text(row.get("Item Name"))
            batch = _clean_text(row.get("Batch"))
            expiry = _parse_expiry(str(row.get("Expiry") or ""))
            pack_size = _pack_size_from_return_row(row)
            med_id = _find_medicine_id(
                conn, name, batch, expiry, as_of_date=return_date, pack_size=pack_size,
            )
            if not med_id:
                stats["errors"].append(f"SR {key}: no medicine {name}")
                continue
            cur.execute("SELECT type, COALESCE(unit,'1') FROM medicines WHERE id=?", (med_id,))
            mrow = cur.fetchone()
            med_type = mrow[0] if mrow else ""
            tps = parse_tablets_per_stripe(mrow[1] if mrow else "1") or 1
            qty = _float(row.get("Qty"))
            if is_strip_count_type(med_type or ""):
                stock_add = qty
            else:
                stock_add = qty
            rate = _float(row.get("D.Price (₹)"))
            amount = _float(row.get("Amount (₹)"))
            if sale_id is None:
                sale_id = _find_sale_for_return(conn, customer_id, med_id, return_date)
            return_items.append({
                "medicine_id": med_id,
                "qty": stock_add,
                "rate": rate,
                "amount": amount,
            })
        if not return_items:
            continue
        refund = round(sum(i["amount"] for i in return_items), 2)
        cur.execute("SELECT COALESCE(MAX(id),0)+1 FROM sales_returns")
        return_no = f"SR{cur.fetchone()[0]}"
        try:
            cur.execute(
                """
                INSERT INTO sales_returns
                    (return_no, sale_id, customer_id, return_date, refund_amount, discount, reason)
                VALUES (?, ?, ?, ?, ?, 0, ?)
                """,
                (return_no, sale_id, customer_id, return_date, refund, f"eVital import {key}"),
            )
            return_id = cur.lastrowid
            for item in return_items:
                cur.execute(
                    """
                    INSERT INTO sales_return_items (return_id, medicine_id, qty, rate, amount)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (return_id, item["medicine_id"], item["qty"], item["rate"], item["amount"]),
                )
                cur.execute(
                    "UPDATE medicines SET stock_qty = stock_qty + ? WHERE id = ?",
                    (item["qty"], item["medicine_id"]),
                )
            conn.commit()
            recalculate_customer_due(conn, customer_id)
            stats["returns"] += 1
            stats["lines"] += len(return_items)
        except Exception as exc:
            conn.rollback()
            stats["errors"].append(f"SR {key}: {exc}")
    return stats


def _is_countable_pack_unit(unit_label: str) -> bool:
    u = (unit_label or "").upper()
    if any(k in u for k in ("TABLET", "CAPSULE", "PIECE", "CAPSULES")):
        return True
    return bool(re.search(r"\d+\s*(ML|GM|G|M\b)", u))


def verify_stock(conn: sqlite3.Connection) -> dict:
    cur = conn.cursor()
    app_meds = _load_app_medicines(conn)

    evital_rows = _load_evital_stock_rows_full()

    matched = mismatched = missing_in_app = 0
    mismatches: list[str] = []
    used_med_ids: set[int] = set()

    name_rows: dict[str, int] = defaultdict(int)
    dup_keys: dict[tuple[str, str], int] = defaultdict(int)
    for item_name, unit_label, _, _, _ in evital_rows:
        name_rows[_stock_name_key(item_name)] += 1
        dup_keys[(_stock_name_key(item_name), unit_label)] += 1

    for item_name, unit_label, ev_qty, gst, hsn in evital_rows:
        use_pack = _evital_use_pack(item_name, unit_label, name_rows)
        use_gst = dup_keys[(_stock_name_key(item_name), unit_label)] > 1
        matches = _match_app_meds(
            app_meds, item_name, unit_label, use_pack,
            gst_pct=gst if use_gst else None,
            hsn_code=hsn if use_gst else "",
        )
        app_qty = sum(float(m[4] or 0) for m in matches)
        for m in matches:
            used_med_ids.add(int(m[0]))
        if app_qty == 0 and ev_qty == 0:
            matched += 1
            continue
        if app_qty == 0 and ev_qty > 0:
            missing_in_app += 1
            mismatches.append(f"MISSING: {item_name} [{unit_label}] evital={ev_qty:.0f}")
            continue
        tol = max(1.0, abs(ev_qty) * 0.01)
        if abs(app_qty - ev_qty) <= tol:
            matched += 1
        else:
            mismatched += 1
            mismatches.append(
                f"MISMATCH: {item_name} [{unit_label}] app={app_qty:.0f} "
                f"evital={ev_qty:.0f} diff={app_qty-ev_qty:.0f}"
            )

    extra_in_app = 0
    for rec in app_meds:
        mid, _, _, _, stock = rec[:5]
        if int(mid) in used_med_ids or float(stock or 0) == 0:
            continue
        extra_in_app += 1

    neg = conn.execute("SELECT COUNT(*) FROM medicines WHERE stock_qty < 0").fetchone()[0]

    report = {
        "evital_items": len(evital_rows),
        "matched": matched,
        "mismatched": mismatched,
        "missing_in_app": missing_in_app,
        "extra_in_app": extra_in_app,
        "negative_stock_batches": neg,
        "mismatches": mismatches,
    }
    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        f.write("Shivkrupa eVital import stock verification\n")
        f.write("=" * 60 + "\n")
        for k in (
            "evital_items", "matched", "mismatched",
            "missing_in_app", "extra_in_app", "negative_stock_batches",
        ):
            f.write(f"{k}: {report[k]}\n")
        f.write(f"match_pct: {100*matched/max(len(evital_rows),1):.1f}%\n\n")
        f.write("Mismatches:\n")
        for line in mismatches:
            f.write(line + "\n")
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--phase",
        choices=("all", "purchases", "opening", "sales", "returns", "reconcile", "force_stock", "sync_master", "verify"),
        default="all",
    )
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--reset", action="store_true", help="Clear store tables before import")
    args = parser.parse_args()

    if not os.path.isfile(DB_PATH):
        raise SystemExit(f"DB not found: {DB_PATH}")

    conn = sqlite3.connect(DB_PATH, timeout=120)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")

    if args.reset:
        _log("Resetting store tables...")
        reset_db(conn)

    if args.phase in ("all", "purchases", "opening", "sales"):
        _setup_sequential_numbers()

    _log(f"Database: {DB_PATH}")
    results: dict[str, Any] = {}

    if args.phase in ("all", "purchases"):
        _log("Phase 1: Importing purchases (serial # by date/time)...")
        results["purchases"] = import_purchases(conn, limit=args.limit)

    if args.phase in ("all", "opening"):
        _log("Phase 2: Opening stock (pre-purchase batches)...")
        results["opening_stock"] = import_opening_stock(conn)

    if args.phase in ("all", "sales"):
        _log("Phase 3: Importing sales (bill # by date/time)...")
        results["sales"] = import_sales(conn, limit=args.limit)

    if args.phase in ("all", "returns"):
        _log("Phase 4: Importing returns...")
        results["purchase_returns"] = import_purchase_returns(conn)
        results["sales_returns"] = import_sales_returns(conn)

    if args.phase in ("all", "reconcile"):
        _log("Phase 5: Reconciling stock to eVital summary...")
        results["reconcile"] = reconcile_stock_to_evital(conn)

    if args.phase in ("all", "force_stock"):
        _log("Phase 6: Setting exact stock per eVital CSV...")
        results["force_stock"] = force_exact_stock_to_evital(conn)

    if args.phase in ("all", "sync_master"):
        _log("Phase 7: Syncing master medicine catalog...")
        results["sync_master"] = sync_master_medicine_db(conn)

    if args.phase in ("all", "verify"):
        _log("Phase 8: Stock verification...")
        results["stock"] = verify_stock(conn)

    conn.close()
    _log("Done.")
    if _SeqNo.purchase or _SeqNo.sales:
        _log(f"Serial numbers assigned: purchases 1..{_SeqNo.purchase}, sales SCB1..SCB{_SeqNo.sales}")
    for phase, data in results.items():
        if phase == "stock":
            _log(f"{phase}: matched={data['matched']}/{data['evital_items']} "
                 f"mismatched={data['mismatched']} neg_batches={data['negative_stock_batches']}")
        else:
            _log(f"{phase}: {data}")
    if "stock" in results:
        _log(f"Full report: {REPORT_PATH}")


if __name__ == "__main__":
    main()
