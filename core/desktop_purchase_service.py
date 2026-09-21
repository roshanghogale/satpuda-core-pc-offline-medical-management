"""Desktop API wrappers for Purchase — classic purchase_service + PurchaseCalculator only."""
from __future__ import annotations

import threading
import time
from datetime import date, datetime
from typing import Any, Optional


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        if v is None or v == "":
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def _safe_int(v: Any, default: int = 0) -> int:
    try:
        if v is None or v == "":
            return default
        return int(float(v))
    except (TypeError, ValueError):
        return default


def _parse_date(raw: Any) -> str:
    if isinstance(raw, date) and not isinstance(raw, datetime):
        return raw.isoformat()
    s = str(raw or "").strip()
    if not s:
        return date.today().isoformat()
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(s[:10], fmt).date().isoformat()
        except ValueError:
            continue
    return date.today().isoformat()


def _normalize_items(raw_items: list) -> list[dict[str, Any]]:
    from core.layout_config import is_strip_count_type, parse_tablets_per_stripe

    out = []
    for it in raw_items or []:
        if not isinstance(it, dict):
            continue
        name = str(it.get("name") or it.get("medicine") or "").strip()
        if not name:
            continue
        med_type = str(it.get("type") or "Tablet").strip()
        qty = _safe_float(it.get("qty"))
        free_qty = _safe_float(it.get("free_qty", it.get("free")))
        rate = _safe_float(it.get("rate"))
        mrp = _safe_float(it.get("mrp"))
        gst_pct = _safe_float(it.get("gst_pct", it.get("gstPct")))
        discount_pct = _safe_float(it.get("discount_pct", it.get("discPct")))
        unit = str(it.get("unit") or it.get("pack") or it.get("quantity_value") or "1")
        item: dict[str, Any] = {
            "medicine_id": _safe_int(it.get("medicine_id") or it.get("id")) or None,
            "name": name.upper(),
            "type": med_type,
            "batch": str(it.get("batch") or "").strip().upper(),
            "expiry": str(it.get("expiry") or "").strip(),
            "qty": qty,
            "free_qty": free_qty,
            "rate": rate,
            "mrp": mrp,
            "gst_pct": gst_pct,
            "discount_pct": discount_pct,
            "hsn_code": str(it.get("hsn_code") or it.get("hsn") or ""),
            "manufacturer": str(it.get("manufacturer") or ""),
            "schedule": str(it.get("schedule") or ""),
            "content_drug": str(it.get("content_drug") or it.get("content") or ""),
            "unit": unit,
        }
        if is_strip_count_type(med_type):
            tps = _safe_int(it.get("tablets_per_stripe"), 0)
            if tps <= 0:
                try:
                    tps = parse_tablets_per_stripe(unit)
                except Exception:
                    tps = 1
            tps = max(1, tps)
            item["tablets_per_stripe"] = tps
            item["total_tablets"] = qty * tps
            item["free_tablets"] = free_qty * tps
        else:
            # A gel, powder, syrup or ointment is counted in bottles and tubes,
            # never in tablets. An import that hands us a pack of "200ML" was
            # letting 200 through as a strip count, so one bottle of gel went
            # onto the shelf as two hundred -- and the whole bill's stock with
            # it. Whatever arrives, a non-strip type carries exactly one.
            item["tablets_per_stripe"] = 1
            item["quantity_value"] = unit
        if it.get("_preserve_line_totals"):
            item["_preserve_line_totals"] = True
            item["taxable"] = _safe_float(it.get("taxable"))
            item["gst_amt"] = _safe_float(it.get("gst_amt", it.get("gstAmt")))
            item["item_amount"] = _safe_float(it.get("item_amount", it.get("amount")))
            item["amount"] = item["item_amount"]
        out.append(item)
    return out


def lookup_medicine(conn, body: dict[str, Any]) -> dict[str, Any]:
    from core.purchase_service import lookup_medicine_details, expiry_to_display

    name = str(body.get("name") or body.get("q") or "").strip()
    if not name:
        return {"ok": False, "error": "Medicine name required."}
    details = lookup_medicine_details(conn, name) or {}
    exp = details.get("expiry_date") or ""
    try:
        exp_disp = expiry_to_display(exp) if exp and "-" in str(exp) else str(exp or "")
    except Exception:
        exp_disp = str(exp or "")
    return {
        "ok": True,
        "details": {
            "name": name,
            "type": details.get("type") or "",
            "manufacturer": details.get("manufacturer") or "",
            "hsn_code": details.get("hsn_code") or "",
            "gst_percent": _safe_float(details.get("gst_percent")),
            "mrp": _safe_float(details.get("mrp")),
            "rate": _safe_float(details.get("rate")),
            "schedule": details.get("schedule") or "",
            "content_drug": details.get("content_drug") or "",
            "batch_no": details.get("batch_no") or "",
            "expiry": exp_disp,
            "unit": details.get("unit") or "1",
            "discount_pct": _safe_float(details.get("discount_pct")),
        },
    }


def calc_purchase(conn, body: dict[str, Any]) -> dict[str, Any]:
    from core.purchase_calculator import PurchaseCalculator
    from core.purchase_service import get_supplier_due

    items = _normalize_items(body.get("items") or [])
    overall = _safe_float(body.get("overall_discount", body.get("discount_rs")))
    if overall <= 0 and _safe_float(body.get("discount_pct")) > 0 and items:
        # rough base for pct→rs preview
        base = sum(
            _safe_float(i.get("qty")) * _safe_float(i.get("rate")) for i in items
        )
        overall = round(base * _safe_float(body.get("discount_pct")) / 100.0, 2)

    rounding = body.get("rounding")
    auto = bool(body.get("auto_rounding", True))
    round_val = _safe_float(rounding) if rounding not in (None, "") else 0.0
    if auto and "rounding" not in body:
        round_val = 0.0  # PurchaseCalculator auto_rounds when 0

    prev_due = _safe_float(body.get("previous_due"))
    prev_credit = _safe_float(body.get("previous_credit"))
    supplier_name = str(body.get("supplier_name") or "").strip()
    # The live totals preview must not go and fetch the supplier's balance.
    #
    # It used to, on every keystroke, with force_refresh=True whenever Online --
    # which re-pulls the whole supplier table past its cache and then reads up to
    # 5000 purchases and 5000 payments to run a FIFO allocation, all over the
    # network with a 60s timeout. That is seconds per edit, and it is why the
    # totals lagged behind the row the shop had just changed. Offline it is
    # cheaper but still a write: a settled supplier falls into a full
    # recalculation that UPDATEs every one of their bills and commits.
    #
    # The balance does not depend on what is being typed. The page resolves it
    # when the supplier is picked and sends it back as previous_due; the save
    # path recomputes it properly. So the preview only asks when the caller says
    # it needs to.
    want_party_due = not bool(body.get("skip_party_due"))
    if supplier_name and want_party_due:
        try:
            force = False
            try:
                from core.sync_prefs import is_online_mode

                force = bool(is_online_mode()) and bool(
                    body.get("force_supplier_due", True)
                )
            except Exception:
                force = False
            d, c = get_supplier_due(
                conn, supplier_name, force_refresh=force
            )
            prev_due = _safe_float(d)
            prev_credit = _safe_float(c)
        except Exception:
            pass

    method = str(
        body.get("gst_calc_method") or "discount_before_gst"
    ).strip() or "discount_before_gst"

    import_mode = bool(body.get("import_bill_mode"))
    inv = body.get("import_invoice_summary")
    if not isinstance(inv, dict):
        inv = {}

    # An imported bill is normally pinned to the supplier's printed footer, so
    # the app agrees with the paper. But that pinning also overrode the GST
    # method and the slab split, so the GST Method dropdown did nothing at all
    # on an imported bill, and Recalculate could not correct a misread figure.
    #
    # Recalculate, and changing the method, both mean "work it out from the rows
    # as if I had typed them" -- so they drop the pinning and the bill behaves
    # exactly like a manually entered one.
    if body.get("recalculate") or body.get("ignore_import_totals"):
        import_mode = False
        inv = {}

    if import_mode and inv:
        try:
            from core.purchase_invoice_engine import (
                reconcile_purchase_items_to_footer_slabs,
            )

            reconcile_purchase_items_to_footer_slabs(items, inv)
        except Exception:
            pass
        # Only adopt the bill's own method when the caller has not chosen one.
        # Overwriting it unconditionally is what made the dropdown inert.
        import_method = inv.get("gst_calc_method")
        if (
            not str(body.get("gst_calc_method") or "").strip()
            and import_method in ("discount_before_gst", "discount_after_gst")
        ):
            method = import_method
        inv_disc = _safe_float(inv.get("parsed_total_discount"))
        if not inv_disc:
            cash = _safe_float(inv.get("cash_discount"))
            prod = _safe_float(inv.get("product_discount"))
            if cash > 0 and prod > 0 and abs(cash - prod) <= 0.05:
                inv_disc = cash
            else:
                inv_disc = round(cash + prod, 2)
        if inv_disc and overall <= 0:
            overall = inv_disc

    cash_paid = _safe_float(body.get("cash_paid"))
    online_paid = _safe_float(body.get("online_paid"))
    expenditure = _safe_float(body.get("expenditure", body.get("delivery")))
    amount_paid = cash_paid + online_paid

    calc = PurchaseCalculator(
        items=items,
        overall_discount=overall,
        rounding=round_val if not auto or "rounding" in body else 0.0,
        previous_due=prev_due,
        previous_credit=prev_credit,
        cash_paid=cash_paid,
        online_paid=online_paid,
        expenditure=expenditure,
        gst_calc_method=method,
    ).calculate()

    if import_mode and inv:
        try:
            from core.purchase_invoice_engine import reconcile_import_slab_breakdown

            use_footer = bool(
                inv.get("use_footer_totals")
                or (
                    inv.get("footer_gst_authoritative")
                    and _safe_float(inv.get("invoice_total")) > 0
                    and _safe_float(inv.get("total_cgst"))
                    + _safe_float(inv.get("total_sgst"))
                    > 0
                )
            )
            if use_footer or inv.get("footer_gst_slabs"):
                if use_footer:
                    footer_cgst = round(_safe_float(inv.get("total_cgst")), 2)
                    footer_sgst = round(_safe_float(inv.get("total_sgst")), 2)
                    inv_net = round(_safe_float(inv.get("invoice_total")), 2)
                    inv_round = round(_safe_float(inv.get("round_off")), 2)
                    footer_gst = round(footer_cgst + footer_sgst, 2)
                    if inv_net > 0 and footer_gst > 0:
                        supplier_gross = _safe_float(inv.get("gross_amount"))
                        calc["cgst"] = footer_cgst
                        calc["sgst"] = footer_sgst
                        calc["total_gst"] = footer_gst
                        calc["pre_round_total"] = round(inv_net - inv_round, 2)
                        calc["total_amount"] = inv_net
                        calc["rounding"] = inv_round if inv_round else round(
                            inv_net - calc["pre_round_total"], 2
                        )
                        if supplier_gross > _safe_float(calc.get("gross_subtotal")):
                            calc["supplier_gross"] = supplier_gross
                        calc["use_footer_totals"] = True
                        final_amount = round(inv_net + expenditure, 2)
                        need_to_pay = round(
                            final_amount + prev_due - prev_credit, 2
                        )
                        due = round(max(0.0, need_to_pay - amount_paid), 2)
                        if due < 0.01:
                            due = 0.0
                        overpay = round(
                            amount_paid - (final_amount + prev_due), 2,
                        )
                        credit = overpay if overpay > 0.01 else 0.0
                        calc["final_amount"] = final_amount
                        calc["need_to_pay"] = need_to_pay
                        calc["due"] = due
                        calc["current_credit"] = credit
                        calc["credit_amount"] = credit
                        calc["total_due"] = due
                        calc["amount_paid"] = amount_paid
                        calc["bill_cleared"] = 1 if due < 0.01 else 0
                try:
                    calc = reconcile_import_slab_breakdown(calc, inv)
                except Exception:
                    pass
        except Exception:
            pass

    out_items = calc.get("items") or items
    _add_line_figures(out_items)
    return {
        "ok": True,
        "calc": calc,
        "items": out_items,
        "previous_due": prev_due,
        "previous_credit": prev_credit,
        "slab_breakdown": calc.get("slab_breakdown") or [],
        "gst_calc_method": method,
        "import_bill_mode": import_mode,
    }


