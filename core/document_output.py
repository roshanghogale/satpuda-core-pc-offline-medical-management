"""Write HTML/PDF documents and offer open/print after save."""
from __future__ import annotations

import os
import re
import subprocess
# tkinter is excluded from the headless engine build; use the
# degrade-safe wrapper instead of importing Tk at module scope.
from core import themed_messagebox as messagebox


def save_html_as_pdf(
    html: str,
    pdf_path: str,
    *,
    paper_width_mm: float | None = None,
    paper_height_mm: float | None = None,
) -> tuple[str | None, str]:
    """Write HTML and try headless-browser PDF. Returns (pdf_path or None, html_path)."""
    pdf_path = pdf_path if pdf_path.lower().endswith(".pdf") else f"{pdf_path}.pdf"
    html_path = pdf_path.rsplit(".", 1)[0] + ".html"
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)
    try:
        from core.bill_output import _try_pdf_via_browser
        ok = _try_pdf_via_browser(
            html_path,
            pdf_path,
            paper_width_mm=paper_width_mm,
            paper_height_mm=paper_height_mm,
        )
    except Exception:
        ok = False
    if ok and os.path.isfile(pdf_path):
        return pdf_path, html_path
    return None, html_path


def open_file(path: str) -> None:
    if not path:
        return
    path = os.path.abspath(path)
    if not os.path.exists(path):
        return
    if os.name == "nt":
        os.startfile(path)
    else:
        subprocess.Popen(["xdg-open", path])


def _format_saved_pdf_message(saved_path: str) -> str:
    folder = os.path.dirname(os.path.abspath(saved_path))
    folder_name = os.path.basename(folder) or folder
    return f"Folder: {folder_name}\n{saved_path}\n\nOpen, print, or close?"


def offer_open_saved_file(parent, saved_path: str, *, title: str = "Saved") -> None:
    if not saved_path or not os.path.isfile(saved_path):
        return
    from core.themed_messagebox import show_open_file
    msg = _format_saved_pdf_message(saved_path).replace("\n\nOpen, print, or close?", "")
    if show_open_file(title, msg.strip(), parent=parent):
        open_file(saved_path)


def _table_document_html(
    title: str,
    subtitle: str,
    headers: list[str],
    rows: list[list],
    *,
    footer_lines: list[str] | None = None,
) -> str:
    from datetime import datetime

    def esc(v) -> str:
        return str(v).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    date_str = datetime.now().strftime("%d/%m/%Y %H:%M")
    hdr = "".join(f"<th>{esc(h)}</th>" for h in headers)
    body_rows = ""
    for i, row in enumerate(rows):
        cls = "even" if i % 2 == 0 else "odd"
        cells = "".join(f"<td>{esc(v)}</td>" for v in row)
        body_rows += f'<tr class="{cls}">{cells}</tr>\n'
    footer_html = ""
    if footer_lines:
        footer_html = "".join(f"<p><strong>{esc(line)}</strong></p>" for line in footer_lines)

    return f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>{esc(title)}</title>
