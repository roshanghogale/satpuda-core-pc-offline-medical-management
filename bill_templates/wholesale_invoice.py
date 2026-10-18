"""The wholesale TAX INVOICE (core/wholesale_sale.py), A4 portrait or A5 landscape.

A B2B invoice to another medical shop: the shop's GSTIN and drug licence numbers, the
buyer's, place of supply, the lines with HSN / batch / expiry / free packs / rates without
GST, the HSN-wise tax summary, the tax totals and the grand total in words, bank details
when the profile has them, and the signatory box. Black on white like every bill; one
document however many lines -- the table header repeats on each printed page.

Input is ``invoice_context`` (a plain dict), so it renders without a database.
"""
from __future__ import annotations

from html import escape
from typing import Any


def _e(v: Any) -> str:
    return escape(str(v if v is not None else ""))


def _m(v: Any) -> str:
    try:
        return f"{float(v or 0):,.2f}"
    except (TypeError, ValueError):
        return "0.00"


def _pct(v: Any) -> str:
    try:
        x = float(v or 0)
    except (TypeError, ValueError):
        return ""
    return f"{x:g}"


def _qty(v: Any) -> str:
    try:
        x = float(v or 0)
    except (TypeError, ValueError):
        return ""
    return f"{x:g}" if x else ""


def _exp(raw: Any) -> str:
    try:
        from core.expiry_text import expiry_display

        out = expiry_display(raw)
        if out:
            return out
    except Exception:
        pass
    s = str(raw or "")
    if len(s) >= 7 and s[4] == "-":
        return f"{s[5:7]}/{s[2:4]}"
    return s


def _date(raw: Any) -> str:
    s = str(raw or "")[:10]
    if len(s) == 10 and s[4] == "-" and s[7] == "-":
        return f"{s[8:10]}-{s[5:7]}-{s[0:4]}"
    return s


def _state(name: str, code: str) -> str:
    if not code:
        return ""
    return f"{_e(name)} ({_e(code)})" if name else _e(code)


def _css(paper: str) -> str:
    a5 = paper == "A5"
    page = "size: 210mm 148mm; margin: 5mm 6mm;" if a5 else "size: A4 portrait; margin: 8mm 9mm;"
    base = "7.2pt" if a5 else "8.6pt"
    title = "11pt" if a5 else "14pt"
    shop = "12pt" if a5 else "16pt"
    return f"""
@page {{ {page} }}
* {{ box-sizing: border-box; }}
html, body {{ margin: 0; padding: 0; background: #fff; color: #000; }}
body {{ font-family: Arial, 'Segoe UI', Helvetica, sans-serif; font-size: {base};
       -webkit-print-color-adjust: exact; print-color-adjust: exact; }}
.inv {{ border: 1px solid #000; }}
.title {{ text-align: center; font-weight: 700; font-size: {title}; letter-spacing: 2px;
          border-bottom: 1px solid #000; padding: 2px 0; }}
.title small {{ font-size: {base}; font-weight: 400; letter-spacing: 0; float: right;
                padding-right: 4px; }}
.row {{ display: flex; border-bottom: 1px solid #000; }}
.cell {{ flex: 1; padding: 3px 5px; }}
.cell + .cell {{ border-left: 1px solid #000; }}
.shop-name {{ font-size: {shop}; font-weight: 700; }}
.lbl {{ font-weight: 700; }}
.kv {{ display: grid; grid-template-columns: auto 1fr; column-gap: 6px; row-gap: 1px; }}
.kv span:nth-child(odd) {{ font-weight: 700; white-space: nowrap; }}
.cap {{ font-weight: 700; text-decoration: underline; margin-bottom: 1px; }}
table {{ width: 100%; border-collapse: collapse; }}
thead {{ display: table-header-group; }}
tr {{ page-break-inside: avoid; }}
.items th, .items td {{ border-right: 1px solid #000; padding: 1.5px 3px; vertical-align: top; }}
.items th:last-child, .items td:last-child {{ border-right: 0; }}
.items th {{ border-bottom: 1px solid #000; font-weight: 700; text-align: center; }}
.items td.n, .sum td.n {{ text-align: right; white-space: nowrap; }}
.items td.c {{ text-align: center; white-space: nowrap; }}
.items tbody tr.filler td {{ height: 100%; }}
.items tfoot td {{ border-top: 1px solid #000; font-weight: 700; padding: 2px 3px; }}
.sum th, .sum td {{ border: 1px solid #000; padding: 1.5px 3px; }}
.sum th {{ font-weight: 700; text-align: center; }}
.tot td {{ padding: 1.5px 5px; }}
.tot td.n {{ text-align: right; }}
.tot tr.grand td {{ border-top: 1px solid #000; font-weight: 700; font-size: 1.15em; }}
.words {{ padding: 3px 5px; border-bottom: 1px solid #000; }}
.sign {{ display: flex; min-height: {"14mm" if a5 else "20mm"}; }}
.sign .cell {{ display: flex; flex-direction: column; justify-content: space-between; }}
.sign .right {{ text-align: right; }}
.muted {{ font-size: 0.92em; }}
"""