def _add_line_figures(items) -> None:
    """Add each line as the supplier's bill prints it.

    taxable/gst_amt/item_amount carry the BILL-level discount spread across the
    rows -- 5857.40 becomes 5564.53 once a 5% overall discount is apportioned.
    That is right for the totals and for GST, and the summary uses it. But no
    supplier bill prints its lines that way: the AMOUNT column is qty x rate,
    and the discount is applied once at the bottom. Showing the apportioned
    figure in the table meant not one row could be ticked off against the paper,
    even though every total agreed.

    So each row also carries what the bill shows, and the table displays that.
    Nothing here changes what is saved or what the summary computes.
    """
    for it in items or []:
        if not isinstance(it, dict):
            continue
        try:
            qty = float(it.get("qty") or 0)
            rate = float(it.get("rate") or 0)
            line = round(qty * rate, 2)
            disc_pct = float(it.get("discount_pct") or 0)
            if disc_pct:
                line = round(line * (1.0 - disc_pct / 100.0), 2)
            gst_pct = float(it.get("gst_pct") or 0)
            it["line_amount"] = line
            it["line_taxable"] = line
            it["line_gst_amt"] = round(line * gst_pct / 100.0, 2)
        except (TypeError, ValueError):
            continue


def save_purchase_bill(conn, body: dict[str, Any]) -> dict[str, Any]:
    from core.purchase_calculator import PurchaseCalculator
    from core.purchase_service import (
        finalize_autosave_purchase,
        get_or_create_supplier,
        get_supplier_due,
        save_purchase,
        update_purchase,
    )
    try:
        from core.sync_v3.repositories.purchase_repository import (
            maybe_finalize_autosave,
            maybe_save_purchase,
        )
    except Exception:
        maybe_finalize_autosave = finalize_autosave_purchase
        maybe_save_purchase = save_purchase

    items = _normalize_items(body.get("items") or [])
    if not items:
        return {
            "ok": False,
            "error": "Please add medicines to the purchase.",
            "code": "no_items",
        }

    supplier_name = str(body.get("supplier_name") or "").strip()
    if not supplier_name:
        return {
            "ok": False,
            "error": "Please enter supplier name.",
            "code": "supplier_required",
        }

    phone = str(body.get("supplier_phone") or "").strip()
    address = str(body.get("supplier_address") or "").strip()
    gstin = str(body.get("gstin") or "").strip()
    dl = str(body.get("dl_numbers") or body.get("dl") or "").strip()
    bill_number = str(body.get("bill_number") or "").strip()
    purchase_date = _parse_date(body.get("purchase_date"))
    method = str(body.get("gst_calc_method") or "discount_before_gst").strip()

    overall = _safe_float(body.get("overall_discount", body.get("discount_rs")))
    rounding = _safe_float(body.get("rounding"))
    cash = _safe_float(body.get("cash_paid"))
    online = _safe_float(body.get("online_paid"))
    expenditure = _safe_float(body.get("expenditure", body.get("delivery")))

    prev_due = _safe_float(body.get("previous_due"))
    prev_credit = _safe_float(body.get("previous_credit"))

    # Editing uses snapshot previous due like sales. The live supplier-due query
    # costs several server round trips and its result was thrown away on the edit
    # path two lines later, so only run it when it is actually used.
    editing_id = _safe_int(body.get("editing_purchase_id"))
    if editing_id > 0 and not _safe_int(body.get("autosave_purchase_id")):
        # Not below what was already returned to the supplier, and not removed.
        from core.desktop_returns_service import edit_below_returned_error

        why = edit_below_returned_error(
            conn, "purchase", editing_id, body.get("items") or [], id_key="medicine_id"
        )
        if why:
            return {"ok": False, "error": why, "code": "below_returned"}
    if editing_id > 0 and body.get("edit_previous_due") not in (None, ""):
        prev_due = _safe_float(body.get("edit_previous_due"))
        prev_credit = _safe_float(body.get("edit_previous_credit"))
    else:
        try:
            d, c = get_supplier_due(conn, supplier_name)
            prev_due = _safe_float(d)
            prev_credit = _safe_float(c)
        except Exception:
            pass

    calc = PurchaseCalculator(
        items=items,
        overall_discount=overall,
        rounding=rounding,
        previous_due=prev_due,
        previous_credit=prev_credit,
        cash_paid=cash,
        online_paid=online,
        expenditure=expenditure,
        gst_calc_method=method,
    ).calculate()

    # An imported bill is shown pinned to the supplier's printed footer (net
    # payable, GST, round-off) by calc_purchase, and the classic page saves
    # those same pinned figures. This save worked the bill out from the rows
    # alone, so what was stored was not what the screen showed: SEEMA FRUITS
    # INV1897 said NET PAYABLE 6473.00 on screen and went into the books, and
    # the supplier's due, as 6421.71.
    if body.get("import_bill_mode") and isinstance(body.get("import_invoice_summary"), dict):
        shown = calc_purchase(
            conn,
            dict(
                body,
                previous_due=prev_due,
                previous_credit=prev_credit,
                skip_party_due=True,
            ),
        )
        if shown.get("ok") and isinstance(shown.get("calc"), dict):
            calc = shown["calc"]
            items = list(shown.get("items") or items)

    # The purchase is saved as typed; what looks mistyped is only pointed out. The same
    # supplier's bill number is looked up before the save, which would otherwise find itself.
    try:
        from core.save_warnings import (
            duplicate_supplier_bill_warnings,
            medicine_type_lookup,
            purchase_line_warnings,
        )

        warnings = duplicate_supplier_bill_warnings(
            conn,
            supplier_name=supplier_name,
            bill_number=bill_number,
            exclude_purchase_id=editing_id or _safe_int(body.get("autosave_purchase_id")),
        ) + purchase_line_warnings(
            items, purchase_date, medicine_type=medicine_type_lookup(conn)
        )
    except Exception:
        warnings = []

    try:
        supplier_id = get_or_create_supplier(
            conn, supplier_name, address, phone, gstin, dl
        )
        autosave_id = _safe_int(body.get("autosave_purchase_id"))

        if editing_id > 0 and not autosave_id:
            # Online the edit hands back the number the store holds. Read from the engine's
            # empty :memory: SQLite instead, the answer was the row id ("Purchase 3026 saved."
            # for 111/FY2026-27).
            try:
                stored_no = update_purchase(
                    conn,
                    editing_id,
                    supplier_id,
                    bill_number,
                    purchase_date,
                    calc,
                    items,
                )
            except Exception as exc:
                # The edit is on the store, but the store refused a stock movement of it
                # (another device moved that medicine under the same key first). It is saved:
                # the medicine to check is a save warning, which never blocks.
                if not getattr(exc, "saved", False):
                    raise
                warnings = list(warnings) + [str(exc)]
                stored_no = str(getattr(exc, "purchase_no", "") or "")
            purchase_no = ""
            if isinstance(stored_no, str) and stored_no.strip():
                from core.fy_serial import display_purchase_no

                purchase_no = display_purchase_no(stored_no)
            if not purchase_no:
                row = conn.execute(
                    "SELECT purchase_no FROM purchases WHERE id=?", (editing_id,)
                ).fetchone()
                purchase_no = row[0] if row else str(editing_id)
            purchase_id = editing_id
        elif autosave_id > 0:
            purchase_no = maybe_finalize_autosave(
                conn,
                autosave_id,
                supplier_id,
                bill_number,
                purchase_date,
                calc,
                items,
            )
            purchase_id = autosave_id
        else:
            purchase_no = maybe_save_purchase(
                conn, supplier_id, purchase_date, bill_number, calc, items
            )
            # save may return display serial; resolve latest real row id.
            row = conn.execute(
                "SELECT id, purchase_no FROM purchases "
                "WHERE COALESCE(deleted,0)=0 AND COALESCE(is_autosave,0)=0 "
                "ORDER BY id DESC LIMIT 1"
            ).fetchone()
            purchase_id = int(row[0]) if row else 0
            if row and row[1]:
                # Prefer encoded no for subsequent lookups
                pass

        reorder_order_id = _safe_int(body.get("reorder_order_id"))
        if reorder_order_id > 0:
            try:
                from core.reorder_service import complete_pending_order_from_purchase

                complete_pending_order_from_purchase(conn, reorder_order_id)
            except Exception:
                pass

        # The bill we just saved already tells us where the supplier stands, so
        # derive it instead of paying for another multi-round-trip ledger scan --
        # nothing reads these two response fields anyway. What the old call really
        # provided was a warm suppliers cache, so patch that directly (no network).
        due = _safe_float(calc.get("due"))
        credit = _safe_float(calc.get("current_credit"))
        if due > 0.01:
            credit = 0.0  # mirrors get_supplier_due's online rule
        try:
            from core.sync_prefs import is_online_mode

            if is_online_mode():
                from core.online_catalog import (
                    find_supplier_by_id,
                    patch_supplier_cache,
                )

                found = find_supplier_by_id(int(supplier_id))
                if found:
                    merged = dict(found)
                    merged["total_due"] = due
                    merged["total_credit"] = credit
                    patch_supplier_cache(merged)
        except Exception:
            pass

        return {
            "ok": True,
            "purchase_no": purchase_no,
            "purchase_id": int(purchase_id),
            "supplier_id": int(supplier_id),
            "supplier_due": due,
            "supplier_credit": credit,
            "calc": calc,
            # Things that look mistyped. The purchase is saved regardless.
            "warnings": warnings,
        }
    except Exception as exc:
        try:
            conn.rollback()
        except Exception:
            pass
        return {"ok": False, "error": f"Failed to save purchase: {exc}"}


