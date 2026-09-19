import os
import sqlite3

from core.store_manager import get_store_db_path

p = get_store_db_path("Store_Roshan")
print("mac2 db", p, "exists", os.path.isfile(p))
if not os.path.isfile(p):
    raise SystemExit(1)
print("size", os.path.getsize(p))
con = sqlite3.connect(p)
cur = con.cursor()
print("=== MAC2 counts ===")
for t in ["customers", "suppliers", "medicines", "sales", "purchases", "doctors"]:
    total = cur.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
    try:
        active = cur.execute(
            f"SELECT COUNT(*) FROM {t} WHERE COALESCE(deleted,0)=0"
        ).fetchone()[0]
    except Exception:
        active = total
    print(f"{t}: total={total} active={active}")
print("=== HARSHAL ===")
for r in cur.execute(
    "SELECT id,name,phone,total_due,total_credit FROM customers "
    "WHERE name LIKE '%HARSH%' OR name LIKE '%Harsh%'"
):
    print(r)
print("=== latest sales ===")
for r in cur.execute(
    "SELECT id,bill_no,substr(bill_date,1,10),customer_id,customer_name,"
    "total_amount,amount_paid,total_due FROM sales "
    "WHERE COALESCE(is_autosave,0)=0 ORDER BY id DESC LIMIT 12"
):
    print(r)
print(
    "max ids",
    {
        t: cur.execute(f"SELECT MAX(id) FROM {t}").fetchone()[0]
        for t in ["customers", "sales", "purchases", "medicines"]
    },
)
print("=== customers by id desc ===")
for r in cur.execute("SELECT id,name,total_due FROM customers ORDER BY id DESC LIMIT 8"):
    print(r)
# Diff sample: sales totals
print("=== sales sum ===")
print(
    cur.execute(
        "SELECT COUNT(*), ROUND(SUM(total_amount),2), ROUND(SUM(amount_paid),2) "
        "FROM sales WHERE COALESCE(deleted,0)=0 AND COALESCE(is_autosave,0)=0"
    ).fetchone()
)
con.close()
