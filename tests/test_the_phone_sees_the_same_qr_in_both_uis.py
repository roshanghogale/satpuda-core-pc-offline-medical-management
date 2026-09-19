"""Mobile Import shows the same QR in Tauri that Classic has always drawn.

Classic draws it itself, in Tk: ui/shared/import_from_mobile.py encodes
get_receive_url() with qrcode.QRCode(version=1, box_size=6, border=2) and packs
the PhotoImage into a Toplevel. A Tk image cannot cross the engine's HTTP
boundary, and the service the Tauri page actually calls returned only
url/ip/port -- so the React panel had the address as text and no picture, which
is the "no QR" the owner sees.

The engine now hands the picture over as a PNG data URI. These tests pin the two
things that make it the right picture:

* it encodes exactly the receive URL the phone has to reach -- the same matrix a
  fresh encode with Classic's own parameters produces, so the two UIs can never
  drift into pointing at different addresses;
* it reaches the panel through the very action the page calls
  (import_action 'start_mobile' / 'mobile_status'), as a PNG data URI.

Plus the graceful degrade Classic has (a PC without qrcode is told to type the
address, not shown a crash), and a scripted phone proving the address in the QR
is the one that accepts a payload.

No phone and no LAN: the receiver is bound to loopback on a free port and urllib
plays the phone. No database is opened and no shop is touched.
"""
from __future__ import annotations

import base64
import inspect
import io
import json
import os
import re
import socket
import unittest
import urllib.request
from unittest import mock

from core import desktop_mobile_import_service as svc
from core import desktop_settings_service, mobile_import_server, qr_image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
DATA_URI = "data:image/png;base64,"
# Stands in for the encoder on a machine that has no qrcode installed: still a
# PNG data URI, so what the panel receives is asserted either way.
STUB_QR = DATA_URI + base64.b64encode(PNG_MAGIC + b"stub").decode("ascii")

PHONE_EXPORT = {
    "export_type": "medicines",
    "device_name": "Test Phone",
    "export_date": "2026-09-16",
    "medicines": [
        {
            "name": "DOLO 650",
            "type": "Tablet",
            "batch_no": "B1",
            "expiry_date": "12/27",
            "stock_qty": 30,
            "unit": "10",
            "mrp": 35.0,
            "rate": 25.0,
            "gst_pct": 5.0,
        }
    ],
}


def src(*parts):
    with io.open(os.path.join(ROOT, *parts), encoding="utf-8") as fh:
        return fh.read()


def shown_text(ts):
    """TSX with comments removed -- what can reach the screen."""
    ts = re.sub(r"/\*.*?\*/", "", ts, flags=re.S)
    return re.sub(r"(?m)^\s*//.*$", "", ts)


def _free_port() -> int:
    sock = socket.socket()
    try:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])
    finally:
        sock.close()


