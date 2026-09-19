"""Parse voice commands: wake word, prefixes, alias matching."""
from __future__ import annotations

import re
import unicodedata

from core.voice.assistant_config import (
    DEFAULT_ASSISTANT_NAME,
    load_assistant_name,
    load_voice_language,
)
from core.voice.marathi_prefixes import MR_COMMAND_PREFIXES, MR_FILLER_WORDS, is_marathi_action_token
from core.voice.screen_registry import SCREEN_ENTRIES
from core.voice.text_normalize import normalize_transcript
from core.voice.voice_log import voice_log

# Legacy Satpuda-only fuzzy parts (used when wake name is still satpuda)
_SATPUDA_LEGACY_ALIASES = (
    "satpuda",
    "sat puda",
    "sapuda",
    "sutpuda",
    "setpuda",
    "soft puda",
    "shut puda",
)

_SAT_PARTS = frozenset({
    "sat", "set", "sut", "sap", "soft", "shut", "satt",
})

_PUDA_PARTS = frozenset({
    "puda", "pudha", "pudaa", "pooda", "pura", "puraa",
})

# Common Whisper mis-hearings for default / popular names
_PHONETIC_EXTRAS = {
    "vira": (
        "vira", "viraa", "vera", "veera", "wira", "waira", "vaira",
        "wera", "weera", "bera", "bhira", "fera", "very", "vary",
    ),
    "vera": (
        "veera", "veeraa", "vira", "viraa", "verra", "verah", "viera",
        "wera", "weera", "wira", "waira", "vaira", "bera", "bhira", "bara",
        "fera", "ferah", "verre", "vere", "vary", "very", "veer",
    ),
    "core": ("kor", "coor", "kore"),
    "pulse": ("puls", "pauls"),
    "clerk": ("clark", "clerc"),
    "pilot": ("pilots",),
    "scout": ("scouts",),
    "satpuda": _SATPUDA_LEGACY_ALIASES[1:],
}

_wake_state = {
    "name": DEFAULT_ASSISTANT_NAME,
    "aliases": (DEFAULT_ASSISTANT_NAME,),
}


def reload_wake_config():
    """Reload wake word from disk (after Administrator save)."""
    global ASSISTANT_NAME
    name = load_assistant_name()
    _wake_state["name"] = name
    _wake_state["aliases"] = _build_wake_aliases(name)
    _wake_state["_loaded"] = True
    ASSISTANT_NAME = name
    return name


def get_assistant_name() -> str:
    if not _wake_state.get("_loaded"):
        reload_wake_config()
    return _wake_state["name"]


def get_wake_aliases() -> tuple[str, ...]:
    get_assistant_name()
    return _wake_state["aliases"]


# Backwards-compatible module constant (updated on reload)
@property
def _assistant_name_prop():
    return get_assistant_name()


def _build_wake_aliases(name: str) -> tuple[str, ...]:
    n = _normalize(name) or DEFAULT_ASSISTANT_NAME
    seen = []
    for candidate in (n, n.replace(" ", ""), n.replace("-", " ")):
        c = candidate.strip()
        if c and c not in seen:
            seen.append(c)
    for extra in _PHONETIC_EXTRAS.get(n, ()):
        e = _normalize(extra)
        if e and e not in seen:
            seen.append(e)
    return tuple(seen)


# Initialise on import (after helpers below)
ASSISTANT_NAME = DEFAULT_ASSISTANT_NAME

COMMAND_PREFIXES = (
    "take me to",
    "navigate to",
    "switch to",
    "go to",
    "launch",
    "display",
    "show",
    "open",
    "edit",
    "change",
)

EN_PREFIXES = COMMAND_PREFIXES
MR_PREFIXES = MR_COMMAND_PREFIXES
HI_PREFIXES = ()


def get_active_language() -> str:
    return load_voice_language()


def get_command_prefixes() -> tuple[str, ...]:
    if get_active_language() == "mr":
        return MR_PREFIXES
    return EN_PREFIXES


def get_filler_words() -> frozenset:
    if get_active_language() == "mr":
        return FILLER_WORDS | MR_FILLER_WORDS
    return FILLER_WORDS


