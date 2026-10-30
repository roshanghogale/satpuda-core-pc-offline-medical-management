"""Doctor Margin Report from this PC's books (owner, 17 Oct 2026; Sales History -> Export).

The figures come from core/doctor_margin.py (the rule the server and the phone share); this
file only reads the store database for it and lays the result out for the screen and the
files (Excel / CSV / PDF / print). Offline and offline-first stores: the books are the local
SQLite. An Online store's books are on the server, so the report is the web's there.

Read once per table, never per bill or per line:
  sales               bills in [from, to] by bill_date, not deleted, not autosave drafts
  sales_items         their lines, named from the medicine row (name, type, batch)
  sales_return_items  what came back on returns (not deleted) of THOSE bills, per medicine
  purchase_items      every purchase line (purchase not deleted, not a draft) of the medicine
                      rows sold, with the units one purchased pack added to stock -- the line's
                      own stock_units, else purchase_service._get_stock_increase, the rule a
                      purchase save uses

Nothing is written: no stock, bill or purchase is touched.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Any

from core.doctor_margin import NO_DOCTOR, doctor_margin, is_injection, rounded

METHOD = "Scheme-adjusted effective purchase rate"
METHOD_NOTE = (
    "Purchase rate = what the batch's purchase lines cost (after discount, with GST) / every "
    "unit received, free units included."
)
ALLOCATION_RULE = "100% of positive profit"
STYLES = ("detailed", "summary")
FLAG_LABELS = {"estimated": "Estimated cost", "unavailable": "No cost", "loss": "Loss"}
FLAG_NOTES = (
    "Estimated cost: no purchase line for the batch; the cost the bill recorded at sale time is used.",
    "No cost: neither is known; the cost is taken as 0, so the profit shown is too high.",
    "Loss: sold below cost; no negative commission is counted -- check these lines.",
)
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class ReportError(ValueError):
    """A filter the shop has to correct (said as it is, HTTP 400)."""


# ── Reading the books ──────────────────────────────────────────────────────────────────────

def _cols(conn, table: str) -> set[str]:
    try:
        return {str(r[1]) for r in conn.execute(f"PRAGMA table_info({table})")}
    except Exception:
        return set()


def _live(conn, table: str, alias: str, *, autosave: bool = False) -> list[str]:
    have = _cols(conn, table)
    out = []
    if "deleted" in have:
        out.append(f"COALESCE({alias}.deleted,0)=0")
    if autosave and "is_autosave" in have:
        out.append(f"COALESCE({alias}.is_autosave,0)=0")
    return out


def _next_day(iso: str) -> str:
    return (date.fromisoformat(iso) + timedelta(days=1)).isoformat()


def _units_per_pack(med_type: str, unit: str, qty: float, free: float, logged: Any,
                    strip_cache: dict[str, bool]) -> float:
    """Sale units one purchased pack of this line put on the shelf."""
    got = qty + free
    if got <= 0:
        return 1.0
    try:
        stocked = float(logged) if logged is not None else 0.0
    except (TypeError, ValueError):
        stocked = 0.0
    if stocked <= 0:
        from core.layout_config import is_strip_count_type, parse_tablets_per_stripe
        from core.purchase_service import _get_stock_increase

        if med_type not in strip_cache:
            strip_cache[med_type] = bool(is_strip_count_type(med_type))
        item: dict[str, Any] = {"type": med_type, "qty": qty, "free_qty": free}
        if strip_cache[med_type]:
            item["tablets_per_stripe"] = parse_tablets_per_stripe(unit)
        stocked = float(_get_stock_increase(item) or 0)
    units = stocked / got
    if abs(units - round(units)) < 1e-9:
        units = float(round(units))
    return units if units > 0 else 1.0


def read_books(conn, from_date: str, to_date: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(sales, purchases) for doctor_margin(), bills of from_date..to_date (bill_date)."""
    from core.fy_serial import display_sales_bill_no

    bill_where = " AND ".join(
        _live(conn, "sales", "s", autosave=True) + ["s.bill_date >= ?", "s.bill_date < ?"]
    )
    span = (from_date, _next_day(to_date))
    bills_sql = f"SELECT s.id FROM sales s WHERE {bill_where}"

    bills = conn.execute(
        f"""
        SELECT s.id, COALESCE(s.bill_no,''), COALESCE(s.bill_date,''),
               COALESCE(NULLIF(TRIM(COALESCE(s.customer_name,'')),''), c.name, ''),
               COALESCE(s.doctor_name,''), COALESCE(s.total_amount,0)
        FROM sales s LEFT JOIN customers c ON c.id = s.customer_id
        WHERE {bill_where}
        ORDER BY substr(s.bill_date,1,10), s.id
        """,
        span,
    ).fetchall()
    if not bills:
        return [], []
    sales: dict[int, dict[str, Any]] = {}
    for sid, bno, bdate, patient, doctor, total in bills:
        sales[int(sid)] = {
            "id": int(sid), "bill_no": display_sales_bill_no(str(bno)), "date": str(bdate)[:10],
            "patient": str(patient or ""), "doctor": str(doctor or ""), "total_amount": float(total or 0),
            "items": [], "returns": [],
        }

    item_live = " AND ".join([""] + _live(conn, "sales_items", "si"))
    for sid, mid, name, mtype, batch, qty, amount, cost in conn.execute(
        f"""
        SELECT si.sale_id, si.medicine_id, COALESCE(m.name,''), COALESCE(m.type,''),
               COALESCE(m.batch_no,''), COALESCE(si.qty,0), COALESCE(si.amount,0),
               COALESCE(si.cost_price,0)
        FROM sales_items si LEFT JOIN medicines m ON m.id = si.medicine_id
        WHERE si.sale_id IN ({bills_sql}){item_live}
        ORDER BY si.sale_id, si.id
        """,
        span,
    ):
        bill = sales.get(int(sid))
        if bill is not None:
            bill["items"].append({
                "medicine_id": int(mid or 0), "name": str(name), "type": str(mtype), "batch": str(batch),
                "qty": float(qty or 0), "amount": float(amount or 0), "cost_price": float(cost or 0),
            })

    if _cols(conn, "sales_returns") and _cols(conn, "sales_return_items"):
        ret_live = " AND ".join(
            [""] + _live(conn, "sales_returns", "sr") + _live(conn, "sales_return_items", "sri")
        )
        for sid, mid, qty in conn.execute(
            f"""
            SELECT sr.sale_id, sri.medicine_id, SUM(COALESCE(sri.qty,0))
            FROM sales_return_items sri JOIN sales_returns sr ON sr.id = sri.return_id
            WHERE sr.sale_id IN ({bills_sql}){ret_live}
            GROUP BY sr.sale_id, sri.medicine_id
            ORDER BY sr.sale_id, sri.medicine_id
            """,
            span,
        ):
            bill = sales.get(int(sid or 0))
            if bill is not None:
                bill["returns"].append({"medicine_id": int(mid or 0), "qty": float(qty or 0)})

    pi_cols = _cols(conn, "purchase_items")
    logged = "pi.stock_units" if "stock_units" in pi_cols else "NULL"
    if "item_amount" in pi_cols and "amount" in pi_cols:
        # Both hold the line's amount after discount, with GST; an older row may have only one.
        line_amount = ("CASE WHEN COALESCE(pi.item_amount,0) <> 0 THEN pi.item_amount "
                       "ELSE COALESCE(pi.amount,0) END")
    else:
        line_amount = "COALESCE(pi.item_amount,0)" if "item_amount" in pi_cols else "COALESCE(pi.amount,0)"
    p_where = " AND ".join(
        _live(conn, "purchases", "p", autosave=True) + _live(conn, "purchase_items", "pi")
        + [f"pi.medicine_id IN (SELECT DISTINCT si.medicine_id FROM sales_items si "
           f"WHERE si.sale_id IN ({bills_sql}){item_live})"]
    )
    purchases: list[dict[str, Any]] = []
    strip_cache: dict[str, bool] = {}
    for mid, qty, free, amount, mtype, unit, stocked in conn.execute(
        f"""
        SELECT pi.medicine_id, COALESCE(pi.qty,0), COALESCE(pi.free_qty,0), {line_amount},
               COALESCE(NULLIF(TRIM(pi.type),''), m.type, ''), COALESCE(m.unit,'1'), {logged}
        FROM purchase_items pi
        JOIN purchases p ON p.id = pi.purchase_id
        LEFT JOIN medicines m ON m.id = pi.medicine_id
        WHERE {p_where}
        ORDER BY pi.id
        """,
        span,
    ):
        q, f = float(qty or 0), float(free or 0)
        purchases.append({
            "medicine_id": int(mid or 0), "qty": q, "free_qty": f, "item_amount": float(amount or 0),
            "units": _units_per_pack(str(mtype or ""), str(unit or "1"), q, f, stocked, strip_cache),
        })
    return list(sales.values()), purchases


