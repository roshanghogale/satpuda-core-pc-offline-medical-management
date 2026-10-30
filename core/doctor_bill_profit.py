"""Doctor bill-wise profit (owner, 30 Oct 2026): Sales History -> Export -> Doctor Margin Report
-> "Bill-wise profit", like the other software's "Selected doctors: bill-wise profit report".

One row per bill of the doctors picked (one, several -- the same doctor written three ways --
or all), for a period (today, this week, this month, or any dates):

    Date | Bill No | Customer | Short address | Sale / -SR amt | Cost amt | Profit amt | Profit %

Sale = what the bill sold less what came back on its returns; cost = the same scheme-adjusted
purchase cost as the rest of the Doctor Margin Report (core/doctor_margin.py, every medicine
type, injections too); profit % = profit / sale. Totals under the table, and per doctor.

Reads this PC's books only; nothing is written.
"""
from __future__ import annotations

from collections import OrderedDict
from typing import Any

from core import doctor_margin_report as dmr
from core.doctor_margin import NO_DOCTOR, doctor_margin

ReportError = dmr.ReportError
TITLE = "Selected doctors: bill-wise profit report"
COLUMNS = ["Date", "Bill No", "Customer", "Short address", "Sale / -SR amt", "Cost amt", "Profit amt", "Profit %"]
DOCTOR_COLUMNS = ["Doctor", "Bills", "Sale / -SR amt", "Cost amt", "Profit amt", "Profit %"]


def _r2(v: float) -> float:
    return float(f"{float(v or 0) + 0.0:.2f}")


def _pct(profit: float, sale: float) -> float:
    return _r2(profit / sale * 100) if sale else 0.0


def parse_filters(body: dict[str, Any]) -> dict[str, Any]:
    """From / To checked like the other reports; doctors = the names ticked ([] = all)."""
    base = dmr.parse_filters({"from": body.get("from"), "to": body.get("to")})
    raw = body.get("doctors")
    if isinstance(raw, str):
        raw = raw.split("|")
    doctors = []
    for d in raw or []:
        name = str(d or "").strip().upper()
        if name and name not in doctors:
            doctors.append(name)
    one = str(body.get("doctor") or "").strip().upper()
    if one and one not in doctors:
        doctors.append(one)
    return {"from": base["from"], "to": base["to"], "doctors": doctors}


def _addresses(conn, from_date: str, to_date: str) -> dict[str, str]:
    """Bill number (as printed) -> the customer's address, for the bills of the period."""
    from core.fy_serial import display_sales_bill_no

    where = " AND ".join(dmr._live(conn, "sales", "s", autosave=True) + ["s.bill_date >= ?", "s.bill_date < ?"])
    has_addr = "address" in dmr._cols(conn, "customers")
    addr = "COALESCE(c.address,'')" if has_addr else "''"
    out: dict[str, str] = {}
    for bno, a in conn.execute(
        f"SELECT COALESCE(s.bill_no,''), {addr} FROM sales s LEFT JOIN customers c ON c.id = s.customer_id "
        f"WHERE {where}", (from_date, dmr._next_day(to_date)),
    ):
        out[display_sales_bill_no(str(bno))] = " ".join(str(a or "").split())
    return out


