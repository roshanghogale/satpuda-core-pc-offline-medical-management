"""
export_manager.py
-----------------
Centralised export engine for CSV, Excel and PDF (HTML-based).
All exports open a save-file dialog then write the chosen format.

Public API
----------
export_data(parent, title, headers, rows, default_name)
    -> shows format chooser dialog then saves

export_all_combined(parent, sections)
    -> sections = list of (title, headers, rows)
    -> one dialog, one file with all sections combined
"""
from __future__ import annotations

import os
import re
import csv
import tempfile

# Tk is deliberately EXCLUDED from the headless data-engine build, exactly as it
# is for core/themed_messagebox.py -- and for the same reason this file must not
# import it at module scope. Four things the desktop engine does reach into this
# module for its Tk-FREE helpers:
#
#   core/desktop_export_service.py:1773  Export dialog -> PDF / Print (every page)
#   core/desktop_startup_service.py:207  Startup alerts -> Export PDF
#   core/desktop_settings_service.py     Settings -> data export / export contacts
#
# and every one of them died at `import tkinter` before reaching a single line of
# export code, so the desktop API answered 500 and the shop saw "Export failed".
# Nothing above the function bodies touches tk, so degrading here costs the Tk
# app nothing: it always has tkinter, and the engine never calls a dialog.
try:
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox

    HAS_TK = True
except Exception:  # frozen sidecar / no display
    tk = None
    ttk = None
    filedialog = None
    messagebox = None
    HAS_TK = False


def _apply_icon(window):
    """Apply satpuda_logo to any Tk/Toplevel window."""
    try:
        from core.window_icon import apply_window_icon
        apply_window_icon(window)
    except Exception:
        pass


# ── Export report picker (keyboard: ↑↓, Enter) ───────────────────────────────

def show_export_option_dialog(parent, title, options, *, width=360, height=None):
    """
    Show a modal list of export actions.

    options: sequence of (label, callable).
    Navigate with ↑↓, confirm with Enter or double-click, cancel with Escape.
    """
    from core.scroll_manager import DIALOG_SIZE_MEDIUM, dialog_root, open_dialog
    from core.font_config import FONT_FAMILY, FONT_SIZE_LABELS
    from core.dialog_escape import bind_escape_to_close
    from core.dialog_keyboard import wire_export_option_listbox

    items = list(options or [])
    if not items:
        return

    top = parent.winfo_toplevel()
    n = len(items)
    dlg_h = height or min(DIALOG_SIZE_MEDIUM[1], 140 + n * 30)
    dlg_w = max(width, DIALOG_SIZE_MEDIUM[0])
    dlg = open_dialog(top, title, width=dlg_w, height=dlg_h, resizable=True)
    body = dialog_root(dlg.content, padding=8)

    lb = tk.Listbox(
        body,
        height=min(n, 14),
        font=(FONT_FAMILY, FONT_SIZE_LABELS),
        activestyle='dotbox',
        exportselection=False,
        takefocus=True,
    )
    lb.pack(fill=tk.BOTH, expand=True, padx=12, pady=12)
    for label, _ in items:
        lb.insert(tk.END, label)
    lb.selection_set(0)
    lb.activate(0)

    def _run_selected():
        sel = lb.curselection()
        idx = int(sel[0]) if sel else 0
        try:
            dlg.grab_release()
        except Exception:
            pass
        dlg.destroy()
        try:
            items[idx][1]()
        except Exception:
            pass

    bind_escape_to_close(dlg)
    wire_export_option_listbox(dlg, lb, _run_selected)

    from core.voice.voice_dialog import (
        consume_voice_dialog_hints,
        register_export_option_dialog,
        speak_voice_dialog_hint,
    )

    register_export_option_dialog(dlg, lb, items)
    if consume_voice_dialog_hints():
        dlg.after(50, lambda: speak_voice_dialog_hint("Select export type."))


# ── Voice / preset format (skip format picker) ───────────────────────────────

_voice_export_fmt: str | None = None


def set_voice_export_format(fmt: str | None) -> None:
    """Satpuda voice sets this before opening an export; consumed by export_data."""
    global _voice_export_fmt
    _voice_export_fmt = (fmt or "").strip().lower() or None


