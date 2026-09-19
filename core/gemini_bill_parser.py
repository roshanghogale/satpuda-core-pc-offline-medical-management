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
    "discount_pct": 0, "amount": 0
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

General:
- pack is NEVER 2.50, 9.00, 12.00 (those are GST half-rates)
- hsn_code = exact digits from the HSN column (do not pad with zeros or shorten)
- batch from Batch column only; mfg from MFG/Mfr/Com column only
- discount_pct = DIS / DIS% column ONLY. If the bill shows 0 or 0%, set discount_pct to 0.
- Do NOT compute discount from MRP minus Rate — MRP is retail price, Rate is purchase rate.
- amount = qty × rate when DIS is zero (ignore free qty in amount)
- medicine_type, schedule, and content_drug are NOT printed on these bills — always leave them as empty strings ""
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
    raw_json = _call_gemini(clean, api_key, on_progress)
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
    line_gross = round(sum(float(it.amount or 0) for it in items), 2)
    inv.line_gross = line_gross
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