def report_options(conn) -> dict[str, Any]:
    """Doctors (the doctors list + names on bills + "(No doctor)") and medicine types."""
    from core.doctor_margin import _u16

    doc_live = " AND ".join([""] + _live(conn, "doctors", "d"))
    bill_live = " AND ".join([""] + _live(conn, "sales", "s", autosave=True))
    names: set[str] = set()
    for (n,) in conn.execute(
        f"SELECT DISTINCT TRIM(d.name) FROM doctors d WHERE TRIM(COALESCE(d.name,''))<>''{doc_live}"
    ):
        names.add(str(n).strip().upper())
    for (n,) in conn.execute(
        f"SELECT DISTINCT TRIM(s.doctor_name) FROM sales s "
        f"WHERE TRIM(COALESCE(s.doctor_name,''))<>''{bill_live}"
    ):
        names.add(str(n).strip().upper())
    names.discard("")
    names.discard(NO_DOCTOR.upper())

    types: list[str] = []
    seen: set[str] = set()
    try:
        from core.layout_config import get_med_types

        configured = [str(t).strip() for t in get_med_types()]
    except Exception:
        configured = []
    med_live = " AND ".join([""] + _live(conn, "medicines", "m"))
    in_use = sorted(
        (str(t).strip() for (t,) in conn.execute(
            f"SELECT DISTINCT TRIM(m.type) FROM medicines m WHERE TRIM(COALESCE(m.type,''))<>''{med_live}"
        )),
        key=lambda t: t.lower(),
    )
    for t in configured + in_use:
        if t and t.lower() not in seen:
            seen.add(t.lower())
            types.append(t)
    return {
        "ok": True,
        "doctors": sorted(names, key=_u16) + [NO_DOCTOR],
        "types": types,
        "injection_types": [t for t in types if is_injection(t)],
        "pct": _saved_pct(),
        "style": _saved_style(),
        "method": METHOD,
        "method_note": METHOD_NOTE,
        "online": _is_online(),
    }