def reload_voice_parser_config():
    """Reload wake word and language-dependent parser settings from disk."""
    reload_wake_config()
    return get_active_language()

FILLER_WORDS = frozenset({
    "please", "the", "a", "an", "to", "me", "my", "screen", "page",
    "window", "module", "tab", "section", "up",
})

# Keep multi-word screen aliases intact when "my" would be removed as a filler.
_PROTECTED_PHRASES = (
    "my assist",
    "my assistant",
)

# When these appear, bare "customer"/"supplier" must not beat payment/ledger screens
_CONTEXT_QUALIFIERS = frozenset({
    "payment", "payments", "pay", "ledger", "ledge", "ledgr", "leder",
    "link", "links", "ling", "leader", "leaders", "engine", "leisure",
    "lazer", "laser", "ladger", "ledgar",
    "account", "accounts", "collection", "collections",
})

_LEDGER_SYNONYMS = frozenset({
    "ledger", "ledge", "ledgr", "leder", "link", "links", "ling",
    "leader", "leaders", "engine", "leisure", "lazer", "laser", "ladger", "ledgar",
})

_PAYMENT_SYNONYMS = frozenset({
    "payment", "payments", "pay", "paymen", "paymnt", "payement", "paiment",
})


def _collapse_stutter(text: str) -> str:
    """Collapse Whisper stutter: 'google google drive backup backup'."""
    tokens = (text or "").split()
    if len(tokens) < 2:
        return text or ""
    out = [tokens[0]]
    for tok in tokens[1:]:
        if tok != out[-1]:
            out.append(tok)
    return " ".join(out)


def _normalize(text: str) -> str:
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text)
    text = text.lower().strip()
    text = re.sub(r"[^\w\s\u0900-\u097F]", " ", text, flags=re.UNICODE)
    text = re.sub(r"\s+", " ", text).strip()
    text = _collapse_stutter(text)
    return text


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", _normalize(text))


def _levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        curr = [i]
        for j, cb in enumerate(b, 1):
            cost = 0 if ca == cb else 1
            curr.append(min(prev[j] + 1, curr[j - 1] + 1, prev[j - 1] + cost))
        prev = curr
    return prev[-1]


def _fuzzy_limit(word_len: int) -> int:
    if word_len <= 3:
        return 1
    if word_len <= 5:
        return 2
    return 3


def _is_satpuda_legacy_token(token: str) -> bool:
    t = _normalize(token)
    if t in _SAT_PARTS:
        return True
    return len(t) <= 5 and _levenshtein(t, "sat") <= 1


def _is_puda_legacy_token(token: str) -> bool:
    t = _normalize(token)
    if get_active_language() == "mr":
        from core.voice.marathi_normalize import is_purchase_mishear_token
        if is_purchase_mishear_token(t):
            return False
    if t in _PUDA_PARTS:
        return True
    return _levenshtein(t, "puda") <= 1


def _token_matches_wake(token: str, wake_aliases: tuple[str, ...]) -> bool:
    if is_marathi_action_token(token):
        return False
    if get_active_language() == "mr":
        from core.voice.marathi_normalize import is_screen_mishear_token
        if is_screen_mishear_token(token):
            return False
    compact = _compact(token)
    if not compact:
        return False
    for wake in wake_aliases:
        wc = _compact(wake)
        if not wc:
            continue
        if compact == wc:
            return True
        limit = _fuzzy_limit(len(wc))
        if len(compact) >= max(2, len(wc) - 2) and _levenshtein(compact, wc) <= limit:
            return True
    name = get_assistant_name()
    if name == "satpuda":
        if compact.startswith("sat") and "pud" in compact:
            return True
        if compact.startswith("sap") and "pud" in compact:
            return True
    if name == "vira":
        if len(compact) >= 2 and compact[0] in "vwbf":
            if _levenshtein(compact, "vira") <= 2:
                return True
            if compact.startswith(("vi", "wi", "ve", "vir", "we", "veer")):
                return True
    if name == "vera":
        if len(compact) >= 2 and compact[0] in "vwbf":
            if _levenshtein(compact, "vera") <= 3:
                return True
            if compact.startswith(("ve", "we", "vi", "wi", "veer", "weer")):
                return True
    return False


