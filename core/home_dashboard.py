"""Home dashboard SQL — shared by Tk home page and Tauri desktop API (no UI imports)."""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Optional

from core.stock_utils import sum_inventory_mrp_value


def fy_bounds(today: Optional[date] = None):
    today = today or datetime.now().date()
    month_start = today.replace(day=1)
    fy_start_year = today.year if today.month >= 4 else today.year - 1
    fy_start = f"{fy_start_year}-04-01"
    fy_end = f"{fy_start_year + 1}-03-31"
    fy_label = f"{fy_start_year}-{str(fy_start_year + 1)[2:]}"
    return month_start, fy_start, fy_end, fy_label


def _cleared_shortfall(rows: list) -> float:
    """Unpaid portion of bills that are already marked cleared/paid."""
    short = 0.0
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        if r.get("deleted") or int(r.get("is_autosave") or 0):
            continue
        total = float(r.get("total_amount") or 0)
        paid = float(r.get("amount_paid") or 0)
        due = float(r.get("total_due") if r.get("total_due") is not None else 0)
        cleared = bool(r.get("account_cleared")) or bool(r.get("bill_cleared")) or due <= 0.01
        if cleared and total > paid + 0.01:
            short += total - paid
    return round(short, 2)


def _collected(bill_paid: float, payments: float, shortfall: float) -> float:
    """bill_paid + max(payments, cleared_shortfall) — no double-count."""
    return round(float(bill_paid or 0) + max(float(payments or 0), float(shortfall or 0)), 2)


