"""The Sales Return popup review (2026-09-11): the holes it found, pinned.

* A discount % below 0 or above 100 was saved as-is. The engine does not clamp
  it: -50 refunded 150% of the goods, 150 saved a negative refund that added to
  the customer's due. Both screens (they share salesReturnLogic.ts) and the
  engine now refuse it.
* Returns keeps its form loaded while the shop uses the Alt+R popup on Sales,
  so the same medicines could be returned from both and F5 on Returns saved
  them again -- two refunds. The engine now re-checks the returnable quantity
  at save time, Online from the server.
* Typing "12" and Enter loaded bill 123 when 123 was the newer bill.
* Enter matched against the result list of an older keystroke.
* Two bill loads in flight: the slower answer won.
* The popup swallowed Alt+F4.
* Ctrl+P on Sales History printed the picked bill AND the last sale.
* "Save Appearance (F10)" advertised a key nothing binds.

No test here reaches a network or a real store: the online path runs with
get_doc, the returns list and the upload queue replaced.
"""
import io
import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
from unittest import mock

from core import desktop_returns_service as drs

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DESKTOP = os.path.join(ROOT, "desktop")
PAGES = os.path.join(DESKTOP, "src", "pages")


def src(*parts):
    with io.open(os.path.join(*parts), encoding="utf-8") as fh:
        return fh.read()


RUNNER = r"""
import * as L from './logic.mjs'
const bill = { ok: true, sale_id: 5, items: [] }
const line = [{ medicine_id: 7, name: 'X', batch: 'B', qty: 1, rate: 10, amount: 10 }]
const lab = (xs) => xs.map((label, i) => ({ sale_id: i + 1, label }))
const out = {
  numberBeatsNewerPrefix: L.matchLabeledBill(
    lab(['123 — Ram (2026-09-10)', '12 — Shyam (2026-09-01)']), '12')?.label || null,
  twoPrefixesIsAGuess: L.matchLabeledBill(
    lab(['123 — Ram (2026-09-10)', '125 — Sita (2026-09-09)']), '12')?.label || null,
  onePrefix: L.matchLabeledBill(lab(['123 — Ram (2026-09-10)']), '12')?.label || null,
  exactLabel: L.matchLabeledBill(
    lab(['123 — Ram (2026-09-10)', '12 — Shyam (2026-09-01)']), '12 — Shyam (2026-09-01)')?.label || null,
  disc: Object.fromEntries(['-50', '150', 'abc', '100.01', '0', '', '10', '100', 12.5].map(
    (d) => [String(d), L.checkSalesReturnSave(bill, line, d)?.message || null])),
}
console.log(JSON.stringify(out))
"""


class TheSharedMoneyRulesRun(unittest.TestCase):
    """Runs salesReturnLogic.ts itself -- both return screens use it."""

    @classmethod
    def setUpClass(cls):
        node, npx = shutil.which("node"), shutil.which("npx")
        if not node or not npx or not os.path.isdir(os.path.join(DESKTOP, "node_modules")):
            raise unittest.SkipTest("node, npx and desktop/node_modules are needed")
        with tempfile.TemporaryDirectory() as tmp:
            build = subprocess.run(
                [npx, "esbuild", "src/pages/salesReturnLogic.ts", "--bundle", "--format=esm",
                 "--outfile=" + os.path.join(tmp, "logic.mjs"), "--log-level=error"],
                cwd=DESKTOP, capture_output=True, text=True,
            )
            if build.returncode != 0:
                raise unittest.SkipTest("esbuild unavailable: " + build.stderr[:200])
            with open(os.path.join(tmp, "run.mjs"), "w", encoding="utf-8") as fh:
                fh.write(RUNNER)
            run = subprocess.run([node, "run.mjs"], cwd=tmp, capture_output=True, text=True)
            if run.returncode != 0:
                raise AssertionError(run.stderr[:500])
            cls.out = json.loads(run.stdout.strip().splitlines()[-1])

    def test_typing_a_bill_number_loads_that_bill_not_a_newer_one_that_starts_with_it(self):
        self.assertEqual(self.out["numberBeatsNewerPrefix"], "12 — Shyam (2026-09-01)")
        self.assertEqual(self.out["exactLabel"], "12 — Shyam (2026-09-01)")

    def test_a_prefix_loads_only_when_it_names_one_bill(self):
        self.assertIsNone(self.out["twoPrefixesIsAGuess"])
        self.assertEqual(self.out["onePrefix"], "123 — Ram (2026-09-10)")

    def test_a_discount_outside_0_to_100_is_refused(self):
        d = self.out["disc"]
        for bad in ("-50", "150", "abc", "100.01"):
            self.assertEqual(d[bad], "Discount % must be between 0 and 100.", bad)
        for ok in ("0", "", "10", "100", "12.5"):
            self.assertIsNone(d[ok], ok)


