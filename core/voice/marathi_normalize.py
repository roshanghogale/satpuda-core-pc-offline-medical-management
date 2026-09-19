"""Extra normalization for Marathi command mode (English screen names + actions)."""

from core.voice.marathi_prefixes import MR_COMMAND_PREFIXES, MR_WAKE_BLOCKLIST, is_marathi_action_token

# Whisper often hears "purchase ugaad" as these — must not match wake word "puda"/"sapuda".
PURCHASE_MISHEAR_TOKENS = frozenset({
    "puda", "pung", "vong", "fum", "fumogad", "fumogat",
    "parichesu", "parichess", "pariches", "porches", "porces",
    "perches", "purchas", "par", "chest",
})

# Whisper often hears "sales dakhau" as these — must not match wake alias "sapuda".
SALES_MISHEAR_TOKENS = frozenset({
    "sense", "sen", "seles", "salus", "sails", "selts", "seltsis", "seltis",
    "sels", "salez", "saels", "seils", "cells", "cell", "sele", "seles",
})

# Longest phrases first (applied with word padding).
_MR_PHRASE_FIXES = (
    ("my assist dakhau", "my assist"),
    ("my assist ugaad", "my assist"),
    ("app updates warja", "app updates"),
    ("supplier ledger dakhau", "supplier ledger"),
    ("customer ledger dakhau", "customer ledger"),
    ("sales history ugaad", "sales history"),
    ("sales history dakhau", "sales history"),
    ("sales history warja", "sales history"),
    ("sales history warda", "sales history"),
    ("seltsis tuber", "sales history"),
    ("seltsis history", "sales history"),
    ("purchase ugaad", "purchase"),
    ("pung uger", "purchase"),
    ("puda uger", "purchase"),
    ("vong uger", "purchase"),
    ("pung ugaad", "purchase"),
    ("vong ugaad", "purchase"),
    ("fum ugaad", "purchase"),
    ("fum uger", "purchase"),
    ("purchase uger", "purchase"),
    ("par chest work", "purchase"),
    ("par chest", "purchase"),
    ("parichesu", "purchase"),
    ("myresisk", "my assist"),
    ("myresist", "my assist"),
    ("myresk", "my assist"),
    ("fumogad", "purchase"),
    ("fumogat", "purchase"),
    ("sense dakhau", "sales"),
    ("sen dakhau", "sales"),
    ("seles dakhau", "sales"),
    ("salus dakhau", "sales"),
    ("sails dakhau", "sales"),
    ("sense ugaad", "sales"),
    ("sen ugaad", "sales"),
    ("seles ugaad", "sales"),
    ("salus ugaad", "sales"),
    ("sense warja", "sales"),
    ("sen warja", "sales"),
    ("seles warja", "sales"),
    ("salus warja", "sales"),
    ("sense warda", "sales"),
    ("sen warda", "sales"),
    ("seles warda", "sales"),
    ("salus warda", "sales"),
    ("sales dakhau", "sales"),
    ("sales ugaad", "sales"),
    ("sales warja", "sales"),
    ("sales warda", "sales"),
    ("ho moge", "home"),
    ("home ugaad", "home"),
    ("home uger", "home"),
    ("inventory warja", "inventory"),
    ("inventory warda", "inventory"),
    ("settings warja", "settings"),
    ("settings warda", "settings"),
    ("purchase warja", "purchase"),
    ("purchase warda", "purchase"),
    ("purchase dakhau", "purchase"),
)


def is_purchase_mishear_token(token: str) -> bool:
    """Purchase mis-hearings must not be treated as the Satpuda wake word."""
    if not token:
        return False
    t = token.strip().lower()
    if t in PURCHASE_MISHEAR_TOKENS:
        return True
    if t.startswith("fum") and len(t) <= 8:
        return True
    if t.startswith("pung") or t.startswith("puda") or t.startswith("vong"):
        return True
    return False


def is_sales_mishear_token(token: str) -> bool:
    """Sales mis-hearings must not be treated as the Satpuda wake word."""
    if not token:
        return False
    t = token.strip().lower()
    if t in SALES_MISHEAR_TOKENS:
        return True
    if t.startswith(("sense", "seles", "salus", "sails", "selts", "selti")):
        return True
    if t in ("sen", "sels", "salez", "saels", "seils"):
        return True
    return False


def is_screen_mishear_token(token: str) -> bool:
    return is_purchase_mishear_token(token) or is_sales_mishear_token(token)


def expand_marathi_command(text: str) -> str:
    """Fix common Whisper mis-hearings before prefix strip / screen match."""
    if not text:
        return ""
    result = f" {text.strip().lower()} "
    for src, dst in _MR_PHRASE_FIXES:
        if src == dst:
            continue
        result = result.replace(f" {src} ", f" {dst} ")
    tokens = []
    for tok in result.split():
        if tok in SALES_MISHEAR_TOKENS:
            tokens.append("sales")
        elif tok in PURCHASE_MISHEAR_TOKENS and tok not in ("par", "chest"):
            tokens.append("purchase")
        else:
            tokens.append(tok)
    return " ".join(tokens).strip()


def is_marathi_action_only(text: str) -> bool:
    """True when user said only an action word (e.g. 'ugaad') with no screen name."""
    from core.voice.assistant_config import load_voice_language

    if load_voice_language() != "mr":
        return False
    t = text.strip().lower()
    if not t:
        return True
    tokens = t.split()
    if len(tokens) != 1:
        return False
    tok = tokens[0]
    if tok in MR_WAKE_BLOCKLIST or is_marathi_action_token(tok):
        return True
    for prefix in MR_COMMAND_PREFIXES:
        p = prefix.strip().lower()
        if " " not in p and p == tok:
            return True
    return False
