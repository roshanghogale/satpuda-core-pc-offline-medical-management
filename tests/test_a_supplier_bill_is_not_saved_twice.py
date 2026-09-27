"""One supplier bill is one purchase, and re-reading it adds to that purchase.

Store 127 held the same supplier bill as purchases 35 and 36, one second apart. The save did
ask the question -- and then warned, after the row was written and the goods were already on
the shelf twice. The shop's words for what it should do instead: "the same invoice number
purchase must not be saved multiple times, and if it already exists it should open in edit mode
with the new medicines from the image import added, if they are missing."

So a NEW purchase for a bill number this supplier already has is refused before anything is
written (the tab keeps its rows; the shop answers), a re-import of the same invoice offers to
merge, and a merge appends only the lines that purchase does not have yet -- never touching one
it does.

Everything runs on an in-memory store. Nothing reaches a server and no file is written.
"""
from __future__ import annotations

import os
import sqlite3
import time
import unittest
from unittest import mock

from core import db_setup, desktop_purchase_service as svc  # noqa: E402
from core import save_warnings, web_purchase_save  # noqa: E402
from core.purchase_line_merge import (  # noqa: E402
    lines_are_the_same,
    merge_new_lines,
    split_new_lines,
)
from core.purchase_importer import ImportedPurchaseItem, PurchaseInvoice  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SUPPLIER = "SHREE PHARMA"
BILL = "INV-9"


def _offline():
    """No catalogue and no server doc: the offline store answers everything."""
    return (
        mock.patch("core.online_catalog.medicine_by_id", return_value=None),
        mock.patch("core.server_crud.get_doc", return_value=None),
    )


class _Shop(unittest.TestCase):
    def setUp(self):
        self.conn = conn = sqlite3.connect(":memory:", check_same_thread=False)
        self.addCleanup(conn.close)
        db_setup.initialise(conn)
        conn.execute(f"INSERT INTO suppliers (id, name) VALUES (1, '{SUPPLIER}')")
        conn.execute("INSERT INTO suppliers (id, name) VALUES (2, 'OTHER AGENCY')")
        conn.commit()

    def line(self, name="PARA TAB", **changes):
        item = {"name": name, "type": "Tablet", "batch": "B1", "expiry": "12/28", "qty": 2,
                "rate": 60.0, "mrp": 100.0, "unit": "10"}
        item.update(changes)
        return item

    def save(self, *, supplier=SUPPLIER, bill=BILL, items=None, **body):
        payload = {
            "supplier_name": supplier,
            "bill_number": bill,
            "purchase_date": "2026-08-31",
            "items": items if items is not None else [self.line()],
            "cash_paid": 0,
        }
        payload.update(body)
        catalog, doc = _offline()
        with catalog, doc:
            return svc.save_purchase_bill(self.conn, payload)

    def live_purchases(self):
        return self.conn.execute(
            "SELECT COUNT(*) FROM purchases WHERE COALESCE(deleted,0)=0"
        ).fetchone()[0]