def _returns_on_edit(conn, purchase_id: int, items: list) -> dict[str, Any]:
    """The returns note and each line's returned qty for a bill opened to edit."""
    from core.desktop_returns_service import edit_returns_info

    return edit_returns_info(conn, "purchase", int(purchase_id), items, id_key="medicine_id")


def load_purchase(conn, purchase_id: int) -> dict[str, Any]:
    from core.layout_config import is_strip_count_type, parse_tablets_per_stripe
    from core.purchase_calculator import reconcile_items_gst_with_header
    from core.purchase_service import expiry_to_display

    try:
        from core.sync_prefs import is_online_mode

        if is_online_mode():
            return _load_purchase_online(conn, int(purchase_id))
    except Exception as exc:
        print(f"[desktop_purchase] online load failed: {exc}")

    try:
        from core.db_setup import ensure_purchase_payment_columns

        ensure_purchase_payment_columns(conn)
    except Exception:
        pass

    cur = conn.cursor()
    cur.execute(
        """
        SELECT s.name, s.address, s.phone, s.gstin, s.dl_numbers, s.id
        FROM suppliers s JOIN purchases p ON s.id=p.supplier_id WHERE p.id=?
        """,
        (int(purchase_id),),
    )
    sup = cur.fetchone()
    cur.execute(
        """
        SELECT bill_number, COALESCE(overall_discount,0),
               COALESCE(cash_paid_at_entry, 0), COALESCE(online_paid_at_entry, 0),
               COALESCE(previous_due,0), COALESCE(previous_credit,0), purchase_date,
               COALESCE(gst_calc_method, 'discount_before_gst'),
               COALESCE(cgst, 0), COALESCE(total_amount, 0),
               COALESCE(rounding, 0), COALESCE(expenditure, 0),
               COALESCE(is_autosave,0), purchase_no
        FROM purchases WHERE id=?
        """,
        (int(purchase_id),),
    )
    hdr = cur.fetchone()
    if not hdr:
        return {"ok": False, "error": "Purchase not found.", "code": "not_found"}

    (
        bill_number,
        overall_disc,
        cash_paid,
        online_paid,
        prev_due,
        prev_credit,
        pur_date,
        gst_calc_method,
        stored_cgst,
        stored_total,
        stored_rounding,
        stored_expenditure,
        is_autosave,
        purchase_no,
    ) = hdr

    method = (gst_calc_method or "discount_before_gst").strip()
    if method not in ("discount_before_gst", "discount_after_gst"):
        method = "discount_before_gst"

    cur.execute(
        """
        SELECT pi.medicine_id, m.name, pi.type, pi.batch_no, pi.expiry_date,
               pi.qty, pi.free_qty, pi.rate,
               COALESCE(pi.gst_pct, pi.gst_value, 0),
               pi.mrp, pi.manufacturer, pi.schedule,
               COALESCE(pi.item_amount, pi.amount, 0),
               pi.hsn_code,
               COALESCE(pi.discount_pct, pi.discount_percent, 0),
               COALESCE(m.content_drug, ''),
               COALESCE(m.unit, ''),
               COALESCE(pi.taxable, 0),
               COALESCE(pi.gst_amt, 0)
        FROM purchase_items pi JOIN medicines m ON pi.medicine_id=m.id
        WHERE pi.purchase_id=?
        """,
        (int(purchase_id),),
    )
    items = []
    for row in cur.fetchall():
        med_type = row[2] or ""
        qty = float(row[5] or 0)
        free_qty = float(row[6] or 0)
        unit = row[16] or ""
        item = {
            "medicine_id": row[0],
            "id": row[0],
            "name": row[1],
            "type": med_type,
            "batch": row[3],
            "expiry": expiry_to_display(row[4]),
            "qty": qty,
            "free_qty": free_qty,
            "rate": row[7],
            "gst_pct": row[8],
            "mrp": row[9],
            "manufacturer": row[10] or "",
            "schedule": row[11] or "",
            "hsn_code": row[13] or "",
            "discount_pct": row[14],
            "content_drug": row[15] or "",
            "item_amount": float(row[12] or 0),
            "amount": float(row[12] or 0),
            "taxable": float(row[17] or 0),
            "gst_amt": float(row[18] or 0),
            "unit": unit,
            "_preserve_line_totals": True,
        }
        if is_strip_count_type(med_type):
            tps = parse_tablets_per_stripe(unit) if unit else 1
            if tps <= 0:
                tps = 1
            item["tablets_per_stripe"] = tps
            item["total_tablets"] = qty * tps
            item["free_tablets"] = free_qty * tps
        else:
            # Not a strip type: one bottle, tube or packet is one unit.
            item["tablets_per_stripe"] = 1
        items.append(item)

    try:
        items = reconcile_items_gst_with_header(
            items,
            stored_cgst,
            stored_total,
            overall_discount=float(overall_disc or 0),
            gst_calc_method=method,
        )
    except Exception:
        pass

    pur_dt = str(pur_date or "")
    if " " in pur_dt:
        pur_dt = pur_dt.split(" ")[0]

    return {
        "ok": True,
        "purchase_id": int(purchase_id),
        "purchase_no": purchase_no,
        "is_autosave": bool(is_autosave),
        "editing_purchase_id": None if is_autosave else int(purchase_id),
        "autosave_purchase_id": int(purchase_id) if is_autosave else None,
        **({} if is_autosave else _returns_on_edit(conn, int(purchase_id), items)),
        "form": {
            "supplier_name": (sup[0] if sup else "") or "",
            "supplier_id": int(sup[5]) if sup else None,
            "supplier_address": (sup[1] if sup else "") or "",
            "supplier_phone": (sup[2] if sup else "") or "",
            "gstin": (sup[3] if sup else "") or "",
            "dl": (sup[4] if sup else "") or "",
            "bill_number": bill_number or "",
            "purchase_date": pur_dt,
            "gst_calc_method": method,
            "overall_discount": _safe_float(overall_disc),
            "rounding": _safe_float(stored_rounding),
            "expenditure": _safe_float(stored_expenditure),
            "cash_paid": _safe_float(cash_paid),
            "online_paid": _safe_float(online_paid),
            "previous_due": _safe_float(prev_due),
            "previous_credit": _safe_float(prev_credit),
            "items": items,
        },
        "edit_payment_snapshot": {
            "previous_due": _safe_float(prev_due),
            "previous_credit": _safe_float(prev_credit),
        },
    }


