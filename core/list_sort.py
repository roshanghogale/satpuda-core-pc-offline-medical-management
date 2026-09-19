"""Client-side sort options for history list pages."""
from __future__ import annotations

SORT_OPTIONS = (
    'Recent (newest first)',
    'Oldest first',
    'Bill No (high to low)',
    'Bill No (low to high)',
    'Alphabetic (A-Z)',
    'Alphabetic (Z-A)',
)

# Sorting the shelf, which is a different list with different columns.
# 'Best match' is the resting state: with an empty search box it IS plain A-Z,
# and while the shop is typing it keeps the ranking name_search_rank builds --
# which the browser used to throw away by re-sorting A-Z on every render.
INVENTORY_SORT_OPTIONS = (
    'Best match',
    'Alphabetic (A-Z)',
    'Alphabetic (Z-A)',
    'Stock (low to high)',
    'Stock (high to low)',
    'Expiry (soonest first)',
    'Expiry (latest first)',
)


def bill_sort_key(value) -> tuple:
    """Order bill numbers the way a shop reads them: 2, 10, 11 -- not 10, 11, 2.

    A number may carry a financial-year tag ("98/FY2026-27") or a supplier's
    letters ("INV 10573"), so the digits are pulled out and compared as a
    number, with the whole string breaking ties.
    """
    import re

    text = str(value or '').strip()
    digits = re.findall(r'\d+', text)
    return (int(digits[0]) if digits else -1, text.lower())


def sort_sales_history_rows(rows, sort_key: str):
    data = list(rows or [])
    key = (sort_key or '').strip()
    if key.startswith('Bill No'):
        return sorted(
            data,
            key=lambda r: bill_sort_key(r[1] if len(r) > 1 else ''),
            reverse='high to low' in key,
        )
    if key.startswith('Oldest'):
        return sorted(data, key=lambda r: (r[0] or '', r[15]))
    if 'Z-A' in key:
        return sorted(data, key=lambda r: (r[1] or '').lower(), reverse=True)
    if 'A-Z' in key:
        return sorted(data, key=lambda r: (r[1] or '').lower())
    return sorted(data, key=lambda r: (r[0] or '', r[15]), reverse=True)


def sort_purchase_history_rows(rows, sort_key: str):
    data = list(rows or [])
    key = (sort_key or '').strip()
    pid = lambda r: r[10] if len(r) > 10 else (r[8] if len(r) > 8 else 0)
    if key.startswith('Bill No'):
        # Purchase No is the first column; it is what the shop files by.
        return sorted(
            data,
            key=lambda r: bill_sort_key(r[0] if len(r) > 0 else ''),
            reverse='high to low' in key,
        )
    if key.startswith('Oldest'):
        return sorted(data, key=lambda r: (r[1] or '', pid(r)))
    if 'Z-A' in key:
        return sorted(data, key=lambda r: (r[2] or '').lower(), reverse=True)
    if 'A-Z' in key:
        return sorted(data, key=lambda r: (r[2] or '').lower())
    return sorted(data, key=lambda r: (r[1] or '', pid(r)), reverse=True)


