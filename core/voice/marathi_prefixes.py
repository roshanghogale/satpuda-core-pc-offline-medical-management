"""Marathi voice command action phrases (screen names stay English)."""

# Devanagari — longest multi-word phrases first.
MR_DEVANAGARI_PREFIXES = (
    "मला दाखव",
    "घेऊन जा",
    "मध्ये जा",
    "वर जा",
    "ओपन कर",
    "सुरू कर",
    "दाखवा",
    "उघडा",
    "दाखव",
    "उघड",
)

# Whisper often outputs Marathi actions in Roman letters (mr model).
MR_ROMAN_PREFIXES = (
    "mala dakhav",
    "mala dakhau",
    "mala dakho",
    "gheun ja",
    "ghrun ja",
    "madhye ja",
    "madhe ja",
    "var ja",
    "vara ja",
    "war ja",
    "war da",
    "var da",
    "open kar",
    "open kara",
    "opan kar",
    "suroo kar",
    "suru kar",
    "dakhava",
    "dakhav",
    "dakhau",
    "dakho",
    "dakhow",
    "ughada",
    "ughad",
    "ugaad",
    "ugad",
    "uger",
    "ughd",
    "warda",
    "varda",
)

MR_COMMAND_PREFIXES = MR_DEVANAGARI_PREFIXES + MR_ROMAN_PREFIXES

MR_FILLER_WORDS = frozenset({
    "मला",
    "mala",
    "na",
    "ना",
    "कृपया",
})

# Never treat these as the wake word (Whisper confuses them with "sapuda", etc.).
MR_WAKE_BLOCKLIST = frozenset({
    "warda",
    "varda",
    "varja",
    "warja",
    "war da",
    "var da",
    "वर दा",
    "dakhav",
    "dakhau",
    "dakho",
    "dakhava",
    "dakhav",
    "dakhow",
    "ughad",
    "ugaad",
    "ugad",
    "uger",
    "ughd",
    "ughada",
    "ughada",
    "उघड",
    "उघडा",
    "दाखव",
    "दाखवा",
    "वर",
    "जा",
})

MR_VOICE_EXAMPLES = (
    'Satpuda Purchase ugaad   (wake word always English)',
    'Satpuda Sales dakhau',
    'Satpuda Inventory warda',
    'Satpuda Supplier Ledger dakhau',
    'Satpuda mala Home dakhau',
)

EN_VOICE_EXAMPLES = (
    'Satpuda open purchase',
    'Satpuda show sales',
    'Satpuda open supplier ledger',
    'Satpuda customer payment',
    'Satpuda open settings',
)


def is_marathi_action_token(token: str) -> bool:
    """True if token is a Marathi command action — must not match wake word."""
    if not token:
        return False
    t = token.strip().lower()
    if t in MR_WAKE_BLOCKLIST:
        return True
    return False
