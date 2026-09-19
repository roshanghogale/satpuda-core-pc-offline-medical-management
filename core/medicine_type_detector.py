"""
Medicine type classification for purchase bill import.

Uses medicine name, pack/PKG unit, QTY unit, and bill line text together
with confidence scoring. Reuses types stored in the database or import_learned.json.
"""
from __future__ import annotations

import json
import os
import re
import sys
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

# Canonical types returned by the detector (mapped to layout types via match_type_to_available)
CANONICAL_TYPES: Tuple[str, ...] = (
    "Tablet", "Bolus", "Capsule", "Syrup", "Suspension", "Liquid", "Powder",
    "Drops", "Eye Drops", "Ear Drops", "Nasal Drops", "Injection",
    "Injection - Vial", "Gel", "Vaccine", "Ointment", "Cream", "Liniment",
    "Granules", "Lotion", "Spray", "Shampoo", "Sachet", "Soap", "Inhaler",
    "Instrument / Medical Device", "Feed Supplement", "Others",
)

# (type_name, patterns on NAME field, patterns on UNIT/PACK fields, patterns on ANY combined text, base_score)
_TYPE_RULES: List[Tuple[str, List[str], List[str], List[str], int]] = [
    (
        "Instrument / Medical Device",
        [
            r"\bsyringe\b", r"\bneedle\b", r"\bgloves?\b", r"\bcatheter\b",
            r"\bscalpel\b", r"\bbandage\b", r"\bsurgical\b", r"\bequipment\b",
            r"\binstrument\b", r"\bthermometer\b", r"\bforceps\b",
            r"\bmedical\s*device\b",
        ],
        [],
        [],
        95,
    ),
    (
        "Injection - Vial",
        [r"\bvial\b", r"\binj\.?\s*vial\b"],
        [r"\bvial\b", r"\biv\b"],
        [r"\bvial\b"],
        90,
    ),
    (
        "Injection",
        [
            r"\binj\b", r"\binjection\b", r"\bamp\b", r"\bampoule\b",
            r"\bamoule\b", r"\bim\b", r"\biv\b",
        ],
        [r"\binj\b", r"\bamp\b", r"\bvial\b"],
        [r"\binjection\b"],
        85,
    ),
    (
        "Vaccine",
        [r"\bvaccine\b", r"\bvac\b", r"\bimmunization\b"],
        [r"\bvac\b", r"\bvial\b"],
        [],
        82,
    ),
    (
        "Eye Drops",
        [r"\beye\s*drops?\b", r"\beyedrop", r"\boptical\b"],
        [r"\beye\b"],
        [r"\beye\s*drop"],
        88,
    ),
    (
        "Ear Drops",
        [r"\bear\s*drops?\b", r"\beardrop"],
        [r"\bear\b"],
        [r"\bear\s*drop"],
        87,
    ),
    (
        "Nasal Drops",
        [r"\bnasal\s*drops?\b", r"\bnasal\s*drop"],
        [r"\bnasal\b"],
        [],
        86,
    ),
    (
        "Drops",
        [r"\bdrops?\b"],
        [r"\bdrop\b"],
        [],
        80,
    ),
    (
        "Suspension",
        [r"\bsuspension\b", r"\bsusp\b"],
        [r"\bsusp\b"],
        [],
        75,
    ),
    (
        "Sachet",
        [r"\bsachet\b", r"\bsach\b"],
        [r"\bsachet\b"],
        [],
        73,
    ),
    (
        "Cream",
        [r"\bcream\b", r"\bcrm\b"],
        [r"\bcream\b", r"\bcrm\b"],
        [],
        71,
    ),
    (
        "Lotion",
        [
            r"\blotion\b",
            r"\bface\s*wash\b", r"\bfacewash\b", r"\bface\s*was\b",
        ],
        [r"\blotion\b"],
        [],
        73,
    ),
    (
        "Spray",
        [r"\bspray\b"],
        [r"\bspray\b"],
        [],
        67,
    ),
    (
        "Shampoo",
        [r"\bshampoo\b", r"\bshmp\b"],
        [],
        [],
        65,
    ),
    (
        "Soap",
        [r"\bsoap\b"],
        [],
        [],
        63,
    ),
    (
        "Inhaler",
        [r"\binhaler\b", r"\binhal\b", r"\brotacap\b"],
        [],
        [],
        61,
    ),
    (
        "Feed Supplement",
        [r"\bfeed\s*supplement\b", r"\bsupplement\b", r"\bmineral\b"],
        [],
        [],
        59,
    ),
    (
        "Bolus",
        [
            r"\bbolus\b", r"\bbol\b", r"\bbls\b",
            r"\b1\s*['']s\b", r"\b2\s*['']s\b", r"\b4\s*['']s\b",
            r"\b1s\b", r"\b2s\b", r"\b4s\b",
        ],
        [r"\bbolus\b", r"\bbol\b", r"\bbls\b"],
        [],
        78,
    ),
    (
        "Capsule",
        [r"\bcaps?\b", r"\bcapsule\b", r"\bcapsules\b"],
        [r"\bcap\b", r"\bcaps\b"],
        [],
        76,
    ),
    (
        "Syrup",
        [r"\bsyrup\b"],
        [r"\bsyrup\b", r"\bbot\b", r"\bbottle\b"],
        [r"\bsyrup\b"],
        74,
    ),
    (
        "Liquid",
        [
            r"\bliquid\b", r"\bliq\b", r"\bsolution\b",
            r"\bsanitizer\b", r"\bsanitiser\b", r"\bhand\s*san\b",
            r"\bltr\b", r"\blitre\b", r"\bliter\b",
        ],
        [r"\bliq\b", r"\bml\b", r"\bltr\b", r"\bbot\b", r"\bbottle\b", r"\b\d+\s*m\b"],
        [r"\bliquid\b", r"\d+\s*ml\b", r"\d+\s*ltr\b"],
        72,
    ),
    (
        "Gel",
        [r"\bgel\b", r"\bjelly\b"],
        [r"\bgel\b"],
        [],
        70,
    ),
    (
        "Ointment",
        [r"\bointment\b", r"\boint\b"],
        [r"\boint\b"],
        [],
        68,
    ),
    (
        "Liniment",
        [r"\bliniment\b", r"\blin\b"],
        [r"\bliniment\b", r"\blin\b"],
        [],
        66,
    ),
    (
        "Granules",
        [r"\bgranules\b", r"\bgran\b", r"\bgrn\b"],
        [r"\bgranules\b", r"\bgran\b"],
        [],
        64,
    ),
    (
        "Powder",
        [r"\bpowder\b", r"\bpwd\b", r"\bpowd\b", r"\bpow\b", r"\belectrolyte\b"],
        [r"\bgm\b", r"\bgram\b", r"\bgrams\b", r"\bkg\b", r"\bpowder\b", r"\bpwd\b", r"\bpowd\b"],
        [r"\d+\s*g\b", r"\d+\s*gm\b", r"\d+\s*kg\b"],
        62,
    ),
    (
        "Tablet",
        [
            r"\btab\b", r"\btabs\b", r"\btablet\b", r"\btablets\b",
            r"\bst\b", r"\bs\s*['']t\b", r"\bstrip\b", r"\bstrips\b",
        ],
        [r"\btab\b", r"\bst\b", r"\bstrip\b", r"\bstrips\b"],
        [],
        60,
    ),
]

