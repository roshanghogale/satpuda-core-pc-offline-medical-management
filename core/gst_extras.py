"""GST made easy for the shop (6 Oct 2026): a one-page summary, the month's GST to pay with
the credit carried forward, medicines without HSN in one list, Tally Prime vouchers, and one
zip for the CA.

Every figure comes from core.gst_reports -- the printed-bill GST -- so nothing here can
disagree with the GSTR-1 / GSTR-3B tables the window already shows.
"""
from __future__ import annotations

import base64
import io
import json
import zipfile
from collections import defaultdict
from dataclasses import replace
from datetime import date
from decimal import Decimal
from typing import Any
from xml.sax.saxutils import escape

from core import gst_reports as gr

ZERO = Decimal("0")
HEADS = ("igst", "cgst", "sgst")


def _d(v) -> Decimal:
    return Decimal(str(v or 0))


def _f(v: Decimal) -> float:
    return float(v.quantize(Decimal("0.01")))


# ── the month's GST: liability, credit, set-off ───────────────────────────────────────────

def set_off(liability: dict, credit: dict) -> dict:
    """Use the credit against the tax due, in the order the GST law fixes (sections 49 / 49A,
    rule 88A): IGST credit first -- IGST, then CGST, then SGST; CGST credit on CGST then IGST;
    SGST credit on SGST then IGST. CGST credit never pays SGST, nor the reverse.

    Returns what is left to pay in cash and the credit carried to the next month."""
    due = {h: max(_d(liability.get(h)), ZERO) for h in HEADS}
    cr = {h: max(_d(credit.get(h)), ZERO) for h in HEADS}

    def use(src: str, dst: str) -> None:
        amt = min(cr[src], due[dst])
        cr[src] -= amt
        due[dst] -= amt

    for dst in ("igst", "cgst", "sgst"):
        use("igst", dst)
    use("cgst", "cgst")
    use("cgst", "igst")
    use("sgst", "sgst")
    use("sgst", "igst")
    return {"cash": {h: _f(due[h]) for h in HEADS}, "carried": {h: _f(cr[h]) for h in HEADS},
            "cash_total": _f(sum(due.values(), ZERO)), "carried_total": _f(sum(cr.values(), ZERO))}


def _month_slices(start: str, end: str) -> list:
    out = []
    y, m = int(start[:4]), int(start[5:7])
    while True:
        a, b = gr.month_range(y, m)
        a, b = max(a, start), min(b, end)
        if a > end:
            break
        out.append((a, b))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def _sub(p: gr.Period, a: str, b: str) -> gr.Period:
    within = lambda d: a <= (d or "")[:10] <= b  # noqa: E731
    return replace(p, start=a, end=b, checks=[],
                   bills=[x for x in p.bills if within(x.date)],
                   notes=[x for x in p.notes if within(x.date)],
                   purchases=[x for x in p.purchases if within(x.date)],
                   purchase_returns=[x for x in p.purchase_returns if within(x.date)])


def _liability_and_credit(p: gr.Period) -> tuple:
    t3b = gr.gstr3b(p)
    out = t3b["3.1(a) Outward taxable supplies"]
    net = t3b["4(C) Net ITC available"]
    return ({h: _d(out[h]) for h in HEADS}, {h: _d(net[h]) for h in HEADS}, t3b)


def monthly_ledger(conn, end: str) -> list:
    """Month by month from 1 April of end's financial year: tax on sales (less credit notes),
    ITC, the credit brought forward, cash to pay and credit carried -- the electronic
    credit ledger as the shop's own books show it. One read of the year, split by month."""
    end = gr._iso(end)
    y = int(end[:4]) if int(end[5:7]) >= 4 else int(end[:4]) - 1
    fy_start = f"{y}-04-01"
    p = gr.load_period(conn, fy_start, end)
    rows, carried = [], {h: ZERO for h in HEADS}
    for a, b in _month_slices(fy_start, end):
        liab, itc, _ = _liability_and_credit(_sub(p, a, b))
        credit = {h: itc[h] + carried[h] for h in HEADS}
        so = set_off(liab, credit)
        rows.append({
            "from": a, "to": b, "month": a[:7],
            "tax_on_sales": _f(sum(liab.values(), ZERO)),
            "itc": _f(sum(itc.values(), ZERO)),
            "brought_forward": _f(sum(carried.values(), ZERO)),
            "cash_to_pay": so["cash_total"], "cash": so["cash"],
            "carried_forward": so["carried_total"], "carried": so["carried"],
        })
        carried = {h: _d(so["carried"][h]) for h in HEADS}
    return rows


