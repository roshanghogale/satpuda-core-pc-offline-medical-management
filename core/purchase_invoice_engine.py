"""
Universal purchase bill understanding engine — v3.0

Format-agnostic extraction helpers: column classification, discount detection
(rupee amount vs percentage), line amount validation, GST slab totals,
confidence scoring, and learned alias support.
"""
import json
import os
import re
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple

PROMPT_VERSION = "3.0"

DISC_RUPEE = "RUPEE_AMOUNT"
DISC_PCT = "PERCENTAGE"
DISC_ABSENT = "ABSENT"

# Maps normalized header tokens → canonical field names used by purchase_importer
HEADER_FIELD_ALIASES: Dict[str, List[str]] = {
    "name": [
        "medicine name", "product name", "item name", "name of product",
        "product", "medicine", "item", "description", "particulars",
    ],
    "batch": ["batch", "batch no", "batch no.", "batch number", "batchno"],
    "expiry": [
        "expiry", "exp date", "expiry date", "expdt", "exp dt", "exp.",
        "exp", "expd", "exp. date",
    ],
    "qty": ["qty", "quantity", "qnty", "pcs", "units", "billed qty"],
    "free_qty": ["free", "free qty", "free quantity", "fqty", "bonus", "sch", "f.qty"],
    "rate": ["rate", "purchase rate", "ptr", "pur rate", "p.rate", "our rate", "unit rate"],
    "gst_pct": ["gst", "gst%", "gst %", "gst(%)", "tax", "tax%", "tax %"],
    "hsn_code": ["hsn", "hsn code", "hsncode", "hsn/sac", "sac"],
    "mrp": ["mrp", "m.r.p.", "m.r.p", "m r p"],
    "amount": ["amount", "net amount", "value", "amt", "line total", "net"],
    "pack": ["pack", "packing", "pack size", "pkg", "pkg.", "size"],
    "manufacturer": ["mfg", "mfr", "manufacturer", "company", "make", "co"],
    "medicine_type": ["type", "medicine type", "product type"],
    "disc_rupees": [
        "disc.", "disc amt", "discount amt", "discount amount", "disc amount",
    ],
    "discount_pct": [
        "disc%", "discount%", "discount percent", "disc percent", "disc pct",
    ],
}

CF_BF_MARKERS = ("total c/f", "total b/f", "total cf", "total bf", "carried forward", "brought forward")

TOTAL_LINE_MARKERS = (
    "grand total", "net amount", "net payable", "net amt", "round off",
    "taxable total", "subtotal", "gross amount", "amount in words",
    "item total", "total cgst", "total sgst", "prod discount", "cash discount",
    "product discount", "less discount",
) + CF_BF_MARKERS


def _clean_cell(value: Any) -> str:
    if value is None:
        return ""
    try:
        if value != value:
            return ""
    except Exception:
        pass
    text = str(value).replace("\r", " ").replace("\n", " ")
    return re.sub(r"\s+", " ", text).strip()


