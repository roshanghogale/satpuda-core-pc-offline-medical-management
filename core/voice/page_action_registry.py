"""In-page and global voice actions (save, export, dialogs, web purchase, switch store)."""
from __future__ import annotations

import re

PAGE_VOICE_ACTIONS = [
    {
        "id": "close_app",
        "label": "Close Application",
        "aliases": [
            "close app", "quit app", "exit app", "close application",
            "quit application", "exit application", "shut down app",
            "shutdown app", "close the app", "quit the app", "exit the app",
            "nose app", "plaudred", "plod app", "closed app",
        ],
    },
    {
        "id": "start_listening",
        "label": "Start Listening",
        "aliases": [
            "hey satpuda", "hi satpuda", "ok satpuda", "okay satpuda",
            "start listening", "begin listening", "listen now",
            "turn on mic", "turn on microphone", "enable mic", "mic on",
            "turn on satpuda", "satpuda on", "enable satpuda", "start satpuda",
            "wake up satpuda", "wake satpuda", "turn on voice",
        ],
    },
    {
        "id": "stop_listening",
        "label": "Stop Listening",
        "aliases": [
            "stop listening", "pause listening", "pause satpuda",
            "stop satpuda", "satpuda stop", "turn off listening",
            "pause voice", "stop voice", "satpuda pause", "stop",
            "from listening", "stop listings", "stop listing",
        ],
    },
    {
        "id": "turn_off_mic",
        "label": "Turn Off Microphone",
        "aliases": [
            "turn off mic", "turn off microphone", "mic off", "disable mic",
            "satpuda off", "turn off satpuda", "disable satpuda", "off",
            "turn satpuda off", "sleep satpuda", "mute satpuda",
            "turn off voice assistant", "microphone off",
        ],
    },
    {
        "id": "save",
        "label": "Save",
        "aliases": [
            "save", "save bill", "save sales", "save purchase", "save return",
            "save changes", "save data", "save record", "save payment",
            "save profile", "save form", "submit", "save it",
        ],
    },
    {
        "id": "close_dialog",
        "label": "Close Dialog",
        "aliases": [
            "close", "closed", "close dialog", "close window", "dismiss", "go back",
            "close popup", "close alert", "close message", "ok close",
        ],
    },
    {
        "id": "cancel_dialog",
        "label": "Cancel",
        "aliases": [
            "cancel", "cancel dialog", "never mind", "no thanks", "abort",
        ],
    },
    {
        "id": "cancel_all_alerts",
        "label": "Cancel All Alerts",
        "aliases": [
            "cancel all", "cancel all alerts", "close all alerts",
            "dismiss all", "skip all alerts", "close all dialogs",
        ],
    },
    {
        "id": "open_web_purchase",
        "label": "Open Web Purchase Entry",
        "aliases": [
            "open web purchase entry", "web purchase entry", "start web purchase",
            "open web purchase", "launch web purchase", "web entry open",
            "open purchase web", "start web entry",
        ],
    },
    {
        "id": "switch_store",
        "label": "Switch Store",
        "aliases": [
            "switch store", "switch to selected store", "change store",
            "switch to store", "use selected store", "activate store",
        ],
    },
    {
        "id": "apply_list_filter",
        "label": "Filter List",
        "aliases": [
            "clear filters", "reset filters", "clear all filters",
            "show all", "show everything", "no filter",
            "low stock", "show low stock", "out of stock", "show expired",
            "expired stock", "near expiry", "due only", "show due sales",
            "this month sales", "this year sales", "today sales",
            "filter inventory", "filter sales", "filter purchases",
        ],
    },
    {
        "id": "export_menu",
        "label": "Export Menu",
        "aliases": [
            "export menu", "export options", "show export", "list exports",
            "what can i export", "export reports",
        ],
    },
    {
        "id": "export_all_data",
        "label": "Export All",
        "aliases": [
            "export all", "export all data", "export everything",
            "export sales purchases inventory", "full export",
        ],
    },
    {
        "id": "export_sales_data",
        "label": "Export Sales",
        "aliases": [
            "export sales", "export all sales", "sales export",
            "download sales", "export sales data",
        ],
    },
    {
        "id": "export_purchases_data",
        "label": "Export Purchases",
        "aliases": [
            "export purchases", "export all purchases", "purchase export",
            "download purchases", "export purchase data",
        ],
    },
    {
        "id": "export_inventory_data",
        "label": "Export Inventory",
        "aliases": [
            "export inventory", "export stock", "inventory export",
            "download inventory", "export all inventory",
        ],
    },
    {
        "id": "export_current_view",
        "label": "Export Current View",
        "aliases": [
            "export current view", "export filtered view", "export this view",
            "export current", "export view", "export filtered",
        ],
    },
    {
        "id": "export_sales_register",
        "label": "Export Sales Register",
        "aliases": [
            "export sales register", "sales register export", "export register",
            "all bills export", "export all bills",
        ],
    },
    {
        "id": "export_schedule_report",
        "label": "Export Schedule Report",
        "aliases": [
            "export schedule report", "schedule report", "schedule report export",
            "export schedule", "export h schedule", "h schedule report",
            "scheduled report", "export scheduled sales",
        ],
    },
    {
        "id": "export_monthly_summary",
        "label": "Export Monthly Summary",
        "aliases": ["export monthly summary", "monthly summary export", "monthly sales export"],
    },
    {
        "id": "export_daily_summary",
        "label": "Export Daily Summary",
        "aliases": ["export daily summary", "daily summary export", "daily sales export"],
    },
    {
        "id": "export_customer_due",
        "label": "Export Customer Due",
        "aliases": ["export customer due", "customer due report", "export dues"],
    },
    {
        "id": "export_doctor_sales",
        "label": "Export Doctor Sales",
        "aliases": ["export doctor sales", "doctor wise sales", "doctor sales export"],
    },
    {
        "id": "export_payment_mode",
        "label": "Export Payment Mode",
        "aliases": ["export payment mode", "payment mode report", "payment report export"],
    },
    {
        "id": "export_generic",
        "label": "Export",
        "aliases": ["export", "download export", "run export", "do export"],
    },
]

