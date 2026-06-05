import os
import threading
import tkinter as tk
from tkinter import filedialog, messagebox

try:
    import ttkbootstrap as ttk
except ImportError:
    from tkinter import ttk

from core.medicine_type_detector import enrich_invoice_medicine_types, resolve_medicine_type
from core.purchase_importer import (
    ImportedPurchaseItem,
    InvoiceParseError,
    PurchaseInvoice,
    import_into_purchase_page,
    normalize_expiry,
    parse_purchase_excel,
    parse_purchase_pdf,
    write_import_log,
)


class ImportPurchaseDialog(tk.Toplevel):
    """Preview and import one supplier purchase invoice into PurchasePage."""

    def __init__(self, parent, purchase_page):
        super().__init__(parent)
        self.purchase_page = purchase_page
        self.invoice = None
        self.items = []
        self._parse_thread = None

        self.file_path_var = tk.StringVar()
        self.status_var = tk.StringVar(value="Select a PDF, CSV or Excel purchase invoice.")
        self.supplier_var = tk.StringVar()
        self.supplier_address_var = tk.StringVar()
        self.supplier_phone_var = tk.StringVar()
        self.supplier_gstin_var = tk.StringVar()
        self.supplier_dl_var = tk.StringVar()
        self.invoice_no_var = tk.StringVar()
        self.invoice_date_var = tk.StringVar()
        self.parser_var = tk.StringVar(value="-")
        self.summary_var = tk.StringVar(value="Items: 0")
        self.amount_var = tk.StringVar(value="Invoice amount: 0.00")
        self.verify_var = tk.StringVar(value="Verification: waiting for file")
        self.confidence_var = tk.StringVar(value="Confidence: —")
        self.disc_info_var = tk.StringVar(value="")

        self.title("Import Purchase Invoice")
        self.transient(parent.winfo_toplevel() if hasattr(parent, "winfo_toplevel") else parent)
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)

        body_shell = ttk.Frame(self)
        body_shell.grid(row=0, column=0, sticky="nsew")
        from core.scroll_manager import make_dialog_scrollable, ensure_toplevel_fits_screen
        scroll_body = make_dialog_scrollable(body_shell)

        footer_shell = ttk.Frame(self)
        footer_shell.grid(row=1, column=0, sticky="ew")
        ttk.Separator(footer_shell, orient="horizontal").pack(fill=tk.X)
        self._dialog_footer = ttk.Frame(footer_shell, padding=(10, 8))
        self._dialog_footer.pack(fill=tk.X)

        self._build_ui(scroll_body)
        self._enable_drag_drop()
        self.after(80, lambda: ensure_toplevel_fits_screen(self, width=1050, height=720, resizable=True))
        self.grab_set()
        self.focus_set()

    def _build_ui(self, outer):
        outer.pack(fill=tk.BOTH, expand=True)
        outer.grid_columnconfigure(0, weight=1)
        outer.grid_rowconfigure(3, weight=1)

        file_frame = ttk.LabelFrame(outer, text="Invoice File")
        file_frame.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        file_frame.grid_columnconfigure(1, weight=1)

        self.drop_label = ttk.Label(file_frame, text="Browse invoice file", anchor="center")
        self.drop_label.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 2), columnspan=4)

        ttk.Label(file_frame, text="File:").grid(row=1, column=0, sticky="w", padx=(8, 4), pady=8)
        self.file_entry = ttk.Entry(file_frame, textvariable=self.file_path_var, state="readonly")
        self.file_entry.grid(row=1, column=1, sticky="ew", padx=4, pady=8)
        self.browse_btn = ttk.Button(file_frame, text="Browse", command=self._browse_file)
        self.browse_btn.grid(row=1, column=2, padx=4, pady=8)
        self.parse_btn = ttk.Button(file_frame, text="Import File", command=self._parse_selected_file)
        self.parse_btn.grid(row=1, column=3, padx=(4, 8), pady=8)

        meta = ttk.LabelFrame(outer, text="Invoice Summary")
        meta.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        for col in range(6):
            meta.grid_columnconfigure(col, weight=1 if col in (1, 3, 5) else 0)

        ttk.Label(meta, text="Supplier:").grid(row=0, column=0, sticky="w", padx=8, pady=(8, 4))
        ttk.Entry(meta, textvariable=self.supplier_var).grid(row=0, column=1, sticky="ew", padx=4, pady=(8, 4))
        ttk.Label(meta, text="Invoice No:").grid(row=0, column=2, sticky="w", padx=8, pady=(8, 4))
        ttk.Entry(meta, textvariable=self.invoice_no_var).grid(row=0, column=3, sticky="ew", padx=4, pady=(8, 4))
        ttk.Label(meta, text="Invoice Date:").grid(row=0, column=4, sticky="w", padx=8, pady=(8, 4))
        ttk.Entry(meta, textvariable=self.invoice_date_var).grid(row=0, column=5, sticky="ew", padx=(4, 8), pady=(8, 4))

        ttk.Label(meta, text="Address:").grid(row=1, column=0, sticky="w", padx=8, pady=4)
        ttk.Entry(meta, textvariable=self.supplier_address_var).grid(
            row=1, column=1, columnspan=3, sticky="ew", padx=4, pady=4,
        )
        ttk.Label(meta, text="Phone:").grid(row=1, column=4, sticky="w", padx=8, pady=4)
        ttk.Entry(meta, textvariable=self.supplier_phone_var).grid(row=1, column=5, sticky="ew", padx=(4, 8), pady=4)

        ttk.Label(meta, text="GSTIN:").grid(row=2, column=0, sticky="w", padx=8, pady=4)
        ttk.Entry(meta, textvariable=self.supplier_gstin_var).grid(row=2, column=1, sticky="ew", padx=4, pady=4)
        ttk.Label(meta, text="DL Numbers:").grid(row=2, column=2, sticky="w", padx=8, pady=4)
        ttk.Entry(meta, textvariable=self.supplier_dl_var).grid(
            row=2, column=3, columnspan=3, sticky="ew", padx=4, pady=4,
        )

        ttk.Label(meta, textvariable=self.summary_var).grid(row=3, column=0, columnspan=2, sticky="w", padx=8, pady=(0, 4))
        ttk.Label(meta, textvariable=self.amount_var).grid(row=3, column=2, columnspan=2, sticky="w", padx=8, pady=(0, 4))
        ttk.Label(meta, textvariable=self.verify_var).grid(row=3, column=4, columnspan=2, sticky="w", padx=8, pady=(0, 4))
        ttk.Label(meta, textvariable=self.confidence_var).grid(row=4, column=0, columnspan=3, sticky="w", padx=8, pady=(0, 4))
        ttk.Label(meta, textvariable=self.disc_info_var).grid(row=4, column=3, columnspan=3, sticky="w", padx=8, pady=(0, 4))
        ttk.Label(meta, text="Parser:").grid(row=5, column=0, sticky="w", padx=8, pady=(0, 8))
        ttk.Label(meta, textvariable=self.parser_var).grid(row=5, column=1, columnspan=5, sticky="w", padx=4, pady=(0, 8))

        status_row = ttk.Frame(outer)
        status_row.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        status_row.grid_columnconfigure(1, weight=1)
        self.progress = ttk.Progressbar(status_row, mode="indeterminate")
        self.progress.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        ttk.Label(status_row, textvariable=self.status_var).grid(row=0, column=1, sticky="w")

        tree_frame = ttk.LabelFrame(outer, text="Preview")
        tree_frame.grid(row=3, column=0, sticky="nsew", pady=(0, 8))
        tree_frame.grid_columnconfigure(0, weight=1)
        tree_frame.grid_rowconfigure(0, weight=1)

        cols = ("Name", "Type", "Batch", "Expiry", "Qty", "Free", "Rate", "Disc", "GST", "MRP", "Amount")
        self.tree = ttk.Treeview(tree_frame, columns=cols, show="headings", height=14)
        widths = {
            "Name": 200, "Type": 70, "Batch": 80, "Expiry": 65, "Qty": 50,
            "Free": 45, "Rate": 70, "Disc": 55, "GST": 45, "MRP": 65, "Amount": 75,
        }
        for col in cols:
            self.tree.heading(col, text=col)
            self.tree.column(col, width=widths[col], anchor="w" if col == "Name" else "e")
        self.tree.tag_configure("invalid", background="#ffd6d6")
        self.tree.tag_configure("warn", background="#fff3cd")
        self.tree.bind("<Double-1>", lambda event: self._edit_selected_row())
        self.tree.bind("<Delete>", lambda event: self._delete_selected_row())

        yscroll = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=self.tree.yview)
        xscroll = ttk.Scrollbar(tree_frame, orient=tk.HORIZONTAL, command=self.tree.xview)
        self.tree.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="ew")

        buttons = self._dialog_footer
        self.delete_btn = ttk.Button(buttons, text="Delete Row", command=self._delete_selected_row)
        self.delete_btn.pack(side=tk.LEFT, padx=(0, 6))
        self.edit_btn = ttk.Button(buttons, text="Edit Row", command=self._edit_selected_row)
        self.edit_btn.pack(side=tk.LEFT, padx=6)
        ttk.Button(buttons, text="Cancel", command=self.destroy).pack(side=tk.RIGHT, padx=(6, 0))
        self.import_btn = ttk.Button(buttons, text="Import to Purchase", command=self._apply_import, state=tk.DISABLED)
        self.import_btn.pack(side=tk.RIGHT, padx=6)

    def _enable_drag_drop(self):
        try:
            self.drop_target_register("DND_Files")
            self.dnd_bind("<<Drop>>", self._on_drop_file)
            self.drop_label.configure(text="Drop invoice file here or use Browse")
        except Exception:
            self.drop_label.configure(text="Browse PDF, CSV or Excel invoice")

    def _on_drop_file(self, event):
        try:
            paths = self.tk.splitlist(event.data)
            if paths:
                self.file_path_var.set(paths[0])
                self._parse_selected_file()
        except Exception as exc:
            messagebox.showerror("Drop Error", str(exc), parent=self)

    def _browse_file(self):
        path = filedialog.askopenfilename(
            title="Select purchase invoice",
            filetypes=[
                ("Invoice files", "*.pdf *.csv *.xlsx *.xls"),
                ("PDF files", "*.pdf"),
                ("CSV files", "*.csv"),
                ("Excel files", "*.xlsx *.xls"),
                ("All files", "*.*"),
            ],
            parent=self,
        )
        if path:
            self.file_path_var.set(path)
            self._parse_selected_file()

    def _parse_selected_file(self):
        path = self.file_path_var.get().strip()
        if not path:
            messagebox.showwarning("Select File", "Please choose an invoice file first.", parent=self)
            return
        if self._parse_thread and self._parse_thread.is_alive():
            return
        self.invoice = None
        self.items = []
        self._refresh_preview()
        self._set_busy(True, "Parsing invoice...")
        self._parse_thread = threading.Thread(target=self._parse_worker, args=(path,), daemon=True)
        self._parse_thread.start()

    def _parse_worker(self, path):
        try:
            ext = os.path.splitext(path)[1].lower()
            if ext == ".pdf":
                invoice = parse_purchase_pdf(path)
            elif ext in (".csv", ".xlsx", ".xls"):
                invoice = parse_purchase_excel(path)
            else:
                raise InvoiceParseError("Unsupported file type: {}".format(ext))
            self.after(0, lambda inv=invoice: self._load_invoice(inv))
        except Exception as exc:
            self.after(0, lambda err=exc: self._parse_failed(err))

    def _load_invoice(self, invoice):
        self._set_busy(False, "Invoice parsed. Review rows before importing.")
        enrich_invoice_medicine_types(
            invoice,
            conn=getattr(self.purchase_page, "conn", None),
            available_types=getattr(self.purchase_page, "_med_types", None),
        )
        self.invoice = invoice
        self.items = list(invoice.items)
        self.supplier_var.set(invoice.supplier_name)
        self.supplier_address_var.set(invoice.supplier_address)
        self.supplier_phone_var.set(invoice.supplier_phone)
        self.supplier_gstin_var.set(invoice.supplier_gstin)
        self.supplier_dl_var.set(invoice.supplier_dl)
        self.invoice_no_var.set(invoice.invoice_number)
        self.invoice_date_var.set(invoice.invoice_date)
        self.parser_var.set(invoice.parser or "-")
        scores = getattr(invoice, "confidence_scores", {}) or {}
        overall = scores.get("overall_confidence", 0)
        self.confidence_var.set(
            "Confidence: {:.0f}%{}".format(
                overall,
                " — review needed" if scores.get("requires_review") else "",
            )
        )
        prod = float(getattr(invoice, "product_discount", 0) or 0)
        cash = float(getattr(invoice, "cash_discount", 0) or 0)
        if prod or cash:
            self.disc_info_var.set(
                "Footer disc: prod ₹{:.2f}  cash ₹{:.2f}".format(prod, cash)
            )
        else:
            self.disc_info_var.set("")
        self._refresh_preview()
        write_import_log(invoice, "parsed", "Invoice parsed for preview")
        if invoice.issues:
            self.status_var.set("Parsed with warnings. Double-check highlighted rows.")
        if not self.items:
            self.import_btn.config(state=tk.DISABLED)
            messagebox.showwarning("Empty Invoice", "No medicine rows were found.", parent=self)

    def _parse_failed(self, error):
        self._set_busy(False, "Import failed.")
        messagebox.showerror("Import Error", str(error), parent=self)

    def _set_busy(self, busy, message):
        self.status_var.set(message)
        if busy:
            self.progress.start(12)
            self.import_btn.config(state=tk.DISABLED)
            self.parse_btn.config(state=tk.DISABLED)
            self.browse_btn.config(state=tk.DISABLED)
        else:
            self.progress.stop()
            self.parse_btn.config(state=tk.NORMAL)
            self.browse_btn.config(state=tk.NORMAL)
            self.import_btn.config(state=tk.NORMAL if self.items else tk.DISABLED)

    def _resolve_item_type(self, item):
        raw = item.raw or {}
        return resolve_medicine_type(
            conn=getattr(self.purchase_page, "conn", None),
            name=item.name,
            pack=item.pack,
            qty_unit=str(raw.get("qty_unit") or raw.get("unit") or ""),
            pkg_unit=str(raw.get("pkg_unit") or item.pack or ""),
            available_types=getattr(self.purchase_page, "_med_types", None),
            save_learned=False,
        )

    def _refresh_preview(self):
        for row in self.tree.get_children():
            self.tree.delete(row)

        invalid_count = 0
        for idx, item in enumerate(self.items):
            valid = item.is_valid
            if not valid:
                invalid_count += 1
            amount = item.amount or round(float(item.qty or 0) * float(item.rate or 0), 2)
            disc_label = ""
            if getattr(item, "disc_column_type", "") == "RUPEE_AMOUNT" and item.disc_column_value:
                disc_label = "₹{:.2f}".format(item.disc_column_value)
            elif getattr(item, "disc_column_type", "") == "PERCENTAGE" and item.disc_column_value:
                disc_label = "{:.1f}%".format(item.disc_column_value)
            elif item.discount_pct:
                disc_label = "{:.1f}%".format(item.discount_pct)
            tags = ("invalid",) if not valid else ()
            if not getattr(item, "amount_validated", True):
                tags = tags + ("warn",)
            self.tree.insert(
                "",
                tk.END,
                iid=str(idx),
                values=(
                    item.name,
                    item.medicine_type or self._resolve_item_type(item),
                    item.batch,
                    item.expiry,
                    "{:.2f}".format(float(item.qty or 0)),
                    "{:.2f}".format(float(item.free_qty or 0)),
                    "{:.2f}".format(float(item.rate or 0)),
                    disc_label,
                    "{:.2f}".format(float(item.gst_pct or 0)),
                    "{:.2f}".format(float(item.mrp or 0)),
                    "{:.2f}".format(float(amount or 0)),
                ),
                tags=tags,
            )

        invoice_total = float(self.invoice.invoice_total or 0) if self.invoice else 0.0
        prod_disc = float(getattr(self.invoice, "product_discount", 0) or 0) if self.invoice else 0.0
        cash_disc = float(getattr(self.invoice, "cash_discount", 0) or 0) if self.invoice else 0.0
        line_taxable = round(
            sum(float(i.amount or (i.qty * i.rate) or 0) for i in self.items),
            2,
        )
        self.summary_var.set("Items: {}   Invalid: {}".format(len(self.items), invalid_count))
        self.amount_var.set(
            "Bill net: {:.2f}   Line taxable: {:.2f}   Disc: {:.2f} (prod {:.2f} + cash {:.2f})".format(
                invoice_total, line_taxable, prod_disc + cash_disc, prod_disc, cash_disc,
            )
        )
        if invoice_total:
            self.verify_var.set("Verification: total will match bill net {:.2f}".format(invoice_total))
        else:
            self.verify_var.set("Verification: bill net not detected — review totals")
        self.import_btn.config(state=tk.NORMAL if self.items else tk.DISABLED)

    def _delete_selected_row(self):
        selection = self.tree.selection()
        if not selection:
            messagebox.showwarning("Select Row", "Please select a row to delete.", parent=self)
            return
        for iid in sorted(selection, key=lambda value: int(value), reverse=True):
            idx = int(iid)
            if 0 <= idx < len(self.items):
                del self.items[idx]
        self._refresh_preview()
        self.status_var.set("Selected row deleted.")

    def _edit_selected_row(self):
        selection = self.tree.selection()
        if not selection:
            messagebox.showwarning("Select Row", "Please select a row to edit.", parent=self)
            return
        idx = int(selection[0])
        if not (0 <= idx < len(self.items)):
            return
        available_types = getattr(self.purchase_page, "_med_types", [])
        _EditImportedItemDialog(
            self,
            self.items[idx],
            available_types,
            self._on_item_edited,
            purchase_page=self.purchase_page,
        )

    def _on_item_edited(self):
        self._refresh_preview()
        self.status_var.set("Row updated. Review totals before importing.")

    def _sync_invoice_header(self):
        if not self.invoice:
            return
        self.invoice.supplier_name = self.supplier_var.get().strip()
        self.invoice.supplier_address = self.supplier_address_var.get().strip()
        self.invoice.supplier_phone = self.supplier_phone_var.get().strip()
        self.invoice.supplier_gstin = self.supplier_gstin_var.get().strip()
        self.invoice.supplier_dl = self.supplier_dl_var.get().strip()
        self.invoice.invoice_number = self.invoice_no_var.get().strip()
        self.invoice.invoice_date = self.invoice_date_var.get().strip()
        self.invoice.items = list(self.items)

    def _apply_import(self):
        if not self.invoice or not self.items:
            messagebox.showwarning("No Data", "Please import and preview an invoice first.", parent=self)
            return
        self._sync_invoice_header()

        invalid = [item for item in self.items if not item.is_valid]
        if invalid:
            first = invalid[0]
            messagebox.showerror(
                "Invalid Rows",
                "Fix or delete highlighted rows before importing.\n\nRow {}: {}".format(
                    first.source_row or "?", "; ".join(first.issues)
                ),
                parent=self,
            )
            return

        if not self.invoice.supplier_name:
            if not messagebox.askyesno(
                "Missing Supplier",
                "Supplier name was not detected. Import anyway and fill it on the purchase page?",
                parent=self,
            ):
                return

        replace_existing = True
        if getattr(self.purchase_page, "purchase_items", None):
            answer = messagebox.askyesnocancel(
                "Existing Purchase Items",
                "This purchase page already has items.\n\n"
                "Yes: replace them with imported rows.\n"
                "No: append imported rows.\n"
                "Cancel: return to preview.",
                parent=self,
            )
            if answer is None:
                return
            replace_existing = bool(answer)

        try:
            result = import_into_purchase_page(
                self.purchase_page,
                self.invoice,
                self.items,
                replace_existing=replace_existing,
            )
        except Exception as exc:
            messagebox.showerror("Import Error", str(exc), parent=self)
            return

        messagebox.showinfo(
            "Import Complete",
            "{} item(s) imported into the purchase page.".format(result["items_imported"]),
            parent=self,
        )
        self.destroy()


