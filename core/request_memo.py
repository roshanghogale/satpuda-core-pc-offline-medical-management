"""One read per request of the small files and settings every Sales call asks for.

Adding one line to a bill (build_line) read the store registry 3 times, the layout file
2-3 times and opened the store database twice just to read the billing settings; the Sales
settings call read the registry 34 times and the layout 33 times; a save opened the database
3 more times and the device fingerprint 14 times. On a fast PC that is ~25 ms a line and
~110 ms for the settings; on a slow disk with an antivirus checking every open it is seconds.

Inside ``one_read()`` each of those is read once and handed out as a copy; outside it nothing
changes. A request reads them as they were when it started -- which is what it did before,
unless something changed them mid-request, and the writers here call ``forget()``.
"""
from __future__ import annotations

import contextlib
import copy
import functools
import threading
from typing import Any, Callable

_local = threading.local()


@contextlib.contextmanager
def one_read():
    depth = getattr(_local, "depth", 0)
    if not depth:
        _local.memo = {}
    _local.depth = depth + 1
    try:
        from core.layout_config import read_once

        with read_once():
            yield
    finally:
        _local.depth = depth
        if not depth:
            _local.memo = None


def per_request(fn: Callable) -> Callable:
    """Decorator: run ``fn`` inside one_read()."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        with one_read():
            return fn(*args, **kwargs)

    return wrapper


def memo(key: Any, load: Callable[[], Any]) -> Any:
    """``load()`` once per request (a deep copy each time it is handed out); every time
    outside a request."""
    memo_ = getattr(_local, "memo", None)
    if memo_ is None:
        return load()
    if key not in memo_:
        memo_[key] = load()
    return copy.deepcopy(memo_[key])


def forget() -> None:
    """Something was written: the rest of this request reads afresh."""
    memo_ = getattr(_local, "memo", None)
    if memo_:
        memo_.clear()
    try:
        from core import layout_config

        layout_config._READ_ONCE.cached = None
    except Exception:
        pass
