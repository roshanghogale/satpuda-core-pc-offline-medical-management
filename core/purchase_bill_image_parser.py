"""
Parse OCR text from photographed pharmacy purchase bills into PurchaseInvoice.

Supports common Maharashtra supplier layouts: VINAR, Shree Distributor, Om Sai,
Raj Medico, Jai Ganesh Pharmavet, plus generic line fallback.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from core.purchase_invoice_engine import (
    extract_invoice_totals,
    normalize_total_gst_pct,
)
from core.purchase_importer import (
    PurchaseInvoice,
    _item_from_record,
    _records_from_text,
)

_NUM = r"[\d,]+(?:\.\d{1,2})?"
_HSN = r"\d{3,8}"
_GSTIN_RE = re.compile(r"\b(\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z])\b", re.I)


def parse_ocr_text_to_invoice(
    text: str,
    source_path: str = "",
    table_lines: Optional[List[str]] = None,
    sections=None,
) -> PurchaseInvoice:
    from core.bill_scan_sections import BillScanSections, classify_text_sections

    text = (text or "").strip()
    if sections is None:
        sections = classify_text_sections(text, table_lines)

    if isinstance(sections, BillScanSections) and sections.medicines_lines:
        line_blob = sections.medicines_text
        table_lines = sections.medicines_lines
    elif table_lines:
        line_blob = "\n".join(line for line in table_lines if line and line.strip())
        text = line_blob or text
    else:
        line_blob = text

    header_text = sections.supplier_text if isinstance(sections, BillScanSections) else text
    totals_text = sections.totals_text if isinstance(sections, BillScanSections) else text
    fmt_source = header_text or text
    med_lines = (
        sections.medicines_lines
        if isinstance(sections, BillScanSections) and sections.medicines_lines
        else (table_lines or line_blob.splitlines())
    )

    candidates = [
        _parse_vinar(line_blob, source_path),
        _parse_shree_distributor(line_blob, source_path),
        _parse_om_sai(line_blob, source_path),
        _parse_raj_medico(line_blob, source_path),
        _parse_jai_ganesh(line_blob, source_path),
        _parse_fuzzy(line_blob, source_path),
        _parse_generic(line_blob, source_path),
    ]
    fmt_hint = _detect_format(fmt_source)
    if fmt_hint == "generic" and med_lines:
        fmt_hint = _detect_format("\n".join(med_lines[:8]))
    fmt_rank = {
        "vinar": "image_vinar",
        "shree_distributor": "image_shree_distributor",
        "om_sai": "image_om_sai",
        "raj_medico": "image_raj_medico",
        "jai_ganesh": "image_jai_ganesh",
        "generic": "image_generic",
    }
    preferred = fmt_rank.get(fmt_hint, "")
    if fmt_hint and fmt_hint != "generic":
        from core.bill_field_mapper import parse_structured_medicines

        struct_records = parse_structured_medicines(med_lines, fmt_hint)
        if struct_records:
            candidates.insert(
                0,
                _invoice_from_records(struct_records, source_path, preferred or "image_structured"),
            )

    def _score(inv: PurchaseInvoice) -> tuple:
        valid = [it for it in inv.items if _is_plausible_product_name(it.name)]
        specific = inv.parser.startswith("image_") and inv.parser not in (
            "image_fuzzy", "image_generic",
        )
        qty_ok = sum(
            1 for it in valid
            if float(it.qty or 0) > 0 and float(it.rate or 0) > 0 and float(it.amount or 0) > 0
            and abs(float(it.qty) * float(it.rate) - float(it.amount))
            <= max(0.35, float(it.amount) * 0.04)
        )
        fmt_match = 1 if inv.parser == preferred else 0
        # Prefer format-specific parser when bill header matches (avoids fuzzy false wins).
        if preferred and inv.parser == preferred:
            fmt_match = 3
        return (fmt_match, len(valid), qty_ok, 1 if specific else 0)

    invoice = max(candidates, key=_score)
    invoice.items = [it for it in invoice.items if _is_plausible_product_name(it.name)]
    if not invoice.items:
        invoice = _parse_fuzzy(line_blob, source_path)
    invoice.raw_text = sections.combined_text if isinstance(sections, BillScanSections) else text

    from core.bill_field_mapper import apply_supplier_to_invoice, apply_totals_to_invoice

    apply_supplier_to_invoice(invoice, header_text)
    if invoice.items:
        _merge_supplemental_items(invoice, candidates)
    totals = apply_totals_to_invoice(invoice, totals_text or text)
    _apply_image_discount_and_charges(invoice, totals)
    return invoice


def _item_name_sig(name: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (name or "").upper())[:16]


def _item_dedup_key(item: Any) -> str:
    return "{}|{}".format(
        _item_name_sig(getattr(item, "name", "")),
        round(float(getattr(item, "amount", 0) or 0), 2),
    )


def _items_overlap(existing: Any, candidate: Any) -> bool:
    if abs(float(existing.amount or 0) - float(candidate.amount or 0)) > 0.05:
        return False
    a = _item_name_sig(existing.name)
    b = _item_name_sig(candidate.name)
    if not a or not b:
        return False
    return a == b or a in b or b in a


def _supplemental_row_rejected(item: Any, parser: str) -> bool:
    if parser != "image_fuzzy":
        return False
    name = (getattr(item, "name", "") or "").upper()
    if re.search(r"\b(SMART|1X\d|IX\d|MXSML|IXZ)\b", name):
        return True
    if re.search(r"[|]", name):
        return True
    if len(name.split()) > 7:
        return True
    if re.match(r"^[\d.]+\s", name):
        return True
    return False


def _merge_supplemental_items(
    invoice: PurchaseInvoice,
    candidates: List[PurchaseInvoice],
) -> None:
    """Fill gaps by merging plausible rows from other parsers (usually fuzzy)."""
    primary_sum = round(sum(float(it.amount or 0) for it in invoice.items), 2)
    footer_gross = float(invoice.gross_amount or 0)
    if footer_gross > 0 and primary_sum >= footer_gross * 0.97:
        return
    seen = {_item_dedup_key(it) for it in invoice.items}
    extras: List[Any] = []
    for cand in candidates:
        if cand is invoice:
            continue
        for it in cand.items:
            if not _is_plausible_product_name(it.name):
                continue
            if _supplemental_row_rejected(it, cand.parser):
                continue
            if any(_items_overlap(ex, it) for ex in invoice.items):
                continue
            key = _item_dedup_key(it)
            if key in seen:
                continue
            amt = float(it.amount or 0)
            qty = float(it.qty or 0)
            rate = float(it.rate or 0)
            if amt <= 0 or qty <= 0 or rate <= 0:
                continue
            if abs(qty * rate - amt) > max(0.5, amt * 0.06):
                continue
            seen.add(key)
            extras.append(it)
    if extras:
        invoice.items.extend(extras)


def _apply_image_discount_and_charges(
    invoice: PurchaseInvoice,
    totals: Dict[str, float],
) -> None:
    """Derive cash-discount % and fold delivery/other charges into round-off."""
    base = (
        float(totals.get("discount_base") or 0)
        or float(invoice.gross_amount or 0)
        or float(invoice.line_gross or 0)
    )
    cash = float(invoice.cash_discount or 0)
    prod = float(invoice.product_discount or 0)
    if base > 0:
        invoice.discount_base = base
        if cash > 0 and not invoice.cash_discount_pct:
            invoice.cash_discount_pct = round(cash * 100.0 / base, 2)
        if prod > 0 and not invoice.product_discount_pct:
            invoice.product_discount_pct = round(prod * 100.0 / base, 2)
    added = float(invoice.added_charges or totals.get("added_charges") or 0)
    if added:
        invoice.added_charges = added


def _detect_format(text: str) -> str:
    u = text.upper()
    if "VINAR MEDICAL" in u or (
        "DESCRIPTION" in u and "SGST" in u and "CGST" in u and "MFG" in u
    ):
        return "vinar"
    if "SHREE DISTRIBUTOR" in u or (
        re.search(r"\bGST\s*%", u) and "M.R.P" in u
    ):
        return "shree_distributor"
    if "OM SAI MEDICO" in u or ("NAME OF PRODUCT" in u and "EXPDT" in u):
        return "om_sai"
    if "RAJ MEDICO" in u or ("PRODUCT NAME" in u and "KHAMGAON" in u):
        return "raj_medico"
    if "JAI GANESH PHARMAVET" in u or "PHARMAVET" in u:
        return "jai_ganesh"
    return "generic"


def _apply_header_fields(invoice: PurchaseInvoice, text: str) -> None:
    u = text.upper()
    m = _GSTIN_RE.search(text)
    if m:
        invoice.supplier_gstin = m.group(1).upper()
    for name_key, patterns in (
        ("VINAR MEDICAL AGENCIES", [r"VINAR\s+MEDICAL"]),
        ("SHREE DISTRIBUTOR", [r"SHREE\s+DISTRIBUTOR"]),
        ("OM SAI MEDICO", [r"OM\s+SAI\s+MEDICO"]),
        ("RAJ MEDICO'S, KHAMGAON", [r"RAJ\s+MEDICO"]),
        ("JAI GANESH PHARMAVET", [r"JAI\s+GANESH\s+PHARMAVET"]),
    ):
        for pat in patterns:
            if re.search(pat, u):
                invoice.supplier_name = name_key
                break
        if invoice.supplier_name:
            break

    inv_patterns = (
        r"Invoice\s*No\.?\s*[:\s]*([A-Z0-9/\-]+)",
        r"INVOICE\s*NO\.?\s*[:\s]*([A-Z0-9/\-]+)",
        r"\b(VM\d{3,6})\b",
        r"\b(CC/\d+)\b",
        r"\b(JCR\d+)\b",
        r"\bINV\s*(\d+)\b",
    )
    for pat in inv_patterns:
        m = re.search(pat, text, flags=re.I)
        if m:
            invoice.invoice_number = m.group(1).strip()
            break

    date_patterns = (
        r"Date\s*[:\s]*(\d{1,2}[-/]\d{1,2}[-/]\d{2,4})",
        r"(\d{1,2}[-/]\d{1,2}[-/]\d{4})",
    )
    for pat in date_patterns:
        m = re.search(pat, text, flags=re.I)
        if m:
            invoice.invoice_date = m.group(1).strip()
            break


def _parse_vinar(text: str, source_path: str) -> PurchaseInvoice:
    records: List[Dict[str, Any]] = []
    for row_no, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line or _is_header_noise(line):
            continue
        rec = _parse_vinar_line(line, row_no)
        if rec:
            records.append(rec)
    return _invoice_from_records(records, source_path, "image_vinar")


def _normalize_ocr_line(line: str) -> str:
    line = re.sub(r"(\d),(\d{2})\b", r"\1.\2", line)
    line = re.sub(r"(\d)\.,(\d)", r"\1.\2", line)
    line = re.sub(r"[\]|{}|:|]", " ", line)
    line = re.sub(r"(\d)[dD](\b|\d)", r"\1.0\2", line)
    line = re.sub(r"\bJ00\b", "0.00", line, flags=re.I)
    line = re.sub(r"(\d+\.\d{2})\d+\b", r"\1", line)
    line = re.sub(r"(\d+\.\d{2})\.(?:\s|$)", r"\1 ", line)
    line = re.sub(r"(\d+\.\d{2})([A-Za-z])", r"\1 \2", line)
    # OCR glues expiry to qty: 11/2812.50 -> 11/28 2.50
    line = re.sub(
        r"\b(\d{1,2}/\d{2,4})1?(\d\.\d{2})\b",
        r"\1 \2",
        line,
    )
    line = re.sub(r"(\d{1,2})/(\d{2,4})/(\d+\.\d{2})", r"\1/\2 \3", line)
    line = re.sub(r"\b(\d{2})2(\d{2})/(\d+\.\d{2})\b", r"\1/\2 \3", line)
    line = re.sub(r"\b(\d{1,2})(\d{2})/(\d+\.\d{2})\b", r"\1/\2 \3", line)
    line = re.sub(r"\b(\d{1,2})\s+(\d{2})(\s+\d+\.\d{2})", r"\1/\2\3", line)
    line = re.sub(r"(\d{1,2}/\d{2,4})/(\d+\.\d{2})", r"\1 \2", line)
    line = re.sub(r"(\d{1,2}/\d{2,4})/(\d+)", r"\1 \2", line)
    line = re.sub(r"(\d{4,6})\|(\d+\.\d{2})", r"\1 \2", line)
    line = re.sub(r"(\d{4,6})/(\d\.\d{2})", r"\1 \2", line)
    # Batch+exp glued: 26516 1228 -> 26S16 1/28 (common VINAR OCR)
    line = re.sub(
        r"\b(\d{2})([A-Z]\d{2})\s+(\d)(\d{2})\b",
        lambda m: "{}S{} {}/{}".format(m.group(1), m.group(2)[1:], m.group(3), m.group(4))
        if m.group(2)[0].isalpha() else m.group(0),
        line,
        flags=re.I,
    )
    line = re.sub(r"\s+", " ", line).strip()
    return line


def _split_vinar_mrp_prefix(line: str) -> tuple:
    """Split leading MRP even when OCR glues it to the product name (130.0OILUUFRESH)."""
    line = re.sub(r"^[A-Z0-9]*[oO]\.[oO0]+\s+", "", line, flags=re.I)
    m = re.match(r"^([\d,]+(?:\.\d{1,2})?)[oO]?\s+(.+)$", line)
    if m:
        return _f(m.group(1)), m.group(2).strip()
    m = re.match(r"^([\d,]+(?:\.\d+)?)[oO]([A-Za-z].+)$", line)
    if m:
        return _f(m.group(1)), m.group(2).strip()
    m = re.match(r"^[A-Za-z0-9]*[oO]\.?[oO]?\s+(.+)$", line)
    if m:
        return 0.0, m.group(1).strip()
    return 0.0, line.strip()


def _clean_num_token(token: str) -> Optional[float]:
    token = (token or "").strip().replace(",", "")
    if not token:
        return None
    token = re.sub(r"[^0-9.\-]", "", token)
    if not token or token in (".", "-"):
        return None
    try:
        return float(token)
    except ValueError:
        return None


def _fix_vinar_expiry(expiry: str) -> str:
    expiry = (expiry or "").strip()
    m = re.match(r"^(\d{1,2}/\d{2})\d$", expiry)
    if m:
        return m.group(1)
    if re.match(r"^\d{1,2}/\d{2,4}$", expiry):
        return expiry
    if re.match(r"^\d{5}$", expiry):
        return "{}/{}".format(expiry[:2], expiry[2:4])
    if re.match(r"^\d{4}$", expiry):
        if int(expiry[:2]) <= 12:
            return "{}/{}".format(expiry[:2], expiry[2:])
        return "{}/{}".format(int(expiry[0]), expiry[2:])
    m = re.match(r"^(\d{1,2})(\d{2})$", expiry)
    if m:
        return "{}/{}".format(m.group(1), m.group(2))
    return expiry


def _extract_vinar_tail_numbers(tail: str) -> List[float]:
    tail = _normalize_ocr_line(tail)
    values: List[float] = []
    for tok in tail.split():
        if re.match(r"^\d{4,6}$", tok):
            continue
        val = _clean_num_token(tok)
        if val is not None:
            values.append(val)
    return values


def _parse_vinar_tail(
    tail: str,
    mrp: float,
    name: str,
    hsn: str,
    pack: str,
    mfg: str,
    batch: str,
    expiry: str,
    row_no: int,
) -> Optional[Dict[str, Any]]:
    nums = _extract_vinar_tail_numbers(tail)
    if len(nums) < 4:
        return None
    amt = nums[-1]
    if amt <= 0:
        return None
    qty = free_qty = cgst = sgst = rate = 0.0
    # VINAR columns: QTY FREE SGST CGST RATE [DIS] AMT
    if len(nums) >= 7:
        qty, free_qty, sgst, cgst, rate, _dis, amt = nums[-7:]
    elif len(nums) == 6:
        if abs(nums[0] * nums[3] - nums[5]) <= max(0.5, nums[5] * 0.03):
            qty, free_qty, sgst, rate, _dis, amt = (
                nums[0], nums[1], nums[2], nums[3], nums[4], nums[5],
            )
            cgst = sgst
        else:
            qty, free_qty, sgst, cgst, rate, amt = nums[-6:]
    elif len(nums) == 5:
        if abs(nums[0] * nums[3] - nums[4]) <= max(0.5, nums[4] * 0.03):
            qty, free_qty, sgst, rate, amt = nums
            cgst = sgst
        else:
            qty, free_qty, sgst, cgst, amt = nums
            rate = round(amt / qty, 4) if qty > 0 else 0.0
    elif len(nums) == 4:
        sgst, cgst, rate, amt = nums[-4:]
        qty = round(amt / rate, 4) if rate > 0 else 0.0
    if rate <= 0.01 and qty > 0:
        rate = round(amt / qty, 4)
    if qty <= 0 and rate > 0:
        qty = round(amt / rate, 4)
    if rate <= 0 and qty > 0:
        rate = round(amt / qty, 4)
    if qty <= 0 or rate <= 0 or amt <= 0:
        return None
    if abs(qty * rate - amt) > max(0.5, amt * 0.06):
        corrected = round(amt / rate, 4) if rate > 0 else 0.0
        if corrected > 0 and abs(corrected * rate - amt) <= max(0.5, amt * 0.06):
            qty = corrected
        elif qty >= 10 and abs((qty / 10) * rate - amt) <= max(0.5, amt * 0.06):
            qty = round(qty / 10, 4)
        else:
            rate = round(amt / qty, 4)
    if not _is_plausible_product_name(name):
        return None
    gst_pct = normalize_total_gst_pct(0, cgst, sgst)
    return {
        "name": re.sub(
            r"^(Col|Ool|A5o|49|85|79|\.0o|\d+\.0o|I)\s*",
            "",
            name.strip(),
            flags=re.I,
        ),
        "mrp": mrp,
        "hsn_code": hsn,
        "pack": pack.replace("/", ""),
        "manufacturer": mfg,
        "batch": batch,
        "expiry": expiry,
        "qty": qty,
        "free_qty": free_qty,
        "gst_pct": gst_pct,
        "cgst_pct": cgst,
        "sgst_pct": sgst,
        "rate": rate,
        "amount": amt if abs(qty * rate - amt) <= max(0.3, amt * 0.04) else round(qty * rate, 2),
        "source_row": row_no,
        "gst_from_bill": True,
    }


def _parse_vinar_loose(
    line: str,
    mrp: float,
    rest: str,
    row_no: int,
) -> Optional[Dict[str, Any]]:
    """End-anchored VINAR row when column regex fails on noisy OCR."""
    hsn_m = re.search(rf"\b({_HSN})\b", rest)
    if not hsn_m:
        return None
    hsn = hsn_m.group(1)
    tail = rest[hsn_m.end():].strip()
    nums = _extract_vinar_tail_numbers(tail)
    if len(nums) < 4:
        nums = [_f(x) for x in re.findall(r"[\d,]+\.\d{2}", tail)]
    if len(nums) < 4:
        return None
    name = rest[: hsn_m.start()].strip()
    name = re.sub(
        r"^(Col|Ool|A5o|49|85|79|\.0o|\d+\.0o|I|\d+\.\d{2})\s*",
        "",
        name,
        flags=re.I,
    )
    name = re.sub(r"^\d+\.\d{2}\s*", "", name)
    if not _is_plausible_product_name(name):
        return None
    mid = tail
    pack = mfg = batch = expiry = ""
    pack_m = re.search(r"\b(1X\S+)\b", mid, flags=re.I)
    if pack_m:
        pack = pack_m.group(1)
    exp_m = re.search(r"\b(\d{1,2}/\d{2,4})\b", mid)
    if exp_m:
        expiry = _fix_vinar_expiry(exp_m.group(1))
    tokens = [t for t in mid.split() if t]
    for tok in tokens:
        if re.match(r"^[A-Z]{2,6}$", tok, flags=re.I) and tok.upper() not in ("SMART", "FREE"):
            mfg = tok
            break
    for tok in tokens:
        if tok == pack or tok == mfg or re.match(r"^\d{1,2}/\d{2,4}$", tok):
            continue
        if re.match(r"^[A-Z0-9][A-Z0-9\-]{3,}$", tok, flags=re.I):
            batch = tok
            break
    return _parse_vinar_tail(
        tail,
        mrp,
        name,
        hsn,
        pack,
        mfg,
        batch,
        expiry,
        row_no,
    )


def _parse_vinar_line(line: str, row_no: int) -> Optional[Dict[str, Any]]:
    line = _normalize_ocr_line(line)
    mrp, rest = _split_vinar_mrp_prefix(line)
    _exp = r"(\d{1,2}/\d{2,4}|\d{4,5})"
    patterns = (
        (
            rf"^(.+?)\s+({_HSN})\s+/?(1X\S+)\s+(\w+)\s+(\S+)\s+"
            + _exp + r"\s+(.*)$",
            ("name", "hsn", "pack", "mfg", "batch", "expiry", "tail"),
        ),
        (
            rf"^(.+?)\s+({_HSN})\s+(\S+)\s+(\w+)\s+(\S+)\s+"
            + _exp + r"\s+(.*)$",
            ("name", "hsn", "pack", "mfg", "batch", "expiry", "tail"),
        ),
        (
            rf"^(.+?)\s+({_HSN})\s+(\w+)\s+(\S+)\s+"
            + _exp + r"\s+(.*)$",
            ("name", "hsn", "mfg", "batch", "expiry", "tail"),
        ),
    )
    for pat, fields in patterns:
        head = re.match(pat, rest, flags=re.I)
        if not head:
            continue
        vals = dict(zip(fields, head.groups()))
        expiry = _fix_vinar_expiry(vals.get("expiry", ""))
        pack = vals.get("pack", "") or ""
        if pack and not re.match(r"^1X", pack, re.I) and "mfg" not in vals:
            pass
        elif "mfg" in vals and not pack:
            pack = ""
        rec = _parse_vinar_tail(
            vals["tail"],
            mrp,
            vals["name"],
            vals["hsn"],
            pack,
            vals.get("mfg", ""),
            vals.get("batch", ""),
            expiry,
            row_no,
        )
        if rec:
            return rec

    loose = _parse_vinar_loose(line, mrp, rest, row_no)
    if loose:
        return loose

    tokens = line.split()
    if len(tokens) < 13:
        return None
    if not re.match(r"^[\d,]+(?:\.\d+)?$", tokens[0].replace(",", "")):
        return None
    try:
        mrp = _f(tokens[0])
        amt = _f(tokens[-1])
        rate = _f(tokens[-3])
        cgst = _f(tokens[-4])
        sgst = _f(tokens[-5])
        free_qty = _f(tokens[-6])
        qty = _f(tokens[-7])
        expiry = tokens[-8]
        batch = tokens[-9]
        mfg = tokens[-10]
        pack = tokens[-11]
        hsn = tokens[-12]
        name = " ".join(tokens[1:-12])
    except (IndexError, ValueError):
        return None
    if qty <= 0 or rate <= 0 or amt <= 0:
        return None
    if not re.match(rf"^({_HSN})$", hsn):
        return None
    if not _is_plausible_product_name(name):
        return None
    gst_pct = normalize_total_gst_pct(0, cgst, sgst)
    if abs(qty * rate - amt) > max(0.15, amt * 0.02):
        amt = round(qty * rate, 2)
    return {
        "name": name,
        "mrp": mrp,
        "hsn_code": hsn,
        "pack": pack,
        "manufacturer": mfg,
        "batch": batch,
        "expiry": expiry,
        "qty": qty,
        "free_qty": free_qty,
        "gst_pct": gst_pct,
        "cgst_pct": cgst,
        "sgst_pct": sgst,
        "rate": rate,
        "amount": amt,
        "source_row": row_no,
        "gst_from_bill": True,
    }


def _parse_shree_distributor(text: str, source_path: str) -> PurchaseInvoice:
    records: List[Dict[str, Any]] = []
    for row_no, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line or _is_header_noise(line):
            continue
        rec = _parse_shree_line(line, row_no)
        if rec:
            records.append(rec)
    return _invoice_from_records(records, source_path, "image_shree_distributor")


def _parse_shree_line(line: str, row_no: int) -> Optional[Dict[str, Any]]:
    line = _normalize_ocr_line(line)
    m = re.match(
        rf"^({_NUM})\s+(.+?)\s+({_HSN})\s+([A-Z]{{2,4}})\s+"
        rf"(\S+)\s+(\S+)\s+(\S+)\s+({_NUM})\s+(\S*)\s+({_NUM})\s+({_NUM})\s+({_NUM})\s*$",
        line,
        flags=re.I,
    )
    if not m:
        m = re.match(
            rf"^({_NUM})\s+(.+?)\s+({_HSN})\s+([A-Z]{{2,4}})\s+"
            rf"(\S+)\s+(\S+)\s+(\S+)\s+({_NUM})\s+({_NUM})\s+({_NUM})\s+({_NUM})\s*$",
            line,
            flags=re.I,
        )
        if not m:
            return None
        gst_pct, name, hsn, com, batch, exp, pack, qty, mrp, rate, amount = m.groups()
        free_qty = 0
    else:
        gst_pct, name, hsn, com, batch, exp, pack, qty, _scm, mrp, rate, amount = m.groups()
        free_qty = 0
    qty_f = _f(qty)
    rate_f = _f(rate)
    amt_f = _f(amount)
    gst_f = normalize_total_gst_pct(_f(gst_pct))
    if qty_f <= 0 or rate_f <= 0:
        return None
    if abs(qty_f * rate_f - amt_f) > max(0.2, amt_f * 0.02):
        amt_f = round(qty_f * rate_f, 2)
    return {
        "name": name.strip(),
        "hsn_code": hsn,
        "manufacturer": com,
        "batch": batch,
        "expiry": exp,
        "pack": pack,
        "qty": qty_f,
        "free_qty": free_qty,
        "gst_pct": gst_f,
        "mrp": _f(mrp),
        "rate": rate_f,
        "amount": amt_f,
        "source_row": row_no,
        "gst_from_bill": True,
    }


def _parse_om_sai(text: str, source_path: str) -> PurchaseInvoice:
    records: List[Dict[str, Any]] = []
    for row_no, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line or _is_header_noise(line):
            continue
        rec = _parse_om_sai_line(line, row_no)
        if rec:
            records.append(rec)
    return _invoice_from_records(records, source_path, "image_om_sai")


def _parse_om_sai_loose(line: str, row_no: int) -> Optional[Dict[str, Any]]:
    """Fallback for Om Sai rows when column spacing is broken in OCR."""
    nums = [_f(x) for x in re.findall(r"[\d,]+\.\d{2}", line)]
    if len(nums) < 3:
        return None
    amount = nums[-1]
    rate = nums[-2]
    qty = nums[-3] if len(nums) >= 3 else 0.0
    if qty <= 0 or rate <= 0 or amount <= 0:
        return None
    if abs(qty * rate - amount) > max(1.0, amount * 0.05):
        qty = round(amount / rate, 4) if rate > 0 else 0.0
    if qty <= 0:
        return None
    hsn_m = re.search(rf"\b({_HSN})\b", line)
    if not hsn_m:
        return None
    gst_m = re.search(r"\b(\d{1,2}(?:\.\d+)?)\s*%", line)
    gst_pct = normalize_total_gst_pct(_f(gst_m.group(1))) if gst_m else 5.0
    name = line[: hsn_m.start()].strip()
    name = re.sub(r"^[\d.]+\s+", "", name)
    name = re.sub(rf"\b{hsn_m.group(1)}\b", "", name, count=1)
    name = re.sub(r"\s+", " ", name).strip(" -|,")
    if not _is_plausible_product_name(name):
        tail = line[hsn_m.end():]
        name_m = re.search(r"([A-Z][A-Z0-9][A-Z0-9 \-]{4,40})", tail, flags=re.I)
        name = name_m.group(1).strip() if name_m else ""
    if not _is_plausible_product_name(name):
        return None
    return {
        "name": name[:120],
        "hsn_code": hsn_m.group(1),
        "qty": qty,
        "rate": rate,
        "amount": amount,
        "gst_pct": gst_pct,
        "source_row": row_no,
        "gst_from_bill": True,
    }


def _parse_om_sai_line(line: str, row_no: int) -> Optional[Dict[str, Any]]:
    line = _normalize_ocr_line(line)
    line = re.sub(r"^902\s+", "", line)
    line = re.sub(r"^1107\s+", "", line)
    m = re.search(
        rf"({_HSN})\s+({_NUM})\s+(.+?)\s+(\S+)\s+(\S+)\s+({_NUM})\s+(\S+)\s+"
        rf"({_NUM})\s+({_NUM})\s+({_NUM})\s+({_NUM})\s*(?:\s+({_NUM}))?\s*$",
        line,
    )
    if not m:
        m = re.search(
            rf"({_HSN})\s+({_NUM})\s+(.+?)\s+(\S+)\s+(\S+)\s+({_NUM})\s+(\S+)\s+"
            rf"({_NUM})\s+({_NUM})\s+({_NUM})\s*$",
            line,
        )
        if not m:
            return _parse_om_sai_loose(line, row_no)
        hsn, gst_pct, name, pack, batch, mrp, exp, qty, rate, amount = m.groups()
        free = "0"
    else:
        groups = m.groups()
        if len(groups) == 12:
            hsn, gst_pct, name, pack, batch, mrp, exp, qty, free, rate, amount, _extra = groups
        else:
            hsn, gst_pct, name, pack, batch, mrp, exp, qty, free, rate, amount = groups
    qty_f = _f(qty)
    rate_f = _f(rate)
    amt_f = _f(amount)
    if qty_f <= 0:
        return None
    return {
        "name": name.strip(),
        "hsn_code": hsn,
        "pack": pack,
        "batch": batch,
        "mrp": _f(mrp),
        "expiry": exp,
        "qty": qty_f,
        "free_qty": _f(free),
        "gst_pct": normalize_total_gst_pct(_f(gst_pct)),
        "rate": rate_f,
        "amount": amt_f if amt_f > 0 else round(qty_f * rate_f, 2),
        "source_row": row_no,
        "gst_from_bill": True,
    }


def _parse_raj_medico(text: str, source_path: str) -> PurchaseInvoice:
    records: List[Dict[str, Any]] = []
    for row_no, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line or _is_header_noise(line):
            continue
        rec = _parse_raj_line(line, row_no)
        if rec:
            records.append(rec)
    return _invoice_from_records(records, source_path, "image_raj_medico")


def _parse_raj_line(line: str, row_no: int) -> Optional[Dict[str, Any]]:
    m = re.search(
        rf"({_HSN})\s+({_NUM})\s+(.+?)\s+([A-Z]{{2,5}})\s+(\S+)\s+(\S+)\s+"
        rf"(\S+)\s+({_NUM})\s+({_NUM})\s+({_NUM})\s+({_NUM})\s+({_NUM})\s*$",
        line,
        flags=re.I,
    )
    if not m:
        return None
    hsn, gst_pct, name, mfr, pack, batch, exp, qty, free, rate, amount = m.groups()
    qty_f = _f(qty)
    rate_f = _f(rate)
    if qty_f <= 0:
        return None
    amt_f = _f(amount) or round(qty_f * rate_f, 2)
    return {
        "name": name.strip(),
        "hsn_code": hsn,
        "manufacturer": mfr,
        "pack": pack,
        "batch": batch,
        "expiry": exp,
        "qty": qty_f,
        "free_qty": _f(free),
        "gst_pct": normalize_total_gst_pct(_f(gst_pct)),
        "rate": rate_f,
        "amount": amt_f,
        "source_row": row_no,
        "gst_from_bill": True,
    }


def _split_jai_ganesh_segments(line: str) -> List[str]:
    line = _normalize_ocr_line(line)
    parts = re.split(rf"(?=(?:\b\d{{1,2}}\s+)?{_HSN}\s+[A-Z]{{2,6}}\s+)", line)
    segments = [p.strip() for p in parts if p.strip()]
    return segments if len(segments) > 1 else [line]


def _parse_jai_ganesh(text: str, source_path: str) -> PurchaseInvoice:
    records: List[Dict[str, Any]] = []
    for row_no, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line or _is_header_noise(line):
            continue
        for segment in _split_jai_ganesh_segments(line):
            rec = _parse_jai_ganesh_line(segment, row_no)
            if rec:
                records.append(rec)
    return _invoice_from_records(records, source_path, "image_jai_ganesh")


def _parse_jai_ganesh_line(line: str, row_no: int) -> Optional[Dict[str, Any]]:
    line = _normalize_ocr_line(line)
    m = re.search(
        rf"({_HSN})\s+([A-Z]{{2,6}})\s+(.+?)\s+(\S+)\s+(\S+)\s+(\S+)\s+"
        rf"({_NUM})\s+({_NUM})\s+({_NUM})\s+({_NUM})\s+({_NUM})\s+({_NUM})\s+(\d+)\s*$",
        line,
        flags=re.I,
    )
    if not m:
        m = re.search(
            rf"({_HSN})\s+([A-Z]{{2,6}})\s+(.+?)\s+(\S+)\s+(\S+)\s+(\S+)\s+"
            rf"({_NUM})\s+({_NUM})\s+({_NUM})\s+({_NUM})\s+({_NUM})\s+(\d+)\s*$",
            line,
            flags=re.I,
        )
        if not m:
            return None
        hsn, mfg, name, pkg, batch, exp, mrp, qty, free, rate, amount, gst = m.groups()
        disc = "0"
    else:
        hsn, mfg, name, pkg, batch, exp, mrp, qty, free, rate, amount, disc, gst = m.groups()
    qty_f = _f(qty)
    rate_f = _f(rate)
    mrp_f = _f(mrp)
    if qty_f <= 0 or rate_f <= 0:
        return None
    amt_f = _f(amount) or round(qty_f * rate_f, 2)
    if amt_f <= 0 or amt_f > 250000:
        return None
    if abs(qty_f * rate_f - amt_f) > max(1.0, amt_f * 0.04):
        qty_corr = round(amt_f / rate_f, 4) if rate_f > 0 else 0.0
        if qty_corr > 0 and abs(qty_corr * rate_f - amt_f) <= max(0.5, amt_f * 0.03):
            qty_f = qty_corr
        else:
            return None
    if mrp_f > 0 and amt_f > mrp_f * max(qty_f, 1.0) * 1.35:
        return None
    if not _is_plausible_product_name(name):
        return None
    return {
        "name": name.strip(),
        "hsn_code": hsn,
        "manufacturer": mfg,
        "pack": pkg,
        "batch": batch,
        "expiry": exp,
        "mrp": mrp_f,
        "qty": qty_f,
        "free_qty": _f(free),
        "rate": rate_f,
        "amount": amt_f,
        "discount_pct": _f(disc) if _f(disc) <= 100 else 0,
        "gst_pct": normalize_total_gst_pct(_f(gst)),
        "source_row": row_no,
        "gst_from_bill": True,
    }


def _parse_generic(text: str, source_path: str) -> PurchaseInvoice:
    records = _records_from_text(text)
    return _invoice_from_records(records, source_path, "image_generic")


def _parse_fuzzy(text: str, source_path: str) -> PurchaseInvoice:
    """Loose line parser for noisy OCR — qty×rate≈amount rows with HSN."""
    records: List[Dict[str, Any]] = []
    for row_no, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line or _is_header_noise(line):
            continue
        rec = _parse_fuzzy_line(line, row_no)
        if rec:
            records.append(rec)
    return _invoice_from_records(records, source_path, "image_fuzzy")


def _parse_fuzzy_line(line: str, row_no: int) -> Optional[Dict[str, Any]]:
    u = line.upper()
    if any(tok in u for tok in (
        "SUB TOTAL", "GRAND TOTAL", "TAXABLE", "BOOKED BY", "IFSC", "OUTSTANDING",
        "INWORDS", "JURISDICTION", "ORDER NO", "ORDER DATE", "CREDIT NOTE",
    )):
        return None
    if re.search(r"@\d+\s*%", u) or re.search(r"\bICICI\b", u):
        return None
    nums = [(_f(m.group(0)), m.start()) for m in re.finditer(r"[\d,]+\.\d{2}", line)]
    if len(nums) < 2:
        nums = [(_f(m.group(0)), m.start()) for m in re.finditer(r"[\d,]+(?:\.\d+)?", line)]
    if len(nums) < 2:
        return None
    amount = nums[-1][0]
    rate = nums[-2][0]
    if amount <= 0 or rate <= 0:
        return None
    qty = 0.0
    if len(nums) >= 3:
        maybe_qty = nums[-3][0]
        if maybe_qty > 0 and abs(maybe_qty * rate - amount) <= max(0.25, amount * 0.03):
            qty = maybe_qty
    if qty <= 0 and rate > 0:
        qty = round(amount / rate, 4)
    if qty <= 0:
        return None

    hsn_m = re.search(rf"\b({_HSN})\b", line)
    if not hsn_m:
        return None
    hsn = hsn_m.group(1)

    gst_pct = 5.0
    decs = [_f(x) for x in re.findall(r"[\d,]+\.\d{2}", line)]
    if len(decs) >= 4 and abs(decs[-2] - rate) < 0.01 and abs(decs[-1] - amount) < 0.01:
        gst_pct = normalize_total_gst_pct(0, decs[-4], decs[-3])
    else:
        gst_m = re.search(r"\b(\d{1,2}(?:\.\d+)?)\s*%", line)
        if gst_m:
            gst_pct = normalize_total_gst_pct(_f(gst_m.group(1)))

    exp_m = re.search(r"\b(\d{1,2}[/\-]\d{2,4})\b", line)
    expiry = exp_m.group(1) if exp_m else ""

    left = line[: nums[-3][1] if len(nums) >= 3 else nums[-2][1]].strip()
    left = re.sub(r"^[\d,]+\.\d{2}\s+", "", left)
    left = re.sub(rf"\b{re.escape(hsn)}\b", " ", left)
    left = re.sub(r"\b\d{1,2}(?:\.\d+)?\s*%?\b", " ", left, count=1)
    name = re.sub(r"\s+", " ", left).strip(" -|,")
    if len(name) < 3:
        return None
    if name.upper() in ("GST", "HSN", "TOTAL", "SUB TOTAL"):
        return None

    tokens = [t for t in line.split() if t]
    batch = ""
    for tok in tokens:
        if re.match(r"^[A-Z0-9][A-Z0-9\-]{3,}$", tok, flags=re.I) and not re.match(r"^\d", tok):
            if tok.upper() not in ("SMART", "LEE", "ALK", "PCS", "STRIP", "PACK"):
                batch = tok
                break

    return {
        "name": name[:120],
        "hsn_code": hsn,
        "qty": qty,
        "rate": rate,
        "amount": amount if abs(qty * rate - amount) <= max(0.3, amount * 0.03) else round(qty * rate, 2),
        "gst_pct": gst_pct,
        "batch": batch,
        "expiry": expiry,
        "source_row": row_no,
        "gst_from_bill": True,
    }


def _invoice_from_records(
    records: List[Dict[str, Any]],
    source_path: str,
    parser: str,
) -> PurchaseInvoice:
    items = [_item_from_record(rec) for rec in records if rec.get("name")]
    return PurchaseInvoice(
        items=items,
        source_path=source_path,
        source_type="image",
        parser=parser,
    )


def _is_header_noise(line: str) -> bool:
    u = line.upper().strip()
    if re.match(r"^[\d,]+\.\d{2}\s+\S", line):
        return False
    if re.search(r"\b\d{4}\b", line) and re.search(r"[\d,]+\.\d{2}", line):
        return False
    header_only = (
        "NAME OF PRODUCT", "PRODUCT NAME", "MFG BATCH", "EXPDT",
        "GST INVOICE", "TAX INVOICE", "CREDIT MEMO", "SUB TOTAL",
        "GRAND TOTAL", "NET PAYABLE", "AMOUNT IN WORDS",
    )
    if any(tok in u for tok in header_only):
        return True
    if u in ("HSN", "QTY", "RATE", "AMOUNT", "BATCH", "EXP", "MRP", "GST"):
        return True
    return False


def _f(value: Any) -> float:
    try:
        return float(str(value or "0").replace(",", ""))
    except (TypeError, ValueError):
        return 0.0


def _is_plausible_product_name(name: str) -> bool:
    n = (name or "").strip()
    if len(n) < 3:
        return False
    u = n.upper()
    bad = (
        "INVOICE", "GSTIN", "PHONE", "MOBILE", "OUTSTANDING", "BANK",
        "TILAK MAIDAN", "NEAR BUS", "MEDICAL STORES", "MEDICAL STORE",
        "COMPLEX", "KHAMGAON", "DISTRIBUTOR", "SUB TOTAL", "GRAND TOTAL",
        "CGST", "SGST", "FSSAI", "E-MAIL", "GMAIL", "CREDIT", "DUE DATE",
        "A/C NO", "TAXABLE", "TOTAL TARABLE", "PRODUCT PACK", "BANK A/C",
    )
    if any(b in u for b in bad):
        return False
    if re.search(r"\+\d{10}", n):
        return False
    if re.match(r"^[\d,.\s]+$", n):
        return False
    alpha = sum(1 for c in n if c.isalpha())
    digits = sum(1 for c in n if c.isdigit())
    if alpha < 3:
        return False
    if digits > alpha * 2:
        return False
    return True
