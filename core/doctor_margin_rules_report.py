"""Doctor Margin Report by the margin rules (owner, 22 Oct 2026) -- the screen, the files, the
pick lists of the rules screen.

Reads the doctor report's saved lines (core/doctor_margin_store.py) after bringing the period
up to date with its bills; the rules are core/doctor_margin_rules.py. Two layouts:
  doctor  the owner's Excel: per doctor, product / batch / quantity / MRP / rate / margin per
          unit / total value, a TOTAL per doctor and a grand TOTAL
  detail  customer-wise product-batch-wise: per product, every bill line (customer, bill no,
          date, batch, qty, MRP, rate, value, doctor, type) and a sub-total
Only the lines a margin is given on appear.
"""
from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

from core import doctor_margin_rules as dmr
from core import doctor_margin_store as store

VIEWS = ("doctor", "detail")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


from core.doctor_margin_report import ReportError  # noqa: E402  (the route answers it with HTTP 400)


def _dmy(iso: str, sep: str = "-") -> str:
    s = str(iso or "")[:10]
    return f"{s[8:10]}{sep}{s[5:7]}{sep}{s[0:4]}" if len(s) == 10 else s


def parse_filters(body: dict[str, Any]) -> dict[str, Any]:
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
    raw = body.get("doctors")
    if raw is None and body.get("doctor"):
        raw = [body.get("doctor")]
    if isinstance(raw, str):
        raw = raw.split(",")
    doctors = sorted({dmr.norm_name(d) for d in (raw or []) if dmr.norm_name(d)})
    basis = str(body.get("rate_basis") or "").strip().lower()
    view = str(body.get("view") or "doctor").strip().lower()
    return {
        "from": fd, "to": td, "doctors": doctors,
        "rate_basis": basis if basis in dmr.RATE_BASES else "",
        "view": view if view in VIEWS else "doctor",
    }


def _is_online() -> bool:
    try:
        from core.sync_prefs import is_online_mode

        return bool(is_online_mode())
    except Exception:
        return False


def _flag_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    out = {k: 0 for k in dmr.FLAG_TEXT}
    for r in rows:
        for f in r.get("flags") or []:
            if f in out:
                out[f] += 1
    return out


