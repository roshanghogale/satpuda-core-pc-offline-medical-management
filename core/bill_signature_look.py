"""The signature on a bill should look signed by hand, not printed (owner, 18 Oct 2026).

Every print gets its own small tilt, size and place for the uploaded signature, so two bills
never carry the exact same stamp; the switch in Sales -> Tabs & Tools turns the picture on
or off (the bill setting print_signature_image, also in Settings -> Bill Fields).
"""
from __future__ import annotations

import random
from typing import Any

# How far one print may differ from the next: a pen held differently each time (owner:
# more than a hint -- up and down too, and a size difference that shows).
MAX_TILT_DEG = 7.0       # left or right
MIN_TILT_DEG = 1.5       # most prints lean visibly one way or the other
SIZE_RANGE = (0.80, 1.12)
MAX_SHIFT_PCT = 8.0      # of the picture, sideways
UP_DOWN_PCT = (-14.0, 8.0)  # of the picture: up (negative) or down


def natural_style(rng: random.Random | None = None) -> str:
    """An inline CSS transform for one print of the signature."""
    r = rng or random.SystemRandom()
    # One print in five sits nearly straight; the rest lean left or right by a visible amount.
    if r.random() < 0.2:
        tilt = r.uniform(-MIN_TILT_DEG, MIN_TILT_DEG)
    else:
        tilt = r.choice((-1, 1)) * r.uniform(MIN_TILT_DEG, MAX_TILT_DEG)
    size = r.uniform(*SIZE_RANGE)
    dx = r.uniform(-MAX_SHIFT_PCT, MAX_SHIFT_PCT)
    dy = r.uniform(*UP_DOWN_PCT)
    return (
        f"transform: translate({dx:.1f}%, {dy:.1f}%) rotate({tilt:.2f}deg) scale({size:.3f});"
        "transform-origin: 50% 85%;"
    )


def signature_switch() -> dict[str, Any]:
    """Is the uploaded signature printed on bills, and is there one?"""
    from core.bill_config import load_bill_print_settings

    s = load_bill_print_settings()
    try:
        from core.bill_context import _signature_to_base64

        uploaded = bool(_signature_to_base64())
    except Exception:
        uploaded = False
    return {"ok": True, "on": bool(s.get("print_signature_image", True)), "uploaded": uploaded}


def set_signature_switch(on: Any) -> dict[str, Any]:
    from core.bill_config import load_bill_print_settings, save_bill_print_settings

    s = dict(load_bill_print_settings())
    s["print_signature_image"] = bool(on)
    save_bill_print_settings(s)
    return signature_switch()