def _load_purchase_online(conn, purchase_id: int) -> dict[str, Any]:
    from core.layout_config import is_strip_count_type, parse_tablets_per_stripe
    from core.purchase_calculator import reconcile_items_gst_with_header
    from core.purchase_service import expiry_to_display
    from core.server_crud import get_doc
    from core.online_catalog import medicine_by_id, find_supplier_by_id, suppliers as oc_suppliers

    doc = get_doc("purchases", int(purchase_id)) or {}
    if not doc or doc.get("deleted"):
        return {"ok": False, "error": "Purchase not found.", "code": "not_found"}

    supplier_id = int(doc.get("supplier_id") or 0)
    sup = find_supplier_by_id(supplier_id) if supplier_id else None
    if not sup and supplier_id:
        try:
            for s in oc_suppliers() or []:
                try:
                    if int(s.get("id") or s.get("local_id") or 0) == supplier_id:
                        sup = s
                        break
                except (TypeError, ValueError):
                    continue
        except Exception:
            sup = None
        if not sup:
            sup = get_doc("suppliers", supplier_id) or {}
    sup = sup or {
        "name": doc.get("supplier_name") or "",
        "address": doc.get("supplier_address") or "",
        "phone": doc.get("supplier_phone") or "",
        "gstin": doc.get("supplier_gstin") or "",
        "dl_numbers": doc.get("supplier_dl") or doc.get("dl_numbers") or "",
        "id": supplier_id,
    }

    overall_disc = _safe_float(doc.get("overall_discount"))
    cash_paid = _safe_float(doc.get("cash_paid_at_entry"))
    online_paid = _safe_float(doc.get("online_paid_at_entry"))
    prev_due = _safe_float(doc.get("previous_due"))
    prev_credit = _safe_float(doc.get("previous_credit"))
    pur_date = str(doc.get("purchase_date") or "")
    if " " in pur_date:
        pur_date = pur_date.split(" ")[0]
    method = str(doc.get("gst_calc_method") or "discount_before_gst").strip()
    if method not in ("discount_before_gst", "discount_after_gst"):
        method = "discount_before_gst"
    stored_cgst = _safe_float(doc.get("cgst"))
    stored_total = _safe_float(doc.get("total_amount"))
    stored_rounding = _safe_float(doc.get("rounding"))
    stored_expenditure = _safe_float(doc.get("expenditure"))
    is_autosave = bool(doc.get("is_autosave") or 0)
    purchase_no = doc.get("purchase_no") or ""
    bill_number = doc.get("bill_number") or ""

    items = []
    for it in doc.get("items") or []:
        if not isinstance(it, dict):
            continue
        mid = int(it.get("medicine_id") or 0)
        mp = medicine_by_id(mid) if mid else None
        if not mp and mid:
            mp = get_doc("medicines", mid) or {}
        mp = mp or {}
        med_type = it.get("type") or mp.get("type") or ""
        qty = _safe_float(it.get("qty") or it.get("quantity"))
        free_qty = _safe_float(it.get("free_qty"))
        unit = str(
            it.get("unit")
            or it.get("pack")
            or it.get("quantity_value")
            or mp.get("unit")
            or ""
        )
        amount = _safe_float(
            it.get("item_amount") if it.get("item_amount") is not None else it.get("amount")
        )
        item = {
            "medicine_id": mid,
            "id": mid,
            "name": it.get("name") or it.get("medicine_name") or mp.get("name") or "",
            "type": med_type,
            "batch": it.get("batch_no") or it.get("batch") or mp.get("batch_no") or "",
            "expiry": expiry_to_display(
                it.get("expiry_date") or it.get("expiry") or mp.get("expiry_date") or ""
            ),
            "qty": qty,
            "free_qty": free_qty,
            "rate": _safe_float(it.get("rate")),
            "gst_pct": _safe_float(
                it.get("gst_pct") or it.get("gst_percent") or it.get("gst_value")
            ),
            "mrp": _safe_float(it.get("mrp") or mp.get("mrp")),
            "manufacturer": it.get("manufacturer") or mp.get("manufacturer") or "",
            "schedule": it.get("schedule") or mp.get("schedule") or "",
            "hsn_code": it.get("hsn_code") or mp.get("hsn_code") or "",
            "discount_pct": _safe_float(it.get("discount_pct") or it.get("discount_percent")),
            "content_drug": it.get("content_drug") or mp.get("content_drug") or "",
            "item_amount": amount,
            "amount": amount,
            "taxable": _safe_float(it.get("taxable")),
            "gst_amt": _safe_float(it.get("gst_amt")),
            "unit": unit,
            "_preserve_line_totals": True,
        }
        if is_strip_count_type(med_type):
            tps = 0
            raw_tps = it.get("tablets_per_stripe")
            try:
                tps = int(float(raw_tps or 0))
            except (TypeError, ValueError):
                tps = 0
            if tps <= 0:
                tps = parse_tablets_per_stripe(unit) if unit else 1
            if tps <= 0:
                tps = 1
            # Prefer real pack over legacy medicine.unit="1" so MRP/Tab and stock match.
            if str(mp.get("unit") or "") in ("", "1") and tps > 1:
                unit = str(tps)
                item["unit"] = unit
            item["tablets_per_stripe"] = tps
            item["total_tablets"] = qty * tps
            item["free_tablets"] = free_qty * tps
        else:
            # Not a strip type: one bottle, tube or packet is one unit.
            item["tablets_per_stripe"] = 1
        items.append(item)

    try:
        items = reconcile_items_gst_with_header(
            items,
            stored_cgst,
            stored_total,
            overall_discount=float(overall_disc or 0),
            gst_calc_method=method,
        )
    except Exception:
        pass

    pid = int(doc.get("id") or doc.get("local_id") or purchase_id)
    return {
        "ok": True,
        "purchase_id": pid,
        "purchase_no": purchase_no,
        "is_autosave": is_autosave,
        "editing_purchase_id": None if is_autosave else pid,
        "autosave_purchase_id": pid if is_autosave else None,
        **({} if is_autosave else _returns_on_edit(conn, pid, items)),
        "form": {
            "supplier_name": (sup.get("name") if isinstance(sup, dict) else "") or "",
            "supplier_id": int(sup.get("id") or supplier_id) if isinstance(sup, dict) else supplier_id or None,
            "supplier_address": (sup.get("address") if isinstance(sup, dict) else "") or "",
            "supplier_phone": (sup.get("phone") if isinstance(sup, dict) else "") or "",
            "gstin": (sup.get("gstin") if isinstance(sup, dict) else "") or "",
            "dl": (sup.get("dl_numbers") if isinstance(sup, dict) else "") or "",
            "bill_number": bill_number or "",
            "purchase_date": pur_date,
            "gst_calc_method": method,
            "overall_discount": _safe_float(overall_disc),
            "rounding": _safe_float(stored_rounding),
            "expenditure": _safe_float(stored_expenditure),
            "cash_paid": _safe_float(cash_paid),
            "online_paid": _safe_float(online_paid),
            "previous_due": _safe_float(prev_due),
            "previous_credit": _safe_float(prev_credit),
            "items": items,
        },
        "edit_payment_snapshot": {
            "previous_due": _safe_float(prev_due),
            "previous_credit": _safe_float(prev_credit),
        },
    }


def recent_purchases(conn, limit: int = 5) -> dict[str, Any]:
    from core.purchase_service import fetch_recent_purchases

    rows = fetch_recent_purchases(conn, limit=limit) or []
    out = []
    for r in rows:
        out.append(
            {
                "id": r[0],
                "purchase_no": r[1],
                "purchase_date": r[2],
                "supplier": r[3] if len(r) > 3 else "",
                "total": _safe_float(r[4]) if len(r) > 4 else 0,
            }
        )
    return {"ok": True, "purchases": out}


def last_purchase(conn) -> dict[str, Any]:
    from core.purchase_service import fetch_last_purchase_id

    pid = fetch_last_purchase_id(conn)
    if not pid:
        return {"ok": False, "error": "No previous purchase found.", "code": "not_found"}
    return load_purchase(conn, int(pid))


def autosave_purchase(conn, body: dict[str, Any]) -> dict[str, Any]:
    from core.autosave_prefs import load_autosave_enabled
    from core.purchase_calculator import PurchaseCalculator
    from core.purchase_service import (
        get_or_create_supplier,
        save_autosave_purchase,
        update_autosave_purchase,
    )

    if not load_autosave_enabled() and not body.get("force"):
        return {"ok": True, "skipped": True, "reason": "autosave_disabled"}

    items = _normalize_items(body.get("items") or [])
    if not items:
        return {"ok": True, "skipped": True, "reason": "nothing_to_save"}

    if _safe_int(body.get("editing_purchase_id")) > 0 and not _safe_int(
        body.get("autosave_purchase_id")
    ):
        return {"ok": True, "skipped": True, "reason": "editing_real"}

    supplier_name = str(body.get("supplier_name") or "").strip()
    if not supplier_name:
        return {"ok": True, "skipped": True, "reason": "no_supplier"}

    try:
        supplier_id = get_or_create_supplier(
            conn,
            supplier_name,
            str(body.get("supplier_address") or ""),
            str(body.get("supplier_phone") or ""),
            str(body.get("gstin") or ""),
            str(body.get("dl") or body.get("dl_numbers") or ""),
        )
        calc = PurchaseCalculator(
            items=items,
            overall_discount=_safe_float(body.get("overall_discount")),
            rounding=_safe_float(body.get("rounding")),
            previous_due=_safe_float(body.get("previous_due")),
            previous_credit=_safe_float(body.get("previous_credit")),
            cash_paid=_safe_float(body.get("cash_paid")),
            online_paid=_safe_float(body.get("online_paid")),
            expenditure=_safe_float(body.get("expenditure", body.get("delivery"))),
            gst_calc_method=str(body.get("gst_calc_method") or "discount_before_gst"),
        ).calculate()
        purchase_date = _parse_date(body.get("purchase_date"))
        bill_number = str(body.get("bill_number") or "")
        autosave_id = _safe_int(body.get("autosave_purchase_id"))
        if autosave_id > 0:
            update_autosave_purchase(
                conn,
                autosave_id,
                supplier_id,
                bill_number,
                purchase_date,
                calc,
                items,
            )
            return {"ok": True, "autosave_purchase_id": autosave_id, "updated": True}
        result = save_autosave_purchase(
            conn, supplier_id, purchase_date, bill_number, calc, items
        )
        if isinstance(result, tuple):
            purchase_no, purchase_id = result[0], result[1]
        else:
            purchase_no = result
            row = conn.execute(
                "SELECT id FROM purchases WHERE purchase_no=?", (purchase_no,)
            ).fetchone()
            purchase_id = int(row[0]) if row else 0
        return {
            "ok": True,
            "autosave_purchase_id": int(purchase_id or 0),
            "purchase_no": purchase_no,
            "updated": False,
        }
    except Exception as exc:
        try:
            conn.rollback()
        except Exception:
            pass
        return {"ok": False, "error": str(exc)}


