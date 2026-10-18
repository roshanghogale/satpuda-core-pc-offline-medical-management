"""The wholesale GST INVOICE (core/wholesale_sale.py): always A5 landscape, one bordered box.

Laid out like the shop's own wholesale invoice (owner, 18 Oct 2026):

    +------------------------+---------------+------------------------+
    | SHOP NAME (large)      |  GST INVOICE  | Buyer Name             |
    | address, Phone, D.L.,  |   (CREDIT)    | M/s PARTY, address,    |
    | GSTIN                  | Invoice No.   | PHONE NO., D.L.No.,    |
    |                        | Date, Due Date| Party GSTIN            |
    +--+-------+----+----+---+-----+----+---+---+----+----+-----+-----+----------+
    |S.|PRODUCT|MFG.|PACK|HSN|BATCH|EXP.|MRP|QTY|FREE|RATE|SGST%|CGST%|GROSS AMT.|
    |  | the lines, then any Return rows; the column rules run down to the footer |
    +------------------------+---------------+------------------------+
    | GST% TAXABLE SGST CGST | Total Item /  | SUB TOTAL              |
    | 0 / 5 / 12 / 18 / 28   | Quantity      | Discount, Bill Disc    |
    | Rs. <words> only       | BANK DETAILS  | SGST / CGST Value      |
    | Declaration ...        | LEDGER BAL.   | CR/DR, CR/DR NOTE      |
    |                        |               | PARTY TOTAL            |
    |                        | Sign of Customer      For SHOP (signed)|
    +------------------------+---------------+------------------------+

On an A5 page the invoice is the page; on A4 portrait two invoices share a sheet, top and
bottom halves with a dashed cut line between (each half is exactly an A5 landscape). A bill
with more rows than one box holds goes on over more boxes, the totals on the last.

Input is ``invoice_context`` (a plain dict), so it renders without a database.
"""
from __future__ import annotations

from html import escape
from typing import Any, Iterable

# Item-table rows one box holds (lines and Return rows alike, one text line each).
ROWS_PER_PAGE = 18

# Fixed heights inside the box (mm), so the item table takes exactly what is left.
_HEAD_MM = 27.0
_THEAD_MM = 4.6
_FOOT_MM = 39.0
_ROW_MM = 3.45
_STANDARD_RATES = (0.0, 5.0, 12.0, 18.0, 28.0)


def _e(v: Any) -> str:
    return escape(str(v if v is not None else ""))


def _f(v: Any) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _m(v: Any) -> str:
    return f"{_f(v):.2f}"


def _mb(v: Any) -> str:
    """Money, blank when zero (the GST table and the line cells)."""
    x = _f(v)
    return f"{x:.2f}" if abs(x) >= 0.005 else ""


def _qty(v: Any) -> str:
    x = _f(v)
    if not x:
        return ""
    return str(int(x)) if x == int(x) else f"{x:g}"


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


def _words(inv: dict) -> str:
    """ "Rs. Four Hundred Thirty Six only" for the PARTY TOTAL."""
    text = str(inv.get("party_total_words") or inv.get("amount_in_words") or "").strip()
    minus = text.startswith("Minus ")
    if minus:
        text = text[len("Minus "):]
    if text.startswith("Rupees "):
        text = text[len("Rupees "):]
    if text.endswith(" Only"):
        text = text[: -len(" Only")]
    return f"Rs. {'Minus ' if minus else ''}{text} only" if text else ""


def _shop_name_pt(name: str) -> float:
    """The shop's name as large as the left header column takes on one line."""
    n = max(1, len(name or ""))
    return max(10.0, min(18.0, 215.0 / (n * 0.68)))


# ── the parts of one box ──────────────────────────────────────────────────────

