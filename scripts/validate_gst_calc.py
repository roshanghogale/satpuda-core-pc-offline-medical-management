"""
Verify Indian pharmacy GST / gross calculations against known bill totals.
Run: python scripts/validate_gst_calc.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core.pharmacy_purchase_calc import calc_pharmacy_purchase_bill  # noqa: E402
from core.purchase_invoice_engine import normalize_total_gst_pct  # noqa: E402


def _items_vinar_vm00060():
    rows = [
        (60.0, 5.0), (128.0, 18.0), (128.0, 18.0), (165.0, 5.0),
        (155.0, 18.0), (145.0, 5.0), (290.0, 18.0),
    ]
    return [{"qty": 1, "rate": amt, "amount": amt, "gst_pct": gst} for amt, gst in rows]


def _items_shree_1846():
    amounts = [118.80, 141.00, 136.80, 264.00, 144.00, 585.00, 150.00, 33.60, 185.00]
    return [{"qty": 1, "rate": a, "amount": a, "gst_pct": 5.0} for a in amounts]


def _items_om_sai_1758():
    return [
        {"qty": 1, "rate": 285.0, "amount": 285.0, "gst_pct": 5.0},
        {"qty": 1, "rate": 480.0, "amount": 480.0, "gst_pct": 5.0},
        {"qty": 1, "rate": 144.50, "amount": 144.50, "gst_pct": 5.0},
        {"qty": 1, "rate": 710.82, "amount": 710.82, "gst_pct": 0.0},
        {"qty": 1, "rate": 87.50, "amount": 87.50, "gst_pct": 5.0},
    ]


CASES = [
    ("VINAR VM00060", _items_vinar_vm00060(), 1071.0, 1216.0, 144.70),
    ("Shree Distributor 1846", _items_shree_1846(), 1758.20, 1846.0, 87.94),
    ("Om Sai 1758", _items_om_sai_1758(), 1707.82, 1758.0, 49.86),
]


def main():
    ok = True
    print("GST normalization:")
    assert normalize_total_gst_pct(0, 2.5, 2.5) == 5.0
    assert normalize_total_gst_pct(5.0) == 5.0
    assert normalize_total_gst_pct(2.5) == 5.0
    assert normalize_total_gst_pct(9.0) == 18.0
    print("  normalize_total_gst_pct OK")

    for name, items, gross, net, gst_total in CASES:
        calc = calc_pharmacy_purchase_bill(
            items,
            net_payable=net,
            gst_calc_method="discount_before_gst",
        )
        g = calc["gross_total"]
        t = calc["total_gst"]
        n = calc["total_amount"]
        if abs(g - gross) > 0.05:
            ok = False
            print("FAIL {} gross {:.2f} expected {:.2f}".format(name, g, gross))
        elif abs(t - gst_total) > 0.15:
            ok = False
            print("FAIL {} gst {:.2f} expected {:.2f}".format(name, t, gst_total))
        elif abs(n - net) > 0.01:
            ok = False
            print("FAIL {} net {:.2f} expected {:.2f}".format(name, n, net))
        else:
            print("OK   {} gross={:.2f} gst={:.2f} net={:.2f}".format(name, g, t, n))

    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