def one_page(conn, start: str, end: str) -> dict:
    """The period on one page, in the words a shopkeeper uses."""
    p = gr.load_period(conn, start, end)
    liab, itc, t3b = _liability_and_credit(p)
    sales_total = sum((b.total for b in p.bills), ZERO)
    returns_total = sum((n.refund for n in p.notes), ZERO)
    purchase_value = sum((pu.value for pu in p.purchases), ZERO)
    taxable = _d(t3b["3.1(a) Outward taxable supplies"]["taxable"])
    nil = _d(t3b["3.1(c) Nil rated / exempted"]["taxable"])
    months = monthly_ledger(conn, p.end)
    this = [m for m in months if m["from"] >= p.start] or months[-1:]
    hsn = missing_hsn_from(p)
    return {
        "period": {"from": p.start, "to": p.end}, "shop": p.shop,
        "bills": len(p.bills), "sales_total": _f(sales_total),
        "returns": len(p.notes), "returns_total": _f(returns_total),
        "taxable_sales": _f(taxable), "nil_sales": _f(nil),
        "tax_on_sales": {h: _f(liab[h]) for h in HEADS}, "tax_on_sales_total": _f(sum(liab.values(), ZERO)),
        "purchases": len(p.purchases), "purchase_value": _f(purchase_value),
        "itc": {h: _f(itc[h]) for h in HEADS}, "itc_total": _f(sum(itc.values(), ZERO)),
        "no_itc_tax": t3b["GST on bills from unregistered suppliers (no ITC)"]["tax"],
        "cash_to_pay": round(sum(m["cash_to_pay"] for m in this), 2),
        "carried_forward": this[-1]["carried_forward"] if this else 0.0,
        "months": months,
        "medicines_without_hsn": len(hsn),
        "checks": len(p.checks),
    }


# ── HSN: one list of medicines instead of a check per bill line ───────────────────────────

def missing_hsn_from(p: gr.Period) -> list:
    by: dict = {}
    for b in p.bills:
        for ln in b.lines:
            problem = gr._hsn_problem(ln.hsn)
            if not problem:
                continue
            key = ln.medicine_id or ln.name
            row = by.setdefault(key, {"medicine_id": ln.medicine_id, "name": ln.name, "hsn": ln.hsn,
                                      "problem": problem, "bills": 0, "last_bill": "", "rate": gr._rate_out(ln.rate)})
            row["bills"] += 1
            row["last_bill"] = max(row["last_bill"], b.date)
    return sorted(by.values(), key=lambda r: (-r["bills"], r["name"]))


def missing_hsn(conn, start: str, end: str) -> list:
    return missing_hsn_from(gr.load_period(conn, start, end))


def save_hsn(conn, items: list) -> dict:
    """HSN for medicines, every batch of the same name. Reports read the medicine master, so
    the old bills' GST tables pick it up too."""
    from core.desktop_inventory_service import update_medicine
    from core.sync_prefs import is_online_mode

    done, failed = 0, []
    for it in items or []:
        mid = int(it.get("medicine_id") or 0)
        hsn = "".join(ch for ch in str(it.get("hsn") or "") if ch.isdigit())
        if mid <= 0 or len(hsn) not in (4, 6, 8):
            failed.append({"medicine_id": mid, "error": "HSN 4, 6 kiva 8 ankI hava"})
            continue
        if is_online_mode():
            from core.online_catalog import medicines
            from core.server_crud import get_doc

            first = get_doc("medicines", mid) or {}
            name = str(first.get("name") or "").strip()
            ids = [int(m.get("id") or m.get("local_id")) for m in medicines(force=True)
                   if str(m.get("name") or "").strip() == name] if name else [mid]
            for one in ids or [mid]:
                doc = get_doc("medicines", one) or {}
                res = update_medicine(conn, {**_edit_body(doc, one), "hsn_code": hsn})
                if not res.get("ok"):
                    failed.append({"medicine_id": one, "error": res.get("error")})
                else:
                    done += 1
        else:
            row = conn.execute("SELECT name FROM medicines WHERE id=?", (mid,)).fetchone()
            if not row:
                failed.append({"medicine_id": mid, "error": "aushadh sapadle nahi"})
                continue
            ids = [r[0] for r in conn.execute("SELECT id FROM medicines WHERE name=?", (row[0],))]
            cols = {r[1] for r in conn.execute("PRAGMA table_info(medicines)")}
            stamp = ", synced_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')" if "synced_at" in cols else ""
            for one in ids:
                conn.execute(f"UPDATE medicines SET hsn_code=?{stamp} WHERE id=?", (hsn, one))
            conn.commit()
            try:
                from core.sync_coordinator import after_medicine_saved

                for one in ids:
                    after_medicine_saved(conn, one)
            except Exception:
                pass
            done += len(ids)
    return {"ok": not failed, "saved": done, "failed": failed}


