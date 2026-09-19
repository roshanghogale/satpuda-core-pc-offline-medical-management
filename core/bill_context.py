"""Tk-free helpers shared by the bill renderer.

_build_bill_context and _logo_to_base64 are pure data/IO -- they never touch a
widget. They used to live in widgets/bill_preview.py, which imports tkinter at
module scope, so the headless data engine (built with tkinter EXCLUDED) could
not print a bill at all: "ModuleNotFoundError: No module named 'tkinter'".
Keeping them here lets the engine render bills with no GUI toolkit present.
widgets/bill_preview.py re-imports them, so the Tk app is unchanged.
"""
from __future__ import annotations

import base64
import os

from core.bill_config import (  # noqa: F401
    BillContext,
    BillItem,
    apply_a5_portrait_bill_layout,
    load_bill_print_settings,
    render_bill_html,
)
from core.bill_gst import printed_bill_gst
from core.font_config import *  # noqa: F401,F403


def _fmt_date(raw):
    """dd/mm/yy for the printed bill. Moved here with its callers."""
    if not raw:
        return ""
    try:
        parts = str(raw).split('-')
        if len(parts) == 3:
            return f"{parts[2]}/{parts[1]}/{parts[0][2:]}"
    except Exception:
        pass
    return str(raw)


def _build_bill_context(profile, bill_info, items, pay_mode, cursor):
    store_name = profile[1] if profile else "MEDICAL STORE"
    address = profile[2] if profile else ""
    phone = profile[3] if profile else ""
    email = profile[4] if profile else ""
    gstin = profile[5] if profile else ""
    dl_no = profile[6] if profile else ""
    gst_enabled = bool(profile[7]) if profile else False
    logo_path = profile[9] if (profile and len(profile) > 9) else ''
    fssai_number = (profile[10] or '').strip() if (profile and len(profile) > 10) else ''
    show_fssai_on_bill = bool(profile[11]) if (profile and len(profile) > 11) else False
    logo_src = _logo_to_base64(logo_path)

    try:
        from core.fy_serial import display_sales_bill_no
        bill_no = display_sales_bill_no(str(bill_info[0] or ""))
    except Exception:
        raw_bno = str(bill_info[0] or "")
        bill_no = raw_bno.split("/FY", 1)[0] if "/FY" in raw_bno else raw_bno
    bill_date = _fmt_date(bill_info[1])
    cust_name = bill_info[2] or ""
    cust_phone = bill_info[3] or ""
    cust_addr = bill_info[4] or ""
    grand_total = float(bill_info[5] or 0)
    discount = float(bill_info[6] or 0)
    amount_paid = float(bill_info[7] or 0)
    prev_due = float(bill_info[8] or 0)
    due_amt = float(bill_info[9] or 0)
    prev_credit = float(bill_info[16] or 0) if len(bill_info) > 16 else 0.0
    total_due = float(bill_info[15] or 0) if len(bill_info) > 15 else (
        prev_due + due_amt
    )
    doctor_name = (bill_info[14] or "").strip()

    doctor_reg = ""
    if doctor_name:
        cursor.execute(
            "SELECT registration_number FROM doctors "
            "WHERE UPPER(name)=? LIMIT 1",
            (doctor_name.upper(),),
        )
        doc_row = cursor.fetchone()
        if doc_row:
            doctor_reg = (doc_row[0] or "").strip()

    bill_items = []
    sub_total = 0.0
    for it in items:
        amt = float(it[7] or 0)
        gst_pct = float(it[8] or 0)
        sub_total += amt
        # The MRP has to be quoted in the SAME unit the quantity counts in, or
        # the line does not add up on the printed bill. A strip's MRP sits in
        # medicines.mrp while the sale is in tablets, so a 10-tablet strip at
        # 199.65 printed "MRP 199.65 x Qty 10" against an amount of 199.65 --
        # out by the strip size, and the shop could not reconcile the bill.
        mrp = float(it[9] or it[6] or 0)
        if len(it) > 11:
            try:
                from core.layout_config import (
                    is_strip_count_type,
                    parse_tablets_per_stripe,
                )

                if is_strip_count_type(str(it[10] or ""), str(it[11] or "")):
                    per_strip = parse_tablets_per_stripe(it[11])
                    if per_strip > 1:
                        mrp = round(mrp / per_strip, 4)
            except Exception:
                pass
        bill_items.append(BillItem(
            name=it[0] or "",
            batch=it[2] or "",
            expiry=it[4] or "",
            qty=float(it[5] or 0),
            rate=float(it[6] or 0),
            mrp=mrp,
            amount=amt,
            gst_percent=gst_pct,
            manufacturer=it[3] or "",
        ))

    sub_total = round(sub_total, 2)
    # The tax comes out of what each line sells for AFTER the bill discount, line by
    # line and half-up (core.bill_gst). It was backed out BEFORE the discount while the
    # taxable value was the grand total after it, so a discounted bill printed too much
    # GST and too little taxable value.
    gst = printed_bill_gst(
        [(item.amount, item.gst_percent) for item in bill_items],
        discount,
        gst_enabled=gst_enabled,
    )

    settings = load_bill_print_settings()
    ctx = BillContext(
        store_name=(store_name or "").upper(),
        address=address or "",
        email=email or "",
        phone=phone or "",
        gstin=gstin or "",
        dl_no=dl_no or "",
        fssai=fssai_number,
        show_fssai_on_bill=show_fssai_on_bill,
        logo_src=logo_src,
        bill_no=bill_no,
        bill_date=bill_date,
        bill_date_landscape=bill_date,
        cust_name=cust_name,
        cust_phone=cust_phone,
        cust_addr=cust_addr,
        pay_mode=pay_mode,
        doctor_name=doctor_name,
        doctor_reg=doctor_reg,
        items=bill_items,
        sub_total=sub_total,
        discount=discount,
        taxable_amount=float(gst.taxable),
        gst_amount=float(gst.tax),
        cgst_amount=float(gst.cgst),
        sgst_amount=float(gst.sgst),
        grand_total=grand_total,
        rounding=float(bill_info[13] or 0),
        amount_paid=amount_paid,
        gst_enabled=gst_enabled,
        blessing_line=settings.get("blessing_line", "SHREE GANESHAY NAMAH"),
        recovery_wish_line=settings.get(
            "recovery_wish_line", "I WISH FOR YOUR SPEEDY RECOVERY."),
        previous_due=prev_due,
        previous_credit=prev_credit,
        due_amount=due_amt,
        total_due=total_due,
    )
    from core.upi_qr import apply_upi_qr_to_context
    apply_upi_qr_to_context(ctx)
    return ctx




def _logo_to_base64(logo_path):
    """The shop's logo as a data: URI.

    logo_path is a HINT, not the answer. It is an absolute path on whichever
    machine last picked the picture, and Online it arrives from the store
    server: every store row on the server carries a NULL logo_path, so this was
    handed '' on a machine whose AppData held the logo all along, and handed
    another PC's C:\\Users\\... path on a machine that never had the file. Both
    printed a bill with no logo. A shop that HAS the file at this path still
    uses it, exactly as before -- that is the first branch, so the offline
    single-PC shop is untouched.
    """
    path = str(logo_path or '')
    try:
        if path and os.path.exists(path):
            import base64
            ext = os.path.splitext(path)[1].lower().lstrip('.')
            mime = {'jpg': 'jpeg', 'jpeg': 'jpeg', 'png': 'png',
                    'gif': 'gif', 'bmp': 'bmp', 'webp': 'webp'}.get(ext, 'png')
            with open(path, 'rb') as f:
                data = base64.b64encode(f.read()).decode('ascii')
            return f"data:image/{mime};base64,{data}"
    except Exception:
        pass
    try:
        from core.store_images import BILL_LOGO, resolve_data_uri

        return resolve_data_uri(BILL_LOGO, path)
    except Exception:
        return ''


