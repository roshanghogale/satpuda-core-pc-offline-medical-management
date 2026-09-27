"""HTTP client for Satpuda Core Server (replaces Server for online sync)."""
from __future__ import annotations

import base64
import contextlib
import datetime as _dt
import http.client
import json
import os
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Optional


class ServerHttpError(RuntimeError):
    def __init__(self, status: int, message: str):
        super().__init__(f"HTTP {status}: {message}")
        self.status = status
        self.message = message


class AdminCredentialRequired(RuntimeError):
    """Raised when something asks for the vendor administrator with no credential.

    The build used to carry the vendor's administrator username and password as
    a literal default, so five unattended code paths signed a shop's PC in as
    the ADMINISTRATOR of every store on the account (see the live access log:
    /api/auth/admin/login answered 200 for shop PCs on four days running). Any
    shopkeeper with a text editor had the password to every other shop's
    licence, expiry and ledger.

    The default is gone. A vendor administrator token now exists only while a
    PERSON has typed the username and password in this session, and everything
    a shop PC does by itself -- setup, pairing, the launch-time store link --
    goes through the store's own SC- pairing key instead.
    """

    DEFAULT_MESSAGE = (
        "This action needs the Satpuda administrator username and password, "
        "typed now. The software does not keep them."
    )

    def __init__(self, message: str = ""):
        super().__init__(message or self.DEFAULT_MESSAGE)


def _json_default(obj: Any):
    if isinstance(obj, (_dt.datetime, _dt.date)):
        return obj.isoformat()
    if isinstance(obj, bytes):
        return obj.decode("utf-8", errors="replace")
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


try:
    import orjson as _orjson
except ImportError:
    _orjson = None


def _json_dumps(obj: Any) -> bytes:
    if _orjson is not None:
        return _orjson.dumps(obj, default=_json_default)
    return json.dumps(obj, default=_json_default).encode("utf-8")


def _json_loads(raw: Any) -> Any:
    if not raw:
        return {}
    if _orjson is not None:
        if isinstance(raw, str):
            raw = raw.encode("utf-8")
        return _orjson.loads(raw)
    if isinstance(raw, (bytes, bytearray)):
        return json.loads(raw.decode("utf-8", errors="replace"))
    return json.loads(raw)


# Direct HTTPS to the VPS, not the Cloudflare tunnel. Measured from a shop PC in
# Maharashtra: tunnel ~460ms per call, direct ~85ms -- Cloudflare routes the
# tunnel hostname to a distant PoP even though origin and shop are in the same
# state, and every screen pays that ~375ms on every server call. Do NOT
# "simplify" this back to one name for both clients: the second name is the
# whole point.
#
# _TUNNEL_BASE reaches the same server and is the fallback that keeps a shop
# running when the direct host is unreachable -- health_ok() switches to it for
# the life of the process, and a shop or the owner can pin it permanently by
# putting it in "api_base" in server_api_prefs.json (see migrate_api_base).
_NEW_HOST = "srv1970994.hstgr.cloud"
_DEFAULT_BASE = f"https://{_NEW_HOST}"
_TUNNEL_BASE = "https://api.satpudacore.online"

# The VPS moved. srv1892850 is the old box: right now it is a Caddy bridge
# forwarding to srv1970994, and it stops answering the day that VPS lapses. An
# already-installed shop has that hostname written into server_api_prefs.json in
# AppData, and the stored value beats the default -- so shipping a new
# _DEFAULT_BASE alone would change nothing for them. Hence migrate_api_base().
_RETIRED_HOSTS = {"srv1892850.hstgr.cloud"}

_PREFS = "server_api_prefs.json"
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36 SatpudaCoreMac2/1.0"
)

# TLS context built LAZILY from the certifi bundle.
#
# This used to be `ssl.create_default_context()` evaluated at import time, which
# ignored core/ssl_utils entirely. configure_ssl_certificates() is called by
# main.py (classic UI) but NOT by run_desktop_api.py (Tauri), so on the Tauri
# desktop — and on frozen Windows builds where the system CA store is not what
# Python expects — every HTTPS call failed with CERTIFICATE_VERIFY_FAILED. The
# app then logged "Server health check failed" and silently dropped to offline,
# which looks to the user like "it just stopped syncing".
_ssl_ctx_cache = None


def _ssl_ctx_get():
    global _ssl_ctx_cache
    if _ssl_ctx_cache is None:
        try:
            from core.ssl_utils import configure_ssl_certificates, ssl_context
            configure_ssl_certificates()
            _ssl_ctx_cache = ssl_context()
        except Exception:
            _ssl_ctx_cache = ssl.create_default_context()
    return _ssl_ctx_cache


def _appdata_dir() -> str:
    from core.license_manager import _appdata_dir as d
    return d()


def prefs_path() -> str:
    return os.path.join(_appdata_dir(), _PREFS)


def _split_base(raw: str) -> urllib.parse.SplitResult:
    text = raw.strip()
    if "://" not in text:
        text = "https://" + text
    return urllib.parse.urlsplit(text)