def _edit_body(doc: dict, mid: int) -> dict:
    """The medicine as update_medicine expects it, stock left alone (no stock key)."""
    return {"id": mid, "name": doc.get("name") or "", "type": doc.get("type") or "",
            "unit": doc.get("unit") or "1", "batch_no": doc.get("batch_no") or "",
            "expiry_date": doc.get("expiry_date") or "", "mrp": doc.get("mrp") or 0,
            "rate": doc.get("rate") or 0, "manufacturer": doc.get("manufacturer") or "",
            "schedule": doc.get("schedule") or "", "content_drug": doc.get("content_drug") or "",
            "supplier_name": doc.get("supplier_name") or ""}


# ── Tally Prime ──────────────────────────────────────────────────────────────────────────

LEDGERS = {
    "cash": "Cash",
    "round_off": "Round Off",
    "out": {"igst": "Output IGST", "cgst": "Output CGST", "sgst": "Output SGST"},
    "in": {"igst": "Input IGST", "cgst": "Input CGST", "sgst": "Input SGST"},
}


def _rate_txt(rate) -> str:
    r = gr._rate_out(_d(rate))
    return f"{int(r)}%" if float(r).is_integer() else f"{r}%"


def _sales_ledger(rate) -> str:
    return "Sales Exempt" if _d(rate) == 0 else f"Sales GST {_rate_txt(rate)}"


def _purchase_ledger(rate) -> str:
    return "Purchase Exempt" if _d(rate) == 0 else f"Purchase GST {_rate_txt(rate)}"


def _amt(v: Decimal) -> str:
    return f"{v.quantize(Decimal('0.01'))}"


def _entry(ledger: str, debit: bool, amount: Decimal) -> str:
    # Tally's convention: a debit is a negative amount with ISDEEMEDPOSITIVE Yes.
    a = abs(amount)
    if a == 0:
        return ""
    return ("<ALLLEDGERENTRIES.LIST>"
            f"<LEDGERNAME>{escape(ledger)}</LEDGERNAME>"
            f"<ISDEEMEDPOSITIVE>{'Yes' if debit else 'No'}</ISDEEMEDPOSITIVE>"
            f"<AMOUNT>{'-' if debit else ''}{_amt(a)}</AMOUNT>"
            "</ALLLEDGERENTRIES.LIST>")


def _voucher(vtype: str, when: str, number: str, party: str, entries: list, narration: str,
             ref: str = "") -> str:
    body = "".join(e for e in entries if e)
    return (f'<TALLYMESSAGE xmlns:UDF="TallyUDF"><VOUCHER VCHTYPE="{vtype}" ACTION="Create" '
            'OBJVIEW="Accounting Voucher View">'
            f"<DATE>{when.replace('-', '')}</DATE><VOUCHERTYPENAME>{vtype}</VOUCHERTYPENAME>"
            f"<VOUCHERNUMBER>{escape(number)}</VOUCHERNUMBER>"
            + (f"<REFERENCE>{escape(ref)}</REFERENCE>" if ref else "")
            + f"<PARTYLEDGERNAME>{escape(party)}</PARTYLEDGERNAME>"
            f"<NARRATION>{escape(narration)}</NARRATION>"
            "<PERSISTEDVIEW>Accounting Voucher View</PERSISTEDVIEW>"
            f"{body}</VOUCHER></TALLYMESSAGE>")


def _ledger_master(name: str, parent: str, extra: str = "") -> str:
    return (f'<TALLYMESSAGE xmlns:UDF="TallyUDF"><LEDGER NAME="{escape(name, {chr(34): "&quot;"})}" '
            'ACTION="Create">'
            f"<NAME.LIST><NAME>{escape(name)}</NAME></NAME.LIST><PARENT>{escape(parent)}</PARENT>"
            f"{extra}</LEDGER></TALLYMESSAGE>")


