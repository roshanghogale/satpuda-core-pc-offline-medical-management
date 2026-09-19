"""Tax invoice — the classic GST bill layout under a TAX INVOICE title."""
from __future__ import annotations

from typing import Any, Dict

from core.bill_config import BillContext
from bill_templates.classic import render_classic_bill_html


def render_legacy_bill_html(ctx: BillContext, settings: Dict[str, Any]) -> str:
    """Legacy template id → classic bill layout with TAX INVOICE title.

    It used to print no GST at all. A Tax Invoice has to state its taxable value and
    tax, so the GST strip and GST row follow "GST amount row" as on the GST Invoice.
    """
    merged = dict(settings or {})
    merged["template"] = "legacy"
    return render_classic_bill_html(ctx, merged)