def migrate_api_base(value: Optional[str]) -> str:
    """Correct a stored base that names a hostname we know is being switched off.

    THE RULE, deliberately narrow: rewrite ONLY the host, and ONLY when that host
    is in _RETIRED_HOSTS. Scheme, port, path, query are carried across untouched.
    Everything else is returned exactly as stored, in particular:

      * https://api.satpudacore.online -- the Cloudflare tunnel. Slower, but it
        works and it reaches the same server, so it is the escape hatch a shop
        or the owner uses when the direct host is unreachable. Rewriting it
        would take that escape hatch away.
      * a LAN address, a localhost test base, a custom port or a path prefix,
        or any other hostname -- somebody typed that on purpose.

    So a dead name is corrected; a live choice is never overruled.
    """
    raw = (value or "").strip()
    if not raw:
        return raw
    parts = _split_base(raw)
    if (parts.hostname or "").lower() not in _RETIRED_HOSTS:
        return raw
    netloc = _NEW_HOST
    if parts.port:
        netloc = f"{netloc}:{parts.port}"
    if "@" in parts.netloc:
        netloc = parts.netloc.rsplit("@", 1)[0] + "@" + netloc
    fixed = urllib.parse.urlunsplit(
        (parts.scheme or "https", netloc, parts.path, parts.query, parts.fragment)
    )
    return fixed.rstrip("/")


_migration_written = False


def _persist_base_migration(old: str, new: str) -> None:
    """Write the corrected host back to disk, once per process.

    Migrating in memory only would be a silent override: the owner would open
    server_api_prefs.json, read the retired host, and have no way to know the app
    was calling somewhere else. Writing it back -- with a note of what it was and
    when -- leaves an honest file and an audit trail.
    """
    global _migration_written
    if _migration_written:
        return
    _migration_written = True
    try:
        p = prefs_path()
        if not os.path.isfile(p):
            return
        with open(p, encoding="utf-8-sig") as fh:
            raw = json.load(fh)
        if not isinstance(raw, dict):
            return
        raw["api_base"] = new
        raw["api_base_migrated_from"] = old
        raw["api_base_migrated_on"] = _dt.date.today().isoformat()
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(raw, fh, indent=2)
    except Exception:
        # A read-only AppData or a file held open by another copy of the app must
        # not stop the shop talking to the server. The in-memory migration has
        # already happened; this is only the paper trail.
        pass


def load_prefs() -> dict:
    p = prefs_path()
    # NO administrator credential here, and never again.
    #
    # These two keys used to default to the vendor's real administrator
    # username and password, and ``admin_login`` fell back to them -- so every
    # copy of the build could sign in as the administrator of every shop on the
    # account. The keys are not written, not defaulted and not read by
    # admin_login any more; an admin token comes only from a person typing the
    # credentials at that moment (see ``AdminCredentialRequired``).
    data = {
        "api_base": _DEFAULT_BASE,
    }
    stored_base = None
    try:
        if os.path.isfile(p):
            with open(p, encoding="utf-8-sig") as fh:
                raw = json.load(fh)
            if isinstance(raw, dict):
                data.update({k: v for k, v in raw.items() if v is not None})
                stored_base = raw.get("api_base")
    except Exception:
        pass
    if stored_base:
        migrated = migrate_api_base(str(stored_base))
        if migrated != str(stored_base).strip():
            data["api_base"] = migrated
            _persist_base_migration(str(stored_base).strip(), migrated)
    return data