def _saved_pct() -> float:
    try:
        from core.desktop_ui_prefs import load_desktop_ui_prefs

        return float(load_desktop_ui_prefs().get("doctor_margin_pct", 10))
    except Exception:
        return 10.0


def _saved_style() -> str:
    try:
        from core.desktop_ui_prefs import load_desktop_ui_prefs

        s = str(load_desktop_ui_prefs().get("doctor_margin_style") or "detailed")
        return s if s in STYLES else "detailed"
    except Exception:
        return "detailed"


def _is_online() -> bool:
    try:
        from core.sync_prefs import is_online_mode

        return bool(is_online_mode())
    except Exception:
        return False


# ── The report ─────────────────────────────────────────────────────────────────────────────

def parse_filters(body: dict[str, Any]) -> dict[str, Any]:
    """The dialog's filters, checked. Raises ReportError with what to correct."""
    today = date.today().isoformat()
    fd = str(body.get("from") or body.get("from_date") or today).strip()[:10]
    td = str(body.get("to") or body.get("to_date") or today).strip()[:10]
    for label, v in (("From", fd), ("To", td)):
        if not _DATE_RE.match(v):
            raise ReportError(f"{label} date is not a date (YYYY-MM-DD).")
        try:
            date.fromisoformat(v)
        except ValueError:
            raise ReportError(f"{label} date is not a date (YYYY-MM-DD).") from None
    if fd > td:
        raise ReportError("From date is after To date.")
    mode = "injection" if str(body.get("mode") or "").strip().lower() == "injection" else "regular"
    raw_pct = body.get("pct")
    if raw_pct is None or str(raw_pct).strip() == "":
        pct = 10.0
    else:
        try:
            pct = float(str(raw_pct).strip())
        except ValueError:
            raise ReportError("Doctor commission must be a number from 0 to 100 %.") from None
    if not (0 <= pct <= 100):
        raise ReportError("Doctor commission must be 0 to 100 %.")
    raw_types = body.get("types")
    if isinstance(raw_types, str):
        raw_types = raw_types.split(",")
    types = [str(t).strip() for t in (raw_types or []) if str(t).strip()]
    style = str(body.get("style") or "detailed").strip().lower()
    if style not in STYLES:
        style = "detailed"
    return {
        "from": fd, "to": td, "mode": mode, "pct": pct, "types": types,
        "doctor": str(body.get("doctor") or "").strip(), "style": style,
    }


