"""One ordering for every medicine dropdown in the app.

Typing "m" used to list AMOXY, BECOSULES and CALPOL above MECOVET, because the
search matched the letter anywhere in the name and then sorted the whole lot
alphabetically. The shop types the first letter of what it wants, so what starts
with that letter belongs at the top.

The order is:

  1. names that START with what was typed          MECOVET, MELONEX
  2. names where a WORD starts with it             AMOXY M, VITAMIN M
  3. names that merely contain it somewhere        AMOXY, CALCIMAX
  4. alphabetical within each of those groups

Sales, Purchase, Inventory and the history pages all go through here, so a
medicine is found the same way wherever it is looked up.
"""
from __future__ import annotations

from typing import Any, Callable, Iterable, Sequence

# Ranks, low sorts first.
STARTS_WITH = 0
WORD_STARTS_WITH = 1
CONTAINS = 2
NO_MATCH = 3

# Characters that separate words in a medicine name, so "AMOXY-M" and
# "AMOXY 500 M" both count as having a word starting with "M".
_WORD_BREAKS = " -/(),.+&_*[]{}:;'\"\\|"


def match_rank(name: str, query: str) -> int:
    """How well ``name`` answers ``query``. Lower is better."""
    q = (query or "").strip().lower()
    if not q:
        return STARTS_WITH
    n = (name or "").strip().lower()
    if not n:
        return NO_MATCH
    if n.startswith(q):
        return STARTS_WITH
    # Every occurrence has to be looked at, not just the first. "AMOXY-M" hits
    # on the m of AMOXY before it reaches the M that is its own word, and taking
    # the first hit alone ranked it as a mere contains.
    pos = n.find(q)
    if pos < 0:
        return NO_MATCH
    while pos >= 0:
        if n[pos - 1] in _WORD_BREAKS:
            return WORD_STARTS_WITH
        pos = n.find(q, pos + 1)
    return CONTAINS


def sort_key(name: str, query: str) -> tuple:
    """Sort key putting the best matches first, alphabetical within each group."""
    return (match_rank(name, query), (name or "").strip().lower())


def rank_names(
    names: Iterable[str], query: str, *, limit: int = 0, keep_non_matching: bool = False
) -> list[str]:
    """Order plain names for a dropdown, dropping what does not match at all."""
    pool = {n for n in names if n}
    if not keep_non_matching and (query or "").strip():
        pool = {n for n in pool if match_rank(n, query) != NO_MATCH}
    out = sorted(pool, key=lambda n: sort_key(n, query))
    return out[:limit] if limit and limit > 0 else out


def rank_rows(
    rows: Sequence[Any],
    query: str,
    *,
    key: Callable[[Any], str] = lambda r: str((r or {}).get("name") or ""),
    limit: int = 0,
    keep_non_matching: bool = True,
) -> list[Any]:
    """Order rows for a dropdown, by their name field.

    Rows are kept by default: the caller's own WHERE clause has usually filtered
    already, and a row may qualify on its batch number rather than its name.
    """
    pool = list(rows or [])
    if not keep_non_matching and (query or "").strip():
        pool = [r for r in pool if match_rank(key(r), query) != NO_MATCH]
    out = sorted(pool, key=lambda r: sort_key(key(r), query))
    return out[:limit] if limit and limit > 0 else out


def order_by_sql(column: str, query: str) -> tuple[str, list[str]]:
    """The same ordering as SQL, for queries that page in the database.

    Returns (order_by_fragment, params) to splice after ORDER BY. Kept in step
    with match_rank above so the dropdown looks the same whichever path fills it.
    """
    q = (query or "").strip()
    if not q:
        return f"{column} COLLATE NOCASE", []
    esc = q.replace("%", r"\%").replace("_", r"\_")
    # The separators a medicine name actually uses: "AMOXY-M", "B/COMPLEX",
    # "VITAMIN M". Kept in step with _WORD_BREAKS above for the cases that
    # matter; SQL cannot test a whole character class in one LIKE.
    breaks = (" ", "-", "/", "(")
    whens = " ".join(
        f"WHEN {column} LIKE ? ESCAPE '\\' COLLATE NOCASE THEN 1"
        for _ in breaks
    )
    return (
        "CASE "
        f"  WHEN {column} LIKE ? ESCAPE '\\' COLLATE NOCASE THEN 0 "
        f"  {whens} "
        "  ELSE 2 "
        f"END, {column} COLLATE NOCASE"
    ), [f"{esc}%"] + [f"%{b}{esc}%" for b in breaks]