def _items_table(inv: dict) -> str:
    rows = []
    for i, it in enumerate(inv.get("lines") or [], start=1):
        disc = _pct(it.get("disc_pct")) if float(it.get("disc_pct") or 0) else ""
        if float(it.get("scheme_pct") or 0):
            disc = (disc or "0") + " + " + _pct(it.get("scheme_pct")) + " sch"
        rows.append(
            "<tr>"
            f"<td class='c'>{i}</td>"
            f"<td>{_e(it.get('name'))}</td>"
            f"<td class='c'>{_e(it.get('hsn_code'))}</td>"
            f"<td class='c'>{_e(it.get('batch'))}</td>"
            f"<td class='c'>{_e(_exp(it.get('expiry')))}</td>"
            f"<td class='n'>{_qty(it.get('qty'))}</td>"
            f"<td class='n'>{_qty(it.get('free'))}</td>"
            f"<td class='n'>{_m(it.get('rate'))}</td>"
            f"<td class='n'>{_e(disc)}</td>"
            f"<td class='n'>{_m(it.get('taxable'))}</td>"
            f"<td class='n'>{_pct(it.get('gst_percent'))}</td>"
            f"<td class='n'>{_m(it.get('gst_amt'))}</td>"
            f"<td class='n'>{_m(it.get('amount'))}</td>"
            "</tr>"
        )
    lines = inv.get("lines") or []
    qty = sum(float(it.get("qty") or 0) for it in lines)
    free = sum(float(it.get("free") or 0) for it in lines)
    taxable = sum(float(it.get("taxable") or 0) for it in lines)
    gst = sum(float(it.get("gst_amt") or 0) for it in lines)
    amount = sum(float(it.get("amount") or 0) for it in lines)
    return (
        "<table class='items'><thead><tr>"
        "<th style='width:3%'>Sr</th><th>Product</th><th style='width:7%'>HSN</th>"
        "<th style='width:8%'>Batch</th><th style='width:5%'>Exp</th>"
        "<th style='width:4.5%'>Qty</th><th style='width:4.5%'>Free</th>"
        "<th style='width:7%'>Rate</th><th style='width:6.5%'>Disc%</th>"
        "<th style='width:8.5%'>Taxable</th><th style='width:4.5%'>GST%</th>"
        "<th style='width:7.5%'>GST Amt</th><th style='width:9%'>Amount</th>"
        "</tr></thead><tbody>"
        + "".join(rows)
        + "</tbody><tfoot><tr>"
        f"<td colspan='5'>Total ({len(lines)} item{'s' if len(lines) != 1 else ''})</td>"
        f"<td class='n'>{_qty(qty)}</td><td class='n'>{_qty(free)}</td><td></td><td></td>"
        f"<td class='n'>{_m(taxable)}</td><td></td><td class='n'>{_m(gst)}</td>"
        f"<td class='n'>{_m(amount)}</td></tr></tfoot></table>"
    )


def _hsn_table(inv: dict) -> str:
    intra = bool(inv.get("intra_state", True))
    head = ("<th>HSN</th><th>Taxable</th>"
            + ("<th>CGST %</th><th>CGST</th><th>SGST %</th><th>SGST</th>" if intra
               else "<th>IGST %</th><th>IGST</th>")
            + "<th>Total Tax</th>")
    body = []
    t_tax = t_c = t_s = t_i = t_all = 0.0
    for h in inv.get("hsn_summary") or []:
        t_tax += float(h.get("taxable") or 0)
        t_c += float(h.get("cgst") or 0)
        t_s += float(h.get("sgst") or 0)
        t_i += float(h.get("igst") or 0)
        t_all += float(h.get("tax") or 0)
        tax_cells = (
            f"<td class='n'>{_pct(h.get('cgst_rate'))}</td><td class='n'>{_m(h.get('cgst'))}</td>"
            f"<td class='n'>{_pct(h.get('sgst_rate'))}</td><td class='n'>{_m(h.get('sgst'))}</td>"
            if intra else
            f"<td class='n'>{_pct(h.get('igst_rate'))}</td><td class='n'>{_m(h.get('igst'))}</td>"
        )
        body.append(
            f"<tr><td>{_e(h.get('hsn_code') or '-')}</td><td class='n'>{_m(h.get('taxable'))}</td>"
            f"{tax_cells}<td class='n'>{_m(h.get('tax'))}</td></tr>"
        )
    foot = (
        f"<tr><th>Total</th><td class='n'><b>{_m(t_tax)}</b></td>"
        + (f"<td></td><td class='n'><b>{_m(t_c)}</b></td><td></td><td class='n'><b>{_m(t_s)}</b></td>"
           if intra else f"<td></td><td class='n'><b>{_m(t_i)}</b></td>")
        + f"<td class='n'><b>{_m(t_all)}</b></td></tr>"
    )
    return f"<table class='sum'><thead><tr>{head}</tr></thead><tbody>{''.join(body)}{foot}</tbody></table>"


