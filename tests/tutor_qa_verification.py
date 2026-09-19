"""Verify Satpuda AI tutor — 58 cases across all major screens.

Run:
  python tests/tutor_qa_verification.py
  python tests/tutor_qa_verification.py --offline-only
  python tests/tutor_qa_verification.py --live-only
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import dataclass, field
from typing import Sequence

if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.tutor_context import _screen_ui_facts
from core.tutor_knowledge import SCREEN_DOC_MAP, load_docs_for_screen
from core.tutor_rules import build_system_instructions


@dataclass
class TutorCase:
    screen_id: str
    question: str
    must_in_context: Sequence[str] = ()
    must_in_response: Sequence[str] = ()  # at least one
    must_in_response_all: Sequence[str] = ()  # all required
    must_not_in_response: Sequence[str] = ()


def C(screen, q, ctx=(), resp=(), resp_all=(), bad=()):
  return TutorCase(screen, q, ctx, resp, resp_all, bad)


CASES = [
    # --- Sales (7) ---
    C("sales_billing", "how to add customer in sales", ("Customer Name", "Save Sales"), ("Customer Name", "Save Sales"), bad=("click the + button", "popup opens", "Add New")),
    C("sales_billing", "how to add medicine in sales bill", ("Add Medicine",), ("Add Medicine",), bad=("registration window", "popup to add medicine")),
    C("sales_billing", "when is stock reduced in sales", (), ("Save Sales",), bad=("Add Medicine reduces stock",)),
    C("sales_billing", "how to open new sales tab", ("Ctrl+Shift+N",), ("Ctrl+Shift+N", "Sale 1"), bad=("Add New button on sales",)),
    C("sales_billing", "counter sale how to bill", ("Save Sales",), ("Cash", "Save Sales"), ()),
    C("sales_billing", "what does Add No Stock button do", ("Add No Stock",), ("Add No Stock", "no stock"), ()),
    C("sales_billing", "sales madhye medicine kashe add karayche", ("Add Medicine",), ("Add Medicine",), bad=("window opens", "dialog")),
    # --- Purchase (6) ---
    C("purchase", "how to add supplier in purchase", ("Supplier Name", "Save Purchase"), ("Supplier Name", "Save Purchase"), bad=("click the + button", "popup opens")),
    C("purchase", "purchase madhye medicine kashe add karayche", ("Medicine Details", "Add Medicine"), ("Add Medicine", "Medicine Details"), bad=("window will open", "dialog opens")),
    C("purchase", "when is supplier saved in purchase", (), ("Save Purchase",), ()),
    C("purchase", "how to add medicine line in purchase", ("Add Medicine",), ("Add Medicine", "items table"), bad=("opens a new window", "a new window will open", "separate dialog opens")),
    C("purchase", "new purchase tab shortcut", ("Ctrl+Shift+N",), ("Ctrl+Shift+N",), bad=("Add New on purchase page",)),
    C("purchase", "when does purchase stock increase", (), ("Save Purchase",), bad=("Add Medicine increases stock",)),
    # --- Inventory (7) ---
    C("inventory", "how to filter inventory", ("Apply",), ("filter",), bad=("click Apply Filter",)),
    C("inventory", "how to edit stock in inventory", (), ("Edit Medicine", "Save Changes"), ()),
    C("inventory", "how to view medicine purchase and sales history", (), ("View Details",), ()),
    C("inventory", "how to reorder from inventory", ("Reorder",), ("Reorder",), ()),
    C("inventory", "how to export all inventory stock", ("Stock Statement",), ("Export", "Stock Statement"), ()),
    C("inventory", "clear inventory filters shortcut", ("Ctrl+Shift+C",), ("Ctrl+Shift+C",), bad=("Apply Filter",)),
    C("inventory", "delete zero stock medicines", (), ("Delete Zero Stock", "zero"), ()),
    # --- Sales History (6) ---
    C("sales_history", "sales history date filter not working", ("Apply Filter",), ("Apply Filter",), bad=("auto-apply dates",)),
    C("sales_history", "how to edit a sales bill from history", (), ("Edit Bill", "Update Sale", "double-click"), ()),
    C("sales_history", "how to view sales bill line items", (), ("View Bill Details", "double-click"), ()),
    C("sales_history", "how to export filtered sales report", ("Current View",), (), resp_all=("Export", "Current View")),
    C("sales_history", "how to print many sales bills", (), ("Print All",), ()),
    C("sales_history", "clear sales history filters", (), ("Clear Filter",), ()),
    # --- Purchase History (5) ---
    C("purchase_history", "how to edit purchase from history", (), ("double-click", "Update Purchase", "Edit Purchase"), bad=("view-only dialog",)),
    C("purchase_history", "purchase history date filter", ("Apply Filter",), ("Apply Filter",), ()),
    C("purchase_history", "export purchase register report", ("Purchase Register",), ("Export", "Purchase Register"), ()),
    C("purchase_history", "what is paid at entry on purchase history", (), ("Paid at Entry",), ()),
    C("purchase_history", "export GST purchase report", ("GST",), ("GST", "Export"), ()),
    # --- Returns (6) ---
    C("returns_sales", "how to do sales return", ("Save Return",), ("Save Return",), ()),
    C("returns_sales", "sales return when is stock restored", (), ("Save Return",), bad=("when you add to return list",)),
    C("returns_sales", "find sales return by medicine how many days", (), ("day", "medicine"), ()),
    C("returns_purchase", "purchase return how to add quantity", ("Return",), ("Save Return",), bad=("Refund Amount",)),
    C("returns_purchase", "purchase return credit to supplier", (), ("Credit to Supplier",), bad=("Refund Amount",)),
    C("returns_disposal", "how to write off expired stock", (), ("Write-off", "Submit"), ()),
    # --- Reorder (5) ---
    C("settings_reorder", "how to receive reorder stock", ("Mark Received", "Open Purchase"), ("Mark Received", "Purchase"), bad=("Mark Received adds stock",)),
    C("settings_reorder", "how to place a reorder", (), ("Mark Ordered", "New Order"), ()),
    C("settings_reorder", "reorder load by supplier", (), ("Load by Supplier",), ()),
    C("settings_reorder", "save reorder as draft", (), ("Save Draft",), ()),
    C("settings_reorder", "reorder defaults minimum stock", (), ("Reorder Defaults",), ()),
    # --- Payment & Ledger (5) ---
    C("settings_payment", "how to record supplier payment", (), ("Save Payment", "Supplier"), ()),
    C("settings_payment", "how to record customer payment", (), ("Customer Payment", "Save Payment"), ()),
    C("settings_ledger", "how to view supplier ledger", (), ("View Report", "Supplier"), ()),
    C("settings_ledger", "how to view customer ledger statement", (), ("Customer Ledger", "View Report"), ()),
    C("settings_ledger", "ledger date range filter", (), ("From", "To", "View Report"), ()),
    # --- Settings Alerts (4) ---
    C("settings_alerts", "how to remove all expired medicines from alerts", ("Remove All Expired",), ("Remove All Expired", "Expired"), ()),
    C("settings_alerts", "low stock alert reorder", (), ("Reorder", "Low Stock"), ()),
    C("settings_alerts", "refresh alerts list", (), ("Refresh",), ()),
    C("settings_alerts", "near expiry alert return medicine", (), ("Return", "Near Expiry"), ()),
    # --- Settings Data (4) ---
    C("settings_data", "where to enable Satpuda AI", ("Satpuda AI",), ("Satpuda AI", "My Assist"), ()),
    C("settings_data", "how to export all data csv excel", ("Export All",), ("Export All",), ()),
    C("settings_data", "where is gemini api key for tutor", ("Import",), ("Import", "Gemini"), ()),
    C("settings_data", "difference Satpuda voice and Satpuda AI", ("Satpuda AI",), ("Satpuda AI", "voice"), ()),
    # --- Settings Pharmacy (2) ---
    C("settings_pharmacy", "how to save pharmacy profile", (), ("Save Profile",), ()),
    C("settings_pharmacy", "how to change bill print template", (), ("Bill Template", "Save Bill Print Style"), ()),
    # --- Settings Contacts (4) ---
    C("settings_contacts", "how to add a doctor", (), ("Add Doctor",), ()),
    C("settings_contacts", "recalculate all customer dues", (), ("Recalculate",), ()),
    C("settings_contacts", "how to add new supplier in contacts", (), ("Suppliers",), bad=("Add Supplier button", "click Add")),
    C("settings_contacts", "export customer list", (), ("Export", "Customer"), ()),
    # --- Settings Import (3) ---
    C("settings_import", "how to import purchase bill from photo", (), ("Import Purchase Bill",), ()),
    C("settings_import", "import medicines from json file", (), ("JSON", "Import"), ()),
    C("settings_import", "import from mobile wifi", (), ("Mobile", "WiFi"), ()),
    # --- Settings Layout & Appearance (4) ---
    C("settings_layout", "how to show hide table columns", (), ("Column Visibility",), ()),
    C("settings_layout", "add new medicine type", (), ("Medicine Type", "Add"), ()),
    C("settings_layout", "change application theme", (), ("Apply Theme", "Theme"), ()),
    C("settings_layout", "enable New Bill on home quick actions", (), ("Quick Access", "New Bill"), ()),
    # --- Settings Sales Billing & Shelf (3) ---
    C("settings_sales_billing", "sales return lookup how many days", (), ("Sales Return", "day"), ()),
    C("settings_sales_billing", "medicine batch picker settings sales", (), ("Batch Picker",), ()),
    C("settings_shelf", "shelf management rack labels", (), ("Shelf",), ()),
    # --- Settings Shortcuts (1) ---
    C("settings_shortcuts", "keyboard shortcut open settings payment tab", (), ("Ctrl+9", "Payment"), ()),
    # --- Home (5) ---
    C("home", "how to open new purchase from home", (), ("New Purchase",), ()),
    C("home", "how to open new bill from home", (), ("New Bill",), ()),
    C("home", "export sales from home", (), ("Export Sales",), ()),
    C("home", "open alerts from home", (), ("Alerts",), ()),
    C("home", "open ledger from home", (), ("Ledger",), ()),
    # --- General Products (2) ---
    C("general_products", "what is general products screen", (), ("General Products",), ()),
    C("general_products", "how to open general products", (), ("General Products", "Home"), ()),
]


def norm(t):
    return re.sub(r"\s+", " ", (t or "").lower())


def missing_all(text, phrases):
    low = norm(text)
    return [p for p in phrases if norm(p) not in low]


def missing_any(text, phrases):
    if not phrases:
        return []
    low = norm(text)
    if any(norm(p) in low for p in phrases):
        return []
    return [f"any of: {', '.join(phrases)}"]


def forbidden(text, phrases):
    low = norm(text)
    return [p for p in phrases if norm(p) in low]


def context_blob(sid):
    return build_system_instructions() + _screen_ui_facts(sid) + load_docs_for_screen(sid)


def run_offline():
    ok = fail = 0
    errs = []
    for c in CASES:
        if c.screen_id not in SCREEN_DOC_MAP:
            errs.append(f"[OFFLINE] unknown screen {c.screen_id!r}: {c.question}")
            fail += 1
            continue
        m = missing_all(context_blob(c.screen_id), c.must_in_context)
        if m:
            errs.append(f"[OFFLINE FAIL] {c.screen_id} | {c.question}\n  missing: {m}")
            fail += 1
        else:
            ok += 1
    return ok, fail, errs


def run_live():
    from core.gemini_tutor_config import tutor_availability_message
    from core.gemini_tutor_service import ask_tutor_gemini

    msg = tutor_availability_message()
    if msg:
        return 0, len(CASES), [f"[LIVE SKIP] {msg}"]

    ok = fail = 0
    errs = []
    n = len(CASES)
    for i, c in enumerate(CASES, 1):
        print(f"  [{i}/{n}] {c.screen_id}: {c.question[:52]}...", flush=True)
        try:
            r = ask_tutor_gemini(c.question, context={"screen_id": c.screen_id})
        except Exception as e:
            errs.append(f"[LIVE ERROR] {c.screen_id} | {c.question}\n  {e}")
            fail += 1
            continue
        m = missing_any(r, c.must_in_response) + missing_all(r, c.must_in_response_all)
        b = forbidden(r, c.must_not_in_response)
        if m or b:
            errs.append(
                f"[LIVE FAIL] {c.screen_id} | {c.question}\n"
                f"  missing: {m}\n  forbidden: {b}\n  preview: {r[:260]}..."
            )
            fail += 1
        else:
            ok += 1
    return ok, fail, errs


def main():
    ap = argparse.ArgumentParser(description="Verify Satpuda AI tutor Q&A")
    ap.add_argument("--offline-only", action="store_true")
    ap.add_argument("--live-only", action="store_true")
    args = ap.parse_args()

    screens = len({c.screen_id for c in CASES})
    print(f"Satpuda AI Q&A verification — {len(CASES)} cases, {screens} screens\n")

    total = 0
    if not args.live_only:
        print("=== OFFLINE (docs + UI facts) ===")
        ok, fail, errs = run_offline()
        print(f"Passed {ok}/{ok + fail}")
        for e in errs:
            print(e)
        total += fail
        print()

    if not args.offline_only:
        print("=== LIVE (Gemini API) ===")
        ok, fail, errs = run_live()
        print(f"Passed {ok}/{ok + fail}")
        for e in errs:
            print(e)
        total += fail

    print()
    if total:
        print(f"FAILED: {total} check(s)")
        return 1
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())