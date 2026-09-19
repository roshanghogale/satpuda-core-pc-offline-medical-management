"""
Shivkrupa-only repair:
  1) Apply medicine schedules from Schedule_* Sales/Purchase xlsx exports
  2) Reconcile batch stock from eVital batch-wise CSV
  3) Fix sale/purchase amount_paid vs eVital paid/total (import overpay bug)
  4) Recalculate all customer + supplier dues

Does NOT reset inventory or re-import bills.
"""
from __future__ import annotations

import argparse
import csv
import glob
import os
import re
import shutil
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import core.online_guard as _og  # noqa: E402

_og.ensure_can_mutate = lambda: None  # type: ignore

from core.customer_service import recalculate_customer_due  # noqa: E402
from core.name_utils import normalize_medicine_name  # noqa: E402
from core.purchase_service import recalculate_supplier_due  # noqa: E402
from scripts.reconcile_shivkrupa_batch_stock import (  # noqa: E402
    DEFAULT_CSV as BATCH_CSV,
    load_batch_csv,
    reconcile_batch_stock,
    write_report,
)

try:
    import openpyxl
except ImportError as exc:  # pragma: no cover
    raise SystemExit("openpyxl required") from exc

DB_PATH = os.path.join(
    ROOT, "config", "stores", "Store_Shivkrupa_Medical_General_Store", "veterinary.db"
)
SCHEDULE_DIR = r"d:\schedule exports shivkrupa"
SALES_CSV = r"c:\Users\win10\Downloads\vm9xzvqmyn.csv"
PURCH_CSV = r"c:\Users\win10\Downloads\xpjfe4h0im.csv"
REPORT_PATH = os.path.join(
    ROOT,
    "config",
    "stores",
    "Store_Shivkrupa_Medical_General_Store",
    "repair_stock_schedules_payments_report.txt",
)

_SCHEDULE_MAP = {
    "H": "H",
    "H1": "H1",
    "G": "G",
    "X": "X",
    "H - NRX": "H",
    "H-NRX": "H",
    "NRX": "H",
}


def _log(msg: str) -> None:
    print(msg, flush=True)


def _norm_name(name: str) -> str:
    return normalize_medicine_name(name or "").upper()


def _norm_batch(batch: str) -> str:
    return (batch or "").strip().upper()


def _norm_schedule(raw: Any) -> str:
    s = str(raw or "").strip().upper().replace("_", " ")
    if not s:
        return ""
    if s in _SCHEDULE_MAP:
        return _SCHEDULE_MAP[s]
    # title-case variants from excel
    key = str(raw or "").strip()
    if key in _SCHEDULE_MAP:
        return _SCHEDULE_MAP[key]
    # H - Nrx etc
    compact = re.sub(r"\s+", " ", key).upper()
    return _SCHEDULE_MAP.get(compact, compact.split()[0] if compact else "")