def _take_voice_export_format() -> str | None:
    global _voice_export_fmt
    fmt = _voice_export_fmt
    _voice_export_fmt = None
    return fmt


def _dot_matrix_print_available() -> bool:
    try:
        from core.printer_manager import PrinterManager
        return PrinterManager.is_dot_matrix_mode()
    except Exception:
        return False


def _export_format_options() -> list[tuple[str, str]]:
    opts: list[tuple[str, str]] = [
        ('CSV', 'csv'),
        ('Excel (.xlsx)', 'xlsx'),
        ('PDF', 'pdf'),
    ]
    if _dot_matrix_print_available():
        opts.append(('Dot Matrix Print (A4 Portrait)', 'dm_a4'))
    return opts


def _print_dot_matrix_report(parent, title, headers, rows) -> None:
    from core.dot_matrix_print import print_dot_matrix_report, DotMatrixPrintError
    try:
        print_dot_matrix_report(title, list(headers), list(rows), paper='A4')
        messagebox.showinfo("Printed", f'"{title}" sent to dot matrix printer.', parent=parent)
    except DotMatrixPrintError as exc:
        messagebox.showerror("Print Error", str(exc), parent=parent)
    except Exception as exc:
        messagebox.showerror("Print Error", str(exc), parent=parent)


def _print_dot_matrix_reports_combined(parent, sections) -> None:
    from core.dot_matrix_print import print_dot_matrix_reports_combined, DotMatrixPrintError
    try:
        print_dot_matrix_reports_combined(sections, paper='A4')
        messagebox.showinfo("Printed", "Report sent to dot matrix printer.", parent=parent)
    except DotMatrixPrintError as exc:
        messagebox.showerror("Print Error", str(exc), parent=parent)
    except Exception as exc:
        messagebox.showerror("Print Error", str(exc), parent=parent)


def _run_single_export(parent, title, headers, rows, default_name, fmt: str) -> None:
    top = parent.winfo_toplevel()
    if fmt == "csv":
        _save_csv(top, headers, rows, default_name)
    elif fmt == "xlsx":
        _save_xlsx(top, headers, rows, default_name)
    elif fmt == "dm_a4":
        _print_dot_matrix_report(top, title, headers, rows)
    else:
        _save_pdf(top, title, headers, rows, default_name)


def _run_all_export(parent, sections, fmt: str) -> None:
    top = parent.winfo_toplevel()
    if fmt == "csv":
        _save_all_csv(top, sections)
    elif fmt == "xlsx":
        _save_all_xlsx(top, sections)
    elif fmt == "dm_a4":
        _print_dot_matrix_reports_combined(top, sections)
    else:
        _save_all_pdf(top, sections)


# ── Format chooser dialog ─────────────────────────────────────────────────────

def export_data(parent, title, headers, rows, default_name='export'):
    """Show format picker then save. rows = list of tuples/lists."""
    from core.export_prefs import load_default_export_format

    voice_fmt = _take_voice_export_format()
    if voice_fmt:
        from core.voice.voice_dialog import speak_voice_dialog_hint
        speak_voice_dialog_hint("Choose save location.")
        _run_single_export(parent, title, headers, rows, default_name, voice_fmt)
        return

    from core.scroll_manager import DIALOG_SIZE_SMALL, dialog_root, open_dialog
    from core.dialog_keyboard import wire_export_format_dialog

    top = parent.winfo_toplevel()
    w, h = DIALOG_SIZE_SMALL
    dlg = open_dialog(top, f"Export — {title}", width=w, height=h + 40, resizable=True)
    body = dialog_root(dlg.content)

    ttk.Label(body, text=f"Export: {title}",
              font=('Segoe UI', 11, 'bold')).pack(pady=(12, 6), padx=12)
    ttk.Label(body, text="Choose format:").pack(padx=12)

    fmt_var = tk.StringVar(value=load_default_export_format())
    fmt_options = _export_format_options()
    btn_row = ttk.Frame(body)
    btn_row.pack(pady=10, padx=12)
    radios = []
    for text, val in fmt_options:
        rb = ttk.Radiobutton(btn_row, text=text, variable=fmt_var, value=val)
        rb.pack(side=tk.LEFT, padx=8)
        radios.append(rb)

    def _do_export():
        fmt = fmt_var.get()
        dlg.destroy()
        if fmt == 'csv':
            _save_csv(top, headers, rows, default_name)
        elif fmt == 'xlsx':
            _save_xlsx(top, headers, rows, default_name)
        elif fmt == 'dm_a4':
            _print_dot_matrix_report(top, title, headers, rows)
        else:
            _save_pdf(top, title, headers, rows, default_name)

    export_btn = ttk.Button(dlg.footer, text="Export", command=_do_export)
    export_btn.pack(side=tk.LEFT, padx=6)
    cancel_btn = ttk.Button(dlg.footer, text="Cancel", command=dlg.destroy)
    cancel_btn.pack(side=tk.LEFT, padx=6)
    wire_export_format_dialog(
        dlg, radios, fmt_var, [v for _, v in fmt_options], export_btn, cancel_btn,
    )
    try:
        from core.voice.voice_dialog import register_export_format_dialog
        register_export_format_dialog(
            dlg, fmt_var=fmt_var, on_export=_do_export, on_cancel=dlg.destroy,
        )
    except Exception:
        pass


