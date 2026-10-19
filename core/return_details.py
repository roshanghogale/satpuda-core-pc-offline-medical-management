"""A saved return as its View details dialog and its slip show it (owner, 18 Oct 2026).

"The purchase return View details looks bad: a header (return no, date, supplier, original
purchase), the medicines as a proper table, the totals, and a Credit part saying how the credit
stands: what is still open and the purchases it was settled in, or settled in cash/online.
Sales returns the same, with a PDF / Print slip like the purchase return's."

- credit_status:         how a return's credit stands (open credit left, the bills it went
                         into, money handed back for it), from the same ledger the Sales /
                         Purchase popup reads (core/return_credit.open_returns).
- sales_return_details:  a sales return's header, lines, totals and credit status.
- purchase_return_more:  the same extras for a purchase return (its header and lines come
                         from desktop_returns_service.get_purchase_return_details).
- sales_return_pdf:      the sales return slip, saved (and printed when asked).

Offline / offline-first stores only: an Online store keeps its returns on the server.
"""
from __future__ import annotations

import os
import sqlite3
from typing import Any

from core.bill_clearing import _kind, clean_allocations, party_ledger, r2


def _has_column(conn: sqlite3.Connection, table: str, col: str) -> bool:
    try:
        return any(r[1] == col for r in conn.execute(f"PRAGMA table_info({table})").fetchall())
    except sqlite3.Error:
        return False


def _show_sale_no(no: Any) -> str:
    from core.fy_serial import display_sales_bill_no

    return display_sales_bill_no(str(no or ""))


def _show_purchase_no(no: Any) -> str:
    from core.fy_serial import display_purchase_no

    return display_purchase_no(str(no or ""))


def _paid_out(conn: sqlite3.Connection, kind: str, party_id: int, return_no: str) -> tuple[float, str]:
    """Money handed back for this return (negative payments carrying its number) and how."""
    if not return_no or party_id <= 0:
        return 0.0, ""
    if kind == "customer":
        table, party_col, ref_col, mode_col = "customer_payments", "customer_id", "reference_no", "payment_mode"
    else:
        table, party_col, ref_col, mode_col = "supplier_payments", "supplier_id", "reference", "mode"
    if not _has_column(conn, table, ref_col):
        return 0.0, ""
    live = "AND COALESCE(deleted,0)=0" if _has_column(conn, table, "deleted") else ""
    mode_sql = mode_col if _has_column(conn, table, mode_col) else "''"
    rows = conn.execute(
        f"SELECT -amount, COALESCE({mode_sql},'') FROM {table} "
        f"WHERE {party_col}=? AND TRIM(COALESCE({ref_col},''))=? AND amount < 0 {live} ORDER BY id",
        (int(party_id), str(return_no).strip()),
    ).fetchall()
    total = r2(sum(float(a or 0) for a, _m in rows))
    modes = {str(m or "").strip().lower() for _a, m in rows if str(m or "").strip()}
    mode = "online" if modes == {"online"} else ("cash" if modes else "")
    if len(modes) > 1:
        mode = "cash/online"
    return total, mode


