"""
Regression check for purchase bill imports across supplier formats.
Run: python scripts/validate_import_bills.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core.purchase_importer import (  # noqa: E402
    parse_purchase_pdf,
    parse_purchase_excel,
    _read_tabular_file,
    _cell_at,
    _clean_cell,
    _to_float,
    _detect_edi_htf_format,
)
from core.purchase_invoice_engine import compute_import_bill_totals  # noqa: E402


def _parse(path: str):
    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf":
        return parse_purchase_pdf(path)
    return parse_purchase_excel(path)


def _footer_expect(path: str) -> dict:
    ext = os.path.splitext(path)[1].lower()
    if ext not in (".csv", ".xlsx", ".xls"):
        return {}
    rows = _read_tabular_file(path, ext)
    f_row = next((row for row in rows if _clean_cell(_cell_at(row, 0)).upper() == "F"), [])
    t_row = next((row for row in rows if _clean_cell(_cell_at(row, 0)).upper() == "T"), [])
    if not f_row or not t_row:
        return {}
    fmt = _detect_edi_htf_format(t_row, "")
    if fmt == "seema":
        return {
            "fmt": fmt,
            "net": _to_float(_cell_at(f_row, 23)),
            "gst": _to_float(_cell_at(f_row, 2)),
            "cash": _to_float(_cell_at(f_row, 24)),
            "prod": _to_float(_cell_at(f_row, 25)),
            "round": _to_float(_cell_at(f_row, 22)),
        }
    if fmt == "seema_legacy":
        return {
            "fmt": fmt,
            "net": _to_float(_cell_at(f_row, 21)),
            "cgst": _to_float(_cell_at(f_row, 4)),
            "sgst": _to_float(_cell_at(f_row, 6)),
            "round": _to_float(_cell_at(f_row, 20)),
            "taxable": _to_float(_cell_at(f_row, 1)),
        }
    if fmt == "marg":
        return {
            "fmt": fmt,
            "net": _to_float(_cell_at(f_row, 1)),
            "gst": _to_float(_cell_at(f_row, 9)),
            "round": _to_float(_cell_at(f_row, 2)),
        }
    return {"fmt": fmt}


BILL_FILES = [
    r"c:\Users\rosha\Downloads\I_INV273.PDF",
    r"c:\Users\rosha\Downloads\INVOICE_7HB0UKEHB.PDF",
    r"c:\Users\rosha\Downloads\INV_375_2026-05-04.pdf",
    r"c:\Users\rosha\Downloads\2627_SJCR00664.CSV",
    r"c:\Users\rosha\Downloads\I_INV1897.PDF",
    r"c:\Users\rosha\Downloads\I_SEEMAFRUITS_INV189704052026.CSV",
    r"c:\Users\rosha\Downloads\INV_8_2026-04-04.xlsx",
    r"c:\Users\rosha\Downloads\2627_SJCR00377.CSV",
    r"c:\Users\rosha\Downloads\INV_111_2026-04-13.pdf",
    r"c:\Users\rosha\Downloads\INV_8_2026-04-04.pdf",
    r"c:\Users\rosha\Downloads\INV_137_2026-04-14.pdf",
    r"c:\Users\rosha\Downloads\2627_SJCR00124.CSV",
    r"c:\Users\rosha\Downloads\INVOICE_7HE0MF30O.PDF",
    r"c:\Users\rosha\Downloads\INVOICE_7HE0MFDMB.PDF",
    r"c:\Users\rosha\Downloads\SWAMI_SAMARTH_MEDICAL_AND_AGENCY_20260519_S_W000227.CSV",
    r"c:\Users\rosha\Downloads\2627_SJCR00012.CSV",
    r"c:\Users\rosha\Downloads\2627_SJCR00542 (1).CSV",
    r"c:\Users\rosha\Downloads\2627_SJCR00542.CSV",
    r"c:\Users\rosha\Downloads\2627_SJCR00609.CSV",
    r"c:\Users\rosha\Downloads\SWAMI_SAMARTH_MEDICAL_AND_AGENCY_20260422_S_W000104.CSV",
    r"c:\Users\rosha\Downloads\INVOICE_7HD0SFB7Q.PDF",
    r"c:\Users\rosha\Downloads\INVOICE_7HD0SGEKN.PDF",
    r"c:\Users\rosha\Downloads\INVOICE_7HD0SFIGK.PDF",
]


def _check(path: str) -> list:
    name = os.path.basename(path)
    if not os.path.exists(path):
        return [f"{name}: file missing"]
    inv = _parse(path)
    line = round(sum(float(it.amount or 0) for it in inv.items), 2)
    cgst = round(float(inv.total_cgst or 0), 2)
    sgst = round(float(inv.total_sgst or 0), 2)
    gst = round(cgst + sgst, 2)
    net = round(float(inv.invoice_total or 0), 2)
    probs = []
    if not inv.items:
        probs.append("no items")
    exp = _footer_expect(path)
    if exp.get("fmt") == "seema":
        if exp.get("net") and abs(net - exp["net"]) > 0.02:
            probs.append(f"net {net} != {exp['net']}")
        if exp.get("gst") and abs(gst - exp["gst"]) > 0.02:
            probs.append(f"gst {gst} != F2 {exp['gst']}")
        if exp.get("cash") and abs(float(inv.cash_discount or 0) - exp["cash"]) > 0.02:
            probs.append(f"cash disc mismatch")
    elif exp.get("fmt") == "seema_legacy":
        if exp.get("net") and abs(net - exp["net"]) > 0.02:
            probs.append(f"net {net} != {exp['net']}")
        if exp.get("cgst") and abs(cgst - exp["cgst"]) > 0.02:
            probs.append(f"cgst {cgst} != F4 {exp['cgst']}")
        if exp.get("sgst") and abs(sgst - exp["sgst"]) > 0.02:
            probs.append(f"sgst {sgst} != F6 {exp['sgst']}")
        if line <= 0:
            probs.append(f"line gross {line}")
    elif exp.get("fmt") == "marg":
        if exp.get("net") and abs(net - exp["net"]) > 0.02:
            probs.append(f"net {net} != {exp['net']}")
        if exp.get("gst") and abs(gst - exp["gst"]) > 0.02:
            probs.append(f"gst {gst} != F9 {exp['gst']}")
    line_gross = round(float(getattr(inv, "line_gross", 0) or 0) or line, 2)
    gross_amount = float(getattr(inv, "gross_amount", 0) or 0) or line_gross
    summary = {
        "invoice_total": net,
        "gross_amount": gross_amount,
        "line_gross": line_gross,
        "total_cgst": cgst,
        "total_sgst": sgst,
        "round_off": float(inv.round_off or 0),
        "product_discount": float(inv.product_discount or 0),
        "cash_discount": float(inv.cash_discount or 0),
        "parsed_total_discount": round(
            float(inv.product_discount or 0) + float(inv.cash_discount or 0), 2
        ),
        "footer_gst_authoritative": bool(getattr(inv, "footer_gst_authoritative", False)),
        "use_footer_totals": bool(net > 0 and gst > 0 and gross_amount > 0),
    }
    calc_items = [
        {
            "qty": it.qty,
            "rate": it.rate,
            "gst_pct": it.gst_pct,
            "amount": it.amount,
            "discount_pct": getattr(it, "discount_pct", 0),
        }
        for it in inv.items
    ]
    bill = compute_import_bill_totals(summary, line, 0, items=calc_items)
    if net > 0 and gst > 0:
        if abs(float(bill.get("cgst") or 0) - cgst) > 0.02:
            probs.append(f"display cgst {bill.get('cgst')} != bill {cgst}")
        if abs(float(bill.get("sgst") or 0) - sgst) > 0.02:
            probs.append(f"display sgst {bill.get('sgst')} != bill {sgst}")
        if not bill.get("use_footer_totals"):
            probs.append("footer GST not used for totals")
        if line > 0 and gst > line * 0.5:
            probs.append(f"gst {gst} implausible for line {line}")
        disc = float(inv.product_discount or 0) + float(inv.cash_discount or 0)
        if (
            line > 0
            and not disc
            and not exp.get("fmt")
            and abs(net - (line + gst + float(inv.round_off or 0))) > max(2.0, net * 0.05)
            and "marg" not in (inv.parser or "").lower()
            and "tuljai" not in (inv.parser or "").lower()
            and abs(net - line - gst) > 5.0
        ):
            probs.append(f"net {net} vs line+gst {line + gst}")
    if name == "I_INV273.PDF" and abs(gst - 65.90) > 0.5:
        probs.append(f"gst {gst} expected ~65.90")
    if name in ("INVOICE_7HE0MF30O.PDF", "INVOICE_7HE0MFDMB.PDF"):
        if cgst <= 0 or sgst <= 0:
            probs.append(f"marg add-gst split missing cgst={cgst} sgst={sgst}")
        elif abs(cgst - sgst) > 0.02:
            probs.append(f"marg cgst {cgst} != sgst {sgst}")
    if name.startswith("INVOICE_7HD0") and net > 0:
        if abs(gst - round((net - line - float(inv.round_off or 0)) * 2, 2) / 2) > 2:
            if gst > line:
                probs.append(f"marg sn gst {gst} too high for line {line}")
    return probs


def main() -> int:
    failed = 0
    for path in BILL_FILES:
        probs = _check(path)
        name = os.path.basename(path)
        if probs:
            failed += 1
            print(f"FAIL {name}: {'; '.join(probs)}")
        else:
            inv = _parse(path)
            gst = round(float(inv.total_cgst or 0) + float(inv.total_sgst or 0), 2)
            print(
                f"OK   {name}: {inv.item_count} items, "
                f"line={sum(float(i.amount or 0) for i in inv.items):.2f}, "
                f"gst={gst:.2f}, net={float(inv.invoice_total or 0):.2f}"
            )
    print(f"\n{len(BILL_FILES) - failed}/{len(BILL_FILES)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