def _flag_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    out = {k: 0 for k in FLAG_LABELS}
    for r in rows:
        for f in r.get("flags") or []:
            if f in out:
                out[f] += 1
    return out


def build_report(conn, body: dict[str, Any]) -> dict[str, Any]:
    """{ok, filters, mode, pct, rows, summary, doctors, totals, flag_counts} -- money in paise.

    mode 'rules' (owner, 22 Oct 2026; the dialog's default): the report by the doctor margin
    rules, from the doctor report's saved lines -- core/doctor_margin_rules_report.py."""
    if str(body.get("mode") or "").strip().lower() == "rules":
        from core import doctor_margin_rules_report

        return doctor_margin_rules_report.build_report(conn, body)
    if str(body.get("mode") or "").strip().lower() == "bills":
        from core import doctor_bill_profit

        return doctor_bill_profit.build_report(conn, body)
    if _is_online():
        raise ReportError(
            "The Doctor Margin Report reads this PC's own books; an Online store's bills are "
            "on the server (Sales History on the web)."
        )
    f = parse_filters(body)
    sales, purchases = read_books(conn, f["from"], f["to"])
    rep = rounded(doctor_margin(sales, purchases, {
        "mode": f["mode"], "pct": f["pct"], "types": f["types"] or None, "doctor": f["doctor"],
    }))
    return {
        "ok": True,
        "filters": f,
        "method": METHOD,
        "method_note": METHOD_NOTE,
        "flag_counts": _flag_counts(rep["rows"]),
        "bill_count": len(sales),
        **rep,
    }


# ── Laying it out (screen columns are the desktop dialog's; these are the files') ─────────

def columns_for(mode: str, style: str) -> list[str]:
    return detailed_columns(mode) if style == "detailed" else summary_columns(mode)


def detailed_columns(mode: str) -> list[str]:
    head = ["Sale Date", "Customer / Patient", "Doctor", "Bill No", "Medicine", "Type", "Batch",
            "Sold Qty", "Selling Rate", "Purchase Rate (scheme-adjusted)", "Sale Amount", "Purchase Cost"]
    if mode == "injection":
        return head + ["Injection Profit", "Doctor Margin ₹", "Allocation Rule", "Store Remaining Profit", "Flags"]
    return head + ["Profit", "Commission %", "Commission ₹", "Store Remaining Profit", "Flags"]


