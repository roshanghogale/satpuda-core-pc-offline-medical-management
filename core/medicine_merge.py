"""Inventory > Merge medicines: rows of ONE medicine kept under two or three names (owner, 30 Oct 2026).

A shop's books held the same batch on several rows -- "DOXY 1 LDR FORTE CAP 10'S" and
"DOXY-L-DR-FORT CAP", both batch DXLF26043 -- so a bill took stock off one row while the
shelf stock showed on the other, and correcting one row in Inventory left the other wrong
(store 131: 167 such batches, 55 with stock on two rows).

``candidates`` lists them: live rows with the same batch whose names start alike (first five
letters / digits). ``merge`` puts the rows the owner picked into the row they keep:

  * every sale, purchase, return, write-off, shelf place and order line on a row that goes is
    pointed at the row that stays (bills keep their printed name, amount and stock);
  * stock moves off the row that goes and onto the row that stays -- the sum, or the count the
    owner typed ("Stock after merge"), the difference being one more movement;
  * the row that goes is hidden and deleted.

Offline and offline-first change this PC's file in one transaction; the offline-first triggers
send every touched bill and both stock movements to the server (the server moves stock only by
those movements, never from a bill sent again). Online goes through the server the way the
same-name merge does (purchase_service.merge_online_medicine_batch_duplicates): bills pushed
back with the new medicine id, stock by merge_out / merge_in movements. Only rows of one batch
are merged: a different batch has its own MRP and expiry.
"""
from __future__ import annotations

import re
import sqlite3
from collections import defaultdict
from typing import Any

# Tables whose rows name a medicine row (this PC's file).
LINE_TABLES = ("sales_items", "purchase_items", "sales_return_items", "purchase_return_items",
               "stock_disposals", "pending_orders", "medicine_shelf")

_NO_BATCH = {"", "NA", "-", "BATCH", "N/A", "NIL"}


def batch_key(batch: Any) -> str:
    return re.sub(r"\s+", "", str(batch or "")).upper()


def name_key(name: Any) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(name or "").upper())[:5]


def _online() -> bool:
    try:
        from core.sync_prefs import is_online_mode

        return bool(is_online_mode())
    except Exception:
        return False


def _num(v: Any) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


# ── Which rows look like one medicine ───────────────────────────────────────────────────────

def _local_rows(conn) -> list[dict]:
    have = {r[1] for r in conn.execute("PRAGMA table_info(medicines)")}
    live = "COALESCE(deleted,0)=0" if "deleted" in have else "1=1"
    rows = conn.execute(
        f"SELECT id, name, COALESCE(batch_no,''), COALESCE(mrp,0), COALESCE(expiry_date,''), "
        f"COALESCE(stock_qty,0), COALESCE(is_hidden,0), COALESCE(type,''), COALESCE(unit,'') "
        f"FROM medicines WHERE {live}"
    ).fetchall()
    return [{"id": int(r[0]), "name": r[1] or "", "batch_no": r[2], "mrp": _num(r[3]),
             "expiry_date": str(r[4] or "")[:10], "stock_qty": _num(r[5]), "is_hidden": bool(r[6]),
             "type": r[7], "unit": r[8]} for r in rows]


def _online_rows() -> list[dict]:
    from core import store_query_client as sq

    out: list[dict] = []
    offset = 0
    while offset < 100000:
        data = sq.list_inventory(q="", limit=500, offset=offset, include_total=False, hidden="all") or {}
        raw = [r for r in (data.get("rows") or []) if isinstance(r, dict)]
        for r in raw:
            if r.get("deleted") in (True, 1, "1", "true"):
                continue
            try:
                rid = int(r.get("id") or r.get("local_id") or 0)
            except (TypeError, ValueError):
                continue
            out.append({"id": rid, "name": r.get("name") or "", "batch_no": r.get("batch_no") or r.get("batch") or "",
                        "mrp": _num(r.get("mrp")), "expiry_date": str(r.get("expiry_date") or "")[:10],
                        "stock_qty": _num(r.get("stock_qty")), "is_hidden": bool(r.get("is_hidden")),
                        "type": r.get("type") or "", "unit": r.get("unit") or ""})
        if len(raw) < 500:
            break
        offset += 500
    return out


def _line_counts(conn, ids: list[int]) -> dict[int, int]:
    if not ids:
        return {}
    out: dict[int, int] = defaultdict(int)
    marks = ",".join("?" * len(ids))
    for table in ("sales_items", "purchase_items"):
        try:
            for mid, n in conn.execute(
                f"SELECT medicine_id, COUNT(*) FROM {table} WHERE medicine_id IN ({marks}) GROUP BY medicine_id", ids
            ):
                out[int(mid)] += int(n)
        except sqlite3.Error:
            continue
    return out


