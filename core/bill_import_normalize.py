"""
Normalize HSN, pack, and manufacturer from bill import (Gemini / OCR).

Pack must be app-friendly:
  1X10  -> 10        (tablets/capsules per strip)
  1X60GM -> 60GM
  1X55ML -> 55ML
  10PIC / 10 PCS -> 10
  10's / 15's -> 10 / 15
  100ML / 250ML -> 100ML / 250ML

VINAR bills: columns are MRP, Name, HSN, Pack, MFG, Batch, Exp, Qty, Free,
SGST%, CGST%, Rate, Dis, Amt — SGST/CGST are half-rates (2.5+2.5=5%, 9+9=18%).
"""
from __future__ import annotations

import re
from typing import Any, Dict

from core.purchase_invoice_engine import normalize_total_gst_pct, predict_gst_from_hsn, resolve_line_gst_percent

# Bill prefix before real HSN (Om Sai DM column + HSN)
_HSN_PREFIXES = frozenset({"902", "1107", "110"})

# Values that belong in GST columns, not pack (VINAR SGST/CGST half-rates)
_GST_HALF_VALUES = frozenset({
    0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 6.0, 9.0, 12.0, 14.0,
})

_STRIP_TYPES = frozenset({
    "tablet", "capsule", "bolus", "cap", "tab",
})

# 500ML, 100GM, etc. — pack size, not tablets-per-strip.
_VOLUME_PACK_RE = re.compile(
    r"^\d+(?:\.\d+)?\s*(GM|G|MG|ML|MD|KG|L|LI|LTR|LT)$",
    re.IGNORECASE,
)


def normalize_bill_hsn(hsn: Any) -> str:
    """
    Keep HSN digits exactly as printed on the bill (no padding or truncation).
    Strips non-digits only. Om Sai / JCR bills may prefix DM no. before HSN.
    """
    raw = str(hsn or "").strip()
    if not raw:
        return ""
    groups = re.findall(r"\d{3,8}", raw.replace("/", " "))
    if len(groups) >= 2:
        first = groups[0]
        if first in _HSN_PREFIXES or len(first) <= 4:
            candidate = groups[-1]
            if len(candidate) >= 3:
                return candidate
    digits = re.sub(r"\D", "", raw)
    return digits


def normalize_bill_manufacturer(mfg: Any) -> str:
    """Clean MFG / Mfr / Com column value."""
    s = re.sub(r"\s+", " ", str(mfg or "").strip())
    if not s or s in ("-", "--", "...", "..", "0", "1"):
        return ""
    s = s.strip(".,|-")
    if len(s) <= 6 and s.isalpha():
        return s.upper()
    return s[:40].strip()


