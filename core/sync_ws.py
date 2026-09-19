"""WebSocket client for Option B sync_hint (revision only — never payloads)."""
from __future__ import annotations

import json
import logging
import threading
import time
from typing import Callable, Optional
from urllib.parse import quote, urlparse, urlunparse

log = logging.getLogger(__name__)

OnHintCb = Callable[..., None]  # (head, source, changes=None, full_refresh=False)
OnConnectCb = Callable[[], None]

_HINT_TYPE = "sync_hint"


def _ws_url(api_base: str, token: str) -> str:
    # Derived from server_api's base so the socket follows the REST client onto
    # the direct host -- and onto the tunnel if health_ok() failed over. No
    # hostname is written here: a literal fallback used to name the Cloudflare
    # tunnel, which would have pinned the socket to the ~460ms-per-call route
    # (direct is ~85ms) while every REST call went direct.
    from core.server_api import _DEFAULT_BASE

    parsed = urlparse((api_base or "").rstrip("/") or _DEFAULT_BASE)
    scheme = "wss" if (parsed.scheme or "https").lower() == "https" else "ws"
    netloc = parsed.netloc or urlparse(_DEFAULT_BASE).netloc
    path = (parsed.path or "").rstrip("/") + "/ws/sync"
    return urlunparse((scheme, netloc, path, "", f"token={quote(token, safe='')}", ""))


class SyncWebSocket:
    """Background reconnecting WebSocket that delivers sync_hint callbacks."""

    def __init__(self) -> None:
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._on_hint: Optional[OnHintCb] = None
        self._on_connect: Optional[OnConnectCb] = None
        self._token_fn: Optional[Callable[[], str]] = None
        self._lock = threading.Lock()
        self._connected = False

    @property
    def connected(self) -> bool:
        return self._connected

    def start(
        self,
        token_fn: Callable[[], str],
        on_hint: OnHintCb,
        on_connect: Optional[OnConnectCb] = None,
    ) -> None:
        with self._lock:
            self._token_fn = token_fn
            self._on_hint = on_hint
            self._on_connect = on_connect
            self._stop.clear()
            if self._thread and self._thread.is_alive():
                return
            self._thread = threading.Thread(
                target=self._loop, daemon=True, name="SyncWebSocket"
            )
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._connected = False
        thread = None
        with self._lock:
            thread = self._thread
            self._thread = None
        if thread and thread.is_alive() and thread is not threading.current_thread():
            try:
                thread.join(timeout=2.0)
            except Exception:
                pass

    def _loop(self) -> None:
        backoff = 1.0
        while not self._stop.is_set():
            try:
                token_fn = self._token_fn
                if not token_fn:
                    self._stop.wait(2.0)
                    continue
                token = (token_fn() or "").strip()
                if not token:
                    self._stop.wait(5.0)
                    continue
                from core.server_api import api_base

                url = _ws_url(api_base(), token)
                self._run_once(url)
                backoff = 1.0
            except Exception as exc:
                self._connected = False
                log.warning("sync_ws: %s", exc)
                self._stop.wait(backoff)
                backoff = min(30.0, backoff * 1.7)

    def _run_once(self, url: str) -> None:
        try:
            import websocket  # websocket-client
        except ImportError as exc:
            raise RuntimeError(
                "websocket-client not installed (pip install websocket-client)"
            ) from exc

        def on_open(ws):
            self._connected = True
            try:
                ws.send(json.dumps({"type": "subscribe"}))
            except Exception:
                pass
            log.info("[SYNC][HINT] sync_ws connected")
            cb = self._on_connect
            if cb:
                try:
                    cb()
                except Exception as exc:
                    log.warning("[SYNC][HINT] on_connect: %s", exc)

        def on_message(_ws, message):
            try:
                msg = json.loads(message)
            except Exception:
                return
            if not isinstance(msg, dict):
                return
            if msg.get("type") == _HINT_TYPE:
                try:
                    head = int(msg.get("head_revision") or 0)
                except (TypeError, ValueError):
                    return
                src = msg.get("source_device_id")
                changes = msg.get("changes") if isinstance(msg.get("changes"), list) else []
                full_refresh = bool(msg.get("full_refresh"))
                cb = self._on_hint
                if cb:
                    try:
                        cb(
                            head,
                            src if isinstance(src, str) else None,
                            changes,
                            full_refresh,
                        )
                    except TypeError:
                        # Back-compat 2-arg callbacks
                        try:
                            cb(head, src if isinstance(src, str) else None)
                        except Exception as exc:
                            log.warning("sync_ws hint callback: %s", exc)
                    except Exception as exc:
                        log.warning("sync_ws hint callback: %s", exc)
            elif msg.get("type") == "ping":
                try:
                    _ws.send(json.dumps({"type": "pong"}))
                except Exception:
                    pass

        def on_error(_ws, error):
            log.debug("sync_ws error: %s", error)

        def on_close(_ws, *_args):
            self._connected = False
            log.info("sync_ws closed")

        ws_app = websocket.WebSocketApp(
            url,
            on_open=on_open,
            on_message=on_message,
            on_error=on_error,
            on_close=on_close,
        )

        # run_forever blocks until disconnect; ping keeps NAT/tunnel alive
        while not self._stop.is_set():
            try:
                ws_app.run_forever(ping_interval=25, ping_timeout=10)
            except Exception as exc:
                log.warning("sync_ws run_forever: %s", exc)
            self._connected = False
            if self._stop.is_set():
                break
            time.sleep(1.0)
            break  # outer loop handles reconnect backoff


_default: Optional[SyncWebSocket] = None
_default_lock = threading.Lock()


def get_sync_ws() -> SyncWebSocket:
    global _default
    with _default_lock:
        if _default is None:
            _default = SyncWebSocket()
        return _default
