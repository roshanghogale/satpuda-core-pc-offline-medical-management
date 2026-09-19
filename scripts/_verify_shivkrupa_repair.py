import csv
import sqlite3
from collections import Counter

DB = r"D:\Satpuda Core Server Update\mac2\config\stores\Store_Shivkrupa_Medical_General_Store\veterinary.db"
c = sqlite3.connect(DB).cursor()

print(
    "schedules",
    c.execute(
        "SELECT COALESCE(NULLIF(TRIM(schedule),''),'(blank)'), COUNT(*) "
        "FROM medicines GROUP BY 1 ORDER BY 2 DESC"
    ).fetchall(),
)
print(
    "overpaid sales",
    c.execute(
        "SELECT COUNT(*) FROM sales WHERE amount_paid>total_amount+0.5"
    ).fetchone(),
)
print(
    "sales due>0.5",
    c.execute(
        "SELECT COUNT(*), ROUND(SUM(due_amount),2) FROM sales WHERE due_amount>0.5"
    ).fetchone(),
)
print(
    "purch due>0.5",
    c.execute(
        "SELECT COUNT(*), ROUND(SUM(due_amount),2) FROM purchases WHERE due_amount>0.5"
    ).fetchone(),
)
print("top customer dues")
for r in c.execute(
    "SELECT name, total_due, total_credit FROM customers "
    "WHERE total_due>0.01 ORDER BY total_due DESC LIMIT 15"
):
    print(" ", r)
print("top customer credits")
for r in c.execute(
    "SELECT name, total_due, total_credit FROM customers "
    "WHERE total_credit>0.01 ORDER BY total_credit DESC LIMIT 10"
):
    print(" ", r)
print("suppliers due/credit")
for r in c.execute(
    "SELECT name, total_due, total_credit FROM suppliers "
    "WHERE total_due>0.01 OR total_credit>0.01 ORDER BY total_due DESC"
):
    print(" ", r)

zero = Counter()
n = 0
with open(r"c:\Users\win10\Downloads\vm9xzvqmyn.csv", encoding="utf-8-sig", newline="") as f:
    seen = set()
    for row in csv.DictReader(f):
        on = row["order_number"]
        if on in seen:
            continue
        seen.add(on)
        n += 1
        pa = float(row.get("paid_amount") or 0)
        tot = float(row.get("total") or 0)
        if pa <= 0 and tot > 0:
            zero[row.get("payment_method") or "(none)"] += 1
print("csv bills", n, "unpaid paid_amount=0", dict(zero))

# Sample a due sale vs CSV alignment
from collections import defaultdict
from datetime import datetime

def parse(raw):
    raw = (raw or "").strip()
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            val = raw if "%f" in fmt else raw.split(".")[0]
            return datetime.strptime(val, fmt)
        except ValueError:
            continue
    return datetime.min

groups = defaultdict(list)
with open(r"c:\Users\win10\Downloads\vm9xzvqmyn.csv", encoding="utf-8-sig", newline="") as f:
    for row in csv.DictReader(f):
        groups[row["order_number"]].append(row)
keys = sorted(groups, key=lambda k: (parse(groups[k][0].get("created_date", "")), k))
sales = c.execute(
    "SELECT id, bill_no, total_amount, amount_paid, due_amount FROM sales ORDER BY id"
).fetchall()
print("sample due sales vs csv:")
shown = 0
for i, (sid, bno, tot, paid, due) in enumerate(sales):
    if float(due or 0) <= 0.5:
        continue
    head = groups[keys[i]][0]
    print(
        bno,
        "db",
        tot,
        paid,
        due,
        "csv",
        head.get("total"),
        head.get("paid_amount"),
        head.get("payment_method"),
        (head.get("patient_name") or "")[:20],
    )
    shown += 1
    if shown >= 12:
        break
