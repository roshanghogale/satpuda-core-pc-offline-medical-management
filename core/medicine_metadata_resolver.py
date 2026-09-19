"""
Resolve medicine schedule and content_drug for bill import.

Order: store DB → learned cache → master catalog → Gemini (schedule + content) → fallback schedule.
"""
from __future__ import annotations

import json
import os
import re
import sys
from difflib import SequenceMatcher
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

ProgressCallback = Optional[Callable[[str], None]]

_SCHEDULE_STOP = frozenset({
    "NON-SCHEDULED", "NON SCHEDULED", "UNSCHEDULED", "NA", "N/A", "NONE", "OTC",
})

_TYPE_STOP = frozenset({
    "TAB", "TABS", "TABLET", "TABLETS", "CAP", "CAPS", "CAPSULE", "CAPSULES",
    "SYP", "SYRUP", "INJ", "INJECTION", "CREAM", "GEL", "LOTION", "OINT",
    "OINTMENT", "DROP", "DROPS", "POW", "POWDER", "SUSP", "SUSPENSION",
    "BOLUS", "BOL", "ML", "GM", "MG", "KG", "MD", "PIC", "PCS", "PC",
})

_GENERIC_NAME_TOKENS = frozenset({
    "HAND", "SANITIZER", "SANITISER", "FACE", "WASH", "FACWASH", "FACEWASH",
    "CHARCOAL", "CREAM", "GEL", "LOTION", "SOAP", "SHAMPOO", "POWDER",
    "SYRUP", "SUSPENSION", "SOLUTION", "DROP", "DROPS", "TABLET", "CAPSULE",
    "NEW", "FORTE", "PLUS", "FRESH", "CLEAN", "CARE", "HEALTH", "MEDIC",
    "MEDICAL", "VET", "VETERINARY", "ORG", "ORGANIC", "NATURAL",
})


def _config_dir() -> str:
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
    os.makedirs(base, exist_ok=True)
    return base


def _learned_path() -> str:
    return os.path.join(_config_dir(), "import_learned.json")


def _normalize_name(name: str) -> str:
    text = re.sub(r"[^\w\s]", " ", (name or "").upper())
    return re.sub(r"\s+", " ", text).strip()


def _name_tokens(name: str) -> List[str]:
    tokens = []
    for tok in _normalize_name(name).split():
        if len(tok) < 2 or tok in _TYPE_STOP:
            continue
        if tok.isdigit():
            continue
        tokens.append(tok)
    return tokens


def _primary_search_key(name: str) -> str:
    tokens = _name_tokens(name)
    if not tokens:
        return (name or "").strip()[:12]
    return tokens[0]


def _is_generic_only_name(name: str) -> bool:
    tokens = _name_tokens(name)
    return bool(tokens) and all(tok in _GENERIC_NAME_TOKENS for tok in tokens)


def _has_brand_token(name: str) -> bool:
    tokens = _name_tokens(name)
    return any(tok not in _GENERIC_NAME_TOKENS for tok in tokens)


def load_learned_metadata() -> Dict[str, Dict[str, str]]:
    path = _learned_path()
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        bucket = data.get("medicine_metadata") or {}
        if isinstance(bucket, dict):
            return {
                str(k).upper(): {
                    "schedule": str(v.get("schedule") or "").strip(),
                    "content_drug": str(v.get("content_drug") or "").strip(),
                }
                for k, v in bucket.items()
                if isinstance(v, dict)
            }
    except Exception:
        pass
    return {}


def save_learned_metadata(name: str, schedule: str = "", content_drug: str = "") -> None:
    key = (name or "").strip().upper()
    if not key:
        return
    sched = (schedule or "").strip()
    content = (content_drug or "").strip()
    if not sched and not content:
        return
    path = _learned_path()
    try:
        data: Dict[str, Any] = {}
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        bucket = data.setdefault("medicine_metadata", {})
        entry = bucket.setdefault(key, {})
        if sched:
            entry["schedule"] = sched
        if content:
            entry["content_drug"] = content
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=True)
    except Exception:
        pass


def _valid_schedule(value: str) -> str:
    sched = (value or "").strip()
    if not sched:
        return ""
    if sched.upper() in _SCHEDULE_STOP:
        return ""
    try:
        from core.layout_config import get_configured_schedules

        allowed = {s.upper() for s in get_configured_schedules()}
    except Exception:
        allowed = {"", "H", "H1", "X", "G", "K", "C", "C1", "P", "N", "M"}
    if sched.upper() in allowed:
        return sched.upper() if sched.upper() != "" else ""
    return ""


