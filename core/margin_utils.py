"""Sales margin helpers — view-only, not stored in DB."""
from __future__ import annotations

from core.layout_config import is_strip_count_type, parse_tablets_per_stripe, load_layout


def _layout_flag(key: str, default: bool = False) -> bool:
    return bool(load_layout().get(key, default))


def show_margin_column() -> bool:
    try:
        from core.billing_layout_prefs import load_billing_layout_prefs

        return bool(load_billing_layout_prefs().get("billing_show_margin_column", True))
    except Exception:
        return _layout_flag("billing_show_margin_column", True)


def show_total_margin() -> bool:
    try:
        from core.billing_layout_prefs import load_billing_layout_prefs

        return bool(load_billing_layout_prefs().get("billing_show_total_margin", True))
    except Exception:
        return _layout_flag("billing_show_total_margin", True)


def margin_loss_warning_enabled() -> bool:
    try:
        from core.billing_layout_prefs import load_billing_layout_prefs

        return bool(load_billing_layout_prefs().get("billing_margin_loss_warning", True))
    except Exception:
        return _layout_flag("billing_margin_loss_warning", True)


def margin_display_is_percent() -> bool:
    try:
        from core.billing_layout_prefs import margin_display_is_percent as _pct

        return bool(_pct())
    except Exception:
        return False


def margin_column_heading() -> str:
    return "Margin %" if margin_display_is_percent() else "Margin ₹"


def _safe_float(val, default: float = 0.0) -> float:
    if val is None or val == "":
        return default
    if isinstance(val, bool):
        return float(int(val))
    if isinstance(val, (int, float)):
        try:
            fv = float(val)
            return default if fv != fv else fv
        except (TypeError, ValueError):
            return default
    try:
        s = str(val).strip().replace(",", "").replace("₹", "").strip()
        if not s:
            return default
        fv = float(s)
        return default if fv != fv else fv
    except (TypeError, ValueError):
        return default


def display_mrp_per_unit(med: dict) -> float:
    """MRP shown per sell unit (tablet for strip types)."""
    from core.layout_config import resolve_tablets_per_stripe

    list_mrp = _safe_float(med.get("list_mrp"), _safe_float(med.get("mrp")))
    med_type = med.get("type") or ""
    unit = med.get("unit") or "1"
    name = med.get("name") or ""
    if is_strip_count_type(med_type, unit):
        tps = resolve_tablets_per_stripe(unit, name=name, med_type=med_type)
        if tps > 0:
            return round(list_mrp / tps, 2)
    return round(list_mrp, 2)


def line_mrp_value(med: dict) -> float:
    """List-MRP sales value for the line (denominator for margin %)."""
    qty = _safe_float(med.get("qty"))
    if qty <= 0:
        return 0.0
    return round(display_mrp_per_unit(med) * qty, 2)


def line_gross_margin(med: dict) -> float:
    """
    Gross margin before any item discount: (MRP - purchase rate) × qty.
    Uses list MRP and inventory purchase rate (not selling rate used for amount).
    """
    qty = _safe_float(med.get("qty"))
    list_mrp = _safe_float(med.get("list_mrp"), _safe_float(med.get("mrp")))
    purchase_rate = _safe_float(med.get("purchase_rate"))
    med_type = med.get("type") or ""
    unit = med.get("unit") or "1"

    if qty <= 0:
        return 0.0

    if is_strip_count_type(med_type, unit):
        from core.layout_config import resolve_tablets_per_stripe
        tps = resolve_tablets_per_stripe(
            unit, name=med.get("name") or "", med_type=med_type,
        )
        if tps <= 0:
            tps = 1
        per_unit = (list_mrp - purchase_rate) / tps
        return round(max(0.0, per_unit * qty), 2)

    return round(max(0.0, (list_mrp - purchase_rate) * qty), 2)


def line_net_margin(med: dict) -> float:
    """Gross margin minus item discount (for display)."""
    disc = _safe_float(med.get("medicine_discount"))
    return round(max(0.0, line_gross_margin(med) - disc), 2)


def line_margin_percent(med: dict) -> float:
    """Net margin as % of list-MRP line value."""
    base = line_mrp_value(med)
    if base <= 0:
        return 0.0
    return round(line_net_margin(med) / base * 100.0, 2)


def format_line_margin_display(med: dict) -> str:
    if margin_display_is_percent():
        return f"{line_margin_percent(med):.2f}"
    return f"{line_net_margin(med):.2f}"


