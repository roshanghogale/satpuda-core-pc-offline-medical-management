"""Opening stock, typed or pasted, with no supplier and no purchase bill.

A shop starting on Satpuda has shelves full of medicine that no purchase bill in
the system ever brought in. Until now the only ways to get that stock in were a
purchase (which needs a supplier and invents a bill the shop never received) or
the phone loader. The owner asked for the third way: the same rows the
data-loading app produces, typed or pasted straight into Settings.

It writes through the product's own medicine import (core.mobile_import_apply),
so Offline writes SQLite and Online writes the server -- one behaviour, one set
of rules, no second implementation to drift.

Nothing here creates a supplier, a purchase or a payment.
"""
from __future__ import annotations

import csv
import io
import json
from typing import Any

# What a row may be called. The first name is what the loader sends; the rest
# are what a person types into a spreadsheet column.
ALIASES: dict[str, tuple[str, ...]] = {
    "name": ("name", "medicine", "medicine_name", "item", "product"),
    "batch_no": ("batch_no", "batch", "batch no", "batchno", "lot"),
    "expiry_date": ("expiry_date", "expiry", "exp", "expiry date", "exp_date"),
    "type": ("type", "medicine_type", "form"),
    "unit": ("unit", "pack", "packing", "pack_size"),
    "stock_qty": ("stock_qty", "stock", "qty", "quantity", "strips", "opening_stock"),
    "extra_medicine": ("extra_medicine", "extra", "loose", "loose_qty", "extra_qty"),
    "mrp": ("mrp", "m.r.p", "mrp_rate"),
    "rate": ("rate", "purchase_rate", "cost", "ptr"),
    "gst_percent": ("gst_percent", "gst", "gst_pct", "gst%", "tax"),
    "hsn_code": ("hsn_code", "hsn"),
    "manufacturer": ("manufacturer", "company", "mfg", "maker"),
    "schedule": ("schedule", "sch"),
    "content_drug": ("content_drug", "content", "salt", "composition"),
    # Where the shop got the stock. A note on the medicine and nothing more:
    # opening stock creates no supplier, no bill and no due, so this never
    # reaches the supplier ledger.
    "supplier_name": ("supplier_name", "supplier", "distributor", "party", "from"),
}
NUMERIC = ("stock_qty", "extra_medicine", "mrp", "rate", "gst_percent")
COLUMNS = tuple(ALIASES)


def template() -> dict[str, Any]:
    """What the screen shows as 'this is the shape of a row'."""
    return {
        "ok": True,
        "columns": list(COLUMNS),
        "required": ["name"],
        "sample_csv": (
            "name,batch_no,expiry_date,type,unit,stock_qty,extra_medicine,mrp,rate,gst_percent\n"
            "AMOXYCILLIN 500MG,B1204,08/27,Tablet,1x10,12,4,85.50,68.40,12\n"
            "CALCIUM LIQUID 500ML,C77,03/28,Liquid,500ML,6,0,240,190,12\n"
        ),
        "notes": [
            "stock_qty is counted the way the shop counts it: for Tablet, Bolus and "
            "Capsule that is STRIPS, and extra_medicine is the loose pieces left over. "
            "For everything else it is simply the number of units.",
            "A medicine already on the shelf with the same name and batch is updated, "
            "not added twice.",
            "No supplier, no purchase bill and no payment is created.",
        ],
    }


def _canonical(header: str) -> str:
    h = str(header or "").strip().lower().replace("-", "_")
    h_compact = h.replace(" ", "_")
    for field, names in ALIASES.items():
        for n in names:
            if h == n or h_compact == n.replace(" ", "_"):
                return field
    return ""


def _number(value: Any) -> float:
    text = str(value if value is not None else "").strip().replace(",", "")
    if not text:
        return 0.0
    try:
        return float(text)
    except ValueError:
        return 0.0


