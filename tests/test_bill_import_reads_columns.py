"""A supplier bill import reads its columns by what they hold, not where they sit.

Owner, 19 Oct 2026: "PDF import is not getting the values correct -- the batch is
mixing with the manufacturer. There are different formats; the columns can't be
the same in every bill." A fixed left-to-right reader of a Marg ERP PDF put the
batch in the maker column on a line whose pack was blank, read a pack jammed
onto its maker code ("450GMVET") as the maker, and dropped every line's GST%.

Every bill here is made up (names, makers, batches); only the STRUCTURE copies
the real ones: a Marg ERP PDF's text lines, the same bill's "<MARGERP FORMAT>"
CSV, distributor CSV / Excel sheets with renamed and shuffled headings, a sheet
whose batch and maker headings are swapped, and a photo reader's reply.
"""
import csv
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ---------------------------------------------------------------- the bill
# name, pack, maker code, maker company, batch, exp (m/yy), qty, free, rate, mrp, hsn, gst
LINES = [
    ("ALPHAVET CARE BOLUS", "4'S", "ALP", "ALPHA VETCARE", "AV-1187", "3/29", 4.5, 0.5, 52.40, 66.00, "2309", 0),
    ("BETAMIN ULTRA MAX", "23GM", "BET", "BETA LABS PVT LTD", "BX219", "11/27", 2.0, 0.0, 198.40, 248.00, "2309", 0),
    ("GAMMACAL SR BOLUS 4'S", "4'S", "GAM", "GAMMA PHARMA", "GC26-311", "9/27", 4.5, 0.5, 71.27, 92.10, "3004", 5),
    ("DELTACEF 562.5MG INJ 1", "1VIAL", "DEL", "DELTA HEALTH", "DLT27014", "5/28", 10.0, 2.0, 48.57, 64.00, "3004", 5),
    ("EPSIVET BOLUS", "", "DEL", "DELTA HEALTH", "EPV27002", "6/28", 5.0, 0.0, 58.00, 72.50, "2309", 0),
    ("ZETAFER 450GM", "450GM", "ZET", "ZETA ANIMAL CARE", "ZF7712045", "2/28", 2.0, 0.0, 141.20, 176.50, "2309", 0),
    ("ETAMOX INJ 300MG", "300MG", "ETA", "ETA BIOTECH", "EM2207", "10/27", 5.0, 0.0, 38.10, 50.00, "3004", 5),
    ("THETALAX LIQ 120ML", "120ML", "Z A", "Z ALPHA REMEDIES", "31947", "7/28", 5.5, 0.5, 49.52, 65.00, "3004", 5),
]


def _amount(qty, rate):
    return round(qty * rate + 1e-9, 2)


def marg_pdf_text():
    """Text as pdfplumber gives a Marg 'MRP | HSN | Description' bill: the blank
    pack, the pack jammed onto its maker code, a two-letter maker 'Z A', and the
    pack printed twice (once in the description)."""
    head = [
        "DEMO MEDICAL DISTRIBUTORS",
        "GST INVOICE CREDIT",
        "Invoice No. : A000101 Date : 15-07-2026 11:32",
        "MRP HSN D e s c r i p t i o n Pack MFG Batch Exp. QTY. FREE RATE DIS% GST% AMOUNT",
    ]
    rows = []
    for name, pack, code, _co, batch, exp, qty, free, rate, mrp, hsn, gst in LINES:
        middle = name
        if pack and name == "ZETAFER 450GM":
            middle += " " + pack + code  # 450GMZET: no space between pack and maker
        elif pack:
            middle += " {} {}".format(pack, code)
        else:
            middle += " " + code
        rows.append("{:.2f} {} {} {} {} {:.1f} {:.1f} {:.2f} 0% {:.2f} {:.2f}".format(
            mrp, hsn, middle, batch, exp, qty, free, rate, gst, _amount(qty, rate)))
    sub = round(sum(_amount(l[6], l[8]) for l in LINES), 2)
    gst5 = round(sum(_amount(l[6], l[8]) for l in LINES if l[11] == 5), 2)
    half = round(gst5 * 0.025, 2)
    grand = round(sub + 2 * half)
    foot = [
        "Class SGST CGST TOTAL ITEMS/QTY. : 8 / 40 SUB TOTAL {:.2f}".format(sub),
        "0.00% {0:.2f} 0.00 {0:.2f} 0.00 ADD SGST {1:.2f}".format(sub - gst5, half),
        "5.00% {0:.2f} {1:.2f} {0:.2f} {1:.2f} ADD CGST {1:.2f}".format(gst5, half),
        "GRAND TOTAL {:.1f}".format(grand),
        "MARG ERP NANO for Chemist",
    ]
    return "\n".join(head + rows + foot), sub, half, grand


