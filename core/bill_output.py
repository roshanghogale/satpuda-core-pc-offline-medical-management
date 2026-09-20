"""Save bill HTML/PDF to the bill folder; silent or dialog printing on Windows."""
from __future__ import annotations

import os
import subprocess
import time
import sys
import webbrowser

from core.bill_config import (
    apply_print_bill_layout,
    get_page_copies,
    get_print_slot_page_copies,
    get_print_slot_settings,
    load_bill_print_settings,
    render_bill_html,
)
# widgets.bill_preview imports tkinter at module scope, which the headless
# engine does not ship. _build_bill_context itself is pure data, so pull it in
# only when a bill is actually rendered.


def default_downloads_directory() -> str:
    """User Downloads folder (Win7–11)."""
    return _downloads_directory()


def _downloads_directory() -> str:
    """User Downloads folder (Win7–11)."""
    candidates = []
    if sys.platform == 'win32':
        userprofile = os.environ.get('USERPROFILE', '')
        if userprofile:
            candidates.append(os.path.join(userprofile, 'Downloads'))
    home = os.path.expanduser('~')
    candidates.extend([
        os.path.join(home, 'Downloads'),
        os.path.join(home, 'download'),
    ])
    for path in candidates:
        if path and os.path.isdir(path):
            return path
    fallback = os.path.join(home, 'Downloads')
    os.makedirs(fallback, exist_ok=True)
    return fallback


def _bills_directory() -> str:
    """Legacy store bills folder (kept for compatibility)."""
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


def _safe_bill_stem(bill_no) -> str:
    return 'Bill_' + ''.join(
        c if c.isalnum() or c in '-_' else '_' for c in str(bill_no)
    )


def resolve_bill_db_path(conn, db_path: str | None = None) -> str:
    """Absolute store DB path for bill PDF/print (prefer explicit path)."""
    if db_path:
        return os.path.abspath(db_path)
    if conn is not None:
        try:
            from core.background_workers import db_path_from_conn
            path = (db_path_from_conn(conn) or "").strip()
            if path:
                return os.path.abspath(path)
        except Exception:
            pass
    try:
        from core.store_manager import get_active_db_path
        path = (get_active_db_path() or "").strip()
        if path:
            return os.path.abspath(path)
    except Exception:
        pass
    return ""


