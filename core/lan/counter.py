"""A Counter PC: its engine works on the Main PC's data over the LAN, and on its own copy when the
Main PC cannot be reached.

The screen keeps talking to this PC's own engine on 127.0.0.1. For each request the engine asks
``CounterLink.handle``:

  * this PC's own things stay here (LOCAL): the licence, the screen's look and prefs, printers,
    the system settings, files, the sync status, this PC's offline-first copy;
  * printing stays here: a bill is saved on the Main PC, its documents are copied into this
    PC's copy (bridge.bundle) and it is printed by this PC's own print code on this PC's
    printer; reports with "print to" are made from this PC's copy;
  * everything else goes to the Main PC with this PC's device token. Files the Main PC made for
    the request come back with the answer and are written into this PC's folders (files.py).

If the Main PC does not answer (no connection could be made), the request is answered here from
this PC's own copy -- its own ids and bill-number blocks, so nothing it makes can clash -- and
the screen shows "Main PC not reachable". A save whose request reached the Main PC but got no
answer is NOT repeated here (it may have been saved there): the shop is told to check.

The link thread (every second): change hints from the Main PC -> this screen; whenever the Main
PC is reachable, this PC's own unsent events are handed to it (bridge.handover_batch) and the
Main PC's changes are copied into this PC's copy (bridge.feed). The first handover after the
Main PC comes back runs BEFORE requests go to it again, so the shop sees its bills there at once.
"""
from __future__ import annotations

import http.client
import io
import json
import queue
import socket
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable, Optional
from urllib.parse import urlparse

from core.lan import config
from core.lan.main_server import FILES_HEADER, FIND_MESSAGE, SERVICE, TOKEN_HEADER, shop_fingerprint

CONNECT_TIMEOUT = 2.0
READ_TIMEOUT = 900.0
HINT_EVERY = 1.0
SYNC_EVERY = 3.0
PAIR_RETRY = 5.0

BANNER_DOWN = "Main PC not reachable — billing on this PC; will join up automatically."
BANNER_UNPAIRED = "This PC is set as a Counter PC but is not paired with a Main PC yet (Settings > Data & System > This PC on the LAN)."
BANNER_REMOVED = "The Main PC no longer knows this PC — pair it again (Settings > Data & System > This PC on the LAN). Billing on this PC meanwhile."

# Requests this PC answers itself, always.
LOCAL_EXACT = frozenset({
    "/api/health", "/api/meta", "/api/prefs", "/api/sync/status", "/api/backup/close",
    "/api/online/migrate", "/api/sales/prefs", "/api/purchase/prefs", "/api/bill/signature-switch",
    "/api/settings/bundle",
})
LOCAL_PREFIXES = (
    "/api/lan/", "/api/offline-first/", "/api/files/", "/api/license/", "/api/login/", "/api/voice/",
    "/api/brand/", "/api/home/banner", "/api/settings/appearance", "/api/settings/save/appearance",
    "/api/settings/layout_lists", "/api/settings/save/layout_lists", "/api/settings/system",
    "/api/settings/save/system", "/api/settings/printer", "/api/settings/sales_billing",
    "/api/settings/save/sales_billing", "/api/settings/thresholds", "/api/settings/save/thresholds",
    "/api/settings/alert_prefs", "/api/settings/import", "/api/settings/save/import",
    "/api/settings/pharmacy", "/api/settings/save/pharmacy", "/api/purchase/import/",
    "/api/sync/",
)
PRINT_PATHS = frozenset({"/api/sales/print", "/api/sales/print-all"})
# Saves whose bill is copied into this PC's copy straight away (a failover a moment later
# then edits the same bill instead of making a second one).
_BUNDLE_AFTER = {
    "/api/sales/save": ("sales", ("sale_id",)),
    "/api/sales/autosave": ("sales", ("autosave_sale_id", "sale_id")),
    "/api/wholesale/sale/save": ("sales", ("sale_id",)),
    "/api/purchase/save": ("purchases", ("purchase_id",)),
    "/api/purchase/autosave": ("purchases", ("autosave_purchase_id", "purchase_id")),
}


class Unreachable(Exception):
    """No connection to the Main PC could be made: nothing was sent."""


class LostAnswer(Exception):
    """The request went to the Main PC but no answer came back."""


