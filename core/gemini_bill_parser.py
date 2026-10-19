"""
Parse purchase bill photos with Google Gemini vision.

Returns PurchaseInvoice with fields matching the Purchase page table:
Medicine, Type, Batch, Expiry, Qty, Pack, HSN, Free, Rate, Disc%, GST%, Amount
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Callable, Dict, List, Optional, Sequence

from core.gemini_bill_config import load_gemini_api_key
from core.purchase_importer import (
    ImportedPurchaseItem,
    PurchaseInvoice,
    _item_from_record,
    _looks_like_non_item_name,
    normalize_expiry,
    normalize_invoice_date,
    normalize_gemini_item_rows,
    sort_import_items_by_bill_order,
)

ProgressCallback = Optional[Callable[[str], None]]

_BILL_PROMPT = """You are an expert at reading Indian pharmacy/veterinary purchase bills.

You may receive MULTIPLE images of the SAME invoice (page 1, page 2, …).
- Treat them as one bill: same invoice number on every page.
- Page 1 often ends with "Continued…" or "Continued....2" — extract ALL product rows from EVERY page.
- Ignore page-break lines (Continued, C/F, B/F, page numbers) — they are NOT products.
- Sub Total / Grand Total / tax summary usually appear only on the LAST page — use those totals.
- Do NOT duplicate rows that appear on both pages.
- items array MUST follow exact top-to-bottom bill order (S.No 1, then 2, then 3…).
- For multi-page bills: page-1 rows first, then page-2 rows, in print order on each page.

Study the bill image(s). Extract EVERY product row from all pages combined. Map each column to the correct JSON field.

Return ONLY valid JSON (no markdown):
{
  "columns": ["the item table's column headings, exactly as printed, left to right"],
  "column_values": {"MRP": [], "Batch": [], "Qty": [], "Rate": []},
  "supplier": {
    "name": "", "address": "", "phone": "", "gstin": "", "dl_numbers": "",
    "invoice_number": "", "invoice_date": "YYYY-MM-DD"
  },
  "totals": {
    "gross_amount": 0, "total_cgst": 0, "total_sgst": 0, "net_payable": 0,
    "product_discount": 0, "cash_discount": 0, "round_off": 0,
    "item_count": 0,
    "gst_slabs": [{"gst_pct": 0, "taxable": 0, "cgst": 0, "sgst": 0}]
  },
  "items": [{
    "line_no": 1,
    "page": 1,
    "row_text": "this row's cells as printed, left to right in the order of columns, one space apart; an empty cell is -",
    "name": "product name only",
    "medicine_type": "",
    "schedule": "",
    "content_drug": "",
    "hsn_code": "exact HSN digits from bill e.g. 3004 or 30049099",
    "batch": "batch no",
    "expiry": "MM/YY",
    "pack": "10 or 60GM or 55ML — NOT gst rates",
    "manufacturer": "MFG code e.g. SMART, ALK",
    "qty": 0, "free_qty": 0, "rate": 0, "mrp": 0,
    "sgst_pct": 0, "cgst_pct": 0, "gst_pct": 0,
    "discount_pct": 0, "amount": 0,
    "rate_a": 0, "rate_b": 0, "rate_c": 0
  }]
}

CRITICAL column rules by supplier format:

VINAR MEDICAL — columns left to right:
  MRP | Description | HSN | Pack | MFG | Batch | Exp | Qty | Free | SGST% | CGST% | Rate | Dis | Amount
  - pack = Pack column ONLY (1X10, 1X60GM, 1X55ML) — save as 10, 60GM, 55ML
  - sgst_pct = SGST column (2.50 or 9.00) — NOT pack
  - cgst_pct = CGST column (2.50 or 9.00) — NOT pack
  - gst_pct = sgst_pct + cgst_pct (5 or 18)
  - rate = Rate column; amount = Amt column

RAJ MEDICO — HSN | GST% | MRP | Product | Mfr | Pack | Batch | Exp | Qty | Free | Rate | Amount

