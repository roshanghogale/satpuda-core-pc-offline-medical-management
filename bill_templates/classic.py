"""
Style 1: GST Vertical Rotated Bill.

Matches the photographed pharmacy GST receipt: thick ruled box, 50/50 header,
dense medicine grid, one-line GST message, and fixed totals/signature block.
"""
from __future__ import annotations

from typing import Any, Dict

from core.bill_config import BillContext
from core.bill_page_config import (
    PRINT_TOOLBAR_CSS,
    get_classic_layout,
    print_toolbar_html,
)
from core.bill_print_settings import load_bill_print_settings
from core.bill_render_utils import esc, fmt_expiry_mm_yy


def _density_class(count: int) -> str:
    if count >= 18:
        return "density-max"
    if count >= 12:
        return "density-tight"
    if count >= 8:
        return "density-compact"
    return "density-normal"


def _gst_line(ctx, settings) -> str:
    if not settings.get("show_gst", True) or not ctx.gst_enabled or ctx.gst_amount <= 0:
        return "HAVE A NICE DAY"

    taxable = max(0.0, round(float(ctx.grand_total or 0) - float(ctx.gst_amount or 0), 2))
    rates = sorted({float(item.gst_percent or 0) for item in ctx.items if item.gst_percent})
    half_tax = round(float(ctx.gst_amount or 0) / 2, 2)
    if len(rates) == 1 and rates[0] > 0:
        half_rate = rates[0] / 2
        return (
            f"GST {taxable:.2f}*{half_rate:g}+{half_rate:g}%="
            f"{half_tax:.2f}SGST+{half_tax:.2f}CGST, HAVE A NICE DAY"
        )
    return f"GST {taxable:.2f} = {half_tax:.2f}SGST + {half_tax:.2f}CGST, HAVE A NICE DAY"


def _medicine_columns(settings):
    cols = [
        ("sr", "Sr.N", "c", "6%"),
        ("name", "Name of Medicine", "l", "37%"),
    ]
    if settings.get("show_batch", True):
        cols.append(("batch", "Batch no", "c", "15%"))
    if settings.get("show_expiry", True):
        cols.append(("expiry", "Exp", "c", "8%"))
    cols.extend([
        ("qty", "QTY", "c", "7%"),
        ("mrp", "MRP", "r", "13%"),
        ("amount", "Amount", "r", "14%"),
    ])
    return cols


