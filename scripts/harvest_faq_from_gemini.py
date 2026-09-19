"""Ask Satpuda AI (Gemini) all tutor Q&A cases and merge responses into offline_faq.json."""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.gemini_tutor_service import ask_tutor_gemini
from tests.tutor_qa_verification import CASES

PROGRESS_PATH = ROOT / "scripts" / ".faq_harvest_progress.json"


def _safe_err(exc: BaseException) -> str:
    return str(exc).encode("ascii", "replace").decode("ascii")


def _ask_with_retry(question: str, screen: str, retries: int = 3) -> str:
    last: BaseException | None = None
    for attempt in range(retries):
        try:
            return ask_tutor_gemini(question, context={"screen_id": screen}).strip()
        except Exception as exc:
            last = exc
            time.sleep(1.5 * (attempt + 1))
    raise last or RuntimeError("ask failed")


def _save_faq(data: Dict[str, Any], by_id: Dict[str, Dict[str, Any]]) -> None:
    data["entries"] = sorted(by_id.values(), key=lambda e: e["id"])
    with open(FAQ_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def _load_progress() -> set[str]:
    if not PROGRESS_PATH.is_file():
        return set()
    try:
        return set(json.loads(PROGRESS_PATH.read_text(encoding="utf-8")))
    except Exception:
        return set()


def _save_progress(done: set[str]) -> None:
    PROGRESS_PATH.write_text(json.dumps(sorted(done), indent=2), encoding="utf-8")

FAQ_PATH = ROOT / "docs" / "app_help" / "offline_faq.json"

CASE_ENTRY_MAP: Dict[Tuple[str, str], str] = {
    ("sales_billing", "how to add customer in sales"): "sales_add_customer",
    ("sales_billing", "how to add medicine in sales bill"): "sales_add_medicine_row",
    ("sales_billing", "when is stock reduced in sales"): "sales_stock_reduce_on_save",
    ("sales_billing", "how to open new sales tab"): "sales_new_tab",
    ("sales_billing", "counter sale how to bill"): "sales_counter_sale",
    ("sales_billing", "what does add no stock button do"): "sales_add_no_stock",
    ("sales_billing", "sales madhye medicine kashe add karayche"): "sales_add_medicine_row",
    ("purchase", "how to add supplier in purchase"): "purchase_add_supplier",
    ("purchase", "purchase madhye medicine kashe add karayche"): "purchase_add_medicine_row",
    ("purchase", "when is supplier saved in purchase"): "purchase_supplier_save_timing",
    ("purchase", "how to add medicine line in purchase"): "purchase_add_medicine_row",
    ("purchase", "new purchase tab shortcut"): "purchase_new_tab",
    ("purchase", "when does purchase stock increase"): "purchase_stock_increase_on_save",
    ("inventory", "how to filter inventory"): "inventory_filter_live",
    ("inventory", "how to edit stock in inventory"): "inventory_edit_medicine",
    ("inventory", "how to view medicine purchase and sales history"): "inventory_view_details",
    ("inventory", "how to reorder from inventory"): "inventory_reorder_action",
    ("inventory", "how to export all inventory stock"): "inventory_export",
    ("inventory", "clear inventory filters shortcut"): "inventory_clear_filters",
    ("inventory", "delete zero stock medicines"): "inventory_delete_zero_stock",
    ("sales_history", "sales history date filter not working"): "sales_history_apply_filter_dates",
    ("sales_history", "how to edit a sales bill from history"): "sales_history_edit_bill",
    ("sales_history", "how to view sales bill line items"): "sales_history_view_details",
    ("sales_history", "how to export filtered sales report"): "sales_history_export_filtered",
    ("sales_history", "how to print many sales bills"): "sales_history_print_all",
    ("sales_history", "clear sales history filters"): "sales_history_clear_filter",
    ("purchase_history", "how to edit purchase from history"): "purchase_history_edit",
    ("purchase_history", "purchase history date filter"): "purchase_history_apply_filter",
    ("purchase_history", "export purchase register report"): "purchase_history_export_register",
    ("purchase_history", "what is paid at entry on purchase history"): "purchase_history_paid_at_entry",
    ("purchase_history", "export gst purchase report"): "purchase_history_gst_export",
    ("returns_sales", "how to do sales return"): "returns_sales",
    ("returns_sales", "sales return when is stock restored"): "returns_sales_stock_restore",
    ("returns_sales", "find sales return by medicine how many days"): "returns_sales_find_days",
    ("returns_purchase", "purchase return how to add quantity"): "returns_purchase",
    ("returns_purchase", "purchase return credit to supplier"): "returns_purchase_credit",
    ("returns_disposal", "how to write off expired stock"): "returns_disposal",
    ("settings_reorder", "how to receive reorder stock"): "reorder_mark_received",
    ("settings_reorder", "how to place a reorder"): "reorder_place_order",
    ("settings_reorder", "reorder load by supplier"): "reorder_load_by_supplier",
    ("settings_reorder", "save reorder as draft"): "reorder_save_draft",
    ("settings_reorder", "reorder defaults minimum stock"): "reorder_defaults",
    ("settings_payment", "how to record supplier payment"): "settings_payment_supplier",
    ("settings_payment", "how to record customer payment"): "settings_payment_customer",
    ("settings_ledger", "how to view supplier ledger"): "settings_ledger",
    ("settings_ledger", "how to view customer ledger statement"): "settings_ledger_customer",
    ("settings_ledger", "ledger date range filter"): "settings_ledger_date_range",
    ("settings_alerts", "how to remove all expired medicines from alerts"): "settings_alerts_remove_all_expired",
    ("settings_alerts", "low stock alert reorder"): "settings_alerts_reorder",
    ("settings_alerts", "refresh alerts list"): "settings_alerts_refresh",
    ("settings_alerts", "near expiry alert return medicine"): "settings_alerts_return",
    ("settings_data", "where to enable satpuda ai"): "settings_data_satpuda_ai_enable",
    ("settings_data", "how to export all data csv excel"): "settings_data_export_all",
    ("settings_data", "where is gemini api key for tutor"): "settings_data_gemini_key",
    ("settings_data", "difference satpuda voice and satpuda ai"): "settings_data_voice_vs_ai",
    ("settings_pharmacy", "how to save pharmacy profile"): "settings_pharmacy",
    ("settings_pharmacy", "how to change bill print template"): "settings_pharmacy_bill_template",
    ("settings_contacts", "how to add a doctor"): "settings_contacts",
    ("settings_contacts", "recalculate all customer dues"): "settings_contacts_recalculate",
    ("settings_contacts", "how to add new supplier in contacts"): "settings_contacts_add_supplier",
    ("settings_contacts", "export customer list"): "settings_contacts_export_customers",
    ("settings_import", "how to import purchase bill from photo"): "settings_import",
    ("settings_import", "import medicines from json file"): "settings_import",
    ("settings_import", "import from mobile wifi"): "settings_import",
    ("settings_layout", "how to show hide table columns"): "settings_layout",
    ("settings_layout", "add new medicine type"): "settings_layout",
    ("settings_layout", "change application theme"): "settings_layout_theme",
    ("settings_layout", "enable new bill on home quick actions"): "settings_layout_quick_access",
    ("settings_sales_billing", "sales return lookup how many days"): "settings_sales_billing_return_days",
    ("settings_sales_billing", "medicine batch picker settings sales"): "settings_sales_billing_batch_picker",
    ("settings_shelf", "shelf management rack labels"): "settings_shelf",
    ("settings_shortcuts", "keyboard shortcut open settings payment tab"): "settings_shortcuts",
    ("home", "how to open new purchase from home"): "home_new_purchase",
    ("home", "how to open new bill from home"): "home_new_bill",
    ("home", "export sales from home"): "home_export",
    ("home", "open alerts from home"): "home_alerts",
    ("home", "open ledger from home"): "home_ledger",
    ("general_products", "what is general products screen"): "general_products",
    ("general_products", "how to open general products"): "general_products_open",
}

NEW_ENTRY_META: Dict[str, Dict[str, Any]] = {
    "returns_sales_stock_restore": {"screens": ["returns_sales"], "keywords": ["stock restored", "when stock"], "triggers": ["restore stock"]},
    "returns_sales_find_days": {"screens": ["returns_sales"], "keywords": ["find medicine", "how many days"], "triggers": ["medicine return days"]},
    "returns_purchase_credit": {"screens": ["returns_purchase"], "keywords": ["credit to supplier"], "triggers": ["supplier credit"]},
    "settings_ledger_customer": {"screens": ["settings_ledger"], "keywords": ["customer ledger"], "triggers": ["customer statement"]},
    "settings_ledger_date_range": {"screens": ["settings_ledger"], "keywords": ["date range", "from to"], "triggers": ["ledger dates"]},
    "settings_pharmacy_bill_template": {"screens": ["settings_pharmacy"], "keywords": ["bill template", "print style"], "triggers": ["bill print"]},
    "settings_contacts_recalculate": {"screens": ["settings_contacts"], "keywords": ["recalculate", "customer dues"], "triggers": ["recalculate dues"]},
    "settings_contacts_add_supplier": {"screens": ["settings_contacts"], "keywords": ["add supplier contacts"], "triggers": ["new supplier contacts"]},
    "settings_contacts_export_customers": {"screens": ["settings_contacts"], "keywords": ["export customer"], "triggers": ["export customers"]},
    "settings_layout_theme": {"screens": ["settings_layout"], "keywords": ["theme", "Apply Theme"], "triggers": ["dark mode"]},
    "settings_layout_quick_access": {"screens": ["settings_layout"], "keywords": ["quick access", "New Bill"], "triggers": ["quick actions"]},
    "settings_sales_billing_return_days": {"screens": ["settings_sales_billing"], "keywords": ["sales return days"], "triggers": ["return lookup"]},
    "settings_sales_billing_batch_picker": {"screens": ["settings_sales_billing"], "keywords": ["batch picker"], "triggers": ["batch settings"]},
    "general_products_open": {"screens": ["general_products", "home"], "keywords": ["open general products"], "triggers": ["non medicine catalog"]},
}


def _norm_question(q: str) -> str:
    return re.sub(r"\s+", " ", (q or "").strip().lower())


def _entry_id_for_case(screen: str, question: str) -> str:
    key = (screen, _norm_question(question))
    eid = CASE_ENTRY_MAP.get(key)
    if eid:
        return eid
    slug = re.sub(r"[^a-z0-9]+", "_", _norm_question(question)).strip("_")[:48]
    return f"{screen}_{slug}"


def detect_lang_hint(text: str) -> str:
    if re.search(r"[\u0900-\u097F]", text):
        if any(h in text for h in ("कसे", "करायच", "मध्ये", "नाही")):
            return "mr"
        return "hi"
    return "en"


def _translate_response(text_en: str, lang: str) -> str:
    if not text_en.strip():
        return ""
    lang_name = "Marathi" if lang == "mr" else "Hindi"
    prompt = (
        f"Translate the following pharmacy app help steps to {lang_name}. "
        "Keep exact English UI labels. Use numbered steps. Plain text only.\n\n"
        f"{text_en}"
    )
    try:
        return ask_tutor_gemini(prompt, context={"screen_id": "overview"}).strip()
    except Exception as exc:
        print(f"  [translate {lang}] error: {exc}")
        return ""


def harvest(
    *,
    dry_run: bool = False,
    skip_translate: bool = False,
    delay_s: float = 0.4,
    only_ids: Optional[List[str]] = None,
    resume: bool = False,
    en_only: bool = False,
) -> None:
    with open(FAQ_PATH, encoding="utf-8") as f:
        data = json.load(f)
    by_id = {e["id"]: e for e in data.get("entries", [])}

    groups: Dict[str, list] = {}
    for case in CASES:
        eid = _entry_id_for_case(case.screen_id, case.question)
        groups.setdefault(eid, []).append(case)

    targets = sorted(groups.keys())
    if only_ids:
        targets = [t for t in targets if t in only_ids]

    done = _load_progress() if resume else set()
    if resume and done:
        print(f"Resuming: {len(done)} already done")

    print(f"Harvesting {len(targets)} FAQ entries from {len(CASES)} test questions...")
    updated = 0

    for eid in targets:
        if resume and eid in done:
            continue

        cases = groups[eid]
        screen = cases[0].screen_id
        en_q = next((c.question for c in cases if detect_lang_hint(c.question) == "en"), cases[0].question)
        mr_q = next((c.question for c in cases if detect_lang_hint(c.question) == "mr"), None)

        if eid not in by_id:
            meta = NEW_ENTRY_META.get(eid, {})
            by_id[eid] = {
                "id": eid,
                "screens": meta.get("screens") or [screen],
                "keywords": list(meta.get("keywords") or [])[:12],
                "responses": {"en": "", "mr": "", "hi": ""},
                "triggers": list(meta.get("triggers") or []),
            }

        entry = by_id[eid]
        if dry_run:
            print(f"  [dry] {eid}: {en_q[:70]}")
            continue

        print(f"  Ask: {eid}")
        try:
            text_en = _ask_with_retry(en_q, screen)
        except Exception as exc:
            print(f"    ERROR en: {_safe_err(exc)}")
            _save_faq(data, by_id)
            continue

        entry["responses"]["en"] = text_en
        updated += 1
        time.sleep(delay_s)

        if not skip_translate and not en_only:
            if mr_q:
                try:
                    entry["responses"]["mr"] = _ask_with_retry(mr_q, screen)
                except Exception as exc:
                    print(f"    WARN mr: {_safe_err(exc)}")
                    entry["responses"]["mr"] = _translate_response(text_en, "mr")
            else:
                entry["responses"]["mr"] = _translate_response(text_en, "mr")
            time.sleep(delay_s)
            entry["responses"]["hi"] = _translate_response(text_en, "hi")
            time.sleep(delay_s)

        done.add(eid)
        _save_progress(done)
        _save_faq(data, by_id)

    if dry_run:
        print(f"Dry run: {len(targets)} entries")
        return

    print(f"Wrote {len(by_id)} entries ({updated} updated this run)")




def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--skip-translate", action="store_true")
    ap.add_argument("--delay", type=float, default=0.4)
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--en-only", action="store_true")
    args = ap.parse_args()
    harvest(dry_run=args.dry_run, skip_translate=args.skip_translate, delay_s=args.delay, only_ids=args.only, resume=args.resume, en_only=args.en_only)


if __name__ == "__main__":
    main()