OM SAI — HSN | GST% | Name | Pack | MFG Batch | MRP | Exp | Qty | Free | Rate | Amount
  - HSN may show "902 30049099" — use 30049099 only (drop the DM prefix, keep full HSN)

JAI GANESH PHARMAVET / JCR bills — columns include GST % per line:
  - gst_pct = GST% column exactly (0 for exempt items, 5 for taxable) — NOT 2.5+2.5
  - When GST% is 0: set gst_pct=0, sgst_pct=0, cgst_pct=0 (do NOT read 2.5 from other columns)
  - amount = qty × rate (pre-tax goods value, GST is in footer slab table)
  - Footer slab: GST 0% goods + GST 5% goods; cash discount split per slab

OM SAI MEDICO — HSN | GST% | Name | … | Rate | Amount
  - gst_pct = GST% column (5.00 or 0.00). CAREX CONDOM and similar may be 0%.
  - amount = qty × rate (pre-tax)

SHREE DISTRIBUTOR — all lines usually GST 5%; amount = qty × rate pre-tax.

SWAMI SAMARTH MEDICAL AND AGENCY — tax-inclusive slab billing:
  - sgst_pct + cgst_pct per line (2.5+2.5=5%, 9+9=18%, 0+0=0%)
  - gst_pct = sgst_pct + cgst_pct
  - amount = qty × rate; cash discount applied per GST slab in footer

JAI GANESH / SHREE DISTRIBUTOR — read each column header; do not shift values left/right.

PARAKH MEDICAL — MRP | HSN | Description | Pack | MFG | Batch | Exp | Qty | Free | Rate | DIS% | GST% | Amount
  - discount_pct = DIS% column only (usually 0 or 0% on these bills)
  - amount = Qty × Rate (free qty is bonus, not added to bill qty)

SHRIGURUDEO / SHRIGURUDEO PHARMACEUTICALS — carry-forward multi-page tax invoice:
  M.R.P. | Qty. | Free | Product | Mfr | Pack | HSN | Exp. | Batch | Disc. | RATE | GST% | Amount
  - Multi-page bills show Total B/F and Total C/F — ignore those rows (NOT products).
  - gst_pct = GST% column (12.00 means 12% total; blank or "-" means 0% exempt).
  - amount = Qty × Rate ONLY (GST is NOT in the Amount column; footer adds CGST+SGST).
  - Read EVERY product row from ALL pages; item_count in totals = "No of items" on last page.
  - Footer Gross + Add GST = Net (e.g. Gross 12264.06 + GST 969.02 = Net 13233.00).

Every bill (whatever the supplier) — read the table one printed row at a time:
- First read the column headings into "columns". Then, for each row, copy the row
  into "row_text" exactly as printed, cell by cell in the same column order.
- Fill the item fields from THAT row only. Never take MRP, rate, batch or expiry
  from the row above or below — handwritten ticks over the Qty column do not move
  a row's other values.
- name = the product-name column ONLY. The MFG / Mfr / Company code has its own
  column: never put it at the start or end of name.
- manufacturer = the MFG / Mfr / Co. cell of the same row (often 2-6 letters like
  A.B., ALK, INT); batch = the Batch cell (letters+digits, e.g. K26K010300); never
  swap the two.
- pack = the Pack / Pkg cell as printed ("1 S", "10'S", "100ML", "1"); keep the
  space in "1 S".
- Check every row: qty × rate (less DIS%) must equal amount. If it does not, read
  the row again before answering.
- column_values: read the MRP, Batch, Qty and Rate columns AGAIN, each on its own,
  straight down the column from the first product row to the last (all pages),
  one value per product row. Do not copy them from items — read the column.