def save_prefs(patch: dict) -> None:
    data = load_prefs()
    data.update(patch or {})
    os.makedirs(_appdata_dir(), exist_ok=True)
    with open(prefs_path(), "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)


def configured_base() -> str:
    """What this PC is set to call, after migration and before any failover."""
    return (load_prefs().get("api_base") or _DEFAULT_BASE).rstrip("/")


# Set only by health_ok() when the configured host does not answer and the other
# name does. Process-only and never written to prefs: restarting the app goes
# back to the configured (fast, direct) host and re-tests it.
_failover_base: Optional[str] = None


def clear_failover() -> None:
    global _failover_base
    _failover_base = None


def api_base() -> str:
    return _failover_base or configured_base()


def _session_path(store_key: str) -> str:
    safe = (store_key or "default").replace(os.sep, "_").replace("/", "_")
    return os.path.join(_appdata_dir(), f"server_session_{safe}.json")


def load_session(store_key: str) -> dict:
    p = _session_path(store_key)
    try:
        if os.path.isfile(p):
            with open(p, encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {}


def save_session(store_key: str, data: dict) -> None:
    os.makedirs(_appdata_dir(), exist_ok=True)
    with open(_session_path(store_key), "w", encoding="utf-8") as fh:
        json.dump(data or {}, fh, indent=2)


def clear_session(store_key: str) -> None:
    try:
        os.remove(_session_path(store_key))
    except OSError:
        pass


# Connections are pooled ACROSS threads, not per thread. The desktop engine is a
# ThreadingHTTPServer: every /api/... request arrives on a brand-new thread, so a
# thread-local connection was never once reused -- each engine request paid a
# fresh TCP + TLS handshake to the remote server before it sent a byte.
_POOL_LOCK = threading.Lock()
_POOL: dict[tuple, list] = {}
_POOL_MAX_PER_KEY = 8
_POOL_MAX_IDLE_SEC = 60.0


def _conn_key(parsed: urllib.parse.ParseResult) -> tuple:
    host = parsed.hostname or "localhost"
    is_https = (parsed.scheme or "https").lower() == "https"
    return (host, parsed.port or (443 if is_https else 80), is_https)


def _shutdown_conn(conn) -> None:
    try:
        conn.close()
    except Exception:
        pass


def _close_conn() -> None:
    """Drop every pooled connection (kept for callers that force a reconnect)."""
    with _POOL_LOCK:
        pooled = [c for conns in _POOL.values() for c, _ in conns]
        _POOL.clear()
    for conn in pooled:
        _shutdown_conn(conn)


def _acquire_conn(key: tuple):
    """Take a live connection for `key` from the pool, or build a fresh one."""
    now = time.monotonic()
    stale = []
    with _POOL_LOCK:
        conns = _POOL.get(key) or []
        while conns:
            conn, idle_since = conns.pop()
            # A connection parked longer than the server's keep-alive timeout is
            # probably already half-closed; reusing it costs a failed round trip.
            if now - idle_since <= _POOL_MAX_IDLE_SEC:
                _POOL[key] = conns
                return conn
            stale.append(conn)
        _POOL[key] = conns
    for conn in stale:
        _shutdown_conn(conn)
    host, port, is_https = key
    if is_https:
        return http.client.HTTPSConnection(host, port, timeout=120, context=_ssl_ctx_get())
    return http.client.HTTPConnection(host, port, timeout=120)


def _release_conn(key: tuple, conn) -> None:
    """Park a still-healthy connection for the next request on any thread."""
    with _POOL_LOCK:
        conns = _POOL.setdefault(key, [])
        if len(conns) >= _POOL_MAX_PER_KEY:
            drop = conn
        else:
            conns.append((conn, time.monotonic()))
            drop = None
    if drop is not None:
        _shutdown_conn(drop)


def _get_conn(parsed: urllib.parse.ParseResult):
    return _acquire_conn(_conn_key(parsed))


_DEADLINE = threading.local()


@contextlib.contextmanager
def request_deadline(deadline: Optional[float]):
    """Every request made inside this block gives up by ``deadline`` (a time.monotonic() value).

    For a caller that promised to wait no longer than a budget -- the back-dated batch check
    runs while the counter waits. _request tries a timed-out request a second time, and it
    used to give that second try the full timeout again, so a 15-second budget became 24.
    """
    before = getattr(_DEADLINE, "value", None)
    _DEADLINE.value = deadline
    try:
        yield
    finally:
        _DEADLINE.value = before


def _request(
    method: str,
    path: str,
    *,
    body: Any = None,
    token: Optional[str] = None,
    timeout: float = 120.0,
    base: Optional[str] = None,
    deadline: Optional[float] = None,
) -> dict:
    if deadline is None:
        deadline = getattr(_DEADLINE, "value", None)
    base = (base or api_base()).rstrip("/")
    parsed = urllib.parse.urlparse(base)
    prefix = (parsed.path or "").rstrip("/")
    full_path = f"{prefix}{path}" if path.startswith("/") else f"{prefix}/{path}"
    data = None
    headers = {
        "Accept": "application/json",
        "User-Agent": _UA,
        "Connection": "keep-alive",
    }
    if body is not None:
        data = _json_dumps(body)
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"

    key = _conn_key(parsed)
    last_exc: Exception | None = None
    for attempt in range(2):
        wait = timeout
        if deadline is not None:
            left = deadline - time.monotonic()
            if left <= 0:
                # The caller's time is used up. Another try would only wait past it.
                break
            wait = min(float(timeout), left)
        conn = None
        try:
            conn = _acquire_conn(key)
            conn.timeout = wait
            conn.request(method.upper(), full_path, body=data, headers=headers)
            resp = conn.getresponse()
            raw = resp.read()
            if resp.status >= 400:
                try:
                    parsed_err = _json_loads(raw) if raw else {}
                    msg = parsed_err.get("error") or (
                        raw.decode("utf-8", errors="replace") if isinstance(raw, (bytes, bytearray)) else raw
                    ) or f"HTTP {resp.status}"
                except Exception:
                    msg = (
                        raw.decode("utf-8", errors="replace")
                        if isinstance(raw, (bytes, bytearray))
                        else (raw or f"HTTP {resp.status}")
                    )
                if resp.status in (408, 425, 502, 503, 504) or resp.will_close:
                    _shutdown_conn(conn)
                else:
                    _release_conn(key, conn)
                conn = None
                raise ServerHttpError(resp.status, msg)
            if resp.will_close:
                _shutdown_conn(conn)
            else:
                _release_conn(key, conn)
            conn = None
            return _json_loads(raw) if raw else {"ok": True}
        except ServerHttpError:
            raise
        except (http.client.HTTPException, OSError, TimeoutError) as exc:
            last_exc = exc
            if conn is not None:
                _shutdown_conn(conn)
                conn = None
            if attempt >= 1:
                break
    if last_exc:
        raise RuntimeError(f"Cannot reach server ({base}): {last_exc}") from last_exc
    raise RuntimeError(f"Cannot reach server ({base})")


def health() -> dict:
    return _request("GET", "/api/health", timeout=15)


# A failover must never be triggered by an impatient probe. Settings calls
# health_ok(timeout=0.4) just to colour a label, and a 0.4s blip on a healthy
# direct host is not a reason to move the whole session onto the ~375ms-slower
# tunnel. Only a probe given a real chance to answer may switch the base.
_FAILOVER_MIN_TIMEOUT = 3.0


def _probe(base: str, timeout: float) -> bool:
    try:
        res = _request("GET", "/api/health", timeout=timeout, base=base)
        return bool(res.get("ok"))
    except Exception:
        return False


def _alternate_base(current: str) -> str:
    """The other name for the same server, to try when `current` is down."""
    configured = configured_base()
    if current != configured:
        # We are already on the fallback -- the way home is the configured host.
        return configured
    return _TUNNEL_BASE if current != _TUNNEL_BASE else _DEFAULT_BASE


def health_ok(*, timeout: float = 8.0) -> bool:
    """Is the server reachable? Also the one place the tunnel fallback lives.

    The direct host is ~375ms/call faster and is what we call. But a shop whose
    direct host is unreachable must not simply stop working, so if the probe
    fails we try the other name for the same server and, if that answers, stay
    on it for the life of this process. Nothing is written to disk: a restart
    goes back to the fast host. To pin the tunnel permanently, put
    "api_base": "https://api.satpudacore.online" in server_api_prefs.json.
    """
    global _failover_base
    current = api_base()
    if _probe(current, timeout):
        return True
    if timeout < _FAILOVER_MIN_TIMEOUT:
        return False
    alternate = _alternate_base(current)
    if not alternate or alternate == current or not _probe(alternate, timeout):
        return False
    _failover_base = None if alternate == configured_base() else alternate
    return True


def admin_login(username: str, password: str) -> str:
    """Sign in as the vendor administrator. BOTH arguments are required.

    There is no default and no fallback -- not a literal, and not the prefs
    file either. A credential inside a build a shopkeeper can download is not a
    credential, and a credential in a JSON file next to it is no better: both
    let a shop PC take administrator access to every other shop on the account
    by itself. The only admin token this software can obtain is one a person
    asked for by typing the username and password a moment ago.

    Callers that have no person in front of them must not reach here at all.
    They use the store's own SC- pairing key (``/api/auth/pair``), which is
    scoped to that one store; see ``server_live.ensure_active_store_on_server``.
    """
    user = str(username or "").strip()
    pw = str(password or "")
    if not user or not pw:
        # Deliberately BEFORE the request: a blank sign-in must not even be a
        # line in the server's access log, and must never be retried with a
        # guess.
        raise AdminCredentialRequired()
    res = _request("POST", "/api/auth/admin/login", body={"username": user, "password": pw}, timeout=30)
    if not res.get("ok"):
        raise RuntimeError(res.get("error") or "Admin login failed")
    token = (res.get("data") or {}).get("token")
    if not token:
        raise RuntimeError("Admin login returned no token")
    return token


def create_store(
    admin_token: str,
    store_name: str,
    store_id: Optional[str] = None,
    store_key: Optional[str] = None,
) -> dict:
    body: dict[str, Any] = {"store_name": store_name}
    if store_id:
        body["store_id"] = store_id
    if store_key:
        body["store_key"] = store_key
    res = _request("POST", "/api/admin/stores", body=body, token=admin_token, timeout=30)
    if not res.get("ok"):
        raise RuntimeError(res.get("error") or "Create store failed")
    return res["data"]


def list_remote_stores(admin_token: str) -> list:
    res = _request("GET", "/api/admin/stores", token=admin_token, timeout=30)
    if not res.get("ok"):
        raise RuntimeError(res.get("error") or "List stores failed")
    return list(res.get("data") or [])


def regenerate_android_key(admin_token: str, store_id_or_pk: str) -> dict:
    res = _request(
        "POST",
        f"/api/admin/stores/{urllib.parse.quote(str(store_id_or_pk))}/regenerate-key",
        body={},
        token=admin_token,
        timeout=30,
    )
    if not res.get("ok"):
        raise RuntimeError(res.get("error") or "Regenerate key failed")
    return res.get("data") or {}


def pair_store(
    *,
    android_key: str,
    store_name: str = "",
    device_id: str,
    device_type: str = "pc",
    device_name: str = "Mac2 PC",
) -> dict:
    res = _request(
        "POST",
        "/api/auth/pair",
        body={
            "android_key": android_key,
            "store_name": store_name or None,
            "device_id": device_id,
            "device_type": device_type,
            "device_name": device_name,
        },
        timeout=30,
    )
    if not res.get("ok"):
        raise RuntimeError(res.get("error") or "Pair failed")
    return res["data"]


def provision_trial(
    *,
    store_name: str,
    device_id: str,
    machine_id: str = "",
    app_version: str = "",
    device_name: str = "",
    timeout: float = 60.0,
) -> dict:
    """Ask the server for a brand-new trial store. No credential of any kind.

    This is the whole of activation for a fresh install. Deliberately NOT
    admin_login + create_store: that path needs the vendor administrator
    password, which is exactly what must not be inside a build a shopkeeper can
    open with a text editor. This call carries nothing secret, because there is
    nothing secret it could carry that would still be secret once the installer
    is downloadable.

    The server answers with a store it has just CREATED. It never resolves the
    name to an existing store, so the name typed here cannot reach another
    shop's books -- see server src/services/provisionService.js.
    """
    body: dict[str, Any] = {
        "store_name": store_name,
        "device_id": device_id,
    }
    if machine_id:
        body["machine_id"] = machine_id
    if app_version:
        body["app_version"] = app_version
    if device_name:
        body["device_name"] = device_name
    # ``timeout`` is shortened by the Offline caller: there the sign-up only
    # puts the shop on the owner's Trials page, and the till must not wait a
    # minute for something it does not need to open.
    res = _request("POST", "/api/provision/trial", body=body, timeout=timeout)
    if not res.get("ok"):
        raise RuntimeError(res.get("error") or "Could not start the trial.")
    return res.get("data") or {}


def auth_me(token: str) -> dict:
    res = _request("GET", "/api/auth/me", token=token, timeout=20)
    if not res.get("ok"):
        raise RuntimeError(res.get("error") or "auth/me failed")
    return res.get("data") or {}


def get_license(token: str) -> dict:
    res = _request("GET", "/api/auth/license", token=token, timeout=20)
    if not res.get("ok"):
        raise RuntimeError(res.get("error") or "get license failed")
    return res.get("data") or {}


def put_license(token: str, payload: dict) -> dict:
    res = _request("PUT", "/api/auth/license", body=payload or {}, token=token, timeout=30)
    if not res.get("ok"):
        raise RuntimeError(res.get("error") or "update license failed")
    return res.get("data") or {}


def get_signed_license(token: str, binding: dict, *, timeout: float = 20.0) -> dict:
    """The store's licence, SIGNED, bound to this machine.

    POST rather than GET because the binding is a body of hashes, and because a
    hardware fingerprint has no business in a URL that a proxy will log.
    """
    res = _request(
        "POST", "/api/auth/license/signed", body=binding or {}, token=token, timeout=timeout
    )
    if not res.get("ok"):
        raise RuntimeError(res.get("error") or "signed license failed")
    return res.get("data") or {}


def recover_signed_license(binding: dict, *, timeout: float = 20.0) -> dict:
    """The licence this COMPUTER already has, without any credential.

    The path that makes deleting the licence file pointless. A wiped AppData
    takes the SC- key with it, so there is no token left to ask with -- but the
    hardware fingerprint is read off the machine rather than stored, so the
    server can still recognise the computer and hand back the same expiry it
    issued the first time.
    """
    res = _request("POST", "/api/provision/license", body=binding or {}, timeout=timeout)
    if not res.get("ok"):
        raise RuntimeError(res.get("error") or "licence recovery failed")
    return res.get("data") or {}


def admin_set_license(
    admin_token: str, store_id: str, payload: dict, *, timeout: float = 30.0
) -> dict:
    """Write a store's expiry AND get the signed blob back, in one call.

    One call on purpose: the server record and the file on the shop's PC are
    written from the same UPDATE, so they cannot end up describing two different
    licences. Needs an ADMINISTRATOR token -- the vendor's password, typed at the
    moment of the edit. A store token cannot reach this endpoint, which is what
    keeps a shop from extending itself.
    """
    res = _request(
        "POST",
        f"/api/admin/stores/{urllib.parse.quote(str(store_id), safe='')}/license",
        body=payload or {},
        token=admin_token,
        timeout=timeout,
    )
    if not res.get("ok"):
        raise RuntimeError(res.get("error") or "admin license update failed")
    return res.get("data") or {}


def _pc_device_id() -> str:
    """Stable per-machine id (hardware fingerprint, else a persisted UUID)."""
    try:
        from core.sync_metadata_schema import get_sync_device_id
        did = (get_sync_device_id() or "").strip()
        if did:
            return f"pc-{did[:32]}"
    except Exception:
        pass
    return ""


_SESSION_PROBE_LOCK = threading.Lock()
_SESSION_PROBE_AT: dict[str, float] = {}
_SESSION_PROBE_EVERY_SEC = 300.0
_TOKEN_EXP_SLACK_SEC = 600.0


def _token_exp(token: str) -> float:
    """Expiry epoch from the JWT payload; 0.0 when it cannot be read."""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload).decode("utf-8"))
        return float(claims.get("exp") or 0)
    except Exception:
        return 0.0


