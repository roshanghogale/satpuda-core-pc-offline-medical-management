"""History bill printing helpers (no Tk) — batch print + date-range lookup."""
from __future__ import annotations

import os
import tempfile
import time
from typing import Any


PRINT_ALL_SLOT = 2


def _online_sales_for_print(
    from_iso: str, to_iso: str, schedule: str = ""
) -> list[dict[str, Any]]:
    """The same list, asked of the store server.

    Print All was completely dead in Online mode and nobody could see why. In
    that mode the engine keeps no business data of its own -- its database is an
    empty in-memory SQLite -- so the query below matched nothing, every time.
    The dialog opened, said "Print 0 bill(s)", and refused. Printing ONE bill
    kept working, which is what made it look like a printer problem: the
    single-bill path has an online fallback and this one never had.
    """
    from core.fy_serial import display_sales_bill_no
    from core.online_mutation_queue import merge_server_rows, overlay_sales_dicts
    from core import store_query_client as sq

    # The schedule filter goes to the SERVER, exactly as the history list
    # sends it. A server sale row carries no per-bill schedule aggregate, so
    # filtering one here would have quietly matched nothing at all.
    data = sq.list_sales(
        from_date=from_iso,
        to_date=to_iso,
        schedule=(schedule or "").strip(),
        limit=5000,
        include_total=False,
    )
    rows = merge_server_rows(
        list((data or {}).get("rows") or []),
        overlay_sales_dicts(),
        collection="sales",
    )
    out: list[dict[str, Any]] = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        # A draft the history list deliberately hides must not be printed, and
        # a deleted bill must not come back on paper.
        if r.get("deleted") or r.get("is_autosave"):
            continue
        # An edit this device has queued but not flushed still merges over the
        # server row. Printing it would put the OLD server copy on paper while
        # the picker showed the new total, and a leftover overlay is returned
        # whatever date window the server was asked for.
        pending = bool(r.get("pending")) or str(r.get("bill_no") or "").upper() == "PENDING"
        when = str(r.get("bill_date") or "")[:10]
        if from_iso and when and when < from_iso:
            continue
        if to_iso and when and when > to_iso:
            continue
        try:
            sid = int(r.get("id") or r.get("local_id") or 0)
        except (TypeError, ValueError):
            sid = 0
        # An unsynced bill used to be dropped in silence, so the picker showed
        # fewer bills than the list behind it and nothing said why. Show it, say
        # why, and refuse to tick it.
        reason = ""
        if sid <= 0:
            reason = "Not synced to the server yet"
        elif pending:
            # A queued edit sits over the server row: printing would put the
            # OLD server copy on paper while the picker showed the new total.
            reason = "Edited on this device, not yet synced"
        out.append(
            {
                "sale_id": sid,
                "bill_no": display_sales_bill_no(r.get("bill_no") or ""),
                "bill_date": str(r.get("bill_date") or "")[:10],
                "customer": str(r.get("customer_name") or ""),
                "total": float(r.get("total_amount") or 0),
                "printable": not reason,
                "reason": reason,
                "schedules": str(r.get("schedules") or r.get("bill_schedules") or ""),
                # A server sale row carries no per-bill schedule aggregate, so
                # "" here means NOT KNOWN, not "this bill has no schedule". The
                # picker used to print the second meaning on screen.
                "schedules_known": (
                    "schedules" in r or "bill_schedules" in r
                ),
            }
        )
    out.sort(key=lambda b: (b["bill_date"], b["sale_id"]))
    return out


def fetch_sales_for_print(
    conn,
    from_iso: str,
    to_iso: str,
    schedule: str = "",
    q: str = "",
    customer: str = "",
) -> list[dict[str, Any]]:
    """Sales in date range for Print All picker.

    q and customer narrow the same way the history list does, so the picker
    offers what the shop filtered down to rather than the whole date range.
    """
    if not from_iso or not to_iso:
        return []
    schedule = (schedule or "").strip()
    online = False
    try:
        from core.sync_prefs import is_online_mode

        online = bool(is_online_mode())
    except Exception:
        online = False
    def _narrow(bills: list[dict[str, Any]]) -> list[dict[str, Any]]:
        term = (q or "").strip().lower()
        cust = (customer or "").strip().lower()
        out = bills
        if term:
            out = [
                b for b in out
                if term in str(b.get("bill_no") or "").lower()
                or term in str(b.get("customer") or "").lower()
            ]
        if cust:
            out = [b for b in out if cust in str(b.get("customer") or "").lower()]
        return out

    if online:
        try:
            return _narrow(_online_sales_for_print(from_iso, to_iso, schedule))
        except Exception as exc:
            # Never fall through to the local query here: in this mode that
            # database is empty, so it would report "no bills in the range" for
            # what is really a broken link to the server -- which is exactly
            # how this bug hid for so long.
            raise RuntimeError(
                f"Could not reach the server for the bill list: {exc}"
            ) from exc
    # "Non-Scheduled" is a real choice meaning "no schedule code on any line",
    # not a code to match -- the history list and the old picker both read it
    # that way, and comparing it as a token selects nothing.
    sched_sql = ""
    sched_params: list[Any] = []
    if schedule:
        if schedule == "Non-Scheduled":
            line_cond = "TRIM(COALESCE(m.schedule,'')) = ''"
        else:
            line_cond = "m.schedule = ?"
            sched_params.append(schedule)
        sched_sql = (
            "AND EXISTS (SELECT 1 FROM sales_items si "
            "JOIN medicines m ON m.id = si.medicine_id "
            f"WHERE si.sale_id = s.id AND {line_cond})"
        )
    cur = conn.cursor()
    cur.execute(
        """
        SELECT s.id, s.bill_no, s.bill_date,
               COALESCE(NULLIF(TRIM(c.name), ''), s.customer_name, ''),
               s.total_amount,
               (SELECT GROUP_CONCAT(DISTINCT NULLIF(TRIM(m.schedule), ''))
                FROM sales_items si
                JOIN medicines m ON m.id = si.medicine_id
                WHERE si.sale_id = s.id) AS bill_schedules
        FROM sales s
        LEFT JOIN customers c ON s.customer_id = c.id
        WHERE COALESCE(s.deleted, 0) = 0
          AND COALESCE(s.is_autosave, 0) = 0
          AND date(s.bill_date) >= date(?) AND date(s.bill_date) <= date(?)
          {sched_sql}
        ORDER BY s.bill_date ASC, s.created_at ASC
        """.format(sched_sql=sched_sql),
        (from_iso, to_iso, *sched_params),
    )
    out: list[dict[str, Any]] = []
    for r in cur.fetchall():
        out.append(
            {
                "sale_id": int(r[0]),
                "bill_no": r[1],
                "bill_date": r[2],
                "customer": r[3],
                "total": float(r[4] or 0),
                "schedules": r[5] or "",
                # GROUP_CONCAT really does know: "" here means non-scheduled.
                "schedules_known": True,
                "printable": True,
                "reason": "",
            }
        )
    return _narrow(out)


