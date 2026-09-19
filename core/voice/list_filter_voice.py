"""Voice commands to apply filters on Inventory, Sales History, and Purchase History."""
from __future__ import annotations

import calendar
import re
from datetime import date, timedelta

from core.voice.voice_log import voice_log

_PAGE_SALES = "sales_history"
_PAGE_PURCHASE = "purchase_history"
_PAGE_INVENTORY = "inventory"


def _norm(text: str) -> str:
    t = " ".join((text or "").lower().split())
    t = t.replace("financial year", "fy").replace("fin year", "fy")
    t = t.replace("aitch", "h")
    return t


def _strip_leads(t: str) -> str:
    for lead in (
        "show me the ", "show me ", "show the ", "show ", "display ",
        "filter by ", "filter ", "apply filter ", "list ", "find ",
        "please ", "kindly ", "i want ", "i need ",
    ):
        if t.startswith(lead):
            t = t[len(lead):].strip()
    return t


def _infer_page(t: str) -> str | None:
    if re.search(
        r"\b(sales? history|sale history|sales? bills?|sales? list|"
        r"customer bills?|billed sales?)\b",
        t,
    ):
        return _PAGE_SALES
    if re.search(r"\b(sales?|sale)\b", t) and not re.search(
        r"\b(purchase|inventory|stock|medicine)\b", t
    ):
        return _PAGE_SALES
    if re.search(
        r"\b(purchase history|purchases? history|purchase bills?|"
        r"purchase list|supplier bills?)\b",
        t,
    ):
        return _PAGE_PURCHASE
    if re.search(r"\b(purchases?|purchase)\b", t) and not re.search(
        r"\b(sales?|inventory|stock|medicine)\b", t
    ):
        return _PAGE_PURCHASE
    if re.search(
        r"\b(inventory|stock|medicines?|medicine list|stock list)\b", t
    ):
        return _PAGE_INVENTORY
    return None


def _parse_period(t: str) -> tuple[str, date, date] | None:
    today = date.today()

    if re.search(r"\b(today|todays?)\b", t):
        return "today", today, today
    if re.search(r"\b(yesterday|yesterdays?)\b", t):
        d = today - timedelta(days=1)
        return "yesterday", d, d
    if re.search(r"\b(this week|current week)\b", t):
        start = today - timedelta(days=today.weekday())
        return "this week", start, today
    if re.search(r"\b(last week|previous week)\b", t):
        end = today - timedelta(days=today.weekday() + 1)
        start = end - timedelta(days=6)
        return "last week", start, end
    if re.search(r"\b(this month|current month)\b", t):
        return "this month", today.replace(day=1), today
    if re.search(r"\b(last month|previous month)\b", t):
        first = today.replace(day=1)
        end = first - timedelta(days=1)
        return "last month", end.replace(day=1), end
    if re.search(r"\b(this year|current year|calendar year)\b", t):
        return "this year", today.replace(month=1, day=1), today
    if re.search(r"\b(last year|previous year)\b", t):
        y = today.year - 1
        return "last year", date(y, 1, 1), date(y, 12, 31)
    if re.search(
        r"\b(this fy|current fy|this financial year|current financial year)\b", t
    ) or (re.search(r"\b(this|current)\b", t) and re.search(r"\bfy\b", t)):
        if today.month >= 4:
            return "this FY", date(today.year, 4, 1), date(today.year + 1, 3, 31)
        return "this FY", date(today.year - 1, 4, 1), date(today.year, 3, 31)
    if re.search(r"\b(last fy|previous fy|last financial year)\b", t):
        if today.month >= 4:
            y = today.year - 1
        else:
            y = today.year - 2
        return "last FY", date(y, 4, 1), date(y + 1, 3, 31)

    months = {
        "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3,
        "april": 4, "apr": 4, "may": 5, "june": 6, "jun": 6,
        "july": 7, "jul": 7, "august": 8, "aug": 8, "september": 9, "sep": 9,
        "sept": 9, "october": 10, "oct": 10, "november": 11, "nov": 11,
        "december": 12, "dec": 12,
    }
    for name, month in months.items():
        m = re.search(rf"\b{name}\b(?:\s+(20\d{{2}}))?", t)
        if m:
            year = int(m.group(1)) if m.group(1) else today.year
            last = calendar.monthrange(year, month)[1]
            return f"{name.title()} {year}", date(year, month, 1), date(year, month, last)

    m = re.search(r"\bfy\s*(20)?(\d{2})\s*[-/ ]\s*(20)?(\d{2})\b", t)
    if not m:
        m = re.search(r"\b(20)(\d{2})\s*[-/]\s*(20)?(\d{2})\b", t)
    if m:
        y1 = int(m.group(2))
        y1 = 2000 + y1 if y1 < 100 else y1
        return f"FY {y1}-{(y1 + 1) % 100:02d}", date(y1, 4, 1), date(y1 + 1, 3, 31)

    return None


