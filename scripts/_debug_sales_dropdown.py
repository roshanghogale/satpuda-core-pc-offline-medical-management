"""Debug sales medicine dropdown — run against active store DB."""
import sqlite3
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

db = ROOT / "config" / "stores" / "Store_Roshan" / "veterinary.db"
if len(sys.argv) > 1:
    db = Path(sys.argv[1])

conn = sqlite3.connect(db)
cur = conn.cursor()

def q(sql, params=()):
    cur.execute(sql, params)
    return cur.fetchall()

print("DB:", db)
print("total:", q("SELECT COUNT(*) FROM medicines")[0][0])
print("visible:", q("SELECT COUNT(*) FROM medicines WHERE COALESCE(is_hidden,0)=0")[0][0])
print("visible+stock:", q("SELECT COUNT(*) FROM medicines WHERE COALESCE(is_hidden,0)=0 AND COALESCE(stock_qty,0)>0")[0][0])
print("names A*+stock:", q("SELECT COUNT(DISTINCT name) FROM medicines WHERE COALESCE(is_hidden,0)=0 AND COALESCE(stock_qty,0)>0 AND LOWER(name) LIKE 'a%'")[0][0])

print("\nSample A* with stock:")
for r in q("SELECT id,name,expiry_date,stock_qty,is_hidden FROM medicines WHERE COALESCE(is_hidden,0)=0 AND COALESCE(stock_qty,0)>0 AND LOWER(name) LIKE 'a%' LIMIT 8"):
    print(" ", r)

from core.batch_visibility import is_expired_as_of, compute_name_stock_totals, compute_oos_anchor_ids
from widgets.two_step_medicine_combo import TwoStepMedicineCombo

as_of = date(2026, 7, 17)
rows = q("SELECT id,name,expiry_date,COALESCE(stock_qty,0) FROM medicines WHERE COALESCE(is_hidden,0)=0 AND LOWER(name) LIKE 'a%' LIMIT 200")
vis = [(r[1], '', '', r[2], r[3], '', '', '', '', '', '', r[0]) for r in rows]
name_totals = compute_name_stock_totals(vis)
anchors = compute_oos_anchor_ids(vis)
expired = sum(1 for r in rows if is_expired_as_of(r[2] or '', as_of))
print(f"\nExpiry as of {as_of}: {expired}/{len(rows)} batches expired among A* visible")

# Simulate fetch + filter
class FakeCombo:
    bill_date_getter = lambda self: as_of
    def _show_zero_stock(self): return False
    def _sales_as_of_date(self): return as_of
    def _batch_visible_for_sales(self, med_id, stock, expiry, name_totals, anchor_ids, name):
        from core.batch_visibility import should_hide_depleted_batch, is_expired_as_of
        row = (name, '', '', expiry, stock, '', '', '', '', '', '', med_id)
        if should_hide_depleted_batch(row, name_totals, anchor_ids):
            return False
        if is_expired_as_of(expiry, as_of):
            return False
        if float(stock or 0) <= 0:
            return False
        return True

fc = FakeCombo()
# can't easily run _fetch without tk - run SQL manually
cur.execute("""
    SELECT m.name, COALESCE(m.schedule, ''), m.mrp, m.type, COALESCE(m.unit, '1'),
           COALESCE(agg.total_stock, 0), COALESCE(agg.batch_count, 1)
    FROM medicines m
    INNER JOIN (
        SELECT name, MAX(id) AS max_id FROM medicines
        WHERE COALESCE(is_hidden, 0) = 0 AND COALESCE(stock_qty, 0) > 0
          AND LOWER(name) LIKE LOWER('a%')
        GROUP BY name
    ) latest ON m.id = latest.max_id
    INNER JOIN (
        SELECT name, SUM(COALESCE(stock_qty, 0)) AS total_stock, COUNT(*) AS batch_count
        FROM medicines
        WHERE COALESCE(is_hidden, 0) = 0 AND COALESCE(stock_qty, 0) > 0
          AND LOWER(name) LIKE LOWER('a%')
        GROUP BY name
    ) agg ON agg.name = m.name
    LIMIT 50
""")
fetched = cur.fetchall()
print(f"\nSQL fetch A* names: {len(fetched)}")
sellable = set()
for r in q("SELECT id,name,expiry_date,COALESCE(stock_qty,0) FROM medicines WHERE COALESCE(is_hidden,0)=0 AND name IN (SELECT name FROM medicines WHERE LOWER(name) LIKE 'a%' GROUP BY name)"):
    name = r[1]
    if fc._batch_visible_for_sales(r[0], r[3], r[2], name_totals, anchors, name):
        if not is_expired_as_of(r[2] or '', as_of):
            if r[3] > 0:
                sellable.add(name)
print(f"After filter sellable names: {len(sellable)}")
if sellable:
    print(" examples:", list(sellable)[:5])
else:
    print(" ALL FILTERED OUT")

conn.close()
