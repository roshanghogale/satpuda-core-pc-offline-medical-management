"""
Style 1: GST Vertical Rotated Bill.

Matches the photographed pharmacy GST receipt: thick ruled box, 50/50 header,
dense medicine grid, one-line GST message, and fixed totals/signature block.
"""
from __future__ import annotations

from typing import Any, Dict

from core.bill_config import BillContext, get_bill_body_padding, get_bill_content_scale
from core.bill_gst import gst_strip_figures
from core.bill_page_config import (
    get_classic_layout,
    print_toolbar_css,
    print_toolbar_html,
)
from core.bill_print_settings import load_bill_print_settings
from core.bill_render_utils import esc, fmt_expiry_mm_yy, bill_due_display, total_due_display


def _density_class(count: int) -> str:
    if count >= 18:
        return "density-max"
    if count >= 12:
        return "density-tight"
    if count >= 8:
        return "density-compact"
    return "density-normal"


def _nice_day_line(settings) -> str:
    return (settings.get("gst_day_line") or "HAVE A NICE DAY").strip() or "HAVE A NICE DAY"


def _is_tax_invoice(settings) -> bool:
    """The "legacy" template id: the classic layout titled TAX INVOICE."""
    return (settings.get("template") or "classic").lower() == "legacy"


def _show_gst_details(settings) -> bool:
    # A document titled Tax Invoice has to state its taxable value and tax. This used
    # to return False for the legacy template, so the Tax Invoice was the one bill with
    # no GST on it at all; it now follows "GST amount row" exactly like GST Invoice.
    return bool(settings.get("show_gst", True))


def _gst_calc_line(ctx, settings) -> str:
    """GST breakdown only (no 'have a nice day' suffix)."""
    if not _show_gst_details(settings) or not ctx.gst_enabled or ctx.gst_amount <= 0:
        return ""

    # Taxable value and CGST/SGST after the bill discount -- see core.bill_gst.
    taxable, cgst, sgst = gst_strip_figures(ctx)
    rates = sorted({float(item.gst_percent or 0) for item in ctx.items if item.gst_percent})
    if len(rates) == 1 and rates[0] > 0:
        half_rate = rates[0] / 2
        return (
            f"GST {taxable:.2f}*{half_rate:g}+{half_rate:g}%="
            f"{sgst:.2f}SGST+{cgst:.2f}CGST"
        )
    return f"GST {taxable:.2f} = {sgst:.2f}SGST + {cgst:.2f}CGST"


def _gst_line(ctx, settings) -> str:
    nice = _nice_day_line(settings)
    calc = _gst_calc_line(ctx, settings)
    return f"{calc}, {nice}" if calc else nice


def _parse_layout_mm(value) -> float:
    try:
        return float(str(value or "").replace("mm", "").strip())
    except ValueError:
        return 12.0


def _sum_table_row_count(ctx, settings) -> int:
    rows = 0
    if settings.get("show_discount", True) and float(getattr(ctx, "discount", 0) or 0) > 0:
        rows += 1
    if _show_gst_details(settings) and ctx.gst_enabled and float(ctx.gst_amount or 0) > 0:
        rows += 1
    if settings.get("show_total", True):
        rows += 1
    if settings.get("show_customer_due", False):
        prev = float(getattr(ctx, "previous_due", 0) or 0)
        bill_due = _bill_due_display(ctx)
        total = _total_due_display(ctx)
        if prev > 0:
            rows += 1
        if bill_due > 0:
            rows += 1
        if total > 0:
            rows += 1
    return max(rows, 1)


def _bottom_row1_mm(ctx, settings, layout: dict, *, upright: bool = False) -> float:
    """Grow totals band when Prev/Bill/Total Due rows are shown."""
    base = _parse_layout_mm(layout.get("bottom_row1", "12mm"))
    rows = _sum_table_row_count(ctx, settings)
    per_row = 3.0 if upright else 3.1
    needed = rows * per_row + 1.4
    if upright and rows >= 4:
        needed += 1.5
    return max(base, needed)


def _medicine_columns(settings):
    cols = []
    mrp_hdr = (settings.get("medicine_mrp_header") or "MRP").strip()
    amt_hdr = (settings.get("medicine_amount_header") or "Amount").strip()
    if settings.get("show_sr_no", True):
        cols.append(("sr", "Sr.N", "c", "6%"))
    if settings.get("show_medicine_name", True):
        cols.append(("name", "Name of Medicine", "l", "37%"))
    if settings.get("show_batch", True):
        cols.append(("batch", "Batch no", "c", "15%"))
    if settings.get("show_expiry", True):
        cols.append(("expiry", "Exp", "c", "8%"))
    if settings.get("show_qty", True):
        cols.append(("qty", "QTY", "c", "7%"))
    if settings.get("show_mrp", True):
        cols.append(("mrp", mrp_hdr, "r", "13%"))
    if settings.get("show_line_amount", True):
        cols.append(("amount", amt_hdr, "r", "14%"))
    if not cols:
        cols = [("name", "Name of Medicine", "l", "100%")]
    return cols


def _item_rows(ctx, settings) -> str:
    cols = _medicine_columns(settings)
    rows = ""
    sr0 = int(getattr(ctx, "item_sr_offset", 0) or 0)
    for index, item in enumerate(ctx.items):
        mrp_val = float(item.rate or item.mrp or 0) if (
            (settings.get("medicine_mrp_header") or "").strip().upper() == "RATE"
        ) else float(item.mrp or item.rate or 0)
        cells = {
            "sr": str(sr0 + index + 1),
            "name": f"<b><i>{esc(item.name.upper())}</i></b>",
            "batch": f"<b><i>{esc(item.batch)}</i></b>",
            "expiry": f"<b><i>{esc(fmt_expiry_mm_yy(item.expiry) if item.expiry else '')}</i></b>",
            "qty": str(int(item.qty)) if float(item.qty or 0) == int(item.qty or 0) else str(item.qty),
            "mrp": f"<b><i>{mrp_val:.2f}</i></b>",
            "amount": f"{float(item.amount or 0):.2f}",
        }
        rows += "<tr>"
        for key, _label, align, _width in cols:
            cls = "med-name" if key == "name" else align
            rows += f'<td class="{cls}">{cells[key]}</td>'
        rows += "</tr>"
    rows += '<tr class="fill-row">'
    for _key, _label, align, _width in cols:
        rows += f'<td class="{align}"></td>'
    rows += "</tr>"
    return rows


def _medicine_table(ctx, settings) -> str:
    cols = _medicine_columns(settings)
    colgroup = "".join(f'<col style="width:{width}">' for _key, _label, _align, width in cols)
    header = "".join(
        f'<th class="{align}">{esc(label)}</th>' for _key, label, align, _width in cols
    )
    return f"""
      <table class="medicine-table">
        <colgroup>{colgroup}</colgroup>
        <thead><tr>{header}</tr></thead>
        <tbody>{_item_rows(ctx, settings)}</tbody>
      </table>"""


