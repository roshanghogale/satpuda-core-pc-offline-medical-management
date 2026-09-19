"""Sell medicine not yet in stock — create on save and allow negative stock."""
from __future__ import annotations

from core.layout_config import is_strip_count_type, load_layout, parse_tablets_per_stripe
from core.margin_utils import enrich_medicine_margin_fields
from core.master_medicine_service import lookup_master_details


def default_pack_for_type(med_type: str) -> str:
    """Tablets per strip to seed for this type, from the shop's own settings.

    Quick Sale pre-filled a hardcoded 10 for everything, so a shop selling
    15-tablet strips retyped it on every no-stock line.
    """
    cfg = load_layout()
    unit = cfg.get(f'unit_{med_type}', '')
    if not is_strip_count_type(med_type, unit):
        return '1'
    if (med_type or '').lower() == 'bolus':
        return '1'
    default_qty = cfg.get(f'typeqty_{med_type}', 0)
    try:
        return str(int(default_qty)) if default_qty else '10'
    except (TypeError, ValueError):
        return '10'


def medicine_unit_for_type(med_type: str, pack_size) -> str:
    if is_strip_count_type(med_type or ''):
        try:
            tps = int(float(pack_size or 1))
        except (ValueError, TypeError):
            tps = 1
        return str(max(1, tps))
    cfg = load_layout()
    return str(cfg.get(f'unit_{med_type}', '1') or '1')


def per_unit_rate(mrp: float, med_type: str, unit: str) -> float:
    mrp = float(mrp or 0)
    if is_strip_count_type(med_type or ''):
        tps = parse_tablets_per_stripe(unit)
        return round(mrp / tps, 4) if tps else mrp
    return round(mrp, 4)


def build_quick_sale_row(
    name: str,
    med_type: str,
    batch: str,
    qty: int,
    pack_size,
    mrp: float,
    *,
    rate=None,
    schedule: str = '',
) -> dict:
    """Build a sales line dict flagged for inventory creation on save."""
    name = (name or '').strip().upper()
    batch = (batch or '').strip().upper()
    med_type = (med_type or 'Tablet').strip()
    unit = medicine_unit_for_type(med_type, pack_size)
    mrp = float(mrp or 0)
    master = lookup_master_details(name) if name else {}
    if mrp <= 0 and master:
        mrp = float(master.get('mrp') or 0)

    if rate is not None and float(rate or 0) > 0:
        sale_rate = round(float(rate), 4)
        if mrp <= 0 and is_strip_count_type(med_type or ''):
            tps = parse_tablets_per_stripe(unit)
            mrp = round(sale_rate * tps, 2) if tps else sale_rate
        elif mrp <= 0:
            mrp = sale_rate
    else:
        sale_rate = per_unit_rate(mrp, med_type, unit)

    qty = int(qty or 0)
    base = round(qty * sale_rate, 2)
    schedule = (schedule or '').strip().upper()
    return {
        'quick_add': True,
        'id': None,
        'name': name,
        'batch': batch,
        'expiry': '',
        'qty': qty,
        'rate': sale_rate,
        'amount': base,
        'original_amount': base,
        'medicine_discount': 0.0,
        'schedule': schedule,
        'type': med_type,
        'display_type': med_type or 'N/A',
        'gst_percent': 0.0,
        'location': '',
        'unit': unit,
        'pack_size': pack_size,
        'mrp': mrp,
    }


def create_quick_sale_medicine(conn, med: dict) -> int:
    from core.purchase_service import get_or_create_medicine
    from core.sync_prefs import is_online_mode

    med_type = med.get('type') or 'Tablet'
    unit = med.get('unit') or medicine_unit_for_type(med_type, med.get('pack_size'))
    mrp = float(med.get('mrp') or 0)
    sale_rate = float(med.get('rate') or 0)
    if sale_rate > 0:
        if is_strip_count_type(med_type or ''):
            tps = parse_tablets_per_stripe(unit)
            rate = round(sale_rate * tps, 2) if tps else sale_rate
        else:
            rate = sale_rate
        if mrp <= 0 and is_strip_count_type(med_type or ''):
            tps = parse_tablets_per_stripe(unit)
            mrp = round(sale_rate * tps, 2) if tps else sale_rate
        elif mrp <= 0:
            mrp = sale_rate
    else:
        rate = per_unit_rate(mrp, med_type, unit)
    master = lookup_master_details(med.get('name', ''))
    manufacturer = (master.get('manufacturer') or '') if master else ''
    content = (master.get('content_drug') or '') if master else ''
    schedule = (med.get('schedule') or '').strip().upper()

    med_id = get_or_create_medicine(
        conn,
        med.get('name', ''),
        med_type,
        med.get('batch', ''),
        '',
        float(med.get('gst_percent', 0) or 0),
        mrp,
        rate,
        manufacturer,
        '',
        schedule,
        content,
    )
    # Mark provisional so a later purchase replaces leftover stock instead of stacking.
    if is_online_mode():
        try:
            from core.server_crud import bump_meta, get_doc, upsert_medicine_online

            mp = get_doc("medicines", int(med_id)) or {
                "id": int(med_id),
                "local_id": int(med_id),
                "name": med.get("name") or "",
                "stock_qty": 0,
            }
            mp = bump_meta(dict(mp))
            mp["id"] = int(med_id)
            mp["local_id"] = int(med_id)
            mp["unit"] = unit
            # NOTE: the server's medicines table has no from_quick_sale column,
            # so this marker survives only in the local cache and is gone on the
            # next pull. Nothing Online may depend on it -- the purchase side
            # identifies an unsettled Add-No-Stock row by its NEGATIVE stock
            # instead (see _adopt_quick_sale_placeholder in purchase_service).
            mp["from_quick_sale"] = True
            if med_type:
                mp["type"] = med_type
            batch = (med.get("batch") or "").strip()
            if batch:
                mp["batch_no"] = batch
            upsert_medicine_online(mp)
        except Exception:
            pass
        return med_id

    if conn is not None:
        cur = conn.cursor()
        try:
            cur.execute("UPDATE medicines SET unit=? WHERE id=?", (unit, med_id))
            conn.commit()
        except Exception:
            pass
    return med_id


def resolve_quick_sale_medicines(conn, medicines: list) -> None:
    """Create inventory rows for quick-add lines before stock deduction."""
    for idx, med in enumerate(list(medicines)):
        if not med.get('quick_add') and med.get('id'):
            continue
        med_id = create_quick_sale_medicine(conn, med)
        row = dict(med)
        row['id'] = med_id
        row.pop('quick_add', None)
        medicines[idx] = row


def _strip_purchase_rate(med: dict) -> float:
    """Inventory purchase rate (per strip or per unit) from a quick-sale line."""
    sale_rate = float(med.get('rate') or 0)
    med_type = med.get('type') or ''
    unit = med.get('unit') or '1'
    if sale_rate <= 0:
        return 0.0
    if is_strip_count_type(med_type, unit):
        tps = parse_tablets_per_stripe(unit)
        return round(sale_rate * tps, 4) if tps else sale_rate
    return round(sale_rate, 4)


def prepare_quick_sale_row_for_display(med: dict) -> dict:
    row = dict(med)
    med_type = row.get('type', '')
    unit = row.get('unit', '1')
    list_mrp = float(row.get('mrp') or row.get('rate') or 0)
    purchase_rate = _strip_purchase_rate(row)
    enrich_medicine_margin_fields(
        row,
        list_mrp,
        purchase_rate,
        med_type,
        unit,
    )
    return row