class TheSecondSaveOfTheSameBill(_Shop):
    def setUp(self):
        super().setUp()
        first = self.save()
        self.assertTrue(first.get("ok"), first)
        self.first_id = int(first["purchase_id"])
        self.first_no = first["purchase_no"]

    def test_it_is_refused_and_writes_nothing(self):
        again = self.save()
        self.assertFalse(again.get("ok"), again)
        self.assertTrue(again.get("need_confirm"))
        self.assertEqual(again.get("code"), "duplicate_bill")
        self.assertEqual(self.live_purchases(), 1, "the refused bill was written anyway")

    def test_the_refusal_names_the_bill_the_shop_already_has(self):
        existing = self.save().get("existing") or {}
        self.assertEqual(existing.get("purchase_id"), self.first_id)
        self.assertEqual(existing.get("purchase_date"), "2026-08-31")
        self.assertEqual(existing.get("item_count"), 1)
        self.assertIn(BILL, self.save().get("message") or "")

    def test_the_same_bill_number_from_another_supplier_saves(self):
        other = self.save(supplier="OTHER AGENCY")
        self.assertTrue(other.get("ok"), other)
        self.assertEqual(self.live_purchases(), 2)

    def test_another_bill_number_from_the_same_supplier_saves(self):
        self.assertTrue(self.save(bill="INV-10").get("ok"))
        self.assertEqual(self.live_purchases(), 2)

    def test_a_bill_with_no_number_is_never_a_duplicate(self):
        self.assertTrue(self.save(bill="").get("ok"))
        self.assertTrue(self.save(bill="").get("ok"))
        self.assertEqual(self.live_purchases(), 3)

    def test_the_shop_can_still_say_save_it_anyway(self):
        anyway = self.save(allow_duplicate=True)
        self.assertTrue(anyway.get("ok"), anyway)
        self.assertEqual(self.live_purchases(), 2)
        # Deliberate, so it is only pointed out -- the old warning still does that.
        self.assertIn("already saved as purchase", "\n".join(anyway.get("warnings") or []))

    def test_editing_the_bill_that_exists_still_works(self):
        catalog, doc = _offline()
        with catalog, doc:
            loaded = svc.load_purchase(self.conn, self.first_id)
        self.assertTrue(loaded.get("ok"), loaded)
        edited = self.save(
            items=loaded["form"]["items"] + [self.line("AMOX CAP", batch="B2")],
            editing_purchase_id=self.first_id,
        )
        self.assertTrue(edited.get("ok"), edited)
        self.assertEqual(self.live_purchases(), 1)
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM purchase_items WHERE purchase_id=?", (self.first_id,)
            ).fetchone()[0],
            2,
        )

    def test_a_store_that_cannot_be_asked_does_not_hold_up_the_save(self):
        # Never decide on an unknown: a lookup that cannot run is not a duplicate.
        with mock.patch(
            "core.save_warnings.find_saved_supplier_bill",
            side_effect=RuntimeError("Cannot reach server"),
        ):
            res = self.save()
        self.assertTrue(res.get("ok"), res)
        self.assertEqual(self.live_purchases(), 2)

    def test_the_online_lookup_itself_answers_nothing_when_the_server_is_down(self):
        with mock.patch("core.sync_prefs.is_online_mode", return_value=True), \
                mock.patch("core.store_query_client.list_purchases",
                           side_effect=RuntimeError("Cannot reach server")):
            self.assertIsNone(save_warnings.find_saved_supplier_bill(
                None, supplier_name=SUPPLIER, bill_number=BILL))


class TheMergeRule(unittest.TestCase):
    """Which parsed lines a purchase already has. See core/purchase_line_merge.py."""

    def saved(self, name="PARA TAB", batch="B1", qty=2, expiry="2028-12-01"):
        return {"name": name, "batch": batch, "qty": qty, "expiry": expiry}

    def test_the_same_medicine_and_batch_is_already_there_however_it_is_spelt(self):
        self.assertTrue(lines_are_the_same(
            self.saved(name="AMOXYRUM-LA", batch="b 1"),
            {"name": "amoxyrum  la", "batch": "B-1", "qty": 9},
        ))

    def test_another_batch_of_the_same_medicine_is_a_new_line(self):
        self.assertFalse(lines_are_the_same(self.saved(), {"name": "PARA TAB", "batch": "B2"}))

    def test_another_medicine_is_a_new_line(self):
        self.assertFalse(lines_are_the_same(self.saved(), {"name": "AMOX CAP", "batch": "B1"}))

    def test_a_blank_batch_matches_only_when_the_count_and_expiry_agree(self):
        # An image import carries WITHOUT BATCH, and a typed line is sometimes left blank; the
        # name alone would swallow a genuine second batch.
        placeholder = {"name": "PARA TAB", "batch": "WITHOUT BATCH", "qty": 2, "expiry": "12/28"}
        self.assertTrue(lines_are_the_same(self.saved(batch=""), placeholder))
        self.assertFalse(lines_are_the_same(self.saved(batch="", qty=5), placeholder))
        self.assertFalse(lines_are_the_same(
            self.saved(batch="", expiry="2029-01-01"), placeholder))

    def test_only_the_missing_lines_are_appended_and_nothing_is_rewritten(self):
        saved = [self.saved(), self.saved(name="AMOX CAP", batch="B2", qty=1)]
        parsed = [
            {"name": "PARA TAB", "batch": "B1", "qty": 99, "rate": 999},  # already there
            {"name": "VITA GEL", "batch": "B7", "qty": 3},                # new
        ]
        merged, added, already = merge_new_lines(saved, parsed)
        self.assertEqual((added, already), (1, 1))
        self.assertEqual([r["name"] for r in merged], ["PARA TAB", "AMOX CAP", "VITA GEL"])
        self.assertEqual(merged[0]["qty"], 2, "an import rewrote a line the shop had saved")
        self.assertNotIn("rate", merged[0])

    def test_a_bill_that_really_lists_the_same_batch_twice_keeps_its_second_line(self):
        missing, already = split_new_lines(
            [self.saved()],
            [{"name": "PARA TAB", "batch": "B1", "qty": 2},
             {"name": "PARA TAB", "batch": "B1", "qty": 2}],
        )
        self.assertEqual((len(missing), len(already)), (1, 1))


