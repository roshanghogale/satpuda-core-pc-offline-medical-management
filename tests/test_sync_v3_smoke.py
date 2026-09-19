"""Quick sync_v3 schema smoke test."""
import os
import sqlite3
import tempfile

from core.sync_v3.schema import ensure_sync_v3_schema
from core.online_guard import commit_local_then_push
from core.sync_v3.flags import is_sync_v3_enabled

p = tempfile.mktemp(suffix=".db")
c = sqlite3.connect(p)
c.execute(
    "CREATE TABLE purchases (id INTEGER PRIMARY KEY, purchase_no TEXT, "
    "supplier_id INT, deleted INT DEFAULT 0, is_autosave INT DEFAULT 0)"
)
c.execute("CREATE TABLE sales (id INTEGER PRIMARY KEY, bill_no TEXT)")
c.execute("CREATE TABLE customers (id INTEGER PRIMARY KEY, name TEXT)")
c.execute("CREATE TABLE suppliers (id INTEGER PRIMARY KEY, name TEXT)")
c.execute("CREATE TABLE medicines (id INTEGER PRIMARY KEY, name TEXT)")
c.execute("CREATE TABLE doctors (id INTEGER PRIMARY KEY, name TEXT)")
c.execute("CREATE TABLE customer_payments (id INTEGER PRIMARY KEY)")
c.execute("CREATE TABLE supplier_payments (id INTEGER PRIMARY KEY)")
c.execute("CREATE TABLE sales_returns (id INTEGER PRIMARY KEY)")
c.execute("CREATE TABLE purchase_returns (id INTEGER PRIMARY KEY)")
c.execute("CREATE TABLE stock_disposals (id INTEGER PRIMARY KEY)")
c.execute("CREATE TABLE pending_orders (id INTEGER PRIMARY KEY)")
c.execute("CREATE TABLE general_products (id INTEGER PRIMARY KEY)")
c.commit()
ensure_sync_v3_schema(c)
row = c.execute(
    "SELECT name FROM sqlite_master WHERE name='sync_outbox_v2'"
).fetchone()
cols = [r[1] for r in c.execute("PRAGMA table_info(purchases)")]
assert row and row[0] == "sync_outbox_v2"
assert "sync_state" in cols
assert is_sync_v3_enabled()
assert callable(commit_local_then_push)
c.close()
os.remove(p)
print("smoke ok")