def _item_rows(ctx, settings) -> str:
    cols = _medicine_columns(settings)
    rows = ""
    for index, item in enumerate(ctx.items):
        mrp_val = float(item.mrp or item.rate or 0)
        cells = {
            "sr": str(index + 1),
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
    if not settings.get("show_doctor", True):
        return ""
    return f"""
          <tr><td>Dr.NAME</td><td>:</td><td>{esc(ctx.doctor_name)}</td></tr>
          <tr><td>Dr.Reg.</td><td>:</td><td>{esc(ctx.cust_addr)}</td></tr>"""


def _terms(settings) -> str:
    return ""


def _signature(ctx, settings) -> str:
    if not settings.get("show_signature", True):
        return ""
    return f"""
        <div class="for-shop">For {esc(ctx.store_name.upper())}</div>
        <div class="sign-label">SIGN OF Q.P.</div>"""


def _profile_lines(ctx) -> str:
    fssai = getattr(ctx, "fssai", "")
    show_fssai = bool(getattr(ctx, "show_fssai_on_bill", False))
    return "".join([
        f'<div>{esc(ctx.address)}</div>' if ctx.address else "",
        f'<div>E-Mail : {esc(ctx.email)}</div>' if ctx.email else "",
        f'<div>Phone : {esc(ctx.phone)}</div>' if ctx.phone else "",
        f'<div>GSTIN : {esc(ctx.gstin)}</div>' if ctx.gstin else "",
        f'<div>DL.No. : {esc(ctx.dl_no)}</div>' if ctx.dl_no else "",
        f'<div>FSSAI : {esc(fssai)}</div>' if show_fssai and fssai else "",
    ])


def _bill_copy(ctx, copy_label: str, settings) -> str:
    bill_date = ctx.bill_date_landscape or ctx.bill_date
    blessing = getattr(ctx, "blessing_line", None) or settings.get(
        "blessing_line", "SHREE GANESHAY NAMAH"
    )
    gst_total_row = (
        f'<tr><td>GST</td><td class="r">{ctx.gst_amount:.2f}</td></tr>'
        if settings.get("show_gst", True) and ctx.gst_enabled and ctx.gst_amount > 0
        else ""
    )
    show_disc = settings.get("show_discount", True)
    less_value = ctx.discount if (show_disc and ctx.discount > 0) else 0.0
    copy_id_html = f'<div class="copy-id">{esc(copy_label)}</div>' if copy_label else ""

    return f"""
  <section class="copy-shell">
    {copy_id_html}
    <div class="rotated-invoice">
      <div class="top-grid">
        <div class="shop-panel">
          <div class="shop-name">{esc(ctx.store_name.upper())}</div>
          {_profile_lines(ctx)}
        </div>
        <div class="invoice-panel">
          <div class="inv-blessing">{esc(blessing)}</div>
          <div class="inv-title">GST INVOICE&nbsp; {esc(ctx.pay_mode).upper()}</div>
          <table class="meta-table">
            <tbody>
              <tr><td>BILL NO.</td><td>:</td><td>{esc(ctx.bill_no)}</td></tr>
              <tr><td>Date</td><td>:</td><td>{esc(bill_date)}</td></tr>
              <tr><td>Pt.NAME</td><td>:</td><td>{esc(ctx.cust_name)}</td></tr>
              <tr><td>Pt.ADD.</td><td>:</td><td>{esc(ctx.cust_addr)}</td></tr>
              {_doctor_rows(ctx, settings)}
            </tbody>
          </table>
        </div>
      </div>

      <div class="table-zone">{_medicine_table(ctx, settings)}</div>

      <div class="bottom-zone">
        <div class="gst-strip">{esc(_gst_line(ctx, settings))}</div>
        <div class="totals-cell">
          <table class="sum-table">
            {f'<tr><td>LESS</td><td class="r">{less_value:.2f}</td></tr>' if less_value > 0 else ''}
            {gst_total_row}
            <tr class="total"><td>Total</td><td class="r">{ctx.grand_total:.2f}</td></tr>
          </table>
        </div>
        <div class="terms-cell">
          <div class="wish">I WISH FOR YOUR <b>SPEEDY RECOVERY.</b></div>
          {_terms(settings)}
        </div>
        <div class="signature-cell">{_signature(ctx, settings)}</div>
      </div>
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


def render_classic_html(ctx: BillContext, settings: Dict[str, Any] | None = None) -> str:
    settings = settings or load_bill_print_settings()
    layout = get_classic_layout(settings.get("paper_size", "A5"))
    paper = layout["paper_size"]
    if paper == "A5":
        font_scale = max(0.75, min(1.1, settings.get("font_size_pct", 100) / 100 * 0.92))
    else:
        font_scale = max(0.8, min(1.2, settings.get("font_size_pct", 100) / 100))
    border = max(0.5, min(2.0, float(settings.get("border_thickness", 1.0))))
    outer_border = round(border * 1.45, 2)
    grid_border = round(max(0.65, border * 0.9), 2)
    density = _density_class(len(ctx.items))
    # A5 sheet always prints two bills side by side
    copies = 2 if paper == "A5" else max(1, min(3, int(settings.get("copies", 2))))
    body_content = _bill_copy(ctx, "", settings)
    for _ in range(1, copies):
        body_content += _separator() + _bill_copy(ctx, "", settings)
    toolbar = print_toolbar_html(layout["toolbar_hint"])

    return f"""<!DOCTYPE html>
<html lang="en" class="preview-mode">
<head>
<meta charset="UTF-8">
<title>Bill - {esc(ctx.bill_no)}</title>
<style>
  {layout["page_css"]}
  {PRINT_TOOLBAR_CSS}
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
    align-items: stretch;
    padding: {layout["body_pad"]};
    -webkit-print-color-adjust: exact;
    print-color-adjust: exact;
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
  .top-grid {{
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
    gap: 1mm;
    padding: 1mm 0.5mm;
    background: #fff;
    font-size: {5.3 * font_scale:.2f}pt;
    font-weight: 900;
    text-align: center;
    white-space: nowrap;
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
<body class="{density}">
{toolbar}
<div class="bill-page">{body_content}</div>
</body>
</html>"""


def render_classic_bill_html(ctx: BillContext, settings: Dict[str, Any] | None = None) -> str:
    """Entry point used by bill_config.render_bill_html."""
    return render_classic_html(ctx, settings)
