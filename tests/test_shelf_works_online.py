"""The Shelf screen, on a shop that keeps its records on the server.

Every read and every write in this feature went through the engine's own
connection -- which Online is sqlite3.connect(":memory:"), created empty at
launch and thrown away at exit. So a pharmacy that had mapped every rack, shelf
and box opened Shelf and saw nothing; added a rack and was told it was saved;
and found it gone after the next restart. The "Show shelf location" switch was
pushed to the server on every change and never read back, so it reset itself to
off each launch and the Location column never appeared.

Nothing here is a new feature: it is the same screen, reading and writing the
place the shop's records actually live.
"""
import os
import sqlite3
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import db_setup, desktop_settings_service as settings  # noqa: E402

RACKS = [{"id": 1, "name": "RACK A"}, {"id": 2, "name": "RACK B"}]
SECTIONS = [{"id": 10, "rack_id": 1, "name": "SEC 1"},
            {"id": 11, "rack_id": 2, "name": "SEC 2"}]
BOXES = [{"id": 100, "section_id": 10, "name": "BOX 1"},
         {"id": 101, "section_id": 10, "name": "BOX 2"},
         {"id": 102, "section_id": 11, "name": "BOX 3"}]

MEDICINES = [
    {"id": 1, "name": "ZZ PARA 500", "batch_no": "A1", "location": "RACK A/SEC 1/BOX 1",
     "stock_qty": 10},
    {"id": 2, "name": "ZZ AMOX 250", "batch_no": "B1", "location": "", "stock_qty": 4},
    {"id": 3, "name": "ZZ COUGH SYP", "batch_no": "C1", "location": "  ", "stock_qty": 7},
]


class _Online(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        db_setup.initialise(self.conn)
        self.queued = []
        self._patches = [
            mock.patch("core.sync_prefs.is_online_mode", return_value=True),
            mock.patch("core.online_catalog.shelf_entities",
                       side_effect=lambda k, **kw: {"racks": RACKS, "sections": SECTIONS,
                                                    "boxes": BOXES}.get(k, [])),
            mock.patch("core.online_catalog.medicines", return_value=MEDICINES),
            mock.patch("core.online_catalog.invalidate", return_value=None),
            mock.patch("core.online_catalog.patch_docs", return_value=None),
            mock.patch("core.desktop_settings_service._shelf_show_location_online",
                       return_value=True),
            mock.patch("core.online_mutation_queue.enqueue",
                       side_effect=lambda **kw: self.queued.append(kw) or
                       {"local_id": kw.get("local_id")}),
            mock.patch("core.server_crud.allocate_id", return_value=55),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        self.conn.close()


class TheShelfIsReadFromTheServer(_Online):
    def test_the_racks_are_not_empty(self):
        out = settings.get_shelf(self.conn)
        self.assertEqual([r["name"] for r in out["racks"]], ["RACK A", "RACK B"])

    def test_sections_hang_off_their_own_rack(self):
        out = settings.get_shelf(self.conn)
        by_name = {r["name"]: r for r in out["racks"]}
        self.assertEqual([s["name"] for s in by_name["RACK A"]["sections"]], ["SEC 1"])
        self.assertEqual([s["name"] for s in by_name["RACK B"]["sections"]], ["SEC 2"])

    def test_boxes_hang_off_their_own_section(self):
        out = settings.get_shelf(self.conn)
        sec = out["racks"][0]["sections"][0]
        self.assertEqual([b["name"] for b in sec["boxes"]], ["BOX 1", "BOX 2"])

    def test_the_show_location_switch_is_read_back(self):
        out = settings.get_shelf_full(self.conn)
        self.assertTrue(
            out["show_location"],
            "the switch was write-only, so it reset to off on every launch",
        )

    def test_medicines_are_split_by_whether_they_have_a_place(self):
        out = settings.get_shelf_full(self.conn)
        self.assertEqual([a["name"] for a in out["assigned"]], ["ZZ PARA 500"])
        self.assertEqual(
            sorted(u["name"] for u in out["unassigned"]),
            ["ZZ AMOX 250", "ZZ COUGH SYP"],
            "a location of only spaces is not a location",
        )

    def test_an_unreachable_server_is_not_an_empty_pharmacy(self):
        with mock.patch("core.online_catalog.shelf_entities",
                        side_effect=OSError("no route to host")):
            out = settings.get_shelf(self.conn)
        self.assertEqual(out["racks"], [])
        self.assertTrue(out.get("error"), "a failed read looked like a shop with no racks")


class ShelfEditsReachTheServer(_Online):
    def _last(self):
        self.assertTrue(self.queued, "nothing was queued -- the edit went nowhere")
        return self.queued[-1]

    def test_a_new_rack_is_queued_with_a_real_id(self):
        settings.mutate_shelf(self.conn, {"action": "add_rack", "name": "RACK C"})
        q = self._last()
        self.assertEqual((q["collection"], q["op"], q["local_id"]), ("racks", "upsert", 55))
        self.assertEqual(q["payload"]["name"], "RACK C")

    def test_a_new_section_keeps_its_rack(self):
        settings.mutate_shelf(
            self.conn, {"action": "add_section", "name": "SEC 9", "rack_id": 2}
        )
        self.assertEqual(self._last()["payload"]["rack_id"], 2)

    def test_a_rename_does_not_orphan_the_row(self):
        settings.mutate_shelf(
            self.conn, {"action": "rename_section", "id": 10, "name": "SEC ONE"}
        )
        payload = self._last()["payload"]
        self.assertEqual(payload["name"], "SEC ONE")
        self.assertEqual(
            payload.get("rack_id"), 1,
            "the rename dropped the parent, re-parenting the section to nothing",
        )

    def test_a_delete_is_queued_as_a_delete(self):
        settings.mutate_shelf(self.conn, {"action": "delete_box", "id": 101})
        q = self._last()
        self.assertEqual((q["collection"], q["op"], q["local_id"]), ("boxes", "delete", 101))

    def test_assigning_a_place_sends_the_whole_medicine(self):
        settings.mutate_shelf(
            self.conn,
            {"action": "assign", "medicine_id": 2, "location": "RACK B/SEC 2/BOX 3"},
        )
        q = self._last()
        self.assertEqual(q["collection"], "medicines")
        self.assertEqual(q["payload"]["location"], "RACK B/SEC 2/BOX 3")
        # The rest of the row must ride along or the upsert blanks it.
        self.assertEqual(q["payload"]["name"], "ZZ AMOX 250")
        self.assertEqual(q["payload"]["stock_qty"], 4)

    def test_unassigning_clears_the_place(self):
        settings.mutate_shelf(self.conn, {"action": "unassign", "medicine_id": 1})
        self.assertEqual(self._last()["payload"]["location"], "")

    def test_the_show_location_switch_is_pushed(self):
        with mock.patch("core.server_api.push_settings_shelf") as push, \
             mock.patch("core.server_live._token", return_value="tok"):
            settings.mutate_shelf(
                self.conn, {"action": "set_show_location", "show_location": True}
            )
        push.assert_called_once()
        self.assertEqual(push.call_args[0][1], {"show_location": True})

    def test_an_unknown_action_is_still_refused(self):
        with self.assertRaises(ValueError):
            settings.mutate_shelf(self.conn, {"action": "explode"})

    def test_a_medicine_the_server_does_not_have_is_refused(self):
        with self.assertRaises(ValueError):
            settings.mutate_shelf(
                self.conn, {"action": "assign", "medicine_id": 999, "location": "X"}
            )


if __name__ == "__main__":
    unittest.main()