# ── Export All Combined ───────────────────────────────────────────────────────

def export_all_combined(parent, sections):
    """One dialog, one file combining all sections.
    sections = list of (title, headers, rows)
    CSV: sections separated by blank line + title header.
    Excel: one sheet per section.
    HTML: one page with all sections.
    """
    from core.export_prefs import load_default_export_format

    voice_fmt = _take_voice_export_format()
    if voice_fmt:
        from core.voice.voice_dialog import speak_voice_dialog_hint
        speak_voice_dialog_hint("Choose save location.")
        _run_all_export(parent, sections, voice_fmt)
        return

    from core.scroll_manager import DIALOG_SIZE_SMALL, dialog_root, open_dialog
    from core.dialog_keyboard import wire_export_format_dialog

    top = parent.winfo_toplevel()
    w, h = DIALOG_SIZE_SMALL
    dlg = open_dialog(top, "Export All Data", width=w + 20, height=h + 60, resizable=True)
    body = dialog_root(dlg.content)

    ttk.Label(body, text="Export All Data",
              font=('Segoe UI', 12, 'bold')).pack(pady=(12, 4), padx=12)
    total = sum(len(r) for _, _, r in sections)
    ttk.Label(body, text=f"{len(sections)} sections  •  {total} total records",
              foreground='gray').pack(pady=(0, 8), padx=12)
    ttk.Label(body, text="Choose format:").pack(padx=12)

    fmt_var = tk.StringVar(value=load_default_export_format())
    fmt_options = _export_format_options()
    btn_row = ttk.Frame(body)
    btn_row.pack(pady=8, padx=12)
    radios = []
    for text, val in fmt_options:
        rb = ttk.Radiobutton(btn_row, text=text, variable=fmt_var, value=val)
        rb.pack(side=tk.LEFT, padx=8)
        radios.append(rb)

    def _do_export():
        fmt = fmt_var.get()
        dlg.destroy()
        if fmt == 'csv':
            _save_all_csv(top, sections)
        elif fmt == 'xlsx':
            _save_all_xlsx(top, sections)
        elif fmt == 'dm_a4':
            _print_dot_matrix_reports_combined(top, sections)
        else:
            _save_all_pdf(top, sections)

    export_btn = ttk.Button(dlg.footer, text="Export", command=_do_export)
    export_btn.pack(side=tk.LEFT, padx=6)
    cancel_btn = ttk.Button(dlg.footer, text="Cancel", command=dlg.destroy)
    cancel_btn.pack(side=tk.LEFT, padx=6)
    wire_export_format_dialog(
        dlg, radios, fmt_var, [v for _, v in fmt_options], export_btn, cancel_btn,
    )
    try:
        from core.voice.voice_dialog import register_export_format_dialog
        register_export_format_dialog(
            dlg, fmt_var=fmt_var, on_export=_do_export, on_cancel=dlg.destroy,
        )
    except Exception:
        pass


