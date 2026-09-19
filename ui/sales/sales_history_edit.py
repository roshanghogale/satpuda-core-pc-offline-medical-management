"""
ui/sales/sales_history_edit.py
──────────────────────────────
Fullscreen sales edit using the same BillingPage as the main Sales screen.
"""
import tkinter as tk

try:
    import ttkbootstrap as ttk
except ImportError:
    from tkinter import ttk

from core.themed_messagebox import showinfo, showerror
from core.billing_service import update_existing_bill
from core.sales_form_io import load_sale_into_billing_page


def open_edit_window(parent, conn, sale_id, bill_label, refresh_callback):
    """Open zoomed sales edit window with full BillingPage."""
    from ui.billing import BillingPage
    from core.scroll_manager import bind_scroll_descendants, refresh_scroll_region

    edit_window = tk.Toplevel(parent)
    edit_window.title(f"Edit Sale - {bill_label}")
    try:
        from core.window_icon import apply_window_icon
        apply_window_icon(edit_window, master=parent.winfo_toplevel(), is_root=False)
    except Exception:
        pass
    edit_window.state('zoomed')

    container = ttk.Frame(edit_window)
    container.pack(fill=tk.BOTH, expand=True)

    page = BillingPage(container, conn)
    page._in_edit_window = True

    root = parent.winfo_toplevel()
    ctrl = getattr(root, '_input_ctrl', None)
    prev_canvas = prev_frame = None
    if ctrl is not None:
        prev_canvas = ctrl._canvas
        prev_frame = getattr(ctrl, '_active_frame', None)
        ctrl.set_active_canvas(getattr(page._inner_frame, '_canvas', None))
        ctrl.set_active_frame(page._inner_frame)

    try:
        bind_scroll_descendants(page._inner_frame, force=True)
        refresh_scroll_region(page._inner_frame)
    except TypeError:
        bind_scroll_descendants(page._inner_frame)
        refresh_scroll_region(page._inner_frame)

    ok = load_sale_into_billing_page(conn, page, sale_id)
    if not ok:
        try:
            edit_window.destroy()
        except Exception:
            pass
        showerror(
            "Edit Sale",
            f"Could not load sale {bill_label} from the server.\n"
            "Refresh Sales History and try again.",
            parent=parent,
        )
        return

    _closed = [False]

    def _close():
        if _closed[0]:
            return
        _closed[0] = True
        if ctrl is not None:
            ctrl.set_active_canvas(prev_canvas)
            ctrl.set_active_frame(prev_frame)
        try:
            edit_window.destroy()
        except Exception:
            pass
        refresh_callback()

    def _update():
        _save_edit(conn, page, sale_id, bill_label, _close)

    page.save_sales = _update
    if hasattr(page, 'generate_btn'):
        page.generate_btn.config(text="Update Sale (F5)", command=_update)
    if hasattr(page, 'print_sales_1_btn'):
        page.print_sales_1_btn.pack_forget()
    if hasattr(page, 'print_sales_2_btn'):
        page.print_sales_2_btn.pack_forget()
    edit_window.protocol("WM_DELETE_WINDOW", _close)
    from core.dialog_escape import bind_escape_to_close
    bind_escape_to_close(edit_window, on_close=_close)


def _save_edit(conn, page, sale_id, bill_label, close_fn):
    from core.background_workers import run_on_ui_with_busy

    saved = run_on_ui_with_busy(
        page.parent,
        "Updating Sale",
        page._persist_sale_edit,
        message="Updating bill… please wait.",
    )
    if not saved:
        return
    showinfo("Success", f"Sale {bill_label} updated successfully!")
    page._editing_sale_id = None
    page._edit_payment_snapshot = None
    close_fn()