def _parse_dt(raw: str) -> datetime:
    raw = (raw or "").strip()
    for fmt in (
        "%Y-%m-%d %H:%M:%S.%f",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
    ):
        try:
            val = raw if "%f" in fmt else raw.split(".")[0]
            return datetime.strptime(val, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(raw[:19])
    except ValueError:
        return datetime.min


def apply_schedules(conn: sqlite3.Connection, schedule_dir: str) -> dict[str, Any]:
    files = sorted(
        p
        for p in glob.glob(os.path.join(schedule_dir, "Schedule_*.xlsx"))
        if "(1)" not in os.path.basename(p)
    )
    # name+batch -> best schedule (prefer H1 over H over G)
    rank = {"H1": 3, "H": 2, "X": 2, "G": 1}
    by_nb: dict[tuple[str, str], str] = {}
    by_name: dict[str, set[str]] = defaultdict(set)
    rows_read = 0

    for path in files:
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        ws = wb.active
        headers = None
        for row in ws.iter_rows(values_only=True):
            vals = list(row) if row else []
            if headers is None:
                if vals and vals[0] == "Bill No":
                    headers = vals
                continue
            if not vals or vals[0] is None:
                continue
            try:
                type_i = headers.index("Type")
            except ValueError:
                continue
            sched = _norm_schedule(vals[type_i])
            if not sched:
                continue
            if "Medicine Name" in headers:
                name = vals[headers.index("Medicine Name")]
            elif "Item Name" in headers:
                name = vals[headers.index("Item Name")]
            else:
                continue
            batch = vals[headers.index("Batch")] if "Batch" in headers else ""
            nn, nb = _norm_name(str(name or "")), _norm_batch(str(batch or ""))
            if not nn:
                continue
            rows_read += 1
            key = (nn, nb)
            prev = by_nb.get(key)
            if prev is None or rank.get(sched, 0) > rank.get(prev, 0):
                by_nb[key] = sched
            by_name[nn].add(sched)
        wb.close()

    cur = conn.cursor()
    meds = cur.execute(
        "SELECT id, name, COALESCE(batch_no,''), COALESCE(schedule,'') FROM medicines"
    ).fetchall()
    updated = 0
    already = 0
    ambiguous = 0
    unmatched = 0
    changes: list[str] = []

    for mid, name, batch, old in meds:
        nn, nb = _norm_name(name), _norm_batch(batch)
        sched = by_nb.get((nn, nb))
        if not sched:
            opts = by_name.get(nn) or set()
            if len(opts) == 1:
                sched = next(iter(opts))
            elif len(opts) > 1:
                # pick highest rank
                sched = max(opts, key=lambda s: rank.get(s, 0))
                ambiguous += 1
            else:
                unmatched += 1
                continue
        old_s = (old or "").strip()
        if old_s == sched:
            already += 1
            continue
        cur.execute("UPDATE medicines SET schedule=? WHERE id=?", (sched, mid))
        updated += 1
        if len(changes) < 40:
            changes.append(f"id={mid} {name} [{batch}] '{old_s}' -> '{sched}'")

    return {
        "files": len(files),
        "rows_read": rows_read,
        "map_keys": len(by_nb),
        "updated": updated,
        "already": already,
        "ambiguous_name": ambiguous,
        "unmatched": unmatched,
        "changes": changes,
    }


def _target_paid(db_total: float, csv_total: float, csv_paid: float) -> float:
    db_total = round(float(db_total or 0), 2)
    csv_total = round(float(csv_total or 0), 2)
    csv_paid = round(float(csv_paid or 0), 2)
    if db_total <= 0:
        return 0.0
    # Fully paid / settled in source → mark our bill fully paid
    if csv_total <= 0 or abs(csv_paid - csv_total) <= 0.51:
        return db_total
    # Partial: scale source ratio onto our computed total
    ratio = max(0.0, min(1.0, csv_paid / csv_total))
    return round(db_total * ratio, 2)


def fix_sales_payments(conn: sqlite3.Connection, sales_csv: str) -> dict[str, Any]:
    groups: dict[str, list[dict]] = defaultdict(list)
    with open(sales_csv, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            groups[str(row.get("order_number") or "").strip()].append(row)
    keys = sorted(
        groups, key=lambda k: (_parse_dt(groups[k][0].get("created_date", "")), k)
    )
    sales = conn.execute(
        """
        SELECT id, total_amount, amount_paid, cash_paid, online_paid, due_amount
        FROM sales
        WHERE COALESCE(deleted,0)=0 AND COALESCE(is_autosave,0)=0
        ORDER BY id
        """
    ).fetchall()
    if len(keys) != len(sales):
        raise RuntimeError(
            f"Sales count mismatch CSV={len(keys)} DB={len(sales)}; abort payment fix"
        )

    updated = 0
    samples: list[str] = []
    for i, (sid, tot, paid, cash, online, due) in enumerate(sales):
        head = groups[keys[i]][0]
        csv_total = float(head.get("total") or 0)
        csv_paid = float(head.get("paid_amount") or 0)
        if csv_paid <= 0 and csv_total > 0:
            # unpaid / credit sale in source
            target = 0.0
        else:
            target = _target_paid(float(tot or 0), csv_total, csv_paid)
        db_tot = round(float(tot or 0), 2)
        new_due = round(max(0.0, db_tot - target), 2)
        new_credit = round(max(0.0, target - db_tot), 2)
        # Keep cash/online split when possible
        old_cash = float(cash or 0)
        old_online = float(online or 0)
        old_paid = float(paid or 0)
        if abs(old_paid - target) <= 0.009 and abs(float(due or 0) - new_due) <= 0.009:
            continue
        if old_paid > 0 and (old_cash + old_online) > 0:
            # rescale modes
            scale = target / old_paid if old_paid else 1.0
            new_cash = round(old_cash * scale, 2)
            new_online = round(target - new_cash, 2)
        else:
            method = (head.get("payment_method") or "").strip().lower()
            if "upi" in method or "online" in method or "card" in method:
                new_cash, new_online = 0.0, target
            else:
                new_cash, new_online = target, 0.0
        conn.execute(
            """
            UPDATE sales SET
              amount_paid=?, cash_paid=?, online_paid=?,
              due_amount=?, credit_amount=?,
              account_cleared=CASE WHEN ?<=0.009 THEN 1 ELSE 0 END,
              bill_cleared=CASE WHEN ?<=0.009 THEN 1 ELSE 0 END
            WHERE id=?
            """,
            (target, new_cash, new_online, new_due, new_credit, new_due, new_due, sid),
        )
        updated += 1
        if len(samples) < 25:
            samples.append(
                f"sale {sid}: paid {old_paid}->{target} due {due}->{new_due} "
                f"(csv paid={csv_paid} total={csv_total} db_total={db_tot})"
            )
    return {"updated": updated, "total": len(sales), "samples": samples}


def fix_purchase_payments(conn: sqlite3.Connection, purch_csv: str) -> dict[str, Any]:
    by_bill: dict[str, dict] = {}
    with open(purch_csv, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            bn = (row.get("bill_no") or "").strip()
            if bn and bn not in by_bill:
                by_bill[bn] = row

    rows = conn.execute(
        """
        SELECT id, bill_number, final_amount, total_amount,
               amount_paid_at_entry, amount_paid, need_to_pay, due_amount,
               cash_paid_at_entry, online_paid_at_entry
        FROM purchases
        WHERE COALESCE(deleted,0)=0 AND COALESCE(is_autosave,0)=0
        """
    ).fetchall()
    updated = 0
    unmatched = 0
    samples: list[str] = []
    for (
        pid,
        bill_number,
        final_amt,
        total_amt,
        entry_paid,
        amount_paid,
        need_to_pay,
        due_amount,
        cash_entry,
        online_entry,
    ) in rows:
        src = by_bill.get(str(bill_number or "").strip())
        if not src:
            unmatched += 1
            continue
        fa = round(float(final_amt if final_amt is not None else total_amt or 0), 2)
        csv_total = float(src.get("total") or src.get("amount") or 0)
        csv_paid = float(src.get("paid_amount") or 0)
        if csv_paid <= 0 and csv_total > 0:
            target = 0.0
        else:
            target = _target_paid(fa, csv_total, csv_paid)
        new_due = round(max(0.0, fa - target), 2)
        old_entry = float(entry_paid or 0)
        if abs(old_entry - target) <= 0.009 and abs(float(due_amount or 0) - new_due) <= 0.009:
            continue
        method = (src.get("payment_method") or "").strip().lower()
        if "upi" in method or "online" in method or "card" in method:
            new_cash, new_online = 0.0, target
        else:
            new_cash, new_online = target, 0.0
        conn.execute(
            """
            UPDATE purchases SET
              amount_paid_at_entry=?, amount_paid=?,
              cash_paid_at_entry=?, online_paid_at_entry=?,
              need_to_pay=?, due_amount=?, due=?,
              credit_amount=?, current_credit=?,
              account_cleared=CASE WHEN ?<=0.009 THEN 1 ELSE 0 END,
              bill_cleared=CASE WHEN ?<=0.009 THEN 1 ELSE 0 END
            WHERE id=?
            """,
            (
                target,
                target,
                new_cash,
                new_online,
                fa,
                new_due,
                new_due,
                round(max(0.0, target - fa), 2),
                round(max(0.0, target - fa), 2),
                new_due,
                new_due,
                pid,
            ),
        )
        updated += 1
        if len(samples) < 25:
            samples.append(
                f"purch {pid} bill={bill_number}: entry_paid {old_entry}->{target} "
                f"due {due_amount}->{new_due} (csv paid={csv_paid} total={csv_total} final={fa})"
            )
    return {
        "updated": updated,
        "matched": len(rows) - unmatched,
        "unmatched": unmatched,
        "samples": samples,
    }


def recalculate_all_dues(conn: sqlite3.Connection) -> dict[str, int]:
    cust_ids = [
        r[0]
        for r in conn.execute(
            "SELECT id FROM customers WHERE COALESCE(deleted,0)=0"
        ).fetchall()
    ]
    supp_ids = [
        r[0]
        for r in conn.execute(
            "SELECT id FROM suppliers WHERE COALESCE(deleted,0)=0"
        ).fetchall()
    ]
    for cid in cust_ids:
        recalculate_customer_due(conn, cid, commit=False, sync=False)
    for sid in supp_ids:
        recalculate_supplier_due(conn, sid, commit=False)
    return {"customers": len(cust_ids), "suppliers": len(supp_ids)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--skip-stock", action="store_true")
    ap.add_argument("--skip-schedules", action="store_true")
    ap.add_argument("--skip-payments", action="store_true")
    ap.add_argument("--batch-csv", default=BATCH_CSV)
    ap.add_argument("--schedule-dir", default=SCHEDULE_DIR)
    args = ap.parse_args()

    if not os.path.isfile(DB_PATH):
        raise SystemExit(f"DB not found: {DB_PATH}")

    backup = ""
    if not args.dry_run:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup = DB_PATH + f".pre_repair_{stamp}"
        shutil.copy2(DB_PATH, backup)
        _log(f"Backup: {backup}")

    conn = sqlite3.connect(DB_PATH)
    report: list[str] = []
    try:
        if not args.skip_schedules:
            _log("Applying schedules…")
            sch = apply_schedules(conn, args.schedule_dir)
            report.append(f"SCHEDULES: {sch}")
            _log(
                f"  schedules updated={sch['updated']} already={sch['already']} "
                f"unmatched={sch['unmatched']} from {sch['files']} files"
            )
            if args.dry_run:
                conn.rollback()
            else:
                conn.commit()

        if not args.skip_stock:
            _log("Reconciling batch stock…")
            csv_rows = load_batch_csv(args.batch_csv)
            stock = reconcile_batch_stock(
                conn, csv_rows, zero_unlisted=True, commit=not args.dry_run
            )
            report.append(
                "STOCK: "
                + str({k: stock[k] for k in stock if k not in ("missing", "changes")})
            )
            _log(
                f"  stock updated={stock['updated']} already_ok={stock['already_ok']} "
                f"zeroed_unlisted={stock['zeroed_unlisted']} missing={len(stock['missing'])}"
            )
            if not args.dry_run:
                write_report(stock, args.batch_csv, backup)

        if not args.skip_payments:
            _log("Fixing sales payments…")
            sfix = fix_sales_payments(conn, SALES_CSV)
            report.append(f"SALES_PAY: updated={sfix['updated']}/{sfix['total']}")
            report.extend("  " + x for x in sfix["samples"])
            _log(f"  sales payment rows updated={sfix['updated']}")

            _log("Fixing purchase payments…")
            pfix = fix_purchase_payments(conn, PURCH_CSV)
            report.append(
                f"PURCH_PAY: updated={pfix['updated']} matched={pfix['matched']} "
                f"unmatched={pfix['unmatched']}"
            )
            report.extend("  " + x for x in pfix["samples"])
            _log(f"  purchase payment rows updated={pfix['updated']}")

            _log("Recalculating dues…")
            import io
            from contextlib import redirect_stdout

            with redirect_stdout(io.StringIO()):
                dues = recalculate_all_dues(conn)
            report.append(f"DUES: {dues}")
            cd = conn.execute(
                "SELECT COUNT(*), ROUND(SUM(total_due),2), ROUND(SUM(total_credit),2) "
                "FROM customers WHERE COALESCE(total_due,0)>0.01 OR COALESCE(total_credit,0)>0.01"
            ).fetchone()
            sd = conn.execute(
                "SELECT COUNT(*), ROUND(SUM(total_due),2), ROUND(SUM(total_credit),2) "
                "FROM suppliers WHERE COALESCE(total_due,0)>0.01 OR COALESCE(total_credit,0)>0.01"
            ).fetchone()
            over_s = conn.execute(
                """
                SELECT COUNT(*) FROM sales
                WHERE COALESCE(deleted,0)=0 AND COALESCE(is_autosave,0)=0
                  AND COALESCE(amount_paid,0) > COALESCE(total_amount,0)+0.5
                """
            ).fetchone()[0]
            over_p = conn.execute(
                """
                SELECT COUNT(*) FROM purchases
                WHERE COALESCE(deleted,0)=0 AND COALESCE(is_autosave,0)=0
                  AND COALESCE(amount_paid_at_entry,0)
                      > COALESCE(final_amount, total_amount, 0)+0.5
                """
            ).fetchone()[0]
            report.append(f"customers nonzero due/credit: {cd}")
            report.append(f"suppliers nonzero due/credit: {sd}")
            report.append(f"overpaid sales remaining: {over_s}")
            report.append(f"overpaid purchases remaining: {over_p}")
            _log(f"  customers with due/credit {cd}")
            _log(f"  suppliers with due/credit {sd}")
            _log(f"  overpaid sales remaining={over_s} purchases={over_p}")

            if args.dry_run:
                conn.rollback()
                _log("Dry-run: rolled back payment/due changes")
            else:
                conn.commit()

        with open(REPORT_PATH, "w", encoding="utf-8") as f:
            f.write("\n".join(report) + "\n")
        _log(f"Report: {REPORT_PATH}")
        if args.dry_run:
            _log("DRY RUN complete (DB unchanged)")
        else:
            _log("Done.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
