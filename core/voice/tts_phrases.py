"""Navigation TTS response pools — varied, natural, anti-repeat."""

from __future__ import annotations

import random
import re
from collections import defaultdict

from core.voice.text_normalize import normalize_transcript

# ── Action pools ({screen} = screen label, e.g. Sales, Inventory) ─────────────

OPEN_POOL = (
    "Opening {screen}",
    "Opening up {screen}",
    "Sure, opening {screen}",
    "Alright, opening {screen}",
    "Getting {screen} ready",
    "Taking you to {screen}",
    "Loading {screen}",
    "Bringing up {screen}",
    "Accessing {screen}",
    "Launching {screen}",
    "Here is {screen}",
    "Got it, opening {screen}",
    "No problem, opening {screen}",
    "Okay, opening {screen}",
    "One moment, opening {screen}",
    "Absolutely, opening {screen}",
    "Done, opening {screen}",
)

SHOW_POOL = (
    "Showing {screen}",
    "Displaying {screen}",
    "Here is {screen}",
    "Bringing up {screen}",
    "Opening {screen}",
    "Loading {screen}",
    "Showing you {screen}",
    "Let me show you {screen}",
    "Pulling up {screen}",
    "Accessing {screen}",
    "Okay, showing {screen}",
    "Sure, showing {screen}",
    "Got it, showing {screen}",
    "One moment, showing {screen}",
    "Alright, displaying {screen}",
)

GOTO_POOL = (
    "Going to {screen}",
    "Navigating to {screen}",
    "Taking you to {screen}",
    "Moving to {screen}",
    "Opening {screen}",
    "Redirecting to {screen}",
    "Switching to {screen}",
    "Loading {screen}",
    "Let's go to {screen}",
    "Alright, taking you to {screen}",
    "Sure, going to {screen}",
    "Got it, navigating to {screen}",
    "One moment, heading to {screen}",
)

SWITCH_POOL = (
    "Switching to {screen}",
    "Moving to {screen}",
    "Changing to {screen}",
    "Opening {screen}",
    "Taking you to {screen}",
    "Navigating to {screen}",
    "Loading {screen}",
    "Redirecting to {screen}",
    "Alright, switching to {screen}",
    "Sure, moving to {screen}",
    "Got it, changing to {screen}",
)

LAUNCH_POOL = (
    "Launching {screen}",
    "Starting {screen}",
    "Opening {screen}",
    "Loading {screen}",
    "Getting {screen} ready",
    "Preparing {screen}",
    "Accessing {screen}",
    "Sure, launching {screen}",
    "Alright, starting {screen}",
    "One moment, preparing {screen}",
)

DIRECT_POOL = (
    "Opening {screen}",
    "Taking you to {screen}",
    "Showing {screen}",
    "Loading {screen}",
    "Bringing up {screen}",
    "Accessing {screen}",
    "Going to {screen}",
    "Navigating to {screen}",
    "Here is {screen}",
    "Sure, opening {screen}",
    "Got it, loading {screen}",
    "Alright, taking you to {screen}",
)

FAST_POOL = (
    "Opening {screen}",
    "Loading {screen}",
    "Showing {screen}",
    "Going to {screen}",
    "Sure",
    "Got it",
    "Done",
)

ERROR_NOT_FOUND_POOL = (
    "I couldn't find that screen",
    "Screen not found",
    "That module isn't available",
    "I couldn't locate that page",
    "Please try again",
    "I didn't recognize that screen",
    "Can you repeat that screen name",
    "Sorry, I didn't catch that screen",
    "That screen isn't in my list",
    "I'm not sure which screen you mean",
)

ERROR_FAILED_POOL = (
    "Could not open that page. Please say it again.",
    "That page didn't open. Please try again.",
    "Something went wrong opening that screen.",
    "I couldn't open that page. Try once more.",
)

ERROR_RETRY_POOL = (
    "Please say it again.",
    "Sorry, I didn't understand. Please try again.",
    "Can you say that once more?",
    "I didn't catch that. Please repeat.",
    "Try saying the screen name again.",
)

ERROR_WAKE_POOL = (
    "Say {wake} first, then the screen name.",
    "Start with {wake}, then tell me the screen.",
    "Please say {wake} before your command.",
)

ERROR_SCREEN_NAME_POOL = (
    "Please say which screen to open.",
    "Which screen should I open?",
    "Tell me the screen name you want.",
    "Say the screen you want to go to.",
)