def summary_columns(mode: str) -> list[str]:
    head = ["Doctor", "Medicine", "Type", "Batch", "Total Qty", "Avg Selling Rate",
            "Purchase Rate (scheme-adjusted)", "Total Sale", "Total Cost"]
    if mode == "injection":
        return head + ["Total Injection Profit", "Total Doctor Margin ₹", "Allocation Rule",
                       "Store Remaining Profit", "Flags"]
    return head + ["Total Profit", "Commission %", "Total Commission ₹", "Store Remaining Profit", "Flags"]


def _flags_text(flags: list[str]) -> str:
    return ", ".join(FLAG_LABELS.get(f, f) for f in flags or [])


def _qty(v: float) -> Any:
    return int(v) if float(v) == int(v) else v


def _tail(r: dict[str, Any], mode: str) -> list[Any]:
    if mode == "injection":
        return [r["profit"], r["commission"], ALLOCATION_RULE, r["remaining"], _flags_text(r["flags"])]
    return [r["profit"], r["pct"], r["commission"], r["remaining"], _flags_text(r["flags"])]


def table_rows(rep: dict[str, Any], style: str) -> list[list[Any]]:
    """The rows of the chosen style. A summary row carries no patient and no bill number."""
    mode = rep["mode"]
    out: list[list[Any]] = []
    if style == "detailed":
        for r in rep["rows"]:
            out.append([
                _fmt_date(r["date"]), r["patient"], r["doctor"], r["bill_no"], r["name"], r["type"], r["batch"],
                _qty(r["qty"]), r["sale_rate"], r["purchase_rate"], r["amount"], r["cost"],
            ] + _tail(r, mode))
    else:
        for r in rep["summary"]:
            out.append([
                r["doctor"], r["name"], r["type"], r["batch"], _qty(r["qty"]), r["avg_sale_rate"],
                r["purchase_rate"], r["amount"], r["cost"],
            ] + _tail(r, mode))
    return out


def total_row(rep: dict[str, Any], style: str, label: str = "TOTAL", t: dict[str, Any] | None = None) -> list[Any]:
    """The totals under the table, in the same columns (rates are never added up)."""
    t = t or rep["totals"]
    mode = rep["mode"]
    if mode == "injection":
        money = [t["profit"], t["commission"], ALLOCATION_RULE]
    else:
        money = [t["profit"], rep["pct"], t["commission"]]
    if style == "detailed":
        return [label, "", "", "", "", "", "", _qty(t["qty"]), "", "", t["amount"], t["cost"]] + money + [t["remaining"], ""]
    return [label, "", "", "", _qty(t["qty"]), "", "", t["amount"], t["cost"]] + money + [t["remaining"], ""]


def doctor_columns(mode: str) -> list[str]:
    margin = "Doctor Margin ₹" if mode == "injection" else "Commission ₹"
    return ["Doctor", "Qty", "Sale Amount", "Purchase Cost", "Profit", margin, "Store Remaining Profit"]


def doctor_rows(rep: dict[str, Any]) -> list[list[Any]]:
    rows = [[d["doctor"], _qty(d["qty"]), d["amount"], d["cost"], d["profit"], d["commission"], d["remaining"]]
            for d in rep["doctors"]]
    t = rep["totals"]
    rows.append(["TOTAL", _qty(t["qty"]), t["amount"], t["cost"], t["profit"], t["commission"], t["remaining"]])
    return rows


def _fmt_date(iso: str) -> str:
    s = str(iso or "")[:10]
    if len(s) == 10 and s[4] == "-" and s[7] == "-":
        return f"{s[8:10]}/{s[5:7]}/{s[0:4]}"
    return s