def _doctor_rows(ctx, settings) -> str:
    rows = ""
    if settings.get("show_doctor", True) and settings.get("show_doctor_name", True):
        rows += f"""
          <tr><td>Dr.NAME</td><td>:</td><td>{esc(ctx.doctor_name)}</td></tr>"""
    if settings.get("show_doctor", True) and settings.get("show_doctor_reg", True):
        rows += f"""
          <tr><td>Dr.Reg.</td><td>:</td><td>{esc(ctx.doctor_reg)}</td></tr>"""
    return rows


def _meta_rows(ctx, settings) -> str:
    rows = ""
    bill_no_label = (settings.get("meta_bill_no_label") or "BILL NO.").strip()
    party_label = (settings.get("meta_party_name_label") or "Pt.NAME").strip()
    party_addr_label = (settings.get("meta_party_address_label") or "Pt.ADD.").strip()
    date_label = (settings.get("meta_date_label") or "Date").strip()
    if settings.get("show_bill_no", True):
        rows += f"""
              <tr><td>{esc(bill_no_label)}</td><td>:</td><td>{esc(ctx.bill_no)}</td></tr>"""
    if settings.get("show_bill_date", True):
        bill_date = ctx.bill_date_landscape or ctx.bill_date
        rows += f"""
              <tr><td>{esc(date_label)}</td><td>:</td><td>{esc(bill_date)}</td></tr>"""
    if not settings.get("hide_party_meta"):
        ref = (getattr(ctx, "reference_line", None) or "").strip()
        if ref:
            rows += f"""
              <tr><td>Ref.</td><td>:</td><td>{esc(ref)}</td></tr>"""
        if settings.get("show_patient_name", True):
            rows += f"""
              <tr><td>{esc(party_label)}</td><td>:</td><td>{esc(ctx.cust_name)}</td></tr>"""
        if settings.get("show_patient_address", True):
            rows += f"""
              <tr><td>{esc(party_addr_label)}</td><td>:</td><td>{esc(ctx.cust_addr)}</td></tr>"""
        for label, value in getattr(ctx, "extra_meta_rows", None) or []:
            text = str(value or "").strip()
            if not text:
                continue
            rows += f"""
              <tr><td>{esc(str(label).strip())}</td><td>:</td><td>{esc(text)}</td></tr>"""
    rows += _doctor_rows(ctx, settings)
    return rows


def _terms(settings) -> str:
    return ""


def _signature(ctx, settings) -> str:
    if not settings.get("show_signature", True):
        return ""
    sign_caption = (settings.get("signature_caption") or "SIGN OF Q.P.").strip()
    return f"""
        <div class="for-shop">For {esc(ctx.store_name.upper())}</div>
        <div class="sign-label">{esc(sign_caption)}</div>"""


def _has_upi_qr(ctx) -> bool:
    return bool(
        getattr(ctx, "show_upi_qr", False) and getattr(ctx, "upi_qr_src", "")
    )


def _nice_day_markup(settings) -> str:
    if not settings.get("show_gst_strip", True):
        return ""
    return f'<div class="nice-day-msg">{esc(_nice_day_line(settings))}</div>'


def _messages_col_markup(ctx, settings) -> str:
    """Two equal-height boxes: nice day on top, recovery wish below."""
    boxes = []
    if settings.get("show_gst_strip", True):
        boxes.append(
            f'<div class="msg-box">{esc(_nice_day_line(settings))}</div>'
        )
    if settings.get("show_recovery_wish", True):
        wish_text = (
            getattr(ctx, "recovery_wish_line", None)
            or settings.get("recovery_wish_line", "I WISH FOR YOUR SPEEDY RECOVERY.")
        )
        boxes.append(f'<div class="msg-box">{esc(wish_text)}</div>')
    return "".join(boxes)


def _wish_markup(ctx, settings) -> str:
    if not settings.get("show_recovery_wish", True):
        return ""
    wish_text = (
        getattr(ctx, "recovery_wish_line", None)
        or settings.get("recovery_wish_line", "I WISH FOR YOUR SPEEDY RECOVERY.")
    )
    return f'<div class="wish">{esc(wish_text)}</div>'


def _terms_zone_content(ctx, settings) -> str:
    return f"{_wish_markup(ctx, settings)}{_terms(settings)}"


def _upi_qr_block(ctx) -> str:
    if not _has_upi_qr(ctx):
        return ""
    amt = float(getattr(ctx, "upi_qr_amount", 0) or 0)
    return (
        f'<div class="upi-qr-wrap">'
        f'<img class="upi-qr" src="{ctx.upi_qr_src}" alt="UPI QR">'
        f'<div class="upi-amt">Scan &amp; Pay ₹{amt:.2f}</div>'
        f"</div>"
    )


def _bottom_zone_rows(ctx, settings, layout: dict, *, upright: bool = False) -> str:
    """Grid row sizes for the footer."""
    totals_h = _bottom_row1_mm(ctx, settings, layout, upright=upright)
    return f"{totals_h:.2f}mm 1fr"


def _footer_strip_text(ctx, settings) -> str:
    custom = (
        getattr(ctx, "footer_strip_line", None)
        or settings.get("footer_strip_line")
        or ""
    ).strip()
    if custom:
        return custom
    return _gst_line(ctx, settings)


def _bottom_zone_markup(ctx, settings, layout: dict, *, upright: bool = False) -> str:
    continued = bool(getattr(ctx, "is_continued", False))
    strip_text = _footer_strip_text(ctx, settings) if not continued else ""
    gst_strip = (
        f'<div class="gst-strip">{esc(strip_text)}</div>'
        if settings.get("show_gst_strip", True) and strip_text
        else '<div class="gst-strip"></div>'
    )
    show_disc = settings.get("show_discount", True)
    less_value = ctx.discount if (show_disc and ctx.discount > 0 and not continued) else 0.0
    disc_label = (settings.get("discount_label") or "DISC").strip()
    total_label = (settings.get("total_label") or "Total").strip()
    gst_total_row = (
        f'<tr><td>GST</td><td class="r">{ctx.gst_amount:.2f}</td></tr>'
        if _show_gst_details(settings) and ctx.gst_enabled and ctx.gst_amount > 0 and not continued
        else ""
    )
    totals_rows = ""
    if less_value > 0:
        totals_rows += f'<tr><td>{esc(disc_label)}</td><td class="r">{less_value:.2f}</td></tr>'
    totals_rows += gst_total_row
    if settings.get("show_total", True):
        if continued:
            # Carry-forward page: page subtotal + Continued... (not full bill total).
            page_amt = float(getattr(ctx, "page_subtotal", 0) or 0)
            totals_rows += (
                f'<tr class="total"><td>Continued...</td>'
                f'<td class="r">{page_amt:.2f}</td></tr>'
            )
        else:
            totals_rows += (
                f'<tr class="total"><td>{esc(total_label)}</td><td class="r">{ctx.grand_total:.2f}</td></tr>'
            )
    if not continued:
        totals_rows += _due_totals_rows(ctx, settings)

    row_sizes = _bottom_zone_rows(ctx, settings, layout, upright=upright)
    upi = _has_upi_qr(ctx)
    zone_class = "bottom-zone bottom-zone-has-upi" if upi else "bottom-zone"

    if upi:
        gst_only = _gst_calc_line(ctx, settings)
        gst_row = (
            f'<div class="gst-strip gst-strip-gst-only">{esc(gst_only)}</div>'
            if gst_only
            else ""
        )
        panel_class = "footer-left-panel"
        if not gst_only:
            panel_class += " footer-left-full"
        return f"""
      <div class="{zone_class}" style="grid-template-rows: {row_sizes};">
        {gst_row}
        <div class="totals-cell">
          <table class="sum-table">
            {totals_rows}
          </table>
        </div>
        <div class="{panel_class}">
          <div class="messages-col">{_messages_col_markup(ctx, settings)}</div>
          <div class="upi-qr-cell">{_upi_qr_block(ctx)}</div>
        </div>
        <div class="signature-cell">{_signature(ctx, settings)}</div>
      </div>"""

    return f"""
      <div class="{zone_class}" style="grid-template-rows: {row_sizes};">
        {gst_strip}
        <div class="totals-cell">
          <table class="sum-table">
            {totals_rows}
          </table>
        </div>
        <div class="terms-cell">
          {_terms_zone_content(ctx, settings)}
        </div>
        <div class="signature-cell">{_signature(ctx, settings)}</div>
      </div>"""


