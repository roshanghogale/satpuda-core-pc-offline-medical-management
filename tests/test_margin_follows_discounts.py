"""The sales margin must follow every discount, the way Classic works it out.

Owner: "margin discount add kelya nantr punha calculate hot nahi aahe classic
sarkhi" -- after a discount the margin was not recomputed like Classic's.

Classic (core/margin_utils.py, repainted by ui/billing/billing_form.py
update_medicine_tree / calculate_total on every change):

    d      = tablets per strip for strip-counted types, else 1
    gross  = round2(max(0, (list_mrp - purchase_rate) / d * qty))
    net    = round2(max(0, gross - item_discount))
    value  = round2(round2(list_mrp / d) * qty)
    line % = round2(net / value * 100)
    bill   = round2(max(0, sum gross - sum item_discount - overall_discount_rs))
    bill % = round2(bill / round2(sum value) * 100)

MRP and the inventory purchase rate both include GST; the overall discount is
not spread over lines; rounding never enters it.

The Tauri sales screen only added up the margins frozen when each line was
built, so the overall discount never reached the total, a merged line kept its
old margin, and the engine's save-time loss check lost the purchase rate.
Nothing here saves, updates, deletes or touches the network.
"""
import json
import os
import random
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
from unittest import mock

from core import margin_utils as mu

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DESKTOP = os.path.join(ROOT, "desktop")
SALES_PAGE = os.path.join(DESKTOP, "src", "pages", "SalesPage.tsx")
MODULE = os.path.join(DESKTOP, "src", "salesMargin.ts")

# Worked lines. A: syrup, not strip-counted. B: tablet strip of 10, sold loose.
SYRUP = {
    "name": "COUGH SYRUP", "qty": 2, "list_mrp": 120.0, "mrp": 120.0,
    "purchase_rate": 90.0, "medicine_discount": 10.0,
    "type": "Syrup", "unit": "100ML",
}
TABLET = {
    "name": "PARA 500", "qty": 15, "list_mrp": 50.0, "mrp": 50.0,
    "purchase_rate": 35.0, "medicine_discount": 0.0,
    "type": "Tablet", "unit": "10",
}


def _line(med):
    m = dict(med)
    m["margin_div"] = mu.margin_unit_divisor(m)
    return m


RUNNER = r"""
import * as M from './salesMargin.mjs'
import { readFileSync } from 'node:fs'
const cases = JSON.parse(readFileSync(process.argv[2], 'utf8'))
const out = cases.map((c) => ({
  lines: c.items.map((m) => ({
    gross: M.lineGrossMargin(m),
    net: M.lineNetMargin(m),
    pct: M.lineMarginPercent(m),
    value: M.lineMrpValue(m),
  })),
  bill: M.billMargin(c.items, c.od),
  msgs: M.billDiscountLossMessages(c.items, c.od),
}))
console.log(JSON.stringify(out))
"""


def _classic(case):
    items, od = case["items"], case["od"]
    msgs = []
    for m in items:
        bad, msg = mu.check_item_discount_loss(m)
        if bad:
            msgs.append(msg)
    bad, msg = mu.check_overall_discount_loss(items, od)
    if bad:
        msgs.append(msg)
    return {
        "lines": [
            {
                "gross": mu.line_gross_margin(m),
                "net": mu.line_net_margin(m),
                "pct": mu.line_margin_percent(m),
                "value": mu.line_mrp_value(m),
            }
            for m in items
        ],
        "bill": {
            "rs": mu.total_net_margin(items, od),
            "pct": mu.total_margin_percent(items, od),
            "gross": mu.total_gross_margin(items),
        },
        "msgs": msgs,
    }