def canonicalize_wake_text(text: str) -> str:
    """Map Whisper wake mis-hearings to the configured assistant name."""
    from core.voice.wake_matcher import get_wake_mishearings

    norm = _normalize(text)
    if not norm:
        return ""
    wake = get_assistant_name()
    mishearings = {_compact(m) for m in get_wake_mishearings(wake)}
    mishearings.update(_compact(a) for a in get_wake_aliases())

    # Phrase-level replacements (longest first)
    result = norm
    for phrase in sorted(get_wake_mishearings(wake), key=len, reverse=True):
        p = _normalize(phrase)
        if p and p in result and p != wake:
            result = result.replace(p, wake)

    tokens = result.split()
    fixed = []
    for tok in tokens:
        if is_marathi_action_token(tok):
            fixed.append(tok)
            continue
        if get_active_language() == "mr":
            from core.voice.marathi_normalize import is_screen_mishear_token
            if is_screen_mishear_token(tok):
                fixed.append(tok)
                continue
        c = _compact(tok)
        if c == _compact(wake) or c in mishearings or _token_matches_wake(tok, get_wake_aliases()):
            fixed.append(wake)
        else:
            fixed.append(tok)
    # Drop consecutive duplicate wake tokens (Whisper echo).
    collapsed = []
    for tok in fixed:
        if collapsed and tok == wake and collapsed[-1] == wake:
            continue
        collapsed.append(tok)
    return " ".join(collapsed).strip()


def _wake_span_end(tokens: list[str], wake_aliases: tuple[str, ...]) -> int | None:
    if not tokens:
        return None

    for wake in sorted(wake_aliases, key=lambda w: len(_normalize(w)), reverse=True):
        wake_tokens = _normalize(wake).split()
        if not wake_tokens:
            continue
        n = len(wake_tokens)
        if len(tokens) >= n and tokens[:n] == wake_tokens:
            return n

    if get_assistant_name() == "satpuda":
        if len(tokens) >= 2 and _is_satpuda_legacy_token(tokens[0]) and _is_puda_legacy_token(tokens[1]):
            return 2

    if _token_matches_wake(tokens[0], wake_aliases):
        return 1

    for i, tok in enumerate(tokens):
        if _token_matches_wake(tok, wake_aliases):
            return i + 1
        if get_assistant_name() == "satpuda" and i + 1 < len(tokens):
            if _is_satpuda_legacy_token(tok) and _is_puda_legacy_token(tokens[i + 1]):
                return i + 2

    norm = " ".join(tokens)
    for wake in sorted(wake_aliases, key=lambda w: len(_compact(w)), reverse=True):
        wc = _compact(wake)
        if not wc:
            continue
        nc = _compact(norm)
        idx = nc.find(wc)
        if idx == 0:
            consumed = len(wc)
            chars = 0
            for j, tok in enumerate(tokens):
                chars += len(_compact(tok))
                if chars >= consumed:
                    return j + 1
        if 0 < idx <= 3:
            chars = 0
            for j, tok in enumerate(tokens):
                chars += len(_compact(tok))
                if chars >= idx + len(wc):
                    return j + 1

    return None


def _trim_leading_wake_tokens(text: str) -> str:
    """Remove duplicate wake-word tokens Whisper echoes in the command part."""
    tokens = text.split()
    wake_aliases = get_wake_aliases()
    changed = True
    while changed and tokens:
        changed = False
        if _token_matches_wake(tokens[0], wake_aliases):
            tokens = tokens[1:]
            changed = True
            continue
        if (
            get_assistant_name() == "satpuda"
            and len(tokens) >= 2
            and _is_satpuda_legacy_token(tokens[0])
            and _is_puda_legacy_token(tokens[1])
        ):
            tokens = tokens[2:]
            changed = True
    return " ".join(tokens).strip()