def _row_from(raw: dict[str, Any]) -> dict[str, Any]:
    row: dict[str, Any] = {}
    for key, value in (raw or {}).items():
        field = _canonical(key)
        if field:
            row[field] = value
    out: dict[str, Any] = {}
    for field in COLUMNS:
        value = row.get(field, "")
        out[field] = _number(value) if field in NUMERIC else str(value or "").strip()
    return out


def parse(body: dict[str, Any]) -> dict[str, Any]:
    """rows / csv / json -> the loader's own medicines shape, plus what is wrong with it."""
    rows: list[dict[str, Any]] = []
    given = body.get("rows")
    text = str(body.get("csv") or body.get("text") or body.get("raw") or "").strip()
    raw_json = str(body.get("json") or "").strip()
    if isinstance(given, list) and given:
        rows = [_row_from(r) for r in given if isinstance(r, dict)]
    elif raw_json or (text.startswith("{") or text.startswith("[")):
        blob = raw_json or text
        try:
            parsed = json.loads(blob)
        except json.JSONDecodeError as exc:
            return {"ok": False, "error": f"That is not valid JSON: {exc}"}
        if isinstance(parsed, dict):
            parsed = parsed.get("medicines") or parsed.get("rows") or []
        if not isinstance(parsed, list):
            return {"ok": False, "error": "The file holds no medicines list."}
        rows = [_row_from(r) for r in parsed if isinstance(r, dict)]
    elif text:
        sample = text.splitlines()[0]
        delimiter = "\t" if "\t" in sample else ("," if "," in sample else ";")
        reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
        if not reader.fieldnames or not any(_canonical(h) for h in reader.fieldnames):
            return {
                "ok": False,
                "error": "The first line must name the columns, for example: "
                         "name,batch_no,expiry_date,type,unit,stock_qty",
            }
        rows = [_row_from(r) for r in reader]
    else:
        return {"ok": False, "error": "Nothing to read: type the rows, or paste them."}

    clean: list[dict[str, Any]] = []
    problems: list[str] = []
    seen: dict[tuple, int] = {}
    for i, row in enumerate(rows, start=1):
        blank_text = not any(str(row[f]).strip() for f in COLUMNS if f not in NUMERIC)
        blank_numbers = not any(float(row[f] or 0) for f in NUMERIC)
        if blank_text and blank_numbers:
            continue                       # a blank line in a pasted sheet
        if not row["name"]:
            problems.append(f"Row {i}: no medicine name — this row will not be imported.")
            continue
        if row["stock_qty"] < 0 or row["extra_medicine"] < 0:
            problems.append(f"Row {i} ({row['name']}): a shelf cannot hold less than nothing.")
            continue
        key = (row["name"].lower(), row["batch_no"].strip())
        if key in seen:
            problems.append(
                f"Row {i} ({row['name']}): the same name and batch is also on row "
                f"{seen[key]} — the later row wins."
            )
        seen[key] = i
        if not row["batch_no"]:
            row["batch_no"] = "-"
        clean.append(row)

    return {
        "ok": True,
        "rows": clean,
        "count": len(clean),
        "problems": problems[:50],
        "total_stock": round(sum(r["stock_qty"] for r in clean), 2),
        "columns": list(COLUMNS),
    }


def preview(body: dict[str, Any]) -> dict[str, Any]:
    out = parse(body)
    if not out.get("ok"):
        return out
    out["message"] = (
        f"{out['count']} medicine row(s) read"
        + (f", {len(out['problems'])} to look at" if out["problems"] else "")
        + ". Nothing has been saved yet."
    )
    return out


def apply(conn, body: dict[str, Any]) -> dict[str, Any]:
    from core.mobile_import_apply import apply_mobile_data

    out = parse(body)
    if not out.get("ok"):
        return out
    if not out["rows"]:
        return {"ok": False, "error": "No row had a medicine name."}
    result = apply_mobile_data(conn, {"export_type": "medicines", "medicines": out["rows"]})
    if isinstance(result, dict):
        result.setdefault("problems", out["problems"])
        result["read"] = out["count"]
    return result