def credit_status(conn: sqlite3.Connection, kind: str, return_id: int) -> dict[str, Any]:
    """{settle_mode, refund, party_id, settled: [{bill_id, bill_no, supplier_bill_no, date,
    amount, how}], settled_total, paid_out, paid_out_mode, left}.

    settled: the bills this return's credit went into, as the clearing rule spends it --
    how = "chosen" (ticked in the bill's popup: a Return row on that bill) or "oldest" (it
    paid a due bill on its own, oldest first). left: the credit the next bill is offered,
    exactly what the popup offers (0 for a return settled in cash / online).
    """
    from core.return_credit import open_returns

    k = _kind(kind)
    rid = int(return_id or 0)
    empty = {"settle_mode": "ledger", "refund": 0.0, "party_id": 0, "settled": [],
             "settled_total": 0.0, "paid_out": 0.0, "paid_out_mode": "", "left": 0.0}
    if rid <= 0:
        return empty
    table = "sales_returns" if k == "customer" else "purchase_returns"
    settle = "r.settle_mode" if _has_column(conn, table, "settle_mode") else "NULL"
    alloc = "r.allocations" if _has_column(conn, table, "allocations") else "NULL"
    if k == "customer":
        row = conn.execute(
            f"SELECT COALESCE(r.return_no,''), COALESCE(r.refund_amount,0), {settle}, {alloc}, "
            "COALESCE(NULLIF(r.customer_id,0), s.customer_id, 0) "
            "FROM sales_returns r LEFT JOIN sales s ON s.id = r.sale_id WHERE r.id=?",
            (rid,),
        ).fetchone()
    else:
        row = conn.execute(
            f"SELECT COALESCE(r.return_no,''), COALESCE(r.refund_amount,0), {settle}, {alloc}, "
            "COALESCE(NULLIF(r.supplier_id,0), p.supplier_id, 0) "
            "FROM purchase_returns r LEFT JOIN purchases p ON p.id = r.purchase_id WHERE r.id=?",
            (rid,),
        ).fetchone()
    if not row:
        return empty
    return_no, refund, mode, allocations, pid = row
    mode = str(mode or "ledger").strip().lower() or "ledger"
    pid = int(pid or 0)
    out = dict(empty, settle_mode=mode, refund=r2(refund), party_id=pid)

    by_bill: dict[int, dict] = {}
    if pid > 0:
        led = party_ledger(conn, k, pid)
        bills = {int(b["id"]): b for b in led.get("bills") or []}
        for a in (led.get("result") or {}).get("applied") or []:
            if a.get("credit_kind") != "return" or int(a.get("credit_id") or 0) != rid:
                continue
            bid = int(a["bill_id"])
            b = bills.get(bid) or {}
            hit = by_bill.get(bid)
            if hit is None:
                if k == "customer":
                    no, sup_no = _show_sale_no(b.get("bill_no")), ""
                else:
                    no = _show_purchase_no(b.get("purchase_no") or b.get("bill_no"))
                    sup_no = str(b.get("bill_no") or "") if b.get("bill_no") != b.get("purchase_no") else ""
                hit = by_bill[bid] = {"bill_id": bid, "bill_no": no, "supplier_bill_no": sup_no,
                                      "date": str(b.get("date") or "")[:10], "amount": 0.0,
                                      "how": a.get("how") or "oldest"}
            hit["amount"] = r2(hit["amount"] + float(a.get("amount") or 0))
            if a.get("how") == "chosen":
                hit["how"] = "chosen"
    else:
        # No party to read a ledger for: the bills it was ticked in, as saved.
        for a in clean_allocations(allocations):
            bid = int(a["bill_id"]) if str(a["bill_id"]).lstrip("-").isdigit() else 0
            prev = by_bill.get(bid)
            if prev:
                prev["amount"] = r2(prev["amount"] + a["amount"])
                continue
            by_bill[bid] = {"bill_id": bid, "bill_no": str(a.get("bill_no") or ""),
                            "supplier_bill_no": "", "date": "", "amount": r2(a["amount"]),
                            "how": "chosen"}
    settled = sorted((s for s in by_bill.values() if s["amount"] > 0.009),
                     key=lambda s: (s["date"], s["bill_id"]))
    out["settled"] = settled
    out["settled_total"] = r2(sum(s["amount"] for s in settled))
    out["paid_out"], out["paid_out_mode"] = _paid_out(conn, k, pid, str(return_no))
    if mode not in ("cash", "online") and pid > 0:
        for r in open_returns(conn, k, pid):
            if int(r["return_id"]) == rid:
                out["left"] = r2(r["left"])
                break
    return out


def _totals(items: list[dict], refund: float) -> dict[str, float]:
    sub = r2(sum(float(it.get("amount") or 0) for it in items))
    refund = r2(refund)
    return {"subtotal": sub, "discount_amount": r2(max(0.0, sub - refund)), "refund_amount": refund}


