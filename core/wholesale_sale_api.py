"""/api/wholesale/sale/* -- the wholesale sale screen's engine endpoints (core/wholesale_sale.py).

GET  /api/wholesale/sale/parties?q=        customers for the party box, medicals first
GET  /api/wholesale/sale/party?customer_id= the party: GSTIN, DL, state, tier, credit, due
GET  /api/wholesale/sale/load?sale_id=      a saved tax invoice, for edit / view
GET  /api/wholesale/sale/preview?sale_id=   the tax invoice HTML (A4 / A5 by print slot 1)
POST /api/wholesale/sale/line               {medicine_id, rate_tier, qty?} -> a new line
POST /api/wholesale/sale/calc               the live totals, place of supply, credit warning
POST /api/wholesale/sale/save               save / re-save (same as /api/sales/save with
                                            invoice_mode 'wholesale')

Each answer is (status, json). Saving and printing also go through the ordinary
/api/sales/save, /api/sales/print and /api/sales/bill/pdf, which know a wholesale bill.
"""
from __future__ import annotations

from typing import Any, Optional

PREFIX = "/api/wholesale/sale/"


def _first(qs: dict, key: str) -> str:
    return str((qs.get(key) or [""])[0] or "").strip()


def handle_get(path: str, qs: dict, conn) -> Optional[tuple[int, dict[str, Any]]]:
    if not path.startswith(PREFIX):
        return None
    if conn is None:
        return 503, {"ok": False, "error": "Database not open"}
    from core import wholesale_sale as ws

    name = path[len(PREFIX):]
    try:
        if name == "parties":
            return 200, {"ok": True, "parties": ws.list_parties(conn, _first(qs, "q"))}
        if name == "party":
            p = ws.party(conn, _first(qs, "customer_id"))
            if not p:
                return 404, {"ok": False, "error": "Party not found."}
            shop = ws.shop_profile(conn)
            pos = ws.place_of_supply(p["gstin"], p["state_code"], shop["state"])
            return 200, {
                "ok": True, "party": p, "shop_state": shop["state"],
                "place_of_supply": pos, "place_of_supply_name": ws.state_name(pos),
                "intra_state": ws.is_intra_state(pos, shop["state"]),
            }
        if name == "load":
            res = ws.load_sale(conn, _first(qs, "sale_id"))
            return (200 if res.get("ok") else 400), res
        if name == "preview":
            from bill_templates.wholesale_invoice import render_wholesale_invoice_html
            from core.bill_config import get_print_slot_settings, load_bill_print_settings

            sid = _first(qs, "sale_id")
            paper = _first(qs, "paper").upper() or ws.invoice_paper(
                get_print_slot_settings(load_bill_print_settings(), 1))
            inv = ws.invoice_context(conn, sid)
            return 200, {"ok": True, "bill_no": inv["bill_no"], "template": "wholesale",
                         "html": render_wholesale_invoice_html(inv, paper=paper)}
    except Exception as exc:
        return 400, {"ok": False, "error": str(exc)}
    return 404, {"ok": False, "error": f"Unknown wholesale endpoint {path}"}


def handle_post(path: str, body: dict, conn) -> Optional[tuple[int, dict[str, Any]]]:
    if not path.startswith(PREFIX):
        return None
    if conn is None:
        return 503, {"ok": False, "error": "Database not open"}
    from core import wholesale_sale as ws

    name = path[len(PREFIX):]
    try:
        if name == "line":
            res = ws.build_line(conn, body)
        elif name == "calc":
            res = ws.calc_sale(conn, body)
            for line in res.get("lines") or []:
                line.pop("_med", None)
        elif name == "save":
            res = ws.save_sale(conn, {**body, "invoice_mode": ws.WHOLESALE})
        else:
            return 404, {"ok": False, "error": f"Unknown wholesale endpoint {path}"}
    except Exception as exc:
        return 400, {"ok": False, "error": str(exc)}
    return (200 if res.get("ok") else 400), res
