#!/usr/bin/env python3
"""Export master medicine names into the web purchase app (run before npm build)."""
import json
import os
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

OUTPUTS = [
    os.path.join(ROOT, 'purchase-entry-web', 'public', 'medicines.json'),
    os.path.join(ROOT, 'web_app', 'medicines.json'),
]


def _merge_inventory_names(names: list, seen: set) -> list:
    """Append distinct inventory medicine names (your stock)."""
    import sqlite3
    db_path = os.path.join(ROOT, 'veterinary.db')
    if not os.path.isfile(db_path):
        return names
    try:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute(
            """
            SELECT DISTINCT name FROM medicines
            WHERE TRIM(COALESCE(name, '')) != ''
            ORDER BY name COLLATE NOCASE
            """
        )
        for (n,) in cur.fetchall():
            key = (n or '').strip().lower()
            if key and key not in seen:
                seen.add(key)
                names.append(n.strip())
        conn.close()
    except Exception as e:
        print(f'Note: inventory names not merged ({e})')
    return names


def main():
    names = []
    seen = set()

    try:
        from core.master_medicine_service import get_all_master_names
        for n in get_all_master_names():
            key = (n or '').strip().lower()
            if key and key not in seen:
                seen.add(key)
                names.append(n.strip())
        print(f'Master DB: {len(names)} names')
    except Exception as e:
        print(f'Warning: master export failed ({e})')

    before = len(names)
    names = _merge_inventory_names(names, seen)
    added = len(names) - before
    if added:
        print(f'Inventory: +{added} names (total {len(names)})')

    names.sort(key=lambda s: s.lower())

    payload = {
        'names': names,
        'count': len(names),
        'generated_at': datetime.now().isoformat(timespec='seconds'),
    }

    for path in OUTPUTS:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(payload, f, ensure_ascii=False, separators=(',', ':'))
        mb = os.path.getsize(path) / (1024 * 1024)
        print(f'Wrote {path} ({mb:.1f} MB, {len(names)} names)')


if __name__ == '__main__':
    main()
