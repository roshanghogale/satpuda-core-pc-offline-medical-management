"""Which of a bill's lines are already on the purchase it belongs to.

Store 127 held the same supplier bill twice, as purchases 35 and 36, one second apart: the
save only warned, and the second copy put the same goods on the shelf again. The bill number
is one purchase now, so re-importing the same invoice -- a second photo, a clearer photo, the
page that was missed the first time -- has to land ON that purchase instead of beside it.

That needs one question answered the same way everywhere: is this parsed line already there?

The rule
--------
A parsed line matches an existing line when the medicine name matches AND the batch matches,
both compared with spaces and punctuation taken out (the same name can come back as
"AMOXYRUM-LA", "AMOXYRUM LA" or "Amoxyrum  LA").

A blank batch on EITHER side is not enough on its own -- an imported photo often carries the
WITHOUT BATCH placeholder, and a shop's own line is sometimes typed without one, so matching
on the name alone would swallow a genuine second batch of the same medicine. So when either
batch is blank the line counts as already there only if the quantity and the expiry agree too.
That way the same line re-read from a clearer photo is recognised, while a real second batch
is appended.

Anything that does not match is appended as a new line. A line that DOES match is left exactly
as the shop saved it: its quantity, rate and free quantity are never touched from an import,
because the shop has usually already corrected them by hand.
"""
from __future__ import annotations

import re
from typing import Any, Optional

from core.save_warnings import PLACEHOLDER_BATCH, PLACEHOLDER_EXPIRY

_QTY = 0.001


def line_name_key(name: Any) -> str:
    """A medicine name as this comparison sees it: the stored form, then punctuation out."""
    from core.name_utils import normalize_medicine_name

    return re.sub(r"[^A-Z0-9]+", " ", normalize_medicine_name(str(name or ""))).strip()


def line_batch_key(batch: Any) -> str:
    """A batch as this comparison sees it. The import's placeholder counts as no batch."""
    text = re.sub(r"[^A-Z0-9]+", "", str(batch or "").strip().upper())
    if not text or text == re.sub(r"[^A-Z0-9]+", "", PLACEHOLDER_BATCH):
        return ""
    return text


def _expiry_key(raw: Any) -> str:
    """One shape for both expiry spellings: a saved line holds 2028-12-01, a bill prints 12/28."""
    text = str(raw or "").strip().upper()
    if not text or text == PLACEHOLDER_EXPIRY:
        return ""
    found = re.match(r"^(\d{4})-(\d{1,2})", text)
    if found:
        return f"{int(found.group(1)):04d}-{int(found.group(2)):02d}"
    found = re.match(r"^(\d{1,2})\s*[/-]\s*(\d{2}|\d{4})$", text)
    if found:
        year = int(found.group(2))
        year = year + 2000 if year < 100 else year
        return f"{year:04d}-{int(found.group(1)):02d}"
    return re.sub(r"[^A-Z0-9]+", "", text)


def _qty(line: Any) -> float:
    try:
        return round(float((line or {}).get("qty") or 0), 3)
    except (TypeError, ValueError):
        return 0.0


def _name(line: Any) -> str:
    row = line or {}
    return line_name_key(row.get("name") or row.get("medicine_name") or row.get("medicine"))


def _batch(line: Any) -> str:
    row = line or {}
    return line_batch_key(row.get("batch") or row.get("batch_no"))


def _expiry(line: Any) -> str:
    row = line or {}
    raw = row.get("expiry")
    if raw in (None, ""):
        raw = row.get("expiry_date")
    return _expiry_key(raw)


def lines_are_the_same(existing: dict, parsed: dict) -> bool:
    """Whether a parsed bill line is the line already saved. See the module note for the rule."""
    if not _name(existing) or _name(existing) != _name(parsed):
        return False
    eb, pb = _batch(existing), _batch(parsed)
    if eb and pb:
        return eb == pb
    # Blank batch on one side: the name alone would swallow a second batch of the same
    # medicine, so the count and the expiry have to agree as well.
    return abs(_qty(existing) - _qty(parsed)) <= _QTY and _expiry(existing) == _expiry(parsed)


def split_new_lines(
    existing_lines: Any, parsed_lines: Any
) -> tuple[list[dict], list[dict]]:
    """(the parsed lines missing from the purchase, the parsed lines already on it).

    Each saved line can answer for only one parsed line, so a bill that genuinely lists the
    same medicine and batch twice still contributes its second line.
    """
    saved = [row for row in (existing_lines or []) if isinstance(row, dict)]
    used: set[int] = set()
    missing: list[dict] = []
    already: list[dict] = []
    for row in parsed_lines or []:
        if not isinstance(row, dict) or not _name(row):
            continue
        hit: Optional[int] = None
        for idx, saved_row in enumerate(saved):
            if idx in used:
                continue
            if lines_are_the_same(saved_row, row):
                hit = idx
                break
        if hit is None:
            missing.append(row)
        else:
            used.add(hit)
            already.append(row)
    return missing, already


def merge_new_lines(existing_lines: Any, parsed_lines: Any) -> tuple[list[dict], int, int]:
    """The purchase's lines with only the missing parsed ones appended.

    Returns (lines, how many were appended, how many were already there). Nothing already on
    the purchase is edited -- an import may add lines, never rewrite them.
    """
    saved = [row for row in (existing_lines or []) if isinstance(row, dict)]
    missing, already = split_new_lines(saved, parsed_lines)
    return saved + missing, len(missing), len(already)
