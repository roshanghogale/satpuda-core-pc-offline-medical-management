from pathlib import Path

path = Path(__file__).resolve().parents[1] / "core" / "server_entity_sync.py"
text = path.read_text(encoding="utf-8")
start = text.find("def _soft_delete_local(")
end = text.find("\ndef _schedule_entity_repush(", start)
if start < 0 or end < 0:
    raise SystemExit(f"markers not found start={start} end={end}")

new = '''def _soft_delete_local(conn, collection: str, doc_id: str, data: dict) -> None:
    """Apply remote delete. Sales/purchases are permanently purged; others soft-delete."""
    from core.conflict_resolver import COLLECTION_TO_TABLE
    table = COLLECTION_TO_TABLE.get(collection)
    if not table:
        return
    try:
        rid = int(doc_id)
    except (TypeError, ValueError):
        return
    with _db_lock:
        cur = conn.cursor()
        try:
            cols = _table_columns(conn, table)
            already = False
            exists_row = None
            if 'deleted' in cols:
                cur.execute(f'SELECT COALESCE(deleted,0) FROM {table} WHERE id=?', (rid,))
                exists_row = cur.fetchone()
                if exists_row is None and collection in ('sales', 'purchases'):
                    return
                already = bool(exists_row and int(exists_row[0] or 0))

            if collection == 'sales':
                cur.execute('SELECT 1 FROM sales WHERE id=?', (rid,))
                if not cur.fetchone():
                    return
                if not already:
                    local_auto = 0
                    if 'is_autosave' in cols:
                        cur.execute(
                            'SELECT COALESCE(is_autosave,0) FROM sales WHERE id=?',
                            (rid,),
                        )
                        row_a = cur.fetchone()
                        local_auto = int(row_a[0] or 0) if row_a else 0
                    if _sale_stock_affects(data, local_is_autosave=local_auto):
                        med_ids = _sale_item_medicine_ids(cur, rid)
                        _restore_sale_items_stock(cur, rid)
                        _unhide_medicines_in_stock(cur, med_ids)
                cur.execute('DELETE FROM sales_items WHERE sale_id=?', (rid,))
                cur.execute('DELETE FROM sales WHERE id=?', (rid,))
                conn.commit()
                return

            if collection == 'purchases':
                cur.execute('SELECT 1 FROM purchases WHERE id=?', (rid,))
                if not cur.fetchone():
                    return
                if not already:
                    try:
                        from core.purchase_service import _reverse_stock_for_purchase
                        cur.execute(
                            'SELECT DISTINCT medicine_id FROM purchase_items WHERE purchase_id=?',
                            (rid,),
                        )
                        med_ids = [int(r[0]) for r in cur.fetchall() if r and r[0]]
                        _reverse_stock_for_purchase(cur, rid)
                        _hide_medicines_at_zero(cur, med_ids)
                    except Exception as exc:
                        log.warning('hard_delete purchase stock reverse %s: %s', rid, exc)
                cur.execute('DELETE FROM purchase_items WHERE purchase_id=?', (rid,))
                cur.execute('DELETE FROM purchases WHERE id=?', (rid,))
                conn.commit()
                return

            if not already and collection == 'sales_returns':
                cur.execute(
                    'SELECT DISTINCT medicine_id FROM sales_return_items WHERE return_id=?',
                    (rid,),
                )
                med_ids = [int(r[0]) for r in cur.fetchall() if r and r[0]]
                _restore_sales_return_stock(cur, rid)
                _hide_medicines_at_zero(cur, med_ids)
            elif not already and collection == 'purchase_returns':
                cur.execute(
                    'SELECT DISTINCT medicine_id FROM purchase_return_items WHERE return_id=?',
                    (rid,),
                )
                med_ids = [int(r[0]) for r in cur.fetchall() if r and r[0]]
                _restore_purchase_return_stock(cur, rid)
                _unhide_medicines_in_stock(cur, med_ids)
            if 'deleted' in cols:
                cur.execute(f'UPDATE {table} SET deleted=1 WHERE id=?', (rid,))
            _apply_sync_meta_columns(cur, table, rid, {**data, 'deleted': True})
            conn.commit()
        except Exception as exc:
            log.warning('soft_delete_local %s/%s: %s', collection, doc_id, exc)
            conn.rollback()


'''
path.write_text(text[:start] + new + text[end + 1 :], encoding="utf-8")
print("patched", path)
