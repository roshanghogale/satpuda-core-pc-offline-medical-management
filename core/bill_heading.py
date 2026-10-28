"""The bill heading (owner, 22 Oct 2026): "Tax Invoice" or "Bill of Supply".

A shop that is a composition dealer, or that sells only exempt goods, may not issue a tax
invoice; it issues a Bill of Supply (Rule 49 of the CGST Rules). The shop picks it once in
Settings > Bill Template, and every bill it prints -- retail A4/A5/A6, dot matrix slip,
wholesale invoice, on the PC, the phone and the web -- follows:

  profile_bill_heading        "tax_invoice" (default; empty or anything else) | "bill_of_supply"
  profile_bill_supply_reason  "composition" (default) | "exempt"

Kept like the bank details (core/pharmacy_profile_io.BANK_KV): two of the store's own
settings rows -- the server's key/value settings Online, this PC's ``settings`` table
Offline, where the offline-first trigger sends them on (``profile_bill_`` is a shop-wide
prefix in core/offline_first/schema.py and core/store_kv_carry.py).

A Bill of Supply prints BILL OF SUPPLY and no tax: no GST rate or amount columns, no GST
row, no CGST/SGST/IGST or taxable summary. A composition dealer's bill also carries
COMPOSITION_DECLARATION. The amounts are the bill's own -- only the printing changes; the
saved bill and the reports never read this.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

log = logging.getLogger(__name__)

HEADING_KV = "profile_bill_heading"
REASON_KV = "profile_bill_supply_reason"
KV_NAMES = (HEADING_KV, REASON_KV)

TAX_INVOICE = "tax_invoice"
BILL_OF_SUPPLY = "bill_of_supply"
COMPOSITION = "composition"
EXEMPT = "exempt"

BILL_OF_SUPPLY_TITLE = "BILL OF SUPPLY"
COMPOSITION_DECLARATION = "Composition taxable person, not eligible to collect tax on supplies"

# The keys a bill print settings dict (and the Settings window) carries them under.
SETTINGS_HEADING = "bill_heading"
SETTINGS_REASON = "bill_supply_reason"


def _word(raw: Any) -> str:
    """A settings value as plain words (a JSON-quoted "bill_of_supply" reads the same)."""
    s = str(raw if raw is not None else "").strip()
    if len(s) >= 2 and s[0] == s[-1] == '"':
        s = s[1:-1].strip()
    return s.lower()


def clean_heading(raw: Any) -> str:
    return BILL_OF_SUPPLY if _word(raw) == BILL_OF_SUPPLY else TAX_INVOICE


def clean_reason(raw: Any) -> str:
    return EXEMPT if _word(raw) == EXEMPT else COMPOSITION


def heading_info(heading: Any = "", reason: Any = "") -> dict[str, Any]:
    """What a bill prints for this heading and reason (both raw settings values)."""
    h = clean_heading(heading)
    r = clean_reason(reason)
    bos = h == BILL_OF_SUPPLY
    return {
        SETTINGS_HEADING: h,
        SETTINGS_REASON: r,
        "bill_of_supply": bos,
        "supply_declaration": COMPOSITION_DECLARATION if bos and r == COMPOSITION else "",
    }


def _is_online() -> bool:
    try:
        from core.sync_prefs import is_online_mode

        return bool(is_online_mode())
    except Exception:
        return False


def _local_rows(conn) -> dict[str, str]:
    rows: dict[str, str] = {}
    if conn is None:
        return rows
    try:
        for name, value in conn.execute(
            "SELECT name, value FROM settings WHERE name IN (?, ?)", KV_NAMES
        ).fetchall():
            rows[str(name)] = str(value if value is not None else "")
    except Exception:
        pass
    return rows


def _server_rows() -> Optional[dict[str, str]]:
    """Online: the two rows from the server (cached by core.store_images), None if unreachable."""
    try:
        from core.store_images import _kv_rows_cached

        rows = _kv_rows_cached(KV_NAMES)
    except Exception as exc:
        log.debug("bill heading kv fetch: %s", exc)
        return None
    if rows is None:
        return None
    return {
        str(r.get("name") or ""): str(r.get("value") if r.get("value") is not None else "")
        for r in rows
        if str(r.get("name") or "") in KV_NAMES
    }


def _forget_server_rows() -> None:
    try:
        from core import store_images as si

        with si._lock:
            si._kv_list_cache.pop((si._store_key(), KV_NAMES), None)
    except Exception:
        pass


def load_bill_heading(conn=None) -> dict[str, Any]:
    """The store's heading: this PC's settings rows, the server's winning Online (where the
    other devices' saves land). Never raises -- a bill always prints."""
    rows = _local_rows(conn)
    if _is_online():
        server = _server_rows()
        if server:
            rows.update(server)
    return heading_info(rows.get(HEADING_KV, ""), rows.get(REASON_KV, ""))


def save_bill_heading(conn, heading: Any, reason: Any = None) -> dict[str, Any]:
    """Keep the shop's choice: Online straight to the server, and this PC's settings table
    either way (Offline its trigger sends the row to the server and the other devices)."""
    from core.desktop_settings_service import _setting_set

    have = load_bill_heading(conn)
    want = {HEADING_KV: clean_heading(heading),
            REASON_KV: clean_reason(have[SETTINGS_REASON] if reason is None else reason)}
    online = _is_online()
    for name, value in want.items():
        if conn is not None:
            _setting_set(conn, name, value)
        if online:
            from core.server_live import push_settings_kv

            push_settings_kv(name, value)
    if conn is not None:
        try:
            conn.commit()
        except Exception:
            pass
    if online:
        _forget_server_rows()
    return heading_info(want[HEADING_KV], want[REASON_KV])


def with_bill_heading(settings: Optional[dict], conn=None) -> dict:
    """The bill print settings with the store's heading in them (bill_heading,
    bill_supply_reason) -- unless the caller already put one there (a test, a preview)."""
    out = dict(settings or {})
    if SETTINGS_HEADING in out:
        return out
    info = load_bill_heading(conn)
    out[SETTINGS_HEADING] = info[SETTINGS_HEADING]
    out[SETTINGS_REASON] = info[SETTINGS_REASON]
    return out


def of(ctx: Any = None, settings: Optional[dict] = None) -> dict[str, Any]:
    """The heading a renderer prints: the bill's own (BillContext.bill_heading, filled from
    the store when the bill was read) first, the settings dict second, Tax Invoice else."""
    heading = getattr(ctx, "bill_heading", "") if ctx is not None else ""
    reason = getattr(ctx, "bill_supply_reason", "") if ctx is not None else ""
    s = settings or {}
    if not heading:
        heading = s.get(SETTINGS_HEADING, "")
        reason = s.get(SETTINGS_REASON, "")
    # The "Bill of Supply (Micropro style)" bill template is a Bill of Supply whatever the
    # store's heading says (core/micropro_bill.py) -- a sale bill, not a supplier document.
    if _word(s.get("template")) == BILL_OF_SUPPLY and not (
            str(getattr(ctx, "document_title", "") or "").strip()
            or str(s.get("document_title") or "").strip()):
        heading = BILL_OF_SUPPLY
    return heading_info(heading, reason)


def is_bill_of_supply(ctx: Any = None, settings: Optional[dict] = None) -> bool:
    return bool(of(ctx, settings)["bill_of_supply"])


def supply_declaration(ctx: Any = None, settings: Optional[dict] = None) -> str:
    return str(of(ctx, settings)["supply_declaration"])