def discard_autosave(conn, body: dict[str, Any]) -> dict[str, Any]:
    from core.purchase_service import delete_autosave_purchase

    pid = _safe_int(body.get("autosave_purchase_id") or body.get("purchase_id"))
    if pid <= 0:
        return {"ok": True, "deleted": False}
    try:
        return {"ok": True, "deleted": bool(delete_autosave_purchase(conn, pid))}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def purchase_runtime_prefs() -> dict[str, Any]:
    from core.app_prefs import load_app_mode
    from core.autosave_prefs import (
        load_autosave_enabled,
        load_autosave_interval_seconds,
    )
    from core.layout_config import (
        get_layout_schedules,
        get_med_types,
        get_type_measure_unit,
        is_strip_count_type,
    )

    types = get_med_types()

    type_meta: dict[str, Any] = {}
    for med_type in types:
        strip = bool(is_strip_count_type(med_type))
        measure = ""
        try:
            measure = str(get_type_measure_unit(med_type) or "")
        except Exception:
            measure = ""
        low = (med_type or "").strip().lower()
        is_vial = low == "injection - vial"
        is_vaccine = low == "vaccine"
        type_meta[med_type] = {
            "strip": strip,
            "measure_unit": measure,
            "qty_label": (
                "Strips (Qty)"
                if strip
                else ("Vials (Qty)" if is_vial else "Units (Qty)")
            ),
            "free_label": (
                "Free Strips"
                if strip
                else ("Free Vials" if is_vial else "Free Units")
            ),
            "pack_label": "Tabs/Strip" if strip else "Pack Size",
            "is_vial": is_vial,
            "is_vaccine": is_vaccine,
        }

    app_mode = "medical"
    try:
        app_mode = str(load_app_mode() or "medical").strip().lower() or "medical"
    except Exception:
        pass

    master_ready = True
    master_count = 0
    if app_mode == "medical":
        try:
            from core.master_medicine_service import ensure_mode_master_state

            ok, count, _msg = ensure_mode_master_state("medical")
            master_ready = bool(ok)
            master_count = int(count or 0)
        except Exception:
            master_ready = False

    out: dict[str, Any] = {
        "ok": True,
        "autosave_enabled": load_autosave_enabled(),
        "autosave_interval_seconds": load_autosave_interval_seconds(),
        "medicine_types": types,
        "schedules": get_layout_schedules(),
        "type_meta": type_meta,
        "app_mode": app_mode,
        "master_ready": master_ready,
        "master_count": master_count,
        "gst_calc_methods": [
            {
                "value": "discount_before_gst",
                "label": "Discount before GST (exclusive)",
            },
            {
                "value": "discount_after_gst",
                "label": "Discount after GST (inclusive)",
            },
        ],
        "default_gst_calc_method": "discount_before_gst",
        "column_visibility": {},
    }
    # Settings -> Layout & Lists -> Column Visibility -> "Purchase — Items List"
    # had no reader at all: the ticks were saved and the Purchase table drew
    # every column regardless. Same shape the Sales screen already uses.
    try:
        from core.column_config import get_column_visibility, TABLE_COLUMNS

        vis = dict(get_column_visibility("purchase") or {})
        for name, _ in TABLE_COLUMNS.get("purchase") or []:
            if name not in vis:
                vis[name] = True
        out["column_visibility"] = vis
    except Exception:
        pass
    return out


def search_purchase_medicines(
    conn, q: str = "", limit: int = 50
) -> dict[str, Any]:
    """Local (+ master in medical mode) name suggestions — same merge as classic."""
    from core.app_prefs import load_app_mode

    q = (q or "").strip()
    limit = max(1, min(int(limit or 50), 100))
    mode = "medical"
    try:
        mode = str(load_app_mode() or "medical").strip().lower() or "medical"
    except Exception:
        pass

    def _local_names() -> list[str]:
        # Online mode runs on an empty in-memory shell, so "SELECT ... FROM
        # medicines" below returns NOTHING and the purchase dropdown showed only
        # unrelated master-catalogue rows -- the shop could not find its own
        # stock. Read the store's medicines from the server catalogue instead.
        try:
            from core.sync_prefs import is_online_mode

            if is_online_mode():
                from core.online_catalog import search_medicines_flat

                rows = search_medicines_flat(q, limit=limit) or []
                names: list[str] = []
                seen: set[str] = set()
                for r in rows:
                    nm = str((r or {}).get("name") or "").strip()
                    key = nm.upper()
                    if nm and key not in seen:
                        seen.add(key)
                        names.append(nm)
                return names
        except Exception:
            # Never 500 the dropdown: fall through to the local query, which
            # returns [] in Online mode anyway.
            pass

        cur = conn.cursor()
        try:
            if not q:
                cur.execute(
                    "SELECT DISTINCT name FROM medicines "
                    "ORDER BY name COLLATE NOCASE LIMIT ?",
                    (limit,),
                )
            else:
                cur.execute(
                    "SELECT DISTINCT name FROM medicines "
                    "WHERE name LIKE ? COLLATE NOCASE "
                    "ORDER BY name COLLATE NOCASE LIMIT ?",
                    (f"%{q}%", limit),
                )
            return [str(r[0]) for r in cur.fetchall() if r and r[0]]
        except Exception:
            return []

    local_names = _local_names()
    master_names: list[str] = []
    master_ready = True
    master_source = "local"
    if mode == "medical":
        # Online: prefer global server search (fast typed dropdown); fall back to local snapshot.
        try:
            from core.sync_prefs import is_online_mode

            if is_online_mode() and len(q) >= 1:
                from core.master_medicine_cloud import search_master_remote

                remote = search_master_remote(q, limit=limit) or []
                master_names = [
                    str(r.get("name") or "").strip()
                    for r in remote
                    if str(r.get("name") or "").strip()
                ]
                if master_names:
                    master_source = "server"
                    master_ready = True
        except Exception:
            master_names = []
        if not master_names:
            try:
                from core.master_medicine_service import (
                    ensure_mode_master_state,
                    search_master_names,
                )

                ok, _count, _msg = ensure_mode_master_state("medical")
                master_ready = bool(ok)
                if master_ready:
                    master_names = search_master_names(q, limit=limit) or []
                    master_source = "local"
            except Exception:
                master_ready = False
                master_names = []

    seen: set[str] = set()
    medicines: list[dict[str, Any]] = []
    # Master first (medical), then local — matches classic purchase.py
    for source, names in (
        ("master", master_names),
        ("local", local_names),
    ):
        for name in names:
            key = (name or "").strip().lower()
            if not key or key in seen:
                continue
            seen.add(key)
            medicines.append({"name": name, "source": source})

    # Then order by how well each name answers what was typed: names that START
    # with it, then names where a word starts with it, then the rest -- and
    # alphabetical within each group. Merging master and local in catalogue
    # order put AMOXY and BECOSULES above MECOVET when the shop typed "m".
    from core.name_search_rank import rank_rows

    medicines = rank_rows(medicines, q, limit=limit)

    return {
        "ok": True,
        "medicines": medicines,
        "app_mode": mode,
        "master_ready": master_ready,
        "master_source": master_source,
    }


def register_purchase_medicine(conn, body: dict[str, Any]) -> dict[str, Any]:
    """Upsert into master (medical) when adding a purchase line — classic parity."""
    from core.app_prefs import load_app_mode

    name = str(body.get("name") or "").strip()
    if not name:
        return {"ok": False, "error": "Medicine name required."}
    mode = "medical"
    try:
        mode = str(load_app_mode() or "medical").strip().lower() or "medical"
    except Exception:
        pass
    upserted = False
    if mode == "medical":
        try:
            from core.master_medicine_service import upsert_master_medicine

            upsert_master_medicine(
                name,
                manufacturer=str(body.get("manufacturer") or ""),
                mrp=_safe_float(body.get("mrp")),
                content_drug=str(
                    body.get("content_drug") or body.get("content") or ""
                ),
                med_type=str(body.get("type") or ""),
                pack_size=str(body.get("unit") or body.get("pack") or ""),
            )
            upserted = True
        except Exception as exc:
            return {"ok": False, "error": str(exc), "upserted": False}
    return {"ok": True, "upserted": upserted, "app_mode": mode}


# ── Purchase bill import (PDF / Excel / CSV / Gemini image) ─────────────────

_IMPORT_SESSIONS: dict[str, dict[str, Any]] = {}


class _ImportPageStub:
    """Minimal stand-in so purchase_importer can run without Tk."""

    def __init__(self, conn, med_types: list[str]):
        self.conn = conn
        self._med_types = med_types or []


def _med_types_list() -> list[str]:
    from core.layout_config import get_med_types

    return get_med_types()


def import_capabilities() -> dict[str, Any]:
    from core.build_features import is_gemini_supported
    from core.gemini_bill_config import bill_photo_import_message

    gemini_ok = bool(is_gemini_supported())
    photo_msg = bill_photo_import_message() if gemini_ok else (
        "Bill photo import is not available in this build."
    )
    images_ok = gemini_ok and not photo_msg
    filetypes = [
        {"label": "PDF", "exts": [".pdf"]},
        {"label": "CSV", "exts": [".csv"]},
        {"label": "Excel", "exts": [".xlsx", ".xls"]},
    ]
    if images_ok:
        filetypes.insert(
            0,
            {
                "label": "Bill photos",
                "exts": [".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"],
            },
        )
    accept = ",".join(
        e for ft in filetypes for e in ft["exts"]
    )
    return {
        "ok": True,
        "images_supported": images_ok,
        "image_message": "" if images_ok else (photo_msg or "Photos unavailable"),
        "filetypes": filetypes,
        "accept": accept,
        "hint": (
            "Import uses the same Python parsers as classic (PDF / Excel / CSV"
            + (" / Gemini bill photos" if images_ok else "")
            + ")."
        ),
    }