def write_marg_csv(path):
    """The same bill as Marg's '<MARGERP FORMAT>' export: H, T and F rows."""
    sub = round(sum(_amount(l[6], l[8]) for l in LINES), 2)
    gst_amt = 0.0
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        h = [""] * 53
        h[0], h[1], h[2], h[3], h[52] = "H", "1", "A000101", "15072026", "<MARGERP FORMAT>"
        w.writerow(h)
        for name, pack, code, company, batch, exp, qty, free, rate, mrp, hsn, gst in LINES:
            t = [""] * 51
            m, y = exp.split("/")
            amount = _amount(qty, rate)
            g_amt = round(amount * gst / 100, 2)
            gst_amt += g_amt
            t[0], t[1], t[2], t[3] = "T", "AB" + code[:2].replace(" ", "X"), company, "1001"
            t[5], t[6], t[7], t[8] = name.replace("'", " "), pack.replace("'", " "), "", batch
            t[9] = "01{:02d}20{}".format(int(m), y)
            t[12], t[14], t[15], t[16] = str(gst), str(rate), "0.00", str(mrp)
            t[20], t[21], t[25], t[26] = str(qty), str(free), str(amount), str(g_amt)
            t[29], t[38], t[41] = "G", hsn + "90", str(rate)
            w.writerow(t)
        f = [""] * 11
        f[0], f[1], f[2], f[9] = "F", str(round(sub + gst_amt)), "0.00", "{:.2f}".format(gst_amt)
        w.writerow(f)
    return sub, round(gst_amt, 2)


class MargPdfTextReadsByHeader(unittest.TestCase):
    def test_every_line_comes_in_as_printed(self):
        from core.bill_columns import check_records, records_from_text

        text, _sub, _half, _grand = marg_pdf_text()
        recs = check_records(records_from_text(text))
        self.assertEqual(len(recs), len(LINES))
        for rec, line in zip(recs, LINES):
            name, pack, code, _co, batch, exp, qty, free, rate, mrp, hsn, gst = line
            with self.subTest(name=name):
                self.assertEqual(rec["name"], name)
                self.assertEqual(rec.get("pack", ""), pack)
                self.assertEqual(rec["manufacturer"], code)
                self.assertEqual(rec["batch"], batch)
                self.assertEqual(rec["expiry"], exp)
                self.assertAlmostEqual(float(rec["qty"]), qty)
                self.assertAlmostEqual(float(rec["free_qty"]), free)
                self.assertAlmostEqual(float(rec["rate"]), rate)
                self.assertAlmostEqual(float(rec["mrp"]), mrp)
                self.assertEqual(rec["hsn_code"], hsn)
                self.assertAlmostEqual(float(rec["gst_pct"]), gst)
                self.assertNotIn("check_fields", rec)

    def test_the_pdf_import_uses_it_and_the_totals_match(self):
        from core import purchase_importer as pi

        text, sub, half, grand = marg_pdf_text()
        tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
        tmp.write(b"%PDF-1.4\n")
        tmp.close()
        self.addCleanup(os.remove, tmp.name)
        with mock.patch.object(pi, "_parse_pdf_with_pdfplumber", return_value=([], text)):
            inv = pi.parse_purchase_pdf(tmp.name)
        self.assertIn("columns-by-header", inv.parser)
        self.assertEqual(len(inv.items), len(LINES))
        for it, line in zip(inv.items, LINES):
            self.assertEqual((it.name, it.pack, it.manufacturer, it.batch),
                             (line[0], line[1], line[2], line[4]))
            self.assertAlmostEqual(it.gst_pct, line[11])
            self.assertAlmostEqual(it.amount, _amount(line[6], line[8]))
        self.assertAlmostEqual(sum(it.amount for it in inv.items), sub, places=2)
        self.assertAlmostEqual(inv.total_cgst, half)
        self.assertAlmostEqual(inv.total_sgst, half)
        self.assertAlmostEqual(inv.invoice_total, grand)
        # No round-off line on the bill: the paise to the whole rupee are the
        # round-off, never an "added charge" counted a second time.
        self.assertAlmostEqual(inv.added_charges or 0.0, 0.0)
        self.assertAlmostEqual(inv.round_off, round(grand - sub - 2 * half, 2))
        # Both GST slabs, though the footer's 0% row was not read as one.
        rates = sorted(float(s["gst_pct"]) for s in inv.footer_gst_slabs)
        self.assertEqual(rates, [0.0, 5.0])