def header_lines(rep: dict[str, Any], generated: datetime | None = None) -> list[tuple[str, str]]:
    """(label, value): what every file says above the table."""
    f = rep["filters"]
    mode = rep["mode"]
    types = f["types"]
    if mode == "injection":
        inj = [t for t in types if is_injection(t)]
        types_text = ", ".join(inj) if inj else "All injection types"
    else:
        reg = [t for t in types if not is_injection(t)]
        types_text = ", ".join(reg) if reg else "All non-injection types"
    t = rep["totals"]
    margin_word = "Doctor margin" if mode == "injection" else "Commission"
    return [
        ("Generated at", (generated or datetime.now()).strftime("%d/%m/%Y %H:%M")),
        ("Date range", f"{_fmt_date(f['from'])} to {_fmt_date(f['to'])}"),
        ("Doctor", f["doctor"] or "All doctors"),
        ("Medicine types", types_text),
        ("Report mode", "Injection report" if mode == "injection" else "Regular medicine report"),
        ("Display style", "Patient-wise detailed" if f["style"] == "detailed" else "Medicine-wise summary"),
        ("Doctor commission", f"{_pct_text(rep['pct'])}%" if mode == "regular"
         else f"{ALLOCATION_RULE} (commission % not used)"),
        ("Purchase cost method", f"{METHOD}. {METHOD_NOTE}"),
        ("Totals", f"Qty {_qty(t['qty'])} | Sale {t['amount']:.2f} | Cost {t['cost']:.2f} | "
                   f"Profit {t['profit']:.2f} | {margin_word} {t['commission']:.2f} | "
                   f"Store remaining {t['remaining']:.2f}"),
    ]


def _pct_text(p: Any) -> str:
    v = float(p or 0)
    return str(int(v)) if v == int(v) else f"{v:g}"


def _file_base(rep: dict[str, Any]) -> str:
    f = rep["filters"]
    kind = "injection" if rep["mode"] == "injection" else "regular"
    return f"doctor_margin_{kind}_{f['style']}_{f['from']}_{f['to']}"


def _csv_bytes(title: str, heads, cols, rows, tot, dcols, drows) -> bytes:
    import csv
    import io

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([title])
    for k, v in heads:
        w.writerow([k, v])
    w.writerow([])
    w.writerow(cols)
    w.writerows(rows)
    w.writerow(tot)
    w.writerow([])
    w.writerow(["Totals by doctor"])
    w.writerow(dcols)
    w.writerows(drows)
    w.writerow([])
    for n in FLAG_NOTES:
        w.writerow([n])
    return buf.getvalue().encode("utf-8-sig")


def _xlsx_bytes(title: str, heads, cols, rows, tot, dcols, drows) -> bytes:
    import io

    import openpyxl
    from openpyxl.styles import Font

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Doctor Margin"
    bold = Font(bold=True)
    ws.append([title])
    ws.cell(row=1, column=1).font = Font(bold=True, size=13)
    for k, v in heads:
        ws.append([k, v])
        ws.cell(row=ws.max_row, column=1).font = bold
    ws.append([])
    ws.append(cols)
    for c in ws[ws.max_row]:
        c.font = bold
    for r in rows:
        ws.append(list(r))
    ws.append(tot)
    for c in ws[ws.max_row]:
        c.font = bold
    ws.append([])
    ws.append(["Totals by doctor"])
    ws.cell(row=ws.max_row, column=1).font = bold
    ws.append(dcols)
    for c in ws[ws.max_row]:
        c.font = bold
    for r in drows:
        ws.append(list(r))
    for c in ws[ws.max_row]:
        c.font = bold
    ws.append([])
    for n in FLAG_NOTES:
        ws.append([n])
    for row in ws.iter_rows():
        for c in row:
            if isinstance(c.value, float):
                c.number_format = "0.00"
    for i, h in enumerate(cols, 1):
        width = max([len(str(h))] + [len(str(r[i - 1])) for r in rows[:500] if i - 1 < len(r)])
        ws.column_dimensions[openpyxl.utils.get_column_letter(i)].width = min(40, max(9, width + 2))
    bio = io.BytesIO()
    wb.save(bio)
    return bio.getvalue()