# Pharmacy-specific (full phrases — no {screen} placeholder)
PHARMACY_PHRASES: dict[str, tuple[str, ...]] = {
    "sales": (
        "Opening Sales Counter",
        "Taking you to Sales",
        "Loading Sales Module",
        "Opening Billing Screen",
        "Getting the sales counter ready",
    ),
    "purchase": (
        "Opening Purchase Entry",
        "Loading Purchase Module",
        "Taking you to Purchase",
        "Bringing up Purchase Entry",
    ),
    "inventory": (
        "Opening Inventory",
        "Loading Stock Management",
        "Showing Inventory",
        "Opening Medicine Stock",
        "Bringing up stock management",
    ),
    "supplier_ledger": (
        "Opening Supplier Ledger",
        "Loading Supplier Accounts",
        "Showing Supplier Ledger",
        "Taking you to Supplier Ledger",
    ),
    "customer_ledger": (
        "Opening Customer Ledger",
        "Loading Customer Accounts",
        "Showing Customer Ledger",
        "Taking you to Customer Ledger",
    ),
    "home": (
        "Going home",
        "Taking you to Home",
        "Opening Home",
        "Back to the home screen",
    ),
}

# Longest triggers first → action bucket
_ACTION_TRIGGERS: tuple[tuple[str, str], ...] = (
    ("take me to", "goto"),
    ("navigate to", "goto"),
    ("switch to", "switch"),
    ("change to", "switch"),
    ("move to", "switch"),
    ("go to", "goto"),
    ("go home", "goto"),
    ("open the", "open"),
    ("mala dakhav", "show"),
    ("mala dakhau", "show"),
    ("mala dakho", "show"),
    ("gheun ja", "goto"),
    ("madhye ja", "goto"),
    ("madhe ja", "goto"),
    ("open kar", "open"),
    ("open kara", "open"),
    ("suru kar", "launch"),
    ("open kar", "open"),
    ("dakhava", "show"),
    ("dakhau", "show"),
    ("dakhav", "show"),
    ("dakhow", "show"),
    ("dakho", "show"),
    ("ughada", "open"),
    ("ughad", "open"),
    ("ugaad", "open"),
    ("ugad", "open"),
    ("uger", "open"),
    ("warda", "goto"),
    ("warja", "goto"),
    ("varda", "goto"),
    ("war da", "goto"),
    ("var da", "goto"),
    ("var ja", "goto"),
    ("vara ja", "goto"),
    ("war ja", "goto"),
    ("display", "show"),
    ("launch", "launch"),
    ("start", "launch"),
    ("show", "show"),
    ("open", "open"),
    ("edit", "open"),
    ("change", "switch"),
    ("run", "launch"),
)

_POOL_BY_ACTION = {
    "open": OPEN_POOL,
    "show": SHOW_POOL,
    "goto": GOTO_POOL,
    "switch": SWITCH_POOL,
    "launch": LAUNCH_POOL,
    "direct": DIRECT_POOL,
}

_recent: dict[str, list[str]] = defaultdict(list)
_RECENT_AVOID = 3
_FAST_CHANCE = 1.0
_PHARMACY_CHANCE = 0.0


def _search_blob(recognized: str, command: str) -> str:
    raw = (recognized or "").lower().strip()
    norm = normalize_transcript(recognized) or raw
    cmd = (command or "").lower().strip()
    return f"{raw} {norm} {cmd}"


def detect_nav_action(recognized: str, command: str) -> str:
    blob = _search_blob(recognized, command)
    for phrase, action in _ACTION_TRIGGERS:
        if phrase in blob:
            return action
    if re.search(r"\b(dakh|ugh|uga|uger|ward|warj|vard)\w*\b", blob):
        if re.search(r"\b(dakh\w*)\b", blob):
            return "show"
        if re.search(r"\b(warj?a|warda|varda|var ja|war ja)\b", blob):
            return "goto"
        return "open"
    return "direct"


def _format_template(template: str, **fields: str) -> str:
    if "{" not in template:
        return template
    try:
        return template.format(**fields)
    except KeyError:
        return template.format(screen=fields.get("screen", ""))


def _pick_from_pool(
    pool: tuple[str, ...],
    *,
    category: str,
    screen: str = "",
    extras: tuple[str, ...] = (),
    **format_fields: str,
) -> str:
    fields = {"screen": screen, **format_fields}
    combined = list(pool) + list(extras)
    recent = _recent[category]
    avoid = set(recent[-_RECENT_AVOID:])
    candidates = [t for t in combined if _format_template(t, **fields) not in avoid]
    if not candidates:
        candidates = list(combined)
    choice = random.choice(candidates)
    spoken = _format_template(choice, **fields)
    recent.append(spoken)
    if len(recent) > _RECENT_AVOID + 2:
        recent.pop(0)
    return spoken


