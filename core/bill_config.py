"""Bill print templates and settings."""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


def _bill_settings_path() -> str:
    if getattr(sys, 'frozen', False):
        base = os.path.join(
            os.environ.get('LOCALAPPDATA', os.path.expanduser('~')),
            'VeterinaryApp',
        )
    else:
        base = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'config')
    return os.path.join(base, 'bill_print_settings.json')


SETTINGS_PATH = _bill_settings_path()

DEFAULT_BILL_PRINT_SETTINGS = {
    "template": "classic",
    "show_gst": True,
    "show_discount": True,
    "show_batch": True,
    "show_expiry": True,
    "show_doctor": True,
    "show_terms": True,
    "show_signature": True,
    "show_store_name": True,
    "show_store_address": True,
    "show_store_email": True,
    "show_store_phone": True,
    "show_store_gstin": True,
    "show_store_dl": True,
    "show_store_fssai": True,
    "show_blessing": True,
    "show_pay_mode": True,
    "show_bill_no": True,
    "show_bill_date": True,
    "show_patient_name": True,
    "show_patient_address": True,
    "show_doctor_name": True,
    "show_doctor_reg": True,
    "show_sr_no": True,
    "show_medicine_name": True,
    "show_qty": True,
    "show_mrp": True,
    "show_line_amount": True,
    "show_gst_strip": True,
    "show_total": True,
    "show_recovery_wish": True,
    "show_customer_due": False,
    "copies": 1,
    "pdf_save_layout": "two_copies",
    "sales_bill_save_dir": "",
    "print_slot_1": {
        # No bill_size_mode: absent means "inherit Paper & Copies", which is
        # what the slot merge documents. Shipping "normal" here made slot 1
        # override the global bill size for every shop, out of the box.
        "paper_size": "A5", "copies": 2, "label": "Print Sales 1",
        "a6_source_half": "bottom",
    },
    "print_slot_2": {
        "paper_size": "A6", "copies": 1, "label": "Print Sales 2",
        "bill_size_mode": "dot_matrix",
        "a6_source_half": "bottom",
    },
    "print_slot_1_key": "F7",
    "print_slot_2_key": "F8",
    "cash_online_enter_action": "save_bill",
    "due_rounding_enter_action": "save_bill",
    "blessing_line": "SHREE GANESHAY NAMAH",
    "recovery_wish_line": "I WISH FOR YOUR SPEEDY RECOVERY.",
    "gst_day_line": "HAVE A NICE DAY",
    "show_logo_top_right": False,
    "show_logo_center_watermark": False,
    "logo_top_right_margin_top": 1.2,
    "logo_top_right_margin_right": 1.5,
    "logo_watermark_opacity": 15,
    "paper_size": "A5",
    "orientation": "portrait",
    "margins": {"top": 0, "bottom": 0, "left": 0, "right": 0},
    "font_size_pct": 100,
    "border_thickness": 1.0,
    "bill_size_mode": "normal",
    "bill_size_pct": 92,
    "a5_single_copy_position": "bottom",
    "a6_source_half": "bottom",
    "a4_single_copy_position": "bottom",
    "a4_two_copy_layout": "side_by_side",
    "a5_single_copy_slot_migrated": False,
    # A6 carry-forward: split long bills into multiple print pages (same layout).
    "items_per_bill_page": 10,
    # Dot matrix A6 line spacing (ESC/P n/180 inch); 28–32 = roomier lines.
    "dot_matrix_line_spacing": 30,
    # Dot matrix A6 horizontal start (ESC $ units of 1/60 inch); 0 = left margin.
    "dot_matrix_hpos_60ths": 0,
    # Dot matrix bill: show | vertical borders (False = horizontal rules only).
    "dot_matrix_vertical_borders": True,
    # "compact" is the slip the shop picked on 25-09-2026: no SHREE GANESHAY NAMAH,
    # no GST INVOICE line, no GST, no recovery wish, signature between the nice line
    # and the total, and every line saved given to the medicine table. "classic" is
    # what was printed before it.
    "dot_matrix_style": "compact",
    # The three the shop picked from the printed samples on 25-09-2026.
    "dot_matrix_bold_total": True,      # Total struck twice by the printer
    "dot_matrix_bold_all": False,       # every line of the slip in bold (asked for 1 Oct 2026)
    "dot_matrix_item_count": True,      # "8 aushadhe, 59 nag" above HAVE A NICE DAY
    "dot_matrix_due_lines": True,       # Prev/Bill/Total Due, but only when money is owed
    # Dot matrix A6: top margin (cm) — how far BELOW the perforation the first
    # line starts. Fed forward after the paper is pulled back to the slip top.
    "dot_matrix_top_offset_cm": 1.0,
    # Dot matrix A6: blank space kept at the bottom of every slip (cm).
    "dot_matrix_bottom_margin_cm": 1.0,
    # Dot matrix A6: printable width (cm). The slip is 14.5 cm wide and the
    # tractor holes take about 0.8 cm at each side.
    "dot_matrix_print_width_cm": 12.9,
    # Dot matrix A6: forward tear feed after print (cm) — ejects slip without blank FF page.
    "dot_matrix_tear_feed_cm": 2.5,
    # Dot matrix A6: perforation-to-perforation height of one slip (cm). When set,
    # every bill advances the paper by exactly this much, whatever its length, so
    # the next bill starts at the same place on the next slip. 0 = off (tear feed).
    # 6 x 4 inch continuous paper: 4 inch = 10.16 cm (10.5 "A6" crept 3 mm a bill).
    "dot_matrix_slip_height_cm": 10.16,
    # Dot matrix A6: who positions the paper between bills. "printer" sends the
    # page length and a form feed and lets the printer's own tear-off park the
    # slip and pull it back - nothing to measure, and it cannot drift.
    # "software" feeds and reverses by dot_matrix_tear_gap_cm instead.
    "dot_matrix_tear_mode": "software",
    # Dot matrix A6: print head to tear edge (cm). Between bills the paper rests
    # with the perforation at the tear edge so the shop can tear the slip off;
    # each bill pulls back this far to start at the top of the next slip and
    # gives it back afterwards. Used only when a slip height is set.
    "dot_matrix_tear_gap_cm": 4.0,
    # Dot matrix A6: move the whole bill right (cm) from the printer's first
    # column. Nothing can print left of that column, so 0 is the leftmost.
    "dot_matrix_left_offset_cm": 0.8,
}


