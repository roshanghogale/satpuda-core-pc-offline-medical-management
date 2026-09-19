"""Set Shivkrupa medicines.stock_qty from eVital batch-wise stock CSV."""
from __future__ import annotations

import argparse
import csv
import os
import re
import shutil
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime
from typing import Any

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core.name_utils import normalize_medicine_name  # noqa: E402
from scripts.import_evital_shivkrupa import (  # noqa: E402
    DB_PATH,
    _apply_unit_metadata,
    _detect_type,
    _float,
    _is_countable_pack_unit,
    _load_app_medicines,
    _names_compatible,
    _parse_expiry,
    _stock_name_key,
    _tps_for_row,
    _units_compatible,
)

DEFAULT_CSV = (
    r"C:\Users\win10\Downloads"
    r"\shivkrupa_medical_and_general_store_batch_wise_inventory_stock_summary_report_09-08-26_09-50-37.csv"
)
REPORT_PATH = os.path.join(
    ROOT,
    "config",
    "stores",
    "Store_Shivkrupa_Medical_General_Store",
    "batch_stock_reconcile_report.txt",
)


def _norm_batch(batch: str) -> str:
    return (batch or "").strip().upper()


def _norm_name(name: str) -> str:
    return normalize_medicine_name(name).upper()


def load_batch_csv(path: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        lines = f.readlines()
    for row in csv.DictReader(lines[2:]):
        name = (row.get("Item Name") or "").strip()
        batch = (row.get("Batch") or "").strip()
        if not name or not batch:
            continue
        raw_stock = _float(row.get("Stock"))
        rows.append(
            {
                "name": name,
                "batch": batch,
                "unit": (row.get("Unit") or "").strip(),
                # Keep signed stock: negatives mean oversold vs eVital net item qty
                "stock": raw_stock,
                "expiry": _parse_expiry(row.get("Expiry") or ""),
                "item_code": (row.get("Item Code") or "").strip(),
                "manufacturer": (row.get("Manufacturer/Company") or "").strip(),
                "mrp": _float(row.get("MRP")),
                "rate": _float(row.get("PTR")),
                "gst": _float(row.get("GST")),
                "hsn_code": str(row.get("HSN Code") or "").strip(),
                "dosage_type": (row.get("Dosage Type") or "").strip(),
                "category": (row.get("Category") or "").strip(),
            }
        )
    return rows


def _index_db(app_meds: list[tuple]) -> dict[tuple[str, str], list[tuple]]:
    idx: dict[tuple[str, str], list[tuple]] = defaultdict(list)
    by_batch: dict[str, list[tuple]] = defaultdict(list)
    for rec in app_meds:
        mid, name, med_type, unit, stock, gst, hsn, batch = rec
        key = (_norm_name(name), _norm_batch(batch))
        idx[key].append(rec)
        by_batch[_norm_batch(batch)].append(rec)
    return idx, by_batch


def _pick_primary(matches: list[tuple]) -> tuple[int, list[int]]:
    """Choose one medicine row to hold batch stock; zero the rest."""
    if len(matches) == 1:
        return int(matches[0][0]), []
    ranked = sorted(
        matches,
        key=lambda m: (
            -float(m[4] or 0),
            str(m[7] if len(m) > 7 else "").upper() != "OPENING",
            -int(m[0]),
        ),
    )
    primary = int(ranked[0][0])
    dupes = [int(m[0]) for m in ranked[1:]]
    return primary, dupes


def _unit_filter(matches: list[tuple], unit_label: str, item_name: str) -> list[tuple]:
    if not matches:
        return matches
    if not unit_label:
        return matches
    filtered = [
        rec
        for rec in matches
        if _units_compatible(unit_label, str(rec[2] or ""), str(rec[3] or ""), item_name)
    ]
    if filtered:
        return filtered
    if len(matches) == 1:
        only = matches[0]
        db_unit = str(only[3] or "").strip()
        if db_unit in ("1", "1.0"):
            return matches
        if not _is_countable_pack_unit(unit_label):
            return matches
    return filtered


def _match_batch_row(
    row: dict[str, Any],
    idx: dict[tuple[str, str], list[tuple]],
    by_batch: dict[str, list[tuple]],
    app_meds: list[tuple],
) -> list[tuple]:
    name = row["name"]
    batch = _norm_batch(row["batch"])
    unit_label = row.get("unit") or ""
    exact = _unit_filter(idx.get((_norm_name(name), batch), []), unit_label, name)
    if exact:
        return exact

    key = _stock_name_key(name)
    stock_key_matches = _unit_filter(
        [rec for rec in by_batch.get(batch, []) if _stock_name_key(rec[1]) == key],
        unit_label,
        name,
    )
    if stock_key_matches:
        return stock_key_matches

    fuzzy = _unit_filter(
        [rec for rec in by_batch.get(batch, []) if _names_compatible(name, rec[1])],
        unit_label,
        name,
    )
    if fuzzy:
        return fuzzy

    return []


def reconcile_batch_stock(
    conn: sqlite3.Connection,
    csv_rows: list[dict[str, Any]],
    *,
    zero_unlisted: bool = True,
    commit: bool = True,
) -> dict[str, Any]:
    cur = conn.cursor()
    app_meds = _load_app_medicines(conn)
    idx, by_batch = _index_db(app_meds)

    stats: dict[str, Any] = {
        "csv_rows": len(csv_rows),
        "updated": 0,
        "zeroed_dupes": 0,
        "zeroed_unlisted": 0,
        "already_ok": 0,
        "missing": [],
        "changes": [],
    }
    touched: set[int] = set()
    csv_keys: set[tuple[str, str]] = set()

    for row in csv_rows:
        csv_keys.add((_norm_name(row["name"]), _norm_batch(row["batch"])))
        matches = _match_batch_row(row, idx, by_batch, app_meds)
        if not matches:
            stats["missing"].append(
                f"{row['name']} | batch={row['batch']} | csv_stock={row['stock']:.0f}"
            )
            continue

        primary, dupes = _pick_primary(matches)
        # Store non-negative batch qty; negatives are drained from sibling batches below
        target_qty = max(0.0, float(row["stock"]))
        cur_stock = float(
            next(m[4] for m in matches if int(m[0]) == primary) or 0
        )

        for dup_id in dupes:
            cur.execute("UPDATE medicines SET stock_qty=0 WHERE id=?", (dup_id,))
            stats["zeroed_dupes"] += 1
            touched.add(dup_id)

        if abs(cur_stock - target_qty) <= 0.009:
            stats["already_ok"] += 1
            touched.add(primary)
            continue

        cur.execute(
            "UPDATE medicines SET stock_qty=? WHERE id=?",
            (target_qty, primary),
        )
        stats["updated"] += 1
        touched.add(primary)
        stats["changes"].append(
            f"{row['name']} | {row['batch']} | {cur_stock:.0f} -> {target_qty:.0f}"
        )

    # Apply eVital negative batch balances against other batches of the same item
    # so item-level stock matches the item-wise report (oversold batches).
    neg_by_name: dict[str, float] = defaultdict(float)
    for row in csv_rows:
        if float(row["stock"]) < -0.009:
            neg_by_name[_norm_name(row["name"])] += abs(float(row["stock"]))
    stats["neg_drained"] = 0.0
    # Reload current stocks after batch pass
    live_rows = cur.execute(
        "SELECT id, name, COALESCE(batch_no,''), COALESCE(stock_qty,0) FROM medicines"
    ).fetchall()
    by_live_name: dict[str, list[tuple]] = defaultdict(list)
    for mid, name, batch, st in live_rows:
        if float(st or 0) > 0:
            by_live_name[_norm_name(name)].append(
                (int(mid), float(st), name, batch)
            )
    for nname, need in neg_by_name.items():
        remaining = float(need)
        if remaining <= 0:
            continue
        refreshed = sorted(by_live_name.get(nname, []), key=lambda x: -x[1])
        for mid, st, name, batch in refreshed:
            if remaining <= 0.009:
                break
            take = min(st, remaining)
            new_st = round(st - take, 2)
            cur.execute("UPDATE medicines SET stock_qty=? WHERE id=?", (new_st, mid))
            touched.add(mid)
            remaining = round(remaining - take, 2)
            stats["neg_drained"] += take
            stats["updated"] += 1
            stats["changes"].append(
                f"NEG-DRAIN {name} | {batch} | {st:.0f} -> {new_st:.0f} (need left {remaining:.0f})"
            )

    if zero_unlisted:
        for rec in app_meds:
            mid = int(rec[0])
            if mid in touched:
                continue
            stock = float(rec[4] or 0)
            if stock == 0:
                continue
            key = (_norm_name(rec[1]), _norm_batch(str(rec[7] if len(rec) > 7 else "")))
            if key in csv_keys:
                continue
            cur.execute("UPDATE medicines SET stock_qty=0 WHERE id=?", (mid,))
            stats["zeroed_unlisted"] += 1
            stats["changes"].append(
                f"ZERO unlisted | {rec[1]} | {rec[7]} | was {stock:.0f}"
            )

    cur.execute("UPDATE medicines SET stock_qty=0 WHERE stock_qty < 0")
    stats["neg_cleared"] = cur.rowcount
    if commit:
        conn.commit()
    else:
        conn.rollback()
    return stats


def _medicine_type(row: dict[str, Any]) -> str:
    raw = (row.get("dosage_type") or "").strip()
    if raw and raw != "-":
        return raw.capitalize()
    return _detect_type(row["name"], row.get("unit") or "")


def _medicine_unit_value(name: str, unit_label: str, med_type: str) -> str:
    from core.purchase_service import _get_unit_value

    tps = _tps_for_row(med_type, None, unit_label)
    unit_meta: dict[str, Any] = {
        "type": med_type,
        "tablets_per_stripe": tps,
        "pack": unit_label,
        "quantity_value": "",
        "auto_unit": "",
    }
    _apply_unit_metadata(unit_meta, unit_label, med_type, tps, None)
    return str(_get_unit_value(unit_meta) or "1")


def _find_existing_id(
    conn: sqlite3.Connection,
    row: dict[str, Any],
    idx: dict[tuple[str, str], list[tuple]],
    by_batch: dict[str, list[tuple]],
    app_meds: list[tuple],
) -> int | None:
    matches = _match_batch_row(row, idx, by_batch, app_meds)
    if not matches:
        return None
    expiry = row.get("expiry") or ""
    cur = conn.cursor()
    for rec in matches:
        mid = int(rec[0])
        cur.execute("SELECT expiry_date FROM medicines WHERE id=?", (mid,))
        fetched = cur.fetchone()
        if fetched and str(fetched[0] or "") == str(expiry):
            return mid
    return int(matches[0][0])


def add_missing_batch_rows(
    conn: sqlite3.Connection,
    csv_rows: list[dict[str, Any]],
    *,
    commit: bool = True,
) -> dict[str, Any]:
    cur = conn.cursor()
    app_meds = _load_app_medicines(conn)
    idx, by_batch = _index_db(app_meds)

    stats: dict[str, Any] = {"added": 0, "skipped": 0, "details": []}
    for row in csv_rows:
        existing_id = _find_existing_id(conn, row, idx, by_batch, app_meds)
        if existing_id is not None:
            stats["skipped"] += 1
            continue

        name = normalize_medicine_name(row["name"])
        batch = (row["batch"] or "").strip()
        expiry = row.get("expiry") or ""
        unit_label = row.get("unit") or ""
        med_type = _medicine_type(row)
        unit_val = _medicine_unit_value(row["name"], unit_label, med_type)
        stock = max(0.0, float(row.get("stock") or 0))

        cur.execute(
            """
            INSERT INTO medicines
                (name, type, batch_no, expiry_date, gst_percent, mrp, rate,
                 manufacturer, hsn_code, schedule, content_drug, location,
                 stock_qty, unit, is_hidden)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '', ?, ?, 0)
            """,
            (
                name,
                med_type,
                batch,
                expiry,
                float(row.get("gst") or 0),
                float(row.get("mrp") or 0),
                float(row.get("rate") or 0),
                row.get("manufacturer") or "",
                row.get("hsn_code") or "",
                "",
                "",
                stock,
                unit_val,
            ),
        )
        mid = int(cur.lastrowid)
        stats["added"] += 1
        stats["details"].append(
            f"ADD id={mid} | {row['name']} | {unit_label} | batch={batch} | stock={stock:.0f}"
        )
        rec = (
            mid,
            name,
            med_type,
            unit_val,
            stock,
            float(row.get("gst") or 0),
            row.get("hsn_code") or "",
            batch,
        )
        app_meds.append(rec)
        idx[(_norm_name(name), _norm_batch(batch))].append(rec)
        by_batch[_norm_batch(batch)].append(rec)

    if commit:
        conn.commit()
    else:
        conn.rollback()
    return stats


def write_report(stats: dict[str, Any], csv_path: str, backup_path: str) -> None:
    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        f.write("Shivkrupa batch-wise stock reconcile\n")
        f.write("=" * 60 + "\n")
        f.write(f"csv: {csv_path}\n")
        f.write(f"backup: {backup_path}\n")
        for k in (
            "csv_rows",
            "updated",
            "already_ok",
            "zeroed_dupes",
            "zeroed_unlisted",
            "neg_cleared",
        ):
            f.write(f"{k}: {stats.get(k, 0)}\n")
        f.write(f"missing: {len(stats.get('missing', []))}\n\n")
        f.write("Missing CSV rows (no DB batch match):\n")
        for line in stats.get("missing", []):
            f.write(f"  {line}\n")
        f.write("\nChanges:\n")
        for line in stats.get("changes", []):
            f.write(f"  {line}\n")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Reconcile Shivkrupa batch stock from eVital CSV",
    )
    parser.add_argument("--csv", default=DEFAULT_CSV, help="Batch-wise stock CSV path")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compare only; do not write DB",
    )
    parser.add_argument(
        "--keep-unlisted",
        action="store_true",
        help="Do not zero DB batches absent from CSV",
    )
    parser.add_argument(
        "--add-missing",
        action="store_true",
        help="Insert CSV batch rows that are not yet in the DB",
    )
    args = parser.parse_args()

    if not os.path.isfile(DB_PATH):
        raise SystemExit(f"DB not found: {DB_PATH}")
    if not os.path.isfile(args.csv):
        raise SystemExit(f"CSV not found: {args.csv}")

    csv_rows = load_batch_csv(args.csv)
    print(f"Loaded {len(csv_rows)} batch rows from CSV")

    backup = ""
    if not args.dry_run:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup = DB_PATH + f".pre_batch_stock_{stamp}"
        shutil.copy2(DB_PATH, backup)
        print(f"Backup: {backup}")

    conn = sqlite3.connect(DB_PATH)
    try:
        if args.add_missing:
            add_stats = add_missing_batch_rows(
                conn, csv_rows, commit=not args.dry_run
            )
            print(f"Added {add_stats['added']} missing batch row(s)")
            for line in add_stats["details"]:
                print(" ", line)
        stats = reconcile_batch_stock(
            conn,
            csv_rows,
            zero_unlisted=not args.keep_unlisted and not args.add_missing,
            commit=not args.dry_run,
        )
    finally:
        conn.close()

    if args.dry_run:
        print(
            f"Dry run: would update {stats['updated']}, "
            f"zero unlisted {stats['zeroed_unlisted']}, "
            f"missing {len(stats['missing'])}"
        )
        for line in stats["changes"][:20]:
            print(" ", line)
        if len(stats["changes"]) > 20:
            print(f"  ... and {len(stats['changes']) - 20} more")
        return

    write_report(stats, args.csv, backup)
    print(
        f"Done. Updated {stats['updated']} batch(es), "
        f"{stats['already_ok']} already correct, "
        f"zeroed {stats['zeroed_unlisted']} unlisted, "
        f"{len(stats['missing'])} missing."
    )
    print(f"Report: {REPORT_PATH}")


if __name__ == "__main__":
    main()
