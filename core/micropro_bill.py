"""Bill of Supply, Micropro style (owner, 28 Oct 2026): the third Bill template.

The shop's old program (Micropro) printed its Bill of Supply as a plain dot matrix slip,
and the owner asked for the same look:

                           BILL OF SUPPLY
    SHRIHARI MEDICAL & GENERAL STORES
    JUNA SONI HOSPITAL ... JALNA          Date : 22-10-2026
    Name : SUNIL PREMDAS WANARSE          Bill No.: CA/3892
    Doct : DR.SHARAD BAJAJ                Addr : SHRI HARI HOSPITAL
    ------------------------------------------------------------
    Qty      Description         Com   Batch      Exp     Amount
    ------------------------------------------------------------
    14xTAB   MOXIKIND CV 625     MANKI B6AEZ191   10-27   257.80
    ------------------------------------------------------------
                                          Net Amount :   1158.82
    ============================================================
    E & O E, Subject to JALNA jurisdiction    For SHRIHARI MEDICAL
    Drug Lic. No. MH-JLN-439868, MH-JLN-439867
    GSTIN :           Get well soon..                  Pharmacist
    Composition taxable person, not eligible to collect tax ...

No boxes, no GST. Only the dot matrix slip takes this layout (core/dot_matrix_print.py);
the normal printer keeps its own bill and only its heading changes to BILL OF SUPPLY
(owner: "for the normal printer only bill of supply heading is enough"). Picking this
template makes the bill a Bill of Supply (core/bill_heading.of), whatever the store's
heading says.
"""
from __future__ import annotations

import re
from typing import Any, Optional

from core.bill_render_utils import (
    bill_due_display, fmt_expiry_mm_yy, return_row_label, return_total_label, total_due_display,
)

TEMPLATE_ID = "bill_of_supply"
TEMPLATE_LABEL = "Bill of Supply (Micropro style)"


def is_micropro(settings: Optional[dict], ctx: Any = None) -> bool:
    """The sale bill is printed in this style. A supplier document (purchase return,
    reorder) has its own title and keeps the normal layout."""
    if str((settings or {}).get("template") or "").strip().lower() != TEMPLATE_ID:
        return False
    doc_title = (getattr(ctx, "document_title", "") if ctx is not None else "") or ""
    return not (doc_title.strip() or str((settings or {}).get("document_title") or "").strip())