_LOW_CONFIDENCE_THRESHOLD = 25
_DETECTION_OVERRIDE_THRESHOLD = 68.0

# Pack like 60GM / 100ML — size notation, not a dosage-form unit hint.
_DIMENSIONAL_PACK_RE = re.compile(
    r"^\d+(?:\.\d+)?\s*(GM|G|MG|ML|MD|KG|L)$",
    re.IGNORECASE,
)

# Truncated OCR / cut bill text for cosmetics (facewash name often incomplete).
_FACEWASH_NAME_PATTERNS = (
    r"FACEWASH", r"FACWASH", r"FACE\s*WASH", r"FACE\s*WAS", r"FACEW",
    r"FACW\b", r"FAC\s*W\b", r"\bFW\b", r"\bFW\.", r"FACE\s*W\b",
    r"CHARCOAL\s*FACE", r"NEEM\s*FACE", r"NEEMWAY\s*FACE",
    r"\bFACE$", r"\bFACE\s*W$", r"GLOW.*FW", r"LOVELY\s*FW",
)


def _normalize_text(*parts: Any) -> str:
    chunks = []
    for p in parts:
        if p is None:
            continue
        t = str(p).strip()
        if t:
            chunks.append(t)
    text = " ".join(chunks).lower()
    text = text.replace(".", " ")
    text = re.sub(r"[_/\\|]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _pack_for_unit_scoring(pack: str) -> str:
    """Ignore 60GM / 100ML style pack sizes when inferring form from units."""
    p = (pack or "").strip()
    if _DIMENSIONAL_PACK_RE.match(p.replace(" ", "")):
        return ""
    return p


def _looks_like_topical_pack(pack: str) -> bool:
    return bool(_DIMENSIONAL_PACK_RE.match((pack or "").strip().replace(" ", "")))


# Dosage form read from the product name. Word-anchored so a brand like
# "DROPZ" or "CAPTOPRIL" cannot be mistaken for a form.
_NAME_FORM_RULES = (
    (r"\bEYE\s*DROPS?\b|\bE/?D\b", "Eye Drops", 92.0),
    (r"\bEAR\s*DROPS?\b", "Ear Drops", 92.0),
    (r"\bNASAL\s*(DROPS?|SPRAY)\b", "Nasal Drops", 92.0),
    (r"\bVIAL\b", "Injection - Vial", 90.0),
    (r"\b(INJ|INJECTION|INJEC)\b|\bINJ$", "Injection", 92.0),
    (r"\b(VACC?|VACCINE)\b", "Vaccine", 90.0),
    (r"\b(SYP|SYRUP|SYRP)\b|\bSYP$", "Syrup", 92.0),
    (r"\b(SUSP|SUSPENSION)\b", "Suspension", 90.0),
    (r"\b(LIQ|LIQUID)\b|\bLIQ$", "Liquid", 90.0),
    (r"\b(TAB|TABS|TABLET|TABLETS)\b|\bTAB$", "Tablet", 92.0),
    (r"\b(CAP|CAPS|CAPSULE|CAPSULES)\b|\bCAP$", "Capsule", 92.0),
    (r"\b(BOLUS|BOL)\b", "Bolus", 90.0),
    (r"\b(SACHET|SACHETS|SACH)\b", "Sachet", 88.0),
    (r"\b(POWDER|PWD|PDR)\b", "Powder", 88.0),
    (r"\bGRANULES?\b", "Granules", 88.0),
    (r"\bLINIMENT\b", "Liniment", 88.0),
    (r"\bINHALER\b", "Inhaler", 88.0),
    (r"\bSPRAY\b", "Spray", 86.0),
    (r"\bDROPS?\b", "Drops", 84.0),
)


def _detect_bill_form_type(name: str, pack: str = "") -> Tuple[str, float]:
    """
    High-confidence form from bill product name (handles truncated OCR text).
    Used before generic rules so cut facewash / cream / gel names classify correctly.
    """
    n = _normalize_text(name).upper()
    if not n:
        return "", 0.0

    for pat in _FACEWASH_NAME_PATTERNS:
        if re.search(pat, n, re.IGNORECASE):
            return "Lotion", 88.0

    if re.search(r"\bCREAM\b|\bCRM\b|CREAM$", n):
        return "Cream", 92.0
    if re.search(r"\bGEL\b|GEL$", n):
        return "Gel", 92.0
    if re.search(r"\bLOTION\b", n):
        return "Lotion", 90.0
    if re.search(r"\bOINTMENT\b|\bOINT\b", n):
        return "Ointment", 90.0
    if re.search(r"\bSHAMPOO\b|\bSHMP\b", n):
        return "Shampoo", 88.0
    if re.search(r"\bSOAP\b", n):
        return "Soap", 88.0
    if re.search(r"\bSANITIZER\b|\bSANITISER\b|\bHAND\s*SAN\b", n):
        return "Liquid", 88.0

    has_oral = bool(re.search(
        r"\b(TAB|TABS|TABLET|CAP|CAPS|CAPSULE|BOLUS|BOL|SYP|SYRUP|INJ|INJECTION|DROP)\b",
        n,
    ))
    if _looks_like_topical_pack(pack) and not has_oral:
        if re.search(r"\b(FACE|FACW|CHARCOAL|NEEM|FW|WASH|CLEAN|SCRUB)\b", n):
            return "Lotion", 78.0

    # The dosage form printed on the product name. On a supplier bill this is
    # the most reliable signal there is -- and it was missing here, so anything
    # measured in ML or LIT fell through to the pack rules and came back as
    # "Liquid". A 30ML injection, a 500ML syrup and a 5LIT liquid all read the
    # same, which is what made every imported row say Liquid.
    #
    # Ordered most specific first: EYE DROPS before DROPS, VIAL before INJ.
    for pattern, med_type, conf in _NAME_FORM_RULES:
        if re.search(pattern, n):
            return med_type, conf

    return "", 0.0


def _score_patterns(text: str, patterns: Sequence[str], weight: float) -> float:
    if not text:
        return 0.0
    score = 0.0
    for pat in patterns:
        if re.search(pat, text, re.IGNORECASE):
            score += weight
    return score


def classify_medicine_type(
    name: str = "",
    pack: str = "",
    qty_unit: str = "",
    pkg_unit: str = "",
    bill_text: str = "",
    available_types: Optional[Iterable[str]] = None,
) -> Tuple[str, float]:
    """
    Classify medicine type from name + units + bill context.
    Returns (canonical_type, confidence_score).
    """
    bill_form, bill_conf = _detect_bill_form_type(name, pack)
    if bill_form and bill_conf >= _DETECTION_OVERRIDE_THRESHOLD:
        matched = match_type_to_available(bill_form, available_types) or bill_form
        return matched, bill_conf

    from core.bill_import_normalize import pack_is_volume_or_weight
    if pack_is_volume_or_weight(pack):
        matched = match_type_to_available("Liquid", available_types) or "Liquid"
        return matched, 80.0

    name_t = _normalize_text(name)
    unit_t = _normalize_text(
        qty_unit,
        pkg_unit,
        _pack_for_unit_scoring(pack),
    )
    combined = _normalize_text(
        name,
        _pack_for_unit_scoring(pack),
        qty_unit,
        pkg_unit,
        bill_text,
    )

    scores: Dict[str, float] = {}
    for type_name, name_pats, unit_pats, any_pats, base in _TYPE_RULES:
        hits = (
            _score_patterns(name_t, name_pats, 12.0)
            + _score_patterns(unit_t, unit_pats, 10.0)
            + _score_patterns(combined, any_pats, 6.0)
            + _score_patterns(combined, name_pats, 4.0)
            + _score_patterns(combined, unit_pats, 4.0)
        )
        if hits <= 0:
            continue
        scores[type_name] = float(base) + hits

    if not scores:
        if bill_form:
            matched = match_type_to_available(bill_form, available_types) or bill_form
            return matched, bill_conf
        fallback = match_type_to_available("Others", available_types) or "Others"
        return fallback, 0.0

    ranked = sorted(scores.items(), key=lambda x: (-x[1], x[0]))
    best_type, best_score = ranked[0]
    second_score = ranked[1][1] if len(ranked) > 1 else 0.0

    if best_score < _LOW_CONFIDENCE_THRESHOLD:
        if bill_form:
            matched = match_type_to_available(bill_form, available_types) or bill_form
            return matched, bill_conf
        fallback = match_type_to_available("Others", available_types) or "Others"
        return fallback, best_score

    # Ambiguous: two types close — prefer higher-priority (already sorted by score)
    if second_score and best_score - second_score < 8:
        pass

    matched = match_type_to_available(best_type, available_types)
    return matched or best_type, best_score


def match_type_to_available(
    med_type: str,
    available_types: Optional[Iterable[str]] = None,
) -> str:
    """Map canonical type to a value from layout med_types list."""
    wanted = (med_type or "").strip()
    if not wanted:
        return ""
    options = list(available_types or [])
    if not options:
        return wanted

    aliases = {
        "injection vial": "Injection - Vial",
        "injection - vial": "Injection - Vial",
        "instrument": "Instruments",
        "instruments": "Instruments",
        "other": "Others",
        "others": "Others",
        "cap": "Capsule",
        "capsule": "Capsule",
        "capsules": "Capsule",
        "drop": "Drops",
        "drops": "Drops",
    }
    key = wanted.lower()
    if key in aliases:
        wanted = aliases[key]

    for option in options:
        if str(option).lower() == wanted.lower():
            return str(option)

    # Avoid Cream/Gel/Lotion collapsing into Ointment via substring match.
    _EXACT_ONLY = frozenset({
        "cream", "gel", "lotion", "tablet", "capsule", "syrup", "liquid",
        "powder", "injection", "drops", "shampoo", "soap", "spray",
    })
    if key not in _EXACT_ONLY:
        wl = wanted.lower()
        for option in options:
            ol = str(option).lower()
            if wl in ol or ol in wl:
                return str(option)

    wl = wanted.lower()
    for option in options:
        if str(option).lower() == wl:
            return str(option)

    fallbacks = {
        "capsule": "Tablet",
        "drops": "Liquid",
        "syrup": "Syrup",
        "instruments": "Others",
        "others": "Others",
        "lotion": "Lotion",
        "cream": "Cream",
        "gel": "Gel",
    }
    if wl in fallbacks:
        fb = fallbacks[wl]
        for option in options:
            if str(option).lower() == fb.lower():
                return str(option)

    for option in options:
        if str(option).lower() == "others":
            return str(option)

    return wanted if wanted in options else ""


def _learned_path() -> str:
    if getattr(sys, "frozen", False):
        base = os.path.join(
            os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
            "VeterinaryApp",
        )
    else:
        base = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "config",
        )
    return os.path.join(base, "import_learned.json")


