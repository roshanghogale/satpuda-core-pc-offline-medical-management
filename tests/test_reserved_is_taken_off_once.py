"""Stock already on the bill must come off the shelf figure exactly once.

The report: a medicine had 363 in stock, the counter chose a quantity of 4, and
the figure went straight to 355 instead of 359. Four units were taken off twice.

The engine takes them off -- both branches of list_medicine_names do, and its own
docstring says so -- and the React picker then took the same quantity off again
before drawing the row. These pin the engine half: the answer is already net, so
nothing downstream may subtract from it a second time.
"""
import os
import sqlite3
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import db_setup, desktop_sales_service as sales  # noqa: E402


def _shop_with(stock: float, name: str = "ZZ RESERVE TEST 500") -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    db_setup.initialise(conn)
    conn.execute(
        "INSERT INTO medicines (id, name, batch_no, expiry_date, stock_qty, mrp, rate, unit, type) "
        "VALUES (1, ?, 'B1', '2031-12-31', ?, 20, 12, '1', 'Tablet')",
        (name, stock),
    )
    conn.commit()
    return conn


class ReservedIsSubtractedExactlyOnce(unittest.TestCase):
    def setUp(self):
        self._offline = mock.patch("core.sync_prefs.is_online_mode", return_value=False)
        self._offline.start()
        self.conn = _shop_with(363)

    def tearDown(self):
        self._offline.stop()
        self.conn.close()

    def test_the_name_list_answers_with_the_shelf_figure_already_net(self):
        """363 on the shelf, 4 on the bill -> 359. Not 363, and not 355."""
        res = sales.list_medicine_names(
            self.conn, q="ZZ RESERVE", limit=10, reserved={"1": 4}
        )
        rows = [n for n in (res.get("names") or []) if "RESERVE" in str(n.get("name", ""))]
        self.assertTrue(rows, f"the medicine was not listed at all: {res}")
        self.assertEqual(
            float(rows[0]["stock"]), 359.0,
            "the name list is not answering with a net figure — anything that "
            "subtracts again downstream now shows 355",
        )

    def test_with_nothing_on_the_bill_the_whole_shelf_is_offered(self):
        res = sales.list_medicine_names(self.conn, q="ZZ RESERVE", limit=10, reserved={})
        rows = [n for n in (res.get("names") or []) if "RESERVE" in str(n.get("name", ""))]
        self.assertEqual(float(rows[0]["stock"]), 363.0)

    def test_the_batch_list_is_net_too_and_by_the_same_amount(self):
        res = sales.list_batches_for_name(self.conn, "ZZ RESERVE TEST 500", reserved={"1": 4})
        batches = res.get("batches") or []
        self.assertTrue(batches, f"no batches returned: {res}")
        self.assertEqual(float(batches[0]["stock"]), 363.0, "raw stock should be untouched")
        self.assertEqual(
            float(batches[0]["available"]), 359.0,
            "available must be the shelf minus what is already on the bill, once",
        )

    def test_a_whole_shelf_on_the_bill_leaves_nothing_to_pick(self):
        res = sales.list_medicine_names(
            self.conn, q="ZZ RESERVE", limit=10, reserved={"1": 363}
        )
        rows = [n for n in (res.get("names") or []) if "RESERVE" in str(n.get("name", ""))]
        self.assertEqual(rows, [], "a fully reserved medicine is still offered")


class TheScreenDoesNotSubtractItAgain(unittest.TestCase):
    """The React half. There is no JavaScript test runner in this repo, so this
    reads the source -- weak, but it is the line that caused the report and it
    is worth a tripwire."""

    def test_the_picker_does_not_reduce_the_name_list_stock(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(root, "desktop/src/pages/TwoStepMedicinePicker.tsx")
        with open(path, encoding="utf-8") as fh:
            body = "\n".join(
                ln for ln in fh.read().splitlines()
                if not ln.lstrip().startswith(("//", "*", "/*"))
            )
        self.assertNotIn(
            "reservedByName", body,
            "the name list is subtracting the bill quantity a second time",
        )


if __name__ == "__main__":
    unittest.main()