def get_items_per_bill_page(settings: Dict[str, Any] | None = None, *, dot_matrix: bool = False) -> int:
    """Max medicine lines on one bill page before carry-forward to the next slip."""
    settings = settings or {}
    try:
        n = int(settings.get("items_per_bill_page") or DEFAULT_BILL_PRINT_SETTINGS["items_per_bill_page"])
    except (TypeError, ValueError):
        n = int(DEFAULT_BILL_PRINT_SETTINGS["items_per_bill_page"])
    n = max(1, min(50, n))
    if dot_matrix:
        n = max(1, min(15, n))
    return n


BILL_SIZE_NORMAL = "normal"
BILL_SIZE_DOT_MATRIX = "dot_matrix"
_BILL_SIZE_MODES = (BILL_SIZE_NORMAL, BILL_SIZE_DOT_MATRIX)

# Slight shrink on full-size PDF print so dot-matrix / pin printers do not clip the top border.
PRINT_NORMAL_SAFE_SCALE = 0.96

BILL_PRINT_FIELD_OPTIONS = [
    ("Store header", [
        ("show_store_name", "Store name"),
        ("show_store_address", "Store address"),
        ("show_store_email", "Store email"),
        ("show_store_phone", "Store phone"),
        ("show_store_gstin", "Store GSTIN"),
        ("show_store_dl", "Store DL number"),
        ("show_store_fssai", "Store FSSAI"),
    ]),
    ("Invoice header", [
        ("show_blessing", "Shree Ganeshay Namah / blessing line"),
        ("show_bill_no", "Bill number"),
        ("show_bill_date", "Bill date"),
        ("show_patient_name", "Patient name"),
        ("show_patient_address", "Patient address"),
        ("show_customer_due", "Customer due (prev / bill / total)"),
        ("show_doctor", "Doctor block"),
        ("show_doctor_name", "Doctor name"),
        ("show_doctor_reg", "Doctor registration no."),
    ]),
    ("Medicine table", [
        ("show_sr_no", "Sr. number column"),
        ("show_medicine_name", "Medicine name column"),
        ("show_batch", "Batch column"),
        ("show_expiry", "Expiry column"),
        ("show_qty", "Quantity column"),
        ("show_mrp", "MRP column"),
        ("show_line_amount", "Amount column"),
    ]),
    ("Totals and footer", [
        ("show_gst_strip", "GST summary strip"),
        ("show_discount", "Discount (LESS) row"),
        ("show_gst", "GST amount row"),
        ("show_total", "Total row"),
        ("show_recovery_wish", "Recovery wish line"),
        ("show_signature", "Signature block"),
    ]),
]

