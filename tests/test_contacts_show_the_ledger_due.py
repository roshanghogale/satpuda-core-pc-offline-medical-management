"""Settings -> Contacts shows the same due as Sales' Previous Due and the ledger.

"contacts madhe due barobar yet nahi": Online, get_contacts printed the catalog row's stored
customers.total_due / suppliers.total_due. That figure is only rewritten when a receipt or
return cascade runs on the server, the desktop keeps its copy for minutes (and a disk snapshot
across restarts), and a bill saved without a customer_id or still queued on this PC never
reaches it. Sales (online_customer_remaining_due) and Payments work the due out from the
ledger -- bills, less paid at the counter, receipts, return refunds, oldest bill first -- so
the same customer showed one due on Sales and another in Contacts.

The bulk ledger read also asked the server for one page of sales. The server hands out at most
5000 a call, newest first, so on a big shop the oldest bills fell off and their receipts had
nothing left to clear.

A fake store stands in; nothing reaches a server.
"""
from __future__ import annotations

import sqlite3
import unittest
from contextlib import ExitStack
from unittest import mock

from core import db_setup
from core import desktop_pages_service as pages
from core import desktop_settings_service as dss

SERVER_PAGE = 3  # the server's per-call cap, shrunk so paging is exercised


def _sale(sid, day, cid, name, total, paid):
    return {"id": sid, "local_id": sid, "bill_no": f"SCB{sid}", "bill_date": day,
            "customer_id": cid, "customer_name": name, "total_amount": float(total),
            "amount_paid": float(paid), "cash_paid": float(paid), "online_paid": 0.0,
            "due_amount": float(total - paid), "total_due": float(total - paid),
            "account_cleared": False, "bill_cleared": False, "is_autosave": False,
            "deleted": False}


# A K PAWAR: billed 1000, paid 100 at the counter, receipt 400, return 50 -> 450.
#   The catalog still says 900 (the receipt and return never reached the stored figure).
# B JADHAV: one bill of 120 saved with no customer_id -> 120. Catalog says 0.
# C OLD: one bill, the oldest in the shop -> beyond the first server page. 75.
# D PAID: bills fully cleared by a receipt -> 0. Catalog still says 60.
SALES = [
    _sale(1, "2025-01-05", 3, "C OLD", 75, 0),
    _sale(2, "2026-08-01", 1, "A K PAWAR", 500, 0),
    _sale(3, "2026-08-10", 4, "D PAID", 60, 0),
    _sale(4, "2026-08-15", 1, "A K PAWAR", 300, 100),
    _sale(5, "2026-09-01", 1, "A K PAWAR", 200, 0),
    _sale(6, "2026-09-05", 0, "B JADHAV", 120, 0),
    _sale(7, "2026-09-06", 2, "B JADHAV", 0, 0),
]
PAYMENTS = [
    {"id": 21, "customer_id": 1, "customer_name": "A K PAWAR", "amount": 400.0,
     "payment_date": "2026-09-10"},
    {"id": 22, "customer_id": 4, "customer_name": "D PAID", "amount": 60.0,
     "payment_date": "2026-09-10"},
]
RETURNS = [
    {"id": 31, "customer_id": 1, "customer_name": "A K PAWAR", "refund_amount": 50.0,
     "return_date": "2026-09-11"},
]
CUSTOMERS = [
    {"id": 1, "local_id": 1, "name": "A K PAWAR", "phone": "", "address": "",
     "total_due": 900.0, "total_credit": 0.0},
    {"id": 2, "local_id": 2, "name": "B JADHAV", "phone": "", "address": "",
     "total_due": 0.0, "total_credit": 0.0},
    {"id": 3, "local_id": 3, "name": "C OLD", "phone": "", "address": "",
     "total_due": 0.0, "total_credit": 0.0},
    {"id": 4, "local_id": 4, "name": "D PAID", "phone": "", "address": "",
     "total_due": 60.0, "total_credit": 0.0},
]
WANT = {1: 450.0, 2: 120.0, 3: 75.0, 4: 0.0}

# Supplier: bill 1000, 200 paid at entry, payment 300 -> 500. Catalog says 800.
PURCHASES = [
    {"id": 41, "local_id": 41, "purchase_date": "2026-08-01", "supplier_id": 9,
     "supplier_name": "MAHA PHARMA", "final_amount": 1000.0, "total_amount": 1000.0,
     "amount_paid_at_entry": 200.0, "cash_paid_at_entry": 200.0, "online_paid_at_entry": 0.0,
     "amount_paid": 200.0, "due_amount": 800.0, "deleted": False, "is_autosave": False},
]
SUP_PAYMENTS = [
    {"id": 51, "supplier_id": 9, "supplier_name": "MAHA PHARMA", "amount": 300.0,
     "payment_date": "2026-09-01"},
]
SUPPLIERS = [
    {"id": 9, "local_id": 9, "name": "MAHA PHARMA", "phone": "", "gstin": "",
     "address": "", "total_due": 800.0, "total_credit": 0.0},
]


def _list_sales(*, q="", limit=5000, offset=0, **_kw):
    rows = sorted(SALES, key=lambda r: (r["bill_date"], r["local_id"]), reverse=True)
    if q:
        rows = [r for r in rows if q.upper() in r["customer_name"].upper()]
    lim = min(int(limit or 500), SERVER_PAGE)
    return {"rows": [dict(r) for r in rows[int(offset or 0): int(offset or 0) + lim]]}