def invalidate_session_probe(store_key: str = "") -> None:
    """Force the next ensure_store_session to probe the server again.

    Called after a 401/403 from a real store call, which is how a revoked or
    rotated token is now detected instead of probing before every request.
    """
    with _SESSION_PROBE_LOCK:
        if store_key:
            _SESSION_PROBE_AT.pop(store_key, None)
        else:
            _SESSION_PROBE_AT.clear()


def ensure_store_session(
    *,
    store_key: str,
    android_key: str,
    store_name: str,
    device_id: Optional[str] = None,
    force_pair: bool = False,
) -> dict:
    """Return session dict with token; pair/re-pair as needed."""
    session = load_session(store_key)
    token = (session.get("token") or "").strip()
    if token and not force_pair:
        # Probe with /auth/license (identity only) — not /auth/me, which asserts
        # store access and fails when expired even though the JWT is still valid.
        #
        # This probe used to run before EVERY store call: one Inventory load paid
        # for five of them, about 2s of the page. Skip it while the JWT itself is
        # still comfortably valid and we probed recently. A token revoked
        # server-side inside that window now surfaces as a 401/403 on the real
        # call, which store_query_client turns into a forced re-pair and retry.
        now = time.time()
        exp = _token_exp(token)
        with _SESSION_PROBE_LOCK:
            probed_at = _SESSION_PROBE_AT.get(store_key, 0.0)
        if (
            exp
            and exp - now > _TOKEN_EXP_SLACK_SEC
            and now - probed_at < _SESSION_PROBE_EVERY_SEC
        ):
            return session
        try:
            get_license(token)
            with _SESSION_PROBE_LOCK:
                _SESSION_PROBE_AT[store_key] = now
            return session
        except ServerHttpError as exc:
            if exc.status not in (401, 403):
                raise
        except Exception:
            pass

    # device_id must identify the MACHINE, not the store. It used to fall back to
    # store_key, so every PC on a store reported the same device_id: the server
    # could not tell them apart, the admin device list collapsed to one row, and
    # sync_hint.source_device_id matched every peer — so each device treated the
    # others' writes as its own echo and skipped the live UI refresh.
    device = device_id or _pc_device_id() or store_key or "mac2-pc"
    # One pairing at a time. A page opens several store calls at once; when the
    # token had run out each of them re-paired on its own -- store #127's PC asked
    # /auth/pair about fifteen times in two seconds, 711 times in half an hour
    # (2026-09-27). The first caller pairs; the others take the session it made.
    with _PAIR_LOCK:
        just = load_session(store_key)
        if (just.get("token") or "").strip() and _paired_within(just, _PAIR_REUSE_SEC):
            return just
        refused = _PAIR_REFUSED.get(store_key)
        if refused and time.time() < refused[0]:
            raise refused[2]          # refused a moment ago: not asked again until the wait is over
        try:
            data = pair_store(
                android_key=android_key,
                store_name=store_name,
                device_id=device,
                device_type="pc",
                device_name=f"Mac2:{store_key}",
            )
        except ServerHttpError as exc:
            if exc.status in (401, 403, 404):
                tries = (refused[1] if refused else 0) + 1
                wait = min(_PAIR_BACKOFF_MAX_SEC, _PAIR_BACKOFF_FIRST_SEC * 2 ** (tries - 1))
                _PAIR_REFUSED[store_key] = (time.time() + wait, tries, exc)
            raise
        _PAIR_REFUSED.pop(store_key, None)
        store = data.get("store") or {}
        session = {
            "token": data.get("token"),
            "store_id": store.get("store_id"),
            "store_key": store.get("store_key") or store_key,
            "store_name": store.get("store_name") or store_name,
            "android_key": store.get("android_key") or android_key,
            "device_id": device,
            "paired_at": _dt.datetime.utcnow().isoformat() + "Z",
        }
        save_session(store_key, session)
    # The server's name for the store is the one the shop sees from now on, so the
    # next pairing sends that and not a folder key like "Store_Shree_Gajanan_...".
    if store.get("store_name"):
        try:
            from core.store_manager import adopt_server_display_name

            adopt_server_display_name(store_key, str(store.get("store_name")))
        except Exception:
            pass
    return session