def _tax_ledger(name: str, head: str) -> str:
    duty = {"igst": "Integrated Tax", "cgst": "Central Tax", "sgst": "State Tax"}[head]
    return _ledger_master(name, "Duties & Taxes", f"<TAXTYPE>GST</TAXTYPE><GSTDUTYHEAD>{duty}</GSTDUTYHEAD>")


def _party_master(name: str, parent: str, gstin: str, state_code: str) -> str:
    extra = ""
    if gstin:
        extra += f"<PARTYGSTIN>{escape(gstin)}</PARTYGSTIN><GSTREGISTRATIONTYPE>Regular</GSTREGISTRATIONTYPE>"
    if state_code and gr.STATES.get(state_code):
        extra += f"<LEDSTATENAME>{escape(gr.STATES[state_code])}</LEDSTATENAME>"
    return _ledger_master(name, parent, extra)


def _envelope(report: str, messages: list) -> str:
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<ENVELOPE><HEADER><TALLYREQUEST>Import Data</TALLYREQUEST>'
            f"</HEADER><BODY><IMPORTDATA><REQUESTDESC><REPORTNAME>{report}</REPORTNAME></REQUESTDESC>"
            f"<REQUESTDATA>{''.join(messages)}</REQUESTDATA></IMPORTDATA></BODY></ENVELOPE>\n")


