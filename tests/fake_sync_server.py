"""A stand-in for the server's GET /api/sync/<collection> pull, for the store-copy tests.

Pages like server syncService.pullCollection: rows in (updated_at, local_id) order; with
after_id the cutoff is `since` exactly and rows at that same updated_at continue after
after_id; without it every row newer than `since`. Like the real server it keeps updated_at
to the microsecond but SENDS it to the millisecond (a doc may carry "_us": the real stamp);
``sync_cursor=True`` also sends the full stamp, as the fixed server does.
"""
from __future__ import annotations


def _ms(ts: str) -> str:
    """'2026-10-22T15:08:51.256972Z' -> '2026-10-22T15:08:51.256Z' (what JSON carries)."""
    if "." in ts:
        head, frac = ts.rstrip("Z").split(".", 1)
        return f"{head}.{(frac + '000')[:3]}Z"
    return ts


def _us(ts: str) -> str:
    if not ts:
        return ""
    if "." in ts:
        head, frac = ts.rstrip("Z").split(".", 1)
        return f"{head}.{(frac + '000000')[:6]}Z"
    return ts.rstrip("Z") + ".000000Z"


class FakeServer:
    def __init__(self, data: dict, *, sync_cursor: bool = False):
        self.data = data
        self.sync_cursor = sync_cursor
        self.calls: list = []

    def _rows(self, col):
        rows = [d for d in self.data.get(col) or [] if isinstance(d, dict)]
        return sorted(rows, key=lambda d: (_us(d.get("_us") or d.get("updated_at") or ""), int(d.get("id") or 0)))

    def pull_collection(self, token, collection, *, since=None, after_id=None, include_deleted=True,
                        limit=5000, timeout=120.0):
        key = (collection, since, after_id, include_deleted)
        if self.calls.count(key) >= 3:
            raise AssertionError(f"the same page was asked for again and again: {key}")
        self.calls.append(key)
        if collection in ("pharmacy_profile", "dropdowns", "shelf_settings"):
            return [], {}
        lim = min(int(limit or 5000), 5000)
        after = int(after_id or 0)
        # What the server can read back from `since`: milliseconds, unless it is a full cursor.
        cut = _us(str(since or "")) if since else ""
        out = []
        for d in self._rows(collection):
            ts = _us(d.get("_us") or d.get("updated_at") or "")
            if after > 0:
                ok = ts > cut or (ts == cut and int(d.get("id") or 0) > after)
            else:
                ok = ts > cut
            if not ok or (not include_deleted and d.get("deleted")):
                continue
            doc = {k: v for k, v in d.items() if k != "_us"}
            if doc.get("updated_at"):
                doc["updated_at"] = _ms(doc["updated_at"])
            if self.sync_cursor:
                doc["sync_cursor"] = ts
            out.append(doc)
            if len(out) >= lim:
                break
        return out, {"server_time": "2026-10-22T00:00:00.000Z"}

    def request(self, method, path, *, body=None, token=None, timeout=120.0, base=None, deadline=None):
        p = path.split("?")[0]
        self.calls.append((method, p))
        if p == "/api/sync/settings/kv":
            return {"ok": True, "data": self.data.get("_settings_kv") or []}
        if p == "/api/store/inventory/counts":
            return {"ok": True, "data": {"all": sum(1 for d in self.data.get("medicines") or []
                                                    if not d.get("deleted"))}}
        counted = {"/api/store/sales": "sales", "/api/store/purchases": "purchases",
                   "/api/store/customers": "customers", "/api/store/suppliers": "suppliers"}
        if p in counted:
            return {"ok": True, "data": {"rows": [], "total": sum(
                1 for d in self.data.get(counted[p]) or [] if not d.get("deleted"))}}
        if p.startswith("/api/store/"):
            return {"ok": True, "data": {"rows": [], "total": 0}}
        raise RuntimeError(f"fake server: no route for {method} {path}")


