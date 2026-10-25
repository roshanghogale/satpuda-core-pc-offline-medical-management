"""Settings > Layout & Lists: medicine types and schedules are the store's lists.

sync_coordinator.after_layout_saved was written for this save and never called: a medicine type
added on one PC reached no other device in any mode, and the next village saved on another
device sent THAT device's lists, which then replaced this PC's on the pull. Offline-first now
notes the dropdowns for the worker like a village save; Online pushes them; Offline sends
nothing. No server is contacted here.
"""
from __future__ import annotations

import sqlite3
import unittest
from contextlib import ExitStack
from unittest import mock

from core import desktop_settings_service as svc
from core import sync_coordinator as sc
from core.offline_first import pull

from tests.test_offline_first_pc_local import Base as OfBase


class _Layout(unittest.TestCase):
    def setUp(self):
        self.layout = {"med_types": ["Tablet", "Syrup"], "schedules": ["", "H"]}
        self.saved = []
        stack = ExitStack()
        self.addCleanup(stack.close)
        for p in (
            mock.patch("core.layout_config.load_layout", lambda: dict(self.layout)),
            mock.patch("core.layout_config.save_layout", lambda d: self.saved.append(dict(d))),
            mock.patch.object(svc, "get_layout_lists", lambda: {"ok": True}),
        ):
            stack.enter_context(p)
        self.conn = sqlite3.connect(":memory:")
        self.addCleanup(self.conn.close)


class TheSaveSendsTheLists(_Layout):
    def test_a_new_medicine_type_is_sent(self):
        with mock.patch.object(sc, "after_layout_saved") as sent:
            svc.save_layout_lists({"med_types": ["Tablet", "Syrup", "Sachet"]}, self.conn)
        sent.assert_called_once_with(self.conn)
        self.assertEqual(self.saved[-1]["med_types"], ["Tablet", "Syrup", "Sachet"])

    def test_a_changed_schedule_list_is_sent(self):
        with mock.patch.object(sc, "after_layout_saved") as sent:
            svc.save_layout_lists({"schedules": ["", "H", "H1"]}, self.conn)
        sent.assert_called_once()

    def test_rows_per_page_alone_send_nothing(self):
        with mock.patch.object(sc, "after_layout_saved") as sent:
            svc.save_layout_lists({"inventory_rows": 20, "med_types": ["Tablet", "Syrup"]}, self.conn)
        sent.assert_not_called()

    def test_online_with_no_internet_says_so_and_keeps_the_save(self):
        from core.online_guard import CloudSaveError

        with mock.patch.object(sc, "after_layout_saved", side_effect=CloudSaveError("server down")):
            out = svc.save_layout_lists({"med_types": ["Tablet"]}, self.conn)
        self.assertIn("did not reach the server", out["sync_warning"])
        self.assertEqual(self.saved[-1]["med_types"], ["Tablet"])

    def test_the_route_hands_over_the_stores_connection(self):
        from pathlib import Path

        src = (Path(__file__).resolve().parents[1] / "core" / "desktop_api.py").read_text(encoding="utf-8")
        self.assertIn("svc.save_layout_lists(body, conn)", src)


class OfflineFirstNotesTheDropdowns(OfBase):
    def test_noted_for_the_worker_not_pushed_on_the_spot(self):
        with mock.patch.object(sc, "is_online_mode", return_value=False), \
                mock.patch("core.offline_first.runtime.is_active", return_value=True), \
                mock.patch("core.offline_first.worker.kick"), \
                mock.patch("core.server_live.push_dropdowns") as pushed:
            sc.after_layout_saved(self.conn)
        pushed.assert_not_called()
        self.assertIn(("dropdowns", 0, "upsert"), self.dirty())

    def test_an_older_copy_from_another_device_waits_for_this_pcs_change(self):
        self.conn.execute("INSERT INTO of_dirty (collection, local_id, op, at) "
                          "VALUES ('dropdowns', 0, 'upsert', 'now')")
        self.conn.commit()
        with mock.patch("core.server_live.apply_server_doc") as applied:
            out = pull.apply_page(self.conn, [{"collection": "dropdowns", "local_id": 0,
                                               "doc": {"med_types": ["Tablet"], "villages": []}}],
                                  to_revision=9)
        applied.assert_not_called()
        self.assertEqual(out["skipped"], 1)

    def test_with_nothing_pending_it_is_applied(self):
        with mock.patch("core.server_live.apply_server_doc") as applied:
            pull.apply_page(self.conn, [{"collection": "dropdowns", "local_id": 0,
                                         "doc": {"med_types": ["Tablet"], "villages": []}}],
                            to_revision=9)
        applied.assert_called_once()


if __name__ == "__main__":
    unittest.main()
