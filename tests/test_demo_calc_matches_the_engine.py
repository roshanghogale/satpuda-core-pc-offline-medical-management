"""The demo's billing arithmetic must be the engine's, to the paisa.

The demonstration site has no engine behind it, and `POST /api/sales/calc`
cannot be answered from a recording: it takes the bill the prospect is building
right now. So desktop/src/demoCalc.ts is a port of core.calc_engine's
calc_bill_summary and calc_payment_result plus the sale rounding rule.

A port written from a description is a guess, and a guess in the money panel is
a figure that does not add up in front of a customer. tests/data/demo_calc_grid.json
holds 360 answers recorded from the real engine over a randomised grid of bills
-- several line items, per-item and overall discounts in both percent and rupees,
auto and manual rounding, cash / online / due / split, previous dues and credits.
Every one of them has to match exactly.

The grid was recorded read-only against the ZZ Test store: core.server_api._request
was locked to GET (plus the two authentication calls) for the whole run, so
nothing was saved, changed or deleted.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GRID = os.path.join(ROOT, "tests", "data", "demo_calc_grid.json")
DESKTOP = os.path.join(ROOT, "desktop")

RUNNER = r"""
import { demoSalesCalc } from './demoCalc.mjs'
import { readFileSync } from 'node:fs'
const cases = JSON.parse(readFileSync(process.argv[2], 'utf8'))
const CHECK = ['summary', 'payment', 'cash_paid', 'online_paid', 'rounding',
               'is_due', 'previous_due', 'previous_credit', 'gst_label']
const bad = []
for (const c of cases) {
  const got = demoSalesCalc(c.body)
  for (const k of CHECK) {
    if (JSON.stringify(c.expected[k]) !== JSON.stringify(got[k])) {
      bad.push(k + ': engine ' + JSON.stringify(c.expected[k]) +
               ' vs demo ' + JSON.stringify(got[k]) +
               '  for ' + JSON.stringify(c.body))
      break
    }
  }
}
console.log(JSON.stringify({ total: cases.length, bad: bad.slice(0, 5), failures: bad.length }))
"""


class DemoCalcMatchesTheEngine(unittest.TestCase):
    def test_every_recorded_bill_matches(self):
        self.assertTrue(os.path.isfile(GRID), "the recorded answer key is missing")
        with open(GRID, encoding="utf-8") as fh:
            cases = json.load(fh)
        self.assertGreaterEqual(len(cases), 300, "the grid is too small to prove anything")

        node = shutil.which("node")
        # which() resolves npx.cmd on Windows; subprocess without shell=True
        # cannot, so calling "npx" by bare name raised FileNotFoundError there
        # and the whole run failed on a machine that simply has no npx.
        npx = shutil.which("npx")
        if not node or not npx or not os.path.isdir(
            os.path.join(DESKTOP, "node_modules")
        ):
            raise unittest.SkipTest(
                "node, npx and desktop/node_modules are needed to run the "
                "TypeScript port"
            )

        with tempfile.TemporaryDirectory() as tmp:
            bundle = os.path.join(tmp, "demoCalc.mjs")
            build = subprocess.run(
                [npx, "esbuild", "src/demoCalc.ts", "--bundle", "--format=esm",
                 f"--outfile={bundle}", "--log-level=error"],
                cwd=DESKTOP, capture_output=True, text=True,
            )
            if build.returncode != 0:
                raise unittest.SkipTest(f"esbuild unavailable: {build.stderr[:200]}")

            runner = os.path.join(tmp, "run.mjs")
            with open(runner, "w", encoding="utf-8") as fh:
                fh.write(RUNNER)
            out = subprocess.run(
                [node, runner, GRID],
                cwd=tmp, capture_output=True, text=True,
            )
            self.assertEqual(out.returncode, 0, out.stderr[:500])
            result = json.loads(out.stdout.strip().splitlines()[-1])

        self.assertEqual(
            result["failures"], 0,
            "the demo shows different money from the engine:\n  "
            + "\n  ".join(result["bad"]),
        )
        self.assertEqual(result["total"], len(cases))


if __name__ == "__main__":
    unittest.main()
