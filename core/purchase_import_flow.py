"""
Direct purchase bill import: file picker → parse → populate Purchase page.
Skips the preview dialog; user verifies and saves on the Purchase screen.
"""

import os
import tkinter as tk
from tkinter import filedialog, messagebox

from core.medicine_type_detector import enrich_invoice_medicine_types
from core.purchase_importer import (
    IMPORT_PLACEHOLDER_BATCH,
    IMPORT_PLACEHOLDER_EXPIRY,
    InvoiceParseError,
    apply_import_placeholders_to_items,
    import_into_purchase_page,
    parse_purchase_excel,
    parse_purchase_pdf,
    write_import_log,
)

_INVOICE_FILETYPES = [
    ("Invoice files", "*.pdf *.csv *.xlsx *.xls"),
    ("PDF files", "*.pdf"),
    ("CSV files", "*.csv"),
    ("Excel files", "*.xlsx *.xls"),
    ("All files", "*.*"),
]


def _parse_invoice_file(path):
    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf":
        return parse_purchase_pdf(path)
    if ext in (".csv", ".xlsx", ".xls"):
        return parse_purchase_excel(path)
    raise InvoiceParseError("Unsupported file type: {}".format(ext))


def _placeholder_note(items) -> str:
    need_batch = [i for i in items if i.batch == IMPORT_PLACEHOLDER_BATCH]
    need_exp = [i for i in items if i.expiry == IMPORT_PLACEHOLDER_EXPIRY]
    if not need_batch and not need_exp:
        return ""
    parts = []
    if need_batch:
        parts.append("{} without batch on bill".format(len(need_batch)))
    if need_exp:
        parts.append("{} without expiry on bill".format(len(need_exp)))
    return (
        "\n\nMarked as WITHOUT BATCH / WITHOUT EXP — update those fields before saving."
        " (" + ", ".join(parts) + ")"
    )


def import_purchase_bill_direct(parent, purchase_page):
    """
    Open the file manager, parse the invoice, and load rows onto PurchasePage.
    Returns True when import succeeded.
    """
    if purchase_page is None:
        messagebox.showerror(
            "Import Purchase Bill",
            "Purchase page is not available. Open Purchase once, then try again.",
            parent=parent,
        )
        return False

    path = filedialog.askopenfilename(
        title="Select purchase invoice",
        filetypes=_INVOICE_FILETYPES,
        parent=parent,
    )
    if not path:
        return False

    root = parent.winfo_toplevel()
    try:
        root.config(cursor="watch")
        root.update_idletasks()
        invoice = _parse_invoice_file(path)
        enrich_invoice_medicine_types(
            invoice,
            conn=getattr(purchase_page, "conn", None),
            available_types=getattr(purchase_page, "_med_types", None),
        )
    except Exception as exc:
        messagebox.showerror("Import Error", str(exc), parent=parent)
        return False
    finally:
        try:
            root.config(cursor="")
        except Exception:
            pass

    items = list(invoice.items)
    if not items:
        messagebox.showwarning(
            "Empty Invoice",
            "No medicine rows were found in this file.",
            parent=parent,
        )
        return False

    apply_import_placeholders_to_items(items)

    invalid = [item for item in items if not item.is_valid]
    if invalid and not any(item.is_valid for item in items):
        first = invalid[0]
        messagebox.showerror(
            "Invalid Invoice",
            "No rows could be imported.\n\nRow {}: {}".format(
                first.source_row or "?",
                "; ".join(first.issues),
            ),
            parent=parent,
        )
        return False

    if invalid:
        first = invalid[0]
        if not messagebox.askyesno(
            "Some Rows Skipped",
            "{} row(s) have errors and will be skipped.\nExample — Row {}: {}\n\n"
            "Import the other {} row(s)?".format(
                len(invalid),
                first.source_row or "?",
                "; ".join(first.issues),
                len(items) - len(invalid),
            ),
            parent=parent,
        ):
            return False
        items = [item for item in items if item.is_valid]

    invoice.items = items

    replace_existing = True
    if getattr(purchase_page, "purchase_items", None):
        answer = messagebox.askyesnocancel(
            "Existing Purchase Items",
            "This purchase page already has items.\n\n"
            "Yes: replace them with imported rows.\n"
            "No: append imported rows.\n"
            "Cancel: abort import.",
            parent=parent,
        )
        if answer is None:
            return False
        replace_existing = bool(answer)

    try:
        result = import_into_purchase_page(
            purchase_page,
            invoice,
            list(invoice.items),
            replace_existing=replace_existing,
        )
    except Exception as exc:
        messagebox.showerror("Import Error", str(exc), parent=parent)
        return False

    write_import_log(invoice, "imported", "Loaded onto purchase page from file")
    note = _placeholder_note(invoice.items)
    disc_note = ""
    inv_disc = float(getattr(invoice, "product_discount", 0) or 0) + float(
        getattr(invoice, "cash_discount", 0) or 0
    )
    if inv_disc > 0:
        disc_note = "\nBill discount (overall): ₹{:.2f} — shown in Overall Disc ₹.".format(
            inv_disc,
        )
    messagebox.showinfo(
        "Import Complete",
        "{} item(s) loaded on the Purchase page.\n"
        "Line discounts are shown in the Disc column (₹ or % from the bill).{}"
        "{}\nReview batches, rates and expiry, then save the purchase.".format(
            result.get("items_imported", len(invoice.items)),
            disc_note,
            note,
        ),
        parent=parent,
    )
    try:
        purchase_page.medicine_name.focus_set()
    except Exception:
        pass
    return True
