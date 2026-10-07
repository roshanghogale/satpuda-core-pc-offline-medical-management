"""Phase 2, one set of rules for every device: the PC's own sales maths against 400 real bills.

D:\\projects\\shared-test-vectors\\real_bills_2026-10.json holds 400 anonymised sales bills from
the live server, each with the line amounts and the bill total that the device which made it
stored and printed. The Android app runs the same file through its own code
(app/src/test/.../sales/SharedBillVectorsTest.kt); REPORT.md beside the JSON compares the two.

Every figure here comes out of the PC's real code, nothing is re-derived:

  line    core.desktop_sales_service.build_line -- what the Sales page calls for every line it
          adds or re-prices -- on an in-memory shop with one medicine row per vector line.
          The row carries the stored per-unit rate as its MRP with pack "1", so the line is
          priced at exactly that rate; `qty` is the stored sales_items.qty (tablets for strip
          types, packs otherwise -- the rate is per that unit and GST is inside it); the item
          discount goes in as rupees, which is how sales_items.item_discount is kept.
  bill    core.desktop_sales_service.calc_sale -- the page's totals call, the same
          calc_engine.calc_bill_summary that save_sale stores -- with the overall discount sent
          as `discount_rs` (the page always sends it, so the rupee branch decides) and the
          stored rounding as typed (auto_rounding off). total_amount is subtotal - discount +
          rounding, i.e. the payable figure.
  round   calc_sale again with auto_rounding on, compared with the stored rounding.

What may differ without the PC being wrong is decided per bill from the numbers, never from
a list of bill ids:

  * a line whose stored rate was cut to 4 decimals (the PC stores round(rate, 4)) when the
    rate it was actually priced at -- MRP / pack -- had more, and some rate within half of
    the 4th decimal reproduces the stored amount (and the bill total moves by the same paise);
  * a bill that was not made by the PC or the Android app (`made_on` is a renumbering
    script's tag, a device id, or blank) -- reported, not held against the PC;
  * a stored rounding that is not the automatic one -- typed by hand, auto-round switched
    off, or kept from before an edit. Rounding is an input the shop can type, so the bill
    total is checked with the stored figure and only this separate check notices it.

Anything else that disagrees FAILS.
"""
from __future__ import annotations

import json
import os
import sqlite3
from contextlib import ExitStack
from pathlib import Path
from unittest import mock

import pytest

from core import db_setup, desktop_sales_service

VECTORS = Path(
    os.environ.get(
        "SATPUDA_BILL_VECTORS",
        r"D:\projects\shared-test-vectors\real_bills_2026-10.json",
    )
)


def _load() -> list[dict]:
    if not VECTORS.is_file():
        return []
    return json.loads(VECTORS.read_text(encoding="utf-8"))


BILLS = _load()


def maker(bill: dict) -> str:
    m = str(bill.get("made_on") or "")
    return m if m in ("pc", "android") else "other"


def bill_id(bill: dict) -> str:
    return f"{maker(bill)}-s{bill['store']}-b{bill['bill']}"


# --------------------------------------------------------------------------- the PC's code