def tally_files(conn, start: str, end: str) -> dict:
    """Two Tally Prime import files: the ledgers (import first), then the vouchers.

    Accounting vouchers, the bill's own GST figures: Sales (B2B bills to the customer's
    ledger with GSTIN, every other bill to Cash), Credit Note for each sales return,
    Purchase and Debit Note on the supplier's ledger. Each voucher balances to the paisa;
    what the bill rounded goes to Round Off."""
    p = gr.load_period(conn, start, end)
    shop_state = p.shop["state"]
    masters: dict = {}
    vouchers: list = []
    skipped: list = []

    def need(name: str, xml: str) -> None:
        masters.setdefault(name, xml)

    for h in HEADS:
        need(LEDGERS["out"][h], _tax_ledger(LEDGERS["out"][h], h))
        need(LEDGERS["in"][h], _tax_ledger(LEDGERS["in"][h], h))
    need(LEDGERS["round_off"], _ledger_master(LEDGERS["round_off"], "Indirect Expenses"))

    def party_for(bill) -> str:
        if bill is not None and bill.gstin:
            name = f"{bill.customer} ({bill.gstin})"
            need(name, _party_master(name, "Sundry Debtors", bill.gstin, gr.state_of(bill.gstin)))
            return name
        return LEDGERS["cash"]

    for b in p.bills:
        g = gr.bill_tax(b)
        inter = gr._inter(b.pos, shop_state)
        sales = defaultdict(lambda: ZERO)
        tax = {h: ZERO for h in HEADS}
        for line in g.lines:
            sales[_sales_ledger(line.rate)] += line.taxable
            i, c, s = gr._split(line.tax, inter)
            tax["igst"] += i
            tax["cgst"] += c
            tax["sgst"] += s
        if not b.lines and b.total:
            sales["Sales (GST rate not known)"] += b.total
        credit_sum = sum(sales.values(), ZERO) + sum(tax.values(), ZERO)
        round_off = b.total - credit_sum
        party = party_for(b)
        entries = [_entry(party, True, b.total)]
        for led, amt in sorted(sales.items()):
            need(led, _ledger_master(led, "Sales Accounts"))
            entries.append(_entry(led, False, amt))
        entries += [_entry(LEDGERS["out"][h], False, tax[h]) for h in HEADS]
        if round_off:
            entries.append(_entry(LEDGERS["round_off"], round_off < 0, round_off))
        if b.total == 0:
            skipped.append(b.no)
            continue
        vouchers.append(_voucher("Sales", b.date, gr._shown_no(b.no), party, entries,
                                 f"{b.customer} - bill {gr._shown_no(b.no)}"))

    for n in p.notes:
        if not n.lines:
            skipped.append(n.no)
            continue
        g = gr.note_tax(n)
        inter = gr._inter(n.bill.pos if n.bill else shop_state, shop_state)
        sales = defaultdict(lambda: ZERO)
        tax = {h: ZERO for h in HEADS}
        for line in g.lines:
            sales[_sales_ledger(line.rate)] += line.taxable
            i, c, s = gr._split(line.tax, inter)
            tax["igst"] += i
            tax["cgst"] += c
            tax["sgst"] += s
        value = max(n.refund, ZERO)
        round_off = value - sum(sales.values(), ZERO) - sum(tax.values(), ZERO)
        party = party_for(n.bill)
        entries = [_entry(led, True, amt) for led, amt in sorted(sales.items())]
        for led in sales:
            need(led, _ledger_master(led, "Sales Accounts"))
        entries += [_entry(LEDGERS["out"][h], True, tax[h]) for h in HEADS]
        if round_off:
            entries.append(_entry(LEDGERS["round_off"], round_off > 0, round_off))
        entries.append(_entry(party, False, value))
        vouchers.append(_voucher("Credit Note", n.date, n.no, party, entries,
                                 f"Return against {gr._shown_no(n.bill.no) if n.bill else '-'}",
                                 gr._shown_no(n.bill.no) if n.bill else ""))

    def supplier_ledger(pu) -> str:
        name = pu.supplier or "Purchase party"
        need(name, _party_master(name, "Sundry Creditors", pu.gstin, gr.state_of(pu.gstin)))
        return name

    for pu in p.purchases:
        inter = gr._inter(gr.state_of(pu.gstin), shop_state)
        buys = defaultdict(lambda: ZERO)
        tax = {h: ZERO for h in HEADS}
        if pu.lines_known and pu.lines:
            for ln in pu.lines:
                buys[_purchase_ledger(ln.rate)] += ln.taxable
        else:
            buys["Purchase"] += pu.taxable
        if inter:
            tax["igst"] = pu.tax
        else:
            tax["cgst"], tax["sgst"] = pu.cgst, pu.sgst
        if not pu.gstin:
            # No ITC on an unregistered supplier's bill: its tax is part of the cost.
            first = sorted(buys)[0]
            buys[first] += sum(tax.values(), ZERO)
            tax = {h: ZERO for h in HEADS}
        debit_sum = sum(buys.values(), ZERO) + sum(tax.values(), ZERO)
        round_off = pu.value - debit_sum
        party = supplier_ledger(pu)
        entries = []
        for led, amt in sorted(buys.items()):
            need(led, _ledger_master(led, "Purchase Accounts"))
            entries.append(_entry(led, True, amt))
        entries += [_entry(LEDGERS["in"][h], True, tax[h]) for h in HEADS]
        if round_off:
            entries.append(_entry(LEDGERS["round_off"], round_off > 0, round_off))
        entries.append(_entry(party, False, pu.value))
        vouchers.append(_voucher("Purchase", pu.date, pu.no, party, entries,
                                 f"Supplier bill {pu.bill_number or '-'}", pu.bill_number or ""))

    for r in p.purchase_returns:
        pu = r.purchase
        if pu is None:
            skipped.append(r.no)
            continue
        inter = gr._inter(gr.state_of(pu.gstin), shop_state)
        i, c, s = gr._split(r.tax, inter)
        tax = {"igst": i, "cgst": c, "sgst": s} if pu.gstin else {h: ZERO for h in HEADS}
        taxable = r.taxable if pu.gstin else r.taxable + r.tax
        round_off = r.value - taxable - sum(tax.values(), ZERO)
        party = supplier_ledger(pu)
        need("Purchase", _ledger_master("Purchase", "Purchase Accounts"))
        entries = [_entry(party, True, r.value), _entry("Purchase", False, taxable)]
        entries += [_entry(LEDGERS["in"][h], False, tax[h]) for h in HEADS]
        if round_off:
            entries.append(_entry(LEDGERS["round_off"], round_off < 0, round_off))
        vouchers.append(_voucher("Debit Note", r.date, r.no, party, entries,
                                 f"Return of {pu.bill_number or pu.no}", pu.bill_number or ""))

    return {
        "masters": _envelope("All Masters", list(masters.values())),
        "vouchers": _envelope("Vouchers", vouchers),
        "counts": {"ledgers": len(masters), "vouchers": len(vouchers), "skipped": skipped},
    }


TALLY_README = """Tally Prime madhe import kase karayche
========================================
1. Tally Prime ughda ani tumchi company nivda (company madhe GST chalu asava).
2. Pahile: Import -> Masters -> "{masters}" nivda -> Import. (Ledgers banatat: Sales GST 5% / 12% /
   18%, Output CGST / SGST / IGST, Input CGST / SGST / IGST, Round Off, B2B grahak ani suppliers.)
3. Mag: Import -> Transactions -> "{vouchers}" nivda -> Import.
   (Sales, Credit Note (sales return), Purchase, Debit Note (purchase return) vouchers.)
4. Saadhya grahakanche bills "Cash" ledger var jatat; GSTIN asnare grahak tyanchya navache ledger.
5. Pratyek voucher bill var chhaplelya GST pramane, paisa-paisa; bill cha round-off "Round Off" madhe.

Ekda import kelele vouchers punha import kele tar Tally dubaar vouchers banvel -
navin mahinyasathi navin file vapra.
"""


