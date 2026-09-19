"""Repair Matoshree medicines.stock_qty from the purchase/sales ledger."""
from __future__ import annotations

import os
import shutil
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core.stock_rebuild import rebuild_stock_at_path

STORE_DIR = os.path.join(
    ROOT, 'config', 'stores', 'Store_Matoshree_Medical_Veterinary_Pet_Shop',
)
DB = os.path.join(STORE_DIR, 'veterinary.db')


def main() -> None:
    if not os.path.isfile(DB):
        raise SystemExit(f'DB not found: {DB}')

    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    backup = DB + f'.pre_stock_repair_{stamp}'
    shutil.copy2(DB, backup)
    print(f'Backup: {backup}')

    fixed = rebuild_stock_at_path(DB, hide_zero=True, bump_meta=True)
    print(f'Done. Updated {fixed} medicine stock row(s).')


if __name__ == '__main__':
    main()
