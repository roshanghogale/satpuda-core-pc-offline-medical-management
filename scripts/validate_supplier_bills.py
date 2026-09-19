"""
Verify slab GST engine against known supplier bills.
Run: python scripts/validate_supplier_bills.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core.purchase_calculator import PurchaseCalculator  # noqa: E402
from core.purchase_invoice_engine import detect_purchase_gst_calc_method  # noqa: E402
from core.bill_import_normalize import _normalize_line_gst  # noqa: E402


def _jcr00609_items():
    return [
        {"qty": 10, "rate": 68, "gst_pct": 0, "name": "RUMIPRO"},
        {"qty": 4.5, "rate": 105.73, "gst_pct": 5},
        {"qty": 3, "rate": 73.91, "gst_pct": 5},
        {"qty": 2.5, "rate": 52, "gst_pct": 0},
        {"qty": 2.5, "rate": 188, "gst_pct": 0},
        {"qty": 2, "rate": 58.58, "gst_pct": 5},
        {"qty": 1, "rate": 232.48, "gst_pct": 5},
        {"qty": 1, "rate": 1400, "gst_pct": 0},
        {"qty": 1, "rate": 2828.80, "gst_pct": 0},
        {"qty": 3.5, "rate": 128, "gst_pct": 0},
        {"qty": 2.5, "rate": 1160, "gst_pct": 0},
    ]


def _om_sai_301_items():
    return [
        {"qty": 50, "rate": 5.70, "gst_pct": 5},
        {"qty": 2, "rate": 240, "gst_pct": 5},
        {"qty": 10, "rate": 14.45, "gst_pct": 5},
        {"qty": 9, "rate": 78.98, "gst_pct": 0, "name": "CAREX"},
        {"qty": 5, "rate": 17.50, "gst_pct": 5},
    ]


def _swami_items():
    return [
        {"qty": 5, "rate": 35.68, "gst_pct": 5},
        {"qty": 5, "rate": 15, "gst_pct": 5},
        {"qty": 8, "rate": 32.25, "gst_pct": 18},
        {"qty": 5, "rate": 50, "gst_pct": 0},
        {"qty": 2, "rate": 89.90, "gst_pct": 5},
        {"qty": 6, "rate": 6.25, "gst_pct": 5},
    ]


def _shree_2163_items():
    amounts = [360, 36, 222, 570, 108, 246.60, 198, 86.40, 232.80]
    return [{"qty": 1, "rate": a, "gst_pct": 5} for a in amounts]


CASES = [
    (
        "JCR00609 Jai Ganesh",
        _jcr00609_items(),
        "discount_before_gst",
        297.10,
        {"0": 8856.80, "5": 1047.16},
        25.40,
        9658.00,
    ),
    (
        "Om Sai 301",
        _om_sai_301_items(),
        "discount_before_gst",
        0,
        {"0": 710.82, "5": 997.00},
        24.93,
        1758.00,
    ),
    (
        "Swami Samarth 1028",
        _swami_items(),
        "discount_before_gst",
        19.58,
        {"0": 250.00, "5": 470.70, "18": 258.00},
        34.30,
        1028.00,
    ),
    (
        "Shree Distributor 2163",
        _shree_2163_items(),
        "discount_before_gst",
        0,
        {"5": 2059.80},
        51.50,
        2163.00,
    ),
]


def test_gemini_gst_zero():
    rec = {
        "gst_pct": 0,
        "cgst_pct": 2.5,
        "sgst_pct": 2.5,
        "gst_from_bill": True,
        "hsn_code": "30049099",
    }
    gst = _normalize_line_gst(rec)
    assert gst == 0.0, "0% bill line became {}".format(gst)
    print("  gemini 0% + stray CGST cols -> 0% OK")


def main():
    ok = True
    test_gemini_gst_zero()

    class Inv:
        pass

    for name, items, method, disc, slabs, exp_cgst, exp_net in CASES:
        inv = Inv()
        inv.supplier_name = name
        inv.parser = "test"
        inv.items = []
        inv.invoice_total = exp_net
        inv.total_cgst = exp_cgst
        inv.total_sgst = exp_cgst
        inv.cash_discount = disc
        detected = detect_purchase_gst_calc_method(inv)
        if detected != method:
            print("WARN {} mode {} expected {}".format(name, detected, method))

        calc = PurchaseCalculator(
            items=[dict(i) for i in items],
            overall_discount=disc,
            gst_calc_method=method,
        ).calculate()
        for slab in calc.get("slab_breakdown") or []:
            pct = str(int(slab["gst_pct"])) if slab["gst_pct"] == int(slab["gst_pct"]) else str(slab["gst_pct"])
            exp = slabs.get(pct)
            if exp is not None and abs(slab["gross"] - exp) > 0.05:
                ok = False
                print(
                    "FAIL {} slab {}% gross {:.2f} expected {:.2f}".format(
                        name, pct, slab["gross"], exp,
                    )
                )
        cgst = float(calc.get("cgst") or 0)
        net = float(calc.get("total_amount") or 0)
        if abs(cgst - exp_cgst) > 0.15:
            ok = False
            print("FAIL {} CGST {:.2f} expected {:.2f}".format(name, cgst, exp_cgst))
        elif abs(net - exp_net) > 0.02:
            ok = False
            print("FAIL {} net {:.2f} expected {:.2f}".format(name, net, exp_net))
        else:
            print(
                "OK   {} slabs OK CGST={:.2f} net={:.2f} sub={:.2f}".format(
                    name, cgst, net, calc.get("subtotal"),
                )
            )

    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