def _columns(intra: bool) -> list[tuple[str, str, str, str]]:
    """(key, header, align, width) of the item table; the name takes what is left."""
    cols = [
        ("sr", "S.", "c", "3.2%"), ("name", "PRODUCT NAME", "l", ""),
        ("mfg", "MFG.", "l", "7.8%"), ("pack", "PACK", "l", "5.6%"),
        ("hsn", "HSN", "c", "6.4%"), ("batch", "BATCH", "l", "8.6%"),
        ("exp", "EXP.", "c", "5%"), ("mrp", "MRP", "r", "6.2%"),
        ("qty", "QTY", "r", "4.4%"), ("free", "FREE", "r", "4.2%"),
        ("rate", "RATE", "r", "6.6%"),
    ]
    if intra:
        cols += [("sgst", "SGST%", "r", "5%"), ("cgst", "CGST%", "r", "5%")]
    else:
        cols += [("igst", "IGST%", "r", "6%")]
    cols.append(("gross", "GROSS AMT.", "r", "8.8%"))
    return cols


def _cls(key: str, align: str) -> str:
    return {"l": "t" if key == "name" else "l", "c": "c", "r": "n"}[align]


def _line_cells(it: dict, sr: int) -> dict[str, str]:
    pct = _f(it.get("gst_percent"))
    gross = it.get("gross")
    if gross is None:
        gross = _f(it.get("qty")) * _f(it.get("rate"))
    return {
        "sr": str(sr),
        "name": _e(str(it.get("name") or "").upper()),
        "mfg": _e(str(it.get("mfg") or "").upper()),
        "pack": _e(it.get("pack")),
        "hsn": _e(it.get("hsn_code")),
        "batch": _e(it.get("batch")),
        "exp": _e(_exp(it.get("expiry"))),
        "mrp": _mb(it.get("mrp")),
        "qty": _qty(it.get("qty")),
        "free": _qty(it.get("free")),
        "rate": _m(it.get("rate")),
        "sgst": f"{pct / 2:.2f}",
        "cgst": f"{pct / 2:.2f}",
        "igst": f"{pct:.2f}",
        "gross": _m(gross),
    }


def _row_entries(inv: dict) -> list[tuple[str, Any]]:
    """Every item-table row in order: ("item", (line, sr)), then the Return rows."""
    out: list[tuple[str, Any]] = []
    for i, it in enumerate(inv.get("lines") or [], start=1):
        out.append(("item", (it, i)))
    for r in inv.get("returns") or []:
        out.append(("return", r))
    return out


def _return_row(r: Any, cols: list) -> str:
    """A return settled in this bill, as the retail bill prints it (bill_templates/classic.py
    _return_rows): the centred "Return SR.. date" row, the medicines that came back as minus
    lines, then the return's total."""
    from types import SimpleNamespace

    from core.bill_render_utils import return_row_label, return_total_label

    row = SimpleNamespace(**r) if isinstance(r, dict) else r
    amount = f"-{_f(getattr(row, 'amount', 0)):.2f}"
    kind = getattr(row, "kind", "")
    if kind == "return":
        return (f"<tr class='r ret-head'><td class='c' colspan='{len(cols)}'>"
                f"<b>{_e(return_row_label(row))}</b></td></tr>")
    if kind == "total":
        return (f"<tr class='r ret-total'><td class='rt' colspan='{len(cols) - 1}'>"
                f"<b>{_e(return_total_label(row))}</b></td>"
                f"<td class='n'><b>{amount}</b></td></tr>")
    expiry = getattr(row, "expiry", "")
    cells = {
        "sr": str(getattr(row, "sr", "") or ""),
        "name": _e(str(getattr(row, "name", "") or "").upper()),
        "batch": _e(getattr(row, "batch", "")),
        "exp": _e(_exp(expiry)) if expiry else "",
        "mrp": _mb(getattr(row, "mrp", 0)),
        "qty": _qty(getattr(row, "qty", 0)),
        "gross": amount,
    }
    return "<tr class='r ret-line'>" + "".join(
        f"<td class='{_cls(k, a)}'>{cells.get(k, '')}</td>" for k, _h, a, _w in cols) + "</tr>"


