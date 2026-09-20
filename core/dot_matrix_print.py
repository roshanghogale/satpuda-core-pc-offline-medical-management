"""RAW ESC/P text printing for 9-pin dot matrix — classic A6 boxed layout."""
from __future__ import annotations

import sys
import textwrap
from dataclasses import dataclass, replace
from typing import Any

from core.bill_gst import gst_strip_figures
from core.bill_render_utils import bill_due_display, fmt_expiry_mm_yy, total_due_display

_BILL_WRAP_KEYS = frozenset({"name", "batch"})
_BILL_FIRST_LINE_ONLY = frozenset({"sr", "expiry", "qty", "mrp", "amount"})
A6_TABLE_ROWS = 6
_BOLD_ON = "---BOLD---"
_BOLD_OFF = "---NOBOLD---"
_TITLE_EM_ON = "---TITLE---"
_TITLE_EM_OFF = "---/TITLE---"
_PAGE_BREAK = "---PAGEBREAK---"
_SIGN_ROWS_PER_PAGE = 10
_DM_SETTINGS_KEY = "_dm_layout"


@dataclass(frozen=True)
class DmLayout:
    line_width: int
    hdr_left_w: int
    hdr_center_w: int
    hdr_right_w: int
    col_widths: tuple[int, ...]
    foot_left_w: int
    foot_right_w: int
    escp_left_margin: int = 0
    escp_line_spacing: int = 30   # n/180 inch (ESC 3 n)
    use_condensed: bool = False
    use_12cpi: bool = False
    page_cols: int = 0            # physical paper width in chars (center content)
    two_col_header: bool = False  # PDF layout: shop left, invoice panel right
    slip_lines: int = 0           # fixed A6 slip height in lines (~10 cm printable)
    escp_top_reverse: int = 0     # ESC j n — reverse feed n/216" before print (~0.5 cm up)
    escp_backspaces: int = 0      # deprecated — do not use (causes horizontal restrike on some LX-310)
    escp_hpos_60ths: int = 0      # ESC $ absolute H-pos in 1/60" at each line start
    escp_page_lines: int = 0      # physical slip height in lines (ESC C); 0 = auto
    escp_form_feed_end: bool = False  # FF at end — eject slip on continuous roll
    preserve_leading_spaces: bool = False  # keep spaces for right-column layout (no | borders)
    escp_tear_feed_216: int = 0       # ESC J forward feed after print (total n/216 inch)
    escp_slip_216: int = 0            # exact slip pitch n/216 inch; >0 = advance exactly one slip per bill
    escp_left_cols: int = 0           # ESC l n left margin in columns of the current pitch (whole bill moves right)
    escp_tear_gap_216: int = 0        # print head -> tear edge, n/216 inch; pulled back before printing, given back after
    escp_tear_mode: str = "software"  # "software": the app ejects to the tear edge and pulls the same back
    escp_top_forward_216: int = 0     # ESC J n — top margin fed FORWARD from the perforation before the first line


# A5 / full-width continuous paper
_LAYOUT_A5 = DmLayout(
    line_width=110,
    hdr_left_w=37,
    hdr_center_w=36,
    hdr_right_w=35,
    col_widths=(4, 38, 12, 6, 5, 10, 27),
    foot_left_w=76,
    foot_right_w=31,
)

# A4 portrait — export reports (80 cols @ 10 CPI, no ASCII rules)
_LAYOUT_A4 = DmLayout(
    line_width=80,
    hdr_left_w=26,
    hdr_center_w=28,
    hdr_right_w=26,
    col_widths=(4, 28, 10, 6, 6, 10, 16),
    foot_left_w=52,
    foot_right_w=28,
    escp_left_margin=0,
    escp_line_spacing=30,
    use_condensed=False,
    use_12cpi=False,
    page_cols=0,
    two_col_header=False,
    slip_lines=0,
    escp_top_reverse=0,
    escp_backspaces=0,
    escp_hpos_60ths=0,
    escp_page_lines=0,
    escp_form_feed_end=True,
    preserve_leading_spaces=True,
)

# A6 on roll — printable 14 cm × 10 cm on 16 cm × 12 cm paper, landscape feed
# 12 CPI → 64 chars wide; ESC C page len = line count → one FF ejects one slip
_LAYOUT_A6 = DmLayout(
    line_width=64,
    hdr_left_w=29,
    hdr_center_w=0,
    hdr_right_w=33,
    col_widths=(3, 20, 8, 4, 4, 7, 10),
    foot_left_w=43,
    foot_right_w=18,
    escp_left_margin=0,
    escp_line_spacing=30,
    use_condensed=False,
    use_12cpi=True,
    page_cols=0,
    two_col_header=True,
    slip_lines=24,
    escp_top_reverse=0,
    escp_backspaces=0,
    escp_hpos_60ths=0,
    escp_page_lines=0,
    escp_form_feed_end=False,  # ESC C auto-feeds one slip; FF would eject a blank page
)

# Back-compat names used elsewhere
LINE_WIDTH = _LAYOUT_A5.line_width
COL_WIDTHS = _LAYOUT_A5.col_widths
FOOT_LEFT_W = _LAYOUT_A5.foot_left_w
FOOT_RIGHT_W = _LAYOUT_A5.foot_right_w


def _layout_for_paper(paper: str) -> DmLayout:
    p = (paper or "").upper()
    if p == "A6":
        return _LAYOUT_A6
    if p == "A4":
        return _LAYOUT_A4
    return _LAYOUT_A5


def _dm(settings: dict) -> DmLayout:
    layout = settings.get(_DM_SETTINGS_KEY)
    if isinstance(layout, DmLayout):
        return layout
    return _layout_for_paper(_resolve_paper(settings))


def _settings_with_layout(settings: dict | None) -> dict:
    merged = dict(settings or {})
    if not isinstance(merged.get(_DM_SETTINGS_KEY), DmLayout):
        layout = _layout_for_paper(_resolve_paper(merged))
        if not _vertical_borders(merged):
            layout = replace(layout, preserve_leading_spaces=True)
        merged[_DM_SETTINGS_KEY] = layout
    return merged


def _resolve_paper(settings: dict | None) -> str:
    settings = settings or {}
    hint = str(settings.get("print_paper_hint") or "").strip().upper()
    if hint in ("A4", "A5", "A6"):
        return hint
    paper = str(settings.get("paper_size") or "A5").strip().upper()
    return paper if paper in ("A4", "A5", "A6") else "A5"


def _table_pad_rows(settings: dict, item_count: int = 0) -> int:
    from core.bill_config import get_items_per_bill_page
    n = get_items_per_bill_page(settings, dot_matrix=True)
    if _resolve_paper(settings) == "A6":
        return max(int(item_count or 0), 1)
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
    # Same rule as the PDF bill (bill_templates/classic.py): a Tax Invoice shows its tax.
    return _setting(settings, "show_gst", True)


def _bold(text: str) -> str:
    return f"{_BOLD_ON}{_ascii_safe(text)}{_BOLD_OFF}"


def _center(text: str, width: int) -> str:
    return _ascii_safe(text)[:width].center(width)


def _meta_row(label: str, value: str, label_w: int = 9, width: int | None = None, *, settings: dict) -> str:
    lbl = _ascii_safe(label)
    val = _ascii_safe(value)
    w = width if width is not None else _dm(settings).hdr_right_w
    return f"{lbl:<{label_w}}: {val}"[:w]


def _cell(text: str, width: int, align: str = "l") -> str:
    s = _ascii_safe(text)[:width]
    if align == "r":
        return s.rjust(width)
    if align == "c":
        return s.center(width)
    return s.ljust(width)


def _vertical_borders(settings: dict) -> bool:
    return bool(settings.get("dot_matrix_vertical_borders", True))


def _col_gutter(settings: dict) -> str:
    """Space between columns when vertical | borders are off — same width as one |."""
    return " " if not _vertical_borders(settings) else ""


def _lr_zones_line(
    left: str,
    right: str,
    *,
    left_w: int,
    right_w: int,
    line_w: int,
    settings: dict,
    inner_pipe: bool = False,
) -> str:
    """Two zones on one row — right block stays in the right columns when | borders are off."""
    l = left[:left_w].ljust(left_w)
    r = right[:right_w].ljust(right_w)
    if _vertical_borders(settings):
        if inner_pipe:
            return f"|{l}|{r}|"
        return f"|{l}{r}|"
    gap = max(0, line_w - left_w - right_w)
    return (l + (" " * gap) + r)[:line_w]


def _border_line(settings: dict) -> str:
    dm = _dm(settings)
    inner = dm.line_width - 2 if _vertical_borders(settings) else dm.line_width
    if _vertical_borders(settings):
        return "+" + "-" * inner + "+"
    return "-" * dm.line_width


def _foot_hbar(settings: dict) -> str:
    dm = _dm(settings)
    if _vertical_borders(settings):
        return "+" + "-" * dm.foot_left_w + "+" + "-" * dm.foot_right_w + "+"
    return "-" * dm.line_width


def _plain_fit(text: str, width: int) -> str:
    """Fit visible text to a column — never truncate bold markers mid-tag."""
    s = str(text or "").replace(_BOLD_ON, "").replace(_BOLD_OFF, "")
    return s[:width].ljust(width)


def _three_col_row(left: str, center: str, right: str, settings: dict) -> str:
    dm = _dm(settings)
    l = _plain_fit(left, dm.hdr_left_w)
    c = _plain_fit(center, dm.hdr_center_w)
    r = _plain_fit(right, dm.hdr_right_w)
    if _vertical_borders(settings):
        return f"|{l}{c}{r}|"
    used = dm.hdr_left_w + dm.hdr_center_w + dm.hdr_right_w
    gap = max(0, dm.line_width - used)
    return (l + c + r + (" " * gap))[:dm.line_width]


def _foot_row(left: str, right: str, settings: dict) -> str:
    dm = _dm(settings)
    return _lr_zones_line(
        left, right,
        left_w=dm.foot_left_w,
        right_w=dm.foot_right_w,
        line_w=dm.line_width,
        settings=settings,
        inner_pipe=True,
    )


def _table_sep_dynamic(widths: tuple[int, ...], settings: dict) -> str:
    if _vertical_borders(settings):
        return "+" + "+".join("-" * w for w in widths) + "+"
    n = len(widths)
    gutter = len(_col_gutter(settings))
    total = sum(widths) + gutter * max(0, n - 1)
    return "-" * total


def _table_row_dynamic(
    cells: list[str],
    widths: tuple[int, ...],
    aligns: tuple[str, ...],
    settings: dict,
) -> str:
    parts = [_cell(c, w, a) for c, w, a in zip(cells, widths, aligns)]
    if _vertical_borders(settings):
        return "|" + "|".join(parts) + "|"
    return _col_gutter(settings).join(parts)