def _totals(inv: dict) -> str:
    intra = bool(inv.get("intra_state", True))
    rows = [("Taxable Value", inv.get("taxable_total"))]
    if intra:
        rows += [("CGST", inv.get("cgst_total")), ("SGST", inv.get("sgst_total"))]
    else:
        rows += [("IGST", inv.get("igst_total"))]
    rows += [("Round Off", inv.get("round_off"))]
    body = "".join(f"<tr><td>{_e(k)}</td><td class='n'>{_m(v)}</td></tr>" for k, v in rows)
    body += f"<tr class='grand'><td>Grand Total</td><td class='n'>{_m(inv.get('grand_total'))}</td></tr>"
    return f"<table class='tot'>{body}</table>"


def render_wholesale_invoice_html(inv: dict, *, paper: str = "A4") -> str:
    paper = "A5" if str(paper or "").upper() == "A5" else "A4"
    shop = inv.get("shop") or {}
    buyer = inv.get("buyer") or {}

    shop_lines = [f"<div class='shop-name'>{_e(shop.get('name'))}</div>"]
    if shop.get("address"):
        shop_lines.append(f"<div>{_e(shop.get('address'))}</div>")
    kv = []
    if shop.get("phone"):
        kv.append(("Phone", _e(shop.get("phone"))))
    if shop.get("gstin"):
        kv.append(("GSTIN", _e(shop.get("gstin"))))
    if shop.get("dl_numbers"):
        kv.append(("DL No.", _e(shop.get("dl_numbers"))))
    if shop.get("state"):
        kv.append(("State", _state(shop.get("state_name", ""), shop.get("state", ""))))
    shop_lines.append(
        "<div class='kv'>" + "".join(f"<span>{k}</span><span>{v}</span>" for k, v in kv) + "</div>"
    )

    meta = [
        ("Invoice No.", _e(inv.get("bill_no"))),
        ("Date", _e(_date(inv.get("bill_date")))),
    ]
    if inv.get("due_date"):
        meta.append(("Due Date", _e(_date(inv.get("due_date")))))
    meta.append(("Place of Supply",
                 _state(inv.get("place_of_supply_name", ""), inv.get("place_of_supply", ""))))
    for key, label in (("transport", "Transport"), ("lr_no", "LR No."),
                       ("eway_bill_no", "E-way Bill No.")):
        if str(inv.get(key) or "").strip():
            meta.append((label, _e(inv.get(key))))
    meta_html = "<div class='kv'>" + "".join(
        f"<span>{k}</span><span>{v}</span>" for k, v in meta) + "</div>"

    bkv = []
    if buyer.get("address"):
        bkv.append(("Address", _e(buyer.get("address"))))
    if buyer.get("phone"):
        bkv.append(("Phone", _e(buyer.get("phone"))))
    bkv.append(("GSTIN", _e(buyer.get("gstin") or "Unregistered")))
    if buyer.get("dl_numbers"):
        bkv.append(("DL No.", _e(buyer.get("dl_numbers"))))
    if buyer.get("state_code"):
        bkv.append(("State", _state(buyer.get("state_name", ""), buyer.get("state_code", ""))))
    buyer_html = (
        "<div class='cap'>Bill To</div>"
        f"<div class='lbl' style='font-size:1.15em'>{_e(buyer.get('name'))}</div>"
        "<div class='kv'>" + "".join(f"<span>{k}</span><span>{v}</span>" for k, v in bkv) + "</div>"
    )

    bank = shop.get("bank") or []
    bank_html = ""
    if bank:
        bank_html = "<div class='cap'>Bank Details</div><div class='kv'>" + "".join(
            f"<span>{_e(k)}</span><span>{_e(v)}</span>" for k, v in bank) + "</div>"

    title = "TAX INVOICE"
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>{_e(title)} {_e(inv.get('bill_no'))}</title>
<style>{_css(paper)}</style></head>
<body class="paper-{paper.lower()}">
<div class="inv">
  <div class="title">{title}<small>Original for Recipient</small></div>
  <div class="row">
    <div class="cell" style="flex:1.35">{''.join(shop_lines)}</div>
    <div class="cell">{meta_html}</div>
  </div>
  <div class="row"><div class="cell">{buyer_html}</div></div>
  <div class="row" style="display:block">{_items_table(inv)}</div>
  <div class="row">
    <div class="cell" style="flex:1.6">{_hsn_table(inv)}</div>
    <div class="cell">{_totals(inv)}</div>
  </div>
  <div class="words"><span class="lbl">Amount in words:</span> {_e(inv.get('amount_in_words'))}</div>
  <div class="row sign" style="border-bottom:0">
    <div class="cell">
      <div>{bank_html}</div>
      <div class="muted">Goods once sold will be taken back only as per the terms agreed.
        Subject to local jurisdiction.<br><br>Receiver's Signature</div>
    </div>
    <div class="cell right">
      <div class="lbl">For {_e(shop.get('name'))}</div>
      <div>Authorised Signatory</div>
    </div>
  </div>
</div>
</body></html>
"""