def order_server_rows(
    rows,
    sort_key: str,
    *,
    no_key: str,
    date_key: str,
    name_key: str,
):
    """Order the dicts an Online read returns, before display rows are built.

    Online history came back in whatever order the server chose and nothing
    asked for another, so the Sort dropdown did nothing at all in the mode the
    shops actually run in. Sorting here -- on the source list rather than on the
    built rows -- keeps rows, row_styles and row_ids in step, since all three
    are appended together in the loop that follows.

    Ordering matches the offline branch exactly, so a shop sees the same list
    whichever mode it is in.
    """
    data = [r for r in (rows or []) if isinstance(r, dict)]
    rest = [r for r in (rows or []) if not isinstance(r, dict)]
    key = (sort_key or "").strip()

    def _ident(r):
        """Tie-break within a day by the number the shop reads.

        Two bills entered on the same date used to fall back to the row's
        internal id, so a bill typed earlier but numbered later sat in the
        middle of that day's bills instead of at the top.
        """
        serial = r.get("fy_serial")
        try:
            if serial not in (None, ""):
                return (int(serial), 0)
        except (TypeError, ValueError):
            pass
        no = r.get(no_key)
        if no not in (None, ""):
            return (bill_sort_key(no)[0], 0)
        try:
            return (int(r.get("id") or r.get("local_id") or 0), 0)
        except (TypeError, ValueError):
            return (0, 0)

    def _when(r) -> str:
        return str(r.get(date_key) or "")

    def _name(r) -> str:
        return str(r.get(name_key) or "").lower()

    if key.startswith("Bill No"):
        data.sort(
            key=lambda r: bill_sort_key(r.get(no_key)),
            reverse="high to low" in key,
        )
    elif key.startswith("Oldest"):
        data.sort(key=lambda r: (_when(r), _ident(r)))
    elif "Z-A" in key:
        data.sort(key=_name, reverse=True)
    elif "A-Z" in key:
        data.sort(key=_name)
    else:
        # Recent first. Offline gets this from its ORDER BY; Online has no
        # query to lean on, so it is spelled out here.
        data.sort(key=lambda r: (_when(r), _ident(r)), reverse=True)
    return data + rest


def order_inventory_rows(rows, sort_key: str, *, name, stock, expiry, query: str = ''):
    """Order the shelf before the display rows are built.

    Inventory cannot use order_server_rows: that one speaks 'Bill No' and
    'Oldest', and 'Stock (low to high)' would fall through into its date branch
    and sort medicines by date -- silently, plausibly, and wrongly.

    Sorting here -- on the SOURCE list rather than on the built rows -- keeps
    rows, row_styles and row_ids in step, since all three are appended together
    in the loop that follows. The browser used to sort the built rows and leave
    the ids and the styles behind, so a right-click opened another medicine's
    card, Delete named a third and hid a fourth, and the red row was whichever
    medicine used to sit in that slot.

    ``name`` / ``stock`` / ``expiry`` pull those fields off one row, so the
    Online dicts and the Offline SQL tuples go through the same ordering.
    """
    from core.name_search_rank import sort_key as _rank_key

    data = list(rows or [])
    key = (sort_key or '').strip()

    def _name(r) -> str:
        return str(name(r) or '').strip().lower()

    def _stock(r) -> float:
        try:
            return float(stock(r) or 0)
        except (TypeError, ValueError):
            return 0.0

    def _expiry(r):
        from core.desktop_pages_service import _parse_expiry

        return _parse_expiry(expiry(r))

    # Sort by name first and let the stable sort keep it: reversing a combined
    # key would flip the tie-break too, so medicines on equal stock would list
    # Z-A in one direction and A-Z in the other.
    if key.startswith('Stock'):
        data.sort(key=_name)
        data.sort(key=_stock, reverse='high to low' in key)
        return data
    if key.startswith('Expiry'):
        # A blank expiry is not "soonest" and it is not "latest" either -- it is
        # unknown, so it sits at the bottom whichever way the shop looks.
        dated = [r for r in data if _expiry(r) is not None]
        blank = [r for r in data if _expiry(r) is None]
        dated.sort(key=_name)
        dated.sort(key=_expiry, reverse='latest first' in key)
        blank.sort(key=_name)
        return dated + blank
    if 'Z-A' in key:
        data.sort(key=_name, reverse=True)
        return data
    if 'A-Z' in key:
        data.sort(key=_name)
        return data
    # Best match, and anything unrecognised: the same order the offline SQL gets
    # from order_by_sql, spelled out in Python so Online has one too. With an
    # empty search box every row ranks STARTS_WITH, so this IS plain A-Z.
    data.sort(key=lambda r: _rank_key(str(name(r) or ''), query))
    return data
