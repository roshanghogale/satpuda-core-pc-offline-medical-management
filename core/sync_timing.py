"""Lightweight sync-cycle timing for non-release Desktop builds.

Logs hint → UI-refresh elapsed so Android-connected hint storms can be measured.
Enable always when logging is allowed; force with env SATPUDA_SYNC_TIMING=1.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any, Optional

log = logging.getLogger("sync.timing")

_lock = threading.Lock()
_cycles: dict[str, dict[str, Any]] = {}
_seq = 0


def _enabled() -> bool:
    if os.environ.get("SATPUDA_SYNC_TIMING", "").strip() in ("1", "true", "yes"):
        return True
    try:
        from core.log_policy import should_write_logs

        return bool(should_write_logs())
    except Exception:
        return True


def begin_hint(
    *,
    head: int = 0,
    source: Optional[str] = None,
    changes: int = 0,
    full: bool = False,
) -> Optional[str]:
    if not _enabled():
        return None
    global _seq
    with _lock:
        _seq += 1
        cid = f"h{_seq}"
        _cycles[cid] = {
            "t0": time.perf_counter(),
            "head": head,
            "source": source or "",
            "changes": changes,
            "full": full,
            "phases": {},
        }
    log.info(
        "[SYNC][TIMING] hint_received id=%s head=%s source=%s changes=%s full=%s",
        cid,
        head,
        source or "-",
        changes,
        full,
    )
    return cid


def mark(cycle_id: Optional[str], phase: str, **extra: Any) -> None:
    if not cycle_id or not _enabled():
        return
    now = time.perf_counter()
    with _lock:
        c = _cycles.get(cycle_id)
        if not c:
            return
        elapsed_ms = (now - float(c["t0"])) * 1000.0
        c["phases"][phase] = elapsed_ms
    extra_s = " ".join(f"{k}={v}" for k, v in extra.items()) if extra else ""
    log.info(
        "[SYNC][TIMING] phase=%s id=%s +%.1fms %s",
        phase,
        cycle_id,
        elapsed_ms,
        extra_s,
    )


def end(cycle_id: Optional[str], *, outcome: str = "done", **extra: Any) -> None:
    if not cycle_id or not _enabled():
        return
    now = time.perf_counter()
    with _lock:
        c = _cycles.pop(cycle_id, None)
    if not c:
        return
    total_ms = (now - float(c["t0"])) * 1000.0
    extra_s = " ".join(f"{k}={v}" for k, v in extra.items()) if extra else ""
    log.info(
        "[SYNC][TIMING] cycle_end id=%s outcome=%s total=%.1fms head=%s changes=%s %s",
        cycle_id,
        outcome,
        total_ms,
        c.get("head"),
        c.get("changes"),
        extra_s,
    )


def span(label: str):
    """Context manager for ad-hoc spans (payment repair/refetch, etc.)."""

    class _Span:
        def __init__(self):
            self.t0 = 0.0

        def __enter__(self):
            self.t0 = time.perf_counter()
            if _enabled():
                log.info("[SYNC][TIMING] start %s", label)
            return self

        def __exit__(self, *exc):
            if _enabled():
                ms = (time.perf_counter() - self.t0) * 1000.0
                log.info("[SYNC][TIMING] end %s %.1fms", label, ms)
            return False

    return _Span()