def _is_wake_only_command(text: str) -> bool:
    tokens = text.split()
    if not tokens:
        return True
    wake_aliases = get_wake_aliases()
    return all(_token_matches_wake(tok, wake_aliases) for tok in tokens)


def _strip_wake_word(text: str) -> str | None:
    text = canonicalize_wake_text(text)
    norm = _normalize(text)
    if not norm:
        return None
    tokens = norm.split()
    wake_aliases = get_wake_aliases()
    end = _wake_span_end(tokens, wake_aliases)
    if end is None:
        return None
    remainder = _trim_leading_wake_tokens(" ".join(tokens[end:]).strip())
    if _is_wake_only_command(remainder):
        return ""
    return remainder


def _strip_prefixes(text: str) -> str:
    result = text
    all_prefixes = sorted(
        get_command_prefixes() + HI_PREFIXES,
        key=len,
        reverse=True,
    )
    changed = True
    while changed:
        changed = False
        for prefix in all_prefixes:
            p = _normalize(prefix)
            if result == p:
                result = ""
                changed = True
                break
            if result.startswith(p + " "):
                result = result[len(p):].strip()
                changed = True
                break
            if result.endswith(" " + p):
                result = result[: -len(p)].strip()
                changed = True
                break
    return result


def _remove_fillers(text: str) -> str:
    protected = {}
    working = text
    for i, phrase in enumerate(_PROTECTED_PHRASES):
        if phrase in working:
            key = f"__prot{i}__"
            working = working.replace(phrase, key)
            protected[key] = phrase
    fillers = get_filler_words()
    tokens = [t for t in working.split() if t not in fillers]
    result = " ".join(tokens).strip()
    for key, phrase in protected.items():
        result = result.replace(key, phrase)
    return result


def _build_alias_index(entries=None):
    entries = entries or SCREEN_ENTRIES
    index = []
    for entry in entries:
        for alias in entry.get("aliases", []):
            norm = _normalize(alias)
            if norm:
                index.append((norm, entry))
    index.sort(key=lambda item: len(item[0]), reverse=True)
    return index


_ALIAS_INDEX = _build_alias_index()


def _token_similar(word: str, candidate: str) -> bool:
    if word == candidate:
        return True
    if not word or not candidate:
        return False
    if get_active_language() == "mr":
        from core.voice.marathi_normalize import is_sales_mishear_token
        if is_sales_mishear_token(word) and candidate in ("shelf", "shelves"):
            return False
    if candidate == "ledger" and word in _LEDGER_SYNONYMS:
        return True
    if word == "ledger" and candidate in _LEDGER_SYNONYMS:
        return True
    if candidate == "payment" and word in _PAYMENT_SYNONYMS:
        return True
    if word == "payment" and candidate in _PAYMENT_SYNONYMS:
        return True
    dist = _levenshtein(word, candidate)
    shorter, longer = (word, candidate) if len(word) <= len(candidate) else (candidate, word)
    # Short tokens must be very close (blocks "seis" -> "sales" / "setings")
    if len(longer) <= 5 or len(shorter) <= 4:
        return dist <= 1 and word[0] == candidate[0]
    if len(word) <= 4 and len(candidate) >= 6:
        return dist <= 1 and word[0] == candidate[0]
    limit = _fuzzy_limit(len(candidate))
    if len(word) + 3 < len(candidate):
        limit = min(limit, _fuzzy_limit(len(word)))
    return dist <= limit


def _subsequence_fuzzy(text_tokens: list[str], alias_tokens: list[str]) -> bool:
    if not alias_tokens:
        return False
    pos = 0
    for alias_tok in alias_tokens:
        found = False
        while pos < len(text_tokens):
            if _token_similar(text_tokens[pos], alias_tok):
                found = True
                pos += 1
                break
            pos += 1
        if not found:
            return False
    return True


def _tokens_match_phrase(text_tokens: list[str], alias_tokens: list[str]) -> bool:
    """Alias words appear consecutively in the heard command."""
    n, m = len(text_tokens), len(alias_tokens)
    if m == 0 or m > n:
        return False
    for start in range(n - m + 1):
        if all(
            _token_similar(text_tokens[start + i], alias_tokens[i])
            for i in range(m)
        ):
            return True
    return False


