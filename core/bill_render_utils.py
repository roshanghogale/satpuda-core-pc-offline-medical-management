"""Shared helpers for bill HTML templates."""
from __future__ import annotations

from typing import Any


def esc(text: Any) -> str:
    return (
        str(text or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def fmt_expiry_mm_yy(raw: str) -> str:
    if not raw:
        return ""
    text = str(raw).strip()
    if len(text) >= 7 and text[4] == "-":
        parts = text.split("-")
        if len(parts) >= 2:
            return f"{parts[1][:2]}/{parts[0][2:]}"
    return text


def fmt_return_date(raw: Any) -> str:
    """dd-mm-yyyy for a Return row ("Return SR1-7 17-10-2026"), as the web prints it."""
    text = str(raw or "").strip()[:10]
    parts = text.split("-")
    if len(parts) == 3 and len(parts[0]) == 4:
        return f"{parts[2]}-{parts[1]}-{parts[0]}"
    return text


def return_row_label(row) -> str:
    """ "Return SR1-7 17-10-2026", the centred row that opens a return settled in a bill
    (core.bill_config.BillReturnRow). The customer is the bill's own, so no name."""
    return f"Return {row.return_no} {fmt_return_date(row.return_date)}".strip()


def return_total_label(row) -> str:
    """The row closing a return under its medicines: "Return total", and "(Rs 5.00 of Rs 10.00
    used in this bill)" -- with the rupee sign -- when part of it went to other bills."""
    text = "Return total"
    if float(row.refund or 0) > float(row.amount or 0) + 0.009:
        rs = "₹"
        text += f" ({rs}{float(row.amount):.2f} of {rs}{float(row.refund):.2f} used in this bill)"
    return text


def returns_total_of(ctx) -> float:
    """What the returns settled in this bill took off it (0 when none)."""
    return round(_ctx_money(ctx, "returns_total"), 2)


def _ctx_money(ctx, *names: str) -> float:
    for name in names:
        try:
            return float(getattr(ctx, name, 0) or 0)
        except (TypeError, ValueError):
            continue
    return 0.0


def bill_due_display(ctx) -> float:
    """Unpaid portion of this bill only: total - returns settled in it - paid."""
    grand = _ctx_money(ctx, "grand_total") - returns_total_of(ctx)
    paid = _ctx_money(ctx, "amount_paid")
    return round(max(0.0, grand - paid), 2)


def total_due_display(ctx) -> float:
    """
    Customer balance still owed after this bill: previous balance + this bill.

    The previous balance is SIGNED. It used to be clamped at zero --
    max(0, due - credit) -- which let credit cancel a previous DUE but never
    come off the bill in the customer's hand. So a customer holding Rs 500 with
    nothing owing was shown "Total Due 0" on the counter screen and handed a
    printed bill saying they owed the whole Rs 400. The screen and the paper
    disagreed, in front of the customer, about money.

    core.calc_engine.calc_payment_result carries the same figure as
    net_total_due; this is the printing side of it.
    """
    prev = _ctx_money(ctx, "previous_due")
    prev_cr = _ctx_money(ctx, "previous_credit")
    # Returns settled in this bill pay part of it (their credit is not in previous_credit).
    grand = _ctx_money(ctx, "grand_total") - returns_total_of(ctx)
    paid = _ctx_money(ctx, "amount_paid")
    prev_balance = prev - prev_cr
    return round(max(0.0, prev_balance + grand - paid), 2)
