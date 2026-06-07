"""Save bill HTML/PDF and open browser for printing — no preview dialog."""
from __future__ import annotations

import os
import subprocess
import webbrowser

from core.bill_config import load_bill_print_settings, render_bill_html
from widgets.bill_preview import _build_bill_context, _write_html_file


def _bills_directory() -> str:
    try:
        from core.store_manager import get_active_store_path
        base = get_active_store_path()
    except Exception:
        base = os.path.join(
            os.environ.get('LOCALAPPDATA', os.path.expanduser('~')),
            'VeterinaryApp',
        )
    path = os.path.join(base, 'bills')
    os.makedirs(path, exist_ok=True)
    return path


def _load_sale_data(conn, sale_id):
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, name, address, phone, email, gstin, dl_number,
               gst_enabled, created_at, COALESCE(logo_path, '') as logo_path,
               COALESCE(fssai_number, '') as fssai_number,
               COALESCE(show_fssai_on_bill, 0) as show_fssai_on_bill
        FROM pharmacy_profile LIMIT 1
    """)
    profile = cursor.fetchone()

    cursor.execute("""
        SELECT s.bill_no, s.bill_date, c.name, c.phone, c.address,
               s.total_amount, s.discount, s.amount_paid, s.previous_due,
               COALESCE(s.due_amount, 0), COALESCE(s.credit_amount, 0),
               COALESCE(s.cash_paid, 0), COALESCE(s.online_paid, 0),
               COALESCE(s.rounding, 0), COALESCE(s.doctor_name, '')
        FROM sales s
        JOIN customers c ON s.customer_id = c.id
        WHERE s.id = ?
    """, (sale_id,))
    bill_info = cursor.fetchone()
    if not bill_info:
        raise ValueError(f'Sale id {sale_id} not found')

    cursor.execute("""
        SELECT m.name, m.hsn_code, m.batch_no, m.manufacturer,
               m.expiry_date, si.qty, si.rate, si.amount,
               COALESCE(m.gst_percent, 0), COALESCE(m.mrp, si.rate, 0)
        FROM sales_items si
        JOIN medicines m ON si.medicine_id = m.id
        WHERE si.sale_id = ?
    """, (sale_id,))
    items = cursor.fetchall()

    cash = float(bill_info[11] or 0)
    online = float(bill_info[12] or 0)
    if cash == 0 and online == 0:
        pay_mode = 'Due'
    elif cash > 0 and online == 0:
        pay_mode = 'Cash'
    elif cash == 0 and online > 0:
        pay_mode = 'Online'
    else:
        pay_mode = 'Cash + Online'

    ctx = _build_bill_context(profile, bill_info, items, pay_mode, cursor)
    settings = load_bill_print_settings()
    return bill_info[0], ctx, settings


def _inject_auto_print(html: str, paper_size: str = 'A5') -> str:
    paper = (paper_size or 'A5').upper()
    hint = 'A5 Landscape' if paper == 'A5' else 'A4 Landscape'
    script = f"""
<script>
document.documentElement.classList.add('preview-mode');
window.addEventListener('load', function() {{
  setTimeout(function() {{ window.print(); }}, 700);
}});
</script>
<meta name="bill-paper-hint" content="{hint}">
"""
    if '</body>' in html:
        return html.replace('</body>', script + '</body>')
    return html + script


def _try_pdf_via_edge(html_path: str, pdf_path: str) -> bool:
    candidates = [
        os.path.join(os.environ.get('PROGRAMFILES', ''), 'Microsoft', 'Edge', 'Application', 'msedge.exe'),
        os.path.join(os.environ.get('PROGRAMFILES(X86)', ''), 'Microsoft', 'Edge', 'Application', 'msedge.exe'),
        os.path.join(os.environ.get('PROGRAMFILES', ''), 'Google', 'Chrome', 'Application', 'chrome.exe'),
        os.path.join(os.environ.get('PROGRAMFILES(X86)', ''), 'Google', 'Chrome', 'Application', 'chrome.exe'),
    ]
    uri = 'file:///' + html_path.replace('\\', '/')
    for exe in candidates:
        if not exe or not os.path.isfile(exe):
            continue
        try:
            subprocess.run(
                [exe, '--headless', '--disable-gpu', '--no-pdf-header-footer',
                 f'--print-to-pdf={pdf_path}', uri],
                timeout=45, capture_output=True, check=False,
            )
            if os.path.isfile(pdf_path) and os.path.getsize(pdf_path) > 500:
                return True
        except Exception:
            continue
    return False


def open_bill_for_print(conn, bill_no, sale_id, *, auto_print: bool = True, save_pdf: bool = True):
    """
    Save bill HTML (and PDF when possible), open in browser with A5 print CSS.
    No Tk preview dialog.
    Returns (html_path, pdf_path or None).
    """
    bill_no, ctx, settings = _load_sale_data(conn, sale_id)
    html = render_bill_html(ctx, settings)
    if auto_print:
        html = _inject_auto_print(html, settings.get('paper_size', 'A5'))

    bills_dir = _bills_directory()
    safe_no = ''.join(c if c.isalnum() or c in '-_' else '_' for c in str(bill_no))
    html_path = os.path.join(bills_dir, f'Bill_{safe_no}.html')
    with open(html_path, 'w', encoding='utf-8') as f:
        f.write(html)

    pdf_path = os.path.join(bills_dir, f'Bill_{safe_no}.pdf') if save_pdf else None
    pdf_saved = None
    if save_pdf and pdf_path:
        if _try_pdf_via_edge(html_path, pdf_path):
            pdf_saved = pdf_path

    webbrowser.open('file:///' + html_path.replace('\\', '/'))
    return html_path, pdf_saved
