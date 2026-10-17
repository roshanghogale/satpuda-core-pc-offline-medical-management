"""The pharmacist's signature picture on the printed bill (owner, 17 Oct 2026).

The shop uploads a transparent PNG under Pharmacy Profile; it prints in the bill's signature
box, between "For SHOP" and "SIGN OF Q.P.". It is kept like the logo (core.store_images): per
store in AppData and carried with the store. Nothing here prints or reaches a server: AppData
is a temp folder and the store is Offline.
"""
from __future__ import annotations

import base64
import io
import os
import tempfile
import unittest
from unittest import mock

from tests._bill_fixtures import discounted_bill_context, isolated_bill_settings

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _png() -> bytes:
    from PIL import Image, ImageDraw

    img = Image.new("RGBA", (300, 100), (0, 0, 0, 0))
    ImageDraw.Draw(img).line([(10, 80), (150, 20), (290, 70)], fill=(0, 0, 120, 255), width=6)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class _TempStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        import core.store_images as si

        for p in (
            mock.patch.object(si, "_appdata_dir", return_value=self.tmp.name),
            mock.patch.object(si, "_store_key", return_value="Store_Sig_Test"),
            mock.patch.object(si, "_is_online", return_value=False),
        ):
            p.start()
            self.addCleanup(p.stop)
        si._reset_server_cache()

    def act(self, action, **data):
        from core.desktop_settings_service import signature_action

        return signature_action(action, data)


class UploadShowRemove(_TempStore):
    def test_upload_then_it_is_read_back_and_printed(self):
        self.assertEqual(self.act("get_signature")["data_uri"], "")
        res = self.act("upload_signature", filename="sign.png",
                       data_base64=base64.b64encode(_png()).decode())
        self.assertTrue(res["ok"], res)
        self.assertTrue(res["data_uri"].startswith("data:image/png;base64,"))
        self.assertNotIn("no transparent background", res["message"])
        self.assertEqual(self.act("get_signature")["data_uri"], res["data_uri"])

        from core.bill_context import _signature_to_base64

        self.assertEqual(_signature_to_base64(), res["data_uri"])

    def test_a_jpeg_is_used_but_the_shop_is_told_about_the_white(self):
        from PIL import Image

        buf = io.BytesIO()
        Image.new("RGB", (200, 80), (255, 255, 255)).save(buf, format="JPEG")
        res = self.act("upload_signature", filename="sign.jpg",
                       data_base64=base64.b64encode(buf.getvalue()).decode())
        self.assertTrue(res["ok"], res)
        self.assertIn("no transparent background", res["message"])

    def test_remove_clears_it(self):
        self.act("upload_signature", filename="sign.png", data_base64=base64.b64encode(_png()).decode())
        self.assertTrue(self.act("remove_signature")["ok"])
        self.assertEqual(self.act("get_signature")["data_uri"], "")

    def test_not_a_picture_is_refused(self):
        res = self.act("upload_signature", filename="notes.txt", data_base64=base64.b64encode(b"hi").decode())
        self.assertFalse(res["ok"])

    def test_it_is_carried_with_the_store_like_the_logo(self):
        from core import store_images as si

        self.assertEqual(si._KV_NAMES[si.BILL_SIGNATURE], "bill_signature_image")

    def test_the_pharmacy_action_routes_to_it(self):
        from core.desktop_settings_service import pharmacy_action

        self.assertEqual(pharmacy_action(None, {"action": "get_signature"})["data_uri"], "")


class OnTheBill(unittest.TestCase):
    URI = "data:image/png;base64," + base64.b64encode(_png()).decode()

    def render(self, *, uri: str, **settings) -> str:
        from bill_templates.classic import render_classic_bill_html_multi

        ctx = discounted_bill_context()
        ctx.signature_src = uri
        with isolated_bill_settings():
            from core.bill_config import apply_print_bill_layout, load_bill_print_settings

            st = apply_print_bill_layout(dict(load_bill_print_settings()), print_slot_copies=1)
            st["paper_size"] = "A5"
            st.update(settings)
            html = render_classic_bill_html_multi([ctx, ctx], st)
        return html.split("</style>", 1)[1]

    def test_it_sits_between_for_shop_and_the_caption(self):
        body = self.render(uri=self.URI)
        self.assertEqual(body.count('class="sign-img"'), 2)
        cell = body[body.index('class="signature-cell"'):]
        self.assertLess(cell.index("For SATPUDA MEDICAL"), cell.index("sign-img"))
        self.assertLess(cell.index("sign-img"), cell.index("SIGN OF Q.P."))

    def test_no_picture_prints_the_old_empty_box(self):
        body = self.render(uri="")
        self.assertNotIn("sign-img", body)
        self.assertIn("SIGN OF Q.P.", body)

    def test_the_bill_setting_turns_it_off(self):
        self.assertNotIn("sign-img", self.render(uri=self.URI, print_signature_image=False))

    def test_hidden_signature_block_hides_it_too(self):
        self.assertNotIn("sign-img", self.render(uri=self.URI, show_signature=False))

    def test_only_a_picture_uri_is_ever_put_in_the_page(self):
        self.assertNotIn("sign-img", self.render(uri='x" onerror="alert(1)'))

    def test_the_setting_is_offered_with_the_other_bill_fields(self):
        from core.bill_config import BILL_PRINT_FIELD_OPTIONS, DEFAULT_BILL_PRINT_SETTINGS

        self.assertTrue(DEFAULT_BILL_PRINT_SETTINGS["print_signature_image"])
        keys = [k for _g, items in BILL_PRINT_FIELD_OPTIONS for k, _l in items]
        self.assertIn("print_signature_image", keys)

    def test_supplier_documents_carry_it(self):
        with mock.patch("core.bill_context._signature_to_base64", return_value=self.URI):
            from core.document_output import _store_fields_from_profile

            fields = _store_fields_from_profile((1, "SHOP", "", "", "", "", "", 0, None, "", "", 0))
        self.assertEqual(fields["signature_src"], self.URI)


class ThePage(unittest.TestCase):
    def test_pharmacy_profile_has_upload_and_remove(self):
        with open(os.path.join(_REPO, "desktop", "src", "pages", "settings", "PharmacyPanels.tsx"),
                  encoding="utf-8") as fh:
            page = fh.read()
        for text in ("action: 'upload_signature'", "action: 'remove_signature'",
                     "action: 'get_signature'", "Bill Signature"):
            self.assertIn(text, page)


if __name__ == "__main__":
    unittest.main()