def lookup_master_details_fuzzy(
    name: str,
    pack: str = "",
    med_type: str = "",
) -> Dict[str, object]:
    """Best-effort master catalog match (387k+ medicines with salt/content)."""
    from core.master_medicine_service import lookup_master_details, search_master_names

    clean = (name or "").strip()
    if not clean:
        return {}

    exact = lookup_master_details(clean)
    if (exact.get("content_drug") or "").strip():
        return exact

    if _is_generic_only_name(clean):
        return {}

    keys = []
    primary = _primary_search_key(clean)
    if primary:
        keys.append(primary)
    for tok in _name_tokens(clean)[:2]:
        if tok not in keys:
            keys.append(tok)

    candidates: List[str] = []
    seen = set()
    for key in keys:
        for hit in search_master_names(key, limit=25):
            low = hit.lower()
            if low not in seen:
                seen.add(low)
                candidates.append(hit)

    if not candidates:
        return {}

    name_norm = _normalize_name(clean)
    name_toks = set(_name_tokens(clean))
    pack_u = (pack or "").upper()
    type_u = (med_type or "").upper()
    brand = _primary_search_key(clean) if _has_brand_token(clean) else ""

    best_name = ""
    best_score = 0.0
    best_details: Dict[str, object] = {}
    for cand in candidates:
        details = lookup_master_details(cand)
        content = (details.get("content_drug") or "").strip()
        if not content:
            continue
        cand_norm = _normalize_name(cand)
        ratio = SequenceMatcher(None, name_norm, cand_norm).ratio()
        cand_toks = set(_name_tokens(cand))
        overlap = 0.0
        if name_toks:
            overlap = len(name_toks & cand_toks) / len(name_toks)
        score = ratio * 0.55 + overlap * 0.45
        if brand and brand in cand_norm:
            score += 0.15
        elif brand and brand not in cand_norm:
            score -= 0.25
        if type_u and type_u in cand_norm:
            score += 0.05
        if pack_u and pack_u in cand_norm.replace(" ", ""):
            score += 0.05
        if score > best_score:
            best_score = score
            best_name = cand
            best_details = dict(details)

    if best_score < 0.58 or not best_name:
        return {}
    best_details["_master_match"] = best_name
    return best_details


def resolve_medicine_metadata(
    name: str,
    conn: Any = None,
    *,
    med_type: str = "",
    pack: str = "",
    manufacturer: str = "",
    use_gemini: bool = False,
    trust_learned_schedule: bool = True,
    on_progress: ProgressCallback = None,
) -> Dict[str, str]:
    """Return schedule and content_drug from all available sources."""
    clean = (name or "").strip()
    result = {"schedule": "", "content_drug": ""}
    if not clean:
        return result

    key = clean.upper()
    learned = load_learned_metadata().get(key, {})
    if trust_learned_schedule and learned.get("schedule"):
        result["schedule"] = _valid_schedule(learned["schedule"])
    if learned.get("content_drug"):
        result["content_drug"] = learned["content_drug"]

    if conn:
        try:
            from core.purchase_service import lookup_medicine_details

            details = lookup_medicine_details(conn, clean)
            if not result["schedule"]:
                result["schedule"] = _valid_schedule(details.get("schedule") or "")
            if not result["content_drug"]:
                result["content_drug"] = (details.get("content_drug") or "").strip()
        except Exception:
            pass

    if not result["content_drug"]:
        try:
            master = lookup_master_details_fuzzy(clean, pack=pack, med_type=med_type)
            content = (master.get("content_drug") or "").strip()
            if content:
                result["content_drug"] = content
        except Exception:
            pass

    if use_gemini and (not result["content_drug"] or not result["schedule"]):
        try:
            from core.gemini_medicine_metadata import infer_medicine_metadata_batch

            batch = infer_medicine_metadata_batch(
                [{
                    "name": clean,
                    "medicine_type": med_type,
                    "pack": pack,
                    "manufacturer": manufacturer,
                }],
                on_progress=on_progress,
            )
            gm = batch.get(key) or {}
            if not result["schedule"] and gm.get("schedule"):
                result["schedule"] = _valid_schedule(gm["schedule"])
            if not result["content_drug"] and gm.get("content_drug"):
                result["content_drug"] = (gm["content_drug"] or "").strip()
            if result["schedule"] or result["content_drug"]:
                save_learned_metadata(clean, result["schedule"], result["content_drug"])
        except Exception:
            pass

    return result