def report_html(title: str, heads, cols, rows, tot, dcols, drows) -> str:
    def esc(v: Any) -> str:
        return str(v).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    def cell(v: Any) -> str:
        if isinstance(v, float):
            return f"<td class=num>{v:.2f}</td>"
        if isinstance(v, int) and not isinstance(v, bool):
            return f"<td class=num>{v}</td>"
        return f"<td>{esc(v)}</td>"

    def table(cs, rs, last_total: bool = False) -> str:
        hdr = "".join(f"<th>{esc(h)}</th>" for h in cs)
        body = "".join(
            f"<tr{' class=total' if last_total and i == len(rs) - 1 else ''}>" + "".join(cell(v) for v in r) + "</tr>"
            for i, r in enumerate(rs)
        )
        return f"<table><thead><tr>{hdr}</tr></thead><tbody>{body}</tbody></table>"

    meta = "".join(f"<tr><th>{esc(k)}</th><td>{esc(v)}</td></tr>" for k, v in heads)
    notes = "".join(f"<li>{esc(n)}</li>" for n in FLAG_NOTES)
    css = (
        "@page{size:A4 landscape;margin:9mm}body{font-family:Segoe UI,Arial;font-size:8pt;color:#000}"
        "h2{margin:0 0 6px}h3{margin:12px 0 4px;font-size:10pt}table{width:100%;border-collapse:collapse}"
        "table.meta{width:auto;margin-bottom:8px}table.meta th{background:none;color:#000;text-align:left;"
        "padding:1px 10px 1px 0;border:0}table.meta td{border:0;padding:1px 0}"
        "th{background:#2c3e50;color:#fff;padding:3px;text-align:left}td{border:1px solid #ccc;padding:2px 3px}"
        "td.num{text-align:right;font-variant-numeric:tabular-nums}tbody tr:nth-child(even){background:#f5f5f5}"
        "tr.total td{font-weight:700;border-top:2px solid #333;background:#fff}thead{display:table-header-group}"
        "ul{margin:4px 0 0 16px;padding:0}"
    )
    return (f"<!DOCTYPE html><html><head><meta charset=UTF-8><title>{esc(title)}</title>"
            f"<style>{css}</style></head><body><h2>{esc(title)}</h2>"
            f"<table class=meta>{meta}</table>"
            f"{table(cols, list(rows) + [tot], last_total=True)}"
            f"<h3>Totals by doctor</h3>{table(dcols, drows, last_total=True)}"
            f"<h3>Flags</h3><ul>{notes}</ul></body></html>")


def _pdf_bytes(html: str) -> tuple[bytes, str]:
    """(bytes, 'pdf' | 'html') -- the HTML itself when no browser could print it to PDF."""
    import os
    import tempfile

    from core.document_output import save_html_as_pdf

    fd, path = tempfile.mkstemp(suffix=".pdf", prefix="doctor_margin_")
    os.close(fd)
    pdf_path, html_path = save_html_as_pdf(html, path)
    saved = pdf_path or html_path
    with open(saved, "rb") as fh:
        payload = fh.read()
    for p in {path, html_path, pdf_path or ""}:
        try:
            if p and os.path.isfile(p):
                os.remove(p)
        except OSError:
            pass
    return payload, "pdf" if pdf_path else "html"


