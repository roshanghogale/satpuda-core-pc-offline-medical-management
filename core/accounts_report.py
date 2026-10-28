"""Accounts: Trial Balance, Profit & Loss and Balance Sheet (owner, 28 Oct 2026).

"add the balance sheet and trial balance for proper account management" -- built ONLY from what
the books already hold. There is no ledger, no new table and nothing is written: every figure is
read from the bills, returns, payments, purchases, the cash register's typed lines and today's
stock, each time the report is opened. What the app does not track (the owner's capital, opening
balances, money put into or taken out of the bank, GST paid to the government) cannot be read, so
ONE line takes it: "Capital & opening balances (balancing figure)". When the books are complete
from the first day, that line is exactly the shop's opening cash + opening stock + opening dues
(the tests pin it); whatever else it holds is what the app was never told.

Dates. A report covers From..To (default: this Indian financial year, 1 April to today; FY tag
like 2026-27). Income, expenses, purchases and GST are the movements dated From..To. Debtors,
creditors, cash and bank are balances AS ON the To date (every record up to it). Deleted rows and
autosave drafts never count (the same ``_alive`` filter as core/cash_register.py; core/gst_reports
filters the same way).

Every account, and the rule behind its figure:

  Sales                Bill totals dated From..To (sales.total_amount: after discount, round-off
                       included -- the figure the customer paid and the dues count). When the shop
                       prints GST (Settings -> Pharmacy, gst_enabled) the output GST is taken out.
  Sales returns        sales_returns.refund_amount dated From..To (what the dues credit), less the
                       GST on the credit notes when GST is split.
  Output GST           core/gst_reports GSTR-3B "Tax payable before ITC" for From..To: the printed
                       bills' own tax, less the credit notes' -- the GST Reports screen's figure.
                       Bills with no medicine lines carry no GST there, so their whole amount
                       stays in Sales (GST Reports lists them under Checks).
  Purchases            Supplier bills dated From..To at COALESCE(final_amount, total_amount) -- the
                       amount the supplier's due counts (core/purchase_service.recalculate_supplier_due)
                       -- less the ITC on them when GST is split.
  Purchase returns     purchase_returns.refund_amount dated From..To, less the ITC reversed on them.
  Input GST (ITC)      GSTR-3B 4(C) "Net ITC available": GST on bills from suppliers with a GSTIN,
                       less GST on goods sent back. GST on bills from unregistered suppliers is no
                       ITC: it stays in the purchase cost (as the GST report says).
  Discount             Not an account: bill totals are already after the discount. The bill and
                       line discounts of the period are shown under the P&L as a note.
  Expenses             The cash register's typed lines (cash_expenses kind 'out') dated From..To,
                       one line per category ("Other" when none). The category "Owner took cash"
                       is the owner's DRAWINGS, not an expense: it lowers capital, not profit.
  Cash added by owner  cash_expenses kind 'in' dated From..To: capital introduced.
  Sundry debtors       Each customer's balance as on To, by the app's own rule
                       (core/customer_service.recalculate_customer_due): bills - paid on the bills
                       - payments received - returns; a customer below 0 is an advance / credit
                       (a liability). Walk-in bills (no customer) are one more "party": their
                       returns refunded in cash / online count as paid back.
  Sundry creditors     Each supplier's balance as on To, by recalculate_supplier_due: bills -
                       paid at entry (due_fifo.PURCHASE_ENTRY_PAID_SQL) - supplier payments -
                       returns; a supplier below 0 is credit we hold with them (an asset).
  Cash in hand         The cash register's rule (core/cash_register.py: _cash_of, _payment_cash, the
                       'cash' mode of supplier payments, walk-in cash refunds, typed expenses and
                       cash added). Anchored on the last count the shop typed: the counted cash of
                       the last closed day on or before To, plus the cash moved after it; else the
                       last opening cash typed, plus the cash moved from that day; else every cash
                       movement in the books up to To. (A count is what was really in the drawer;
                       what it differs from the books goes to the balancing figure.)
  Bank (online)        Everything the dues count as paid or received, less the part the cash
                       register counts as cash: the online halves of bills, receipts, refunds,
                       purchases paid at entry and non-cash supplier payments, up to To. There is
                       no bank statement in the app, so deposits / withdrawals are not in it.
  Closing stock        Today's medicines.stock_qty x purchase cost per unit (core/stock_utils
                       effective_cost_per_unit on medicines.rate -- the cost the app's own profit
                       uses), stock above 0 only. For a To date before today it is worked back by
                       value: today's stock + cost of goods sold after To + stock written off after
                       To - purchases (net) after To (the app keeps only today's stock).
  Cost of goods sold   The app's existing profit logic (core/stock_utils.sale_line_profit, used by
                       Sales History): qty x cost per unit, from the cost kept on each sale line
                       (sales_items.cost_price) else the medicine's purchase rate; less the same
                       cost of the medicines returned (their bill line's cost).
  Opening stock        Worked back so the trading account gives exactly that cost of goods sold:
                       cost of goods sold + closing stock + goods written off - purchases + purchase
                       returns. Stock loaded without a purchase (opening stock, the loader app)
                       lands here, which is where it belongs.
  Stock written off    stock_disposals of type 'writeoff' dated From..To, quantity x the medicine's
                       cost per unit: a loss in the P&L and goods taken out of purchases (the
                       textbook entry). Disposals of type 'return' made a purchase return, which is
                       where they count.
  Capital & opening    The balancing figure: debits less credits of every account above. On the
  balances             Balance Sheet: + cash added + net profit - drawings = capital at the end.

Trial balance: Dr = Cr by construction (the balancing line). Closing stock is listed under the
totals, not in them, as Tally shows it: it is the P&L's and the Balance Sheet's. P&L: net sales -
cost of goods sold = gross profit; - expenses - stock written off = net profit. Balance sheet as on
To: capital + creditors + customers' advances + GST payable = stock + debtors + suppliers' credit +
cash + bank (+ GST input credit). An account below 0 changes column / side (a negative bank is shown
with the liabilities), as in any trial balance.

Routes (core/desktop_api.py hands /api/accounts/* here):
    GET  /api/accounts/report?from=&to=          the whole report (all three parts)
    GET  /api/accounts/trial-balance?from=&to=   the same report, view = trial_balance
    GET  /api/accounts/profit-loss?from=&to=     the same report, view = profit_loss
    GET  /api/accounts/balance-sheet?from=&to=   the same report, view = balance_sheet
    POST /api/accounts/export  {from, to, part: all|trial_balance|profit_loss|balance_sheet,
                                format: xlsx|pdf|csv, print_to: printer}
Online mode: the engine's database is an empty throwaway copy, so every route answers 409 with
ONLINE_REFUSAL instead of a report of zeroes.
"""
from __future__ import annotations

