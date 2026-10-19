"""Local WiFi receiver for SatpudaCore Android → mac2 Mobile Import (Win7–Win11, no internet).

Three kinds of visitor reach the address in the PC's Mobile Import QR:

* the Satpuda Loader / Satpuda Core apps: GET /ping, then POST /mobile-import (JSON);
* a phone's camera / browser: GET /mobile-import (or /) asking for HTML gets the phone entry
  page (purchase-entry-web, built into web_app/), which reads /catalog.json and POSTs its bills
  back to /mobile-import. Before, a browser got the bare JSON ping, so scanning the QR with
  the phone's camera opened a page of code;
* tests and scripts: anything not asking for HTML keeps getting the JSON it always got.

/ping and /catalog.json carry ``store_kind`` ('retail' | 'wholesale', the PC's open store),
so the phone enters strips + Rate A/B/C for a wholesale store.
"""
from __future__ import annotations

import json
import mimetypes
import os
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
from typing import Any, Callable, Optional, Tuple
from urllib.parse import unquote

try:
    from http.server import ThreadingHTTPServer as _ThreadingHTTPServer
except ImportError:

    class _ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
        daemon_threads = True


class ThreadingHTTPServer(_ThreadingHTTPServer):
    # Not on Windows: there SO_REUSEADDR lets a second socket bind a port another process
    # is still listening on, and the phone's POST then lands in whichever one Windows picks
    # (an old engine that never closed, say). Without it the bind fails and we move on to
    # the next port, which the QR then carries.
    allow_reuse_address = os.name != "nt"
    daemon_threads = True


DEFAULT_PORT = 9123
ReceiveCallback = Callable[[str, dict], None]
CatalogProvider = Callable[[], dict]

_server: Optional[ThreadingHTTPServer] = None
_server_thread: Optional[threading.Thread] = None
_port = DEFAULT_PORT
_on_receive: Optional[ReceiveCallback] = None
_catalog_provider: Optional[CatalogProvider] = None


def set_catalog_provider(provider: Optional[CatalogProvider]) -> None:
    """What /catalog.json and /ping answer with (suppliers, store_kind, ...)."""
    global _catalog_provider
    _catalog_provider = provider


def page_root() -> str:
    """The folder holding the phone page (web_app/index.html).

    Frozen engine: PyInstaller unpacks the bundled web_app next to the code (sys._MEIPASS,
    the engine's _internal folder) -- read only, which is all this server needs. The Web
    Entry server copies it to LOCALAPPDATA because it writes catalog.json beside it; this
    one answers /catalog.json itself and never writes there.
    """
    if getattr(sys, "frozen", False):
        base = getattr(sys, "_MEIPASS", "") or os.path.dirname(os.path.abspath(sys.executable))
        bundled = os.path.join(base, "web_app")
        if os.path.isfile(os.path.join(bundled, "index.html")):
            return bundled
        # The Web Entry server's copy, when an older build left the bundle out.
        return os.path.join(
            os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "VeterinaryApp", "web_app"
        )
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web_app")


def page_ready() -> bool:
    return os.path.isfile(os.path.join(page_root(), "index.html"))


def _catalog() -> dict:
    provider = _catalog_provider
    out: dict[str, Any] = {}
    if provider is not None:
        try:
            out = dict(provider() or {})
        except Exception as exc:  # the page still works with its built-in lists
            out = {"ok": False, "error": str(exc)}
    out.setdefault("ok", True)
    out.setdefault("store_kind", "retail")
    out["source"] = "mobile_import"
    out["post_to"] = "/mobile-import"
    return out


def _private_ipv4_candidates() -> list:
    """Collect non-loopback IPv4 addresses (works on hotspot with no internet)."""
    seen = set()
    out = []

    def add(ip: str) -> None:
        if not ip or ip.startswith("127.") or ip in seen:
            return
        seen.add(ip)
        out.append(ip)

    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            add(info[4][0])
    except OSError:
        pass
    try:
        _, _, ips = socket.gethostbyname_ex(socket.gethostname())
        for ip in ips:
            add(ip)
    except OSError:
        pass
    return out