def _has_context_qualifier(text_tokens: list[str]) -> bool:
    return any(tok in _CONTEXT_QUALIFIERS for tok in text_tokens)


def _bare_noun_blocked(alias_tokens: list[str], text_tokens: list[str]) -> bool:
    """Block 'customer'/'supplier' alone when user said payment/ledger phrase."""
    if len(alias_tokens) != 1 or len(text_tokens) <= 1:
        return False
    if not _has_context_qualifier(text_tokens):
        return False
    return not any(q in alias_tokens for q in _CONTEXT_QUALIFIERS)


_WEAK_TYPO_ALIASES = frozenset({
    "histroy", "histry", "histro", "histic", "inventry", "inventori",
    "saels", "purchas", "perchase", "purcahse", "retrns", "retuns",
    "dashbord", "hom", "hoam", "hone", "farmacy",
})


def _weak_typo_alias_blocked(alias_tokens: list[str], text_tokens: list[str]) -> bool:
    """Block typo-only aliases from matching multi-word phrases ('parker history' → histroy)."""
    if len(alias_tokens) != 1 or len(text_tokens) <= 1:
        return False
    return alias_tokens[0] in _WEAK_TYPO_ALIASES


_MIN_MULTI_TOKEN_MATCH_SCORE = 2000


def _match_score(text: str, text_tokens: list[str], alias_norm: str) -> int:
    alias_tokens = alias_norm.split()

    if text == alias_norm:
        return 10000 + len(alias_norm)

    if len(alias_tokens) >= 2:
        if _tokens_match_phrase(text_tokens, alias_tokens):
            return 9500 + len(alias_norm)
        if _subsequence_fuzzy(text_tokens, alias_tokens):
            return 8800 + len(alias_norm)

    if len(alias_tokens) == 1 and len(text_tokens) == 1:
        if _token_similar(text_tokens[0], alias_tokens[0]):
            return 7000 + len(alias_tokens[0])

    if alias_norm in text:
        if _bare_noun_blocked(alias_tokens, text_tokens):
            return 0
        return 900 + len(alias_norm)

    if len(alias_tokens) == 1:
        for tok in text_tokens:
            if _token_similar(tok, alias_tokens[0]):
                if _bare_noun_blocked(alias_tokens, text_tokens):
                    return 0
                if _weak_typo_alias_blocked(alias_tokens, text_tokens):
                    return 0
                return 500 + len(alias_tokens[0])

    return 0


def match_screen(command_text: str, alias_index=None):
    from core.voice.assistant_config import load_voice_language
    from core.voice.marathi_normalize import expand_marathi_command

    norm = _normalize(command_text)
    if load_voice_language() == "mr":
        norm = expand_marathi_command(norm)
    text = _remove_fillers(_strip_prefixes(norm))
    if not text:
        return None
    alias_index = alias_index or _ALIAS_INDEX
    text_tokens = text.split()
    best_entry = None
    best_score = 0
    for alias_norm, entry in alias_index:
        score = _match_score(text, text_tokens, alias_norm)
        if score > best_score:
            best_score = score
            best_entry = entry
    if best_entry is None or best_score <= 0:
        return None
    if len(text_tokens) >= 2 and best_score < _MIN_MULTI_TOKEN_MATCH_SCORE:
        voice_log(
            f'Weak screen match rejected (score={best_score}): "{text}"'
        )
        return None
    return best_entry


class CommandParseResult:
    __slots__ = ("recognized", "entry", "label", "wake_found", "reason", "command_text", "action")

    def __init__(
        self,
        recognized,
        entry=None,
        label=None,
        wake_found=False,
        reason="",
        command_text="",
        action=None,
    ):
        self.recognized = recognized
        self.entry = entry
        self.action = action
        self.label = label or (entry.get("label") if entry else None) or (
            action.get("label") if action else None
        )
        self.wake_found = wake_found
        self.reason = reason
        self.command_text = command_text


