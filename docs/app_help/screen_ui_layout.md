# Screen UI layout — single page, no entry dialogs

CRITICAL for Satpuda AI: Purchase and Sales are ONE scrollable screen. Supplier, customer, and medicine are entered in panels already visible on that screen. Nothing opens in a separate window or popup to add supplier, customer, or medicine.

## Purchase screen layout (top to bottom)

1. Tab bar: Purchase 1, Purchase 2, ...
2. Top row — two panels side by side:
   - LEFT: **Supplier Information** — Supplier Name, Address, Phone, GSTIN, DL Numbers, Purchase Date, Bill Number. All inline. No Add/+ button. No Save for supplier alone.
   - RIGHT: **Medicine Details** — Medicine Name, Type, Quantity Details (strips/units per type), HSN Code, GST %, MRP, Rate, Manufacturer, Batch No, Expiry, Schedule, Content/Drug, Discount %, and **Add Medicine** button. All inline on the same screen.
3. Items table — lines added by Add Medicine appear here.
4. **Purchase Summary** — discounts, rounding, payment, **Save Purchase (F5)**.

### What Add Medicine does on Purchase
- User fills the **Medicine Details** fields that are already on screen (right panel).
- **Add Medicine** copies that filled row into the items table below. It does NOT open a new window or dialog.
- It does NOT mean "open a form to register a new medicine". Type a new name in Medicine Name if needed, fill batch/expiry/rate/qty on the same panel, then click Add Medicine.
- Repeat for each product line.
- Stock and supplier are NOT saved until **Save Purchase (F5)**.

### New supplier on Purchase
- Type in Supplier Name field (left panel). Fill Address, Phone, etc. in the same panel.
- No popup. Supplier saved only on Save Purchase (F5).

## Sales screen layout (top to bottom)

1. Tab bar: Sale 1, Sale 2, ...
2. **Customer Information** panel — Payment, Customer Name, Phone, Address (Village), Previous Due, Doctor, Bill Date. All inline. No Add/+ for customer. No separate Save for customer.
3. **Medicine Selection** panel — Medicine search, Quantity, Disc, **Add Medicine** (green). All inline.
4. **Selected Medicines** table — bill lines.
5. Totals and **Save Sales (F5)**.

### What Add Medicine does on Sales
- Pick or type medicine in the Medicine field, enter Quantity (and Disc if needed) in the same panel.
- **Add Medicine** adds that line to the Selected Medicines table. It does NOT open a dialog for normal billing.
- Stock is NOT reduced until Save Sales (F5).

### New customer on Sales
- Type in Customer Name on the same screen. Fill phone/address in Customer Information panel.
- Customer saved only on Save Sales (F5).

## Never tell users (wrong instructions)
- "A window will open" / "new window opens" / "popup" / "dialog" for supplier, customer, or medicine on Purchase or Sales.
- "Click Add Medicine to add a new medicine" (sounds like master registration) — say fill Medicine Details then Add Medicine to add the line to the bill.
- Add/+ button next to supplier or customer.
- Separate Save for supplier or customer before saving the bill.
- Home New Purchase when already on Purchase; Home + New Bill when already on Sales.

## Optional (Sales only, special case)
- **Add No Stock (Ctrl+Shift+M)** is a separate optional feature for selling without stock — not the normal Add Medicine flow. Only mention if user asks about no-stock sales.