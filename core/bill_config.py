"""Bill print templates and settings."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

CONFIG_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config")
SETTINGS_PATH = os.path.join(CONFIG_DIR, "bill_print_settings.json")

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
    "copies": 2,
    "blessing_line": "SHREE GANESHAY NAMAH",
    "paper_size": "A5",
    "orientation": "landscape",
    "margins": {"top": 8, "bottom": 8, "left": 8, "right": 8},
    "font_size_pct": 100,
    "border_thickness": 1.0,
}

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
        ("show_blessing", "Blessing line"),
        ("show_pay_mode", "Payment mode in title"),
        ("show_bill_no", "Bill number"),
        ("show_bill_date", "Bill date"),
        ("show_patient_name", "Patient name"),
        ("show_patient_address", "Patient address"),
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
        ("show_terms", "Terms block"),
        ("show_signature", "Signature block"),
    ]),
]

AVAILABLE_TEMPLATES = {
    "classic": "GST Vertical Rotated (Classic)",
    "legacy": "Tax Invoice — Side by Side",
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
    grand_total: float = 0.0
    rounding: float = 0.0
    amount_paid: float = 0.0
    gst_enabled: bool = True
    blessing_line: str = "SHREE GANESHAY NAMAH"
    previous_due: float = 0.0
    due_amount: float = 0.0


def load_bill_print_settings() -> Dict[str, Any]:
    settings = dict(DEFAULT_BILL_PRINT_SETTINGS)
    try:
        if os.path.isfile(SETTINGS_PATH):
            with open(SETTINGS_PATH, "r", encoding="utf-8") as fh:
                stored = json.load(fh)
            if isinstance(stored, dict):
                settings.update(stored)
    except Exception:
        pass
    return settings


def save_bill_print_settings(settings: Dict[str, Any]) -> None:
    os.makedirs(CONFIG_DIR, exist_ok=True)
    merged = dict(DEFAULT_BILL_PRINT_SETTINGS)
    merged.update(settings or {})
    with open(SETTINGS_PATH, "w", encoding="utf-8") as fh:
        json.dump(merged, fh, indent=2)


def get_density_class(item_count: int) -> str:
    if item_count >= 18:
        return "density-max"
    if item_count >= 12:
        return "density-tight"
    if item_count >= 8:
        return "density-compact"
    return "density-normal"


def render_bill_html(ctx: BillContext, settings: Optional[Dict[str, Any]] = None) -> str:
    """Render full printable HTML for the selected template."""
    settings = settings or load_bill_print_settings()
    template = (settings.get("template") or "classic").lower()
    if template == "legacy":
        from bill_templates.legacy import render_legacy_bill_html
        return render_legacy_bill_html(ctx, settings)
    from bill_templates.classic import render_classic_bill_html
    return render_classic_bill_html(ctx, settings)