import base64
import sqlite3
from datetime import date, timedelta
from typing import Any, Optional

from core.cash_register import _alive, _cash_of, _cols, _f, _payment_cash, _rows, r2
from core.due_fifo import PURCHASE_ENTRY_PAID_SQL

# Cash-register categories that are the owner's drawings, not a shop expense (lower case).
DRAWINGS_CATEGORIES = ("owner took cash", "drawings", "drawing")

PARTS = ("trial_balance", "profit_loss", "balance_sheet")

ONLINE_REFUSAL = ("Accounts are worked out from this PC's own copy of the books, and Online mode keeps "
                  "none here -- every figure would read 0. Switch to Offline-first to see them.")


# ── dates ─────────────────────────────────────────────────────────────────────

def _dmy(day: str) -> str:
    return f"{day[8:10]}-{day[5:7]}-{day[0:4]}" if day and len(day) >= 10 else str(day or "")


def fy_default(today: Optional[date] = None) -> tuple:
    """(from, to, FY tag) of the financial year running today: 1 April .. today."""
    from core.fy_serial import fy_date_bounds, fy_label, fy_start_year_for_date

    t = today or date.today()
    y = fy_start_year_for_date(t)
    start, end = fy_date_bounds(y)
    return start, min(end, t.isoformat()), fy_label(y)


def clean_range(start, end) -> tuple:
    """ISO From / To; an empty From is the start of To's financial year, an empty To is today."""
    from core.cash_register import clean_day
    from core.fy_serial import fy_date_bounds, fy_start_year_for_date

    s, e = str(start or "").strip()[:10], str(end or "").strip()[:10]
    if not s and not e:
        s, e, _ = fy_default()
    elif not s:
        e = clean_day(e)
        s = fy_date_bounds(fy_start_year_for_date(e))[0]
    s, e = clean_day(s), clean_day(e)            # an empty To is today
    if e < s:
        s, e = e, s
    return s, e


def _between(col: str, start: Optional[str], end: Optional[str]) -> tuple:
    """A date filter on the first ten characters (a stamp with a time still counts on its day);
    None = no bound."""
    parts, params = [], []
    if start:
        parts.append(f"substr({col},1,10) >= ?")
        params.append(start)
    if end:
        parts.append(f"substr({col},1,10) <= ?")
        params.append(end)
    return (" AND ".join(parts) or "1=1"), params


def _next_day(day: str) -> str:
    return (date.fromisoformat(day) + timedelta(days=1)).isoformat()


# ── money moved: cash (the cash register's rule) and bank (the rest) ───────────

_CASH_KEYS = ("sales", "receipts", "purchase_returns", "cash_added",
              "sales_returns", "purchases", "supplier_payments", "expenses")
_BANK_KEYS = ("sales", "receipts", "purchase_returns", "sales_returns", "purchases", "supplier_payments")


def money_moved(conn: sqlite3.Connection, start: Optional[str], end: Optional[str]) -> dict:
    """Cash and bank moved by the books between start and end (None = no bound).

    Cash is read exactly as core/cash_register.py's day sections read it (summed over the dates),
    so the period's cash in / out equals the register's days added up. Bank is what the dues
    count as paid / received less that cash part, so cash + bank is always the money the
    debtors' and creditors' figures moved. walkin_paid_out: refunds of walk-in returns handed
    out (they settle the walk-in "party"'s credit)."""
    cash = dict.fromkeys(_CASH_KEYS, 0.0)
    bank = dict.fromkeys(_BANK_KEYS, 0.0)
    walkin_out = 0.0

    cols = _cols(conn, "sales")
    if cols:
        w, p = _between("s.bill_date", start, end)
        for r in _rows(conn, f"SELECT s.cash_paid, s.online_paid, s.amount_paid FROM sales s "
                             f"WHERE {w} AND {_alive(cols, 's', 'deleted', 'is_autosave')}", p):
            c = r2(_cash_of(r["cash_paid"], r["online_paid"], r["amount_paid"]))
            cash["sales"] += c
            bank["sales"] += _f(r["amount_paid"]) - c

    # Refund rows whose ref the register already counted as cash handed back (per day), and every
    # refund row's ref (for walk-in returns refunded online).
    cash_refs, all_refs = set(), set()
    cols = _cols(conn, "customer_payments")
    if cols:
        w, p = _between("p.payment_date", start, end)
        for r in _rows(conn, f"SELECT p.payment_date, p.amount, p.payment_mode, p.cash_amount, p.online_amount, "
                             f"p.reference_no FROM customer_payments p WHERE {w} AND {_alive(cols, 'p', 'deleted')}", p):
            amt = _f(r["amount"])
            c = r2(_payment_cash(r))
            if amt > 0:
                ce = c if c > 0 else 0.0              # cash_register.cash_receipts
                cash["receipts"] += ce
                bank["receipts"] += amt - ce
            elif amt < 0:
                ce = c if c < 0 else 0.0              # cash_register.cash_sales_returns (refund rows)
                cash["sales_returns"] += -ce
                bank["sales_returns"] += -(amt - ce)
                key = (str(r["payment_date"] or "")[:10], str(r["reference_no"] or ""))
                all_refs.add(key)
                if ce < 0:
                    cash_refs.add(key)

    cols = _cols(conn, "sales_returns")
    if cols and "settle_mode" in cols:
        # A walk-in return (no customer, so no refund row) settled in cash or online: the refund itself.
        w, p = _between("r.return_date", start, end)
        for r in _rows(conn, f"""
                SELECT r.return_date, r.return_no, r.refund_amount, LOWER(COALESCE(r.settle_mode,'')) AS mode
                FROM sales_returns r
                WHERE {w} AND COALESCE(r.customer_id, 0) = 0 AND LOWER(COALESCE(r.settle_mode,'')) IN ('cash','online')
                  AND {_alive(cols, 'r', 'deleted')}""", p):
            amt = r2(r["refund_amount"])
            if amt <= 0:
                continue
            key = (str(r["return_date"] or "")[:10], str(r["return_no"] or ""))
            if r["mode"] == "cash":
                if key in cash_refs:
                    continue
                cash["sales_returns"] += amt
            else:
                if key in all_refs:
                    continue
                bank["sales_returns"] += amt
            walkin_out += amt

    cols = _cols(conn, "purchases")
    if cols:
        w, p = _between("p.purchase_date", start, end)
        for r in _rows(conn, f"""
                SELECT p.cash_paid_at_entry AS c, p.online_paid_at_entry AS o, p.amount_paid_at_entry AS a,
                       {PURCHASE_ENTRY_PAID_SQL} AS paid
                FROM purchases p WHERE {w} AND {_alive(cols, 'p', 'deleted', 'is_autosave')}""", p):
            c = r2(_cash_of(r["c"], r["o"], r["a"]))
            ce = c if c > 0 else 0.0                  # cash_register.cash_purchases
            cash["purchases"] += ce
            bank["purchases"] += _f(r["paid"]) - ce

    cols = _cols(conn, "supplier_payments")
    if cols:
        w, p = _between("p.payment_date", start, end)
        for r in _rows(conn, f"SELECT p.amount, LOWER(COALESCE(p.mode,'cash'))='cash' AS is_cash "
                             f"FROM supplier_payments p WHERE {w} AND {_alive(cols, 'p', 'deleted')}", p):
            amt = _f(r["amount"])
            ce = r2(amt) if r["is_cash"] else 0.0     # cash_supplier_payments / cash_purchase_returns
            if amt > 0:
                cash["supplier_payments"] += max(ce, 0.0)
                bank["supplier_payments"] += amt - max(ce, 0.0)
            elif amt < 0:
                cash["purchase_returns"] += max(-ce, 0.0)
                bank["purchase_returns"] += -amt - max(-ce, 0.0)

    expenses: dict = {}
    drawings = 0.0
    cols = _cols(conn, "cash_expenses")
    if cols:
        w, p = _between("e.day", start, end)
        for r in _rows(conn, f"SELECT e.kind, e.category, e.amount FROM cash_expenses e "
                             f"WHERE {w} AND {_alive(cols, 'e', 'deleted')}", p):
            kind, amt = (r["kind"] or "out"), r2(r["amount"])
            if kind == "in":
                cash["cash_added"] += amt
            elif kind == "out":
                cash["expenses"] += amt
                cat = " ".join(str(r["category"] or "").split()) or "Other"
                if cat.lower() in DRAWINGS_CATEGORIES:
                    drawings += amt
                else:
                    expenses[cat] = expenses.get(cat, 0.0) + amt

    cash = {k: r2(v) for k, v in cash.items()}
    bank = {k: r2(v) for k, v in bank.items()}
    cash_in = r2(cash["sales"] + cash["receipts"] + cash["purchase_returns"] + cash["cash_added"])
    cash_out = r2(cash["sales_returns"] + cash["purchases"] + cash["supplier_payments"] + cash["expenses"])
    bank_in = r2(bank["sales"] + bank["receipts"] + bank["purchase_returns"])
    bank_out = r2(bank["sales_returns"] + bank["purchases"] + bank["supplier_payments"])
    return {"cash": cash, "bank": bank, "cash_in": cash_in, "cash_out": cash_out, "cash_net": r2(cash_in - cash_out),
            "bank_in": bank_in, "bank_out": bank_out, "bank_net": r2(bank_in - bank_out),
            "walkin_paid_out": r2(walkin_out),
            "expenses": {k: r2(v) for k, v in sorted(expenses.items(), key=lambda kv: (-kv[1], kv[0]))},
            "drawings": r2(drawings)}


