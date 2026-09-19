"""Bill photo path helpers — scanning is done by Gemini AI (cloud), not local OCR."""

from __future__ import annotations

import os

_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"}


def is_image_invoice_path(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in _IMAGE_EXTS