_PAGE_INDEX: list[tuple[str, dict]] | None = None

_FORMAT_ALIASES = {
    "csv": "csv",
    "excel": "xlsx",
    "xlsx": "xlsx",
    "spreadsheet": "xlsx",
    "pdf": "pdf",
    "html": "pdf",
}


def _build_page_index():
    global _PAGE_INDEX
    if _PAGE_INDEX is not None:
        return _PAGE_INDEX
    index = []
    for entry in PAGE_VOICE_ACTIONS:
        for alias in entry.get("aliases", ()):
            norm = " ".join(str(alias).lower().split())
            if norm:
                index.append((norm, entry))
    index.sort(key=lambda item: len(item[0]), reverse=True)
    _PAGE_INDEX = index
    return index


def parse_voice_export_format(text: str) -> str | None:
    low = (text or "").lower()
    for word, fmt in _FORMAT_ALIASES.items():
        if re.search(rf"\b{re.escape(word)}\b", low):
            return fmt
    if re.search(r"\bas\s+(csv|excel|xlsx|pdf)\b", low):
        m = re.search(r"\bas\s+(csv|excel|xlsx|pdf)\b", low)
        return _FORMAT_ALIASES.get(m.group(1), m.group(1))
    return None


def _candidates(text: str):
    from core.voice.command_parser import _collapse_stutter, _normalize, _remove_fillers, _strip_prefixes

    norm = _normalize(text or "")
    if not norm:
        return
    base = _collapse_stutter(_remove_fillers(_strip_prefixes(norm)))
    if base:
        yield base
    stripped = base
    for lead in (
        "open ", "show ", "go to ", "switch to ", "navigate to ",
        "launch ", "display ", "take me to ", "please ", "kindly ",
    ):
        if stripped.startswith(lead):
            rest = stripped[len(lead):].strip()
            if rest:
                yield rest
    yield norm


def match_page_voice_action(command_text: str, *, exact_only: bool = False) -> tuple[dict | None, dict]:
    """Match page/global actions. Returns (action_entry, params).

    exact_only: only full phrase matches (used when user said open/show/go to …).
    """
    params: dict = {}
    fmt = parse_voice_export_format(command_text)
    if fmt:
        params["export_format"] = fmt

    # List filters (inventory / sales history / purchase history) — parametric.
    try:
        from core.voice.list_filter_voice import parse_list_filter_command
        filter_spec = parse_list_filter_command(command_text)
    except Exception:
        filter_spec = None
    if filter_spec is not None:
        params.update(filter_spec)
        return {
            "id": "apply_list_filter",
            "label": "Filter List",
            "aliases": (),
        }, params

    for candidate in _candidates(command_text):
        if not candidate:
            continue
        for alias_norm, entry in _build_page_index():
            if candidate == alias_norm:
                return entry, params
        if exact_only:
            continue
        for alias_norm, entry in _build_page_index():
            # User said the export phrase — alias must appear inside what they said.
            if len(alias_norm) >= 5 and alias_norm in candidate:
                return entry, params
    return None, params


_NAV_COMMAND_LEADS = (
    "open ", "show ", "go to ", "switch to ", "navigate to ",
    "launch ", "display ", "take me to ",
)


def is_navigation_command(command_text: str) -> bool:
    t = " ".join((command_text or "").lower().split())
    return any(t.startswith(p) for p in _NAV_COMMAND_LEADS)