def cash_in_hand(conn: sqlite3.Connection, end: str) -> dict:
    """Cash in the drawer as on ``end``: the last count typed (a closed day's counted cash, else a
    day's opening cash) plus the cash the books moved after it; with nothing typed, every cash
    movement in the books up to ``end``."""
    from core import cash_register as cr

    counted_day = opened_day = None
    if _cols(conn, "cash_days"):
        counted_day = conn.execute(
            "SELECT MAX(substr(day,1,10)) FROM cash_days WHERE substr(day,1,10) <= ? AND COALESCE(deleted,0)=0 "
            "AND counted_cash IS NOT NULL", (end,)).fetchone()[0]
        opened_day = conn.execute(
            "SELECT MAX(substr(day,1,10)) FROM cash_days WHERE substr(day,1,10) <= ? AND COALESCE(deleted,0)=0 "
            "AND opening_cash IS NOT NULL", (end,)).fetchone()[0]
    if counted_day and (not opened_day or counted_day >= opened_day):
        row = cr.day_row(conn, counted_day) or {}
        base = r2(row.get("counted_cash"))
        moved = money_moved(conn, _next_day(counted_day), end)["cash_net"] if counted_day < end else 0.0
        return {"amount": r2(base + moved), "basis": "counted", "day": counted_day, "anchor": base, "moved": moved,
                "note": f"counted on {_dmy(counted_day)}" + (f" + cash moved since ({moved:.2f})" if counted_day < end else "")}
    if opened_day:
        row = cr.day_row(conn, opened_day) or {}
        base = r2(row.get("opening_cash"))
        moved = money_moved(conn, opened_day, end)["cash_net"]
        return {"amount": r2(base + moved), "basis": "opening", "day": opened_day, "anchor": base, "moved": moved,
                "note": f"opening cash of {_dmy(opened_day)} + cash moved since ({moved:.2f})"}
    moved = money_moved(conn, None, end)["cash_net"]
    return {"amount": moved, "basis": "books", "day": None, "anchor": 0.0, "moved": moved,
            "note": "no opening or counted cash typed in Cash & Expenses: every cash entry in the books"}


# ── parties: debtors and creditors as on a date (the app's due rule) ──────────

def _net_parts(nets: dict) -> tuple:
    """(sum owed to us / by us, sum on the other side, how many parties each), with the dues'
    own rule that a paisa either way is nothing."""
    pos = neg = 0.0
    npos = nneg = 0
    for v in nets.values():
        v = r2(v)
        if abs(v) <= 0.01:
            continue
        if v > 0:
            pos += v
            npos += 1
        else:
            neg += -v
            nneg += 1
    return r2(pos), r2(neg), npos, nneg


