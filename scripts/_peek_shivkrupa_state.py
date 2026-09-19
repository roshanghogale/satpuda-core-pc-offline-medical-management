import sqlite3
from pathlib import Path

db = Path(
    r"D:\Satpuda Core Server Update\mac2\config\stores"
    r"\Store_Shivkrupa_Medical_General_Store\veterinary.db"
)
print("db", db.exists(), db)
conn = sqlite3.connect(str(db))
cur = conn.cursor()
queries = [
    "SELECT COUNT(*) FROM medicines",
    "SELECT COUNT(*) FROM medicines WHERE TRIM(COALESCE(schedule,''))!=''",
    "SELECT COALESCE(NULLIF(TRIM(schedule),''),'(blank)') AS s, COUNT(*) FROM medicines GROUP BY 1 ORDER BY 2 DESC LIMIT 20",
    "SELECT COUNT(*) FROM medicines WHERE COALESCE(stock_qty,0)>0",
    "SELECT COUNT(*) FROM sales WHERE COALESCE(is_autosave,0)=0 AND COALESCE(deleted,0)=0",
    "SELECT COUNT(*) FROM purchases WHERE COALESCE(is_autosave,0)=0 AND COALESCE(deleted,0)=0",
    "SELECT COUNT(*) FROM sales_returns",
    "SELECT COUNT(*) FROM purchase_returns",
    "SELECT COUNT(*) FROM sales_returns WHERE COALESCE(deleted,0)=0",
    "SELECT COUNT(*) FROM purchase_returns WHERE COALESCE(deleted,0)=0",
    "SELECT COUNT(*) FROM customers WHERE COALESCE(total_due,0)!=0",
    "SELECT COUNT(*) FROM suppliers WHERE COALESCE(total_due,0)!=0",
]
for q in queries:
    try:
        rows = cur.execute(q).fetchall()
        print(q[:80], "=>", rows[:20])
    except Exception as e:
        print("ERR", q[:60], e)
# sample medicines without schedule with stock
print("sample blank schedule with stock:")
for r in cur.execute(
    """
    SELECT id, name, batch_no, stock_qty, schedule
    FROM medicines
    WHERE COALESCE(stock_qty,0)>0 AND TRIM(COALESCE(schedule,''))=''
    ORDER BY stock_qty DESC LIMIT 10
    """
):
    print(r)
conn.close()