def pick_success_phrase(
    screen_label: str,
    recognized: str,
    command: str,
    screen_id: str = "",
) -> str:
    screen = (screen_label or "screen").strip()
    action = detect_nav_action(recognized, command)
    category = f"{action}:{screen_id or screen}"

    if screen_id and screen_id in PHARMACY_PHRASES and random.random() < _PHARMACY_CHANCE:
        extras = PHARMACY_PHRASES[screen_id]
        return _pick_from_pool((), category=category, screen=screen, extras=extras)

    if random.random() < _FAST_CHANCE:
        return _pick_from_pool(FAST_POOL, category=category, screen=screen)

    pool = _POOL_BY_ACTION.get(action, DIRECT_POOL)
    return _pick_from_pool(pool, category=category, screen=screen)


def pick_error_phrase(detail: str = "", *, wake_name: str = "", lang: str = "en") -> str:
    low = (detail or "").lower()

    if lang == "mr":
        if "wake word" in low or ("say" in low and wake_name.lower() in low):
            return f"आधी {wake_name} म्हणा, मग स्क्रीनचे नाव सांगा."
        if "wake only" in low or "screen name" in low or "empty command" in low:
            return "कोणती स्क्रीन उघडायची ते सांगा."
        if "no matching" in low or "not found" in low:
            return random.choice((
                "ती स्क्रीन सापडली नाही.",
                "समजले नाही. पुन्हा सांगा.",
                "कृपया पुन्हा एकदा सांगा.",
            ))
        return random.choice((
            "समजले नाही. पुन्हा एकदा सांगा.",
            "कृपया पुन्हा सांगा.",
        ))

    if "navigation failed" in low or detail == "Navigation failed":
        return _pick_from_pool(ERROR_FAILED_POOL, category="error:failed", screen="")

    if "say your command after" in low or "heard " in low and "after it" in low:
        return _pick_from_pool(ERROR_SCREEN_NAME_POOL, category="error:screen", screen="")

    if "wake word" in low or ("say" in low and "before your command" in low):
        return _pick_from_pool(
            ERROR_WAKE_POOL,
            category="error:wake",
            wake=wake_name,
        )

    if "wake only" in low or "screen name" in low or "empty command" in low:
        return _pick_from_pool(ERROR_SCREEN_NAME_POOL, category="error:screen", screen="")

    if "no matching" in low or "not found" in low:
        return _pick_from_pool(ERROR_NOT_FOUND_POOL, category="error:notfound", screen="")

    return _pick_from_pool(ERROR_RETRY_POOL, category="error:retry", screen="")


VOICE_ACTION_PHRASES: dict[str, tuple[str, ...]] = {
    "check_updates": (
        "Checking for updates now",
        "One moment, checking GitHub for updates",
        "Sure, looking for app updates",
        "Alright, checking for a new version",
        "Let me check for updates",
        "Searching for the latest release",
        "Got it, checking for updates",
    ),
    "install_update": (
        "Downloading and installing the update",
        "Starting the app update now",
        "One moment, downloading the latest version",
        "Sure, installing the update",
        "Alright, updating the app now",
        "Getting the new version ready to install",
        "Got it, downloading and installing",
    ),
    "open_installer": (
        "Opening the installer",
        "Sure, opening Satpuda installer",
        "Launching the installer now",
        "Got it, opening the installer",
    ),
    "reinstall_installer": (
        "Reinstalling the installer",
        "Sure, downloading a fresh installer",
        "Starting installer reinstall",
        "Got it, reinstalling the installer",
    ),
    "backup_now": (
        "Starting backup now",
        "Backing up to Google Drive",
        "One moment, uploading your backup",
        "Sure, running backup now",
        "Alright, backing up your data",
        "Got it, starting Google Drive backup",
        "Uploading backup now",
    ),
    "save": (
        "Saving now",
        "Got it, saving",
        "Sure, saving your changes",
    ),
    "open_web_purchase": (
        "Opening web purchase entry",
        "Starting web purchase entry",
        "Launching web purchase in your browser",
    ),
    "switch_store": (
        "Switching store",
        "Sure, switching to the selected store",
    ),
    "export_generic": (
        "Select export type",
        "Opening export menu",
    ),
    "export_schedule_report": (
        "Opening schedule report",
        "Schedule report",
    ),
    "close_app": (
        "Closing application",
        "Shutting down",
    ),
    "start_listening": (
        "Microphone on",
        "I'm listening",
        "Satpuda is listening",
    ),
    "stop_listening": (
        "Stopped listening",
        "Paused listening",
    ),
    "turn_off_mic": (
        "Microphone off",
        "Turning off microphone",
    ),
    "sync_from_drive": (
        "Syncing from Google Drive",
        "One moment, downloading backup from Drive",
        "Sure, pulling the latest backup",
        "Alright, syncing from Drive now",
        "Got it, restoring from Google Drive",
        "Starting sync from Drive",
    ),
}


def pick_voice_action_phrase(action_id: str) -> str:
    pool = VOICE_ACTION_PHRASES.get(action_id, ("Working on it", "One moment"))
    return _pick_from_pool(pool, category=f"voice_action:{action_id}", screen="")