def _profile_lines(ctx, settings) -> str:
    fssai = getattr(ctx, "fssai", "")
    lines = []
    if settings.get("show_store_address", True) and ctx.address:
        lines.append(f"<div>{esc(ctx.address)}</div>")
    if settings.get("show_store_email", True) and ctx.email:
        lines.append(f"<div>E-Mail : {esc(ctx.email)}</div>")
    if settings.get("show_store_phone", True) and ctx.phone:
        lines.append(f"<div>Phone : {esc(ctx.phone)}</div>")
    if settings.get("show_store_gstin", True) and ctx.gstin:
        lines.append(f"<div>GSTIN : {esc(ctx.gstin)}</div>")
    if settings.get("show_store_dl", True) and ctx.dl_no:
        lines.append(f"<div>DL.No. : {esc(ctx.dl_no)}</div>")
    # Both the Bill Fields tick AND the shop's own "Print FSSAI on sale bills"
    # profile setting, which had no reader at all before.
    if (
        settings.get("show_store_fssai", True)
        and getattr(ctx, "show_fssai_on_bill", False)
        and fssai
    ):
        lines.append(f"<div>FSSAI : {esc(fssai)}</div>")
    return "".join(lines)


def _left_panel_extra(ctx, settings) -> str:
    """Supplier / return details under the pharmacy block (left column)."""
    if not settings.get("show_party_on_left_panel"):
        return ""
    rows = getattr(ctx, "left_panel_lines", None) or []
    parts = []
    for label, value in rows:
        text = str(value or "").strip()
        if not text:
            continue
        parts.append(
            f'<div class="shop-party-line">'
            f'<span class="shop-party-lbl">{esc(str(label).strip())}</span> '
            f'{esc(text)}</div>'
        )
    if not parts:
        return ""
    return f'<div class="shop-party-block">{"".join(parts)}</div>'


def _compact_supplier_bottom(ctx, settings, layout: dict, *, upright: bool = False) -> str:
    """Minimal footer for A6 supplier return/reorder — totals only."""
    total_label = (settings.get("total_label") or "Total").strip()
    disc_label = (settings.get("discount_label") or "DISC").strip()
    rows = ""
    if float(ctx.discount or 0) > 0 and settings.get("show_discount", True):
        rows += f'<tr><td>{esc(disc_label)}</td><td class="r">{ctx.discount:.2f}</td></tr>'
    rows += (
        f'<tr class="total"><td>{esc(total_label)}</td>'
        f'<td class="r">{ctx.grand_total:.2f}</td></tr>'
    )
    note = (
        getattr(ctx, "footer_strip_line", None)
        or settings.get("footer_strip_line")
        or ""
    ).strip()
    note_html = f'<div class="compact-note">{esc(note)}</div>' if note else ""
    return f"""
      <div class="bottom-zone bottom-zone-compact">
        {note_html}
        <div class="totals-cell totals-cell-full">
          <table class="sum-table">{rows}</table>
        </div>
      </div>"""


def _bill_due_display(ctx) -> float:
    return bill_due_display(ctx)


def _total_due_display(ctx) -> float:
    return total_due_display(ctx)


def _due_totals_rows(ctx, settings) -> str:
    """Customer due lines on printed bill (optional setting)."""
    if not settings.get("show_customer_due", False):
        return ""
    rows = ""
    prev = float(getattr(ctx, "previous_due", 0) or 0)
    bill_due = _bill_due_display(ctx)
    total = _total_due_display(ctx)
    if total <= 0 and prev <= 0 and bill_due <= 0:
        return ""
    if prev > 0:
        rows += f'<tr><td>Prev Due</td><td class="r">{prev:.2f}</td></tr>'
    if bill_due > 0:
        rows += f'<tr><td>Bill Due</td><td class="r">{bill_due:.2f}</td></tr>'
    show_total = total > 0 and (prev > 0 or abs(total - bill_due) > 0.01)
    if show_total:
        rows += f'<tr><td>Total Due</td><td class="r">{total:.2f}</td></tr>'
    return rows


def _logo_markup(ctx, settings) -> str:
    if not getattr(ctx, "logo_src", ""):
        return ""
    parts = []
    if settings.get("show_logo_top_right", False):
        parts.append(f'<img class="bill-logo-tr" src="{ctx.logo_src}" alt="">')
    if settings.get("show_logo_center_watermark", False):
        opacity = max(5, min(80, int(settings.get("logo_watermark_opacity", 15) or 15))) / 100.0
        parts.append(
            f'<img class="bill-logo-wm" src="{ctx.logo_src}" alt="" '
            f'style="opacity:{opacity:.2f};">'
        )
    if not parts:
        return ""
    return f'<div class="bill-logo-layer">{"".join(parts)}</div>'


def _bill_copy(ctx, copy_label: str, settings, *, upright: bool = False, layout: dict | None = None) -> str:
    blessing = getattr(ctx, "blessing_line", None) or settings.get(
        "blessing_line", "SHREE GANESHAY NAMAH"
    )
    copy_id_html = f'<div class="copy-id">{esc(copy_label)}</div>' if copy_label else ""
    doc_title = (getattr(ctx, "document_title", None) or settings.get("document_title") or "").strip()
    if doc_title:
        inv_title = doc_title
    else:
        inv_title = "TAX INVOICE" if _is_tax_invoice(settings) else "GST INVOICE"
    store_name_html = (
        f'<div class="shop-name">{esc(ctx.store_name.upper())}</div>'
        if settings.get("show_store_name", True)
        else ""
    )
    logo_html = _logo_markup(ctx, settings)
    inv_class = "upright-invoice" if upright else "rotated-invoice"
    layout = layout or {}
    if settings.get("compact_supplier_footer"):
        bottom_zone = _compact_supplier_bottom(ctx, settings, layout, upright=upright)
    else:
        bottom_zone = _bottom_zone_markup(ctx, settings, layout, upright=upright)

    hide_blessing = settings.get("hide_blessing") or settings.get("compact_supplier_footer")
    blessing_html = (
        f'<div class="inv-blessing">{esc(blessing)}</div>'
        if settings.get("show_blessing", True) and not hide_blessing
        else ""
    )

    shell_extra = " copy-shell-supplier" if settings.get("supplier_doc_compact") else ""
    return f"""
  <section class="copy-shell{shell_extra}">
    {copy_id_html}
    <div class="{inv_class}">
      {logo_html}
      <div class="top-grid">
        <div class="shop-panel">
          {store_name_html}
          {_profile_lines(ctx, settings)}
          {_left_panel_extra(ctx, settings)}
        </div>
        <div class="invoice-panel">
          {blessing_html}
          <div class="inv-title">{inv_title}</div>
          <table class="meta-table">
            <tbody>{_meta_rows(ctx, settings)}
            </tbody>
          </table>
        </div>
      </div>

      <div class="table-zone">{_medicine_table(ctx, settings)}</div>

      {bottom_zone}
    </div>
  </section>"""


