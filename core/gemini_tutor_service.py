"""Satpuda AI tutor: Gemini online when available, bundled offline FAQ fallback."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from core.gemini_bill_config import is_gemini_configured, load_gemini_api_key
from core.gemini_rest_client import DEFAULT_TEXT_MODELS, generate_with_model_fallback
from core.gemini_tutor_config import is_tutor_enabled, tutor_availability_message
from core.offline_tutor import ask_offline_faq, is_internet_available, offline_faq_loaded
from core.tutor_context import format_context_for_llm
from core.tutor_knowledge import load_docs_for_screen
from core.tutor_rules import (
    build_system_instructions,
    humanize_tutor_response,
    refusal_for_question,
    sanitize_tutor_response,
)

ProgressCallback = Optional[Callable[[str], None]]


@dataclass(frozen=True)
class TutorResult:
    text: str
    offline: bool = False


def _format_history(history: Sequence[Tuple[str, str]], *, limit: int = 8) -> str:
    lines: List[str] = []
    for role, text in list(history or [])[-limit:]:
        prefix = "User" if role == "user" else "Tutor"
        lines.append(f"{prefix}: {(text or '').strip()}")
    return "\n".join(lines)


def ask_tutor_gemini(
    question: str,
    *,
    context: Dict,
    history: Optional[Sequence[Tuple[str, str]]] = None,
    on_progress: ProgressCallback = None,
) -> str:
    if not is_gemini_configured():
        raise RuntimeError("Gemini API key is not configured.")

    q = (question or "").strip()
    if not q:
        raise ValueError("Please enter a question.")

    blocked = refusal_for_question(q)
    if blocked:
        return blocked

    api_key = load_gemini_api_key()
    if not api_key:
        raise RuntimeError("Gemini API key is not configured.")

    screen_id = (context or {}).get("screen_id") or "overview"
    docs = load_docs_for_screen(screen_id)
    ctx_text = format_context_for_llm(context or {})
    hist = _format_history(history or [])

    user_block = f"""USER CONTEXT:
{ctx_text}

SCREEN DOCUMENTATION:
{docs or '(none)'}

RECENT CHAT:
{hist or '(none)'}

USER QUESTION:
{q}"""

    parts = [build_system_instructions(), user_block]
    if on_progress:
        on_progress("Thinking...")

    raw = generate_with_model_fallback(
        api_key=api_key,
        parts=parts,
        models=DEFAULT_TEXT_MODELS,
        on_progress=on_progress,
        temperature=0.55,
        response_mime_type="text/plain",
    ).strip()

    return humanize_tutor_response(sanitize_tutor_response(raw))


def ask_tutor(
    question: str,
    *,
    context: Dict,
    history: Optional[Sequence[Tuple[str, str]]] = None,
    on_progress: ProgressCallback = None,
) -> TutorResult:
    """Online Gemini when possible; otherwise bundled multilingual offline FAQ."""
    if not is_tutor_enabled():
        raise RuntimeError(tutor_availability_message() or "Satpuda AI is disabled.")

    q = (question or "").strip()
    if not q:
        raise ValueError("Please enter a question.")

    blocked = refusal_for_question(q)
    if blocked:
        return TutorResult(text=blocked, offline=False)

    can_try_online = (
        is_gemini_configured()
        and is_internet_available()
        and not tutor_availability_message()
    )

    if can_try_online:
        try:
            text = ask_tutor_gemini(
                q,
                context=context,
                history=history,
                on_progress=on_progress,
            )
            return TutorResult(text=text, offline=False)
        except Exception:
            pass

    if offline_faq_loaded():
        offline = ask_offline_faq(q, context=context)
        if offline:
            return TutorResult(text=offline, offline=True)

    msg = tutor_availability_message()
    if msg:
        raise RuntimeError(msg)
    raise RuntimeError(
        "Could not answer offline and online Satpuda AI is unavailable. Check internet or Gemini key."
    )