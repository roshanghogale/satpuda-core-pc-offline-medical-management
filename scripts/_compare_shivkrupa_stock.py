"""Compare Shivkrupa DB stock vs batch + item CSVs."""
from __future__ import annotations

import csv
import sqlite3
from collections import defaultdict

from core.name_utils import normalize_medicine_name

DB = r"D:\Satpuda Core Server Update\mac2\config\stores\Store_Shivkrupa_Medical_General_Store\veterinary.db"
BATCH = (
    r"C:\Users\win10\Downloads"
    r"\shivkrupa_medical_and_general_store_batch_wise_inventory_stock_summary_report_09-08-26_09-50-37.csv"
)
ITEM = (
    r"C:\Users\win10\Downloads"
    r"\shivkrupa_medical_and_general_store_item_wise_inventory_stock_summary_report_08-08-26_05-22-30.csv"
)


def nf(x):
    try:
        return float(x or 0)
    except Exception:
        return 0.0


def nn(name):
    return normalize_medicine_name(name or "").upper()


def nb(batch):
    return (batch or "").strip().upper()


def main():
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    db_batches = {}
    db_by_name = defaultdict(float)
    for mid, name, batch, stock in c.execute(
        "SELECT id, name, COALESCE(batch_no,''), COALESCE(stock_qty,0) FROM medicines"
    ):
        db_batches[(nn(name), nb(batch))] = (mid, float(stock), name, batch)
        db_by_name[nn(name)] += float(stock)

    with open(BATCH, encoding="utf-8-sig", newline="") as f:
        brows = list(csv.DictReader(f.readlines()[2:]))
    with open(ITEM, encoding="utf-8-sig", newline="") as f:
        irows = list(csv.DictReader(f.readlines()[2:]))

    batch_sum = sum(nf(r.get("Stock")) for r in brows)
    item_sum = sum(nf(r.get("Stock")) for r in irows)
    db_sum = sum(v[1] for v in db_batches.values())
    print("sums batch_csv", batch_sum, "item_csv", item_sum, "db", round(db_sum, 2))

    mism = []
    for r in brows:
        key = (nn(r.get("Item Name")), nb(r.get("Batch")))
        csv_s = nf(r.get("Stock"))
        row = db_batches.get(key)
        if not row:
            mism.append(("missing_db", r.get("Item Name"), r.get("Batch"), csv_s, None))
            continue
        if abs(row[1] - csv_s) > 0.01:
            mism.append(("diff", row[2], row[3], csv_s, row[1]))
    print("batch mismatches", len(mism))
    for m in mism[:25]:
        print(" ", m)

    # item-wise name totals
    item_by = defaultdict(float)
    for r in irows:
        item_by[nn(r.get("Item Name"))] += nf(r.get("Stock"))
    name_diff = []
    for name, csv_s in item_by.items():
        dbs = db_by_name.get(name, 0.0)
        if abs(dbs - csv_s) > 0.5:
            name_diff.append((name, csv_s, dbs, dbs - csv_s))
    name_diff.sort(key=lambda x: abs(x[3]), reverse=True)
    print("item-name diffs (>0.5)", len(name_diff))
    for m in name_diff[:30]:
        print(" ", m)
    conn.close()


if __name__ == "__main__":
    import sys
    import os

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    main()
