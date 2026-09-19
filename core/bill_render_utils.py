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


def _ctx_money(ctx, *names: str) -> float:
    for name in names:
        try:
            return float(getattr(ctx, name, 0) or 0)
        except (TypeError, ValueError):
            continue
    return 0.0


def bill_due_display(ctx) -> float:
    """Unpaid portion of this bill only: total - paid."""
    grand = _ctx_money(ctx, "grand_total")
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
    grand = _ctx_money(ctx, "grand_total")
    paid = _ctx_money(ctx, "amount_paid")
    prev_balance = prev - prev_cr
    return round(max(0.0, prev_balance + grand - paid), 2)