def _invoice_title(ctx, settings: dict) -> str:
    doc_title = (getattr(ctx, "document_title", None) or settings.get("document_title") or "").strip()
    if doc_title:
        return _ascii_safe(doc_title)
    return "TAX INVOICE" if _is_tax_invoice(settings) else "GST INVOICE"


def _medicine_columns(settings: dict) -> list[tuple[str, str, str, int]]:
    dm = _dm(settings)
    mrp_hdr = _label(settings, "medicine_mrp_header", "MRP")
    amt_hdr = _label(settings, "medicine_amount_header", "Amount")
    keys = [
        ("sr", "Sr.N", "c", dm.col_widths[0]),
        ("name", "Name of Medicine", "l", dm.col_widths[1]),
        ("batch", "Batch No", "c", dm.col_widths[2]),
        ("expiry", "Exp", "c", dm.col_widths[3]),
        ("qty", "Qty", "c", dm.col_widths[4]),
        ("mrp", mrp_hdr, "c", dm.col_widths[5]),
        ("amount", amt_hdr, "l", dm.col_widths[6]),
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
        cols = [("name", "Name of Medicine", "l", _dm(settings).line_width - 10)]
    return cols


def _active_col_layout(settings: dict) -> tuple[tuple[int, ...], tuple[str, ...], list[tuple[str, str, str, int]]]:
    cols = _medicine_columns(settings)
    widths = tuple(c[3] for c in cols)
    aligns = tuple(c[2] for c in cols)
    return widths, aligns, cols


def _build_header_left(ctx, settings: dict) -> list[str]:
    dm = _dm(settings)
    lines: list[str] = []
    if _setting(settings, "show_store_name", True) and ctx.store_name:
        lines.extend(_wrap_text(_ascii_safe(ctx.store_name).upper(), dm.hdr_left_w))
    if _setting(settings, "show_store_address", True) and ctx.address:
        for chunk in _ascii_safe(ctx.address).splitlines():
            chunk = chunk.strip()
            if chunk:
                lines.append(chunk.title()[:dm.hdr_left_w])
    if _setting(settings, "show_store_phone", True) and ctx.phone:
        lines.append(f"Phone : {ctx.phone}"[:dm.hdr_left_w])
    # "Store email" is honoured on the PDF bill (bill_templates/classic.py) and
    # was silently dropped here, so the same tick printed on one kind of bill
    # and not the other.
    email = (getattr(ctx, "email", None) or "").strip()
    if _setting(settings, "show_store_email", True) and email:
        # Wrapped, not sliced: the A6 left header is 29 columns and the label
        # eats nine of them, so a normal address would have been cut mid-word
        # with nothing to say it had been.
        for chunk in _wrap_text(f"E-Mail : {email}", dm.hdr_left_w):
            if chunk:
                lines.append(chunk)
    lines.extend(_store_license_left_lines(ctx, settings))
    return lines


def _store_license_left_lines(ctx, settings: dict) -> list[str]:
    """Pharmacy licence numbers — left header (store / medical info)."""
    dm = _dm(settings)
    lines: list[str] = []
    if _setting(settings, "show_store_gstin", True) and (getattr(ctx, "gstin", None) or "").strip():
        lines.append(f"GSTIN : {ctx.gstin}"[:dm.hdr_left_w])
    if _setting(settings, "show_store_dl", True) and (getattr(ctx, "dl_no", None) or "").strip():
        lines.append(f"DL No. : {ctx.dl_no}"[:dm.hdr_left_w])
    fssai = (getattr(ctx, "fssai", None) or "").strip()
    if (
        _setting(settings, "show_store_fssai", True)
        and getattr(ctx, "show_fssai_on_bill", False)
        and fssai
    ):
        lines.append(f"FSSAI : {fssai}"[:dm.hdr_left_w])
    return lines


def _two_col_row(left: str, right: str, settings: dict) -> str:
    dm = _dm(settings)
    l = _plain_fit(left, dm.hdr_left_w)
    r = _plain_fit(right, dm.hdr_right_w)
    return _lr_zones_line(
        l, r,
        left_w=dm.hdr_left_w,
        right_w=dm.hdr_right_w,
        line_w=dm.line_width,
        settings=settings,
        inner_pipe=False,
    )


def _build_header_right(ctx, settings: dict) -> list[str]:
    dm = _dm(settings)
    rows: list[str] = []
    # PDF invoice-panel: blessing + GST INVOICE at top, then bill meta below
    if _setting(settings, "show_blessing", True) and ctx.blessing_line:
        rows.append(_center(ctx.blessing_line, dm.hdr_right_w))
    rows.append(_center(_invoice_title(ctx, settings).upper(), dm.hdr_right_w))
    rows.append("")

    bill_no_l = _label(settings, "meta_bill_no_label", "BILL NO.")
    date_l = _label(settings, "meta_date_label", "Date")
    party_l = _label(settings, "meta_party_name_label", "Pt.NAME")
    addr_l = _label(settings, "meta_party_address_label", "Pt.ADD.")

    if _setting(settings, "show_bill_no", True) and ctx.bill_no:
        rows.append(_meta_row(bill_no_l, ctx.bill_no, settings=settings))
    if _setting(settings, "show_bill_date", True):
        bill_date = getattr(ctx, "bill_date_landscape", None) or ctx.bill_date
        if bill_date:
            rows.append(_meta_row(date_l, bill_date, settings=settings))

    if not settings.get("hide_party_meta"):
        ref_line = (getattr(ctx, "reference_line", None) or "").strip()
        if ref_line:
            rows.append(_meta_row("Ref.", ref_line, settings=settings))
        if _setting(settings, "show_patient_name", True) and ctx.cust_name:
            rows.append(_meta_row(party_l, ctx.cust_name, settings=settings))
        if _setting(settings, "show_patient_address", True) and ctx.cust_addr:
            rows.append(_meta_row(addr_l, ctx.cust_addr, settings=settings))
        for label, value in getattr(ctx, "extra_meta_rows", None) or []:
            if str(value or "").strip():
                rows.append(_meta_row(str(label), str(value), settings=settings))

    if _setting(settings, "show_doctor", True) and _setting(settings, "show_doctor_name", True) and ctx.doctor_name:
        rows.append(_meta_row("Dr.NAME", ctx.doctor_name, settings=settings))
    if _setting(settings, "show_doctor", True) and _setting(settings, "show_doctor_reg", True) and ctx.doctor_reg:
        rows.append(_meta_row("Dr.Reg.", ctx.doctor_reg, settings=settings))
    return rows


def _merge_header(left: list[str], center: list[str], right: list[str], settings: dict) -> list[str]:
    dm = _dm(settings)
    height = max(len(left), len(center), len(right), 1)
    out: list[str] = [_border_line(settings)]
    if dm.two_col_header:
        for i in range(height):
            l = left[i] if i < len(left) else ""
            r = right[i] if i < len(right) else ""
            out.append(_two_col_row(l, r, settings))
        return out
    for i in range(height):
        l = left[i] if i < len(left) else ""
        c = center[i] if i < len(center) else ""
        r = right[i] if i < len(right) else ""
        out.append(_three_col_row(l, c, r, settings))
    return out


def _settings_for_ctx(settings: dict | None, ctx) -> dict:
    """Per-page settings — A6 layout tightens when many items must fit one slip."""
    merged = _settings_with_layout(settings)
    if _resolve_paper(merged) != "A6":
        return merged
    merged = dict(merged)
    merged[_DM_SETTINGS_KEY] = _layout_a6_for_ctx(ctx, merged)
    return merged


def _dot_matrix_top_reverse_units(settings: dict) -> int:
    """ESC j n — reverse paper n/216 inch (~move print start up on slip)."""
    try:
        cm = float(settings.get("dot_matrix_top_offset_cm", 0.8) or 0)
    except (TypeError, ValueError):
        cm = 0.8
    cm = max(0.0, min(3.0, cm))
    return max(0, min(255, int(round(cm / 2.54 * 216))))


def _cm_setting(settings: dict, key: str, default: float, lo: float, hi: float) -> float:
    try:
        cm = float(settings.get(key, default) if settings.get(key) is not None else default)
    except (TypeError, ValueError):
        cm = default
    return max(lo, min(hi, cm))


def _units(cm: float) -> int:
    """cm -> 1/216 inch, the unit every 9-pin paper motion command uses."""
    return int(round(cm / 2.54 * 216))


def _a6_paper(settings: dict) -> dict:
    """The shop's slip, in numbers: height, margins and printable width.

    Measured on the paper itself, not guessed from "A6": the slips are 9 cm
    tall and 14.5 cm wide, and the tractor holes take 0.8 cm at each side.
    """
    slip_cm = _cm_setting(settings, "dot_matrix_slip_height_cm", 0.0, 0.0, 30.0)
    return {
        "slip": _units(slip_cm) if slip_cm > 0 else 0,
        "top": _units(_cm_setting(settings, "dot_matrix_top_offset_cm", 1.0, 0.0, 5.0)),
        "bottom": _units(_cm_setting(settings, "dot_matrix_bottom_margin_cm", 1.0, 0.0, 5.0)),
        "width_cols": max(40, min(80, int(
            _cm_setting(settings, "dot_matrix_print_width_cm", 12.9, 5.0, 20.0) / 2.54 * 12
        ))),
    }


def _a6_width_scaled(base: DmLayout, cols: int) -> DmLayout:
    """Same bill, fitted to the printable width of the paper.

    The pieces have to add up exactly or the borders do not meet: a header row
    is width - 2 (two pipes), the footer width - 3, and the table columns
    width - (columns + 1). Width is taken from (or given to) the medicine name
    column first, since it is the one with room to spare.
    """
    cols = max(40, min(80, int(cols)))
    if cols == base.line_width:
        return base
    widths = list(base.col_widths)
    want_cells = cols - (len(widths) + 1)
    widths[1] = max(10, widths[1] + (want_cells - sum(widths)))
    if sum(widths) != want_cells:                      # name column hit its floor
        widths[-1] = max(6, widths[-1] + (want_cells - sum(widths)))
    hdr_left = max(12, round(base.hdr_left_w / (base.line_width - 2) * (cols - 2)))
    foot_left = max(12, round(base.foot_left_w / (base.line_width - 3) * (cols - 3)))
    return replace(
        base,
        line_width=cols,
        col_widths=tuple(widths),
        hdr_left_w=hdr_left,
        hdr_right_w=max(8, cols - 2 - hdr_left),
        foot_left_w=foot_left,
        foot_right_w=max(8, cols - 3 - foot_left),
    )


def _a6_lines_that_fit(paper: dict, spacing: int) -> int:
    """How many lines fit between the top and bottom margins of one slip."""
    if not paper["slip"]:
        return 0
    content = max(spacing, paper["slip"] - paper["top"] - paper["bottom"])
    return max(1, content // spacing)


def _dot_matrix_tear_mode(settings: dict) -> str:
    """Who puts the paper where the next bill starts.

    "software" (default): the app runs the whole cycle. After a bill it feeds
    the slip out by the tear-off distance so the perforation clears the tear
    edge, and before the next bill it pulls back exactly the same distance it
    fed out - never more - so the paper lands on the top of the next slip. It
    can neither over-pull nor under-eject, because both ends use the one
    number, and nothing depends on the printer's own tear-off working.

    "printer": send the page length and a form feed and let a printer whose
    auto tear-off does work do it instead.
    """
    raw = str(settings.get("dot_matrix_tear_mode") or "software").strip().lower()
    return raw if raw in ("printer", "software") else "software"


def _a6_page_plan(slip_216: int, wanted_spacing: int) -> tuple[int, int]:
    """(line spacing, page length in lines) whose product is the slip, exactly.

    The printer counts a page in LINES, so a page length that does not divide
    the slip leaves a fraction of a line over on every bill and the position
    creeps down the roll. Nudging the line spacing by a step or two makes it
    come out even.
    """
    if slip_216 <= 0:
        return wanted_spacing, 0
    best = None
    for spacing in range(20, 33):
        lines = slip_216 / spacing
        err = abs(lines - round(lines))
        rank = (round(err, 6), abs(spacing - wanted_spacing))
        if best is None or rank < best[0]:
            best = (rank, spacing, max(1, min(127, int(round(lines)))))
    return best[1], best[2]


def _dot_matrix_tear_gap_units(settings: dict) -> int:
    """Print head to tear edge, in 1/216 inch.

    Between bills the paper rests with the perforation at the tear edge, which
    is where the shop tears the slip off - so the top of the NEXT slip is that
    far ABOVE the print head, and printing from where the paper stands would
    start halfway down the slip. Each bill pulls the paper back by this much
    first, prints from the top of the slip, and gives it back at the end, so
    the perforation is at the tear edge again and the net movement is still
    exactly one slip.
    """
    try:
        cm = float(settings.get("dot_matrix_tear_gap_cm", 4.0) or 0)
    except (TypeError, ValueError):
        cm = 4.0
    # Up to 8 cm: on this printer the tear edge sits well above the print head,
    # and a slip that does not clear it cannot be torn off cleanly.
    cm = max(0.0, min(8.0, cm))
    return max(0, int(round(cm / 2.54 * 216)))


def _a6_reverse_feed_bytes(units_216: int) -> bytes:
    """ESC j n, repeated: one command moves at most 255/216 inch."""
    out = bytearray()
    remaining = max(0, int(units_216 or 0))
    while remaining > 0:
        n = min(255, remaining)
        out += bytes([0x1B, 0x6A, n])
        remaining -= n
    return bytes(out)


def _dot_matrix_tear_feed_units(settings: dict) -> int:
    """ESC J forward feed total — eject slip to tear bar (no full FF blank page)."""
    try:
        cm = float(settings.get("dot_matrix_tear_feed_cm", 2.5) or 0)
    except (TypeError, ValueError):
        cm = 2.5
    cm = max(0.0, min(6.0, cm))
    return max(0, min(600, int(round(cm / 2.54 * 216))))


def _dot_matrix_slip_units(settings: dict) -> int:
    """Slip pitch (perforation to perforation) in 1/216 inch; 0 = not set."""
    try:
        cm = float(settings.get("dot_matrix_slip_height_cm", 0) or 0)
    except (TypeError, ValueError):
        cm = 0.0
    if cm <= 0:
        return 0
    cm = max(5.0, min(30.0, cm))
    return int(round(cm / 2.54 * 216))


def _dot_matrix_left_offset_cm(settings: dict) -> float:
    """How far right of the printer's first column the bill starts (cm).

    Carries the old ``dot_matrix_hpos_60ths`` over when the new setting was
    never saved: that one was sent as ESC $, which moves only the line it is on,
    so it shifted the first line of a bill and nothing else.
    """
    try:
        cm = float(settings.get("dot_matrix_left_offset_cm", 0) or 0)
    except (TypeError, ValueError):
        cm = 0.0
    if cm <= 0:
        try:
            hpos = int(settings.get("dot_matrix_hpos_60ths") or 0)
        except (TypeError, ValueError):
            hpos = 0
        cm = hpos / 60 * 2.54 if hpos > 0 else 0.0
    return max(0.0, min(5.0, cm))


def _a6_tear_feed_bytes(units_216: int) -> bytes:
    out = bytearray()
    remaining = max(0, int(units_216 or 0))
    while remaining > 0:
        n = min(255, remaining)
        out += bytes([0x1B, 0x4A, n])
        remaining -= n
    return bytes(out)


def _a6_base_layout(settings: dict) -> DmLayout:
    """A6 layout with user line spacing from bill print settings."""
    base = _LAYOUT_A6
    try:
        spacing = int(settings.get("dot_matrix_line_spacing") or base.escp_line_spacing)
    except (TypeError, ValueError):
        spacing = int(base.escp_line_spacing or 30)
    spacing = max(20, min(32, spacing))
    paper = _a6_paper(settings)
    base = _a6_width_scaled(base, paper["width_cols"])
    slip_units = _dot_matrix_slip_units(settings)
    mode = _dot_matrix_tear_mode(settings)
    if mode == "printer" and slip_units:
        # Let the printer own the paper: the line spacing is picked so a PAGE is
        # exactly one slip, and the form feed at the end takes it to the next
        # top-of-form, where the printer's own tear-off parks it and pulls it
        # back. The BILL still stops at the bottom margin, below.
        spacing, _page_lines = _a6_page_plan(slip_units, spacing)
    if paper["slip"]:
        # Fixed page: the margins are kept and the bill always fills the rest,
        # so every slip carries the same frame whatever the bill holds.
        slip = _a6_lines_that_fit(paper, spacing)
        page_lines = max(1, min(127, paper["slip"] // spacing))
        if mode == "printer":
            # Never print the page's last line: a page filled to the end leaves
            # the printer AT the page end, and the form feed is then answered
            # with a whole blank slip.
            slip = min(slip, max(1, page_lines - 1))
    else:
        line_mm = spacing / 180.0 * 25.4
        slip = max(20, min(36, int(round(100.0 / line_mm))))
        page_lines = max(slip + 3, min(127, int(round(120.0 / line_mm))))
    # The software pull-back only makes sense in the exact-slip cycle, where the
    # end feed gives the same distance back, and only when the printer is not
    # doing it itself.
    gap = _dot_matrix_tear_gap_units(settings) if (slip_units and mode == "software") else 0
    # The top margin is a distance DOWN from the perforation, so it is fed
    # forward after the pull-back. Reversing for it (as before) fought the
    # paper's own position and left the setting unusable.
    top_fwd = paper["top"] if slip_units else 0
    top_rev = gap if slip_units else _dot_matrix_top_reverse_units(settings)
    tear = _dot_matrix_tear_feed_units(settings)
    return replace(
        base,
        escp_line_spacing=spacing,
        slip_lines=slip,
        escp_page_lines=page_lines,
        escp_backspaces=0,
        escp_hpos_60ths=0,
        escp_tear_gap_216=gap,
        escp_tear_mode=mode,
        escp_top_forward_216=top_fwd,
        escp_top_reverse=top_rev,
        escp_tear_feed_216=tear,
        escp_slip_216=_dot_matrix_slip_units(settings),
        escp_left_cols=int(round(_dot_matrix_left_offset_cm(settings) / 2.54 * 12)),
        preserve_leading_spaces=not _vertical_borders(settings),
    )


def _layout_a6_for_ctx(ctx, settings: dict) -> DmLayout:
    """Pick line spacing / slip height so header + N items + footer fit one A6 slip."""
    base = _a6_base_layout(settings)
    n_items = len(getattr(ctx, "items", None) or [])
    page_index = int(getattr(ctx, "page_index", 0) or 0)
    # Measured, not guessed. It was a constant 10, so adding a header line --
    # the store e-mail -- silently pushed a full slip one line over and the last
    # item fell off the paper.
    if page_index > 0:
        header_est = 4
    else:
        try:
            header_est = 1 + max(
                len(_build_header_left(ctx, settings)),
                len(_build_header_right(ctx, settings)),
            )
        except Exception:
            header_est = 10
    footer_est = 3 if bool(getattr(ctx, "is_continued", False)) else 7
    table_est = 3 + n_items + 1
    need = header_est + table_est + footer_est
    if need <= base.slip_lines:
        return base
    paper = _a6_paper(settings)
    start_spacing = int(base.escp_line_spacing or 30)
    for spacing in range(start_spacing, 17, -1):
        if paper["slip"]:
            fits = _a6_lines_that_fit(paper, spacing)
            if fits >= need:
                return replace(
                    base,
                    escp_line_spacing=spacing,
                    slip_lines=fits,
                    escp_page_lines=max(1, min(127, paper["slip"] // spacing)),
                )
            continue
        line_mm = spacing / 180.0 * 25.4
        slip = max(need, min(36, int(round(100.0 / line_mm))))
        if slip >= need:
            page_lines = max(slip + 3, min(127, int(round(120.0 / line_mm))))
            return replace(
                base,
                escp_line_spacing=spacing,
                slip_lines=slip,
                escp_page_lines=page_lines,
            )
    if paper["slip"]:
        # The bill is longer than the slip even at the tightest spacing: it
        # carries on to the next one rather than printing over the perforation.
        return replace(
            base,
            escp_line_spacing=18,
            slip_lines=_a6_lines_that_fit(paper, 18),
            escp_page_lines=max(1, min(127, paper["slip"] // 18)),
        )
    return replace(base, escp_line_spacing=22, slip_lines=max(need, 32), escp_page_lines=36)


def _build_header_carry(ctx, settings: dict) -> list[str]:
    """Compact header on carry-forward pages (page 2+)."""
    dm = _dm(settings)
    left: list[str] = []
    if _setting(settings, "show_store_name", True) and ctx.store_name:
        left.extend(_wrap_text(_ascii_safe(ctx.store_name).upper(), dm.hdr_left_w))
    right: list[str] = []
    if _setting(settings, "show_bill_no", True) and ctx.bill_no:
        bill_no_l = _label(settings, "meta_bill_no_label", "BILL NO.")
        right.append(_meta_row(bill_no_l, ctx.bill_no, settings=settings))
    page_no = int(getattr(ctx, "page_index", 0) or 0) + 1
    page_count = int(getattr(ctx, "page_count", 0) or 0)
    if bool(getattr(ctx, "is_continued", False)):
        label = f"CONTINUED  Page {page_no}/{page_count}" if page_count > 1 else "CONTINUED"
    elif page_count > 1:
        label = f"Page {page_no}/{page_count}"
    else:
        label = ""
    if label:
        right.append(_center(label, dm.hdr_right_w))
    return _merge_header(left, [], right, settings)


def _build_header(ctx, settings: dict) -> list[str]:
    if int(getattr(ctx, "page_index", 0) or 0) > 0:
        return _build_header_carry(ctx, settings)
    return _merge_header(
        _build_header_left(ctx, settings),
        [],
        _build_header_right(ctx, settings),
        settings,
    )


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


def _bill_row_physical_lines(
    cells: list[str],
    cols: list[tuple[str, str, str, int]],
) -> list[list[str]]:
    """Soft-wrap medicine name and batch; other columns only on first line."""
    col_wraps: list[list[str]] = []
    for j, (key, _h, _a, w) in enumerate(cols):
        val = cells[j] if j < len(cells) else ""
        if key in _BILL_WRAP_KEYS:
            col_wraps.append(_wrap_text(val, w))
        else:
            col_wraps.append([val])
    max_l = max((len(cl) for cl in col_wraps), default=1)
    physical: list[list[str]] = []
    for li in range(max_l):
        row_vals: list[str] = []
        for j, (key, _h, _a, _w) in enumerate(cols):
            if li > 0 and key in _BILL_FIRST_LINE_ONLY:
                row_vals.append("")
            else:
                row_vals.append(col_wraps[j][li] if li < len(col_wraps[j]) else "")
        physical.append(row_vals)
    return physical


def _build_table(ctx, settings: dict) -> list[str]:
    widths, aligns, cols = _active_col_layout(settings)
    out: list[str] = []
    header = [hdr for _k, hdr, _a, _w in cols]
    out.append(_table_sep_dynamic(widths, settings))
    out.append(_table_row_dynamic(header, widths, aligns, settings))
    out.append(_table_sep_dynamic(widths, settings))

    sr_offset = int(getattr(ctx, "item_sr_offset", 0) or 0)
    item_rows: list[list[str]] = []
    for idx, item in enumerate(ctx.items or []):
        cells = _item_cells(item, sr_offset + idx + 1, settings)
        row = [cells[key] for key, _h, _a, _w in cols]
        item_rows.extend(_bill_row_physical_lines(row, cols))

    min_rows = _table_pad_rows(settings, len(ctx.items or []))
    while len(item_rows) < min_rows:
        item_rows.append([""] * len(cols))

    for row in item_rows:
        out.append(_table_row_dynamic(row, widths, aligns, settings))
    out.append(_table_sep_dynamic(widths, settings))
    return out


def _expand_table_blank_rows(table_lines: list[str], extra: int, settings: dict) -> list[str]:
    """Insert blank medicine rows before the closing table rule — pushes footer down."""
    if extra <= 0 or not table_lines:
        return table_lines
    widths, aligns, cols = _active_col_layout(settings)
    blank = _table_row_dynamic([""] * len(cols), widths, aligns, settings)
    if table_lines[-1].startswith("+"):
        return table_lines[:-1] + [blank] * extra + [table_lines[-1]]
    return table_lines + [blank] * extra


def _a6_slip_line_count(dm: DmLayout) -> int:
    if dm.slip_lines > 0:
        return dm.slip_lines
    spacing = max(18, int(dm.escp_line_spacing or 24))
    line_mm = spacing / 180.0 * 25.4
    return max(24, min(36, int(round(100.0 / line_mm))))


def format_bill_text(ctx, settings: dict | None = None) -> str:
    """Plain-text bill — 2-col header, table, footer pinned to bottom on A6."""
    merged = _settings_for_ctx(settings, ctx)
    header = _build_header(ctx, merged)
    table = _build_table(ctx, merged)
    footer = _build_footer(ctx, merged)
    lines = header + table + footer

    if _resolve_paper(merged) == "A6":
        dm = _dm(merged)
        slip = _a6_slip_line_count(dm)
        gap = slip - len(lines)
        if gap > 0:
            table = _expand_table_blank_rows(table, gap, merged)
            lines = header + table + footer
        if len(lines) < slip:
            pad = _border_line(merged)
            lines += [pad] * (slip - len(lines))
        elif len(lines) > slip:
            slip = len(lines)

    return "\n".join(lines)


def _footer_strip_line(ctx, settings: dict) -> str:
    custom = (getattr(ctx, "footer_strip_line", None) or settings.get("footer_strip_line") or "").strip()
    if custom:
        return _ascii_safe(custom)
    nice = _label(settings, "gst_day_line", "HAVE A NICE DAY")
    if not _show_gst_details(settings) or not ctx.gst_enabled or float(ctx.gst_amount or 0) <= 0:
        return nice
    taxable, cgst, sgst = gst_strip_figures(ctx)
    rates = sorted({float(item.gst_percent or 0) for item in ctx.items if item.gst_percent})
    if len(rates) == 1 and rates[0] > 0:
        half_rate = rates[0] / 2
        gst_part = (
            f"GST {taxable:.2f}*{half_rate:g}+{half_rate:g}%="
            f"{sgst:.2f}SGST+{cgst:.2f}CGST"
        )
    else:
        gst_part = f"GST {taxable:.2f} = {sgst:.2f}SGST + {cgst:.2f}CGST"
    return f"{gst_part}, {nice}"[:_dm(settings).foot_left_w]


def _totals_lines(ctx, settings: dict) -> list[str]:
    continued = bool(getattr(ctx, "is_continued", False))
    lines: list[str] = []
    disc_l = _label(settings, "discount_label", "DISC")
    total_l = _label(settings, "total_label", "Total")
    foot_right_w = _dm(settings).foot_right_w

    def _amt_row(label: str, amount: float) -> str:
        amt = f"{float(amount):.2f}"
        gap = foot_right_w - len(amt)
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
    # "Total row" off means no total: this used to hand one back anyway, so the
    # setting did nothing on a bill that had no discount, GST or rounding.
    if not lines and _setting(settings, "show_total", True):
        lines.append(_amt_row(total_l, float(ctx.grand_total or 0)))
    return lines


def _footer_left_lines(ctx, settings: dict) -> list[str]:
    dm = _dm(settings)
    lines: list[str] = []
    if _setting(settings, "show_recovery_wish", True):
        wish = (
            getattr(ctx, "recovery_wish_line", None)
            or settings.get("recovery_wish_line", "I WISH FOR YOUR SPEEDY RECOVERY.")
        )
        text = _ascii_safe(wish).strip()
        if text:
            wrapped = textwrap.wrap(text, width=dm.foot_left_w) or [text[:dm.foot_left_w]]
            if _resolve_paper(settings) == "A6":
                wrapped = wrapped[:1]
            lines.extend(wrapped)
    # Phone stays in header left on A6 — do not repeat in footer
    if (
        _setting(settings, "show_store_phone", True)
        and ctx.phone
        and _resolve_paper(settings) != "A6"
    ):
        lines.append(f"Phone  : {ctx.phone}"[:dm.foot_left_w])
    return lines or [""]


def _signature_lines(ctx, settings: dict) -> list[str]:
    if not _setting(settings, "show_signature", True):
        return ["", ""]
    dm = _dm(settings)
    sign_l = _label(settings, "signature_caption", "SIGN OF Q.P.")
    if _resolve_paper(settings) == "A6":
        # Two blank lines for signature, then caption — no "For STORE NAME" on dot matrix slip.
        return ["", "", _center(sign_l, dm.foot_right_w)]
    if not ctx.store_name:
        return ["", _center(sign_l, dm.foot_right_w)]
    for_line = f"For {_ascii_safe(ctx.store_name).upper()}"
    return [for_line[:dm.foot_right_w], _center(sign_l, dm.foot_right_w)]


def _build_footer(ctx, settings: dict) -> list[str]:
    continued = bool(getattr(ctx, "is_continued", False))
    out: list[str] = []

    if not continued:
        # The GST strip and the money are two different things: hiding the strip
        # used to take Total, LESS and Due down with it, because both lived
        # under the same switch and the bill came out with no amount on it.
        show_strip = _setting(settings, "show_gst_strip", True)
        strip = _footer_strip_line(ctx, settings) if show_strip else ""
        totals = _totals_lines(ctx, settings)
        if strip or totals:
            out.append(_foot_hbar(settings))
            for i in range(max(1, len(totals))):
                left = strip if i == 0 else ""
                right = totals[i] if i < len(totals) else ""
                out.append(_foot_row(left, right, settings))
    elif continued:
        totals = _totals_lines(ctx, settings)
        out.append(_foot_hbar(settings))
        for row in totals:
            out.append(_foot_row("", row, settings))

    if not continued:
        left_lines = _footer_left_lines(ctx, settings)
        sig_lines = _signature_lines(ctx, settings)
        out.append(_foot_hbar(settings))
        height = max(len(left_lines), len(sig_lines), 2)
        for i in range(height):
            left = left_lines[i] if i < len(left_lines) else ""
            right = sig_lines[i] if i < len(sig_lines) else ""
            out.append(_foot_row(left, right, settings))

    out.append(_border_line(settings))
    return out


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


def _a6_line_pad(line: str, dm: DmLayout) -> str:
    """A6 line prep — preserve leading spaces when layout uses space-based right columns."""
    if dm.preserve_leading_spaces:
        return line[: dm.line_width].ljust(dm.line_width)
    return line.lstrip()


def _a6_init_suffix(dm: DmLayout) -> bytes:
    """One-time H-pos after init (optional). Per-line ESC $ causes restrike on LX-310."""
    hpos = max(0, min(120, int(dm.escp_hpos_60ths or 0)))
    if hpos <= 0:
        return b""
    return bytes([0x1B, 0x24, hpos & 0xFF, (hpos >> 8) & 0xFF])


def render_escp_document(
    text: str,
    *,
    paper: str = "A5",
    layout: DmLayout | None = None,
    init: bool = True,
    form_feed_end: bool | None = None,
    draft_a4: bool = False,
) -> bytes:
    paper_u = (paper or "A5").upper()
    dm = layout or _layout_for_paper(paper_u)
    a6 = paper_u == "A6"
    do_ff = dm.escp_form_feed_end if form_feed_end is None else bool(form_feed_end)
    out = bytearray()
    if init:
        out += b"\x1b@"          # init
        if a6:
            out += b"\x1bx\x00"   # draft — single pass (LQ/NLQ double-strikes on LX-310)
        else:
            out += b"\x1bx\x01"   # LQ for wide paper
    if a6:
        out += b"\x1bO"   # cancel skip-over-perforation: a printer default of ON adds its own blank lines at every perforation
        out += b"\x1bM"   # 12 CPI — landscape slip, 64 chars across 14 cm print width
        out += b"\x1ba\x00"  # left align
        out += b"\x1bU\x00"  # unidirectional
        # ESC l n — left margin in 12 CPI columns, set AFTER ESC M because it is
        # counted in the current pitch. It moves every line, unlike ESC $.
        out += bytes([0x1B, 0x6C, max(0, min(30, int(dm.escp_left_cols or 0)))])
        spacing = max(20, min(32, int(dm.escp_line_spacing or 28)))
        out += bytes([0x1B, 0x33, spacing])
        line_count = max(1, len(text.splitlines()))
        page_len = max(1, min(127, line_count))
        if int(dm.escp_slip_216 or 0) > 0:
            # The printer's own page length follows the real slip as closely as
            # whole lines allow; the exact advance is done by the end feed below.
            page_len = max(1, min(127, int(round(dm.escp_slip_216 / float(spacing)))))
            if getattr(dm, "escp_tear_mode", "") == "printer":
                # Here the page length IS the slip (the spacing was chosen to
                # divide it), and the form feed at the end lands on the next
                # top-of-form, so nothing accumulates.
                _sp, page_len = _a6_page_plan(int(dm.escp_slip_216), spacing)
        out += bytes([0x1B, 0x43, page_len])  # page = one A6 slip; auto-feed after last line
        out += _a6_init_suffix(dm)
        # Pull the paper back to the top of this slip: the tear gap (the paper
        # is parked with the perforation at the tear edge) plus the top offset.
        out += _a6_reverse_feed_bytes(int(dm.escp_top_reverse or 0))
        # Top margin: down from the perforation, fed forward.
        out += _a6_tear_feed_bytes(int(getattr(dm, "escp_top_forward_216", 0) or 0))
        # No CR/LF here — avoids a blank line before the first border row
    elif paper_u == "A4":
        if draft_a4:
            out += b"\x1bx\x00"   # draft — single pass (same as sales bill on LX-310)
        else:
            out += b"\x1bx\x01"   # LQ for wide paper
        out += b"\x1bP"   # 10 CPI — A4 portrait
        out += b"\x1b2"   # 1/6 inch line spacing
        out += b"\x1ba\x00"
        out += b"\x1bU\x00"  # unidirectional
        # No leading CR/LF — avoids blank line before title
    elif init:
        out += b"\x1bP"
        out += b"\x1b2"
    printed_rows = 0
    for line in text.splitlines():
        if line == _BOLD_ON:
            out += b"\x1bE"
            continue
        if line == _BOLD_OFF:
            out += b"\x1bF"
            continue
        if line == _TITLE_EM_ON:
            out += b"\x1bE"
            out += bytes([0x1B, 0x21, 0x10])  # double height — title / subtitle
            continue
        if line == _TITLE_EM_OFF:
            out += bytes([0x1B, 0x21, 0x00])
            out += b"\x1bF"
            continue
        if line == _PAGE_BREAK:
            out += b"\x0c"  # form feed — next schedule page
            continue
        row = _a6_line_pad(line, dm) if a6 else line
        out += _encode_line_with_bold(row)
        out += b"\r\n"
        printed_rows += 1
    out += b"\x1bF"
    if a6 and dm.use_condensed:
        out += b"\x12"
    if a6:
        if _a6_uses_printer_tear_off(dm):
            page_len = max(1, int(dm.escp_slip_216 // max(1, spacing)))
            if printed_rows < page_len:
                out += b"\x0c"      # form feed: on to the printer's next top-of-form
        else:
            out += _a6_tear_feed_bytes(_a6_end_feed_units(dm, printed_rows))
    elif paper_u == "A4" and do_ff:
        out += b"\x0c"
    return bytes(out)


def _a6_uses_printer_tear_off(dm: DmLayout) -> bool:
    """True when the printer itself parks the slip and pulls it back."""
    return (
        str(getattr(dm, "escp_tear_mode", "printer")) == "printer"
        and int(getattr(dm, "escp_slip_216", 0) or 0) > 0
    )


def _a6_end_feed_units(dm: DmLayout, printed_rows: int) -> int:
    """Forward feed (1/216 inch) after an A6 slip.

    With a slip height set, the whole job moves the paper by exactly one slip:
    it went back by the top offset, forward by every printed line, and this
    feed makes up the rest - so bill after bill starts at the same place on its
    own slip, however many lines each one had. On a 9-pin printer ESC 3, ESC J
    and ESC j all count in 1/216 inch, so the sum is exact. A bill taller than
    the slip gets no extra feed rather than a negative one.

    Without a slip height: the fixed tear feed, as before.
    """
    slip = int(getattr(dm, "escp_slip_216", 0) or 0)
    if slip > 0:
        spacing = max(20, min(32, int(dm.escp_line_spacing or 28)))
        used = (int(printed_rows) * spacing
                + int(getattr(dm, "escp_top_forward_216", 0) or 0)
                - int(dm.escp_top_reverse or 0))
        # ... and the slip is fed out to the tear edge again, so the next bill
        # can pull back the same distance. A bill longer than its slip still
        # gets the eject, or the shop could not tear it off.
        return max(int(getattr(dm, "escp_tear_gap_216", 0) or 0), slip - used)
    tear = int(getattr(dm, "escp_tear_feed_216", 0) or 0)
    if tear <= 0:
        tear = _dot_matrix_tear_feed_units({})
    return tear


def render_escp_bill_pages(
    texts: list[str],
    *,
    paper: str,
    layouts: list[DmLayout],
    last_page_copies: int = 1,
) -> bytes:
    """One RAW job — form feed after each slip; last page may repeat (e.g. 2 copies)."""
    if not texts:
        return b""
    if len(texts) == 1 and last_page_copies <= 1:
        return render_escp_document(texts[0], paper=paper, layout=layouts[0])
    out = bytearray()
    last_idx = len(texts) - 1
    repeats_last = max(1, min(4, int(last_page_copies or 1)))
    for i, (text, layout) in enumerate(zip(texts, layouts)):
        repeats = repeats_last if (i == last_idx and len(texts) > 1) else 1
        for _copy in range(repeats):
            out += render_escp_document(
                text,
                paper=paper,
                layout=layout,
                init=True,
                form_feed_end=True,
            )
    return bytes(out)


def gdi_placement_for(settings: dict | None, paper: str) -> dict | None:
    """Where an A6 bill goes when it is drawn through the Windows driver.

    Top-left of the printable area, moved right by the Left offset, fitted to
    the A6 print width (64 columns at 12 CPI = 13.6 cm) and to the slip; with a
    slip height the driver page is that long, so each bill ejects one slip.
    Other papers keep the old centred layout (None).
    """
    if (paper or "").upper() != "A6":
        return None
    merged = dict(settings or {})
    slip_cm = 0.0
    if _dot_matrix_slip_units(merged):
        slip_cm = max(5.0, min(30.0, float(merged.get("dot_matrix_slip_height_cm") or 0)))
    else:
        # No slip height set: still ask the driver for an A6-high page. Its own
        # default is a full sheet (A4 / 11 inch / fanfold), so every A6 bill fed
        # a whole page and the next bill started a page later.
        slip_cm = 10.5
    return {
        "left_cm": _dot_matrix_left_offset_cm(merged),
        "top_cm": 0.0,
        "width_cm": 13.6,
        "height_cm": slip_cm or 10.5,
        "slip_cm": slip_cm,
    }


def dot_matrix_print_summary(settings: dict | None, printer: str) -> str:
    """One log line: how this bill is about to be printed, and with what."""
    from core.printer_manager import PrinterManager

    merged = dict(settings or {})
    try:
        method = PrinterManager.get_dot_matrix_print_method()
        via = "GDI" if PrinterManager.should_use_dot_matrix_gdi(printer) else "RAW"
    except Exception:
        method, via = "?", "?"
    return (
        f'dot_matrix preflight printer="{printer}" method={method} -> {via} '
        f"top_offset_cm={merged.get('dot_matrix_top_offset_cm', 0.8)} "
        f"slip_height_cm={merged.get('dot_matrix_slip_height_cm', 0)} "
        f"left_offset_cm={_dot_matrix_left_offset_cm(merged)} "
        f"tear_feed_cm={merged.get('dot_matrix_tear_feed_cm', 2.5)} "
        f"tear_gap_cm={merged.get('dot_matrix_tear_gap_cm', 2.5)} "
        f"tear_mode={_dot_matrix_tear_mode(merged)} (eject and pull back the same) "
        f"line_spacing={merged.get('dot_matrix_line_spacing', 30)} "
        f"bottom_margin_cm={merged.get('dot_matrix_bottom_margin_cm', 1.0)} "
        f"print_width_cm={merged.get('dot_matrix_print_width_cm', 12.9)}"
    )


def print_dot_matrix_bill_pages(
    contexts,
    settings: dict | None,
    printer_name: str | None,
    *,
    copies: int = 1,
    last_page_copies: int = 1,
) -> None:
    """Print carry-forward pages in one RAW job with form feed between slips."""
    if not sys.platform.startswith("win"):
        raise DotMatrixPrintError(
            "Dot matrix RAW printing is only implemented on Windows (win32print)."
        )
    texts: list[str] = []
    layouts: list[DmLayout] = []
    paper = "A5"
    for ctx in contexts:
        merged = _settings_for_ctx(settings, ctx)
        texts.append(format_bill_text(ctx, merged))
        layouts.append(_dm(merged))
        paper = _resolve_paper(merged)
    multi = len(texts) > 1
    last_copies = max(1, min(4, int(last_page_copies or 1))) if multi else 1
    spooler_copies = 1 if multi else max(1, min(int(copies or 1), 10))
    payload = render_escp_bill_pages(
        texts, paper=paper, layouts=layouts, last_page_copies=last_copies,
    )
    try:
        from core.bill_config import get_bill_size_pct
        scale_pct = get_bill_size_pct(settings)
    except Exception:
        scale_pct = 92.0
    try:
        from core.print_log import print_log
        print_log(
            f"format_bill_text pages={len(texts)} lines={[len(t.splitlines()) for t in texts]} "
            f"payload_bytes={len(payload)} paper={paper} scale_pct={scale_pct} "
            f"last_page_copies={last_copies} spooler_copies={spooler_copies} "
            f'printer="{printer_name}" slot_copies={copies}'
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

    _log(f'dot_matrix target="{printer}" driver="{driver}" pages={len(texts)}')
    _log(dot_matrix_print_summary(settings, printer))

    if not PrinterManager.should_use_dot_matrix_gdi(printer):
        try:
            _log(f'dot_matrix trying RAW ESC/P on "{printer}"')
            PrinterManager.print_raw_escp(payload, printer, copies=spooler_copies)
            return
        except PrinterError as exc:
            errors.append(f"RAW ESC/P: {exc}")
            _log(f"RAW failed: {exc}", level="WARN")

    combined_text = "\f".join(texts)
    try:
        _log(f'dot_matrix trying GDI text on "{printer}" paper={paper}')
        PrinterManager.print_text_gdi(
            combined_text, printer, copies=copies, paper_size=paper, scale_pct=scale_pct,
            placement=gdi_placement_for(settings, paper),
        )
        return
    except PrinterError as exc:
        errors.append(f"GDI text: {exc}")
        _log(f"GDI failed: {exc}", level="WARN")

    if PrinterManager.should_use_dot_matrix_gdi(printer):
        try:
            _log(f'dot_matrix trying RAW fallback on "{printer}"')
            PrinterManager.print_raw_escp(payload, printer, copies=spooler_copies)
            return
        except PrinterError as exc:
            errors.append(f"RAW fallback: {exc}")

    hint = (
        'Use printer queue "EPSON LX-310 ESC/P" (not the Class Driver queue). '
        "Check USB cable and that the queue is online."
    )
    raise DotMatrixPrintError("; ".join(errors) + f" {hint}")


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
    text = format_bill_text(ctx, _settings_for_ctx(settings, ctx))
    merged = _settings_for_ctx(settings, ctx)
    paper = _resolve_paper(merged)
    layout = _dm(merged)
    try:
        from core.bill_config import get_bill_size_pct
        scale_pct = get_bill_size_pct(settings)
    except Exception:
        scale_pct = 92.0
    payload = render_escp_document(text, paper=paper, layout=layout)
    try:
        from core.print_log import print_log
        print_log(
            f"format_bill_text lines={len(text.splitlines())} max_cols={layout.line_width} "
            f"payload_bytes={len(payload)} paper={paper} scale_pct={scale_pct} "
            f'printer="{printer_name}" copies={copies}'
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
    _log(dot_matrix_print_summary(merged, printer))

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
            placement=gdi_placement_for(merged, paper),
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


def _report_col_widths(headers: list, rows: list, line_width: int) -> tuple[int, ...]:
    """Proportional column widths (plain HTML-style table, no rules)."""
    n = max(1, len(headers))
    if n == 1:
        return (line_width,)
    gap_chars = max(0, n - 1)  # one space between columns
    usable = max(n * 4, line_width - gap_chars)
    hdrs = [str(h or "") for h in headers]
    min_w = 4
    max_w = max(min_w, usable // max(1, n // 2))
    weights = []
    for i in range(n):
        sample = len(hdrs[i])
        for row in (rows or [])[:40]:
            if i < len(row):
                sample = max(sample, len(str(row[i] if row[i] is not None else "")))
        weights.append(max(min_w, min(max_w, sample + 1)))
    total = sum(weights) or n
    if total <= usable:
        extra = usable - total
        weights[-1] += extra
        return tuple(weights)
    scale = usable / total
    scaled = [max(min_w, int(w * scale)) for w in weights]
    while sum(scaled) > usable:
        idx = scaled.index(max(scaled))
        scaled[idx] = max(min_w, scaled[idx] - 1)
    while sum(scaled) < usable:
        scaled[-1] += 1
    return tuple(scaled)


def _table_row_plain(
    cells: list[str],
    widths: tuple[int, ...],
    aligns: tuple[str, ...],
) -> str:
    parts = [_cell(c, w, a) for c, w, a in zip(cells, widths, aligns)]
    return " ".join(parts).rstrip()


def _report_plain_settings(settings: dict) -> dict:
    """Export reports: spaced columns like HTML/PDF — no | or ----- rules."""
    out = dict(settings or {})
    out["dot_matrix_vertical_borders"] = False
    layout = out.get(_DM_SETTINGS_KEY)
    if isinstance(layout, DmLayout):
        out[_DM_SETTINGS_KEY] = replace(layout, preserve_leading_spaces=True)
    return out


def format_report_text(
    title: str,
    headers: list,
    rows: list,
    settings: dict | None = None,
    *,
    paper: str = "A4",
) -> str:
    """Plain-text export report for dot matrix A4 portrait (HTML/PDF style, no rules)."""
    from datetime import datetime

    merged = _settings_with_layout(dict(settings or {}))
    report = _report_plain_settings(merged)
    paper_u = (paper or "A4").upper()
    report["paper_size"] = paper_u
    report["print_paper_hint"] = paper_u
    dm = _layout_for_paper(paper_u)
    report[_DM_SETTINGS_KEY] = replace(dm, preserve_leading_spaces=True)

    lines: list[str] = []
    lines.append(_TITLE_EM_ON)
    lines.append(_center(_ascii_safe(title), dm.line_width))
    lines.append(_TITLE_EM_OFF)
    lines.append(_TITLE_EM_ON)
    lines.append(_center(datetime.now().strftime("%d/%m/%Y %H:%M"), dm.line_width))
    lines.append(_TITLE_EM_OFF)
    row_count = len(rows or [])
    lines.append(_center(f"Total Records: {row_count}", dm.line_width))
    lines.append("")
    if not headers:
        return "\n".join(lines)

    hdrs = [str(h or "") for h in headers]
    widths = _report_col_widths(hdrs, rows or [], dm.line_width)
    aligns = tuple("l" for _ in hdrs)
    lines.append(_BOLD_ON)
    lines.append(_table_row_plain(hdrs, widths, aligns))
    lines.append(_BOLD_OFF)
    for row in rows or []:
        cells = [str(v) if v is not None else "" for v in row]
        while len(cells) < len(hdrs):
            cells.append("")
        lines.append(_table_row_plain(cells[: len(hdrs)], widths, aligns))
    return "\n".join(lines)


def format_reports_combined(
    sections: list[tuple[str, list, list]],
    settings: dict | None = None,
    *,
    paper: str = "A4",
) -> str:
    blocks: list[str] = []
    for i, (title, headers, rows) in enumerate(sections or []):
        if i:
            blocks.append("")
        blocks.append(format_report_text(title, headers, rows, settings, paper=paper))
    return "\n".join(blocks)


def _schedule_portrait_col_widths(
    headers: list,
    data_width: int = 76,
    *,
    col_gap: int = 1,
    style: str = "classic",
) -> tuple[int, ...]:
    """Fixed portrait widths — total cell chars + gaps = data_width."""
    classic = {
        "Date / Bill": 10,
        "Customer": 10,
        "Doctor": 11,
        "Medicine": 15,
        "Batch": 7,
        "Expiry": 8,
        "Schedule": 4,
        "Qty": 3,
        "Content/Drug": 10,
        "Sign": 6,
    }
    # Narrower doctor; Batch/Expiry merged; Sign at end — classic unchanged.
    sign_style = {
        "Date / Bill": 10,
        "Customer": 11,
        "Doctor": 7,
        "Medicine": 16,
        "Batch/Expiry": 10,
        "Batch": 10,
        "Expiry": 8,
        "Schedule": 4,
        "Qty": 3,
        "Content/Drug": 10,
        "Sign": 6,
    }
    defaults = sign_style if (style or "").strip().lower() == "sign" else classic
    n = max(1, len(headers))
    gap_total = max(0, n - 1) * max(0, col_gap)
    usable = max(n * 3, data_width - gap_total)
    widths = [max(3, defaults.get(h, 8)) for h in headers]
    total = sum(widths)
    if total != usable and widths:
        prefer = ("Medicine", "Customer", "Patient Details")
        idx = next((i for i, h in enumerate(headers) if h in prefer), len(widths) - 1)
        widths[idx] = max(8, widths[idx] + (usable - total))
    return tuple(widths)


def _strip_fy_in_schedule_cell(header: str, cell: str) -> str:
    """Remove /FY… from Bill No lines in Date/Bill (or standalone Bill No)."""
    if header not in ("Date / Bill", "Bill No", "Bill No."):
        return cell
    try:
        from core.fy_serial import display_sales_bill_no
    except Exception:
        def display_sales_bill_no(v):  # type: ignore
            s = str(v or "")
            return s.split("/FY", 1)[0] if "/FY" in s else s

    s = str(cell or "").replace("\r\n", "\n").replace("\r", "\n")
    if "\n" not in s:
        return display_sales_bill_no(s) if header.startswith("Bill") else s
    a, b = s.split("\n", 1)
    return f"{a}\n{display_sales_bill_no(b)}"


def _reshape_schedule_for_dm_style(
    headers: list[str],
    rows: list,
    style: str,
) -> tuple[list[str], list[list[str]]]:
    """Apply DM-only preset. Classic keeps columns; sign merges Batch/Expiry + Sign."""
    hdrs = [str(h or "") for h in headers]
    style_key = (style or "classic").strip().lower()

    def _norm_rows(src_rows: list) -> list[list[str]]:
        out: list[list[str]] = []
        for row in src_rows or []:
            cells = [str(c if c is not None else "") for c in row]
            while len(cells) < len(hdrs):
                cells.append("")
            cells = [
                _strip_fy_in_schedule_cell(hdrs[j], cells[j]) if j < len(hdrs) else cells[j]
                for j in range(len(cells))
            ]
            out.append(cells[: len(hdrs)])
        return out

    if style_key != "sign":
        return hdrs, _norm_rows(rows)

    # Already reshaped
    if "Batch/Expiry" in hdrs and "Sign" in hdrs:
        return hdrs, _norm_rows(rows)

    out_headers: list[str] = []
    index_map: list[tuple[str, int | None, int | None]] = []  # (title, i1, i2)

    def _idx(name: str) -> int | None:
        return hdrs.index(name) if name in hdrs else None

    for h in hdrs:
        if h in ("Batch", "Expiry"):
            continue
        out_headers.append(h)
        index_map.append((h, _idx(h), None))

    # Insert Batch/Expiry after Medicine (or before Schedule/Qty)
    bi, ei = _idx("Batch"), _idx("Expiry")
    if bi is not None or ei is not None:
        insert_at = len(out_headers)
        for name in ("Schedule", "Qty", "Sign"):
            if name in out_headers:
                insert_at = out_headers.index(name)
                break
        out_headers.insert(insert_at, "Batch/Expiry")
        index_map.insert(insert_at, ("Batch/Expiry", bi, ei))

    if "Sign" not in out_headers:
        out_headers.append("Sign")
        index_map.append(("Sign", None, None))

    # Normalize FY on source cells first, then reshape.
    normed = _norm_rows(rows)
    out_rows: list[list[str]] = []
    for cells in normed:
        out: list[str] = []
        for title, i1, i2 in index_map:
            if title == "Batch/Expiry":
                batch = cells[i1].strip() if i1 is not None and i1 < len(cells) else ""
                exp = cells[i2].strip() if i2 is not None and i2 < len(cells) else ""
                if batch in ("", "—"):
                    batch = ""
                if exp in ("", "—"):
                    exp = ""
                if batch and exp:
                    out.append(f"{batch}\n{exp}")
                else:
                    out.append(batch or exp or "")
            elif title == "Sign":
                out.append("")
            elif i1 is not None and i1 < len(cells):
                out.append(cells[i1])
            else:
                out.append("")
        out_rows.append(out)
    return out_headers, out_rows


def _schedule_cell_lines(cell: str) -> tuple[str, str]:
    """Split a cell into up to two lines (Date/Bill, or Medicine/Content)."""
    s = str(cell or "").strip()
    if not s or s == "—":
        return "", ""

    def _fmt_date(raw: str) -> str:
        raw = raw.strip()
        if len(raw) == 10 and raw[4] == "-":
            y, m, d = raw.split("-")
            return f"{d}/{m}/{y[2:]}"
        return raw

    if "\n" in s:
        a, b = s.split("\n", 1)
        return _fmt_date(a), b.strip()
    parts = s.split(None, 1)
    if parts and len(parts[0]) == 10 and parts[0][4] == "-":
        return _fmt_date(parts[0]), parts[1].strip() if len(parts) > 1 else ""
    return s, ""


def _wrap_text(text: str, width: int) -> list[str]:
    """Word-wrap (or hard-break) text to fit dot matrix column width."""
    s = _ascii_safe(str(text or "").strip())
    if not s or s == "—":
        return [""]
    w = max(1, int(width))
    if len(s) <= w:
        return [s]
    out: list[str] = []
    words = s.split()
    if len(words) > 1:
        cur = ""
        for word in words:
            if len(word) > w:
                if cur:
                    out.append(cur)
                    cur = ""
                for i in range(0, len(word), w):
                    out.append(word[i:i + w])
                continue
            if not cur:
                cur = word
            elif len(cur) + 1 + len(word) <= w:
                cur = f"{cur} {word}"
            else:
                out.append(cur)
                cur = word
        if cur:
            out.append(cur)
        return out or [""]
    return [s[i:i + w] for i in range(0, len(s), w)]


def _column_wrap_lines(cell: str, header: str, width: int) -> list[str]:
    """All physical lines needed for one column (Date/Bill stack, Medicine subline, wrap)."""
    h = header or ""
    if h in ("Date / Bill", "Batch/Expiry", "Medicine"):
        raw = str(cell or "").replace("\r\n", "\n").replace("\r", "\n")
        if "\n" in raw or h == "Date / Bill":
            line1, line2 = _schedule_cell_lines(cell)
            result: list[str] = []
            if line1:
                result.extend(_wrap_text(line1, width))
            if line2:
                result.extend(_wrap_text(line2, width))
            return result or [""]
        raw_s = raw.strip()
        if not raw_s or raw_s == "—":
            return [""]
        return _wrap_text(raw_s, width)
    raw = str(cell or "").strip()
    if not raw or raw == "—":
        return [""]
    return _wrap_text(raw, width)


_SCHEDULE_FIRST_LINE_ONLY = frozenset({"Qty", "Expiry", "Schedule", "Sign"})


def _schedule_col_gaps(headers: list[str], default_gap: str = " ") -> list[str]:
    """Gap strings between columns — extra space after Doctor before Medicine."""
    gaps = [default_gap] * max(0, len(headers) - 1)
    if "Doctor" in headers and "Medicine" in headers:
        di = headers.index("Doctor")
        mi = headers.index("Medicine")
        if mi == di + 1 and di < len(gaps):
            gaps[di] = "   "
    return gaps


def _schedule_extra_gap_chars(headers: list[str], default_gap: str = " ") -> int:
    gaps = _schedule_col_gaps(headers, default_gap)
    return sum(max(0, len(g) - len(default_gap)) for g in gaps)


def _schedule_spaced_values(
    values: list[str],
    headers: list[str],
    widths: tuple[int, ...],
    aligns: tuple[str, ...],
    *,
    col_gap: str = " ",
) -> str:
    parts = [
        _cell(values[j] if j < len(values) else "", widths[j], aligns[j])
        for j in range(len(widths))
    ]
    gaps = _schedule_col_gaps(headers, col_gap)
    if not parts:
        return ""
    out = parts[0]
    for j in range(1, len(parts)):
        out += gaps[j - 1] + parts[j]
    return out


def _schedule_row_physical_lines(
    cells: list[str],
    headers: list[str],
    widths: tuple[int, ...],
) -> list[list[str]]:
    """Build one value-list per printed line — long cells wrap to next line."""
    col_wraps = [
        _column_wrap_lines(cells[j], headers[j], widths[j])
        for j in range(len(headers))
    ]
    max_l = max((len(cl) for cl in col_wraps), default=1)
    physical: list[list[str]] = []
    for li in range(max_l):
        row_vals: list[str] = []
        for j, h in enumerate(headers):
            if li > 0 and h in _SCHEDULE_FIRST_LINE_ONLY:
                row_vals.append("")
            else:
                row_vals.append(col_wraps[j][li] if li < len(col_wraps[j]) else "")
        physical.append(row_vals)
    return physical


def _format_schedule_page_block(
    title: str,
    subtitle: str,
    hdrs: list[str],
    shaped_rows: list[list[str]],
    *,
    style: str,
    use_borders: bool,
    total_qty: int | None,
    sr_offset: int = 0,
    page_no: int = 1,
    page_count: int = 1,
) -> str:
    """One physical page of schedule text (title + table)."""
    line_w = 80
    sr_w = 3
    settings = {"dot_matrix_vertical_borders": use_borders}
    lines: list[str] = []
    lines.append(_TITLE_EM_ON)
    lines.append(_center(_ascii_safe(title), line_w))
    lines.append(_TITLE_EM_OFF)
    sub = _ascii_safe(subtitle)
    if page_count > 1:
        sub = f"{sub} | Page {page_no}/{page_count}"
    lines.append(_TITLE_EM_ON)
    lines.append(_center(sub, line_w))
    lines.append(_TITLE_EM_OFF)
    lines.append("")

    n = len(hdrs)
    if use_borders:
        data_usable = max(n * 3, line_w - (n + 1 + 1) - sr_w)
        data_widths = _schedule_portrait_col_widths(
            hdrs, data_usable, col_gap=0, style=style,
        )
        widths = (sr_w,) + data_widths
        aligns = ("r",) + tuple("l" for _ in hdrs)

        def _row(cells: list[str]) -> str:
            return _table_row_dynamic(cells, widths, aligns, settings)

        sep = _table_sep_dynamic(widths, settings)
        lines.append(sep)
        lines.append(_BOLD_ON)
        if hdrs and hdrs[0] == "Date / Bill":
            hdr1 = ["Sr", "Date"] + [
                ("Batch" if h == "Batch/Expiry" else h) for h in hdrs[1:]
            ]
            lines.append(_row(hdr1))
            hdr2 = ["", "Bill"] + [
                ("Expiry" if h == "Batch/Expiry" else "") for h in hdrs[1:]
            ]
            lines.append(_row(hdr2))
        elif "Batch/Expiry" in hdrs:
            bi = hdrs.index("Batch/Expiry")
            hdr1 = ["Sr"] + list(hdrs)
            hdr1[bi + 1] = "Batch"
            lines.append(_row(hdr1))
            hdr2 = [""] * len(widths)
            hdr2[bi + 1] = "Expiry"
            lines.append(_row(hdr2))
        else:
            lines.append(_row(["Sr"] + hdrs))
        lines.append(_BOLD_OFF)
        lines.append(sep)

        row_sep = style == "sign"
        for i, row in enumerate(shaped_rows, 1):
            cells = list(row)
            while len(cells) < len(hdrs):
                cells.append("")
            cells = cells[: len(hdrs)]
            for li, vals in enumerate(_schedule_row_physical_lines(cells, hdrs, data_widths)):
                sr = str(sr_offset + i) if li == 0 else ""
                lines.append(_row([sr] + vals))
            if row_sep and i < len(shaped_rows):
                lines.append(sep)

        if total_qty is not None:
            lines.append(sep)
            total_cells = [""] * len(widths)
            qty_i = next((i for i, h in enumerate(hdrs) if h == "Qty"), len(hdrs) - 1)
            label_i = qty_i
            while label_i > 0 and widths[label_i] < 5:
                label_i -= 1
            total_cells[label_i] = "Total"
            total_cells[qty_i + 1] = str(int(total_qty))
            lines.append(_row(total_cells))
        lines.append(sep)
        return "\n".join(lines)

    col_gap = " "
    gap_chars = max(0, n - 1) * len(col_gap) + _schedule_extra_gap_chars(hdrs, col_gap)
    data_w = line_w - 4
    data_widths = _schedule_portrait_col_widths(
        hdrs, data_w - gap_chars, col_gap=len(col_gap), style=style,
    )
    aligns = tuple("l" for _ in hdrs)
    lines.append(_BOLD_ON)
    if hdrs and hdrs[0] == "Date / Bill":
        hdr1 = list(hdrs)
        hdr1[0] = "Date"
        if "Batch/Expiry" in hdr1:
            hdr1[hdr1.index("Batch/Expiry")] = "Batch"
        lines.append(
            f"{'Sr':>3} {_schedule_spaced_values(hdr1, hdr1, data_widths, aligns, col_gap=col_gap)}"
        )
        hdr2 = [""] * len(hdrs)
        hdr2[0] = "Bill"
        if "Batch/Expiry" in hdrs:
            hdr2[hdrs.index("Batch/Expiry")] = "Expiry"
        lines.append(
            f"{'':>3} {_schedule_spaced_values(hdr2, hdrs, data_widths, aligns, col_gap=col_gap)}"
        )
    else:
        lines.append(
            f"{'Sr':>3} {_schedule_spaced_values(hdrs, hdrs, data_widths, aligns, col_gap=col_gap)}"
        )
    lines.append(_BOLD_OFF)

    plain_row_sep = "-" * line_w if style == "sign" else ""
    for i, row in enumerate(shaped_rows, 1):
        cells = list(row)
        while len(cells) < len(hdrs):
            cells.append("")
        cells = cells[: len(hdrs)]
        for li, vals in enumerate(_schedule_row_physical_lines(cells, hdrs, data_widths)):
            prefix = f"{sr_offset + i:>3} " if li == 0 else "    "
            lines.append(
                f"{prefix}{_schedule_spaced_values(vals, hdrs, data_widths, aligns, col_gap=col_gap)}"
            )
        if plain_row_sep and i < len(shaped_rows):
            lines.append(plain_row_sep)

    if total_qty is not None:
        if plain_row_sep:
            lines.append(plain_row_sep)
        else:
            lines.append("")
        qty_w = data_widths[-1] if data_widths else 4
        if "Sign" in hdrs and hdrs[-1] == "Sign" and len(data_widths) >= 2:
            qty_i = hdrs.index("Qty") if "Qty" in hdrs else len(data_widths) - 2
            qty_w = data_widths[qty_i]
            pad = 4 + sum(data_widths[:qty_i]) + qty_i * len(col_gap)
            lines.append(f"{'Total':>{pad}}{int(total_qty):>{qty_w}}")
        else:
            lines.append(f"{'Total':>{line_w - qty_w}}{int(total_qty):>{qty_w}}")
    return "\n".join(lines)


def format_schedule_report_text(
    title: str,
    subtitle: str,
    headers: list,
    rows: list,
    *,
    total_qty: int | None = None,
    dm_style: str = "classic",
    borders: bool = True,
) -> str:
    """ESC/P schedule report — classic or Sign preset; optional | / +---+ borders.

    Sign style paginates at 10 data rows per page (form feed between pages).
    """
    style = (dm_style or "classic").strip().lower()
    use_borders = bool(borders)
    hdrs, shaped_rows = _reshape_schedule_for_dm_style(headers, rows, style)

    if style == "sign":
        per = _SIGN_ROWS_PER_PAGE
        chunks = [
            shaped_rows[i:i + per] for i in range(0, max(1, len(shaped_rows)), per)
        ] if shaped_rows else [[]]
        page_count = len(chunks)
        blocks: list[str] = []
        for pi, chunk in enumerate(chunks):
            is_last = pi == page_count - 1
            blocks.append(
                _format_schedule_page_block(
                    title, subtitle, hdrs, chunk,
                    style=style,
                    use_borders=use_borders,
                    total_qty=total_qty if is_last else None,
                    sr_offset=pi * per,
                    page_no=pi + 1,
                    page_count=page_count,
                )
            )
        return f"\n{_PAGE_BREAK}\n".join(blocks)

    return _format_schedule_page_block(
        title, subtitle, hdrs, shaped_rows,
        style=style,
        use_borders=use_borders,
        total_qty=total_qty,
        sr_offset=0,
        page_no=1,
        page_count=1,
    )


def print_schedule_report_dot_matrix(
    title: str,
    subtitle: str,
    plain: dict,
    *,
    page_layout: str = "portrait",
    printer_name: str | None = None,
    dm_style: str | None = None,
    borders: bool | None = None,
) -> None:
    """Print schedule report on dot matrix via RAW ESC/P (after PDF is saved)."""
    if not sys.platform.startswith("win"):
        raise DotMatrixPrintError("Dot matrix RAW printing is only implemented on Windows.")

    try:
        from core.export_prefs import load_schedule_dm_prefs
        prefs = load_schedule_dm_prefs()
    except Exception:
        prefs = {"style": "classic", "borders": True}
    style = (dm_style if dm_style is not None else prefs.get("style")) or "classic"
    use_borders = bool(prefs.get("borders", True) if borders is None else borders)

    layout_key = (page_layout or "portrait").strip().lower()
    paper_u = "A4"
    headers = list(plain.get("headers") or [])
    rows = list(plain.get("rows") or [])
    total_qty = plain.get("total_qty")

    if layout_key.startswith("land"):
        text = format_report_text(title, headers, rows, paper=paper_u)
        draft = True
    else:
        text = format_schedule_report_text(
            title, subtitle, headers, rows,
            total_qty=total_qty,
            dm_style=style,
            borders=use_borders,
        )
        draft = True

    layout = _layout_for_paper(paper_u)
    payload = render_escp_document(
        text, paper=paper_u, layout=layout, form_feed_end=True, draft_a4=draft,
    )
    from core.printer_manager import PrinterManager, PrinterError

    printer = PrinterManager.resolve_dot_matrix_printer(printer_name)
    try:
        PrinterManager.print_raw_escp(payload, printer, copies=1)
    except PrinterError as exc:
        raise DotMatrixPrintError(str(exc)) from exc


def print_dot_matrix_report(
    title: str,
    headers: list,
    rows: list,
    settings: dict | None = None,
    *,
    paper: str = "A4",
    printer_name: str | None = None,
) -> None:
    """Print export report on dot matrix (A4 portrait, HTML-style plain columns)."""
    if not sys.platform.startswith("win"):
        raise DotMatrixPrintError("Dot matrix RAW printing is only implemented on Windows.")
    merged = dict(settings or {})
    try:
        from core.bill_config import load_bill_print_settings
        merged = {**load_bill_print_settings(), **merged}
    except Exception:
        pass
    paper_u = (paper or "A4").upper()
    text = format_report_text(title, headers, rows, merged, paper=paper_u)
    layout = _layout_for_paper(paper_u)
    payload = render_escp_document(text, paper=paper_u, layout=layout, form_feed_end=True)
    from core.printer_manager import PrinterManager, PrinterError

    printer = PrinterManager.resolve_dot_matrix_printer(printer_name)
    try:
        PrinterManager.print_raw_escp(payload, printer, copies=1)
    except PrinterError as exc:
        raise DotMatrixPrintError(str(exc)) from exc


def print_dot_matrix_reports_combined(
    sections: list[tuple[str, list, list]],
    settings: dict | None = None,
    *,
    paper: str = "A4",
    printer_name: str | None = None,
) -> None:
    if not sections:
        return
    if len(sections) == 1:
        title, headers, rows = sections[0]
        print_dot_matrix_report(title, headers, rows, settings, paper=paper, printer_name=printer_name)
        return
    if not sys.platform.startswith("win"):
        raise DotMatrixPrintError("Dot matrix RAW printing is only implemented on Windows.")
    merged = dict(settings or {})
    try:
        from core.bill_config import load_bill_print_settings
        merged = {**load_bill_print_settings(), **merged}
    except Exception:
        pass
    paper_u = (paper or "A4").upper()
    text = format_reports_combined(sections, merged, paper=paper_u)
    layout = _layout_for_paper(paper_u)
    payload = render_escp_document(text, paper=paper_u, layout=layout, form_feed_end=True)
    from core.printer_manager import PrinterManager, PrinterError

    printer = PrinterManager.resolve_dot_matrix_printer(printer_name)
    try:
        PrinterManager.print_raw_escp(payload, printer, copies=1)
    except PrinterError as exc:
        raise DotMatrixPrintError(str(exc)) from exc


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


def format_alignment_test(settings: dict | None = None) -> tuple[str, DmLayout]:
    """A6 alignment slip: rulers that show where a bill really lands.

    Built on the same layout, top offset, line spacing and end feed as a real A6
    bill, so it lands exactly where one would. Printed twice in a row it also
    proves the slip height: both copies must sit at the same place on their slips.
    """
    merged = dict(settings or {})
    merged["paper_size"] = "A6"
    dm = _a6_base_layout(merged)
    width = dm.line_width                       # 64 characters at 12 CPI
    chars_per_cm = 12 / 2.54
    spacing = max(20, min(32, int(dm.escp_line_spacing or 28)))
    line_cm = spacing / 216 * 2.54              # 9-pin: ESC 3 n = n/216 inch

    ticks = ["-"] * width
    numbers = [" "] * width
    k = 0
    while True:
        col = int(round(k * chars_per_cm))
        if col >= width:
            break
        ticks[col] = "|"
        for j, ch in enumerate(str(k)):
            if col + j < width:
                numbers[col + j] = ch
        k += 1

    def top_off() -> str:
        try:
            return f"{float(merged.get('dot_matrix_top_offset_cm', 0.8) or 0):.1f} cm"
        except (TypeError, ValueError):
            return "0.8 cm"

    slip_cm = dm.escp_slip_216 / 216 * 2.54 if dm.escp_slip_216 else 0
    info = [
        "SATPUDA ALIGNMENT TEST (left numbers = cm down)",
        f"Top offset {top_off()}  Spacing {spacing}  "
        + (f"Slip height {slip_cm:.1f} cm" if slip_cm else "Slip height: not set"),
        "",
        "1. ==== is where every bill starts. Too low on the slip:",
        "   raise Top offset. Top cut off: lower Top offset.",
        "2. Ruler is in cm; 0 = first character of the bill.",
        "3. Print this test 2 times: both must sit at the same",
        "   place on their slips. If the 2nd creeps, set Slip",
        "   height = perforation to perforation in cm.",
    ]

    rows = ["=" * width, "".join(numbers), "".join(ticks)]
    body_rows = max(len(info) + 2, _a6_slip_line_count(dm) - len(rows) - 1)
    for i in range(body_rows):
        n = len(rows)
        mark = f"{n * line_cm:4.1f}"
        text = info[i] if i < len(info) else ""
        room = width - len(mark) - 3
        rows.append(f"{mark} {text[:room].ljust(room)} |")
    rows.append("^" * width)
    return "\n".join(rows), dm


def print_alignment_test(settings: dict | None, printer_name: str | None) -> str:
    """Send the alignment slip RAW to the dot matrix queue; returns the queue used."""
    if not sys.platform.startswith("win"):
        raise DotMatrixPrintError("Dot matrix printing is only available on Windows.")
    from core.printer_manager import PrinterManager

    text, dm = format_alignment_test(settings)
    payload = render_escp_document(text, paper="A6", layout=dm)
    printer = PrinterManager.resolve_dot_matrix_printer(printer_name)
    try:
        from core.print_log import print_log
        print_log(dot_matrix_print_summary(settings, printer))
        print_log(
            f'alignment test printer="{printer}" lines={len(text.splitlines())} '
            f"spacing={dm.escp_line_spacing} top_rev={dm.escp_top_reverse} "
            f"slip_216={dm.escp_slip_216} left_cols={dm.escp_left_cols} payload_bytes={len(payload)}"
        )
    except Exception:
        pass
    # The same route a bill takes, or the test would prove nothing about bills.
    if PrinterManager.should_use_dot_matrix_gdi(printer):
        PrinterManager.print_text_gdi(
            text, printer, copies=1, paper_size="A6",
            placement=gdi_placement_for(settings, "A6"),
        )
    else:
        PrinterManager.print_raw_escp(payload, printer, copies=1)
    return printer
