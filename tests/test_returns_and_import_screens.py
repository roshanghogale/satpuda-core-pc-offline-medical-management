"""Screen-level promises made on 2026-09-11, pinned by reading the sources.

* The bill-photo import never names the AI model or provider, and tells the
  operator to keep the internet on and to check MRP / rate / quantity /
  schedule before saving the purchase.
* Clear starts a genuinely new bill: the title never carries the number of the
  bill that was being edited or autosaved.
* Sales has a Sales Return button, Inventory has Return expired (search by
  supplier, medicine or batch), and Returns can edit a saved purchase return.
"""
import io
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def src(*parts):
    with io.open(os.path.join(ROOT, *parts), encoding="utf-8") as fh:
        return fh.read()


def shown_text(ts):
    """TSX with comments removed -- what can reach the screen, not what a
    developer wrote about it."""
    ts = re.sub(r"/\*.*?\*/", "", ts, flags=re.S)
    return re.sub(r"(?m)^\s*//.*$", "", ts)


def between(text, start, end):
    i = text.index(start)
    return text[i:text.index(end, i)]


class TheImportPopupNamesNoAiModel(unittest.TestCase):
    def test_no_user_facing_string_names_the_provider_or_model(self):
        for f in ("gemini_bill_parser.py", "gemini_bill_config.py", "purchase_importer.py"):
            for n, ln in enumerate(src("core", f).splitlines(), 1):
                s = ln.strip()
                if '"""' in s or s.startswith("#"):
                    continue
                if re.fullmatch(r'"gemini-[a-z-]+",', s):
                    continue  # model ids in a list -- never shown to anyone
                shown = s.startswith('"') or "_report(" in s or 'Error("' in s
                if shown:
                    self.assertNotRegex(s, r"Gemini|aistudio", f"{f}:{n}: {s}")

    def test_the_reading_popup_carries_the_owners_message(self):
        t = shown_text(src("desktop", "src", "components", "BillScanProgressOverlay.tsx"))
        self.assertIn("keep the internet on", t)
        self.assertIn("check MRP, rate, quantity and schedule", t)
        self.assertNotIn("Gemini", t)

    def test_the_message_after_import_asks_to_check_before_saving(self):
        # The reading popup has closed by the time this shows: it is the one
        # thing on screen when the operator is about to save.
        py = src("core", "desktop_purchase_service.py")
        self.assertIn("Check MRP, rate, quantity and schedule on every line", py)
        self.assertNotIn("Review batches, rates and expiry, then save the purchase.", py)

    def test_the_review_dialog_shows_a_source_not_a_model(self):
        t = src("desktop", "src", "pages", "PurchaseImportReviewDialog.tsx")
        self.assertNotIn("{preview.parser ||", t)
        self.assertIn("importSourceLabel(preview.parser", t)
        self.assertRegex(t, r"Check MRP, rate, quantity and schedule")

    def test_import_settings_do_not_name_the_model(self):
        t = shown_text(src("desktop", "src", "pages", "settings", "ImportDataPanels.tsx"))
        self.assertNotRegex(t, r'title="[^"]*Gemini|label="[^"]*Gemini|>[^<{]*Gemini')


class ClearStartsANewBill(unittest.TestCase):
    def test_sales_clear_never_keeps_a_bill_number_title(self):
        body = between(src("desktop", "src", "pages", "SalesPage.tsx"),
                       "const clearForm = async", "const applyLoadedSale")
        self.assertNotIn("blank.title = cur?.title || blank.title", body)
        self.assertIn("!cur?.editingSaleId", body)
        self.assertIn("!cur?.autosaveSaleId", body)
        self.assertIn("reloadMeta()", body, "Clear must fetch the real next number")

    def test_purchase_clear_never_keeps_an_edited_title(self):
        body = between(src("desktop", "src", "pages", "PurchasePage.tsx"),
                       "const clearForm = async", "const applyImportedForm")
        self.assertNotIn("startsWith('Purchase')", body)
        self.assertIn("!cur?.editingPurchaseId", body)


class ReturnsAreReachable(unittest.TestCase):
    def test_sales_has_a_sales_return_button(self):
        t = src("desktop", "src", "pages", "SalesPage.tsx")
        self.assertIn('label="Sales Return"', t)
        # A popup on Sales now (tests/test_sales_return_popup_and_shortcuts.py),
        # not a trip to Returns.
        self.assertIn("<SalesReturnDialog", t)
        self.assertNotIn("returnsSaleId", t)
        self.assertIn("onNavigate={navigate}", between(src("desktop", "src", "App.tsx"), "<SalesPage", "/>"))

    def test_inventory_opens_return_expired(self):
        t = src("desktop", "src", "pages", "InventoryPage.tsx")
        self.assertIn('label="Return expired"', t)
        self.assertIn("<ExpiredReturnDialog", t)

    def test_the_expired_popup_searches_supplier_medicine_and_batch(self):
        t = src("desktop", "src", "pages", "ExpiredReturnDialog.tsx")
        for field in ("r.supplier_name.toLowerCase().includes(needle)",
                      "r.name.toLowerCase().includes(needle)",
                      "r.batch.toLowerCase().includes(needle)"):
            self.assertIn(field, t)
        self.assertIn("saveBulkPurchaseReturn", t)
        self.assertIn("savePurchaseReturnPdf", t)
        self.assertIn("onEditReturn", t)

    def test_returns_can_edit_a_saved_purchase_return(self):
        t = src("desktop", "src", "pages", "ReturnsPage.tsx")
        self.assertIn('label="Edit Return"', t)
        self.assertIn("replacePurchaseReturn({ ...body, return_id: editReturn.id })", t)
        self.assertNotIn("Saved returns cannot be changed", t)


if __name__ == "__main__":
    unittest.main()
