"""
Parser for MARG ERP Nano / Chemist GST invoices (common in Maharashtra).

These PDFs extract cleanly as line text but pdfplumber merges table rows into one
broken cell — so we parse item lines from raw text instead.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple


def looks_like_marg_erp_invoice(raw_text: str) -> bool:
    if not raw_text or len(raw_text.strip()) < 80:
        return False
    upper = raw_text.upper()
    if "MARG ERP" in upper:
        return True
    if re.search(
        r"Sn\.\s+Mfg\.\s+Item\s+Name\s+Pack\s+Hsn\s+Batch\s+Exp",
        raw_text,
        flags=re.IGNORECASE,
    ):
        return True
    if re.search(r"MRP\s+HSN\s+.*(?:Desc|D\s*e\s*s\s*c)", raw_text, flags=re.IGNORECASE):
        return True
    if "ORIGINAL FOR BUYER GST INVOICE" in upper and re.search(
        r"Sn\.\s+Mfg\.", raw_text, flags=re.IGNORECASE
    ):
        return True
    return False


def _to_float(value: Any) -> float:
    try:
        text = str(value or "").strip().replace(",", "")
        if text.endswith("%"):
            text = text[:-1].strip()
        return float(text) if text else 0.0
    except (TypeError, ValueError):
        return 0.0


def _clean_line(line: str) -> str:
    return re.sub(r"\s+", " ", (line or "").strip())


def _split_jammed_pack_hsn_token(token: str) -> Tuple[str, str]:
    """
    Split tokens where PDF text merged pack + HSN (e.g. 50GM3808, 15ML3004).
    Do not split batch numbers (PL-25014, G25H010589).
    Returns (pack_or_name_part, hsn_or_empty).
    """
    t = (token or "").strip()
    if not t:
        return "", ""
    if re.match(r"^\d{4}$", t):
        return "", t
    if "-" in t or len(t) > 12:
        return t, ""
    m = re.match(r"^(\d+(?:\.\d+)?)(GM|ML|KG|L)(\d{4})$", t, re.IGNORECASE)
    if m:
        return "{}{}".format(m.group(1), m.group(2)), m.group(3)
    m = re.match(r"^(\d+ML)(\d{4})$", t, re.IGNORECASE)
    if m:
        return m.group(1), m.group(2)
    m = re.match(r"^([A-Z]*\d+(?:GM|ML|KG|L))(\d{4})$", t, re.IGNORECASE)
    if m and len(m.group(1)) <= 10:
        return m.group(1), m.group(2)
    return t, ""


def _expand_line_tokens(tokens: List[str]) -> List[str]:
    out: List[str] = []
    for token in tokens:
        pack_part, hsn_part = _split_jammed_pack_hsn_token(token)
        if pack_part:
            out.append(pack_part)
        if hsn_part:
            out.append(hsn_part)
        if not pack_part and not hsn_part:
            out.append(token)
    return out


def _looks_like_pack_token(token: str) -> bool:
    """MARG Pack column: 10*S, 50GM, 15ML, IV, 25*P, etc."""
    t = (token or "").strip()
    if not t or t in ("**", "*", "-"):
        return False
    if re.search(r"\*", t):
        return True
    if re.match(r"^\d+[\*'\"]?[A-Z]{1,2}$", t, re.IGNORECASE):
        return True
    if re.match(r"^\d+['\']S$", t, re.IGNORECASE):
        return True
    if re.match(r"^\d+(?:GM|ML|KG|L)$", t, re.IGNORECASE):
        return True
    if re.match(
        r"^(IV|IM|INJ|DT|TAB|CAP|AMP|VIAL|SYP|SYR|OINT|CRM|GEL)$",
        t,
        re.IGNORECASE,
    ):
        return True
    if len(t) <= 8 and re.match(r"^[A-Z0-9\*]+$", t) and not re.match(r"^\d{4}$", t):
        return True
    return False


def _parse_marg_item_identity(tokens: List[str]) -> Tuple[str, str, str, str, str, str]:
    """
    From line body (no numeric tail): mfg, name, pack, hsn, batch, expiry.
    Bill column order: Item Name | Pack | Hsn | Batch | Exp.
    """
    tokens = _expand_line_tokens(list(tokens))

    expiry = ""
    if tokens and re.match(r"^\d{1,2}/\d{2,4}$", tokens[-1]):
        expiry = tokens.pop()

    batch = ""
    if tokens and tokens[-1] in ("**", "*", "-"):
        tokens.pop()
    elif tokens and re.match(r"^[\w\-]+$", tokens[-1]):
        batch = tokens.pop()

    hsn = ""
    if tokens and re.match(r"^\d{4}$", tokens[-1]):
        hsn = tokens.pop()

    pack = ""
    if tokens and _looks_like_pack_token(tokens[-1]):
        pack = tokens.pop()

    mfg = tokens.pop(0) if tokens else ""
    name = " ".join(tokens).strip()
    return mfg, name, pack, hsn, batch, expiry


def _is_skip_line(line: str) -> bool:
    u = line.upper()
    if not line or len(line) < 4:
        return True
    skip_markers = (
        "GST 0.00", "GST 5.00", "GST 12.00", "GST 18.00", "GST 28.00",
        "CLASS", "SUB TOTAL", "GRAND TOTAL", "TOTAL ", "BANK NAME",
        "MARG ERP", "DECLARATION", "I/WE DECLARE", "RS. ", "RUPEES ONLY",
        "JURISDICTION", "RECIVER", "RECEIVER", "HAPPY NEW YEAR",
    )
    # Watermark fragments (not product lines)
    if re.match(r"^(SWAMI|SAMARTH|MEDICAL|AGENCY|AND)$", u):
        return True
    if any(m in u for m in skip_markers):
        return True
    if u.startswith("GST ") and "*" in u:
        return True
    return False


def parse_marg_sn_item_lines(raw_text: str) -> List[Dict[str, Any]]:
    """
    Format: 1. ALKE PAN-40MG IV INJ IV 3004 25770097 11/27 5 5 53.90 35.68 ...
    Header: Sn. Mfg. Item Name Pack Hsn Batch Exp. Qty Free Mrp Rate ...
    """
    records: List[Dict[str, Any]] = []
    for row_no, raw_line in enumerate(raw_text.splitlines(), start=1):
        line = _clean_line(raw_line)
        line = re.sub(r"(\d):(\d)", r"\1 \2", line)
        if _is_skip_line(line):
            continue
        m = re.match(r"^(\d+)\.\s+(.+)$", line)
        if not m:
            continue
        tokens = m.group(2).split()
        if len(tokens) < 10:
            continue

        # Fixed tail: qty, [free], mrp, rate, dis, sgst, cgst, amount (7 or 8 numbers only).
        # Prefer 7 — an 8th numeric token is often batch no. sitting before qty (e.g. ... 181 5 112.50 ...).
        tail_len = 0
        if len(tokens) >= 7 and all(re.match(r"^[\d.]+$", t) for t in tokens[-7:]):
            tail_len = 7
        if len(tokens) >= 8 and all(re.match(r"^[\d.]+$", t) for t in tokens[-8:]):
            q8 = _to_float(tokens[-8])
            q7 = _to_float(tokens[-7])
            if q8 <= 99 and q7 <= 999:
                tail_len = 8
        if not tail_len:
            continue

        tail = [_to_float(t) for t in tokens[-tail_len:]]
        tokens = tokens[:-tail_len]
        if tail_len == 8:
            qty, free_qty, mrp, rate, dis, sgst, cgst, amount = tail
        else:
            qty, mrp, rate, dis, sgst, cgst, amount = tail
            free_qty = 0.0

        if qty <= 0 or rate <= 0:
            continue

        mfg, name, pack, hsn, batch, expiry = _parse_marg_item_identity(tokens)
        if not name:
            continue

        gst_pct = round(float(sgst or 0) + float(cgst or 0), 2)
        records.append({
            "source_row": row_no,
            "name": name,
            "manufacturer": mfg,
            "pack": pack,
            "hsn_code": hsn,
            "batch": batch,
            "expiry": expiry,
            "qty": qty,
            "free_qty": free_qty,
            "mrp": mrp,
            "rate": rate,
            "disc_rupees": dis,
            "gst_pct": gst_pct,
            "amount": amount or round(qty * rate, 2),
        })
    return records


def parse_marg_parakh_item_lines(raw_text: str) -> List[Dict[str, Any]]:
    """
    Format: 3400.00 2309 MINFA GOLD <Lot> 15KG INT INMG25275 8/27 1.0 0.0 2266.67 0% 0.00 2266.67
    Header: MRP HSN Description Pack MFG Batch Exp. QTY. FREE RATE DIS% GST% AMOUNT
    """
    records: List[Dict[str, Any]] = []
    for row_no, raw_line in enumerate(raw_text.splitlines(), start=1):
        line = _clean_line(raw_line)
        if _is_skip_line(line) or line.upper().startswith("CLASS "):
            continue
        tokens = line.split()
        if len(tokens) < 12:
            continue
        if not re.match(r"^[\d.]+$", tokens[0]):
            continue
        if not re.match(r"^\d{3,4}$", tokens[1]):
            continue

        mrp = _to_float(tokens[0])
        hsn = tokens[1]
        idx = len(tokens) - 1

        def pop_num() -> float:
            nonlocal idx
            val = _to_float(tokens[idx])
            idx -= 1
            return val

        amount = pop_num()
        pop_num()  # gst amount column
        dis_token = tokens[idx]
        idx -= 1
        rate = pop_num()
        free_qty = pop_num()
        qty = pop_num()
        expiry_token = tokens[idx]
        idx -= 1
        batch = tokens[idx]
        idx -= 1
        pack = tokens[idx]
        idx -= 1
        name = " ".join(tokens[2: idx + 1]).strip()

        if qty <= 0 or rate <= 0 or not name:
            continue

        rec = {
            "source_row": row_no,
            "name": name,
            "pack": pack,
            "hsn_code": hsn,
            "batch": batch,
            "expiry": expiry_token,
            "qty": qty,
            "free_qty": free_qty,
            "mrp": mrp,
            "rate": rate,
            "gst_pct": 0.0,
            "amount": amount or round(qty * rate, 2),
        }
        if "%" in dis_token:
            rec["discount_pct"] = _to_float(dis_token.replace("%", ""))
        else:
            rec["disc_rupees"] = _to_float(dis_token)
        records.append(rec)
    return records


def extract_marg_erp_footer_discounts(raw_text: str) -> Dict[str, float]:
    """
    MARG ERP footer: DIS. 19.58, CLASS … DISC., TOTAL 978.70 19.58 …
    Display/import only — not applied twice in line calculations.
    """
    out = {
        "product_discount": 0.0,
        "cash_discount": 0.0,
        "subtotal": 0.0,
    }
    if not raw_text:
        return out

    for line in raw_text.splitlines():
        low = line.lower()
        m = re.search(r"\bDIS\.?\s*([\d,]+(?:\.\d{1,2})?)", line, flags=re.IGNORECASE)
        if m:
            val = _to_float(m.group(1))
            if val > 0:
                if "cash" in low:
                    out["cash_discount"] = max(out["cash_discount"], val)
                else:
                    out["product_discount"] = max(out["product_discount"], val)
        m = re.search(r"SUB\s*TOTAL\s*([\d,]+(?:\.\d{1,2})?)", line, flags=re.IGNORECASE)
        if m:
            out["subtotal"] = _to_float(m.group(1))
        m = re.search(
            r"^TOTAL\s+([\d,]+(?:\.\d{1,2})?)\s+([\d,]+(?:\.\d{1,2})?)",
            line.strip(),
            flags=re.IGNORECASE,
        )
        if m:
            disc = _to_float(m.group(2))
            if disc > 0:
                out["product_discount"] = disc

    return out


def _marg_supplier_has_agency(text: str) -> bool:
    if re.search(r"MEDICAL\s+AND\s+AGENCY", text, flags=re.IGNORECASE):
        return True
    return bool(
        re.search(
            r"For\s+[A-Z0-9\s&\.\-]{3,80}MEDICAL\s+AND\s+AGENCY",
            text,
            flags=re.IGNORECASE,
        )
    )


def _build_marg_supplier_name(text: str) -> str:
    """M/s line + AND AGENCY from the shop line or footer — not shop/address noise."""
    m = re.search(r"^M/s\s+([^\n]+)", text, flags=re.MULTILINE | re.IGNORECASE)
    if not m:
        return ""
    name = _clean_line(m.group(1))
    name = re.split(r"\s+SHOP\s+NO\.", name, maxsplit=1, flags=re.IGNORECASE)[0].strip()
    if _marg_supplier_has_agency(text) and not re.search(r"\bAGENCY\b", name, flags=re.IGNORECASE):
        name = f"{name} And Agency"
    return name


def _strip_marg_address_noise(line: str) -> str:
    value = _clean_line(line)
    value = re.split(r"\s+GST\s*:", value, maxsplit=1, flags=re.IGNORECASE)[0]
    value = re.split(r"\s+DL\.?\s*NO", value, maxsplit=1, flags=re.IGNORECASE)[0]
    value = re.split(r",?\s*Ph\.?\s*No\.?\s*:", value, maxsplit=1, flags=re.IGNORECASE)[0]
    value = re.split(r"\s+PROP\s+NO\.", value, maxsplit=1, flags=re.IGNORECASE)[0]
    value = re.split(r"\s+State\s*:", value, maxsplit=1, flags=re.IGNORECASE)[0]
    value = re.split(r"\s+NIWANE\s+COMPLEX", value, maxsplit=1, flags=re.IGNORECASE)[0]
    return value.strip(" ,")


def _extract_marg_supplier_address(text: str) -> str:
    """
    MARG header: CTS…, MALMATTA…, city/pin line — without GST/DL/phone/shop lines.
    """
    parts: List[str] = []
    for raw in text.splitlines()[:15]:
        line = _clean_line(raw)
        if not line:
            continue
        low = line.lower()
        if "shop no" in low or line.upper().startswith("M/S "):
            continue
        if re.match(r"^CTS\s+NO\.", line, flags=re.IGNORECASE):
            part = _strip_marg_address_noise(line)
            if part:
                parts.append(part)
            continue
        if re.match(r"^MALMATTA\s+NO\.", line, flags=re.IGNORECASE):
            part = _strip_marg_address_noise(line)
            if part:
                parts.append(part)
            continue
        if re.search(r"\b\d{6}\b", line) and not re.match(
            r"^(phone|date|due|e-?mail|sales|gst|dl|sn\.)",
            line,
            flags=re.IGNORECASE,
        ):
            part = _strip_marg_address_noise(line)
            if part and not re.search(r"\bdl\s*no", part, flags=re.IGNORECASE):
                parts.append(part)
    return ", ".join(parts)


def enhance_marg_supplier_details(raw_text: str, details: Dict[str, str]) -> Dict[str, str]:
    out = dict(details or {})
    text = raw_text or ""

    name = _build_marg_supplier_name(text)
    if name:
        out["supplier_name"] = name

    if not out.get("supplier_name"):
        for line in text.splitlines()[:12]:
            line = _clean_line(line)
            if re.match(
                r"^[A-Z][A-Z\s&\.]{4,60}(?:MEDICAL|AGENCIES|PHARMA|DISTRIBUT)",
                line,
                flags=re.IGNORECASE,
            ):
                if "GST INVOICE" not in line.upper() and "ORIGINAL FOR" not in line.upper():
                    out["supplier_name"] = line
                    break

    address = _extract_marg_supplier_address(text)
    if address:
        out["supplier_address"] = address

    m = re.search(r"Invoice\s+No\.\s*:\s*([A-Z0-9\-]+)", text, flags=re.IGNORECASE)
    if m:
        out["invoice_number"] = m.group(1).strip()

    m = re.search(
        r"Date\s*:\s*(\d{1,2}[-/]\d{1,2}[-/]\d{2,4})",
        text,
        flags=re.IGNORECASE,
    )
    if m:
        out["invoice_date"] = m.group(1).replace("-", "/")

    m = re.search(
        r"For\s+([A-Z][A-Z0-9\s&\.\-]{4,60}(?:MEDICAL|AGENCY|PHARMA))",
        text,
        flags=re.IGNORECASE,
    )
    if m and not out.get("supplier_name"):
        out["supplier_name"] = _clean_line(m.group(1))

    return out


def pick_marg_item_records(raw_text: str) -> Tuple[List[Dict[str, Any]], str]:
    sn_records = parse_marg_sn_item_lines(raw_text)
    parakh_records = parse_marg_parakh_item_lines(raw_text)
    if len(sn_records) >= len(parakh_records) and sn_records:
        return sn_records, "marg-erp-sn-lines"
    if parakh_records:
        return parakh_records, "marg-erp-mrp-hsn-lines"
    return [], ""