def _windows_pick_import_files(title: str, filter_spec: str) -> list[str]:
    """Native Windows multi-select dialog (works from the API thread)."""
    import os
    import ctypes
    from ctypes import wintypes

    class OPENFILENAMEW(ctypes.Structure):
        _fields_ = [
            ("lStructSize", wintypes.DWORD),
            ("hwndOwner", wintypes.HWND),
            ("hInstance", wintypes.HINSTANCE),
            ("lpstrFilter", wintypes.LPCWSTR),
            ("lpstrCustomFilter", wintypes.LPWSTR),
            ("nMaxCustFilter", wintypes.DWORD),
            ("nFilterIndex", wintypes.DWORD),
            ("lpstrFile", wintypes.LPWSTR),
            ("nMaxFile", wintypes.DWORD),
            ("lpstrFileTitle", wintypes.LPWSTR),
            ("nMaxFileTitle", wintypes.DWORD),
            ("lpstrInitialDir", wintypes.LPCWSTR),
            ("lpstrTitle", wintypes.LPCWSTR),
            ("Flags", wintypes.DWORD),
            ("nFileOffset", wintypes.WORD),
            ("nFileExtension", wintypes.WORD),
            ("lpstrDefExt", wintypes.LPCWSTR),
            ("lCustData", wintypes.LPARAM),
            ("lpfnHook", ctypes.c_void_p),
            ("lpTemplateName", wintypes.LPCWSTR),
            ("pvReserved", ctypes.c_void_p),
            ("dwReserved", wintypes.DWORD),
            ("FlagsEx", wintypes.DWORD),
        ]

    ofn_explorer = 0x00080000
    ofn_allow_multi = 0x00000200
    ofn_file_must_exist = 0x00001000
    ofn_path_must_exist = 0x00000800
    ofn_hidereadonly = 0x00000004

    buf_chars = 32768
    buf = ctypes.create_unicode_buffer(buf_chars)
    ofn = OPENFILENAMEW()
    ofn.lStructSize = ctypes.sizeof(OPENFILENAMEW)
    try:
        ofn.hwndOwner = ctypes.windll.user32.GetForegroundWindow()
    except Exception:
        ofn.hwndOwner = None
    ofn.lpstrFilter = filter_spec
    ofn.nFilterIndex = 1
    # cast, not a bare assignment: lpstrFile is declared LPWSTR, and handing it
    # the buffer object raised
    #   "incompatible types, c_wchar_Array_32768 instance instead of
    #    c_wchar_p instance"
    # before the dialog was ever shown. The picker therefore failed instantly on
    # every Import, which the desktop reported as an endless "importing".
    ofn.lpstrFile = ctypes.cast(buf, wintypes.LPWSTR)
    ofn.nMaxFile = buf_chars
    ofn.lpstrTitle = title
    ofn.Flags = (
        ofn_explorer
        | ofn_allow_multi
        | ofn_file_must_exist
        | ofn_path_must_exist
        | ofn_hidereadonly
    )
    ok = ctypes.windll.comdlg32.GetOpenFileNameW(ctypes.byref(ofn))
    if not ok:
        return []
    raw = buf.raw.decode("utf-16le", errors="ignore").split("\x00")
    parts = [p for p in raw if p]
    if not parts:
        return []
    if len(parts) == 1:
        return [parts[0]] if os.path.isfile(parts[0]) else []
    folder, *names = parts
    out: list[str] = []
    for name in names:
        path = os.path.join(folder, name)
        if os.path.isfile(path):
            out.append(path)
    return out


def pick_purchase_import_files() -> dict[str, Any]:
    """Tell the caller to use its own file chooser.

    This used to open a native Windows dialog from inside the engine. The engine
    is a hidden background process, so the dialog it created was owned by a
    window belonging to ANOTHER process and was not reliably shown -- the user
    saw nothing while the HTTP request sat waiting for a dialog that was never
    on screen, which is what "still importing" after half an hour actually was.

    The desktop opens its own <input type="file"> in the click itself and sends
    the file content, so it no longer calls this at all. An older desktop build
    talking to this engine gets a clean answer instead of a hang, and falls back
    to that same input.
    """
    return {
        "ok": False,
        "cancelled": False,
        "use_browser_picker": True,
        "paths": [],
        "error": "",
    }


def _native_pick_purchase_import_files() -> dict[str, Any]:
    """The old native picker, kept for a UI that owns a real window."""
    import os

    from core.build_features import is_gemini_supported
    from core.gemini_bill_config import bill_photo_import_message

    images_ok = bool(is_gemini_supported()) and not bill_photo_import_message()
    photo = "*.jpg;*.jpeg;*.png;*.bmp;*.tif;*.tiff;*.webp"
    docs = "*.pdf;*.csv;*.xlsx;*.xls"
    if images_ok:
        filt = (
            "Bill photos\0" + photo + "\0"
            "PDF / Excel / CSV\0" + docs + "\0"
            "All invoice files\0" + docs + ";" + photo + "\0"
            "All files\0*.*\0\0"
        )
        title = "Select purchase invoice — PDF, Excel, CSV, or bill photo"
    else:
        filt = (
            "PDF / Excel / CSV\0" + docs + "\0"
            "All files\0*.*\0\0"
        )
        title = "Select purchase invoice — PDF, Excel, or CSV"
    try:
        paths = _windows_pick_import_files(title, filt)
    except Exception as exc:
        return {"ok": False, "error": str(exc), "paths": [], "cancelled": False}
    if len(paths) > 1:
        from core.purchase_image_ocr import is_image_invoice_path

        if not all(is_image_invoice_path(p) for p in paths):
            return {
                "ok": False,
                "error": (
                    "For PDF, Excel, or CSV — select only ONE file.\n\n"
                    "Select MULTIPLE files only when they are photos of the same bill."
                ),
                "paths": [],
                "cancelled": False,
            }
        from core.bill_page_utils import sort_bill_page_paths

        paths = sort_bill_page_paths(paths)
    return {"ok": True, "paths": paths, "cancelled": not bool(paths)}


def _write_upload_files(files: list) -> tuple[list[str], str]:
    """Write base64 uploads to a temp dir. Returns (paths, temp_dir)."""
    import base64
    import os
    import re
    import tempfile

    if not files:
        raise ValueError("No files uploaded.")
    tmp = tempfile.mkdtemp(prefix="satpuda_pur_import_")
    paths: list[str] = []
    for i, f in enumerate(files):
        if not isinstance(f, dict):
            continue
        name = str(f.get("name") or f"bill_{i}.bin")
        ext = os.path.splitext(name)[1].lower()
        if not re.match(r"^\.[a-z0-9]{1,8}$", ext or ""):
            ext = ".jpg"
        safe_name = f"bill_{i}{ext}"
        raw = str(f.get("content_base64") or f.get("base64") or "")
        if "base64," in raw:
            raw = raw.split("base64,", 1)[1]
        elif raw.strip().startswith("data:") and "," in raw:
            raw = raw.split(",", 1)[1]
        raw = "".join(str(raw).split())
        try:
            data = base64.b64decode(raw, validate=False)
        except Exception as exc:
            raise ValueError(f"Invalid file data for {name}: {exc}") from exc
        if not data:
            raise ValueError(
                f"Could not read '{name}'. Use Import Bill again and pick the file "
                "from the Windows dialog (same as classic)."
            )
        path = os.path.join(tmp, safe_name)
        with open(path, "wb") as out:
            out.write(data)
        paths.append(path)
    if not paths:
        raise ValueError("No valid files uploaded.")
    return paths, tmp


def _resolve_import_paths(body: dict[str, Any]) -> tuple[list[str], Optional[str]]:
    """Return (paths, temp_dir_or_None). Prefer absolute paths; else decode uploads."""
    import os

    paths = [str(p) for p in (body.get("paths") or []) if str(p).strip()]
    paths = [p for p in paths if os.path.isfile(p)]
    if paths:
        return paths, None
    files = body.get("files") or []
    if files:
        return _write_upload_files(files)
    raise ValueError(
        "Provide file paths or upload files (name + content_base64)."
    )


def _placeholder_note(items) -> str:
    from core.purchase_importer import (
        IMPORT_PLACEHOLDER_BATCH,
        IMPORT_PLACEHOLDER_EXPIRY,
    )

    need_batch = [i for i in items if getattr(i, "batch", "") == IMPORT_PLACEHOLDER_BATCH]
    need_exp = [i for i in items if getattr(i, "expiry", "") == IMPORT_PLACEHOLDER_EXPIRY]
    if not need_batch and not need_exp:
        return ""
    parts = []
    if need_batch:
        parts.append(f"{len(need_batch)} without batch on bill")
    if need_exp:
        parts.append(f"{len(need_exp)} without expiry on bill")
    return (
        "\n\nMarked as WITHOUT BATCH / WITHOUT EXP — update those fields before saving."
        " (" + ", ".join(parts) + ")"
    )


def _purge_old_import_sessions(max_age_sec: int = 1800) -> None:
    import time

    now = time.time()
    dead = [
        k
        for k, v in _IMPORT_SESSIONS.items()
        if now - float(v.get("created") or 0) > max_age_sec
    ]
    for k in dead:
        sess = _IMPORT_SESSIONS.pop(k, None)
        _cleanup_session_tmp(sess)


def _cleanup_session_tmp(sess: Optional[dict]) -> None:
    import os
    import shutil

    if not sess:
        return
    tmp = sess.get("tmp_dir")
    if tmp and os.path.isdir(tmp):
        try:
            shutil.rmtree(tmp, ignore_errors=True)
        except Exception:
            pass


_IMPORT_PROGRESS: dict[str, dict[str, Any]] = {}
_IMPORT_PROGRESS_LOCK = threading.Lock()


def _progress_sink(progress_id: str):
    """on_progress callback that parks the latest line for the poller.

    parse_invoice_worker, enrich_invoice_for_import and import_into_purchase_page
    have always emitted these strings -- the classic screen pumps them into a Tk
    label. The desktop had no channel at all, so its overlay showed a frozen
    default for the whole wait while the shop wondered whether anything was
    happening.
    """
    if not progress_id:
        return None

    def _cb(msg) -> None:
        with _IMPORT_PROGRESS_LOCK:
            _IMPORT_PROGRESS[progress_id] = {"status": str(msg or ""), "ts": time.time()}

    return _cb


def _clear_import_progress(progress_id: str) -> None:
    if not progress_id:
        return
    with _IMPORT_PROGRESS_LOCK:
        _IMPORT_PROGRESS.pop(progress_id, None)
        # A run that ends without clearing (a kill, a crash) must not grow this
        # forever.
        stale = [
            k for k, v in _IMPORT_PROGRESS.items()
            if time.time() - float(v.get("ts") or 0) > 900
        ]
        for k in stale:
            _IMPORT_PROGRESS.pop(k, None)