def has_command_prefix(text: str) -> bool:
    norm = _normalize(text)
    if not norm:
        return False
    for prefix in get_command_prefixes():
        p = _normalize(prefix)
        if norm == p or norm.startswith(p + " ") or norm.endswith(" " + p):
            return True
    return False


def try_direct_command(text: str, alias_index=None) -> CommandParseResult | None:
    """Run navigation when user says 'open sales' without the wake word."""
    raw = (text or "").strip()
    if not raw or not has_command_prefix(raw):
        return None
    result = parse_command_only(raw, alias_index=alias_index)
    if result.entry is None:
        return None
    voice_log(f'Direct command (no wake word): "{result.recognized}"')
    result.wake_found = True
    return result


def has_wake_word(text: str) -> bool:
    raw = (text or "").strip()
    if not raw:
        return False
    recognized = normalize_transcript(raw) or raw
    return _strip_wake_word(recognized) is not None


def _exact_screen_match(command_text: str, alias_index=None):
    """Screen alias match only when the phrase equals a known alias (no fuzzy)."""
    norm = _normalize(command_text)
    if not norm:
        return None
    text = _remove_fillers(_strip_prefixes(norm))
    if not text:
        return None
    alias_index = alias_index or _ALIAS_INDEX
    for alias_norm, entry in alias_index:
        if text == alias_norm:
            return entry
    return None


def is_intentional_command_attempt(text: str, alias_index=None) -> bool:
    """True when the user is likely talking to Satpuda, not ambient room noise."""
    raw = (text or "").strip()
    if not raw:
        return False

    if has_wake_word(raw):
        return True

    if has_command_prefix(raw):
        return True

    recognized = normalize_transcript(raw) or raw
    norm = _normalize(recognized)
    if not norm:
        return False

    if has_command_prefix(norm):
        return True

    from core.voice.action_registry import match_voice_action, normalize_action_phrase

    action_text = normalize_action_phrase(_remove_fillers(_strip_prefixes(norm))) or norm
    if match_voice_action(action_text):
        return True

    remainder = _remove_fillers(_strip_prefixes(norm))
    # Single-word screen names ("sales", "home") are often ambient TV/noise — require 2+ words.
    if remainder and len(remainder.split()) >= 2:
        if _exact_screen_match(norm, alias_index=alias_index) is not None:
            return True

    return False


def parse_command_only(text: str, alias_index=None) -> CommandParseResult:
    raw = (text or "").strip()
    if not raw:
        return CommandParseResult(raw, reason="No speech detected")
    recognized = normalize_transcript(raw) or raw
    if recognized != raw:
        voice_log(f'Normalized: "{raw}" -> "{recognized}"')
    remainder = _remove_fillers(_strip_prefixes(_normalize(recognized)))
    if not remainder:
        return CommandParseResult(recognized, wake_found=True, reason="Empty command")
    entry = match_screen(remainder, alias_index=alias_index)
    if entry is None:
        from core.voice.marathi_normalize import is_marathi_action_only
        if is_marathi_action_only(remainder):
            return CommandParseResult(
                recognized,
                entry=None,
                wake_found=True,
                command_text=remainder,
                reason='Say screen name + action, e.g. "Purchase ugaad"',
            )
        return CommandParseResult(
            recognized,
            entry=None,
            wake_found=True,
            command_text=remainder,
            reason=f"No matching screen for: {remainder}",
        )
    return CommandParseResult(
        recognized,
        entry=entry,
        label=entry.get("label"),
        wake_found=True,
        command_text=remainder,
    )


