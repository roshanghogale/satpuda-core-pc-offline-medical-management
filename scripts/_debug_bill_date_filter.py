"""Debug medicine bill-date filter against a store DB."""
import sqlite3
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.batch_visibility import (
    is_expired_as_of,
    medicine_existed_as_of,
    medicine_existed_sql,
)

db = ROOT / "config" / "stores" / "Store_Shivkrupa_Medical_General_Store" / "veterinary.db"
if len(sys.argv) > 1:
    db = Path(sys.argv[1])
if not db.exists():
    print("DB not found:", db)
    sys.exit(1)

conn = sqlite3.connect(db)
cur = conn.cursor()
bill = "2026-06-17"
as_of = date(2026, 6, 17)

rows = cur.execute(
    f"""
    SELECT id, name, created_at, expiry_date,
           (SELECT MIN(p.purchase_date) FROM purchase_items pi
            JOIN purchases p ON p.id = pi.purchase_id
            WHERE pi.medicine_id = medicines.id) AS first_purchase
    FROM medicines
    WHERE COALESCE(is_hidden, 0) = 0
      AND {medicine_existed_sql()}
    LIMIT 10
    """,
    (bill,),
).fetchall()
print("DB:", db)
print(f"New SQL filter matches (sample): {len(rows)}")
for r in rows:
    print(r[0], (r[1] or "")[:24], "first_pur=", r[4], "exp=", r[3])

cnt = cur.execute(
    "SELECT COUNT(*) FROM medicines WHERE COALESCE(is_hidden,0)=0"
).fetchone()[0]
pass_created = pass_exp = pass_both = 0
for ca, exp, fp in cur.execute(
    """
    SELECT created_at, expiry_date,
           (SELECT MIN(p.purchase_date) FROM purchase_items pi
            JOIN purchases p ON p.id = pi.purchase_id
            WHERE pi.medicine_id = medicines.id)
    FROM medicines WHERE COALESCE(is_hidden,0)=0
    """
):
    ok_c = medicine_existed_as_of(ca, as_of, first_purchase_raw=fp)
    ok_e = not (exp and is_expired_as_of(exp, as_of))
    if ok_c:
        pass_created += 1
    if ok_e:
        pass_exp += 1
    if ok_c and ok_e:
        pass_both += 1
print(f"Total visible: {cnt}")
print(f"pass added-date: {pass_created}")
print(f"pass expiry: {pass_exp}")
print(f"pass both (dropdown): {pass_both}")
conn.close()