def _separator() -> str:
    return """
  <div class="cut-separator" aria-hidden="true">
    <div class="cut-copy">
      <span class="scissors scissors-top">&#9986;</span>
      <span class="cut-text">CUT HERE</span>
      <span class="scissors scissors-bottom">&#9986;</span>
    </div>
  </div>"""


def _build_pdf_save_body(ctx, settings, pdf_layout: str, layout: dict) -> str:
    """A5 portrait / A6 landscape PDF: upright copies (no 90° rotation)."""
    one = _bill_copy(ctx, "", settings, upright=True, layout=layout)
    layout_key = (pdf_layout or "two_copies").lower()
    if layout_key == "one_a6_landscape":
        return f'<div class="bill-band band-a5p-bottom band-a6-full">{one}</div>'
    if layout_key == "one_top":
        return (
            f'<div class="bill-band band-a5p-half band-a5p-top-copy">{one}</div>'
            f'<div class="band-a5p-spacer" aria-hidden="true"></div>'
        )
    if layout_key != "one_bottom":
        return (
            f'<div class="bill-band band-a5p-half band-a5p-top-copy">{one}</div>'
            f'<div class="bill-band band-a5p-half band-a5p-bottom-copy">{one}</div>'
        )
    return (
        f'<div class="band-a5p-spacer" aria-hidden="true"></div>'
        f'<div class="bill-band band-a5p-half band-a5p-bottom">{one}</div>'
    )


def _build_classic_body_content(ctx, settings, paper: str, copies: int, layout: dict) -> str:
    """Lay out GST invoice copies for cut-friendly printing."""
    paper = (paper or "A5").upper()
    copies = max(1, int(copies or 1))
    if paper == "A5":
        copies = max(1, min(2, copies))
    else:
        copies = max(1, min(4, copies))

    one = _bill_copy(ctx, "", settings, layout=layout)

    def pair():
        return one + _separator() + one

    if paper == "A5":
        if copies == 1:
            pos = str(settings.get("a5_single_copy_position") or "bottom").strip().lower()
            if pos == "top":
                return f'<div class="bill-band band-a5 band-a5-top">{one}</div>'
            return f'<div class="bill-band band-a5 band-one">{one}</div>'
        return f'<div class="bill-band band-a5">{pair()}</div>'

    # A4 portrait — each band = one A5-landscape row (148mm tall), cut horizontally between bands
    if copies == 1:
        pos = str(settings.get("a4_single_copy_position") or "bottom").strip().lower()
        if pos == "top":
            return f'<div class="bill-band band-a4 band-a4-top">{one}</div>'
        return f'<div class="bill-band band-a4">{one}</div>'
    if copies == 2:
        two_layout = str(settings.get("a4_two_copy_layout") or "side_by_side").strip().lower()
        if two_layout in ("top_bottom", "stacked"):
            return (
                f'<div class="bill-band band-a4 band-single">{one}</div>'
                f'<div class="band-cut-h"></div>'
                f'<div class="bill-band band-a4 band-single">{one}</div>'
            )
        return f'<div class="bill-band band-a4">{pair()}</div>'
    if copies == 3:
        return (
            f'<div class="bill-band band-a4">{pair()}</div>'
            f'<div class="band-cut-h"></div>'
            f'<div class="bill-band band-a4 band-single">{one}</div>'
        )
    return (
        f'<div class="bill-band band-a4">{pair()}</div>'
        f'<div class="band-cut-h"></div>'
        f'<div class="bill-band band-a4">{pair()}</div>'
    )


def _scale_layout_mm(layout: dict, scale: float) -> dict:
    """Shrink invoice box dimensions only — never the paper page size."""
    if abs(scale - 1.0) < 0.001:
        return layout
    invoice_keys = frozenset({
        "inv_w", "inv_h", "top_h", "shop_col", "table_h", "meta_lbl",
        "bottom_row1", "totals_col", "cut_sep", "for_shop_mb", "th_h", "body_pad",
    })
    out = dict(layout)
    for key in invoice_keys:
        val = out.get(key)
        if val is None:
            continue
        s = str(val).strip()
        if not s.endswith("mm"):
            continue
        try:
            out[key] = f"{float(s[:-2]) * scale:.2f}mm"
        except ValueError:
            pass
    return out


