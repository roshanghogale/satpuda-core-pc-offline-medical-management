"""Mobile Import: the QR is on screen when the screen opens, the QR opens a page on the phone,
and that page (and the loader app) learn from the PC whether the store is retail or wholesale.

Owner, 19 Oct 2026: "Still the mobile import QR code is not visible -- that's the main issue."

Root cause: the QR existed only after "Start WiFi receiver" was pressed. The screen opened on
two buttons and an empty box, came back empty after every restart of the app, and a start that
failed put the raw JSON answer in the address box under a note blaming the QR. Scanning the QR
with the phone's camera then opened ``{"ok": true, "service": ...}`` -- there was no page.

Now:
* import action ``mobile_open`` (called when the screen opens) starts the receiver if needed
  and answers with the QR; a receiver that cannot start answers with the reason;
* a PC on no network is told why the address will not work on a phone;
* GET /mobile-import from a browser is the phone entry page (web_app/index.html, the bundled
  copy in a frozen build); /ping and /catalog.json carry store_kind;
* a wholesale purchase from the phone needs Rate A on every line and its Rate A/B/C reach the
  purchase line and the medicine batch; a loader medicine in a wholesale store keeps its
  purchase rate and Rate A/B/C; a retail store never takes them;
* an imported payload is not offered (and imported) a second time.

The receiver is bound to loopback on a free port; no phone, no LAN, no server.
"""
from __future__ import annotations

import base64
import io
import json
import os
import re
import socket
import sqlite3
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from unittest import mock

from core import db_setup, desktop_mobile_import_service as svc
from core import desktop_settings_service, mobile_import_apply, mobile_import_server, qr_image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STUB_QR = "data:image/png;base64," + base64.b64encode(b"\x89PNG\r\n\x1a\nstub").decode("ascii")


def _free_port() -> int:
    sock = socket.socket()
    try:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])
    finally:
        sock.close()


def _src(*parts) -> str:
    with io.open(os.path.join(ROOT, *parts), encoding="utf-8") as fh:
        return fh.read()


class _Receiver(unittest.TestCase):
    """The receiver on loopback only, a page folder of our own, a made-up catalog."""

    kind = "retail"

    def setUp(self):
        svc._last_raw, svc._last_data = "", None
        self.addCleanup(setattr, svc, "_last_data", None)
        self.addCleanup(mobile_import_server.stop_mobile_import_server)
        self.page_dir = tempfile.mkdtemp()
        with open(os.path.join(self.page_dir, "index.html"), "w", encoding="utf-8") as fh:
            fh.write("<!doctype html><title>Purchase Entry</title><div id=root></div>")
        os.makedirs(os.path.join(self.page_dir, "assets"))
        with open(os.path.join(self.page_dir, "assets", "app.js"), "w", encoding="utf-8") as fh:
            fh.write("console.log('page')")

        real_server = mobile_import_server.ThreadingHTTPServer
        real_start = mobile_import_server.start_mobile_import_server
        port = _free_port()

        class LoopbackOnly(real_server):
            def __init__(self, address, handler):
                super().__init__(("127.0.0.1", address[1]), handler)

        catalog = {"ok": True, "store_kind": self.kind, "store_name": "TEST SHOP",
                   "suppliers": [{"name": "TEST SUPPLIER"}], "margins": {"A": 8, "B": 10, "C": 12}}
        for patch in (
            mock.patch.object(mobile_import_server, "ThreadingHTTPServer", LoopbackOnly),
            mock.patch.object(mobile_import_server, "start_mobile_import_server",
                              lambda on_receive, _p=port: real_start(on_receive, _p)),
            mock.patch.object(mobile_import_server, "get_lan_ip", return_value="192.168.1.50"),
            mock.patch.object(mobile_import_server, "page_root", return_value=self.page_dir),
            mock.patch.object(svc, "phone_catalog", return_value=catalog),
            mock.patch.object(svc, "_store_kind", return_value=self.kind),
            mock.patch.object(qr_image, "qr_png_data_uri", return_value=STUB_QR),
        ):
            patch.start()
            self.addCleanup(patch.stop)
        self.base = f"http://127.0.0.1:{port}"

    def get(self, path, accept=None):
        req = urllib.request.Request(self.base + path, headers={"Accept": accept} if accept else {})
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.headers.get("Content-Type", ""), resp.read().decode("utf-8")


