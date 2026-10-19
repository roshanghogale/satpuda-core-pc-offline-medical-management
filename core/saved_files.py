"""Files the shop saves from the app: reports, exports, bill PDFs (owner, 17 Oct 2026).

Exports and reports used to reach the shop as a browser download -- a blob link
clicked inside WebView2. Nothing said where the file went (usually Downloads, without a
word), and on some PCs nothing came out at all, so the shop saw only "Print" and said
there was no way to save a PDF or a file. Now the engine writes the file itself into
the shop's Exports folder (Settings -> bill save folder\\Exports), answers with the
exact path, and the app offers Open and Show in folder. Every export is saved under a
dated name (dated_name), so a new one never replaces an old one (19 Oct 2026).

Open / Show in folder act only on files this engine saved, or files inside the shop's
own save folders -- the engine listens on localhost, and a request must not be able to
make it launch an arbitrary path.
"""
from __future__ import annotations

import base64
import os
import re
import subprocess
import threading
from datetime import datetime
from typing import Any

_lock = threading.Lock()
_SAVED: set[str] = set()
_OPENABLE_EXTS = {".pdf", ".csv", ".xlsx", ".xls", ".html", ".htm", ".json", ".zip", ".txt", ".xml"}

# Exports carry the moment they were saved (owner, 19 Oct 2026: "exports should have the
# date in the file name so an old file can't be replaced"): Sales_Register.pdf is saved as
# Sales_Register_2026-10-19_14-32.pdf. A name that already has a date range keeps it and
# still gets the save time; a second save in the same minute still becomes "... (2)".
STAMP_FORMAT = "%Y-%m-%d_%H-%M"
_STAMPED = re.compile(r"_\d{4}-\d{2}-\d{2}_(?:[01]\d|2[0-3])-[0-5]\d(?: \(\d+\))?$")


def dated_name(filename: str, when: datetime | None = None) -> str:
    """The file name with _YYYY-MM-DD_HH-MM before its extension -- once: a name that
    already ends in a save stamp is left as it is."""
    name = str(filename or "")
    stem, ext = os.path.splitext(name)
    if not stem:   # ".csv"
        stem, ext = name, ""
    if _STAMPED.search(stem):
        return name
    return f"{stem}_{(when or datetime.now()).strftime(STAMP_FORMAT)}{ext}"


def dated_path(path: str, when: datetime | None = None) -> str:
    """A full path under its dated name, never over an older file."""
    folder, name = os.path.split(str(path or ""))
    return _unique(os.path.join(folder, dated_name(name, when)))


def reports_dir() -> str:
    """Where exports and reports go: <the shop's save folder>\\Exports."""
    try:
        from core.bill_save_prefs import resolve_exports_save_dir

        path = resolve_exports_save_dir()
    except Exception:
        path = os.path.join(os.path.expanduser("~"), "Documents", "Satpuda Reports")
    os.makedirs(path, exist_ok=True)
    return path


def _save_roots() -> list[str]:
    """Folders whose files Open / Show in folder accept. The shop's save folder holds its
    Bills\\<day>, Purchase Bills, Returns and Exports sub-folders, and the files saved
    before them (reports, supplier_docs, sales_returns, bills at its top level)."""
    roots = [reports_dir()]
    try:
        from core.bill_save_prefs import resolve_sales_bill_save_dir

        roots.append(resolve_sales_bill_save_dir())
    except Exception:
        pass
    return [os.path.normcase(os.path.abspath(r)) for r in roots if r]


def safe_filename(name: str, default: str = "report") -> str:
    base = os.path.basename(str(name or "").replace("\\", "/"))
    base = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", base).strip(" .")
    return base[:120] or default


def _unique(path: str) -> str:
    if not os.path.exists(path):
        return path
    stem, ext = os.path.splitext(path)
    n = 2
    while os.path.exists(f"{stem} ({n}){ext}"):
        n += 1
    return f"{stem} ({n}){ext}"


def remove_quietly(*paths: str) -> None:
    """Delete temporary files; one that is gone or locked is left alone."""
    for p in paths:
        try:
            if p and os.path.isfile(p):
                os.remove(p)
        except OSError:
            pass


def remove_later(*paths: str, delay: float = 60.0) -> None:
    """Delete a printed job's temporary files once the spooler has had time to read them."""
    paths = tuple(p for p in paths if p)
    if not paths:
        return

    def _work() -> None:
        import time

        time.sleep(delay)
        remove_quietly(*paths)

    threading.Thread(target=_work, name="temp-file-cleanup", daemon=True).start()


def remember(path: str) -> str:
    """Note a file another part of the engine saved, so Open / Show in folder accept it."""
    if path:
        with _lock:
            _SAVED.add(os.path.normcase(os.path.abspath(path)))
    return path


def save_bytes(filename: str, raw: bytes, folder: str = "", *, dated: bool = True) -> str:
    """Write a file into `folder` (the Exports folder by default) under its dated name
    (dated_name), never over an older file."""
    target = folder or reports_dir()
    os.makedirs(target, exist_ok=True)
    name = safe_filename(filename)
    if dated:
        name = dated_name(name)
    path = _unique(os.path.join(target, name))
    tmp = path + ".part"
    with open(tmp, "wb") as fh:
        fh.write(raw)
    os.replace(tmp, path)
    return remember(path)


def describe(path: str, message: str = "") -> dict[str, Any]:
    """What the app shows after a save."""
    path = str(path or "")
    return {
        "ok": bool(path),
        "path": path,
        "file_name": os.path.basename(path),
        "folder": os.path.dirname(path),
        "message": message or (f"Saved to:\n{path}" if path else ""),
    }


def save_base64(filename: str, content_base64: str) -> dict[str, Any]:
    try:
        raw = base64.b64decode(str(content_base64 or ""), validate=False)
    except Exception as exc:
        return {"ok": False, "error": f"Not a file: {exc}"}
    if not raw:
        return {"ok": False, "error": "Nothing to save."}
    try:
        return describe(save_bytes(filename, raw))
    except OSError as exc:
        return {"ok": False, "error": f"Could not save the file: {exc}"}


def _allowed(path: str) -> str:
    """The normalised path when it may be opened, else ''."""
    p = str(path or "").strip()
    if not p or not os.path.isabs(p):
        return ""
    full = os.path.normcase(os.path.abspath(p))
    if os.path.splitext(full)[1].lower() not in _OPENABLE_EXTS or not os.path.isfile(full):
        return ""
    with _lock:
        if full in _SAVED:
            return full
    for root in _save_roots():
        if full.startswith(root.rstrip("\\/") + os.sep):
            return full
    return ""


def open_file(path: str) -> dict[str, Any]:
    full = _allowed(path)
    if not full:
        return {"ok": False, "error": "That file cannot be opened from here."}
    try:
        os.startfile(full)  # type: ignore[attr-defined]
    except Exception as exc:
        return {"ok": False, "error": f"Could not open the file: {exc}"}
    return {"ok": True}


def show_in_folder(path: str) -> dict[str, Any]:
    full = _allowed(path)
    if not full:
        return {"ok": False, "error": "That file cannot be shown from here."}
    try:
        subprocess.Popen(["explorer", "/select,", full])
    except Exception as exc:
        return {"ok": False, "error": f"Could not open the folder: {exc}"}
    return {"ok": True}
