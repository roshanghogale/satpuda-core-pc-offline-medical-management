"""Helpers for multi-page purchase bill photo import."""

from __future__ import annotations

import re
from typing import Any, Sequence

# Page-break / carry-forward markers seen on Indian pharmacy bills.
_CONTINUED_PATTERNS = (
    r"continued[\s.]*\d",
    r"\bcontinued\b",
    r"\bcont\.?\s*\d+\b",
    r"total\s+c/f",
    r"total\s+b/f",
    r"\bc/f\s*:",
    r"\bb/f\s*:",
    r"carried\s+forward",
    r"carry\s+forward",
    r"page\s+\d+\s+of",
    r"^page\s+\d+\b",
)


def looks_like_bill_continues(text: str) -> bool:
    """True when OCR/text suggests more pages follow (e.g. 'Continued....2')."""
    low = (text or "").strip().lower()
    if not low:
        return False
    for pat in _CONTINUED_PATTERNS:
        if re.search(pat, low, flags=re.I):
            return True
    return False


def is_bill_page_artifact_line(line: str) -> bool:
    """Lines that mark page breaks — must not merge into product rows."""
    low = (line or "").strip().lower()
    if not low:
        return True
    for pat in _CONTINUED_PATTERNS:
        if re.search(pat, low, flags=re.I):
            return True
    return False


def invoice_lines_mismatch_totals(invoice: Any) -> bool:
    """
    True when printed subtotal is much higher than summed line amounts —
    usually means only the last page (totals) or first page was imported.
    """
    gross = float(getattr(invoice, "gross_amount", 0) or 0)
    if gross <= 0:
        return False
    items = getattr(invoice, "items", None) or []
    line_sum = round(sum(float(getattr(it, "amount", 0) or 0) for it in items), 2)
    if line_sum <= 0:
        return False
    return gross > line_sum * 1.08


def invoice_may_need_more_pages(invoice: Any, page_count: int = 1) -> bool:
    """Heuristic: bill likely incomplete after scan (missing pages or partial OCR)."""
    del page_count  # reserved for future use (e.g. expected page count)
    raw = getattr(invoice, "raw_text", "") or ""
    if looks_like_bill_continues(raw):
        return True
    return invoice_lines_mismatch_totals(invoice)


def sort_bill_page_paths(paths: Sequence[str]) -> list:
    """
    Keep user order by default; if every filename ends with _1, _2, etc.,
    sort numerically so WhatsApp exports import in page order.
    """
    clean = [p for p in paths if p]
    if len(clean) <= 1:
        return list(clean)

    def _page_key(path: str):
        base = path.rsplit("\\", 1)[-1].rsplit("/", 1)[-1]
        stem = base.rsplit(".", 1)[0].lower()
        m = re.search(r"(\d+)\s*$", stem)
        if m:
            return (0, int(m.group(1)), path.lower())
        m = re.search(r"page[\s_-]*(\d+)", stem, flags=re.I)
        if m:
            return (0, int(m.group(1)), path.lower())
        return (1, 0, path.lower())

    keyed = [_page_key(p) for p in clean]
    if all(k[0] == 0 for k in keyed):
        return [p for _, p in sorted(zip(keyed, clean), key=lambda pair: pair[0])]
    return list(clean)