AVAILABLE_TEMPLATES = {
    "classic": "GST Invoice",
    "legacy": "Tax Invoice",
}


@dataclass
class BillItem:
    name: str = ""
    batch: str = ""
    expiry: str = ""
    qty: float = 0.0
    rate: float = 0.0
    mrp: float = 0.0
    amount: float = 0.0
    gst_percent: float = 0.0
    manufacturer: str = ""


@dataclass
class BillContext:
    store_name: str = ""
    address: str = ""
    email: str = ""
    phone: str = ""
    gstin: str = ""
    dl_no: str = ""
    fssai: str = ""
    show_fssai_on_bill: bool = False
    logo_src: str = ""
    bill_no: str = ""
    bill_date: str = ""
    bill_date_landscape: str = ""
    cust_name: str = ""
    cust_phone: str = ""
    cust_addr: str = ""
    pay_mode: str = "CASH"
    doctor_name: str = ""
    doctor_reg: str = ""
    items: List[BillItem] = field(default_factory=list)
    sub_total: float = 0.0
    discount: float = 0.0
    taxable_amount: float = 0.0
    gst_amount: float = 0.0
    # CGST + SGST == gst_amount, split per line after the bill discount (core.bill_gst).
    cgst_amount: float = 0.0
    sgst_amount: float = 0.0
    grand_total: float = 0.0
    rounding: float = 0.0
    amount_paid: float = 0.0
    gst_enabled: bool = True
    blessing_line: str = "SHREE GANESHAY NAMAH"
    recovery_wish_line: str = "I WISH FOR YOUR SPEEDY RECOVERY."
    previous_due: float = 0.0
    previous_credit: float = 0.0
    due_amount: float = 0.0
    total_due: float = 0.0
    show_upi_qr: bool = False
    upi_qr_src: str = ""
    upi_qr_amount: float = 0.0
    # When printing carry-forward pages, continue Sr.No. across pages (0-based offset).
    item_sr_offset: int = 0
    # True on non-final carry-forward pages (show Continued... instead of bill Total).
    is_continued: bool = False
    # Sum of line amounts on this carry-forward page only.
    page_subtotal: float = 0.0
    # Multi-page dot matrix / PDF pagination (0-based index, total page count).
    page_index: int = 0
    page_count: int = 1
    # Supplier documents (purchase return, reorder): override invoice title / ref line.
    document_title: str = ""
    reference_line: str = ""
    extra_meta_rows: List[tuple] = field(default_factory=list)
    left_panel_lines: List[tuple] = field(default_factory=list)
    footer_strip_line: str = ""