def normalize_bill_pack(
    pack: Any,
    medicine_type: str = "",
    product_name: str = "",
) -> str:
    """Convert bill pack column to value the Purchase page can calculate with."""
    text = re.sub(r"\s+", " ", str(pack or "").strip())
    if not text:
        return ""
    compact = re.sub(r"\s+", "", text.upper())
    med = (medicine_type or "").lower()
    name_u = (product_name or "").upper()
    is_strip_item = _is_strip_product(med, name_u)

    # 1X10, 1 X 10, 1×10
    m = re.match(r"^1[Xx×](\d+(?:\.\d+)?)(GM|G|ML|MG|KG|L|LI|LT|LTR|MD|MCG)?\.?$", compact, re.I)
    if m:
        num, unit = m.group(1), (m.group(2) or "").upper()
        if not unit:
            return num
        if unit in ("G", "GM"):
            return "{}{}".format(num, "GM")
        if unit == "ML":
            return "{}{}".format(num, "ML")
        if unit == "KG":
            return "{}{}".format(num, "KG")
        if unit in ("L", "LI", "LT", "LTR"):
            return "{}{}".format(num, "L")
        if unit == "MD":
            return "{}{}".format(num, "MD")
        return num

    # 10's, 15's, 60's
    m = re.match(r"^(\d+)['']S?$", compact.replace("'", ""))
    if m:
        return m.group(1)

    # 10PIC, 10-PIC, 10 PCS, 1PIC, 10 PIC
    m = re.match(r"^(\d+)[-]?(PIC|PICS|PCS|PC|PIECES?|P)$", compact, re.I)
    if m:
        return m.group(1) if is_strip_item else "{} {}".format(m.group(1), "PIC")

    # 100ML, 60GM, 250ML, 5ML, 450G
    m = re.match(r"^(\d+(?:\.\d+)?)(ML|GM|G|KG|L|LI|LTR|MD|MG)$", compact, re.I)
    if m:
        num, unit = m.group(1), m.group(2).upper()
        if unit == "G":
            unit = "GM"
        return "{}{}".format(num, unit)

    # 1Strip, 1 Stri, 1STRIP
    m = re.match(r"^(\d+)(STRIP|STRI|STR|ST)$", compact, re.I)
    if m:
        return m.group(1)

    # 6-BO, 1 BO (bolus pack count)
    m = re.match(r"^(\d+)[-]?(BO|BOLUS|BOT)$", compact, re.I)
    if m:
        return m.group(1)

    # 1 P, 10 P (pieces)
    m = re.match(r"^(\d+)[-]?P$", compact, re.I)
    if m:
        return m.group(1)

    # Plain number for strip types
    if is_strip_item and re.match(r"^\d+$", compact):
        return compact

    return text


def pack_is_volume_or_weight(pack: Any) -> bool:
    """True when pack is a liquid/powder size (500ML, 100GM), not strip count."""
    text = re.sub(r"\s+", "", str(pack or "").strip())
    if not text:
        return False
    return bool(_VOLUME_PACK_RE.match(text))


def tablets_per_strip_from_pack(pack: Any) -> int:
    """Tablets/capsules per strip from normalized or raw pack."""
    if pack_is_volume_or_weight(pack):
        return 1
    norm = normalize_bill_pack(pack)
    if re.match(r"^\d+$", norm):
        try:
            return max(1, int(norm))
        except ValueError:
            pass
    raw = re.sub(r"\s+", "", str(pack or "").upper())
    m = re.search(r"1[Xx×](\d+)", raw)
    if m:
        return max(1, int(m.group(1)))
    # 2X12, 4X6 — some bills encode total tablets per strip this way
    m = re.match(r"^(\d+)[Xx×](\d+)$", raw)
    if m:
        left, right = int(m.group(1)), int(m.group(2))
        if left > 1 and right > 0:
            return max(1, left * right)
        if right > 0:
            return right
    nums = re.findall(r"\d+", str(pack or ""))
    if len(nums) >= 2:
        return max(1, int(nums[-1]))
    if nums:
        return max(1, int(nums[0]))
    return 1


def _fix_batch_looks_like_price(rec: dict) -> None:
    """Clear batch when OCR put MRP/rate/GST there; recover MRP when missing."""
    batch = str(rec.get("batch") or "").strip()
    if not batch:
        return
    mrp = _to_float(rec.get("mrp"))
    rate = _to_float(rec.get("rate"))
    try:
        as_price = float(batch.replace(",", ""))
    except ValueError:
        as_price = None
    if as_price is not None:
        if mrp > 0 and abs(as_price - mrp) <= 0.05:
            rec["batch"] = ""
            return
        if rate > 0 and abs(as_price - rate) <= 0.05:
            rec["batch"] = ""
            return
        if as_price in _GST_HALF_VALUES:
            rec["batch"] = ""
            return
        if mrp <= 0 and as_price >= 1.0:
            rec["mrp"] = as_price
            rec["batch"] = ""
            return
    m = re.match(r"^\d{2,4}$", batch)
    if m:
        iv = int(m.group(0))
        if mrp > 0 and abs(iv - mrp) <= 1.0:
            rec["batch"] = ""
        elif mrp <= 0 and iv >= 10:
            rec["mrp"] = float(iv)
            rec["batch"] = ""


