"""
Tk-free purchase bill parse + enrich helpers for the desktop HTTP API.

Classic UI file picker flows stay in purchase_import_flow.py (Tkinter).
"""

from __future__ import annotations

import os
from typing import Any, Callable, List, Optional

from core.medicine_metadata_resolver import enrich_invoice_item_metadata
from core.medicine_type_detector import enrich_invoice_medicine_types
from core.purchase_image_ocr import is_image_invoice_path
from core.purchase_importer import (
    InvoiceParseError,
    parse_purchase_excel,
    parse_purchase_image,
    parse_purchase_images,
    parse_purchase_pdf,
    sort_import_items_by_bill_order,
)

_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"}


def _image_import_availability_message() -> str:
    from core.gemini_bill_config import bill_photo_import_message

    return bill_photo_import_message()


def _is_image_import(paths) -> bool:
    clean = [p for p in paths if p]
    if not clean:
        return False
    if len(clean) == 1:
        return os.path.splitext(clean[0])[1].lower() in _IMAGE_EXTS
    return all(is_image_invoice_path(p) for p in clean)


def parse_invoice_file(path: str, on_progress=None):
    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf":
        return parse_purchase_pdf(path)
    if ext in (".csv", ".xlsx", ".xls"):
        return parse_purchase_excel(path)
    if ext in _IMAGE_EXTS:
        msg = _image_import_availability_message()
        if msg:
            raise InvoiceParseError(msg)
        return parse_purchase_image(path, on_progress=on_progress)
    raise InvoiceParseError("Unsupported file type: {}".format(ext))


def parse_invoice_paths(paths: List[str], on_progress=None):
    """Parse one file or multiple images (same bill, multiple pages)."""
    from core.bill_page_utils import sort_bill_page_paths

    clean = sort_bill_page_paths([p for p in paths if p and os.path.isfile(p)])
    if not clean:
        raise InvoiceParseError("No file selected.")
    if len(clean) == 1:
        return parse_invoice_file(clean[0], on_progress=on_progress)
    if all(is_image_invoice_path(p) for p in clean):
        msg = _image_import_availability_message()
        if msg:
            raise InvoiceParseError(msg)
        return parse_purchase_images(clean, on_progress=on_progress)
    raise InvoiceParseError(
        "Select either one invoice file, or multiple images of the same bill."
    )


def parse_invoice_worker(path_list: List[str], on_progress=None):
    """Parse bill file(s) only — no SQLite (safe in background thread)."""
    if on_progress:
        on_progress("Reading invoice file…")
    return parse_invoice_paths(path_list, on_progress=on_progress)


def enrich_invoice_for_import(
    invoice,
    purchase_page,
    path_list,
    on_progress: Optional[Callable[[str], None]] = None,
    conn=None,
):
    """Enrich imported rows (types, schedule, content). Uses conn when provided."""
    db = conn if conn is not None else getattr(purchase_page, "conn", None)
    try:
        if on_progress:
            on_progress("Matching medicine types…")
        enrich_invoice_medicine_types(
            invoice,
            conn=db,
            available_types=getattr(purchase_page, "_med_types", None),
            save_learned=False,
        )
        if on_progress:
            on_progress("Looking up medicine details…")
        enrich_invoice_item_metadata(
            invoice,
            conn=db,
            use_gemini=_is_image_import(path_list),
            on_progress=on_progress,
        )
    except Exception as exc:
        if "locked" in str(exc).lower():
            raise InvoiceParseError(
                "Database is busy (sync or another task is running). "
                "Wait a few seconds and try import again."
            ) from exc
        raise
    invoice.items = sort_import_items_by_bill_order(invoice.items)
    return invoice