def _grid():
    rnd = random.Random(20260911)
    kinds = [
        ("Tablet", "10"), ("Capsule", "15"), ("Tablet", "1x10"),
        ("Syrup", "100ML"), ("Injection", "1"), ("Ointment", "30GM"),
    ]
    cases = [
        {"items": [_line(SYRUP), _line(TABLET)], "od": 20.0},
        {"items": [_line(SYRUP), _line(TABLET)], "od": 80.0},
        {"items": [_line(dict(SYRUP, qty=3))], "od": 0.0},
    ]
    for n in range(300):
        items = []
        for i in range(rnd.randint(1, 5)):
            t, u = rnd.choice(kinds)
            mrp = round(rnd.uniform(5, 600), 2)
            m = {
                "name": f"MED{n}-{i}", "qty": rnd.randint(1, 40),
                "list_mrp": mrp, "mrp": mrp,
                "purchase_rate": round(mrp * rnd.uniform(0.5, 1.05), 2),
                "medicine_discount": 0.0, "type": t, "unit": u,
            }
            gross = mu.line_gross_margin(m)
            if rnd.random() < 0.6:
                m["medicine_discount"] = round(rnd.uniform(0, gross * 1.3 + 1), 2)
            items.append(_line(m))
        total = sum(mu.line_mrp_value(m) for m in items)
        od = 0.0 if rnd.random() < 0.3 else round(rnd.uniform(0, total * 0.4), 2)
        cases.append({"items": items, "od": od})
    return cases


class ClassicFormulaWorkedNumbers(unittest.TestCase):
    """The reference: what Classic shows for the worked bill."""

    def test_line_margins(self):
        a, b = _line(SYRUP), _line(TABLET)
        # A: (120 - 90) x 2 = 60 gross, less Rs 10 item discount = 50; 50/240 = 20.83 %
        self.assertEqual(mu.line_gross_margin(a), 60.0)
        self.assertEqual(mu.line_net_margin(a), 50.0)
        self.assertEqual(mu.line_margin_percent(a), 20.83)
        # B: (50 - 35) / 10 x 15 tablets = 22.50; value 5.00 x 15 = 75; 30 %
        self.assertEqual(b["margin_div"], 10)
        self.assertEqual(mu.line_gross_margin(b), 22.5)
        self.assertEqual(mu.line_mrp_value(b), 75.0)
        self.assertEqual(mu.line_margin_percent(b), 30.0)

    def test_overall_discount_comes_off_the_bill_margin(self):
        meds = [_line(SYRUP), _line(TABLET)]
        # 82.50 gross - 10 item - 20 overall = 52.50 ; 52.50 / 315 = 16.67 %
        self.assertEqual(mu.total_net_margin(meds, 20.0), 52.5)
        self.assertEqual(mu.total_margin_percent(meds, 20.0), 16.67)
        # The old Tauri total: the frozen line margins added up, 50 + 22.50.
        self.assertNotEqual(sum(mu.line_net_margin(m) for m in meds), 52.5)

    def test_overall_discount_past_the_margin_is_a_loss(self):
        meds = [_line(SYRUP), _line(TABLET)]
        bad, msg = mu.check_overall_discount_loss(meds, 80.0)
        self.assertTrue(bad)
        self.assertIn("remaining margin ₹72.50", msg)
        self.assertEqual(mu.total_net_margin(meds, 80.0), 0.0)

    def test_enrich_stamps_the_pack_divisor(self):
        med = {"name": "PARA 500", "qty": 15}
        mu.enrich_medicine_margin_fields(med, 50.0, 35.0, "Tablet", "10")
        self.assertEqual(med["margin_div"], 10)
        med = {"name": "COUGH SYRUP", "qty": 2}
        mu.enrich_medicine_margin_fields(med, 120.0, 90.0, "Syrup", "100ML")
        self.assertEqual(med["margin_div"], 1)


