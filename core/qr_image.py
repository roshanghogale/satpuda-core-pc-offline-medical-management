"""One QR encoder for the whole product: a PNG QR as a base64 data URI.

The bill's UPI QR (core/upi_qr.py) and the Mobile Import receive URL both need
the same thing -- a QR that a webview can render with <img src=...> and that a
print template can embed. Classic drew the Mobile Import QR itself, as a Tk
PhotoImage (ui/shared/import_from_mobile.py), which is an object the engine's
HTTP boundary cannot carry, so the Tauri panel had no QR at all.

The body below is the one that has been printing bill QRs; it moved here
unchanged (defaults included) so bill output stays byte-for-byte what it was,
and core.upi_qr keeps exporting the name it always did.

Nothing is imported at module scope on purpose: qrcode/PIL are optional (the
shipped Windows engine bundles both; a mac dev engine may not), and callers
that cannot show a QR must degrade, never crash.
"""
from __future__ import annotations

import base64
from io import BytesIO

# What bills have always used.
BILL_BOX_SIZE = 8
# Classic's Mobile Import window: qrcode.QRCode(version=1, box_size=6, border=2).
SCREEN_BOX_SIZE = 6
DEFAULT_BORDER = 2


def qr_png_data_uri(
    text: str,
    *,
    box_size: int = BILL_BOX_SIZE,
    border: int = DEFAULT_BORDER,
) -> str:
    """Encode `text` as a PNG QR and return it as a data: URI.

    Raises if qrcode/PIL are missing -- the caller decides what to show then.
    """
    import qrcode
    from qrcode.constants import ERROR_CORRECT_M

    qr = qrcode.QRCode(
        version=None,
        error_correction=ERROR_CORRECT_M,
        box_size=box_size,
        border=border,
    )
    qr.add_data(text or "")
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = BytesIO()
    img.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/png;base64,{b64}"
