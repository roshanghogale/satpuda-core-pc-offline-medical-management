"""Inspect active store purchases / outbox for image-import bug."""
import sqlite3
from core.store_manager import get_active_db_path

p = get_active_db_path()
print("db", p)
c = sqlite3.connect(p)
print("purchase cols has sync_state", any(r[1] == "sync_state" for r in c.execute("PRAGMA table_info(purchases)")))
print(
    "outbox_v2",
    c.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='sync_outbox_v2'"
    ).fetchone(),
)
rows = c.execute(
    """
    SELECT id, purchase_no, purchase_date, supplier_id,
           COALESCE(is_autosave,0), COALESCE(deleted,0),
           COALESCE(final_amount, total_amount), bill_number
    FROM purchases
    ORDER BY id DESC LIMIT 20
    """
).fetchall()
print("recent purchases:")
for r in rows:
    print(r)
try:
    ob = c.execute(
        """
        SELECT id, collection, local_id, status, needs_fy, retries, last_error
        FROM sync_outbox_v2 ORDER BY id DESC LIMIT 15
        """
    ).fetchall()
    print("outbox_v2:", ob)
except Exception as e:
    print("outbox_v2 err", e)
try:
    ob2 = c.execute(
        """
        SELECT id, collection, local_id, status, last_error
        FROM sync_outbox ORDER BY id DESC LIMIT 10
        """
    ).fetchall()
    print("legacy outbox:", ob2)
except Exception as e:
    print("legacy outbox err", e)
c.close()