def get_lan_ip() -> str:
    """Best-effort LAN IPv4 for QR code (same WiFi or PC on phone hotspot)."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.connect(("8.8.8.8", 80))
            ip = sock.getsockname()[0]
            if ip and not ip.startswith("127."):
                return ip
        finally:
            sock.close()
    except OSError:
        pass

    candidates = _private_ipv4_candidates()
    for ip in candidates:
        if ip.startswith("192.168."):
            return ip
    for ip in candidates:
        if ip.startswith("10.") or ip.startswith("172."):
            return ip
    if candidates:
        return candidates[0]

    try:
        ip = socket.gethostbyname(socket.gethostname())
        if ip and not ip.startswith("127."):
            return ip
    except OSError:
        pass
    return "127.0.0.1"


def get_receive_url(port: Optional[int] = None) -> str:
    p = _port if port is None else int(port)
    return f"http://{get_lan_ip()}:{p}/mobile-import"


def is_running() -> bool:
    return _server is not None


def current_port() -> int:
    return _port


def _json_response(handler: BaseHTTPRequestHandler, status: int, payload: dict) -> None:
    body = json.dumps(payload).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
    handler.send_header("Access-Control-Allow-Headers", "Content-Type")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


class _MobileImportHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        return

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def _wants_page(self) -> bool:
        return "text/html" in str(self.headers.get("Accept") or "").lower()

    def _send_file(self, full: str, content_type: str = "") -> None:
        with open(full, "rb") as fh:
            body = fh.read()
        ctype = content_type or mimetypes.guess_type(full)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
            ctype += "; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_page(self) -> None:
        index = os.path.join(page_root(), "index.html")
        if os.path.isfile(index):
            self._send_file(index, "text/html")
            return
        # A build without the page still tells the phone what to do instead of a 404.
        body = (
            "<!doctype html><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            "<title>Satpuda Mobile Import</title>"
            "<body style='font-family:sans-serif;padding:16px'>"
            "<h2>Satpuda PC found</h2>"
            "<p>This PC's entry page is missing from this build. Use the Satpuda Loader app: "
            "Send to PC &rarr; Scan the PC's QR.</p></body>"
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_static(self, path: str) -> bool:
        """/assets/*, /medicines.json, /favicon.svg from the page folder; False if not there."""
        root = os.path.realpath(page_root())
        rel = unquote(path).lstrip("/").replace("\\", "/")
        if not rel or rel.startswith("."):
            return False
        full = os.path.realpath(os.path.join(root, *rel.split("/")))
        # Never outside the page folder (../, absolute paths, drive letters).
        if os.path.commonpath([root, full]) != root or not os.path.isfile(full):
            return False
        self._send_file(full)
        return True

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/ping" or (path in ("/", "/mobile-import") and not self._wants_page()):
            cat = _catalog()
            _json_response(
                self,
                200,
                {
                    "ok": True,
                    "service": "satpuda-mobile-import",
                    "endpoint": "/mobile-import",
                    "page": "/mobile-import",
                    # The loader's "pair answer": which kind of store this PC has open.
                    "store_kind": cat.get("store_kind") or "retail",
                    "store_name": cat.get("store_name") or "",
                },
            )
            return
        if path in ("/", "/mobile-import", "/index.html"):
            self._send_page()
            return
        if path in ("/catalog.json", "/api/catalog"):
            _json_response(self, 200, _catalog())
            return
        if path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
            return
        if self._send_static(path):
            return
        self.send_error(404)

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        if path not in ("/mobile-import", "/"):
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
        except (TypeError, ValueError):
            length = 0
        try:
            raw = self.rfile.read(length).decode("utf-8") if length else ""
            data = json.loads(raw) if raw.strip() else {}
        except json.JSONDecodeError as exc:
            _json_response(self, 400, {"ok": False, "error": f"invalid JSON: {exc}"})
            return
        except Exception as exc:
            _json_response(self, 400, {"ok": False, "error": str(exc)})
            return

        cb = _on_receive
        if cb is not None:
            try:
                cb(raw, data)
            except Exception as exc:
                _json_response(self, 500, {"ok": False, "error": str(exc)})
                return

        export_type = str(data.get("export_type", "") or "").lower()
        _json_response(
            self,
            200,
            {
                "ok": True,
                "received": True,
                "export_type": export_type,
            },
        )


def start_mobile_import_server(
    on_receive: ReceiveCallback,
    port: int = DEFAULT_PORT,
) -> Tuple[str, int]:
    """Start LAN listener on 0.0.0.0. Returns (receive_url, port)."""
    global _server, _server_thread, _port, _on_receive
    stop_mobile_import_server()
    _on_receive = on_receive

    last_err = None
    for attempt in range(port, port + 10):
        try:
            _server = ThreadingHTTPServer(("0.0.0.0", attempt), _MobileImportHandler)
            _port = attempt
            break
        except OSError as exc:
            last_err = exc
            _server = None
    else:
        raise RuntimeError(
            f"Could not start mobile import receiver on ports {port}-{port + 9}: {last_err}"
        )

    _server_thread = threading.Thread(target=_server.serve_forever, daemon=True)
    _server_thread.start()
    time.sleep(0.05)
    return get_receive_url(_port), _port


def stop_mobile_import_server() -> None:
    global _server, _server_thread, _on_receive
    srv = _server
    _server = None
    _on_receive = None
    if srv is not None:
        try:
            srv.shutdown()
        except Exception:
            pass
        try:
            srv.server_close()
        except Exception:
            pass
    _server_thread = None
    time.sleep(0.05)
