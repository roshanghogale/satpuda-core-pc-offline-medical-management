"""Local WiFi receiver for SatpudaCore Android → mac2 Mobile Import (Win7–Win11, no internet)."""
from __future__ import annotations

import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
from typing import Callable, Optional, Tuple

try:
    from http.server import ThreadingHTTPServer as _ThreadingHTTPServer
except ImportError:

    class _ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
        daemon_threads = True


class ThreadingHTTPServer(_ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True


DEFAULT_PORT = 9123
ReceiveCallback = Callable[[str, dict], None]

_server: Optional[ThreadingHTTPServer] = None
_server_thread: Optional[threading.Thread] = None
_port = DEFAULT_PORT
_on_receive: Optional[ReceiveCallback] = None


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

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path in ("/", "/ping", "/mobile-import"):
            _json_response(
                self,
                200,
                {
                    "ok": True,
                    "service": "satpuda-mobile-import",
                    "endpoint": "/mobile-import",
                },
            )
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