def customer_balances(conn: sqlite3.Connection, end: str, walkin_paid_out: float = 0.0) -> dict:
    """recalculate_customer_due for every customer, with only the records dated up to ``end``.
    Customer 0 is the walk-in counter (bills with no customer)."""
    nets: dict = {}

    def add(sql: str, params, sign: float) -> None:
        for pid, v in conn.execute(sql, params).fetchall():
            nets[int(pid or 0)] = nets.get(int(pid or 0), 0.0) + sign * _f(v)

    cols = _cols(conn, "sales")
    if cols:
        w, p = _between("s.bill_date", None, end)
        add(f"SELECT COALESCE(s.customer_id,0), COALESCE(SUM(s.total_amount),0) - COALESCE(SUM(s.amount_paid),0) "
            f"FROM sales s WHERE {w} AND {_alive(cols, 's', 'deleted', 'is_autosave')} GROUP BY 1", p, 1)
    cols = _cols(conn, "customer_payments")
    if cols:
        w, p = _between("p.payment_date", None, end)
        add(f"SELECT COALESCE(p.customer_id,0), COALESCE(SUM(p.amount),0) FROM customer_payments p "
            f"WHERE {w} AND {_alive(cols, 'p', 'deleted')} GROUP BY 1", p, -1)
    cols = _cols(conn, "sales_returns")
    if cols:
        w, p = _between("r.return_date", None, end)
        add(f"SELECT COALESCE(r.customer_id,0), COALESCE(SUM(r.refund_amount),0) FROM sales_returns r "
            f"WHERE {w} AND {_alive(cols, 'r', 'deleted')} GROUP BY 1", p, -1)
    if walkin_paid_out:
        nets[0] = nets.get(0, 0.0) + walkin_paid_out
    due, credit, ndue, ncredit = _net_parts(nets)
    return {"debtors": due, "advances": credit, "debtor_count": ndue, "advance_count": ncredit,
            "walkin": r2(nets.get(0, 0.0))}


def supplier_balances(conn: sqlite3.Connection, end: str) -> dict:
    """recalculate_supplier_due for every supplier, with only the records dated up to ``end``.
    Supplier 0 holds purchases / payments / returns with no supplier."""
    nets: dict = {}

    def add(sql: str, params, sign: float) -> None:
        for pid, v in conn.execute(sql, params).fetchall():
            nets[int(pid or 0)] = nets.get(int(pid or 0), 0.0) + sign * _f(v)

    cols = _cols(conn, "purchases")
    if cols:
        w, p = _between("p.purchase_date", None, end)
        add(f"SELECT COALESCE(p.supplier_id,0), COALESCE(SUM(COALESCE(p.final_amount, p.total_amount)),0) "
            f"- COALESCE(SUM({PURCHASE_ENTRY_PAID_SQL}),0) FROM purchases p "
            f"WHERE {w} AND {_alive(cols, 'p', 'deleted', 'is_autosave')} GROUP BY 1", p, 1)
    cols = _cols(conn, "supplier_payments")
    if cols:
        w, p = _between("sp.payment_date", None, end)
        add(f"SELECT COALESCE(sp.supplier_id,0), COALESCE(SUM(sp.amount),0) FROM supplier_payments sp "
            f"WHERE {w} AND {_alive(cols, 'sp', 'deleted')} GROUP BY 1", p, -1)
    cols = _cols(conn, "purchase_returns")
    if cols:
        w, p = _between("pr.return_date", None, end)
        pcols = _cols(conn, "purchases")
        live_p = _alive(pcols, "p", "deleted")
        add(f"SELECT COALESCE(NULLIF(pr.supplier_id,0), CASE WHEN p.id IS NOT NULL AND {live_p} THEN p.supplier_id END, 0), "
            f"COALESCE(SUM(pr.refund_amount),0) FROM purchase_returns pr LEFT JOIN purchases p ON p.id = pr.purchase_id "
            f"WHERE {w} AND {_alive(cols, 'pr', 'deleted')} GROUP BY 1", p, -1)
    due, credit, ndue, ncredit = _net_parts(nets)
    return {"creditors": due, "advances": credit, "creditor_count": ndue, "advance_count": ncredit}


# ── stock and the cost of what was sold (the app's own profit rule) ────────────

def _cost_per_unit(cost_price, rate, med_type, unit) -> float:
    from core.stock_utils import effective_cost_per_unit

    return effective_cost_per_unit(cost_price, rate, str(med_type or ""), str(unit or "1"))


def stock_value_now(conn: sqlite3.Connection) -> dict:
    """Today's stock at purchase cost: stock_qty (above 0) x cost per unit of medicines.rate."""
    cols = _cols(conn, "medicines")
    if not cols:
        return {"value": 0.0, "items": 0, "no_rate": 0}
    value, items, no_rate = 0.0, 0, 0
    for qty, rate, typ, unit in conn.execute(
            f"SELECT stock_qty, rate, type, COALESCE(unit,'1') FROM medicines m "
            f"WHERE COALESCE(m.stock_qty,0) > 0 AND {_alive(cols, 'm', 'deleted')}").fetchall():
        cpu = _cost_per_unit(0, rate, typ, unit)
        items += 1
        if cpu <= 0:
            no_rate += 1
            continue
        value += r2(_f(qty) * cpu)
    return {"value": r2(value), "items": items, "no_rate": no_rate}


def cost_of_goods_sold(conn: sqlite3.Connection, start: str, end: str) -> dict:
    """Sum of qty x cost per unit over the bills' lines (Sales History's profit rule,
    core/stock_utils.sale_line_profit), less the same cost of the medicines returned."""
    sold = returned = 0.0
    no_cost_lines = 0
    scols, icols = _cols(conn, "sales"), _cols(conn, "sales_items")
    if scols and icols:
        w, p = _between("s.bill_date", start, end)
        for qty, cp, rate, typ, unit in conn.execute(f"""
                SELECT si.qty, si.cost_price, m.rate, m.type, COALESCE(m.unit,'1')
                FROM sales_items si JOIN sales s ON s.id = si.sale_id
                LEFT JOIN medicines m ON m.id = si.medicine_id
                WHERE {w} AND {_alive(scols, 's', 'deleted', 'is_autosave')} AND {_alive(icols, 'si', 'deleted')}""",
                p).fetchall():
            q = _f(qty)
            if q <= 0:
                continue
            cpu = _cost_per_unit(cp, rate, typ, unit)
            if cpu <= 0:
                no_cost_lines += 1
            sold += r2(q * cpu)
    rcols, ricols = _cols(conn, "sales_returns"), _cols(conn, "sales_return_items")
    if rcols and ricols and icols:
        w, p = _between("r.return_date", start, end)
        for qty, cp, rate, typ, unit in conn.execute(f"""
                SELECT ri.qty,
                       (SELECT si.cost_price FROM sales_items si
                        WHERE si.sale_id = r.sale_id AND si.medicine_id = ri.medicine_id ORDER BY si.id LIMIT 1),
                       m.rate, m.type, COALESCE(m.unit,'1')
                FROM sales_return_items ri JOIN sales_returns r ON r.id = ri.return_id
                LEFT JOIN medicines m ON m.id = ri.medicine_id
                WHERE {w} AND {_alive(rcols, 'r', 'deleted')} AND {_alive(ricols, 'ri', 'deleted')}""", p).fetchall():
            q = _f(qty)
            if q <= 0:
                continue
            returned += r2(q * _cost_per_unit(cp, rate, typ, unit))
    return {"sold": r2(sold), "returned": r2(returned), "cost": r2(sold - returned), "no_cost_lines": no_cost_lines}


