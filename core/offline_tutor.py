"""Bundled offline FAQ for Satpuda AI when internet or Gemini is unavailable."""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request
from functools import lru_cache
from typing import Any, Dict, List, Optional, Sequence, Tuple

from core.tutor_rules import refusal_for_question

OFFLINE_NOTE_EN = "Offline help (no internet)."
OFFLINE_NOTE_MR = "ऑफलाइन मदत (इंटरनेट नाही)."
OFFLINE_NOTE_HI = "ऑफलाइन सहायता (इंटरनेट नहीं है)."

_MIN_SCORE = 3.0


def _help_dir() -> str:
    if getattr(sys, "frozen", False):
        base = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
        return os.path.join(base, "docs", "app_help")
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(root, "docs", "app_help")


def faq_path() -> str:
    return os.path.join(_help_dir(), "offline_faq.json")


def offline_faq_loaded() -> bool:
    return os.path.isfile(faq_path())


@lru_cache(maxsize=1)
def _load_faq() -> List[Dict[str, Any]]:
    path = faq_path()
    if not os.path.isfile(path):
        return []
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return list(data.get("entries") or [])
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return []


def is_internet_available(*, timeout: float = 2.0) -> bool:
  try:
    urllib.request.urlopen("https://generativelanguage.googleapis.com/", timeout=timeout)
    return True
  except Exception:
    try:
      urllib.request.urlopen("https://www.google.com/generate_204", timeout=timeout)
      return True
    except Exception:
      return False


def detect_language(text: str) -> str:
    t = text or ""
    if not re.search(r"[\u0900-\u097F]", t):
        return "en"
    marathi_hints = (
        "कसे", "कशी", "काय", "मध्ये", "करायच", "करा", "येईल", "आहे", "नाही",
        "पुरवठादार", "औषध", "विक्री", "खरेदी",
    )
    low = t.lower()
    if any(h in t or h in low for h in marathi_hints):
        return "mr"
    hindi_hints = ("कैसे", "क्या", "में", "करें", "है", "नहीं", "खरीद", "बिक्री")
    if any(h in t for h in hindi_hints):
        return "hi"
    return "mr"


def _tokens(text: str) -> List[str]:
    return [w for w in re.findall(r"[a-z0-9]+|[\u0900-\u097F]+", (text or "").lower()) if len(w) > 1]


def _score_entry(question: str, screen_id: str, entry: Dict[str, Any]) -> Tuple[float, int]:
    q = (question or "").lower()
    qtok = set(_tokens(question))
    score = 0.0
    hits = 0
    screens = entry.get("screens") or []
    if screen_id and screen_id in screens:
        score += 4.0
    elif "overview" in screens:
        score += 0.5
    for kw in entry.get("keywords") or []:
        kwl = (kw or "").lower()
        if kwl and kwl in q:
            score += 3.0
            hits += 1
        elif kwl in qtok:
            score += 2.0
            hits += 1
    for trig in entry.get("triggers") or []:
        if trig and trig.lower() in q:
            score += 6.0
            hits += 2
    eid = (entry.get("id") or "").lower()
    topic_hints = (
        ("medicine", ("medicine", "औषध", "दवा", "औषधे", "medicine")),
        ("supplier", ("supplier", "पुरवठा", "पुरवठादार")),
        ("customer", ("customer", "ग्राहक", "कस्टमर")),
        ("payment", ("payment", "पेमेंट", "पेमेंट")),
        ("ledger", ("ledger", "लेजर")),
        ("export", ("export", "एक्सपोर्ट")),
        ("return", ("return", "रिटर्न", "परत")),
        ("reorder", ("reorder", "रीऑर्डर")),
        ("filter", ("filter", "फिल्टर")),
    )
    for tag, words in topic_hints:
        if tag in eid and any(w.lower() in q or w in (question or "") for w in words):
            score += 5.0
            hits += 1
    return score, hits


def match_offline_faq(question: str, screen_id: str = "overview") -> Optional[Dict[str, Any]]:
    entries = _load_faq()
    if not entries:
        return None
    best: Optional[Dict[str, Any]] = None
    best_score = 0.0
    best_hits = 0
    for entry in entries:
        s, hits = _score_entry(question, screen_id or "overview", entry)
        if s > best_score or (s == best_score and hits > best_hits):
            best_score = s
            best_hits = hits
            best = entry
    if best is None or best_score < _MIN_SCORE:
        return None
    return best


def format_offline_answer(entry: Dict[str, Any], lang: str) -> str:
    responses = entry.get("responses") or {}
    text = responses.get(lang) or responses.get("en") or ""
    text = (text or "").strip()
    if lang == "mr":
        note = OFFLINE_NOTE_MR
    elif lang == "hi":
        note = OFFLINE_NOTE_HI
    else:
        note = OFFLINE_NOTE_EN
    return f"{note}\n\n{text}".strip()


def ask_offline_faq(question: str, *, context: Optional[Dict] = None) -> Optional[str]:
    q = (question or "").strip()
    if not q:
        return None
    blocked = refusal_for_question(q)
    if blocked:
        return blocked
    screen_id = (context or {}).get("screen_id") or "overview"
    entry = match_offline_faq(q, screen_id)
    if not entry:
        lang = detect_language(q)
        fallback = {
            "en": "I could not match your question offline. Try keywords like supplier, medicine, purchase, sales, inventory, payment, or reconnect internet for full Satpuda AI.",
            "mr": "ऑफलाइन जुळणारा प्रश्न सापडला नाही. पुरवठादार, औषध, खरेदी, विक्री, स्टॉक, पेमेंट असे शब्द वापरून पुन्हा विचारा. पूर्ण Satpuda AI साठी इंटरनेट लावा.",
            "hi": "ऑफलाइन में सवाल मेल नहीं खाया। supplier, medicine, purchase, sales, stock, payment जैसे शब्द आज़माएं। पूरा Satpuda AI के लिए इंटरनेट चालू करें।",
        }
        note = OFFLINE_NOTE_MR if lang == "mr" else OFFLINE_NOTE_HI if lang == "hi" else OFFLINE_NOTE_EN
        return f"{note}\n\n{fallback.get(lang, fallback['en'])}"
    lang = detect_language(q)
    return format_offline_answer(entry, lang)


def offline_entry_count() -> int:
    return len(_load_faq())