class OpeningTheBillThatExistsWithTheRowsOnScreen(_Shop):
    """"Junya bill madhe ughad": the refused tab's rows go onto the saved purchase."""

    def setUp(self):
        super().setUp()
        first = self.save()
        self.first_id = int(first["purchase_id"])

    def merge(self, items):
        catalog, doc = _offline()
        with catalog, doc:
            return svc.merge_lines_into_purchase(
                self.conn, {"purchase_id": self.first_id, "items": items}
            )

    def test_the_saved_bill_comes_back_open_to_edit_with_the_new_lines_added(self):
        out = self.merge([self.line(), self.line("AMOX CAP", batch="B2")])
        self.assertTrue(out.get("ok"), out)
        loaded = out["loaded"]
        self.assertEqual(loaded.get("editing_purchase_id"), self.first_id)
        self.assertEqual((out["items_added"], out["items_already_there"]), (1, 1))
        self.assertEqual(
            [str(i.get("name")) for i in loaded["form"]["items"]],
            ["PARA TAB", "AMOX CAP"],
        )

    def test_nothing_is_written_until_that_bill_is_saved(self):
        self.merge([self.line("AMOX CAP", batch="B2")])
        self.assertEqual(self.live_purchases(), 1)
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM purchase_items WHERE purchase_id=?", (self.first_id,)
            ).fetchone()[0],
            1,
        )

    def test_an_unknown_purchase_is_refused_plainly(self):
        catalog, doc = _offline()
        with catalog, doc:
            out = svc.merge_lines_into_purchase(self.conn, {"purchase_id": 0, "items": []})
        self.assertFalse(out.get("ok"))


def _parsed(name, batch="B1", qty=2, expiry="12/28"):
    return ImportedPurchaseItem(name=name, medicine_type="Tablet", batch=batch, expiry=expiry,
                                qty=qty, rate=60.0, mrp=100.0, pack="10")


class ImportingAnInvoiceThatIsAlreadySaved(_Shop):
    def setUp(self):
        super().setUp()
        first = self.save()
        self.first_id = int(first["purchase_id"])
        self.first_no = first["purchase_no"]

    def session(self, items):
        token = f"tok-{time.time()}"
        svc._IMPORT_SESSIONS[token] = {
            "invoice": PurchaseInvoice(
                supplier_name=SUPPLIER, invoice_number=BILL, invoice_date="2026-08-31",
                items=list(items), source_type="csv", parser="test",
            ),
            "path_list": [], "tmp_dir": None, "created": time.time(), "page_count": 1,
        }
        self.addCleanup(svc._IMPORT_SESSIONS.pop, token, None)
        return token

    def apply(self, items, **body):
        catalog, doc = _offline()
        with catalog, doc:
            return svc.apply_purchase_import(
                self.conn, {"import_token": self.session(items), **body}
            )

    def test_the_apply_asks_before_it_loads_anything(self):
        out = self.apply([_parsed("PARA TAB"), _parsed("AMOX CAP", batch="B2")])
        self.assertFalse(out.get("ok"), out)
        self.assertTrue(out.get("need_confirm"))
        self.assertEqual(out.get("code"), "bill_already_saved")
        self.assertEqual(out["existing"]["purchase_id"], self.first_id)
        # One of the two parsed lines is on that purchase already.
        self.assertEqual(out.get("new_line_count"), 1)
        self.assertEqual(out.get("already_line_count"), 1)
        self.assertIn("already saved aahe", out.get("message") or "")
        self.assertIn("navin aushadhe jodu ka", out.get("message") or "")

    def test_merging_hands_back_the_existing_purchase_plus_only_the_missing_lines(self):
        out = self.apply(
            [_parsed("PARA TAB"), _parsed("AMOX CAP", batch="B2")],
            merge_into_purchase_id=self.first_id,
        )
        self.assertTrue(out.get("ok"), out)
        self.assertTrue(out.get("merged"))
        loaded = out["loaded"]
        self.assertEqual(loaded.get("editing_purchase_id"), self.first_id)
        self.assertEqual(
            loaded.get("purchase_no"),
            self.conn.execute(
                "SELECT purchase_no FROM purchases WHERE id=?", (self.first_id,)
            ).fetchone()[0],
        )
        names = [str(i.get("name")) for i in loaded["form"]["items"]]
        self.assertEqual(names, ["PARA TAB", "AMOX CAP"])
        self.assertEqual((out["items_added"], out["items_already_there"]), (1, 1))
        self.assertEqual(self.live_purchases(), 1, "the merge wrote a second purchase")

    def test_a_line_already_on_the_bill_is_not_added_a_second_time(self):
        out = self.apply([_parsed("PARA TAB")], merge_into_purchase_id=self.first_id)
        self.assertTrue(out.get("ok"), out)
        self.assertEqual(out["items_added"], 0)
        self.assertEqual([str(i.get("name")) for i in out["loaded"]["form"]["items"]],
                         ["PARA TAB"])

    def test_confirm_merge_finds_the_purchase_on_its_own(self):
        out = self.apply([_parsed("AMOX CAP", batch="B2")], confirm="merge")
        self.assertTrue(out.get("ok"), out)
        self.assertEqual(out.get("merge_into_purchase_id"), self.first_id)

    def test_naveen_bill_banav_loads_an_ordinary_new_form(self):
        out = self.apply([_parsed("AMOX CAP", batch="B2")], allow_duplicate=True)
        self.assertTrue(out.get("ok"), out)
        self.assertFalse(out.get("merged"))
        self.assertIn("form", out)

    def test_an_invoice_the_shop_does_not_have_is_not_asked_about(self):
        catalog, doc = _offline()
        token = self.session([_parsed("AMOX CAP", batch="B2")])
        svc._IMPORT_SESSIONS[token]["invoice"].invoice_number = "INV-FRESH"
        with catalog, doc:
            out = svc.apply_purchase_import(self.conn, {"import_token": token})
        self.assertTrue(out.get("ok"), out)
        self.assertFalse(out.get("merged"))