def load_learned_medicine_types() -> Dict[str, str]:
    try:
        path = _learned_path()
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            bucket = data.get("medicine_types") or {}
            if isinstance(bucket, dict):
                return {str(k).upper(): str(v) for k, v in bucket.items()}
    except Exception:
        pass
    return {}


def save_learned_medicine_type(name: str, med_type: str) -> None:
    if not name or not med_type:
        return
    path = _learned_path()
    try:
        data = {}
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        bucket = data.setdefault("medicine_types", {})
        bucket[name.strip().upper()] = med_type.strip()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=True)
    except Exception:
        pass


def lookup_stored_medicine_type(conn: Any, name: str) -> str:
    """Type from medicines / medicines_master / last purchase."""
    if not conn or not (name or "").strip():
        return ""
    try:
        from core.purchase_service import lookup_medicine_details

        details = lookup_medicine_details(conn, name.strip())
        return (details.get("type") or "").strip()
    except Exception:
        return ""


def resolve_medicine_type(
    conn: Any = None,
    name: str = "",
    pack: str = "",
    qty_unit: str = "",
    pkg_unit: str = "",
    bill_text: str = "",
    available_types: Optional[Iterable[str]] = None,
    *,
    use_learned: bool = True,
    save_learned: bool = True,
    prefer_name_detection: bool = False,
) -> str:
    """
    Resolve type: name detection (bill import) → DB/master → import_learned → classify.
    """
    clean_name = (name or "").strip()
    name_key = clean_name.upper()

    detected, conf = classify_medicine_type(
        name=clean_name,
        pack=pack,
        qty_unit=qty_unit,
        pkg_unit=pkg_unit,
        bill_text=bill_text,
        available_types=available_types,
    )
    if prefer_name_detection and conf >= _DETECTION_OVERRIDE_THRESHOLD:
        if use_learned and save_learned and name_key and detected:
            save_learned_medicine_type(name_key, detected)
        return detected

    if conn and clean_name:
        stored = lookup_stored_medicine_type(conn, clean_name)
        if stored:
            stored_matched = match_type_to_available(stored, available_types) or stored
            if prefer_name_detection and conf >= _DETECTION_OVERRIDE_THRESHOLD:
                if _stored_type_conflicts_with_detection(stored_matched, detected):
                    if use_learned and save_learned and name_key:
                        save_learned_medicine_type(name_key, detected)
                    return detected
            if use_learned and save_learned and name_key:
                save_learned_medicine_type(name_key, stored_matched)
            return stored_matched

    if use_learned and name_key:
        learned = load_learned_medicine_types().get(name_key)
        if learned:
            learned_matched = match_type_to_available(learned, available_types) or learned
            if prefer_name_detection and conf >= _DETECTION_OVERRIDE_THRESHOLD:
                if _stored_type_conflicts_with_detection(learned_matched, detected):
                    if save_learned and name_key:
                        save_learned_medicine_type(name_key, detected)
                    return detected
            return learned_matched

    if use_learned and save_learned and name_key and detected:
        save_learned_medicine_type(name_key, detected)
    return detected