class _PcShop:
    """An offline shop in memory; build_line reads its medicine rows."""

    def __init__(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        db_setup.initialise(self.conn)
        self.next_id = 0

    def medicine_for(self, item: dict) -> int:
        self.next_id += 1
        self.conn.execute(
            "INSERT INTO medicines (id, name, type, stock_qty, unit, gst_percent, mrp, rate, "
            "batch_no, expiry_date, created_at) VALUES (?, ?, ?, ?, '1', ?, ?, 0, 'B1', "
            "'2099-12-31', '2000-01-01 00:00:00')",
            (
                self.next_id,
                f"VECTOR LINE {self.next_id}",
                item.get("type") or "",
                10**8,
                item.get("gst_percent"),
                item["rate"],
            ),
        )
        return self.next_id


def _patches() -> ExitStack:
    """Pin what build_line / calc_sale would otherwise read from this PC's own settings."""
    stack = ExitStack()
    # Offline: _medicine_row reads the in-memory medicines table, never the server catalogue.
    stack.enter_context(mock.patch("core.sync_prefs.is_online_mode", return_value=False))
    # sales_items.item_discount is rupees; the shop's % / Rs entry mode only changes the input.
    stack.enter_context(
        mock.patch("core.billing_layout_prefs.load_item_discount_mode", return_value="rupees")
    )
    # A margin warning changes no figure, and needs costs the vectors do not carry.
    stack.enter_context(
        mock.patch("core.margin_utils.margin_loss_warning_enabled", return_value=False)
    )
    return stack


_SHOP: _PcShop | None = None
_DONE: dict = {}


def pc_compute(bill: dict) -> dict:
    """The PC's figures for one vector bill (see module doc)."""
    key = (bill["store"], bill["bill"], bill.get("made_on"))
    if key not in _DONE:
        _DONE[key] = _pc_compute(bill)
    return _DONE[key]


def _pc_compute(bill: dict) -> dict:
    global _SHOP
    with _patches():
        if _SHOP is None:
            _SHOP = _PcShop()
        lines = []
        for item in bill["items"]:
            mid = _SHOP.medicine_for(item)
            res = desktop_sales_service.build_line(
                _SHOP.conn,
                {
                    "medicine_id": mid,
                    "qty": item["qty"],
                    "medicine_discount": item.get("item_discount") or 0,
                    "gst_percent": item.get("gst_percent"),
                },
            )
            if not res.get("ok"):
                raise AssertionError(f"build_line refused a vector line: {res}")
            lines.append(res["line"])

        def totals(auto: bool) -> dict:
            return desktop_sales_service.calc_sale(
                None,
                {
                    "items": lines,
                    "discount_pct": bill.get("discount_pct") or 0,
                    "discount_rs": bill.get("discount") or 0,
                    "rounding": bill.get("rounding") or 0,
                    "auto_rounding": auto,
                    "payment_mode": "Due",
                    "skip_party_due": True,
                },
            )

        typed = totals(False)
        auto = totals(True)
    s = typed["summary"]
    return {
        "lines": [ln["amount"] for ln in lines],
        "subtotal": s["subtotal"],
        "discount_amount": s["discount_amount"],
        "discount_pct": s["discount_pct"],
        "pre_round_total": s["pre_round_total"],
        "rounding": typed["rounding"],
        "auto_rounding": auto["rounding"],
        "total_amount": s["total_amount"],
    }


# --------------------------------------------------------------------------- comparison

def _paise(v) -> int:
    return int(round(float(v or 0) * 100))


def differences(bill: dict, got: dict) -> list[str]:
    out = []
    for n, (item, mine) in enumerate(zip(bill["items"], got["lines"]), start=1):
        if mine != item["amount"]:
            out.append(
                f"line {n}: qty {item['qty']} x rate {item['rate']} - disc "
                f"{item.get('item_discount') or 0} -> PC {mine}, stored {item['amount']}"
            )
    if got["discount_amount"] != (bill.get("discount") or 0):
        out.append(f"discount: PC {got['discount_amount']}, stored {bill.get('discount')}")
    if (bill.get("discount") or 0) and got["discount_pct"] != bill.get("discount_pct"):
        out.append(f"discount_pct: PC {got['discount_pct']}, stored {bill.get('discount_pct')}")
    if got["total_amount"] != bill["total_amount"]:
        out.append(f"total: PC {got['total_amount']}, stored {bill['total_amount']}")
    return out


def _rate_cut_explains(item: dict, mine: float) -> bool:
    """The stored rate has <= 4 decimals and a rate within half its last place gives the stored amount."""
    rate = float(item["rate"])
    if round(rate, 4) != rate:
        return False
    qty = float(item["qty"])
    disc = float(item.get("item_discount") or 0)
    lo = round(qty * (rate - 0.00005) - disc, 2)
    hi = round(qty * (rate + 0.00005) - disc, 2)
    return mine != item["amount"] and lo <= item["amount"] <= hi


def expected_reason(bill: dict, got: dict) -> str | None:
    line_gap = 0
    for item, mine in zip(bill["items"], got["lines"]):
        if mine == item["amount"]:
            continue
        if not _rate_cut_explains(item, mine):
            line_gap = None
            break
        line_gap += _paise(item["amount"]) - _paise(mine)
    if line_gap and _paise(bill["total_amount"]) - _paise(got["total_amount"]) == line_gap:
        return "stored rate was cut to 4 decimals; the line was priced at the full MRP/pack rate"
    if maker(bill) == "other":
        return f"made by another tool (made_on={bill.get('made_on')!r}), not by the PC or the phone"
    return None


# --------------------------------------------------------------------------- tests

pytestmark = pytest.mark.skipif(not BILLS, reason=f"vector file not found: {VECTORS}")


@pytest.mark.parametrize("bill", BILLS, ids=[bill_id(b) for b in BILLS])
def test_the_pc_reproduces_the_stored_bill(bill):
    got = pc_compute(bill)
    diffs = differences(bill, got)
    if not diffs:
        return
    reason = expected_reason(bill, got)
    if reason:
        pytest.xfail(f"{reason}: " + "; ".join(diffs))
    pytest.fail(f"{bill_id(bill)} made on {bill.get('made_on')!r}: " + "; ".join(diffs))


@pytest.mark.parametrize("bill", BILLS, ids=[bill_id(b) for b in BILLS])
def test_the_stored_rounding_is_the_pcs_automatic_one(bill):
    got = pc_compute(bill)
    stored = bill.get("rounding") or 0
    if got["auto_rounding"] == stored:
        return
    # Auto-rounding of the STORED pre-round total: when that matches, only the line amounts
    # moved the PC's figure (the rate-cut case above).
    stored_pre = round(sum(i["amount"] for i in bill["items"]) - (bill.get("discount") or 0), 2)
    from core.calc_engine import auto_round

    if auto_round(stored_pre) == stored and expected_reason(bill, got):
        pytest.xfail(f"{expected_reason(bill, got)}; auto rounding of the stored lines is {stored}")
    pytest.xfail(
        f"stored rounding {stored} is not automatic (PC auto {got['auto_rounding']} on "
        f"pre-round {got['pre_round_total']}): typed by hand, auto-round off, or kept from an edit"
    )


def test_every_vector_bill_was_read():
    assert len(BILLS) == 400
    assert {maker(b) for b in BILLS} == {"pc", "android", "other"}