class TheWebAndPhoneJsonPath(_Shop):
    def bill(self, bill_number=BILL, **extra):
        payload = {
            "supplier_name": SUPPLIER,
            "bill_number": bill_number,
            "purchase_date": "2026-08-31",
            "items": [{
                "medicine_name": "PARA TAB", "type": "Tablet", "batch_no": "B1",
                "expiry_date": "12/28", "qty": 2, "rate": 60.0, "mrp": 100.0,
                "quantity_value": "10",
            }],
        }
        payload.update(extra)
        return payload

    def post(self, *bills, **data):
        catalog, doc = _offline()
        with catalog, doc:
            return web_purchase_save.save_purchases_from_web_json(
                self.conn, {"bills": list(bills), **data}
            )

    def test_a_bill_the_shop_already_has_is_skipped_and_named(self):
        first = self.post(self.bill())
        self.assertEqual(first["saved"], 1)
        again = self.post(self.bill(), self.bill("INV-11"))
        self.assertEqual(again["saved"], 1, "only the new bill should have saved")
        self.assertEqual(again["errors"], [])
        [skipped] = again["skipped_duplicate"]
        self.assertEqual(skipped["bill_number"], BILL)
        self.assertTrue(skipped["purchase_no"])
        self.assertEqual(self.live_purchases(), 2)

    def test_allow_duplicate_on_the_payload_saves_it(self):
        self.post(self.bill())
        again = self.post(self.bill(), allow_duplicate=True)
        self.assertEqual(again["saved"], 1)
        self.assertFalse(again["skipped_duplicate"])
        self.assertEqual(self.live_purchases(), 2)

    def test_allow_duplicate_on_the_one_bill_saves_it(self):
        self.post(self.bill())
        again = self.post(self.bill(allow_duplicate=True))
        self.assertEqual(again["saved"], 1)
        self.assertEqual(self.live_purchases(), 2)


class TheScreenOffersTheThreeAnswers(unittest.TestCase):
    """The dialogs are TypeScript, so pin their three answers from here.

    A refused save and a re-imported bill each have three real answers, and collapsing them into
    two prompts in a row is how the wrong one gets clicked.
    """

    def source(self, *parts):
        with open(os.path.join(ROOT, *parts), encoding="utf-8") as fh:
            return fh.read()

    def test_a_refused_save_offers_open_save_anyway_and_back(self):
        page = self.source("desktop", "src", "pages", "PurchasePage.tsx")
        self.assertIn("duplicate_bill", page)
        for label in ("Junya bill madhe ughad", "Tari save kar", "Radd"):
            self.assertIn(label, page)
        # "Open it" carries the rows on screen over through the engine's merge.
        self.assertIn("mergePurchaseLines(", page)
        self.assertIn("allow_duplicate: true", page)

    def test_the_import_offers_merge_a_new_bill_and_back(self):
        page = self.source("desktop", "src", "pages", "PurchasePage.tsx")
        self.assertIn("bill_already_saved", page)
        self.assertIn("merge_into_purchase_id", page)
        self.assertIn("Naveen bill banav", page)

    def test_the_review_screen_says_it_in_the_shops_words(self):
        dialog = self.source("desktop", "src", "pages", "PurchaseImportReviewDialog.tsx")
        self.assertIn("already saved aahe", dialog)
        self.assertIn("navin aushadhe jodu ka", dialog)

    def test_a_third_answer_is_something_the_dialog_can_show(self):
        self.assertIn("altLabel", self.source("desktop", "src", "pages", "SalesDialogs.tsx"))


if __name__ == "__main__":
    unittest.main()