class TheEngineOffersTheQr(unittest.TestCase):
    """The receiver runs on loopback only -- never 0.0.0.0 -- inside tests."""

    def setUp(self):
        self._forget_payload()
        self.addCleanup(self._forget_payload)
        self.addCleanup(mobile_import_server.stop_mobile_import_server)

        real_server = mobile_import_server.ThreadingHTTPServer
        real_start = mobile_import_server.start_mobile_import_server
        port = _free_port()

        class LoopbackOnly(real_server):
            def __init__(self, address, handler):
                super().__init__(("127.0.0.1", address[1]), handler)

        for patch in (
            mock.patch.object(mobile_import_server, "ThreadingHTTPServer", LoopbackOnly),
            mock.patch.object(
                mobile_import_server,
                "start_mobile_import_server",
                lambda on_receive, _p=port: real_start(on_receive, _p),
            ),
            mock.patch.object(mobile_import_server, "get_lan_ip", return_value="127.0.0.1"),
        ):
            patch.start()
            self.addCleanup(patch.stop)

    @staticmethod
    def _forget_payload():
        svc._last_raw = ""
        svc._last_data = None

    def test_start_and_status_both_offer_the_qr_of_the_receive_url(self):
        seen = []

        def recorder(text, *, box_size=qr_image.BILL_BOX_SIZE, border=qr_image.DEFAULT_BORDER):
            seen.append((text, box_size, border))
            return STUB_QR

        with mock.patch.object(qr_image, "qr_png_data_uri", recorder):
            started = svc.start_mobile_server()
            status = svc.mobile_server_status()

        url = started["url"]
        self.assertTrue(url.endswith("/mobile-import"), url)
        self.assertEqual(status["url"], url)
        # Both payloads encoded the receive URL and nothing else.
        self.assertEqual([text for text, _b, _r in seen], [url, url])
        for _text, box_size, border in seen:
            self.assertEqual(box_size, qr_image.SCREEN_BOX_SIZE)  # Classic's 6
            self.assertEqual(border, qr_image.DEFAULT_BORDER)  # Classic's 2
        self.assertEqual(started["qr"], STUB_QR)
        self.assertEqual(status["qr"], STUB_QR)
        self.assertEqual(status["qr_error"], "")

    def test_the_qr_matrix_is_a_fresh_encode_of_the_receive_url(self):
        try:
            import qrcode
            from PIL import Image
        except ImportError as exc:  # a dev machine without the encoder
            self.skipTest(f"qrcode/PIL not installed here: {exc}")

        svc.start_mobile_server()
        status = svc.mobile_server_status()

        self.assertTrue(status["qr"].startswith(DATA_URI), status["qr"][:40])
        png = base64.b64decode(status["qr"][len(DATA_URI):])
        self.assertTrue(png.startswith(PNG_MAGIC), "not a PNG")

        # Exactly what Classic encodes, encoded again here.
        fresh = qrcode.QRCode(
            version=1,
            box_size=qr_image.SCREEN_BOX_SIZE,
            border=qr_image.DEFAULT_BORDER,
        )
        fresh.add_data(status["url"])
        fresh.make(fit=True)
        expected = [[bool(cell) for cell in row] for row in fresh.get_matrix()]

        box = qr_image.SCREEN_BOX_SIZE
        dim = len(expected)
        img = Image.open(io.BytesIO(png)).convert("L")
        self.assertEqual(img.size, (dim * box, dim * box))
        got = [
            [
                img.getpixel((col * box + box // 2, row * box + box // 2)) < 128
                for col in range(dim)
            ]
            for row in range(dim)
        ]
        self.assertEqual(got, expected)

    def test_a_pc_without_the_encoder_is_told_why_instead_of_breaking(self):
        missing = ModuleNotFoundError("No module named 'qrcode'")
        with mock.patch.object(qr_image, "qr_png_data_uri", side_effect=missing):
            started = svc.start_mobile_server()
            status = svc.mobile_server_status()

        for payload in (started, status):
            self.assertEqual(payload["qr"], "")
            self.assertIn("qrcode", payload["qr_error"])
        # The address still works -- Classic's "type the URL instead" case.
        self.assertTrue(status["running"])
        self.assertTrue(status["url"].endswith("/mobile-import"))

    def test_a_stopped_receiver_offers_no_qr_and_no_complaint(self):
        svc.start_mobile_server()
        svc.stop_mobile_server()
        status = svc.mobile_server_status()
        self.assertFalse(status["running"])
        self.assertEqual(status["url"], "")
        self.assertEqual(status["qr"], "")
        self.assertEqual(status["qr_error"], "")

    def test_the_action_the_panel_calls_carries_the_png_to_the_page(self):
        with mock.patch.object(qr_image, "qr_png_data_uri", return_value=STUB_QR):
            started = desktop_settings_service.import_action(None, {"action": "start_mobile"})
            status = desktop_settings_service.import_action(None, {"action": "mobile_status"})

        for payload in (started, status):
            self.assertTrue(payload.get("ok"), payload)
            self.assertTrue(str(payload["qr"]).startswith(DATA_URI), str(payload["qr"])[:40])
            self.assertTrue(
                base64.b64decode(str(payload["qr"])[len(DATA_URI):]).startswith(PNG_MAGIC)
            )

    def test_a_scripted_phone_reaches_the_address_the_qr_carries(self):
        started = svc.start_mobile_server()
        url = started["url"]

        with urllib.request.urlopen(url, timeout=5) as resp:
            ping = json.loads(resp.read().decode("utf-8"))
        self.assertEqual(ping.get("service"), "satpuda-mobile-import")

        body = json.dumps(PHONE_EXPORT).encode("utf-8")
        req = urllib.request.Request(
            url, data=body, headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            answer = json.loads(resp.read().decode("utf-8"))
        self.assertTrue(answer.get("received"))
        self.assertEqual(answer.get("export_type"), "medicines")

        status = svc.mobile_server_status()
        self.assertTrue(status["has_payload"])
        self.assertEqual(json.loads(status["last_raw"]), PHONE_EXPORT)
        self.assertEqual(status["last_preview"]["count"], 1)
        self.assertEqual(status["last_preview"]["preview"][0]["name"], "DOLO 650")


class TheTauriPanelShowsItNextToTheAddress(unittest.TestCase):
    def panel(self):
        return src("desktop", "src", "pages", "settings", "ImportDataPanels.tsx")

    def test_the_panel_renders_the_png_the_engine_sends(self):
        text = shown_text(self.panel())
        self.assertIn("src={mobileQr}", text)
        # Both answers set it: the start and the refresh.
        self.assertEqual(text.count("setMobileQr(String(res.qr || ''))"), 2)

    def test_the_address_stays_beside_the_qr(self):
        text = shown_text(self.panel())
        self.assertIn("mobile-receiver", text)
        self.assertIn("value={mobileInfo}", text)
        self.assertIn("mobile-qr", src("desktop", "src", "satpuda.css"))

    def test_stopping_the_receiver_takes_the_qr_off_the_screen(self):
        text = shown_text(self.panel())
        stop = text[text.index("action === 'stop_mobile'"):]
        self.assertIn("setMobileQr('')", stop[:400])

    def test_the_instructions_name_the_screen_the_phone_really_has(self):
        text = shown_text(self.panel())
        self.assertIn("Mobile Import", text)
        self.assertNotIn("open the URL on the same WiFi", text)


class ClassicKeepsItsOwnQr(unittest.TestCase):
    def classic(self):
        return src("ui", "shared", "import_from_mobile.py")

    def test_classic_still_draws_the_same_code_itself(self):
        py = self.classic()
        self.assertIn("qrcode.QRCode(version=1, box_size=6, border=2)", py)
        self.assertIn("ImageTk.PhotoImage(img)", py)

    def test_classic_no_longer_sends_the_owner_to_a_screen_that_does_not_exist(self):
        py = self.classic()
        # There is no Export tab and no scanner in the Android app.
        self.assertNotIn("Scan this QR", py)
        self.assertNotIn("Scan QR from SatpudaCore App", py)
        self.assertIn("Settings → Mobile Import", py)


class TheBillQrIsUndisturbed(unittest.TestCase):
    def test_bill_printing_still_imports_the_helper_under_its_own_name(self):
        from core import upi_qr

        self.assertIs(upi_qr.qr_png_data_uri, qr_image.qr_png_data_uri)

    def test_bills_keep_the_size_they_have_always_printed_at(self):
        params = inspect.signature(qr_image.qr_png_data_uri).parameters
        self.assertEqual(params["box_size"].default, 8)
        self.assertEqual(params["border"].default, 2)
        self.assertEqual(qr_image.BILL_BOX_SIZE, 8)

    def test_the_bill_asks_for_no_size_of_its_own(self):
        py = src("core", "upi_qr.py")
        self.assertIn("ctx.upi_qr_src = qr_png_data_uri(url)", py)


if __name__ == "__main__":
    unittest.main()
