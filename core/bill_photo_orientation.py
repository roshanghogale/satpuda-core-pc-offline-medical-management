"""
Turn a sideways bill photo upright before it is read.

A shop photographs a long bill in portrait, so the bill lies on its side in the
picture. The AI reader then reads the item table across instead of down: on one
real bill it put the second row's MRP on the first row (and the first row's on
the second) on every try, while the same photo turned upright came back with all
65 values right. Upside down reads nearly as well as upright; sideways is what
breaks it.

So the photo is turned before it is sent: by the orientation the camera saved
(EXIF), and a quarter turn when the printed lines run up and down the picture.
(Asking the reader which way the photo lies did not work: it answered 0 for
a bill on its side.)
"""
from __future__ import annotations

import os
import tempfile
from typing import List, Sequence, Tuple


def _profile(img, rows: bool) -> List[float]:
    from PIL import Image

    w, h = img.size
    small = img.resize((1, h), Image.BOX) if rows else img.resize((w, 1), Image.BOX)
    data = getattr(small, "get_flattened_data", None) or small.getdata
    return [float(v) for v in data()]


def _smooth(values: List[float], width: int = 5) -> List[float]:
    half = width // 2
    out: List[float] = []
    for i in range(len(values)):
        lo, hi = max(0, i - half), min(len(values), i + half + 1)
        out.append(sum(values[lo:hi]) / (hi - lo))
    return out


def _spread(values: List[float]) -> float:
    """Coefficient of variation of a profile (0 when flat)."""
    if not values:
        return 0.0
    mean = sum(values) / len(values)
    if mean <= 0:
        return 0.0
    var = sum((v - mean) ** 2 for v in values) / len(values)
    return var ** 0.5 / mean


def looks_sideways(image) -> bool:
    """True when the printed lines run up and down the photo (the bill lies on its side).

    No OCR: across printed lines the amount of ink swings between each text line
    and the white gap under it; along a line it stays much more even. So the
    ink profile taken ACROSS the lines spreads far more than the one taken along
    them. The middle of the photo is used, away from the table top and the edges.
    """
    from PIL import ImageOps

    gray = ImageOps.exif_transpose(image).convert("L")
    gray.thumbnail((1200, 1200))
    w, h = gray.size
    if w < 100 or h < 100:
        return False
    gray = gray.crop((int(w * 0.2), int(h * 0.2), int(w * 0.8), int(h * 0.8)))
    gray = ImageOps.autocontrast(gray, cutoff=2)
    ink = gray.point(lambda p: 255 if p < 100 else 0)
    across_rows = _spread(_smooth(_profile(ink, rows=True)))
    across_cols = _spread(_smooth(_profile(ink, rows=False)))
    return across_cols > across_rows * 1.3


def upright_copies(paths: Sequence[str]) -> Tuple[List[str], List[str]]:
    """Copies of the photos turned upright: by the camera's EXIF orientation, and
    a quarter turn for a bill lying on its side. Returns (paths to read,
    temporary files to delete). A photo that needs no turning, or cannot be
    opened, is read as it is."""
    out: List[str] = []
    temps: List[str] = []
    for path in paths:
        try:
            from PIL import Image, ImageOps

            with Image.open(path) as img:
                img.load()
                orientation = int(img.getexif().get(0x0112, 1) or 1)
                fixed = ImageOps.exif_transpose(img) if orientation not in (0, 1) else img
                sideways = looks_sideways(fixed)
                if orientation in (0, 1) and not sideways:
                    out.append(path)
                    continue
                if sideways:
                    # Either quarter turn makes the lines run across; upside down
                    # reads nearly as well as upright, sideways does not.
                    fixed = fixed.rotate(90, expand=True)
                fd, tmp = tempfile.mkstemp(prefix="bill_upright_", suffix=".jpg")
                os.close(fd)
                fixed.convert("RGB").save(tmp, "JPEG", quality=92)
                out.append(tmp)
                temps.append(tmp)
        except Exception:
            out.append(path)
    return out, temps


def remove_quietly(paths: Sequence[str]) -> None:
    for p in paths:
        try:
            os.remove(p)
        except OSError:
            pass
