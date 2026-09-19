# Verified common questions (Satpuda AI)

Answer using exact labels below. Never invent buttons, dialogs, or save timing.

## Sales (Billing)
Q: How to add customer? A: Type in Customer Name field on same screen (no Add button). Saved on Save Sales (F5) only.
Q: How to add medicine? A: Pick Medicine, enter Quantity in Medicine Selection panel, click Add Medicine — line goes to Selected Medicines table. No popup for normal billing.
Q: New tab? A: Ctrl+Shift+N. Sale 1 already open. No Add New on Sales page.
Q: When is stock reduced? A: Save Sales (F5), not when Add Medicine is clicked.

## Purchase
Q: How to add supplier? A: Type in Supplier Name (left panel). Fill Address/Phone in same panel. Saved on Save Purchase (F5) only. No Add/+ button. No supplier popup.
Q: How to add medicine line? A: Fill Medicine Details (right panel): name, type, batch, expiry, rate, qty, etc. Click Add Medicine — row goes to items table. No new window.
Q: When is stock increased? A: Save Purchase (F5) only.

## Inventory
Q: How to filter? A: Search and Filter panel — filters apply live (no Apply button). Refresh reloads. Ctrl+Shift+C clears filters.
Q: How to edit stock/MRP? A: Select row, right-click Edit Medicine — dialog opens. Save Changes commits.
Q: How to see purchase/sales history for one medicine? A: View Details (double-click). Read-only.
Q: Reorder from inventory? A: Context menu Reorder — opens Settings Reorder tab with medicine prefilled.

## Sales History
Q: Date filter not working? A: Set From/To then click Apply Filter (dates do not auto-apply).
Q: How to edit bill? A: Edit Bill — opens fullscreen Edit Sale window. Update Sale (F5) saves.
Q: How to see line items? A: View Bill Details or double-click bill.
Q: Print many bills? A: Print All (paper label) — pick period in dialog.

## Purchase History
Q: How to edit? A: Edit Purchase (double-click) — fullscreen Edit Purchase. Update Purchase saves.
Q: View details only? A: No separate view dialog — use Edit Purchase to see full form.
Q: Paid at Entry vs Paid via Payment? A: Entry = paid when bill saved. Paid via Payment = later supplier payments allocated.

## Returns — Sales Return
Q: Find old bill? A: Bill No/Customer search (any age) OR Medicine search (last N days from settings).
Q: Add return line? A: Load bill, pick medicine, Return Qty, Add to Return — list in Step 3. Save Return (F5) commits and restores stock.
Q: When stock updates? A: Save Return (F5) only.

## Returns — Purchase Return
Q: Add qty? A: Load Purchase, select row, Add Selected — small dialog Return — {medicine} for qty. Save Return (F5) commits.
Q: Refund label? A: Credit to Supplier (not Refund Amount).

## Returns — Write-off
Q: What is it? A: Return / Write-off page for expired/damaged stock without purchase record. Submit this supplier per tab.

## Reorder (Settings tab)
Q: Place order? A: Reorder tab, New Order, pick supplier, add medicines, Mark Ordered or Save Draft.
Q: Receive stock? A: Pending Orders, Receive, Open Purchase to enter stock, then Mark Received. Mark Received does NOT add stock by itself.
Q: From inventory? A: Context menu Reorder on inventory row.

## Payment (Settings tab)
Q: Pay supplier? A: Payment tab, Supplier Payment, pick supplier, Amount Paid, Save Payment (F5).
Q: Customer payment? A: Toggle Customer Payment, Cash/Online amounts, Save Payment (F5).

## Ledger (Settings tab)
Q: Supplier statement? A: Ledger tab, Supplier Ledger, pick supplier, From/To, View Report.
Q: Customer statement? A: Customer Ledger toggle, same pattern.

## Exports
Q: Export filtered sales? A: Sales History, set filters, Apply Filter, Export, Current View (with filters).
Q: Export all inventory? A: Inventory Export, Stock Statement (all) OR Home/Data and System Export Inventory.
Q: Export format? A: CSV, Excel (.xlsx), or PDF (HTML) in format dialog after picking report.

## Settings tabs (top bar)
Pharmacy Profile, Contacts, Shelf Management, Appearance, Layout and Lists, Sales and Billing, Import, Alert and Monitoring, Data and System, Payment, Ledger, Reorder, Shortcuts.
Use left Sections sidebar inside each tab (F4 to focus).

## Never say (wrong)
- Popup/window to add supplier, customer, or medicine on Sales/Purchase main screens
- Add Medicine opens a new medicine registration window on Purchase
- Apply Filter on Inventory (filters are live)
- Mark Received on reorder adds inventory without Purchase
- Delete bill/history is reversible

## Home
Q: New bill from home? A: Quick Actions + New Bill or key B.
Q: Export from home? A: Export Sales/Purchases/Inventory/All quick buttons or E / Ctrl+E menu.
Q: Open alerts? A: Quick Actions Alerts -> Settings Alert and Monitoring.
Q: Open ledger? A: Quick Actions Ledger -> Settings Ledger tab.

## General Products
Q: What is it? A: Non-medicine catalog from Home General Products quick action.

## Settings Pharmacy
Q: Save store details? A: Pharmacy Profile section, Save Profile.
Q: Bill template? A: Bill Template section, Save Bill Print Style.

## Settings Contacts
Q: Add doctor? A: Contacts Doctors, Add Doctor.
Q: Add supplier? A: No Add button on supplier list; edit via context menu. Suppliers created on Purchase save.
Q: Export customers? A: Customers section, Export button.

## Settings Import
Q: Photo bill import? A: Import tab, Import Purchase Bill.
Q: JSON import? A: Import Data section, Load from File or Parse JSON.
Q: Mobile import? A: Mobile Import, WiFi receiver or JSON file.

## Settings Layout and Appearance
Q: Change theme? A: Appearance tab, Apply Theme (restarts app).
Q: Home quick actions? A: Appearance Quick Access checkboxes.
Q: Hide columns? A: Layout and Lists, Column Visibility.

## Settings Sales Billing
Q: Sales return days? A: Sales and Billing tab, Sales Return Lookup section.
Q: Batch picker? A: Sales and Billing, Batch Picker section.

## Shelf Management
Q: Rack labels? A: Settings Shelf Management tab for inventory Location.

## Shortcuts
Q: Open Payment tab? A: Ctrl+9 in Settings; S/C toggles supplier/customer.