def written_off(conn: sqlite3.Connection, start: str, end: str) -> float:
    """Write-offs (stock_disposals type 'writeoff') at the medicine's cost per unit. The quantity
    is in stock units: the disposal took exactly that off medicines.stock_qty."""
    cols = _cols(conn, "stock_disposals")
    if not cols:
        return 0.0
    w, p = _between("d.disposal_date", start, end)
    total = 0.0
    for qty, rate, typ, unit in conn.execute(f"""
            SELECT d.quantity, m.rate, m.type, COALESCE(m.unit,'1') FROM stock_disposals d
            LEFT JOIN medicines m ON m.id = d.medicine_id
            WHERE {w} AND LOWER(COALESCE(d.disposal_type,''))='writeoff' AND {_alive(cols, 'd', 'deleted')}""",
            p).fetchall():
        total += r2(_f(qty) * _cost_per_unit(0, rate, typ, unit))
    return r2(total)


# ── sales / purchases / GST of a period ───────────────────────────────────────

def _sum(conn: sqlite3.Connection, table: str, alias: str, expr: str, date_col: str, start, end, *flags) -> float:
    cols = _cols(conn, table)
    if not cols:
        return 0.0
    w, p = _between(f"{alias}.{date_col}", start, end)
    row = conn.execute(f"SELECT COALESCE(SUM({expr}),0) FROM {table} {alias} WHERE {w} AND {_alive(cols, alias, *flags)}",
                       p).fetchone()
    return r2(row[0] if row else 0)


def gst_enabled(conn: sqlite3.Connection) -> bool:
    """The shop prints GST on its bills (Settings -> Pharmacy): then GST is split out."""
    from core.gst_reports import _shop

    try:
        return bool(_shop(conn).get("gst_enabled"))
    except Exception:
        return False


def trading_figures(conn: sqlite3.Connection, start: str, end: str, split_gst: bool) -> dict:
    """Sales, returns, purchases, purchase returns of the period, and the GST taken out of them
    (core/gst_reports: the GSTR-3B figures of the same dates)."""
    out = {
        "sales_gross": _sum(conn, "sales", "s", "s.total_amount", "bill_date", start, end, "deleted", "is_autosave"),
        "returns_gross": _sum(conn, "sales_returns", "r", "r.refund_amount", "return_date", start, end, "deleted"),
        "purchases_gross": _sum(conn, "purchases", "p", "COALESCE(p.final_amount, p.total_amount)", "purchase_date",
                                start, end, "deleted", "is_autosave"),
        "preturns_gross": _sum(conn, "purchase_returns", "pr", "pr.refund_amount", "return_date", start, end, "deleted"),
        "discount": r2(_sum(conn, "sales", "s", "s.discount", "bill_date", start, end, "deleted", "is_autosave")
                       + _item_discount(conn, start, end)),
        "output_gst": 0.0, "notes_gst": 0.0, "itc": 0.0, "itc_reversed": 0.0, "net_itc": 0.0, "gst_checks": 0,
    }
    if split_gst:
        from core import gst_reports as gr

        p = gr.load_period(conn, start, end)
        g3 = gr.gstr3b(p)
        pay = g3["Tax payable before ITC"]
        out["output_gst"] = r2(_f(pay["igst"]) + _f(pay["cgst"]) + _f(pay["sgst"]))
        notes = gr.ZERO
        for n in p.notes:
            if not n.problem and n.bill and n.lines:
                notes += gr.note_tax(n).tax
        out["notes_gst"] = gr._f(notes)
        itc, rev, net = g3["4(A)(5) All other ITC"], g3["4(B)(2) Reversed: goods returned to suppliers"], \
            g3["4(C) Net ITC available"]
        out["itc"] = r2(_f(itc["igst"]) + _f(itc["cgst"]) + _f(itc["sgst"]))
        out["itc_reversed"] = r2(_f(rev["igst"]) + _f(rev["cgst"]) + _f(rev["sgst"]))
        out["net_itc"] = r2(_f(net["igst"]) + _f(net["cgst"]) + _f(net["sgst"]))
        out["gst_checks"] = len(p.checks)
    # Net of GST: the bills' tax (net of credit notes) out of sales, the notes' tax out of returns.
    out["sales"] = r2(out["sales_gross"] - out["output_gst"] - out["notes_gst"])
    out["returns"] = r2(out["returns_gross"] - out["notes_gst"])
    out["purchases"] = r2(out["purchases_gross"] - out["itc"])
    out["purchase_returns"] = r2(out["preturns_gross"] - out["itc_reversed"])
    return out


def _item_discount(conn: sqlite3.Connection, start: str, end: str) -> float:
    scols, icols = _cols(conn, "sales"), _cols(conn, "sales_items")
    if not scols or "item_discount" not in icols:
        return 0.0
    w, p = _between("s.bill_date", start, end)
    row = conn.execute(f"SELECT COALESCE(SUM(si.item_discount),0) FROM sales_items si JOIN sales s ON s.id = si.sale_id "
                       f"WHERE {w} AND {_alive(scols, 's', 'deleted', 'is_autosave')} AND {_alive(icols, 'si', 'deleted')}",
                       p).fetchone()
    return r2(row[0] if row else 0)


# ── the report ────────────────────────────────────────────────────────────────

def _n(count: int, word: str) -> str:
    return f"{count} {word}{'' if count == 1 else 's'}"


def _place(side: str, value: float) -> tuple:
    """(dr, cr) of an account whose natural side is ``side``; below 0 it changes column."""
    v = r2(value)
    if side == "dr":
        return (v, None) if v >= 0 else (None, -v)
    return (None, v) if v >= 0 else (-v, None)


