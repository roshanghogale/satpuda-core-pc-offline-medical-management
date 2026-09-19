"""Global master medicines API client (separate from business store sync)."""
from __future__ import annotations

import logging
import threading
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

_WATERMARK_KEY = "global_master_medicines_since"


def _token() -> str:
    from core import server_api as api

    return api.store_token_for_active()


def pull_master_page(
    *,
    since: Optional[str] = None,
    after_id: int = 0,
    limit: int = 500,
) -> List[dict]:
    import urllib.parse

    from core import server_api as api

    q: Dict[str, Any] = {"limit": int(limit)}
    if since:
        q["since"] = since
    if after_id:
        q["after_id"] = int(after_id)
    path = "/api/master-medicines?" + urllib.parse.urlencode(q)
    res = api._request("GET", path, token=_token(), timeout=120.0)
    data = res.get("data") if isinstance(res, dict) else res
    if isinstance(data, dict):
        return list(data.get("docs") or [])
    return []


def export_master_all() -> List[dict]:
    from core import server_api as api

    res = api._request("GET", "/api/master-medicines/export", token=_token(), timeout=300.0)
    data = res.get("data") if isinstance(res, dict) else res
    if isinstance(data, dict):
        return list(data.get("docs") or [])
    return []


def upsert_master_remote(docs: List[dict], *, enrich: bool = False) -> dict:
    from core import server_api as api

    if not docs:
        return {"upserted": 0, "skipped": 0}
    body = {"docs": docs, "enrich": bool(enrich)}
    res = api._request(
        "POST",
        "/api/master-medicines/upsert",
        body=body,
        token=_token(),
        timeout=180.0,
    )
    data = res.get("data") if isinstance(res, dict) else res
    return data if isinstance(data, dict) else {"upserted": 0, "skipped": 0}


def search_master_remote(q: str, *, limit: int = 50) -> List[dict]:
    """Online purchase dropdown — server prefix/contains search."""
    import urllib.parse

    from core import server_api as api

    path = "/api/master-medicines/search?" + urllib.parse.urlencode(
        {"q": q or "", "limit": int(limit)}
    )
    res = api._request("GET", path, token=_token(), timeout=30.0)
    data = res.get("data") if isinstance(res, dict) else res
    if isinstance(data, dict):
        return list(data.get("medicines") or [])
    return []


def pull_letter_chunk(letter: str, *, after: str = "", limit: int = 500) -> dict:
    import urllib.parse

    from core import server_api as api

    q: Dict[str, Any] = {"letter": letter, "limit": int(limit)}
    if after:
        q["after"] = after
    path = "/api/master-medicines/chunk?" + urllib.parse.urlencode(q)
    res = api._request("GET", path, token=_token(), timeout=60.0)
    data = res.get("data") if isinstance(res, dict) else res
    return data if isinstance(data, dict) else {}


def prefetch_alphabet_chunks_into_local(*, letters: Optional[str] = None) -> int:
    """Pull A–Z chunks from server into local snapshot (online progressive cache)."""
    from core.master_medicine_service import apply_remote_docs

    total = 0
    alphabet = letters or ("#" + "".join(chr(c) for c in range(ord("A"), ord("Z") + 1)))
    for letter in alphabet:
        after = ""
        while True:
            chunk = pull_letter_chunk(letter, after=after, limit=500)
            docs = list(chunk.get("docs") or [])
            if not docs:
                break
            total += apply_remote_docs(docs, replace=False)
            if not chunk.get("has_more"):
                break
            after = str(chunk.get("next_after") or "")
            if not after:
                break
    return total


def push_master_docs_async(docs: List[dict], *, enrich: bool = False) -> None:
    try:
        from core.sync_prefs import is_online_mode

        if not is_online_mode():
            return
    except Exception:
        return

    def _run():
        try:
            upsert_master_remote(docs, enrich=enrich)
        except Exception as exc:
            log.warning("master medicine push failed: %s", exc)

    threading.Thread(target=_run, daemon=True).start()


def download_master_replace_local() -> Tuple[bool, str, int]:
    """Full export from server → replace local master_medicine.db snapshot."""
    try:
        docs = export_master_all()
    except Exception as exc:
        return False, f"Download failed: {exc}", 0
    from core.master_medicine_service import apply_remote_docs, master_row_count

    n = apply_remote_docs(docs, replace=True)
    try:
        from core import desktop_settings_service as dss

        # best-effort watermark clear so next incremental starts fresh
        pass
    except Exception:
        pass
    return True, f"Replaced local master with {n} rows from server (local={master_row_count()}).", n


def pull_master_incremental() -> Tuple[bool, str, int]:
    """Pull changed global rows since watermark and merge into local snapshot."""
    from core.master_medicine_service import apply_remote_docs

    since = _load_watermark()
    total = 0
    after_id = 0
    try:
        while True:
            docs = pull_master_page(since=since, after_id=after_id, limit=500)
            if not docs:
                break
            total += apply_remote_docs(docs, replace=False)
            last = docs[-1]
            after_id = int(last.get("id") or last.get("local_id") or 0)
            ts = str(last.get("updated_at") or "")
            if ts:
                since = ts
            if len(docs) < 500:
                break
        if since:
            _save_watermark(since)
        return True, f"Pulled {total} master row updates.", total
    except Exception as exc:
        return False, f"Master pull failed: {exc}", total


def push_stock_into_master(inventory_conn) -> Tuple[bool, str, dict]:
    """Enrich global master from local inventory, then refresh local snapshot."""
    from core.master_medicine_service import (
        list_master_docs,
        sync_master_with_inventory,
    )

    try:
        n_local = sync_master_with_inventory(inventory_conn)
    except Exception as exc:
        return False, f"Local enrich failed: {exc}", {}

    docs = list_master_docs()
    # Prefer inventory-derived fields: mark enrich
    try:
        result = upsert_master_remote(docs, enrich=True)
    except Exception as exc:
        return False, f"Push stock→master failed: {exc}", {"local_enriched": n_local}

    ok, msg, count = download_master_replace_local()
    summary = {
        "local_enriched": n_local,
        "remote": result,
        "redownloaded": count if ok else 0,
    }
    return ok, (
        f"Pushed stock into global master (local names {n_local}). "
        f"Server upserted {result.get('upserted', 0)}, skipped {result.get('skipped', 0)}. "
        + msg
    ), summary


def on_switch_to_offline_download() -> Tuple[bool, str]:
    """Called when switching Online → Offline: refresh local snapshot from server."""
    ok, msg, _n = download_master_replace_local()
    return ok, msg


def _watermark_path() -> str:
    import os

    from core.master_medicine_service import _config_dir

    return os.path.join(_config_dir(), "master_medicine_watermark.txt")


def _load_watermark() -> Optional[str]:
    try:
        p = _watermark_path()
        with open(p, encoding="utf-8") as fh:
            v = fh.read().strip()
            return v or None
    except Exception:
        return None


def _save_watermark(ts: str) -> None:
    try:
        with open(_watermark_path(), "w", encoding="utf-8") as fh:
            fh.write(str(ts or ""))
    except Exception:
        pass
