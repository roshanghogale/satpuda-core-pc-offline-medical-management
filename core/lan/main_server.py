"""The Main PC's listener on the LAN, for its paired counters only.

TCP (port 8775 by default):
  GET  /lan/hello                 who this is (shop, PC name) -- no data, no token needed
  POST /lan/pair                  {code, name, install_id, shop_fp} -> {token, ...}  (code needed)
  -- everything below needs the header X-Satpuda-Lan-Token of a paired counter --
  GET  /lan/ping                  alive; the hint epoch
  GET  /lan/hints?after=&epoch=   collections changed since (core/lan/hints.py)
  POST /lan/handover              a counter's own events -> this copy and on to the cloud
  GET  /lan/feed?after=&relay_after=   this PC's events, for the counter's copy
  GET  /lan/bundle?collection=&id=     one bill's documents, for printing on the counter
  ANY  /api/...                   passed to this PC's own engine on 127.0.0.1 (the counter's
                                  screen works on this PC's data); files the answer names are
                                  sent along (core/lan/files.py)

UDP (port 8776): "SATPUDA-LAN-FIND/1" -> {"service": "satpuda-lan-main", shop, pc, port, shop_fp}.
"""
from __future__ import annotations

import http.client
import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Optional
from urllib.parse import parse_qs, urlparse

from core.lan import hints, pairing

TOKEN_HEADER = "X-Satpuda-Lan-Token"
ORIGIN_HEADER = "X-Satpuda-Lan-Origin"
FILES_HEADER = "X-Satpuda-Lan-Files"
FIND_MESSAGE = b"SATPUDA-LAN-FIND/1"
SERVICE = "satpuda-lan-main"
MAX_BODY = 64 * 1024 * 1024
PAIR_GAP = 1.0                    # seconds between pairing attempts from the LAN


def shop_name() -> str:
    try:
        from core.store_manager import get_active_display_name

        return str(get_active_display_name() or "")
    except Exception:
        return ""


def shop_fingerprint() -> str:
    """A short sign of WHICH shop this PC is on (from its SC- key), so a counter of another shop
    on the same LAN is never paired here. Not the key itself."""
    import hashlib

    try:
        from core.store_link import get_local_android_key

        key = (get_local_android_key() or "").strip().upper()
    except Exception:
        key = ""
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:12] if key else ""


def install_id() -> str:
    try:
        from core.offline_first.client import install_id as iid

        return iid()
    except Exception:
        return ""


def pc_name() -> str:
    try:
        return socket.gethostname()[:60]
    except Exception:
        return "Main PC"


class _LanServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True


class MainServer:
    def __init__(self, *, port: int, discovery_port: int, engine_port: Callable[[], int],
                 db_path: Callable[[], str], bind: str = "0.0.0.0"):
        self.port = int(port)
        self.discovery_port = int(discovery_port)
        self.engine_port = engine_port
        self.db_path = db_path
        self.bind = bind
        self.httpd: Optional[_LanServer] = None
        self.udp: Optional[socket.socket] = None
        self._stop = threading.Event()
        self.error = ""
        self._last_pair = 0.0
        self._pair_lock = threading.Lock()

    # ---------------------------------------------------------------- start / stop
    def start(self) -> None:
        server = self

        class Handler(_Handler):
            owner = server

        try:
            self.httpd = _LanServer((self.bind, self.port), Handler)
        except OSError as exc:
            self.error = f"Port {self.port} is busy on this PC ({exc}). Pick another port in Settings."
            raise
        threading.Thread(target=self.httpd.serve_forever, name="LanMain", daemon=True).start()
        try:
            self.udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.udp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.udp.bind((self.bind, self.discovery_port))
            self.udp.settimeout(1.0)
            threading.Thread(target=self._discovery_loop, name="LanFind", daemon=True).start()
        except OSError as exc:
            # Counters can still be pointed at this PC's address by hand.
            self.error = f"Finding this PC on the LAN is off (UDP {self.discovery_port}: {exc})."
            self.udp = None

    def stop(self) -> None:
        self._stop.set()
        if self.httpd is not None:
            try:
                self.httpd.shutdown()
                self.httpd.server_close()
            except Exception:
                pass
            self.httpd = None
        if self.udp is not None:
            try:
                self.udp.close()
            except Exception:
                pass
            self.udp = None

    def announce(self) -> dict:
        # "id": the same Main PC answers once per address it is reached on (127.0.0.1, its LAN
        # address, the broadcast); the counter lists it once.
        return {"service": SERVICE, "shop": shop_name(), "pc": pc_name(), "port": self.port,
                "shop_fp": shop_fingerprint(), "id": f"{hints.EPOCH}:{self.port}"}

    def _discovery_loop(self) -> None:
        while not self._stop.is_set() and self.udp is not None:
            try:
                data, addr = self.udp.recvfrom(2048)
            except socket.timeout:
                continue
            except OSError:
                break
            if not data.startswith(FIND_MESSAGE):
                continue
            try:
                self.udp.sendto(json.dumps(self.announce()).encode("utf-8"), addr)
            except OSError:
                pass

    # ---------------------------------------------------------------- pairing
    def pair(self, body: dict, ip: str) -> tuple[int, dict]:
        with self._pair_lock:
            wait = PAIR_GAP - (time.time() - self._last_pair)
            if wait > 0:
                time.sleep(wait)
            self._last_pair = time.time()
            fp_here = shop_fingerprint()
            fp_there = str(body.get("shop_fp") or "")
            if fp_here and fp_there and fp_here != fp_there:
                return 409, {"ok": False, "code": "other_shop",
                             "error": "This Main PC is set up for a different shop. Install the counter "
                                      "for the same shop (same SC- key) first."}
            problem = pairing.check_code(body.get("code"))
            if problem:
                return 403, {"ok": False, "code": "bad_code", "error": problem}
            token, dev = pairing.add_device(str(body.get("name") or ""), str(body.get("install_id") or ""), ip)
            pairing.forget_cache()
            return 200, {"ok": True, "token": token, "device_id": dev["id"], "main_name": pc_name(),
                         "shop": shop_name(), "main_install_id": install_id(), "shop_fp": fp_here}