def render_classic_html(ctx: BillContext, settings: Dict[str, Any] | None = None) -> str:
    settings = settings or load_bill_print_settings()
    for_pdf_save = bool(settings.get("for_pdf_save"))
    if for_pdf_save:
        from core.bill_page_config import get_pdf_save_layout
        pdf_layout = settings.get("pdf_save_layout") or "two_copies"
        layout = get_pdf_save_layout(pdf_layout)
        paper = (layout.get("paper_size") or "A5").upper()
        if settings.get("supplier_doc_compact"):
            layout = dict(layout)
            layout["top_h"] = "36.00mm"
            layout["table_h"] = "44.00mm"
            layout["bottom_row1"] = "9.00mm"
    else:
        layout = get_classic_layout(settings.get("paper_size", "A5"))
        paper = layout["paper_size"]
    body_pad = get_bill_body_padding(settings, layout["body_pad"])
    from core.bill_config import get_bill_layout_copies, get_bill_size_mode, BILL_SIZE_DOT_MATRIX
    content_scale = get_bill_content_scale(settings)
    if content_scale != 1.0:
        layout = _scale_layout_mm(layout, content_scale)

    if for_pdf_save:
        font_scale = max(0.78, min(1.0, settings.get("font_size_pct", 100) / 100 * 0.9))
        pdf_layout = layout.get("pdf_layout") or "two_copies"
        copies = int(layout.get("pdf_copies") or 2)
        if copies >= 2:
            copies_note = "2 upright bills on A5 portrait — cut centre"
        elif pdf_layout == "one_a6_landscape":
            copies_note = "1 upright bill on A6 landscape — full page"
        elif pdf_layout == "one_top":
            copies_note = "1 upright bill on A5 portrait — top half"
        else:
            copies_note = "1 upright bill on A5 portrait — bottom half"
    elif paper == "A5":
        font_scale = max(0.75, min(1.1, settings.get("font_size_pct", 100) / 100 * 0.92))
        copies = get_bill_layout_copies(settings, paper)
        copies_note = f"{copies} bill(s) on A5 landscape — cut centre if 2"
    else:
        font_scale = max(0.8, min(1.2, settings.get("font_size_pct", 100) / 100))
        copies = get_bill_layout_copies(settings, paper)
        copies_note = f"{copies} bill(s) on A4 portrait — cut between bands"
    if get_bill_size_mode(settings) == BILL_SIZE_DOT_MATRIX and content_scale != 1.0:
        copies_note += f" · dot matrix {int(round(content_scale * 100))}%"
    elif content_scale != 1.0 and settings.get("for_pdf_save"):
        copies_note += f" · fit {int(round(content_scale * 100))}%"
    if content_scale != 1.0:
        font_scale *= content_scale
    border = max(0.5, min(2.0, float(settings.get("border_thickness", 1.0))))
    outer_border = round(border * 1.45, 2)
    grid_border = round(max(0.65, border * 0.9), 2)
    logo_tr_top = max(0.0, min(25.0, float(settings.get("logo_top_right_margin_top", 1.2) or 1.2)))
    logo_tr_right = max(0.0, min(25.0, float(settings.get("logo_top_right_margin_right", 1.5) or 1.5)))
    density = _density_class(len(ctx.items))
    batch_body = settings.get("_batch_body_override")
    if batch_body:
        body_content = batch_body
        page_mode = settings.get("_batch_page_mode", "a4-portrait")
        page_align = settings.get("_batch_page_align", "")
        copies_note = settings.get("_batch_copies_note", copies_note)
    elif for_pdf_save:
        pdf_layout = layout.get("pdf_layout") or "two_copies"
        body_content = _build_pdf_save_body(ctx, settings, pdf_layout, layout)
    else:
        body_content = _build_classic_body_content(ctx, settings, paper, copies, layout)
    toolbar = print_toolbar_html(layout["toolbar_hint"], copies_note)
    if batch_body:
        pass
    elif for_pdf_save:
        page_mode = "a6-landscape" if paper == "A6" else "a5-portrait"
        pdf_layout_key = layout.get("pdf_layout") or "two_copies"
        if (layout.get("pdf_copies") or 2) >= 2:
            page_align = " for-pdf-save a5-pdf-two"
        elif pdf_layout_key == "one_a6_landscape":
            page_align = " for-pdf-save a5-pdf-one-a6"
        elif pdf_layout_key == "one_top":
            page_align = " for-pdf-save a5-pdf-one-top"
        else:
            page_align = " for-pdf-save a5-pdf-one-bottom"
    else:
        page_mode = "a4-portrait" if paper == "A4" else "a5-landscape"
        # A4 with 1–2 copies uses bottom half by default; anchor bands there for counter printers.
        a4_pos = str(settings.get("a4_single_copy_position") or "bottom").strip().lower()
        if paper == "A4" and copies == 1 and a4_pos == "top":
            page_align = " a4-band-top"
        else:
            a4_align_bottom = paper == "A4" and copies <= 2
            page_align = " a4-band-bottom" if a4_align_bottom else ""

    dm_class = " dot-matrix-bill" if get_bill_size_mode(settings) == BILL_SIZE_DOT_MATRIX else ""

    return f"""<!DOCTYPE html>
<html lang="en" class="preview-mode">
<head>
<meta charset="UTF-8">
<title>Bill - {esc(ctx.bill_no)}</title>
<style>
  {layout["page_css"]}
  {print_toolbar_css(body_pad)}
  html.preview-mode .bill-page {{
    width: {layout["body_w"]};
    height: {layout["body_h"]};
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  html, body {{
    width: {layout["body_w"]};
    height: {layout["body_h"]};
    max-width: {layout["body_w"]};
    max-height: {layout["body_h"]};
    overflow: hidden;
    background: transparent;
  }}
  body {{
    color: #000;
    font-family: Arial, 'Nirmala UI', sans-serif;
    font-size: {7.1 * font_scale:.2f}pt;
    display: flex;
    flex-direction: column;
    align-items: stretch;
    padding: {body_pad};
    -webkit-print-color-adjust: exact;
    print-color-adjust: exact;
  }}
  .bill-page {{
    width: 100%;
    height: 100%;
    display: flex;
    flex-direction: column;
    flex: 1 1 auto;
    min-height: 0;
  }}
  body.a4-portrait.a4-band-bottom .bill-page {{
    justify-content: flex-end;
  }}
  body.a4-portrait.a4-band-top .bill-page {{
    justify-content: flex-start;
  }}
  body.a5-pdf-one-bottom .bill-page {{
    justify-content: flex-start;
  }}
  body.a5-pdf-one-top .bill-page {{
    justify-content: flex-start;
  }}
  .band-a5p-spacer {{
    flex: 0 0 50%;
    width: 100%;
    min-height: 0;
    height: 50%;
    max-height: 50%;
  }}
  body.for-pdf-save.a5-pdf-one-bottom .band-a5p-bottom {{
    flex: 0 0 50%;
    width: 100%;
    min-height: 0;
    height: 50%;
    max-height: 50%;
    box-sizing: border-box;
    padding: 2mm 3mm 3.5mm 4.5mm;
  }}
  body.for-pdf-save.a5-pdf-one-top .band-a5p-top-copy {{
    flex: 0 0 50%;
    width: 100%;
    min-height: 0;
    height: 50%;
    max-height: 50%;
    box-sizing: border-box;
    padding: 2mm 3mm 3.5mm 4.5mm;
  }}
  body.for-pdf-save.a5-pdf-one-a6 .band-a6-full {{
    flex: 1 1 100%;
    width: 100%;
    min-height: 0;
    height: 100%;
    max-height: 100%;
    box-sizing: border-box;
    padding: 2mm 3mm 3.5mm 4.5mm;
  }}
  body.for-pdf-save.dot-matrix-bill.a5-pdf-one-bottom .band-a5p-bottom,
  body.for-pdf-save.dot-matrix-bill.a5-pdf-one-top .band-a5p-top-copy,
  body.for-pdf-save.dot-matrix-bill.a5-pdf-one-a6 .band-a6-full {{
    padding: 2.5mm 2.5mm 4mm 5.5mm;
  }}
  body.for-pdf-save .copy-shell {{
    display: flex;
    align-items: center;
    justify-content: center;
    overflow: hidden;
    width: 100%;
    height: 100%;
  }}
  body.for-pdf-save .upright-invoice {{
    position: relative;
    left: auto;
    top: auto;
    transform: none;
    flex: 0 0 auto;
    max-width: 100%;
    max-height: 100%;
  }}
  body.for-pdf-save.a5-pdf-one-top .band-a5p-top-copy .upright-invoice {{
    transform: rotate(180deg);
    transform-origin: center center;
  }}
  body.a5-pdf-one-a6 .bill-page {{
    justify-content: flex-start;
  }}
  body.for-pdf-save.a5-pdf-two .bill-page {{
    display: flex;
    flex-direction: column;
    height: 100%;
    min-height: 0;
    overflow: hidden;
  }}
  body.for-pdf-save.a5-pdf-two .band-a5p-half {{
    flex: 0 0 50%;
    width: 100%;
    height: 50%;
    max-height: 50%;
    min-height: 50%;
    box-sizing: border-box;
    overflow: hidden;
    padding: 1.5mm 3mm;
  }}
  body.for-pdf-save.a5-pdf-two .band-a5p-top-copy {{
    border-bottom: 1pt dashed #000;
    padding-bottom: 2mm;
  }}
  body.for-pdf-save.a5-pdf-two .band-a5p-bottom-copy {{
    padding-top: 2mm;
  }}
  body.for-pdf-save.dot-matrix-bill.a5-pdf-two .band-a5p-half {{
    padding: 2mm 2.5mm 2.5mm 4.5mm;
  }}
  .band-a6-landscape {{
    flex: 1 1 100%;
    width: 100%;
    height: 100%;
    min-height: 0;
  }}
  .bill-band {{
    display: flex;
    flex-direction: row;
    align-items: stretch;
    width: 100%;
    min-height: 0;
  }}
  .band-a5 {{
    flex: 1 1 auto;
    height: 100%;
  }}
  .band-a4 {{
    flex: 0 0 50%;
    height: 50%;
    max-height: 50%;
  }}
  .band-a5p-half {{
    flex: 0 0 50%;
    height: 50%;
    max-height: 50%;
    width: 100%;
  }}
  body.for-pdf-save .band-a5p-half .copy-shell {{
    flex: 1 1 100%;
    max-width: 100%;
    width: 100%;
  }}
  .band-cut-h {{
    flex: 0 0 5mm;
    width: 100%;
    min-height: 5mm;
    height: 5mm;
    position: relative;
    border: none;
    background: transparent;
    overflow: visible;
    align-self: stretch;
  }}
  .band-cut-h::before {{
    content: "";
    position: absolute;
    left: 2mm;
    right: 2mm;
    top: 50%;
    transform: translateY(-50%);
    border-top: 1pt dashed #000;
    pointer-events: none;
  }}
  .band-cut-h::after {{
    content: "CUT HERE";
    position: absolute;
    left: 50%;
    top: 50%;
    transform: translate(-50%, -50%);
    background: #fff;
    padding: 0 1.8mm;
    font-size: {5.5 * font_scale:.2f}pt;
    font-weight: 900;
    line-height: 1;
    white-space: nowrap;
    z-index: 1;
  }}
  .band-cut-scissors::after {{
    content: "\\2702  CUT HERE  \\2702";
    font-size: {6.5 * font_scale:.2f}pt;
    padding: 0 2.2mm;
  }}
  .upright-invoice {{
    position: absolute;
    left: 50%;
    top: 50%;
    width: {layout["inv_w"]};
    height: {layout["inv_h"]};
    transform: translate(-50%, -50%);
    transform-origin: center center;
    border: {outer_border:.2f}pt solid #000;
    display: flex;
    flex-direction: column;
    overflow: hidden;
    background: #fff;
  }}
  .band-a4.band-single .copy-shell:first-child {{
    flex: 0 0 50%;
    max-width: 50%;
  }}
  .band-a5.band-one .copy-shell {{
    flex: 0 0 50%;
    max-width: 50%;
  }}
  .copy-shell {{
    position: relative;
    flex: 1 1 0;
    height: 100%;
    min-width: 0;
    overflow: hidden;
  }}
  .copy-id {{
    position: absolute;
    left: 1.4mm;
    bottom: 0.4mm;
    font-size: {6.0 * font_scale:.2f}pt;
    font-weight: 800;
    transform: rotate(-90deg);
    transform-origin: left bottom;
    white-space: nowrap;
  }}
  .rotated-invoice {{
    position: absolute;
    left: 50%;
    top: 50%;
    width: {layout["inv_w"]};
    height: {layout["inv_h"]};
    transform: translate(-50%, -50%) rotate(90deg);
    transform-origin: center center;
    border: {outer_border:.2f}pt solid #000;
    display: flex;
    flex-direction: column;
    overflow: hidden;
    background: #fff;
  }}
  .bill-logo-layer {{
    position: absolute;
    inset: 0;
    z-index: 1;
    pointer-events: none;
    overflow: hidden;
  }}
  .bill-logo-tr {{
    position: absolute;
    top: {logo_tr_top:.1f}mm;
    right: {logo_tr_right:.1f}mm;
    max-height: 11mm;
    max-width: 16mm;
    object-fit: contain;
  }}
  .bill-logo-wm {{
    position: absolute;
    left: 50%;
    top: 52%;
    transform: translate(-50%, -50%);
    max-height: 42mm;
    max-width: 50mm;
    object-fit: contain;
  }}
  .top-grid {{
    position: relative;
    z-index: 2;
    display: grid;
    grid-template-columns: {layout["shop_col"]} 1fr;
    height: {layout["top_h"]};
    border-bottom: {grid_border:.2f}pt solid #000;
    flex-shrink: 0;
  }}
  .shop-panel {{
    padding: 2mm 2.3mm 1mm;
    border-right: {grid_border:.2f}pt solid #000;
    line-height: 1.16;
    font-size: {6.55 * font_scale:.2f}pt;
    overflow: hidden;
  }}
  .shop-name {{
    font-size: {12.6 * font_scale:.2f}pt;
    line-height: 1;
    font-weight: 900;
    margin-bottom: 1.6mm;
  }}
  .shop-party-block {{
    margin-top: 1.1mm;
    padding-top: 0.9mm;
    border-top: {grid_border:.2f}pt solid #000;
    font-size: {5.85 * font_scale:.2f}pt;
    line-height: 1.12;
  }}
  .shop-party-line {{
    margin-bottom: 0.35mm;
  }}
  .shop-party-lbl {{
    font-weight: 900;
  }}
  .copy-shell-supplier .shop-panel {{
    padding: 1.4mm 2mm 0.8mm;
    font-size: {5.9 * font_scale:.2f}pt;
    line-height: 1.1;
  }}
  .copy-shell-supplier .shop-name {{
    font-size: {11.2 * font_scale:.2f}pt;
    margin-bottom: 1mm;
  }}
  .copy-shell-supplier .shop-party-block {{
    margin-top: 0.8mm;
    padding-top: 0.7mm;
    font-size: {5.5 * font_scale:.2f}pt;
    line-height: 1.08;
  }}
  .copy-shell-supplier .shop-party-line {{
    margin-bottom: 0.25mm;
  }}
  .bottom-zone-compact {{
    grid-template-columns: 1fr !important;
    grid-template-rows: auto minmax(7mm, auto) !important;
  }}
  .bottom-zone-compact .compact-note {{
    grid-column: 1 / -1;
    padding: 0.45mm 1.2mm;
    border-bottom: {grid_border:.2f}pt solid #000;
    font-size: {5.6 * font_scale:.2f}pt;
    line-height: 1.08;
    font-weight: 700;
    white-space: normal;
  }}
  .bottom-zone-compact .totals-cell-full {{
    grid-column: 1 / -1;
    grid-row: 2 / 3;
    border-bottom: none;
  }}
  .invoice-panel {{
    padding: 1.2mm 2.2mm 0.7mm;
    overflow: hidden;
  }}
  .inv-blessing {{
    text-align: center;
    font-size: {5.1 * font_scale:.2f}pt;
    line-height: 1;
    font-weight: 700;
  }}
  .inv-title {{
    text-align: center;
    font-size: {10.2 * font_scale:.2f}pt;
    line-height: 1.05;
    font-weight: 900;
    margin-bottom: 1.5mm;
  }}
  .meta-table {{
    width: 100%;
    border-collapse: collapse;
    font-size: {7.0 * font_scale:.2f}pt;
    line-height: 1.18;
    font-weight: 700;
  }}
  .meta-table td:first-child {{ width: {layout["meta_lbl"]}; font-weight: 700; }}
  .meta-table td:nth-child(2) {{ width: 3mm; text-align: center; }}
  .meta-table td:last-child {{ font-weight: 700; }}
  .table-zone {{
    position: relative;
    z-index: 2;
    height: {layout["table_h"]};
    flex-shrink: 0;
    display: flex;
  }}
  .medicine-table {{
    width: 100%;
    height: 100%;
    table-layout: fixed;
    border-collapse: collapse;
  }}
  .medicine-table th,
  .medicine-table td {{
    border-right: {grid_border:.2f}pt solid #000;
    padding: 0.22mm 0.75mm;
    vertical-align: top;
    overflow-wrap: anywhere;
  }}
  .medicine-table th {{
    height: {layout["th_h"]};
    border-bottom: {grid_border:.2f}pt solid #000;
    font-size: {7.0 * font_scale:.2f}pt;
    font-weight: 900;
    line-height: 1.05;
    white-space: nowrap;
  }}
  .medicine-table td {{
    font-size: {7.25 * font_scale:.2f}pt;
    line-height: 1.02;
  }}
  .medicine-table tbody td,
  .medicine-table tbody tr + tr td {{
    border-top: 0 !important;
    border-bottom: 0 !important;
  }}
  .medicine-table th:last-child,
  .medicine-table td:last-child {{ border-right: 0; }}
  .medicine-table .med-name {{ font-weight: 900; }}
  .medicine-table .fill-row td {{
    height: 100%;
    padding: 0;
    vertical-align: top;
  }}
  .density-compact .medicine-table td {{ padding-top: 0.14mm; padding-bottom: 0.14mm; }}
  .density-tight .medicine-table td {{
    padding-top: 0.08mm;
    padding-bottom: 0.08mm;
    font-size: {6.2 * font_scale:.2f}pt;
    line-height: 1;
  }}
  .density-max .medicine-table td {{
    padding-top: 0.02mm;
    padding-bottom: 0.02mm;
    font-size: {5.5 * font_scale:.2f}pt;
    line-height: 0.96;
  }}
  .bottom-zone {{
    position: relative;
    z-index: 2;
    flex: 1;
    min-height: 0;
    border-top: {grid_border:.2f}pt solid #000;
    display: grid;
    grid-template-columns: 1fr {layout["totals_col"]};
    grid-template-rows: {layout["bottom_row1"]} 1fr;
  }}
  .gst-strip {{
    grid-column: 1 / 2;
    grid-row: 1 / 2;
    padding: 1.1mm 1.4mm;
    border-right: {grid_border:.2f}pt solid #000;
    border-bottom: {grid_border:.2f}pt solid #000;
    font-size: {6.25 * font_scale:.2f}pt;
    line-height: 1.1;
    white-space: nowrap;
    overflow: hidden;
  }}
  .totals-cell {{
    grid-column: 2 / 3;
    grid-row: 1 / 2;
    border-bottom: {grid_border:.2f}pt solid #000;
    overflow: hidden;
  }}
  .upright-invoice .sum-table {{
    font-size: {7.2 * font_scale:.2f}pt;
  }}
  .upright-invoice .sum-table td {{
    padding: 0.18mm 0.65mm;
    line-height: 1.02;
  }}
  .upright-invoice .sum-table .total td {{
    font-size: {7.8 * font_scale:.2f}pt;
  }}
  .sum-table {{
    width: 100%;
    height: 100%;
    border-collapse: collapse;
    font-size: {9.0 * font_scale:.2f}pt;
  }}
  .sum-table td {{
    padding: 0.5mm 1.1mm;
    line-height: 1;
  }}
  .sum-table .total td {{
    font-size: {9.8 * font_scale:.2f}pt;
    font-weight: 900;
  }}
  .terms-cell {{
    grid-column: 1 / 2;
    grid-row: 2 / 3;
    padding: 3mm 2mm 1mm;
    border-right: {grid_border:.2f}pt solid #000;
    font-size: {6.6 * font_scale:.2f}pt;
    line-height: 1.35;
  }}
  .wish {{
    font-size: {8.0 * font_scale:.2f}pt;
    font-weight: 700;
    margin-bottom: 1.4mm;
  }}
  .bottom-zone-has-upi .gst-strip-gst-only {{
    padding: 0.7mm 1.2mm;
    font-size: {5.8 * font_scale:.2f}pt;
    line-height: 1.05;
    white-space: normal;
    overflow: hidden;
    overflow-wrap: break-word;
    word-break: break-word;
    display: -webkit-box;
    -webkit-box-orient: vertical;
    -webkit-line-clamp: 3;
  }}
  .footer-left-panel {{
    grid-column: 1 / 2;
    grid-row: 2 / 3;
    display: grid;
    grid-template-columns: 1fr 27mm;
    align-items: stretch;
    min-height: 0;
    border-right: {grid_border:.2f}pt solid #000;
    overflow: hidden;
  }}
  .footer-left-panel.footer-left-full {{
    grid-row: 1 / 3;
    border-top: 0;
  }}
  .messages-col {{
    display: grid;
    grid-template-rows: 1fr 1fr;
    align-content: stretch;
    padding: 0;
    border-right: {grid_border:.2f}pt solid #000;
    overflow: hidden;
    min-width: 0;
    height: 100%;
    min-height: 0;
  }}
  .msg-box {{
    display: flex;
    align-items: flex-start;
    justify-content: flex-start;
    padding: 0.5mm 1.4mm 0;
    text-align: left;
    font-size: {6.6 * font_scale:.2f}pt;
    font-weight: 700;
    line-height: 1.1;
    overflow: hidden;
    white-space: nowrap;
    border-bottom: {grid_border:.2f}pt solid #000;
    min-height: 0;
    height: auto;
    max-height: none;
  }}
  .msg-box:last-child {{
    border-bottom: 0;
  }}
  .bottom-zone-has-upi .msg-box {{
    display: -webkit-box;
    -webkit-box-orient: vertical;
    -webkit-line-clamp: 3;
    white-space: normal;
    overflow-wrap: break-word;
    word-break: break-word;
  }}
  .bottom-zone-has-upi .upi-qr-cell {{
    grid-column: auto;
    grid-row: auto;
    padding: 0;
    border-top: 0;
    border-right: 0;
    display: flex;
    align-items: center;
    justify-content: center;
    overflow: hidden;
    min-height: 0;
    height: 100%;
  }}
  .bottom-zone-has-upi .signature-cell {{
    grid-column: 2 / 3;
    grid-row: 2 / 3;
    padding: 1.8mm 1.5mm 0.6mm;
  }}
  .terms-title {{
    display: inline-block;
    font-size: {8.2 * font_scale:.2f}pt;
    font-style: italic;
    font-weight: 900;
    text-decoration: underline;
    margin-bottom: 2.6mm;
  }}
  .no-return-line {{
    margin-bottom: 0.7mm;
  }}
  .signature-cell {{
    grid-column: 2 / 3;
    grid-row: 2 / 3;
    padding: 2.7mm 1.5mm 0.6mm;
    text-align: center;
    font-size: {8.5 * font_scale:.2f}pt;
    font-weight: 900;
    line-height: 1.15;
  }}
  .for-shop {{
    margin-bottom: {layout["for_shop_mb"]};
  }}
  .sign-label {{
    font-size: {8.8 * font_scale:.2f}pt;
  }}
  .upi-qr-wrap {{
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    text-align: center;
    margin: 0;
    flex-shrink: 0;
    width: 100%;
    height: 100%;
    padding: 1mm 0.6mm;
    box-sizing: border-box;
  }}
  .upi-qr {{
    width: 18mm;
    height: 18mm;
    object-fit: contain;
    display: block;
    margin: 0 auto;
  }}
  .upi-amt {{
    font-size: {5.6 * font_scale:.2f}pt;
    font-weight: 900;
    line-height: 1.05;
    margin-top: 0.4mm;
    text-align: center;
    white-space: nowrap;
  }}
  .cut-separator {{
    position: relative;
    flex: 0 0 {layout["cut_sep"]};
    height: 100%;
  }}
  .cut-separator::before {{
    content: "";
    position: absolute;
    left: 50%;
    top: 0;
    bottom: 0;
    width: 0;
    transform: translateX(-50%);
    border-left: 1pt dashed #000;
  }}
  .cut-copy {{
    position: absolute;
    left: 50%;
    top: 50%;
    transform: translate(-50%, -50%);
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 0.6mm;
    padding: 0.8mm 0.4mm;
    background: #fff;
    font-size: {5.3 * font_scale:.2f}pt;
    font-weight: 900;
    text-align: center;
    white-space: nowrap;
    z-index: 1;
  }}
  .cut-text {{
    writing-mode: vertical-rl;
    text-orientation: mixed;
    background: #fff;
    padding: 0.7mm 0.35mm;
  }}
  .scissors {{
    display: block;
    font-size: {8.0 * font_scale:.2f}pt;
    line-height: 1;
    transform: rotate(45deg);
    transform-origin: center;
  }}
  .c {{ text-align: center; }}
  .r {{ text-align: right; }}
  .l {{ text-align: left; }}
  @media print {{
    {layout["page_css"]}
    html, body {{
      width: {layout["body_w"]} !important;
      height: {layout["body_h"]} !important;
      overflow: hidden !important;
    }}
    html.preview-mode, html.preview-mode body {{
      background: transparent !important;
    }}
  }}
</style>
</head>
<body class="{density} {page_mode}{page_align}{dm_class}">
{toolbar}
<div class="bill-page">{body_content}</div>
</body>
</html>"""