def _normalize_header(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", _clean_cell(value).lower())


def _to_float(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return round(float(value), 4)
        except Exception:
            return 0.0
    text = _clean_cell(value).replace(",", "")
    text = re.sub(r"(rs\.?|inr|/-)", "", text, flags=re.IGNORECASE)
    text = re.sub(r"[^0-9.\-]", "", text)
    if text in ("", "-", ".", "-."):
        return 0.0
    try:
        return round(float(text), 4)
    except ValueError:
        return 0.0


def classify_header_field(header: Any) -> str:
    """Map a column header to a canonical field name."""
    raw = _clean_cell(header)
    key = _normalize_header(raw)
    if not key:
        return ""

    # Disc column: rupee amount vs percentage (Tuljai uses rupee Disc.)
    if "%" in raw or "pct" in key or "percent" in key:
        if "disc" in key or "discount" in key:
            return "discount_pct"
    if key in ("disc", "discount") or (
        (key.startswith("disc") or key.startswith("discount"))
        and "pct" not in key and "percent" not in key
    ):
        return "disc_rupees"

    for field_name, aliases in HEADER_FIELD_ALIASES.items():
        for alias in aliases:
            alias_key = _normalize_header(alias)
            if key == alias_key:
                return field_name
    for field_name, aliases in HEADER_FIELD_ALIASES.items():
        for alias in aliases:
            alias_key = _normalize_header(alias)
            if alias_key and (alias_key in key or key in alias_key):
                return field_name
    return ""


def detect_disc_column_type(
    disc_raw: float,
    mrp: float,
    rate: float,
    header_field: str = "",
) -> str:
    """Decide whether Disc column is rupee amount (MRP−Rate) or percentage."""
    if not disc_raw:
        return DISC_ABSENT
    if header_field == "disc_rupees":
        return DISC_RUPEE
    if header_field == "discount_pct":
        return DISC_PCT
    if mrp > 0 and rate > 0 and abs(disc_raw - (mrp - rate)) <= 0.10:
        return DISC_RUPEE
    if disc_raw < 50 and mrp > 0 and rate > 0:
        expected = mrp * (1 - disc_raw / 100.0)
        if abs(expected - rate) <= 0.10:
            return DISC_PCT
    if disc_raw < 100 and mrp > rate > 0:
        return DISC_PCT
    if mrp > rate > 0:
        return DISC_RUPEE
    return DISC_ABSENT


def resolve_line_pricing(
    mrp: float,
    rate: float,
    qty: float,
    amount: float,
    disc_raw: float = 0.0,
    disc_header: str = "",
    disc_type: str = "",
) -> Dict[str, Any]:
    """
    Normalize rate/amount/discount for DB storage.

    Indian pharma invoices usually print Rate as the final net rate.
    Amount = Rate × Qty — do NOT apply discount twice in PurchaseCalculator.
    """
    mrp = _to_float(mrp)
    rate = _to_float(rate)
    qty = _to_float(qty)
    amount = _to_float(amount)
    disc_raw = _to_float(disc_raw)

    if not disc_type:
        disc_type = detect_disc_column_type(disc_raw, mrp, rate, disc_header)

    # Tuljai / similar: Rate is net even when Disc cell was not parsed
    if disc_type == DISC_ABSENT and mrp > rate > 0 and (mrp - rate) >= 0.01:
        disc_type = DISC_RUPEE
        if disc_raw <= 0:
            disc_raw = round(mrp - rate, 4)

    if rate <= 0 and amount > 0 and qty > 0:
        rate = round(amount / qty, 4)
    if amount <= 0 and rate > 0 and qty > 0:
        amount = round(rate * qty, 2)

    amount_validated = True
    if rate > 0 and qty > 0 and amount > 0:
        expected = round(rate * qty, 2)
        if abs(expected - amount) > 0.02:
            amount_validated = False
            if abs(expected - amount) <= 1.0:
                amount = expected

    # PurchaseCalculator uses discount_pct on base = qty × rate
    discount_pct = 0.0
    if disc_type == DISC_PCT and disc_raw > 0:
        if mrp > 0 and rate > 0 and abs(rate - mrp * (1 - disc_raw / 100.0)) <= 0.10:
            discount_pct = disc_raw
        elif mrp <= 0:
            discount_pct = disc_raw
        # else rate already net — leave discount_pct at 0
    elif disc_type == DISC_RUPEE and mrp > 0 and rate > 0:
        discount_pct = 0.0

    return {
        "mrp": mrp,
        "rate": rate,
        "qty": qty,
        "amount": amount,
        "discount_pct": round(discount_pct, 4),
        "disc_column_value": round(disc_raw, 4),
        "disc_column_type": disc_type,
        "amount_validated": amount_validated,
    }


def format_discount_display(
    discount_pct: float = 0.0,
    disc_column_value: float = 0.0,
    disc_column_type: str = "",
) -> str:
    """
    Human-readable discount from bill for UI only (tree / form).
    Does not change PurchaseCalculator totals.
    """
    pct = _to_float(discount_pct)
    val = _to_float(disc_column_value)
    dtype = _clean_cell(disc_column_type).upper()
    if dtype == DISC_RUPEE and val > 0:
        return "₹{:.2f}".format(val)
    if dtype == DISC_PCT and val > 0:
        return "{:.1f}%".format(val)
    if pct > 0:
        return "{:.1f}%".format(pct)
    if val > 0:
        return "₹{:.2f}".format(val)
    return "0"


def predict_gst_from_hsn(hsn: str) -> float:
    """Predict typical GST % from HSN prefix."""
    digits = re.sub(r"\D", "", str(hsn or ""))
    if len(digits) < 4:
        return 0.0
    prefix = digits[:4]
    if prefix.startswith("2309"):
        return 0.0
    if prefix.startswith(("3002", "3003", "3004")):
        return 5.0
    if prefix.startswith("3304"):
        return 18.0
    return 0.0


def expiry_flag(expiry_mmyy: str) -> str:
    """Return OK | EXPIRED | EXPIRING_SOON for MM/YY expiry."""
    if not expiry_mmyy or "/" not in expiry_mmyy:
        return "OK"
    try:
        parts = expiry_mmyy.split("/")
        month = int(parts[0])
        year = int(parts[1])
        if year < 100:
            year += 2000
        exp_end = datetime(year, month, 28)
        if month == 12:
            exp_end = datetime(year, 12, 31)
        else:
            from calendar import monthrange
            exp_end = datetime(year, month, monthrange(year, month)[1])
        now = datetime.now()
        if exp_end < now:
            return "EXPIRED"
        if exp_end < now.replace(day=1) + __import__("datetime").timedelta(days=180):
            return "EXPIRING_SOON"
    except Exception:
        pass
    return "OK"


def record_has_item_data(rec: Dict[str, Any]) -> bool:
    """True when a row/record line has batch, expiry, qty, rate, or amount."""
    return bool(
        _clean_cell(rec.get("batch"))
        or _clean_cell(rec.get("expiry"))
        or _to_float(rec.get("qty")) > 0
        or _to_float(rec.get("rate")) > 0
        or _to_float(rec.get("amount")) > 0
        or _clean_cell(rec.get("hsn_code"))
    )


def merge_multiline_item_records(records: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Phase 6 — merge wrapped product names and continuation lines.
    A line without batch/expiry/qty/rate/amount continues the previous name.
    """
    merged: List[Dict[str, Any]] = []
    current: Optional[Dict[str, Any]] = None

    for rec in records:
        if not any(_clean_cell(rec.get(k)) for k in rec if k != "source_row"):
            continue
        if _looks_like_skip_line(" ".join(_clean_cell(rec.get(k)) for k in rec if k != "source_row")):
            continue

        has_data = record_has_item_data(rec)
        name_only = (
            _clean_cell(rec.get("name"))
            and not has_data
            and not any(_clean_cell(rec.get(k)) for k in ("pack", "mrp", "manufacturer") if k in rec)
        )

        if has_data and current is None:
            current = dict(rec)
        elif has_data and current is not None:
            merged.append(current)
            current = dict(rec)
        elif name_only and current is not None:
            current["name"] = "{} {}".format(
                _clean_cell(current.get("name")),
                _clean_cell(rec.get("name")),
            ).strip()
        elif not has_data and current is not None:
            for key, value in rec.items():
                if key == "source_row":
                    continue
                if _clean_cell(value) and not _clean_cell(current.get(key)):
                    current[key] = value
        elif _clean_cell(rec.get("name")):
            if current:
                merged.append(current)
            current = dict(rec)

    if current:
        merged.append(current)
    return merged if merged else list(records)


def _looks_like_skip_line(line: str) -> bool:
    low = _clean_cell(line).lower()
    if not low:
        return True
    return any(token in low for token in TOTAL_LINE_MARKERS)


def is_skip_row(row: Sequence[Any]) -> bool:
    return _looks_like_skip_line(" ".join(_clean_cell(c) for c in row))


def detect_document_format(path: str, rows: Sequence[Sequence[Any]], text: str) -> str:
    ext = os.path.splitext(path or "")[1].lower()
    if ext == ".xlsx":
        base = "XLSX"
    elif ext == ".xls":
        base = "XLS"
    elif ext == ".csv":
        base = "CSV"
    elif ext == ".pdf":
        base = "PDF"
    else:
        base = "UNKNOWN"

    if rows:
        for r in range(min(5, len(rows))):
            try:
                marker = _clean_cell(rows[r][0]).upper()
            except Exception:
                marker = ""
            if marker in ("H", "T", "F"):
                return "EDI_CSV"
                break
    if ext in (".xlsx", ".xls"):
        return base
    if ext == ".csv":
        if rows:
            try:
                if _clean_cell(rows[0][0]).upper() == "H":
                    return "EDI_CSV"
            except Exception:
                pass
        return "CSV"
    if text and "|" in text and "HSN CODE" in text.upper():
        return "DIGITAL_PDF"
    if text and len(text.strip()) > 100:
        return "DIGITAL_PDF"
    return "SCANNED_PDF" if base == "PDF" else base


def _cell_at(rows_or_row, row_idx: int = 0, col_idx: int = 0) -> Any:
    if isinstance(rows_or_row, (list, tuple)) and row_idx == 0 and col_idx == 0:
        row = rows_or_row
    else:
        try:
            row = rows_or_row[row_idx]
        except Exception:
            return ""
    try:
        return row[col_idx]
    except Exception:
        return ""


def _gst_footer_noise_line(line: str) -> bool:
    """Lines where loose SGST/CGST regex would grab item counts, not tax amounts."""
    low = _clean_cell(line).lower()
    if not low:
        return True
    if re.search(r"\badd\s+(?:cgst|sgst)\b", low):
        return False
    noise = (
        "items/qty",
        "items / qty",
        "class sgst cgst",
        "class tot.amt",
        "tot.amt.",
        "tot.gst.",
        "sub total",
        "sn.",
        "mfg.",
        "particulars",
        "hsn",
        "batch",
        "gst payble",
        "gst 0.00",
        "gst 12.00",
        "gst 18.00",
        "gst 28.00",
        "ac no",
        "ifsc",
        "cr/dr note",
        "cr note",
    )
    if low.startswith("gst ") and re.match(r"^gst\s+[\d.]+\s+[\d,.]", low):
        return False
    if low.startswith("gst ") and "gstin" not in low and re.search(r"^\s*gst\s+\d", low):
        return True
    return any(token in low for token in noise)


def _plausible_bill_gst_component(value: float, line_gross: float = 0.0) -> bool:
    if value <= 0:
        return False
    if value > 250000:
        return False
    if line_gross > 0 and value > line_gross * 0.35:
        return False
    return True


def _gst_totals_from_items(items: Sequence[Any]) -> Tuple[float, float]:
    total = round(
        sum(
            round(float(_item_field(it, "amount", 0) or 0) * float(_item_field(it, "gst_pct", 0) or 0) / 100, 2)
            for it in (items or [])
        ),
        2,
    )
    cgst = round(total / 2, 2)
    return cgst, round(total - cgst, 2)


def _extract_footer_gst_amounts(text: str, line_gross: float = 0.0) -> Tuple[float, float]:
    """
    Parse bill-level CGST/SGST rupee totals from footer text.
    MARG-style headers like 'Class SGST CGST TOTAL ITEMS/QTY. : 21 / 62'
    must not be read as ₹21 tax.
    """
    cgst_patterns = (
        r"\badd\s+cgst\s*(?:amount|amt)?\D+([\d,]+(?:\.\d{1,2})?)",
        r"(?:total\s*cgst|cgst\s*total|cgst\s*amt)\D+([\d,]+(?:\.\d{1,2})?)",
    )
    sgst_patterns = (
        r"\badd\s+sgst\s*(?:amount|amt)?\D+([\d,]+(?:\.\d{1,2})?)",
        r"(?:total\s*sgst|sgst\s*total|sgst\s*amt)\D+([\d,]+(?:\.\d{1,2})?)",
    )
    loose_cgst = r"\bcgst\D+([\d,]+(?:\.\d{1,2})?)(?!\s*%)"
    loose_sgst = r"\bsgst\D+([\d,]+(?:\.\d{1,2})?)(?!\s*%)"

    cgst = sgst = 0.0
    blob = text or ""
    lines = [ln for ln in blob.splitlines() if ln.strip()]

    for pattern in cgst_patterns:
        m = re.search(pattern, blob, flags=re.IGNORECASE)
        if m:
            val = _to_float(m.group(1))
            if _plausible_bill_gst_component(val, line_gross):
                cgst = val
                break
    for pattern in sgst_patterns:
        m = re.search(pattern, blob, flags=re.IGNORECASE)
        if m:
            val = _to_float(m.group(1))
            if _plausible_bill_gst_component(val, line_gross):
                sgst = val
                break
    if cgst and sgst:
        return cgst, sgst

    for line in reversed(lines):
        m = re.search(
            r"\bTOTAL\s+[\d,.]+\s+[\d,.]+\s+([\d,.]+)\s+([\d,.]+)\s+[\d,.]+",
            line,
            flags=re.IGNORECASE,
        )
        if m:
            sgst_val = _to_float(m.group(1))
            cgst_val = _to_float(m.group(2))
            if _plausible_bill_gst_component(sgst_val, line_gross) and _plausible_bill_gst_component(cgst_val, line_gross):
                return cgst_val, sgst_val

    for line in reversed(lines):
        m = re.search(
            r"\bGST\s+5\.00\s+[\d,.]+\s+[\d,.]+\s+([\d,.]+)\s+([\d,.]+)\s+[\d,.]+",
            line,
            flags=re.IGNORECASE,
        )
        if m:
            sgst_val = _to_float(m.group(1))
            cgst_val = _to_float(m.group(2))
            if _plausible_bill_gst_component(sgst_val, line_gross) and _plausible_bill_gst_component(cgst_val, line_gross):
                return cgst_val, sgst_val

    for line in reversed(lines):
        if cgst:
            break
        if _gst_footer_noise_line(line):
            continue
        for pattern in cgst_patterns:
            m = re.search(pattern, line, flags=re.IGNORECASE)
            if m:
                val = _to_float(m.group(1))
                if _plausible_bill_gst_component(val, line_gross):
                    cgst = val
                break
    for line in reversed(lines):
        if sgst:
            break
        if _gst_footer_noise_line(line):
            continue
        for pattern in sgst_patterns:
            m = re.search(pattern, line, flags=re.IGNORECASE)
            if m:
                val = _to_float(m.group(1))
                if _plausible_bill_gst_component(val, line_gross):
                    sgst = val
                break

    if not cgst:
        for line in reversed(lines):
            if _gst_footer_noise_line(line):
                continue
            m = re.search(loose_cgst, line, flags=re.IGNORECASE)
            if m:
                val = _to_float(m.group(1))
                if _plausible_bill_gst_component(val, line_gross):
                    cgst = val
                break
    if not sgst:
        for line in reversed(lines):
            if _gst_footer_noise_line(line):
                continue
            m = re.search(loose_sgst, line, flags=re.IGNORECASE)
            if m:
                val = _to_float(m.group(1))
                if _plausible_bill_gst_component(val, line_gross):
                    sgst = val
                break

    return cgst, sgst


def extract_invoice_totals(text: str) -> Dict[str, float]:
    """Extract header/footer totals: discounts, GST, round-off, net payable."""
    result = {
        "gross_amount": 0.0,
        "product_discount": 0.0,
        "cash_discount": 0.0,
        "total_cgst": 0.0,
        "total_sgst": 0.0,
        "total_igst": 0.0,
        "round_off": 0.0,
        "net_payable": 0.0,
        "taxable_amount": 0.0,
    }
    if not text:
        return result

    if "MARG ERP" in text.upper() or re.search(
        r"Sn\.\s+Mfg\.\s+Item\s+Name", text, flags=re.IGNORECASE
    ):
        try:
            from core.marg_erp_parser import extract_marg_erp_footer_discounts
            marg = extract_marg_erp_footer_discounts(text)
            if marg.get("product_discount"):
                result["product_discount"] = marg["product_discount"]
            if marg.get("cash_discount"):
                result["cash_discount"] = marg["cash_discount"]
            if marg.get("subtotal"):
                result["taxable_amount"] = marg["subtotal"]
        except Exception:
            pass

    patterns = {
        "product_discount": (
            r"prod(?:uct)?\.?\s*disc(?:ount)?\D+([\d,]+(?:\.\d{1,2})?)",
            r"less\s*prod(?:uct)?\s*disc\D+([\d,]+(?:\.\d{1,2})?)",
            r"\bDIS\.?\s*([\d,]+(?:\.\d{1,2})?)",
            r"\bDISC\.?\s*([\d,]+(?:\.\d{1,2})?)",
        ),
        "cash_discount": (
            r"cash\s*disc(?:ount)?\.?\D+([\d,]+(?:\.\d{1,2})?)",
            r"less\s*cash\s*disc(?:ount)?\D+([\d,]+(?:\.\d{1,2})?)",
            r"\bc\.?\s*d\.?\s*(?:amt|amount)?\D+([\d,]+(?:\.\d{1,2})?)",
        ),
        "round_off": (
            r"(?:round(?:ing)?\s*(?:off|adj)?)\D+([-]?[\d,]+(?:\.\d{1,2})?)",
        ),
        "net_payable": (
            r"net\s*amt\.?\s*:?\s*([\d,]+(?:\.\d{1,2})?)",
            r"net\s*amt\s*r/?o\D+([\d,]+(?:\.\d{1,2})?)",
            r"(?:net\s+payable|net\s+amount)\D+([\d,]+(?:\.\d{1,2})?)",
        ),
        "gross_amount": (
            r"\bgross\D+([\d,]+(?:\.\d{1,2})?)",
            r"gross\s+amount\s*:?\s*([\d,]+(?:\.\d{1,2})?)",
            r"(?:item\s+total|sub\s*total)\D+([\d,]+(?:\.\d{1,2})?)",
        ),
        "taxable_amount": (
            r"(?:taxable\s+(?:amount|value|total))\D+([\d,]+(?:\.\d{1,2})?)",
        ),
    }

    for line in text.splitlines():
        low = line.lower()
        if any(m in low for m in CF_BF_MARKERS):
            continue
        for field, field_patterns in patterns.items():
            if result[field]:
                continue
            if field == "round_off" and "net amt" in low:
                continue
            for pattern in field_patterns:
                m = re.search(pattern, line, flags=re.IGNORECASE)
                if m:
                    result[field] = _to_float(m.group(1))
                    break

    if not result["net_payable"]:
        for line in reversed(text.splitlines()):
            m = re.search(
                r"(?:net\s+payable|grand\s+total)\D+([\d,]+(?:\.\d{1,2})?)",
                line,
                flags=re.IGNORECASE,
            )
            if m:
                result["net_payable"] = _to_float(m.group(1))
                break

    line_hint = result.get("gross_amount") or result.get("taxable_amount") or 0.0
    cgst, sgst = _extract_footer_gst_amounts(text, line_gross=line_hint)
    if cgst:
        result["total_cgst"] = cgst
    if sgst:
        result["total_sgst"] = sgst
    if not result["total_cgst"] and not result["total_sgst"]:
        m = re.search(r"gst\s*\+\s*cess\D+([\d,.]+)", text, flags=re.IGNORECASE)
        if m:
            total_gst = _to_float(m.group(1))
            if total_gst > 0:
                result["total_cgst"] = round(total_gst / 2, 2)
                result["total_sgst"] = round(total_gst - result["total_cgst"], 2)

    return result


def compute_import_bill_totals(
    summary: Dict[str, Any],
    line_subtotal: float,
    line_gst: float,
    items: Optional[Sequence[Dict[str, Any]]] = None,
    overall_discount: Optional[float] = None,
) -> Dict[str, float]:
    """
    Build purchase summary using Indian pharmacy GST pipeline when items are
    available; bill net payable is authoritative when parsed from footer.
    """
    from core.pharmacy_purchase_calc import calc_pharmacy_purchase_bill

    prod_disc = round(float(summary.get("product_discount") or 0), 2)
    cash_disc = round(float(summary.get("cash_discount") or 0), 2)
    parsed_disc = round(float(summary.get("parsed_total_discount") or prod_disc + cash_disc), 2)
    inv_net = round(float(summary.get("invoice_total") or 0), 2)
    inv_round = round(float(summary.get("round_off") or 0), 2)
    supply_type = str(summary.get("supply_type") or "intra")
    footer_cgst = round(float(summary.get("total_cgst") or 0), 2)
    footer_sgst = round(float(summary.get("total_sgst") or 0), 2)
    footer_gst = round(footer_cgst + footer_sgst, 2)
    line_gross_hint = round(float(summary.get("line_gross") or line_subtotal or 0), 2)
    footer_gross = round(float(summary.get("gross_amount") or 0), 2) or line_gross_hint
    use_footer = bool(
        summary.get("use_footer_totals")
        or (
            summary.get("footer_gst_authoritative")
            and inv_net > 0
            and footer_gst > 0
        )
        or (
            inv_net > 0
            and footer_gst > 0
            and footer_gross > 0
        )
    )

    if use_footer:
        line_gross = line_gross_hint
        display_subtotal = line_gross or footer_gross
        total_disc = round(
            float(overall_discount if overall_discount is not None else parsed_disc),
            2,
        )
        total_gst = round(footer_cgst + footer_sgst, 2)
        if overall_discount is not None and abs(total_disc - parsed_disc) > 0.005:
            total_amount = round(inv_net + parsed_disc - total_disc, 2)
        else:
            total_amount = inv_net
        # Supplier net = taxable base + GST + round (from footer); not subtotal − discount.
        taxable_base = round(inv_net - total_gst - inv_round, 2)
        pre_round = round(taxable_base + total_gst, 2)
        rounding = inv_round if inv_round else round(total_amount - pre_round, 2)
        return {
            "subtotal": display_subtotal,
            "taxable_total": round(display_subtotal - total_disc, 2),
            "product_discount": prod_disc if overall_discount is None else 0.0,
            "cash_discount": cash_disc if overall_discount is None else 0.0,
            "discount_amount": total_disc,
            "overall_discount": total_disc,
            "total_gst": total_gst,
            "cgst": footer_cgst,
            "sgst": footer_sgst,
            "pre_round_total": pre_round,
            "rounding": rounding,
            "total_amount": total_amount,
            "gross_total": display_subtotal,
            "supplier_gross": footer_gross,
            "use_footer_totals": True,
            "calc_items": [],
        }

    if items:
        calc = calc_pharmacy_purchase_bill(
            items=[dict(i) for i in items],
            cash_discount=cash_disc,
            product_discount=prod_disc,
            round_off=inv_round if inv_round else None,
            net_payable=inv_net if inv_net > 0 else None,
            supply_type=supply_type,
        )
        return {
            "subtotal": calc["gross_total"],
            "taxable_total": calc["taxable_total"],
            "product_discount": calc["product_discount"],
            "cash_discount": calc["cash_discount"],
            "discount_amount": calc["discount_amount"],
            "overall_discount": calc["overall_discount"],
            "total_gst": calc["total_gst"],
            "cgst": calc["cgst"],
            "sgst": calc["sgst"],
            "pre_round_total": calc["pre_round_total"],
            "rounding": calc["rounding"],
            "total_amount": calc["total_amount"],
            "gross_total": calc["gross_total"],
            "validation": calc.get("validation", {}),
            "calc_items": calc.get("items", []),
        }

    # Fallback when no item rows — footer-only
    line_sub = round(float(line_subtotal or 0), 2)
    line_gst_val = round(float(line_gst or 0), 2)
    total_disc = round(prod_disc + cash_disc, 2)
    cgst = round(float(summary.get("total_cgst") or 0), 2)
    sgst = round(float(summary.get("total_sgst") or 0), 2)

    if cgst > 0 or sgst > 0:
        total_gst = round(cgst + sgst, 2)
    elif total_disc > 0 and line_sub > 0:
        total_gst = round(line_gst_val * max(0.0, line_sub - total_disc) / line_sub, 2)
        cgst = round(total_gst / 2, 2)
        sgst = round(total_gst - cgst, 2)
    else:
        total_gst = line_gst_val
        cgst = round(total_gst / 2, 2)
        sgst = round(total_gst - cgst, 2)

    pre_round = round(line_sub - total_disc + total_gst, 2)
    if inv_net > 0:
        total_amount = inv_net
        rounding = inv_round if inv_round else round(total_amount - pre_round, 2)
    else:
        total_amount = pre_round
        rounding = 0.0

    return {
        "subtotal": line_sub,
        "taxable_total": round(line_sub - total_disc, 2),
        "product_discount": prod_disc,
        "cash_discount": cash_disc,
        "discount_amount": total_disc,
        "overall_discount": total_disc,
        "total_gst": total_gst,
        "cgst": cgst,
        "sgst": sgst,
        "pre_round_total": pre_round,
        "rounding": rounding,
        "total_amount": total_amount,
        "gross_total": line_sub,
    }


def score_supplier_confidence(gstin: str, dl: str, name: str) -> float:
    if gstin and len(gstin) == 15:
        return 100.0
    if dl and len(dl) >= 4:
        return 95.0
    if name and len(name) >= 4:
        return 60.0
    return 30.0


def _item_field(item: Any, field: str, default: Any = ""):
    """Read a field from ImportedPurchaseItem or a plain dict."""
    if isinstance(item, dict):
        return item.get(field, default)
    return getattr(item, field, default)


def score_item_table(items: Sequence[Any]) -> float:
    if not items:
        return 0.0
    complete = 0
    for item in items:
        fields = (
            _item_field(item, "name"),
            _item_field(item, "batch"),
            _item_field(item, "expiry"),
            _item_field(item, "qty", 0),
            _item_field(item, "rate", 0),
            _item_field(item, "amount", 0),
        )
        filled = sum(1 for f in fields[:6] if _clean_cell(f) or _to_float(f) > 0)
        if filled >= 5:
            complete += 1
    return round(100.0 * complete / len(items), 1)


def score_totals_validation(gross_match: bool, net_match: bool) -> float:
    if gross_match and net_match:
        return 100.0
    if gross_match:
        return 60.0
    return 0.0


def build_confidence_scores(invoice: Any) -> Dict[str, Any]:
    supplier_score = score_supplier_confidence(
        getattr(invoice, "supplier_gstin", "") or "",
        getattr(invoice, "supplier_dl", "") or "",
        getattr(invoice, "supplier_name", "") or "",
    )
    header_fields = [
        getattr(invoice, "invoice_number", ""),
        getattr(invoice, "invoice_date", ""),
        getattr(invoice, "supplier_name", ""),
    ]
    header_score = round(100.0 * sum(1 for f in header_fields if _clean_cell(f)) / 3, 1)
    item_score = score_item_table(getattr(invoice, "items", []) or [])
    validation = getattr(invoice, "validation", {}) or {}
    totals_score = score_totals_validation(
        validation.get("gross_match", False),
        validation.get("net_match", False),
    )
    overall = round(
        supplier_score * 0.2 + header_score * 0.1 + item_score * 0.4 + totals_score * 0.3,
        1,
    )
    requires_review = overall < 75.0 or bool(getattr(invoice, "review_flags", []))
    review_reason = ""
    if overall < 75.0:
        review_reason = "Overall confidence below 75%"
    elif getattr(invoice, "review_flags", []):
        review_reason = "; ".join(invoice.review_flags[:3])

    return {
        "supplier_confidence": supplier_score,
        "header_confidence": header_score,
        "item_table_confidence": item_score,
        "totals_validation_confidence": totals_score,
        "overall_confidence": overall,
        "requires_review": requires_review,
        "review_reason": review_reason,
        "prompt_version": PROMPT_VERSION,
    }


def validate_invoice_math(invoice: Any) -> Dict[str, Any]:
    items = getattr(invoice, "items", []) or []
    line_errors: List[str] = []
    calculated_gross = 0.0
    for item in items:
        qty = _to_float(getattr(item, "qty", 0))
        rate = _to_float(getattr(item, "rate", 0))
        amount = _to_float(getattr(item, "amount", 0) or qty * rate)
        calculated_gross += amount
        expected = round(qty * rate, 2)
        if rate and qty and amount and abs(expected - amount) > 0.02:
            # Supplier CSV col 31 may be pre-discounted taxable (amount < rate×qty).
            if amount < expected - 0.02:
                pass
            elif abs(expected - amount) > max(1.0, expected * 0.02):
                line_errors.append(
                    "{}: amount {:.2f} != rate×qty {:.2f}".format(
                        getattr(item, "name", "?")[:30], amount, expected
                    )
                )
    calculated_gross = round(calculated_gross, 2)

    totals = extract_invoice_totals(getattr(invoice, "raw_text", "") or "")
    if getattr(invoice, "product_discount", 0):
        totals["product_discount"] = float(invoice.product_discount)
    if getattr(invoice, "cash_discount", 0):
        totals["cash_discount"] = float(invoice.cash_discount)
    if getattr(invoice, "invoice_total", 0):
        totals["net_payable"] = float(invoice.invoice_total)

    invoice_gross = totals.get("gross_amount") or calculated_gross
    gross_match = (
        not totals.get("gross_amount")
        or abs(calculated_gross - totals["gross_amount"]) <= max(2.0, calculated_gross * 0.02)
    )

    prod_disc = totals.get("product_discount", 0)
    cash_disc = totals.get("cash_discount", 0)
    if getattr(invoice, "total_cgst", 0) or getattr(invoice, "total_sgst", 0):
        footer_cgst = round(float(getattr(invoice, "total_cgst", 0) or 0), 2)
        footer_sgst = round(float(getattr(invoice, "total_sgst", 0) or 0), 2)
        total_gst = round(footer_cgst + footer_sgst, 2)
    else:
        total_gst = totals.get("total_cgst", 0) + totals.get("total_sgst", 0)
    calculated_net = round(
        calculated_gross - prod_disc - cash_disc + total_gst + totals.get("round_off", 0),
        2,
    )
    invoice_net = totals.get("net_payable") or getattr(invoice, "invoice_total", 0)
    doc_fmt = getattr(invoice, "document_format", "") or ""
    parser = getattr(invoice, "parser", "") or ""
    is_edi_htf = doc_fmt == "EDI_CSV" or "H/T/F" in parser
    if is_edi_htf and invoice_net:
        net_match = True
    else:
        net_match = not invoice_net or abs(calculated_net - invoice_net) <= 1.0

    item_cgst, item_sgst = _gst_totals_from_items(items)
    item_gst_total = round(item_cgst + item_sgst, 2)
    gst_footer_match = (
        total_gst <= 0
        or item_gst_total <= 0
        or abs(item_gst_total - total_gst) <= max(0.5, total_gst * 0.02)
        or getattr(invoice, "footer_gst_authoritative", False)
    )

    flags: List[str] = []
    if line_errors:
        flags.append("{} line amount mismatch(es)".format(len(line_errors)))
    if not gross_match:
        flags.append("Gross total mismatch")
    if not net_match and invoice_net:
        flags.append("Net payable mismatch")
    if total_gst > 0 and not gst_footer_match:
        flags.append(
            "Bill GST ₹{:.2f} differs from line sum ₹{:.2f} — using bill footer".format(
                total_gst, item_gst_total
            )
        )

    return {
        "line_amount_errors": line_errors[:10],
        "calculated_gross": calculated_gross,
        "invoice_gross": invoice_gross,
        "gross_match": gross_match,
        "calculated_net": calculated_net,
        "invoice_net": invoice_net,
        "net_match": net_match,
        "gst_slab_totals_match": gst_footer_match,
        "footer_gst_total": total_gst,
        "item_gst_total": item_gst_total,
        "flags": flags,
    }


def enrich_purchase_invoice(invoice: Any) -> None:
    """Attach totals, validation, confidence, and review flags to a PurchaseInvoice."""
    text = getattr(invoice, "raw_text", "") or ""
    doc_fmt = getattr(invoice, "document_format", "") or ""
    parser = getattr(invoice, "parser", "") or ""
    is_edi_htf = doc_fmt == "EDI_CSV" or "H/T/F" in parser
    totals = {} if is_edi_htf else extract_invoice_totals(text)
    if not getattr(invoice, "product_discount", 0):
        invoice.product_discount = totals.get("product_discount", 0)
    if not getattr(invoice, "cash_discount", 0):
        invoice.cash_discount = totals.get("cash_discount", 0)
    line_gross = round(sum(float(_item_field(it, "amount", 0) or 0) for it in invoice.items), 2)
    if line_gross and not getattr(invoice, "line_gross", 0):
        invoice.line_gross = line_gross
    if not getattr(invoice, "gross_amount", 0) and line_gross:
        invoice.gross_amount = line_gross
    if not getattr(invoice, "total_cgst", 0):
        invoice.total_cgst = totals.get("total_cgst", 0)
    if not getattr(invoice, "total_sgst", 0):
        invoice.total_sgst = totals.get("total_sgst", 0)
    if (
        float(invoice.total_cgst or 0) > 0
        and float(invoice.total_sgst or 0) > 0
        and not getattr(invoice, "footer_gst_authoritative", False)
    ):
        invoice.footer_gst_authoritative = True
    footer_gst = round(float(invoice.total_cgst or 0) + float(invoice.total_sgst or 0), 2)
    item_cgst, item_sgst = _gst_totals_from_items(invoice.items)
    item_gst = round(item_cgst + item_sgst, 2)
    if not getattr(invoice, "footer_gst_authoritative", False) and item_gst > 0 and (
        footer_gst <= 0
        or footer_gst > max(line_gross * 0.35, item_gst * 2.5 + 1.0)
    ):
        invoice.total_cgst = item_cgst
        invoice.total_sgst = item_sgst
    if not getattr(invoice, "taxable_amount", 0):
        invoice.taxable_amount = totals.get("taxable_amount", 0)
    if not getattr(invoice, "gross_amount", 0):
        invoice.gross_amount = totals.get("gross_amount", 0)
    if not is_edi_htf and not getattr(invoice, "round_off", 0):
        invoice.round_off = totals.get("round_off", 0)
    if totals.get("net_payable") and not getattr(invoice, "invoice_total", 0):
        invoice.invoice_total = totals["net_payable"]
    elif totals.get("gross_amount") and not getattr(invoice, "invoice_total", 0):
        invoice.invoice_total = totals["gross_amount"]

    invoice.validation = validate_invoice_math(invoice)
    invoice.review_flags = list(invoice.validation.get("flags", []))
    for item in invoice.items:
        if not item.gst_pct and item.hsn_code:
            predicted = predict_gst_from_hsn(item.hsn_code)
            if predicted and not item.gst_pct:
                item.gst_pct = predicted
                item.raw["gst_predicted_from_hsn"] = True
        if not item.amount_validated:
            invoice.review_flags.append(
                "Row {} amount validation".format(item.source_row or "?")
            )
    invoice.confidence_scores = build_confidence_scores(invoice)
    if invoice.confidence_scores.get("requires_review") and not invoice.review_flags:
        invoice.review_flags.append(invoice.confidence_scores.get("review_reason", ""))


def apply_record_pricing(rec: Dict[str, Any]) -> Dict[str, Any]:
    """Resolve pricing fields on a raw record dict before building ImportedPurchaseItem."""
    disc_header = ""
    disc_raw = 0.0
    if _clean_cell(rec.get("disc_rupees")):
        disc_raw = _to_float(rec.get("disc_rupees"))
        disc_header = "disc_rupees"
    elif _clean_cell(rec.get("discount_pct")) is not None and rec.get("discount_pct") != "":
        disc_raw = _to_float(rec.get("discount_pct"))
        disc_header = "discount_pct"

    pricing = resolve_line_pricing(
        _to_float(rec.get("mrp")),
        _to_float(rec.get("rate")),
        _to_float(rec.get("qty")),
        _to_float(rec.get("amount")),
        disc_raw,
        disc_header,
    )
    rec["mrp"] = pricing["mrp"]
    rec["rate"] = pricing["rate"]
    rec["amount"] = pricing["amount"]
    rec["discount_pct"] = pricing["discount_pct"]
    rec["disc_column_value"] = pricing["disc_column_value"]
    rec["disc_column_type"] = pricing["disc_column_type"]
    rec["amount_validated"] = pricing["amount_validated"]
    return rec


def _aliases_path() -> str:
    if getattr(sys, "frozen", False):
        base = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "VeterinaryApp")
    else:
        base = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config")
    return os.path.join(base, "import_learned.json")


def load_import_aliases() -> Dict[str, Any]:
    path = _aliases_path()
    try:
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as handle:
                return json.load(handle)
    except Exception:
        pass
    return {"supplier_aliases": {}, "medicine_aliases": {}, "column_aliases": {}}


def save_import_alias(kind: str, raw_key: str, mapped_value: str) -> None:
    """Persist a user-confirmed mapping for future imports."""
    if not raw_key or not mapped_value:
        return
    data = load_import_aliases()
    bucket = data.setdefault(kind, {})
    bucket[raw_key.strip().upper()] = mapped_value
    try:
        path = _aliases_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, ensure_ascii=True)
    except Exception:
        pass