<style>
  @page {{ size: A4; margin: 12mm; }}
  body {{ font-family: 'Segoe UI', Arial, sans-serif; font-size: 10pt; color: #111; }}
  h1 {{ font-size: 16pt; margin: 0 0 4px; }}
  .sub {{ color: #444; margin-bottom: 10px; font-size: 9pt; }}
  table {{ width: 100%; border-collapse: collapse; margin-top: 8px; }}
  th {{ background: #2c3e50; color: #fff; padding: 6px 8px; text-align: left; border: 1px solid #222; }}
  td {{ padding: 5px 8px; border: 1px solid #ccc; }}
  tr.even {{ background: #f7f7f7; }}
  .footer {{ margin-top: 14px; font-size: 10pt; }}
</style></head><body>
<h1>{esc(title)}</h1>
<p class="sub">{esc(subtitle)}<br/>Generated: {date_str}</p>
<table><thead><tr>{hdr}</tr></thead><tbody>{body_rows}</tbody></table>
<div class="footer">{footer_html}</div>
</body></html>"""


def _fmt_doc_date(raw) -> str:
    if not raw:
        return ""
    try:
        parts = str(raw).split("-")
        if len(parts) == 3:
            return f"{parts[2]}/{parts[1]}/{parts[0][2:]}"
    except Exception:
        pass
    return str(raw)


def _supplier_document_settings(*, doc_kind: str) -> dict:
    from core.bill_config import load_bill_print_settings
    from core.bill_save_prefs import PDF_LAYOUT_ONE_A6

    settings = dict(load_bill_print_settings())
    settings.update({
        "for_pdf_save": True,
        "pdf_save_layout": PDF_LAYOUT_ONE_A6,
        "paper_size": "A6",
        "copies": 1,
        "bill_copies": 1,
        "show_doctor": False,
        "show_doctor_name": False,
        "show_doctor_reg": False,
        "show_customer_due": False,
        "show_pay_mode": False,
        "show_gst": False,
    })
    if doc_kind == "purchase_return":
        settings.update({
            "supplier_doc_compact": True,
            "compact_supplier_footer": True,
            "hide_party_meta": True,
            "show_party_on_left_panel": True,
            "hide_blessing": True,
            "show_blessing": False,
            "show_store_address": False,
            "show_store_phone": False,
            "show_store_email": False,
            "show_store_gstin": False,
            "show_store_dl": False,
            "show_recovery_wish": False,
            "show_signature": False,
            "show_gst_strip": False,
            "document_title": "PURCHASE RETURN",
            "meta_bill_no_label": "RETURN NO.",
            "meta_date_label": "RETURN DATE",
            "total_label": "CREDIT AMT",
            "discount_label": "LESS DISC",
            "medicine_mrp_header": "RATE",
            "medicine_amount_header": "AMT",
            "footer_strip_line": "GOODS RETURNED - CREDIT TO ACCOUNT",
            "signature_caption": "AUTH. SIGN.",
        })
    elif doc_kind == "reorder":
        settings.update({
            "supplier_doc_compact": True,
            "compact_supplier_footer": True,
            "hide_party_meta": True,
            "show_party_on_left_panel": True,
            "hide_blessing": True,
            "show_blessing": False,
            "show_store_address": False,
            "show_store_phone": False,
            "show_store_email": False,
            "show_store_gstin": False,
            "show_store_dl": False,
            "show_recovery_wish": False,
            "show_signature": False,
            "show_gst_strip": False,
            "document_title": "REORDER ORDER",
            "meta_bill_no_label": "ORDER NO.",
            "meta_date_label": "ORDER DATE",
            "total_label": "ORDER AMT",
            "discount_label": "LESS DISC",
            "medicine_mrp_header": "RATE",
            "medicine_amount_header": "AMT",
            "footer_strip_line": "PLEASE SUPPLY AT EARLIEST",
            "signature_caption": "AUTH. SIGN.",
        })
    return settings


# A6 landscape sheet for supplier return/reorder PDFs (148 × 105 mm).
SUPPLIER_DOC_PAPER_MM = (148.0, 105.0)


def _store_fields_from_profile(profile) -> dict:
    from core.bill_context import _logo_to_base64

    if not profile:
        return {
            "store_name": "MEDICAL STORE",
            "address": "",
            "phone": "",
            "email": "",
            "gstin": "",
            "dl_no": "",
            "fssai": "",
            "show_fssai_on_bill": False,
            "logo_src": "",
            "gst_enabled": False,
            "blessing_line": "SHREE GANESHAY NAMAH",
        }
    return {
        "store_name": (profile[1] or "MEDICAL STORE").upper(),
        "address": profile[2] or "",
        "phone": profile[3] or "",
        "email": profile[4] or "",
        "gstin": profile[5] or "",
        "dl_no": profile[6] or "",
        "gst_enabled": bool(profile[7]),
        "logo_src": _logo_to_base64(profile[9] if len(profile) > 9 else ""),
        "fssai": (profile[10] or "").strip() if len(profile) > 10 else "",
        "show_fssai_on_bill": bool(profile[11]) if len(profile) > 11 else False,
        "blessing_line": "SHREE GANESHAY NAMAH",
    }


def _render_supplier_bill_html(ctx, settings: dict) -> str:
    from core.bill_config import render_bill_html

    return render_bill_html(ctx, settings)


def render_purchase_return_html(conn, return_id: int) -> str:
    from core.pharmacy_profile_io import fetch_pharmacy_profile_row

    cur = conn.cursor()
    profile = fetch_pharmacy_profile_row(conn)
    cur.execute(
        """
        SELECT pr.return_no, pr.return_date, pr.refund_amount, pr.discount, COALESCE(pr.reason, ''),
               s.name, COALESCE(s.phone, ''), COALESCE(s.address, ''),
               COALESCE(s.gstin, ''), COALESCE(s.dl_numbers, ''),
               p.purchase_no, COALESCE(p.bill_number, '')
        FROM purchase_returns pr
        JOIN suppliers s ON pr.supplier_id = s.id
        JOIN purchases p ON pr.purchase_id = p.id
        WHERE pr.id = ?
        """,
        (int(return_id),),
    )
    row = cur.fetchone()
    if not row:
        raise ValueError(f"Purchase return {return_id} not found")
    (
        return_no, return_date, refund, discount_pct, reason,
        supplier, phone, address, gstin, dl, purchase_no, bill_no,
    ) = row
    cur.execute(
        """
        SELECT m.name, m.batch_no, m.expiry_date, pri.qty, pri.rate, pri.amount,
               COALESCE(m.mrp, pri.rate, 0)
        FROM purchase_return_items pri
        JOIN medicines m ON pri.medicine_id = m.id
        WHERE pri.return_id = ?
        ORDER BY pri.id
        """,
        (int(return_id),),
    )
    lines = [
        {"name": name, "batch": batch, "expiry": expiry, "qty": qty,
         "rate": rate, "amount": amount, "mrp": mrp}
        for name, batch, expiry, qty, rate, amount, mrp in cur.fetchall()
    ]
    head = {
        "return_no": return_no, "return_date": return_date, "refund": refund,
        "reason": reason, "supplier": supplier, "phone": phone, "address": address,
        "purchase_no": purchase_no, "bill_no": bill_no,
    }
    return _purchase_return_html_from(profile, head, lines)


def _purchase_return_html_from(profile, head: dict, lines: list) -> str:
    """Render a purchase return from plain values. Shared by the Offline reader
    above and the Online path, whose values come from the server."""
    from core.bill_config import BillContext, BillItem

    return_no = head.get("return_no")
    return_date = head.get("return_date")
    refund = head.get("refund")
    reason = head.get("reason")
    supplier = head.get("supplier")
    phone = head.get("phone")
    address = head.get("address")
    purchase_no = head.get("purchase_no")
    bill_no = head.get("bill_no")
    bill_items = []
    sub_total = 0.0
    for ln in lines:
        name, batch, expiry = ln.get("name"), ln.get("batch"), ln.get("expiry")
        qty, rate, amount, mrp = ln.get("qty"), ln.get("rate"), ln.get("amount"), ln.get("mrp")
        amt = float(amount or 0)
        sub_total += amt
        bill_items.append(BillItem(
            name=name or "",
            batch=batch or "",
            expiry=expiry or "",
            qty=float(qty or 0),
            rate=float(rate or 0),
            mrp=float(mrp or rate or 0),
            amount=amt,
        ))
    sub_total = round(sub_total, 2)
    refund = round(float(refund or 0), 2)
    discount_amt = round(max(0.0, sub_total - refund), 2)

    left_panel = [
        ("Supplier", (supplier or "").upper()),
    ]
    if phone:
        left_panel.append(("Phone", phone))
    if address:
        left_panel.append(("Address", address))
    left_panel.append(("Purchase", purchase_no))
    if bill_no:
        left_panel.append(("Pur.Bill", bill_no))
    if reason:
        left_panel.append(("Reason", reason))

    store = _store_fields_from_profile(profile)
    store["gst_enabled"] = False
    ctx = BillContext(
        **store,
        document_title="PURCHASE RETURN",
        left_panel_lines=left_panel,
        footer_strip_line="GOODS RETURNED - CREDIT TO ACCOUNT",
        bill_no=str(return_no),
        bill_date=_fmt_doc_date(return_date),
        bill_date_landscape=_fmt_doc_date(return_date),
        items=bill_items,
        sub_total=sub_total,
        discount=discount_amt,
        taxable_amount=refund,
        gst_amount=0.0,
        grand_total=refund,
        amount_paid=0.0,
    )
    settings = _supplier_document_settings(doc_kind="purchase_return")
    return _render_supplier_bill_html(ctx, settings)


def render_reorder_order_html(conn, group_id) -> str:
    from core.bill_config import BillContext, BillItem
    from core.pharmacy_profile_io import fetch_pharmacy_profile_row
    from core.reorder_service import fetch_pending_orders_by_group

    orders = fetch_pending_orders_by_group(conn, group_id)
    if not orders:
        raise ValueError(f"Reorder group {group_id} not found")
    first = orders[0]
    supplier = first.get("supplier_name") or first.get("supplier_name_manual") or "Supplier"
    phone = first.get("supplier_phone") or ""
    delivery = first.get("expected_delivery_date") or ""
    notes = first.get("notes") or ""
    order_date = first.get("order_date") or ""
    order_no = first.get("order_no") or str(group_id)

    profile = fetch_pharmacy_profile_row(conn)

    bill_items = []
    sub_total = 0.0
    for line in orders:
        qty = float(line.get("quantity") or 0)
        rate = float(line.get("unit_price") or 0)
        amt = round(qty * rate, 2)
        sub_total += amt
        bill_items.append(BillItem(
            name=line.get("medicine_name") or "",
            batch=line.get("pack_size") or "",
            expiry="",
            qty=qty,
            rate=rate,
            mrp=rate,
            amount=amt,
        ))
    sub_total = round(sub_total, 2)

    left_panel = [
        ("Supplier", (supplier or "").upper()),
    ]
    if phone:
        left_panel.append(("Phone", phone))
    if delivery:
        left_panel.append(("Delivery", delivery))
    if notes:
        left_panel.append(("Notes", notes))

    store = _store_fields_from_profile(profile)
    store["gst_enabled"] = False
    ctx = BillContext(
        **store,
        document_title="REORDER ORDER",
        left_panel_lines=left_panel,
        footer_strip_line="PLEASE SUPPLY AT EARLIEST",
        bill_no=str(order_no),
        bill_date=_fmt_doc_date(order_date),
        bill_date_landscape=_fmt_doc_date(order_date),
        items=bill_items,
        sub_total=sub_total,
        discount=0.0,
        taxable_amount=sub_total,
        gst_amount=0.0,
        grand_total=sub_total,
        amount_paid=0.0,
    )
    settings = _supplier_document_settings(doc_kind="reorder")
    return _render_supplier_bill_html(ctx, settings)


def _safe_doc_filename(name: str) -> str:
    safe = re.sub(r"[^\w\-]+", "_", str(name or "document")).strip("_")
    return (safe[:80] or "document")


def save_supplier_document(html: str, default_name: str) -> tuple[str | None, str]:
    """Write supplier PDF/HTML to configured bill folder (supplier_docs subdir)."""
    from core.bill_save_prefs import resolve_supplier_doc_save_dir

    save_dir = resolve_supplier_doc_save_dir()
    base_path = os.path.join(save_dir, _safe_doc_filename(default_name))
    w_mm, h_mm = SUPPLIER_DOC_PAPER_MM
    return save_html_as_pdf(html, base_path, paper_width_mm=w_mm, paper_height_mm=h_mm)


def offer_supplier_document(parent, html: str, default_name: str) -> None:
    """After save: auto-save PDF then offer Open / Print / Close."""
    pdf_path, html_path = save_supplier_document(html, default_name)
    target = pdf_path or html_path
    if not target or not os.path.isfile(target):
        messagebox.showwarning(
            "PDF saved",
            "Could not save PDF.\n\n"
            "Install Microsoft Edge or Google Chrome and try again.",
            parent=parent,
        )
        return
    from core.themed_messagebox import _show
    choice = _show(
        parent,
        "PDF saved",
        _format_saved_pdf_message(target),
        "info",
        [
            ("Open", "open", "primary"),
            ("Print", "print", "secondary"),
            ("Close", "close", "secondary"),
        ],
    )
    if choice == "open":
        open_file(target)
    elif choice == "print":
        if pdf_path and os.name == "nt":
            try:
                os.startfile(pdf_path, "print")
            except Exception:
                open_file(pdf_path)
        else:
            open_file(target)


SCHEDULE_LAYOUT_PORTRAIT = "portrait"
SCHEDULE_LAYOUT_LANDSCAPE = "landscape"
SCHEDULE_LAYOUT_STYLED = "styled"

SCHEDULE_LAYOUT_LABELS = {
    SCHEDULE_LAYOUT_PORTRAIT: "A4 Portrait (separate columns, no rate/amount)",
    SCHEDULE_LAYOUT_LANDSCAPE: "A4 Landscape (wide — all columns)",
    SCHEDULE_LAYOUT_STYLED: "Styled Vertical (combined columns — Classic & Sign)",
}

_PORTRAIT_SKIP_COLS = frozenset({"Rate", "Amount"})


def schedule_report_title(sch_label: str, *, single_schedule: str | None = None) -> str:
    code = (single_schedule or "").strip()
    if code:
        return f"{code} Schedule Report"
    return "Schedule Sales Report"


def _header_col_class(name: str) -> str:
    hl = (name or "").lower()
    if name == "Qty" or hl == "qty":
        return "col-qty c"
    if name == "Batch":
        return "col-batch"
    if name == "Expiry":
        return "col-exp c"
    if name == "Schedule":
        return "col-sch c"
    if name == "Doctor":
        return "col-doc"
    if name == "Customer":
        return "col-cust"
    if "date" in hl and "bill" in hl:
        return "col-date"
    if hl == "date":
        return "col-date"
    if hl == "bill no":
        return "col-bill"
    if "content" in hl:
        return "col-content"
    if "medicine" in hl:
        return "col-med"
    if hl in ("rate", "amount") or "amt" in hl:
        return "col-amt r"
    if "qty" in hl:
        return "col-qty c"
    return ""


def _schedule_paper_mm(page_layout: str) -> tuple[float, float]:
    if (page_layout or "").strip().lower().startswith("land"):
        return 297.0, 210.0
    return 210.0, 297.0


def _esc_html(v) -> str:
    return str(v).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _display_schedule_bill_no(raw) -> str:
    try:
        from core.fy_serial import display_sales_bill_no
        return display_sales_bill_no(str(raw or ""))
    except Exception:
        s = str(raw or "").strip()
        return s.split("/FY", 1)[0] if "/FY" in s else s


def _cell_two_line(line1: str, line2: str, *, second_class: str = "sub") -> str:
    p1 = _esc_html(str(line1 or "").strip())
    p2 = _esc_html(str(line2 or "").strip())
    if p1 and p2:
        return f'{p1}<br><span class="{second_class}">{p2}</span>'
    return p1 or p2 or "—"


def _schedule_row_dict(headers: list, row: list) -> dict:
    return {h: row[i] if i < len(row) else "" for i, h in enumerate(headers)}


def prepare_schedule_report_display(
    headers: list,
    table_rows: list,
    *,
    page_layout: str = SCHEDULE_LAYOUT_PORTRAIT,
    total_qty: int | None = None,
    total_amount: float | None = None,
    qty_idx: int | None = None,
    amt_idx: int | None = None,
    single_schedule: str | None = None,
) -> dict:
    """Portrait/styled: separate columns, no rate/amount. Landscape: flat wide table."""
    headers = list(headers)
    layout = (page_layout or SCHEDULE_LAYOUT_PORTRAIT).strip().lower()
    if layout.startswith("land"):
        flat_rows = [[str(c) for c in row] for row in table_rows]
        return {
            "headers": headers,
            "rows": flat_rows,
            "page_layout": SCHEDULE_LAYOUT_LANDSCAPE,
            "qty_idx": qty_idx,
            "amt_idx": amt_idx,
            "total_qty": total_qty,
            "total_amount": total_amount,
            "html_cells": False,
        }

    # styled + portrait share vertical combined-column layout
    layout_out = (
        SCHEDULE_LAYOUT_STYLED if layout.startswith("styl") else SCHEDULE_LAYOUT_PORTRAIT
    )

    hset = set(headers) - _PORTRAIT_SKIP_COLS
    if (single_schedule or "").strip():
        hset.discard("Schedule")

    out_headers: list[str] = []
    builders: list = []
    qty_out_idx = None

    def add_col(title: str, fn) -> None:
        out_headers.append(title)
        builders.append(fn)

    if "Date / Bill" in hset:       # already merged by an earlier pass
        add_col("Date / Bill", lambda d: _esc_html(d.get("Date / Bill", "")))
    elif hset & {"Date", "Bill No"}:
        add_col(
            "Date / Bill",
            lambda d: _cell_two_line(
                d.get("Date", "") if "Date" in hset else "",
                _display_schedule_bill_no(d.get("Bill No", "")) if "Bill No" in hset else "",
            ),
        )

    for col in ("Customer", "Doctor"):
        if col in hset:
            add_col(col, lambda d, c=col: _esc_html(d.get(c, "")))

    if hset & {"Medicine", "Content/Drug"}:
        def _medicine_cell(d, hs=hset):
            med = d.get("Medicine", "") if "Medicine" in hs else ""
            content = d.get("Content/Drug", "") if "Content/Drug" in hs else ""
            if "Content/Drug" in hs and str(content or "").strip():
                return _cell_two_line(med, content, second_class="sub muted")
            return _esc_html(med or content or "—")

        add_col("Medicine", _medicine_cell)

    # "Batch/Expiry" arrives already merged when the Sign preset has reshaped the
    # register first; it used to fall through this list and the batch and expiry
    # vanished from the printed H1 register -- the very data the signature is against.
    if "Batch/Expiry" in hset:
        add_col("Batch/Expiry", lambda d: _esc_html(d.get("Batch/Expiry", "")))
    for col in ("Batch", "Expiry", "Schedule"):
        if col in hset:
            add_col(col, lambda d, c=col: _esc_html(d.get(c, "")))

    if "Qty" in hset:
        qty_out_idx = len(out_headers)
        add_col("Qty", lambda d: _esc_html(d.get("Qty", "")))

    if "Sign" in hset:      # the box the customer signs in, on the register itself
        add_col("Sign", lambda d: _esc_html(d.get("Sign", "")))

    if not out_headers:
        out_headers = [h for h in headers if h not in _PORTRAIT_SKIP_COLS]
        builders = [lambda d, h=h: _esc_html(d.get(h, "")) for h in out_headers]

    out_rows: list[list[str]] = []
    for row in table_rows:
        d = _schedule_row_dict(headers, row)
        out_rows.append([fn(d) for fn in builders])

    return {
        "headers": out_headers,
        "rows": out_rows,
        "page_layout": layout_out,
        "qty_idx": qty_out_idx,
        "amt_idx": None,
        "total_qty": total_qty,
        "total_amount": total_amount,
        "html_cells": True,
    }


def build_schedule_report_html(
    *,
    sch_label: str,
    date_range: str,
    headers: list,
    table_rows: list,
    total_qty: int | None = None,
    total_amount: float | None = None,
    qty_idx: int | None = None,
    amt_idx: int | None = None,
    page_layout: str = SCHEDULE_LAYOUT_PORTRAIT,
    single_schedule: str | None = None,
    report_title: str | None = None,
) -> str:
    title = (report_title or "").strip() or schedule_report_title(
        sch_label, single_schedule=single_schedule,
    )
    display = prepare_schedule_report_display(
        headers,
        table_rows,
        page_layout=page_layout,
        total_qty=total_qty,
        total_amount=total_amount,
        qty_idx=qty_idx,
        amt_idx=amt_idx,
        single_schedule=single_schedule,
    )
    disp_headers = display["headers"]
    disp_rows = display["rows"]
    layout = display["page_layout"]
    html_cells = display.get("html_cells", False)
    qty_i = display.get("qty_idx")
    amt_i = display.get("amt_idx")
    t_qty = display.get("total_qty")
    t_amt = display.get("total_amount")

    landscape = layout == SCHEDULE_LAYOUT_LANDSCAPE
    page_css = "A4 landscape" if landscape else "A4 portrait"
    body_fs = "9pt" if landscape else "9pt"
    table_fs = "8pt" if landscape else "8pt"

    th_parts = ['<th class="c sr">Sr</th>']
    for h in disp_headers:
        css = _header_col_class(h)
        cls = f' class="{css}"' if css else ""
        th_parts.append(f"<th{cls}>{_esc_html(h)}</th>")
    th_cells = "".join(th_parts)

    row_html = ""
    for i, row in enumerate(disp_rows, 1):
        cells = ""
        for j, cell in enumerate(row):
            inner = cell if html_cells else _esc_html(cell)
            hname = disp_headers[j] if j < len(disp_headers) else ""
            css = _header_col_class(hname)
            cls = f' class="{css}"' if css else ""
            cells += f"<td{cls}>{inner}</td>"
        row_html += f'<tr><td class="c sr">{i}</td>{cells}</tr>'

    foot_cols = len(disp_headers) + 1
    foot_left = max(1, foot_cols - 1)
    tfoot = ""
    if landscape and (qty_i is not None or amt_i is not None):
        foot_left = max(1, foot_cols - 3)
        tfoot = f"""<tfoot><tr>
  <td colspan="{foot_left}" class="r">Total</td>
  {'<td class="c">' + _esc_html(str(t_qty or 0)) + '</td>' if qty_i is not None else ''}
  {'<td></td>' if qty_i is not None and amt_i is not None else ''}
  {'<td class="r">' + _esc_html(f"{float(t_amt or 0):.2f}") + '</td>' if amt_i is not None else ''}
</tr></tfoot>"""
    elif qty_i is not None:
        tfoot = f"""<tfoot><tr>
  <td colspan="{foot_left}" class="r">Total</td>
  <td class="col-qty c">{_esc_html(str(t_qty or 0))}</td>
</tr></tfoot>"""

    # Landscape is the flat table with every column: its widths have to add up.
    landscape_widths = (
        ".sr{width:3%}.col-date{width:7%}.col-bill{width:6%}.col-cust{width:11%}"
        ".col-doc{width:11%}.col-med{width:14%}.col-content{width:14%}.col-batch{width:8%}"
        ".col-sch{width:5%}.col-exp{width:7%}.col-qty{width:4%}.col-amt{width:5%}"
        "td.col-bill,td.col-date,td.col-exp,td.col-amt{white-space:nowrap}"
        if landscape else ""
    )
    layout_label = SCHEDULE_LAYOUT_LABELS.get(layout, layout)
    if single_schedule:
        subtitle = f'Period: <b>{_esc_html(date_range)}</b> | Records: <b>{len(disp_rows)}</b>'
    else:
        subtitle = (
            f'Schedule: <b>{_esc_html(sch_label)}</b> | '
            f'Period: <b>{_esc_html(date_range)}</b> | Records: <b>{len(disp_rows)}</b>'
        )
    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">
<title>{_esc_html(title)}</title>
<style>
@page{{size:{page_css};margin:8mm}}
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:'Courier New',Courier,monospace;font-size:{body_fs};color:#000;background:#fff}}
h2{{text-align:center;font-size:14pt;margin-bottom:2mm;color:#000;font-weight:bold}}
.subtitle{{text-align:center;font-size:9.5pt;color:#000;margin-bottom:3mm;font-weight:bold}}
.layout-tag{{text-align:center;font-size:7.5pt;color:#000;margin-bottom:2mm}}
table{{width:100%;border-collapse:collapse;font-size:{table_fs};table-layout:fixed}}
thead th{{background:#fff;color:#000;padding:1.2mm 1mm;text-align:center;border:0.5pt solid #000;font-size:{table_fs};font-weight:bold;word-wrap:break-word}}
tbody td{{padding:1.2mm 1mm;border:0.5pt solid #000;vertical-align:top;word-wrap:break-word;overflow-wrap:break-word;line-height:1.3;color:#000;background:#fff}}
tfoot td{{padding:1.5mm 1mm;border:0.5pt solid #000;font-weight:bold;font-size:{table_fs};color:#000;background:#fff}}
.c{{text-align:center}}.r{{text-align:right}}
.sr{{width:4%}}
.col-date{{width:9%}}
.col-cust{{width:14%}}
.col-doc{{width:14%;padding-right:2mm}}
.col-med{{width:20%;padding-left:1.5mm}}
.col-batch{{width:9%}}
.col-exp{{width:7%}}
.col-sch{{width:5%}}
.col-qty{{width:4%;max-width:10mm;padding-left:0.5mm;padding-right:0.5mm}}
.col-bill{{width:7%}}
.col-content{{width:16%}}
.col-amt{{width:6%}}
{landscape_widths}
.sub{{font-size:7pt;color:#000;display:block;margin-top:0.5mm}}
.muted{{color:#000}}
</style></head><body>
<h2>{_esc_html(title)}</h2>
<div class="subtitle">{subtitle}</div>
<div class="layout-tag">{_esc_html(layout_label)}</div>
<table>
<thead><tr>{th_cells}</tr></thead>
<tbody>{row_html}</tbody>
{tfoot}
</table>
</body></html>"""


def save_schedule_report_document(
    html: str,
    default_name: str,
    *,
    page_layout: str = SCHEDULE_LAYOUT_PORTRAIT,
) -> tuple[str | None, str]:
    """Save schedule report PDF/HTML under Documents save folder → reports/."""
    from core.bill_save_prefs import resolve_sales_report_save_dir

    save_dir = resolve_sales_report_save_dir()
    base_path = os.path.join(save_dir, _safe_doc_filename(default_name))
    w_mm, h_mm = _schedule_paper_mm(page_layout)
    return save_html_as_pdf(
        html,
        base_path,
        paper_width_mm=w_mm,
        paper_height_mm=h_mm,
    )


def schedule_report_plain_from_display(display: dict) -> dict:
    """Plain ASCII rows for dot matrix — strips HTML from portrait display cells."""
    import html as html_module
    import re

    def _strip_cell(cell: str) -> str:
        s = str(cell or "")
        s = re.sub(r"<br\s*/?>", "\n", s, flags=re.I)
        s = re.sub(r"<[^>]+>", "", s)
        s = html_module.unescape(s)
        lines = [ln.strip() for ln in s.split("\n") if ln.strip()]
        if not lines:
            return "—"
        return "\n".join(lines[:2])

    rows = [[_strip_cell(c) for c in row] for row in (display.get("rows") or [])]
    return {
        "headers": list(display.get("headers") or []),
        "rows": rows,
        "qty_idx": display.get("qty_idx"),
        "total_qty": display.get("total_qty"),
        "page_layout": display.get("page_layout"),
    }


def _schedule_report_save_message(
    pdf_path: str | None,
    html_path: str,
    save_dir: str,
) -> str:
    folder = os.path.abspath(save_dir)
    if pdf_path and os.path.isfile(pdf_path):
        return (
            f"Reports folder:\n{folder}\n\n"
            f"PDF file:\n{os.path.abspath(pdf_path)}"
        )
    return (
        f"Reports folder:\n{folder}\n\n"
        f"PDF could not be created (install Microsoft Edge or Google Chrome).\n"
        f"HTML saved instead:\n{os.path.abspath(html_path)}"
    )


def offer_schedule_report_saved(
    parent,
    pdf_path: str | None,
    html_path: str,
    save_dir: str,
    *,
    title: str = "Report saved",
) -> None:
    """After save — offer to open the file or the reports folder."""
    from core.themed_messagebox import _show

    msg = _schedule_report_save_message(pdf_path, html_path, save_dir)
    target = pdf_path if pdf_path and os.path.isfile(pdf_path) else html_path
    choice = _show(
        parent,
        title,
        msg,
        "info",
        [
            ("Open file", "file", "primary"),
            ("Open folder", "folder", "secondary"),
            ("Close", "close", "secondary"),
        ],
    )
    if choice == "file" and target:
        open_file(target)
    elif choice == "folder":
        open_file(os.path.abspath(save_dir))


def deliver_schedule_report_pdf(
    parent,
    html: str,
    default_name: str,
    *,
    do_print: bool = False,
    page_layout: str = SCHEDULE_LAYOUT_PORTRAIT,
    dot_matrix_print=None,
) -> str | None:
    """Save PDF/HTML to documents/reports first; print via dot matrix RAW or PDF spooler."""
    from core.bill_save_prefs import resolve_sales_report_save_dir

    save_dir = resolve_sales_report_save_dir()
    pdf_path, html_path = save_schedule_report_document(
        html, default_name, page_layout=page_layout,
    )
    target = pdf_path or html_path
    if not target or not os.path.isfile(target):
        messagebox.showwarning(
            "Report not saved",
            "Could not save report.\n\n"
            f"Expected folder:\n{save_dir}\n\n"
            "Install Microsoft Edge or Google Chrome and try again.",
            parent=parent,
        )
        return None

    save_note = _schedule_report_save_message(pdf_path, html_path, save_dir)
    if do_print:
        printed = False
        if callable(dot_matrix_print):
            try:
                dot_matrix_print()
                printed = True
            except Exception as exc:
                from core.themed_messagebox import showwarning
                showwarning(
                    "Dot matrix print",
                    f"RAW print failed ({exc}).\nTrying PDF print instead.",
                    parent=parent,
                )
        if not printed:
            if pdf_path and os.name == "nt":
                try:
                    os.startfile(pdf_path, "print")
                    printed = True
                except Exception:
                    open_file(pdf_path)
            else:
                open_file(target)
        from core.themed_messagebox import showinfo
        showinfo(
            "Printed",
            f"{save_note}\n\nReport sent to printer.",
            parent=parent,
        )
    else:
        offer_schedule_report_saved(
            parent, pdf_path, html_path, save_dir, title="Report saved",
        )
    return target


def offer_purchase_return_document(parent, conn, return_id: int) -> None:
    cur = conn.cursor()
    cur.execute("SELECT return_no FROM purchase_returns WHERE id=?", (int(return_id),))
    row = cur.fetchone()
    if not row:
        raise ValueError(f"Purchase return {return_id} not found")
    html = render_purchase_return_html(conn, int(return_id))
    offer_supplier_document(parent, html, row[0])


def offer_reorder_document(parent, conn, group_id, default_name: str) -> None:
    html = render_reorder_order_html(conn, group_id)
    offer_supplier_document(parent, html, default_name)