class TauriMarginIsClassics(unittest.TestCase):
    """desktop/src/salesMargin.ts must give Classic's numbers, to the paisa."""

    def test_every_case_matches_classic(self):
        self.assertTrue(os.path.isfile(MODULE), "desktop/src/salesMargin.ts is missing")
        node, npx = shutil.which("node"), shutil.which("npx")
        if not node or not npx or not os.path.isdir(os.path.join(DESKTOP, "node_modules")):
            raise unittest.SkipTest("node, npx and desktop/node_modules are needed")
        cases = _grid()
        with tempfile.TemporaryDirectory() as tmp:
            bundle = os.path.join(tmp, "salesMargin.mjs")
            build = subprocess.run(
                [npx, "esbuild", "src/salesMargin.ts", "--bundle", "--format=esm",
                 f"--outfile={bundle}", "--log-level=error"],
                cwd=DESKTOP, capture_output=True, text=True,
            )
            self.assertEqual(build.returncode, 0, build.stderr[:500])
            data = os.path.join(tmp, "cases.json")
            with open(data, "w", encoding="utf-8") as fh:
                json.dump(cases, fh)
            runner = os.path.join(tmp, "run.mjs")
            with open(runner, "w", encoding="utf-8") as fh:
                fh.write(RUNNER)
            out = subprocess.run([node, runner, data], cwd=tmp,
                                 capture_output=True, text=True)
            self.assertEqual(out.returncode, 0, out.stderr[:500])
            got = json.loads(out.stdout.strip().splitlines()[-1])
        self.assertEqual(len(got), len(cases))
        # The worked bill first, so a failure names the numbers above.
        self.assertEqual(got[0]["bill"], {"rs": 52.5, "pct": 16.67, "gross": 82.5})
        self.assertEqual(got[2]["lines"][0]["net"], 80.0)  # merged: 3 x 30 - 10
        self.assertEqual(len(got[1]["msgs"]), 1)
        bad = [
            (i, _classic(c), g) for i, (c, g) in enumerate(zip(cases, got))
            if _classic(c) != g
        ]
        self.assertFalse(bad, f"{len(bad)} bills differ from Classic, first: {bad[:1]}")


class EngineLossChecksUseTheCost(unittest.TestCase):
    """The save and preview loss checks must see the purchase rate."""

    def _items(self):
        return [dict(_line(SYRUP), id=1, rate=120.0, amount=230.0,
                     original_amount=240.0),
                dict(_line(TABLET), id=2, rate=5.0, amount=75.0,
                     original_amount=75.0)]

    def test_save_check_sees_the_overall_loss(self):
        from core import desktop_sales_service as svc
        with mock.patch.object(mu, "margin_loss_warning_enabled", return_value=True):
            msgs = svc._save_discount_loss_messages(self._items(), 80.0)
            self.assertEqual(len(msgs), 1)
            self.assertIn("remaining margin ₹72.50", msgs[0])
            self.assertEqual(svc._save_discount_loss_messages(self._items(), 20.0), [])

    def test_save_path_uses_it(self):
        with open(os.path.join(ROOT, "core", "desktop_sales_service.py"),
                  encoding="utf-8") as fh:
            src = fh.read()
        self.assertIn(
            'loss_msgs = _save_discount_loss_messages(body.get("items") or [], disc_rs)',
            src,
        )

    def test_preview_warns_on_the_overall_loss(self):
        from core import desktop_sales_service as svc
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        body ={"items": self._items(), "discount_rs": 80.0, "discount_pct": 0,
                "rounding": 0, "auto_rounding": False, "skip_party_due": True,
                "payment_mode": "Cash"}
        with mock.patch.object(mu, "margin_loss_warning_enabled", return_value=True):
            res = svc.calc_sale(conn, body)
        self.assertTrue(res.get("ok"), res)
        self.assertTrue(any("remaining margin ₹72.50" in w for w in res["warnings"]),
                        res["warnings"])


class SalesPageRecomputes(unittest.TestCase):
    def setUp(self):
        with open(SALES_PAGE, encoding="utf-8") as fh:
            self.src = fh.read()

    def test_total_margin_takes_the_overall_discount(self):
        self.assertIn("billMargin(tab.items, tab.overallDisc, packDivisor)", self.src)
        self.assertNotIn("tab.items.reduce((s, it) => s + (it.margin || 0), 0)", self.src)

    def test_line_margin_is_recomputed(self):
        self.assertIn("lineMargin(it, packDivisor)", self.src)

    def test_merge_and_edit_warn_on_the_recomputed_margin(self):
        self.assertIn("itemDiscountLoss(merged, packDivisor)", self.src)
        self.assertIn("billDiscountLossMessages(\n              tabRef.current.items.map", self.src)
        self.assertIn("billDiscountLossMessages(t.items, t.overallDisc, packDivisor)", self.src)


if __name__ == "__main__":
    unittest.main()
