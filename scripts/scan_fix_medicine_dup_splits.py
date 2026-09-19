"""Scan Matoshree DB for DICRYSTICIN-style duplicate splits and fix them."""
from __future__ import annotations

import os
import re
import shutil
import sqlite3
from datetime import datetime
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in __import__('sys').path:
    __import__('sys').path.insert(0, ROOT)
DB = os.path.join(
    ROOT, 'config', 'stores',
    'Store_Matoshree_Medical_Veterinary_Pet_Shop', 'veterinary.db',
)


def norm_name(s: str) -> str:
    s = (s or '').upper().strip()
    s = re.sub(r'[^A-Z0-9]+', ' ', s)
    return re.sub(r'\s+', ' ', s).strip()


def norm_batch(s: str) -> str:
    return re.sub(r'\s+', '', (s or '').upper().strip())


def ledger(cur, mid: int):
    purch = float(cur.execute(
        '''
        SELECT COALESCE(SUM(COALESCE(pi.qty,0)+COALESCE(pi.free_qty,0)),0)
        FROM purchase_items pi JOIN purchases p ON p.id=pi.purchase_id
        WHERE pi.medicine_id=? AND COALESCE(pi.deleted,0)=0 AND COALESCE(p.deleted,0)=0
        ''', (mid,)
    ).fetchone()[0])
    sold = float(cur.execute(
        '''
        SELECT COALESCE(SUM(si.qty),0)
        FROM sales_items si JOIN sales s ON s.id=si.sale_id
        WHERE si.medicine_id=? AND COALESCE(si.deleted,0)=0
          AND COALESCE(s.deleted,0)=0 AND COALESCE(s.is_autosave,0)=0
        ''', (mid,)
    ).fetchone()[0])
    return purch, sold


def move_refs(cur, src: int, dst: int) -> dict:
    moved = {}
    for table, col in (
        ('sales_items', 'medicine_id'),
        ('sales_return_items', 'medicine_id'),
        ('purchase_items', 'medicine_id'),
        ('purchase_return_items', 'medicine_id'),
        ('stock_disposals', 'medicine_id'),
    ):
        try:
            cur.execute(f'UPDATE {table} SET {col}=? WHERE {col}=?', (dst, src))
            if cur.rowcount:
                moved[table] = cur.rowcount
        except sqlite3.OperationalError:
            pass
    return moved


