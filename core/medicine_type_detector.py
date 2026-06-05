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
    "Tablet",
    "Bolus",
    "Capsule",
    "Syrup",
    "Liquid",
    "Powder",
    "Drops",
    "Injection",
    "Injection - Vial",
    "Gel",
    "Vaccine",
    "Ointment",
    "Liniment",
    "Granules",
    "Instruments",
    "Others",
)

# (type_name, patterns on NAME field, patterns on UNIT/PACK fields, patterns on ANY combined text, base_score)
_TYPE_RULES: List[Tuple[str, List[str], List[str], List[str], int]] = [
    (
        "Instruments",
        [
            r"\bsyringe\b", r"\bneedle\b", r"\bgloves?\b", r"\bcatheter\b",
            r"\bscalpel\b", r"\bbandage\b", r"\bsurgical\b", r"\bequipment\b",
            r"\binstrument\b", r"\bthermometer\b", r"\bforceps\b",
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
        "Drops",
        [
            r"\bdrops?\b", r"\beye\s*drop", r"\bear\s*drop", r"\bnasal\s*drop",
            r"\boptical\b",
        ],
        [r"\bdrop\b"],
        [],
        80,
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
        [r"\bsyrup\b", r"\bsuspension\b", r"\bsusp\b"],
        [r"\bsyrup\b", r"\bsusp\b", r"\bbot\b", r"\bbottle\b"],
        [r"\bsyrup\b"],
        74,
    ),
    (
        "Liquid",
        [
            r"\bliquid\b", r"\bliq\b", r"\bsolution\b", r"\bwash\b",
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
        [r"\bointment\b", r"\boint\b", r"\bcream\b", r"\bcrm\b"],
        [r"\boint\b", r"\bcrm\b"],
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
    name_t = _normalize_text(name)
    unit_t = _normalize_text(qty_unit, pkg_unit, pack)
    combined = _normalize_text(name, pack, qty_unit, pkg_unit, bill_text)

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
        fallback = match_type_to_available("Others", available_types) or "Tablet"
        return fallback, 0.0

    ranked = sorted(scores.items(), key=lambda x: (-x[1], x[0]))
    best_type, best_score = ranked[0]
    second_score = ranked[1][1] if len(ranked) > 1 else 0.0

    if best_score < _LOW_CONFIDENCE_THRESHOLD:
        fallback = match_type_to_available("Others", available_types) or "Tablet"
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

    # Fuzzy contains
    wl = wanted.lower()
    for option in options:
        ol = str(option).lower()
        if wl in ol or ol in wl:
            return str(option)

    # Capsule/Drops/Instruments/Others not in layout → Tablet or first fallback
    fallbacks = {
        "capsule": "Tablet",
        "drops": "Liquid",
        "syrup": "Syrup",
        "instruments": "Others",
        "others": options[0] if options else "Tablet",
    }
    if wl in fallbacks:
        fb = fallbacks[wl]
        for option in options:
            if str(option).lower() == fb.lower():
                return str(option)
        return options[0] if options else wanted

    return wanted if wanted in options else (options[0] if options else wanted)


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
) -> str:
    """
    Resolve type: DB/master → import_learned → intelligent detection.
    """
    clean_name = (name or "").strip()
    name_key = clean_name.upper()

    if conn and clean_name:
        stored = lookup_stored_medicine_type(conn, clean_name)
        if stored:
            matched = match_type_to_available(stored, available_types) or stored
            if use_learned and save_learned and name_key:
                save_learned_medicine_type(name_key, matched)
            return matched

    if use_learned and name_key:
        learned = load_learned_medicine_types().get(name_key)
        if learned:
            return match_type_to_available(learned, available_types) or learned

    detected, _conf = classify_medicine_type(
        name=clean_name,
        pack=pack,
        qty_unit=qty_unit,
        pkg_unit=pkg_unit,
        bill_text=bill_text,
        available_types=available_types,
    )
    if use_learned and save_learned and name_key and detected:
        save_learned_medicine_type(name_key, detected)
    return detected


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
) -> None:
    """Set medicine_type on each imported line using full detection pipeline."""
    if available_types is None:
        try:
            from core.layout_config import MED_TYPES

            available_types = list(MED_TYPES)
        except Exception:
            available_types = list(CANONICAL_TYPES)

    for item in getattr(invoice, "items", []) or []:
        if (item.raw or {}).get("medicine_type_locked"):
            continue
        if (item.medicine_type or "").strip() and (item.raw or {}).get(
            "medicine_type_source"
        ) == "column":
            continue

        qty_u, pkg_u, bill_t = _item_context_text(item)
        resolved = resolve_medicine_type(
            conn=conn,
            name=item.name,
            pack=item.pack or pkg_u,
            qty_unit=qty_u,
            pkg_unit=pkg_u,
            bill_text=bill_t,
            available_types=available_types,
            save_learned=bool(conn),
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