_PAIR_LOCK = threading.Lock()
_PAIR_REUSE_SEC = 10.0            # a session paired this recently is taken as it is
_PAIR_BACKOFF_FIRST_SEC = 30.0    # a refused pairing waits 30 s, then 60, 120 ... up to 10 min
_PAIR_BACKOFF_MAX_SEC = 600.0
_PAIR_REFUSED: dict = {}          # store_key -> (not before, refusals in a row, the error)


def _paired_within(session: dict, seconds: float) -> bool:
    at = str(session.get("paired_at") or "").rstrip("Z")
    try:
        when = _dt.datetime.fromisoformat(at)
    except ValueError:
        return False
    return (_dt.datetime.utcnow() - when).total_seconds() < seconds


def store_token_for_active(force_pair: bool = False) -> str:
    """Convenience: ensure session for active local store and return JWT.

    force_pair skips the cached session entirely and re-pairs; callers use it to
    recover from a 401/403 on a real store call.
    """
    from core.store_link import get_local_android_key
    from core.store_manager import get_active_display_name, get_active_store_key

    store_key = get_active_store_key() or "Store_Default"
    name = get_active_display_name() or store_key
    key = get_local_android_key()
    if not key:
        raise RuntimeError("No Android store key. Pair this PC with the server first.")
    session = ensure_store_session(
        store_key=store_key,
        android_key=key,
        store_name=name,
        device_id=_pc_device_id(),
        force_pair=force_pair,
    )
    token = (session.get("token") or "").strip()
    if not token:
        raise RuntimeError("Server pairing returned no token")
    return token


