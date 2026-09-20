"""One reading of an expiry date, for every way one arrives.

A shop loads stock from a phone. Nobody types the slash, because no field puts
it there for them, so the row says ``0428``. Four separate converters in this
product -- purchase_service, mobile_import_apply, desktop_inventory_service and
the classic import window -- each tested for a ``/`` and each threw away
anything without one. The medicine imported fine and its expiry was simply
gone: no error, no warning, an empty column in Inventory.

An expiry typed by a person is not a format, it is an intention. ``04/28``,
``4-28``, ``0428``, ``04.2028`` and ``2028-04`` all say April 2028, and a
pharmacy has no business losing stock dates over a punctuation mark.

Everything here is text in, text out, with no I/O, so it can be read against
real rows the shops have sent.
"""
from __future__ import annotations

import re

# The DB keeps the first of the month: a pack expires during its month, and the
# product has stored it that way since the beginning.
_DAY = "01"

# Two numbers with any of the separators people actually type, or none at all.
_SPLIT = re.compile(r"[\s/\-.\\|]+")
_DIGITS = re.compile(r"^\d+$")


def _year(text: str) -> int:
    """A 2- or 4-digit year as a full year, or 0 when it is neither."""
    if not _DIGITS.match(text):
        return 0
    if len(text) == 2:
        return 2000 + int(text)
    if len(text) == 4:
        year = int(text)
        return year if 1900 <= year <= 2199 else 0
    return 0


def _month(text: str) -> int:
    if not _DIGITS.match(text) or len(text) > 2:
        return 0
    month = int(text)
    return month if 1 <= month <= 12 else 0


def _from_pair(first: str, second: str) -> str:
    """MM + YY|YYYY, or the same two the other way round when only that reads."""
    month, year = _month(first), _year(second)
    if month and year:
        return "{:04d}-{:02d}-{}".format(year, month, _DAY)
    # "2028-04" and the rare "28/04": the month cannot be first, so it is second.
    month, year = _month(second), _year(first)
    if month and year:
        return "{:04d}-{:02d}-{}".format(year, month, _DAY)
    return ""


def expiry_to_db(raw) -> str:
    """Any expiry a person or a phone can produce -> ``YYYY-MM-01``.

    Returns "" when the text holds no month and year that can be read, so a
    caller can tell "no expiry given" from "April 2028" -- it never invents one.
    """
    text = str(raw or "").strip()
    if not text:
        return ""

    # Already a date, possibly with a time on the end.
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        head = text[:10]
        if _DIGITS.match(head[:4]) and _DIGITS.match(head[5:7]):
            return "{}-{}-{}".format(head[:4], head[5:7], _DAY)

    parts = [p for p in _SPLIT.split(text) if p]
    if len(parts) >= 3 and len(parts[0]) == 4:
        # YYYY-MM-DD in a shape the fast path above did not match.
        return _from_pair(parts[0], parts[1])
    if len(parts) >= 2:
        return _from_pair(parts[0], parts[1])

    # One run of digits and no separator at all: 0428, 042028, 202804.
    lone = parts[0] if parts else ""
    if not _DIGITS.match(lone):
        return ""
    if len(lone) == 4:
        return _from_pair(lone[:2], lone[2:])
    if len(lone) == 6:
        # 042028 (MMYYYY) reads first; 202804 (YYYYMM) is the fallback inside.
        return _from_pair(lone[:2], lone[2:]) or _from_pair(lone[:4], lone[4:])
    return ""


def expiry_display(raw) -> str:
    """``MM/YY`` for the screen -- the form every entry field in the product asks for."""
    db = expiry_to_db(raw)
    if not db:
        return ""
    return "{}/{}".format(db[5:7], db[2:4])
