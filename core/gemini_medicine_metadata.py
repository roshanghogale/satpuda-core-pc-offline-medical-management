"""
Infer medicine schedule and content_drug from product names via Gemini (text-only).
Uses Google Search grounding when google-genai is available for up-to-date lookups.
"""
from __future__ import annotations

import json
import re
from difflib import SequenceMatcher
from typing import Any, Callable, Dict, List, Optional, Sequence

from core.gemini_bill_config import load_gemini_api_key

ProgressCallback = Optional[Callable[[str], None]]

_METADATA_PROMPT = """You are an expert Indian pharmacy and veterinary medicine catalog assistant.

Look up each medicine's Indian CDSCO drug schedule and composition. Use Google Search when needed to verify the brand's active ingredients and schedule.

For each medicine return:
1. schedule — exact Indian drug schedule code (allowed codes below), or "" when non-scheduled / OTC / feed supplement / cosmetic / device
2. content_drug — active ingredients / salt composition

Allowed schedule codes: {allowed_schedules}

Indian schedule guide (decide from ACTIVE INGREDIENT / salt):
- "" (empty): probiotics, rumen bolus, mineral/vitamin supplements, feed supplements, diapers, devices, cosmetics — NO drug schedule
- H: Schedule H — common Rx medicines (many anthelmintics, antibiotics not in H1 list, etc.)
- H1: Schedule H1 — Rule 97 drugs (e.g. Ceftriaxone, Cefixime combos, many vet antibiotic bolus/injections with H1 molecules)
- X: narcotics / psychotropics
- G, K, C, C1, P, N, M — only when clearly applicable

Veterinary examples:
- RUMIPRO BOLUS (probiotic/rumen supplement) → schedule ""
- OLONE CEF PLUS BOLUS (Ceftriaxone + Sulbactam) → schedule "H1"
- MEGLOC ADVANCE BOLUS (Meloxicam + antibiotic combo bolus) → schedule "H1" when it contains H1-scheduled antibiotics
- ELECTROLYTE / CPE GEL / mineral powder → schedule ""

CRITICAL:
- Copy each "name" field EXACTLY as given in the input JSON — character for character.
- Return one object per input medicine, same order.
- Do NOT default everything to H — use "" for non-drug items and H1 when the salt requires it.

Return ONLY valid JSON array (no markdown):
[{{"name": "exact name from input", "schedule": "H1", "content_drug": "..."}}]
"""


def _allowed_schedule_list() -> List[str]:
    try:
        from core.layout_config import get_configured_schedules

        codes = [s for s in get_configured_schedules() if s is not None]
    except Exception:
        codes = ["H", "H1", "X", "G", "K", "C", "C1", "P", "N", "M"]
    display = ['"" (non-scheduled/OTC)'] + [c for c in codes if c]
    return display


def _normalize_schedule(value: str) -> str:
    raw = (value or "").strip()
    if not raw:
        return ""
    upper = raw.upper()
    stop = frozenset({
        "NON-SCHEDULED", "NON SCHEDULED", "UNSCHEDULED", "OTC", "NA", "N/A", "NONE",
    })
    if upper in stop:
        return ""
    m = re.match(r"^(?:SCHEDULE\s*)?([A-Z]\d?)$", upper)
    if m:
        upper = m.group(1)
    try:
        from core.layout_config import get_configured_schedules

        allowed = {s.upper() for s in get_configured_schedules()}
    except Exception:
        allowed = {"", "H", "H1", "X", "G", "K", "C", "C1", "P", "N", "M"}
    return upper if upper in allowed else ""


def _normalize_name_key(name: str) -> str:
    text = re.sub(r"[^\w\s]", " ", (name or "").upper())
    return re.sub(r"\s+", " ", text).strip()


