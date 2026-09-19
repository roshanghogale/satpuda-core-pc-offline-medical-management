"""Sales Return as a popup on the Sales page, and a shortcut page that says
what the code binds -- pinned by reading the sources (2026-09-11).

The owner: a sales return should open as a popup right on the Sales screen --
find the bill by customer name, bill number or medicine, pick the bill, pick
the medicines and quantities, save -- without going to another screen. Give
it a key, and bring the whole shortcut page up to date.

* The popup uses the same engine calls and the SAME money rules as
  Returns -> Sales: both import them from pages/salesReturnLogic.ts.
* Alt+R opens it. Nothing else reads Alt+R, and it is no WebView2 browser key.
* Settings -> Shortcuts is written from the key handlers. These tests read the
  handlers and fail when a bound key is missing from the page, or when the
  page lists, for a screen, a key that screen does not bind.
"""
import io
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UI = ("desktop", "src")
PAGES = UI + ("pages",)


def src(*parts):
    with io.open(os.path.join(ROOT, *parts), encoding="utf-8") as fh:
        return fh.read()


def between(text, start, end):
    i = text.index(start)
    return text[i:text.index(end, i)]


def handler(text, anchor):
    """One window keydown handler: from the anchor to where it is attached."""
    i = text.index(anchor)
    return text[i:text.index("window.addEventListener('keydown', onKey)", i)]


def shortcut_sections():
    t = src(*PAGES, "settings", "shortcutSections.ts")
    body = t[t.index("export const SHORTCUT_SECTIONS"):]
    out = {}
    for m in re.finditer(r"title: '([^']*)',\s*rows: \[(.*?)\n    \],", body, re.S):
        rows = re.findall(r"\[\s*'([^']*)',\s*'([^']*)',?\s*\]", m.group(2))
        out[m.group(1)] = rows
    return out


def tokens(cell):
    return [k.strip() for k in re.split(r"\s+/\s+", cell) if k.strip()]


def listed(sections, *titles):
    keys = set()
    for title in titles:
        for key, _ in sections[title]:
            keys.update(tokens(key))
    return keys


KEYLIKE = re.compile(
    r"^(F\d+|Ctrl\+.+|Shift\+.+|Alt\+.+|Insert|End|Escape|Delete|Backspace|"
    r"[A-Z0-9`]|[←→↑↓])$"
)

# `key === '<x>'` inside the Sales / Purchase handlers -> what the page shows.
BILLING_KEYS = {
    "Escape": "Escape", "F2": "F2", "F3": "F3", "F4": "F4", "F5": "F5",
    "F6": "F6", "F7": "F7", "F8": "F8", "F9": "F9", "F10": "F10",
    "F11": "F11", "F12": "F12", "Insert": "Insert", "End": "End",
    "p": "Ctrl+P", "P": "Ctrl+P", "c": "Ctrl+Shift+C", "C": "Ctrl+Shift+C",
    "n": "Ctrl+Shift+N", "N": "Ctrl+Shift+N", "w": "Ctrl+Shift+W",
    "W": "Ctrl+Shift+W", "m": "Ctrl+Shift+M", "M": "Ctrl+Shift+M",
    "PageUp": "Ctrl+PgUp", "PageDown": "Ctrl+PgDn", "[": "Ctrl+[",
    "]": "Ctrl+]", "ArrowLeft": "Ctrl+Alt+←", "ArrowRight": "Ctrl+Alt+→",
}

# usePageHotkeys callbacks -> keys (hooks/usePageHotkeys.ts).
HOOK_KEYS = {
    "onSave": ("F5", "Ctrl+G"), "onClear": ("F6",), "onF2": ("F2",),
    "onF3": ("F3",), "onExport": ("Ctrl+E",), "onPrintLast": ("Ctrl+P",),
    "onFocusFilter": ("Ctrl+F",), "onApplyFilter": ("Ctrl+Enter",),
    "onClearFilter": ("Ctrl+Shift+C",),
}


def hook_keys(block):
    names = set(re.findall(r"\b(on[A-Z]\w*|enabled)\s*:", block))
    unknown = names - set(HOOK_KEYS) - {"enabled", "onLetter"}
    assert not unknown, "usePageHotkeys grew %s: map it here and list it" % unknown
    keys = set()
    for n in names & set(HOOK_KEYS):
        keys.update(HOOK_KEYS[n])
    if "onLetter" in names:
        keys.update(k.upper() for k in re.findall(r"k === '([a-z])'", block))
    return keys