def _parse_due(t: str) -> str | None:
    if re.search(r"\b(credit only|only credit|credit bills?)\b", t):
        return "Credit Only"
    if re.search(r"\b(paid only|cleared only|paid.?cleared|fully paid|paid sales?|paid purchases?|cleared sales?|cleared purchases?)\b", t):
        return "Paid / Cleared"
    if re.search(
        r"\b(due only|only due|unpaid|pending dues?|dues? only|show dues?|"
        r"due sales?|due purchases?|sales? with due|purchases? with due)\b",
        t,
    ) or (
        re.search(r"\bdue\b", t)
        and not re.search(r"\b(export|report|customer due|supplier due)\b", t)
    ):
        return "Due Only"
    return None


def _parse_stock(t: str) -> str | None:
    if re.search(r"\b(out of stock|zero stock|no stock)\b", t):
        return "Out of Stock"
    if re.search(r"\b(low stock|low stocks|running low)\b", t):
        return "Low Stock"
    if re.search(r"\b(in stock|available stock)\b", t):
        return "In Stock"
    return None


def _parse_expiry(t: str) -> str | None:
    if re.search(r"\b(near expir(?:y|ing)|expiring soon|about to expire)\b", t):
        return "Near Expiry"
    if re.search(r"\b(expired stock|expired medicines?|expired|expiry)\b", t):
        return "Expired"
    return None


def _parse_schedule(t: str) -> str | None:
    if re.search(r"\b(non[- ]?scheduled|unscheduled|no schedule|without schedule)\b", t):
        return "Non-Scheduled"
    try:
        from core.layout_config import get_configured_schedules
        schedules = [s for s in get_configured_schedules() if s]
    except Exception:
        schedules = ["H", "H1", "X", "G", "K", "C", "C1", "P", "N", "M"]

    spoken = {
        "h one": "H1", "h 1": "H1", "c one": "C1", "c 1": "C1",
        "schedule h": "H", "h schedule": "H",
    }
    for phrase, code in spoken.items():
        if phrase in t and code in schedules:
            return code

    for sch in sorted(schedules, key=len, reverse=True):
        low = sch.lower()
        patterns = [
            rf"\bschedule\s+{re.escape(low)}\b",
            rf"\b{re.escape(low)}\s+schedule\b",
            rf"\b{re.escape(low)}\s+medicines?\b",
            rf"\bmedicines?\s+(?:of|in|under)\s+(?:the\s+)?{re.escape(low)}\b",
            rf"\b{re.escape(low)}\b",
        ]
        for pat in patterns:
            if re.search(pat, t):
                if len(low) == 1 and not re.search(
                    r"\b(schedule|medicine|stock|filter|show|list)\b", t
                ):
                    continue
                return sch
    return None


def _parse_med_type(t: str) -> str | None:
    try:
        from core.layout_config import get_med_types
        types = get_med_types()
    except Exception:
        return None
    for typ in sorted(types, key=len, reverse=True):
        low = typ.lower()
        if len(low) < 3:
            continue
        if re.search(rf"\b{re.escape(low)}\b", t):
            return typ
    return None


