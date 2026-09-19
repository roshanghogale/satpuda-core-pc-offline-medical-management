#!/usr/bin/env python3
"""Regenerate the wizard header logo BMPs from satpuda.ico.

Inno Setup's WizardSmallImageFile takes BMPs, not PNG or ICO, and it picks the
one whose size best matches the screen's DPI -- so three are shipped: 100%, 150%
and 200%. The modern wizard's page header is white, so the icon (which is a
solid black tile) is given rounded corners and composited onto white, which is
what makes it read as an app tile rather than a black box.

    python3 make_wizard_logo.py

Needs Pillow. The three .bmp files it writes sit next to SatpudaCore.iss and are
referenced from [Setup]; the reference is guarded with ISPP's FileExists, so a
missing .bmp costs the header logo and does NOT fail the build.
"""
from __future__ import annotations

import os

from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
SOURCE = os.path.join(HERE, "satpuda.ico")

# 55px is Inno's own small-image width at 100%; the other two are the 150% and
# 200% steps. Windows' scale steps in between land on the nearest of these.
SIZES = {
    "wizard_logo.bmp": 55,
    "wizard_logo_150.bmp": 83,
    "wizard_logo_200.bmp": 110,
}

BACKGROUND = (255, 255, 255)
# Corner radius as a fraction of the tile, roughly Windows 11's app-icon radius.
RADIUS_RATIO = 0.18
# Supersampling factor, so the rounded corners are not a staircase at 55px.
SS = 8


def build(size: int) -> Image.Image:
    src = Image.open(SOURCE).convert("RGBA")

    big = size * SS
    tile = src.resize((big, big), Image.LANCZOS)

    mask = Image.new("L", (big, big), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, big - 1, big - 1), radius=int(big * RADIUS_RATIO), fill=255
    )
    tile.putalpha(mask)

    out = Image.new("RGBA", (big, big), BACKGROUND + (255,))
    out.alpha_composite(tile)
    # 24-bit BMP: Inno reads the alpha channel only when WizardImageAlphaFormat
    # is set, and the header is white anyway, so the alpha is baked in here.
    return out.convert("RGB").resize((size, size), Image.LANCZOS)


def main() -> None:
    for name, size in SIZES.items():
        path = os.path.join(HERE, name)
        build(size).save(path, format="BMP")
        print(f"{name}  {size}x{size}  {os.path.getsize(path)} bytes")


if __name__ == "__main__":
    main()