class _Online(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.addCleanup(self.conn.close)
        db_setup.initialise(self.conn)
        stack = ExitStack()
        self.addCleanup(stack.close)
        for patch in (
            mock.patch("core.sync_prefs.is_online_mode", return_value=True),
            mock.patch("core.store_query_client.list_sales", side_effect=_list_sales),
            mock.patch("core.store_query_client.list_customer_payments",
                       side_effect=lambda **kw: {"rows": [dict(p) for p in PAYMENTS]}),
            mock.patch("core.store_query_client.list_sales_returns",
                       side_effect=lambda **kw: {"rows": [dict(r) for r in RETURNS]}),
            mock.patch("core.store_query_client.list_purchases",
                       side_effect=lambda **kw: {"rows": [dict(r) for r in PURCHASES]}),
            mock.patch("core.store_query_client.list_supplier_payments",
                       side_effect=lambda **kw: {"rows": [dict(p) for p in SUP_PAYMENTS]}),
            mock.patch("core.online_catalog.customers",
                       side_effect=lambda **kw: [dict(c) for c in CUSTOMERS]),
            mock.patch("core.online_catalog.suppliers",
                       side_effect=lambda **kw: [dict(s) for s in SUPPLIERS]),
            mock.patch("core.online_catalog.doctors", return_value=[]),
            mock.patch("core.online_mutation_queue.overlay_sales_dicts", return_value=[]),
            mock.patch("core.online_mutation_queue.overlay_customer_payment_dicts",
                       return_value=[]),
            mock.patch("core.online_mutation_queue.overlay_sales_return_dicts", return_value=[]),
            mock.patch("core.online_mutation_queue.overlay_purchase_dicts", return_value=[]),
            mock.patch("core.online_mutation_queue.overlay_supplier_payment_dicts",
                       return_value=[]),
            mock.patch.object(pages, "_SALES_PAGE", SERVER_PAGE),
        ):
            stack.enter_context(patch)

    def contacts(self):
        return dss.get_contacts(self.conn)


class ContactsCustomerDueIsTheLedgerDue(_Online):
    def test_each_customer_shows_the_ledger_due(self):
        got = {c["id"]: c["total_due"] for c in self.contacts()["customers"]}
        self.assertEqual(got, WANT)

    def test_contacts_agrees_with_sales_previous_due(self):
        for c in self.contacts()["customers"]:
            sales_page = dss.online_customer_remaining_due(int(c["id"]), c["name"])
            self.assertAlmostEqual(c["total_due"], sales_page, places=2, msg=c["name"])

    def test_payments_screen_reads_the_oldest_bills_too(self):
        parties = dss._apply_live_party_dues(
            "customer", [{"id": c["id"], "name": c["name"], "due": c["total_due"]}
                         for c in CUSTOMERS])
        self.assertEqual(parties[2]["due"], 75.0, "the shop's oldest bill was not read")


class ContactsSupplierDueIsTheLedgerDue(_Online):
    def test_supplier_shows_the_ledger_due(self):
        (s,) = self.contacts()["suppliers"]
        self.assertEqual(s["total_due"], 500.0)
        self.assertAlmostEqual(
            s["total_due"], dss.online_supplier_remaining_due(9, "MAHA PHARMA"), places=2)


class ContactsFallsBackWhenTheLedgerCannotBeRead(_Online):
    def test_stored_figure_when_the_server_fails(self):
        with mock.patch("core.store_query_client.list_sales",
                        side_effect=RuntimeError("offline link")):
            got = {c["id"]: c["total_due"] for c in self.contacts()["customers"]}
        self.assertEqual(got, {c["id"]: c["total_due"] for c in CUSTOMERS})


class OfflineContactsMatchSalesAndLedger(unittest.TestCase):
    def test_offline_due_matches_sales_form_and_ledger(self):
        from core.customer_service import get_customer_due, recalculate_customer_due

        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        db_setup.initialise(conn)
        with mock.patch("core.sync_prefs.is_online_mode", return_value=False):
            conn.execute("INSERT INTO customers (id, name, phone, address) "
                         "VALUES (1, 'A K PAWAR', '', '')")
            for sid, total, paid in ((1, 500, 0), (2, 300, 100), (3, 200, 0)):
                conn.execute(
                    "INSERT INTO sales (id, bill_no, bill_date, customer_id, customer_name, "
                    "total_amount, amount_paid) VALUES (?,?,?,?,?,?,?)",
                    (sid, f"SCB{sid}", "2026-09-01", 1, "A K PAWAR", total, paid))
            conn.execute("INSERT INTO customer_payments (customer_id, payment_date, amount) "
                         "VALUES (1, '2026-09-10', 400)")
            conn.execute("INSERT INTO sales_returns (customer_id, refund_amount) VALUES (1, 50)")
            conn.commit()
            with mock.patch("core.sync_coordinator.after_customer_saved", return_value=None):
                recalculate_customer_due(conn, 1, sync=False)
            (c,) = dss.get_contacts(conn)["customers"]
            ledger_due, _credit = get_customer_due(conn, 1)
            sales_form = {d["id"]: d["due"]
                          for d in pages.sales_form_defaults(conn)["customer_details"]}
        self.assertEqual(ledger_due, 450.0)
        self.assertEqual(c["total_due"], ledger_due)
        self.assertEqual(sales_form[1], ledger_due)


if __name__ == "__main__":
    unittest.main()
