"""Online bill save and print without megabytes of downloads (owner, 17 Oct 2026: "saving
bills or editing them are very slow in the new version, online").

Measured against a copy of a large shop's data, one bill used to pull about 8 MB: the whole
customer list (twice), every receipt and return, the settings list with the home banner
picture in it (three times), the year's bills, the whole inventory -- and Contacts or
Payments the store's every bill (2.2 MB). Now the same steps take a few KB. Nothing here
reaches a server.
"""
from __future__ import annotations

import os
import time
import unittest
from unittest import mock

import core.online_catalog as oc

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def src(*parts) -> str:
    with open(os.path.join(_REPO, *parts), encoding="utf-8") as fh:
        return fh.read()


class _Catalog(unittest.TestCase):
    def setUp(self):
        oc.invalidate()
        self.addCleanup(oc.invalidate)
        for p in (mock.patch.object(oc, "_token", return_value="t"),
                  mock.patch("core.server_api.store_token_for_active", return_value="t"),
                  mock.patch.object(oc, "_save_snapshot_key", create=True)):
            p.start()
            self.addCleanup(p.stop)


class ChangesNotTheWholeList(_Catalog):
    CUSTOMERS = [{"id": 1, "name": "RAM", "total_due": 10.0}, {"id": 2, "name": "SHAM", "total_due": 0.0}]

    def load(self):
        with mock.patch.object(oc, "_rows_from_store", return_value=[dict(c) for c in self.CUSTOMERS]) as full:
            rows = oc.customers()
        return rows, full

    def test_after_the_ttl_only_the_changes_are_asked_for(self):
        rows, full = self.load()
        self.assertEqual(len(rows), 2)
        with mock.patch.object(oc, "_rows_from_store", side_effect=AssertionError("whole list")), \
             mock.patch.object(oc.time, "time", return_value=time.time() + oc._TTL_SEC + 5), \
             mock.patch("core.server_api._request", return_value={
                 "ok": True, "meta": {"server_time": "2026-10-17T13:00:00Z"},
                 "data": [{"id": 1, "name": "RAM", "total_due": 0.0},
                          {"id": 2, "deleted": True},
                          {"id": 3, "name": "NEW", "total_due": 5.0}]}) as req:
            rows = oc.customers()
        path = req.call_args.args[1]
        self.assertTrue(path.startswith("/api/sync/customers?since="), path)
        got = {int(r["id"]): r.get("total_due") for r in rows}
        self.assertEqual(got, {1: 0.0, 3: 5.0})
        self.assertEqual(oc.find_customer_by_name("new")["id"], 3)

    def test_invalidate_keeps_the_list_and_asks_for_changes(self):
        self.load()
        oc.invalidate("customers")
        with mock.patch.object(oc, "_rows_from_store", side_effect=AssertionError("whole list")), \
             mock.patch("core.server_api._request", return_value={"ok": True, "data": []}) as req:
            self.assertEqual(len(oc.customers()), 2)
        req.assert_called_once()

    def test_the_whole_list_again_every_half_hour(self):
        self.load()
        later = time.time() + oc._FULL_RELOAD_SEC + 5
        with mock.patch.object(oc.time, "time", return_value=later), \
             mock.patch("core.server_api._request", side_effect=AssertionError("change pull")), \
             mock.patch.object(oc, "_rows_from_store", return_value=[{"id": 9, "name": "X"}]) as full:
            rows = oc.customers()
        full.assert_called_once()
        self.assertEqual([r["id"] for r in rows], [9])

    def test_a_failed_change_pull_reads_the_whole_list(self):
        self.load()
        oc.invalidate("customers")
        with mock.patch("core.server_api._request", side_effect=TimeoutError("x")), \
             mock.patch.object(oc, "_rows_from_store", return_value=[{"id": 7, "name": "Y"}]) as full:
            rows = oc.customers()
        full.assert_called_once()
        self.assertEqual([r["id"] for r in rows], [7])

    def test_a_hidden_medicine_leaves_the_inventory_list(self):
        inv = {"rows": [{"id": 5, "name": "A", "stock_qty": 3}, {"id": 6, "name": "B", "stock_qty": 4}]}
        with mock.patch("core.store_query_client.list_inventory", return_value=inv):
            oc.medicines()
        oc.invalidate("medicines")
        with mock.patch("core.store_query_client.list_inventory", side_effect=AssertionError("whole list")), \
             mock.patch("core.server_api._request", return_value={"ok": True, "data": [
                 {"id": 5, "name": "A", "stock_qty": 2}, {"id": 6, "name": "B", "is_hidden": 1}]}):
            rows = oc.medicines()
        self.assertEqual([(r["id"], r["stock_qty"]) for r in rows], [(5, 2)])


