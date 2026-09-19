"""Common Whisper mis-hearings and accent variants -> canonical screen words."""

import re

PHRASE_FIXES = (
    ("sale history", "sales history"),
    ("sales historic", "sales history"),
    ("sale historic", "sales history"),
    ("purchases history", "purchase history"),
    ("purchase historic", "purchase history"),
    ("sale return", "sales return"),
    ("sale returns", "sales return"),
    ("sales returns", "sales return"),
    ("sales retrn", "sales return"),
    ("sales retun", "sales return"),
    ("purchase return", "purchase return"),
    ("purchase returns", "purchase return"),
    ("purchases return", "purchase return"),
    ("purchase retrn", "purchase return"),
    ("return sales", "sales return"),
    ("returns sales", "sales return"),
    ("return purchase", "purchase return"),
    ("returns purchase", "purchase return"),
    ("supplier list", "suppliers"),
    ("suppliers list", "suppliers"),
    ("bill print", "bill print style"),
    ("row count", "table row counts"),
    ("table row count", "table row counts"),
    ("column setting", "column visibility"),
    ("quick menu", "quick access"),
    ("pharma profile", "pharmacy profile"),
    ("farmacy profile", "pharmacy profile"),
    ("purchase bill", "purchase bill import"),
    ("web enter", "web entry"),
    ("danger area", "danger zone"),
    ("app update", "app updates"),
    ("store manage", "store management"),
    ("supplier pay", "supplier payment"),
    ("customer pay", "customer payment"),
    ("keyboard shortcut", "keyboard shortcuts"),
    ("perches", "purchase"),
    ("porches", "purchase"),
    ("porces", "purchase"),
    ("porches history", "purchase history"),
    ("porces history", "purchase history"),
    ("sense history", "sales history"),
    ("boto settings", "open settings"),
    ("o2 supplier", "open suppliers"),
    ("o2 suppliers", "open suppliers"),
    ("open purchase return", "purchase return"),
    ("customers payment", "customer payment"),
    ("suppliers payment", "supplier payment"),
    ("customer payments", "customer payment"),
    ("supplier payments", "supplier payment"),
    ("customers ledger", "customer ledger"),
    ("suppliers ledger", "supplier ledger"),
    ("customer payment ledger", "customer ledger"),
    ("supplier payment ledger", "supplier ledger"),
    ("customer link", "customer ledger"),
    ("supplier link", "supplier ledger"),
    ("customer ling", "customer ledger"),
    ("supplier ling", "supplier ledger"),
    ("supplier leader", "supplier ledger"),
    ("customer leader", "customer ledger"),
    ("supplier engine", "supplier ledger"),
    ("customer engine", "customer ledger"),
    ("supplier leisure", "supplier ledger"),
    ("customer leisure", "customer ledger"),
    ("open custom", "open customer payment"),
    ("go to custom", "go to customer payment"),
)

# Whisper often hears "ledger" as these — keep in sync with command_parser._LEDGER_SYNONYMS
LEDGER_MISHEARINGS = frozenset({
    "ledger", "ledge", "ledgr", "leder", "link", "links", "ling",
    "leader", "leaders", "engine", "leisure", "lazer", "laser", "ladger", "ledgar",
})

PAYMENT_MISHEARINGS = frozenset({
    "payment", "payments", "pay", "paymen", "paymnt", "payement", "paiment",
})


def _normalize_party(token: str) -> str:
    tok = token.lower().strip()
    if tok in ("customer", "customers", "custom", "custmer", "customrs"):
        return "customer"
    if tok in ("supplier", "suppliers", "suplier", "supliers", "suppliar", "vendor", "vendors"):
        return "supplier"
    return tok


def _sounds_like_ledger(word: str) -> bool:
    w = word.lower().strip()
    if w in LEDGER_MISHEARINGS:
        return True
    if w.startswith(("led", "leg")) and len(w) <= 8:
        return True
    return False


def _sounds_like_payment(word: str) -> bool:
    w = word.lower().strip()
    if w in PAYMENT_MISHEARINGS:
        return True
    if w.startswith("pay") and len(w) <= 9:
        return True
    return False