def _items_table(inv: dict, entries: Iterable[tuple[str, Any]]) -> str:
    cols = _columns(bool(inv.get("intra_state", True)))
    colgroup = "".join(f"<col style='width:{w}'>" if w else "<col>" for _k, _h, _a, w in cols)
    head = "".join(f"<th>{_e(h)}</th>" for _k, h, _a, _w in cols)
    body = []
    for kind, data in entries:
        if kind == "item":
            it, sr = data
            cells = _line_cells(it, sr)
            body.append("<tr class='r'>" + "".join(
                f"<td class='{_cls(k, a)}'>{cells.get(k, '')}</td>" for k, _h, a, _w in cols)
                + "</tr>")
        else:
            body.append(_return_row(data, cols))
    filler = "<tr class='filler'>" + "".join("<td></td>" for _ in cols) + "</tr>"
    return (f"<table class='items'><colgroup>{colgroup}</colgroup><thead><tr>{head}</tr></thead>"
            f"<tbody>{''.join(body)}{filler}</tbody></table>")


def _gst_table(inv: dict) -> str:
    """GST% | TAXABLE | SGST | CGST | TOTAL, a row for 0 / 5 / 12 / 18 / 28 (and any other
    rate on the bill); a zero prints blank. IGST in place of SGST / CGST for another state."""
    intra = bool(inv.get("intra_state", True))
    zero = {"taxable": 0.0, "cgst": 0.0, "sgst": 0.0, "igst": 0.0}
    rates: dict[float, dict[str, float]] = {r: dict(zero) for r in _STANDARD_RATES}
    for it in inv.get("lines") or []:
        b = rates.setdefault(round(_f(it.get("gst_percent")), 2), dict(zero))
        for k in zero:
            b[k] = round(b[k] + _f(it.get(k)), 2)
    head = ("<th>GST%</th><th>TAXABLE</th>"
            + ("<th>SGST</th><th>CGST</th>" if intra else "<th>IGST</th>") + "<th>TOTAL</th>")
    body = []
    for pct in sorted(rates):
        b = rates[pct]
        tax = b["sgst"] + b["cgst"] if intra else b["igst"]
        taxes = (f"<td class='n'>{_mb(b['sgst'])}</td><td class='n'>{_mb(b['cgst'])}</td>"
                 if intra else f"<td class='n'>{_mb(b['igst'])}</td>")
        body.append(f"<tr><td>{pct:.2f}</td><td class='n'>{_mb(b['taxable'])}</td>{taxes}"
                    f"<td class='n'>{_mb(b['taxable'] + tax)}</td></tr>")
    return f"<table class='gst'><thead><tr>{head}</tr></thead><tbody>{''.join(body)}</tbody></table>"


def _bank_lines(shop: dict) -> list[str]:
    """BANK DETAILS: the bank (branch in brackets), A/C NO., IFSC CODE -- from the profile's
    bank fields or the settings text (core/wholesale_sale.shop_profile)."""
    name = branch = ""
    rest: list[tuple[str, str]] = []
    for label, value in shop.get("bank") or []:
        low = str(label or "").strip().lower()
        if low.startswith("bank") and not name:
            name = str(value)
        elif low.startswith("branch") and not branch:
            branch = str(value)
        else:
            rest.append((str(label or ""), str(value)))
    out = []
    if name or branch:
        out.append(_e(name.upper()) + (f" ({_e(branch.upper())})" if branch else ""))
    for label, value in rest:
        key = label.strip().lower().replace(".", "").replace(" ", "")
        if key in ("a/cno", "acno", "accountno", "account", "a/c", "ac"):
            label = "A/C NO."
        elif key.startswith("ifsc"):
            label = "IFSC CODE"
        out.append(f"{_e(label.upper())} : {_e(value)}" if label else _e(value))
    return out