def parse_voice_command(text: str, alias_index=None) -> CommandParseResult:
    raw = (text or "").strip()
    if not raw:
        return CommandParseResult(raw, reason="No speech detected")
    recognized = normalize_transcript(raw) or raw
    if recognized != raw:
        voice_log(f'Normalized: "{raw}" -> "{recognized}"')
    remainder = _strip_wake_word(recognized)
    wake_name = get_assistant_name()
    if remainder is None:
        voice_log(f'Wake word not found in: "{recognized}"')
        return CommandParseResult(
            recognized,
            wake_found=False,
            reason=f'Say "{wake_name}" before your command',
        )
    voice_log(f'Wake word OK, command part: "{remainder or "(wake only)"}"')
    if remainder:
        from core.voice.voice_control import normalize_mic_command
        fixed = normalize_mic_command(remainder)
        if fixed and fixed != remainder:
            voice_log(f'Mic phrase normalized: "{remainder}" -> "{fixed}"')
            remainder = fixed
    if not remainder:
        from core.voice.voice_control import parse_standby_wake
        from core.voice.page_action_registry import match_page_voice_action

        activated, wake_rest = parse_standby_wake(recognized)
        if activated:
            action, voice_params = match_page_voice_action(
                wake_rest or "start listening", exact_only=True,
            )
            if action is None:
                action, voice_params = match_page_voice_action("start listening", exact_only=True)
            if action is not None:
                action = dict(action)
                if voice_params:
                    action["voice_params"] = voice_params
                cmd = wake_rest or "start listening"
                return CommandParseResult(
                    recognized,
                    entry=None,
                    label=action.get("label"),
                    wake_found=True,
                    command_text=cmd,
                    action=action,
                )
        return CommandParseResult(
            recognized,
            wake_found=True,
            reason=f"Heard {wake_name} — say your command after it",
        )
    if load_voice_language() == "mr":
        from core.voice.marathi_normalize import expand_marathi_command
        remainder = expand_marathi_command(remainder)
    from core.voice.action_registry import match_voice_action, normalize_action_phrase
    from core.voice.page_action_registry import is_navigation_command, match_page_voice_action

    # Nav phrases like "open sales" / "open backup" must hit screens first.
    # List-filter phrases ("show this month sales", "show low stock") are matched
    # inside match_page_voice_action before screen aliases.
    # match_voice_action strips "open " internally and would match "backup" → backup_now.
    _NAV_PHRASE_ACTIONS = frozenset({"open_web_purchase", "apply_list_filter"})
    action = None
    voice_params = {}

    if is_navigation_command(remainder):
        nav_action, voice_params = match_page_voice_action(remainder, exact_only=True)
        if nav_action is not None and nav_action.get("id") in _NAV_PHRASE_ACTIONS:
            action = nav_action
        else:
            entry = match_screen(remainder, alias_index=alias_index)
            if entry is not None:
                return CommandParseResult(
                    recognized,
                    entry=entry,
                    label=entry.get("label"),
                    wake_found=True,
                    command_text=remainder,
                )
            if nav_action is not None:
                action = nav_action
    if action is None:
        norm_remainder = normalize_action_phrase(remainder) or remainder
        action = match_voice_action(norm_remainder)

    if action is None:
        action, voice_params = match_page_voice_action(remainder)
    if action is not None:
        action = dict(action)
        if voice_params:
            action["voice_params"] = voice_params
        return CommandParseResult(
            recognized,
            entry=None,
            label=action.get("label"),
            wake_found=True,
            command_text=remainder,
            action=action,
        )
    entry = match_screen(remainder, alias_index=alias_index)
    if entry is None:
        from core.voice.marathi_normalize import is_marathi_action_only
        if is_marathi_action_only(remainder):
            return CommandParseResult(
                recognized,
                entry=None,
                wake_found=True,
                command_text=remainder,
                reason='Say screen name + action, e.g. "Purchase ugaad"',
            )
        return CommandParseResult(
            recognized,
            entry=None,
            wake_found=True,
            command_text=remainder,
            reason=f"No matching screen for: {remainder}",
        )
    return CommandParseResult(
        recognized,
        entry=entry,
        label=entry.get("label"),
        wake_found=True,
        command_text=remainder,
    )