def pull_collection(
    store_token: str,
    collection: str,
    *,
    since: Optional[str] = None,
    after_id: Optional[int] = None,
    include_deleted: bool = True,
    limit: int = 5000,
    timeout: float = 120.0,
) -> tuple[list, dict]:
    """Node GET /api/sync/:collection — page with since + after_id keyset."""
    q = [
        f"include_deleted={'1' if include_deleted else '0'}",
        f"limit={int(limit)}",
    ]
    if since:
        q.append(f"since={urllib.parse.quote(str(since))}")
    if after_id is not None:
        try:
            q.append(f"after_id={int(after_id)}")
        except (TypeError, ValueError):
            pass
    res = _request(
        "GET",
        f"/api/sync/{collection}?{'&'.join(q)}",
        token=store_token,
        timeout=timeout,
    )
    if not res.get("ok"):
        raise RuntimeError(res.get("error") or f"Pull {collection} failed")
    data = res.get("data")
    docs: list
    if isinstance(data, list):
        docs = data
    elif isinstance(data, dict):
        nested = data.get("docs")
        if nested is None:
            nested = data.get("items")
        if nested is None:
            nested = data.get("rows")
        if isinstance(nested, list):
            docs = nested
        elif nested is not None:
            docs = [nested]
        else:
            # Single special doc (pharmacy_profile / dropdowns / shelf_settings)
            docs = [data] if data else []
    elif data is None:
        docs = []
    else:
        docs = []
    return docs, (res.get("meta") or {})