def is_local(path: str) -> bool:
    return path in LOCAL_EXACT or any(path.startswith(p) for p in LOCAL_PREFIXES)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def local_addresses() -> list[str]:
    out: list[str] = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip not in out and not ip.startswith("127."):
                out.append(ip)
    except OSError:
        pass
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("10.255.255.255", 1))
            ip = s.getsockname()[0]
            if ip and ip not in out and not ip.startswith("127."):
                out.insert(0, ip)
        finally:
            s.close()
    except OSError:
        pass
    return out


def discover(discovery_port: int = config.DEFAULT_DISCOVERY_PORT, timeout: float = 1.5,
             extra_hosts: tuple[str, ...] = ()) -> list[dict]:
    """Main PCs answering on this LAN: [{host, port, shop, pc, same_shop}]."""
    found: dict[str, dict] = {}
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.bind(("", 0))
        targets = {"255.255.255.255", "127.0.0.1", *extra_hosts}
        for ip in local_addresses():
            parts = ip.split(".")
            if len(parts) == 4:
                targets.add(".".join(parts[:3] + ["255"]))
        for t in targets:
            try:
                sock.sendto(FIND_MESSAGE, (t, int(discovery_port)))
            except OSError:
                pass
        mine = shop_fingerprint()
        end = time.time() + timeout
        while time.time() < end:
            sock.settimeout(max(0.05, end - time.time()))
            try:
                data, addr = sock.recvfrom(4096)
            except (socket.timeout, OSError):
                break
            try:
                info = json.loads(data.decode("utf-8"))
            except ValueError:
                continue
            if not isinstance(info, dict) or info.get("service") != SERVICE:
                continue
            host = addr[0]
            key = str(info.get("id") or f"{host}:{info.get('port')}")
            if key in found and not found[key]["host"].startswith("127."):
                continue                     # the same Main PC again; keep its LAN address
            fp = str(info.get("shop_fp") or "")
            found[key] = {"host": host, "port": int(info.get("port") or config.DEFAULT_PORT),
                          "shop": info.get("shop") or "", "pc": info.get("pc") or "",
                          "same_shop": (not mine or not fp or fp == mine)}
    finally:
        sock.close()
    return sorted(found.values(), key=lambda d: (not d["same_shop"], d["host"]))


def _request(host: str, port: int, method: str, path: str, body: Optional[bytes] = None,
             headers: Optional[dict] = None, timeout: float = READ_TIMEOUT) -> tuple[int, str, bytes]:
    conn = http.client.HTTPConnection(host, int(port), timeout=CONNECT_TIMEOUT)
    try:
        try:
            conn.connect()
        except OSError as exc:
            raise Unreachable(str(exc)) from exc
        conn.sock.settimeout(timeout)
        try:
            hdrs = dict(headers or {})
            if body is not None:
                hdrs["Content-Length"] = str(len(body))
            conn.request(method, path, body=body, headers=hdrs)
            resp = conn.getresponse()
            raw = resp.read()
            return resp.status, resp.getheader("Content-Type") or "application/octet-stream", raw
        except (OSError, http.client.HTTPException) as exc:
            raise LostAnswer(str(exc)) from exc
    finally:
        conn.close()


def pair(host: str, port: int, code: str) -> dict:
    """Pair this PC with the Main PC at host:port using its 6-digit code; saves the token."""
    from core.lan.main_server import install_id, pc_name

    body = json.dumps({"code": str(code or "").strip(), "name": pc_name(), "install_id": install_id(),
                       "shop_fp": shop_fingerprint()}).encode("utf-8")
    try:
        status, _ct, raw = _request(host, port, "POST", "/lan/pair", body,
                                    {"Content-Type": "application/json"}, timeout=20)
    except Unreachable:
        return {"ok": False, "code": "unreachable",
                "error": f"No Main PC answered at {host}:{port}. Check the cable/switch, that the Main PC "
                         "is on with Satpuda open, and its firewall."}
    except LostAnswer as exc:
        return {"ok": False, "code": "unreachable", "error": f"The Main PC did not answer: {exc}"}
    try:
        out = json.loads(raw.decode("utf-8"))
    except ValueError:
        out = {}
    if status != 200 or not out.get("ok") or not out.get("token"):
        return {"ok": False, "code": out.get("code") or "refused",
                "error": out.get("error") or f"The Main PC refused (HTTP {status})."}

    def change(cfg):
        cfg["role"] = config.ROLE_COUNTER
        cfg["counter"] = {
            "main_host": host, "main_port": int(port), "token": out["token"],
            "main_name": out.get("main_name") or "", "shop_name": out.get("shop") or "",
            "main_install_id": out.get("main_install_id") or "", "device_id": out.get("device_id") or "",
            "paired_at": _now_iso(), "pending_code": "", "pair_error": "",
        }
        return cfg

    config.update(change)
    return {"ok": True, "main_name": out.get("main_name") or "", "shop": out.get("shop") or "",
            "host": host, "port": int(port)}