def _build_classic_body_multi(ctxs, settings, paper: str, layout: dict) -> str:
    """Lay out multiple different bills on one sheet (Print All / carry-forward)."""
    paper = (paper or "A5").upper()
    if not ctxs:
        return ""
    if len(ctxs) == 1:
        return _build_classic_body_content(ctxs[0], settings, paper, 1, layout)

    if paper == "A4":
        bills = [_bill_copy(ctx, "", settings, layout=layout) for ctx in ctxs[:4]]
        n = len(bills)
        two_layout = str(settings.get("a4_two_copy_layout") or "side_by_side").strip().lower()
        stacked = two_layout in ("top_bottom", "stacked", "top and bottom")

        def half_pair(left, right):
            if left and right:
                return left + _separator() + right
            return left or right or ""

        if n == 2:
            if stacked:
                return (
                    f'<div class="bill-band band-a4 band-single">{bills[0]}</div>'
                    f'<div class="band-cut-h"></div>'
                    f'<div class="bill-band band-a4 band-single">{bills[1]}</div>'
                )
            return f'<div class="bill-band band-a4">{half_pair(bills[0], bills[1])}</div>'

        if n == 3:
            return (
                f'<div class="bill-band band-a4">{half_pair(bills[0], bills[1])}</div>'
                f'<div class="band-cut-h"></div>'
                f'<div class="bill-band band-a4 band-single">{bills[2]}</div>'
            )

        # 4 bills — two side-by-side rows
        while len(bills) < 4:
            bills.append("")
        return (
            f'<div class="bill-band band-a4">{half_pair(bills[0], bills[1])}</div>'
            f'<div class="band-cut-h"></div>'
            f'<div class="bill-band band-a4">{half_pair(bills[2], bills[3])}</div>'
        )

    if paper == "A5":
        pdf_layout = dict(layout)
        top = _bill_copy(ctxs[0], "", settings, upright=True, layout=pdf_layout)
        bottom = _bill_copy(ctxs[1], "", settings, upright=True, layout=pdf_layout) if len(ctxs) > 1 else ""
        return (
            f'<div class="bill-band band-a5p-half band-a5p-top-copy">{top}</div>'
            f'<div class="bill-band band-a5p-half band-a5p-bottom-copy">{bottom}</div>'
        )

    return _build_classic_body_content(ctxs[0], settings, paper, 1, layout)