def _num(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def qty_text(item: Any) -> str:
    """"14xTAB", "1x30 GM", "1x100 ML", "1xSYRUP": the count and what it counts."""
    qty = _num(getattr(item, "qty", 0))
    count = str(int(qty)) if qty == int(qty) else f"{qty:g}"
    med_type = str(getattr(item, "type", "") or "").strip()
    unit = str(getattr(item, "unit", "") or "").strip()
    word = ""
    try:
        from core.layout_config import is_strip_count_type

        strip = bool(med_type or unit) and is_strip_count_type(med_type, unit)
    except Exception:
        strip = False
    if strip:
        word = "CAP" if "CAP" in med_type.upper() else "TAB"
    elif re.search(r"[A-Za-z]", unit):
        word = unit.upper()
    elif med_type:
        word = med_type.upper()
    return f"{count}x{word}" if word else count


def company_short(manufacturer: Any) -> str:
    """The Com column: the first five letters of the company ("MANKI", "CIPLA")."""
    return "".join(ch for ch in str(manufacturer or "").upper() if ch.isalnum())[:5]


def expiry_text(raw: Any) -> str:
    return fmt_expiry_mm_yy(str(raw or "")).replace("/", "-") if raw else ""


def jurisdiction_city(address: Any) -> str:
    """The town for "Subject to JALNA jurisdiction": the last part of the shop's address
    that is not a PIN code or the state; its last word when that part is a whole street."""
    for part in reversed(re.split(r"[,\n]", str(address or ""))):
        word = re.sub(r"[\d\-\s]+$", "", part.strip()).strip(" .-")
        if not word or word.upper() in {"MAHARASHTRA", "INDIA", "MS", "M.S", "DIST", "TQ"}:
            continue
        words = word.split()
        return (words[-1] if len(words) > 2 else word).upper()
    return ""


def item_rows(ctx: Any) -> list[dict[str, str]]:
    """One dict per printed row: the medicines, then the returns settled in this bill."""
    rows: list[dict[str, str]] = []
    for item in getattr(ctx, "items", None) or []:
        rows.append({
            "qty": qty_text(item),
            "desc": str(getattr(item, "name", "") or "").upper(),
            "com": company_short(getattr(item, "manufacturer", "")),
            "batch": str(getattr(item, "batch", "") or ""),
            "exp": expiry_text(getattr(item, "expiry", "")),
            "amount": f"{_num(getattr(item, 'amount', 0)):.2f}",
        })
    for row in getattr(ctx, "returns", None) or []:
        kind = getattr(row, "kind", "")
        if kind == "return":
            rows.append({"qty": "", "desc": "*** " + return_row_label(row) + " ***", "com": "",
                         "batch": "", "exp": "", "amount": ""})
        elif kind == "total":
            rows.append({"qty": "", "desc": return_total_label(row), "com": "", "batch": "",
                         "exp": "", "amount": f"-{_num(row.amount):.2f}"})
        else:
            qty = _num(row.qty)
            rows.append({
                "qty": str(int(qty)) if qty == int(qty) else f"{qty:g}",
                "desc": str(row.name or "").upper(), "com": "",
                "batch": str(row.batch or ""), "exp": expiry_text(row.expiry),
                "amount": f"-{_num(row.amount):.2f}",
            })
    return rows


def totals(ctx: Any, settings: dict, *, dues: bool) -> list[tuple[str, float, bool]]:
    """(label, amount, bold) for the right of the bill: Net Amount, and around it the rows
    the shop's ticks ask for. No GST row: a Bill of Supply states no tax."""
    if getattr(ctx, "is_continued", False):
        return [("Continued...", _num(getattr(ctx, "page_subtotal", 0)), False)]
    out: list[tuple[str, float, bool]] = []
    try:
        from bill_templates.classic import gross_total_to_show

        gross = gross_total_to_show(ctx, settings)
    except Exception:
        gross = 0.0
    if gross:
        out.append(("Gross Total", gross, False))
    if settings.get("show_discount", True) and _num(ctx.discount) > 0:
        out.append(("Less Disc", _num(ctx.discount), False))
    if abs(_num(ctx.rounding)) >= 0.01:
        out.append(("Rounding", _num(ctx.rounding), False))
    out.append(("Net Amount", _num(ctx.grand_total), True))
    returned = round(_num(getattr(ctx, "returns_total", 0)), 2)
    if returned > 0:
        out.append(("Return total", -returned, False))
        out.append(("Net to pay", round(_num(ctx.grand_total) - returned, 2), True))
    if dues:
        prev = _num(getattr(ctx, "previous_due", 0))
        if prev > 0:
            out.append(("Prev Due", prev, False))
        if bill_due_display(ctx) > 0:
            out.append(("Bill Due", bill_due_display(ctx), False))
        if total_due_display(ctx) > 0:
            out.append(("Total Due", total_due_display(ctx), False))
    return out


def header(ctx: Any, settings: dict) -> dict[str, Any]:
    """The words above the table, each one only when its tick is on and it has a value."""
    from core import bill_heading

    on = lambda key: bool(settings.get(key, True))  # noqa: E731
    address = []
    if on("show_store_address"):
        address = [line.strip() for line in str(ctx.address or "").splitlines() if line.strip()]
    contact = []
    if on("show_store_phone") and str(ctx.phone or "").strip():
        contact.append(f"Ph : {str(ctx.phone).strip()}")
    email = str(getattr(ctx, "email", "") or "").strip()
    if on("show_store_email") and email:
        contact.append(f"E-Mail : {email}")
    page_count = int(getattr(ctx, "page_count", 0) or 0)
    page_no = int(getattr(ctx, "page_index", 0) or 0) + 1
    page = ""
    if getattr(ctx, "is_continued", False):
        page = f"CONTINUED  Page {page_no}/{page_count}" if page_count > 1 else "CONTINUED"
    elif page_count > 1:
        page = f"Page {page_no}/{page_count}"
    date = (getattr(ctx, "bill_date_landscape", "") or ctx.bill_date or "").strip()
    party = not settings.get("hide_party_meta")
    show_doc = on("show_doctor") and on("show_doctor_name")
    return {
        "title": bill_heading.BILL_OF_SUPPLY_TITLE,
        "store": str(ctx.store_name or "").upper() if on("show_store_name") else "",
        "address": address,
        "contact": contact,
        "date": date.replace("/", "-") if on("show_bill_date") else "",
        "bill_no": str(ctx.bill_no or "") if on("show_bill_no") else "",
        "name": str(ctx.cust_name or "").upper() if party and on("show_patient_name") else "",
        "addr": str(ctx.cust_addr or "").upper() if party and on("show_patient_address") else "",
        "doctor": str(ctx.doctor_name or "").upper() if show_doc else "",
        "page": page,
        "first_page": int(getattr(ctx, "page_index", 0) or 0) == 0,
    }


def footer(ctx: Any, settings: dict) -> dict[str, str]:
    """E & O E, the drug licence, the nice line, the signature and the composition line."""
    from core import bill_heading

    if getattr(ctx, "is_continued", False):
        return {}
    on = lambda key: bool(settings.get(key, True))  # noqa: E731
    city = jurisdiction_city(ctx.address)
    dl = ", ".join(p.strip() for p in re.split(r"[\n;]", str(getattr(ctx, "dl_no", "") or ""))
                   if p.strip())
    gstin = str(getattr(ctx, "gstin", "") or "").strip()
    fssai = str(getattr(ctx, "fssai", "") or "").strip()
    wish = ""
    if settings.get("show_recovery_wish", False):
        wish = str(getattr(ctx, "recovery_wish_line", "") or settings.get("recovery_wish_line") or "").strip()
    # Micropro's own words, unless the shop typed its own line in place of HAVE A NICE DAY.
    nice = str(settings.get("gst_day_line") or "").strip()
    if not nice or nice.upper() == "HAVE A NICE DAY":
        nice = "Get well soon.."
    return {
        "eoe": f"E & O E, Subject to {city} jurisdiction" if city else "E & O E",
        "for_shop": f"For {str(ctx.store_name or '').upper()}" if ctx.store_name else "",
        "dl": f"Drug Lic. No. {dl}" if on("show_store_dl") and dl else "",
        "fssai": (f"FSSAI : {fssai}" if on("show_store_fssai") and fssai
                  and getattr(ctx, "show_fssai_on_bill", False) else ""),
        "gstin": f"GSTIN : {gstin}" if on("show_store_gstin") and gstin else "",
        "nice": nice,
        "wish": wish,
        "sign": "Pharmacist" if on("show_signature") else "",
        "declaration": bill_heading.supply_declaration(ctx, settings).strip(),
    }