def build(conn: sqlite3.Connection, start=None, end=None, *, today: Optional[str] = None) -> dict:
    start, end = clean_range(start, end)
    today = today or date.today().isoformat()
    from core.fy_serial import fy_label, fy_start_year_for_date

    split = gst_enabled(conn)
    t = trading_figures(conn, start, end, split)
    cogs = cost_of_goods_sold(conn, start, end)
    wo = written_off(conn, start, end)
    period_money = money_moved(conn, start, end)
    upto = money_moved(conn, None, end)

    # Closing stock: today's, worked back by value to the To date when it is earlier.
    now = stock_value_now(conn)
    closing = now["value"]
    worked_back = end < today
    if worked_back:
        after = _next_day(end)
        ta = trading_figures(conn, after, today, split)
        ca = cost_of_goods_sold(conn, after, today)
        wa = written_off(conn, after, today)
        closing = r2(closing - (ta["purchases"] - ta["purchase_returns"]) + ca["cost"] + wa)
    C, CS, W = cogs["cost"], closing, wo
    P, PR = t["purchases"], t["purchase_returns"]
    S, R = t["sales"], t["returns"]
    OS = r2(C + CS + W - P + PR)

    cash = cash_in_hand(conn, end)
    bank = upto["bank_net"]
    cust = customer_balances(conn, end, upto["walkin_paid_out"])
    sup = supplier_balances(conn, end)
    expenses = period_money["expenses"]
    CI, DW = period_money["cash"]["cash_added"], period_money["drawings"]
    OUT, IN = (t["output_gst"], t["net_itc"]) if split else (0.0, 0.0)

    # ── trial balance ──
    accounts = []

    def acc(group: str, name: str, side: str, value: float, note: str = "", key: str = "", always: bool = False):
        v = r2(value)
        if not v and not always:
            return
        dr, cr = _place(side, v)
        accounts.append({"key": key or name, "group": group, "account": name, "dr": dr, "cr": cr, "note": note})

    gst_word = " (GST taken out)" if split else ""
    acc("Capital", "Cash added by owner (capital introduced)", "cr", CI, "Cash & Expenses: lines typed as Cash added",
        "cash_added")
    acc("Capital", "Owner took cash (drawings)", "dr", DW, "Cash & Expenses: expenses in the category Owner took cash",
        "drawings")
    acc("Sales", f"Sales{gst_word}", "cr", S,
        f"bill totals {t['sales_gross']:.2f}" + (f" - output GST {r2(OUT + t['notes_gst']):.2f}" if split else ""),
        "sales", always=True)
    acc("Sales", f"Sales returns{gst_word}", "dr", R,
        f"refunds {t['returns_gross']:.2f}" + (f" - GST on credit notes {t['notes_gst']:.2f}" if split else ""),
        "sales_returns")
    acc("Purchases", f"Purchases{gst_word}", "dr", P,
        f"supplier bills {t['purchases_gross']:.2f}" + (f" - ITC {t['itc']:.2f}" if split else ""), "purchases",
        always=True)
    acc("Purchases", f"Purchase returns{gst_word}", "cr", PR,
        f"returns {t['preturns_gross']:.2f}" + (f" - ITC reversed {t['itc_reversed']:.2f}" if split else ""),
        "purchase_returns")
    acc("Stock", "Opening stock (worked back)", "dr", OS,
        "cost of goods sold + closing stock + goods written off - purchases + purchase returns", "opening_stock",
        always=True)
    acc("Stock", "Goods written off (taken out of purchases)", "cr", W, "stock written off in these dates", "goods_written_off")
    acc("Indirect expenses", "Stock written off (expired / damaged)", "dr", W,
        "write-offs x purchase cost per unit", "stock_written_off")
    for cat, amt in expenses.items():
        acc("Indirect expenses", cat, "dr", amt, "Cash & Expenses", f"expense:{cat}")
    if split:
        acc("Duties & taxes", "Output GST", "cr", OUT, "GSTR-3B tax payable (bills less credit notes)", "output_gst",
            always=True)
        acc("Duties & taxes", "Input GST (ITC)", "dr", IN, "GSTR-3B 4(C) net ITC", "input_gst", always=True)
    acc("Sundry debtors", "Sundry debtors (customers' dues)", "dr", cust["debtors"],
        f"{_n(cust['debtor_count'], 'customer')} owing, as on {_dmy(end)}", "debtors", always=True)
    acc("Sundry debtors", "Customers' advance / credit", "cr", cust["advances"],
        f"{_n(cust['advance_count'], 'customer')} in credit", "customer_advances")
    acc("Sundry creditors", "Sundry creditors (suppliers' dues)", "cr", sup["creditors"],
        f"{_n(sup['creditor_count'], 'supplier')} to pay, as on {_dmy(end)}", "creditors", always=True)
    acc("Sundry creditors", "Credit with suppliers (advance / returns)", "dr", sup["advances"],
        f"{_n(sup['advance_count'], 'supplier')} owing us", "supplier_advances")
    acc("Cash & bank", "Cash in hand", "dr", cash["amount"], cash["note"], "cash", always=True)
    acc("Cash & bank", "Bank (online money)", "dr", bank, "online received - online paid, up to the To date", "bank",
        always=True)
    other_dr = r2(sum(a["dr"] or 0 for a in accounts))
    other_cr = r2(sum(a["cr"] or 0 for a in accounts))
    K = r2(other_dr - other_cr)                   # credit when positive
    kdr, kcr = _place("cr", K)
    accounts.insert(0, {"key": "capital", "group": "Capital", "account": "Capital & opening balances (balancing figure)",
                        "dr": kdr, "cr": kcr,
                        "note": "what the app does not track: opening cash / stock / dues, owner's capital, bank "
                                "deposits, GST paid"})
    total_dr = r2(sum(a["dr"] or 0 for a in accounts))
    total_cr = r2(sum(a["cr"] or 0 for a in accounts))

    # ── profit & loss ──
    NS = r2(S - R)
    GP = r2(NS - C)
    EXP = r2(sum(expenses.values()))
    NP = r2(GP - EXP - W)
    pl = [
        {"kind": "head", "label": "Income"},
        {"kind": "line", "label": f"Sales{gst_word}", "amount": S},
        {"kind": "line", "label": "Less: sales returns", "amount": -R},
        {"kind": "sub", "label": "Net sales", "amount": NS},
        {"kind": "head", "label": "Cost of goods sold"},
        {"kind": "line", "label": "Opening stock (worked back)", "amount": OS},
        {"kind": "line", "label": "Add: purchases", "amount": P},
        {"kind": "line", "label": "Less: purchase returns", "amount": -PR},
        {"kind": "line", "label": "Less: goods written off", "amount": -W},
        {"kind": "line", "label": f"Less: closing stock{' (worked back to ' + _dmy(end) + ')' if worked_back else ''}",
         "amount": -CS},
        {"kind": "sub", "label": "Cost of goods sold", "amount": C,
         "note": f"the cost kept on each medicine sold ({cogs['sold']:.2f}) less returned ({cogs['returned']:.2f})"},
        {"kind": "total", "label": "Gross profit" if GP >= 0 else "Gross loss", "amount": GP},
        {"kind": "head", "label": "Expenses"},
    ]
    for cat, amt in expenses.items():
        pl.append({"kind": "line", "label": cat, "amount": amt})
    pl.append({"kind": "line", "label": "Stock written off (expired / damaged)", "amount": W})
    pl.append({"kind": "sub", "label": "Total expenses", "amount": r2(EXP + W)})
    pl.append({"kind": "total", "label": "Net profit" if NP >= 0 else "Net loss", "amount": NP})
    pl_notes = []
    if t["discount"]:
        pl_notes.append(f"Discount given on bills in these dates: {t['discount']:.2f} (already off the sales figure).")
    if DW:
        pl_notes.append(f"Owner took cash {DW:.2f}: drawings, shown on the Balance Sheet, not an expense.")

    # ── balance sheet ──
    KE = r2(K + CI + NP - DW)
    gst_net = r2(OUT - IN)
    capital = [
        {"kind": "head", "label": "Capital"},
        {"kind": "line", "label": "Capital & opening balances (balancing figure)", "amount": K},
        {"kind": "line", "label": "Add: cash added by owner", "amount": CI},
        {"kind": "line", "label": "Add: net profit" if NP >= 0 else "Less: net loss", "amount": NP},
        {"kind": "line", "label": "Less: owner took cash (drawings)", "amount": -DW},
        {"kind": "sub", "label": "Capital at the end", "amount": KE},
    ]
    others, assets = [], []          # the liabilities below capital; the assets

    def item(side_list: list, other: list, label: str, value: float, neg_label: str, always: bool = False, note=""):
        """A balance on its side; below 0 it is shown on the other side, as its opposite."""
        v = r2(value)
        if v < 0:
            other.append({"kind": "line", "label": neg_label, "amount": -v, "note": note})
        elif v or always:
            side_list.append({"kind": "line", "label": label, "amount": v, "note": note})

    item(others, assets, "Sundry creditors (suppliers' dues)", sup["creditors"], "", always=True)
    item(others, assets, "Customers' advance / credit", cust["advances"], "")
    if split:
        item(others, assets, "GST payable (output - input)", gst_net, "GST input credit (input more than output)")
    item(assets, others, f"Closing stock (at purchase rate){' worked back' if worked_back else ''}", CS,
         "Closing stock (below 0)", always=True)
    item(assets, others, "Sundry debtors (customers' dues)", cust["debtors"], "", always=True)
    item(assets, others, "Credit with suppliers (advance / returns)", sup["advances"], "")
    item(assets, others, "Cash in hand", cash["amount"], "Cash in hand below 0 (more paid out than the books took in)",
         always=True, note=cash["note"])
    item(assets, others, "Bank (online money)", bank, "Bank below 0 (more paid online than received online)",
         always=True)
    liab = capital + others
    TL = r2(KE + sum(x["amount"] for x in others))
    TA = r2(sum(x["amount"] for x in assets))

    checks = []
    if worked_back:
        checks.append(f"The app keeps only today's stock: closing stock on {_dmy(end)} is worked back from today's "
                      f"({now['value']:.2f}) by the purchases, sales and write-offs after it.")
    if now["no_rate"]:
        checks.append(f"{now['no_rate']} medicines in stock have no purchase rate: they are valued at 0 in the stock.")
    if cogs["no_cost_lines"]:
        checks.append(f"{cogs['no_cost_lines']} sale lines have no purchase cost (none on the line, no rate on the "
                      "medicine): their cost is taken as 0, so the profit is higher by it.")
    if OS < 0:
        checks.append("The opening stock worked back is below 0: more was bought than today's stock and the cost of "
                      "what was sold explain. Usually purchase rates are missing on medicines or stock was corrected "
                      "by hand.")
    if t["gst_checks"]:
        checks.append(f"GST Reports lists {t['gst_checks']} things to look at for these dates (Checks): the GST here "
                      "is that report's.")
    if not split:
        checks.append("The bills do not print GST (Settings -> Pharmacy): sales and purchases are shown with the GST "
                      "inside them, and there is no GST account.")
    if abs(cust["walkin"]) > 0.01:
        checks.append(f"Bills with no customer (walk-in) are {abs(cust['walkin']):.2f} "
                      f"{'unpaid' if cust['walkin'] > 0 else 'overpaid / not yet refunded'}: counted in "
                      f"{'debtors' if cust['walkin'] > 0 else 'customers advance'} here; the Dues screens list "
                      "named customers only.")
    if bank < 0:
        checks.append("Bank is below 0: more was paid online than received online in the books. Money put into the "
                      "bank in cash is not in the app; it sits in the balancing figure.")

    y = fy_start_year_for_date(start)
    return {
        "ok": True,
        "from": start, "to": end, "from_dmy": _dmy(start), "to_dmy": _dmy(end), "fy": fy_label(y),
        "today": today, "gst_split": split, "stock_worked_back": worked_back,
        "trial_balance": {
            "rows": accounts, "total_dr": total_dr, "total_cr": total_cr, "difference": r2(total_dr - total_cr),
            "memo": [{"account": "Closing stock", "amount": CS,
                      "note": "not in the totals: it goes to the Profit & Loss and the Balance Sheet"}],
        },
        "profit_loss": {"rows": pl, "net_sales": NS, "cost_of_goods_sold": C, "gross_profit": GP, "expenses": EXP,
                        "written_off": W, "net_profit": NP, "notes": pl_notes},
        "balance_sheet": {"as_on": end, "as_on_dmy": _dmy(end), "liabilities": liab, "assets": assets,
                          "total_liabilities": TL, "total_assets": TA, "difference": r2(TL - TA)},
        "figures": {"sales": S, "sales_returns": R, "purchases": P, "purchase_returns": PR, "opening_stock": OS,
                    "closing_stock": CS, "cost_of_goods_sold": C, "written_off": W, "output_gst": OUT,
                    "input_gst": IN, "debtors": cust["debtors"], "customer_advances": cust["advances"],
                    "creditors": sup["creditors"], "supplier_advances": sup["advances"], "cash": cash["amount"],
                    "bank": bank, "capital_introduced": CI, "drawings": DW, "expenses": EXP, "balancing": K,
                    "capital_end": KE, "net_profit": NP},
        "trading": t, "cash_basis": cash, "money_moved": period_money,
        "checks": checks,
    }