class BothScreensPassTheDiscount(unittest.TestCase):
    def test_both_callers_hand_the_discount_to_the_check(self):
        self.assertIn("checkSalesReturnSave(b, linesRef.current, disc)",
                      src(PAGES, "SalesReturnDialog.tsx"))
        self.assertIn("checkSalesReturnSave(salesBill, salesReturnItems, salesDisc)",
                      src(PAGES, "ReturnsPage.tsx"))


class ThePopupsOwnHoles(unittest.TestCase):
    def setUp(self):
        self.dlg = src(PAGES, "SalesReturnDialog.tsx")

    def test_the_last_bill_asked_for_is_the_one_shown(self):
        load = self.dlg[self.dlg.index("const doLoad = async"):self.dlg.index("const loadBill =")]
        self.assertIn("const seq = ++loadSeq.current", load)
        self.assertLess(load.index("await loadSalesReturnBill(saleId)"),
                        load.index("if (seq !== loadSeq.current) return"))
        self.assertLess(load.index("if (seq !== loadSeq.current) return"),
                        load.index("billRef.current = loaded"))

    def test_enter_never_matches_an_older_keystrokes_list(self):
        self.assertIn("billsFor.current = searchKey(query, medicine)", self.dlg)
        self.assertIn("const fresh = billsFor.current === searchKey(q, med)", self.dlg)
        self.assertIn("fresh && !med.trim() ? matchLabeledBill(bills, q) : undefined", self.dlg)

    def test_alt_f4_is_left_to_windows(self):
        self.assertIn("(/^F\\d+$/.test(e.key) && !e.altKey)", self.dlg)


class TheKeysElsewhere(unittest.TestCase):
    def test_ctrl_p_on_sales_history_prints_once(self):
        app = src(DESKTOP, "src", "App.tsx")
        self.assertIn("if (page === 'sales' || page === 'sales_history') return", app)
        hist = src(PAGES, "SalesHistoryPage.tsx")
        self.assertIn("onPrintLast: () => void printBill(2, 'silent')", hist)

    def test_no_button_advertises_f10_in_settings(self):
        prefs = src(PAGES, "settings", "PrefsPanels.tsx")
        self.assertNotIn("(F10)", prefs)


SALE = {
    "id": 5, "bill_no": "12", "customer_id": 3, "customer_name": "Ram",
    "items": [
        {"medicine_id": 7, "medicine_name": "AMOXY 250", "qty": 5, "rate": 10, "amount": 50},
        {"medicine_id": 7, "medicine_name": "AMOXY 250", "qty": 3, "rate": 12, "amount": 36},
        {"medicine_id": 9, "medicine_name": "PARA 500", "qty": 2, "rate": 5, "amount": 10},
    ],
}


def body(qty7=0, qty9=0, discount=0, extra=None):
    items = []
    if qty7:
        items.append({"medicine_id": 7, "name": "AMOXY 250", "qty": qty7, "rate": 10,
                      "orig_qty": 5, "orig_amount": 50})
    if qty9:
        items.append({"medicine_id": 9, "name": "PARA 500", "qty": qty9, "rate": 5,
                      "orig_qty": 2, "orig_amount": 10})
    items.extend(extra or [])
    return {"sale_id": 5, "customer_id": 3, "customer_name": "Ram", "bill_no": "12",
            "items": items, "discount": discount, "reason": "", "settle_mode": "ledger"}