def _align_results_to_inputs(
    chunk: Sequence[Dict[str, str]],
    rows: List[Dict[str, Any]],
) -> Dict[str, Dict[str, str]]:
    """Map Gemini rows back to input medicine names (exact, positional, then fuzzy)."""
    out: Dict[str, Dict[str, str]] = {}
    if not chunk:
        return out

    input_by_key = {row["name"].upper(): row["name"] for row in chunk}

    for row in rows:
        name = (row.get("name") or "").strip()
        if not name:
            continue
        canon = input_by_key.get(name.upper())
        if not canon:
            norm = _normalize_name_key(name)
            for key, orig in input_by_key.items():
                if _normalize_name_key(orig) == norm:
                    canon = orig
                    break
        if not canon:
            continue
        out[canon.upper()] = {
            "schedule": _normalize_schedule(str(row.get("schedule") or "")),
            "content_drug": str(row.get("content_drug") or "").strip(),
        }

    unmatched_inputs = [row for row in chunk if row["name"].upper() not in out]
    unmatched_rows = [
        row for row in rows
        if (row.get("name") or "").strip()
        and not any(
            _normalize_name_key((row.get("name") or "")) == _normalize_name_key(c["name"])
            for c in chunk
        )
    ]

    if len(unmatched_inputs) == len(rows) and len(rows) == len(chunk):
        for inp, row in zip(chunk, rows):
            key = inp["name"].upper()
            if key in out:
                continue
            out[key] = {
                "schedule": _normalize_schedule(str(row.get("schedule") or "")),
                "content_drug": str(row.get("content_drug") or "").strip(),
            }
        return out

    for inp in unmatched_inputs:
        inp_norm = _normalize_name_key(inp["name"])
        best_row = None
        best_score = 0.0
        for row in rows:
            resp_norm = _normalize_name_key(row.get("name") or "")
            if not resp_norm:
                continue
            score = SequenceMatcher(None, inp_norm, resp_norm).ratio()
            if score > best_score:
                best_score = score
                best_row = row
        if best_row is not None and best_score >= 0.72:
            out[inp["name"].upper()] = {
                "schedule": _normalize_schedule(str(best_row.get("schedule") or "")),
                "content_drug": str(best_row.get("content_drug") or "").strip(),
            }
    return out


def infer_medicine_metadata_batch(
    items: Sequence[Dict[str, str]],
    on_progress: ProgressCallback = None,
) -> Dict[str, Dict[str, str]]:
    """
    Batch infer schedule and content_drug from Gemini (+ Google Search when available).
    Returns dict keyed by UPPER(input name).
    """
    clean: List[Dict[str, str]] = []
    seen = set()
    for row in items:
        name = (row.get("name") or "").strip()
        if not name:
            continue
        key = name.upper()
        if key in seen:
            continue
        seen.add(key)
        clean.append({
            "name": name,
            "medicine_type": (row.get("medicine_type") or row.get("type") or "").strip(),
            "pack": (row.get("pack") or "").strip(),
            "manufacturer": (row.get("manufacturer") or row.get("mfg") or "").strip(),
        })
    if not clean:
        return {}

    api_key = load_gemini_api_key()
    if not api_key:
        return {}

    out: Dict[str, Dict[str, str]] = {}
    chunk_size = 20
    for start in range(0, len(clean), chunk_size):
        chunk = clean[start:start + chunk_size]
        if on_progress:
            if len(clean) > chunk_size:
                on_progress(
                    "Gemini AI — schedule & content ({}/{})…".format(
                        min(start + len(chunk), len(clean)), len(clean),
                    )
                )
            elif start == 0:
                on_progress("Gemini AI — looking up schedule & content (Google Search)…")
        rows = _call_gemini_metadata(chunk, api_key)
        out.update(_align_results_to_inputs(chunk, rows))
    return out


def _build_prompt(items: Sequence[Dict[str, str]]) -> str:
    return (
        _METADATA_PROMPT.format(allowed_schedules=", ".join(_allowed_schedule_list()))
        + "\n\nMedicines:\n"
        + json.dumps(list(items), ensure_ascii=False)
    )


def _call_gemini_metadata(
    items: Sequence[Dict[str, str]],
    api_key: str,
) -> List[Dict[str, Any]]:
    from core.ssl_utils import configure_ssl_certificates

    configure_ssl_certificates()
    prompt = _build_prompt(items)

    rows = _call_gemini_with_google_search(prompt, api_key)
    if rows:
        return rows
    return _call_gemini_legacy(prompt, api_key)