class _EditImportedItemDialog(tk.Toplevel):
    def __init__(
        self,
        parent,
        item: ImportedPurchaseItem,
        available_types,
        on_save,
        purchase_page=None,
    ):
        super().__init__(parent)
        self.item = item
        self.on_save = on_save
        self.purchase_page = purchase_page
        self.available_types = list(available_types or [])
        self.vars = {}

        self.title("Edit Imported Row")
        self.transient(parent)
        self.grab_set()
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)

        body_shell = ttk.Frame(self)
        body_shell.grid(row=0, column=0, sticky="nsew")
        from core.scroll_manager import make_dialog_scrollable, ensure_toplevel_fits_screen
        scroll_body = make_dialog_scrollable(body_shell)

        footer_shell = ttk.Frame(self)
        footer_shell.grid(row=1, column=0, sticky="ew")
        ttk.Separator(footer_shell, orient="horizontal").pack(fill=tk.X)
        self._row_footer = ttk.Frame(footer_shell, padding=(10, 8))
        self._row_footer.pack(fill=tk.X)

        self._build_ui(scroll_body)
        self.after(50, lambda: ensure_toplevel_fits_screen(self, width=520, height=430, resizable=True))
        self.focus_set()

    def _build_ui(self, frame):
        frame.pack(fill=tk.BOTH, expand=True, padx=12, pady=8)
        frame.grid_columnconfigure(1, weight=1)

        fields = [
            ("Name", "name"),
            ("Type", "medicine_type"),
            ("Pack", "pack"),
            ("Batch", "batch"),
            ("Expiry (MM/YY)", "expiry"),
            ("Qty", "qty"),
            ("Free", "free_qty"),
            ("Rate", "rate"),
            ("GST %", "gst_pct"),
            ("MRP", "mrp"),
            ("HSN", "hsn_code"),
            ("Manufacturer", "manufacturer"),
            ("Amount", "amount"),
        ]
        for row, (label, attr) in enumerate(fields):
            ttk.Label(frame, text=label + ":").grid(row=row, column=0, sticky="w", padx=(0, 8), pady=4)
            value = getattr(self.item, attr)
            var = tk.StringVar(value="" if value is None else str(value))
            self.vars[attr] = var
            if attr == "medicine_type" and self.available_types:
                widget = ttk.Combobox(frame, textvariable=var, values=self.available_types, state="normal")
            else:
                widget = ttk.Entry(frame, textvariable=var)
            widget.grid(row=row, column=1, sticky="ew", pady=4)

        ttk.Button(self._row_footer, text="Cancel", command=self.destroy).pack(side=tk.RIGHT, padx=(6, 0))
        ttk.Button(self._row_footer, text="Save", command=self._save).pack(side=tk.RIGHT)

    def _save(self):
        try:
            self.item.name = self.vars["name"].get().strip()
            self.item.medicine_type = self.vars["medicine_type"].get().strip()
            self.item.pack = self.vars["pack"].get().strip()
            self.item.batch = self.vars["batch"].get().strip()
            self.item.expiry = normalize_expiry(self.vars["expiry"].get())
            self.item.qty = self._as_float("qty")
            self.item.free_qty = self._as_float("free_qty")
            self.item.rate = self._as_float("rate")
            self.item.gst_pct = self._as_float("gst_pct")
            self.item.mrp = self._as_float("mrp")
            self.item.hsn_code = self.vars["hsn_code"].get().strip()
            self.item.manufacturer = self.vars["manufacturer"].get().strip()
            self.item.amount = self._as_float("amount") or round(self.item.qty * self.item.rate, 2)
            if not self.item.medicine_type:
                raw = self.item.raw or {}
                self.item.medicine_type = resolve_medicine_type(
                    conn=getattr(self.purchase_page, "conn", None) if self.purchase_page else None,
                    name=self.item.name,
                    pack=self.item.pack,
                    qty_unit=str(raw.get("qty_unit") or raw.get("unit") or ""),
                    pkg_unit=str(raw.get("pkg_unit") or self.item.pack or ""),
                    available_types=self.available_types or None,
                    save_learned=False,
                )
            self.item.validate()
        except ValueError as exc:
            messagebox.showerror("Invalid Value", str(exc), parent=self)
            return
        self.on_save()
        self.destroy()

    def _as_float(self, attr):
        text = self.vars[attr].get().strip().replace(",", "")
        if not text:
            return 0.0
        try:
            return float(text)
        except ValueError as exc:
            raise ValueError("{} must be a number.".format(attr.replace("_", " ").title())) from exc
