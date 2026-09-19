"""The Write-off and Bulk Purchase Return tabs, on a shop that keeps its records
on the server.

Every other function on the Returns screen branches on is_online_mode(); these
did not. In Online mode the engine's connection is sqlite3.connect(":memory:"),
so they read and wrote an empty database that is thrown away when the engine
stops -- and the screen said it had saved. "Load stock" found nothing to return
on a shop with plenty.

Two things this pins:
  * the bulk list is built from the server's catalog, honours the shop's own
    near-expiry months, and separates batches that came in on a purchase bill
    (returnable to the supplier, with credit) from those that did not (a plain
    write-off);
  * a return to the supplier can find its purchase bill online at all. It could
    not: the lookup read the local purchases table, so "Return to supplier"
    quietly became a write-off and the shop lost the credit it was owed.
"""
import os
import sqlite3
import sys
import unittest
from datetime import date, timedelta
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import db_setup, desktop_returns_service as ret  # noqa: E402


def iso(days):
    return (date.today() + timedelta(days=days)).isoformat()


CATALOG = [
    # expired, and it came in on a purchase bill -> returnable to the supplier
    {"id": 1, "name": "ZZ EXPIRED MED", "batch_no": "E1", "expiry_date": iso(-30),
     "stock_qty": 5, "type": "Tablet", "unit": "10"},
    # near expiry, no purchase bill on the server -> write-off
    {"id": 2, "name": "ZZ NEAR MED", "batch_no": "N1", "expiry_date": iso(20),
     "stock_qty": 3, "type": "Tablet", "unit": "10"},
    # far future -> not offered at all
    {"id": 3, "name": "ZZ FINE MED", "batch_no": "F1", "expiry_date": iso(900),
     "stock_qty": 9, "type": "Tablet", "unit": "10"},
    # expired but nothing left on the shelf -> nothing to return
    {"id": 4, "name": "ZZ EMPTY MED", "batch_no": "Z1", "expiry_date": iso(-10),
     "stock_qty": 0, "type": "Tablet", "unit": "10"},
]

PURCHASE_FOR = {
    1: {"purchase_id": 77, "bill_number": "B-77", "purchase_date": iso(-200),
        "supplier_id": 4, "supplier_name": "ZZ SUPPLIER ONE", "qty": 10, "rate": 12.5},
}

BILL_77 = {
    "id": 77, "bill_number": "B-77", "purchase_date": iso(-200), "supplier_id": 4,
    "supplier_name": "ZZ SUPPLIER ONE",
    "items": [{"medicine_id": 1, "medicine_name": "ZZ EXPIRED MED", "batch_no": "E1",
               "qty": 10, "rate": 12.5, "amount": 125, "type": "Tablet", "unit": "10"}],
}