def _fix_shrigurudeo_line_gst(rec: dict, supplier_name: str = "") -> None:
    """SHRIGURUDEO bills: GST% 12 on medicines (300x HSN); '-' on feed (2309/23099)."""
    supplier = (supplier_name or rec.get("_supplier") or "").upper()
    if "SHRIGURUDEO" not in supplier and "SADGURU" not in supplier:
        return
    hsn = re.sub(r"\D", "", str(rec.get("hsn_code") or ""))
    if len(hsn) < 4:
        return
    g = float(rec.get("gst_pct") or 0)
    # OCR sometimes reads 6% (half CGST) in the GST% column — full rate is 12%.
    if abs(g - 6.0) < 0.05:
        rec["gst_pct"] = 12.0
        rec.pop("gst_explicit_zero", None)
        return
    # Feed/supplement chapters — keep 0% when bill shows '-'.
    if hsn.startswith(("2309", "23099")) or hsn.startswith("2304"):
        return
    # Veterinary medicine HSN (300x): blank GST column means OCR miss, not exempt.
    if rec.get("gst_explicit_zero") and hsn.startswith("300"):
        rec["gst_pct"] = 12.0
        rec.pop("gst_explicit_zero", None)
        rec["gst_inferred_shrigurudeo"] = True


def normalize_gemini_item_record(rec: dict, supplier_name: str = "") -> dict:
    """Apply HSN/pack/mfg/GST fixes to one Gemini item before _item_from_record."""
    _fix_misplaced_columns(rec, supplier_name)
    _fix_discount_column(rec)
    _fix_batch_looks_like_price(rec)
    med = str(rec.get("medicine_type") or "")
    name = str(rec.get("name") or "")
    rec["hsn_code"] = normalize_bill_hsn(rec.get("hsn_code"))
    rec["pack"] = normalize_bill_pack(rec.get("pack"), med, name)
    if not rec["pack"] or _pack_looks_like_gst(rec.get("pack"), med, name, rec):
        recovered = _recover_pack_from_row(rec)
        if recovered:
            rec["pack"] = normalize_bill_pack(recovered, med, name)
    mfg = normalize_bill_manufacturer(
        rec.get("manufacturer") or rec.get("mfg") or rec.get("mfr") or rec.get("com")
    )
    rec["manufacturer"] = mfg
    rec["gst_pct"] = _normalize_line_gst(rec)
    _fix_shrigurudeo_line_gst(rec, supplier_name)
    rec["gemini_import"] = True
    # Type / schedule / content are not on bill photos — detect or DB-fill later.
    rec.pop("medicine_type", None)
    rec.pop("schedule", None)
    rec.pop("content_drug", None)
    return rec


def _normalize_line_gst(rec: dict) -> float:
    g_raw = rec.get("gst_pct")
    if g_raw is None and rec.get("gst") is not None:
        g_raw = rec.get("gst")
    c_raw = rec.get("cgst_pct") or rec.get("cgst") or rec.get("CGST")
    s_raw = rec.get("sgst_pct") or rec.get("sgst") or rec.get("SGST")

    has_total_col = _cell_has_value(g_raw)
    if has_total_col or rec.get("gst_from_bill"):
        gst = resolve_line_gst_percent(g_raw if has_total_col else "", c_raw, s_raw)
        if gst == 0.0:
            rec["gst_explicit_zero"] = True
            rec["cgst_pct"] = 0
            rec["sgst_pct"] = 0
        rec["gst_pct"] = gst
        return gst

    gst = resolve_line_gst_percent(g_raw, c_raw, s_raw)
    if gst > 0:
        rec["gst_pct"] = gst
        return gst

    if rec.get("gst_from_bill") or rec.get("gemini_import"):
        rec["gst_pct"] = 0.0
        return 0.0

    hsn = str(rec.get("hsn_code") or "")
    if hsn:
        predicted = predict_gst_from_hsn(hsn)
        if predicted > 0:
            rec["gst_pct"] = predicted
            return predicted
    rec["gst_pct"] = float(rec.get("gst_pct") or 0)
    return rec["gst_pct"]