class MargCsvExport(unittest.TestCase):
    def test_marg_h_t_f_csv(self):
        from core.purchase_importer import parse_purchase_excel

        d = tempfile.mkdtemp()
        path = os.path.join(d, "bill.csv")
        sub, gst_amt = write_marg_csv(path)
        inv = parse_purchase_excel(path)
        self.assertTrue(inv.parser.startswith("EDI H/T/F (marg)"))
        self.assertEqual(len(inv.items), len(LINES))
        for it, line in zip(inv.items, LINES):
            name, pack, _code, company, batch, exp, qty, free, rate, mrp, _hsn, gst = line
            with self.subTest(name=name):
                self.assertEqual(it.name, name.replace("'", " "))
                self.assertEqual(it.pack, pack.replace("'", " "))
                self.assertEqual(it.manufacturer, company)  # T[2], not the blank T[7]
                self.assertEqual(it.batch, batch)
                m, y = exp.split("/")
                self.assertEqual(it.expiry, "{:02d}/{}".format(int(m), y))
                self.assertAlmostEqual(it.qty, qty)
                self.assertAlmostEqual(it.free_qty, free)
                self.assertAlmostEqual(it.rate, rate)
                self.assertAlmostEqual(it.mrp, mrp)
                self.assertAlmostEqual(it.gst_pct, gst)
                self.assertNotIn("check_fields", it.raw)
        self.assertAlmostEqual(sum(it.amount for it in inv.items), sub, places=2)
        self.assertAlmostEqual(inv.total_cgst + inv.total_sgst, gst_amt, places=2)
        self.assertAlmostEqual(inv.invoice_total, round(sub + gst_amt))


# ------------------------------------------------- distributor sheets
SHEET_ROWS = [
    # name, pack, maker, hsn, batch, exp, qty, free, mrp, rate, disc%, gst
    ("KAPPA 650 TAB", "15'S", "KAPPA LABS", "30049099", "KL24A1", "08/27", 10, 1, 33.60, 24.10, 0, 12),
    ("LAMBDA 40 TAB", "10'S", "LAMBDA", "30049099", "LM5521", "11/27", 5, 0, 155.00, 110.00, 5, 12),
    ("MUCAL LIQUID", "1LTR", "MU VET", "23099090", "MC152", "04/28", 3, 0, 715.00, 518.00, 0, 5),
    ("NUFLOX BH", "250ML", "NU ANIMAL", "30042019", "NF26035", "06/28", 5, 0, 550.00, 442.00, 2, 5),
]


def _line_amount(qty, rate, disc):
    return round(qty * rate * (1 - disc / 100.0), 2)


def write_sheet(path, header, order, extra_rows=(), swap_batch_maker=False):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["DEMO DISTRIBUTORS, STATION ROAD"])
        w.writerow(["Invoice No: DD/2026/77", "", "Date: 18/09/2026"])
        w.writerow([])
        w.writerow(header)
        for i, (name, pack, mfg, hsn, batch, exp, qty, free, mrp, rate, disc, gst) in enumerate(SHEET_ROWS, 1):
            if swap_batch_maker:
                batch, mfg = mfg, batch
            vals = {
                "sr": i, "name": name, "pack": pack, "mfg": mfg, "hsn": hsn,
                "batch": batch, "exp": exp, "qty": qty, "free": free, "mrp": mrp,
                "rate": rate, "disc": disc, "gst": gst,
                "amount": _line_amount(qty, rate, disc),
            }
            w.writerow([vals[k] for k in order])
        for r in extra_rows:
            w.writerow(r)