def _parse_sort(t: str) -> str | None:
    if re.search(r"\b(oldest first|oldest|oldest to newest)\b", t):
        return "Oldest first"
    if re.search(r"\b(a\s*to\s*z|alphabetic(?:al)?(?:\s+a\s*[- ]?\s*z)?)\b", t):
        return "Alphabetic (A-Z)"
    if re.search(r"\b(z\s*to\s*a|alphabetic(?:al)?\s+z|reverse alpha)\b", t):
        return "Alphabetic (Z-A)"
    if re.search(r"\b(newest first|recent|latest first|newest)\b", t):
        return "Recent (newest first)"
    return None



def _parse_one_date(token: str, *, default_year: int | None = None) -> date | None:
    """Parse a spoken/typed date fragment into a date."""
    raw = (token or "").strip().lower()
    raw = raw.replace(",", " ").replace("/", "-").replace(".", "-")
    raw = re.sub(r"\b(\d+)(st|nd|rd|th)\b", r"\1", raw)
    raw = re.sub(r"\s+", " ", raw).strip("- ")
    if not raw:
        return None
    today = date.today()
    year = default_year or today.year

    # ISO / numeric: 2025-06-01, 01-06-2025, 1-6-2025
    m = re.fullmatch(r"(20\d{2})-(\d{1,2})-(\d{1,2})", raw)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            return date(y, mo, d)
        except ValueError:
            return None
    m = re.fullmatch(r"(\d{1,2})-(\d{1,2})-(20\d{2})", raw)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        # Prefer D-M-Y (common in IN); if month>12 swap.
        if mo > 12 and d <= 12:
            mo, d = d, mo
        try:
            return date(y, mo, d)
        except ValueError:
            return None
    m = re.fullmatch(r"(\d{1,2})-(\d{1,2})", raw)
    if m:
        d, mo = int(m.group(1)), int(m.group(2))
        if mo > 12 and d <= 12:
            mo, d = d, mo
        try:
            return date(year, mo, d)
        except ValueError:
            return None

    months = {
        "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3,
        "april": 4, "apr": 4, "may": 5, "june": 6, "jun": 6,
        "july": 7, "jul": 7, "august": 8, "aug": 8, "september": 9, "sep": 9,
        "sept": 9, "october": 10, "oct": 10, "november": 11, "nov": 11,
        "december": 12, "dec": 12,
    }
    # 1 june 2025 / 1 june / june 1 2025 / june 1
    m = re.fullmatch(
        r"(\d{1,2})\s+([a-z]+)(?:\s+(20\d{2}))?",
        raw,
    )
    if m and m.group(2) in months:
        d = int(m.group(1))
        mo = months[m.group(2)]
        y = int(m.group(3)) if m.group(3) else year
        try:
            return date(y, mo, d)
        except ValueError:
            return None
    m = re.fullmatch(
        r"([a-z]+)\s+(\d{1,2})(?:\s+(20\d{2}))?",
        raw,
    )
    if m and m.group(1) in months:
        mo = months[m.group(1)]
        d = int(m.group(2))
        y = int(m.group(3)) if m.group(3) else year
        try:
            return date(y, mo, d)
        except ValueError:
            return None
    return None


def _parse_date_range(t: str) -> tuple[str, date, date] | None:
    """Parse 'from ... to ...' / 'between ... and ...' custom ranges."""
    m = re.search(
        r"\b(?:from|between)\s+(.+?)\s+(?:to|through|till|until|and)\s+(.+?)(?=\s+(?:sales?|purchases?|bills?|history|due|schedule|customer|supplier)\b|$)",
        t,
    )
    if not m:
        m = re.search(
            r"\b(\d{1,2}\s+[a-z]+(?:\s+20\d{2})?|[a-z]+\s+\d{1,2}(?:\s+20\d{2})?|20\d{2}-\d{1,2}-\d{1,2}|\d{1,2}-\d{1,2}-\d{2,4})\s+to\s+"
            r"(\d{1,2}\s+[a-z]+(?:\s+20\d{2})?|[a-z]+\s+\d{1,2}(?:\s+20\d{2})?|20\d{2}-\d{1,2}-\d{1,2}|\d{1,2}-\d{1,2}-\d{2,4})\b",
            t,
        )
    if not m:
        return None
    left, right = m.group(1).strip(" ,"), m.group(2).strip(" ,")
    # Strip trailing junk words from right side
    right = re.sub(
        r"\b(sales?|purchases?|bills?|history|only)\b.*$",
        "",
        right,
    ).strip(" ,")
    d1 = _parse_one_date(left)
    d2 = _parse_one_date(right, default_year=(d1.year if d1 else None))
    if d1 is None or d2 is None:
        return None
    if d2 < d1:
        d1, d2 = d2, d1
    label = f"{d1.isoformat()} to {d2.isoformat()}"
    return label, d1, d2