class TheBulkTabIsBuiltFromTheServer(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        db_setup.initialise(self.conn)
        self._patches = [
            mock.patch("core.sync_prefs.is_online_mode", return_value=True),
            mock.patch("core.online_catalog.medicines", return_value=CATALOG),
            mock.patch(
                "core.stock_disposal_service.lookup_batch_purchase_online",
                side_effect=lambda mid, name="", batch="": PURCHASE_FOR.get(int(mid)),
            ),
            # Each bill is now opened for what it still allows (the offline
            # builder's remaining_qty cap): the bill document and its returns.
            mock.patch("core.server_crud.get_doc", side_effect=lambda col, lid: (
                dict(BILL_77) if (col, int(lid)) == ("purchases", 77) else None)),
            mock.patch("core.store_query_client.list_purchase_returns",
                       return_value={"rows": [], "total": 0}),
            mock.patch("core.online_catalog.medicine_by_id", side_effect=lambda mid: next(
                (dict(m) for m in CATALOG if m["id"] == int(mid)), None)),
            mock.patch.object(ret, "_pending_returned_qty", return_value=0.0),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        self.conn.close()

    def _prefill(self, **kw):
        return ret.bulk_purchase_prefill(self.conn, **kw)

    def test_it_no_longer_refuses_and_no_longer_comes_back_empty(self):
        res = self._prefill()
        self.assertTrue(res.get("ok"), res)
        total = len(res["purchase_groups"]) + len(res["writeoff_lines"])
        self.assertEqual(total, 2, "expected the expired and the near-expiry batch")

    def test_a_batch_with_a_purchase_bill_is_returnable_to_the_supplier(self):
        res = self._prefill()
        self.assertEqual(len(res["purchase_groups"]), 1)
        grp = res["purchase_groups"][0]
        self.assertEqual(grp["purchase_id"], 77)
        self.assertEqual(grp["supplier_name"], "ZZ SUPPLIER ONE")
        self.assertEqual(len(grp["items"]), 1)
        # The rate is what makes the supplier credit a real number.
        self.assertEqual(grp["items"][0]["rate"], 12.5)

    def test_a_batch_with_no_purchase_bill_becomes_a_write_off(self):
        res = self._prefill()
        names = [w["name"] for w in res["writeoff_lines"]]
        self.assertEqual(names, ["ZZ NEAR MED"])

    def test_stock_that_is_not_near_expiry_is_left_alone(self):
        res = self._prefill()
        listed = [i["name"] for g in res["purchase_groups"] for i in g["items"]]
        listed += [w["name"] for w in res["writeoff_lines"]]
        self.assertNotIn("ZZ FINE MED", listed)

    def test_a_batch_with_no_stock_is_not_offered(self):
        res = self._prefill()
        listed = [i["name"] for g in res["purchase_groups"] for i in g["items"]]
        listed += [w["name"] for w in res["writeoff_lines"]]
        self.assertNotIn("ZZ EMPTY MED", listed)

    def test_the_two_switches_are_honoured(self):
        only_expired = self._prefill(include_near_expiry=False)
        names = [i["name"] for g in only_expired["purchase_groups"] for i in g["items"]]
        names += [w["name"] for w in only_expired["writeoff_lines"]]
        self.assertEqual(names, ["ZZ EXPIRED MED"])

        only_near = self._prefill(include_expired=False)
        names = [i["name"] for g in only_near["purchase_groups"] for i in g["items"]]
        names += [w["name"] for w in only_near["writeoff_lines"]]
        self.assertEqual(names, ["ZZ NEAR MED"])


class AReturnToTheSupplierFindsItsBillOnline(unittest.TestCase):
    """lookup_batch_purchase reads the local purchases table, which is empty
    online -- so the credit half of a return was silently dropped."""

    def test_the_lookup_asks_the_server_and_needs_two_calls_not_one_per_bill(self):
        from core import stock_disposal_service as sd

        calls = {"list": 0, "get": 0}

        def fake_list(**kw):
            calls["list"] += 1
            return {"rows": [
                {"id": 55, "purchase_date": "2026-01-01"},
                {"id": 77, "purchase_date": "2026-06-01"},
            ]}

        def fake_get(pid):
            calls["get"] += 1
            if pid != 77:
                return {"items": []}
            return {
                "bill_number": "B-77", "purchase_date": "2026-06-01",
                "supplier_id": 4, "supplier_name": "ZZ SUPPLIER ONE",
                "items": [{"medicine_id": 1, "batch_no": "E1", "qty": 10, "rate": 12.5}],
            }

        with mock.patch("core.store_query_client.list_purchases", fake_list), \
             mock.patch("core.store_query_client.get_purchase", fake_get):
            info = sd.lookup_batch_purchase_online(1, "ZZ EXPIRED MED", "E1")

        self.assertIsNotNone(info, "the return could not find its purchase bill")
        self.assertEqual(info["purchase_id"], 77)
        self.assertEqual(info["rate"], 12.5)
        self.assertEqual(info["supplier_id"], 4)
        self.assertEqual(calls["list"], 1, "the bill list was fetched more than once")
        self.assertLessEqual(
            calls["get"], 1,
            "only the newest matching bill should be opened — one call per bill "
            "is the shape that made the returns search take a minute",
        )

    def test_no_matching_bill_reads_as_no_bill_rather_than_an_error(self):
        from core import stock_disposal_service as sd

        with mock.patch("core.store_query_client.list_purchases", lambda **kw: {"rows": []}), \
             mock.patch("core.store_query_client.get_purchase", lambda pid: {}):
            self.assertIsNone(sd.lookup_batch_purchase_online(1, "ZZ ANY", "B"))


if __name__ == "__main__":
    unittest.main()
