"""The master list check's confirmed fields (owner, 30 Oct 2026: "master win hoil").

The server's global master carries checked_fields ("schedule,hsn,gst" ...): the fields the
master list check confirmed against the official rules and the web. A PC keeps them on a pull;
the shop's own inventory never overwrites them in this PC's master copy (nor on the server,
server-live masterMedicineService.lockedColumns); an unchecked field fills as before.
Made-up medicines only. Nothing here touches the network.
"""
import os
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import master_medicine_service as mms  # noqa: E402


class CheckedFields(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.remove(self.path)
        p = mock.patch.object(mms, "get_master_db_path", lambda: self.path)
        p.start()
        self.addCleanup(p.stop)
        mms.apply_remote_docs([{
            "name": "DEMO CEF 1GM INJ", "manufacturer": "DEMO LABS LTD", "mrp": 60, "content_drug": "Cefotaxime (1gm)",
            "med_type": "Injection", "pack_size": "1 vial", "schedule": "H1", "hsn_code": "30042019",
            "gst_percent": 5, "checked_fields": "content,schedule,hsn,gst",
        }, {
            "name": "DEMO PLAIN TAB", "manufacturer": "", "schedule": "", "checked_fields": "",
        }])

    def test_a_pull_keeps_them(self):
        got = mms.lookup_master_details("demo cef 1gm inj")
        self.assertEqual(got["checked_fields"], ["content", "schedule", "hsn", "gst"])
        self.assertEqual(got["schedule"], "H1")
        self.assertEqual(mms.lookup_master_details("DEMO PLAIN TAB")["checked_fields"], [])

    def test_the_shops_value_does_not_overwrite_a_checked_field(self):
        mms.upsert_master_medicine("DEMO CEF 1GM INJ", manufacturer="DEMO", content_drug="CEFOTAXIME",
                                   schedule="H", hsn_code="3004", gst_percent=12, push_remote=False)
        got = mms.lookup_master_details("DEMO CEF 1GM INJ")
        self.assertEqual((got["schedule"], got["hsn_code"], got["gst_percent"], got["content_drug"]),
                         ("H1", "30042019", 5.0, "Cefotaxime (1gm)"))
        self.assertEqual(got["manufacturer"], "DEMO")            # not checked: the shop's fills it

    def test_unchecked_rows_fill_as_before(self):
        mms.upsert_master_medicine("DEMO PLAIN TAB", schedule="H", push_remote=False)
        self.assertEqual(mms.lookup_master_details("DEMO PLAIN TAB")["schedule"], "H")

    def test_the_inventory_sync_too(self):
        inv = sqlite3.connect(":memory:")
        inv.execute("CREATE TABLE medicines (id INTEGER PRIMARY KEY, name TEXT, manufacturer TEXT, mrp REAL, "
                    "content_drug TEXT, type TEXT, unit TEXT, schedule TEXT, hsn_code TEXT, gst_percent REAL)")
        inv.execute("INSERT INTO medicines (name, schedule, hsn_code, gst_percent) VALUES ('DEMO CEF 1GM INJ', 'H', '3004', 12)")
        mms.sync_master_with_inventory(inv)
        got = mms.lookup_master_details("DEMO CEF 1GM INJ")
        self.assertEqual((got["schedule"], got["hsn_code"], got["gst_percent"]), ("H1", "30042019", 5.0))


class ImportedSchedule(CheckedFields):
    """A purchase import does not take the bill's schedule (owner, 30 Oct 2026): the master's
    confirmed one, else the shop's inventory, else the master's, else the bill's."""

    def shop(self, rows):
        conn = sqlite3.connect(":memory:")
        from core import db_setup

        db_setup.initialise(conn)
        for name, sch in rows:
            conn.execute("INSERT INTO medicines (name, schedule) VALUES (?, ?)", (name, sch))
        return conn

    def test_the_order(self):
        from core.medicine_metadata_resolver import verified_import_schedule as v

        conn = self.shop([("DEMO CEF 1GM INJ", "H"), ("DEMO SHOP ONLY TAB", "H")])
        with mock.patch("core.sync_prefs.is_online_mode", lambda *a, **k: False):
            self.assertEqual(v(conn, "DEMO CEF 1GM INJ", "G"), ("H1", "master (checked)"))
            self.assertEqual(v(conn, "DEMO SHOP ONLY TAB", "G"), ("H", "inventory"))
            mms.apply_remote_docs([{"name": "DEMO MASTER TAB", "schedule": "H"}])
            self.assertEqual(v(conn, "DEMO MASTER TAB", "G"), ("H", "master"))
            self.assertEqual(v(conn, "DEMO NOWHERE TAB", "H1"), ("H1", "bill"))


class CheckWithMaster(CheckedFields):
    """Inventory > Check with master: the differences, and Fix / Fix all (owner, 30 Oct 2026)."""

    def setUp(self):
        super().setUp()
        p = mock.patch("core.sync_prefs.is_online_mode", lambda *a, **k: False)
        p.start()
        self.addCleanup(p.stop)
        from core import db_setup

        self.conn = sqlite3.connect(":memory:")
        db_setup.initialise(self.conn)
        for name, batch, sch, hsn, gst, content in (
            ("DEMO CEF 1GM INJ", "B1", "H", "3004", 12, "CEFOTAXIME"),
            ("DEMO CEF 1GM INJ", "B2", "", "", 0, ""),
            ("DEMO PLAIN TAB", "P1", "G", "", 0, ""),
            ("DEMO NOT IN MASTER", "N1", "H", "", 0, ""),
        ):
            self.conn.execute("INSERT INTO medicines (name, batch_no, schedule, hsn_code, gst_percent, content_drug) "
                              "VALUES (?,?,?,?,?,?)", (name, batch, sch, hsn, gst, content))
        mms.apply_remote_docs([{"name": "DEMO PLAIN TAB", "schedule": "H", "checked_fields": ""}])
        self.conn.commit()

    def test_the_differences(self):
        from core import master_check

        out = master_check.differences(self.conn)
        by = {r["name"]: r for r in out["rows"]}
        self.assertEqual(list(by), ["DEMO CEF 1GM INJ", "DEMO PLAIN TAB"])   # sure ones first
        cef = by["DEMO CEF 1GM INJ"]
        self.assertEqual(cef["batches"], 2)
        # the newest batch (B2) is compared: empty schedule / HSN / GST / content
        self.assertEqual({d["field"]: (d["shop"], d["master"], d["sure"]) for d in cef["diffs"]}, {
            "schedule": ("", "H1", True), "hsn": ("", "30042019", True), "gst": ("", "5", True),
            "content": ("", "Cefotaxime (1gm)", True), "manufacturer": ("", "DEMO LABS LTD", False)})
        self.assertEqual([d["field"] for d in by["DEMO PLAIN TAB"]["diffs"]], ["schedule"])
        self.assertFalse(by["DEMO PLAIN TAB"]["sure"])
        self.assertEqual(len(master_check.differences(self.conn, sure_only=True)["rows"]), 1)

    def test_fix_all_takes_only_the_sure_fields(self):
        from core import master_check

        out = master_check.fix(self.conn, {"all": True})
        self.assertEqual((out["fixed"], out["rows"]), (1, 2))
        rows = self.conn.execute("SELECT batch_no, schedule, hsn_code, gst_percent, content_drug, manufacturer "
                                 "FROM medicines WHERE name='DEMO CEF 1GM INJ' ORDER BY batch_no").fetchall()
        for r in rows:          # every batch, the maker (not confirmed) left alone
            self.assertEqual(r[1:5], ("H1", "30042019", 5.0, "Cefotaxime (1gm)"))
            self.assertIn(r[5], (None, ""))
        self.assertEqual(self.conn.execute("SELECT schedule FROM medicines WHERE name='DEMO PLAIN TAB'").fetchone()[0], "G")

    def test_one_medicine_fix(self):
        from core import master_check

        master_check.fix(self.conn, {"items": [{"name": "demo plain tab", "fields": ["schedule"]}]})
        self.assertEqual(self.conn.execute("SELECT schedule FROM medicines WHERE name='DEMO PLAIN TAB'").fetchone()[0], "H")
        self.assertEqual([r["name"] for r in master_check.differences(self.conn)["rows"]], ["DEMO CEF 1GM INJ"])


if __name__ == "__main__":
    unittest.main()