def _parse_party(t: str) -> tuple[str, str] | None:
    """
    Customer / supplier name filter.
    Returns (role, name) where role is 'customer' or 'supplier'.
    """
    # Explicit role
    m = re.search(
        r"\b(?:customer|party)\s+(?:name\s+|is\s+|named\s+)?([a-z0-9][a-z0-9 .'\-]{1,40})",
        t,
    )
    if m:
        name = _clean_party_name(m.group(1))
        if name:
            return "customer", name
    m = re.search(
        r"\bsupplier\s+(?:name\s+|is\s+|named\s+)?([a-z0-9][a-z0-9 .'\-]{1,40})",
        t,
    )
    if m:
        name = _clean_party_name(m.group(1))
        if name:
            return "supplier", name

    # sales for X / purchases for X / bills of X
    m = re.search(
        r"\b(?:sales?|bills?)\s+(?:for|of|by|from)\s+(?:customer\s+)?([a-z0-9][a-z0-9 .'\-]{1,40})",
        t,
    )
    if m:
        name = _clean_party_name(m.group(1))
        if name:
            return "customer", name
    m = re.search(
        r"\b(?:purchases?|purchase bills?)\s+(?:for|of|by|from)\s+(?:supplier\s+)?([a-z0-9][a-z0-9 .'\-]{1,40})",
        t,
    )
    if m:
        name = _clean_party_name(m.group(1))
        if name:
            return "supplier", name

    # X sales / X purchases (after show stripped: "ram sales this month")
    m = re.search(
        r"\b(?:show|list|filter)\s+([a-z0-9][a-z0-9 .'\-]{1,30}?)\s+sales?\b",
        t,
    )
    if not m:
        m = re.search(
            r"^([a-z0-9][a-z0-9 .'\-]{1,30}?)\s+sales?\b",
            t,
        )
    if m:
        name = _clean_party_name(m.group(1))
        if name and name.lower() not in _PARTY_STOP:
            return "customer", name
    m = re.search(
        r"\b(?:show|list|filter)\s+([a-z0-9][a-z0-9 .'\-]{1,30}?)\s+purchases?\b",
        t,
    )
    if not m:
        m = re.search(
            r"^([a-z0-9][a-z0-9 .'\-]{1,30}?)\s+purchases?\b",
            t,
        )
    if m:
        name = _clean_party_name(m.group(1))
        if name and name.lower() not in _PARTY_STOP:
            return "supplier", name
    return None


_PARTY_STOP = frozenset({
    "due", "paid", "credit", "all", "today", "yesterday", "this", "last",
    "month", "year", "week", "schedule", "h", "h1", "low", "stock",
    "expired", "near", "my", "the", "a", "an", "from", "between",
    "open", "show", "list", "filter", "go", "goto", "please",
    "history", "sale", "sales", "purchase", "purchases", "bill", "bills",
    "inventory", "medicine", "medicines", "customer", "supplier", "party",
    "january", "february", "march", "april", "may", "june", "july",
    "august", "september", "october", "november", "december",
    "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "oct", "nov", "dec",
})


def _clean_party_name(name: str) -> str:
    q = (name or "").strip(" .,")
    q = re.sub(
        r"\b(in\s+)?(sales?|purchases?|history|bills?|only|due|list)\b.*$",
        "",
        q,
        flags=re.I,
    ).strip(" .,")
    q = re.sub(r"\s+", " ", q).strip()
    if len(q) < 2:
        return ""
    low = q.lower()
    tokens = low.split()
    # Reject period phrases mistaken as names ("this month", "last year").
    if low in _PARTY_STOP or all(tok in _PARTY_STOP for tok in tokens):
        return ""
    if re.search(r"\b(this|last|today|yesterday|current|previous)\b", low):
        return ""
    # Simple title case for the combo box; SQL LIKE is case-insensitive.
    return " ".join(w.capitalize() if w.islower() else w for w in q.split())

