"""Offline-first: a shop-wide setting saved twice before the worker ran never blocks the sync.

_setting_set writes INSERT OR REPLACE, so the second save of e.g. a customer's regular
medicines gets a NEW row id (noted on its own) and the first noted id names nothing. The event
built for it carried no document; the server refuses the whole push over one such event ("doc
required", syncV2.normalizeEvent) -- this PC's every later bill, payment and stock movement
waited behind it for good. No server is contacted here.
"""
from __future__ import annotations

import json
from unittest import mock

from core.offline_first import outbox

from tests.test_offline_first_pc_local import Base as OfBase


class ASettingSavedTwice(OfBase):
    def save_twice(self):
        from core.desktop_settings_service import _setting_set

        with mock.patch("core.settings_mirror.remember"):
            _setting_set(self.conn, "regular_meds:5", "[1]")
            self.conn.commit()
            _setting_set(self.conn, "regular_meds:5", "[1,2]")
            self.conn.commit()

    def events(self):
        return [json.loads(p) for (p,) in self.conn.execute("SELECT payload FROM of_events ORDER BY seq")]

    def test_every_event_carries_a_document(self):
        self.save_twice()
        outbox.build_events(self.conn, "pc-test-install")
        evs = self.events()
        self.assertEqual(len(evs), 1)
        self.assertEqual(evs[0]["doc"], {"name": "regular_meds:5", "value": "[1,2]"})
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM of_dirty").fetchone()[0], 0)

    def test_an_event_an_older_build_made_without_one_is_mended_under_its_number(self):
        bad = {"seq": 4, "event_uuid": "pc-old", "op": "upsert", "collection": "settings",
               "doc": None, "base_version": None, "device_time": "2026-10-25T00:00:00"}
        self.conn.execute(
            "INSERT INTO of_events (seq, event_uuid, collection, op, local_id, payload, created_at) "
            "VALUES (4, 'pc-old', 'settings', 'upsert', 7, ?, 'now')", (json.dumps(bad),))
        self.conn.commit()
        self.assertEqual(outbox.repair_docless_events(self.conn), 1)
        ev = outbox.pending_events(self.conn)[0]
        self.assertEqual(ev["seq"], 4)
        self.assertEqual(ev["event_uuid"], "pc-old")
        self.assertEqual(ev["doc"], {"settings": []})      # the server applies nothing
        self.assertEqual(outbox.repair_docless_events(self.conn), 0)

    def test_the_worker_mends_before_it_sends(self):
        from pathlib import Path

        src = (Path(__file__).resolve().parents[1] / "core" / "offline_first" / "worker.py").read_text(
            encoding="utf-8")
        self.assertIn("outbox.repair_docless_events(conn)", src)


class TheMainPcRelaysACountersOldEvent(OfBase):
    def test_a_docless_counter_event_goes_up_as_the_filler(self):
        from core.lan import bridge

        bridge.ensure_tables(self.conn)
        bad = {"seq": 9, "event_uuid": "pc-counter-old", "op": "upsert", "collection": "settings", "doc": None}
        cols = [r[1] for r in self.conn.execute("PRAGMA table_info(of_lan_relay)")]
        vals = {"install_id": "pc-counter", "seq": 9, "event_uuid": "pc-counter-old",
                "payload": json.dumps(bad), "cloud_status": "pending"}
        vals = {k: v for k, v in vals.items() if k in cols}
        self.conn.execute(f"INSERT INTO of_lan_relay ({', '.join(vals)}) VALUES ({', '.join('?' * len(vals))})",
                          tuple(vals.values()))
        self.conn.commit()
        sent = []

        def push(install, events):
            sent.extend(events)
            return {"results": [{"seq": e["seq"], "outcome": "applied"} for e in events]}

        from core.offline_first.worker import open_worker_connection

        conn = open_worker_connection(self.path)
        try:
            bridge.relay_to_cloud(conn, push=push)
        finally:
            conn.close()
        self.assertEqual(sent[0]["doc"], {"settings": []})
        self.assertEqual(sent[0]["seq"], 9)