class TheScreenOpensWithTheQr(_Receiver):
    def test_opening_the_screen_starts_the_receiver_and_answers_with_the_qr(self):
        self.assertFalse(mobile_import_server.is_running())
        res = desktop_settings_service.import_action(None, {"action": "mobile_open"})
        self.assertTrue(res["ok"], res)
        self.assertTrue(res["running"])
        self.assertEqual(res["qr"], STUB_QR)
        self.assertEqual(res["url"], self.base.replace("127.0.0.1", "192.168.1.50") + "/mobile-import")
        self.assertEqual(res["problem"], "")
        self.assertTrue(res["page_ready"])

    def test_opening_it_again_keeps_the_running_receiver(self):
        first = svc.open_mobile_server()
        with mock.patch.object(mobile_import_server, "start_mobile_import_server") as start:
            again = svc.open_mobile_server()
        start.assert_not_called()
        self.assertEqual(again["url"], first["url"])
        self.assertEqual(again["qr"], STUB_QR)

    def test_a_receiver_that_cannot_start_says_why_instead_of_breaking(self):
        with mock.patch.object(mobile_import_server, "start_mobile_import_server",
                               side_effect=RuntimeError("ports 9123-9132 are all taken")):
            res = svc.open_mobile_server()
        self.assertFalse(res["ok"])
        self.assertIn("ports 9123-9132 are all taken", res["problem"])
        self.assertEqual(res["url"], "")

    def test_a_pc_on_no_network_is_told_the_address_will_not_work_on_a_phone(self):
        with mock.patch.object(mobile_import_server, "get_lan_ip", return_value="127.0.0.1"):
            res = svc.start_mobile_server()
        self.assertEqual(res["qr"], STUB_QR)  # still drawn, the reason is beside it
        self.assertIn("not connected to any WiFi", res["problem"])


class TheQrOpensAPage(_Receiver):
    def test_a_phone_browser_gets_the_entry_page(self):
        svc.start_mobile_server()
        ctype, body = self.get("/mobile-import", accept="text/html,application/xhtml+xml,*/*")
        self.assertIn("text/html", ctype)
        self.assertIn("Purchase Entry", body)
        ctype, body = self.get("/assets/app.js")
        self.assertIn("javascript", ctype)

    def test_the_apps_still_get_json(self):
        svc.start_mobile_server()
        _ctype, body = self.get("/mobile-import")
        self.assertEqual(json.loads(body)["service"], "satpuda-mobile-import")

    def test_nothing_outside_the_page_folder_is_served(self):
        svc.start_mobile_server()
        for path in ("/../core/mobile_import_server.py", "/..%2F..%2Fsecret", "/%2e%2e/x"):
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                self.get(path)
            self.assertEqual(ctx.exception.code, 404, path)

    def test_a_frozen_engine_serves_the_page_it_bundles(self):
        bundle = tempfile.mkdtemp()
        os.makedirs(os.path.join(bundle, "web_app"))
        open(os.path.join(bundle, "web_app", "index.html"), "w").close()
        mock.patch.stopall()  # the real page_root, no fixtures
        with mock.patch.object(sys, "frozen", True, create=True), \
                mock.patch.object(sys, "_MEIPASS", bundle, create=True):
            self.assertEqual(mobile_import_server.page_root(), os.path.join(bundle, "web_app"))

    def test_the_built_page_is_in_the_repo(self):
        page = _src("web_app", "index.html")
        # The phone page of this source: it knows the receiver and the wholesale line.
        self.assertIn("mobile_import", page)
        self.assertIn("Rate A", page)


class ThePageAndTheLoaderLearnTheStoreKind(_Receiver):
    kind = "wholesale"

    def test_ping_and_catalog_say_wholesale(self):
        svc.start_mobile_server()
        ping = json.loads(self.get("/ping")[1])
        self.assertEqual(ping["store_kind"], "wholesale")
        cat = json.loads(self.get("/catalog.json")[1])
        self.assertEqual(cat["store_kind"], "wholesale")
        self.assertEqual(cat["source"], "mobile_import")
        self.assertEqual(cat["post_to"], "/mobile-import")
        self.assertEqual(cat["suppliers"][0]["name"], "TEST SUPPLIER")

    def test_the_screen_shows_which_kind(self):
        res = svc.open_mobile_server()
        self.assertEqual(res["store_kind"], "wholesale")


class _Store(unittest.TestCase):
    wholesale = True

    def setUp(self):
        self.appdata = tempfile.mkdtemp()
        for p in (
            mock.patch("core.sync_prefs.get_sync_mode", lambda: "offline"),
            mock.patch("core.sync_prefs.is_online_mode", return_value=False),
            mock.patch("core.license_manager._appdata_dir", lambda: self.appdata),
            mock.patch("core.online_catalog.medicine_by_id", return_value=None),
            mock.patch("core.server_crud.get_doc", return_value=None),
            mock.patch.dict(os.environ, {"SATPUDA_WHOLESALE_OVERRIDE": "1" if self.wholesale else "0"}),
        ):
            p.start()
            self.addCleanup(p.stop)
        self.conn = sqlite3.connect(tempfile.mktemp(suffix=".db"), check_same_thread=False)
        db_setup.initialise(self.conn)
        self.addCleanup(self.conn.close)

    def phone_bill(self, **line):
        item = {"medicine_name": "TEST CETIRIZINE 10", "type": "Tablet", "batch_no": "CT1",
                "expiry_date": "09/27", "qty": 5, "free_qty": 0, "rate": 12.4, "mrp": 25,
                "gst_percent": 12, "quantity_value": "1x10", "tablets_per_stripe": 10}
        item.update(line)
        return {"export_type": "purchases", "device_name": "Phone (Purchase Entry page)",
                "store_kind": "wholesale",
                "bills": [{"supplier": {"name": "TEST SUPPLIER"}, "purchase_date": "2026-10-19",
                           "bill_number": "T-1", "items": [item]}]}

    def loader_sheet(self, **row):
        med = {"name": "TEST AMOXY 500", "type": "Capsule", "unit": "10", "batch_no": "AX1",
               "expiry_date": "08/27", "stock_qty": 4, "extra_medicine": 0, "mrp": 0,
               "rate": 50, "purchase_rate": 50, "rate_a": 55, "rate_b": 57, "rate_c": 0,
               "gst_percent": 12}
        med.update(row)
        return {"export_type": "medicines", "device_name": "Satpuda Loader",
                "store_kind": "wholesale", "medicines": [med]}