class DistributorSheets(unittest.TestCase):
    def _check(self, inv):
        self.assertEqual(len(inv.items), len(SHEET_ROWS))
        for it, row in zip(inv.items, SHEET_ROWS):
            name, pack, mfg, hsn, batch, exp, qty, free, mrp, rate, disc, gst = row
            with self.subTest(name=name):
                self.assertEqual((it.name, it.pack, it.manufacturer, it.batch, it.expiry),
                                 (name, pack, mfg, batch, exp))
                self.assertEqual(it.hsn_code, hsn)
                self.assertAlmostEqual(it.qty, qty)
                self.assertAlmostEqual(it.free_qty, free)
                self.assertAlmostEqual(it.mrp, mrp)
                self.assertAlmostEqual(it.rate, rate)
                self.assertAlmostEqual(it.gst_pct, gst)
                self.assertAlmostEqual(it.amount, _line_amount(qty, rate, disc))
                # qty x rate less Disc% is the amount: the bill proves the percent.
                self.assertAlmostEqual(it.discount_pct, disc)

    def test_renamed_and_shuffled_headings_and_a_total_row(self):
        from core.purchase_importer import parse_purchase_excel

        path = os.path.join(tempfile.mkdtemp(), "bill.csv")
        write_sheet(
            path,
            ["Net Amt", "Particulars", "Co.", "B.No", "Exp Dt", "Packing", "M.R.P.",
             "Sch Qty", "Qty", "PTR", "Disc %", "Tax %", "HSN/SAC"],
            ["amount", "name", "mfg", "batch", "exp", "pack", "mrp", "free", "qty",
             "rate", "disc", "gst", "hsn"],
            extra_rows=[["4567.89", "TOTAL"]],
        )
        self._check(parse_purchase_excel(path))

    def test_excel_sheet(self):
        import openpyxl

        from core.purchase_importer import parse_purchase_excel

        path = os.path.join(tempfile.mkdtemp(), "bill.xlsx")
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["DEMO DISTRIBUTORS"])
        ws.append(["Item Description", "HSN Code", "Batch No", "Expiry", "Mfr", "Pack Size",
                   "Quantity", "Free", "Rate", "MRP", "Disc%", "GST%", "Amount"])
        for name, pack, mfg, hsn, batch, exp, qty, free, mrp, rate, disc, gst in SHEET_ROWS:
            ws.append([name, hsn, batch, exp, mfg, pack, qty, free, rate, mrp, disc, gst,
                       _line_amount(qty, rate, disc)])
        wb.save(path)
        self._check(parse_purchase_excel(path))

    def test_batch_and_maker_headings_swapped(self):
        """The 'Batch' column holds the makers and the 'Mfg' column the batches."""
        from core.purchase_importer import parse_purchase_excel

        path = os.path.join(tempfile.mkdtemp(), "bill.csv")
        write_sheet(
            path,
            ["Sr", "Product Name", "Pack", "Mfg", "HSN", "Batch", "Exp", "Qty", "Free",
             "MRP", "Rate", "Disc%", "GST%", "Amount"],
            ["sr", "name", "pack", "mfg", "hsn", "batch", "exp", "qty", "free", "mrp",
             "rate", "disc", "gst", "amount"],
            swap_batch_maker=True,
        )
        inv = parse_purchase_excel(path)
        self._check(inv)
        self.assertTrue(any("not batch" in i or "not manufacturer" in i for i in inv.issues))

    def test_no_heading_row_at_all(self):
        from core.bill_columns import records_from_rows

        rows = []
        for name, pack, mfg, hsn, batch, exp, qty, free, mrp, rate, disc, gst in SHEET_ROWS:
            rows.append([name, pack, mfg, hsn, batch, exp, qty, free, mrp, rate, gst,
                         _line_amount(qty, rate, 0)])
        recs, info = records_from_rows(rows)
        self.assertEqual(info["source"], "shape")
        self.assertEqual(len(recs), len(SHEET_ROWS))
        for rec, row in zip(recs, SHEET_ROWS):
            self.assertEqual(rec["name"], row[0])
            self.assertEqual(rec["batch"], row[4])
            self.assertEqual(rec["expiry"], row[5])
            self.assertEqual(rec["hsn_code"], row[3])
            self.assertAlmostEqual(float(rec["qty"]), row[6])
            self.assertAlmostEqual(float(rec["rate"]), row[9])
            self.assertAlmostEqual(float(rec["mrp"]), row[8])

    def test_wholesale_rates_only_when_the_bill_prints_them(self):
        """A wholesaler's own Rate A / B / C come in only from columns so headed;
        MRP stays the bill's MRP either way."""
        from core.bill_columns import records_from_rows

        header = ["Product", "Batch", "Exp", "Qty", "Rate", "MRP", "Amount"]
        body = [["KAPPA 650 TAB", "KL24A1", "08/27", "10", "24.10", "33.60", "241.00"],
                ["LAMBDA 40 TAB", "LM5521", "11/27", "5", "110.00", "155.00", "550.00"]]
        plain, _ = records_from_rows([header] + body)
        self.assertTrue(all("rate_a" not in r for r in plain))
        self.assertEqual([float(r["mrp"]) for r in plain], [33.60, 155.00])
        with_rates, _ = records_from_rows(
            [header + ["Rate A", "Rate B"]] + [row + ["27.00", "26.00"] for row in body]
        )
        self.assertEqual([r["rate_a"] for r in with_rates], ["27.00", "27.00"])
        self.assertEqual([float(r["mrp"]) for r in with_rates], [33.60, 155.00])


