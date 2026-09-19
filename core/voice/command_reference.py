"""Voice command reference text for My Assist settings panel."""
from __future__ import annotations

from core.voice.action_registry import VOICE_ACTIONS
from core.voice.assistant_config import get_assistant_display_name, get_voice_language_label
from core.voice.marathi_prefixes import EN_VOICE_EXAMPLES, MR_COMMAND_PREFIXES, MR_VOICE_EXAMPLES
from core.voice.page_action_registry import PAGE_VOICE_ACTIONS
from core.voice.screen_registry import SCREEN_ENTRIES

_MIC_ACTION_IDS = frozenset({
    "start_listening", "stop_listening", "turn_off_mic", "close_app",
})


def _format_action_lines(name: str, entries: list, *, skip_ids: frozenset | None = None) -> list[str]:
    lines = []
    skip_ids = skip_ids or frozenset()
    for entry in entries:
        aid = entry.get("id", "")
        if aid in skip_ids:
            continue
        label = entry.get("label") or aid
        aliases = entry.get("aliases") or ()
        samples = list(aliases[:3])
        if not samples:
            samples = [label.lower()]
        phrase_parts = [f"{name} {s}" for s in samples]
        lines.append(f"  {label}:")
        lines.append(f"    {'  |  '.join(phrase_parts)}")
    return lines


def build_my_assist_reference(language: str | None = None) -> str:
    from core.voice.assistant_config import load_voice_language

    lang = language or load_voice_language()
    name = get_assistant_display_name()
    lines = [
        f"Language: {get_voice_language_label(lang)}",
        f"Wake word: {name}",
        "",
        "Microphone (indicator shows only after Hey Satpuda):",
        f"  Start listening:  Hey {name}  |  {name} start listening  |  {name} turn on mic",
        f"    (Hey {name} works without wake word while mic is on standby)",
        f"  Stop listening:  {name} stop listening  |  {name} pause satpuda",
        f"  Turn off mic:  {name} turn off mic  |  {name} satpuda off",
        f"  Close app:  {name} close app  |  {name} quit app",
        "",
        f"Every command needs {name} first (except dialog buttons while a popup is open):",
        f"  {name} open sales  |  {name} open inventory  |  {name} open purchase history",
        "",
    ]

    if lang == "mr":
        lines.extend([
            "Marathi mode — say wake word + English screen + Marathi action:",
            "",
            "Roman actions: ugaad / dakhau / warda / mala dakhau",
            "  " + "  |  ".join(MR_COMMAND_PREFIXES[:6]),
            "",
            "Examples:",
        ])
        for ex in MR_VOICE_EXAMPLES:
            lines.append(f"  {ex}")
        lines.extend(["", "Navigation (English screen name + ugaad / dakhau):"])
    else:
        lines.extend([
            "Quick examples:",
        ])
        for ex in EN_VOICE_EXAMPLES:
            lines.append(f"  {ex}")
        lines.extend([
            "",
            "Navigation — prefixes: open, show, go to, switch to, navigate to",
            "",
            "All screens:",
        ])

    for entry in SCREEN_ENTRIES:
        label = entry.get("label") or entry.get("id", "")
        aliases = entry.get("aliases") or ()
        sample = aliases[0] if aliases else label
        if lang == "mr":
            lines.append(f"  {label}:  {sample} ugaad  |  {sample} dakhau")
        else:
            lines.append(f"  {label}:  {name} open {sample}")

    lines.extend([
        "",
        "Management actions:",
    ])
    lines.extend(_format_action_lines(name, VOICE_ACTIONS))

    lines.extend([
        "",
        "Page actions (save, export, dialogs):",
    ])
    lines.extend(_format_action_lines(name, PAGE_VOICE_ACTIONS, skip_ids=_MIC_ACTION_IDS))

    lines.extend([
        "",
        "List filters (Inventory / Sales History / Purchase History):",
        f"  {name} show low stock  |  {name} show expired stock  |  {name} show H1 schedule",
        f"  {name} show this month sales  |  {name} show due sales  |  {name} show this year purchases",
        f"  {name} show today sales  |  {name} show last month purchases  |  {name} clear filters",
        f"  {name} show near expiry  |  {name} show out of stock  |  {name} show non scheduled medicines",
        f"  {name} sales for Ram  |  {name} purchases for MedPlus  |  {name} show customer Suresh",
        f"  {name} from 1 June to 15 June sales  |  {name} from 2025-06-01 to 2025-06-15 due sales",
        "",
        "While a dialog is open (no wake word):",
        "  cancel  |  close  |  select H and export report  |  schedule report",
        "  (export menu) sales register  |  show schedules",
        "",
        "My Assist settings:",
        f"  {name} open my assist",
    ])

    return "\n".join(lines)