General:
- pack is NEVER 2.50, 9.00, 12.00 (those are GST half-rates)
- hsn_code = exact digits from the HSN column (do not pad with zeros or shorten)
- batch from Batch column only; mfg from MFG/Mfr/Com column only
- discount_pct = DIS / DIS% column ONLY. If the bill shows 0 or 0%, set discount_pct to 0.
- Do NOT compute discount from MRP minus Rate — MRP is retail price, Rate is purchase rate.
- amount = qty × rate when DIS is zero (ignore free qty in amount)
- medicine_type, schedule, and content_drug are NOT printed on these bills — always leave them as empty strings ""
- rate_a / rate_b / rate_c = ONLY when the bill has columns literally headed "Rate A", "Rate B", "Rate C"
  (a wholesaler's own sale rates). Almost no supplier bill has them — then leave all three 0.
  NEVER copy MRP, PTR, PTS, Rate, Amount or any other column into rate_a / rate_b / rate_c.
"""


def _report(cb: ProgressCallback, msg: str) -> None:
    if cb:
        cb(msg)


def parse_bill_images_with_gemini(
    paths: Sequence[str],
    *,
    on_progress: ProgressCallback = None,
) -> PurchaseInvoice:
    """Send bill photo(s) to Gemini; return PurchaseInvoice."""
    clean = [p for p in paths if p and os.path.isfile(p)]
    if not clean:
        raise ValueError("No image files to parse.")

    api_key = load_gemini_api_key()
    if not api_key:
        raise RuntimeError("Bill reading is not set up on this PC (the AI key is missing).")

    if len(clean) > 1:
        _report(on_progress, "Reading {} bill pages…".format(len(clean)))
    else:
        _report(on_progress, "Reading the bill…")
    from core.bill_photo_orientation import remove_quietly, upright_copies

    # A bill lying on its side in the photo is read across instead of down (one
    # row's MRP landed on the next row on every try); turned upright first, the
    # same photo read right (core/bill_photo_orientation).
    send, temps = upright_copies(clean)
    try:
        raw_json = _call_gemini(send, api_key, on_progress)
    finally:
        remove_quietly(temps)
    _report(on_progress, "Matching medicines…")
    return _json_to_invoice(raw_json, clean[0])


def _call_gemini(
    paths: Sequence[str],
    api_key: str,
    on_progress: ProgressCallback,
) -> Dict[str, Any]:
    for path in paths:
        if not os.path.isfile(path):
            raise RuntimeError("Could not open image: {}".format(path))

    from core.gemini_bill_config import _sdk_supports_bill_import

    if _sdk_supports_bill_import():
        text = _call_gemini_sdk(paths, api_key, on_progress)
    else:
        text = _call_gemini_rest(paths, api_key, on_progress)
    return _parse_json_response(text)


def _call_gemini_rest(
    paths: Sequence[str],
    api_key: str,
    on_progress: ProgressCallback,
) -> str:
    from core.gemini_rest_client import (
        DEFAULT_VISION_MODELS,
        generate_with_model_fallback,
    )

    parts: List[Any] = [_BILL_PROMPT]
    parts.extend(paths)
    return generate_with_model_fallback(
        api_key=api_key,
        parts=parts,
        models=DEFAULT_VISION_MODELS,
        on_progress=on_progress,
    )


def _call_gemini_sdk(
    paths: Sequence[str],
    api_key: str,
    on_progress: ProgressCallback,
) -> str:
    from core.ssl_utils import configure_ssl_certificates

    configure_ssl_certificates()

    import google.generativeai as genai

    genai.configure(api_key=api_key)

    images = []
    for path in paths:
        try:
            from PIL import Image
            images.append(Image.open(path))
        except Exception:
            return _call_gemini_rest(paths, api_key, on_progress)

    models = _resolve_gemini_models(genai)
    if not models:
        models = (
            "gemini-flash-lite-latest",
            "gemini-flash-latest",
        )
    last_err = None
    quota_hits = 0
    for model_name in models:
        try:
            _report(on_progress, "Reading the bill…")
            model = genai.GenerativeModel(
                model_name,
                generation_config=genai.GenerationConfig(
                    temperature=0.1,
                    response_mime_type="application/json",
                ),
            )
            parts: List[Any] = [_BILL_PROMPT]
            parts.extend(images)
            response = model.generate_content(parts)
            text = (response.text or "").strip()
            if text:
                return text
        except Exception as exc:
            last_err = exc
            if _is_quota_or_limit_error(exc):
                quota_hits += 1
                _report(
                    on_progress,
                    "Busy right now — trying again…",
                )
                continue
            continue

    if quota_hits and quota_hits >= len(models):
        raise RuntimeError(
            "The bill-reading service has reached its limit for now.\n"
            "Wait 1–2 minutes and retry, or tomorrow for daily quota reset.\n"
            "For unlimited use, ask your software provider for a paid key.\n\n"
            "Last error: {}".format(last_err)
        )
    raise RuntimeError(
        "The bill could not be read.\n"
        "Check the internet connection, then retry with a clearer photo.\n\n"
        "{}".format(last_err)
    )


def _is_quota_or_limit_error(exc: Exception) -> bool:
    err = str(exc).lower()
    return any(tok in err for tok in (
        "quota", "resource_exhausted", "rate limit", "rate_limit",
        "limit reached", "limit:", "429", "too many requests",
    ))


def _resolve_gemini_models(genai) -> List[str]:
    """Pick vision-capable Gemini models — flash-latest aliases only."""
    from core.gemini_rest_client import is_blocked_gemini_model

    preferred = (
        "gemini-flash-lite-latest",
        "gemini-flash-latest",
    )
    available: List[str] = []
    try:
        for m in genai.list_models():
            methods = getattr(m, "supported_generation_methods", None) or []
            if "generateContent" not in methods:
                continue
            name = m.name or ""
            short = name.split("/", 1)[-1] if "/" in name else name
            low = short.lower()
            if "gemini" not in low:
                continue
            if any(skip in low for skip in ("tts", "embedding", "aqa", "preview-tts")):
                continue
            available.append(short)
    except Exception:
        return []

    ordered: List[str] = []
    for pref in preferred:
        for short in available:
            if pref in short and short not in ordered:
                ordered.append(short)
    for short in available:
        if is_blocked_gemini_model(short):
            continue
        if short not in ordered:
            ordered.append(short)
    return ordered[:2]


def _parse_json_response(text: str) -> Dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        m = re.search(r"\{[\s\S]*\}", text)
        if not m:
            raise RuntimeError("The bill could not be read (unexpected reply). Try again.") from exc
        data = json.loads(m.group(0))
    if not isinstance(data, dict):
        raise RuntimeError("The bill could not be read (unexpected reply). Try again.")
    return data


def _int_field(row: Dict[str, Any], *keys: str, default: int = 0) -> int:
    for key in keys:
        try:
            val = row.get(key)
            if val is not None and str(val).strip():
                return int(float(str(val).replace(",", "").strip()))
        except (TypeError, ValueError):
            continue
    return default


def _json_to_invoice(data: Dict[str, Any], source_path: str) -> PurchaseInvoice:
    supplier = data.get("supplier") or {}
    totals = data.get("totals") or {}
    raw_items = normalize_gemini_item_rows(data.get("items"))
    supplier_name = str(supplier.get("name") or "")
    columns = data.get("columns") if isinstance(data.get("columns"), list) else []

    items: List[ImportedPurchaseItem] = []
    for idx, row in enumerate(raw_items, 1):
        if not isinstance(row, dict):
            continue
        line_no = _int_field(row, "line_no", "sno", "serial", "sr_no", "sr", default=idx)
        page_no = _int_field(row, "page", "bill_page", "image_page", default=1)
        rec = {
            "name": str(row.get("name") or "").strip(),
            "medicine_type": str(row.get("medicine_type") or "").strip(),
            "hsn_code": str(row.get("hsn_code") or "").strip(),
            "batch": str(row.get("batch") or "").strip(),
            "expiry": normalize_expiry(row.get("expiry")),
            "pack": str(row.get("pack") or row.get("Pack") or "").strip(),
            "manufacturer": str(
                row.get("manufacturer") or row.get("mfg") or row.get("MFG")
                or row.get("mfr") or row.get("com") or ""
            ).strip(),
            "qty": _num(row.get("qty")),
            "free_qty": _num(row.get("free_qty") or row.get("free")),
            "rate": _num(row.get("rate")),
            "mrp": _num(row.get("mrp")),
            "gst_pct": _num(row.get("gst_pct") or row.get("gst")),
            "sgst_pct": _num(row.get("sgst_pct") or row.get("sgst") or row.get("SGST")),
            "cgst_pct": _num(row.get("cgst_pct") or row.get("cgst") or row.get("CGST")),
            "discount_pct": _num(row.get("discount_pct")),
            "amount": _num(row.get("amount") or row.get("amt")),
            # Only a bill that prints Rate A / B / C columns fills these (else 0).
            "rate_a": _num(row.get("rate_a")),
            "rate_b": _num(row.get("rate_b")),
            "rate_c": _num(row.get("rate_c")),
            "source_row": line_no,
            "line_no": line_no,
            "page": page_no,
            "gst_from_bill": True,
            "_supplier": supplier_name,
        }
        if not rec["name"]:
            continue
        if _looks_like_non_item_name(rec["name"]):
            continue
        # The reader's own copy of the printed row, read under the printed
        # headings: where the two disagree the bill's arithmetic decides, and
        # what it cannot decide is marked for the shop (core/bill_columns).
        row_text = str(row.get("row_text") or "").strip()
        from core.bill_columns import (
            column_values_for_row,
            cross_check_row,
            reconcile_readings,
        )

        try:
            if row_text and columns:
                cross_check_row(rec, row_text, columns)
            # The same columns read straight down on their own.
            by_column = column_values_for_row(
                data.get("column_values"), idx - 1, len(raw_items),
            )
            if by_column:
                reconcile_readings(rec, by_column, "column")
        except Exception:
            pass
        from core.bill_import_normalize import normalize_gemini_item_record

        normalize_gemini_item_record(rec, supplier_name)
        if rec["amount"] <= 0 and rec["qty"] > 0 and rec["rate"] > 0:
            rec["amount"] = round(rec["qty"] * rec["rate"], 2)
        item = _item_from_record(rec)
        if item.name:
            items.append(item)

    items = sort_import_items_by_bill_order(items)

    inv = PurchaseInvoice(
        supplier_name=str(supplier.get("name") or "").strip(),
        supplier_address=str(supplier.get("address") or "").strip(),
        supplier_phone=str(supplier.get("phone") or "").strip(),
        supplier_gstin=str(supplier.get("gstin") or "").strip(),
        supplier_dl=str(supplier.get("dl_numbers") or supplier.get("dl") or "").strip(),
        invoice_number=str(supplier.get("invoice_number") or "").strip(),
        invoice_date=normalize_invoice_date(supplier.get("invoice_date")),
        items=items,
        source_path=source_path,
        source_type="image",
        parser="gemini",
        gross_amount=_num(totals.get("gross_amount")),
        total_cgst=_num(totals.get("total_cgst")),
        total_sgst=_num(totals.get("total_sgst")),
        invoice_total=_num(totals.get("net_payable")),
        product_discount=_num(totals.get("product_discount")),
        cash_discount=_num(totals.get("cash_discount")),
        round_off=_num(totals.get("round_off")),
        footer_gst_authoritative=bool(
            _num(totals.get("total_cgst")) and _num(totals.get("total_sgst"))
        ),
        raw_text=json.dumps(data, ensure_ascii=False, indent=2),
    )
    inv.expected_item_count = _int_field(totals, "item_count", "no_of_items", "item_count")
    raw_slabs = totals.get("gst_slabs")
    if isinstance(raw_slabs, list):
        inv.footer_gst_slabs = [
            {
                "gst_pct": _num(s.get("gst_pct")),
                "taxable": _num(s.get("taxable") or s.get("gross")),
                "gross": _num(s.get("gross") or s.get("taxable")),
                "cgst": _num(s.get("cgst")),
                "sgst": _num(s.get("sgst")),
            }
            for s in raw_slabs
            if isinstance(s, dict) and (_num(s.get("taxable") or s.get("gross")) > 0)
        ]
    _prefer_slab_gst(inv)
    line_gross = round(sum(float(it.amount or 0) for it in items), 2)
    inv.line_gross = line_gross
    _drop_foreign_item_count(inv, line_gross)
    footer_gross = _num(totals.get("gross_amount"))
    if footer_gross > line_gross * 1.003:
        inv.gross_amount = footer_gross
    elif not inv.gross_amount and line_gross:
        inv.gross_amount = line_gross
    if not inv.invoice_total and inv.gross_amount:
        gst = inv.total_cgst + inv.total_sgst
        inv.invoice_total = round(inv.gross_amount + gst, 2)

    from core.purchase_invoice_engine import detect_purchase_gst_calc_method

    method = detect_purchase_gst_calc_method(inv)
    inv.document_format = (
        "tax_exclusive" if method == "discount_before_gst" else "tax_inclusive"
    )

    if inv.expected_item_count and len(items) < inv.expected_item_count:
        inv.issues.append(
            "Bill shows {} items but only {} rows were read — rescan missing page(s).".format(
                inv.expected_item_count, len(items),
            )
        )
        inv.review_flags.append("Item count mismatch")

    if not items:
        inv.issues.append("No medicine lines were found on this bill. Try a clearer photo.")
    return inv


def _prefer_slab_gst(inv: PurchaseInvoice) -> None:
    """Footer CGST / SGST that do not add up, when the GST slab table does.

    A photo of two overlapping bills (a shop, 18 Oct 2026) gave the front bill's slab table but
    the CGST / SGST totals of the bill behind it, nearly three times as much: the screen showed
    that GST on a bill whose gross + GST is its net payable only with the slab figures. The
    slab sums win when they make gross + GST = net payable and the footer figures do not;
    otherwise nothing changes.
    """
    slabs = list(getattr(inv, "footer_gst_slabs", None) or [])
    gross = float(inv.gross_amount or 0)
    net = float(inv.invoice_total or 0)
    if not slabs or gross <= 0 or net <= 0:
        return
    s_cgst = round(sum(float(s.get("cgst") or 0) for s in slabs), 2)
    s_sgst = round(sum(float(s.get("sgst") or 0) for s in slabs), 2)
    if s_cgst <= 0 and s_sgst <= 0:
        return
    f_cgst, f_sgst = float(inv.total_cgst or 0), float(inv.total_sgst or 0)
    if abs(f_cgst - s_cgst) < 0.05 and abs(f_sgst - s_sgst) < 0.05:
        return
    disc = float(inv.product_discount or 0) + float(inv.cash_discount or 0)
    off = float(inv.round_off or 0)

    def miss(c: float, s: float) -> float:
        return min(abs(gross + c + s + off - net), abs(gross - disc + c + s + off - net))

    if miss(s_cgst, s_sgst) <= 1.0 and miss(f_cgst, f_sgst) > 1.0:
        inv.total_cgst, inv.total_sgst = s_cgst, s_sgst


def _drop_foreign_item_count(inv: PurchaseInvoice, line_gross: float) -> None:
    """An item count that cannot be this bill's: the rows read add up to the bill's own gross
    exactly, so none is missing -- the count was read off another paper in the photo (the
    overlapping bill behind it counted six times as many items). A missing page shows as a
    footer gross above the rows, which keeps the count and its warning."""
    expected = int(getattr(inv, "expected_item_count", 0) or 0)
    gross = float(inv.gross_amount or 0)
    if expected <= len(inv.items) or gross <= 0 or line_gross <= 0:
        return
    if abs(gross - line_gross) <= max(0.5, gross * 0.001):
        inv.expected_item_count = 0


def _num(value: Any) -> float:
    try:
        if value is None:
            return 0.0
        s = str(value).replace(",", "").replace("₹", "").strip()
        if not s:
            return 0.0
        return float(s)
    except (TypeError, ValueError):
        return 0.0
