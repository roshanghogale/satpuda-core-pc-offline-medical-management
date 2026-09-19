# Screen list and navigation flow

## Top navigation
| Button | Screen |
| Home | Dashboard, quick actions |
| Sales | Billing — Sale 1 already open |
| Purchase | Stock-in — Purchase 1 already open |
| Inventory | Stock list |
| Sales History | Past sales |
| Purchase History | Past purchases |
| Returns | Returns |
| Settings | Configuration |

## Home quick actions (left column only)
+ New Bill, New Purchase, Search Medicine, Contacts, Ledger, Alerts, General Products, Export buttons.

Do not send users to Home quick actions when already on Sales or Purchase.

## Single-screen rule
Purchase and Sales use inline panels only. See screen_ui_layout.md. No popup to add supplier, customer, or medicine.

## When data is saved
### Sales
- Customer: saved on **Save Sales (F5)** only.
- Lines: **Add Medicine** adds to table; stock reduces on F5.

### Purchase
- Supplier: saved on **Save Purchase (F5)** only.
- Lines: fill **Medicine Details**, **Add Medicine** adds to table; stock increases on F5.

## Multi-tab
Ctrl+Shift+N new tab. Ctrl+Shift+W close. No Add New button on Sales/Purchase pages.
## Tutor reference
See common_questions.md for verified Q&A per screen. See exports.md for all export menus. See settings_tabs.md for every Settings tab.