def _call_gemini_with_google_search(prompt: str, api_key: str) -> List[Dict[str, Any]]:
    """Gemini + Google Search grounding via google-genai SDK (live web lookup)."""
    try:
        from google import genai
        from google.genai import types
    except ImportError:
        return []

    client = genai.Client(api_key=api_key)
    models = (
        "gemini-flash-lite-latest",
        "gemini-flash-latest",
    )
    config = types.GenerateContentConfig(
        temperature=0.1,
        tools=[types.Tool(google_search=types.GoogleSearch())],
    )
    for model_name in models:
        try:
            response = client.models.generate_content(
                model=model_name,
                contents=prompt,
                config=config,
            )
            text = (getattr(response, "text", None) or "").strip()
            parsed = _parse_json_array(text)
            if parsed:
                return parsed
        except Exception as exc:
            if _is_quota_or_limit_error(exc):
                continue
            continue
    return []


def _call_gemini_legacy(prompt: str, api_key: str) -> List[Dict[str, Any]]:
    from core.gemini_bill_config import _sdk_supports_bill_import

    if _sdk_supports_bill_import():
        return _call_gemini_legacy_sdk(prompt, api_key)
    return _call_gemini_legacy_rest(prompt, api_key)


def _call_gemini_legacy_rest(prompt: str, api_key: str) -> List[Dict[str, Any]]:
    from core.gemini_rest_client import (
        DEFAULT_TEXT_MODELS,
        generate_with_model_fallback,
        is_quota_or_limit_error,
    )

    last_err = None
    for model_name in DEFAULT_TEXT_MODELS:
        try:
            text = generate_with_model_fallback(
                api_key=api_key,
                parts=[prompt],
                models=[model_name],
            )
            parsed = _parse_json_array(text)
            if parsed:
                return parsed
        except Exception as exc:
            last_err = exc
            if is_quota_or_limit_error(exc):
                continue
            continue

    if last_err:
        raise RuntimeError(str(last_err))
    return []


def _call_gemini_legacy_sdk(prompt: str, api_key: str) -> List[Dict[str, Any]]:
    import google.generativeai as genai

    genai.configure(api_key=api_key)
    models = _resolve_text_models(genai)
    if not models:
        models = (
            "gemini-flash-lite-latest",
            "gemini-flash-latest",
        )

    last_err = None
    for model_name in models:
        try:
            model = genai.GenerativeModel(
                model_name,
                generation_config=genai.GenerationConfig(
                    temperature=0.1,
                    response_mime_type="application/json",
                ),
            )
            response = model.generate_content(prompt)
            text = (response.text or "").strip()
            parsed = _parse_json_array(text)
            if parsed:
                return parsed
        except Exception as exc:
            last_err = exc
            if _is_quota_or_limit_error(exc):
                continue
            continue

    if last_err:
        raise RuntimeError(str(last_err))
    return []


def _is_quota_or_limit_error(exc: Exception) -> bool:
    err = str(exc).lower()
    return any(tok in err for tok in (
        "quota", "resource_exhausted", "rate limit", "rate_limit",
        "limit reached", "429", "too many requests",
    ))


def _resolve_text_models(genai) -> List[str]:
    from core.gemini_rest_client import is_blocked_gemini_model

    preferred = (
        "gemini-flash-lite-latest",
        "gemini-flash-latest",
    )
    available: List[str] = []
    try:
        for m in genai.list_models():
            methods = getattr(m, "supported_generation_methods", None) or []
            if "generateContent" not in methods:
                continue
            name = m.name or ""
            short = name.split("/", 1)[-1] if "/" in name else name
            if "gemini" not in short.lower():
                continue
            if any(skip in short.lower() for skip in ("tts", "embedding", "aqa", "preview-tts", "image")):
                continue
            available.append(short)
    except Exception:
        return []

    ordered: List[str] = []
    for pref in preferred:
        for short in available:
            if pref in short and short not in ordered:
                ordered.append(short)
    for short in available:
        if is_blocked_gemini_model(short):
            continue
        if short not in ordered:
            ordered.append(short)
    return ordered[:2]


def _parse_json_array(text: str) -> List[Dict[str, Any]]:
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\[[\s\S]*\]", text)
        if not m:
            return []
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError:
            return []
    if isinstance(data, dict):
        data = data.get("items") or data.get("medicines") or data.get("results") or []
    if not isinstance(data, list):
        return []
    return [row for row in data if isinstance(row, dict)]