def load_bill_print_settings() -> Dict[str, Any]:
    settings = dict(DEFAULT_BILL_PRINT_SETTINGS)
    try:
        if os.path.isfile(SETTINGS_PATH):
            with open(SETTINGS_PATH, "r", encoding="utf-8-sig") as fh:
                stored = json.load(fh)
            if isinstance(stored, dict):
                settings.update(stored)
    except Exception:
        pass
    # Merge slot defaults deeply and accept older key names from previous builds.
    for slot in (1, 2):
        key = f"print_slot_{slot}"
        slot_defaults = dict(DEFAULT_BILL_PRINT_SETTINGS.get(key) or {})
        raw_slot = settings.get(key)
        slot_cfg = dict(raw_slot) if isinstance(raw_slot, dict) else {}
        # Legacy flat keys fallback.
        legacy_map = {
            "paper_size": (
                settings.get(f"{key}_paper_size")
                or settings.get(f"{key}_paper")
            ),
            "copies": settings.get(f"{key}_copies"),
            "bill_size_mode": settings.get(f"{key}_bill_size_mode"),
            "label": settings.get(f"{key}_label"),
        }
        for field, value in legacy_map.items():
            if field not in slot_cfg and value not in (None, ""):
                slot_cfg[field] = value
        slot_defaults.update(slot_cfg)
        # Normalize common paper aliases.
        paper = str(slot_defaults.get("paper_size") or "").strip().upper()
        if paper in {"A6 LANDSCAPE", "A6_HORIZONTAL", "A6 HORIZONTAL"}:
            paper = "A6"
        elif paper in {"A5 PORTRAIT", "A5_VERTICAL"}:
            paper = "A5"
        elif paper in {"A4 PORTRAIT", "A4_VERTICAL"}:
            paper = "A4"
        if paper:
            slot_defaults["paper_size"] = paper
        settings[key] = slot_defaults
    # One-time legacy fix:
    # older builds used A5 slot-1 with 2 copies by default; when users switch to
    # top/bottom single-copy mode, migrate that legacy default to 1 copy once.
    try:
        migrated = bool(settings.get("a5_single_copy_slot_migrated"))
        single_pos = str(settings.get("a5_single_copy_position") or "bottom").strip().lower()
        slot1 = settings.get("print_slot_1") if isinstance(settings.get("print_slot_1"), dict) else {}
        paper1 = str(slot1.get("paper_size") or "A5").upper()
        copies1 = int(slot1.get("copies") or 1)
        if not migrated and single_pos in {"top", "bottom"} and paper1 == "A5" and copies1 == 2:
            slot1["copies"] = 1
            settings["print_slot_1"] = slot1
            settings["a5_single_copy_slot_migrated"] = True
            try:
                save_bill_print_settings(settings)
            except Exception:
                pass
    except Exception:
        pass
    return settings


def save_bill_print_settings(settings: Dict[str, Any]) -> None:
    path = _bill_settings_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    merged = dict(DEFAULT_BILL_PRINT_SETTINGS)
    merged.update(settings or {})
    payload = json.dumps(merged, indent=2)
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(payload)
            fh.flush()
        os.replace(tmp, path)
    except OSError:
        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(payload)
        except OSError:
            pass


def get_bill_margins_mm(settings: Dict[str, Any] | None = None) -> Dict[str, float]:
    """Page margins in mm (top / right / bottom / left)."""
    raw = (settings or {}).get("margins") or {}
    out: Dict[str, float] = {}
    for key in ("top", "right", "bottom", "left"):
        try:
            out[key] = max(0.0, min(20.0, float(raw.get(key, 0))))
        except (TypeError, ValueError):
            out[key] = 0.0
    return out


def uniform_bill_margin_mm(settings: Dict[str, Any] | None = None) -> float:
    """Single margin value when all sides match; otherwise returns top."""
    m = get_bill_margins_mm(settings)
    vals = [m["top"], m["right"], m["bottom"], m["left"]]
    if len({round(v, 2) for v in vals}) == 1:
        return vals[0]
    return m["top"]


def margins_to_css_padding(settings: Dict[str, Any] | None, fallback_pad: str) -> str:
    """CSS padding for bill body — uses settings margins or layout fallback."""
    m = get_bill_margins_mm(settings)
    if all(v == 0 for v in m.values()):
        return fallback_pad
    return f"{m['top']}mm {m['right']}mm {m['bottom']}mm {m['left']}mm"


def save_uniform_bill_margin(settings: Dict[str, Any], margin_mm: float) -> None:
    """Set the same margin on all four sides."""
    val = max(0.0, min(20.0, float(margin_mm)))
    settings["margins"] = {"top": val, "right": val, "bottom": val, "left": val}