def _stored_type_conflicts_with_detection(stored: str, detected: str) -> bool:
    """True when DB/learned type likely wrong vs strong name-based detection."""
    s = (stored or "").strip().lower()
    d = (detected or "").strip().lower()
    if not s or not d or s == d:
        return False
    topical = frozenset({"cream", "gel", "lotion", "shampoo", "soap", "ointment"})
    wrong_for_topical = frozenset({"tablet", "bolus", "capsule", "powder", "liquid"})
    if d in topical and s in wrong_for_topical:
        return True
    if d == "lotion" and s in {"tablet", "powder", "liquid", "ointment"}:
        return True
    if d in {"cream", "gel"} and s == "ointment":
        return True
    liquid_forms = frozenset({
        "liquid", "syrup", "suspension", "injection", "injection - vial",
        "drops", "eye drops", "ear drops", "nasal drops",
    })
    strip_forms = frozenset({"tablet", "bolus", "capsule", "tablet pack", "bolus pack"})
    if d in liquid_forms and s in strip_forms:
        return True
    if d in strip_forms and s in liquid_forms:
        return True
    return False


def detect_medicine_type(
    pack: Any = "",
    product_name: Any = "",
    qty_unit: Any = "",
    pkg_unit: Any = "",
    bill_text: Any = "",
    available_types: Optional[Iterable[str]] = None,
) -> str:
    """Backward-compatible wrapper used by purchase_importer."""
    med_type, _ = classify_medicine_type(
        name=str(product_name or ""),
        pack=str(pack or ""),
        qty_unit=str(qty_unit or ""),
        pkg_unit=str(pkg_unit or ""),
        bill_text=str(bill_text or ""),
        available_types=available_types,
    )
    return med_type