# ── on paper / in a file ──────────────────────────────────────────────────────

def _money(v) -> Any:
    return "" if v is None else r2(v)


def _pl_rows(rows: list) -> tuple:
    out, bold = [], []
    for r in rows:
        if r["kind"] == "head":
            bold.append(len(out))
            out.append([r["label"], ""])
        else:
            if r["kind"] in ("sub", "total"):
                bold.append(len(out))
            out.append([("    " if r["kind"] == "line" else "") + r["label"], _money(r.get("amount"))])
    return out, bold


def sections(rep: dict, part: str = "all") -> list:
    """The report as titled tables for Excel / CSV / PDF / paper."""
    out = []
    if part in ("all", "trial_balance"):
        tb = rep["trial_balance"]
        rows = [[a["account"], a["group"], _money(a["dr"]), _money(a["cr"]), a["note"]] for a in tb["rows"]]
        bold = [len(rows)]
        rows.append(["Total", "", tb["total_dr"], tb["total_cr"], ""])
        for m in tb["memo"]:
            rows.append([m["account"], "", "", "", f"{r2(m['amount']):.2f} -- {m['note']}"])
        out.append({"title": f"Trial Balance {rep['from_dmy']} to {rep['to_dmy']}", "sheet": "Trial Balance",
                    "columns": ["Account", "Group", "Dr (Rs)", "Cr (Rs)", "Worked out from"], "rows": rows,
                    "bold": bold})
    if part in ("all", "profit_loss"):
        pl = rep["profit_loss"]
        rows, bold = _pl_rows(pl["rows"])
        for n in pl["notes"]:
            rows.append([n, ""])
        out.append({"title": f"Profit & Loss {rep['from_dmy']} to {rep['to_dmy']}", "sheet": "Profit & Loss",
                    "columns": ["Particulars", "Rs"], "rows": rows, "bold": bold})
    if part in ("all", "balance_sheet"):
        bs = rep["balance_sheet"]
        for key, title, total in (("liabilities", "Liabilities", bs["total_liabilities"]),
                                  ("assets", "Assets", bs["total_assets"])):
            rows, bold = _pl_rows(bs[key])
            bold.append(len(rows))
            rows.append(["Total", total])
            out.append({"title": f"Balance Sheet as on {bs['as_on_dmy']} -- {title}", "sheet": "Balance Sheet",
                        "columns": [title, "Rs"], "rows": rows, "bold": bold})
    if part == "all" and rep.get("checks"):
        out.append({"title": "Checks", "sheet": "Checks", "columns": ["What to look at"],
                    "rows": [[c] for c in rep["checks"]]})
    return out


