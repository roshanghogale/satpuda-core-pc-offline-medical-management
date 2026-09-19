"""UPI pay QR code for bill printing."""
from __future__ import annotations

import re
import time
from urllib.parse import quote

# The encoder moved to core/qr_image.py so Mobile Import can reuse it without
# dragging the bill's UPI settings along. Re-exported here: this is still the
# name bill printing (and anything patching it) imports.
from core.qr_image import qr_png_data_uri
from core.upi_prefs import (
    AMOUNT_TOTAL,
    AMOUNT_TOTAL_PLUS_PREV_DUE,
    load_upi_id,
    load_upi_qr_amount_mode,
    load_upi_qr_enabled,
)


def normalize_vpa(vpa: str) -> str:
    """Normalize UPI ID for QR (no spaces, lowercase)."""
    return (vpa or "").strip().lower().replace(" ", "")


def sanitize_payee_name(name: str) -> str:
    """Keep payee name QR-safe (no &, =, ? or other URL delimiters)."""
    raw = (name or "Merchant").strip()
    cleaned = re.sub(r"[&=?#%]+", " ", raw)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return (cleaned[:50] or "Merchant")


def sanitize_tr(ref: str) -> str:
    """
    Transaction reference for dynamic merchant QR (mandatory for GPay/NPCI).

    NPCI: alphanumeric; avoid < > = : & '
    """
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "", str(ref or "").strip())
    if cleaned:
        return cleaned[:50]
    return f"BILL{int(time.time())}"


def _q(value: str) -> str:
    """URL-encode a UPI query parameter value."""
    return quote((value or "").strip(), safe="")


def upi_pay_url(
    vpa: str,
    payee_name: str,
    amount: float,
    *,
    bill_no: str = "",
) -> str:
    """
    Build NPCI dynamic merchant UPI deep link for QR encoding.

    pa must stay literal (shop@oksbi). pn/tr/tn are URL-encoded.
    tr is required for dynamic merchant QRs (Google Pay / PhonePe).
    """
    pa = normalize_vpa(vpa)
    if not pa or "@" not in pa:
        raise ValueError("invalid vpa")

    pn = sanitize_payee_name(payee_name)
    tr = sanitize_tr(bill_no)
    amt = max(0.0, round(float(amount or 0), 2))

    parts = [
        f"pa={pa}",
        f"pn={_q(pn)}",
        f"tr={_q(tr)}",
        f"am={amt:.2f}",
        "cu=INR",
    ]
    if bill_no:
        tn = sanitize_payee_name(f"Bill {bill_no}")[:50]
        if tn:
            parts.append(f"tn={_q(tn)}")
    return "upi://pay?" + "&".join(parts)


def _grand_total(ctx) -> float:
    try:
        return round(max(0.0, float(getattr(ctx, "grand_total", 0) or 0)), 2)
    except (TypeError, ValueError):
        return 0.0


def qr_amount_for_context(ctx) -> float:
    """Amount to encode in UPI QR — bill total and/or outstanding balance per settings."""
    from core.bill_render_utils import total_due_display

    mode = load_upi_qr_amount_mode()
    if mode == AMOUNT_TOTAL_PLUS_PREV_DUE:
        amt = total_due_display(ctx)
    else:
        amt = _grand_total(ctx)
    if amt <= 0:
        amt = _grand_total(ctx)
    return round(max(0.0, amt), 2)


def apply_upi_qr_to_context(ctx) -> None:
    """Attach UPI QR image to BillContext when enabled and configured."""
    ctx.show_upi_qr = False
    ctx.upi_qr_src = ""
    ctx.upi_qr_amount = 0.0

    if not load_upi_qr_enabled():
        return

    vpa = load_upi_id()
    if not vpa:
        return

    amt = qr_amount_for_context(ctx)
    if amt <= 0:
        return

    bill_no = getattr(ctx, "bill_no", "") or ""
    if not bill_no:
        bill_no = f"TXN{int(time.time())}"

    try:
        url = upi_pay_url(
            vpa,
            getattr(ctx, "store_name", ""),
            amt,
            bill_no=bill_no,
        )
        ctx.upi_qr_src = qr_png_data_uri(url)
    except Exception:
        return

    ctx.upi_qr_amount = amt
    ctx.show_upi_qr = True