def pull_all(
    store_token: str,
    *,
    since: Optional[str] = None,
    timeout: float = 180.0,
) -> tuple[dict, dict]:
    q = f"?since={urllib.parse.quote(str(since))}" if since else ""
    res = _request("GET", f"/api/sync{q}", token=store_token, timeout=timeout)
    if not res.get("ok"):
        raise RuntimeError(res.get("error") or "Pull all failed")
    data = res.get("data") or {}
    if not isinstance(data, dict):
        data = {}
    return data, (res.get("meta") or {})


def push_collection(store_token: str, collection: str, docs: list, *, timeout: float = 180.0) -> dict:
    if not docs:
        return {"upserted": 0, "skipped": 0}
    res = _request(
        "POST",
        f"/api/sync/{collection}",
        body=docs,
        token=store_token,
        timeout=timeout,
    )
    if not res.get("ok"):
        raise RuntimeError(res.get("error") or f"Push {collection} failed")
    return res.get("data") or {}


def push_bundle(store_token: str, bundle: dict, *, timeout: float = 180.0) -> dict:
    res = _request("POST", "/api/sync/bundle", body=bundle, token=store_token, timeout=timeout)
    if not res.get("ok"):
        raise RuntimeError(res.get("error") or "Push bundle failed")
    return res.get("data") or {}


def delete_doc(store_token: str, collection: str, local_id, *, timeout: float = 60.0) -> dict:
    res = _request(
        "DELETE",
        f"/api/sync/{collection}/{urllib.parse.quote(str(local_id))}",
        token=store_token,
        timeout=timeout,
    )
    if not res.get("ok"):
        raise RuntimeError(res.get("error") or f"Delete {collection}/{local_id} failed")
    return res.get("data") or {}


def pull_doc(store_token: str, collection: str, local_id, *, timeout: float = 60.0) -> dict | None:
    try:
        res = _request(
            "GET",
            f"/api/sync/{collection}/{urllib.parse.quote(str(local_id))}",
            token=store_token,
            timeout=timeout,
        )
    except ServerHttpError as exc:
        if int(getattr(exc, "status", 0) or 0) == 404:
            return None
        raise
    if not res.get("ok"):
        err = str(res.get("error") or "")
        if "not found" in err.lower():
            return None
        raise RuntimeError(err or f"Pull {collection}/{local_id} failed")
    data = res.get("data")
    return data if isinstance(data, dict) else None


def allocate_ids(
    store_token: str,
    requests: list | None = None,
    *,
    collection: str | None = None,
    count: int = 1,
    timeout: float = 30.0,
) -> dict:
    """POST /api/sync/allocate-ids → { ids: { collection: id|ids } }."""
    body: dict
    if requests:
        body = {"requests": requests}
    elif collection:
        body = {"collection": collection, "count": int(count)}
    else:
        raise ValueError("allocate_ids requires requests or collection")
    res = _request(
        "POST",
        "/api/sync/allocate-ids",
        body=body,
        token=store_token,
        timeout=timeout,
    )
    if not res.get("ok"):
        raise RuntimeError(res.get("error") or "allocate-ids failed")
    return res.get("data") or {}