class OneLineChecks(unittest.TestCase):
    def test_swapped_batch_and_maker_cells(self):
        from core.bill_columns import check_record

        rec = check_record({"name": "OMEGA TAB", "batch": "OMG", "manufacturer": "OB2231",
                            "expiry": "05/28", "qty": 10, "rate": 12.5, "amount": 125})
        self.assertEqual((rec["batch"], rec["manufacturer"]), ("OB2231", "OMG"))
        self.assertNotIn("check_fields", rec)

    def test_mrp_and_rate_swapped_are_put_back_by_the_amount(self):
        from core.bill_columns import check_record

        rec = check_record({"name": "OMEGA TAB", "batch": "OB2231", "expiry": "05/28",
                            "qty": 10, "rate": 16.0, "mrp": 12.5, "amount": 125})
        self.assertEqual((float(rec["rate"]), float(rec["mrp"])), (12.5, 16.0))

    def test_what_cannot_be_proven_is_marked_not_guessed(self):
        from core.bill_columns import check_record

        rec = check_record({"name": "OMEGA TAB", "batch": "OB2231", "expiry": "13/2X",
                            "qty": 10, "rate": 12.5, "mrp": 16, "amount": 140, "gst_pct": 7})
        self.assertEqual(float(rec["rate"]), 12.5)  # nothing swapped
        for f in ("qty", "rate", "amount", "expiry", "gst_pct"):
            self.assertIn(f, rec["check_fields"])

    def test_marks_reach_the_purchase_line(self):
        from core.purchase_importer import (
            ImportedPurchaseItem, PurchaseInvoice, _build_import_payload, check_invoice_items,
        )

        inv = PurchaseInvoice(items=[ImportedPurchaseItem(
            name="OMEGA TAB", batch="OB2231", expiry="05/28", qty=10, rate=12.5,
            mrp=16, amount=150, gst_pct=12)], parser="csv")
        check_invoice_items(inv)

        class Page:
            _med_types = []

        payload = _build_import_payload(Page(), inv, inv.items, None)
        line = payload["converted"][0]
        self.assertIn("amount", line["check_fields"])
        self.assertIn("Amount", line["check_note"])