def total_gross_margin(medicines: list) -> float:
    return round(sum(line_gross_margin(m) for m in medicines), 2)


def total_net_margin(medicines: list, overall_discount: float = 0.0) -> float:
    gross = total_gross_margin(medicines)
    item_disc = sum(_safe_float(m.get("medicine_discount")) for m in medicines)
    overall = _safe_float(overall_discount)
    return round(max(0.0, gross - item_disc - overall), 2)


def total_margin_percent(medicines: list, overall_discount: float = 0.0) -> float:
    base = round(sum(line_mrp_value(m) for m in medicines), 2)
    if base <= 0:
        return 0.0
    return round(total_net_margin(medicines, overall_discount) / base * 100.0, 2)


def format_total_margin_display(medicines: list, overall_discount: float = 0.0) -> str:
    if margin_display_is_percent():
        return f"{total_margin_percent(medicines, overall_discount):.2f}"
    return f"{total_net_margin(medicines, overall_discount):.2f}"


def margin_unit_divisor(med: dict) -> int:
    """The pack divisor line_gross_margin / display_mrp_per_unit use: tablets
    per strip for strip-counted types, 1 otherwise. Sent to the Tauri sales
    screen as margin_div so it recomputes Classic's margin exactly when a
    discount or quantity changes (it cannot look up sibling packs itself)."""
    med_type = med.get("type") or ""
    unit = med.get("unit") or "1"
    if not is_strip_count_type(med_type, unit):
        return 1
    from core.layout_config import resolve_tablets_per_stripe

    tps = resolve_tablets_per_stripe(unit, name=med.get("name") or "", med_type=med_type)
    return int(tps) if tps and tps > 0 else 1


def enrich_medicine_margin_fields(
    med: dict, list_mrp: float, purchase_rate: float, med_type: str, unit: str
) -> None:
    med["list_mrp"] = round(_safe_float(list_mrp), 4)
    med["purchase_rate"] = round(_safe_float(purchase_rate), 4)
    med["type"] = med_type or med.get("type") or ""
    med["unit"] = unit or med.get("unit") or "1"
    med["margin_div"] = margin_unit_divisor(med)
    med["gross_margin"] = line_gross_margin(med)
    med["net_margin"] = line_net_margin(med)
    med["margin"] = med["net_margin"]
    med["margin_pct"] = line_margin_percent(med)


def check_item_discount_loss(med: dict) -> tuple[bool, str]:
    """True if item discount exceeds gross (MRP - rate) margin."""
    gross = line_gross_margin(med)
    disc = _safe_float(med.get("medicine_discount"))
    if disc <= gross + 0.001:
        return False, ""
    name = med.get("name") or "Medicine"
    return True, (
        f"{name}: discount ₹{disc:.2f} is more than margin ₹{gross:.2f} "
        f"(MRP − purchase rate).\nYou are selling below cost rate."
    )


def check_overall_discount_loss(medicines: list, overall_discount: float) -> tuple[bool, str]:
    gross = total_gross_margin(medicines)
    item_disc = sum(_safe_float(m.get("medicine_discount")) for m in medicines)
    remaining = round(gross - item_disc, 2)
    od = _safe_float(overall_discount)
    if od <= remaining + 0.001:
        return False, ""
    return True, (
        f"Overall discount ₹{od:.2f} is more than remaining margin ₹{remaining:.2f} "
        f"(total MRP−rate margin ₹{gross:.2f} minus item discounts).\n"
        f"You are selling below cost rate."
    )


def confirm_discount_loss(parent, messages: list) -> bool:
    """Ask user to proceed with loss-making discount. Returns True to continue."""
    if not messages:
        return True
    from core.themed_messagebox import askyesno

    body = (
        "Discount is larger than margin — you are selling below purchase rate (loss).\n\n"
        + "\n\n".join(messages)
        + "\n\nApply this discount anyway?"
    )
    return bool(askyesno("Margin Warning", body, parent=parent))


def validate_bill_discounts(parent, medicines: list, overall_discount: float = 0.0) -> bool:
    """Return True if save/apply may continue."""
    if not margin_loss_warning_enabled():
        return True
    msgs = []
    for med in medicines:
        bad, msg = check_item_discount_loss(med)
        if bad:
            msgs.append(msg)
    bad, msg = check_overall_discount_loss(medicines, overall_discount)
    if bad:
        msgs.append(msg)
    if not msgs:
        return True
    return confirm_discount_loss(parent, msgs)
