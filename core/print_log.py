"""File logging for bill print / dot matrix diagnostics."""
from __future__ import annotations

import os
import sys
import traceback
from datetime import datetime


def _log_dir() -> str:
    if getattr(sys, "frozen", False):
        base = os.path.join(
            os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
            "VeterinaryApp",
        )
    else:
        base = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "config",
        )
    os.makedirs(base, exist_ok=True)
    return base


def print_log_path() -> str:
    return os.path.join(_log_dir(), "print_log.txt")


def print_log(message: str, *, level: str = "INFO") -> None:
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"{stamp}  {level.upper():5}  {message}\n"
    path = print_log_path()
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(line)
    except Exception:
        pass
    try:
        print(f"[Print] {line.rstrip()}", flush=True)
    except Exception:
        pass


def print_log_exception(context: str, exc: BaseException) -> None:
    print_log(f"{context}: {type(exc).__name__}: {exc}", level="ERROR")
    for row in traceback.format_exc().strip().splitlines():
        print_log(f"  {row}", level="ERROR")