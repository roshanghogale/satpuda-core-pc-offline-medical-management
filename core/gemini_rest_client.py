"""
Gemini REST API client — stdlib only (Win7 / PyInstaller safe).

Bill photo import uses this when google-generativeai is missing or too old
(Python 3.8 Win7 builds only ship 0.1.0rc1 without GenerativeModel).
"""
from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, List, Optional, Sequence, Union

from core.ssl_utils import configure_ssl_certificates, ssl_context

_API_BASE = "https://generativelanguage.googleapis.com/v1beta"

# Only flash-latest aliases — 2.0 / 2.5 models need paid quota on many keys.
ALLOWED_GEMINI_MODELS = (
    "gemini-flash-lite-latest",
    "gemini-flash-latest",
)

DEFAULT_VISION_MODELS = ALLOWED_GEMINI_MODELS
DEFAULT_TEXT_MODELS = ALLOWED_GEMINI_MODELS


def is_blocked_gemini_model(short: str) -> bool:
    """Allow only gemini-flash-lite-latest and gemini-flash-latest."""
    low = short.lower()
    return not any(allowed in low for allowed in ALLOWED_GEMINI_MODELS)

PartInput = Union[str, Dict[str, Any]]


def is_rest_client_available() -> bool:
    """REST client needs only stdlib + bundled CA certs."""
    return True


def _mime_for_path(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    return {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
        ".gif": "image/gif",
        ".bmp": "image/bmp",
        ".tif": "image/tiff",
        ".tiff": "image/tiff",
    }.get(ext, "image/jpeg")


def _image_part(path: str) -> Dict[str, Any]:
    with open(path, "rb") as handle:
        data = base64.b64encode(handle.read()).decode("ascii")
    return {
        "inline_data": {
            "mime_type": _mime_for_path(path),
            "data": data,
        }
    }


def _build_parts(parts: Sequence[PartInput]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for part in parts:
        if isinstance(part, str):
            if os.path.isfile(part):
                out.append(_image_part(part))
            else:
                out.append({"text": part})
        elif isinstance(part, dict):
            out.append(part)
    return out


def _extract_text(payload: Dict[str, Any]) -> str:
    if payload.get("error"):
        err = payload["error"]
        raise RuntimeError(
            err.get("message") or err.get("status") or "Gemini API error"
        )
    candidates = payload.get("candidates") or []
    if not candidates:
        raise RuntimeError("Gemini returned no candidates.")
    content = candidates[0].get("content") or {}
    texts = [
        str(p.get("text") or "")
        for p in (content.get("parts") or [])
        if "text" in p
    ]
    text = "".join(texts).strip()
    if not text:
        raise RuntimeError("Gemini returned an empty response.")
    return text


def _read_error_body(exc: urllib.error.HTTPError) -> str:
    try:
        raw = exc.read().decode("utf-8", errors="replace")
        data = json.loads(raw)
        if isinstance(data, dict) and data.get("error"):
            err = data["error"]
            return str(err.get("message") or err.get("status") or raw)
        return raw
    except Exception:
        return str(exc)


def generate_content(
    *,
    api_key: str,
    model: str,
    parts: Sequence[PartInput],
    temperature: float = 0.1,
    response_mime_type: str = "application/json",
    timeout: int = 180,
) -> str:
    """Call Gemini generateContent; return response text."""
    configure_ssl_certificates()
    body = {
        "contents": [{"parts": _build_parts(parts)}],
        "generationConfig": {
            "temperature": temperature,
            "responseMimeType": response_mime_type,
        },
    }
    url = "{}/models/{}:generateContent?key={}".format(
        _API_BASE, model, api_key,
    )
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(
            req, timeout=timeout, context=ssl_context(),
        ) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise RuntimeError(_read_error_body(exc)) from exc
    return _extract_text(payload)


def is_quota_or_limit_error(exc: Exception) -> bool:
    err = str(exc).lower()
    return any(tok in err for tok in (
        "quota", "resource_exhausted", "rate limit", "rate_limit",
        "limit reached", "limit:", "429", "too many requests",
    ))


def generate_with_model_fallback(
    *,
    api_key: str,
    parts: Sequence[PartInput],
    models: Sequence[str] = DEFAULT_VISION_MODELS,
    on_progress: Optional[Callable[[str], None]] = None,
    temperature: float = 0.1,
    response_mime_type: str = "application/json",
) -> str:
    """Try models in order until one returns text."""
    last_err: Optional[Exception] = None
    quota_hits = 0
    model_list = list(models) or list(DEFAULT_VISION_MODELS)

    for model_name in model_list:
        try:
            if on_progress:
                on_progress("Gemini AI ({})…".format(model_name))
            return generate_content(
                api_key=api_key,
                model=model_name,
                parts=parts,
                temperature=temperature,
                response_mime_type=response_mime_type,
            )
        except Exception as exc:
            last_err = exc
            if is_quota_or_limit_error(exc):
                quota_hits += 1
                if on_progress:
                    on_progress(
                        "{} limit reached — trying another model…".format(
                            model_name,
                        ),
                    )
                continue
            continue

    if quota_hits and quota_hits >= len(model_list):
        raise RuntimeError(
            "Gemini free-tier limit reached on all models (2.5 Flash, 2.0 Flash, etc.).\n"
            "Wait 1–2 minutes and retry, or tomorrow for daily quota reset.\n"
            "For unlimited use: enable billing at aistudio.google.com\n\n"
            "Last error: {}".format(last_err)
        )
    raise RuntimeError(
        "No Gemini model could complete this request.\n"
        "Check internet connection and API key in Settings → Import → Gemini AI.\n\n"
        "{}".format(last_err)
    )
