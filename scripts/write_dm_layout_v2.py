from pathlib import Path

CONTENT = r'''"""RAW ESC/P text printing for 9-pin dot matrix — classic A6 boxed layout."""
from __future__ import annotations

import sys
import textwrap
from typing import Any

from core.bill_render_utils import bill_due_display, fmt_expiry_mm_yy, total_due_display

# User dot-matrix template width (3-col header + 7-col table + 2-col footer)
LINE_WIDTH = 110
HDR_LEFT_W = 37
HDR_CENTER_W = 36
HDR_RIGHT_W = 35
COL_WIDTHS = (4, 38, 12, 6, 5, 10, 27)
COL_ALIGNS = ("c", "l", "c", "c", "c", "c", "l")
FOOT_LEFT_W = 77
FOOT_RIGHT_W = 31
A6_TABLE_ROWS = 6
_BOLD_ON = "---BOLD---"
_BOLD_OFF = "---NOBOLD---"


def _resolve_paper(settings: dict | None) -> str:
    settings = settings or {}
    hint = str(settings.get("print_paper_hint") or "").strip().upper()
    if hint in ("A4", "A5", "A6"):
        return hint
    paper = str(settings.get("paper_size") or "A5").strip().upper()
    return paper if paper in ("A4", "A5", "A6") else "A5"


def _table_pad_rows(settings: dict) -> int:
    try:
        n = int(settings.get("items_per_bill_page") or 12)
    except (TypeError, ValueError):
        n = 12
    n = max(1, min(50, n))
    if _resolve_paper(settings) == "A6":
        return min(n, A6_TABLE_ROWS)
    return n


class DotMatrixPrintError(Exception):
    """Dot matrix RAW print failed."""


def _ascii_safe(text: Any) -> str:
    raw = str(text or "")
    raw = raw.replace("\u20b9", "Rs.")
    return raw.encode("ascii", errors="replace").decode("ascii")


def _setting(settings: dict, key: str, default: bool = True) -> bool:
    return bool(settings.get(key, default))


def _label(settings: dict, key: str, default: str) -> str:
    return _ascii_safe((settings.get(key) or default).strip() or default)


def _is_tax_invoice(settings: dict) -> bool:
    return (settings.get("template") or "classic").lower() == "legacy"


def _show_gst_details(settings: dict) -> bool:
    if _is_tax_invoice(settings):
        return False
    return _setting(settings, "show_gst", True)


def _bold(text: str) -> str:
    return f"{_BOLD_ON}{_ascii_safe(text)}{_BOLD_OFF}"


def _center(text: str, width: int) -> str:
    return _ascii_safe(text)[:width].center(width)


def _meta_row(label: str, value: str, label_w: int = 9, width: int = HDR_RIGHT_W) -> str:
    lbl = _ascii_safe(label)
    val = _ascii_safe(value)
    return f"{lbl:<{label_w}}: {val}"[:width]


def _cell(text: str, width: int, align: str = "l") -> str:
    s = _ascii_safe(text)[:width]
    if align == "r":
        return s.rjust(width)
    if align == "c":
        return s.center(width)
    return s.ljust(width)


def _border_line() -> str:
    return "+" + "-" * (LINE_WIDTH - 2) + "+"


def _foot_hbar() -> str:
    return "+" + "-" * FOOT_LEFT_W + "+" + "-" * FOOT_RIGHT_W + "+"


def _three_col_row(left: str, center: str, right: str) -> str:
    l = left[:HDR_LEFT_W].ljust(HDR_LEFT_W)
    c = center[:HDR_CENTER_W].ljust(HDR_CENTER_W)
    r = right[:HDR_RIGHT_W].ljust(HDR_RIGHT_W)
    return f"|{l}{c}{r}|"


def _foot_row(left: str, right: str) -> str:
    l = left[:FOOT_LEFT_W].ljust(FOOT_LEFT_W)
    r = right[:FOOT_RIGHT_W].ljust(FOOT_RIGHT_W)
    return f"|{l}|{r}|"


def _table_sep_dynamic(widths: tuple[int, ...]) -> str:
    return "+" + "+".join("-" * w for w in widths) + "+"


def _table_row_dynamic(cells: list[str], widths: tuple[int, ...], aligns: tuple[str, ...]) -> str:
    parts = [_cell(c, w, a) for c, w, a in zip(cells, widths, aligns)]
    return "|" + "|".join(parts) + "|"


def _invoice_title(ctx, settings: dict) -> str:
    doc_title = (getattr(ctx, "document_title", None) or settings.get("document_title") or "").strip()
    if doc_title:
        return _ascii_safe(doc_title)
    return "TAX INVOICE" if _is_tax_invoice(settings) else "GST INVOICE"


def _medicine_columns(settings: dict) -> list[tuple[str, str, str, int]]:
    mrp_hdr = _label(settings, "medicine_mrp_header", "MRP")
    amt_hdr = _label(settings, "medicine_amount_header", "Amount")
    keys = [
        ("sr", "Sr.N", "c", COL_WIDTHS[0]),
        ("name", "Name of Medicine", "l", COL_WIDTHS[1]),
        ("batch", "Batch No", "c", COL_WIDTHS[2]),
        ("expiry", "Exp", "c", COL_WIDTHS[3]),
        ("qty", "Qty", "c", COL_WIDTHS[4]),
        ("mrp", mrp_hdr, "c", COL_WIDTHS[5]),
        ("amount", amt_hdr, "l", COL_WIDTHS[6]),
    ]
    show_map = {
        "sr": "show_sr_no",
        "name": "show_medicine_name",
        "batch": "show_batch",
        "expiry": "show_expiry",
        "qty": "show_qty",
        "mrp": "show_mrp",
        "amount": "show_line_amount",
    }
    cols: list[tuple[str, str, str, int]] = []
    for key, hdr, align, width in keys:
        if _setting(settings, show_map[key], True):
            cols.append((key, hdr, align, width))
    if not cols:
        cols = [("name", "Name of Medicine", "l", LINE_WIDTH - 10)]
    return cols


def _active_col_layout(settings: dict) -> tuple[tuple[int, ...], tuple[str, ...], list[tuple[str, str, str, int]]]:
    cols = _medicine_columns(settings)
    widths = tuple(c[3] for c in cols)
    aligns = tuple(c[2] for c in cols)
    return widths, aligns, cols


def _build_header_left(ctx, settings: dict) -> list[str]:
    lines: list[str] = []
    if _setting(settings, "show_store_name", True) and ctx.store_name:
        lines.append(_bold(_ascii_safe(ctx.store_name).upper()))
    if _setting(settings, "show_store_address", True) and ctx.address:
        for chunk in _ascii_safe(ctx.address).splitlines():
            chunk = chunk.strip()
            if chunk:
                lines.append(chunk.title()[:HDR_LEFT_W])
    if _setting(settings, "show_store_phone", True) and ctx.phone:
        lines.append(f"Phone : {ctx.phone}"[:HDR_LEFT_W])
    return lines


def _build_header_center(ctx, settings: dict) -> list[str]:
    lines: list[str] = []
    if _setting(settings, "show_blessing", True) and ctx.blessing_line:
        lines.append(_center(ctx.blessing_line, HDR_CENTER_W))
    lines.append(_center(_bold(_invoice_title(ctx, settings)), HDR_CENTER_W))
    return lines


def _build_header_right(ctx, settings: dict) -> list[str]:
    rows: list[str] = []
    bill_no_l = _label(settings, "meta_bill_no_label", "BILL NO.")
    date_l = _label(settings, "meta_date_label", "Date")
    party_l = _label(settings, "meta_party_name_label", "Pt.NAME")
    addr_l = _label(settings, "meta_party_address_label", "Pt.ADD.")

    if _setting(settings, "show_bill_no", True) and ctx.bill_no:
        rows.append(_meta_row(bill_no_l, ctx.bill_no))
    if _setting(settings, "show_bill_date", True):
        bill_date = getattr(ctx, "bill_date_landscape", None) or ctx.bill_date
        if bill_date:
            rows.append(_meta_row(date_l, bill_date))

    if not settings.get("hide_party_meta"):
        ref_line = (getattr(ctx, "reference_line", None) or "").strip()
        if ref_line:
            rows.append(_meta_row("Ref.", ref_line))
        if _setting(settings, "show_patient_name", True) and ctx.cust_name:
            rows.append(_meta_row(party_l, ctx.cust_name))
        if _setting(settings, "show_patient_address", True) and ctx.cust_addr:
            rows.append(_meta_row(addr_l, ctx.cust_addr))
        for label, value in getattr(ctx, "extra_meta_rows", None) or []:
            if str(value or "").strip():
                rows.append(_meta_row(str(label), str(value)))

    if _setting(settings, "show_doctor", True) and _setting(settings, "show_doctor_name", True) and ctx.doctor_name:
        rows.append(_meta_row("Dr.NAME", ctx.doctor_name))
    if _setting(settings, "show_doctor", True) and _setting(settings, "show_doctor_reg", True) and ctx.doctor_reg:
        rows.append(_meta_row("Dr.Reg.", ctx.doctor_reg))
    return rows


def _merge_header(left: list[str], center: list[str], right: list[str]) -> list[str]:
    height = max(len(left), len(center), len(right), 1)
    out: list[str] = [_border_line()]
    for i in range(height):
        l = left[i] if i < len(left) else ""
        c = center[i] if i < len(center) else ""
        r = right[i] if i < len(right) else ""
        out.append(_three_col_row(l, c, r))
    return out


def _item_cells(item, sr: int, settings: dict) -> dict[str, str]:
    mrp_hdr = (settings.get("medicine_mrp_header") or "MRP").strip().upper()
    use_rate = mrp_hdr == "RATE"
    mrp_val = float(item.rate or item.mrp or 0) if use_rate else float(item.mrp or item.rate or 0)
    qty = float(item.qty or 0)
    qty_s = str(int(qty)) if qty == int(qty) else f"{qty:.2f}"
    return {
        "sr": str(sr),
        "name": _ascii_safe(item.name).upper(),
        "batch": _ascii_safe(item.batch),
        "expiry": fmt_expiry_mm_yy(item.expiry) if item.expiry else "",
        "qty": qty_s,
        "mrp": f"{mrp_val:.2f}",
        "amount": f"{float(item.amount or 0):.2f}",
    }


def _build_table(ctx, settings: dict) -> list[str]:
    widths, aligns, cols = _active_col_layout(settings)
    out: list[str] = []
    header = [hdr for _k, hdr, _a, _w in cols]
    out.append(_table_sep_dynamic(widths))
    out.append(_table_row_dynamic(header, widths, aligns))
    out.append(_table_sep_dynamic(widths))

    sr_offset = int(getattr(ctx, "item_sr_offset", 0) or 0)
    item_rows: list[list[str]] = []
    for idx, item in enumerate(ctx.items or []):
        cells = _item_cells(item, sr_offset + idx + 1, settings)
        row = [cells[key] for key, _h, _a, _w in cols]
        item_rows.append(row)
        name_w = next((w for k, _h, _a, w in cols if k == "name"), 0)
        if name_w and len(cells["name"]) > name_w:
            for extra in textwrap.wrap(cells["name"][name_w:], width=name_w) or []:
                extra_row = [""] * len(cols)
                name_idx = next(i for i, c in enumerate(cols) if c[0] == "name")
                extra_row[name_idx] = extra
                item_rows.append(extra_row)

    min_rows = _table_pad_rows(settings)
    while len(item_rows) < min_rows:
        item_rows.append([""] * len(cols))

    for row in item_rows:
        out.append(_table_row_dynamic(row, widths, aligns))
    out.append(_table_sep_dynamic(widths))
    return out


def _footer_strip_line(ctx, settings: dict) -> str:
    custom = (getattr(ctx, "footer_strip_line", None) or settings.get("footer_strip_line") or "").strip()
    if custom:
        return _ascii_safe(custom)
    nice = _label(settings, "gst_day_line", "HAVE A NICE DAY")
    if not _show_gst_details(settings) or not ctx.gst_enabled or float(ctx.gst_amount or 0) <= 0:
        return nice
    taxable = max(0.0, round(float(ctx.grand_total or 0) - float(ctx.gst_amount or 0), 2))
    rates = sorted({float(item.gst_percent or 0) for item in ctx.items if item.gst_percent})
    half_tax = round(float(ctx.gst_amount or 0) / 2, 2)
    if len(rates) == 1 and rates[0] > 0:
        half_rate = rates[0] / 2
        gst_part = (
            f"GST {taxable:.2f}*{half_rate:g}+{half_rate:g}%="
            f"{half_tax:.2f}SGST+{half_tax:.2f}CGST"
        )
    else:
        gst_part = f"GST {taxable:.2f} = {half_tax:.2f}SGST + {half_tax:.2f}CGST"
    return f"{gst_part}, {nice}"[:FOOT_LEFT_W]


def _totals_lines(ctx, settings: dict) -> list[str]:
    continued = bool(getattr(ctx, "is_continued", False))
    lines: list[str] = []
    disc_l = _label(settings, "discount_label", "DISC")
    total_l = _label(settings, "total_label", "Total")

    def _amt_row(label: str, amount: float) -> str:
        amt = f"{float(amount):.2f}"
        gap = FOOT_RIGHT_W - len(amt)
        lbl = _ascii_safe(label)[: max(1, gap - 1)]
        return f"{lbl:<{gap}}{amt}"

    if continued:
        page_amt = float(getattr(ctx, "page_subtotal", 0) or 0)
        lines.append(_amt_row("Continued...", page_amt))
        return lines

    if _setting(settings, "show_discount", True) and float(ctx.discount or 0) > 0:
        lines.append(_amt_row(disc_l, float(ctx.discount)))
    if _show_gst_details(settings) and ctx.gst_enabled and float(ctx.gst_amount or 0) > 0:
        lines.append(_amt_row("GST", float(ctx.gst_amount)))
    if abs(float(ctx.rounding or 0)) >= 0.01:
        lines.append(_amt_row("Rounding", float(ctx.rounding)))
    if _setting(settings, "show_total", True):
        lines.append(_amt_row(total_l, float(ctx.grand_total or 0)))
    if _setting(settings, "show_customer_due", False):
        prev = float(getattr(ctx, "previous_due", 0) or 0)
        bill_due = bill_due_display(ctx)
        total_due = total_due_display(ctx)
        if prev > 0:
            lines.append(_amt_row("Prev Due", prev))
        if bill_due > 0:
            lines.append(_amt_row("Bill Due", bill_due))
        if total_due > 0:
            lines.append(_amt_row("Total Due", total_due))
    return lines or [_amt_row(total_l, float(ctx.grand_total or 0))]


def _footer_left_lines(ctx, settings: dict) -> list[str]:
    lines: list[str] = []
    if _setting(settings, "show_recovery_wish", True):
        wish = (
            getattr(ctx, "recovery_wish_line", None)
            or settings.get("recovery_wish_line", "I WISH FOR YOUR SPEEDY RECOVERY.")
        )
        text = _ascii_safe(wish).strip()
        if text:
            wrapped = textwrap.wrap(text, width=FOOT_LEFT_W) or [text[:FOOT_LEFT_W]]
            if _resolve_paper(settings) == "A6":
                wrapped = wrapped[:2]
            lines.extend(wrapped)
    if _setting(settings, "show_store_dl", True) and ctx.dl_no:
        lines.append(f"DL No. : {ctx.dl_no}"[:FOOT_LEFT_W])
    if _setting(settings, "show_store_phone", True) and ctx.phone:
        lines.append(f"Phone  : {ctx.phone}"[:FOOT_LEFT_W])
    return lines or [""]


def _signature_lines(ctx, settings: dict) -> list[str]:
    if not _setting(settings, "show_signature", True) or not ctx.store_name:
        return ["", "", ""]
    sign_l = _label(settings, "signature_caption", "SIGN OF Q.P.")
    for_line = f"For {_ascii_safe(ctx.store_name).upper()}"
    return ["", for_line[:FOOT_RIGHT_W], _center(sign_l, FOOT_RIGHT_W)]


def _build_footer(ctx, settings: dict) -> list[str]:
    continued = bool(getattr(ctx, "is_continued", False))
    out: list[str] = []

    if not continued and _setting(settings, "show_gst_strip", True):
        strip = _footer_strip_line(ctx, settings)
        totals = _totals_lines(ctx, settings)
        out.append(_foot_hbar())
        max_h = max(1, len(totals))
        for i in range(max_h):
            left = strip if i == 0 else ""
            right = totals[i] if i < len(totals) else ""
            out.append(_foot_row(left, right))
    elif continued:
        totals = _totals_lines(ctx, settings)
        out.append(_foot_hbar())
        for row in totals:
            out.append(_foot_row("", row))

    if not continued:
        left_lines = _footer_left_lines(ctx, settings)
        sig_lines = _signature_lines(ctx, settings)
        out.append(_foot_hbar())
        height = max(len(left_lines), len(sig_lines), 4)
        for i in range(height):
            left = left_lines[i] if i < len(left_lines) else ""
            right = sig_lines[i] if i < len(sig_lines) else ""
            out.append(_foot_row(left, right))

    out.append(_border_line())
    return out


def format_bill_text(ctx, settings: dict | None = None) -> str:
    """Plain-text bill — 3-column header, wide table, footer with DL/phone."""
    settings = dict(settings or {})
    lines: list[str] = []
    lines.extend(_merge_header(
        _build_header_left(ctx, settings),
        _build_header_center(ctx, settings),
        _build_header_right(ctx, settings),
    ))
    lines.extend(_build_table(ctx, settings))
    lines.extend(_build_footer(ctx, settings))
    return "\n".join(lines)


def _encode_line_with_bold(line: str) -> bytes:
    out = bytearray()
    chunk = line
    while chunk:
        start = chunk.find(_BOLD_ON)
        if start < 0:
            out += chunk.encode("cp437", errors="replace")
            break
        out += chunk[:start].encode("cp437", errors="replace")
        chunk = chunk[start + len(_BOLD_ON):]
        end = chunk.find(_BOLD_OFF)
        if end < 0:
            out += b"\x1bE"
            out += chunk.encode("cp437", errors="replace")
            break
        out += b"\x1bE"
        out += chunk[:end].encode("cp437", errors="replace")
        out += b"\x1bF"
        chunk = chunk[end + len(_BOLD_OFF):]
    return bytes(out)


def render_escp_document(text: str, *, paper: str = "A5") -> bytes:
    paper_u = (paper or "A5").upper()
    a6 = paper_u == "A6"
    out = bytearray()
    out += b"\x1b@"
    out += b"\x1bx\x01"
    if a6:
        out += b"\x0f"
        out += b"\x1b3\x14"
    else:
        out += b"\x1bP"
        out += b"\x1b2"
    for line in text.splitlines():
        if line == _BOLD_ON:
            out += b"\x1bE"
            continue
        if line == _BOLD_OFF:
            out += b"\x1bF"
            continue
        out += _encode_line_with_bold(line)
        out += b"\r\n"
    out += b"\x1bF"
    if a6:
        out += b"\x12"
    out += b"\x1bJ\x18"
    return bytes(out)


def print_dot_matrix_bill(
    ctx,
    settings: dict | None,
    printer_name: str | None,
    *,
    copies: int = 1,
) -> None:
    if not sys.platform.startswith("win"):
        raise DotMatrixPrintError(
            "Dot matrix RAW printing is only implemented on Windows (win32print)."
        )
    text = format_bill_text(ctx, settings)
    paper = _resolve_paper(settings)
    try:
        from core.bill_config import get_bill_size_pct
        scale_pct = get_bill_size_pct(settings)
    except Exception:
        scale_pct = 92.0
    payload = render_escp_document(text, paper=paper)
    try:
        from core.print_log import print_log
        print_log(
            f"format_bill_text lines={len(text.splitlines())} payload_bytes={len(payload)} "
            f'paper={paper} scale_pct={scale_pct} printer="{printer_name}" copies={copies}'
        )
    except Exception:
        pass
    from core.printer_manager import PrinterError, PrinterManager

    printer = PrinterManager.resolve_dot_matrix_printer(printer_name)
    driver = PrinterManager.get_printer_driver_name(printer)
    errors: list[str] = []

    def _log(msg: str, level: str = "INFO") -> None:
        try:
            from core.print_log import print_log
            print_log(msg, level=level)
        except Exception:
            pass

    _log(f'dot_matrix target="{printer}" driver="{driver}"')

    if not PrinterManager.should_use_dot_matrix_gdi(printer):
        try:
            _log(f'dot_matrix trying RAW ESC/P on "{printer}"')
            PrinterManager.print_raw_escp(payload, printer, copies=copies)
            return
        except PrinterError as exc:
            errors.append(f"RAW ESC/P: {exc}")
            _log(f"RAW failed: {exc}", level="WARN")

    try:
        _log(f'dot_matrix trying GDI text on "{printer}" paper={paper}')
        PrinterManager.print_text_gdi(
            text, printer, copies=copies, paper_size=paper, scale_pct=scale_pct,
        )
        return
    except PrinterError as exc:
        errors.append(f"GDI text: {exc}")
        _log(f"GDI failed: {exc}", level="WARN")

    if PrinterManager.should_use_dot_matrix_gdi(printer):
        try:
            _log(f'dot_matrix trying RAW fallback on "{printer}"')
            PrinterManager.print_raw_escp(payload, printer, copies=copies)
            return
        except PrinterError as exc:
            errors.append(f"RAW fallback: {exc}")

    hint = (
        'Use printer queue "EPSON LX-310 ESC/P" (not the Class Driver queue). '
        "Settings -> Printer Setup -> Print Sales 1 = EPSON LX-310 ESC/P."
    )
    raise DotMatrixPrintError(
        "Dot matrix print failed on all methods:\n- " + "\n- ".join(errors) + f"\n\n{hint}"
    )


def format_test_page(printer_name: str) -> str:
    class _Item:
        def __init__(self, name, batch, expiry, qty, mrp, amount):
            self.name = name
            self.batch = batch
            self.expiry = expiry
            self.qty = qty
            self.mrp = mrp
            self.rate = mrp
            self.amount = amount
            self.gst_percent = 0

    class _Ctx:
        store_name = "ROSHAN MEDICAL"
        address = "Wadshingi"
        phone = "9325485954"
        email = ""
        gstin = ""
        dl_no = "20-MH-EUL-640037"
        fssai = ""
        show_fssai_on_bill = False
        blessing_line = "SHREE GANESHAY NAMAH"
        bill_no = "SCB67"
        bill_date = "12/07/26"
        bill_date_landscape = "12/07/26"
        cust_name = "HARSHAL MHASAL"
        cust_addr = "KHERDA"
        doctor_name = "DR BHELKE SIR"
        doctor_reg = "8887454"
        items = [
            _Item("AEROCORT ROTACAPS 60'S", "5SA2441", "04/27", 2, 142.67, 285.34),
            _Item("DOLO 650 TABLET", "A123456", "08/27", 5, 34.50, 172.50),
            _Item("AZEE 500", "B554422", "09/27", 1, 120.00, 120.00),
        ]
        grand_total = 577.84
        discount = 0
        gst_amount = 0
        gst_enabled = False
        rounding = 0
        recovery_wish_line = "I WISH FOR YOUR SPEEDY RECOVERY."
        is_continued = False

    settings = {
        "paper_size": "A6",
        "show_store_name": True,
        "show_store_address": True,
        "show_store_phone": True,
        "show_store_dl": True,
        "show_blessing": True,
        "show_bill_no": True,
        "show_bill_date": True,
        "show_patient_name": True,
        "show_patient_address": True,
        "show_doctor": True,
        "show_doctor_name": True,
        "show_doctor_reg": True,
        "show_sr_no": True,
        "show_medicine_name": True,
        "show_batch": True,
        "show_expiry": True,
        "show_qty": True,
        "show_mrp": True,
        "show_line_amount": True,
        "show_gst_strip": True,
        "show_total": True,
        "show_recovery_wish": True,
        "show_signature": True,
        "items_per_bill_page": 12,
        "gst_day_line": "HAVE A NICE DAY",
    }
    return format_bill_text(_Ctx(), settings)
'''

p = Path('core/dot_matrix_print.py')
p.write_text(CONTENT, encoding='utf-8', newline='\n')
print('written', p.read_bytes().count(b'\x00'), 'nulls')