def main() -> None:
    conn = sqlite3.connect(DB)
    cur = conn.cursor()
    meds = cur.execute(
        '''
        SELECT id, name, COALESCE(batch_no,''), COALESCE(stock_qty,0),
               COALESCE(is_hidden,0), COALESCE(deleted,0)
        FROM medicines
        '''
    ).fetchall()

    rows = []
    for mid, name, batch, stock, hid, deleted in meds:
        purch, sold = ledger(cur, mid)
        rows.append({
            'id': mid, 'name': name, 'batch': batch, 'stock': stock,
            'hid': hid, 'deleted': deleted, 'purch': purch, 'sold': sold,
            'nn': norm_name(name), 'nb': norm_batch(batch),
        })

    # Group ONLY by exact normalized name + batch.
    # NEVER group by name alone — different batch numbers stay separate.
    by_key = defaultdict(list)
    for r in rows:
        if r['deleted']:
            continue
        if not r['nn']:
            continue
        # Skip empty batch for auto-merge grouping (cannot safely match batch)
        if not r['nb']:
            continue
        by_key[(r['nn'], r['nb'])].append(r)

    issues = []
    for key, group in by_key.items():
        _nn, batch_key = key
        # Safety: refuse empty batch keys (already skipped above)
        if not batch_key:
            continue

        if len(group) < 2:
            r = group[0]
            if r['sold'] > 0 and r['purch'] <= 0:
                issues.append({
                    'kind': 'orphan_sales',
                    'key': key,
                    'group': group,
                    'sale_ids': [r['id']],
                    'keep_id': None,
                })
            continue

        # Extra guard: every row in group must share the same normalized batch
        batches = {r['nb'] for r in group}
        if len(batches) != 1 or '' in batches:
            print(f'SKIP mixed/empty batch group {key}: {batches}')
            continue

        with_purch = [r for r in group if r['purch'] > 0]
        with_sold_no_purch = [r for r in group if r['sold'] > 0 and r['purch'] <= 0]
        if with_sold_no_purch and with_purch:
            keep = sorted(with_purch, key=lambda x: x['id'])[0]
            # Final guard before recording fix: keep batch == each sale-row batch
            bad = [r for r in with_sold_no_purch if r['nb'] != keep['nb']]
            if bad:
                print(
                    f'SKIP — different batch would merge into id={keep["id"]} '
                    f'batch={keep["nb"]!r}: {[ (r["id"], r["nb"]) for r in bad ]}'
                )
                continue
            issues.append({
                'kind': 'split_duplicate',
                'key': key,
                'group': group,
                'sale_ids': [r['id'] for r in with_sold_no_purch],
                'keep_id': keep['id'],
            })
            continue

    # Also: sales-only rows that match ANOTHER medicine with SAME norm name but DIFFERENT batch
    # (weaker) — only if batch empty on one side? Skip for auto-fix; report separately.

    print('=== ISSUES FOUND ===')
    if not issues:
        print('None (no same-name+batch sales-only / purchase splits).')
    for i, iss in enumerate(issues, 1):
        print(f"\n#{i} {iss['kind']} name_key={iss['key'][0]!r} batch={iss['key'][1]!r}")
        for r in iss['group']:
            print(
                f"  id={r['id']} hid={r['hid']} stock={r['stock']} "
                f"purch={r['purch']:g} sold={r['sold']:g} name={r['name']!r} batch={r['batch']!r}"
            )
        print(f"  keep={iss['keep_id']} move_from={iss['sale_ids']}")

    # Orphan sales (no matching purchase sibling) — report only
    orphans = [i for i in issues if i['kind'] == 'orphan_sales']
    splits = [i for i in issues if i['kind'] == 'split_duplicate']

    # Extra report: name-only matches (different batch) where one has sales-only
    print('\n=== SALES-ONLY ROWS (any) — check for near-duplicates ===')
    sales_only = [r for r in rows if not r['deleted'] and r['sold'] > 0 and r['purch'] <= 0]
    for r in sales_only:
        # find other rows same norm name with purch
        sibs = [
            x for x in rows
            if not x['deleted'] and x['id'] != r['id'] and x['nn'] == r['nn'] and x['purch'] > 0
        ]
        flag = 'HAS_NAME_SIBLING_WITH_PURCH' if sibs else 'true_orphan'
        print(
            f"  id={r['id']} hid={r['hid']} sold={r['sold']:g} batch={r['batch']!r} "
            f"name={r['name']!r} [{flag}]"
        )
        for s in sibs:
            print(
                f"    sibling id={s['id']} purch={s['purch']:g} sold={s['sold']:g} "
                f"batch={s['batch']!r} stock={s['stock']}"
            )

    if not splits:
        print('\nNo auto-fixable same-name+batch splits. Done (scan only).')
        conn.close()
        return

    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    backup = DB + f'.pre_dup_split_fix_{stamp}'
    shutil.copy2(DB, backup)
    print(f'\nBackup: {backup}')
    print(f'Fixing {len(splits)} split(s)...')

    from core.stock_rebuild import expected_stock_qty

    fixed_names = []
    for iss in splits:
        keep = iss['keep_id']
        keep_row = next(r for r in iss['group'] if r['id'] == keep)
        keep_batch = keep_row['nb']
        if not keep_batch:
            print(f'  REFUSE keep id={keep}: empty batch')
            continue
        for src in iss['sale_ids']:
            if src == keep:
                continue
            src_row = next(r for r in iss['group'] if r['id'] == src)
            if src_row['nb'] != keep_batch:
                print(
                    f'  REFUSE merge id={src} batch={src_row["nb"]!r} '
                    f'into id={keep} batch={keep_batch!r} (different batch)'
                )
                continue
            moved = move_refs(cur, src, keep)
            print(f'  moved {src} -> {keep} (batch {keep_batch!r}): {moved}')
            cur.execute(
                'UPDATE medicines SET stock_qty=0, is_hidden=1 WHERE id=?',
                (src,),
            )
        # rebuild stock on keep
        m = cur.execute(
            "SELECT type, COALESCE(unit,'') FROM medicines WHERE id=?", (keep,)
        ).fetchone()
        exp = expected_stock_qty(cur, keep, m[0] or '', m[1] or '')
        exp_i = max(0, int(round(exp)))
        cur.execute('UPDATE medicines SET stock_qty=? WHERE id=?', (exp_i, keep))
        name = next(r['name'] for r in iss['group'] if r['id'] == keep)
        batch = next(r['batch'] for r in iss['group'] if r['id'] == keep)
        fixed_names.append((name, batch, keep, exp_i, iss['sale_ids']))
        print(f'  keep id={keep} stock -> {exp_i} ({name!r} / {batch!r})')

    conn.commit()
    conn.close()
    print('\n=== FIXED ===')
    for name, batch, keep, stock, srcs in fixed_names:
        print(f'  {name} [{batch}] -> stock {stock} (merged sales from ids {srcs} into {keep})')
    if orphans:
        print('\n=== ORPHANS NOT AUTO-FIXED (no purchase sibling same name+batch) ===')
        for iss in orphans:
            for r in iss['group']:
                print(f"  id={r['id']} {r['name']!r} batch={r['batch']!r} sold={r['sold']:g}")


if __name__ == '__main__':
    main()
