"""The vendor administrator, held only while a person is actually here.

WHAT WAS WRONG
--------------
``server_api.admin_login()`` took no arguments and fell back to a username and
password compiled into the build. Five code paths called it that way, and three
of them run unattended:

    store_manager._init_empty_db -> db_setup.initialise
      -> pharmacy_profile_io.load_pharmacy_profile -> fetch_profile_from_server
      -> server_live._token -> server_api.store_token_for_active
      -> server_api.admin_login()      <- the vendor ADMINISTRATOR, on boot

So a shop's till signed itself in as the administrator of every store on the
account, and the live access log shows it happening dozens of times a day from
shop addresses. Anyone who opened the downloadable build with a text editor had
the password to every other shop's licence, expiry and ledger.

THE SHAPE NOW
-------------
Two different questions, answered in two different ways:

  * "What is THIS shop's store on the server?" -- answered with the shop's own
    SC- pairing key through ``/api/auth/pair``. It is scoped to one store, it is
    already on the PC, and it needs no administrator at all. Every automatic
    path uses it.

  * "Do something only the vendor may do" -- answered by asking the person in
    front of the screen for the administrator username and password, now.

This module is the second one. It holds the TOKEN the server issued, in memory,
for a few minutes, so that one typed sign-in can carry a short piece of work
(list the stores, then join one) without asking twice. It deliberately does NOT
hold the password, does not write anything to disk, and does not survive the
process. Nothing here can be reached by a code path that has no person behind
it: with nothing signed in, ``token()`` raises.
"""
from __future__ import annotations

import threading
import time

# How long one typed sign-in stays usable. Long enough for "list the stores,
# read them, pick one" -- short enough that a machine left alone stops being an
# administrator on its own.
SESSION_TTL_SEC = 15 * 60

_LOCK = threading.RLock()
_TOKEN = ""
_EXPIRES_AT = 0.0


def sign_in(username: str, password: str) -> str:
    """Exchange a TYPED username and password for an admin token. Never cached to disk."""
    from core import server_api as api

    user = str(username or "").strip()
    pw = str(password or "")
    if not user or not pw:
        raise api.AdminCredentialRequired()
    token = api.admin_login(user, pw)
    with _LOCK:
        global _TOKEN, _EXPIRES_AT
        _TOKEN = str(token or "")
        _EXPIRES_AT = time.time() + SESSION_TTL_SEC
    return token


def token(*, username: str = "", password: str = "") -> str:
    """The admin token for this moment.

    Credentials typed with THIS call are used first; otherwise a sign-in from a
    few minutes ago is reused. With neither, this raises rather than inventing
    one -- which is the whole point of the module.
    """
    if str(username or "").strip() and str(password or ""):
        return sign_in(username, password)
    with _LOCK:
        if _TOKEN and time.time() < _EXPIRES_AT:
            return _TOKEN
        _clear_locked()
    from core.server_api import AdminCredentialRequired

    raise AdminCredentialRequired()


def is_signed_in() -> bool:
    with _LOCK:
        if _TOKEN and time.time() < _EXPIRES_AT:
            return True
        _clear_locked()
    return False


def _clear_locked() -> None:
    global _TOKEN, _EXPIRES_AT
    _TOKEN = ""
    _EXPIRES_AT = 0.0


def clear() -> None:
    with _LOCK:
        _clear_locked()