def candidates(conn, q: str = "", only_split_stock: bool = False) -> dict[str, Any]:
    """{ok, groups: [{batch, rows: [...]}]} -- rows of one batch whose names start alike."""
    online = _online()
    rows = _online_rows() if online else _local_rows(conn)
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in rows:
        b = batch_key(r["batch_no"])
        k = name_key(r["name"])
        if b in _NO_BATCH or not k or r["id"] <= 0:
            continue
        groups[(b, k)].append(r)
    want = str(q or "").strip().upper()
    out = []
    for (b, _k), g in groups.items():
        if len(g) < 2:
            continue
        if only_split_stock and sum(1 for r in g if round(r["stock_qty"])) < 2:
            continue
        if want and not any(want in str(r["name"]).upper() for r in g) and want not in b:
            continue
        g.sort(key=lambda r: (r["is_hidden"], -abs(r["stock_qty"]), r["id"]))
        out.append({"batch": g[0]["batch_no"], "rows": g,
                    "stock_total": sum(r["stock_qty"] for r in g),
                    "split_stock": sum(1 for r in g if round(r["stock_qty"])) > 1})
    if not online:
        counts = _line_counts(conn, [r["id"] for g in out for r in g["rows"]])
        for g in out:
            for r in g["rows"]:
                r["bill_lines"] = counts.get(r["id"], 0)
    out.sort(key=lambda g: (not g["split_stock"], str(g["rows"][0]["name"]).upper()))
    return {"ok": True, "online": online, "groups": out}


# ── The merge ───────────────────────────────────────────────────────────────────────────────

def _ids(body: dict) -> tuple[int, list[int], Any]:
    try:
        keep = int(body.get("keep_id") or 0)
    except (TypeError, ValueError):
        keep = 0
    merge: list[int] = []
    for raw in body.get("merge_ids") or []:
        try:
            v = int(raw)
        except (TypeError, ValueError):
            continue
        if v > 0 and v != keep and v not in merge:
            merge.append(v)
    after = body.get("stock_after")
    if after in (None, ""):
        after = None
    else:
        after = int(round(_num(after)))
        if after < 0:
            raise ValueError("Stock after merge cannot be below zero.")
    if keep <= 0 or not merge:
        raise ValueError("Pick the row to keep and at least one row to merge into it.")
    return keep, merge, after


def _check_same_batch(keep: dict, losers: list[dict]) -> None:
    b = batch_key(keep.get("batch_no"))
    if b in _NO_BATCH:
        raise ValueError("A row with no batch cannot be merged.")
    for r in losers:
        if batch_key(r.get("batch_no")) != b:
            raise ValueError(f"{r.get('name')} is batch {r.get('batch_no') or '-'}, not {keep.get('batch_no')}: "
                             "only rows of one batch are merged.")


def merge(conn, body: dict) -> dict[str, Any]:
    """{keep_id, merge_ids: [...], stock_after?} -> {ok, merged, keep_id, stock}."""
    keep_id, merge_ids, after = _ids(body or {})
    if _online():
        return _merge_online(keep_id, merge_ids, after)
    return _merge_local(conn, keep_id, merge_ids, after)


def _merge_local(conn, keep_id: int, merge_ids: list[int], after) -> dict[str, Any]:
    rows = {r["id"]: r for r in _local_rows(conn) if r["id"] in {keep_id, *merge_ids}}
    keep = rows.get(keep_id)
    if not keep:
        raise ValueError("The row to keep is not in this store (or was deleted).")
    losers = [rows[i] for i in merge_ids if i in rows]
    if len(losers) != len(merge_ids):
        raise ValueError("A row to merge is not in this store any more. Refresh and try again.")
    _check_same_batch(keep, losers)
    total = int(round(keep["stock_qty"] + sum(r["stock_qty"] for r in losers)))
    final = total if after is None else after
    have_cols = {t: {r[1] for r in conn.execute(f"PRAGMA table_info({t})")} for t in LINE_TABLES}
    moved = 0
    try:
        conn.execute("BEGIN IMMEDIATE") if not conn.in_transaction else None
        for r in losers:
            for table in LINE_TABLES:
                if "medicine_id" not in have_cols.get(table, set()):
                    continue
                if table == "medicine_shelf":
                    # one place per medicine and shelf: a place the kept row has already wins
                    conn.execute("UPDATE OR IGNORE medicine_shelf SET medicine_id=? WHERE medicine_id=?",
                                 (keep_id, r["id"]))
                    conn.execute("DELETE FROM medicine_shelf WHERE medicine_id=?", (r["id"],))
                    continue
                moved += conn.execute(f"UPDATE {table} SET medicine_id=? WHERE medicine_id=?",
                                      (keep_id, r["id"])).rowcount
            med_cols = {c[1] for c in conn.execute("PRAGMA table_info(medicines)")}
            gone = "stock_qty=0, is_hidden=1" + (", deleted=1" if "deleted" in med_cols else "")
            conn.execute(f"UPDATE medicines SET {gone} WHERE id=?", (r["id"],))
        conn.execute("UPDATE medicines SET stock_qty=?, is_hidden=0 WHERE id=?", (final, keep_id))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    try:
        from core.sync_meta_bump import bump_row_meta

        for mid in [keep_id, *merge_ids]:
            bump_row_meta(conn, "medicines", int(mid))
        conn.commit()
    except Exception:
        pass
    try:
        from core.offline_first import worker

        worker.kick()
    except Exception:
        pass
    return {"ok": True, "merged": len(losers), "keep_id": keep_id, "stock": final,
            "stock_total": total, "lines_moved": moved}