def _cell_has_value(value: Any) -> bool:
    return str(value if value is not None else "").strip() != ""


def _row_has_dedicated_gst(rec: dict) -> bool:
    """True when the row already has GST from its own column (PDF/CSV/Excel/Gemini)."""
    for key in (
        "gst_pct", "gst", "GST",
        "cgst_pct", "cgst", "CGST",
        "sgst_pct", "sgst", "SGST",
    ):
        if _cell_has_value(rec.get(key)):
            return True
    return False


def _pack_looks_like_gst(
    pack: Any,
    medicine_type: str = "",
    product_name: str = "",
    rec: dict | None = None,
) -> bool:
    """
    Only for bills with NO GST column (e.g. some VINAR scans where SGST/CGST
    land in the pack field). When gst_pct / sgst / cgst are present, pack is
    always tablets-per-strip or pack size — never tax.
    """
    if rec and _row_has_dedicated_gst(rec):
        return False
    text = str(pack or "").strip().replace(",", "")
    if not text:
        return False
    try:
        val = float(text)
    except ValueError:
        return False
    # Plain integers (6, 10, 12, 24…) are pack/strip sizes, not GST half-rates.
    is_whole = "." not in text and abs(val - round(val)) < 0.001
    if is_whole and 1 <= int(round(val)) <= 100:
        return False
    if val in _GST_HALF_VALUES:
        return True
    if val in (5.0, 12.0, 18.0, 28.0) and "." in text:
        return True
    return False


def _fix_discount_column(rec: dict) -> None:
    """Bill photos: DIS=0 while MRP>Rate is wholesale margin, not a line discount."""
    disc = _to_float(rec.get("discount_pct") or rec.get("disc") or rec.get("discount") or 0)
    qty = _to_float(rec.get("qty"))
    rate = _to_float(rec.get("rate"))
    amount = _to_float(rec.get("amount"))
    gst = _to_float(rec.get("gst_pct") or 0)

    # GST half-rates sometimes land in discount_pct by mistake (2.5, 9, 12…)
    if disc in _GST_HALF_VALUES or disc in (5.0, 12.0, 18.0, 28.0):
        if gst <= 0:
            rec["gst_pct"] = disc * 2 if disc in _GST_HALF_VALUES else disc
        disc = 0.0

    if qty > 0 and rate > 0 and amount > 0:
        expected = round(qty * rate, 2)
        if abs(expected - amount) <= max(0.05, amount * 0.01):
            disc = 0.0

    if disc <= 0:
        rec["discount_pct"] = 0
        rec["disc_column_value"] = 0
        rec["disc_column_type"] = "ABSENT"
    else:
        rec["discount_pct"] = disc


def _fix_misplaced_columns(rec: dict, supplier_name: str) -> None:
    """Fix VINAR-style column swaps (SGST/CGST landing in pack or gst_pct)."""
    med = str(rec.get("medicine_type") or "")
    name = str(rec.get("name") or "")
    pack = str(rec.get("pack") or "").strip()
    if _pack_looks_like_gst(pack, med, name, rec):
        half = _to_float(pack)
        if not rec.get("sgst_pct") and not rec.get("sgst"):
            rec["sgst_pct"] = half
        if not rec.get("cgst_pct") and not rec.get("cgst"):
            rec["cgst_pct"] = half
        rec["pack"] = ""

    gst = _to_float(rec.get("gst_pct"))
    if gst in _GST_HALF_VALUES and gst not in (0.0,):
        if not rec.get("sgst_pct"):
            rec["sgst_pct"] = gst
        if not rec.get("cgst_pct"):
            rec["cgst_pct"] = gst

    # Rate sometimes misread as pack (e.g. 12.00, 64.00)
    if pack and re.match(r"^\d+\.\d{2}$", pack):
        try:
            pv = float(pack)
            rate = _to_float(rec.get("rate"))
            if rate > 0 and abs(pv - rate) < 0.02:
                rec["pack"] = ""
        except ValueError:
            pass

    supplier_u = (supplier_name or rec.get("_supplier") or "").upper()
    if "VINAR" in supplier_u and not rec.get("pack"):
        _apply_vinar_pack_hints(rec)


