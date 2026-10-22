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