def _totals(inv: dict) -> str:
    intra = bool(inv.get("intra_state", True))
    ret = _f(inv.get("returns_total"))
    rows = [("SUB TOTAL", _m(inv.get("sub_total"))),
            ("Discount", _mb(inv.get("line_discount"))),
            ("Bill Disc", _mb(inv.get("bill_discount")))]
    if intra:
        rows += [("SGST Value", _mb(inv.get("sgst_total"))),
                 ("CGST Value", _mb(inv.get("cgst_total")))]
    else:
        rows += [("IGST Value", _mb(inv.get("igst_total")))]
    rows += [("CR/DR", _mb(inv.get("round_off"))),
             ("CR/DR NOTE", f"{-ret:.2f}" if ret else "0.00")]
    body = "".join(f"<tr><td>{_e(k)}</td><td class='n'>{v}</td></tr>" for k, v in rows)
    body += (f"<tr class='party'><td>PARTY TOTAL</td>"
             f"<td class='n'>{_m(inv.get('party_total', inv.get('grand_total')))}</td></tr>")
    paid = []
    if _f(inv.get("amount_paid")) > 0.004:
        paid.append(f"Paid Rs {_m(inv.get('amount_paid'))}")
    for r in inv.get("receipts") or []:
        paid.append(f"Paid Rs {_m(r.get('amount'))} (Rcpt {_e(r.get('receipt_no'))})")
    # Room for two such lines under the PARTY TOTAL; more are left off.
    body += "".join(f"<tr class='paid'><td colspan='2'>{p}</td></tr>" for p in paid[:2])
    return f"<table class='tot'>{body}</table>"


def _ledger(inv: dict) -> str:
    bal = _f(inv.get("ledger_balance"))
    return f"{abs(bal):.2f}" + (" Cr" if bal < -0.004 else "")


def _signature(inv: dict) -> str:
    src = str(inv.get("signature_src") or "")
    if not src.startswith("data:image/"):
        return ""
    # Each print its own small tilt, size and place, as on the retail bill
    # (core/bill_signature_look); a test pins it with inv["signature_look"].
    look = inv.get("signature_look")
    if not isinstance(look, str):
        from core.bill_signature_look import natural_style

        look = natural_style()
    return f"<img class='sign-img' src='{src}' alt='' style='{_e(look)}'>"