def bill_size_mode_label(mode: str) -> str:
    if (mode or "").lower() == BILL_SIZE_DOT_MATRIX:
        return "Dot matrix (smaller bill)"
    return "Full size"


def bill_size_mode_from_label(label: str) -> str:
    text = (label or "").strip()
    if text == bill_size_mode_label(BILL_SIZE_DOT_MATRIX):
        return BILL_SIZE_DOT_MATRIX
    return BILL_SIZE_NORMAL


def bill_size_mode_combo_values() -> tuple[str, ...]:
    return tuple(bill_size_mode_label(m) for m in _BILL_SIZE_MODES)


def get_bill_size_mode(settings: Dict[str, Any] | None) -> str:
    mode = (settings or {}).get("bill_size_mode") or BILL_SIZE_NORMAL
    return mode if str(mode).lower() in _BILL_SIZE_MODES else BILL_SIZE_NORMAL


def get_bill_size_pct(settings: Dict[str, Any] | None) -> float:
    """Shrink percentage for dot-matrix mode (85–98, default 92)."""
    try:
        return max(85.0, min(98.0, float((settings or {}).get("bill_size_pct") or 92)))
    except (TypeError, ValueError):
        return 92.0


def get_bill_content_scale(settings: Dict[str, Any] | None) -> float:
    """
    Scale factor for the invoice box (not the paper page).

    Dot-matrix mode shrinks the bill so borders are not clipped by pins.
    Full-size PDF print uses a small safe inset on pin printers.
    """
    settings = settings or {}
    if get_bill_size_mode(settings) == BILL_SIZE_DOT_MATRIX:
        return get_bill_size_pct(settings) / 100.0
    if settings.get("for_pdf_save"):
        return PRINT_NORMAL_SAFE_SCALE
    margin = uniform_bill_margin_mm(settings)
    if margin <= 0:
        return 1.0
    return max(0.88, 1.0 - margin * 0.012)


def get_bill_body_padding(settings: Dict[str, Any] | None, fallback_pad: str) -> str:
    """CSS body padding — zero for PDF print; minimal gap for dot matrix preview."""
    settings = settings or {}
    if settings.get("for_pdf_save"):
        return "0"
    if get_bill_size_mode(settings) == BILL_SIZE_DOT_MATRIX:
        return "0.8mm"
    return margins_to_css_padding(settings, fallback_pad)


def get_bill_layout_copies(settings: Dict[str, Any] | None, paper: str | None = None) -> int:
    """Bill copies laid out on one printed page (Print Sales slot setting)."""
    settings = settings or {}
    paper = (paper or settings.get("paper_size") or "A5").upper()
    if paper == "A6":
        max_c = 1
    elif paper == "A5":
        max_c = 2
    else:
        max_c = 4
    if settings.get("bill_copies") is not None:
        return max(1, min(max_c, int(settings["bill_copies"])))
    return 1


def get_print_slot_page_copies(
    settings: Dict[str, Any] | None,
    slot: int,
) -> int:
    """
    How many times to send the composed bill PDF to the printer for Print Sales.

    Bill copies are already laid out inside the PDF (Print Sales slot setting).
    Page copies (Paper & Copies) only applies to Print Bill / F5 save — not here,
    so choosing 2 bill copies does not accidentally become 4.
    """
    return 1


def get_page_copies(settings: Dict[str, Any] | None = None) -> int:
    """How many times each page is sent to the printer (Paper & Copies setting)."""
    settings = settings or load_bill_print_settings()
    return max(1, min(10, int(settings.get("copies") or 1)))


