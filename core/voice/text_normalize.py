"""Normalize English Whisper output for command matching."""

import re
import unicodedata

from core.voice.pronunciation import apply_pronunciation_fixes

# Indic scripts that are never valid navigation text (Devanagari allowed in Marathi mode).
_OTHER_INDIC_SCRIPTS = re.compile(
    r"[\u0D00-\u0D7F\u0B80-\u0BFF\u0C00-\u0CFF\u0C80-\u0CFF\u0B00-\u0B7F]",
    flags=re.UNICODE,
)

_ALL_INDIC_INCLUDING_DEVANAGARI = re.compile(
    r"[\u0D00-\u0D7F\u0B80-\u0BFF\u0C00-\u0CFF\u0C80-\u0CFF\u0B00-\u0B7F\u0900-\u097F]",
    flags=re.UNICODE,
)

_PHRASE_MAP = (
    ("parker history", "purchase history"),
    ("parking history", "purchase history"),
    ("open inventory open inventory", "open inventory"),
    ("open sales open sales", "open sales"),
    ("open purchase open purchase", "open purchase"),
    ("open purchase screen", "open purchase"),
    ("open purchase page", "open purchase"),
    ("open the purchase", "open purchase"),
    ("open in looking", "open inventory"),
    ("open sales screen", "open sales"),
    ("open sales page", "open sales"),
    ("go for sale", "open sales"),
    ("go for sales", "open sales"),
    ("show purchase screen", "show purchase"),
    ("open the settings", "open settings"),
    ("open setting", "open settings"),
    ("show setting", "show settings"),
    ("go to setting", "go to settings"),
)


def _is_repetition_hallucination(text: str) -> bool:
    tokens = text.split()
    if len(tokens) >= 5 and len(set(tokens)) <= 2:
        return True
    compact = re.sub(r"\s+", "", text)
    if len(compact) >= 12:
        from collections import Counter
        char, count = Counter(compact).most_common(1)[0]
        if count >= max(8, int(len(compact) * 0.55)):
            return True
    return False


def is_garbage_transcript(text: str) -> bool:
    if not text or not text.strip():
        return True
    compact = re.sub(r"\s+", "", text)
    if len(compact) < 2:
        return True

    from core.voice.assistant_config import load_voice_language

    if load_voice_language() == "mr":
        if _OTHER_INDIC_SCRIPTS.search(text):
            return True
        if _is_repetition_hallucination(text):
            return True
    else:
        if _ALL_INDIC_INCLUDING_DEVANAGARI.search(text):
            return True

    if len(compact) > 40 and len(set(compact)) < max(6, len(compact) // 8):
        return True
    return False


def _apply_phrase_map(text: str) -> str:
    """Replace known phrases using word boundaries (avoids 'open settings' -> 'open settingss')."""
    result = text
    for src, dst in sorted(_PHRASE_MAP, key=lambda item: len(item[0]), reverse=True):
        if src == dst or src not in result:
            continue
        pattern = r"(?<!\w)" + re.escape(src) + r"(?!\w)"
        if re.search(pattern, result):
            result = re.sub(pattern, dst, result)
    return result


def _dedupe_repeated_phrase(text: str) -> str:
    """Whisper often doubles phrases: 'open inventory open inventory'."""
    tokens = text.split()
    if len(tokens) >= 4 and len(tokens) % 2 == 0:
        half = len(tokens) // 2
        if tokens[:half] == tokens[half:]:
            return " ".join(tokens[:half])
    return text


def normalize_transcript(text: str) -> str:
    """Cleanup + accent/pronunciation fixes for navigation commands."""
    if not text or not text.strip():
        return ""
    if is_garbage_transcript(text):
        return ""

    from core.voice.assistant_config import load_voice_language

    norm = unicodedata.normalize("NFKC", text).lower().strip()
    norm = _dedupe_repeated_phrase(norm)
    if load_voice_language() == "mr":
        norm = re.sub(r"[^\w\s\u0900-\u097F]", " ", norm, flags=re.UNICODE)
    else:
        norm = re.sub(r"[^\w\s]", " ", norm)
    norm = re.sub(r"\s+", " ", norm).strip()
    norm = _apply_phrase_map(norm)
    norm = apply_pronunciation_fixes(norm)
    if load_voice_language() == "mr":
        from core.voice.marathi_normalize import expand_marathi_command
        norm = expand_marathi_command(norm)
        norm = apply_marathi_transcript_fixes(norm)
    return norm.strip()


def apply_marathi_transcript_fixes(text: str) -> str:
    """Remaining Roman/Devanagari tweaks after expand_marathi_command."""
    if not text:
        return ""
    fixes = (
        ("sails warja", "sales"),
        ("sails warda", "sales"),
        ("sails ", "sales "),
        (" selts warja ", " shelves warja "),
        (" selts warda ", " shelves warda "),
        ("kashkamer", "customer"),
        ("kash customer", "customer"),
        ("cash customer", "customer"),
        ("customar", "customer"),
        ("lajardhwam", "ledger"),
        ("lajardham", "ledger"),
        ("lajardhw", "ledger"),
        ("lajard", "ledger"),
        ("supplier leisure", "supplier ledger"),
        ("customer leisure", "customer ledger"),
        ("hom ", "home "),
        (" hom", " home"),
        ("परईजे", "purchase"),
        ("परचेस", "purchase"),
        ("वर दा", "warja"),
    )
    result = f" {text} "
    for src, dst in fixes:
        result = result.replace(f" {src} ", f" {dst} ")
    return result.strip()
