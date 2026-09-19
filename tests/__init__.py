"""Test-suite safety net: no test may touch a real shop.

Two things were true of this suite and neither was intended.

The shop's sync mode is read from a file on this machine, so the tests ran in
whatever mode the machine happened to be in. On a PC set to Online, every test
that opened a database went db_setup.initialise -> ensure_pharmacy_profile_table
-> load_pharmacy_profile -> fetch_profile_from_server, which asks
core.server_live for a store JWT -- and that call PAIRS this machine with the
live store when the cached token has expired, registering a device against a
real pharmacy. Reading a live shop's header into a test fixture is bad enough;
pairing is a write.

So: force Offline for the duration of the suite, and slam the door on outbound
HTTP entirely. A test that wants Online behaviour patches is_online_mode and the
specific client function it needs -- which is what the online tests already do,
and none of them need a socket. Anything that still reaches for the network is a
test reaching for a real pharmacy's data, and it now fails loudly instead.
"""
import http.client
import os
import socket

os.environ.setdefault("SATPUDA_TEST", "1")

# --- 1. Offline, whatever this machine is set to -----------------------------
try:
    from core import sync_prefs as _sync_prefs

    _sync_prefs.get_sync_mode = lambda: _sync_prefs.MODE_OFFLINE  # type: ignore[assignment]
    _sync_prefs.is_online_mode = lambda: False  # type: ignore[assignment]
except Exception:  # pragma: no cover - core not importable yet
    pass


# --- 2. No sockets -----------------------------------------------------------
class LiveServerCallInTest(RuntimeError):
    """A test tried to reach the network. Patch the client function instead."""


LOOPBACK = ("127.0.0.1", "::1", "localhost")


def _is_loopback(host) -> bool:
    return str(host or "").split("%")[0].strip("[]") in LOOPBACK


def _guard(cls):
    original = cls.request

    def request(self, method, url, *args, **kwargs):
        host = getattr(self, "host", "?")
        # The engine's own HTTP handler is fair game -- that is this machine
        # talking to itself, not a test reaching into a pharmacy's records.
        if _is_loopback(host):
            return original(self, method, url, *args, **kwargs)
        raise LiveServerCallInTest(
            f"test tried to call {method} {host}{url}. Tests must never reach a "
            f"real server -- patch the client function (core.store_query_client.*, "
            f"core.server_crud.*, core.server_api._request) in the test instead."
        )

    cls.request = request


_guard(http.client.HTTPConnection)
_guard(http.client.HTTPSConnection)

_real_create_connection = socket.create_connection


def _create_connection(address, *args, **kwargs):
    host = address[0] if isinstance(address, tuple) else address
    # Loopback is fine: the engine's own HTTP handler is tested end to end.
    if _is_loopback(host):
        return _real_create_connection(address, *args, **kwargs)
    raise LiveServerCallInTest(
        f"test tried to open a socket to {host}. Tests must never reach a real "
        f"server -- patch the client function in the test instead."
    )


socket.create_connection = _create_connection
