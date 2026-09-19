"""Print bill — save to Downloads and open system printer dialog (no preview window)."""
from __future__ import annotations

from core.bill_output import get_page_copies, save_bill_to_downloads
from core.themed_messagebox import showerror, showinfo


def show_bill_print_dialog(parent, conn, bill_no, sale_id):
    from core.background_workers import run_with_progress

    hwnd = int(parent.winfo_id()) if parent else 0

    def _worker(put):
        put("Generating bill PDF…")
        _, pdf_path = save_bill_to_downloads(conn, sale_id)
        if not pdf_path:
            raise RuntimeError(
                'Bill HTML was saved to Downloads, but PDF could not be created for printing.\n'
                'Install Microsoft Edge (or Google Chrome) and try again.'
            )
        return pdf_path

    def _done(pdf_path):
        from core.bill_output import _open_system_print_dialog
        try:
            _open_system_print_dialog(pdf_path, hwnd_owner=hwnd, copies=get_page_copies())
            showinfo("Print Bill", f"Bill {bill_no} sent to printer.", parent=parent)
        except Exception as exc:
            showerror("Print Bill", f"Could not print bill:\n{exc}", parent=parent)

    run_with_progress(
        parent,
        "Printing Bill",
        _worker,
        on_complete=_done,
        on_error=lambda exc: showerror("Print Bill", f"Could not print bill:\n{exc}", parent=parent),
    )
