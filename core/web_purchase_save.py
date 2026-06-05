"""Save purchase bills posted from the web purchase entry UI."""
from datetime import datetime


def _normalize_supplier(b):
    sup = b.get('supplier') or {}
    if isinstance(sup, dict) and (sup.get('name') or '').strip():
        return sup
    return {
        'name': b.get('supplier_name', '') or '',
        'address': b.get('supplier_address', '') or '',
        'phone': b.get('supplier_phone', '') or '',
        'gstin': b.get('supplier_gstin', '') or '',
        'dl_numbers': b.get('supplier_dl', '') or '',
    }


def _tablets_per_stripe(it, med_type):
    from core.layout_config import parse_tablets_per_stripe
    is_tb = med_type.lower() in ('tablet', 'bolus')
    raw = it.get('tablets_per_stripe')
    try:
        tps = int(raw) if raw is not None and str(raw).strip() != '' else 0
    except (TypeError, ValueError):
        tps = 0
    if tps <= 0 and is_tb:
        tps = parse_tablets_per_stripe(it.get('quantity_value', '1'))
    return max(tps, 1) if is_tb else 1


def _item_is_valid(it):
    name = (it.get('medicine_name') or it.get('name') or '').strip()
    if not name:
        return False
    try:
        qty = float(it.get('qty', 0) or 0)
    except (TypeError, ValueError):
        qty = 0
    return qty > 0


def json_bill_to_internal(b):
    """Convert one JSON bill dict to internal format (matches ImportPurchasesPage)."""
    items = []
    for it in b.get('items', []):
        if not _item_is_valid(it):
            continue

        med_type = (it.get('type') or '').strip()
        is_tablet_bolus = med_type.lower() in ('tablet', 'bolus')
        qty = float(it.get('qty', 0) or 0)
        free_qty = float(it.get('free_qty', 0) or 0)
        tps = _tablets_per_stripe(it, med_type)
        rate = float(it.get('rate', 0) or 0)
        gst = float(it.get('gst_percent', it.get('gst_pct', 0)) or 0)
        item_disc = float(it.get('item_discount', it.get('discount_pct', 0)) or 0)

        schedule = (it.get('schedule') or '').strip()
        if schedule == 'Non-Scheduled':
            schedule = ''

        expiry_raw = (it.get('expiry_date') or it.get('expiry') or '').strip()
        if '/' in expiry_raw:
            parts = expiry_raw.split('/')
            mm = parts[0].zfill(2)
            yy = parts[1][-2:]
            expiry = f"{mm}/{yy}"
        else:
            expiry = expiry_raw

        items.append({
            'medicine_id': None,
            'name': (it.get('medicine_name') or it.get('name') or '').strip(),
            'type': med_type,
            'batch': (it.get('batch_no') or it.get('batch') or '').strip(),
            'expiry': expiry,
            'qty': qty,
            'free_qty': free_qty,
            'rate': rate,
            'discount_pct': item_disc,
            'item_discount': item_disc,
            'gst_pct': gst,
            'gst_value': gst,
            'mrp': float(it.get('mrp', 0) or 0),
            'manufacturer': (it.get('manufacturer') or '').strip(),
            'schedule': schedule,
            'content_drug': (it.get('content_drug') or '').strip(),
            'hsn_code': (it.get('hsn_code') or '').strip(),
            'tablets_per_stripe': tps,
            'total_tablets': qty * tps if is_tablet_bolus else 0,
            'free_tablets': free_qty * tps if is_tablet_bolus else 0,
            'quantity_value': str(it.get('quantity_value', '1') or '1'),
            'auto_unit': '',
        })

    overall_rs = float(b.get('overall_discount', 0) or 0)
    overall_pct = float(b.get('overall_discount_pct', 0) or 0)

    return {
        'supplier': _normalize_supplier(b),
        'purchase_date': (b.get('purchase_date') or datetime.now().strftime('%Y-%m-%d')).strip(),
        'bill_number': (b.get('bill_number') or '').strip(),
        'gst_calc_method': (b.get('gst_calc_method') or 'discount_before_gst').strip(),
        'overall_discount': overall_rs,
        'overall_discount_pct': overall_pct,
        'amount_paid': float(b.get('amount_paid', 0) or 0),
        'items': items,
    }


def save_internal_bill(conn, bill):
    from core.purchase_calculator import PurchaseCalculator
    from core.purchase_service import (
        get_or_create_supplier, get_or_create_medicine,
        save_purchase as svc_save_purchase, get_supplier_due,
    )

    sup = bill['supplier']
    items = list(bill['items'])
    if not items:
        raise ValueError('no valid line items (need medicine name and qty > 0)')

    for item in items:
        if not item.get('medicine_id'):
            item['medicine_id'] = get_or_create_medicine(
                conn,
                item['name'], item['type'],
                item['batch'], item['expiry'],
                item['gst_pct'], item.get('mrp', 0), item['rate'],
                item.get('manufacturer', ''), item.get('hsn_code', ''),
                item.get('schedule', ''), item.get('content_drug', ''),
            )

    supplier_id = get_or_create_supplier(
        conn,
        sup.get('name', '').strip(),
        sup.get('address', ''),
        sup.get('phone', ''),
        sup.get('gstin', ''),
        sup.get('dl_numbers', ''),
    )

    prev_due, prev_credit = get_supplier_due(conn, sup.get('name', '').strip())

    overall_discount = float(bill.get('overall_discount', 0) or 0)
    amount_paid = float(bill.get('amount_paid', 0) or 0)

    calc_kw = dict(
        items=items,
        overall_discount=overall_discount,
        previous_due=prev_due,
        previous_credit=prev_credit,
        amount_paid=amount_paid,
    )

    if bill.get('rounding') is not None:
        rounding = float(bill.get('rounding', 0) or 0)
    else:
        from core.calc_engine import auto_round
        pre = PurchaseCalculator(**calc_kw, rounding=0).calculate()
        rounding = auto_round(pre.get('pre_round_total', 0))

    result = PurchaseCalculator(**calc_kw, rounding=rounding).calculate()

    result['gst_calc_method'] = bill.get('gst_calc_method', 'discount_before_gst')

    purchase_no = svc_save_purchase(
        conn, supplier_id,
        bill.get('purchase_date', datetime.now().strftime('%Y-%m-%d')),
        bill.get('bill_number', ''),
        result, items,
    )
    return purchase_no


def save_purchases_from_web_json(conn, data):
    """
    Save bills from web JSON { bills: [...] }.
    Returns { saved, errors, purchase_nos }.
    """
    bills = data.get('bills') if isinstance(data, dict) else data
    if not isinstance(bills, list) or not bills:
        raise ValueError('Expected {"bills": [...]} with at least one bill.')

    saved = 0
    errors = []
    purchase_nos = []
    saved_medicine_names = []

    for i, raw in enumerate(bills):
        label = raw.get('bill_number') or f'Bill {i + 1}'
        try:
            bill = json_bill_to_internal(raw)
            if not bill['items']:
                raise ValueError('no valid items')
            if not (bill['supplier'].get('name') or '').strip():
                raise ValueError('supplier name required')
            pno = save_internal_bill(conn, bill)
            conn.commit()
            saved += 1
            purchase_nos.append(str(pno))
            for item in bill.get('items', []):
                n = (item.get('name') or '').strip()
                if n:
                    saved_medicine_names.append(n)
        except Exception as e:
            conn.rollback()
            errors.append(f"{label}: {e}")

    return {
        'saved': saved,
        'errors': errors,
        'purchase_nos': purchase_nos,
        'saved_medicine_names': sorted(set(saved_medicine_names)),
    }