def purchase_import_progress(progress_id: str) -> dict[str, Any]:
    """Read-only poll. An unknown id is not an error -- it is an import that has
    not written a line yet, or one that has just finished."""
    with _IMPORT_PROGRESS_LOCK:
        row = dict(_IMPORT_PROGRESS.get(progress_id) or {})
    return {"ok": True, "status": str(row.get("status") or "")}


def start_purchase_import(conn, body: dict[str, Any]) -> dict[str, Any]:
    """
    Parse + enrich purchase bill (same Python stack as classic Shift+F2).
    Returns a session token; client confirms then calls apply_purchase_import.
    """
    import time
    import uuid

    from core.bill_page_utils import invoice_may_need_more_pages
    from core.purchase_image_ocr import is_image_invoice_path
    from core.purchase_import_api import (
        enrich_invoice_for_import,
        parse_invoice_worker,
    )
    from core.purchase_importer import (
        InvoiceParseError,
        apply_import_placeholders_to_items,
    )

    _purge_old_import_sessions()
    tmp_dir: Optional[str] = None
    try:
        path_list, tmp_dir = _resolve_import_paths(body)
    except ValueError as exc:
        return {"ok": False, "error": str(exc), "code": "no_files"}

    progress_id = str(body.get("progress_id") or "")
    _clear_import_progress(progress_id)
    on_prog = _progress_sink(progress_id)

    try:
        invoice = parse_invoice_worker(path_list, on_progress=on_prog)
        stub = _ImportPageStub(conn, _med_types_list())
        invoice = enrich_invoice_for_import(
            invoice, stub, path_list, on_progress=on_prog, conn=conn
        )
    except InvoiceParseError as exc:
        _cleanup_session_tmp({"tmp_dir": tmp_dir})
        return {"ok": False, "error": str(exc), "code": "parse_error"}
    except Exception as exc:
        _cleanup_session_tmp({"tmp_dir": tmp_dir})
        msg = str(exc)
        if "locked" in msg.lower():
            return {
                "ok": False,
                "error": (
                    "Database is busy (sync or another task is running). "
                    "Wait a few seconds and try import again."
                ),
                "code": "db_busy",
            }
        return {"ok": False, "error": f"Import failed: {exc}", "code": "parse_error"}

    items = list(invoice.items)
    if not items:
        hint = ""
        if getattr(invoice, "source_type", "") == "image":
            hint = (
                "\n\nTips for bill photos:\n"
                "• Add every page if the bill says “Continued…”\n"
                "• Place each page flat with good lighting\n"
                "• Include the full table (all columns visible)\n"
                "• Hold camera steady; avoid blur"
            )
        _cleanup_session_tmp({"tmp_dir": tmp_dir})
        return {
            "ok": False,
            "error": "No medicine rows were found in this file." + hint,
            "code": "empty",
        }

    apply_import_placeholders_to_items(items)
    invalid = [item for item in items if not item.is_valid]
    valid = [item for item in items if item.is_valid]

    if invalid and not valid:
        first = invalid[0]
        _cleanup_session_tmp({"tmp_dir": tmp_dir})
        return {
            "ok": False,
            "error": (
                "No rows could be imported.\n\n"
                f"Row {first.source_row or '?'}: {'; '.join(first.issues)}"
            ),
            "code": "all_invalid",
        }

    page_count = (
        len(path_list)
        if path_list and all(is_image_invoice_path(p) for p in path_list)
        else 1
    )
    may_more = False
    try:
        may_more = bool(invoice_may_need_more_pages(invoice, page_count))
    except Exception:
        may_more = False

    expected_count = int(getattr(invoice, "expected_item_count", 0) or 0)
    token = str(uuid.uuid4())
    _IMPORT_SESSIONS[token] = {
        "invoice": invoice,
        "path_list": path_list,
        "tmp_dir": tmp_dir,
        "created": time.time(),
        "page_count": page_count,
    }

    first_invalid = None
    if invalid:
        fi = invalid[0]
        first_invalid = {
            "source_row": fi.source_row,
            "issues": list(fi.issues or []),
        }

    preview_items = []
    for it in (valid or items)[:8]:
        preview_items.append(
            {
                "name": getattr(it, "name", "") or "",
                "batch": getattr(it, "batch", "") or "",
                "qty": float(getattr(it, "qty", 0) or 0),
                "rate": float(getattr(it, "rate", 0) or 0),
            }
        )

    return {
        "ok": True,
        "import_token": token,
        "source_type": getattr(invoice, "source_type", "") or "",
        "parser": getattr(invoice, "parser", "") or "",
        "supplier_name": (invoice.supplier_name or "").strip(),
        "bill_number": (invoice.invoice_number or "").strip(),
        "purchase_date": str(getattr(invoice, "invoice_date", "") or ""),
        "valid_count": len(valid),
        "invalid_count": len(invalid),
        "expected_item_count": expected_count,
        "page_count": page_count,
        "may_need_more_pages": may_more,
        "first_invalid": first_invalid,
        "preview_items": preview_items,
        "confirmations": {
            "skip_invalid": bool(invalid and valid),
            "item_count_mismatch": bool(
                expected_count > 0 and len(valid) < expected_count
            ),
            "may_need_more_pages": may_more,
        },
        "images_supported": import_capabilities().get("images_supported"),
    }


def get_purchase_import_preview(token: str) -> dict[str, Any]:
    """Full parsed import session for review before apply."""
    tok = str(token or "").strip()
    sess = _IMPORT_SESSIONS.get(tok)
    if not sess:
        return {"ok": False, "error": "Import session expired. Select files again."}
    invoice = sess["invoice"]
    from core.purchase_importer import apply_import_placeholders_to_items

    items = list(invoice.items)
    apply_import_placeholders_to_items(items)
    lines = []
    for it in items:
        lines.append(
            {
                "name": getattr(it, "name", "") or "",
                "batch": getattr(it, "batch", "") or "",
                "qty": float(getattr(it, "qty", 0) or 0),
                "rate": float(getattr(it, "rate", 0) or 0),
                "mrp": float(getattr(it, "mrp", 0) or 0),
                "valid": bool(getattr(it, "is_valid", False)),
                "issues": list(getattr(it, "issues", None) or []),
                "source_row": getattr(it, "source_row", None),
            }
        )
    valid = sum(1 for ln in lines if ln["valid"])
    return {
        "ok": True,
        "import_token": tok,
        "source_type": getattr(invoice, "source_type", "") or "",
        "parser": getattr(invoice, "parser", "") or "",
        "supplier_name": (invoice.supplier_name or "").strip(),
        "supplier_address": (getattr(invoice, "supplier_address", "") or "").strip(),
        "supplier_phone": (getattr(invoice, "supplier_phone", "") or "").strip(),
        "supplier_gstin": (getattr(invoice, "supplier_gstin", "") or "").strip(),
        "bill_number": (invoice.invoice_number or "").strip(),
        "purchase_date": str(getattr(invoice, "invoice_date", "") or ""),
        "valid_count": valid,
        "invalid_count": len(lines) - valid,
        "lines": lines,
    }


def _import_discount_pct(invoice, converted, total_disc: float) -> float:
    """The bill's overall discount as a percentage of its gross.

    Uses the bill's own printed gross when it has one, and otherwise the sum of
    the imported lines -- the same base the Purchase page applies a percentage
    to, so entering either box gives the same rupees.
    """
    try:
        disc = float(total_disc or 0)
    except (TypeError, ValueError):
        return 0.0
    if disc <= 0:
        return 0.0
    base = 0.0
    for attr in ("gross_amount", "subtotal", "total_amount"):
        try:
            base = float(getattr(invoice, attr, 0) or 0)
        except (TypeError, ValueError):
            base = 0.0
        if base > 0:
            break
    if base <= 0:
        for row in converted or []:
            try:
                base += float((row or {}).get("amount") or 0)
            except (TypeError, ValueError):
                continue
    if base <= 0:
        return 0.0
    return round(disc * 100.0 / base, 2)