def _chunk_sale_ids_for_print(sale_ids: list[int], paper: str) -> list[list[int]]:
    from core.bill_config import print_all_bills_per_page

    per = print_all_bills_per_page(paper)
    ids = [int(x) for x in sale_ids]
    return [ids[i : i + per] for i in range(0, len(ids), per)]


def print_bills_batch(
    conn,
    sale_ids: list[int],
    *,
    paper: str = "A6",
    slot: int = PRINT_ALL_SLOT,
    hwnd_owner: int = 0,
    settings_override: dict | None = None,
) -> tuple[int, list[str]]:
    """Print many sales bills — same logic as Tk sales_history_actions."""
    from core.bill_config import (
        apply_print_bill_layout,
        get_print_slot_settings,
        load_bill_print_settings,
    )
    from core.bill_output import (
        _load_sale_data,
        _try_pdf_via_browser,
        print_bill_with_slot,
    )
    from core.printer_manager import PrinterManager

    if not sale_ids:
        return 0, []

    paper_u = (paper or "A6").upper()
    base = dict(load_bill_print_settings())
    if settings_override:
        base.update(settings_override)
    slot_settings = get_print_slot_settings(base, slot)
    slot_settings["paper_size"] = paper_u
    slot_settings["bill_copies"] = 1
    render_settings = apply_print_bill_layout(slot_settings, print_slot_copies=1)
    render_settings["paper_size"] = paper_u
    printer = PrinterManager.get_printer_for_slot(slot)
    failures: list[str] = []
    pages = 0

    if paper_u == "A6":
        for sale_id in sale_ids:
            try:
                print_bill_with_slot(
                    conn,
                    sale_id,
                    slot,
                    hwnd_owner=hwnd_owner,
                    # The batch's OWN resolved settings, not the caller's -- which
                    # is None on every desktop Print All. Without it each bill was
                    # re-resolved from the slot on disk, so a slot set to A5 with
                    # two copies quietly produced two slips on A5 while the dialog
                    # said "A6 — one bill per sheet". This is the same flat dict
                    # the A5/A4 branch below already feeds to _load_sale_data;
                    # a nested {"print_slot_N": ...} would NOT survive, because
                    # bill_output resolves the slot from disk before applying it.
                    settings_override=render_settings,
                )
                pages += 1
            except Exception as exc:
                failures.append(f"Sale {sale_id}: {exc}")
            time.sleep(0.35)
        return pages, failures

    from bill_templates.classic import render_classic_bill_html_multi

    for chunk in _chunk_sale_ids_for_print(sale_ids, paper_u):
        try:
            contexts = []
            for sale_id in chunk:
                _, ctx, _ = _load_sale_data(
                    conn, sale_id, settings_override=render_settings
                )
                contexts.append(ctx)
            html = render_classic_bill_html_multi(contexts, render_settings)
            fd, html_path = tempfile.mkstemp(suffix=".html", prefix="batch_bills_")
            os.close(fd)
            pdf_path = html_path.replace(".html", ".pdf")
            with open(html_path, "w", encoding="utf-8") as fh:
                fh.write(html)
            if not _try_pdf_via_browser(html_path, pdf_path):
                raise RuntimeError("Could not create PDF for batch print.")
            PrinterManager.print_pdf_silently(pdf_path, printer, copies=1)
            pages += 1
        except Exception as exc:
            label = ", ".join(str(x) for x in chunk[:4])
            failures.append(f"Bills [{label}]: {exc}")
        time.sleep(0.35)
    return pages, failures