def _box(inv: dict, entries: list, page: int, pages: int, copy_label: str) -> str:
    shop = inv.get("shop") or {}
    buyer = inv.get("buyer") or {}
    last = page == pages
    intra = bool(inv.get("intra_state", True))
    name = str(shop.get("name") or "")

    left = [f"<div class='shop-name' style='font-size:{_shop_name_pt(name):.1f}pt'>"
            f"{_e(name.upper())}</div>"]
    if shop.get("address"):
        left.append(f"<div class='addr'>{_e(str(shop.get('address')).upper())}</div>")
    if shop.get("phone"):
        left.append(f"<div>Phone : {_e(shop.get('phone'))}</div>")
    if shop.get("dl_numbers"):
        left.append(f"<div class='b'>D.L. : {_e(shop.get('dl_numbers'))}</div>")
    if shop.get("gstin"):
        left.append(f"<div class='b'>GSTIN : {_e(shop.get('gstin'))}</div>")

    meta = [("Invoice No.", _e(inv.get("bill_no"))), ("Date", _e(_date(inv.get("bill_date"))))]
    if inv.get("due_date") and str(inv.get("due_date"))[:10] != str(inv.get("bill_date"))[:10]:
        meta.append(("Due Date", _e(_date(inv.get("due_date")))))
    if pages > 1:
        meta.append(("Page", f"{page} of {pages}"))
    mid = ("<div class='title'>GST INVOICE</div>"
           f"<div class='pay'>({_e(inv.get('pay_label') or 'CREDIT')})</div>"
           "<table class='meta'>" + "".join(
               f"<tr><td>{k}</td><td>:</td><td class='b'>{v}</td></tr>" for k, v in meta)
           + "</table>")

    right = ["<div>Buyer Name</div>",
             f"<div class='party-name'>M/s {_e(str(buyer.get('name') or '').upper())}</div>"]
    if buyer.get("address"):
        right.append(f"<div class='addr'>{_e(str(buyer.get('address')).upper())}</div>")
    right.append(f"<div>PHONE NO. : {_e(buyer.get('phone'))}</div>")
    right.append(f"<div class='b'>D.L.No. : {_e(buyer.get('dl_numbers'))}</div>")
    right.append(f"<div class='b'>Party GSTIN : {_e(buyer.get('gstin'))}</div>")
    if not intra:
        pos = inv.get("place_of_supply_name") or buyer.get("state_name") or ""
        code = inv.get("place_of_supply") or buyer.get("state_code") or ""
        if code:
            right.append(f"<div>Place of Supply : {_e(pos)} ({_e(code)})</div>")
    # Transport, LR and e-way bill under the buyer, on one line when they fit.
    carry = [f"{label} : {_e(inv.get(key))}" for key, label in (
        ("transport", "Transport"), ("lr_no", "LR No."), ("eway_bill_no", "E-way Bill"))
        if str(inv.get(key) or "").strip()]
    if carry:
        right.append(f"<div class='carry'>{'&nbsp;&nbsp;'.join(carry)}</div>")

    if last:
        bank = _bank_lines(shop)
        bank_html = ("<div class='cap'>BANK DETAILS</div>"
                     + "".join(f"<div class='line'>{b}</div>" for b in bank)) if bank else ""
        foot_left = (f"{_gst_table(inv)}<div class='words'>{_e(_words(inv))}</div>"
                     f"<div class='decl'>{_e(inv.get('declaration') or '')}</div>")
        foot_mid = (f"<div class='line'>Total Item / Quantity : "
                    f"{int(inv.get('total_items') or 0)} / {_qty(inv.get('total_qty')) or 0}</div>"
                    f"{bank_html}<div class='ledger'>LEDGER BAL. : {_ledger(inv)}</div>")
        foot_right = _totals(inv)
    else:
        foot_left = f"<div class='cont'>Page {page} of {pages}</div>"
        foot_mid = "<div class='cont'>Continued on next page ...</div>"
        foot_right = ""
    label = f"<div class='copy-label'>{_e(copy_label)}</div>" if copy_label else ""
    sign = _signature(inv) if last else ""
    return f"""<div class="inv">
  <div class="head">
    <div class="h-left">{''.join(left)}</div>
    <div class="h-mid">{mid}</div>
    <div class="h-right">{''.join(right)}</div>
  </div>
  <div class="body">{_items_table(inv, entries)}</div>
  <div class="foot">
    <div class="f-left">{foot_left}</div>
    <div class="f-mid">{foot_mid}</div>
    <div class="f-right">{foot_right}</div>
    <div class="f-sign">
      <div class="sign-cust">Sign of Customer</div>
      <div class="sign-shop">{sign}<span>For {_e(name.upper())}</span></div>
    </div>
  </div>
</div>{label}"""


def invoice_boxes(inv: dict, copy_label: str = "") -> list[str]:
    """One invoice as its boxes (an A5 landscape each): ROWS_PER_PAGE item-table rows a box."""
    entries = _row_entries(inv)
    chunks = [entries[i:i + ROWS_PER_PAGE] for i in range(0, len(entries), ROWS_PER_PAGE)]
    chunks = chunks or [[]]
    return [_box(inv, chunk, i, len(chunks), copy_label) for i, chunk in enumerate(chunks, 1)]


