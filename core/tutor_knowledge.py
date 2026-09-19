"""Load App Tutor help documents (read-only markdown bundled with the app)."""
from __future__ import annotations

import os
import sys
from typing import Dict, List, Sequence

from core.tutor_rules import sanitize_documentation

_BASE = ("overview.md", "screen_ui_layout.md", "navigation_flow.md", "common_questions.md", "exports.md", "tutor_ai_rules.md", "tutor_safety.md")

SCREEN_DOC_MAP: Dict[str, Sequence[str]] = {
    "overview": _BASE,
    "home": _BASE + ("home.md", "voice_and_shortcuts.md"),
    "sales_billing": _BASE + ("sales_billing.md", "voice_and_shortcuts.md"),
    "purchase": _BASE + ("purchase.md", "import.md", "voice_and_shortcuts.md"),
    "inventory": _BASE + ("inventory.md", "alerts.md", "reorder.md"),
    "sales_history": _BASE + ("sales_history.md", "exports.md", "voice_and_shortcuts.md"),
    "purchase_history": _BASE + ("purchase_history.md", "payment.md", "exports.md"),
    "returns": _BASE + ("returns.md", "purchase_history.md", "sales_history.md"),
    "returns_sales": _BASE + ("returns.md", "sales_history.md"),
    "returns_purchase": _BASE + ("returns.md", "purchase_history.md"),
    "returns_disposal": _BASE + ("returns.md", "inventory.md"),
    "general_products": _BASE + ("general_products.md",),
    "settings": _BASE + ("settings.md", "settings_tabs.md"),
    "settings_pharmacy": _BASE + ("settings.md", "settings_tabs.md", "pharmacy_profile.md"),
    "settings_contacts": _BASE + ("settings.md", "settings_tabs.md", "contacts.md"),
    "settings_shelf": _BASE + ("settings.md", "settings_tabs.md", "shelf_management.md"),
    "settings_layout": _BASE + ("settings.md", "settings_tabs.md", "appearance_layout.md"),
    "settings_sales_billing": _BASE + ("settings.md", "settings_tabs.md", "sales_billing_settings.md", "sales_billing.md"),
    "settings_import": _BASE + ("settings.md", "settings_tabs.md", "import.md", "purchase.md"),
    "settings_alerts": _BASE + ("settings.md", "settings_tabs.md", "alerts.md", "inventory.md"),
    "settings_data": _BASE + ("settings.md", "settings_tabs.md", "data_system_safe.md", "voice_and_shortcuts.md"),
    "settings_payment": _BASE + ("settings.md", "settings_tabs.md", "payment.md", "ledger.md"),
    "settings_ledger": _BASE + ("settings.md", "settings_tabs.md", "ledger.md", "payment.md"),
    "settings_reorder": _BASE + ("settings.md", "settings_tabs.md", "reorder.md", "purchase.md"),
    "settings_shortcuts": _BASE + ("voice_and_shortcuts.md", "settings.md", "settings_tabs.md"),
}


def _help_dir() -> str:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return os.path.join(sys._MEIPASS, "docs", "app_help")
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(root, "docs", "app_help")


def _read_doc(filename: str) -> str:
    path = os.path.join(_help_dir(), filename)
    if not os.path.isfile(path):
        return ""
    try:
        with open(path, encoding="utf-8") as f:
            return sanitize_documentation(f.read().strip())
    except OSError:
        return ""


def load_docs_for_screen(screen_id: str, *, max_chars: int = 52000) -> str:
    files = SCREEN_DOC_MAP.get(screen_id) or SCREEN_DOC_MAP.get("overview", _BASE)
    seen = set()
    parts: List[str] = []
    total = 0
    for name in files:
        if name in seen:
            continue
        seen.add(name)
        text = _read_doc(name)
        if not text:
            continue
        chunk = f"--- {name} ---\n{text}\n"
        if total + len(chunk) > max_chars:
            parts.append(chunk[: max_chars - total])
            break
        parts.append(chunk)
        total += len(chunk)
    return "\n".join(parts).strip()


def list_available_topics() -> List[str]:
    base = _help_dir()
    if not os.path.isdir(base):
        return []
    skip = {"tutor_safety.md", "tutor_ai_rules.md"}
    return sorted(f for f in os.listdir(base) if f.endswith(".md") and f not in skip)