def apply_purchase_import(conn, body: dict[str, Any]) -> dict[str, Any]:
    """Apply a parsed import session onto purchase form payload (no Tk)."""
    from core.purchase_importer import (
        _build_import_invoice_summary,
        _ensure_import_medicine_ids,
        import_into_purchase_page,
        invoice_bill_discount,
        invoice_gst_calc_method,
        write_import_log,
    )

    token = str(body.get("import_token") or "").strip()
    sess = _IMPORT_SESSIONS.get(token)
    if not sess:
        return {
            "ok": False,
            "error": "Import session expired. Select the file(s) again.",
            "code": "expired",
        }

    invoice = sess["invoice"]
    items = list(invoice.items)
    from core.purchase_importer import apply_import_placeholders_to_items

    apply_import_placeholders_to_items(items)

    invalid = [item for item in items if not item.is_valid]
    valid = [item for item in items if item.is_valid]

    if invalid and valid:
        if not body.get("skip_invalid"):
            first = invalid[0]
            return {
                "ok": False,
                "need_confirm": True,
                "code": "skip_invalid",
                "import_token": token,
                "message": (
                    f"{len(invalid)} row(s) have errors and will be skipped.\n"
                    f"Example — Row {first.source_row or '?'}: "
                    f"{'; '.join(first.issues)}\n\n"
                    f"Import the other {len(valid)} row(s)?"
                ),
                "invalid_count": len(invalid),
                "valid_count": len(valid),
            }
        items = valid

    if invalid and not valid:
        return {
            "ok": False,
            "error": "No valid rows to import.",
            "code": "all_invalid",
        }

    invoice.items = items
    expected_count = int(getattr(invoice, "expected_item_count", 0) or 0)
    if expected_count > 0 and len(items) < expected_count:
        if not body.get("continue_count_mismatch"):
            return {
                "ok": False,
                "need_confirm": True,
                "code": "item_count_mismatch",
                "import_token": token,
                "message": (
                    f"The bill footer shows {expected_count} item(s), but only "
                    f"{len(items)} row(s) were imported.\n\n"
                    "Some medicines may be missing — check all pages were scanned "
                    "clearly.\n\n"
                    f"Continue with {len(items)} row(s)?"
                ),
                "expected_count": expected_count,
                "valid_count": len(items),
            }

    # Existing items replace/append is decided by the client (has_items).
    replace_existing = True
    if body.get("has_existing_items"):
        if "replace_existing" not in body:
            return {
                "ok": False,
                "need_confirm": True,
                "code": "replace_or_append",
                "import_token": token,
                "message": (
                    "This purchase page already has items.\n\n"
                    "Yes: replace them with imported rows.\n"
                    "No: append imported rows.\n"
                    "Cancel: abort import."
                ),
            }
        replace_existing = bool(body.get("replace_existing"))

    stub = _ImportPageStub(conn, _med_types_list())
    # The apply pass is the second long wait: online it does a live store lookup
    # per row, so a forty-line bill is forty network calls. It emits progress
    # lines too; nothing was listening.
    apply_progress_id = str(body.get("progress_id") or "")
    try:
        prepared = import_into_purchase_page(
            stub,
            invoice,
            list(invoice.items),
            replace_existing=replace_existing,
            conn=conn,
            on_progress=_progress_sink(apply_progress_id),
            ui_apply=False,
        )
        _ensure_import_medicine_ids(stub, prepared.get("converted") or [])
    except Exception as exc:
        msg = str(exc).lower()
        if "database is locked" in msg or "database is busy" in msg:
            return {
                "ok": False,
                "error": (
                    "Database is busy (another task briefly locked the store file).\n\n"
                    "Wait a few seconds and try Import again."
                ),
                "code": "db_busy",
            }
        return {"ok": False, "error": str(exc), "code": "prepare_failed"}

    summary = _build_import_invoice_summary(invoice)
    gst_method = invoice_gst_calc_method(invoice)
    total_disc = invoice_bill_discount(invoice)
    added_charges = float(getattr(invoice, "added_charges", 0) or 0)
    import_paid = float(getattr(invoice, "amount_paid", 0) or 0)

    note = _placeholder_note(invoice.items)
    disc_note = ""
    if total_disc > 0:
        disc_note = (
            f"\nBill discount (overall): ₹{total_disc:.2f} — shown in Overall Disc ₹."
        )
    supplier_note = ""
    if (getattr(invoice, "parser", "") or "").startswith("EDI H/T/F") and not (
        invoice.supplier_name or ""
    ).strip():
        supplier_note = (
            "\nEnter the supplier name manually — this CSV has bill no/date only."
        )
    elif (invoice.supplier_name or "").strip():
        supplier_note = f"\nSupplier: {invoice.supplier_name.strip()}."
    ocr_note = ""
    page_count = int(sess.get("page_count") or 1)
    if getattr(invoice, "source_type", "") == "image":
        pages_note = f" from {page_count} pages" if page_count > 1 else ""
        count_note = ""
        if expected_count > 0 and len(invoice.items) < expected_count:
            count_note = (
                f"\nWarning: bill shows {expected_count} items but only "
                f"{len(invoice.items)} were imported — verify all rows."
            )
        ocr_note = (
            f"\nImported from bill photo{pages_note} ({len(invoice.items)} items) "
            f"— verify every row before saving.{count_note}"
        )

    try:
        write_import_log(
            invoice,
            "imported",
            "Loaded onto desktop purchase page",
        )
    except Exception:
        pass

    converted = list(prepared.get("converted") or [])
    form = {
        "supplier_name": prepared.get("supplier_name") or "",
        "supplier_address": prepared.get("supplier_address") or "",
        "supplier_phone": prepared.get("supplier_phone") or "",
        "gstin": prepared.get("supplier_gstin") or "",
        "dl": prepared.get("supplier_dl") or "",
        "bill_number": prepared.get("bill_number") or "",
        "purchase_date": prepared.get("purchase_date") or "",
        "gst_calc_method": gst_method,
        "overall_discount": total_disc,
        # The bill prints the discount in rupees; the Purchase page shows both a
        # rupee box and a percent box, and the percent one was left blank on
        # every import. Derive it from the bill's own gross so the two agree.
        "discount_pct": _import_discount_pct(invoice, converted, total_disc),
        "expenditure": added_charges if added_charges > 0 else 0.0,
        "cash_paid": import_paid if import_paid > 0 else 0.0,
        "online_paid": 0.0,
        "items": converted,
        "edi_manual_supplier": bool(prepared.get("edi_manual_supplier")),
    }

    message = (
        f"{len(converted)} item(s) loaded on the Purchase page.\n"
        "Line discounts are shown in the Disc column (₹ or % from the bill)."
        f"{disc_note}{supplier_note}{ocr_note}{note}\n"
        "Check MRP, rate, quantity and schedule on every line (and batch / expiry) "
        "before you save the purchase."
    )

    # Drop session (and temp files) after successful apply
    _IMPORT_SESSIONS.pop(token, None)
    _cleanup_session_tmp(sess)

    return {
        "ok": True,
        "replace_existing": replace_existing,
        "items_imported": len(converted),
        "form": form,
        "import_bill_mode": True,
        "import_invoice_summary": summary,
        "message": message,
    }


def cancel_purchase_import(body: dict[str, Any]) -> dict[str, Any]:
    token = str(body.get("import_token") or "").strip()
    sess = _IMPORT_SESSIONS.pop(token, None)
    _cleanup_session_tmp(sess)
    return {"ok": True, "cancelled": bool(sess)}


def delete_saved_purchase(conn, body: dict[str, Any]) -> dict[str, Any]:
    """Permanently delete a purchase if none of its items were sold; reverse stock."""
    import re as _re

    from core.purchase_service import _reverse_stock_for_purchase, recalculate_supplier_due

    purchase_id = _safe_int(body.get("purchase_id"))
    if purchase_id == 0:
        return {"ok": False, "error": "Invalid purchase id."}

    try:
        from core.sync_prefs import is_online_mode

        if is_online_mode():
            from core.online_mutation_queue import enqueue, cancel_matching

            if purchase_id < 0:
                cancel_matching(collection="purchases", local_id=purchase_id)
                return {"ok": True, "purchase_id": purchase_id, "queued": True}
            enqueue(
                collection="purchases",
                op="delete",
                payload={"purchase_id": purchase_id, "id": purchase_id},
                local_id=purchase_id,
            )
            return {"ok": True, "purchase_id": purchase_id, "queued": True}
    except Exception as exc:
        return {"ok": False, "error": f"Failed to delete purchase: {exc}"}

    try:
        from core.online_guard import ensure_can_mutate
        ensure_can_mutate()
    except Exception as exc:
        return {"ok": False, "error": str(exc), "code": "online_unavailable"}

    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT supplier_id FROM purchases WHERE id=? AND COALESCE(deleted,0)=0",
            (purchase_id,),
        )
        sup_row = cur.fetchone()
        if not sup_row:
            return {"ok": False, "error": "Purchase not found or already deleted."}
        supplier_id = sup_row[0]

        cur.execute(
            """
            SELECT 1
            FROM purchase_items pi
            JOIN sales_items si ON si.medicine_id = pi.medicine_id
            JOIN sales s ON s.id = si.sale_id AND COALESCE(s.deleted,0)=0
            WHERE pi.purchase_id=?
            LIMIT 1
            """,
            (purchase_id,),
        )
        if cur.fetchone():
            return {
                "ok": False,
                "error": "Cannot delete purchase — one or more of its items have already been sold.",
                "code": "items_sold",
            }

        # What each line put on the shelf is logged on the line (stock_units), and the delete
        # below takes back exactly that. The check has to use the same figure: worked out from
        # the medicine's own pack, a loose line (5 tablets of a strip-of-10 batch) read as 50
        # and a delete that could go ahead was refused as "stock would go negative".
        from core.purchase_service import _purchase_item_columns

        logged_col = (
            "pi.stock_units" if "stock_units" in _purchase_item_columns(cur) else "NULL"
        )
        cur.execute(
            f"""
            SELECT pi.medicine_id, pi.qty, pi.free_qty, pi.type,
                   COALESCE(m.unit,'1'), m.stock_qty, {logged_col}
            FROM purchase_items pi
            JOIN medicines m ON pi.medicine_id=m.id
            WHERE pi.purchase_id=?
            """,
            (purchase_id,),
        )
        for med_id, qty, free_qty, med_type, unit_str, stock, logged in cur.fetchall():
            if logged is not None:
                decrease = float(logged or 0)
            elif (med_type or "").lower() in ("tablet", "bolus"):
                nums = _re.findall(r"\d+", str(unit_str or ""))
                tps = int(nums[0]) if nums else 1
                decrease = (float(qty or 0) + float(free_qty or 0)) * tps
            else:
                decrease = float(qty or 0) + float(free_qty or 0)
            if float(stock or 0) < decrease:
                return {
                    "ok": False,
                    "error": (
                        "Cannot delete — stock would go negative. "
                        f"Current: {stock}, required to remove: {decrease:.0f}"
                    ),
                    "code": "insufficient_stock",
                }

        cur.execute(
            """
            SELECT DISTINCT pi.medicine_id
            FROM purchase_items pi
            WHERE pi.purchase_id=?
            """,
            (purchase_id,),
        )
        medicine_ids = [int(r[0]) for r in cur.fetchall() if r[0]]

        _reverse_stock_for_purchase(cur, purchase_id)
        cur.execute("DELETE FROM purchase_items WHERE purchase_id=?", (purchase_id,))
        cur.execute("DELETE FROM purchases WHERE id=?", (purchase_id,))

        from core.medicine_visibility import hide_medicines_with_zero_stock

        hide_medicines_with_zero_stock(conn, medicine_ids)
        conn.commit()

        if supplier_id:
            recalculate_supplier_due(conn, supplier_id)

        from core.sync_coordinator import after_purchase_deleted

        after_purchase_deleted(conn, purchase_id, supplier_id, medicine_ids)
        return {"ok": True, "purchase_id": purchase_id}
    except Exception as exc:
        try:
            conn.rollback()
        except Exception:
            pass
        return {"ok": False, "error": f"Failed to delete purchase: {exc}"}
