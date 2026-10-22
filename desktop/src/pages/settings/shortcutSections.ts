/**
 * Settings -> Shortcuts.
 *
 * Written from the keys the code binds -- keyboard.ts, hooks/usePageHotkeys.ts
 * and each page's own keydown handler -- not from the classic Tk list, which
 * this page used to copy (it advertised an Alt "focus first input", F2 on Home,
 * F10 in Settings, Delete on Inventory and a table action menu, none of which
 * this app binds). tests/test_sales_return_popup_and_shortcuts.py reads the
 * handlers and fails when a bound key is missing here, or when a screen lists
 * a key it does not bind.
 *
 * A key cell lists alternatives separated by ' / '. An empty key cell is a note.
 *
 * Sales Return is Alt+R. On Sales every key from F2 to F12 is taken, as are
 * Insert, End, Ctrl+P and Ctrl+Shift+C/N/W/M; WebView2 keeps Ctrl+R, Ctrl+F,
 * F3, F5 and F12 for itself unless a page takes them. Nothing in the app reads
 * Alt with R, and AltGr arrives as Ctrl+Alt, which the key refuses.
 *
 * Return expired is Alt+X on Inventory. Inventory binds Ctrl+E/F/Enter and
 * Ctrl+Shift+C, App binds Ctrl+P and the bare digits; nothing reads X with or
 * without Alt, Settings reads Alt only with a digit, and Alt+X is no WebView2 key.
 */
export type ShortcutSection = { title: string; rows: [string, string][] }

