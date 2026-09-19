"""
Direct purchase bill import: file picker → parse → populate Purchase page.
Skips the preview dialog; user verifies and saves on the Purchase screen.
"""

import os
import queue
import threading
import tkinter as tk
from contextlib import contextmanager
from tkinter import filedialog, messagebox
from typing import List, Optional

try:
    import ttkbootstrap as ttk
except ImportError:
    from tkinter import ttk

from core.medicine_metadata_resolver import enrich_invoice_item_metadata
from core.medicine_type_detector import enrich_invoice_medicine_types
from core.purchase_image_ocr import is_image_invoice_path
from core.purchase_importer import (
    IMPORT_PLACEHOLDER_BATCH,
    IMPORT_PLACEHOLDER_EXPIRY,
    InvoiceParseError,
    apply_import_placeholders_to_items,
    import_into_purchase_page,
    parse_purchase_excel,
    parse_purchase_image,
    parse_purchase_images,
    parse_purchase_pdf,
    sort_import_items_by_bill_order,
    write_import_log,
)

_INVOICE_FILETYPES_FULL = [
    (
        "Invoice files",
        "*.pdf *.csv *.xlsx *.xls *.jpg *.jpeg *.png *.bmp *.tif *.tiff *.webp",
    ),
    ("PDF files", "*.pdf"),
    ("Image bills (scan)", "*.jpg *.jpeg *.png *.bmp *.tif *.tiff *.webp"),
    ("CSV files", "*.csv"),
    ("Excel files", "*.xlsx *.xls"),
    ("All files", "*.*"),
]

_INVOICE_FILETYPES_NO_IMAGES = [
    ("Invoice files", "*.pdf *.csv *.xlsx *.xls"),
    ("PDF files", "*.pdf"),
    ("CSV files", "*.csv"),
    ("Excel files", "*.xlsx *.xls"),
    ("All files", "*.*"),
]


def _invoice_filetypes():
    from core.build_features import is_gemini_supported

    if is_gemini_supported():
        return _INVOICE_FILETYPES_FULL
    return _INVOICE_FILETYPES_NO_IMAGES


def _image_import_supported() -> bool:
    from core.build_features import is_gemini_supported

    return is_gemini_supported()

_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"}

_IMAGE_FILETYPES = [
    ("Bill photos (multi-page OK)", "*.jpg *.jpeg *.png *.bmp *.tif *.tiff *.webp"),
    ("All files", "*.*"),
]


def _image_import_availability_message() -> str:
    """Bill photos require Gemini AI and an internet connection."""
    from core.gemini_bill_config import bill_photo_import_message

    return bill_photo_import_message()


def _is_image_import(paths) -> bool:
    clean = [p for p in paths if p]
    if not clean:
        return False
    if len(clean) == 1:
        return os.path.splitext(clean[0])[1].lower() in _IMAGE_EXTS
    return all(is_image_invoice_path(p) for p in clean)


def _parse_invoice_file(path, on_progress=None):
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


def _parse_invoice_paths(paths, on_progress=None):
    """Parse one file or multiple images (same bill, multiple pages)."""
    from core.bill_page_utils import sort_bill_page_paths

    clean = sort_bill_page_paths([p for p in paths if p and os.path.isfile(p)])
    if not clean:
        raise InvoiceParseError("No file selected.")
    if len(clean) == 1:
        return _parse_invoice_file(clean[0], on_progress=on_progress)
    if all(is_image_invoice_path(p) for p in clean):
        msg = _image_import_availability_message()
        if msg:
            raise InvoiceParseError(msg)
        return parse_purchase_images(clean, on_progress=on_progress)
    raise InvoiceParseError(
        "Select either one invoice file, or multiple images of the same bill."
    )