def _save_all_csv(parent, sections):
    path = filedialog.asksaveasfilename(
        parent=parent,
        defaultextension='.csv',
        filetypes=[('CSV files', '*.csv')],
        initialfile='export_all.csv')
    if not path:
        return
    try:
        with open(path, 'w', newline='', encoding='utf-8-sig') as f:
            w = csv.writer(f)
            for i, (title, headers, rows) in enumerate(sections):
                if i > 0:
                    w.writerow([])  # blank separator
                w.writerow([f'=== {title} ==='])
                w.writerow(headers)
                w.writerows(rows)
        messagebox.showinfo("Exported", f"Saved to:\n{path}", parent=parent)
    except Exception as e:
        messagebox.showerror("Export Error", str(e), parent=parent)


def _save_all_xlsx(parent, sections):
    try:
        import openpyxl
        from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    except ImportError:
        messagebox.showwarning(
            "openpyxl not installed",
            "openpyxl is required for Excel export.\n"
            "Install it with:  pip install openpyxl\n\n"
            "Saving as CSV instead.",
            parent=parent)
        _save_all_csv(parent, sections)
        return

    path = filedialog.asksaveasfilename(
        parent=parent,
        defaultextension='.xlsx',
        filetypes=[('Excel files', '*.xlsx')],
        initialfile='export_all.xlsx')
    if not path:
        return
    try:
        wb = openpyxl.Workbook()
        wb.remove(wb.active)  # remove default empty sheet

        hdr_fill = PatternFill('solid', fgColor='2C3E50')
        hdr_font = Font(color='FFFFFF', bold=True)
        thin = Side(style='thin', color='CCCCCC')
        border = Border(left=thin, right=thin, top=thin, bottom=thin)

        for title, headers, rows in sections:
            ws = wb.create_sheet(title=title[:31])
            for ci, h in enumerate(headers, 1):
                cell = ws.cell(row=1, column=ci, value=h)
                cell.fill = hdr_fill
                cell.font = hdr_font
                cell.alignment = Alignment(horizontal='center')
                cell.border = border
            for ri, row in enumerate(rows, 2):
                bg = 'F7F7F7' if ri % 2 == 0 else 'FFFFFF'
                fill = PatternFill('solid', fgColor=bg)
                for ci, val in enumerate(row, 1):
                    cell = ws.cell(row=ri, column=ci, value=val)
                    cell.fill = fill
                    cell.border = border
                    cell.alignment = Alignment(horizontal='left')
            for col in ws.columns:
                max_len = max((len(str(c.value or '')) for c in col), default=10)
                ws.column_dimensions[col[0].column_letter].width = min(max_len + 4, 40)

        wb.save(path)
        messagebox.showinfo("Exported", f"Saved to:\n{path}", parent=parent)
    except Exception as e:
        messagebox.showerror("Export Error", str(e), parent=parent)


def _save_all_pdf(parent, sections):
    try:
        from datetime import datetime
        date_str = datetime.now().strftime('%d/%m/%Y %H:%M')

        def esc(v):
            return str(v).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')

        body = ''
        for title, headers, rows in sections:
            hdr_html = ''.join(f'<th>{esc(h)}</th>' for h in headers)
            rows_html = ''.join(
                f'<tr class="{"even" if i%2==0 else "odd"}">'
                + ''.join(f'<td>{esc(v)}</td>' for v in row)
                + '</tr>'
                for i, row in enumerate(rows)
            )
            body += f"""
<h3>{esc(title)}</h3>
<p class="meta">{len(rows)} records</p>
<table><thead><tr>{hdr_html}</tr></thead><tbody>{rows_html}</tbody></table>
<div style="margin-bottom:12mm"></div>
"""

        html = f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>Export All</title>