def purchase_return_more(conn: sqlite3.Connection, return_id: int) -> dict[str, Any]:
    """What View details shows beyond get_purchase_return_details: the purchase entry no,
    the supplier's bill no and date, each line's expiry, the totals and the credit status."""
    rid = int(return_id or 0)
    row = conn.execute(
        "SELECT COALESCE(p.purchase_no,''), COALESCE(p.bill_number,''), COALESCE(p.purchase_date,''), "
        "COALESCE(pr.discount,0), COALESCE(pr.refund_amount,0) "
        "FROM purchase_returns pr LEFT JOIN purchases p ON p.id = pr.purchase_id WHERE pr.id=?",
        (rid,),
    ).fetchone()
    if not row:
        return {}
    pno, bill_number, pdate, disc_pct, refund = row
    expiry = {
        int(i): str(e or "")
        for i, e in conn.execute(
            "SELECT pri.id, COALESCE(m.expiry_date,'') FROM purchase_return_items pri "
            "LEFT JOIN medicines m ON m.id = pri.medicine_id WHERE pri.return_id=? ORDER BY pri.id",
            (rid,),
        ).fetchall()
    }
    return {
        "purchase_entry_no": _show_purchase_no(pno),
        "supplier_bill_no": str(bill_number or ""),
        "purchase_date": str(pdate or "")[:10],
        "discount_pct": float(disc_pct or 0),
        "expiry_by_line": list(expiry.values()),
        "refund_amount": r2(refund),
        "credit": credit_status(conn, "supplier", rid),
    }


def sales_return_details(conn: sqlite3.Connection, return_id: int) -> dict[str, Any]:
    """Header + lines + totals + credit of a saved sales return, for View details."""
    try:
        rid = int(return_id or 0)
    except (TypeError, ValueError):
        rid = 0
    if rid <= 0:
        return {"ok": False, "error": "return_id required"}
    try:
        from core.sync_prefs import is_online_mode

        if is_online_mode():
            return {"ok": False, "error": "Sales return details are read from this PC's own "
                                          "data; an Online shop keeps them on the server."}
    except Exception:
        pass
    from core.desktop_returns_service import _ensure_return_tables

    _ensure_return_tables(conn)
    settle = "sr.settle_mode" if _has_column(conn, "sales_returns", "settle_mode") else "NULL"
    head = conn.execute(
        f"""
        SELECT sr.return_no, sr.return_date, COALESCE(s.bill_no, ''), COALESCE(s.bill_date, ''),
               COALESCE(c.name, ''), COALESCE(c.phone, ''), COALESCE(c.address, ''),
               COALESCE(sr.refund_amount, 0), COALESCE(sr.discount, 0), COALESCE(sr.reason, ''),
               COALESCE(sr.sale_id, 0), COALESCE(NULLIF(sr.customer_id, 0), s.customer_id, 0), {settle}
        FROM sales_returns sr
        LEFT JOIN sales s ON s.id = sr.sale_id
        LEFT JOIN customers c ON c.id = COALESCE(NULLIF(sr.customer_id, 0), s.customer_id)
        WHERE sr.id = ? AND COALESCE(sr.deleted, 0) = 0
        """,
        (rid,),
    ).fetchone()
    if not head:
        return {"ok": False, "error": "Return not found."}
    (rno, rdate, bill_no, bill_date, customer, phone, address, refund, disc_pct, reason,
     sale_id, customer_id, mode) = head
    items = [
        {"medicine_id": int(mid or 0), "name": str(name or ""), "batch": str(batch or ""),
         "expiry": str(expiry or ""), "mrp": float(mrp or 0), "qty": float(qty or 0),
         "rate": float(rate or 0), "amount": float(amount or 0)}
        for mid, name, batch, expiry, mrp, qty, rate, amount in conn.execute(
            """
            SELECT sri.medicine_id, COALESCE(m.name, ''), COALESCE(m.batch_no, ''),
                   COALESCE(m.expiry_date, ''), COALESCE(m.mrp, 0),
                   sri.qty, sri.rate, sri.amount
            FROM sales_return_items sri
            LEFT JOIN medicines m ON m.id = sri.medicine_id
            WHERE sri.return_id = ?
            ORDER BY sri.id
            """,
            (rid,),
        ).fetchall()
    ]
    in_packs = _in_packs(conn, int(sale_id or 0), items)
    out: dict[str, Any] = {
        "ok": True,
        "kind": "sales",
        "id": rid,
        "return_no": str(rno or ""),
        "return_date": str(rdate or "")[:10],
        "bill_no": _show_sale_no(bill_no),
        "bill_date": str(bill_date or "")[:10],
        "sale_id": int(sale_id or 0),
        "customer_id": int(customer_id or 0),
        "customer": str(customer or ""),
        "phone": str(phone or ""),
        "address": str(address or ""),
        "reason": str(reason or ""),
        "settle_mode": str(mode or "ledger").strip().lower() or "ledger",
        "discount_pct": float(disc_pct or 0),
        "items": items,
        # "strip" when the bill was a wholesale tax invoice: items' qty in packs, rate per pack
        "qty_unit": "strip" if in_packs else "",
    }
    out.update(_totals(items, float(refund or 0)))
    out["credit"] = credit_status(conn, "customer", rid)
    return out