def _parse_search(t: str) -> str | None:
    m = re.search(
        r"\b(?:search(?:\s+for)?|find|look(?:\s+up)?)\s+(?:medicine\s+|drug\s+|item\s+)?"
        r"([a-z0-9][a-z0-9 \-]{1,40})$",
        t,
    )
    if not m:
        return None
    q = m.group(1).strip()
    q = re.sub(
        r"\b(in\s+)?(inventory|stock|sales?|purchases?|history)\b",
        "",
        q,
    ).strip(" -")
    if q and q not in ("low", "due", "all", "the"):
        return q
    return None


def _is_clear(t: str) -> bool:
    return bool(
        re.search(
            r"\b(clear(?:\s+\w+){0,3}\s+filters?|reset(?:\s+\w+){0,3}\s+filters?|remove(?:\s+\w+){0,3}\s+filters?|"
            r"clear all filters?|show all|show everything|no filter|"
            r"reset list|clear list filter)\b",
            t,
        )
    )


def _has_filter_intent(t: str) -> bool:
    if _is_clear(t):
        return True
    return bool(
        re.search(
            r"\b(filter|low stock|out of stock|in stock|expired|near expir|"
            r"expir(?:y|ing)|schedule|non[- ]?scheduled|due|credit only|"
            r"paid|cleared|this month|last month|this year|last year|"
            r"this week|last week|today|yesterday|this fy|financial year|"
            r"fy\s*\d|january|february|march|april|may|june|july|august|"
            r"september|october|november|december|oldest|newest|a to z|"
            r"z to a|alphabetic|search for|find medicine|"
            r"customer|supplier|party|from\s+\d|between\s+\d|"
            r"\d{1,2}\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)|"
            r"sales?\s+for|purchases?\s+for)\b",
            t,
        )
    )


def parse_list_filter_command(text: str) -> dict | None:
    """Parse spoken filter text into a spec dict, or None."""
    raw = _norm(text)
    if not raw:
        return None
    t = _strip_leads(raw)

    # Bare page names (with or without open/show) are navigation, not filters.
    _BARE_PAGES = (
        "sales history", "sale history", "purchase history", "purchases history",
        "inventory", "stock", "sales", "purchase", "purchases",
        "billing", "home", "returns", "customers", "suppliers",
    )
    bare = re.sub(
        r"^(open|show|go to|goto|switch to|navigate to|launch|display|take me to)\s+",
        "",
        t,
    ).strip()
    if t in _BARE_PAGES or bare in _BARE_PAGES:
        return None
    if (
        not _has_filter_intent(t)
        and not _parse_schedule(t)
        and not _parse_med_type(t)
        and not _parse_date_range(t)
        and not _parse_party(t)
    ):
        return None

    if re.search(r"\b(export|download|backup|installer|update)\b", t):
        return None
    # Keep Alert & Monitoring screens: "low stock alerts", "expired alerts"
    if re.search(r"\b(alerts?|monitoring)\b", t):
        return None

    spec: dict = {"clear": False}
    if _is_clear(t):
        spec["clear"] = True
        page = _infer_page(t)
        if page:
            spec["page"] = page
        return spec

    page = _infer_page(t)
    stock = _parse_stock(t)
    expiry = _parse_expiry(t)
    schedule = _parse_schedule(t)
    due = _parse_due(t)
    # Custom from/to wins over relative periods when both appear.
    date_range = _parse_date_range(t)
    period = None if date_range else _parse_period(t)
    med_type = _parse_med_type(t)
    sort = _parse_sort(t)
    search = _parse_search(t)
    party = _parse_party(t)

    if page is None:
        if stock or expiry or med_type or search:
            page = _PAGE_INVENTORY
        elif party and party[0] == "supplier":
            page = _PAGE_PURCHASE
        elif party and party[0] == "customer":
            page = _PAGE_SALES
        elif due and re.search(r"\bpurchase", t):
            page = _PAGE_PURCHASE
        elif due:
            page = _PAGE_SALES
        elif (date_range or period) and re.search(r"\bpurchase", t):
            page = _PAGE_PURCHASE
        elif date_range or period:
            page = _PAGE_SALES
        elif schedule and re.search(r"\b(medicine|stock|inventory)\b", t):
            page = _PAGE_INVENTORY
        elif schedule and re.search(r"\bpurchase", t):
            page = _PAGE_PURCHASE
        elif schedule and re.search(r"\bsales?\b", t):
            page = _PAGE_SALES
        elif schedule:
            page = _PAGE_INVENTORY

    if page:
        spec["page"] = page
    if stock:
        spec["stock"] = stock
    if expiry:
        spec["expiry"] = expiry
    if schedule:
        spec["schedule"] = schedule
    if due:
        spec["due"] = due
    if date_range:
        label, fd, td = date_range
        spec["period_label"] = label
        spec["from_date"] = fd.isoformat()
        spec["to_date"] = td.isoformat()
    elif period:
        label, fd, td = period
        spec["period_label"] = label
        spec["from_date"] = fd.isoformat()
        spec["to_date"] = td.isoformat()
    if med_type:
        spec["med_type"] = med_type
    if sort:
        spec["sort"] = sort
    if search:
        spec["search"] = search
    if party:
        role, name = party
        spec["party"] = name
        spec["party_role"] = role
        # History pages reuse the party combo via search key.
        if page in (_PAGE_SALES, _PAGE_PURCHASE) and "search" not in spec:
            spec["search"] = name

    # "clear": False is always present — only real filters / clear=True count.
    actionable = bool(spec.get("clear")) or any(
        k in spec
        for k in (
            "stock", "expiry", "schedule", "due", "from_date",
            "med_type", "sort", "search", "party",
        )
    )
    if not actionable:
        return None
    return spec