def hook_block(text, start=0):
    """One usePageHotkeys({...}) call: a single line, or up to its closing
    `  })` -- a callback body can itself end a line with `})`."""
    i = text.index("usePageHotkeys({", start)
    line = text[i:text.index("\n", i)]
    if line.rstrip().endswith("})"):
        return line
    return text[i:text.index("\n  })", i)]


def panel(text, name):
    i = text.index("export function %s(" % name)
    j = text.find("\nexport function ", i + 1)
    return text[i:j if j > 0 else len(text)]


class TheSalesReturnPopup(unittest.TestCase):
    def setUp(self):
        self.dlg = src(*PAGES, "SalesReturnDialog.tsx")
        self.sales = src(*PAGES, "SalesPage.tsx")

    def test_the_button_opens_the_popup_not_the_returns_page(self):
        body = between(self.sales, "const openSalesReturn = () => {", "\n  }\n")
        self.assertNotIn("onNavigate", body)
        self.assertNotIn("returnsSaleId", self.sales)
        self.assertIn("setReturnOpen(true)", body)
        self.assertIn("<SalesReturnDialog", self.sales)

    def test_an_edited_bill_opens_already_loaded(self):
        body = between(self.sales, "const openSalesReturn = () => {", "\n  }\n")
        self.assertIn("setReturnSaleId(tabRef.current?.editingSaleId || null)", body)
        self.assertIn("initialSaleId={returnSaleId}", self.sales)
        self.assertIn("if (initialSaleId) void doLoad(Number(initialSaleId))", self.dlg)

    def test_it_searches_by_bill_customer_or_medicine_on_the_same_engine_calls(self):
        self.assertIn("searchSalesReturnBills(parseBillSearch(query), medicine.trim())", self.dlg)
        self.assertIn("loadSalesReturnBill(saleId)", self.dlg)
        self.assertIn("saveSalesReturn(salesReturnBody(", self.dlg)
        self.assertIn('placeholder="Bill no or customer name"', self.dlg)
        self.assertIn('placeholder="or medicine"', self.dlg)

    def test_both_return_screens_share_one_set_of_money_rules(self):
        logic = src(*PAGES, "salesReturnLogic.ts")
        returns = src(*PAGES, "ReturnsPage.tsx")
        for name in ("discountAsPct", "salesRemainingQty", "salesRefundPreview",
                     "salesSettleHint", "checkSalesReturnAdd", "checkSalesReturnSave",
                     "salesReturnBody", "salesReturnSavedMessage"):
            self.assertIn("export function %s(" % name, logic)
            for text, f in ((self.dlg, "SalesReturnDialog"), (returns, "ReturnsPage")):
                imports = between(text, "import {\n  checkSalesReturnAdd", "from './salesReturnLogic'")
                self.assertIn(name, imports, "%s must use the shared %s" % (f, name))
                self.assertNotIn("function %s(" % name, text)
        # Returns no longer carries its own copy of the rules.
        self.assertNotIn("const used = salesReturnItems", returns)

    def test_the_discount_box_is_a_percentage_of_the_bill(self):
        logic = src(*PAGES, "salesReturnLogic.ts")
        self.assertIn("const gross = total + rs", logic)
        self.assertIn("setDisc(String(discountAsPct(loaded.discount, loaded.bill_total)))", self.dlg)
        self.assertIn("Discount %", self.dlg)
        # sent as the percentage, the way the engine reads it
        self.assertIn("discount: Number(discPct) || 0", logic)

    def test_quantity_never_exceeds_what_is_still_returnable(self):
        logic = src(*PAGES, "salesReturnLogic.ts")
        rem = between(logic, "export function salesRemainingQty(", "\n}\n")
        self.assertIn(".filter((r) => r.medicine_id === medicineId)", rem)
        self.assertIn("Math.max(0, baseRemaining - used)", rem)
        add = between(logic, "export function checkSalesReturnAdd(", "\n}\n")
        self.assertIn("if (qty > remaining)", add)
        self.assertIn("Cannot return more than", add)
        self.assertIn("is already in the return list", add)
        self.assertIn("checkSalesReturnAdd(b, linesRef.current, pickId", self.dlg)
        # the quantity box starts at what is left, never the sold quantity
        self.assertIn("setQtyText(String(salesRemainingQty(linesRef.current, id, it.remaining_qty)))",
                      self.dlg)

    def test_refund_or_credit_is_offered_as_on_returns(self):
        for value, label in (("ledger", "Keep as this customer&apos;s credit — no cash given"),
                             ("cash", "Give cash back now"),
                             ("online", "Send online / UPI now")):
            self.assertIn('<option value="%s">%s</option>' % (value, label), self.dlg)
        self.assertIn("salesSettleHint(refund, Number(bill?.previous_due || 0), settle)", self.dlg)

    def test_one_press_saves_once(self):
        save = between(self.dlg, "const save = async () => {", "const requestClose")
        guard = save.index("if (savingRef.current) return")
        armed = save.index("savingRef.current = true")
        sent = save.index("await saveSalesReturn(")
        self.assertLess(guard, armed)
        self.assertLess(armed, sent)
        self.assertIn("savingRef.current = false", save[sent:])
        # the Returns tab had the same hole on F5 -- closed there too
        returns = src(*PAGES, "ReturnsPage.tsx")
        rsave = between(returns, "const saveSales = async () => {", "const deleteSelectedSalesReturn")
        self.assertLess(rsave.index("if (salesSavingRef.current) return"),
                        rsave.index("salesSavingRef.current = true"))

    def test_after_save_it_shows_the_number_and_refund_and_stays_usable(self):
        save = between(self.dlg, "const save = async () => {", "const requestClose")
        self.assertIn("salesReturnSavedMessage(res, settle)", save)
        self.assertIn("resetForm()", save)
        self.assertNotIn("onClose()", save)
        logic = src(*PAGES, "salesReturnLogic.ts")
        self.assertIn("`Saved ${res.return_no} — refund ${money(res.refund_amount || 0)}`", logic)

    def test_the_sales_page_refreshes_that_customers_due_and_credit(self):
        body = between(self.sales, "const afterSalesReturn = async", "const clearForm = async")
        self.assertIn("dispatchPaymentsChanged('customer')", body)
        self.assertIn("lookupCustomerByName(name, true)", body)
        self.assertIn("prevDue: due, prevCredit: credit", body)
        self.assertIn("onSaved={(r) => void afterSalesReturn(r)}", self.sales)

    def test_the_sales_keys_stand_down_while_the_popup_is_open(self):
        body = handler(self.sales, "const key = e.key\n      const ctrl = e.ctrlKey")
        self.assertLess(body.index("if (returnOpenRef.current) return"),
                        body.index("if (key === 'F2')"))
        keys = handler(self.dlg, "const onKey = (e: KeyboardEvent) => {")
        self.assertIn("void actions.current.save()", keys)
        self.assertIn("actions.current.requestClose()", keys)

    def test_no_pdf_because_returns_offers_none_for_a_sales_return(self):
        api = src(*UI, "pagesApi.ts")
        self.assertNotRegex(api, r"[Ss]alesReturnPdf",
                            "Returns now has a sales-return PDF: offer it in the popup too")
        self.assertNotIn("Pdf", self.dlg)