class AWholesalePurchaseFromThePhone(_Store):
    def test_rate_a_b_c_reach_the_line_and_the_batch(self):
        res = mobile_import_apply.apply_mobile_data(
            self.conn, self.phone_bill(purchase_rate=12.4, rate_a=13.5, rate_b=13.9, rate_c=14.2))
        self.assertEqual(res["saved"], 1, res)
        line = self.conn.execute("SELECT rate_a, rate_b, rate_c FROM purchase_items").fetchone()
        self.assertEqual(tuple(line), (13.5, 13.9, 14.2))
        med = self.conn.execute(
            "SELECT purchase_rate, rate_a, rate_b, rate_c, stock_qty FROM medicines "
            "WHERE name LIKE 'TEST CETIRIZINE%'").fetchone()
        self.assertEqual(tuple(med[:4]), (12.4, 13.5, 13.9, 14.2))
        self.assertEqual(med[4], 50)  # 5 strips of 10

    def test_a_line_without_rate_a_is_refused_and_nothing_is_written(self):
        res = mobile_import_apply.apply_mobile_data(self.conn, self.phone_bill(rate_b=13.9))
        self.assertEqual(res["saved"], 0)
        self.assertIn("Rate A", res["errors"][0])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM purchases").fetchone()[0], 0)
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM medicines WHERE name LIKE 'TEST CETIRIZINE%'").fetchone()[0], 0)

    def test_a_loader_medicine_keeps_its_purchase_rate_and_rates(self):
        res = mobile_import_apply.apply_mobile_data(self.conn, self.loader_sheet())
        self.assertEqual(res["inserted"], 1, res)
        row = self.conn.execute(
            "SELECT purchase_rate, rate_a, rate_b, rate_c, stock_qty FROM medicines "
            "WHERE name LIKE 'TEST AMOXY%'").fetchone()
        self.assertEqual(tuple(row[:3]), (50.0, 55.0, 57.0))
        self.assertIn(row[3], (None, 0, 0.0))
        self.assertEqual(row[4], 40)  # 4 strips of 10


class ARetailStoreNeverTakesTheRates(_Store):
    wholesale = False

    def test_retail_purchase_needs_no_rate_a_and_stores_none(self):
        res = mobile_import_apply.apply_mobile_data(self.conn, self.phone_bill(rate_a=99))
        self.assertEqual(res["saved"], 1, res)
        line = self.conn.execute("SELECT rate_a FROM purchase_items").fetchone()
        self.assertIn(line[0], (None, 0, 0.0))

    def test_retail_loader_row_stores_no_rates(self):
        mobile_import_apply.apply_mobile_data(self.conn, self.loader_sheet())
        row = self.conn.execute(
            "SELECT rate_a FROM medicines WHERE name LIKE 'TEST AMOXY%'").fetchone()
        self.assertIn(row[0], (None, 0, 0.0))


class AnImportedPayloadIsNotOfferedAgain(_Store):
    wholesale = False

    def test_after_import_the_receiver_forgets_it(self):
        raw = json.dumps(self.loader_sheet())
        svc._on_receive(raw, json.loads(raw))
        self.addCleanup(setattr, svc, "_last_data", None)
        res = svc.import_mobile(self.conn, {"json": raw})
        self.assertTrue(res["ok"], res)
        status = svc.mobile_server_status()
        self.assertFalse(status["has_payload"])
        self.assertEqual(status["last_raw"], "")


class TheDesktopScreen(unittest.TestCase):
    def panel(self):
        ts = _src("desktop", "src", "pages", "settings", "ImportDataPanels.tsx")
        ts = re.sub(r"/\*.*?\*/", "", ts, flags=re.S)
        return re.sub(r"(?m)^\s*//.*$", "", ts)

    def test_it_asks_for_the_qr_when_it_opens(self):
        text = self.panel()
        self.assertIn("importAction({ action: 'mobile_open' })", text)

    def test_a_failed_start_is_not_put_in_the_address_box(self):
        self.assertNotIn("setMobileInfo(url || JSON.stringify(res))", self.panel())

    def test_no_qr_shows_the_address_big_with_the_reason(self):
        text = self.panel()
        self.assertIn("mobile-url-big", text)
        self.assertIn("{mobileProblem}", text)
        self.assertIn("mobile-url-big", _src("desktop", "src", "satpuda.css"))


if __name__ == "__main__":
    unittest.main()