def _in_packs(conn: sqlite3.Connection, sale_id: int, items: list[dict]) -> bool:
    """A return of a wholesale tax invoice is shown in packs, as the invoice sold it (owner,
    18 Oct 2026: wholesale works in strips): each item's qty becomes packs (``units`` keeps
    the stored tablets, ``units_per_pack`` the size) and its rate the amount per pack. What
    is stored is not touched. False (items as stored) for a retail bill."""
    if sale_id <= 0 or not items:
        return False
    try:
        from core.wholesale_sale import is_wholesale_sale, units_per_pack
    except Exception:
        return False
    if not is_wholesale_sale(conn, sale_id):
        return False
    ids = sorted({int(it["medicine_id"]) for it in items if it.get("medicine_id")})
    size = {int(mid): units_per_pack(mtype, unit) for mid, mtype, unit in conn.execute(
        f"SELECT id, COALESCE(type,''), COALESCE(unit,'1') FROM medicines "
        f"WHERE id IN ({','.join('?' * len(ids))})", ids).fetchall()} if ids else {}
    for it in items:
        ups = size.get(int(it.get("medicine_id") or 0), 1)
        units = float(it.get("qty") or 0)
        packs = round(units / ups, 2)
        it["units"] = units
        it["units_per_pack"] = ups
        it["qty"] = packs
        if packs > 0:
            it["rate"] = round(float(it.get("amount") or 0) / packs, 2)
    return True


def print_pdf(path: str) -> str:
    """The saved slip to the shop's printer: "" when sent, else why not (A6 landscape sheet)."""
    if not path or not path.lower().endswith(".pdf") or not os.path.isfile(path):
        return "The PDF could not be made, so nothing was printed. Install Edge or Chrome."
    from core.desktop_export_service import print_pdf_on_printer

    return print_pdf_on_printer(path, landscape=True)


def finish_pdf(result: dict[str, Any], print_it: bool) -> dict[str, Any]:
    """Remember the saved file (the File saved box may open it) and print it when asked."""
    if not result.get("ok"):
        return result
    try:
        from core import saved_files

        for p in (result.get("pdf_path"), result.get("html_path")):
            if p:
                saved_files.remember(str(p))
    except Exception:
        pass
    if print_it:
        problem = print_pdf(str(result.get("pdf_path") or ""))
        result["printed"] = not problem
        if problem:
            result["print_error"] = problem
    return result


def sales_return_pdf(conn: sqlite3.Connection, return_id: int, *, print_it: bool = False) -> dict[str, Any]:
    """Save the sales return slip as PDF (<save folder>\\Returns), print it if asked."""
    d = sales_return_details(conn, return_id)
    if not d.get("ok"):
        return {"ok": False, "error": d.get("error") or "Return not found."}
    from core.document_output import _sales_return_html_from, save_customer_document
    from core.pharmacy_profile_io import fetch_pharmacy_profile_row

    try:
        profile = fetch_pharmacy_profile_row(conn)
    except Exception:
        profile = None
    html = _sales_return_html_from(profile, d, d["items"])
    pdf_path, html_path = save_customer_document(html, d["return_no"] or f"SR{d['id']}")
    target = pdf_path or html_path
    if not target:
        return {"ok": False, "error": "PDF could not be created. Install Edge or Chrome."}
    return finish_pdf({"ok": True, "pdf_path": pdf_path or "", "html_path": html_path or "",
                       "path": target, "return_no": d["return_no"]}, print_it)