<style>
  @page {{ size: A4 landscape; margin: 10mm; }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: 'Segoe UI', Arial, sans-serif; font-size: 8.5pt; color: #000; }}
  h2 {{ text-align:center; font-size:13pt; margin-bottom:2mm; }}
  h3 {{ font-size:11pt; margin:6mm 0 2mm; color:#2c3e50; border-bottom:1pt solid #2c3e50; padding-bottom:1mm; }}
  .meta {{ font-size:8pt; color:#555; margin-bottom:2mm; }}
  table {{ width:100%; border-collapse:collapse; font-size:8pt; margin-bottom:4mm; }}
  thead th {{ background:#2c3e50; color:#fff; padding:1.5mm 2mm; text-align:left; border:0.3pt solid #000; }}
  tbody td {{ padding:1.2mm 2mm; border:0.3pt solid #ccc; }}
  tr.even {{ background:#f7f7f7; }} tr.odd {{ background:#fff; }}
</style></head><body>
<h2>Full Data Export</h2>
<p class="meta" style="text-align:center">Generated: {date_str}</p>
{body}
</body></html>"""
        _write_export_pdf(parent, html, 'export_all')
    except Exception as e:
        messagebox.showerror("Export Error", str(e), parent=parent)


# ── CSV ───────────────────────────────────────────────────────────────────────

def _save_csv(parent, headers, rows, default_name):
    path = filedialog.asksaveasfilename(
        parent=parent,
        defaultextension='.csv',
        filetypes=[('CSV files', '*.csv')],
        initialfile=f'{default_name}.csv')
    if not path:
        return
    try:
        with open(path, 'w', newline='', encoding='utf-8-sig') as f:
            w = csv.writer(f)
            w.writerow(headers)
            w.writerows(rows)
        messagebox.showinfo("Exported", f"Saved to:\n{path}", parent=parent)
    except Exception as e:
        messagebox.showerror("Export Error", str(e), parent=parent)


# ── Excel ─────────────────────────────────────────────────────────────────────

def _save_xlsx(parent, headers, rows, default_name):
    try:
        import openpyxl
        from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    except ImportError:
        # Fallback: save as CSV with .xlsx extension hint
        messagebox.showwarning(
            "openpyxl not installed",
            "openpyxl is required for Excel export.\n"
            "Install it with:  pip install openpyxl\n\n"
            "Saving as CSV instead.",
            parent=parent)
        _save_csv(parent, headers, rows, default_name)
        return

    path = filedialog.asksaveasfilename(
        parent=parent,
        defaultextension='.xlsx',
        filetypes=[('Excel files', '*.xlsx')],
        initialfile=f'{default_name}.xlsx')
    if not path:
        return
    try:
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = default_name[:31]

        # Header style
        hdr_fill = PatternFill('solid', fgColor='2C3E50')
        hdr_font = Font(color='FFFFFF', bold=True)
        thin = Side(style='thin', color='CCCCCC')
        border = Border(left=thin, right=thin, top=thin, bottom=thin)

        for ci, h in enumerate(headers, 1):
            cell = ws.cell(row=1, column=ci, value=h)
            cell.fill = hdr_fill
            cell.font = hdr_font
            cell.alignment = Alignment(horizontal='center')
            cell.border = border

        # Data rows
        for ri, row in enumerate(rows, 2):
            bg = 'F7F7F7' if ri % 2 == 0 else 'FFFFFF'
            fill = PatternFill('solid', fgColor=bg)
            for ci, val in enumerate(row, 1):
                cell = ws.cell(row=ri, column=ci, value=val)
                cell.fill = fill
                cell.border = border
                cell.alignment = Alignment(horizontal='left')

        # Auto column width
        for col in ws.columns:
            max_len = max((len(str(c.value or '')) for c in col), default=10)
            ws.column_dimensions[col[0].column_letter].width = min(max_len + 4, 40)

        wb.save(path)
        messagebox.showinfo("Exported", f"Saved to:\n{path}", parent=parent)
    except Exception as e:
        messagebox.showerror("Export Error", str(e), parent=parent)


# ── PDF (HTML) ────────────────────────────────────────────────────────────────

def _offer_export_open(parent, saved_path: str) -> None:
    try:
        from core.document_output import offer_open_saved_file
        offer_open_saved_file(parent, saved_path, title="Exported")
    except Exception:
        messagebox.showinfo("Exported", f"Saved to:\n{saved_path}", parent=parent)


def _write_export_pdf(parent, html: str, default_name: str) -> None:
    path = filedialog.asksaveasfilename(
        parent=parent,
        defaultextension='.pdf',
        filetypes=[('PDF files', '*.pdf'), ('HTML files', '*.html')],
        initialfile=f'{default_name}.pdf')
    if not path:
        return
    if path.lower().endswith('.html'):
        with open(path, 'w', encoding='utf-8') as f:
            f.write(html)
        _offer_export_open(parent, path)
        return
    from core.document_output import save_html_as_pdf
    pdf_path, html_path = save_html_as_pdf(html, path)
    _offer_export_open(parent, pdf_path or html_path)


def _save_pdf(parent, title, headers, rows, default_name):
    try:
        html = _build_html(title, headers, rows)
        _write_export_pdf(parent, html, default_name)
    except Exception as e:
        messagebox.showerror("Export Error", str(e), parent=parent)


def _build_html(title, headers, rows):
    from datetime import datetime
    date_str = datetime.now().strftime('%d/%m/%Y %H:%M')

    def esc(v):
        return str(v).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')

    header_html = ''.join(f'<th>{esc(h)}</th>' for h in headers)
    rows_html = ''
    for i, row in enumerate(rows):
        cls = 'even' if i % 2 == 0 else 'odd'
        cells = ''.join(f'<td>{esc(v)}</td>' for v in row)
        rows_html += f'<tr class="{cls}">{cells}</tr>\n'

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<title>{esc(title)}</title>
<style>
  @page {{ size: A4 landscape; margin: 10mm; }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: 'Segoe UI', Arial, sans-serif; font-size: 9pt; color: #000; }}
  h2 {{ text-align: center; font-size: 13pt; margin-bottom: 2mm; }}
  .meta {{ text-align: center; font-size: 8pt; color: #555; margin-bottom: 4mm; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 8.5pt; }}
  thead th {{ background: #2c3e50; color: #fff; padding: 2mm 2mm;
              text-align: left; border: 0.3pt solid #000; font-size: 8.5pt; }}
  tbody td {{ padding: 1.5mm 2mm; border: 0.3pt solid #ccc; vertical-align: middle; }}
  tr.even {{ background: #f7f7f7; }}
  tr.odd  {{ background: #ffffff; }}
  tfoot td {{ padding: 2mm; border-top: 1pt solid #000; font-weight: bold; }}
  .print-btn {{ display: block; margin: 5mm auto; padding: 2mm 10mm;
                font-size: 11pt; background: #2c3e50; color: white;
                border: none; border-radius: 3px; cursor: pointer; }}
  @media print {{ .print-btn {{ display: none; }} }}
</style>
</head>
<body>
<h2>{esc(title)}</h2>
<div class="meta">Generated: {date_str} &nbsp;|&nbsp; Total Records: {len(rows)}</div>
<table>
  <thead><tr>{header_html}</tr></thead>
  <tbody>{rows_html}</tbody>
</table>
<button class="print-btn" onclick="window.print()">&#128424; Print / Save as PDF</button>
</body>
</html>"""


# ── Voice / auto-save to Downloads (no file picker) ───────────────────────────

def downloads_folder() -> str:
    home = os.path.expanduser("~")
    path = os.path.join(home, "Downloads")
    os.makedirs(path, exist_ok=True)
    return path


def _voice_export_path(default_name: str, fmt: str) -> str:
    from datetime import datetime
    safe = re.sub(r'[^\w\-]+', '_', (default_name or "export").strip())[:60]
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    ext = fmt if fmt == "pdf" else fmt
    if fmt == "pdf":
        ext = "pdf"
    return os.path.join(downloads_folder(), f"{safe}_{ts}.{ext}")


def export_data_direct(parent, title, headers, rows, default_name="export", fmt="csv", *, speak_path=True):
    """Save export straight to Downloads — used by Satpuda voice."""
    fmt = (fmt or "csv").lower()
    path = _voice_export_path(default_name, fmt)
    try:
        if fmt == "csv":
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                w = csv.writer(f)
                w.writerow(headers)
                w.writerows(rows)
        elif fmt == "xlsx":
            import openpyxl
            wb = openpyxl.Workbook()
            ws = wb.active
            ws.title = (title or "Export")[:31]
            for ci, h in enumerate(headers, 1):
                ws.cell(row=1, column=ci, value=h)
            for ri, row in enumerate(rows, 2):
                for ci, val in enumerate(row, 1):
                    ws.cell(row=ri, column=ci, value=val)
            wb.save(path)
        else:
            saved = _save_pdf_to_path(path, title, headers, rows)
        if speak_path:
            return saved, f"Saved to Downloads folder. File name {os.path.basename(saved)}."
        return saved, f"Saved to:\n{saved}"
    except Exception as e:
        return None, f"Export failed. {e}"


def export_all_combined_direct(parent, sections, fmt="xlsx"):
    """Combined export to Downloads for voice."""
    fmt = (fmt or "xlsx").lower()
    path = _voice_export_path("export_all", fmt)
    try:
        if fmt == "csv":
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                w = csv.writer(f)
                for i, (title, headers, rows) in enumerate(sections):
                    if i > 0:
                        w.writerow([])
                    w.writerow([f"=== {title} ==="])
                    w.writerow(headers)
                    w.writerows(rows)
        elif fmt == "xlsx":
            import openpyxl
            wb = openpyxl.Workbook()
            wb.remove(wb.active)
            for title, headers, rows in sections:
                ws = wb.create_sheet(title=title[:31])
                for ci, h in enumerate(headers, 1):
                    ws.cell(row=1, column=ci, value=h)
                for ri, row in enumerate(rows, 2):
                    for ci, val in enumerate(row, 1):
                        ws.cell(row=ri, column=ci, value=val)
            wb.save(path)
        else:
            saved = _save_all_pdf_to_path(path, sections)
        return saved, f"Saved to Downloads folder. File name {os.path.basename(saved)}."
    except Exception as e:
        return None, f"Export failed. {e}"


def _save_pdf_to_path(path, title, headers, rows):
    from datetime import datetime
    date_str = datetime.now().strftime("%d/%m/%Y %H:%M")

    def esc(v):
        return str(v).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    hdr_html = "".join(f"<th>{esc(h)}</th>" for h in headers)
    rows_html = "".join(
        f'<tr class="{"even" if i % 2 == 0 else "odd"}">'
        + "".join(f"<td>{esc(v)}</td>" for v in row)
        + "</tr>"
        for i, row in enumerate(rows)
    )
    html = f"""<!DOCTYPE html><html><head><meta charset="UTF-8"><title>{esc(title)}</title>
<style>@page{{size:A4 landscape;margin:10mm}}body{{font-family:Segoe UI,Arial;font-size:9pt}}
table{{width:100%;border-collapse:collapse}}th{{background:#2c3e50;color:#fff;padding:4px}}
td{{border:1px solid #ccc;padding:3px}}tr.even{{background:#f7f7f7}}</style></head><body>
<h2>{esc(title)}</h2><p>{date_str} — {len(rows)} records</p>
<table><thead><tr>{hdr_html}</tr></thead><tbody>{rows_html}</tbody></table>
</body></html>"""
    if not path.lower().endswith(".pdf"):
        path = path.rsplit(".", 1)[0] + ".pdf"
    from core.document_output import save_html_as_pdf
    pdf_path, html_path = save_html_as_pdf(html, path)
    return pdf_path or html_path


def _save_all_pdf_to_path(path, sections):
    from datetime import datetime
    date_str = datetime.now().strftime("%d/%m/%Y %H:%M")

    def esc(v):
        return str(v).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    body = ""
    for title, headers, rows in sections:
        hdr = "".join(f"<th>{esc(h)}</th>" for h in headers)
        rows_html = "".join(
            "<tr>" + "".join(f"<td>{esc(v)}</td>" for v in row) + "</tr>" for row in rows
        )
        body += f"<h3>{esc(title)}</h3><table><thead><tr>{hdr}</tr></thead><tbody>{rows_html}</tbody></table>"
    html = (
        f"<!DOCTYPE html><html><head><meta charset=UTF-8><title>Export All</title></head><body>"
        f"<h2>Full Export</h2><p>{date_str}</p>{body}</body></html>"
    )
    if not path.lower().endswith(".pdf"):
        path = path.rsplit(".", 1)[0] + ".pdf"
    from core.document_output import save_html_as_pdf
    pdf_path, html_path = save_html_as_pdf(html, path)
    return pdf_path or html_path