export const SHORTCUT_SECTIONS: ShortcutSection[] = [
  {
    title: 'Global Navigation',
    rows: [
      ['` / 0', 'Home'],
      ['1', 'Sales (Billing)'],
      ['2', 'Purchase'],
      ['3', 'Inventory'],
      ['4', 'Sales History'],
      ['5', 'Purchase History'],
      ['6', 'Returns'],
      ['7', 'Payments'],
      ['8', 'Settings'],
      ['', 'Only while the cursor is not in a field and no popup is open — press Escape first to leave a field'],
    ],
  },
  {
    title: 'Every Screen',
    rows: [
      ['Enter', 'Next field · presses the button the cursor is on'],
      ['← / → / ↑ / ↓', 'Move between fields (← → leave a text box only at its start or end)'],
      ['Shift+↑ / Shift+↓', 'On a drop-down list: previous / next field (plain ↑ ↓ change the choice)'],
      ['Escape', 'Leave the field so number keys work again'],
      ['Ctrl+P', 'Every page except Sales and Sales History: silent print of the last saved sale (Print Sales 2)'],
    ],
  },
  {
    title: 'Popups',
    rows: [
      ['Enter', 'OK / Yes on a message or question'],
      ['Escape', 'Close a message, list or menu (No on a question)'],
    ],
  },
  {
    title: 'Home',
    rows: [
      ['B', 'New Bill (Sales)'],
      ['P', 'New Purchase'],
      ['I', 'Inventory'],
      ['E / Ctrl+E', 'Export'],
      ['G', 'GST Reports (Saransh, GSTR-1, GSTR-3B, Tally Prime, CA zip)'],
    ],
  },
  {
    title: 'GST Reports',
    rows: [
      ['Escape', 'Close the GST Reports window'],
      ['', 'Home → G (or the GST Reports button). Pick Mahina / Timahi / Tarikh, then a tab; Excel saves every table at once'],
    ],
  },
  {
    title: 'Sales / Billing',
    rows: [
      ['F5', 'Save Sales'],
      ['F7', 'Print Sales 1 — saves the bill, then prints'],
      ['F8', 'Print Sales 2 — saves the bill, then prints'],
      ['Ctrl+P', 'Print Sales 1 (same as F7)'],
      ['F9', 'Silent reprint of the last saved sale'],
      ['Shift+F5 / Ctrl+Shift+C', 'Clear form'],
      ['F6', 'Overall discount %'],
      ['F2', 'Medicine items list'],
      ['F10', 'Recent sales'],
      ['F11', 'Load last sale'],
      ['F12', 'Tabs & Tools'],
      ['Insert / Ctrl+Shift+M', 'Add a medicine without stock (quick dialog)'],
      ['End', 'From a field: jump to Cash Paid'],
      ['Alt+R', 'Sales Return popup — find the bill, pick medicines and quantity, save, without leaving Sales'],
      ['C / D', 'On the Payment mode field: C = Cash, D = Due'],
      ['Enter on Disc', 'Add the medicine to the bill'],
      ['Enter on Online', 'When not Due: run the action set in Pharmacy Profile (save or print)'],
      ['Enter on Rounding', 'When Due: run the action set in Pharmacy Profile · otherwise fill Cash with the amount owed'],
      ['Enter on list', 'Edit the line (qty / discount)'],
      ['Delete / Backspace', 'On the items list: remove the line'],
      ['↑ / ↓', 'On the items list: move between lines'],
      ['Escape', 'Cancel a line edit · from the items list back to medicine search'],
      ['', 'The key typed in Pharmacy Profile → Sales Print Buttons changes only the label on the button; the keyboard answers F7 and F8'],
    ],
  },
  {
    title: 'Sales — Multi-tab',
    rows: [
      ['F3 / Ctrl+Shift+N', 'New sale tab'],
      ['F4 / Ctrl+Shift+W', 'Close current sale tab'],
      ['Ctrl+PgUp / Ctrl+[ / Ctrl+Alt+←', 'Previous sale tab'],
      ['Ctrl+PgDn / Ctrl+] / Ctrl+Alt+→', 'Next sale tab'],
    ],
  },
  {
    title: 'Sales Return popup (Alt+R on Sales)',
    rows: [
      ['Alt+R', 'Open it from Sales · inside the popup: back to the bill search'],
      ['Enter', 'In a search box: search now — loads the bill when only one matches'],
      ['↓ / ↑', 'From the search box into the bills found · through the bills and the bill lines'],
      ['Enter on a bill', 'Load it'],
      ['Enter on a bill line', 'Pick the medicine — Return Qty is filled with what is still returnable'],
      ['Enter in Return Qty', 'Add to the return'],
      ['Delete / Backspace', 'On the return list: remove the line'],
      ['F5', 'Save Sales Return — once; a second press while saving is ignored'],
      ['F6', 'Clear the popup'],
      ['Escape', 'Close (asks first when lines are not saved) · in Return Qty: back to the bill lines'],
      ['', 'While the popup is open the Sales keys stand down, so F5 saves the return and never the bill behind it'],
    ],
  },
  {
    title: 'Purchase',
    rows: [
      ['F5', 'Save Purchase'],
      ['F9 / Ctrl+Shift+C', 'Clear form'],
      ['Shift+F2', 'Import Purchase bill'],
      ['F2', 'Purchase items list'],
      ['F6', 'Overall discount %'],
      ['F7', 'GST slab table'],
      ['F8', 'Recalculate totals'],
      ['F10', 'Recent purchases'],
      ['F11', 'Load last purchase'],
      ['F12', 'Tabs & Tools'],
      ['End', 'From a field: jump to Amount Paid'],
      ['Enter on list', 'Edit the line'],
      ['Delete / Backspace', 'On the items list: remove the line'],
      ['↑ / ↓', 'On the items list: move between lines'],
      ['Escape', 'Cancel a line edit'],
    ],
  },
  {
    title: 'Purchase — Multi-tab',
    rows: [
      ['F3 / Ctrl+Shift+N', 'New purchase tab'],
      ['F4 / Ctrl+Shift+W', 'Close current purchase tab'],
      ['Ctrl+PgUp / Ctrl+[ / Ctrl+Alt+←', 'Previous purchase tab'],
      ['Ctrl+PgDn / Ctrl+] / Ctrl+Alt+→', 'Next purchase tab'],
    ],
  },
  {
    title: 'Inventory',
    rows: [
      ['Ctrl+F', 'Focus the search box'],
      ['Ctrl+Enter', 'Apply filters'],
      ['Ctrl+Shift+C', 'Clear the search and reload'],
      ['Ctrl+E', 'Export'],
      ['Alt+X', 'Return expired popup — find expired batches by supplier, medicine or batch, pick, set the quantity, save'],
    ],
  },
  {
    title: 'Return expired popup (Alt+X on Inventory)',
    rows: [
      ['Alt+X', 'Open it from Inventory · inside the popup: back to the search box'],
      ['Enter', 'In the search box: the only match goes straight to its quantity · otherwise into the list'],
      ['↓ / ↑', 'From the search box into the batches found · through the batches'],
      ['Enter on a batch', 'Pick it — Return Qty is filled with the whole batch (or with what is already on the return)'],
      ['Enter in Return Qty', 'Add to the return · on a batch already there: change its quantity'],
      ['Delete / Backspace', 'On the return list: remove the line'],
      ['F5', 'Save — one return per purchase bill, grouped by supplier; once, a second press while saving is ignored'],
      ['F6', 'Clear the popup'],
      ['Escape', 'Close (asks first when lines are not saved) · in Return Qty: back to the list'],
      ['', 'After saving the popup stays open with PDF and Edit for each return · while it is open the Inventory keys stand down'],
    ],
  },
  {
    title: 'Sales History',
    rows: [
      ['Ctrl+F', 'Focus the filters'],
      ['Ctrl+Enter', 'Apply filters'],
      ['Enter in a search box', 'Apply filters'],
      ['Ctrl+Shift+C', 'Clear filters and reload'],
      ['Ctrl+E', 'Export'],
      ['F7', 'Print Sales 1 — selected bill'],
      ['F8', 'Print Sales 2 — selected bill'],
      ['F9 / Ctrl+P', 'Silent print of the selected bill (Print Sales 2)'],
      ['Delete', 'Delete the selected bill'],
      ['', 'F7, F8, F9 and Delete work while the cursor is not in a box — click a row first'],
    ],
  },
  {
    title: 'Purchase History',
    rows: [
      ['Ctrl+F', 'Focus the filters'],
      ['Ctrl+Enter', 'Apply filters'],
      ['Enter in a search box', 'Apply filters'],
      ['Ctrl+Shift+C', 'Clear filters and reload'],
      ['Ctrl+E', 'Export'],
      ['Delete', 'Delete the selected purchase (cursor not in a box)'],
    ],
  },
  {
    title: 'Returns',
    rows: [
      ['S / P / W', 'Sales Return / Purchase Return / Write-off tab (cursor not in a field)'],
      ['F5 / Ctrl+G', 'Save the return on the open tab'],
      ['F6', 'Clear the open tab — on Purchase this also leaves Edit Return'],
      ['F2', 'Sales / Purchase: bill lines (bill search while no bill is loaded) · Bulk: items'],
      ['F3', 'Saved returns list (Sales, Purchase, Write-off) · Bulk: write-off lines'],
      ['Enter in Return Qty', 'Add to the return'],
      ['Ctrl+[ / Ctrl+]', 'Bulk Purchase Return: previous / next bill'],
      ['', 'Edit Return (Purchase tab, button) loads a saved return; F5 then saves the correction on the same bill'],
    ],
  },
  {
    title: 'Payments & Ledger',
    rows: [
      ['S / C', 'Supplier / Customer (cursor not in a field)'],
      ['F5 / Ctrl+G', 'Payment: save the payment'],
      ['F2', 'Ledger: the ledger table'],
      ['Ctrl+Enter', 'Ledger: load'],
      ['Enter in a filter box', 'Ledger: load'],
    ],
  },
  {
    title: 'Settings',
    rows: [
      ['Ctrl+1', 'Pharmacy Profile'],
      ['Ctrl+2', 'Contacts'],
      ['Ctrl+3', 'Shelf Management'],
      ['Ctrl+4', 'Appearance'],
      ['Ctrl+5', 'Layout & Lists'],
      ['Ctrl+6', 'Import'],
      ['Ctrl+7', 'Alert & Monitoring'],
      ['Ctrl+8', 'Data & System'],
      ['Ctrl+9', 'Payment'],
      ['Ctrl+0', 'Ledger'],
      ['Ctrl+Tab / Ctrl+Shift+Tab', 'Next / previous settings tab'],
      ['F4', 'Focus the section list'],
      ['↑ / ↓ / Enter', 'In the section list: move · open the section'],
      ['Alt+1…9', 'Jump to section 1–9 of the open tab'],
    ],
  },
  {
    title: 'Settings — Contacts',
    rows: [
      ['Ctrl+F', 'Focus the search'],
      ['F2', 'Contacts table'],
      ['F5 / Ctrl+G', 'Save'],
      ['F6', 'Clear the form'],
    ],
  },
  {
    title: 'Settings — Alert & Monitoring',
    rows: [
      ['F5 / Ctrl+G', 'Refresh every alert section'],
    ],
  },
  {
    title: 'Settings — Reorder',
    rows: [
      ['Ctrl+Shift+N', 'New supplier tab'],
      ['Ctrl+Shift+W', 'Close current supplier tab'],
      ['Ctrl+[ / Ctrl+]', 'Previous / next supplier tab'],
    ],
  },
]
