"""Stock quantity helpers — strips × pack + extra loose units for tablet/bolus/capsule."""
from __future__ import annotations

from core.layout_config import is_strip_count_type, parse_tablets_per_stripe


def strip_stock_total(strips: int, tablets_per_strip: int, extra: int = 0) -> int:
    """Total tablet/capsule/bolus units stored in medicines.stock_qty."""
    tps = max(1, int(tablets_per_strip or 1))
    return max(0, int(strips or 0)) * tps + max(0, int(extra or 0))


def decompose_strip_stock(total_tablets: int, tablets_per_strip: int) -> tuple[int, int]:
    """Split stored tablet count into (strips, extra_loose)."""
    tps = max(1, int(tablets_per_strip or 1))
    total = max(0, int(total_tablets or 0))
    return total // tps, total % tps


def android_import_stock(med_type: str, stock_qty: int, unit: str, extra: int = 0) -> int:
    """
    Convert Android export row to desktop stock_qty (tablets for strip types).
    Android stock_qty = strips; unit = tablets per strip; extra_medicine = loose units.
    """
    if is_strip_count_type(med_type or '', unit):
        try:
            tps = parse_tablets_per_stripe(unit)
        except (TypeError, ValueError):
            tps = 10
        return strip_stock_total(stock_qty, tps, extra)
    return max(0, int(stock_qty or 0))


def inventory_save_stock_qty(med_type: str, unit: str, strips_or_qty: int, extra: int = 0) -> int:
    """Compute DB stock_qty from inventory edit fields."""
    if is_strip_count_type(med_type or '', unit):
        return strip_stock_total(strips_or_qty, parse_tablets_per_stripe(unit), extra)
    return max(0, int(strips_or_qty or 0))


def per_unit_mrp(mrp, med_type: str, unit=None) -> float:
    """MRP for one sold/stocked unit (tablet/capsule/bolus) when type uses strip counting."""
    m = float(mrp or 0)
    if m <= 0:
        return 0.0
    if is_strip_count_type(med_type or '', unit):
        tps = max(1, parse_tablets_per_stripe(unit))
        return m / tps
    return m


def stock_value_at_mrp(stock_qty, mrp, med_type: str, unit=None) -> float:
    """Inventory value: per-unit MRP × stored stock_qty (tablets for strip types)."""
    qty = float(stock_qty or 0)
    if qty <= 0:
        return 0.0
    return round(qty * per_unit_mrp(mrp, med_type, unit), 2)


def sum_inventory_mrp_value(rows) -> float:
    """Sum stock value from DB rows: (stock_qty, mrp, type, unit)."""
    total = 0.0
    for row in rows:
        stock_qty, mrp, med_type, unit = row[0], row[1], row[2], row[3]
        total += stock_value_at_mrp(stock_qty, mrp, med_type, unit)
    return round(total, 2)


def effective_cost_per_unit(cost_price, purchase_rate, med_type: str, unit=None) -> float:
    """Per-unit purchase cost for profit/margin (handles legacy strip-rate snapshots)."""
    cp = float(cost_price or 0)
    pr = float(purchase_rate or 0)
    if is_strip_count_type(med_type or '', unit):
        tps = max(1, parse_tablets_per_stripe(unit))
        if cp > 0:
            if pr > 0 and abs(cp - pr) < 0.02:
                return cp / tps
            return cp
        return pr / tps if pr > 0 else 0.0
    return cp if cp > 0 else pr


def snapshot_sale_cost_price(purchase_rate, med_type: str, unit=None) -> float:
    """Per-unit cost stored on sales_items at sale time (Offline + Online)."""
    pr = float(purchase_rate or 0)
    if pr <= 0:
        return 0.0
    if is_strip_count_type(med_type or "", unit):
        tps = max(1, parse_tablets_per_stripe(unit))
        return round(pr / tps, 4) if tps else round(pr, 4)
    return round(pr, 4)


def sale_line_profit(amount, qty, cost_price, purchase_rate, med_type: str, unit=None) -> float:
    """Profit for one sales line using per-unit cost."""
    q = float(qty or 0)
    if q <= 0:
        return 0.0
    cpu = effective_cost_per_unit(cost_price, purchase_rate, med_type, unit)
    return round(float(amount or 0) - q * cpu, 2)


def current_stock_for_medicine(conn, medicine_name: str, pack_size: str = "") -> float:
    """Re-export — callers historically imported this from stock_utils."""
    from core.reorder_service import current_stock_for_medicine as _impl
    return _impl(conn, medicine_name, pack_size)