def _schedule_from_store_db(name: str, conn: Any) -> str:
    """Schedule from an existing inventory row only — never guessed."""
    if not conn:
        return ""
    try:
        from core.purchase_service import lookup_medicine_details

        details = lookup_medicine_details(conn, name)
        return _valid_schedule(details.get("schedule") or "")
    except Exception:
        return ""


def _apply_import_fallback_schedule(item: Any, conn: Any = None) -> None:
    """Last resort when Gemini did not answer — store DB, then configured fallback."""
    if (getattr(item, "schedule", "") or "").strip():
        return
    name = (getattr(item, "name", "") or "").strip()
    if not name:
        return
    existing = _schedule_from_store_db(name, conn)
    if existing:
        item.schedule = existing
        return
    from core.gemini_bill_config import load_import_default_schedule

    default = _valid_schedule(load_import_default_schedule())
    if default:
        item.schedule = default


def enrich_invoice_item_metadata(
    invoice: Any,
    conn: Any = None,
    *,
    use_gemini: bool = True,
    on_progress: ProgressCallback = None,
) -> None:
    """Fill schedule (Gemini + fallback) and content_drug on every imported line."""
    from core.build_features import is_gemini_supported

    if not is_gemini_supported():
        use_gemini = False

    items = list(getattr(invoice, "items", []) or [])
    if not items:
        return

    for item in items:
        name = (getattr(item, "name", "") or "").strip()
        if not name:
            continue
        if not use_gemini:
            db_sched = _schedule_from_store_db(name, conn)
            if db_sched:
                item.schedule = db_sched
        meta = resolve_medicine_metadata(
            name,
            conn,
            med_type=(getattr(item, "medicine_type", "") or "").strip(),
            pack=(getattr(item, "pack", "") or "").strip(),
            manufacturer=(getattr(item, "manufacturer", "") or "").strip(),
            use_gemini=False,
            trust_learned_schedule=not use_gemini,
        )
        if not (getattr(item, "content_drug", "") or "").strip() and meta.get("content_drug"):
            item.content_drug = meta["content_drug"]
        if not use_gemini and not (getattr(item, "schedule", "") or "").strip() and meta.get("schedule"):
            item.schedule = meta["schedule"]

    if not use_gemini:
        for item in items:
            _apply_import_fallback_schedule(item, conn)
        return

    try:
        from core.gemini_bill_config import is_gemini_configured, is_gemini_enabled

        if not (is_gemini_enabled() and is_gemini_configured()):
            for item in items:
                _apply_import_fallback_schedule(item, conn)
            return
    except Exception:
        for item in items:
            _apply_import_fallback_schedule(item, conn)
        return

    try:
        from core.gemini_medicine_metadata import infer_medicine_metadata_batch
    except Exception:
        for item in items:
            _apply_import_fallback_schedule(item, conn)
        return

    payload = [
        {
            "name": (it.name or "").strip(),
            "medicine_type": (getattr(it, "medicine_type", "") or "").strip(),
            "pack": (getattr(it, "pack", "") or "").strip(),
            "manufacturer": (getattr(it, "manufacturer", "") or "").strip(),
        }
        for it in items
        if (it.name or "").strip()
    ]
    if not payload:
        return

    if on_progress:
        on_progress("Gemini AI — looking up schedule & content (Google Search)…")

    try:
        batch = infer_medicine_metadata_batch(payload, on_progress=on_progress)
    except Exception:
        batch = {}

    gemini_answered: set = set()
    for item in items:
        key = (item.name or "").strip().upper()
        if not key:
            continue
        gm = batch.get(key)
        if gm is None:
            continue
        gemini_answered.add(key)
        item.schedule = _valid_schedule(gm.get("schedule") or "")
        content = (gm.get("content_drug") or "").strip()
        if content and not (getattr(item, "content_drug", "") or "").strip():
            item.content_drug = content
        save_learned_metadata(item.name, item.schedule, item.content_drug)

    for item in items:
        key = (getattr(item, "name", "") or "").strip().upper()
        if key in gemini_answered:
            continue
        _apply_import_fallback_schedule(item, conn)