class TheSalesReturnKey(unittest.TestCase):
    def test_alt_r_is_shown_and_bound(self):
        sales = src(*PAGES, "SalesPage.tsx")
        btn = between(sales, 'label="Sales Return"', "/>")
        self.assertIn('kbd="Alt+R"', btn)
        body = handler(sales, "const key = e.key\n      const ctrl = e.ctrlKey")
        self.assertIn("if (isSalesReturnKey(e))", body)
        dlg = src(*PAGES, "SalesReturnDialog.tsx")
        fn = between(dlg, "export function isSalesReturnKey(", "\n}\n")
        self.assertIn("if (!e.altKey || e.ctrlKey || e.metaKey || e.shiftKey) return false", fn)
        self.assertIn("e.code === 'KeyR'", fn)

    def test_nothing_else_answers_alt_r(self):
        kb = src(*UI, "keyboard.ts")
        self.assertNotRegex(between(kb, "const KEY_TO_PAGE", "}"), r"'[rR]'")
        self.assertIn("if (e.ctrlKey || e.altKey || e.metaKey) return", kb)
        hooks = src(*UI, "hooks", "usePageHotkeys.ts")
        self.assertIn("['b', 'p', 'i', 'e', 's', 'w']", hooks)
        # Settings reads Alt only with a digit.
        settings = src(*PAGES, "SettingsPage.tsx")
        alt = between(settings, "if (e.altKey && !e.ctrlKey && !e.metaKey) {", "\n      }\n")
        self.assertIn("const digit = Number(e.key)", alt)
        self.assertIn("digit >= 1 && digit <= 9", alt)
        pat = re.compile(r"(key|k)(\.toLowerCase\(\))?\s*===\s*'[rR]'|'KeyR'")
        for base, _, files in os.walk(os.path.join(ROOT, *UI)):
            for f in files:
                # "._x.tsx" are macOS resource forks on the external drive
                if (not f.endswith((".ts", ".tsx")) or f.startswith("._")
                        or f == "SalesReturnDialog.tsx"):
                    continue
                with io.open(os.path.join(base, f), encoding="utf-8") as fh:
                    self.assertNotRegex(fh.read(), pat, "%s also reads R" % f)

    def test_it_is_not_a_key_webview2_keeps(self):
        browser = {"Ctrl+R", "Ctrl+Shift+R", "F5", "Ctrl+P", "Ctrl+F", "F3",
                   "Ctrl+G", "F7", "F12", "Ctrl+Shift+I", "Alt+←", "Alt+→",
                   "Alt+Home", "Alt+F4", "Alt+Space"}
        self.assertNotIn("Alt+R", browser)


