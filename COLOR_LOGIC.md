# Row Color Logic — All Pages

This document explains exactly how row colors work on every page that has a list/table.

---

## 1. Sales History (`ui/sales_history.py`)

**Tag priority (highest wins):**

| Tag | Background | Text | Condition |
|-----|-----------|------|-----------|
| `account_cleared` | `#d4edda` (light green) | `#155724` (dark green) | `account_cleared = 1` — the customer's entire account balance is zero |
| `bill_cleared` | `#fff3cd` (light yellow) | `#856404` (dark yellow) | `bill_cleared = 1` AND `account_cleared = 0` — this specific bill is paid but the customer still has older outstanding dues |
| `has_due` | `#f8d7da` (light red) | `#721c24` (dark red) | `total_due > 0` — this bill has an unpaid balance |
| *(no tag)* | default theme color | default | Bill is paid and no outstanding account balance |

**How it works:**
- Each row checks `account_cleared`, `bill_cleared`, and `total_due` from the `sales` table.
- `account_cleared = 1` means ALL bills for this customer up to this point are settled.
- `bill_cleared = 1` means only this bill's own amount is paid, but older dues still exist.
- `total_due > 0` means this bill itself has an unpaid portion.

---

## 2. Purchase History (`ui/purchase_history.py`)

**Tag priority (highest wins):**

| Tag | Background | Text | Condition |
|-----|-----------|------|-----------|
| `account_cleared` | `#d4edda` (light green) | `#155724` (dark green) | `account_cleared = 1` — supplier's entire account is settled |
| `bill_cleared` | `#fff3cd` (light yellow) | `#856404` (dark yellow) | `bill_cleared = 1` AND `account_cleared = 0` — this purchase bill is paid but supplier still has older dues |
| `has_due` | `#f8d7da` (light red) | `#721c24` (dark red) | `total_due > 0` — this purchase has an unpaid balance |
| *(no tag)* | default theme color | default | Fully paid, no outstanding balance |

**How it works:**
- Same logic as Sales History but applied to the `purchases` table and suppliers.
- `account_cleared` and `bill_cleared` flags are set when a purchase is saved or updated.

---

## 3. Inventory (`ui/inventory.py`)

**Tag priority (highest wins):**

| Tag | Background | Text | Condition |
|-----|-----------|------|-----------|
| `out_of_stock` | `#ffebee` (very light red) | `#000000` (black) | `stock_qty = 0` |
| `low_stock` | `#fff3e0` (very light orange) | `#000000` (black) | `0 < stock_qty < threshold` (threshold from Settings → Thresholds, default 10) |
| `expired` | `#ffebee` (very light red) | `#000000` (black) | expiry date is in the past |
| `near_expiry` | `#fff3e0` (very light orange) | `#000000` (black) | expiry date is within the configured months (default 3 months) |
| *(no tag)* | default theme color | default | In stock, not expired, not near expiry |

**How it works:**
- `out_of_stock` is checked first — if stock is 0, no other check runs.
- `low_stock` uses a per-type threshold from the `settings` table (`low_stock_tablet`, `low_stock_syrup`, etc.). For tablets/bolus, stock is converted to stripes before comparing.
- `expired` checks if `expiry_date <= today`.
- `near_expiry` checks if `expiry_date <= today + (threshold_months × 30 days)` AND `expiry_date > today`.
- The same tag logic runs in both `load_inventory()` (full load) and `filter_inventory()` (filtered view) — so colors are always correct regardless of which filters are active.

**Note:** `out_of_stock` and `expired` share the same background color (`#ffebee`) but are separate tags. `low_stock` and `near_expiry` also share the same background (`#fff3e0`).

---

## 4. Customers (`ui/customers.py`)

**Tags:**

| Tag | Background | Text | Condition |
|-----|-----------|------|-----------|
| `has_due` | `#f8d7da` (light red) | `#721c24` (dark red) | `total_due > 0` — customer owes money |
| `has_credit` | `#d4edda` (light green) | `#155724` (dark green) | `total_credit > 0` — customer has overpaid (credit balance) |
| *(no tag)* | default theme color | default | No due, no credit — fully cleared |

**How it works:**
- `has_due` takes priority over `has_credit` — if a customer somehow has both, they show as red.
- Values come from `get_all_customers()` in `core/customer_service.py` which aggregates the latest `total_due` and `total_credit` from the `sales` table per customer.
- Colors update live when the Due Filter dropdown is used (the filter calls `_render()` which re-applies tags).

---

## 5. Billing Page (`ui/billing.py`)

No row colors on the selected medicines list. The medicine tree shows plain rows only.

---

## 6. Purchase Page (`ui/purchase.py`)

No row colors on the purchase items list. The items tree shows plain rows only.

---

## Summary Table

| Page | Green | Yellow | Red | Orange |
|------|-------|--------|-----|--------|
| Sales History | Account fully cleared | Bill paid, old dues remain | Has outstanding due | — |
| Purchase History | Account fully cleared | Bill paid, old dues remain | Has outstanding due | — |
| Inventory | — | — | Out of stock / Expired | Low stock / Near expiry |
| Customers | Has credit balance | — | Has outstanding due | — |
| Billing | — | — | — | — |
| Purchase | — | — | — | — |