def _inr(v) -> str:
    """12,34,567.89 (the Indian grouping) for paper."""
    if v in ("", None) or isinstance(v, bool) or not isinstance(v, (int, float)):
        return "" if v is None else str(v)
    neg = v < 0
    whole, frac = f"{abs(v):.2f}".split(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        whole = ",".join(groups + [tail])
    return f"{'-' if neg else ''}{whole}.{frac}"


def export(conn: sqlite3.Connection, body: dict) -> dict:
    fmt = str(body.get("format") or "pdf").strip().lower()
    print_to = str(body.get("print_to") or "").strip().lower()
    part = str(body.get("part") or "all").strip().lower().replace("-", "_")
    if part not in PARTS:
        part = "all"
    rep = build(conn, body.get("from"), body.get("to"))
    secs = sections(rep, part)
    names = {"all": "Accounts", "trial_balance": "Trial_Balance", "profit_loss": "Profit_Loss",
             "balance_sheet": "Balance_Sheet"}
    stem = f"{names[part]}_{rep['from_dmy']}_{rep['to_dmy']}"
    title = f"{names[part].replace('_', ' ').replace('Profit Loss', 'Profit & Loss')} {rep['from_dmy']} - {rep['to_dmy']}"
    from core.cash_register import _shop_name, _xlsx_bytes

    shop = _shop_name(conn)
    if shop:
        title = f"{title} -- {shop}"
    if fmt in ("xlsx", "excel") and not print_to:
        data = _xlsx_bytes(title, secs)
        return {"ok": True, "filename": f"{stem}.xlsx", "format": "xlsx",
                "mime": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "content_base64": base64.b64encode(data).decode("ascii")}
    # Paper and PDF get the figures as text (12,34,567.00); CSV keeps plain numbers.
    paper = fmt != "csv" or bool(print_to)
    flat = [{"title": s["title"], "columns": s["columns"],
             "rows": [[(_inr(v) if paper else (f"{v:.2f}" if isinstance(v, float) else v)) for v in r]
                      for r in s["rows"]]} for s in secs]
    from core.desktop_export_service import export_alert_sections

    res = export_alert_sections({"title": title, "sections": flat, "format": "csv" if fmt == "csv" else "pdf",
                                 "print_to": print_to, "page_layout": body.get("page_layout") or "portrait",
                                 "paper": body.get("paper")})
    if res.get("ok") and res.get("filename"):
        res["filename"] = f"{stem}.{res.get('format') or 'pdf'}"
    return res


# ── routes ────────────────────────────────────────────────────────────────────

_VIEWS = {"/api/accounts/report": "all", "/api/accounts/trial-balance": "trial_balance",
          "/api/accounts/profit-loss": "profit_loss", "/api/accounts/balance-sheet": "balance_sheet"}


def _q(qs: dict, key: str) -> str:
    v = qs.get(key)
    if isinstance(v, (list, tuple)):
        v = v[0] if v else ""
    return str(v or "").strip()


def _online_shell() -> bool:
    try:
        from core.sync_prefs import is_online_mode

        return bool(is_online_mode())
    except Exception:
        return False


def handle_get(path: str, qs: dict, conn) -> Optional[tuple]:
    if not path.startswith("/api/accounts/"):
        return None
    view = _VIEWS.get(path)
    if view is None:
        return None
    if _online_shell():
        # Online the engine's connection is a throwaway :memory: copy: a report from it would be
        # a page of zeroes that looks like the shop's books.
        return 409, {"ok": False, "online_mode": True, "error": ONLINE_REFUSAL}
    if conn is None:
        return 503, {"ok": False, "error": "Database not open"}
    try:
        out = build(conn, _q(qs, "from"), _q(qs, "to"))
        out["view"] = view
        return 200, out
    except Exception as exc:
        return 400, {"ok": False, "error": str(exc)}


def handle_post(path: str, body: dict, conn) -> Optional[tuple]:
    if path != "/api/accounts/export":
        return None
    if _online_shell():
        return 409, {"ok": False, "online_mode": True, "error": ONLINE_REFUSAL}
    if conn is None:
        return 503, {"ok": False, "error": "Database not open"}
    try:
        out = export(conn, body if isinstance(body, dict) else {})
        return (200 if out.get("ok") else 400), out
    except Exception as exc:
        return 400, {"ok": False, "error": str(exc)}