def parse_active_session_command(text: str, alias_index=None) -> CommandParseResult | None:
    """Parse commands while actively listening — wake word not required."""
    from core.voice.action_registry import match_voice_action, normalize_action_phrase
    from core.voice.page_action_registry import is_navigation_command, match_page_voice_action
    from core.voice.voice_control import match_active_mic_action, normalize_mic_command

    raw = (text or "").strip()
    if not raw:
        return None

    norm = normalize_mic_command(raw) or raw
    if norm != raw:
        voice_log(f'Active session phrase normalized: "{raw}" -> "{norm}"')

    mic = match_active_mic_action(norm)
    if mic is not None:
        return CommandParseResult(
            raw,
            entry=None,
            label=mic.get("label"),
            wake_found=True,
            command_text=norm,
            action=dict(mic),
        )

    direct = try_direct_command(norm, alias_index=alias_index)
    if direct is not None:
        direct.recognized = raw
        return direct

    recognized = normalize_transcript(norm) or norm
    if recognized != norm:
        voice_log(f'Active session normalized: "{norm}" -> "{recognized}"')

    action_text = normalize_action_phrase(recognized) or recognized
    action = match_voice_action(action_text)
    voice_params = {}
    if action is None:
        # Strict page actions — avoid fuzzy "load" → wrong export, etc.
        action, voice_params = match_page_voice_action(recognized, exact_only=True)
        if action is None and norm != recognized:
            action, voice_params = match_page_voice_action(norm, exact_only=True)
    if action is not None:
        action = dict(action)
        if voice_params:
            action["voice_params"] = voice_params
        return CommandParseResult(
            raw,
            entry=None,
            label=action.get("label"),
            wake_found=True,
            command_text=action_text,
            action=action,
        )

    remainder = _remove_fillers(_strip_prefixes(_normalize(recognized)))
    if not remainder:
        return None

    if is_navigation_command(remainder):
        entry = match_screen(remainder, alias_index=alias_index)
        if entry is not None:
            return CommandParseResult(
                raw,
                entry=entry,
                label=entry.get("label"),
                wake_found=True,
                command_text=remainder,
            )

    entry = match_screen(remainder, alias_index=alias_index)
    if entry is None:
        return None
    return CommandParseResult(
        raw,
        entry=entry,
        label=entry.get("label"),
        wake_found=True,
        command_text=remainder,
    )


def parse_followup_command(text: str, alias_index=None) -> CommandParseResult | None:
    """Alias for active-session parsing (backup now, open sales, stop listening, etc.)."""
    return parse_active_session_command(text, alias_index=alias_index)


def is_incomplete_command(command_text: str) -> bool:
    """True when user said only a prefix ('open', 'go to') with no screen name yet."""
    norm = _normalize(command_text or "")
    if not norm:
        return False
    stripped = _remove_fillers(_strip_prefixes(norm))
    if stripped:
        return False
    for prefix in get_command_prefixes():
        p = _normalize(prefix)
        if not p:
            continue
        if norm == p or norm.startswith(p + " "):
            return True
    return False


def score_transcript_candidate(
    text: str,
    alias_index=None,
    *,
    for_rerank: bool = False,
) -> int:
    """Rank Whisper hypotheses — higher = better match to app commands."""
    from core.voice.text_normalize import normalize_transcript

    raw = (text or "").strip()
    if not raw:
        return 0
    result = parse_voice_command(raw, alias_index=alias_index)
    if for_rerank:
        # Rerank must not prefer prompt-biased commands without the wake word.
        if result.wake_found and result.action is not None:
            return 11000 + len(result.command_text or "")
        if result.wake_found and result.entry is not None:
            return 10000 + len(result.command_text or "")
        if result.wake_found and (result.command_text or "").strip():
            return 1000 + len(result.command_text or "")
        if result.wake_found:
            return 100
        norm = normalize_transcript(raw) or ""
        return min(40, len(norm)) if norm else 0

    if result.action is not None:
        return 11000 + len(result.command_text or "")
    if result.entry is not None:
        return 10000 + len(result.command_text or "")
    if result.wake_found and (result.command_text or "").strip():
        return 1000 + len(result.command_text or "")
    if result.wake_found:
        return 100
    direct = try_direct_command(raw, alias_index=alias_index)
    if direct is not None and direct.entry is not None:
        return 500 + len(direct.command_text or "")
    return 1 if normalize_transcript(raw) else 0
