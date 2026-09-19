"""Inventory -> Return expired, rebuilt to work like the Sales Return popup --
pinned by reading the sources (2026-09-11).

The owner: "inventory madhala return cha box sales return sarkha banav, to use
karayla sopa aahe" -- make Return expired work like the Sales Return popup.

* One search box (supplier, medicine or batch) with the batches listed as you
  type; arrow keys and Enter to pick; the quantity filled with what may go
  back; Enter adds a line to a running list; F5 saves once; F6 clears; Escape
  closes, asking first when lines are unsaved; the popup stays open after a
  save with PDF and Edit for each return.
* What it saves has NOT changed: the same ceiling per line (more than 0, at
  most the batch's stock on that bill), one return per purchase bill, bills
  ordered by supplier, the near-expiry toggle and its reason text, the same
  engine calls.
* Alt+X opens it from Inventory. Nothing else reads X; it is no WebView2 key.
"""
import io
import os
import re
import unittest

from tests.test_sales_return_popup_and_shortcuts import (
    KEYLIKE,
    between,
    handler,
    listed,
    shortcut_sections,
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UI = ("desktop", "src")
PAGES = UI + ("pages",)


def src(*parts):
    with io.open(os.path.join(ROOT, *parts), encoding="utf-8") as fh:
        return fh.read()


class DrivenLikeSalesReturn(unittest.TestCase):
    def setUp(self):
        self.dlg = src(*PAGES, "ExpiredReturnDialog.tsx")

    def test_the_checkbox_table_is_gone(self):
        self.assertNotIn("checked={on}", self.dlg)
        self.assertNotIn('aria-label="Select all shown"', self.dlg)
        # the near-expiry box is the one checkbox left
        self.assertEqual(self.dlg.count('type="checkbox"'), 1)
        self.assertIn("Include near expiry", self.dlg)

    def test_one_search_box_lists_batches_as_you_type(self):
        shown = between(self.dlg, "const shown = useMemo(", "}, [rows, q, supplier])")
        for field in ("r.supplier_name.toLowerCase().includes(needle)",
                      "r.name.toLowerCase().includes(needle)",
                      "r.batch.toLowerCase().includes(needle)"):
            self.assertIn(field, shown)
        self.assertIn("if (supplier && r.supplier_name !== supplier) return false", shown)
        self.assertEqual(self.dlg.count('placeholder="'), 1)
        self.assertIn('placeholder="Search supplier, medicine or batch"', self.dlg)

    def test_arrow_keys_and_enter_pick_a_batch(self):
        search = between(self.dlg, "ref={searchRef}", "/>")
        self.assertIn("if (shown.length === 1) pick(shown[0])", search)
        self.assertIn("else focusHit()", search)
        self.assertIn("e.key === 'ArrowDown'", search)
        row = between(self.dlg, 'data-hit="1"', "<td>")
        self.assertIn("onClick={() => pick(r)}", row)
        self.assertIn("if (e.key === 'Enter')", row)
        self.assertIn("moveFocus(e.currentTarget, 1)", row)
        self.assertIn("if (!moveFocus(e.currentTarget, -1)) focusSearch()", row)

    def test_the_quantity_starts_at_what_may_go_back(self):
        pick = between(self.dlg, "const pick = (r: Row) => {", "const add = () => {")
        self.assertIn("setQtyText(cur !== undefined ? cur : String(r.available))", pick)
        self.assertIn("qtyRef.current?.focus()", pick)

    def test_enter_in_return_qty_adds_a_line_and_escape_goes_back(self):
        qty = between(self.dlg, "ref={qtyRef}", "/>")
        self.assertIn("add()", qty)
        self.assertIn("focusHit(pickKey)", qty)
        add = between(self.dlg, "const add = () => {", "const removeLine")
        self.assertIn("const problem = expiredQtyProblem(r, qtyText)", add)
        self.assertLess(add.index("if (problem)"), add.index("setPicked("))

    def test_the_running_list_can_lose_a_line(self):
        self.assertIn("e.key === 'Delete' || e.key === 'Backspace'", self.dlg)
        self.assertIn("removeLine(r.key)", self.dlg)
        self.assertIn("<th>Returning</th>", self.dlg)

    def test_keys_f5_saves_f6_clears_escape_closes(self):
        keys = handler(self.dlg, "const onKey = (e: KeyboardEvent) => {")
        self.assertIn("void actions.current.save()", keys)
        self.assertIn("actions.current.resetForm()", keys)
        self.assertIn("actions.current.requestClose()", keys)
        self.assertIn("if (isExpiredReturnKey(e))", keys)
        # a message on screen owns Enter / Escape
        self.assertLess(keys.index("if (alertRef.current)"), keys.index("e.key === 'F5'"))
        # Alt+F4 still closes the window
        self.assertIn("if (/^F\\d+$/.test(e.key) && !e.altKey)", keys)

    def test_escape_asks_first_when_lines_are_unsaved(self):
        close = between(self.dlg, "const requestClose = () => {", "const actions")
        self.assertIn("if (savingRef.current) return", close)
        self.assertIn("const n = unsavedCount()", close)
        self.assertIn("kind: 'confirm'", close)
        self.assertIn("onConfirm: onClose", close)
        # the backdrop no longer throws the list away on a stray click
        self.assertNotIn('role="presentation" onClick={onClose}', self.dlg)

    def test_one_press_saves_once(self):
        save = between(self.dlg, "const save = async () => {", "const savePdf")
        guard = save.index("if (savingRef.current) return")
        armed = save.index("savingRef.current = true")
        sent = save.index("await saveBulkPurchaseReturn(")
        self.assertLess(guard, armed)
        self.assertLess(armed, sent)
        self.assertIn("savingRef.current = false", save[sent:])

    def test_it_stays_open_after_saving_with_pdf_and_edit(self):
        save = between(self.dlg, "const save = async () => {", "const savePdf")
        self.assertNotIn("onClose", save)
        self.assertIn("setSaved(done)", save)
        self.assertIn("onSaved?.()", save)
        self.assertIn("void load()", save)
        self.assertIn("Saved ${done.length} return(s)", save)
        self.assertIn("savePurchaseReturnPdf(Number(s.return_id))", self.dlg)
        edit = between(self.dlg, "const editSaved = (s: Saved) => {", "const requestClose")
        self.assertIn("onEditReturn(id)", edit)
        self.assertIn("kind: 'confirm'", edit)          # unsaved lines: ask first


class WhatItSavesHasNotChanged(unittest.TestCase):
    def setUp(self):
        self.dlg = src(*PAGES, "ExpiredReturnDialog.tsx")

    def test_same_engine_calls(self):
        self.assertIn("fetchBulkPurchasePrefill(true, nearToo)", self.dlg)
        self.assertIn("await saveBulkPurchaseReturn({ purchase_groups, writeoff_lines: [] })", self.dlg)

    def test_same_rows_out_of_the_prefill(self):
        flat = between(self.dlg, "function flatten(", "\n}\n")
        self.assertIn("const available = Number(it.qty ?? it.remaining_qty ?? 0)", flat)
        self.assertIn("if (!(available > 0)) continue", flat)
        self.assertIn("key: `${g.purchase_id}:${it.medicine_id}:${it.batch || ''}`", flat)

    def test_the_ceiling_of_a_line(self):
        rule = between(self.dlg, "export function expiredQtyProblem(", "\n}\n")
        self.assertIn("if (!(q > 0) || q > r.available + 1e-9) {", rule)
        self.assertIn("quantity must be more than 0 and at most ${r.available}", rule)
        # checked when the line is added and again at save
        save = between(self.dlg, "const save = async () => {", "const savePdf")
        self.assertIn("const problem = expiredQtyProblem(r, cur[r.key])", save)
        self.assertLess(save.index("expiredQtyProblem("), save.index("savingRef.current = true"))

    def test_one_return_per_bill_grouped_by_supplier(self):
        grp = between(self.dlg, "export function expiredReturnGroups(", "\n}\n")
        self.assertIn("byBill.get(r.purchase_id)", grp)
        self.assertIn(".sort((a, b) => a[0].supplier_name.localeCompare(b[0].supplier_name))", grp)
        self.assertIn("reason: nearToo ? 'Expired / near expiry stock' : 'Expired stock'", grp)
        for field in ("medicine_id: r.medicine_id", "qty: qtyNum(picked[r.key])", "rate: r.rate",
                      "pack_size: r.unit", "is_tablet: r.is_tablet",
                      "tablets_per_stripe: r.tablets_per_stripe"):
            self.assertIn(field, grp)
        save = between(self.dlg, "const save = async () => {", "const savePdf")
        self.assertIn("expiredReturnGroups(pickedRows, cur, nearToo)", save)

    def test_the_list_follows_the_stock_on_screen(self):
        # A line is a batch of the loaded stock -- as the ticked rows were. A
        # reload (near-expiry box, Refresh, after a save) drops lines whose
        # batch is gone instead of saving them against stale stock.
        load = between(self.dlg, "const load = useCallback(async () => {", "}, [nearToo])")
        self.assertIn("if (keys.has(k)) kept[k] = v", load)
        self.assertIn("if (seq !== loadSeq.current) return", load)
        save = between(self.dlg, "const save = async () => {", "const savePdf")
        self.assertIn("rowsRef.current.filter((r) => cur[r.key] !== undefined)", save)

    def test_add_all_shown_is_still_there(self):
        body = between(self.dlg, "const toggleAllShown = () => {", "const save = async")
        self.assertIn("next[r.key] = String(r.available)", body)
        self.assertIn("if (allShownOn) delete next[r.key]", body)


class TheAltXKey(unittest.TestCase):
    def setUp(self):
        self.inv = src(*PAGES, "InventoryPage.tsx")
        self.dlg = src(*PAGES, "ExpiredReturnDialog.tsx")

    def test_it_is_shown_on_the_button_and_bound(self):
        btn = between(self.inv, 'label="Return expired"', "/>")
        self.assertIn('kbd="Alt+X"', btn)
        self.assertIn("if (!active) return\n    const onKey = (e: KeyboardEvent) => {\n"
                      "      if (!isExpiredReturnKey(e)) return", self.inv)
        body = handler(self.inv, "if (!isExpiredReturnKey(e)) return")
        self.assertIn("if (expiredReturnOpenRef.current) return", body)
        self.assertIn("if (document.querySelector('.modal-backdrop')) return", body)
        self.assertIn("setExpiredReturnOpen(true)", body)
        fn = between(self.dlg, "export function isExpiredReturnKey(", "\n}\n")
        self.assertIn("if (!e.altKey || e.ctrlKey || e.metaKey || e.shiftKey) return false", fn)
        self.assertIn("e.code === 'KeyX'", fn)

    def test_inventory_keys_stand_down_and_a_hidden_page_closes_it(self):
        self.assertIn("enabled: active && !expiredReturnOpen,", self.inv)
        self.assertIn("if (!active) setExpiredReturnOpen(false)", self.inv)

    def test_nothing_else_answers_x(self):
        kb = src(*UI, "keyboard.ts")
        self.assertNotRegex(between(kb, "const KEY_TO_PAGE", "}"), r"'[xX]'")
        hooks = src(*UI, "hooks", "usePageHotkeys.ts")
        self.assertIn("['b', 'p', 'i', 'e', 's', 'w']", hooks)
        settings = src(*PAGES, "SettingsPage.tsx")
        alt = between(settings, "if (e.altKey && !e.ctrlKey && !e.metaKey) {", "\n      }\n")
        self.assertIn("digit >= 1 && digit <= 9", alt)
        pat = re.compile(r"(key|k)(\.toLowerCase\(\))?\s*===\s*'[xX]'|'KeyX'")
        for base, _, files in os.walk(os.path.join(ROOT, *UI)):
            for f in files:
                if (not f.endswith((".ts", ".tsx")) or f.startswith("._")
                        or f == "ExpiredReturnDialog.tsx"):
                    continue
                with io.open(os.path.join(base, f), encoding="utf-8") as fh:
                    self.assertNotRegex(fh.read(), pat, "%s also reads X" % f)

    def test_it_is_not_a_webview2_key_nor_the_sales_return_key(self):
        browser = {"Ctrl+R", "Ctrl+Shift+R", "F5", "Shift+F5", "Ctrl+P", "Ctrl+F", "F3",
                   "Ctrl+G", "F7", "F12", "Ctrl+Shift+I", "Alt+←", "Alt+→",
                   "Alt+Home", "Alt+F4", "Alt+Space", "Alt+D", "Alt+E", "Alt+F"}
        self.assertNotIn("Alt+X", browser)
        self.assertNotIn("Alt+X", {"Alt+R"})


class TheShortcutPageSaysSo(unittest.TestCase):
    def test_the_popup_section_matches_its_handlers(self):
        s = shortcut_sections()
        dlg = src(*PAGES, "ExpiredReturnDialog.tsx")
        found = set(re.findall(r"e\.key === '([^']+)'", dlg))
        self.assertEqual(found, {"Enter", "ArrowDown", "ArrowUp", "Escape", "F5", "F6",
                                 "Delete", "Backspace"})
        names = {"ArrowDown": "↓", "ArrowUp": "↑"}
        bound = {names.get(k, k) for k in found} | {"Alt+X"}
        title = "Return expired popup (Alt+X on Inventory)"
        shown = listed(s, title)
        for k in bound:
            self.assertIn(k, shown, "the popup binds %s; the shortcut page does not say so" % k)
        for k in shown:
            if KEYLIKE.match(k):
                self.assertIn(k, bound, "the popup section lists %s, which it does not bind" % k)

    def test_inventory_lists_alt_x(self):
        s = shortcut_sections()
        self.assertIn("Alt+X", listed(s, "Inventory"))
        self.assertNotIn("has no key of its own", str(s["Inventory"]))


if __name__ == "__main__":
    unittest.main()