def by_product(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The detail layout: per product (name), its lines by batch / date, and a sub-total."""
    out: dict[str, dict[str, Any]] = {}
    for r in sorted(rows, key=lambda r: (r["name"], r["batch"], r["date"], r["bill_no"])):
        g = out.get(r["name"])
        if g is None:
            g = out[r["name"]] = {"name": r["name"], "type": r["type"], "lines": [], "qty": 0.0, "value": 0.0}
        g["lines"].append(r)
        g["qty"] = round(g["qty"] + r["qty"], 3)
        g["value"] = dmr.r2(g["value"] + r["value"])
    return list(out.values())


def build_report(conn, body: dict[str, Any]) -> dict[str, Any]:
    if _is_online():
        raise ReportError(
            "The Doctor Margin Report reads this PC's own books; an Online store's bills are "
            "on the server (Sales History on the web)."
        )
    f = parse_filters(body)
    if body.get("recalculate"):
        done = store.recalculate(conn, f["from"], f["to"])
        sync = {"recalculated": True, **done}
    else:
        sync = store.reconcile(conn, f["from"], f["to"])
    lines = store.read_lines(conn, f["from"], f["to"], f["doctors"])
    if f["rate_basis"]:
        lines = [dmr.with_basis(r, f["rate_basis"]) for r in lines]
    rep = dmr.assemble(lines)
    rules = dmr.load_rules(conn)
    return {
        "ok": True,
        "mode": "rules",
        "filters": f,
        "has_rules": dmr.has_rules(rules),
        "rules_rate_basis": rules["rate_basis"],
        "rate_basis_text": dmr.RATE_BASIS_TEXT.get(f["rate_basis"], "As saved with each bill (rules)"),
        "flag_counts": _flag_counts(rep["rows"]),
        "products": by_product(rep["rows"]),
        "sync": sync,
        **rep,
    }


# ── the rules screen's pick lists ───────────────────────────────────────────────────────────

def rule_products(conn, med_type: str = "", find: str = "") -> dict[str, Any]:
    """Products of a type for the pick list: name, stock (sale units), pack, company, contents,
    and its batches (batch, stock, expiry, MRP). Hidden / deleted medicine rows left out."""
    have = store._cols(conn, "medicines")
    where = ["TRIM(COALESCE(m.name,''))<>''"]
    args: list[Any] = []
    if "deleted" in have:
        where.append("COALESCE(m.deleted,0)=0")
    if med_type:
        where.append("LOWER(TRIM(COALESCE(m.type,'')))=?")
        args.append(dmr.norm_type(med_type))
    if find:
        where.append("UPPER(m.name) LIKE ?")
        args.append(f"%{find.strip().upper()}%")
    company = "m.manufacturer" if "manufacturer" in have else "''"
    contents = "m.content_drug" if "content_drug" in have else "''"
    products: dict[str, dict[str, Any]] = {}
    for name, mtype, batch, stock, unit, comp, cont, expiry, mrp in conn.execute(
        f"""
        SELECT m.name, COALESCE(m.type,''), COALESCE(m.batch_no,''), COALESCE(m.stock_qty,0),
               COALESCE(m.unit,''), COALESCE({company},''), COALESCE({contents},''),
               COALESCE(m.expiry_date,''), COALESCE(m.mrp,0)
        FROM medicines m WHERE {' AND '.join(where)}
        ORDER BY UPPER(m.name), m.batch_no
        """,
        args,
    ):
        key = dmr.norm_name(name)
        p = products.get(key)
        if p is None:
            p = products[key] = {"product": key, "type": str(mtype), "stock": 0.0, "pack": str(unit or ""),
                                 "company": str(comp or ""), "contents": str(cont or ""), "batches": []}
        p["stock"] += float(stock or 0)
        b = dmr.norm_batch(batch)
        if b and all(x["batch"] != b for x in p["batches"]):
            p["batches"].append({"batch": b, "stock": float(stock or 0), "expiry": str(expiry or "")[:10],
                                 "mrp": float(mrp or 0)})
        elif b:
            for x in p["batches"]:
                if x["batch"] == b:
                    x["stock"] += float(stock or 0)
        if not p["company"] and comp:
            p["company"] = str(comp)
        if not p["contents"] and cont:
            p["contents"] = str(cont)
    return {"ok": True, "type": med_type, "products": list(products.values())}


def rules_payload(conn) -> dict[str, Any]:
    from core.doctor_margin_report import report_options

    opts = report_options(conn)
    return {
        "ok": True,
        "rules": dmr.load_rules(conn),
        "doctors": [d for d in opts["doctors"] if d != dmr.NO_DOCTOR],
        "types": opts["types"],
        "online": opts["online"],
    }


# ── files ──────────────────────────────────────────────────────────────────────────────────

DOCTOR_COLS = ["Product name", "Batch", "Quantity", "MRP", "Rate", "Margin / unit", "Total value"]
DETAIL_COLS = ["Product / Customer", "Bill no", "Date", "Batch", "Qty", "MRP", "Rate", "Margin / unit",
               "Value", "Doctor", "Type"]


def _q(v: float) -> Any:
    return int(v) if float(v) == int(v) else v


def table(rep: dict[str, Any]) -> tuple[list[str], list[tuple[str, list[Any]]]]:
    """(columns, [(kind, cells)]) for the chosen view. kind: head | line | sub | total."""
    out: list[tuple[str, list[Any]]] = []
    if rep["filters"]["view"] == "detail":
        for p in rep["products"]:
            out.append(("head", [p["name"]] + [""] * (len(DETAIL_COLS) - 1)))
            for r in p["lines"]:
                out.append(("line", [r["patient"], r["bill_no"], _dmy(r["date"], "/"), r["batch"], _q(r["qty"]),
                                     r["mrp"], r["rate"], r["margin_unit"], r["value"], r["doctor"], r["type"]]))
            out.append(("sub", ["Sub total", "", "", "", _q(p["qty"]), "", "", "", p["value"], "", ""]))
        t = rep["totals"]
        out.append(("total", ["TOTAL value", "", "", "", _q(t["qty"]), "", "", "", t["value"], "", ""]))
        return DETAIL_COLS, out
    for d in rep["doctors"]:
        out.append(("head", [d["doctor"]] + [""] * (len(DOCTOR_COLS) - 1)))
        for r in rep["summary"]:
            if r["doctor"] == d["doctor"]:
                out.append(("line", [r["name"], r["batch"], _q(r["qty"]), r["mrp"], r["rate"], r["margin_unit"],
                                     r["value"]]))
        out.append(("sub", ["", "", _q(d["qty"]), "", "", "TOTAL value", d["value"]]))
    if len(rep["doctors"]) > 1:
        t = rep["totals"]
        out.append(("total", ["ALL DOCTORS", "", _q(t["qty"]), "", "", "TOTAL value", t["value"]]))
    return DOCTOR_COLS, out


def header_lines(rep: dict[str, Any]) -> list[tuple[str, str]]:
    f = rep["filters"]
    return [
        ("Period", f"{_dmy(f['from'])} to {_dmy(f['to'])}"),
        ("Doctor", ", ".join(f["doctors"]) or "All doctors"),
        ("Rate", rep["rate_basis_text"]),
        ("Layout", "Doctor-wise (product, qty, MRP, rate, total value)" if f["view"] == "doctor"
         else "Customer-wise product-batch-wise"),
        ("Generated at", datetime.now().strftime("%d/%m/%Y %H:%M")),
    ]


def _shop_name(conn=None) -> str:
    try:
        from core.pharmacy_profile_io import load_pharmacy_profile

        return str((load_pharmacy_profile(conn) or {}).get("name") or "")
    except Exception:
        return ""


def _xlsx(title: str, heads, cols, rows) -> bytes:
    import io

    import openpyxl
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

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
    band = PatternFill("solid", fgColor="1F4E79")
    for c in ws[ws.max_row]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = band
    line = Side(style="thin", color="999999")
    for kind, cells in rows:
        ws.append(list(cells))
        r = ws[ws.max_row]
        if kind == "head":
            r[0].font = Font(bold=True, size=11, color="1F4E79")
        elif kind in ("sub", "total"):
            for c in r:
                c.font = bold
                c.border = Border(top=line)
    for row in ws.iter_rows(min_row=len(heads) + 3):
        for c in row:
            if isinstance(c.value, float):
                c.number_format = "0.00"
                c.alignment = Alignment(horizontal="right")
    for i, h in enumerate(cols, 1):
        width = max([len(str(h))] + [len(str(r[i - 1])) for _, r in rows[:800] if i - 1 < len(r)])
        ws.column_dimensions[openpyxl.utils.get_column_letter(i)].width = min(42, max(9, width + 2))
    bio = io.BytesIO()
    wb.save(bio)
    return bio.getvalue()


def _csv(title: str, heads, cols, rows) -> bytes:
    import csv
    import io

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([title])
    for k, v in heads:
        w.writerow([k, v])
    w.writerow([])
    w.writerow(cols)
    for _, cells in rows:
        w.writerow(cells)
    return buf.getvalue().encode("utf-8-sig")


def report_html(title: str, heads, cols, rows, shop: str = "") -> str:
    def esc(v: Any) -> str:
        return str(v).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    def cell(v: Any) -> str:
        if isinstance(v, float):
            return f"<td class=num>{v:.2f}</td>"
        if isinstance(v, int) and not isinstance(v, bool):
            return f"<td class=num>{v}</td>"
        return f"<td>{esc(v)}</td>"

    body = []
    for kind, cells in rows:
        if kind == "head":
            body.append(f"<tr class=head><td colspan={len(cols)}>{esc(cells[0])}</td></tr>")
        else:
            body.append(f"<tr class={kind}>" + "".join(cell(v) for v in cells) + "</tr>")
    meta = "".join(f"<tr><th>{esc(k)}</th><td>{esc(v)}</td></tr>" for k, v in heads)
    hdr = "".join(f"<th>{esc(h)}</th>" for h in cols)
    css = (
        "@page{size:A4 portrait;margin:10mm}body{font-family:Segoe UI,Arial;font-size:9pt;color:#000}"
        "h2{margin:0 0 4px}table{width:100%;border-collapse:collapse}"
        "table.meta{width:auto;margin-bottom:8px}table.meta th{background:none;color:#000;text-align:left;"
        "padding:1px 10px 1px 0;border:0}table.meta td{border:0;padding:1px 0}"
        "th{background:#1f4e79;color:#fff;padding:4px;text-align:left}td{border-bottom:1px solid #ddd;padding:3px 4px}"
        "td.num{text-align:right;font-variant-numeric:tabular-nums}thead{display:table-header-group}"
        "tr.head td{font-weight:700;color:#1f4e79;padding-top:9px;border-bottom:1px solid #1f4e79}"
        "tr.sub td,tr.total td{font-weight:700;border-top:1.5px solid #333}tr.total td{font-size:10pt}"
    )
    return (f"<!DOCTYPE html><html><head><meta charset=UTF-8><title>{esc(title)}</title><style>{css}</style>"
            f"</head><body>{f'<div><b>{esc(shop)}</b></div>' if shop else ''}<h2>{esc(title)}</h2>"
            f"<table class=meta>{meta}</table><table><thead><tr>{hdr}</tr></thead><tbody>{''.join(body)}"
            f"</tbody></table></body></html>")


def export_report(conn, body: dict[str, Any]) -> dict[str, Any]:
    import base64

    from core.doctor_margin_report import _pdf_bytes

    rep = build_report(conn, body)
    if not rep["rows"]:
        return {"ok": False, "error": "No doctor margin in this period for these doctors."}
    f = rep["filters"]
    title = "Doctor Margin Report" + (" - customer-wise product-batch-wise" if f["view"] == "detail" else "")
    heads = header_lines(rep)
    cols, rows = table(rep)
    base = f"doctor_margin_{f['view']}_{f['from']}_{f['to']}"
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

            dm_title = f"{title} | " + " | ".join(f"{k}: {v}" for k, v in heads[:3])
            try:
                print_dot_matrix_report(dm_title, cols, [c for _, c in rows], paper="A4", landscape=f["view"] == "detail")
            except Exception as exc:
                return {"ok": False, "printed": False, "print_error": str(exc),
                        "error": f"Dot matrix var print zala nahi: {exc}"}
            return {"ok": True, "printed": True, "message": "Sent to the dot matrix."}
        from core import saved_files
        from core.desktop_export_service import print_pdf_on_printer

        payload, ext = _pdf_bytes(report_html(title, heads, cols, rows, _shop_name(conn)))
        if ext != "pdf":
            return {"ok": False, "printed": False, "error": "PDF could not be made for printing. Install Edge or Chrome."}
        path = saved_files.save_bytes(f"{base}.pdf", payload)
        problem = print_pdf_on_printer(path, landscape=False)
        if problem:
            return {"ok": False, "printed": False, "print_error": problem, "error": problem, "path": path}
        return {"ok": True, "printed": True, "path": path, "message": "Sent to the printer."}
    if fmt == "csv":
        payload, mime = _csv(title, heads, cols, rows), "text/csv;charset=utf-8"
    elif fmt == "xlsx":
        try:
            payload = _xlsx(title, heads, cols, rows)
        except ImportError:
            return {"ok": False, "error": "openpyxl not installed for Excel export."}
        mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    elif fmt == "pdf":
        payload, fmt = _pdf_bytes(report_html(title, heads, cols, rows, _shop_name(conn)))
        mime = "application/pdf" if fmt == "pdf" else "text/html"
    else:
        return {"ok": False, "error": f"Unknown export format: {fmt}"}
    return {"ok": True, "filename": f"{base}.{fmt}", "mime": mime, "format": fmt,
            "content_base64": base64.b64encode(payload).decode("ascii"), "row_count": len(rep["rows"]),
            "totals": rep["totals"]}
