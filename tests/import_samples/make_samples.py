"""Build the distributor-bill samples the import comparison runs on.

    python tests\\import_samples\\make_samples.py

One bill, written the four ways a distributor sends it: an Excel sheet, a CSV,
and a PDF with a real text layer. Strip tablets, liquids and a powder; free
quantity; a percentage discount -- the fields a shop checks after an import.
"""
from __future__ import annotations

import csv
import os

HERE = os.path.dirname(os.path.abspath(__file__))

SUPPLIER = [
    ["SHREE BALAJI PHARMA DISTRIBUTORS"],
    ["Shop 4, Station Road, Buldhana 443001"],
    ["Phone: 9822012345   GSTIN: 27ABCDE1234F1Z5"],
    ["D.L. No: 20B-12345, 21B-12346"],
    ["Invoice No: SBP/2026/1142", "", "", "", "", "Date: 18/09/2026"],
    [],
]
HEADER = ["Sr", "Product Name", "Pack", "Mfg", "HSN", "Batch", "Exp",
          "Qty", "Free", "MRP", "Rate", "Disc%", "GST%", "Amount"]
LINES = [
    # name,               pack,    mfg,          hsn,        batch,       exp,     qty, free, mrp,    rate,   disc, gst
    ("DOLO 650 TAB",        "15'S",  "MICRO LABS", "30049099", "DL24A1",   "08/27", 10, 1, 33.60,  24.10,  0, 12),
    ("PAN 40 TAB",          "10'S",  "ALKEM",      "30049099", "PN5521",   "11/27", 5,  0, 155.00, 110.00, 5, 12),
    ("CALGOPHOS",           "1LTR",  "VIR",        "23099090", "GK152",    "04/28", 3,  0, 715.00, 518.00, 0, 5),
    ("MERIFLOX BH",         "250ML", "VETOQUINOL", "30042019", "RMB26035", "06/28", 5,  0, 550.00, 442.00, 0, 5),
    ("SUPERCOX PDR",        "100GM", "INTAS",      "23099090", "RSC26029", "07/28", 3,  0, 480.00, 397.00, 5, 5),
    ("AMLOKIND 5 TAB",      "15'S",  "MANKIND",    "30049099", "AM771",    "09/27", 20, 2, 45.50,  30.20,  0, 12),
    ("NEUROBION FORTE TAB", "30'S",  "P&G HEALTH", "30045090", "NB8812",   "05/27", 4,  0, 38.90,  28.50,  0, 12),
    ("ZANDU COUGH SYP",     "100ML", "ZANDU",      "30049011", "ZC101",    "11/27", 6,  1, 95.00,  68.00,  2, 12),
]


def rows():
    out = []
    for i, (name, pack, mfg, hsn, batch, exp, qty, free, mrp, rate, disc, gst) in enumerate(LINES, 1):
        amount = round(qty * rate * (1 - disc / 100.0), 2)
        out.append([i, name, pack, mfg, hsn, batch, exp, qty, free, mrp, rate, disc, gst, amount])
    return out


def total():
    return round(sum(r[-1] for r in rows()), 2)


def write_csv(path):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        for r in SUPPLIER:
            w.writerow(r)
        w.writerow(HEADER)
        for r in rows():
            w.writerow(r)
        w.writerow(["", "TOTAL", "", "", "", "", "", "", "", "", "", "", "", total()])


def write_xlsx(path):
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "Invoice"
    for r in SUPPLIER:
        ws.append(r)
    ws.append(HEADER)
    for r in rows():
        ws.append(r)
    ws.append(["", "TOTAL", "", "", "", "", "", "", "", "", "", "", "", total()])
    wb.save(path)


def write_pdf(path):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    from reportlab.lib.styles import getSampleStyleSheet

    styles = getSampleStyleSheet()
    doc = SimpleDocTemplate(path, pagesize=landscape(A4), leftMargin=24, rightMargin=24,
                            topMargin=24, bottomMargin=24)
    story = [Paragraph("<b>SHREE BALAJI PHARMA DISTRIBUTORS</b>", styles["Title"])]
    for line in ("Shop 4, Station Road, Buldhana 443001",
                 "Phone: 9822012345   GSTIN: 27ABCDE1234F1Z5",
                 "D.L. No: 20B-12345, 21B-12346",
                 "Invoice No: SBP/2026/1142        Date: 18/09/2026"):
        story.append(Paragraph(line, styles["Normal"]))
    story.append(Spacer(1, 10))
    data = [HEADER] + [[str(c) for c in r] for r in rows()]
    data.append(["", "TOTAL", "", "", "", "", "", "", "", "", "", "", "", f"{total():.2f}"])
    t = Table(data, repeatRows=1)
    t.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
    ]))
    story.append(t)
    doc.build(story)


if __name__ == "__main__":
    write_xlsx(os.path.join(HERE, "distributor_bill.xlsx"))
    write_csv(os.path.join(HERE, "distributor_bill.csv"))
    write_pdf(os.path.join(HERE, "distributor_bill.pdf"))
    print("samples written; bill total", total())