def render_classic_bill_html_multi(
    contexts: list,
    settings: Dict[str, Any] | None = None,
) -> str:
    """Render one printable page containing multiple different bills."""
    if not contexts:
        raise ValueError("No bills to render.")
    settings = dict(settings or load_bill_print_settings())
    paper = (settings.get("paper_size") or "A5").upper()
    if paper == "A5" and len(contexts) > 1:
        settings["for_pdf_save"] = True
        settings["orientation"] = "portrait"
        from core.bill_page_config import get_pdf_save_layout
        layout = get_pdf_save_layout("two_copies")
        body_content = _build_classic_body_multi(contexts, settings, paper, layout)
        page_mode = "a5-portrait"
        page_align = " for-pdf-save a5-pdf-two"
        copies_note = f"{len(contexts)} different bills on A5 portrait"
    else:
        from core.bill_page_config import get_classic_layout
        layout = get_classic_layout(paper)
        body_content = _build_classic_body_multi(contexts, settings, paper, layout)
        page_mode = "a4-portrait" if paper == "A4" else "a5-landscape"
        page_align = " a4-band-bottom" if paper == "A4" else ""
        copies_note = f"{len(contexts)} different bills on {paper}"

    return render_classic_html(contexts[0], {
        **settings,
        "_batch_body_override": body_content,
        "_batch_page_mode": page_mode,
        "_batch_page_align": page_align,
        "_batch_copies_note": copies_note,
    })


def render_classic_bill_html(ctx: BillContext, settings: Dict[str, Any] | None = None) -> str:
    """Entry point used by bill_config.render_bill_html."""
    return render_classic_html(ctx, settings)
