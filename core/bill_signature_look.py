"""The signature on a bill should look signed by hand, not printed (owner, 18 Oct 2026).

Every print gets its own small tilt, size and place for the uploaded signature, so two bills
never carry the exact same stamp; the switch in Sales -> Tabs & Tools turns the picture on
or off (the bill setting print_signature_image, also in Settings -> Bill Fields).
"""
from __future__ import annotations

import random
from typing import Any

# How far one print may differ from the next: a pen held a little differently.
MAX_TILT_DEG = 4.0       # left or right
SIZE_RANGE = (0.90, 1.04)
MAX_SHIFT_PCT = 4.0      # of the box, sideways
MAX_LIFT_PCT = 6.0       # of the box, up


def natural_style(rng: random.Random | None = None) -> str:
    """An inline CSS transform for one print of the signature."""
    r = rng or random.SystemRandom()
    tilt = r.uniform(-MAX_TILT_DEG, MAX_TILT_DEG)
    size = r.uniform(*SIZE_RANGE)
    dx = r.uniform(-MAX_SHIFT_PCT, MAX_SHIFT_PCT)
    dy = -r.uniform(0.0, MAX_LIFT_PCT)
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
