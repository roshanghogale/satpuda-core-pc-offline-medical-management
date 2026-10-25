"""The General Products page refreshes when another device's products arrive.

It was the one page with no automatic refresh: a product added on the phone or another PC was
in this PC's copy within a second (offline-first worker) or on the server (Online), and the
list showed it only after the Refresh button. Now the pull's note reaches the shell's poller
as 'general_products', the poller maps it to the page, and the page reloads on the nonce.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DESKTOP = os.path.join(ROOT, "desktop")


class GeneralProductsFollowTheSync(unittest.TestCase):
    def test_a_pulled_product_is_noted_for_the_screens(self):
        from core import sync_status
        from core.offline_first import worker

        sync_status.take_refresh_batch()
        worker._note_pulled([{"collection": "general_products", "local_id": 7, "doc": {"id": 7}}])
        self.assertIn("general_products", sync_status.take_refresh_batch()["collections"])

    def test_the_page_is_mapped_to_its_collection(self):
        node = shutil.which("node")
        esbuild = os.path.join(DESKTOP, "node_modules", ".bin", "esbuild")
        if os.name == "nt" and os.path.exists(esbuild + ".cmd"):
            esbuild += ".cmd"
        if not node or not os.path.exists(esbuild):
            raise unittest.SkipTest("node and desktop/node_modules are needed")
        cases = [
            ["general_products", ["general_products"]],
            ["general_products", ["sales"]],
            ["general_products", ["all"]],
            ["inventory", ["general_products"]],
        ]
        with tempfile.TemporaryDirectory() as tmp:
            bundle = os.path.join(tmp, "m.mjs")
            subprocess.run([esbuild, "src/syncRefresh.ts", "--bundle", "--format=esm",
                            f"--outfile={bundle}", "--log-level=error"],
                           cwd=DESKTOP, check=True, timeout=60)
            runner = os.path.join(tmp, "run.mjs")
            with open(runner, "w", encoding="utf-8") as f:
                f.write("import { pageAffectedBySync } from './m.mjs'\n"
                        f"console.log(JSON.stringify({json.dumps(cases)}"
                        ".map(([p, c]) => pageAffectedBySync(p, c))))\n")
            out = subprocess.run([node, runner], capture_output=True, text=True,
                                 check=True, timeout=60).stdout
        self.assertEqual(json.loads(out), [True, False, True, False])

    def test_the_shell_hands_the_page_the_nonce_and_the_page_reloads_on_it(self):
        with open(os.path.join(DESKTOP, "src", "App.tsx"), encoding="utf-8") as f:
            app = f.read()
        start = app.index("<GeneralProductsPage")
        tag = app[start:app.index("/>", start)]
        self.assertIn("syncRefreshNonce={syncRefreshNonce}", tag)
        self.assertIn("active={page === 'general_products'}", tag)
        with open(os.path.join(DESKTOP, "src", "pages", "GeneralProductsPage.tsx"), encoding="utf-8") as f:
            page = f.read()
        self.assertIn("syncRefreshNonce", page)
        self.assertRegex(page, r"\}, \[syncRefreshNonce, active\]\)")


if __name__ == "__main__":
    unittest.main()