def get_print_slot_settings(settings: Dict[str, Any] | None, slot: int) -> Dict[str, Any]:
    """Merge base bill settings with print slot 1 or 2 preset."""
    base = dict(settings or load_bill_print_settings())
    key = f"print_slot_{slot}"
    slot_cfg = base.get(key) or {}
    if isinstance(slot_cfg, dict):
        # a5_single_copy_position belongs here too: without it a slot set to A5
        # fell back to the global half, so the "Half position" dropdown beside
        # that slot did nothing at all.
        for field in (
            "paper_size",
            "orientation",
            "a6_source_half",
            "a5_single_copy_position",
            "a4_two_copy_layout",
            "a4_single_copy_position",
        ):
            if field in slot_cfg and slot_cfg[field] is not None:
                base[field] = slot_cfg[field]
        # Only an explicit dot-matrix choice on the slot overrides the global default.
        # Slots saved as "Full size" inherit Paper & Copies bill size (dot matrix, shrink %).
        # Three states, not two. ABSENT (or blank) means inherit Paper & Copies;
        # an explicit choice on the slot wins either way. It used to be two, and
        # print_slot_1 shipped saying "normal" -- so "Bill size" and "Shrink %"
        # in Paper & Copies were overridden for every shop before they could
        # apply. Making "normal" inert instead would have left a shop whose
        # global is dot matrix with shrunk bills on every slot and no way back.
        slot_mode = str(slot_cfg.get("bill_size_mode") or "").lower()
        if slot_mode == BILL_SIZE_DOT_MATRIX:
            base["bill_size_mode"] = BILL_SIZE_DOT_MATRIX
        elif slot_mode == BILL_SIZE_NORMAL:
            base["bill_size_mode"] = BILL_SIZE_NORMAL
        if get_bill_size_mode(base) == BILL_SIZE_DOT_MATRIX:
            base["bill_size_pct"] = get_bill_size_pct(
                slot_cfg if slot_cfg.get("bill_size_pct") is not None else base
            )
    paper = (base.get("paper_size") or "A5").upper()
    base["paper_size"] = paper
    base["orientation"] = "portrait"
    if isinstance(slot_cfg, dict) and slot_cfg.get("copies") is not None:
        if paper == "A6":
            max_c = 1
        elif paper == "A5":
            max_c = 2
        else:
            max_c = 4
        base["bill_copies"] = max(1, min(max_c, int(slot_cfg.get("copies") or 1)))
    else:
        base["bill_copies"] = 1
    # base["copies"] stays as page copies (Paper & Copies) — not overwritten by slot.
    base["margins"] = get_bill_margins_mm(base)
    return base


def get_print_slot_key(settings: Dict[str, Any] | None, slot: int) -> str:
    base = settings or load_bill_print_settings()
    return str(base.get(f"print_slot_{slot}_key") or ("F7" if slot == 1 else "F8")).upper()


SALES_ENTER_ACTIONS: Dict[str, str] = {
    "save_bill": "Save Bill",
    "print_slot_1": "Print Sales 1",
    "print_slot_2": "Print Sales 2",
}


def get_sales_enter_action(
    settings: Dict[str, Any] | None,
    key: str,
    default: str = "save_bill",
) -> str:
    allowed = frozenset(SALES_ENTER_ACTIONS)
    val = str((settings or {}).get(key) or default).strip()
    return val if val in allowed else default


def sales_enter_action_label(action_key: str) -> str:
    return SALES_ENTER_ACTIONS.get(action_key, SALES_ENTER_ACTIONS["save_bill"])


def sales_enter_action_from_label(label: str) -> str:
    for action_key, action_label in SALES_ENTER_ACTIONS.items():
        if action_label == label:
            return action_key
    return "save_bill"


def get_density_class(item_count: int) -> str:
    if item_count >= 18:
        return "density-max"
    if item_count >= 12:
        return "density-tight"
    if item_count >= 8:
        return "density-compact"
    return "density-normal"


