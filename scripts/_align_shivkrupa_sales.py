"""Check if Shivkrupa sales align 1:1 with CSV order_number import order."""
from __future__ import annotations

import csv
import sqlite3
from collections import defaultdict
from datetime import datetime
from pathlib import Path

DB = Path(
    r"D:\Satpuda Core Server Update\mac2\config\stores"
    r"\Store_Shivkrupa_Medical_General_Store\veterinary.db"
)
SALES_CSV = Path(r"c:\Users\win10\Downloads\vm9xzvqmyn.csv")


def parse(raw: str) -> datetime:
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


def main() -> None:
    conn = sqlite3.connect(str(DB))
    c = conn.cursor()
    groups: dict[str, list[dict]] = defaultdict(list)
    with open(SALES_CSV, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            groups[row["order_number"]].append(row)
    keys = sorted(
        groups, key=lambda k: (parse(groups[k][0].get("created_date", "")), k)
    )
    sales = c.execute(
        """
        SELECT s.id, s.bill_no, s.bill_date, s.total_amount, s.amount_paid, c.name
        FROM sales s JOIN customers c ON c.id=s.customer_id
        WHERE COALESCE(s.deleted,0)=0 AND COALESCE(s.is_autosave,0)=0
        ORDER BY s.id
        """
    ).fetchall()
    print("csv bills", len(keys), "db sales", len(sales))

    name_ok = paid_close = 0
    samples = []
    for i, (sid, bno, bdate, tot, paid, cname) in enumerate(sales):
        if i >= len(keys):
            break
        head = groups[keys[i]][0]
        ct = float(head.get("total") or 0)
        cp = float(head.get("paid_amount") or 0)
        pn = (head.get("patient_name") or "COUNTER SALE").strip().upper() or "COUNTER SALE"
        cn = (cname or "").upper()
        if pn[:10] in cn or cn[:10] in pn or (pn == "COUNTER SALE" and "COUNTER" in cn):
            name_ok += 1
        else:
            if len(samples) < 12:
                samples.append(
                    (
                        bno,
                        bdate,
                        cname,
                        head.get("created_date", "")[:10],
                        pn,
                        ct,
                        float(tot),
                        cp,
                        float(paid),
                    )
                )
        if abs(cp - float(paid)) <= 1.0:
            paid_close += 1

    n = min(len(keys), len(sales))
    print("compared", n, "name_ok", name_ok, "paid_close", paid_close)
    print("name mismatches sample:")
    for s in samples:
        print(" ", s)
    print("first 8 aligned:")
    for i in range(8):
        sid, bno, bdate, tot, paid, cname = sales[i]
        head = groups[keys[i]][0]
        print(
            i,
            bno,
            bdate,
            (cname or "")[:18],
            float(tot),
            float(paid),
            "|",
            head.get("created_date", "")[:10],
            (head.get("patient_name") or "")[:18],
            head.get("total"),
            head.get("paid_amount"),
            "bill",
            head.get("bill_no"),
        )

    # How many overpaid if we cap paid to db total when CSV fully paid
    would_fix = 0
    for i, (sid, bno, bdate, tot, paid, cname) in enumerate(sales):
        if i >= len(keys):
            break
        head = groups[keys[i]][0]
        ct = float(head.get("total") or 0)
        cp = float(head.get("paid_amount") or 0)
        db_tot = float(tot)
        db_paid = float(paid)
        if abs(cp - ct) < 0.51:  # fully paid in source
            target = db_tot
        else:
            # scale partial
            target = round(db_tot * (cp / ct), 2) if ct > 0 else min(cp, db_tot)
        if abs(db_paid - target) > 0.5:
            would_fix += 1
    print("sales that would change paid", would_fix)

    # purchases by bill_number
    by_p = {}
    with open(
        r"c:\Users\win10\Downloads\xpjfe4h0im.csv", encoding="utf-8-sig", newline=""
    ) as f:
        for row in csv.DictReader(f):
            bn = (row.get("bill_no") or "").strip()
            if bn and bn not in by_p:
                by_p[bn] = row
    purch = c.execute(
        """
        SELECT id, purchase_no, bill_number, final_amount, total_amount,
               amount_paid_at_entry, amount_paid, need_to_pay, due_amount
        FROM purchases
        WHERE COALESCE(deleted,0)=0 AND COALESCE(is_autosave,0)=0
        """
    ).fetchall()
    matched = over = under = 0
    for row in purch:
        bn = str(row[2] or "").strip()
        src = by_p.get(bn)
        if not src:
            continue
        matched += 1
        fa = float(row[3] or row[4] or 0)
        ape = float(row[5] or 0)
        cp = float(src.get("paid_amount") or 0)
        ct = float(src.get("total") or src.get("amount") or 0)
        if ape > fa + 0.5:
            over += 1
        if ape + 0.5 < fa and abs(cp - ct) < 0.51:
            # CSV says fully paid but we have due
            under += 1
    print("purch matched", matched, "overpaid_entry", over, "csv_full_but_db_under?", under)
    conn.close()


if __name__ == "__main__":
    main()