class TheShortcutPageMatchesTheCode(unittest.TestCase):
    def setUp(self):
        self.s = shortcut_sections()

    def assert_page(self, titles, bound, extra=()):
        """Every bound key is on the page; every key-like cell on the page is bound."""
        shown = listed(self.s, *titles)
        for k in bound:
            self.assertIn(k, shown, "%s binds %s; the shortcut page does not say so" % (titles[0], k))
        allowed = set(bound) | set(extra)
        for k in shown:
            if KEYLIKE.match(k):
                self.assertIn(k, allowed, "%s lists %s, which nothing there binds" % (titles[0], k))

    def test_the_panel_renders_this_list(self):
        panels = src(*PAGES, "settings", "InteractivePanels.tsx")
        self.assertIn("import { SHORTCUT_SECTIONS } from './shortcutSections'", panels)
        self.assertNotIn("const SHORTCUT_SECTIONS", panels)

    def test_page_navigation(self):
        kb = src(*UI, "keyboard.ts")
        keys = re.findall(r"^\s+'(.)': '\w+',$", between(kb, "const KEY_TO_PAGE", "}"), re.M)
        self.assertEqual(len(keys), 10)
        self.assert_page(["Global Navigation"], keys)

    def test_sales(self):
        body = handler(src(*PAGES, "SalesPage.tsx"), "const key = e.key\n      const ctrl = e.ctrlKey")
        found = set(re.findall(r"key === '([^']+)'", body))
        self.assertFalse(found - set(BILLING_KEYS), "new Sales key: list it and map it here")
        bound = {BILLING_KEYS[k] for k in found} | {"Shift+F5", "Alt+R"}
        self.assertIn("if (key === 'F5' && shift)", body)
        sales = src(*PAGES, "SalesPage.tsx")
        self.assertIn("if (k === 'd')", sales)          # Payment mode field
        self.assertIn("e.key === 'Delete' || e.key === 'Backspace'", sales)
        self.assert_page(["Sales / Billing", "Sales — Multi-tab"], bound,
                         extra={"C", "D", "Delete", "Backspace", "↑", "↓"})

    def test_purchase(self):
        text = src(*PAGES, "PurchasePage.tsx")
        body = handler(text, "const key = e.key")
        found = set(re.findall(r"key === '([^']+)'", body))
        self.assertFalse(found - set(BILLING_KEYS), "new Purchase key: list it and map it here")
        self.assertIn("openImportPicker()", between(body, "if (key === 'F2') {", "return\n      }"))
        bound = {BILLING_KEYS[k] for k in found} | {"Shift+F2"}
        self.assert_page(["Purchase", "Purchase — Multi-tab"], bound,
                         extra={"Delete", "Backspace", "↑", "↓"})

    def test_sales_return_popup(self):
        dlg = src(*PAGES, "SalesReturnDialog.tsx")
        found = set(re.findall(r"e\.key === '([^']+)'", dlg))
        names = {"ArrowDown": "↓", "ArrowUp": "↑"}
        bound = {names.get(k, k) for k in found} | {"Alt+R"}
        self.assertEqual(found, {"Enter", "ArrowDown", "ArrowUp", "Escape", "F5", "F6",
                                 "Delete", "Backspace"})
        self.assert_page(["Sales Return popup (Alt+R on Sales)"], bound - {"Enter"}, extra={"Enter"})

    def test_pages_on_the_shared_hotkey_hook(self):
        own = {"Home": set(), "Inventory": set(), "Returns": {"Ctrl+[", "Ctrl+]"}}
        # Inventory's own key outside the hook: Alt+X, the Return expired popup
        # (tests/test_expired_return_popup.py pins it).
        if "if (!isExpiredReturnKey(e)) return" in src(*PAGES, "InventoryPage.tsx"):
            own["Inventory"] = {"Alt+X"}
        for f, title in (("HomePage.tsx", "Home"), ("InventoryPage.tsx", "Inventory"),
                         ("ReturnsPage.tsx", "Returns")):
            self.assert_page([title], hook_keys(hook_block(src(*PAGES, f))), extra=own[title])

    def test_returns_bulk_tabs(self):
        body = handler(src(*PAGES, "ReturnsPage.tsx"), "if (tab !== 'bulk') return")
        self.assertIn("e.key === '['", body)
        self.assertIn("e.key === ']'", body)
        self.assertTrue({"Ctrl+[", "Ctrl+]"} <= listed(self.s, "Returns"))

    def test_the_history_pages(self):
        sh = src(*PAGES, "SalesHistoryPage.tsx")
        own = set(re.findall(r"e\.key === '([^']+)'", handler(sh, "if (e.key === 'F7') {")))
        self.assertEqual(own, {"F7", "F8", "F9", "Delete"})
        self.assert_page(["Sales History"], own | hook_keys(hook_block(sh)))
        ph = src(*PAGES, "PurchaseHistoryPage.tsx")
        self.assertIn("if (e.key !== 'Delete') return", ph)
        self.assert_page(["Purchase History"], {"Delete"} | hook_keys(hook_block(ph)))

    def test_every_screen_and_popups(self):
        app = src(*UI, "App.tsx")
        self.assertIn("if (page === 'sales' || page === 'sales_history') return", app)
        self.assertIn("e.key.toLowerCase() !== 'p'", app)
        self.assertTrue({"Enter", "Escape", "←", "→", "↑", "↓", "Shift+↑", "Shift+↓",
                         "Ctrl+P"} <= listed(self.s, "Every Screen"))
        self.assertTrue({"Enter", "Escape"} <= listed(self.s, "Popups"))

    def test_settings(self):
        cfg = src(*PAGES, "settings", "settingsConfig.ts")
        tabs = re.findall(r"id: '(\w+)',\s*label: '([^']+)',\s*ctrlDigit: (\d+),", cfg)
        self.assertEqual(len(tabs), 10)
        rows = dict(self.s["Settings"])
        for _, label, digit in tabs:
            self.assertEqual(rows.get("Ctrl+%s" % digit), label)
        settings = src(*PAGES, "SettingsPage.tsx")
        self.assertIn("e.key === 'F4'", settings)
        self.assertIn("e.key === 'Tab'", settings)
        shown = listed(self.s, "Settings")
        self.assertTrue({"F4", "Ctrl+Tab", "Ctrl+Shift+Tab", "Alt+1…9"} <= shown)
        panels = src(*PAGES, "settings", "InteractivePanels.tsx")
        for fn, title in (("ContactsPanel", "Settings — Contacts"),
                          ("AlertsPanel", "Settings — Alert & Monitoring")):
            self.assert_page([title], hook_keys(hook_block(panel(panels, fn))))
        pay = hook_keys(hook_block(panel(panels, "PaymentPanel")))
        led = hook_keys(hook_block(panel(panels, "LedgerPanel")))
        self.assertIn("setToggleId('supplier')", settings)
        self.assert_page(["Payments & Ledger"], pay | led | {"S", "C"})
        reorder = handler(src(*PAGES, "settings", "ReorderMultiTabEditor.tsx"), "const onKey")
        self.assertIn("e.shiftKey && e.key.toLowerCase() === 'n'", reorder)
        self.assert_page(["Settings — Reorder"], {"Ctrl+Shift+N", "Ctrl+Shift+W", "Ctrl+[", "Ctrl+]"})

    def test_the_rows_no_handler_binds_are_gone(self):
        all_rows = [(t, k, d) for t, rows in self.s.items() for k, d in rows]
        cells = {k for _, k, _ in all_rows}
        self.assertNotIn("Alt", cells)                  # "focus first input": nothing binds it
        self.assertNotIn("Alt+1…4", cells)
        self.assertNotIn("F2", listed(self.s, "Home"))
        self.assertNotIn("F10", listed(self.s, "Settings"))
        self.assertNotIn("Delete", listed(self.s, "Inventory"))
        for _, k, d in all_rows:
            self.assertNotIn("Keyboard action menu", d)
            self.assertNotIn("Reprint last sale", d)    # Ctrl+P on Sales prints THIS bill
        f3 = dict(self.s["Returns"])["F3"]
        self.assertNotIn("Return items list", f3)       # F3 is the saved-returns list


if __name__ == "__main__":
    unittest.main()
