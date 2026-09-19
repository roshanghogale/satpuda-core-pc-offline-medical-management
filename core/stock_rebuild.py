"""Rebuild medicines.stock_qty from purchase/sales/return/disposal history.

stock_qty is a cache. Drive backups (and older Server pulls) can leave it
stale relative to the ledger. Recompute:

  purch*tps - sold + sales_returns - purchase_returns*tps - disposals
"""
from __future__ import annotations

import logging
import sqlite3
from typing import Optional

log = logging.getLogger(__name__)


def _tps_for(mtype: str, unit: str) -> int:
    from core.layout_config import is_strip_count_type, parse_tablets_per_stripe

    if is_strip_count_type(mtype or ''):
        t = parse_tablets_per_stripe(unit or '1')
        return t if t > 0 else 1
    return 1


def expected_stock_qty(cur, medicine_id: int, med_type: str, unit: str) -> float:
    """Ledger-derived stock for one medicine (may be negative before clamp)."""
    tps = _tps_for(med_type, unit)
    purch = float(
        cur.execute(
            '''
            SELECT COALESCE(SUM(COALESCE(pi.qty,0)+COALESCE(pi.free_qty,0)),0)
            FROM purchase_items pi
            JOIN purchases p ON p.id = pi.purchase_id
            WHERE pi.medicine_id=?
              AND COALESCE(pi.deleted,0)=0
              AND COALESCE(p.deleted,0)=0
            ''',
            (medicine_id,),
        ).fetchone()[0]
    )
    sold = float(
        cur.execute(
            '''
            SELECT COALESCE(SUM(COALESCE(si.qty,0)),0)
            FROM sales_items si
            JOIN sales s ON s.id = si.sale_id
            WHERE si.medicine_id=?
              AND COALESCE(si.deleted,0)=0
              AND COALESCE(s.deleted,0)=0
              AND COALESCE(s.is_autosave,0)=0
            ''',
            (medicine_id,),
        ).fetchone()[0]
    )
    sret = 0.0
    try:
        sret = float(
            cur.execute(
                '''
                SELECT COALESCE(SUM(COALESCE(sri.qty,0)),0)
                FROM sales_return_items sri
                JOIN sales_returns sr ON sr.id = sri.return_id
                WHERE sri.medicine_id=?
                  AND COALESCE(sri.deleted,0)=0
                  AND COALESCE(sr.deleted,0)=0
                ''',
                (medicine_id,),
            ).fetchone()[0]
        )
    except sqlite3.OperationalError:
        pass
    pret = 0.0
    try:
        pret = float(
            cur.execute(
                '''
                SELECT COALESCE(SUM(COALESCE(pri.qty,0)),0)
                FROM purchase_return_items pri
                JOIN purchase_returns pr ON pr.id = pri.return_id
                WHERE pri.medicine_id=?
                  AND COALESCE(pri.deleted,0)=0
                  AND COALESCE(pr.deleted,0)=0
                ''',
                (medicine_id,),
            ).fetchone()[0]
        )
    except sqlite3.OperationalError:
        pass
    disp = 0.0
    try:
        disp = float(
            cur.execute(
                '''
                SELECT COALESCE(SUM(COALESCE(quantity,0)),0)
                FROM stock_disposals WHERE medicine_id=?
                ''',
                (medicine_id,),
            ).fetchone()[0]
        )
    except sqlite3.OperationalError:
        pass
    return purch * tps - sold + sret - pret * tps - disp


def rebuild_stock_from_ledger(
    conn: sqlite3.Connection,
    *,
    hide_zero: bool = True,
    bump_meta: bool = False,
) -> int:
    """
    Set each medicine's stock_qty from purchase/sales history.
    Returns number of rows updated.
    """
    cur = conn.cursor()
    meds = cur.execute(
        '''
        SELECT id, name, type, COALESCE(unit,''), COALESCE(stock_qty,0)
        FROM medicines WHERE COALESCE(deleted,0)=0
        '''
    ).fetchall()

    bump_row_meta = None
    if bump_meta:
        try:
            from core.sync_meta_bump import bump_row_meta as _bump
            bump_row_meta = _bump
        except Exception:
            bump_row_meta = None

    fixed = 0
    for mid, _name, mtype, unit, stock in meds:
        exp = expected_stock_qty(cur, mid, mtype or '', unit or '')
        # No floor at zero: a medicine sold with "Add No Stock" is legitimately
        # negative until the delivery arrives, and clamping erased that debt so
        # the later purchase added to 0 instead of to the shortage.
        exp_i = int(round(exp))
        cur_i = int(round(float(stock or 0)))
        if cur_i == exp_i:
            continue
        cur.execute('UPDATE medicines SET stock_qty=? WHERE id=?', (exp_i, mid))
        if bump_row_meta is not None:
            try:
                bump_row_meta(conn, 'medicines', mid, mark_pending=True)
            except Exception:
                pass
        fixed += 1

    if fixed:
        conn.commit()

    if hide_zero:
        try:
            from core.medicine_visibility import hide_zero_stock_medicines
            hide_zero_stock_medicines(conn)
        except Exception as exc:
            log.debug('hide_zero_stock after rebuild: %s', exc)

    return fixed


def rebuild_stock_at_path(db_path: str, **kwargs) -> int:
    """Open db_path, rebuild stock, close. Returns rows updated."""
    from core.db_utils import open_store_db

    conn = open_store_db(db_path, timeout=60.0)
    try:
        n = rebuild_stock_from_ledger(conn, **kwargs)
        try:
            conn.commit()
        except Exception:
            pass
        return n
    finally:
        conn.close()
