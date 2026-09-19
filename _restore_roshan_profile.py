"""Restore Roshan pharmacy_profile on Mac2 from Android device copy."""
import os
import sqlite3
import subprocess
from pathlib import Path

from core.store_manager import get_store_db_path

base = Path(os.environ["TEMP"]) / "roshan_now"
base.mkdir(exist_ok=True)
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
    (base / name).write_bytes(data)

am = sqlite3.connect(str(base / "veterinary.db"))
cols = [c[1] for c in am.execute("PRAGMA table_info(pharmacy_profile)")]
print("Android cols", cols)
row = am.execute("SELECT * FROM pharmacy_profile LIMIT 1").fetchone()
am.close()
if not row:
    raise SystemExit("Android has no pharmacy_profile to copy")
amap = dict(zip(cols, row))
print("Android profile:", amap)

mac_path = get_store_db_path("Store_Roshan")
mm = sqlite3.connect(mac_path)
cur = mm.cursor()
exists = cur.execute("SELECT id FROM pharmacy_profile LIMIT 1").fetchone()
vals = (
    amap.get("name") or "",
    amap.get("address") or "",
    amap.get("phone") or "",
    amap.get("email") or "",
    amap.get("gstin") or "",
    amap.get("dl_number") or "",
    int(amap.get("gst_enabled") or 1),
    amap.get("fssai_number") or "",
    int(amap.get("show_fssai_on_bill") or 0),
)
if exists:
    cur.execute(
        "UPDATE pharmacy_profile SET name=?, address=?, phone=?, email=?, "
        "gstin=?, dl_number=?, gst_enabled=?, fssai_number=?, show_fssai_on_bill=? "
        "WHERE id=?",
        (*vals, exists[0]),
    )
    print("Updated Mac2 pharmacy_profile id", exists[0])
else:
    cur.execute(
        "INSERT INTO pharmacy_profile "
        "(name, address, phone, email, gstin, dl_number, gst_enabled, "
        "fssai_number, show_fssai_on_bill) VALUES (?,?,?,?,?,?,?,?,?)",
        vals,
    )
    print("Inserted Mac2 pharmacy_profile")
mm.commit()
print("Mac2 now:", cur.execute("SELECT * FROM pharmacy_profile").fetchone())
mm.close()
print("OK", mac_path)