def query_dashboard_stats(conn, today=None) -> dict[str, Any]:
    """Run dashboard SQL (safe off the UI thread). Online uses server store summary."""
    _online_error = ""
    try:
        from core.sync_prefs import is_online_mode

        if is_online_mode():
            from core import store_query_client as sq

            remote = sq.home_summary() or {}
            today = today or datetime.now().date()
            today_str = str(remote.get("today") or today)
            month_start = remote.get("month_start") or today.replace(day=1)
            fy_start = str(remote.get("fy_start") or "")
            fy_end = str(remote.get("fy_end") or "")
            fy_label = str(remote.get("fy_label") or "")
            if not fy_start:
                month_start, fy_start, fy_end, fy_label = fy_bounds(today)

            # Align Collected with Paid/cleared bills (e.g. amount_paid=0 but cleared).
            try:
                # Today, month and year are three independent queries, and this is
                # the landing page -- it re-runs on every sync nonce. Serially they
                # cost three round trips; fetched together they cost one wave.
                from core.store_query_client import fetch_parallel

                m_from = str(month_start)[:10]
                y_from = str(fy_start)[:10]
                y_to = str(fy_end)[:10]

                # A payments hiccup must not blank the sales tiles with it. The `except`
                # below drops every tile back to the bare summary; the receipts are one
                # term of one tile and are not worth that.
                def _pays(a, b):
                    try:
                        return sq.list_customer_payments(
                            from_date=a, to_date=b, limit=5000
                        ) or {}
                    except Exception:
                        return {}
                (
                    t_data,
                    m_data,
                    y_data,
                    t_pay_data,
                    m_pay_data,
                    y_pay_data,
                ) = fetch_parallel(
                    lambda: sq.list_sales(
                        from_date=today_str, to_date=today_str, limit=5000
                    )
                    or {},
                    lambda: sq.list_sales(
                        from_date=m_from,
                        to_date=today_str,
                        limit=5000,
                    )
                    or {},
                    lambda: sq.list_sales(
                        from_date=y_from,
                        to_date=y_to,
                        limit=5000,
                    )
                    or {},
                    lambda: _pays(today_str, today_str),
                    lambda: _pays(m_from, today_str),
                    lambda: _pays(y_from, y_to),
                )
                t_rows = list((t_data or {}).get("rows") or [])
                m_rows = list((m_data or {}).get("rows") or [])
                y_rows = list((y_data or {}).get("rows") or [])
                t_bill = sum(float(r.get("amount_paid") or 0) for r in t_rows)
                m_bill = sum(float(r.get("amount_paid") or 0) for r in m_rows)
                y_bill = sum(float(r.get("amount_paid") or 0) for r in y_rows)
                t_short = _cleared_shortfall(t_rows)
                m_short = _cleared_shortfall(m_rows)
                y_short = _cleared_shortfall(y_rows)
                # Standalone receipts, read from the payments table rather than
                # reverse-engineered out of the summary.
                #
                # `/summaries/home` returns `collected = SUM(sales.amount_paid)` and has
                # no payments field at all -- `grep -rn 'today_payments'` over the
                # server source returns nothing. So `max(0, month_collected - m_bill)`
                # was subtracting a number from itself: `m_coll_raw` IS `m_bill`, and
                # `m_pay` was therefore structurally 0 on every store, for ever. Online
                # "Collected" silently degraded to `bill_paid + cleared_shortfall` while
                # this same file's Offline branch (:186-247) computes
                # `bill_paid + max(customer_payments in window, shortfall)`.
                #
                # Live Roshan, FY 2026-27: 26,379.48 online against 64,205.71 offline
                # for one set of books -- a Rs 23,968.68 gap on the owner's headline
                # tile, with the two modes of one app disagreeing. `/payments/customers`
                # has taken `from`/`to` all along, so the real figure costs one request
                # in the same parallel wave.
                def _pay_sum(data):
                    rows = list((data or {}).get("rows") or [])
                    return sum(float(r.get("amount") or 0) for r in rows)

                t_pay = _pay_sum(t_pay_data)
                m_pay = _pay_sum(m_pay_data)
                y_pay = _pay_sum(y_pay_data)
                t_collected = _collected(t_bill, t_pay, t_short)
                m_collected = _collected(m_bill, m_pay, m_short)
                y_collected = _collected(y_bill, y_pay, y_short)
                t_sales = sum(float(r.get("total_amount") or 0) for r in t_rows) or float(
                    remote.get("today_sales") or 0
                )
                m_sales = sum(float(r.get("total_amount") or 0) for r in m_rows) or float(
                    remote.get("month_sales") or 0
                )
                y_sales = sum(float(r.get("total_amount") or 0) for r in y_rows) or float(
                    remote.get("year_sales") or 0
                )
            except Exception:
                t_sales = float(remote.get("today_sales") or 0)
                t_collected = float(remote.get("today_collected") or 0)
                m_sales = float(remote.get("month_sales") or 0)
                m_collected = float(remote.get("month_collected") or 0)
                y_sales = float(remote.get("year_sales") or 0)
                y_collected = float(remote.get("year_collected") or 0)
                t_rows = []
                m_rows = []
                y_rows = []

            return {
                "today_str": today_str,
                "month_start": month_start,
                "fy_start": fy_start,
                "fy_end": fy_end,
                "fy_label": fy_label,
                "values": [
                    f"\u20b9{t_sales:,.0f}",
                    f"\u20b9{t_collected:,.0f}",
                    str(len(t_rows) if t_rows else int(remote.get("today_bills") or 0)),
                    f"\u20b9{float(remote.get('customer_due') or 0):,.0f}",
                    f"\u20b9{float(remote.get('supplier_due') or 0):,.0f}",
                    f"\u20b9{float(remote.get('stock_value') or 0):,.0f}",
                    f"\u20b9{m_sales:,.0f}",
                    f"\u20b9{m_collected:,.0f}",
                    str(len(m_rows) if m_rows else int(remote.get("month_bills") or 0)),
                    f"\u20b9{y_sales:,.0f}",
                    f"\u20b9{y_collected:,.0f}",
                    str(len(y_rows) if y_rows else int(remote.get("year_bills") or 0)),
                ],
            }
    except Exception as exc:
        print(f"[home dashboard] online summary: {exc}")
        # Remember it. In Online mode the fallthrough below queries an EMPTY
        # :memory: database, so a broken link to the server rendered as a clean
        # Home screen reading zero for everything -- which is the shape the
        # "store opened empty" report took. Inventory and the two history pages
        # already say so; Home was the one that stayed silent.
        _online_error = str(exc).strip() or exc.__class__.__name__

    server_error = ""
    if _online_error:
        # Only a genuine Online failure counts. If is_online_mode() itself was
        # what raised, the store is offline and these SQL figures are the real
        # ones -- saying "could not read from the server" there would be a lie.
        try:
            from core.sync_prefs import is_online_mode

            if is_online_mode():
                from core.desktop_pages_service import _server_error_text

                server_error = _server_error_text([_online_error])
        except Exception:
            server_error = ""

    today = today or datetime.now().date()
    today_str = str(today)
    month_start, fy_start, fy_end, fy_label = fy_bounds(today)
    cur = conn.cursor()
    cur.execute(
        "SELECT COALESCE(SUM(total_amount),0),COALESCE(SUM(amount_paid),0),COUNT(*), "
        "COALESCE(SUM(CASE WHEN COALESCE(account_cleared,0)=1 OR COALESCE(bill_cleared,0)=1 "
        "OR COALESCE(total_due,0)<=0.01 "
        "THEN MAX(0, COALESCE(total_amount,0)-COALESCE(amount_paid,0)) ELSE 0 END),0) "
        "FROM sales WHERE COALESCE(deleted,0)=0 AND COALESCE(is_autosave,0)=0 AND bill_date=?",
        (today_str,),
    )
    t_sales, t_bill_paid, t_bills, t_short = cur.fetchone()
    t_pay = 0.0
    try:
        cur.execute(
            "SELECT COALESCE(SUM(amount),0) FROM customer_payments "
            "WHERE COALESCE(deleted,0)=0 AND payment_date=?",
            (today_str,),
        )
        t_pay = float(cur.fetchone()[0] or 0)
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
    t_collected = _collected(t_bill_paid, t_pay, t_short)
    cur.execute(
        "SELECT COALESCE(SUM(total_amount),0),COALESCE(SUM(amount_paid),0),COUNT(*), "
        "COALESCE(SUM(CASE WHEN COALESCE(account_cleared,0)=1 OR COALESCE(bill_cleared,0)=1 "
        "OR COALESCE(total_due,0)<=0.01 "
        "THEN MAX(0, COALESCE(total_amount,0)-COALESCE(amount_paid,0)) ELSE 0 END),0) "
        "FROM sales WHERE COALESCE(deleted,0)=0 AND COALESCE(is_autosave,0)=0 "
        "AND bill_date>=? AND bill_date<=?",
        (str(month_start), today_str),
    )
    m_sales, m_bill_paid, m_bills, m_short = cur.fetchone()
    m_pay = 0.0
    try:
        cur.execute(
            "SELECT COALESCE(SUM(amount),0) FROM customer_payments "
            "WHERE COALESCE(deleted,0)=0 "
            "AND payment_date>=? AND payment_date<=?",
            (str(month_start), today_str),
        )
        m_pay = float(cur.fetchone()[0] or 0)
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
    m_collected = _collected(m_bill_paid, m_pay, m_short)
    cur.execute(
        "SELECT COALESCE(SUM(total_amount),0),COALESCE(SUM(amount_paid),0),COUNT(*), "
        "COALESCE(SUM(CASE WHEN COALESCE(account_cleared,0)=1 OR COALESCE(bill_cleared,0)=1 "
        "OR COALESCE(total_due,0)<=0.01 "
        "THEN MAX(0, COALESCE(total_amount,0)-COALESCE(amount_paid,0)) ELSE 0 END),0) "
        "FROM sales WHERE COALESCE(deleted,0)=0 AND COALESCE(is_autosave,0)=0 "
        "AND bill_date>=? AND bill_date<=?",
        (fy_start, fy_end),
    )
    y_sales, y_bill_paid, y_bills, y_short = cur.fetchone()
    y_pay = 0.0
    try:
        cur.execute(
            "SELECT COALESCE(SUM(amount),0) FROM customer_payments "
            "WHERE COALESCE(deleted,0)=0 "
            "AND payment_date>=? AND payment_date<=?",
            (fy_start, fy_end),
        )
        y_pay = float(cur.fetchone()[0] or 0)
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
    y_collected = _collected(y_bill_paid, y_pay, y_short)
    cur.execute("SELECT COALESCE(SUM(total_due),0) FROM customers WHERE total_due>0")
    total_cust_due = cur.fetchone()[0]
    cur.execute("SELECT COALESCE(SUM(total_due),0) FROM suppliers WHERE total_due>0")
    total_sup_due = cur.fetchone()[0]
    cur.execute(
        "SELECT stock_qty, mrp, type, unit FROM medicines "
        # Deleted and hidden stock lines must not inflate the owner-facing stock
        # value. The old drive copy had this filter; it was lost on this side.
        "WHERE stock_qty>0 AND mrp>0 "
        "AND COALESCE(deleted,0)=0 AND COALESCE(is_hidden,0)=0"
    )
    stock_val = sum_inventory_mrp_value(cur.fetchall())
    return {
        "today_str": today_str,
        # Empty unless the Online read failed and these figures came from the
        # empty local database instead.
        "server_error": server_error,
        "month_start": month_start,
        "fy_start": fy_start,
        "fy_end": fy_end,
        "fy_label": fy_label,
        "values": [
            f"\u20b9{t_sales:,.0f}",
            f"\u20b9{t_collected:,.0f}",
            str(t_bills),
            f"\u20b9{total_cust_due:,.0f}",
            f"\u20b9{total_sup_due:,.0f}",
            f"\u20b9{stock_val:,.0f}",
            f"\u20b9{m_sales:,.0f}",
            f"\u20b9{m_collected:,.0f}",
            str(m_bills),
            f"\u20b9{y_sales:,.0f}",
            f"\u20b9{y_collected:,.0f}",
            str(y_bills),
        ],
    }


# Back-compat aliases used by ui/shared/home_page.py
_fy_bounds = fy_bounds
_query_dashboard_stats = query_dashboard_stats