class _Handler(BaseHTTPRequestHandler):
    owner: MainServer = None  # type: ignore[assignment]
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args) -> None:
        return

    def _send(self, status: int, payload: Any, *, raw: Optional[bytes] = None,
              content_type: str = "application/json; charset=utf-8") -> None:
        body = raw if raw is not None else json.dumps(payload, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _body(self) -> bytes:
        n = int(self.headers.get("Content-Length") or 0)
        if n < 0 or n > MAX_BODY:
            raise ValueError("request too large")
        return self.rfile.read(n) if n else b""

    def do_GET(self) -> None:
        self._handle("GET")

    def do_POST(self) -> None:
        self._handle("POST")

    def do_PUT(self) -> None:
        self._handle("PUT")

    def _handle(self, method: str) -> None:
        url = urlparse(self.path)
        path = url.path.rstrip("/") or "/"
        ip = self.client_address[0] if self.client_address else ""
        try:
            body = self._body() if method in ("POST", "PUT") else b""
        except ValueError as exc:
            self._send(413, {"ok": False, "error": str(exc)})
            return
        try:
            if path == "/lan/hello":
                self._send(200, {"ok": True, **self.owner.announce()})
                return
            if path == "/lan/pair" and method == "POST":
                try:
                    data = json.loads(body.decode("utf-8") or "{}")
                except ValueError:
                    data = {}
                status, out = self.owner.pair(data if isinstance(data, dict) else {}, ip)
                self._send(status, out)
                return
            dev = pairing.device_for_token(self.headers.get(TOKEN_HEADER) or "", ip)
            if dev is None:
                self._send(401, {"ok": False, "code": "not_paired",
                                 "error": "This PC is not paired with the Main PC (or was removed there). "
                                          "Pair it again with a new code."})
                return
            q = parse_qs(url.query)

            def qint(name: str, default: int = -1) -> int:
                try:
                    return int((q.get(name) or [default])[0])
                except (TypeError, ValueError):
                    return default

            if path == "/lan/ping":
                self._send(200, {"ok": True, "epoch": hints.EPOCH, "seq": hints.head(), "shop": shop_name(),
                                 "main_name": pc_name(), "main_install_id": install_id(),
                                 "device_id": dev["id"], "time": time.time()})
                return
            if path == "/lan/hints":
                self._send(200, {"ok": True, **hints.since(qint("after"), dev["id"],
                                                           (q.get("epoch") or [""])[0])})
                return
            if path == "/lan/handover" and method == "POST":
                self._handover(dev, body)
                return
            if path == "/lan/feed":
                from core.lan import bridge

                db = self.owner.db_path()
                if not db:
                    self._send(503, {"ok": False, "error": "The Main PC has no store open."})
                    return
                self._send(200, {"ok": True, **bridge.feed(db, qint("after"), qint("relay_after"),
                                                           dev.get("install_id") or "")})
                return
            if path == "/lan/bundle":
                from core.lan import bridge

                db = self.owner.db_path()
                docs = bridge.bundle(db, (q.get("collection") or [""])[0], qint("id", 0)) if db else []
                self._send(200, {"ok": True, "docs": docs})
                return
            if path.startswith("/api/"):
                self._proxy(method, dev, body)
                return
            self._send(404, {"ok": False, "error": f"{path} is not a LAN route"})
        except Exception as exc:  # never leave a counter hanging without an answer
            try:
                self._send(500, {"ok": False, "error": f"Main PC: {type(exc).__name__}: {exc}"})
            except Exception:
                pass

    def _handover(self, dev: dict, body: bytes) -> None:
        from core.lan import bridge
        from core.sync_status import note_screen_change

        data = json.loads(body.decode("utf-8") or "{}")
        db = self.owner.db_path()
        if not db:
            self._send(503, {"ok": False, "error": "The Main PC has no store open."})
            return
        install = str(data.get("install_id") or dev.get("install_id") or "")
        res = bridge.receive_handover(db, install, list(data.get("events") or []), origin=dev["id"])
        cols = (res.get("applied") or {}).get("collections") or []
        if cols:
            note_screen_change(cols, origin=dev["id"])
        try:
            from core.offline_first import worker

            worker.kick()          # on to the cloud now, not in six seconds
        except Exception:
            pass
        self._send(200, {"ok": True, **res})

    def _proxy(self, method: str, dev: dict, body: bytes) -> None:
        port = int(self.owner.engine_port())
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=900)
        headers = {ORIGIN_HEADER: str(dev["id"])}
        ctype = self.headers.get("Content-Type")
        if ctype:
            headers["Content-Type"] = ctype
        if body or method in ("POST", "PUT"):
            headers["Content-Length"] = str(len(body))
        try:
            conn.request(method, self.path, body=body if method in ("POST", "PUT") else None, headers=headers)
            resp = conn.getresponse()
            raw = resp.read()
            status = resp.status
            rtype = resp.getheader("Content-Type") or "application/octet-stream"
        finally:
            conn.close()
        if self.headers.get(FILES_HEADER) and "json" in rtype.lower() and raw:
            try:
                from core.lan import files

                payload = json.loads(raw.decode("utf-8"))
                with_files = files.attach(payload)
                if with_files is not payload:
                    raw = json.dumps(with_files, default=str).encode("utf-8")
            except Exception:
                pass
        self._send(status, None, raw=raw, content_type=rtype)
