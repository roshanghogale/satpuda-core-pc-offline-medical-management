"""Voice actions — run page commands (backup, check updates, install)."""
from __future__ import annotations

import re

VOICE_ACTIONS = [
    {
        "id": "check_updates",
        "label": "Check for Updates",
        "aliases": [
            "check for updates",
            "check updates",
            "check for update",
            "check update",
            "look for updates",
            "search for updates",
            "find updates",
            "update check",
            "have updates",
            "see updates",
            "is update available",
            "any updates",
        ],
    },
    {
        "id": "install_update",
        "label": "Download and Install",
        "aliases": [
            "download and install",
            "download install",
            "install update",
            "install updates",
            "update app",
            "update the app",
            "download update",
            "download the update",
            "download updates",
            "install the update",
            "apply update",
        ],
    },
    {
        "id": "open_installer",
        "label": "Open Installer",
        "aliases": [
            "open installer",
            "open the installer",
            "launch installer",
            "run installer",
            "start installer",
            "open satpuda installer",
            "installer",
            "satpuda installer",
        ],
    },
    {
        "id": "reinstall_installer",
        "label": "Reinstall Installer",
        "aliases": [
            "reinstall installer",
            "reinstall the installer",
            "re install installer",
            "download installer",
            "reinstall satpuda installer",
        ],
    },
    {
        "id": "backup_now",
        "label": "Backup Now",
        "aliases": [
            "backup now",
            "back up now",
            "run backup",
            "start backup",
            "do backup",
            "backup data",
            "upload backup",
            "google drive backup now",
            "drive backup now",
            "backup to drive",
            "backup to google drive",
            "backup",
        ],
    },
    {
        "id": "sync_from_drive",
        "label": "Sync from Drive",
        "aliases": [
            "sync from drive",
            "sync from google drive",
            "sync drive",
            "sync google drive",
            "download backup",
            "download from drive",
            "restore from drive",
            "restore from google drive",
            "pull from drive",
            "sync backup",
            "sink from drive",
            "sin from drive",
        ],
    },
]

_ACTION_INDEX: list[tuple[str, dict]] | None = None


def _build_action_index():
    global _ACTION_INDEX
    if _ACTION_INDEX is not None:
        return _ACTION_INDEX
    index = []
    for entry in VOICE_ACTIONS:
        for alias in entry.get("aliases", ()):
            norm = " ".join(str(alias).lower().split())
            if norm:
                index.append((norm, entry))
    index.sort(key=lambda item: len(item[0]), reverse=True)
    _ACTION_INDEX = index
    return index


def normalize_action_phrase(text: str) -> str:
    """Fix common Whisper mis-hearings before action matching."""
    t = " ".join((text or "").lower().split())
    if not t:
        return ""
    t = t.replace("back up", "backup")
    t = re.sub(r"\btake\s+4\s+updates?\b", "check for updates", t)
    t = re.sub(r"\btake\s+for\s+updates?\b", "check for updates", t)
    if re.fullmatch(r"for\s+updates?", t):
        return "check for updates"
    if re.fullmatch(r"have\s+updates?", t):
        return "check for updates"
    if re.match(r"^(have|check|take|see|fetch)\s+.*\bupdates?\b", t):
        if not any(t.startswith(p) for p in ("open ", "show ", "go to ", "switch to ", "navigate to ")):
            return "check for updates"
    _sync_fixes = (
        (r"\bsink\s+from\s+drive\b", "sync from drive"),
        (r"\bsin\s+from\s+drive\b", "sync from drive"),
        (r"\bsink\s+front\s+right\b", "sync from drive"),
        (r"\bsin\s+front\s+right\b", "sync from drive"),
        (r"\bthink\s+from\s+drive\b", "sync from drive"),
        (r"\bsync\s+from\s+dry\b", "sync from drive"),
        (r"\bsyncing\s+from\s+drive\b", "sync from drive"),
    )
    for pattern, repl in _sync_fixes:
        t = re.sub(pattern, repl, t)
    if not any(t.startswith(p) for p in ("open ", "show ", "go to ", "switch to ", "navigate to ")):
        if re.search(r"\b(sync|sink|sin|think|restore|pull|download)\b", t) and "drive" in t:
            if "backup now" not in t and not re.search(r"\bbackup\s+now\b", t):
                return "sync from drive"
    return t


def match_voice_action(command_text: str) -> dict | None:
    """Match imperative voice action (longest alias wins)."""
    from core.voice.command_parser import _collapse_stutter, _normalize, _remove_fillers, _strip_prefixes

    norm = _normalize(command_text or "")
    if not norm:
        return None
    text = _collapse_stutter(_remove_fillers(_strip_prefixes(norm)))
    text = normalize_action_phrase(text)
    if not text:
        return None
    # Exact alias match first (allows "open installer", "reinstall installer").
    for alias_norm, entry in _build_action_index():
        if text == alias_norm:
            return entry
    nav_leads = (
        "open ", "show ", "go to ", "switch to ", "navigate to ",
        "launch ", "display ", "take me to ",
    )
    if any(text.startswith(p) for p in nav_leads):
        return None
    return None