def _set_combo(combo, value: str) -> None:
    if combo is None:
        return
    try:
        combo.set(value or "")
    except Exception:
        try:
            combo.var.set(value or "")
        except Exception:
            pass


def _ensure_page(app, page_key: str | None):
    from core.voice.export_ui import (
        _ensure_inventory,
        _ensure_purchase_history,
        _ensure_sales_history,
    )

    if page_key == _PAGE_INVENTORY:
        return _ensure_inventory(app), "Inventory"
    if page_key == _PAGE_SALES:
        return _ensure_sales_history(app), "Sales History"
    if page_key == _PAGE_PURCHASE:
        return _ensure_purchase_history(app), "Purchase History"

    for attr, label in (
        ("_inventory_page", "Inventory"),
        ("_sales_history_page", "Sales History"),
        ("_purchase_history_page", "Purchase History"),
    ):
        page = getattr(app, attr, None)
        if page is None:
            continue
        try:
            if page.parent.winfo_ismapped():
                return page, label
        except Exception:
            pass
    return None, None


def _clear_inventory(page) -> None:
    if hasattr(page, "_clear_filters"):
        page._clear_filters()
        return
    for name in (
        "search_entry", "type_filter", "stock_filter",
        "expiry_filter", "schedule_filter",
    ):
        _set_combo(getattr(page, name, None), "")
    if hasattr(page, "_run_filter_inventory"):
        page._run_filter_inventory()
    elif hasattr(page, "load_inventory"):
        page.load_inventory()


