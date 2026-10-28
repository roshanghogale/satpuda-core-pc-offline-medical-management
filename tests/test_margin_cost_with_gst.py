"""Settings > Sales & Billing > Margin cost: purchase rate excluding or including GST (28 Oct 2026).

A shop compared one bill in both programs: Satpuda showed margin 84.06,
Medivision 76.69. MRP includes GST, but the purchase rate Satpuda takes off it is the rate
BEFORE GST, so every margin came out about 5% of cost higher. The shop picks: "excluding GST"
(the old figure, still the default) or "including GST" (purchase rate + the line's GST, the
landing cost Medivision uses). The engine (core.margin_utils) and the Sales screen
(desktop/src/salesMargin.ts) must agree in both settings.

Nothing here saves, updates, deletes or touches the network.
"""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from core import margin_utils as mu

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DESKTOP = os.path.join(ROOT, "desktop")

# That shop's bill of 28 Oct 2026 (purchase rates as stored for store 154).
BILL = [
    {"name": "DOXYTOP L", "qty": 6, "list_mrp": 92.63, "purchase_rate": 70.58,
     "gst_percent": 5, "type": "Tablet", "unit": "10", "medicine_discount": 0},
    {"name": "PENTAB 40", "qty": 3, "list_mrp": 184.7, "purchase_rate": 38.95,
     "gst_percent": 5, "type": "Tablet", "unit": "15", "medicine_discount": 0},
    {"name": "ZERODOL SP", "qty": 6, "list_mrp": 150.85, "purchase_rate": 114.94,
     "gst_percent": 5, "type": "Tablet", "unit": "10", "medicine_discount": 0},
    {"name": "O2 BACT CREAM", "qty": 1, "list_mrp": 126.56, "purchase_rate": 96.43,
     "gst_percent": 5, "type": "Cream", "unit": "5 GM", "medicine_discount": 0},
]
OVERALL_DISC = 10.0


def _lines():
    out = []
    for m in BILL:
        m = dict(m, mrp=m["list_mrp"])
        m["margin_div"] = mu.margin_unit_divisor(m)
        out.append(m)
    return out


def _engine(with_gst: bool):
    with mock.patch.object(mu, "margin_cost_with_gst", return_value=with_gst):
        items = _lines()
        return {
            "lines": [mu.line_net_margin(m) for m in items],
            "rs": mu.total_net_margin(items, OVERALL_DISC),
            "pct": mu.total_margin_percent(items, OVERALL_DISC),
        }


class EngineMargin(unittest.TestCase):
    def test_excluding_gst_is_the_old_figure(self):
        self.assertEqual(_engine(False)["rs"], 84.06)

    def test_including_gst_takes_the_gst_off_the_margin(self):
        got = _engine(True)
        # 84.06 less 5% GST on 215.53 of purchase cost (10.78), rounded line by line.
        self.assertEqual(got["rs"], 73.28)
        self.assertEqual(got["lines"][3], 25.31)  # O2: 126.56 - 96.43 x 1.05

    def test_no_gst_line_is_the_same_either_way(self):
        m = dict(_lines()[3], gst_percent=0)
        with mock.patch.object(mu, "margin_cost_with_gst", return_value=True):
            on = mu.line_gross_margin(m)
        with mock.patch.object(mu, "margin_cost_with_gst", return_value=False):
            off = mu.line_gross_margin(m)
        self.assertEqual(on, off)

    def test_the_default_is_excluding_gst(self):
        from core import billing_layout_prefs as blp

        self.assertIs(blp._DEFAULTS["billing_margin_cost_with_gst"], False)
        self.assertIs(blp._normalize({})["billing_margin_cost_with_gst"], False)
        self.assertIs(blp._normalize({"billing_margin_cost_with_gst": 1})["billing_margin_cost_with_gst"], True)


RUNNER = r"""
import * as M from './salesMargin.mjs'
import { readFileSync } from 'node:fs'
const c = JSON.parse(readFileSync(process.argv[2], 'utf8'))
const out = {}
for (const on of [false, true]) {
  M.setMarginCostWithGst(on)
  const b = M.billMargin(c.items, c.od)
  out[on ? 'on' : 'off'] = { lines: c.items.map((m) => M.lineNetMargin(m)), rs: b.rs, pct: b.pct }
}
console.log(JSON.stringify(out))
"""


class SalesScreenAgrees(unittest.TestCase):
    def test_both_settings_match_the_engine(self):
        node, npx = shutil.which("node"), shutil.which("npx")
        if not node or not npx or not os.path.isdir(os.path.join(DESKTOP, "node_modules")):
            raise unittest.SkipTest("node, npx and desktop/node_modules are needed")
        with tempfile.TemporaryDirectory() as tmp:
            bundle = os.path.join(tmp, "salesMargin.mjs")
            build = subprocess.run(
                [npx, "esbuild", "src/salesMargin.ts", "--bundle", "--format=esm",
                 f"--outfile={bundle}", "--log-level=error"],
                cwd=DESKTOP, capture_output=True, text=True,
            )
            self.assertEqual(build.returncode, 0, build.stderr[:500])
            data = os.path.join(tmp, "case.json")
            with open(data, "w", encoding="utf-8") as fh:
                json.dump({"items": _lines(), "od": OVERALL_DISC}, fh)
            runner = os.path.join(tmp, "run.mjs")
            with open(runner, "w", encoding="utf-8") as fh:
                fh.write(RUNNER)
            out = subprocess.run([node, runner, data], cwd=tmp, capture_output=True, text=True)
            self.assertEqual(out.returncode, 0, out.stderr[:500])
            got = json.loads(out.stdout.strip().splitlines()[-1])
        self.assertEqual(got["off"], _engine(False))
        self.assertEqual(got["on"], _engine(True))


class TheSettingIsWired(unittest.TestCase):
    def _src(self, *parts):
        with open(os.path.join(DESKTOP, "src", *parts), encoding="utf-8") as fh:
            return fh.read()

    def test_sales_page_reads_it(self):
        self.assertIn("setMarginCostWithGst(p?.billing_margin_cost_with_gst === true)",
                      self._src("pages", "SalesPage.tsx"))

    def test_settings_saves_it(self):
        src = self._src("pages", "settings", "PrefsPanels.tsx")
        self.assertIn("'billing_margin_cost_with_gst',", src)
        self.assertIn("Purchase rate including GST", src)

    def test_settings_service_keeps_it(self):
        from core import desktop_settings_service as dss

        with open(dss.__file__, encoding="utf-8") as fh:
            src = fh.read()
        self.assertIn('"billing_margin_cost_with_gst",', src)


if __name__ == "__main__":
    unittest.main()