class TheEngineChecksAgainOnline(unittest.TestCase):
    """The shops run Online: the engine sqlite is :memory:, the bill and its
    returns come from the server."""

    def setUp(self):
        self.enqueue = mock.Mock(side_effect=AssertionError("a refused return was queued"))
        get_doc = lambda col, lid: dict(SALE) if (col, int(lid)) == ("sales", 5) else None
        patches = [
            mock.patch("core.sync_prefs.is_online_mode", return_value=True),
            mock.patch("core.server_crud.get_doc", side_effect=get_doc),
            # 6 of the 8 AMOXY already went back: 4 on the server, 2 still queued.
            mock.patch.object(drs, "_online_returned_map", return_value={7: 4.0}),
            mock.patch.object(drs, "_pending_returned_qty",
                              side_effect=lambda c, k, p, m: 2.0 if m == 7 else 0.0),
            mock.patch("core.online_mutation_queue.enqueue", self.enqueue),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.conn = sqlite3.connect(":memory:")
        self.addCleanup(self.conn.close)

    def test_returning_more_than_is_left_is_refused_and_nothing_is_queued(self):
        res = drs.save_sales_return(self.conn, body(qty7=3))
        self.assertFalse(res["ok"])
        self.assertIn("Cannot return more than 2 for AMOXY 250", res["error"])
        self.enqueue.assert_not_called()

    def test_every_line_of_one_medicine_counts(self):
        # Sold 5 + 3; the limit is 8 - 6, not the first line's 5 - 6.
        self.assertEqual(drs._sales_returnable(self.conn, 5, True)[7], ("AMOXY 250", 2.0))
        self.assertEqual(drs._sales_return_request_error(self.conn, 5, body(qty7=2, qty9=2), True), "")
        # two lines of it on one return are added together
        split = body(extra=[{"medicine_id": 7, "qty": 1}, {"medicine_id": 7, "qty": 2}])
        self.assertIn("Cannot return more than 2", drs._sales_return_request_error(self.conn, 5, split, True))

    def test_a_medicine_that_is_not_on_the_bill_is_refused(self):
        res = drs.save_sales_return(self.conn, body(extra=[{"medicine_id": 99, "name": "ZZ", "qty": 1}]))
        self.assertEqual(res, {"ok": False, "error": "ZZ is not on this bill."})
        self.enqueue.assert_not_called()

    def test_a_discount_outside_0_to_100_is_refused(self):
        for bad in (-50, 150, "abc"):
            res = drs.save_sales_return(self.conn, body(qty9=1, discount=bad))
            self.assertEqual(res, {"ok": False, "error": "Discount % must be between 0 and 100."}, bad)
        self.enqueue.assert_not_called()
        self.assertEqual(drs._sales_return_request_error(self.conn, 5, body(qty9=1, discount=100), True), "")

    def test_a_bill_the_server_cannot_give_is_refused_not_let_through(self):
        # get_doc answers None for an unreachable server as well as a missing
        # bill. This used to let 50 of an 8-strip bill through ("the screen's
        # check is in charge") -- and a stale screen's check is the very thing
        # the save-time check exists to overrule. Unreadable is not zero.
        with mock.patch("core.server_crud.get_doc", return_value=None), \
             mock.patch("core.online_mutation_queue.pending_by_local_id", return_value=None):
            with self.assertRaises(drs.ReturnsUnreadable):
                drs._sales_returnable(self.conn, 5, True)
            self.assertIn("Could not read this bill",
                          drs._sales_return_request_error(self.conn, 5, body(qty7=50), True))
            res = drs.save_sales_return(self.conn, body(qty7=1))
            self.assertFalse(res["ok"])
            self.enqueue.assert_not_called()


class TheEngineChecksAgainOffline(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.addCleanup(self.conn.close)
        c = self.conn
        c.executescript(
            """
            CREATE TABLE medicines (id INTEGER PRIMARY KEY, name TEXT);
            CREATE TABLE sales_items (id INTEGER PRIMARY KEY, sale_id INT, medicine_id INT, qty REAL);
            CREATE TABLE sales_returns (id INTEGER PRIMARY KEY, sale_id INT, deleted INT DEFAULT 0);
            CREATE TABLE sales_return_items (id INTEGER PRIMARY KEY, return_id INT, medicine_id INT, qty REAL);
            INSERT INTO medicines VALUES (7, 'AMOXY 250');
            INSERT INTO sales_items (sale_id, medicine_id, qty) VALUES (5, 7, 5), (5, 7, 3);
            INSERT INTO sales_returns (id, sale_id, deleted) VALUES (1, 5, 0), (2, 5, 1);
            INSERT INTO sales_return_items (return_id, medicine_id, qty) VALUES (1, 7, 6), (2, 7, 8);
            """
        )

    def test_saved_returns_count_and_deleted_ones_do_not(self):
        self.assertEqual(drs._sales_returnable(self.conn, 5, False), {7: ("AMOXY 250", 2.0)})
        self.assertIn("Cannot return more than 2",
                      drs._sales_return_request_error(self.conn, 5, body(qty7=3), False))
        self.assertEqual(drs._sales_return_request_error(self.conn, 5, body(qty7=2), False), "")


if __name__ == "__main__":
    unittest.main()