def _apply_inventory(page, spec: dict) -> str:
    if spec.get("clear"):
        _clear_inventory(page)
        return "Inventory filters cleared."

    # Replace prior filters so each command is a full new view.
    for name in (
        "search_entry", "type_filter", "stock_filter",
        "expiry_filter", "schedule_filter",
    ):
        _set_combo(getattr(page, name, None), "")

    if "search" in spec:
        _set_combo(page.search_entry, spec["search"])
    if "med_type" in spec:
        _set_combo(page.type_filter, spec["med_type"])
    if "stock" in spec:
        _set_combo(page.stock_filter, spec["stock"])
    if "expiry" in spec:
        _set_combo(page.expiry_filter, spec["expiry"])
    if "schedule" in spec:
        _set_combo(page.schedule_filter, spec["schedule"])

    if hasattr(page, "_run_filter_inventory"):
        page._run_filter_inventory()
    elif hasattr(page, "filter_inventory"):
        page.filter_inventory()

    bits = []
    for key, fmt in (
        ("stock", "{}"),
        ("expiry", "{}"),
        ("schedule", "schedule {}"),
        ("med_type", "{}"),
        ("search", "search {}"),
    ):
        if spec.get(key):
            bits.append(fmt.format(spec[key]))
    return "Inventory: " + (", ".join(bits) if bits else "filters applied") + "."


def _clear_history(page) -> None:
    if hasattr(page, "clear_filter"):
        page.clear_filter()


def _apply_history(page, spec: dict, *, kind: str) -> str:
    label = "Sales" if kind == "sales" else "Purchases"
    if spec.get("clear"):
        _clear_history(page)
        return f"{label} History filters cleared."

    # Replace prior filters so each command is a full new view.
    page._clear_date_filter(page.from_date)
    page._clear_date_filter(page.to_date)
    party = page.customer_filter if kind == "sales" else page.supplier_filter
    for combo in (party, page.due_filter, page.schedule_filter):
        _set_combo(combo, "")
    try:
        page.fy_filter.set("")
    except Exception:
        pass

    if "from_date" in spec and "to_date" in spec:
        page._set_date_filter(page.from_date, spec["from_date"])
        page._set_date_filter(page.to_date, spec["to_date"])

    if "due" in spec:
        _set_combo(page.due_filter, spec["due"])
    if "schedule" in spec:
        _set_combo(page.schedule_filter, spec["schedule"])
    if "sort" in spec:
        _set_combo(page.sort_filter, spec["sort"])
    if "search" in spec:
        _set_combo(party, spec["search"])

    page.apply_filter()
    if "sort" in spec and hasattr(page, "_resort_visible"):
        page._resort_visible()

    bits = []
    if spec.get("period_label"):
        bits.append(spec["period_label"])
    elif spec.get("from_date"):
        bits.append(f"{spec['from_date']} to {spec['to_date']}")
    if spec.get("party"):
        role = spec.get("party_role") or ("customer" if kind == "sales" else "supplier")
        bits.append(f"{role} {spec['party']}")
    for key, fmt in (
        ("due", "{}"),
        ("schedule", "schedule {}"),
        ("sort", "{}"),
    ):
        if spec.get(key):
            bits.append(fmt.format(spec[key]))
    if spec.get("search") and not spec.get("party"):
        bits.append(spec["search"])
    return f"{label} History: " + (", ".join(bits) if bits else "filters applied") + "."


def apply_list_filter(app, spec: dict | None) -> tuple[bool, str]:
    """Apply parsed filter spec on the UI thread. Returns (ok, message)."""
    if not spec:
        return False, "No filter recognized."

    page_key = spec.get("page")
    page, page_label = _ensure_page(app, page_key)
    if page is None:
        if page_key == _PAGE_INVENTORY or spec.get("stock") or spec.get("expiry"):
            page, page_label = _ensure_page(app, _PAGE_INVENTORY)
        elif page_key == _PAGE_PURCHASE:
            page, page_label = _ensure_page(app, _PAGE_PURCHASE)
        else:
            page, page_label = _ensure_page(app, _PAGE_SALES)
    if page is None:
        return False, "Could not open the list page."

    try:
        if hasattr(page, "stock_filter") and hasattr(page, "_run_filter_inventory"):
            msg = _apply_inventory(page, spec)
        elif hasattr(page, "customer_filter"):
            msg = _apply_history(page, spec, kind="sales")
        elif hasattr(page, "supplier_filter"):
            msg = _apply_history(page, spec, kind="purchase")
        else:
            return False, f"{page_label or 'Page'} does not support list filters."
        voice_log(f"List filter applied: {msg}")
        return True, msg
    except Exception as exc:
        voice_log(f"List filter failed: {exc}", level="error")
        return False, f"Could not apply filter. {exc}"