def export_report(conn, body: dict[str, Any]) -> dict[str, Any]:
    """The report as a file (format xlsx | csv | pdf) or on paper (format print).

    Files come back as base64 for the File saved box (saved_files); a print keeps a PDF copy
    in the reports folder and says where."""
    import base64

    if str(body.get("mode") or "").strip().lower() == "rules":
        from core import doctor_margin_rules_report

        return doctor_margin_rules_report.export_report(conn, body)
    if str(body.get("mode") or "").strip().lower() == "bills":
        from core import doctor_bill_profit

        return doctor_bill_profit.export_report(conn, body)
    rep = build_report(conn, body)
    style = rep["filters"]["style"]
    rows = table_rows(rep, style)
    if not rows:
        return {"ok": False, "error": "No sales match these filters."}
    title = "Doctor Margin Report"
    heads = header_lines(rep)
    cols = columns_for(rep["mode"], style)
    tot = total_row(rep, style)
    dcols, drows = doctor_columns(rep["mode"]), doctor_rows(rep)
    return deliver(body, title, heads, cols, rows, tot, dcols, drows, _file_base(rep), rep["totals"],
                   dot_matrix_heads=heads[1:7])


def deliver(body: dict[str, Any], title: str, heads, cols, rows, tot, dcols, drows, base: str,
            totals: dict[str, Any], *, dot_matrix_heads=None) -> dict[str, Any]:
    """A laid-out report as a file (format xlsx | csv | pdf, base64 for the File saved box) or on
    paper (format print: the dot matrix A4 horizontal, or the printer with a PDF copy kept)."""
    import base64

    fmt = str(body.get("format") or "xlsx").strip().lower()
    if fmt == "excel":
        fmt = "xlsx"

    if fmt == "print":
        target = str(body.get("print_to") or "").strip().lower()
        if target not in ("dot_matrix", "printer"):
            try:
                from core.printer_manager import PrinterManager

                target = "dot_matrix" if PrinterManager.is_dot_matrix_mode() else "printer"
            except Exception:
                target = "printer"
        if target == "dot_matrix":
            from core.dot_matrix_print import print_dot_matrix_report

            dm_heads = heads if dot_matrix_heads is None else dot_matrix_heads
            dm_title = f"{title} | " + " | ".join(f"{k}: {v}" for k, v in dm_heads)
            try:
                print_dot_matrix_report(dm_title, cols, rows + [tot], paper="A4", landscape=True)
            except Exception as exc:
                return {"ok": False, "printed": False, "print_error": str(exc),
                        "error": f"Dot matrix var print zala nahi: {exc}"}
            return {"ok": True, "printed": True, "message": "Sent to the dot matrix (A4 horizontal)."}
        from core import saved_files
        from core.desktop_export_service import print_pdf_on_printer

        payload, ext = _pdf_bytes(report_html(title, heads, cols, rows, tot, dcols, drows))
        if ext != "pdf":
            return {"ok": False, "printed": False,
                    "error": "PDF could not be made for printing. Install Edge or Chrome."}
        path = saved_files.save_bytes(f"{base}.pdf", payload)
        problem = print_pdf_on_printer(path, landscape=True)
        if problem:
            return {"ok": False, "printed": False, "print_error": problem, "error": problem, "path": path}
        return {"ok": True, "printed": True, "path": path, "message": "Sent to the printer (A4 horizontal)."}

    if fmt == "csv":
        payload, mime = _csv_bytes(title, heads, cols, rows, tot, dcols, drows), "text/csv;charset=utf-8"
    elif fmt == "xlsx":
        try:
            payload = _xlsx_bytes(title, heads, cols, rows, tot, dcols, drows)
        except ImportError:
            return {"ok": False, "error": "openpyxl not installed for Excel export."}
        mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    elif fmt == "pdf":
        payload, fmt = _pdf_bytes(report_html(title, heads, cols, rows, tot, dcols, drows))
        mime = "application/pdf" if fmt == "pdf" else "text/html"
    else:
        return {"ok": False, "error": f"Unknown export format: {fmt}"}
    return {
        "ok": True,
        "filename": f"{base}.{fmt}",
        "mime": mime,
        "format": fmt,
        "content_base64": base64.b64encode(payload).decode("ascii"),
        "row_count": len(rows),
        "totals": totals,
    }