def _merge_online(keep_id: int, merge_ids: list[int], after) -> dict[str, Any]:
    from core.online_catalog import invalidate as catalog_invalidate
    from core.purchase_service import _repoint_medicine_lines
    from core.server_crud import _device_id, bump_meta, get_doc, upsert_docs

    def held(mid: int):
        d = get_doc("medicines", mid)
        if not d or d.get("deleted") in (True, 1, "1", "true"):
            return None
        d = dict(d)
        d["id"] = mid
        return d

    keep = held(keep_id)
    if not keep:
        raise ValueError("The row to keep is not on the server (or was deleted).")
    losers = [held(i) for i in merge_ids]
    if any(x is None for x in losers):
        raise ValueError("A row to merge is not on the server any more. Refresh and try again.")
    _check_same_batch(keep, losers)
    keep_stock = _num(keep.get("stock_qty"))
    batch = str(keep.get("batch_no") or "").strip()
    merged = 0
    for loser in losers:
        lid = int(loser["id"])
        if not _repoint_medicine_lines(lid, keep_id, batch):
            raise RuntimeError(f"The bills of {loser.get('name')} could not all be moved; nothing more was changed.")
        gone = int(round(_num(loser.get("stock_qty"))))
        out_doc = bump_meta(dict(loser))
        out_doc.update({"id": lid, "local_id": lid, "stock_qty": 0.0, "is_hidden": True, "deleted": True})
        out_doc["client_uuid"] = str(loser.get("client_uuid") or "")
        keep_doc = bump_meta(dict(keep))
        keep_doc.update({"id": keep_id, "local_id": keep_id, "stock_qty": keep_stock + gone,
                         "is_hidden": False, "deleted": False})
        keep_doc["client_uuid"] = str(keep.get("client_uuid") or "")
        if gone:
            out_doc["stock_ops"] = [{"op_uuid": f"merge:{lid}:into:{keep_id}:out:v1", "op": "merge_out",
                                     "qty_delta": -gone, "medicine_id": lid, "ref_collection": "medicines",
                                     "ref_id": keep_id, "device_id": _device_id()}]
            keep_doc["stock_ops"] = [{"op_uuid": f"merge:{lid}:into:{keep_id}:in:v1", "op": "merge_in",
                                      "qty_delta": gone, "medicine_id": keep_id, "ref_collection": "medicines",
                                      "ref_id": lid, "device_id": _device_id()}]
        upsert_docs("medicines", [out_doc] + ([keep_doc] if gone else []))
        merged += 1
        keep_stock += gone
        keep["version"] = int(keep_doc.get("version") or keep.get("version") or 0)
        keep = held(keep_id) or keep
    total = int(round(keep_stock))
    if after is not None and after != total:
        doc = bump_meta(dict(keep))
        doc.update({"id": keep_id, "local_id": keep_id, "stock_qty": float(after), "is_hidden": False})
        doc["client_uuid"] = str(keep.get("client_uuid") or "")
        doc["stock_ops"] = [{"op_uuid": f"merge-count:{keep_id}:{'-'.join(map(str, merge_ids))}:v1", "op": "set",
                             "qty_delta": int(after - total), "medicine_id": keep_id, "device_id": _device_id()}]
        upsert_docs("medicines", [doc])
    try:
        catalog_invalidate()
    except Exception:
        pass
    return {"ok": True, "merged": merged, "keep_id": keep_id,
            "stock": total if after is None else after, "stock_total": total}