def _css(paper: str) -> str:
    a4 = paper == "A4"
    sheet_h = "297mm" if a4 else "148mm"
    half_h = "148.5mm" if a4 else "148mm"
    page = "size: 210mm 297mm; margin: 0;" if a4 else "size: 210mm 148mm; margin: 0;"
    return f"""
@page {{ {page} }}
* {{ box-sizing: border-box; }}
html, body {{ margin: 0; padding: 0; background: #fff; color: #000; }}
body {{ font-family: 'Times New Roman', Times, serif; font-size: 7.6pt; line-height: 1.18;
       -webkit-print-color-adjust: exact; print-color-adjust: exact; }}
.sheet {{ width: 210mm; height: {sheet_h}; position: relative; overflow: hidden;
          page-break-after: always; break-after: page; }}
.sheet:last-child {{ page-break-after: auto; break-after: auto; }}
.half {{ width: 210mm; height: {half_h}; padding: 4mm 5mm 4.2mm; position: relative; }}
.cut {{ position: absolute; left: 0; right: 0; top: 148.5mm; border-top: 0.3mm dashed #000; }}
.copy-label {{ position: absolute; right: 6mm; bottom: 0.6mm; font: 700 6.5pt Arial, sans-serif;
               letter-spacing: 0.5px; }}
.inv {{ height: 100%; border: 0.3mm solid #000; display: flex; flex-direction: column; }}
.b {{ font-weight: 700; }}
.head {{ height: {_HEAD_MM}mm; flex: 0 0 auto; display: grid;
         grid-template-columns: 40% 22% 38%; border-bottom: 0.3mm solid #000; overflow: hidden; }}
.head > div {{ padding: 1mm 1.6mm; overflow: hidden; }}
.h-mid {{ border-left: 0.3mm solid #000; border-right: 0.3mm solid #000; }}
.shop-name {{ font-weight: 700; line-height: 1.05; white-space: nowrap; overflow: hidden;
              margin-bottom: 0.6mm; }}
.addr {{ max-height: 3.6em; overflow: hidden; }}
.title {{ text-align: center; font-weight: 700; font-size: 12pt; margin-top: 0.8mm; }}
.pay {{ text-align: center; font-weight: 700; font-size: 9pt; margin-bottom: 1.6mm; }}
.meta {{ border-collapse: collapse; width: 100%; border-top: 0.3mm solid #000; }}
.meta td {{ padding: 0.5mm 0.6mm 0; white-space: nowrap; vertical-align: top; }}
.meta td:nth-child(2) {{ width: 2mm; }}
.carry {{ font-size: 7pt; margin-top: 0.4mm; }}
.party-name {{ font-weight: 700; font-size: 9pt; line-height: 1.1; }}
.body {{ flex: 1 1 auto; min-height: 0; position: relative; }}
.items {{ position: absolute; top: 0; left: 0; width: 100%; height: 100%;
          border-collapse: collapse; table-layout: fixed; }}
.items th {{ height: {_THEAD_MM}mm; font-weight: 400; text-align: left; padding: 0 0.8mm;
             border-bottom: 0.3mm solid #000; border-right: 0.3mm solid #000;
             white-space: nowrap; overflow: hidden; font-size: 7.4pt; }}
.items td {{ border-right: 0.3mm solid #000; padding: 0 0.8mm; white-space: nowrap;
             overflow: hidden; text-overflow: clip; vertical-align: top; }}
.items th:last-child, .items td:last-child {{ border-right: 0; }}
.items tr.r td {{ height: {_ROW_MM}mm; line-height: {_ROW_MM}mm; }}
.items tr.filler td {{ height: 100%; }}
.items td.t {{ text-overflow: ellipsis; }}
.items td.n, .items td.rt {{ text-align: right; }}
.items td.c {{ text-align: center; }}
.items tr.ret-line td {{ font-style: italic; }}
.foot {{ height: {_FOOT_MM}mm; flex: 0 0 auto; display: grid; border-top: 0.3mm solid #000;
         grid-template-columns: 37% 30% 33%; grid-template-rows: 1fr 6.4mm; }}
.f-left {{ grid-row: 1 / span 2; overflow: hidden; }}
.f-mid {{ border-left: 0.3mm solid #000; padding: 0.6mm 1.6mm; overflow: hidden; }}
.f-right {{ border-left: 0.3mm solid #000; overflow: hidden; }}
.f-sign {{ grid-column: 2 / span 2; border-left: 0.3mm solid #000; display: flex;
           align-items: flex-end; justify-content: space-between; padding: 0 1.6mm 0.8mm; }}
.sign-cust {{ font-family: Arial, sans-serif; font-size: 7.6pt; }}
.sign-shop {{ position: relative; font-family: Arial, sans-serif; font-weight: 700;
              font-size: 8.4pt; white-space: nowrap; }}
.sign-shop span {{ position: relative; z-index: 1; }}
.sign-img {{ position: absolute; right: 8%; bottom: 1.2mm; height: 13mm; max-width: 46mm;
             object-fit: contain; z-index: 2; transform-origin: 50% 85%; }}
.gst {{ width: 100%; border-collapse: collapse; }}
.gst th {{ font-weight: 400; text-align: left; padding: 0.3mm 0.8mm;
           border-bottom: 0.3mm solid #000; border-right: 0.3mm solid #000; }}
.gst th:last-child {{ border-right: 0; }}
.gst td {{ padding: 0 0.8mm; height: 3.1mm; }}
.gst td.n {{ text-align: right; }}
.words {{ font-weight: 700; padding: 0.6mm 0.8mm 0.4mm; border-top: 0.3mm solid #000; }}
.decl {{ font-size: 6.2pt; line-height: 1.12; padding: 0 0.8mm; }}
.cap {{ font-weight: 700; text-decoration: underline; margin-top: 0.6mm; }}
.line {{ white-space: nowrap; overflow: hidden; }}
.ledger {{ font-weight: 700; margin-top: 0.8mm; white-space: nowrap; }}
.tot {{ width: 100%; border-collapse: collapse; }}
.tot td {{ padding: 0 1.4mm; height: 3.25mm; white-space: nowrap; }}
.tot td.n {{ text-align: right; }}
.tot tr.party td {{ font-weight: 700; font-size: 10.5pt; height: 5.4mm;
                    border-top: 0.3mm solid #000; border-bottom: 0.3mm solid #000; }}
.tot tr.paid td {{ font-size: 6.8pt; height: 3mm; text-align: right; }}
.cont {{ padding: 2mm; font-style: italic; }}
"""