def _recover_pack_from_row(rec: dict) -> str:
    """Find 1X10 / 60GM etc. anywhere in the row when pack column was wrong."""
    skip = {
        "qty", "free_qty", "rate", "amount", "mrp", "gst_pct",
        "discount_pct", "source_row", "hsn_chapter", "_supplier",
    }
    blob = " ".join(
        str(rec.get(k) or "")
        for k in sorted(rec.keys())
        if k not in skip
    )
    m = re.search(r"1[Xx×]\s*(\d+(?:\.\d+)?)\s*(GM|G|ML|MG|KG|L|MD)?", blob, flags=re.I)
    if m:
        unit = (m.group(2) or "").upper()
        num = m.group(1)
        if unit in ("G", "GM"):
            return "{}GM".format(num)
        if unit == "ML":
            return "{}ML".format(num)
        if unit == "MD":
            return "{}MD".format(num)
        return num

    name = str(rec.get("name") or "")
    name_u = name.upper()
    if re.search(r"\bTAB\b", name_u):
        m = re.search(r"(?:TAB|CAP)[\s\-]*(\d+)\b", name_u)
        if m:
            return m.group(1)
    if any(k in name_u for k in ("GEL", "CREAM", "LOTION", "OINT")):
        m = re.search(r"(\d+)\s*GM", blob, flags=re.I)
        if m:
            return "{}GM".format(m.group(1))
    if any(k in name_u for k in ("SYRUP", "SYP", "LIQ", "SANITIZER", "INJ", "ML")):
        m = re.search(r"(\d+)\s*ML", blob, flags=re.I)
        if m:
            return "{}ML".format(m.group(1))
    if "FACEWASH" in name_u or "FACE WASH" in name_u:
        m = re.search(r"(\d+)\s*GM", blob, flags=re.I)
        if m:
            return "{}GM".format(m.group(1))
    return ""


def _apply_vinar_pack_hints(rec: dict) -> None:
    """Common VINAR pack patterns from product name when pack column missing."""
    name_u = str(rec.get("name") or "").upper()
    med = str(rec.get("medicine_type") or "")
    recovered = _recover_pack_from_row(rec)
    if recovered:
        rec["pack"] = normalize_bill_pack(recovered, med, name_u)
        return
    if "TAB" in name_u or "CAP" in name_u:
        m = re.search(r"(?:TAB|CAP)[\s\-]*(\d+)\b", name_u)
        if m:
            rec["pack"] = m.group(1)
            return
        rec["pack"] = "10"
        return
    hints = (
        ("FACEWASH", "60GM"),
        ("FACE WASH", "75GM"),
        ("SANITIZER", "55ML"),
        (" GEL", "20GM"),
        ("CREAM", "10GM"),
    )
    for key, default_pack in hints:
        if key in name_u:
            rec["pack"] = default_pack
            return


def _to_float(value: Any) -> float:
    try:
        return float(str(value or "0").replace(",", ""))
    except (TypeError, ValueError):
        return 0.0


def _is_strip_product(medicine_type: str, name_upper: str) -> bool:
    t = (medicine_type or "").lower()
    if any(x in t for x in ("tablet", "capsule", "bolus", "cap", "tab")):
        return True
    if any(k in name_upper for k in (" TAB", " CAP", " BOLUS", " ROTACAP")):
        return True
    return False
