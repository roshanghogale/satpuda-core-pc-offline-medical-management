"""HTML -> PDF through one headless browser kept running (owner, 17 Oct 2026: "print should
be fast").

Every bill used to start a browser of its own (msedge/chrome/brave --headless
--print-to-pdf): about a second a bill before the printer heard anything. Now the first
PDF starts one headless browser with a DevTools port, and every later PDF is one page
opened in it and printed with Page.printToPDF -- a few hundred milliseconds.

Anything unexpected -- no browser, no port, a refused connection, a timeout -- returns
False, and bill_output falls back to the one-browser-per-PDF way it has always used.
The browser runs with its own profile folder under AppData, so it never touches the
shop's own browser windows or settings, and is closed when the engine exits.
"""
from __future__ import annotations

import atexit
import base64
import json
import os
import subprocess
import threading
import time
import urllib.request
from typing import Optional

_lock = threading.Lock()
_proc: Optional[subprocess.Popen] = None
_port: int = 0
_exe: str = ""
_disabled_until = 0.0
_START_TIMEOUT = 15.0
_PDF_TIMEOUT = 20.0


def _profile_dir() -> str:
    try:
        from core.license_manager import _appdata_dir

        base = _appdata_dir()
    except Exception:
        import tempfile

        base = tempfile.gettempdir()
    path = os.path.join(base, "pdf_browser_profile")
    os.makedirs(path, exist_ok=True)
    return path


def _read_port(profile: str) -> int:
    try:
        with open(os.path.join(profile, "DevToolsActivePort"), encoding="utf-8") as fh:
            return int(fh.readline().strip() or 0)
    except Exception:
        return 0


def _alive(port: int) -> bool:
    if port <= 0:
        return False
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


def _start(exe: str) -> int:
    """Start (or reuse) the headless browser; the DevTools port, or 0."""
    global _proc, _port, _exe
    if _proc is not None and _proc.poll() is None and _exe == exe and _alive(_port):
        return _port
    if _proc is None and _port and _exe == exe and _alive(_port):
        return _port        # one adopted below, still answering
    _stop()
    profile = _profile_dir()
    # A browser left running by an engine that was killed (no atexit) still holds this
    # profile; a second one would hand its arguments to it and exit. Use it.
    left = _read_port(profile)
    if left and _alive(left):
        _port, _exe = left, exe
        return left
    try:
        os.remove(os.path.join(profile, "DevToolsActivePort"))
    except OSError:
        pass
    flags = 0
    if os.name == "nt":
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    cmd = [
        exe,
        "--headless=new",
        # Port 0: the browser picks a free one and writes it to DevToolsActivePort. No
        # --remote-allow-origins: this client sends no Origin, and without it no web
        # page in the shop's own browser can talk to this one.
        "--remote-debugging-port=0",
        f"--user-data-dir={profile}",
        "--disable-gpu",
        "--disable-extensions",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-background-networking",
        "--disable-component-update",
        "--disable-sync",
        "--mute-audio",
        "about:blank",
    ]
    try:
        _proc = subprocess.Popen(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags
        )
    except Exception:
        _proc = None
        return 0
    deadline = time.time() + _START_TIMEOUT
    while time.time() < deadline:
        port = _read_port(profile)
        if port and _alive(port):
            _port, _exe = port, exe
            return port
        if _proc.poll() is not None:
            break
        time.sleep(0.1)
    _stop()
    return 0


def _stop() -> None:
    global _proc, _port
    p, port = _proc, _port
    _proc, _port = None, 0
    if p is None and port:
        # Adopted from an earlier engine: no process handle, so ask it to close.
        try:
            from websockets.sync.client import connect

            with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=2) as r:
                ws_url = json.loads(r.read().decode("utf-8")).get("webSocketDebuggerUrl") or ""
            if ws_url:
                with connect(ws_url, open_timeout=3) as ws:
                    ws.send(json.dumps({"id": 1, "method": "Browser.close"}))
        except Exception:
            pass
        return
    if p is not None and p.poll() is None:
        try:
            p.terminate()
            p.wait(timeout=5)
        except Exception:
            try:
                p.kill()
            except Exception:
                pass


atexit.register(_stop)


def _new_target(port: int, url: str) -> dict:
    from urllib.parse import quote

    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/json/new?{quote(url, safe=':/')}", method="PUT"
    )
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read().decode("utf-8"))


def _close_target(port: int, target_id: str) -> None:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/close/{target_id}", timeout=3):
            pass
    except Exception:
        pass


def _print(ws_url: str, paper_w_in: float | None, paper_h_in: float | None) -> bytes:
    from websockets.sync.client import connect

    deadline = time.time() + _PDF_TIMEOUT
    with connect(ws_url, open_timeout=5, max_size=None) as ws:
        seq = [0]

        def call(method: str, params: dict | None = None) -> dict:
            seq[0] += 1
            mid = seq[0]
            ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
            while True:
                left = deadline - time.time()
                if left <= 0:
                    raise TimeoutError(method)
                msg = json.loads(ws.recv(timeout=left))
                if msg.get("id") == mid:
                    if "error" in msg:
                        raise RuntimeError(f"{method}: {msg['error']}")
                    return msg.get("result") or {}

        # The page was opened with the file already loading; wait until it, its pictures
        # and its fonts are all there -- what --virtual-time-budget waited for before.
        call(
            "Runtime.evaluate",
            {
                "expression": (
                    "new Promise(r => { const done = () => (document.fonts ? document.fonts.ready"
                    " : Promise.resolve()).then(() => r(true));"
                    " if (document.readyState === 'complete') done();"
                    " else window.addEventListener('load', done, {once: true}); })"
                ),
                "awaitPromise": True,
                "returnByValue": True,
            },
        )
        params = {
            "printBackground": False,
            "displayHeaderFooter": False,
            "preferCSSPageSize": True,
            "marginTop": 0,
            "marginBottom": 0,
            "marginLeft": 0,
            "marginRight": 0,
        }
        if paper_w_in and paper_h_in:
            # As with --paper-width/--paper-height: the page's own @page size still wins;
            # this size is used only by a page that names none.
            params.update(paperWidth=paper_w_in, paperHeight=paper_h_in)
        out = call("Page.printToPDF", params)
        return base64.b64decode(out.get("data") or "")


def html_to_pdf(
    html_path: str,
    pdf_path: str,
    exe: str,
    *,
    paper_width_mm: float | None = None,
    paper_height_mm: float | None = None,
) -> bool:
    """True when the PDF was written through the kept-running browser."""
    global _disabled_until
    if not exe or not os.path.isfile(exe) or time.time() < _disabled_until:
        return False
    uri = "file:///" + os.path.abspath(html_path).replace("\\", "/")
    w_in = paper_width_mm / 25.4 if paper_width_mm and paper_height_mm else None
    h_in = paper_height_mm / 25.4 if paper_width_mm and paper_height_mm else None
    with _lock:
        port = _start(exe)
        if not port:
            _disabled_until = time.time() + 600   # do not try again for every bill
            return False
        target = None
        try:
            target = _new_target(port, uri)
            data = _print(str(target.get("webSocketDebuggerUrl") or ""), w_in, h_in)
            if len(data) <= 500 or not data.startswith(b"%PDF"):
                return False
            tmp = pdf_path + ".part"
            with open(tmp, "wb") as fh:
                fh.write(data)
            os.replace(tmp, pdf_path)
            return True
        except Exception:
            # A browser that misbehaved once is restarted on the next bill.
            _stop()
            return False
        finally:
            if target and target.get("id"):
                _close_target(port, str(target["id"]))