def apply_party_qualifier_fixes(text: str) -> str:
    """Map 'supplier engine/leisure/link' -> 'supplier ledger', etc."""
    if not text:
        return ""
    tokens = text.split()
    out = []
    i = 0
    while i < len(tokens):
        if i + 1 < len(tokens):
            party = _normalize_party(tokens[i])
            qual = tokens[i + 1].lower()
            if party in ("customer", "supplier"):
                if _sounds_like_ledger(qual):
                    out.extend([party, "ledger"])
                    i += 2
                    continue
                if _sounds_like_payment(qual):
                    out.extend([party, "payment"])
                    i += 2
                    continue
        out.append(tokens[i])
        i += 1
    return " ".join(out).strip()

TOKEN_FIXES = {
    "setting": "settings",
    "setings": "settings",
    "settins": "settings",
    "settin": "settings",
    "inventry": "inventory",
    "inventori": "inventory",
    "inventary": "inventory",
    "inventery": "inventory",
    "purchas": "purchase",
    "perchase": "purchase",
    "purcahse": "purchase",
    "purchese": "purchase",
    "perches": "purchase",
    "porches": "purchase",
    "porces": "purchase",
    "selt": "sales",
    "seils": "sales",
    "saels": "sales",
    "sels": "sales",
    "sails": "sales",
    "selts": "shelves",
    "sle": "sales",
    "salez": "sales",
    "histroy": "history",
    "histori": "history",
    "histry": "history",
    "histery": "history",
    "farmacy": "pharmacy",
    "pharma": "pharmacy",
    "pharmecy": "pharmacy",
    "pharmasy": "pharmacy",
    "docter": "doctors",
    "dockter": "doctors",
    "custmer": "customers",
    "customrs": "customers",
    "suplier": "suppliers",
    "suppliar": "suppliers",
    "supliers": "suppliers",
    "vender": "vendor",
    "shelfs": "shelves",
    "appearence": "appearance",
    "aprearance": "appearance",
    "theam": "theme",
    "thme": "theme",
    "fon": "font",
    "fonts": "font",
    "bannr": "banner",
    "dashbord": "dashboard",
    "dashbaord": "dashboard",
    "hom": "home",
    "hoam": "home",
    "ohm": "home",
    "hone": "home",
    "homme": "home",
    "colomn": "column",
    "collumn": "column",
    "collum": "column",
    "schedul": "schedules",
    "threshhold": "thresholds",
    "threshholds": "thresholds",
    "threshol": "thresholds",
    "adminstrator": "administrator",
    "admins": "administrator",
    "link": "ledger",
    "links": "ledger",
    "ling": "ledger",
    "leader": "ledger",
    "leaders": "ledger",
    "engine": "ledger",
    "leisure": "ledger",
    "lazer": "ledger",
    "laser": "ledger",
    "ladger": "ledger",
    "ledgar": "ledger",
    "ledgr": "ledger",
    "leder": "ledger",
    "custom": "customer",
    "hotkey": "hotkeys",
    "importing": "import",
    "managment": "management",
    "managemnt": "management",
    "paymnt": "payment",
    "payement": "payment",
    "preferance": "preferences",
    "prefernces": "preferences",
    "contacs": "contacts",
    "mobil": "mobile",
    "updats": "updates",
    "updat": "updates",
}


def _apply_phrase_fixes(text: str) -> str:
    result = text
    # Expand bare "google drive" / "drive backup" without doubling existing phrases.
    if "google drive backup" not in result:
        result = re.sub(
            r"(?<!\w)google drive(?!\s+backup)(?!\w)",
            "google drive backup",
            result,
        )
        result = re.sub(
            r"(?<!google )(?<!\w)drive backup(?!\w)",
            "google drive backup",
            result,
        )
    for src, dst in sorted(PHRASE_FIXES, key=lambda item: len(item[0]), reverse=True):
        if src == dst or src not in result:
            continue
        pattern = r"(?<!\w)" + re.escape(src) + r"(?!\w)"
        if re.search(pattern, result):
            result = re.sub(pattern, dst, result)
    return result


def apply_pronunciation_fixes(text: str) -> str:
    if not text:
        return ""
    result = text.lower().strip()
    result = _apply_phrase_fixes(result)
    tokens = result.split()
    from core.voice.assistant_config import load_voice_language
    mr_mode = load_voice_language() == "mr"
    fixed = []
    for tok in tokens:
        if mr_mode and tok == "selts":
            fixed.append("sales")
        else:
            fixed.append(TOKEN_FIXES.get(tok, tok))
    result = " ".join(fixed).strip()
    return apply_party_qualifier_fixes(result)
