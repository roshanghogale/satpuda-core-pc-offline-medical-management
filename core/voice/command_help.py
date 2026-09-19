"""Voice command phrase counts and examples."""
from __future__ import annotations

from core.voice.command_parser import COMMAND_PREFIXES
from core.voice.screen_registry import SCREEN_ENTRIES


def _entry_by_id(screen_id: str):
    for entry in SCREEN_ENTRIES:
        if entry["id"] == screen_id:
            return entry
    return None


def count_command_phrases(screen_id: str) -> dict:
    """Return counts of distinct command phrases for a screen (after wake word)."""
    entry = _entry_by_id(screen_id)
    if entry is None:
        return {"aliases": 0, "prefixes": 0, "with_prefix": 0, "without_prefix": 0, "total": 0}
    aliases = entry.get("aliases") or [entry["label"].lower()]
    n_aliases = len(aliases)
    n_prefixes = len(COMMAND_PREFIXES)
    with_prefix = n_aliases * n_prefixes
    without_prefix = n_aliases
    return {
        "aliases": n_aliases,
        "prefixes": n_prefixes,
        "with_prefix": with_prefix,
        "without_prefix": without_prefix,
        "total": with_prefix + without_prefix,
        "alias_list": list(aliases),
        "prefix_list": list(COMMAND_PREFIXES),
    }


def example_phrases(screen_id: str, limit: int = 12) -> list[str]:
    """Sample command phrases (without wake word) for help text."""
    info = count_command_phrases(screen_id)
    aliases = info.get("alias_list") or []
    prefixes = info.get("prefix_list") or []
    examples = []
    for alias in aliases[:4]:
        examples.append(alias)
    for prefix in prefixes[:3]:
        if aliases:
            examples.append(f"{prefix} {aliases[0]}")
    for prefix in prefixes[3:6]:
        if len(aliases) > 1:
            examples.append(f"{prefix} {aliases[1]}")
    return examples[:limit]
