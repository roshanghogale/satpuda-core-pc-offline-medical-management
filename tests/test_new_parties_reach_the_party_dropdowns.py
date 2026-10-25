"""A customer / supplier made after a screen opened is in its party dropdown (25 Oct 2026).

Owner: "in the payment and return, new supplier / customer are not in the drop-down".

* Payments (offline / offline-first) reads the whole customers / suppliers table on every
  read -- a party made a moment ago, by any route (bill, Contacts, sync), is in it. The
  screen now reads it again each time it is shown (desktop/src/pages/SettingsPage.tsx).
* Contacts listed only the first 500 customers by name, so a new "Z..." on a big shop was
  never there to edit.
* Online: a party made on another device arrives as a server hint. The catalogue kept its
  copy for up to two minutes regardless, so the page refreshed on the hint and read the old
  list back; the hint now marks the catalogue's party lists stale.
"""
from __future__ import annotations

import sqlite3
import unittest
from unittest import mock

from core import db_setup
from core import desktop_settings_service as dss
from core import sync_status


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    db_setup.initialise(conn)
    return conn


class OfflinePaymentsListEveryParty(unittest.TestCase):
    def setUp(self):
        self.conn = _conn()
        self.addCleanup(self.conn.close)
        p = mock.patch("core.sync_prefs.is_online_mode", return_value=False)
        p.start()
        self.addCleanup(p.stop)

    def test_a_customer_made_after_the_first_read_is_in_the_next(self):
        for i in range(600):
            self.conn.execute("INSERT INTO customers (name, phone, address) VALUES (?, '', '')",
                              (f"A CUSTOMER {i:04d}",))
        before = {p["name"] for p in dss.get_payments(self.conn, "customer")["parties"]}
        self.assertNotIn("ZZ NEW CUSTOMER", before)
        self.conn.execute("INSERT INTO customers (name, phone, address) "
                          "VALUES ('ZZ NEW CUSTOMER', '', '')")
        after = {p["name"] for p in dss.get_payments(self.conn, "customer")["parties"]}
        self.assertIn("ZZ NEW CUSTOMER", after)
        self.assertEqual(len(after), 601)

    def test_a_supplier_made_after_the_first_read_is_in_the_next(self):
        dss.get_payments(self.conn, "supplier")
        self.conn.execute("INSERT INTO suppliers (name) VALUES ('ZZ NEW SUPPLIER')")
        names = {p["name"] for p in dss.get_payments(self.conn, "supplier")["parties"]}
        self.assertIn("ZZ NEW SUPPLIER", names)

    def test_contacts_list_customers_past_the_first_500(self):
        for i in range(600):
            self.conn.execute("INSERT INTO customers (name, phone, address) VALUES (?, '', '')",
                              (f"A CUSTOMER {i:04d}",))
        self.conn.execute("INSERT INTO customers (name, phone, address) "
                          "VALUES ('ZZ NEW CUSTOMER', '', '')")
        names = {c["name"] for c in dss.get_contacts(self.conn)["customers"]}
        self.assertIn("ZZ NEW CUSTOMER", names)
        self.assertEqual(len(names), 601)


class OnlineHintForgetsTheCachedPartyList(unittest.TestCase):
    def _note(self, col: str, *, online: bool) -> list:
        with mock.patch("core.sync_prefs.is_online_mode", return_value=online), \
                mock.patch("core.online_catalog.invalidate") as inv, \
                mock.patch("core.lan.hints.note"):
            sync_status.note_collection_change(col)
        return [c.args[0] for c in inv.call_args_list]

    def test_a_customer_hint_marks_customers_stale(self):
        self.assertEqual(self._note("customers", online=True), ["customers"])

    def test_a_supplier_hint_marks_suppliers_stale(self):
        self.assertEqual(self._note("suppliers", online=True), ["suppliers"])

    def test_a_full_refresh_marks_both_stale(self):
        self.assertEqual(self._note("all", online=True), ["customers", "suppliers"])

    def test_other_collections_leave_the_catalogue_alone(self):
        self.assertEqual(self._note("sales", online=True), [])
        self.assertEqual(self._note("medicines", online=True), [])

    def test_offline_first_does_not_touch_the_online_catalogue(self):
        self.assertEqual(self._note("customers", online=False), [])

    def test_a_stale_party_list_takes_in_the_new_party(self):
        """The real catalogue: after the hint the next read asks the server again."""
        from core import online_catalog as oc

        reads = {"n": 0}

        def _rows(kind, **_kw):
            reads["n"] += 1
            rows = [{"id": 1, "name": "OLD"}]
            if reads["n"] > 1:
                rows.append({"id": 2, "name": "ZZ NEW"})
            return rows

        oc.invalidate("customers")
        with mock.patch.object(oc, "_rows_from_store", side_effect=_rows), \
                mock.patch.object(oc, "_persist_snapshot"), \
                mock.patch.object(oc, "_refresh_incrementally", return_value=False), \
                mock.patch.object(oc, "_load_snapshot_key", return_value=[]), \
                mock.patch("core.sync_prefs.is_online_mode", return_value=True), \
                mock.patch("core.lan.hints.note"):
            first = [c["name"] for c in oc.customers()]
            self.assertEqual(first, ["OLD"])
            sync_status.note_collection_change("customers")
            second = [c["name"] for c in oc.customers()]
        oc.invalidate("customers")
        self.assertIn("ZZ NEW", second)


if __name__ == "__main__":
    unittest.main()
