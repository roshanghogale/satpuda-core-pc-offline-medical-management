"""A counter's bills handed to the Main PC over the LAN, and sent on to a REHEARSAL server by the
Main PC (core/lan/bridge.py) -- never live.

    OF_TEST_BASE=http://127.0.0.1:3999 OF_TEST_KEY=SC-V2TEST01 python -m pytest -q tests/test_lan_relay_server.py

The counter has no internet: it bills on its own copy, hands the bills to the Main PC, and the
Main PC sends them to the server under the counter's own device id. Later the counter gets
internet and sends the same events itself. The server, the Main PC and the counter must all end
with each bill once and the stock moved once.
"""
from __future__ import annotations

import os
import unittest

from core.lan import bridge
from tests.test_offline_first_pc_server import BASE, PC

from core.offline_first import client


@unittest.skipUnless(BASE, "set OF_TEST_BASE to a rehearsal server")
class MainPcRelaysACountersBills(unittest.TestCase):
    def setUp(self):
        import uuid

        run = uuid.uuid4().hex[:8]
        self.main = PC("MAIN", f"pc-lan-main-{run}")
        self.counter = PC("COUNTER", f"pc-lan-counter-{run}")
        self.addCleanup(bridge.close_all)
        self.addCleanup(self.main.close)
        self.addCleanup(self.counter.close)
        self.main.prepare()
        self.counter.prepare()

    def stock(self, pc, mid):
        return pc.q("SELECT stock_qty FROM medicines WHERE id=?", mid)[0][0]

    def test_handed_over_bills_reach_the_server_once_whoever_sends_them(self):
        m, c = self.main, self.counter
        mid = m.till.execute(
            "INSERT INTO medicines (name, type, stock_qty, unit, mrp, rate, batch_no) "
            "VALUES ('LAN DOLO','Tablet',100,'10',30,20,'B1')").lastrowid
        cid = m.till.execute("INSERT INTO customers (name) VALUES ('LAN RAM')").lastrowid
        m.till.commit()
        m.sync()
        c.sync()
        self.assertEqual(self.stock(c, mid), 100)

        # The counter, Main PC off and no internet: two bills on its own copy.
        s1, b1 = c.sell(cid, mid, 3)
        s2, b2 = c.sell(cid, mid, 4)
        # The Main PC is back on the LAN: the counter hands them over.
        install, events = bridge.handover_batch(c.path)
        self.assertEqual(install, c.install)
        res = bridge.receive_handover(m.path, install, events, origin="counter")
        bridge.mark_handed(c.path, events, res["accepted"], res["results"])
        self.assertEqual(self.stock(m, mid), 93)
        self.assertEqual(sorted(r[0] for r in m.q("SELECT bill_no FROM sales WHERE COALESCE(deleted,0)=0")),
                         sorted([b1, b2]))

        # The Main PC syncs: its loop sends the counter's events on, under the counter's id.
        m.sync(rounds=3)
        with m.acting():
            self.assertEqual(client.stock([mid])["stock"][0][1], 93, "the server has the counter's bills once")
        self.assertEqual(self.stock(m, mid), 93, "the Main PC's stock did not move twice")
        self.assertEqual(len(m.q("SELECT id FROM sales WHERE COALESCE(deleted,0)=0")), 2)

        # The server's answers reach the counter through the Main PC ...
        res = bridge.receive_handover(m.path, install, [])
        self.assertTrue(res["results"])
        bridge.mark_handed(c.path, [], [], res["results"])
        # ... and when the counter gets internet itself nothing is sent twice.
        c.sync(rounds=3)
        with c.acting():
            self.assertEqual(client.stock([mid])["stock"][0][1], 93)
        self.assertEqual(self.stock(c, mid), 93)
        self.assertEqual(c.q("SELECT COUNT(*) FROM of_events WHERE status='pending'")[0][0], 0)
        m.sync(rounds=2)
        self.assertEqual(self.stock(m, mid), 93)
        self.assertEqual(len(m.q("SELECT id FROM sales WHERE COALESCE(deleted,0)=0")), 2)

    def test_the_counter_sending_first_and_the_main_pc_second_is_still_once(self):
        m, c = self.main, self.counter
        mid = m.till.execute(
            "INSERT INTO medicines (name, type, stock_qty, unit, mrp, rate, batch_no) "
            "VALUES ('LAN CROCIN','Tablet',50,'10',30,20,'B1')").lastrowid
        cid = m.till.execute("INSERT INTO customers (name) VALUES ('LAN SHAM')").lastrowid
        m.till.commit()
        m.sync()
        c.sync()
        c.sell(cid, mid, 5)
        install, events = bridge.handover_batch(c.path)
        bridge.receive_handover(m.path, install, events)
        c.sync(rounds=2)                 # the counter's own internet first
        m.sync(rounds=3)                 # then the Main PC relays the same events
        with m.acting():
            self.assertEqual(client.stock([mid])["stock"][0][1], 45)
        self.assertEqual(self.stock(m, mid), 45)
        self.assertEqual(self.stock(c, mid), 45)
        conn = bridge.connection(m.path)
        self.assertEqual(bridge.relay_counts(conn)["relay_waiting"], 0)


if __name__ == "__main__":
    unittest.main()