def _load_sale_data(
    conn,
    sale_id,
    settings_override: dict | None = None,
    *,
    print_slot: int | None = None,
):
    cursor = conn.cursor()
    try:
        sale_id = int(sale_id)
    except (TypeError, ValueError):
        raise ValueError(f'Sale id {sale_id} not found')

    try:
        from core.sync_prefs import is_online_mode

        if is_online_mode():
            local = cursor.execute(
                "SELECT id FROM sales WHERE id=?", (sale_id,)
            ).fetchone()
            if not local:
                return _load_sale_data_online(
                    conn, sale_id, settings_override, print_slot=print_slot
                )
    except Exception as exc:
        print(f"[bill_output] online check: {exc}")

    from core.pharmacy_profile_io import fetch_pharmacy_profile_row

    profile = fetch_pharmacy_profile_row(conn)

    cursor.execute("""
        SELECT s.bill_no, s.bill_date,
               COALESCE(c.name, ''), COALESCE(c.phone, ''), COALESCE(c.address, ''),
               s.total_amount, s.discount, s.amount_paid, s.previous_due,
               COALESCE(s.due_amount, 0), COALESCE(s.credit_amount, 0),
               COALESCE(s.cash_paid, 0), COALESCE(s.online_paid, 0),
               COALESCE(s.rounding, 0), COALESCE(s.doctor_name, ''),
               COALESCE(s.total_due, 0), COALESCE(s.previous_credit, 0)
        FROM sales s
        LEFT JOIN customers c ON s.customer_id = c.id
        WHERE s.id = ?
    """, (sale_id,))
    bill_info = cursor.fetchone()
    if not bill_info:
        online_err = None
        try:
            from core.sync_prefs import is_online_mode

            if is_online_mode():
                return _load_sale_data_online(
                    conn, sale_id, settings_override, print_slot=print_slot
                )
        except Exception as exc:
            # Do NOT discard this. In Online mode the server IS the source of the
            # bill, so its failure is the real reason printing did not work --
            # swallowing it left the counter staring at a bare "Sale id N not
            # found" while the actual cause (no token, server down, deleted row)
            # was thrown away.
            online_err = exc
            print(f"[bill_output] online sale load failed: {type(exc).__name__}: {exc}", flush=True)
        if online_err is not None:
            raise online_err
        cursor.execute("SELECT id FROM sales WHERE id=?", (sale_id,))
        if cursor.fetchone():
            raise ValueError(
                f'Sale id {sale_id} exists but customer link is missing — reopen Sales History and print again.'
            )
        raise ValueError(f'Sale id {sale_id} not found')

    # GST % is the rate the line was SOLD at (sales_items.gst_percent), not the
    # medicine's rate today: once a rate changed, every reprint printed tax the
    # customer was never charged. Only a line that never stored a rate (NULL) falls
    # back to the medicine. HSN is not kept per sale line and is not printed.
    cursor.execute("""
        SELECT m.name, m.hsn_code, m.batch_no, m.manufacturer,
               m.expiry_date, si.qty, si.rate, si.amount,
               COALESCE(si.gst_percent, m.gst_percent, 0), COALESCE(m.mrp, si.rate, 0),
               COALESCE(m.type, ''), COALESCE(m.unit, '')
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

    from core.bill_context import _build_bill_context

    ctx = _build_bill_context(profile, bill_info, items, pay_mode, cursor)
    if print_slot:
        settings = get_print_slot_settings(load_bill_print_settings(), print_slot)
    else:
        settings = dict(load_bill_print_settings())
    if settings_override:
        settings.update(settings_override)
    from core.fy_serial import display_sales_bill_no

    return display_sales_bill_no(bill_info[0]), ctx, settings


def _load_sale_data_online(
    conn,
    sale_id,
    settings_override: dict | None = None,
    *,
    print_slot: int | None = None,
):
    """Build print context from server sale doc when local SQLite row is missing."""
    from core.server_crud import get_doc
    from core.online_catalog import find_customer_by_id, medicine_by_id
    from core.pharmacy_profile_io import fetch_pharmacy_profile_row, load_pharmacy_profile
    from core.fy_serial import display_sales_bill_no

    doc = get_doc("sales", int(sale_id)) or {}
    if not doc or doc.get("deleted"):
        # Distinguish "the server does not have this bill" from "we could not ask
        # the server". Both used to print the same unhelpful line.
        detail = ""
        try:
            from core import server_api as api

            tok = (api.store_token_for_active() or "").strip()
            if not tok:
                detail = " (this device is not signed in to the server)"
            else:
                api.health()
        except Exception as exc:
            detail = f" (server unreachable: {type(exc).__name__})"
        if doc.get("deleted"):
            detail = " (the bill was deleted)"
        raise ValueError(f'Sale id {sale_id} not found on the server{detail}')

    customer_id = int(doc.get("customer_id") or 0)
    cust = find_customer_by_id(customer_id) or {}
    if not cust and customer_id:
        cust = get_doc("customers", customer_id) or {}

    profile = fetch_pharmacy_profile_row(conn)
    if not profile:
        p = load_pharmacy_profile(conn) or {}
        profile = (
            1,
            p.get("name") or "",
            p.get("address") or "",
            p.get("phone") or "",
            p.get("email") or "",
            p.get("gstin") or "",
            p.get("dl_number") or "",
            1 if p.get("gst_enabled", True) else 0,
            None,
            p.get("logo_path") or "",
            p.get("fssai_number") or "",
            1 if p.get("show_fssai_on_bill") else 0,
        )

    bill_info = (
        doc.get("bill_no") or "",
        doc.get("bill_date") or "",
        cust.get("name") or doc.get("customer_name") or "",
        cust.get("phone") or doc.get("customer_phone") or "",
        cust.get("address") or doc.get("customer_address") or "",
        float(doc.get("total_amount") or 0),
        float(doc.get("discount") or 0),
        float(doc.get("amount_paid") or 0),
        float(doc.get("previous_due") or 0),
        float(doc.get("due_amount") or 0),
        float(doc.get("credit_amount") or 0),
        float(doc.get("cash_paid") or 0),
        float(doc.get("online_paid") or 0),
        float(doc.get("rounding") or 0),
        doc.get("doctor_name") or "",
        float(doc.get("total_due") or 0),
        float(doc.get("previous_credit") or 0),
    )

    items = []
    for it in doc.get("items") or []:
        if not isinstance(it, dict):
            continue
        mid = int(it.get("medicine_id") or 0)
        mp = medicine_by_id(mid) if mid else {}
        mp = mp or {}
        # The rate the line was sold at, even 0%: `or` sent a stored 0 to the
        # medicine's rate today, so an exempt line reprinted with tax.
        sold_gst = it.get("gst_percent")
        if sold_gst in (None, ""):
            sold_gst = mp.get("gst_percent")
        items.append(
            (
                it.get("medicine_name") or it.get("name") or mp.get("name") or "",
                it.get("hsn_code") or mp.get("hsn_code") or "",
                it.get("batch_no") or it.get("batch") or mp.get("batch_no") or "",
                it.get("manufacturer") or mp.get("manufacturer") or "",
                it.get("expiry_date") or it.get("expiry") or mp.get("expiry_date") or "",
                float(it.get("qty") or 0),
                float(it.get("rate") or 0),
                float(it.get("amount") or 0),
                float(sold_gst or 0),
                float(it.get("mrp") or mp.get("mrp") or it.get("rate") or 0),
                str(it.get("type") or mp.get("type") or ""),
                str(it.get("unit") or mp.get("unit") or ""),
            )
        )

    cash = float(bill_info[11] or 0)
    online = float(bill_info[12] or 0)
    if cash == 0 and online == 0:
        pay_mode = "Due"
    elif cash > 0 and online == 0:
        pay_mode = "Cash"
    elif cash == 0 and online > 0:
        pay_mode = "Online"
    else:
        pay_mode = "Cash + Online"

    cursor = conn.cursor()
    from core.bill_context import _build_bill_context
    ctx = _build_bill_context(profile, bill_info, items, pay_mode, cursor)
    if print_slot:
        settings = get_print_slot_settings(load_bill_print_settings(), print_slot)
    else:
        settings = dict(load_bill_print_settings())
    if settings_override:
        settings.update(settings_override)
    return display_sales_bill_no(bill_info[0]), ctx, settings


def _items_per_bill_page(settings: dict, *, dot_matrix: bool = False) -> int:
    from core.bill_config import get_items_per_bill_page
    return get_items_per_bill_page(settings, dot_matrix=dot_matrix)


def _logical_print_paper(settings: dict, html_settings: dict | None = None) -> str:
    """Original paper (A4/A5/A6) before A6 is remapped to half-A5 for PDF."""
    hs = html_settings or {}
    hint = str(hs.get("print_paper_hint") or "").strip().upper()
    if hint in ("A4", "A5", "A6"):
        return hint
    for src in (settings, hs):
        paper = str(src.get("paper_size") or "").strip().upper()
        if paper in ("A4", "A5", "A6"):
            return paper
    return "A5"


def _chunk_bill_items(items, per_page: int):
    items = list(items or [])
    if not items:
        return [items]
    return [items[i:i + per_page] for i in range(0, len(items), per_page)]


def _carry_forward_contexts(ctx, per_items: int):
    """Split one sale into carry-forward pages; non-final pages mark Continued..."""
    from dataclasses import replace

    chunks = _chunk_bill_items(ctx.items, per_items)
    if len(chunks) <= 1:
        return [ctx]
    out = []
    last = len(chunks) - 1
    for i, chunk in enumerate(chunks):
        page_sub = round(sum(float(getattr(it, "amount", 0) or 0) for it in chunk), 2)
        out.append(replace(
            ctx,
            items=chunk,
            item_sr_offset=i * per_items,
            is_continued=(i < last),
            page_subtotal=page_sub,
            page_index=i,
            page_count=len(chunks),
            # UPI / full dues only on the final page
            show_upi_qr=bool(ctx.show_upi_qr) and i == last,
        ))
    return out


def _render_bill_files(bill_no, ctx, html_settings, save_dir: str, *, page_index: int = 0, page_count: int = 1):
    """Write one HTML (+ PDF) for a bill context. Multi-page stems get _p2, _p3, …"""
    html = render_bill_html(ctx, html_settings)
    stem = _safe_bill_stem(bill_no)
    if page_count > 1:
        stem = f"{stem}_p{page_index + 1}"
    html_path = os.path.join(save_dir, f"{stem}.html")
    pdf_path = os.path.join(save_dir, f"{stem}.pdf")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)
    paper = None
    try:
        from core.bill_page_config import pdf_paper_mm_from_settings
        paper = pdf_paper_mm_from_settings(html_settings)
    except Exception:
        paper = None
    if paper:
        w_mm, h_mm = paper
        pdf_saved = pdf_path if _try_pdf_via_browser(
            html_path, pdf_path, paper_width_mm=w_mm, paper_height_mm=h_mm,
        ) else None
    else:
        pdf_saved = pdf_path if _try_pdf_via_browser(html_path, pdf_path) else None
    return html_path, pdf_saved


def _open_bill_pdf_conn(conn, path: str):
    """Open a dedicated store connection for bill render; fall back to shared conn."""
    try:
        from core.sync_prefs import is_online_mode
        from core.pharmacy_profile_io import ensure_pharmacy_profile_table

        if is_online_mode():
            # Online print must use the live schema shell, not a wiped store file.
            ensure_pharmacy_profile_table(conn)
            return conn, False
    except Exception:
        pass
    if path:
        try:
            from core.db_utils import open_store_db
            pdf_conn = open_store_db(path, timeout=30.0)
            from core.pharmacy_profile_io import ensure_pharmacy_profile_table

            ensure_pharmacy_profile_table(pdf_conn)
            return pdf_conn, True
        except Exception:
            pass
        try:
            import sqlite3
            pdf_conn = sqlite3.connect(path, check_same_thread=False, timeout=30)
            pdf_conn.execute("PRAGMA busy_timeout=30000")
            from core.pharmacy_profile_io import ensure_pharmacy_profile_table

            ensure_pharmacy_profile_table(pdf_conn)
            return pdf_conn, True
        except Exception:
            pass
    try:
        from core.pharmacy_profile_io import ensure_pharmacy_profile_table

        ensure_pharmacy_profile_table(conn)
    except Exception:
        pass
    return conn, False


def _load_sale_data_for_pdf(
    conn,
    sale_id,
    settings_override: dict | None = None,
    *,
    print_slot: int | None = None,
    db_path: str | None = None,
):
    """
    Load sale for PDF/print using a dedicated DB connection.

    Captures the store path up front (or uses db_path from the UI thread) and
    retries briefly so a just-committed WAL write is visible to the new connection.
    """
    import time

    primary = resolve_bill_db_path(conn, db_path)
    candidates: list[str] = []
    for path in (primary, resolve_bill_db_path(conn, None)):
        if path and path not in candidates:
            candidates.append(path)
    if not candidates:
        candidates.append("")

    last_err: Exception | None = None
    for path in candidates:
        for attempt in range(8):
            pdf_conn, own = _open_bill_pdf_conn(conn, path)
            try:
                return _load_sale_data(
                    pdf_conn, sale_id, settings_override, print_slot=print_slot,
                )
            except ValueError as exc:
                last_err = exc
                msg = str(exc).lower()
                if "not found" not in msg:
                    raise
                time.sleep(0.04 * (attempt + 1))
            finally:
                if own:
                    try:
                        pdf_conn.close()
                    except Exception:
                        pass
    if last_err:
        raise last_err
    raise ValueError(f'Sale id {sale_id} not found')


def save_bill_pdf_pages(
    conn,
    sale_id,
    settings_override: dict | None = None,
    *,
    print_slot: int | None = None,
    db_path: str | None = None,
) -> list[tuple[str, str | None]]:
    """
    Save bill HTML/PDF using Print Sales slot presets (Pharmacy Profile).

    print_slot 1 = Print Sales 1, 2 = Print Sales 2.
    F5 / Save Bill defaults to Print Sales 1 when no slot is passed.
    """
    effective_slot = 1 if print_slot is None else int(print_slot)
    bill_no, ctx, settings = _load_sale_data_for_pdf(
        conn, sale_id, settings_override, print_slot=effective_slot, db_path=db_path,
    )

    from core.bill_save_prefs import resolve_sales_bill_save_dir

    slot_cfg = (load_bill_print_settings().get(f"print_slot_{effective_slot}") or {})
    if isinstance(slot_cfg, dict) and slot_cfg.get("copies") is not None:
        slot_copies = int(slot_cfg.get("copies") or 1)
    else:
        slot_copies = int(settings.get("bill_copies") or 1)
    html_settings = apply_print_bill_layout(settings, print_slot_copies=slot_copies)

    save_dir = resolve_sales_bill_save_dir()

    per_items = _items_per_bill_page(html_settings)
    contexts = _carry_forward_contexts(ctx, per_items)

    if len(contexts) <= 1:
        return [_render_bill_files(bill_no, ctx, html_settings, save_dir)]

    # One physical sheet per 12-medicine page; Bill copies duplicates that page only.
    pages = []
    for i, page_ctx in enumerate(contexts):
        pages.append(
            _render_bill_files(
                bill_no, page_ctx, html_settings, save_dir,
                page_index=i, page_count=len(contexts),
            )
        )
    return pages


def save_bill_pdf_only(
    conn,
    sale_id,
    settings_override: dict | None = None,
    *,
    print_slot: int | None = None,
    db_path: str | None = None,
) -> tuple[str, str | None]:
    """Save bill HTML + PDF. Multi-sheet bills return the first sheet paths."""
    pages = save_bill_pdf_pages(
        conn, sale_id, settings_override, print_slot=print_slot, db_path=db_path,
    )
    return pages[0] if pages else ("", None)


def _chromium_browser_paths():
    import shutil
    paths = []
    seen = set()

    def _add(path):
        if not path:
            return
        norm = os.path.normcase(os.path.abspath(path))
        if norm in seen or not os.path.isfile(path):
            return
        seen.add(norm)
        paths.append(path)

    for exe in (
        'msedge', 'chrome', 'chromium', 'brave', 'opera', 'vivaldi',
        'google-chrome', 'microsoft-edge',
    ):
        _add(shutil.which(exe))

    # macOS keeps browsers inside .app bundles, which shutil.which() never finds
    # and the Windows PROGRAMFILES scan below cannot see. Without these the Mac
    # build reported "Install Microsoft Edge (or Google Chrome)" on every bill
    # while Chrome was sitting in /Applications.
    if sys.platform == 'darwin':
        for mac_rel in (
            'Google Chrome.app/Contents/MacOS/Google Chrome',
            'Microsoft Edge.app/Contents/MacOS/Microsoft Edge',
            'Chromium.app/Contents/MacOS/Chromium',
            'Brave Browser.app/Contents/MacOS/Brave Browser',
            'Vivaldi.app/Contents/MacOS/Vivaldi',
        ):
            _add(os.path.join('/Applications', mac_rel))
            _add(os.path.join(os.path.expanduser('~/Applications'), mac_rel))

    program_roots = []
    for key in ('PROGRAMFILES', 'PROGRAMFILES(X86)', 'LOCALAPPDATA'):
        root = os.environ.get(key, '')
        if root:
            program_roots.append(root)

    rel_paths = [
        ('Microsoft', 'Edge', 'Application', 'msedge.exe'),
        ('Google', 'Chrome', 'Application', 'chrome.exe'),
        ('BraveSoftware', 'Brave-Browser', 'Application', 'brave.exe'),
        ('Opera Software', 'Opera', 'launcher.exe'),
        ('Opera Software', 'Opera', 'opera.exe'),
        ('Vivaldi', 'Application', 'vivaldi.exe'),
        ('Chromium', 'Application', 'chrome.exe'),
    ]
    for root in program_roots:
        for parts in rel_paths:
            _add(os.path.join(root, *parts))

    return paths


def _try_pdf_via_browser(
    html_path: str,
    pdf_path: str,
    *,
    paper_width_mm: float | None = None,
    paper_height_mm: float | None = None,
) -> bool:
    uri = 'file:///' + os.path.abspath(html_path).replace('\\', '/')
    # The browser that worked last time goes first, and with a short timeout:
    # every bill pays for this conversion, so a browser that is slow to start,
    # blocked by antivirus or mid-update must not hold the counter up for a
    # minute per attempt before the next one is tried.
    for exe, headless_flag, timeout_s in _pdf_browser_attempts():
        try:
            if os.path.isfile(pdf_path):
                os.remove(pdf_path)
        except Exception:
            pass
        started = time.time()
        try:
            cmd = [
                exe,
                headless_flag,
                '--disable-gpu',
                '--no-pdf-header-footer',
                '--disable-extensions',
                '--run-all-compositor-stages-before-draw',
                '--virtual-time-budget=5000',
            ]
            if paper_width_mm and paper_height_mm:
                cmd.append(f'--paper-width={paper_width_mm / 25.4:.4f}')
                cmd.append(f'--paper-height={paper_height_mm / 25.4:.4f}')
            cmd.append(f'--print-to-pdf={pdf_path}')
            cmd.append(uri)
            subprocess.run(
                cmd,
                timeout=timeout_s,
                capture_output=True,
                check=False,
            )
            if os.path.isfile(pdf_path) and os.path.getsize(pdf_path) > 500:
                _remember_pdf_browser(exe, headless_flag, time.time() - started)
                return True
            _log_pdf_browser(f'no PDF from "{os.path.basename(exe)}" {headless_flag} '
                             f'in {time.time() - started:.1f}s')
        except subprocess.TimeoutExpired:
            _log_pdf_browser(f'"{os.path.basename(exe)}" {headless_flag} timed out after {timeout_s}s')
        except Exception as exc:
            _log_pdf_browser(f'"{os.path.basename(exe)}" {headless_flag} failed: {exc}')
    return False


def _log_pdf_browser(message: str) -> None:
    try:
        from core.print_log import print_log
        print_log(f'[pdf] {message}')
    except Exception:
        pass


def _pdf_browser_attempts() -> list[tuple[str, str, int]]:
    """(browser, headless flag, timeout) to try, best first.

    The pair that last produced a PDF is tried first with a short timeout, so
    the usual bill costs one browser start. Everything else follows as before.
    """
    flags = ('--headless=new', '--headless')
    attempts: list[tuple[str, str, int]] = []
    try:
        from core.printer_manager import PrinterManager
        cfg = PrinterManager.load_settings()
        last_exe = str(cfg.get('pdf_browser_path') or '').strip()
        last_flag = str(cfg.get('pdf_browser_flag') or '').strip()
        if last_exe and os.path.isfile(last_exe) and last_flag in flags:
            attempts.append((last_exe, last_flag, 30))
    except Exception:
        pass
    for exe in _chromium_browser_paths():
        for flag in flags:
            if not any(exe == a[0] and flag == a[1] for a in attempts):
                attempts.append((exe, flag, 60))
    return attempts


def _remember_pdf_browser(exe: str, headless_flag: str, seconds: float) -> None:
    """Keep the browser that just worked, so the next bill starts with it."""
    try:
        from core.printer_manager import PrinterManager
        cfg = PrinterManager.load_settings()
        if cfg.get('pdf_browser_path') == exe and cfg.get('pdf_browser_flag') == headless_flag:
            return
        cfg['pdf_browser_path'] = exe
        cfg['pdf_browser_flag'] = headless_flag
        PrinterManager.save_settings(cfg)
        _log_pdf_browser(f'using "{os.path.basename(exe)}" {headless_flag} ({seconds:.1f}s) from now on')
    except Exception:
        pass


def _try_pdf_via_edge(html_path: str, pdf_path: str) -> bool:
    """Back-compat alias."""
    return _try_pdf_via_browser(html_path, pdf_path)


def _print_pdf_fallback(pdf_path: str, reason: Exception | None = None) -> None:
    """Open PDF for printing when the native GDI dialog path fails."""
    path = os.path.abspath(pdf_path)
    if sys.platform == 'win32':
        try:
            os.startfile(path, 'print')
            return
        except Exception:
            pass
    webbrowser.open('file:///' + path.replace('\\', '/'))
    if reason is not None:
        from core.themed_messagebox import showwarning
        showwarning(
            'Print Bill',
            'Native print dialog was unavailable '
            f'({reason}).\n\nPDF opened — use your viewer\'s Print command.',
        )


def _open_system_print_dialog(
    pdf_path: str,
    hwnd_owner: int = 0,
    *,
    copies: int = 1,
    paper_hint: str = "",
) -> None:
    """Open the native Windows print dialog for the saved bill PDF."""
    if sys.platform != 'win32':
        webbrowser.open('file:///' + os.path.abspath(pdf_path).replace('\\', '/'))
        return

    from core.windows_print_dialog import print_pdf_with_native_dialog

    try:
        if not print_pdf_with_native_dialog(
            pdf_path,
            hwnd_owner=hwnd_owner,
            copies=copies,
            paper_hint=paper_hint,
        ):
            raise RuntimeError('Could not open the Windows print dialog.')
    except OSError as exc:
        _print_pdf_fallback(pdf_path, exc)
    except Exception as exc:
        _print_pdf_fallback(pdf_path, exc)


def _is_dot_matrix_printer_mode() -> bool:
    try:
        from core.printer_manager import PrinterManager
        return PrinterManager.is_dot_matrix_mode()
    except Exception:
        return False


def _log_print(message: str, *, level: str = 'INFO') -> None:
    try:
        from core.print_log import print_log
        print_log(message, level=level)
    except Exception:
        pass


def _print_bill_dot_matrix_with_slot(
    conn,
    sale_id,
    slot: int,
    *,
    db_path: str | None = None,
    settings_override: dict | None = None,
    fallback_to_html: bool = True,
) -> tuple[str, str | None]:
    """RAW ESC/P print for 9-pin dot matrix; optional HTML fallback on failure."""
    from core.bill_config import BILL_SIZE_DOT_MATRIX
    from core.dot_matrix_print import DotMatrixPrintError, print_dot_matrix_bill_pages
    from core.printer_manager import PrinterError, PrinterManager
    from core.themed_messagebox import showwarning

    _log_print(
        f'dot_matrix print start sale_id={sale_id} slot={slot} db_path={db_path or ""}'
    )

    base = load_bill_print_settings()
    bill_no, ctx, settings = _load_sale_data_for_pdf(
        conn, sale_id, settings_override=settings_override, print_slot=slot, db_path=db_path,
    )
    per_items = _items_per_bill_page(settings, dot_matrix=True)
    contexts = _carry_forward_contexts(ctx, per_items)
    printer = PrinterManager.resolve_dot_matrix_printer(
        PrinterManager.get_printer_for_slot(slot)
    )
    page_copies = get_print_slot_page_copies(base, slot)
    _log_print(
        f'bill_no={bill_no} printer="{printer}" pages={len(contexts)} '
        f'items={len(ctx.items or [])} copies={page_copies}'
    )

    def _print_all_pages(target_printer: str) -> None:
        _log_print(
            f'dot_matrix pages={len(contexts)} items={len(ctx.items or [])} '
            f'continued={[bool(getattr(c, "is_continued", False)) for c in contexts]}'
        )
        print_dot_matrix_bill_pages(
            contexts, settings, target_printer,
            copies=page_copies,
        )

    try:
        _print_all_pages(printer)
        _log_print(f'dot_matrix print OK bill_no={bill_no}')
        return "", None
    except (DotMatrixPrintError, PrinterError) as exc:
        live = PrinterManager.find_escp_lx310_printer()
        if live and live.lower() != (printer or "").lower():
            _log_print(
                f'dot_matrix retry on live queue "{live}" after failure on "{printer}"',
                level='WARN',
            )
            try:
                _print_all_pages(live)
                _log_print(f'dot_matrix print OK bill_no={bill_no}')
                return "", None
            except (DotMatrixPrintError, PrinterError) as retry_exc:
                exc = retry_exc
        try:
            from core.print_log import print_log_exception
            print_log_exception(f'dot_matrix print failed bill_no={bill_no}', exc)
        except Exception:
            _log_print(f'dot_matrix print failed: {exc}', level='ERROR')
        if not fallback_to_html:
            raise RuntimeError(str(exc)) from exc
        showwarning(
            "Dot Matrix Print Failed",
            f"{exc}\n\nCould not print on dot matrix. Check printer USB cable and queue.",
        )
        _log_print('dot_matrix print failed — not falling back to PDF (avoid duplicate slip)', level='ERROR')
        raise RuntimeError(str(exc)) from exc


def print_bill_silent_with_slot(
    conn,
    sale_id,
    slot: int,
    *,
    db_path: str | None = None,
    settings_override: dict | None = None,
    skip_dot_matrix: bool = False,
) -> tuple[str, str | None]:
    """Render with print slot preset, save PDF(s), print silently via SumatraPDF."""
    if not skip_dot_matrix and _is_dot_matrix_printer_mode():
        _log_print(f'print_bill_silent_with_slot -> dot_matrix sale_id={sale_id} slot={slot}')
        return _print_bill_dot_matrix_with_slot(
            conn, sale_id, slot, db_path=db_path, settings_override=settings_override,
        )

    _log_print(f'print_bill_silent_with_slot -> HTML/PDF sale_id={sale_id} slot={slot}')

    base = load_bill_print_settings()
    if settings_override:
        base.update(settings_override)
    pages = save_bill_pdf_pages(
        conn, sale_id, settings_override=base if settings_override else None,
        print_slot=slot, db_path=db_path,
    )
    if not pages:
        raise RuntimeError('Could not render bill for printing.')
    from core.printer_manager import PrinterManager

    printer = PrinterManager.get_printer_for_slot(slot)
    page_copies = get_print_slot_page_copies(base, slot)
    last_html, last_pdf = pages[0]
    for html_path, pdf_path in pages:
        if not pdf_path:
            raise RuntimeError(
                'Bill HTML was saved to Downloads, but PDF could not be created for printing.\n'
                'Install Microsoft Edge (or Google Chrome) and try again.'
            )
        PrinterManager.print_pdf_silently(pdf_path, printer, copies=page_copies)
        last_html, last_pdf = html_path, pdf_path
    return last_html, last_pdf


def print_bill_with_slot(
    conn,
    sale_id,
    slot: int,
    hwnd_owner: int = 0,
    settings_override: dict | None = None,
    *,
    db_path: str | None = None,
    skip_dot_matrix: bool = False,
) -> tuple[str, str | None]:
    """Render with print slot preset, save PDF(s), then print (silent or dialog)."""
    from core.printer_manager import PrinterManager

    if not skip_dot_matrix and _is_dot_matrix_printer_mode():
        return _print_bill_dot_matrix_with_slot(
            conn, sale_id, slot, db_path=db_path, settings_override=settings_override,
        )

    cfg = PrinterManager.load_settings()
    if cfg.get('silent_print_enabled', True):
        try:
            if settings_override:
                base = load_bill_print_settings()
                base.update(settings_override)
                pages = save_bill_pdf_pages(
                    conn, sale_id, settings_override=base, print_slot=slot, db_path=db_path,
                )
                if not pages:
                    raise RuntimeError('Could not render bill for printing.')
                printer = PrinterManager.get_printer_for_slot(slot)
                page_copies = get_print_slot_page_copies(base, slot)
                last_html, last_pdf = pages[0]
                for html_path, pdf_path in pages:
                    if not pdf_path:
                        raise RuntimeError(
                            'Bill HTML was saved to Downloads, but PDF could not be created for printing.\n'
                            'Install Microsoft Edge (or Google Chrome) and try again.'
                        )
                    PrinterManager.print_pdf_silently(pdf_path, printer, copies=page_copies)
                    last_html, last_pdf = html_path, pdf_path
                return last_html, last_pdf
            return print_bill_silent_with_slot(
                conn, sale_id, slot, db_path=db_path, settings_override=settings_override,
                skip_dot_matrix=True,
            )
        except Exception:
            pass  # Fall back to the native print dialog below.

    base = load_bill_print_settings()
    if settings_override:
        base.update(settings_override)
    preset = get_print_slot_settings(base, slot)
    pages = save_bill_pdf_pages(
        conn, sale_id, settings_override=base if settings_override else None,
        print_slot=slot, db_path=db_path,
    )
    if not pages:
        raise RuntimeError('Could not render bill for printing.')
    paper = (preset.get('paper_size') or 'A5').upper()
    page_copies = get_print_slot_page_copies(base, slot)
    last_html, last_pdf = pages[0]
    for html_path, pdf_path in pages:
        if not pdf_path:
            raise RuntimeError(
                'Bill HTML was saved to Downloads, but PDF could not be created for printing.\n'
                'Install Microsoft Edge (or Google Chrome) and try again.'
            )
        _open_system_print_dialog(
            pdf_path,
            hwnd_owner=hwnd_owner,
            copies=page_copies,
            paper_hint=paper,
        )
        last_html, last_pdf = html_path, pdf_path
    return last_html, last_pdf


def save_bill_to_downloads(conn, sale_id) -> tuple[str, str | None]:
    """
    Write Bill_<no>.html and Bill_<no>.pdf (when possible) to the bill folder.
    Returns (html_path, pdf_path or None).
    """
    return save_bill_pdf_only(conn, sale_id)


def save_bill_pdf_a6(
    conn,
    sale_id,
    *,
    db_path: str | None = None,
) -> tuple[str, str | None]:
    """Save an A6 landscape bill PDF to the configured sales bill folder."""
    from core.bill_save_prefs import PDF_LAYOUT_ONE_A6, resolve_sales_bill_save_dir

    override = {
        "for_pdf_save": True,
        "pdf_save_layout": PDF_LAYOUT_ONE_A6,
        "copies": 1,
        "bill_copies": 1,
    }
    bill_no, ctx, settings = _load_sale_data_for_pdf(
        conn, sale_id, settings_override=override, db_path=db_path,
    )
    html_settings = dict(settings)
    html_settings.update(override)

    save_dir = resolve_sales_bill_save_dir()
    per_items = _items_per_bill_page(html_settings)
    contexts = _carry_forward_contexts(ctx, per_items)

    if len(contexts) <= 1:
        return _render_bill_files(bill_no, ctx, html_settings, save_dir)

    pages = []
    for i, page_ctx in enumerate(contexts):
        pages.append(
            _render_bill_files(
                bill_no, page_ctx, html_settings, save_dir,
                page_index=i, page_count=len(contexts),
            )
        )
    return pages[0] if pages else ("", None)


def prepare_and_print_bill(conn, sale_id, hwnd_owner: int = 0) -> tuple[str, str | None]:
    """Save bill files to the bill folder, then open the Windows print dialog."""
    if _is_dot_matrix_printer_mode():
        from core.printer_manager import PrinterManager
        slot = 1
        return _print_bill_dot_matrix_with_slot(
            conn, sale_id, slot, fallback_to_html=True,
        )

    html_path, pdf_path = save_bill_to_downloads(conn, sale_id)
    if not pdf_path:
        raise RuntimeError(
            'Bill HTML was saved to Downloads, but PDF could not be created for printing.\n'
            'Install Microsoft Edge (or Google Chrome) and try again.'
        )
    page_copies = get_page_copies()
    _open_system_print_dialog(pdf_path, hwnd_owner=hwnd_owner, copies=page_copies)
    return html_path, pdf_path


def open_bill_for_print(
    conn, bill_no, sale_id, *, auto_print: bool = True, save_pdf: bool = True, hwnd_owner: int = 0,
):
    """Save to Downloads and open the Windows print dialog."""
    html_path, pdf_path = save_bill_to_downloads(conn, sale_id)
    if auto_print:
        if _is_dot_matrix_printer_mode():
            return _print_bill_dot_matrix_with_slot(conn, sale_id, 1, fallback_to_html=True)
        if not pdf_path:
            raise RuntimeError(
                'Bill HTML was saved to Downloads, but PDF could not be created for printing.\n'
                'Install Microsoft Edge (or Google Chrome) and try again.'
            )
        _open_system_print_dialog(
            pdf_path, hwnd_owner=hwnd_owner, copies=get_page_copies(),
        )
    return html_path, pdf_path