def allocate_fy(store_token: str, kind: str, date: str, *, timeout: float = 30.0) -> dict:
    res = _request(
        "POST",
        "/api/sync/fy/allocate",
        body={"kind": kind, "date": date},
        token=store_token,
        timeout=timeout,
    )
    if not res.get("ok"):
        raise RuntimeError(res.get("error") or "FY allocate failed")
    return res.get("data") or {}


def peek_fy(store_token: str, kind: str, date: str, *, timeout: float = 20.0) -> dict:
    """Next FY serial on server without consuming the counter (UI hints)."""
    res = _request(
        "GET",
        f"/api/sync/fy/peek?kind={kind}&date={date}",
        token=store_token,
        timeout=timeout,
    )
    if not res.get("ok"):
        raise RuntimeError(res.get("error") or "FY peek failed")
    return res.get("data") or {}


def push_settings_profile(store_token: str, profile: dict) -> None:
    res = _request("PUT", "/api/sync/settings/pharmacy_profile", body=profile, token=store_token, timeout=60)
    if not res.get("ok"):
        raise RuntimeError(res.get("error") or "Push pharmacy_profile failed")


def get_settings_dropdowns(store_token: str) -> dict:
    """GET /api/sync/settings/dropdowns → villages / med_types / schedules."""
    res = _request("GET", "/api/sync/settings/dropdowns", token=store_token, timeout=60)
    if not res.get("ok"):
        raise RuntimeError(res.get("error") or "Get dropdowns failed")
    data = res.get("data")
    return data if isinstance(data, dict) else {}


def push_settings_dropdowns(store_token: str, dropdowns: dict) -> None:
    res = _request("PUT", "/api/sync/settings/dropdowns", body=dropdowns, token=store_token, timeout=60)
    if not res.get("ok"):
        raise RuntimeError(res.get("error") or "Push dropdowns failed")


def push_settings_shelf(store_token: str, shelf: dict) -> None:
    res = _request("PUT", "/api/sync/settings/shelf_settings", body=shelf, token=store_token, timeout=60)
    if not res.get("ok"):
        raise RuntimeError(res.get("error") or "Push shelf_settings failed")


def push_settings_kv(store_token: str, settings) -> None:
    body = settings if isinstance(settings, (list, dict)) else {"settings": settings}
    res = _request("PUT", "/api/sync/settings/kv", body=body, token=store_token, timeout=60)
    if not res.get("ok"):
        raise RuntimeError(res.get("error") or "Push settings kv failed")


def get_sync_status(store_token: str, *, timeout: float = 20.0) -> dict:
    """GET /api/sync/status → { head_revision, server_time }."""
    res = _request("GET", "/api/sync/status", token=store_token, timeout=timeout)
    if not res.get("ok"):
        raise RuntimeError(res.get("error") or "sync/status failed")
    data = res.get("data") or {}
    return data if isinstance(data, dict) else {}


def ack_sync_revision(
    store_token: str,
    revision: int,
    *,
    device_id=None,
    timeout: float = 20.0,
) -> dict:
    """POST /api/sync/ack — B4.3 device lag tracking."""
    body = {"revision": int(revision or 0)}
    if device_id:
        body["device_id"] = device_id
    res = _request("POST", "/api/sync/ack", body=body, token=store_token, timeout=timeout)
    if not res.get("ok"):
        raise RuntimeError(res.get("error") or "sync/ack failed")
    data = res.get("data") or {}
    return data if isinstance(data, dict) else {}


def get_sync_changes(
    store_token: str,
    *,
    after: int = 0,
    limit: int = 200,
    timeout: float = 60.0,
) -> dict:
    """GET /api/sync/changes?after=&limit= (changelog rows only)."""
    q = f"?after={int(after or 0)}&limit={int(limit or 200)}"
    res = _request("GET", f"/api/sync/changes{q}", token=store_token, timeout=timeout)
    if not res.get("ok"):
        raise RuntimeError(res.get("error") or "sync/changes failed")
    data = res.get("data") or {}
    return data if isinstance(data, dict) else {}


def get_sync_changes_full(
    store_token: str,
    *,
    after: int = 0,
    limit: int = 200,
    timeout: float = 120.0,
) -> dict:
    """GET /api/sync/changes/full — changelog + entity docs (sales include items[])."""
    q = f"?after={int(after or 0)}&limit={int(limit or 200)}"
    res = _request(
        "GET",
        f"/api/sync/changes/full{q}",
        token=store_token,
        timeout=timeout,
    )
    if not res.get("ok"):
        raise RuntimeError(res.get("error") or "sync/changes/full failed")
    data = res.get("data") or {}
    return data if isinstance(data, dict) else {}