def render_wholesale_invoices_html(invs: list[dict], *, paper: str = "A5",
                                   copy_label: str = "") -> str:
    """Several invoices in one document: an A5 landscape page each ("A5"), or two to an A4
    portrait sheet, top and bottom, with a dashed cut line between ("A4")."""
    paper = "A4" if str(paper or "").upper() == "A4" else "A5"
    boxes: list[str] = []
    for inv in invs:
        boxes += invoice_boxes(inv, copy_label)
    per = 2 if paper == "A4" else 1
    sheets = []
    for i in range(0, len(boxes), per):
        pair = boxes[i:i + per]
        halves = "".join(f"<div class='half'>{b}</div>" for b in pair)
        cut = "<div class='cut'></div>" if len(pair) > 1 else ""
        sheets.append(f"<div class='sheet'>{halves}{cut}</div>")
    title = " ".join(str(inv.get("bill_no") or "") for inv in invs[:3])
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>GST INVOICE {_e(title)}</title>
<style>{_css(paper)}</style></head>
<body class="paper-{paper.lower()}">
{''.join(sheets)}
</body></html>
"""


def render_wholesale_invoice_html(inv: dict, *, paper: str = "A4", copy_label: str = "") -> str:
    """One invoice: its own A5 landscape page(s) ("A5"), or the top half of an A4 portrait
    sheet ("A4")."""
    return render_wholesale_invoices_html([inv], paper=paper, copy_label=copy_label)
