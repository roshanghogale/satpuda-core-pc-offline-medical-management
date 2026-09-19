"""Fix DICRYSTICIN INJ-2.5 duplicate: move sales from id=126 onto purchase row id=56."""
from __future__ import annotations

import os
import shutil
import sqlite3
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(
    ROOT, 'config', 'stores',
    'Store_Matoshree_Medical_Veterinary_Pet_Shop', 'veterinary.db',
)

KEEP_ID = 56   # has purchases
DUP_ID = 126   # has sales only (SCB40/45/51)


def main() -> None:
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    backup = DB + f'.pre_dicry_fix_{stamp}'
    shutil.copy2(DB, backup)
    print(f'Backup: {backup}')

    conn = sqlite3.connect(DB)
    cur = conn.cursor()

    keep = cur.execute(
        'SELECT id,name,batch_no,stock_qty FROM medicines WHERE id=?', (KEEP_ID,)
    ).fetchone()
    dup = cur.execute(
        'SELECT id,name,batch_no,stock_qty FROM medicines WHERE id=?', (DUP_ID,)
    ).fetchone()
    print('keep', keep)
    print('dup ', dup)

    sales = cur.execute(
        '''
        SELECT si.id, s.bill_no, si.qty
        FROM sales_items si JOIN sales s ON s.id=si.sale_id
        WHERE si.medicine_id=?
        ''',
        (DUP_ID,),
    ).fetchall()
    print('sales to reassign', sales)
    sold = sum(float(r[2] or 0) for r in sales)

    cur.execute(
        'UPDATE sales_items SET medicine_id=? WHERE medicine_id=?',
        (KEEP_ID, DUP_ID),
    )
    print(f'Reassigned {cur.rowcount} sales_item row(s) {DUP_ID} -> {KEEP_ID}')

    # Also move any returns / other refs if present
    for table, col in (
        ('sales_return_items', 'medicine_id'),
        ('purchase_return_items', 'medicine_id'),
        ('purchase_items', 'medicine_id'),
    ):
        try:
            cur.execute(
                f'UPDATE {table} SET {col}=? WHERE {col}=?',
                (KEEP_ID, DUP_ID),
            )
            if cur.rowcount:
                print(f'Moved {cur.rowcount} row(s) in {table}')
        except sqlite3.OperationalError:
            pass

    purch = float(cur.execute(
        '''
        SELECT COALESCE(SUM(COALESCE(pi.qty,0)+COALESCE(pi.free_qty,0)),0)
        FROM purchase_items pi JOIN purchases p ON p.id=pi.purchase_id
        WHERE pi.medicine_id=? AND COALESCE(pi.deleted,0)=0 AND COALESCE(p.deleted,0)=0
        ''',
        (KEEP_ID,),
    ).fetchone()[0])
    sold_now = float(cur.execute(
        '''
        SELECT COALESCE(SUM(si.qty),0)
        FROM sales_items si JOIN sales s ON s.id=si.sale_id
        WHERE si.medicine_id=? AND COALESCE(si.deleted,0)=0
          AND COALESCE(s.deleted,0)=0 AND COALESCE(s.is_autosave,0)=0
        ''',
        (KEEP_ID,),
    ).fetchone()[0])
    new_stock = max(0, int(round(purch - sold_now)))
    cur.execute('UPDATE medicines SET stock_qty=? WHERE id=?', (new_stock, KEEP_ID))
    cur.execute(
        'UPDATE medicines SET stock_qty=0, is_hidden=1 WHERE id=?',
        (DUP_ID,),
    )
    print(f'id={KEEP_ID} stock -> {new_stock} (purch={purch:g} sold={sold_now:g})')
    print(f'id={DUP_ID} hidden, stock=0')

    conn.commit()
    conn.close()
    print('Done.')


if __name__ == '__main__':
    main()