class FakeV2Server:
    """The offline-first server in memory (server-live routes/syncV2.js push + GET
    /api/sync/changes/full), for round-trip tests of the outbox and the pull worker.

    Events are applied in order per device: an upsert stores the doc (version = stored + 1,
    as the server numbers it; the device's device_id kept), a delete marks it deleted, a stock
    event is accepted and ignored. Every accepted write becomes one entry of the changes feed,
    numbered 1, 2, 3 ... which every device reads with ``changes(after, limit)`` -- its own
    writes included, as the real server sends them.

    ``bind(install_id)`` patches core.offline_first.client so a worker.SyncCycle talks to this
    object; no socket, never port 8765.

    ``numbering=True`` adds the server's bill-number rules (services/syncV2.js + syncService.js):
    a number block starts after every bill number held and every block promised, number-head
    answers which numbers of a range are held, and a sale / purchase whose number another live
    bill already holds is quarantined (kept in ``quarantined``, not stored). Without it every
    block is 1..size and number-head finds nothing taken, as before.
    """

    def __init__(self, *, numbering: bool = False):
        self.docs: dict = {}            # (collection, id) -> doc
        self.feed: list = []            # changes, revision = index + 1
        self.last_seq: dict = {}        # install_id -> last seq taken
        self.pushed: list = []          # every event taken, in order
        self.numbering = numbering
        self.blocks: list = []          # (install_id, kind, fy, from, to)
        self.quarantined: list = []     # events refused for a number already held

    # -- bill numbers (numbering=True) ---------------------------------------------------
    _CODE = {"sales": "bill_no", "purchases": "purchase_no"}

    def _serial(self, kind: str, doc: dict, fy: int):
        import re

        code = str(doc.get(self._CODE[kind]) or "").upper()
        if f"/FY{fy}-" not in code and int(doc.get("fy_start_year") or 0) != int(fy):
            return None
        m = re.match(r"^(?:SCB|APU)?(\d{1,9})$", code.split("/FY")[0])
        return int(m.group(1)) if m else None

    def held(self, kind: str, fy: int) -> list[int]:
        return [n for (col, _), d in self.docs.items() if col == kind and not d.get("deleted")
                for n in [self._serial(kind, d, fy)] if n is not None]

    def import_bills(self, kind: str, fy: int, first: int, last: int) -> None:
        """Bills the server got some other way (a data import): numbers first..last, held on the
        server, not in the changes feed."""
        from core.fy_serial import encode_purchase_no, encode_sales_bill_no

        for n in range(int(first), int(last) + 1):
            code = encode_sales_bill_no(n, fy) if kind == "sales" else encode_purchase_no(n, fy)
            self.docs[(kind, 8_000_000 + n)] = {"id": 8_000_000 + n, self._CODE[kind]: code,
                                                "fy_start_year": fy, "version": 1}

    def number_block(self, install_id: str, kind: str, fy: int, size: int) -> dict:
        if not self.numbering:
            return {"from_serial": 1, "to_serial": int(size)}
        top = max([0, *self.held(kind, fy), *[b[4] for b in self.blocks if b[1] == kind and b[2] == fy]])
        blk = (install_id, kind, int(fy), top + 1, top + int(size))
        self.blocks.append(blk)
        return {"kind": kind, "fy_start_year": int(fy), "from_serial": blk[3], "to_serial": blk[4]}

    def number_head(self, fy: int, ranges: dict) -> dict:
        out = {"fy_start_year": int(fy)}
        for kind in ("sales", "purchases"):
            nums = self.held(kind, fy) if self.numbering else []
            out[kind] = {"head": max([0, *nums]), "ranges": [
                {"from": lo, "to": hi, "taken": max([0, *[n for n in nums if lo <= n <= hi]])}
                for lo, hi in (ranges.get(kind) or [])]}
        return out

    def _number_clash(self, col: str, doc: dict) -> bool:
        if not self.numbering or col not in self._CODE or doc.get("deleted"):
            return False
        code = str(doc.get(self._CODE[col]) or "")
        lid = int(doc.get("id") or 0)
        return bool(code) and any(c == col and i != lid and not d.get("deleted") and d.get(self._CODE[col]) == code
                                  for (c, i), d in self.docs.items())

    def _note(self, collection: str, local_id: int, operation: str, doc: dict) -> None:
        self.feed.append({"revision": len(self.feed) + 1, "collection": collection, "local_id": int(local_id),
                          "operation": operation, "doc": dict(doc), "entity_version": int(doc.get("version") or 1)})

    def push(self, install_id: str, events: list) -> dict:
        results = []
        last = int(self.last_seq.get(install_id, 0))
        for ev in sorted(events, key=lambda e: int(e["seq"])):
            seq = int(ev["seq"])
            if seq <= last:
                results.append({"seq": seq, "outcome": "duplicate", "stored_outcome": "applied"})
                continue
            if seq != last + 1:
                return {"results": results, "last_seq": last, "missing_from": last + 1}
            op = ev.get("op")
            col = ev.get("collection")
            if op == "upsert" and self._number_clash(col, dict(ev.get("doc") or {})):
                self.quarantined.append(ev)
                last = seq
                results.append({"seq": seq, "outcome": "quarantined", "flag_code": "number_taken"})
                continue
            if op == "stock":
                pass
            else:
                doc = dict(ev.get("doc") or {})
                lid = int(doc.get("id") or 0)
                stored = self.docs.get((col, lid))
                if op == "delete":
                    if stored:
                        gone = {**stored, "deleted": True, "version": int(stored.get("version") or 1) + 1}
                        self.docs[(col, lid)] = gone
                        self._note(col, lid, "delete", gone)
                else:
                    doc["version"] = (int(stored.get("version") or 1) + 1) if stored else max(1, int(doc.get("version") or 1))
                    doc["updated_at"] = "2026-10-25T12:00:00.000Z"
                    doc["device_id"] = doc.get("device_id") or install_id
                    self.docs[(col, lid)] = doc
                    self._note(col, lid, "upsert", doc)
            self.pushed.append(ev)
            last = seq
            results.append({"seq": seq, "outcome": "applied"})
        self.last_seq[install_id] = last
        return {"results": results, "last_seq": last}

    def changes(self, after: int, limit: int = 200) -> dict:
        rows = self.feed[int(after): int(after) + int(limit)]
        to = int(after) + len(rows)
        return {"changes": rows, "to_revision": to, "has_more": to < len(self.feed)}

    def stock(self, ids=None) -> dict:
        return {"stock": []}

    def bind(self, install_id: str):
        """Context manager: core.offline_first.client talks to this server as ``install_id``."""
        import contextlib

        from core.offline_first import client

        server = self

        @contextlib.contextmanager
        def _bound():
            saved = (client.push, client.changes, client.stock, client.install_id, client.number_block,
                     client.head_revision, client.number_head)
            client.push = lambda events: server.push(install_id, events)
            client.changes = lambda after, limit=200: server.changes(after, limit)
            client.stock = lambda ids=None: server.stock(ids)
            client.install_id = lambda: install_id
            client.number_block = lambda kind, fy, size: server.number_block(install_id, kind, fy, size)
            client.head_revision = lambda: len(server.feed)
            client.number_head = lambda fy, ranges: server.number_head(fy, ranges)
            try:
                yield server
            finally:
                (client.push, client.changes, client.stock, client.install_id, client.number_block,
                 client.head_revision, client.number_head) = saved

        return _bound()