def _item_context_text(item: Any) -> Tuple[str, str, str]:
    """Extract qty unit, pkg unit, and extra bill text from an import item."""
    raw = getattr(item, "raw", None) or {}
    pack = str(getattr(item, "pack", "") or raw.get("pack") or "")
    qty_unit = str(raw.get("qty_unit") or raw.get("unit") or "")
    pkg_unit = str(raw.get("pkg_unit") or raw.get("pkg") or pack or "")
    if not qty_unit and pack:
        m = re.search(
            r"\b(tab|tabs|tablet|strip|st|cap|bolus|inj|ml|gm|g|kg|vial|bot|bottle)\b",
            pack,
            re.I,
        )
        if m:
            qty_unit = m.group(1)
    bill_parts = [
        getattr(item, "name", ""),
        pack,
        getattr(item, "content_drug", ""),
        raw.get("name", ""),
    ]
    bill_text = " ".join(str(p) for p in bill_parts if p)
    return qty_unit, pkg_unit, bill_text


def enrich_invoice_medicine_types(
    invoice: Any,
    conn: Any = None,
    available_types: Optional[Iterable[str]] = None,
    *,
    save_learned: bool = True,
) -> None:
    """Set medicine_type on each imported line using full detection pipeline."""
    if available_types is None:
        try:
            from core.layout_config import get_med_types

            available_types = get_med_types()
        except Exception:
            available_types = list(CANONICAL_TYPES)

    for item in getattr(invoice, "items", []) or []:
        raw = item.raw or {}
        if raw.get("medicine_type_locked"):
            continue
        if (
            (item.medicine_type or "").strip()
            and raw.get("medicine_type_source") == "column"
            and not raw.get("gemini_import")
        ):
            continue

        qty_u, pkg_u, bill_t = _item_context_text(item)
        bill_photo = bool(raw.get("gemini_import") or raw.get("hsn_chapter"))
        resolved = resolve_medicine_type(
            conn=conn,
            name=item.name,
            pack=item.pack or pkg_u,
            qty_unit=qty_u,
            pkg_unit=pkg_u,
            bill_text=bill_t,
            available_types=available_types,
            save_learned=bool(conn) and save_learned,
            prefer_name_detection=bill_photo,
        )
        item.medicine_type = resolved
        if item.raw is None:
            item.raw = {}
        item.raw["medicine_type_source"] = "detected"
        detected, conf = classify_medicine_type(
            name=item.name,
            pack=item.pack or pkg_u,
            qty_unit=qty_u,
            pkg_unit=pkg_u,
            bill_text=bill_t,
            available_types=available_types,
        )
        item.raw["medicine_type_confidence"] = round(conf, 1)