class OneCustomerByName(_Catalog):
    def test_force_asks_the_server_for_that_name_only(self):
        with mock.patch("core.store_query_client.list_customers",
                        return_value={"rows": [{"id": 4, "name": "A K PAWAR", "total_due": 12.5}]}) as q, \
             mock.patch.object(oc, "_rows_from_store", side_effect=AssertionError("whole list")):
            got = oc.find_customer_by_name("a k pawar", force=True)
        self.assertEqual((got["id"], got["total_due"]), (4, 12.5))
        self.assertEqual(q.call_args.kwargs["q"], "a k pawar")


class SettingsWithoutTheBanner(unittest.TestCase):
    def test_gst_and_regulars_ask_by_prefix_and_remember_an_empty_answer(self):
        from core import customer_gst, regular_medicines

        for mod in (customer_gst, regular_medicines):
            mod._cache.update(at=0.0, rows={})
            with mock.patch("core.server_api._request", return_value={"ok": True, "data": []}) as req, \
                 mock.patch("core.server_live._token", return_value="t"):
                mod._server_rows()
                mod._server_rows()
            req.assert_called_once()
            self.assertIn("?prefix=", req.call_args.args[1])

    def test_bill_pictures_ask_by_name(self):
        import core.store_images as si

        si._reset_server_cache()
        with mock.patch.object(si, "_is_online", return_value=True), \
             mock.patch.object(si, "_store_key", return_value="S"), \
             mock.patch("core.server_live._token", return_value="t"), \
             mock.patch("core.server_api._request", return_value={"ok": True, "data": [
                 {"name": "home_banner_image", "value": "data:image/png;base64,BIG"}]}) as req:
            self.assertEqual(si.fetch_from_server(si.BILL_LOGO), "")
        self.assertIn("?names=bill_logo_image,bill_signature_image", req.call_args.args[1])
        si._reset_server_cache()

    def test_the_server_filters_the_list(self):
        route = src("..", "server-live", "src", "routes", "sync.js") if os.path.isdir(
            os.path.join(_REPO, "..", "server-live")) else ""
        if route:
            self.assertIn("req.query.names", route)
            self.assertIn("req.query.prefix", route)


class PdfThroughOneBrowser(unittest.TestCase):
    def test_the_kept_browser_is_tried_first(self):
        from core import bill_output

        def fake(html, pdf, exe, **kw):
            with open(pdf, "wb") as fh:
                fh.write(b"%PDF-1.4" + b"0" * 600)
            return True

        import tempfile

        d = tempfile.mkdtemp()
        html, pdf = os.path.join(d, "b.html"), os.path.join(d, "b.pdf")
        open(html, "w").write("<html></html>")
        with mock.patch.object(bill_output, "_pdf_browser_attempts", return_value=[("x.exe", "--headless=new", 30)]), \
             mock.patch("core.pdf_browser.html_to_pdf", side_effect=fake) as kept, \
             mock.patch("subprocess.run", side_effect=AssertionError("started a browser")):
            self.assertTrue(bill_output._try_pdf_via_browser(html, pdf))
        kept.assert_called_once()

    def test_it_falls_back_to_a_browser_per_pdf(self):
        from core import bill_output

        import tempfile

        d = tempfile.mkdtemp()
        html, pdf = os.path.join(d, "b.html"), os.path.join(d, "b.pdf")
        open(html, "w").write("<html></html>")

        def run(cmd, **kw):
            with open(pdf, "wb") as fh:
                fh.write(b"%PDF-1.4" + b"0" * 600)

        with mock.patch.object(bill_output, "_pdf_browser_attempts", return_value=[("x.exe", "--headless=new", 30)]), \
             mock.patch("core.pdf_browser.html_to_pdf", return_value=False), \
             mock.patch.object(bill_output, "_remember_pdf_browser"), \
             mock.patch("subprocess.run", side_effect=run) as started:
            self.assertTrue(bill_output._try_pdf_via_browser(html, pdf))
        started.assert_called_once()

    def test_no_devtools_origin_is_opened(self):
        # as a command-line argument (a quoted string), not merely named in a comment
        self.assertNotIn('"--remote-allow-origins', src("core", "pdf_browser.py"))


class HiddenPagesDoNotReload(unittest.TestCase):
    def test_pages_behind_sales_wait_until_opened(self):
        for f in ("HomePage.tsx", "InventoryPage.tsx", "SalesHistoryPage.tsx",
                  "PurchaseHistoryPage.tsx", "SettingsPage.tsx"):
            text = src("desktop", "src", "pages", f)
            self.assertRegex(text, r"if \(!syncRefreshNonce \|\| !active\) return", f)


if __name__ == "__main__":
    unittest.main()