class CounterLink:
    def __init__(self, *, conn: Callable[[], Any], db_path: Callable[[], str],
                 discovery_port: int = config.DEFAULT_DISCOVERY_PORT):
        self.conn = conn
        self.db_path = db_path
        self.discovery_port = int(discovery_port)
        self._load()
        self.up = False
        self.removed = False
        self.last_ok = 0.0
        self.down_since = time.time()
        self.last_error = ""
        self.hint_seq = -1
        self.hint_epoch = ""
        self.last_sync = 0.0
        self.last_handover: dict = {}
        self.last_feed: dict = {}
        self.last_sale_id = 0
        self._stop = threading.Event()
        self._bundles: "queue.Queue[tuple[str, int]]" = queue.Queue()
        self._sync_lock = threading.Lock()
        self.thread: Optional[threading.Thread] = None

    # ------------------------------------------------------------------ config
    def _load(self) -> None:
        cfg = config.load()
        c = cfg.get("counter") or {}
        self.host = str(c.get("main_host") or "").strip()
        try:
            self.port = int(c.get("main_port") or cfg.get("port") or config.DEFAULT_PORT)
        except (TypeError, ValueError):
            self.port = config.DEFAULT_PORT
        if ":" in self.host:            # typed as "192.168.1.10:8775"
            self.host, _, p = self.host.rpartition(":")
            if p.isdigit():
                self.port = int(p)
        self.token = str(c.get("token") or "")
        self.main_name = str(c.get("main_name") or "")
        self.main_id = str(c.get("main_install_id") or c.get("device_id") or self.host)
        self.pending_code = str(c.get("pending_code") or "")
        self.pair_error = str(c.get("pair_error") or "")

    @property
    def paired(self) -> bool:
        return bool(self.host and self.token)

    def _headers(self, extra: Optional[dict] = None) -> dict:
        h = {TOKEN_HEADER: self.token}
        h.update(extra or {})
        return h

    def _mark_down(self, why: str) -> None:
        if self.up:
            self.down_since = time.time()
        self.up = False
        self.last_error = why

    # ------------------------------------------------------------------ the engine's hook
    def handle(self, handler, method: str, path: str) -> bool:
        """True when the request was answered from the Main PC (or its failure answered).
        False: the engine answers it itself from this PC's copy (rfile is rewound)."""
        body = b""
        if method in ("POST", "PUT"):
            n = int(handler.headers.get("Content-Length") or 0)
            body = handler.rfile.read(n) if n > 0 else b""
            handler.rfile = io.BytesIO(body)
        if is_local(path) or not self.paired:
            return False
        data: dict = {}
        if body:
            try:
                parsed = json.loads(body.decode("utf-8"))
                data = parsed if isinstance(parsed, dict) else {}
            except ValueError:
                data = {}
        if str(data.get("print_to") or "").strip():
            return False                      # a report printed here, from this PC's copy
        if not self.up:
            return False                      # failover: this PC's own copy
        if path in PRINT_PATHS and method == "POST":
            status, payload = self._print(path, data)
            self._reply_json(handler, status, payload)
            return True
        target = handler.path
        if path == "/api/sales/last" and self.last_sale_id:
            # "Last bill" / F9 on a counter is THIS counter's last bill, not the shop's.
            target = f"/api/sales/load?id={int(self.last_sale_id)}"
        try:
            status, ctype, raw = _request(self.host, self.port, method, target,
                                          body if method in ("POST", "PUT") else None,
                                          self._headers({"Content-Type": handler.headers.get("Content-Type")
                                                         or "application/json", FILES_HEADER: "1"}))
        except Unreachable as exc:
            self._mark_down(str(exc))
            return False
        except LostAnswer as exc:
            self._mark_down(str(exc))
            if method == "GET":
                return False
            self._reply_json(handler, 502, {
                "ok": False, "code": "main_pc_no_answer",
                "error": "The Main PC stopped answering while this was being saved. It may have been "
                         "saved there: check Sales / Purchase History before saving it again.",
            })
            return True
        if status == 401:
            self.removed = True
            self._mark_down("not paired")
            return False
        self.removed = False
        if "json" in ctype.lower() and raw:
            try:
                payload = json.loads(raw.decode("utf-8"))
                if isinstance(payload, dict) and payload.get("_lan_files"):
                    from core.lan import files

                    payload = files.receive(payload)
                    raw = json.dumps(payload, default=str).encode("utf-8")
                if method == "POST" and status == 200 and path in _BUNDLE_AFTER and isinstance(payload, dict):
                    col, keys = _BUNDLE_AFTER[path]
                    bid = 0
                    for key in keys:
                        try:
                            bid = int(payload.get(key) or 0)
                        except (TypeError, ValueError):
                            bid = 0
                        if bid:
                            break
                    if bid > 0 and not payload.get("skipped"):
                        self._bundles.put((col, bid))
                        if path in ("/api/sales/save", "/api/wholesale/sale/save"):
                            self.last_sale_id = bid
            except ValueError:
                pass
        self.last_ok = time.time()
        self._reply(handler, status, ctype, raw)
        return True

    @staticmethod
    def _reply(handler, status: int, ctype: str, raw: bytes) -> None:
        handler.send_response(status)
        handler.send_header("Content-Type", ctype)
        handler.send_header("Access-Control-Allow-Origin", "*")
        handler.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, OPTIONS")
        handler.send_header("Access-Control-Allow-Headers", "Content-Type")
        handler.send_header("Content-Length", str(len(raw)))
        handler.end_headers()
        if status != 204:
            handler.wfile.write(raw)

    def _reply_json(self, handler, status: int, payload: Any) -> None:
        self._reply(handler, status, "application/json; charset=utf-8",
                    json.dumps(payload, default=str).encode("utf-8"))

    # ------------------------------------------------------------------ talking to the Main PC
    def call(self, method: str, path: str, payload: Any = None, timeout: float = 60.0) -> tuple[int, Any]:
        body = None if payload is None else json.dumps(payload, default=str).encode("utf-8")
        status, _ct, raw = _request(self.host, self.port, method, path, body,
                                    self._headers({"Content-Type": "application/json"}), timeout=timeout)
        try:
            out = json.loads(raw.decode("utf-8")) if raw else {}
        except ValueError:
            out = {"ok": False, "error": raw[:200].decode("utf-8", "replace")}
        if status == 401:
            self.removed = True
        return status, out

    def fetch_bundle(self, collection: str, local_id: int) -> int:
        from core.lan import bridge

        status, out = self.call("GET", f"/lan/bundle?collection={collection}&id={int(local_id)}", timeout=30)
        docs = out.get("docs") or [] if status == 200 else []
        path = self.db_path()
        if docs and path:
            bridge.apply_docs(path, docs)
        return len(docs)

    # ------------------------------------------------------------------ printing here
    def _print(self, path: str, body: dict) -> tuple[int, dict]:
        """Save on the Main PC when the bill is new, copy the bill into this PC's copy, then print
        it with this PC's own print code, settings and printer."""
        from core import desktop_sales_service as sales_svc

        conn = self.conn()
        if conn is None:
            return 503, {"ok": False, "error": "Database not open"}
        if path == "/api/sales/print-all":
            for sid in body.get("sale_ids") or []:
                try:
                    self.fetch_bundle("sales", int(sid))
                except (Unreachable, LostAnswer, ValueError, TypeError):
                    pass
            result = sales_svc.print_all_sales(conn, body)
            return (200 if result.get("ok") else 400), result

        def num(v) -> int:
            try:
                return int(v or 0)
            except (TypeError, ValueError):
                return 0

        sale_id = num(body.get("sale_id")) or num(body.get("editing_sale_id"))
        saved: dict = {}
        if body.get("save_first") or (sale_id <= 0 and body.get("items")):
            try:
                status, saved = self.call("POST", "/api/sales/save", {**body, "sync_save": True}, timeout=300)
            except Unreachable as exc:
                self._mark_down(str(exc))
                return 503, {"ok": False, "code": "main_pc_unreachable",
                             "error": "The Main PC went off just now. Press print again: the bill will be "
                                      "saved and printed on this PC."}
            except LostAnswer as exc:
                self._mark_down(str(exc))
                return 502, {"ok": False, "code": "main_pc_no_answer",
                             "error": "The Main PC stopped answering while saving. Check Sales History "
                                      "before saving this bill again."}
            if status != 200 or not saved.get("ok"):
                return status if status != 200 else 400, saved
            sale_id = num(saved.get("sale_id"))
            if sale_id > 0:
                self.last_sale_id = sale_id
        if sale_id <= 0:
            return 400, {"ok": False, "error": "No sale to print.", "code": "no_sale"}
        try:
            self.fetch_bundle("sales", sale_id)
        except (Unreachable, LostAnswer) as exc:
            # Printed from this PC's copy as it is (the feed copies the Main PC's bills too).
            self.last_error = f"bill copy: {exc}"
        local_body = {k: v for k, v in body.items() if k not in ("save_first", "items")}
        local_body["sale_id"] = sale_id
        if saved.get("bill_no"):
            local_body["bill_no"] = saved["bill_no"]
        result = sales_svc.print_sale(conn, local_body, db_path=self.db_path())
        if saved:
            result["cleared"] = bool(body.get("save_first"))
            result["warnings"] = list(saved.get("warnings") or [])
            if saved.get("next_bill_hint"):
                result["next_bill_hint"] = saved["next_bill_hint"]
            for k in ("sale_id", "bill_no"):
                if saved.get(k):
                    result[k] = saved[k]
        status = 200 if result.get("ok") else 400
        if result.get("need_confirm"):
            status = 409
        return status, result

    # ------------------------------------------------------------------ the link thread
    def start(self) -> None:
        if self.thread and self.thread.is_alive():
            return
        self._stop.clear()
        self.thread = threading.Thread(target=self._loop, name="LanCounter", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._bundles.put(("", 0))
        if self.thread:
            self.thread.join(5)

    def _try_pending_pair(self) -> None:
        if not self.pending_code:
            return
        host, port = self.host, self.port
        if not host:
            found = [m for m in discover(self.discovery_port) if m["same_shop"]]
            if len(found) != 1:
                self.pair_error = ("No Main PC of this shop found on the LAN yet." if not found else
                                   "More than one Main PC found; pick one in Settings.")
                return
            host, port = found[0]["host"], found[0]["port"]
        res = pair(host, port, self.pending_code)
        if res.get("ok"):
            self._load()
            return
        if res.get("code") in ("bad_code", "other_shop"):
            msg = str(res.get("error") or "")

            def change(cfg):
                cfg["counter"]["pending_code"] = ""
                cfg["counter"]["pair_error"] = msg
                return cfg

            config.update(change)
            self._load()
        else:
            self.pair_error = str(res.get("error") or "")

    def _poll_hints(self) -> bool:
        status, out = self.call(
            "GET", f"/lan/hints?after={self.hint_seq}&epoch={self.hint_epoch}", timeout=5)
        if status != 200:
            if status == 401:
                self._mark_down("not paired")
            return False
        first = self.hint_seq < 0
        self.hint_seq = int(out.get("seq") or 0)
        self.hint_epoch = str(out.get("epoch") or "")
        cols = out.get("collections") or []
        if cols and not first:
            try:
                from core.sync_status import note_collection_change

                for col in cols:
                    note_collection_change(str(col))
            except Exception:
                pass
        return True

    def sync_now(self) -> dict:
        """Hand this PC's unsent events to the Main PC, then copy the Main PC's changes here."""
        from core.lan import bridge

        path = self.db_path()
        if not path:
            return {"ok": False, "error": "no store open"}
        with self._sync_lock:
            handed = 0
            for _ in range(20):
                install, events = bridge.handover_batch(path)
                if not events:
                    break
                status, out = self.call("POST", "/lan/handover", {"install_id": install, "events": events},
                                        timeout=120)
                if status != 200:
                    raise LostAnswer(out.get("error") or f"handover HTTP {status}")
                bridge.mark_handed(path, events, out.get("accepted") or [], out.get("results") or [])
                handed += len(out.get("accepted") or [])
                if len(events) < bridge.HANDOVER_PAGE:
                    break
            if not handed and int(bridge.counter_counts(path).get("waiting") or 0):
                # The server's answers for events handed over earlier.
                install, _ = bridge.handover_batch(path, limit=0)
                if install:
                    status, out = self.call("POST", "/lan/handover", {"install_id": install, "events": []},
                                            timeout=60)
                    if status == 200 and out.get("results"):
                        bridge.mark_handed(path, [], [], out["results"])
            pulled = 0
            after, relay_after = bridge.feed_cursors(path, self.main_id)
            for _ in range(25):
                status, page = self.call("GET", f"/lan/feed?after={after}&relay_after={relay_after}", timeout=60)
                if status != 200:
                    break
                new_after, new_relay = int(page.get("cursor") or 0), int(page.get("relay_cursor") or 0)
                if page.get("events") or page.get("relay_events") or (new_after, new_relay) != (after, relay_after):
                    res = bridge.apply_feed(path, page)
                    pulled += int(res.get("new_events") or 0)
                after, relay_after = new_after, new_relay
                if not page.get("has_more"):
                    break
            self.last_sync = time.time()
            self.last_handover = {"at": _now_iso(), "handed": handed}
            self.last_feed = {"at": _now_iso(), "applied": pulled, "cursor": after}
            return {"ok": True, "handed": handed, "pulled": pulled}

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                if not self.paired:
                    self._try_pending_pair()
                    self._stop.wait(PAIR_RETRY)
                    continue
                was_up = self.up
                ok = self._poll_hints()
                if ok and not was_up:
                    # Back: what this PC made meanwhile goes to the Main PC first.
                    self.sync_now()
                    self.up = True
                    self.removed = False
                    self.last_ok = time.time()
                    self.last_error = ""
                elif ok:
                    self.last_ok = time.time()
                    if time.time() - self.last_sync >= SYNC_EVERY:
                        self.sync_now()
                elif not ok:
                    self._mark_down(self.last_error or "no answer")
            except (Unreachable, LostAnswer) as exc:
                self._mark_down(str(exc))
            except Exception as exc:  # the link never dies of one bad page
                self.last_error = f"{type(exc).__name__}: {exc}"
            # Bills to copy here at once, else wait for the next round.
            deadline = time.time() + (HINT_EVERY if self.up else 2.0)
            while not self._stop.is_set():
                left = deadline - time.time()
                if left <= 0:
                    break
                try:
                    col, bid = self._bundles.get(timeout=left)
                except queue.Empty:
                    break
                if col and bid and self.up:
                    try:
                        self.fetch_bundle(col, bid)
                    except Exception as exc:
                        self.last_error = f"bill copy: {exc}"

    # ------------------------------------------------------------------ status
    def status(self) -> dict:
        from core.lan import bridge

        counts: dict = {}
        try:
            path = self.db_path()
            if path:
                counts = bridge.counter_counts(path)
        except Exception:
            counts = {}
        if not self.paired:
            banner = BANNER_UNPAIRED
        elif self.removed:
            banner = BANNER_REMOVED
        elif not self.up:
            banner = BANNER_DOWN
        else:
            banner = ""
        return {
            "paired": self.paired,
            "main_host": self.host,
            "main_port": self.port,
            "main_name": self.main_name,
            "main_reachable": bool(self.up),
            "last_ok": (datetime.fromtimestamp(self.last_ok, timezone.utc).isoformat(timespec="seconds")
                        if self.last_ok else None),
            "down_seconds": int(time.time() - self.down_since) if not self.up else 0,
            "last_error": self.last_error,
            "pending_code": bool(self.pending_code),
            "pair_error": self.pair_error,
            "waiting_for_main": int(counts.get("not_handed") or 0),
            "last_handover": self.last_handover,
            "last_feed": self.last_feed,
            "banner": banner,
        }