def build_report(conn, body: dict[str, Any]) -> dict[str, Any]:
    """{ok, filters, rows, doctors, totals, flag_counts, bill_count} -- money in rupees, 2 decimals."""
    if dmr._is_online():
        raise ReportError(
            "The Doctor Margin Report reads this PC's own books; an Online store's bills are "
            "on the server (Sales History on the web)."
        )
    f = parse_filters(body)
    sales, purchases = dmr.read_books(conn, f["from"], f["to"])
    lines: list[dict[str, Any]] = []
    for mode in ("regular", "injection"):        # the two halves are every medicine type
        lines += doctor_margin(sales, purchases, {"mode": mode, "pct": 0, "types": None, "doctor": ""})["rows"]
    want = set(f["doctors"])
    if want:
        lines = [r for r in lines if str(r.get("doctor") or NO_DOCTOR).strip().upper() in want]

    order = {(s["bill_no"], s["date"]): i for i, s in enumerate(sales)}
    bills: "OrderedDict[tuple, dict[str, Any]]" = OrderedDict()
    flags = {k: 0 for k in dmr.FLAG_LABELS}
    for r in sorted(lines, key=lambda r: order.get((r["bill_no"], r["date"]), 0)):
        key = (r["bill_no"], r["date"])
        b = bills.get(key)
        if b is None:
            b = bills[key] = {"date": r["date"], "bill_no": r["bill_no"], "patient": r["patient"],
                              "doctor": str(r.get("doctor") or NO_DOCTOR), "sale": 0.0, "cost": 0.0, "flags": set()}
        b["sale"] += float(r.get("amount") or 0)
        b["cost"] += float(r.get("cost") or 0)
        for fl in r.get("flags") or []:
            b["flags"].add(fl)
            if fl in flags:
                flags[fl] += 1

    addresses = _addresses(conn, f["from"], f["to"]) if bills else {}
    rows, per_doc = [], OrderedDict()
    for b in bills.values():
        sale, cost = _r2(b["sale"]), _r2(b["cost"])
        profit = _r2(sale - cost)
        rows.append({"date": b["date"], "bill_no": b["bill_no"], "patient": b["patient"], "doctor": b["doctor"],
                     "address": addresses.get(b["bill_no"], ""), "sale": sale, "cost": cost, "profit": profit,
                     "pct": _pct(profit, sale), "flags": sorted(b["flags"])})
        d = per_doc.setdefault(b["doctor"], {"doctor": b["doctor"], "bills": 0, "sale": 0.0, "cost": 0.0})
        d["bills"] += 1
        d["sale"] += sale
        d["cost"] += cost
    doctors = []
    for d in sorted(per_doc.values(), key=lambda d: d["doctor"]):
        sale, cost = _r2(d["sale"]), _r2(d["cost"])
        doctors.append({**d, "sale": sale, "cost": cost, "profit": _r2(sale - cost), "pct": _pct(sale - cost, sale)})
    sale = _r2(sum(r["sale"] for r in rows))
    cost = _r2(sum(r["cost"] for r in rows))
    return {
        "ok": True,
        "mode": "bills",
        "filters": f,
        "rows": rows,
        "doctors": doctors,
        "totals": {"bills": len(rows), "sale": sale, "cost": cost, "profit": _r2(sale - cost), "pct": _pct(sale - cost, sale)},
        "flag_counts": flags,
        "bill_count": len(rows),
        "method": dmr.METHOD,
        "method_note": dmr.METHOD_NOTE,
    }


def _shop_lines(conn) -> list[tuple[str, str]]:
    """The shop's name, address, mobile and e-mail at the top, as the other software prints."""
    try:
        from core.pharmacy_profile_io import fetch_pharmacy_profile_row

        p = fetch_pharmacy_profile_row(conn) or ()
    except Exception:
        p = ()
    out = []
    for label, i in (("Shop", 1), ("Address", 2), ("Mobile", 3), ("Email", 4)):
        v = " ".join(str(p[i] or "").split()) if len(p) > i else ""
        if v:
            out.append((label, v))
    return out


def export_report(conn, body: dict[str, Any]) -> dict[str, Any]:
    """The report as Excel / CSV / PDF, or printed (the Doctor Margin Report's own ways out)."""
    rep = build_report(conn, body)
    if not rep["rows"]:
        return {"ok": False, "error": "No bills of these doctors in these dates."}
    f = rep["filters"]
    t = rep["totals"]
    rows = [[dmr._fmt_date(r["date"]), r["bill_no"], r["patient"], r["address"], r["sale"], r["cost"],
             r["profit"], r["pct"]] for r in rep["rows"]]
    tot = ["Totals", "", "", "", t["sale"], t["cost"], t["profit"], t["pct"]]
    drows = [[d["doctor"], d["bills"], d["sale"], d["cost"], d["profit"], d["pct"]] for d in rep["doctors"]]
    drows.append(["TOTAL", t["bills"], t["sale"], t["cost"], t["profit"], t["pct"]])
    heads = _shop_lines(conn) + [
        ("Doctors", ", ".join(f["doctors"]) if f["doctors"] else "All doctors"),
        ("Date range", f"{dmr._fmt_date(f['from'])} to {dmr._fmt_date(f['to'])}"),
        ("Bills", str(t["bills"])),
        ("Purchase cost method", f"{dmr.METHOD}. {dmr.METHOD_NOTE}"),
    ]
    base = f"doctor_bill_profit_{f['from']}_{f['to']}"
    return dmr.deliver(body, TITLE, heads, COLUMNS, rows, tot, DOCTOR_COLUMNS, drows, base, t)