class PhotoReader(unittest.TestCase):
    """A photo reader's reply is checked like any other bill, and read twice."""

    def _reply(self):
        return {
            "columns": ["Sr.", "HSN", "MFG", "Name of Product", "Pkg.", "Batch", "Exp.",
                        "MRP", "Qty", "Free", "Rate", "Amount", "Disc", "GST %"],
            "column_values": {"MRP": ["18.00", "24.00"], "Batch": ["731265", "K31K020400"]},
            "supplier": {"name": "DEMO SURGICALS", "invoice_number": "D1", "invoice_date": "2026-10-12"},
            "totals": {},
            "items": [
                {"line_no": 1, "row_text": "1 9018 PQ. SIGMA SET 12NO. 1 731265 05/30 18.00 120 0 4.10 492.00 0.00 5",
                 "name": "SIGMA SET 12NO.", "manufacturer": "PQ.", "pack": "1", "batch": "731265",
                 "expiry": "05/30", "mrp": 24, "qty": 120, "free_qty": 0, "rate": 4.1,
                 "amount": 492, "gst_pct": 5, "hsn_code": "9018"},
                {"line_no": 2, "row_text": "2 9018 RSTUV SIGMA SET NO 12 1 S K31K020400 08/30 24.00 80 0 6.25 500.00 0.00 5",
                 "name": "RSTUV SIGMA SET NO 12", "manufacturer": "RST", "pack": "1 S",
                 "batch": "K31K020400", "expiry": "08/30", "mrp": 24, "qty": 80, "free_qty": 0,
                 "rate": 6.25, "amount": 500, "gst_pct": 5, "hsn_code": "9018"},
            ],
        }

    def test_maker_out_of_the_name_and_the_disputed_mrp_marked(self):
        from core.gemini_bill_parser import _json_to_invoice
        from core.purchase_importer import _finalize_parsed_invoice

        inv = _json_to_invoice(self._reply(), "bill.jpg")
        _finalize_parsed_invoice(inv, enrich_metadata=False)
        first, second = inv.items
        self.assertEqual(second.name, "SIGMA SET NO 12")
        self.assertEqual(second.manufacturer, "RSTUV")
        # Line 1: the fields say MRP 24, its own row and its column say 18 -- the
        # arithmetic cannot settle an MRP, so the cell is marked, not guessed.
        self.assertIn("mrp", first.raw.get("check_fields", []))
        self.assertNotIn("mrp", second.raw.get("check_fields", []))

    def test_a_sideways_photo_is_turned_upright(self):
        from PIL import Image, ImageDraw

        from core.bill_photo_orientation import looks_sideways, upright_copies

        img = Image.new("L", (900, 1200), 255)
        draw = ImageDraw.Draw(img)
        for y in range(40, 1160, 28):
            x = 40
            while x < 860:
                w = 30 + (x * 7 + y) % 50
                draw.rectangle([x, y, min(x + w, 860), y + 12], fill=0)
                x += w + 12
        self.assertFalse(looks_sideways(img))
        side = img.rotate(90, expand=True)
        self.assertTrue(looks_sideways(side))
        path = os.path.join(tempfile.mkdtemp(), "side.png")
        side.save(path)
        out, temps = upright_copies([path])
        self.addCleanup(lambda: [os.remove(t) for t in temps])
        self.assertEqual(len(temps), 1)
        with Image.open(out[0]) as turned:
            self.assertFalse(looks_sideways(turned))
        upright = os.path.join(tempfile.mkdtemp(), "up.png")
        img.save(upright)
        self.assertEqual(upright_copies([upright]), ([upright], []))


class MargSerialLayout(unittest.TestCase):
    """The other Marg layout ('Sn. Mfg. Item Name Pack Hsn ... SGST CGST Amount'):
    the header reader and the old fixed reader agree line for line."""

    TEXT = "\n".join([
        "ORIGINAL FOR BUYER GST INVOICE",
        "Sn. Mfg. Item Name Pack Hsn Batch Exp. Qty Free Mrp Rate Dis SGST CGST Amount",
        "1. OMNI RHO-40MG IV INJ IV 3004 25770097 11/27 5 5 53.90 35.68 0.00 6.00 6.00 178.40",
        "2. SIGL SIGMOX 500 TAB 10*S 3004 SX2231 05/28 10 0 42.00 30.10 0.00 6.00 6.00 301.00",
        "MARG ERP",
    ])

    def test_same_lines_as_the_fixed_reader(self):
        from core.bill_columns import records_from_text
        from core.marg_erp_parser import parse_marg_sn_item_lines

        old = parse_marg_sn_item_lines(self.TEXT)
        new = records_from_text(self.TEXT)
        self.assertEqual(len(new), 2)
        for a, b in zip(old, new):
            for f in ("manufacturer", "name", "pack", "hsn_code", "batch", "expiry"):
                self.assertEqual(a[f], b[f], f)
            for f in ("qty", "free_qty", "mrp", "rate", "amount"):
                self.assertAlmostEqual(float(a[f]), float(b[f]), msg=f)
            self.assertAlmostEqual(a["gst_pct"], float(b["sgst_pct"]) + float(b["cgst_pct"]))


if __name__ == "__main__":
    unittest.main()
