"""Original side-by-side Customer / Store copy tax invoice."""
from __future__ import annotations

from typing import Any, Dict

from core.bill_config import BillContext


def _esc(text: Any) -> str:
    return (
        str(text or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def render_legacy_bill_html(ctx: BillContext, settings: Dict[str, Any]) -> str:
    contact_parts = []
    if ctx.phone:
        contact_parts.append(f"Ph: {_esc(ctx.phone)}")
    if ctx.email:
        contact_parts.append(f"Email: {_esc(ctx.email)}")
    contact_line = "  |  ".join(contact_parts)

    total_outstanding = round(
        float(getattr(ctx, "previous_due", 0) or 0)
        + float(getattr(ctx, "due_amount", 0) or 0),
        2,
    )
    if total_outstanding > 0:
        due_line = f"Due as per Date : &nbsp;&#8377;{total_outstanding:.2f}"
    else:
        due_line = "Due as per Date : &nbsp;Nil"

    item_rows_html = ""
    for i, it in enumerate(ctx.items):
        exp = it.expiry
        if exp and len(str(exp)) >= 7 and str(exp)[4] == "-":
            parts = str(exp).split("-")
            exp = f"{parts[2]}/{parts[1]}/{parts[0][2:]}"
        qty = float(it.qty or 0)
        qty_s = str(int(qty)) if qty == int(qty) else f"{qty:.2f}"
        item_rows_html += f"""
            <tr>
                <td class="c">{i + 1}</td>
                <td class="l">{_esc(it.name)}</td>
                <td class="c">{_esc(it.batch)}</td>
                <td class="c">{_esc(getattr(it, 'manufacturer', '') or '')}</td>
                <td class="c">{_esc(exp)}</td>
                <td class="r">&#8377;{float(it.rate or 0):.2f}</td>
                <td class="c">{qty_s}</td>
                <td class="r">&#8377;{float(it.amount or 0):.2f}</td>
            </tr>"""
    item_rows_html += '<tr class="spacer-row"><td colspan="8"></td></tr>'

    def bill_block(label: str) -> str:
        return f"""
<div class="bill">
  <div class="copy-label">{label}</div>
  <div class="tax-invoice">TAX INVOICE &nbsp;&mdash;&nbsp; Bill No : {_esc(ctx.bill_no)}</div>
  <div class="info-row">
    <div class="info-left">
      <div>Name &nbsp;&nbsp;&nbsp;&nbsp;: &nbsp;{_esc(ctx.cust_name)}</div>
      <div>Contact &nbsp;: &nbsp;{_esc(ctx.cust_phone)}</div>
      {"<div>Address : &nbsp;" + _esc(ctx.cust_addr) + "</div>" if ctx.cust_addr else ""}
      <div>Date &nbsp;&nbsp;&nbsp;&nbsp;: &nbsp;{_esc(ctx.bill_date)}</div>
      <div>Payment &nbsp;: &nbsp;{_esc(ctx.pay_mode)}</div>
    </div>
    <div class="info-center">
      {f'<img src="{ctx.logo_src}" style="max-height:16mm;max-width:26mm;object-fit:contain;" alt="logo">' if ctx.logo_src else ''}
    </div>
    <div class="info-right">
      <div class="store-name">{_esc(ctx.store_name)}</div>
      {"<div>" + _esc(ctx.address) + "</div>" if ctx.address else ""}
      {"<div>" + contact_line + "</div>" if contact_line else ""}
      {"<div>GSTIN: " + _esc(ctx.gstin) + "</div>" if ctx.gstin else ""}
      {"<div>DL No: " + _esc(ctx.dl_no) + "</div>" if ctx.dl_no else ""}
      {"<div>FSSAI: " + _esc(ctx.fssai) + "</div>" if getattr(ctx, "show_fssai_on_bill", False) and ctx.fssai else ""}
    </div>
  </div>
  <table>
    <thead>
      <tr>
        <th class="c" style="width:5%">Sr</th>
        <th class="l" style="width:28%">Item Name</th>
        <th class="c" style="width:10%">Batch No</th>
        <th class="c" style="width:12%">Mfg</th>
        <th class="c" style="width:10%">Exp</th>
        <th class="r" style="width:12%">Rate</th>
        <th class="c" style="width:7%">Qty</th>
        <th class="r" style="width:16%">Amount</th>
      </tr>
    </thead>
    <tbody>{item_rows_html}</tbody>
  </table>
  <div class="totals-row">
    <table class="totals-table">
      <tr>
        <td>Sub Total</td>
        <td class="r">&#8377;{ctx.sub_total:.2f}</td>
        <td style="width:6mm"></td>
        <td>Total Amount</td>
        <td class="r">&#8377;{ctx.grand_total:.2f}</td>
      </tr>
      {f'''<tr>
        <td>Discount</td>
        <td class="r">&#8377;{ctx.discount:.2f}</td>
        <td></td>
        <td>Amount Paid</td>
        <td class="r">&#8377;{ctx.amount_paid:.2f}</td>
      </tr>''' if settings.get("show_discount", True) and ctx.discount > 0 else f'''<tr>
        <td></td><td></td><td></td>
        <td>Amount Paid</td>
        <td class="r">&#8377;{ctx.amount_paid:.2f}</td>
      </tr>'''}
    </table>
  </div>
  <div class="due-row">
    <span>{due_line}</span>
    {f'<span>GST (Incl.) : &#8377;{ctx.gst_amount:.2f}</span>' if settings.get("show_gst", True) and ctx.gst_enabled and ctx.gst_amount > 0 else ''}
  </div>
  <div class="sig-row">
    <div>Customer Signature : ___________</div>
    <div>Authorised Signatory</div>
  </div>
  <div class="footer-row">
    <div>All Subject to Sangrampur Jurisdiction</div>
    <div>सातपुडा मेडिकल</div>
  </div>
</div>"""

    return f"""<!DOCTYPE html>
<html lang="mr">
<head>
<meta charset="UTF-8">
<title>Bill - {_esc(ctx.bill_no)}</title>
<style>
  @page {{ size: A4 landscape; margin: 0; }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    font-family: 'Nirmala UI', 'Mangal', Arial, sans-serif;
    font-size: 7.5pt; color: #000;
    display: flex; flex-direction: column;
    margin: 0; padding: 0;
  }}
  .legacy-sheet {{
    display: flex; flex-direction: row;
    width: 297mm; height: 210mm;
    overflow: hidden; padding: 6px;
    background: #fff;
  }}
  .bill {{
    width: 144mm; height: 100%; padding: 3mm 4mm;
    display: flex; flex-direction: column; overflow: hidden; flex-shrink: 0;
  }}
  .cut-line {{
    width: 12px; border-left: 1pt dashed #888;
    height: 210mm; flex-shrink: 0; margin: 0 6px;
  }}
  .copy-label {{
    text-align: center; font-size: 6.5pt; color: #555;
    margin-bottom: 0.5mm; letter-spacing: 0.5pt; text-transform: uppercase;
  }}
  .tax-invoice {{
    text-align: center; font-size: 8pt; font-weight: bold;
    padding-bottom: 0.8mm; border-bottom: 0.5pt solid #000;
    margin-bottom: 0.8mm; flex-shrink: 0;
  }}
  .info-row {{
    display: flex; align-items: center;
    border-bottom: 0.5pt solid #000; padding: 0.8mm 0; flex-shrink: 0;
  }}
  .info-left {{ flex: 1; font-size: 7pt; line-height: 1.35; }}
  .info-center {{ flex: 0 0 auto; text-align: center; padding: 0 1.5mm; }}
  .info-right {{ flex: 1; font-size: 7pt; line-height: 1.35; text-align: right; }}
  .info-right .store-name {{ font-size: 9pt; font-weight: bold; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 6.5pt; flex: 1; min-height: 0; }}
  thead tr th {{
    border-top: 0.5pt solid #000; border-bottom: 0.5pt solid #000;
    padding: 0.4mm 0.8mm; font-weight: bold; white-space: nowrap;
  }}
  tbody tr td {{ padding: 0mm 0.8mm 8px 0.8mm; vertical-align: middle; }}
  tbody tr.spacer-row td {{ height: 100%; padding: 0; border: none; }}
  .totals-row {{ flex-shrink: 0; padding: 0.4mm 0; border-top: 0.5pt solid #000; }}
  .totals-table {{ width: 100%; font-size: 7pt; height: auto; flex: none; }}
  .totals-table td {{ padding: 0.25mm 0.8mm; }}
  .due-row {{
    display: flex; justify-content: space-between;
    font-size: 7pt; font-weight: bold; padding: 0.6mm 0;
    border-top: 0.5pt solid #000; flex-shrink: 0;
  }}
  .sig-row {{
    display: flex; justify-content: space-between;
    padding: 1mm 0 0.4mm 0; font-size: 7pt; flex-shrink: 0;
  }}
  .footer-row {{
    display: flex; justify-content: space-between; font-size: 6pt;
    padding-top: 0.4mm; border-top: 0.5pt solid #000; flex-shrink: 0;
  }}
  .c {{ text-align: center; }}
  .r {{ text-align: right; }}
  .l {{ text-align: left; }}
  .print-toolbar {{
    display: flex; justify-content: center; align-items: center; gap: 12px;
    padding: 14px 16px; background: #1565c0; position: sticky; top: 0; z-index: 9999;
    box-shadow: 0 2px 8px rgba(0,0,0,0.25);
  }}
  .print-toolbar .hint {{ color: #fff; font-size: 11pt; }}
  .print-btn {{
    padding: 10px 28px; font-size: 13pt; font-weight: bold;
    background: #fff; color: #1565c0; border: none; border-radius: 6px; cursor: pointer;
  }}
  @media print {{
    .print-toolbar {{ display: none !important; }}
    @page {{ size: A4 landscape; margin: 0; }}
    body {{ width: 297mm; height: 210mm; overflow: hidden; }}
    .legacy-sheet {{ padding: 0; }}
  }}
  @media screen {{
    body {{ background: #e8e8e8; overflow: auto; min-height: 100vh; }}
    .legacy-sheet {{ margin: 12px auto; box-shadow: 0 2px 12px rgba(0,0,0,0.15); }}
  }}
</style>
</head>
<body>
<div class="print-toolbar">
  <span class="hint">A4 Landscape · margins minimum · 100% scale</span>
  <button type="button" class="print-btn" onclick="window.print()">&#128424; Print Bill</button>
</div>
<div class="legacy-sheet">
{bill_block("Customer Copy")}
<div class="cut-line"></div>
{bill_block("Store Copy")}
</div>
</body>
</html>"""