def apply_print_bill_layout(
    settings: Dict[str, Any],
    *,
    print_slot_copies: int | None = None,
) -> Dict[str, Any]:
    """Portrait upright PDF layout for Print Sales / saved bills (A5, A6, or classic A4)."""
    from core.bill_save_prefs import (
        PDF_LAYOUT_ONE_A6,
        PDF_LAYOUT_ONE_BOTTOM,
        PDF_LAYOUT_ONE_TOP,
        PDF_LAYOUT_TWO_COPIES,
        load_pdf_save_layout,
    )

    out = dict(settings)
    paper = (out.get("paper_size") or "A5").upper()
    copies = max(1, min(4, int(print_slot_copies or out.get("bill_copies") or 1)))

    if paper == "A6":
        # Dot-matrix counters feed A5; A6 is derived from top/bottom half of A5.
        out["for_pdf_save"] = True
        out["orientation"] = "portrait"
        out["paper_size"] = "A5"
        a6_half = str(out.get("a6_source_half") or "bottom").strip().lower()
        out["pdf_save_layout"] = PDF_LAYOUT_ONE_TOP if a6_half == "top" else PDF_LAYOUT_ONE_BOTTOM
        out["print_paper_hint"] = "A6"
        return out

    if paper == "A4":
        copies = max(1, min(4, copies))
        out["bill_copies"] = copies
        pos = str(
            out.get("a4_single_copy_position")
            or out.get("a5_single_copy_position")
            or "bottom"
        ).strip().lower()
        out["a4_single_copy_position"] = pos
        two_layout = str(out.get("a4_two_copy_layout") or "side_by_side").strip().lower()
        out["a4_two_copy_layout"] = (
            "top_bottom" if two_layout in ("top_bottom", "stacked", "top and bottom") else "side_by_side"
        )
        out.pop("for_pdf_save", None)
        out.pop("pdf_save_layout", None)
        return out

    if paper != "A5":
        out.pop("for_pdf_save", None)
        out.pop("pdf_save_layout", None)
        return out

    out["for_pdf_save"] = True
    out["orientation"] = "portrait"
    if print_slot_copies is not None:
        copies = max(1, min(2, int(print_slot_copies or 1)))
    elif copies > 2:
        copies = 2

    if copies >= 2:
        out["pdf_save_layout"] = PDF_LAYOUT_TWO_COPIES
    else:
        # A5 single copy position can be selected from Paper & Copies settings.
        single_pos = str(out.get("a5_single_copy_position") or "bottom").strip().lower()
        out["pdf_save_layout"] = PDF_LAYOUT_ONE_TOP if single_pos == "top" else PDF_LAYOUT_ONE_BOTTOM

    # Print Sales: never let saved F5 layout override slot copy count.
    if print_slot_copies is None:
        saved = load_pdf_save_layout()
        if copies >= 2:
            out["pdf_save_layout"] = PDF_LAYOUT_TWO_COPIES
        elif saved == PDF_LAYOUT_ONE_A6:
            out["pdf_save_layout"] = PDF_LAYOUT_ONE_A6
        elif saved == PDF_LAYOUT_ONE_TOP:
            out["pdf_save_layout"] = PDF_LAYOUT_ONE_TOP
        elif copies < 2:
            single_pos = str(out.get("a5_single_copy_position") or "bottom").strip().lower()
            out["pdf_save_layout"] = PDF_LAYOUT_ONE_TOP if single_pos == "top" else PDF_LAYOUT_ONE_BOTTOM
    return out


def print_all_bills_per_page(paper: str) -> int:
    """How many different bills fit on one physical page for Print All."""
    p = (paper or "A6").upper()
    if p == "A4":
        return 4
    if p == "A5":
        return 2
    return 1


def print_all_total_pages(bill_count: int, paper: str) -> int:
    if bill_count <= 0:
        return 0
    per = print_all_bills_per_page(paper)
    return max(1, (bill_count + per - 1) // per)


def apply_a5_portrait_bill_layout(
    settings: Dict[str, Any],
    *,
    print_slot_copies: int | None = None,
) -> Dict[str, Any]:
    """Back-compat alias for apply_print_bill_layout."""
    return apply_print_bill_layout(settings, print_slot_copies=print_slot_copies)


def render_bill_html(ctx: BillContext, settings: Optional[Dict[str, Any]] = None) -> str:
    """Render full printable HTML for the selected template."""
    settings = settings or load_bill_print_settings()
    template = (settings.get("template") or "classic").lower()
    if template == "legacy":
        from bill_templates.legacy import render_legacy_bill_html
        return render_legacy_bill_html(ctx, settings)
    from bill_templates.classic import render_classic_bill_html
    return render_classic_bill_html(ctx, settings)