class _MultiPageBillPickerDialog:
    """
    Collect every page of one bill before OCR/Gemini scan.
    Makes carry-forward invoices (Continued… page 2) easy to import.
    """

    def __init__(self, parent: tk.Misc, initial_paths: Optional[List[str]] = None):
        self._parent = parent
        self._pages: List[str] = []
        self._result: Optional[List[str]] = None
        self._top: Optional[tk.Toplevel] = None
        self._listbox = None
        self._hint_var = tk.StringVar(
            master=parent,
            value="Add each photo of the same invoice in order (page 1, then 2, …).",
        )
        for path in initial_paths or []:
            if path and os.path.isfile(path) and path not in self._pages:
                self._pages.append(path)

    def _basename(self, path: str) -> str:
        return path.rsplit("\\", 1)[-1].rsplit("/", 1)[-1]

    def _refresh_list(self):
        if self._listbox is None:
            return
        self._listbox.delete(0, tk.END)
        for idx, path in enumerate(self._pages, 1):
            self._listbox.insert(tk.END, "Page {} — {}".format(idx, self._basename(path)))
        n = len(self._pages)
        if n == 0:
            self._hint_var.set(
                "Tip: If the bill says “Continued…”, add page 2 before scanning."
            )
        elif n == 1:
            self._hint_var.set(
                "One page added. If the bottom says “Continued…”, tap Add page."
            )
        else:
            self._hint_var.set(
                "{} pages ready — page 1 first, last page usually has Grand Total.".format(n)
            )

    def _selected_index(self) -> Optional[int]:
        if self._listbox is None:
            return None
        sel = self._listbox.curselection()
        if not sel:
            return None
        return int(sel[0])

    def _add_pages(self):
        paths = filedialog.askopenfilenames(
            title="Add bill page photo(s)",
            filetypes=_IMAGE_FILETYPES,
            parent=self._top,
        )
        if not paths:
            return
        for path in paths:
            if path and os.path.isfile(path) and path not in self._pages:
                self._pages.append(path)
        self._refresh_list()

    def _remove_selected(self):
        idx = self._selected_index()
        if idx is None:
            messagebox.showinfo(
                "Remove page",
                "Select a page in the list first.",
                parent=self._top,
            )
            return
        del self._pages[idx]
        self._refresh_list()

    def _move(self, delta: int):
        idx = self._selected_index()
        if idx is None:
            return
        new_idx = idx + delta
        if new_idx < 0 or new_idx >= len(self._pages):
            return
        self._pages[idx], self._pages[new_idx] = self._pages[new_idx], self._pages[idx]
        self._refresh_list()
        if self._listbox is not None:
            self._listbox.selection_clear(0, tk.END)
            self._listbox.selection_set(new_idx)
            self._listbox.see(new_idx)

    def _on_scan(self):
        if not self._pages:
            messagebox.showwarning(
                "No pages",
                "Add at least one bill page photo.",
                parent=self._top,
            )
            return
        self._result = list(self._pages)
        if self._top is not None:
            self._top.destroy()

    def _on_cancel(self):
        self._result = None
        if self._top is not None:
            self._top.destroy()

    def show(self) -> Optional[List[str]]:
        top = tk.Toplevel(self._parent)
        self._top = top
        top.title("Multi-page bill — add all pages")
        top.transient(self._parent)
        top.resizable(True, True)
        top.minsize(480, 360)
        try:
            top.grab_set()
        except Exception:
            pass

        frame = ttk.Frame(top, padding=16)
        frame.pack(fill=tk.BOTH, expand=True)

        ttk.Label(
            frame,
            text="One bill, multiple photos",
            font=("", 11, "bold"),
        ).pack(anchor=tk.W)
        ttk.Label(
            frame,
            text=(
                "Many supplier bills span 2–3 pages. The first page often ends with "
                "“Continued…2”; the last page has Sub Total and Grand Total. "
                "Add every page of the same invoice number, then scan."
            ),
            wraplength=440,
            justify=tk.LEFT,
        ).pack(anchor=tk.W, pady=(4, 10))

        list_frame = ttk.Frame(frame)
        list_frame.pack(fill=tk.BOTH, expand=True)
        scroll = ttk.Scrollbar(list_frame, orient=tk.VERTICAL)
        self._listbox = tk.Listbox(
            list_frame,
            height=8,
            yscrollcommand=scroll.set,
            selectmode=tk.SINGLE,
        )
        scroll.config(command=self._listbox.yview)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self._listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        btn_row = ttk.Frame(frame)
        btn_row.pack(fill=tk.X, pady=(8, 4))
        ttk.Button(btn_row, text="+ Add page", command=self._add_pages).pack(
            side=tk.LEFT, padx=(0, 4),
        )
        ttk.Button(btn_row, text="Remove", command=self._remove_selected).pack(
            side=tk.LEFT, padx=4,
        )
        ttk.Button(btn_row, text="↑ Up", command=lambda: self._move(-1)).pack(
            side=tk.LEFT, padx=4,
        )
        ttk.Button(btn_row, text="↓ Down", command=lambda: self._move(1)).pack(
            side=tk.LEFT, padx=4,
        )

        ttk.Label(
            frame,
            textvariable=self._hint_var,
            foreground="gray",
            wraplength=440,
            justify=tk.LEFT,
        ).pack(anchor=tk.W, pady=(4, 12))

        action_row = ttk.Frame(frame)
        action_row.pack(fill=tk.X)
        ttk.Button(action_row, text="Cancel", command=self._on_cancel).pack(
            side=tk.RIGHT, padx=(4, 0),
        )
        try:
            scan_btn = ttk.Button(
                action_row,
                text="Scan all pages",
                command=self._on_scan,
                bootstyle="primary",
            )
        except Exception:
            scan_btn = ttk.Button(
                action_row,
                text="Scan all pages",
                command=self._on_scan,
            )
        scan_btn.pack(side=tk.RIGHT)

        self._refresh_list()
        top.update_idletasks()
        x = self._parent.winfo_rootx() + max(0, (self._parent.winfo_width() - top.winfo_width()) // 2)
        y = self._parent.winfo_rooty() + max(0, (self._parent.winfo_height() - top.winfo_height()) // 2)
        top.geometry("+{}+{}".format(x, y))
        top.protocol("WM_DELETE_WINDOW", self._on_cancel)
        self._parent.wait_window(top)
        return self._result


def _pick_bill_import_paths(parent) -> Optional[List[str]]:
    """
    Open file picker for all supported invoice types (multi-select enabled).

    - One file (PDF, CSV, Excel, or one image): single bill
    - Multiple images only: same bill with carry-forward pages (page 1, 2, …)
    """
    paths = filedialog.askopenfilenames(
        title=(
            "Select purchase invoice — PDF, Excel, or CSV"
            + (" or bill photo (Ctrl+click multiple pages)" if _image_import_supported() else "")
        ),
        filetypes=_invoice_filetypes(),
        parent=parent,
    )
    if not paths:
        return None

    clean = [p for p in paths if p and os.path.isfile(p)]
    if not clean:
        return None

    if len(clean) == 1:
        return clean

    if all(is_image_invoice_path(p) for p in clean):
        from core.bill_page_utils import sort_bill_page_paths

        return sort_bill_page_paths(clean)

    messagebox.showerror(
        "Import Purchase Bill",
        "For PDF, Excel, or CSV — select only ONE file.\n\n"
        "Select MULTIPLE files only when they are photos of the same bill "
        "(page 1, page 2, … with carry-forward / “Continued…”).",
        parent=parent,
    )
    return None


class _BillScanProgressDialog:
    """Progress UI while import/OCR runs in a background thread."""

    def __init__(
        self,
        parent: tk.Misc,
        *,
        title: str = "Scanning purchase bill",
        heading: str = "Reading your bill — please wait",
        hint: str = "Large files may take a minute while models load.",
    ):
        self._parent = parent
        self._title = title
        self._heading = heading
        self._hint = hint
        self._queue: queue.Queue = queue.Queue()
        self._finished = tk.BooleanVar(master=parent, value=False)
        self._result: dict = {}
        self._top: Optional[tk.Toplevel] = None
        self._status = None

    def _build(self):
        top = tk.Toplevel(self._parent)
        self._top = top
        top.title(self._title)
        top.transient(self._parent)
        top.resizable(False, False)
        top.protocol("WM_DELETE_WINDOW", self._ignore_close)
        try:
            top.grab_set()
        except Exception:
            pass
        frame = ttk.Frame(top, padding=20)
        frame.pack(fill=tk.BOTH, expand=True)
        ttk.Label(
            frame,
            text=self._heading,
            font=("", 11, "bold"),
        ).pack(anchor=tk.W, pady=(0, 8))
        self._status = ttk.Label(
            frame,
            text="Starting…",
            wraplength=420,
            justify=tk.LEFT,
        )
        self._status.pack(anchor=tk.W, pady=(0, 12))
        bar = ttk.Progressbar(frame, mode="indeterminate", length=380)
        bar.pack(fill=tk.X)
        bar.start(12)
        ttk.Label(
            frame,
            text=self._hint,
            foreground="gray",
            wraplength=420,
        ).pack(anchor=tk.W, pady=(10, 0))
        top.update_idletasks()
        x = self._parent.winfo_rootx() + (self._parent.winfo_width() // 2) - (top.winfo_width() // 2)
        y = self._parent.winfo_rooty() + (self._parent.winfo_height() // 2) - (top.winfo_height() // 2)
        top.geometry(f"+{max(0, x)}+{max(0, y)}")

    def _ignore_close(self):
        pass

    def _poll(self):
        try:
            while True:
                msg = self._queue.get_nowait()
                if self._status is not None:
                    self._status.configure(text=msg)
        except queue.Empty:
            pass
        if self._finished.get():
            if self._top is not None:
                try:
                    self._top.grab_release()
                    self._top.destroy()
                except Exception:
                    pass
            return
        if self._top is not None:
            try:
                self._top.update_idletasks()
                self._parent.update_idletasks()
            except Exception:
                pass
            self._top.after(120, self._poll)

    def run(self, worker):
        self._build()
        self._poll()

        def _thread():
            try:
                def progress(msg):
                    self._queue.put(msg)

                self._result["invoice"] = worker(progress)
            except Exception as exc:
                self._result["error"] = exc
            finally:
                self._parent.after(0, lambda: self._finished.set(True))

        threading.Thread(target=_thread, daemon=True, name="BillOCR").start()
        self._parent.wait_variable(self._finished)

        if "error" in self._result:
            raise self._result["error"]
        return self._result.get("invoice")

    def run_sync(self, worker):
        """Deprecated — blocks UI. Use run() with a background DB connection instead."""
        return self.run(worker)


@contextmanager
def _background_db_conn(purchase_page):
    """Read-only SQLite handle for import matching (main conn stays on UI thread).

    Import prep must not INSERT/UPDATE the store DB — that races the UI and
    shows "database is locked". Writes happen later on the UI connection.
    """
    from core.background_workers import db_path_from_conn
    from core.db_utils import open_store_db

    main_conn = getattr(purchase_page, "conn", None)
    path = db_path_from_conn(main_conn) if main_conn is not None else ""
    if not path:
        yield main_conn
        return
    bg = open_store_db(path, readonly=True, timeout=60.0)
    try:
        yield bg
    finally:
        try:
            bg.close()
        except Exception:
            pass


def _enrich_invoice_worker(purchase_page, invoice, path_list, on_progress=None):
    """Run type/metadata enrichment off the UI thread."""
    with _background_db_conn(purchase_page) as conn:
        return _enrich_invoice_for_import(
            invoice,
            purchase_page,
            path_list,
            on_progress=on_progress,
            conn=conn,
        )


def _parse_invoice_worker(path_list, on_progress=None):
    """Parse bill file(s) only — no SQLite (safe in background thread)."""
    if on_progress:
        on_progress("Reading invoice file…")
    return _parse_invoice_paths(path_list, on_progress=on_progress)


def _enrich_invoice_for_import(invoice, purchase_page, path_list, on_progress=None, conn=None):
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


def _parse_and_enrich_invoice(path_list, purchase_page, on_progress=None):
    """Legacy wrapper — parse then enrich (prefer split calls from UI thread)."""
    invoice = _parse_invoice_worker(path_list, on_progress=on_progress)
    return _enrich_invoice_for_import(invoice, purchase_page, path_list, on_progress=on_progress)


def _import_progress_dialog(root, path_list) -> _BillScanProgressDialog:
    if _is_image_import(path_list):
        return _BillScanProgressDialog(
            root,
            title="Scanning purchase bill",
            heading="Reading your bill photo — please wait",
            hint="Gemini reads your photos and looks up each medicine schedule online — keep internet on.",
        )
    return _BillScanProgressDialog(
        root,
        title="Reading purchase bill",
        heading="Reading PDF or Excel — please wait",
        hint="Large PDFs are parsed in the background so the app stays responsive.",
    )


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

    path_list = _pick_bill_import_paths(parent)
    if not path_list:
        return False
    root = parent.winfo_toplevel()

    try:
        dialog = _import_progress_dialog(root, path_list)
        invoice = dialog.run(
            lambda progress: _parse_invoice_worker(path_list, on_progress=progress)
        )
    except Exception as exc:
        messagebox.showerror("Import Error", str(exc), parent=parent)
        return False
    finally:
        try:
            root.config(cursor="")
        except Exception:
            pass

    if not invoice:
        return False

    try:
        enrich_dialog = _BillScanProgressDialog(
            root,
            title="Preparing imported rows",
            heading="Matching medicines — please wait",
            hint="Uses your store database and Gemini (for bill photos).",
        )
        invoice = enrich_dialog.run(
            lambda progress: _enrich_invoice_worker(
                purchase_page, invoice, path_list, on_progress=progress,
            )
        )
    except Exception as exc:
        messagebox.showerror("Import Error", str(exc), parent=parent)
        return False

    from core.bill_page_utils import invoice_may_need_more_pages

    page_count = len(path_list) if _is_image_import(path_list) else 1
    if invoice_may_need_more_pages(invoice, page_count):
        if messagebox.askyesno(
            "More pages?",
            "This bill may be incomplete — the bill looks like it continues on "
            "another page, or the line total does not match the printed Sub Total "
            "({} page(s) scanned).\n\n"
            "Add the remaining page(s) and scan again?".format(page_count),
            parent=parent,
        ):
            extra = _MultiPageBillPickerDialog(parent, initial_paths=path_list).show()
            if extra and len(extra) > page_count:
                try:
                    dialog = _import_progress_dialog(root, extra)
                    invoice = dialog.run(
                        lambda progress: _parse_invoice_worker(extra, on_progress=progress),
                    )
                    path_list = extra
                    enrich_dialog = _BillScanProgressDialog(
                        root,
                        title="Preparing imported rows",
                        heading="Matching medicines — please wait",
                        hint="Uses your store database and Gemini (for bill photos).",
                    )
                    invoice = enrich_dialog.run(
                        lambda progress: _enrich_invoice_worker(
                            purchase_page, invoice, path_list, on_progress=progress,
                        )
                    )
                except Exception as exc:
                    messagebox.showerror("Import Error", str(exc), parent=parent)
                    return False

    items = list(invoice.items)
    if not items:
        hint = ""
        if invoice.source_type == "image":
            hint = (
                "\n\nTips for bill photos:\n"
                "• Add every page if the bill says “Continued…”\n"
                "• Place each page flat with good lighting\n"
                "• Include the full table (all columns visible)\n"
                "• Hold camera steady; avoid blur"
            )
        messagebox.showwarning(
            "Empty Invoice",
            "No medicine rows were found in this file." + hint,
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

    expected_count = int(getattr(invoice, "expected_item_count", 0) or 0)
    if expected_count > 0 and len(items) < expected_count:
        if not messagebox.askyesno(
            "Item count mismatch",
            "The bill footer shows {} item(s), but only {} row(s) were imported.\n\n"
            "Some medicines may be missing — check all 4 pages were scanned clearly.\n\n"
            "Continue with {} row(s)?".format(
                expected_count, len(items), len(items),
            ),
            parent=parent,
        ):
            return False

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
        load_dialog = _BillScanProgressDialog(
            root,
            title="Loading purchase rows",
            heading="Adding medicines to purchase page — please wait",
            hint="Matching inventory and building the purchase table in the background.",
        )

        def _load_worker(progress):
            progress("Preparing purchase rows…")
            with _background_db_conn(purchase_page) as conn:
                return import_into_purchase_page(
                    purchase_page,
                    invoice,
                    list(invoice.items),
                    replace_existing=replace_existing,
                    conn=conn,
                    on_progress=progress,
                    ui_apply=False,
                )

        prepared = load_dialog.run(_load_worker)
        result = import_into_purchase_page(
            purchase_page,
            invoice,
            list(invoice.items),
            replace_existing=replace_existing,
            prepared=prepared,
        )
    except Exception as exc:
        msg = str(exc).lower()
        if "database is locked" in msg or "database is busy" in msg:
            messagebox.showerror(
                "Import Error",
                "Database is busy (another task briefly locked the store file).\n\n"
                "Close any second Satpuda window, wait a few seconds, "
                "and try Import Purchase again.",
                parent=parent,
            )
            return False
        messagebox.showerror("Import Error", str(exc), parent=parent)
        return False

    source_label = "image scan" if invoice.source_type == "image" else "file"
    write_import_log(invoice, "imported", "Loaded onto purchase page from {}".format(source_label))
    note = _placeholder_note(invoice.items)
    disc_note = ""
    inv_disc = float(getattr(invoice, "product_discount", 0) or 0) + float(
        getattr(invoice, "cash_discount", 0) or 0
    )
    if inv_disc > 0:
        disc_note = "\nBill discount (overall): ₹{:.2f} — shown in Overall Disc ₹.".format(
            inv_disc,
        )
    supplier_note = ""
    if (getattr(invoice, "parser", "") or "").startswith("EDI H/T/F") and not (
        invoice.supplier_name or ""
    ).strip():
        supplier_note = (
            "\nEnter the supplier name manually — this CSV has bill no/date only."
        )
    elif (invoice.supplier_name or "").strip():
        supplier_note = "\nSupplier: {}.".format(invoice.supplier_name.strip())
    ocr_note = ""
    if invoice.source_type == "image":
        pages_note = ""
        if page_count > 1:
            pages_note = " from {} pages".format(page_count)
        count_note = ""
        expected_count = int(getattr(invoice, "expected_item_count", 0) or 0)
        if expected_count > 0 and len(invoice.items) < expected_count:
            count_note = (
                "\nWarning: bill shows {} items but only {} were imported — verify all rows.".format(
                    expected_count, len(invoice.items),
                )
            )
        ocr_note = (
            "\nImported from bill photo{} ({} items) — verify every row before saving.{}".format(
                pages_note,
                len(invoice.items),
                count_note,
            )
        )
    messagebox.showinfo(
        "Import Complete",
        "{} item(s) loaded on the Purchase page.\n"
        "Line discounts are shown in the Disc column (₹ or % from the bill).{}"
        "{}{}{}\nReview batches, rates and expiry, then save the purchase.".format(
            result.get("items_imported", len(invoice.items)),
            disc_note,
            supplier_note,
            ocr_note,
            note,
        ),
        parent=parent,
    )
    try:
        purchase_page.medicine_name.focus_set()
    except Exception:
        pass
    return True
