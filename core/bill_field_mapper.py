"""
Map OCR bill sections into purchase-page fields: supplier, medicines, totals.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

from core.purchase_invoice_engine import extract_invoice_totals, normalize_total_gst_pct

_NUM = r"[\d,]+(?:\.\d{1,2})?"
_GSTIN_RE = re.compile(r"\b(\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z])\b", re.I)

# Known Maharashtra suppliers — fill gaps when OCR header is partial.
KNOWN_SUPPLIERS: Dict[str, Dict[str, str]] = {
    "RAJ MEDICO": {
        "supplier_name": "RAJ MEDICO'S, KHAMGAON",
        "supplier_address": "SHRI CHATRAPATI TOWER, 3RD FLOOR, GANDHI CHOWK, KHAMGAON",
        "supplier_phone": "9422203650",
        "supplier_gstin": "27AABFR1234F1Z5",
        "supplier_dl": "20B-BUL223156, 21B-BUL223158",
    },
    "VINAR MEDICAL": {
        "supplier_name": "VINAR MEDICAL AGENCIES",
        "supplier_address": "KHAMGAON",
        "supplier_phone": "",
        "supplier_gstin": "",
        "supplier_dl": "",
    },
    "JAI GANESH": {
        "supplier_name": "JAI GANESH PHARMAVET",
        "supplier_address": "SHRI CHATRAPATI TOWER 3RD FLOOR GANDHI CHOWK, KHAMGAON",
        "supplier_phone": "8983008800",
        "supplier_gstin": "27AJCPD5972D1ZE",
        "supplier_dl": "20B-BUL223156, 21B-BUL223158, 20D-BUL223160",
    },
    "OM SAI MEDICO": {
        "supplier_name": "OM SAI MEDICO",
        "supplier_address": "KHAMGAON",
        "supplier_phone": "",
        "supplier_gstin": "",
        "supplier_dl": "",
    },
    "SHREE DISTRIBUTOR": {
        "supplier_name": "SHREE DISTRIBUTOR",
        "supplier_address": "KHAMGAON",
        "supplier_phone": "",
        "supplier_gstin": "",
        "supplier_dl": "",
    },
}


def apply_supplier_to_invoice(invoice: Any, header_text: str) -> None:
    """Fill supplier_name, address, phone, GSTIN, DL, invoice no, date."""
    from core.purchase_importer import extract_supplier_details, normalize_invoice_date

    details = extract_supplier_details(header_text or "")
    _enrich_known_supplier(details, header_text)

    for key in (
        "supplier_name", "supplier_address", "supplier_phone",
        "supplier_gstin", "supplier_dl",
    ):
        val = (details.get(key) or "").strip()
        if val and not (getattr(invoice, key, "") or "").strip():
            setattr(invoice, key, val)

    inv_no = (details.get("invoice_number") or "").strip()
    if inv_no and not (invoice.invoice_number or "").strip():
        invoice.invoice_number = inv_no

    inv_date = (details.get("invoice_date") or "").strip()
    if inv_date and not (invoice.invoice_date or "").strip():
        invoice.invoice_date = normalize_invoice_date(inv_date) or inv_date

    # RAJ MEDICO: invoice often printed as "INV 5532" or "5532"
    if not invoice.invoice_number:
        m = re.search(r"\bINV\.?\s*(\d{3,6})\b", header_text or "", flags=re.I)
        if m:
            invoice.invoice_number = m.group(1)
    if not invoice.invoice_number:
        m = re.search(r"\bBill\s*No\.?\s*[:\s]*([A-Z0-9/\-]+)", header_text or "", flags=re.I)
        if m:
            invoice.invoice_number = m.group(1).strip()


def _enrich_known_supplier(details: Dict[str, str], header_text: str) -> None:
    u = (header_text or "").upper()
    for key, preset in KNOWN_SUPPLIERS.items():
        if key not in u:
            continue
        if preset.get("supplier_name"):
            details["supplier_name"] = preset["supplier_name"]
        for field, value in preset.items():
            if field == "supplier_name":
                continue
            if value and not (details.get(field) or "").strip():
                details[field] = value
        break


def extract_image_bill_totals(totals_text: str, line_gross: float = 0.0) -> Dict[str, float]:
    """Extract footer totals from the totals-only OCR section."""
    text = totals_text or ""
    result = dict(extract_invoice_totals(text))
    ref = line_gross or result.get("gross_amount") or result.get("taxable_amount") or 0.0

    # OCR often reads "CGST % 5.00" as ₹5 tax — drop tiny mistaken GST totals.
    for key in ("total_cgst", "total_sgst"):
        val = float(result.get(key) or 0)
        if 0 < val < 15 and ref > 100:
            result[key] = 0.0

    # RAJ MEDICO / dot-matrix: "Gross : 4643.87", "Net Amt : 4951"
    if not result.get("gross_amount"):
        for pat in (
            r"G[O0]{1,2}SS\s*:?\s*([\d,]+(?:\.\d{1,2})?)",
            r"GROSS\s+AMT\.?\s*:?\s*([\d,]+(?:\.\d{1,2})?)",
            r"TOTAL\s+TARABLE\s*:?\s*([\d,]+(?:\.\d{1,2})?)",
        ):
            m = re.search(pat, text, flags=re.I)
            if m:
                val = _f(m.group(1))
                if val > 0:
                    result["gross_amount"] = val
                    break

    if not result.get("net_payable"):
        for pat in (
            r"NET\s*AMT\.?\s*:?\s*([\d,]+(?:\.\d{1,2})?)",
            r"NET\s+PAYABLE\s*:?\s*([\d,]+(?:\.\d{1,2})?)",
            r"NET\s+AMOUNT\s*:?\s*([\d,]+(?:\.\d{1,2})?)",
        ):
            m = re.search(pat, text, flags=re.I)
            if m:
                val = _f(m.group(1))
                if val > 0 and (not ref or ref * 0.5 <= val <= ref * 2.5):
                    result["net_payable"] = val
                    break

    # CGST % / SGST % rows: last rupee amount (skip 5/12/18/28 % labels)
    if not result.get("total_cgst") or not result.get("total_sgst"):
        cgst = sgst = 0.0
        pct_vals = {5.0, 12.0, 18.0, 28.0}
        for line in text.splitlines():
            u = line.upper()
            decs = [_f(x) for x in re.findall(r"[\d,]+\.\d{2}", line)]
            tax_amts = [d for d in decs if d not in pct_vals and d > 1.0]
            if not tax_amts:
                continue
            val = tax_amts[-1]
            if not ref or val < ref * 0.25:
                if "CGST" in u:
                    cgst = val
                if "SGST" in u:
                    sgst = val
        if cgst and not result.get("total_cgst"):
            result["total_cgst"] = cgst
        if sgst and not result.get("total_sgst"):
            result["total_sgst"] = sgst

    if not result.get("taxable_amount") and result.get("gross_amount"):
        result["taxable_amount"] = result["gross_amount"]

    return result


def apply_totals_to_invoice(
    invoice: Any,
    totals_text: str,
    *,
    footer_authoritative: bool = True,
) -> Dict[str, float]:
    """Map gross, GST, net, discounts into PurchaseInvoice."""
    line_gross = round(sum(float(it.amount or 0) for it in invoice.items), 2)
    totals = extract_image_bill_totals(totals_text, line_gross=line_gross or invoice.gross_amount)

    if totals.get("gross_amount"):
        invoice.gross_amount = float(totals["gross_amount"])
    elif line_gross:
        invoice.gross_amount = line_gross

    if totals.get("net_payable"):
        invoice.invoice_total = float(totals["net_payable"])
    if totals.get("total_cgst"):
        invoice.total_cgst = float(totals["total_cgst"])
    if totals.get("total_sgst"):
        invoice.total_sgst = float(totals["total_sgst"])
    if totals.get("cash_discount"):
        invoice.cash_discount = float(totals["cash_discount"])
    if totals.get("product_discount"):
        invoice.product_discount = float(totals["product_discount"])
    if totals.get("round_off"):
        invoice.round_off = float(totals["round_off"])
    if totals.get("added_charges"):
        invoice.added_charges = float(totals["added_charges"])
    if totals.get("taxable_amount"):
        invoice.taxable_amount = float(totals["taxable_amount"])

    invoice.line_gross = line_gross or invoice.line_gross
    if footer_authoritative and invoice.total_cgst and invoice.total_sgst and invoice.invoice_total:
        invoice.footer_gst_authoritative = True

    # Validate: gross + GST ≈ net
    gross = float(invoice.gross_amount or line_gross or 0)
    gst = float(invoice.total_cgst or 0) + float(invoice.total_sgst or 0)
    net = float(invoice.invoice_total or 0)
    if gross > 0 and gst > 0 and net > 0:
        expected = round(gross + gst, 2)
        if abs(expected - net) <= max(2.0, gross * 0.02):
            invoice.footer_gst_authoritative = True

    return totals


def split_merged_medicine_lines(lines: Sequence[str]) -> List[str]:
    """Split OCR rows that glued multiple products (multiple HSN codes)."""
    out: List[str] = []
    for raw in lines:
        line = (raw or "").strip()
        if not line:
            continue
        parts = re.split(r"(?=\b\d{4}\s)", line)
        parts = [p.strip() for p in parts if p.strip()]
        if len(parts) <= 1:
            out.append(line)
            continue
        for part in parts:
            if re.match(r"^\d{4}\b", part):
                out.append(part)
            elif out:
                out[-1] = out[-1] + " " + part
            else:
                out.append(part)
    return out


def parse_structured_medicines(
    lines: Sequence[str],
    fmt: str,
) -> List[Dict[str, Any]]:
    """Column-aware medicine parsing for a known bill format."""
    expanded = split_merged_medicine_lines(lines)
    records: List[Dict[str, Any]] = []
    parsers = {
        "raj_medico": _parse_raj_structured,
        "vinar": _parse_vinar_structured,
        "om_sai": _parse_om_sai_structured,
        "shree_distributor": _parse_shree_structured,
        "jai_ganesh": _parse_jai_ganesh_structured,
    }
    parse_fn = parsers.get(fmt)
    if not parse_fn:
        return records
    for row_no, line in enumerate(expanded, 1):
        if _is_table_noise(line):
            continue
        rec = parse_fn(line, row_no)
        if rec:
            records.append(rec)
    return records


def _is_table_noise(line: str) -> bool:
    u = line.upper()
    if re.search(r"\b\d{4}\b", line) and re.search(r"\d+\.\d{2}", line):
        return False
    noise = (
        "PRODUCT NAME", "NAME OF PRODUCT", "HSN", "M.R.P", "BATCH", "EXPDT",
        "SUB TOTAL", "GRAND TOTAL", "NET AMT", "GROSS", "CGST %", "SGST %",
    )
    return any(n in u for n in noise)


def _parse_raj_structured(line: str, row_no: int) -> Optional[Dict[str, Any]]:
    """
    RAJ MEDICO columns:
    HSN | GST% | DmNo | MRP | Product | Mfr | Pack | Batch | Exp | Qty | Free | Rate | Amount
    """
    line = _normalize_line(line)
    if "\t" in line:
        cols = [c.strip() for c in line.split("\t") if c.strip()]
        if len(cols) >= 10:
            return _raj_from_columns(cols, row_no)

    m = re.search(
        rf"^(\d{{3,4}})\s+({_NUM})\s+(?:\d+\s+)?({_NUM})\s+(.+?)\s+"
        rf"([A-Z]{{2,5}})\s+(\S+)\s+(\S+)\s+(\S+)\s+({_NUM})\s+({_NUM})\s+"
        rf"({_NUM})\s+({_NUM})\s*$",
        line,
        flags=re.I,
    )
    if m:
        hsn, gst, mrp, name, mfr, pack, batch, exp, qty, free, rate, amount = m.groups()
        return _raj_record(
            hsn, gst, mrp, name, mfr, pack, batch, exp, qty, free, rate, amount, row_no,
        )

    m = re.search(
        rf"(\d{{3,4}})\s+({_NUM})\s+(.+?)\s+([A-Z]{{2,5}})\s+(\S+)\s+(\S+)\s+"
        rf"(\S+)\s+({_NUM})\s+({_NUM})\s+({_NUM})\s+({_NUM})\s*$",
        line,
        flags=re.I,
    )
    if m:
        hsn, gst, name, mfr, pack, batch, exp, qty, free, rate, amount = m.groups()
        return _raj_record(
            hsn, gst, "0", name, mfr, pack, batch, exp, qty, free, rate, amount, row_no,
        )

    return _raj_from_tail(line, row_no)


def _raj_from_columns(cols: List[str], row_no: int) -> Optional[Dict[str, Any]]:
    if len(cols) < 10:
        return None
    try:
        hsn = cols[0]
        gst = cols[1]
        off = 2
        mrp = "0"
        if len(cols) >= 13 and _f(cols[2]) > 10:
            mrp = cols[2]
            off = 3
        name_idx = off
        tail = cols[-4:]
        mid = cols[name_idx:-4]
        if len(mid) < 4:
            return None
        name = mid[0] if len(mid) == 4 else " ".join(mid[:-3])
        mfr, pack, batch, exp = mid[-3], mid[-2], mid[-1], mid[-4] if len(mid) >= 4 else ""
        if len(mid) >= 5:
            name = " ".join(mid[:-4])
            mfr, pack, batch, exp = mid[-3], mid[-2], mid[-1], mid[-4]
        qty, free, rate, amount = tail
        return _raj_record(hsn, gst, mrp, name, mfr, pack, batch, exp, qty, free, rate, amount, row_no)
    except (IndexError, ValueError):
        return None


def _raj_from_tail(line: str, row_no: int) -> Optional[Dict[str, Any]]:
    hsn_m = re.match(r"^(\d{3,8})\b", line)
    if not hsn_m:
        return None
    hsn = hsn_m.group(1)
    rest = line[hsn_m.end():].strip()
    nums: List[Tuple[int, float]] = []
    tokens = rest.split()
    cut = len(tokens)
    for i in range(len(tokens) - 1, -1, -1):
        val = _clean_num(tokens[i])
        if val is not None:
            nums.insert(0, (i, val))
            if len(nums) >= 4:
                cut = i
                break
        elif nums:
            break
    if len(nums) < 3:
        return None
    amount = nums[-1][1]
    rate = nums[-2][1]
    free = nums[-3][1] if len(nums) >= 3 else 0.0
    qty = nums[-4][1] if len(nums) >= 4 else 0.0
    if qty <= 0 and rate > 0:
        qty = round(amount / rate, 4)
    if qty <= 0 or rate <= 0 or amount <= 0:
        return None
    if abs(qty * rate - amount) > max(1.0, amount * 0.05):
        qty = round(amount / rate, 4) if rate > 0 else 0.0
    if qty <= 0:
        return None

    left = " ".join(tokens[:cut]).strip()
    gst_m = re.match(r"^(\d+(?:\.\d+)?)\s+", left)
    gst = gst_m.group(1) if gst_m else "5"
    if gst_m:
        left = left[gst_m.end():].strip()

    exp_m = re.search(r"\b(\d{1,2}[/\-]\d{2,4})\b", left)
    expiry = exp_m.group(1) if exp_m else ""
    if exp_m:
        left = left[: exp_m.start()] + left[exp_m.end():]
    left = re.sub(r"\s+", " ", left).strip()

    mfr_m = re.search(r"\b([A-Z]{2,5})\s+(\S+)\s+(\S+)\s*$", left, flags=re.I)
    mfr = pack = batch = ""
    name = left
    if mfr_m:
        mfr, pack, batch = mfr_m.group(1), mfr_m.group(2), mfr_m.group(3)
        name = left[: mfr_m.start()].strip()
    name = re.sub(r"^[\d,.]+\s+", "", name)
    name = re.sub(r"\s+", " ", name).strip()
    if len(name) < 3:
        return None
    return _raj_record(hsn, gst, "0", name, mfr, pack, batch, expiry, qty, free, rate, amount, row_no)


def _raj_record(
    hsn, gst, mrp, name, mfr, pack, batch, exp, qty, free, rate, amount, row_no,
) -> Optional[Dict[str, Any]]:
    qty_f, rate_f, amt_f = _f(qty), _f(rate), _f(amount)
    if qty_f <= 0 or rate_f <= 0:
        return None
    if amt_f <= 0:
        amt_f = round(qty_f * rate_f, 2)
    if abs(qty_f * rate_f - amt_f) > max(1.0, amt_f * 0.06):
        qty_f = round(amt_f / rate_f, 4)
    if qty_f <= 0:
        return None
    return {
        "name": name.strip(),
        "hsn_code": str(hsn),
        "manufacturer": (mfr or "").strip(),
        "pack": (pack or "").strip(),
        "batch": (batch or "").strip(),
        "expiry": (exp or "").strip(),
        "mrp": _f(mrp),
        "qty": qty_f,
        "free_qty": _f(free),
        "gst_pct": normalize_total_gst_pct(_f(gst)),
        "rate": rate_f,
        "amount": amt_f,
        "source_row": row_no,
        "gst_from_bill": True,
    }


def _parse_vinar_structured(line: str, row_no: int) -> Optional[Dict[str, Any]]:
    from core.purchase_bill_image_parser import _parse_vinar_line
    return _parse_vinar_line(_normalize_line(line), row_no)


def _parse_om_sai_structured(line: str, row_no: int) -> Optional[Dict[str, Any]]:
    from core.purchase_bill_image_parser import _parse_om_sai_line
    return _parse_om_sai_line(_normalize_line(line), row_no)


def _parse_shree_structured(line: str, row_no: int) -> Optional[Dict[str, Any]]:
    from core.purchase_bill_image_parser import _parse_shree_line
    return _parse_shree_line(_normalize_line(line), row_no)


def _parse_jai_ganesh_structured(line: str, row_no: int) -> Optional[Dict[str, Any]]:
    from core.purchase_bill_image_parser import _parse_jai_ganesh_line
    return _parse_jai_ganesh_line(_normalize_line(line), row_no)


def _normalize_line(line: str) -> str:
    line = re.sub(r"(\d),(\d{2})\b", r"\1.\2", line)
    line = re.sub(r"[\]|{}]", " ", line)
    line = re.sub(r"\s+", " ", line).strip()
    if "  " in line:
        line = re.sub(r" {2,}", "\t", line)
    return line


def _f(value: Any) -> float:
    try:
        return float(str(value or "0").replace(",", ""))
    except (TypeError, ValueError):
        return 0.0


def _clean_num(token: str) -> Optional[float]:
    token = (token or "").strip().replace(",", "")
    if not token:
        return None
    if re.match(r"^\d{1,2}/\d{2,4}$", token):
        return None
    token = re.sub(r"[^0-9.\-]", "", token)
    if not token or token in (".", "-"):
        return None
    try:
        return float(token)
    except ValueError:
        return None