# ── one zip for the CA ───────────────────────────────────────────────────────────────────

def ca_zip(conn, start: str, end: str) -> dict:
    """Everything the CA asks for, one file: Excel (every table), PDF, GSTR-1 JSON, the
    one-page summary, and the Tally Prime files."""
    stem = f"GST_{(gr._shop(conn).get('gstin') or 'GST')}_{gr._iso(start)}_{gr._iso(end)}"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for fmt in ("xlsx", "pdf", "json"):
            res = gr.export(conn, {"from": start, "to": end, "format": fmt, "tables": [],
                                   "page_layout": "landscape"})
            if res.get("ok") and res.get("content_base64"):
                z.writestr(res.get("filename") or f"{stem}.{fmt}", base64.b64decode(res["content_base64"]))
        summary = one_page(conn, start, end)
        z.writestr(f"{stem}_saransh.txt", summary_text(summary))
        t = tally_files(conn, start, end)
        m, v = f"Tally_1_ledgers_{stem}.xml", f"Tally_2_vouchers_{stem}.xml"
        z.writestr(m, t["masters"])
        z.writestr(v, t["vouchers"])
        z.writestr("Tally_import_kase_karayche.txt", TALLY_README.format(masters=m, vouchers=v))
    return {"ok": True, "filename": f"{stem}_CA.zip", "mime": "application/zip", "format": "zip",
            "content_base64": base64.b64encode(buf.getvalue()).decode("ascii")}


def tally_zip(conn, start: str, end: str) -> dict:
    stem = f"Tally_{gr._iso(start)}_{gr._iso(end)}"
    t = tally_files(conn, start, end)
    buf = io.BytesIO()
    m, v = f"Tally_1_ledgers_{stem}.xml", f"Tally_2_vouchers_{stem}.xml"
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(m, t["masters"])
        z.writestr(v, t["vouchers"])
        z.writestr("Tally_import_kase_karayche.txt", TALLY_README.format(masters=m, vouchers=v))
    return {"ok": True, "filename": f"{stem}.zip", "mime": "application/zip", "format": "zip",
            "counts": t["counts"], "content_base64": base64.b64encode(buf.getvalue()).decode("ascii")}


def summary_text(s: dict) -> str:
    r = lambda v: f"Rs {v:,.2f}"  # noqa: E731
    lines = [
        f"GST saransh: {s['shop'].get('name', '')}  GSTIN {s['shop'].get('gstin') or '-'}",
        f"Kalavadhi: {gr._dmy(s['period']['from'])} te {gr._dmy(s['period']['to'])}",
        "",
        f"Vikri: {s['bills']} bills, {r(s['sales_total'])}  (returns {s['returns']}, {r(s['returns_total'])})",
        f"  Karpaatra vikri (taxable): {r(s['taxable_sales'])}   0% vikri: {r(s['nil_sales'])}",
        f"  Vikri var GST: {r(s['tax_on_sales_total'])}  (CGST {r(s['tax_on_sales']['cgst'])}, "
        f"SGST {r(s['tax_on_sales']['sgst'])}, IGST {r(s['tax_on_sales']['igst'])})",
        f"Kharedi: {s['purchases']} bills, {r(s['purchase_value'])}",
        f"  ITC (kharedi varcha GST): {r(s['itc_total'])}",
        "",
        f"BHARAYCHA GST (cash): {r(s['cash_to_pay'])}",
        f"Pudhchya mahinyat jaanara ITC: {r(s['carried_forward'])}",
        "",
        "Mahina-dar:",
    ]
    for m in s["months"]:
        lines.append(f"  {m['month']}: vikri GST {r(m['tax_on_sales'])}, ITC {r(m['itc'])}, "
                     f"aadhicha ITC {r(m['brought_forward'])} -> bharayche {r(m['cash_to_pay'])}, "
                     f"pudhe {r(m['carried_forward'])}")
    if s["medicines_without_hsn"]:
        lines += ["", f"HSN nasleli aushadhe: {s['medicines_without_hsn']} (GST Reports -> HSN tab madhe bhara)"]
    return "\n".join(lines) + "\n"


def to_json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)
