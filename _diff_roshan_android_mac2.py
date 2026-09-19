"""Row-level diff Mac2 Roshan vs Android Roshan pulled DB."""
import os
import sqlite3
import subprocess
from pathlib import Path

from core.store_manager import get_store_db_path

android_dir = Path(os.environ["TEMP"]) / "roshan_compare"
mac = get_store_db_path("Store_Roshan")
and_db = str(android_dir / "veterinary.db")

# refresh android pull
android_dir.mkdir(exist_ok=True)
for name in ["veterinary.db", "veterinary.db-wal", "veterinary.db-shm"]:
    data = subprocess.check_output(
        [
            "adb",
            "exec-out",
            "run-as",
            "com.selling.satpudacore",
            "cat",
            f"files/stores/Store_Roshan/{name}",
        ]
    )
    (android_dir / name).write_bytes(data)

am = sqlite3.connect(and_db)
mm = sqlite3.connect(mac)


def rows(con, sql):
    return list(con.execute(sql))


print("MAC", mac)
print("AND", and_db)

# sales by id
ms = {
    r[0]: r
    for r in mm.execute(
        "SELECT id, bill_no, substr(COALESCE(bill_date,''),1,10), customer_id, "
        "ROUND(total_amount,2), ROUND(amount_paid,2), ROUND(COALESCE(total_due,0),2), "
        "COALESCE(deleted,0) FROM sales WHERE COALESCE(is_autosave,0)=0"
    )
}
as_ = {
    r[0]: r
    for r in am.execute(
        "SELECT id, bill_no, substr(COALESCE(bill_date,''),1,10), customer_id, "
        "ROUND(total_amount,2), ROUND(amount_paid,2), ROUND(COALESCE(total_due,0),2), "
        "COALESCE(deleted,0) FROM sales WHERE COALESCE(is_autosave,0)=0"
    )
}
only_m = sorted(set(ms) - set(as_))
only_a = sorted(set(as_) - set(ms))
diff = []
for i in sorted(set(ms) & set(as_)):
    if ms[i] != as_[i]:
        diff.append((ms[i], as_[i]))
print(f"\nSALES only_mac2={only_m} only_android={only_a} value_diffs={len(diff)}")
for a, b in diff[:15]:
    print(" MAC", a)
    print(" AND", b)

# customers
mc = {
    r[0]: r
    for r in mm.execute(
        "SELECT id, name, ROUND(COALESCE(total_due,0),2), ROUND(COALESCE(total_credit,0),2), "
        "COALESCE(deleted,0) FROM customers"
    )
}
ac = {
    r[0]: r
    for r in am.execute(
        "SELECT id, name, ROUND(COALESCE(total_due,0),2), ROUND(COALESCE(total_credit,0),2), "
        "COALESCE(deleted,0) FROM customers"
    )
}
cdiff = [i for i in sorted(set(mc) & set(ac)) if mc[i] != ac[i]]
print(
    f"\nCUSTOMERS only_mac={sorted(set(mc)-set(ac))} only_and={sorted(set(ac)-set(mc))} "
    f"diffs={len(cdiff)}"
)
for i in cdiff[:20]:
    print(" MAC", mc[i])
    print(" AND", ac[i])

# medicines stock
mm_med = {
    r[0]: r
    for r in mm.execute(
        "SELECT id, name, COALESCE(stock_qty,0), ROUND(COALESCE(mrp,0),2), "
        "COALESCE(is_hidden,0), COALESCE(deleted,0) FROM medicines"
    )
}
am_med = {
    r[0]: r
    for r in am.execute(
        "SELECT id, name, COALESCE(stock_qty,0), ROUND(COALESCE(mrp,0),2), "
        "COALESCE(is_hidden,0), COALESCE(deleted,0) FROM medicines"
    )
}
mdiff = [i for i in sorted(set(mm_med) & set(am_med)) if mm_med[i] != am_med[i]]
print(
    f"\nMEDICINES only_mac={len(set(mm_med)-set(am_med))} only_and={len(set(am_med)-set(mm_med))} "
    f"diffs={len(mdiff)}"
)
for i in mdiff[:25]:
    print(" MAC", mm_med[i])
    print(" AND", am_med[i])

# purchases
mp = {
    r[0]: r
    for r in mm.execute(
        "SELECT id, purchase_no, substr(COALESCE(purchase_date,''),1,10), supplier_id, "
        "ROUND(COALESCE(final_amount,total_amount,0),2), "
        "ROUND(COALESCE(amount_paid_at_entry,amount_paid,0),2), COALESCE(deleted,0) "
        "FROM purchases WHERE COALESCE(is_autosave,0)=0"
    )
}
ap = {
    r[0]: r
    for r in am.execute(
        "SELECT id, purchase_no, substr(COALESCE(purchase_date,''),1,10), supplier_id, "
        "ROUND(COALESCE(final_amount,total_amount,0),2), "
        "ROUND(COALESCE(amount_paid_at_entry,amount_paid,0),2), COALESCE(deleted,0) "
        "FROM purchases WHERE COALESCE(is_autosave,0)=0"
    )
}
pdiff = [i for i in sorted(set(mp) & set(ap)) if mp[i] != ap[i]]
print(
    f"\nPURCHASES only_mac={sorted(set(mp)-set(ap))[:20]} only_and={sorted(set(ap)-set(mp))[:20]} "
    f"diffs={len(pdiff)}"
)
for i in pdiff[:15]:
    print(" MAC", mp[i])
    print(" AND", ap[i])

print("\n=== Recent customer by latest sale (MAC) ===")
for r in mm.execute(
    """
    SELECT c.id, c.name, c.total_due, s.id, s.bill_no, substr(s.bill_date,1,10)
    FROM sales s JOIN customers c ON c.id=s.customer_id
    WHERE COALESCE(s.is_autosave,0)=0
    ORDER BY s.id DESC LIMIT 5
    """
):
    print(r)
print("=== Recent customer by latest sale (AND) ===")
for r in am.execute(
    """
    SELECT c.id, c.name, c.total_due, s.id, s.bill_no, substr(s.bill_date,1,10)
    FROM sales s JOIN customers c ON c.id=s.customer_id
    WHERE COALESCE(s.is_autosave,0)=0
    ORDER BY s.id DESC LIMIT 5
    """
):
    print(r)

am.close()
mm.close()
