"""Files a counter asked the Main PC to make (exports, report PDFs, bill PDFs) land on the COUNTER.

The Main PC's engine writes such a file into its own save folders and answers with the path.
For a request that came from a counter, the Main PC's LAN listener puts the file itself into
the answer (``_lan_files``: which folder it is in, its path inside that folder, the bytes); the
counter writes it into ITS OWN folder of the same kind, remembers it for Open / Show in folder,
and changes the paths in the answer to its own. Only files inside the Main PC's save folders
are ever sent.
"""
from __future__ import annotations

import base64
import os
import time
from typing import Any, Callable

MAX_FILE = 40 * 1024 * 1024
MAX_AGE = 15 * 60          # files made for this request, not old ones a path happens to name


def _roots() -> list[tuple[str, str]]:
    """(kind, folder) of the save folders on THIS PC, most specific first."""
    out: list[tuple[str, str]] = []
    try:
        from core.saved_files import reports_dir

        out.append(("exports", reports_dir()))
    except Exception:
        pass
    try:
        from core.bill_save_prefs import resolve_sales_bill_save_dir

        out.append(("saves", resolve_sales_bill_save_dir()))
    except Exception:
        pass
    try:
        from core.bill_output import default_downloads_directory

        out.append(("downloads", default_downloads_directory()))
    except Exception:
        pass
    return [(k, os.path.abspath(p)) for k, p in out if p]


def _under(path: str, root: str) -> str | None:
    """path relative to root, or None when it is not inside it."""
    try:
        p = os.path.normcase(os.path.abspath(path))
        r = os.path.normcase(os.path.abspath(root))
        if os.path.commonpath([p, r]) != r or p == r:
            return None
        return os.path.relpath(os.path.abspath(path), os.path.abspath(root))
    except (ValueError, OSError):
        return None


def _walk_strings(value: Any, fn: Callable[[str], Any]) -> Any:
    if isinstance(value, str):
        return fn(value)
    if isinstance(value, list):
        return [_walk_strings(v, fn) for v in value]
    if isinstance(value, dict):
        return {k: _walk_strings(v, fn) for k, v in value.items()}
    return value


def attach(payload: Any, roots: list[tuple[str, str]] | None = None) -> Any:
    """Main PC: add the files the answer names (inside this PC's save folders) to it."""
    if not isinstance(payload, dict):
        return payload
    roots = roots if roots is not None else _roots()
    found: dict[str, dict] = {}
    now = time.time()

    def look(s: str) -> str:
        if len(s) < 4 or len(s) > 600 or s in found:
            return s
        if not (os.path.isabs(s) and os.path.isfile(s)):
            return s
        try:
            st = os.stat(s)
        except OSError:
            return s
        if st.st_size > MAX_FILE or now - st.st_mtime > MAX_AGE:
            return s
        for kind, root in roots:
            rel = _under(s, root)
            if rel is None:
                continue
            try:
                with open(s, "rb") as fh:
                    data = fh.read()
            except OSError:
                return s
            found[s] = {"path": s, "kind": kind, "rel": rel, "b64": base64.b64encode(data).decode("ascii")}
            break
        return s

    _walk_strings(payload, look)
    if found:
        payload = dict(payload)
        payload["_lan_files"] = list(found.values())
    return payload


def receive(payload: Any, roots: list[tuple[str, str]] | None = None) -> Any:
    """Counter: write the attached files into this PC's own folders and point the answer at
    them. Folders the answer names (a bill's pdf_dir) are pointed at this PC's too."""
    if not isinstance(payload, dict) or not payload.get("_lan_files"):
        return payload
    roots = dict(roots if roots is not None else _roots())
    files = payload.pop("_lan_files") or []
    mapping: dict[str, str] = {}
    dir_map: dict[str, str] = {}
    for f in files:
        try:
            kind, rel, remote = str(f["kind"]), str(f["rel"]), str(f["path"])
            data = base64.b64decode(str(f["b64"]))
        except (KeyError, ValueError, TypeError):
            continue
        root = roots.get(kind) or roots.get("exports") or next(iter(roots.values()), "")
        if not root or os.path.isabs(rel) or ".." in rel.replace("\\", "/").split("/"):
            continue
        local = os.path.join(root, rel)
        try:
            os.makedirs(os.path.dirname(local), exist_ok=True)
            with open(local, "wb") as fh:
                fh.write(data)
        except OSError:
            continue
        try:
            from core.saved_files import remember

            remember(local)
        except Exception:
            pass
        mapping[os.path.normcase(remote)] = local
        dir_map[os.path.normcase(os.path.dirname(remote))] = os.path.dirname(local)

    def swap(s: str) -> str:
        key = os.path.normcase(s) if len(s) < 600 else ""
        if key in mapping:
            return mapping[key]
        if key in dir_map:
            return dir_map[key]
        return s

    return _walk_strings(payload, swap)
