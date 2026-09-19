"""Bills for the printed-GST tests: built through _build_bill_context, printed nowhere.

isolated_bill_settings() points bill print settings at a scratch file -- the read path
AND the save path, because load_bill_print_settings saves a one-time migration -- and
stubs the logo and UPI QR lookups. These tests never touch config/bill_print_settings.json
or a store's pictures.
"""
from __future__ import annotations

import os
import tempfile
from contextlib import ExitStack, contextmanager
from unittest import mock

# pharmacy_profile print row: id, name, address, phone, email, gstin, dl, gst_enabled,
# created_at, logo_path, fssai, show_fssai_on_bill
PROFILE = (1, "SATPUDA MEDICAL", "MAIN ROAD", "99", "", "27ABCDE1234F1Z5", "DL-1", 1, None, "", "", 0)


def bill_info(total: float = 250.0, discount: float = 8.0) -> tuple:
    """bill_no, date, customer, phone, address, total, discount, paid, prev due, due,
    credit, cash, online, rounding, doctor, total due, prev credit."""
    return ("SCB1", "2026-09-13", "RAM", "", "", total, discount, total,
            0.0, 0.0, 0.0, total, 0.0, 0.0, "", 0.0, 0.0)


def item(name: str, qty: float, rate: float, amount: float, gst: float) -> tuple:
    """name, hsn, batch, manufacturer, expiry, qty, rate, amount, gst %, mrp, type, unit."""
    return (name, "3004", "B1", "", "2027-05-31", qty, rate, amount, gst, rate, "", "")


# 258.00 of 12% lines less an 8.00 bill discount: the customer pays 250.00.
DISCOUNTED_ITEMS = [
    item("PARACETAMOL 500", 10, 2.5, 25.0, 12.0),
    item("AMOXICILLIN 250", 2, 85.0, 170.0, 12.0),
    item("ORS LEMON", 3, 21.0, 63.0, 12.0),
]


@contextmanager
def isolated_bill_settings():
    import core.bill_config as bill_config

    with tempfile.TemporaryDirectory() as tmp, ExitStack() as stack:
        path = os.path.join(tmp, "bill_print_settings.json")
        stack.enter_context(mock.patch.object(bill_config, "SETTINGS_PATH", path))
        stack.enter_context(mock.patch.object(bill_config, "_bill_settings_path", lambda: path))
        stack.enter_context(mock.patch("core.bill_context._logo_to_base64", return_value=""))
        stack.enter_context(mock.patch("core.upi_qr.apply_upi_qr_to_context", lambda ctx: None))
        yield


def discounted_bill_context(*, gst_enabled: bool = True):
    from core.bill_context import _build_bill_context

    profile = PROFILE[:7] + (1 if gst_enabled else 0,) + PROFILE[8:]
    with isolated_bill_settings():
        return _build_bill_context(profile, bill_info(), DISCOUNTED_ITEMS, "Cash", None)
