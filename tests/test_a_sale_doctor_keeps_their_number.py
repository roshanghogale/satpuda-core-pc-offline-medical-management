"""A doctor picked on the Sales page brings their number, and a new one is kept.

The Sales list carried doctor NAMES only, so the phone was typed on every bill.
Online, a sale wrote the doctor's name onto the bill row and nowhere else: a
new doctor never joined the list, and the number typed beside them was lost.
"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import threading
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import core.sync_prefs as sync_prefs  # noqa: E402


def _run_inline(target=None, name=None, daemon=None):
    """Stand-in for threading.Thread: runs the work at start(), so a test can look."""
    t = mock.Mock()
    t.start = lambda: target()
    return t


class SaleDoctorTest(unittest.TestCase):
    def test_offline_list_carries_each_doctors_number(self):
        from core import db_setup
        from core.desktop_pages_service import sales_form_defaults

        with mock.patch.object(sync_prefs, "is_online_mode", lambda: False):
            conn = sqlite3.connect(os.path.join(tempfile.mkdtemp(), "s.db"))
            db_setup.initialise(conn)
            conn.execute("INSERT INTO doctors (name, phone) VALUES ('DR PATIL', '9822001122')")
            conn.execute("INSERT INTO doctors (name, phone) VALUES ('DR SHAH', '')")
            conn.commit()
            d = sales_form_defaults(conn)
        self.assertIn("DR PATIL", d["doctors"])
        self.assertEqual(d["doctor_phones"].get("DR PATIL"), "9822001122")
        self.assertNotIn("DR SHAH", d["doctor_phones"])

    def _remember(self, known, name, phone):
        from core import desktop_sales_service as svc

        sent = []
        with mock.patch.object(sync_prefs, "is_online_mode", lambda: True), \
                mock.patch("core.online_catalog.doctors", lambda: known), \
                mock.patch("core.online_catalog.patch_docs", lambda c, docs: None), \
                mock.patch("core.server_crud.upsert_contact_online",
                           lambda c, doc: sent.append((c, dict(doc))) or 77), \
                mock.patch.object(threading, "Thread", _run_inline):
            svc._remember_doctor_online(name, phone)
        return sent

    def test_online_a_new_doctor_joins_the_list_with_their_number(self):
        sent = self._remember([], "dr kale", "9000011111")
        self.assertEqual(len(sent), 1)
        coll, doc = sent[0]
        self.assertEqual(coll, "doctors")
        self.assertEqual(doc["name"], "DR KALE")
        self.assertEqual(doc["phone"], "9000011111")

    def test_online_a_known_doctors_new_number_is_kept(self):
        known = [{"id": 5, "local_id": 5, "name": "DR KALE", "phone": "111", "version": 3}]
        sent = self._remember(known, "Dr Kale", "222")
        self.assertEqual(len(sent), 1)
        doc = sent[0][1]
        self.assertEqual(doc["id"], 5)
        self.assertEqual(doc["phone"], "222")
        self.assertGreater(doc["version"], 3)  # the server keeps the newer version only

    def test_online_nothing_is_sent_when_nothing_changed(self):
        known = [{"id": 5, "name": "DR KALE", "phone": "111"}]
        self.assertEqual(self._remember(known, "DR KALE", "111"), [])
        self.assertEqual(self._remember(known, "DR KALE", ""), [])
        self.assertEqual(self._remember(known, "", "999"), [])


if __name__ == "__main__":
    unittest.main()
