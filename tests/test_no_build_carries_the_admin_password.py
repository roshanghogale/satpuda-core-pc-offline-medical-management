"""The vendor administrator is not inside the build, and cannot be reached by accident.

WHAT WAS WRONG, and it was live
-------------------------------
``core/server_api.admin_login`` took no arguments and fell back to a username
and password compiled into every copy of the desktop::

    user = username or prefs.get("admin_username") or "admin"
    pw    = password or prefs.get("admin_password") or "satpudacore"

Five call sites used it that way, and three of them run with nobody watching --
the launch-time store link, the pairing key lookup, the key rotation. Confirmed
on the live server on 2026-09-14: ``POST /api/auth/admin/login`` answered 200
for shop PCs with the SatpudaCoreMac2 user agent 23 times on 11 Sep, 20 on
12 Sep, 8 on 13 Sep. Every till in the fleet was signing itself in as the
ADMINISTRATOR of every shop on the account, and anyone who opened the
downloadable build with a text editor could do the same by hand: read, edit and
disable the licence, expiry and data of every shop.

WHAT HOLDS NOW
--------------
  * ``admin_login`` REQUIRES both arguments and refuses a blank one before it
    makes any request. There is no literal default and no prefs fallback.
  * No shipped module calls it with no arguments -- checked here by parsing the
    source, so a comment cannot hide a call and a call cannot hide in a comment.
  * The automatic paths carry no credential at all: they resolve a store from
    its own SC- pairing key, which reaches one store and no others.
  * An admin token exists only while a person has typed the credentials in this
    session (``core/admin_session.py``), it is never written to disk, and the
    password is never kept even in memory.

These tests are the fence. The fix they guard cannot be finished on the server
side until every shop is on a build that has it -- rotating the password first
would break setup on PCs still running the old one -- so the build must be
provably clean before that day.
"""
import ast

import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core import admin_session  # noqa: E402
from core import server_api as api  # noqa: E402

# Everything that is shipped and runs on a shop's PC, plus the developer
# scripts -- a password in a repo file is a password that leaks with the repo.
_SHIPPED_DIRS = ("core", "ui", "widgets", "scripts")
_SHIPPED_FILES = ("main.py",)

# The exact strings that used to be the defaults. Named here rather than
# described, because the point is that they are gone from the code.
_OLD_DEFAULTS = ("satpudacore", "admin")


def _shipped_sources():
    for name in _SHIPPED_FILES:
        path = os.path.join(ROOT, name)
        if os.path.isfile(path):
            yield path
    for folder in _SHIPPED_DIRS:
        for dirpath, dirnames, filenames in os.walk(os.path.join(ROOT, folder)):
            dirnames[:] = [d for d in dirnames if d != "__pycache__"]
            for fn in sorted(filenames):
                if fn.endswith(".py") and not fn.startswith("._"):
                    yield os.path.join(dirpath, fn)


def _admin_login_calls(path):
    """Every real call to admin_login in one file, as (line, positional, keywords).

    Parsed, not searched. A docstring that talks about ``api.admin_login()`` --
    and several of them do, because the hole is worth describing -- is not a
    call, and a call spread over three lines is still one.
    """
    with open(path, encoding="utf-8") as fh:
        try:
            tree = ast.parse(fh.read(), filename=path)
        except SyntaxError:  # pragma: no cover - a broken file fails elsewhere
            return []
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        if name != "admin_login":
            continue
        out.append((node.lineno, len(node.args), {kw.arg for kw in node.keywords}))
    return out


class TheCredentialIsNotInTheBuild(unittest.TestCase):
    def test_admin_login_has_no_default_credential(self):
        """Both parameters are required: a bare admin_login() cannot compile a password."""
        import inspect

        sig = inspect.signature(api.admin_login)
        for name in ("username", "password"):
            self.assertIn(name, sig.parameters)
            self.assertIs(
                sig.parameters[name].default,
                inspect.Parameter.empty,
                f"{name} has a default again -- that default was the password",
            )

    def test_calling_it_with_nothing_refuses_before_any_request(self):
        sent = []
        with mock.patch.object(api, "_request", lambda *a, **k: sent.append(a)):
            # No arguments at all does not even compile a call now, which is the
            # strongest form of the guarantee; blanks are refused explicitly.
            with self.assertRaises(TypeError):
                api.admin_login()
            for args in (("", ""), ("admin", ""), ("", "pw"), ("  ", "pw")):
                with self.assertRaises(api.AdminCredentialRequired):
                    api.admin_login(*args)
        self.assertEqual(sent, [], "a blank sign-in reached the server")

    def test_the_old_default_strings_are_gone_from_server_api(self):
        with open(os.path.join(ROOT, "core", "server_api.py"), encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        literals = {
            n.value for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
        }
        for gone in _OLD_DEFAULTS:
            self.assertNotIn(
                gone, literals,
                f'"{gone}" is a string literal in server_api again',
            )

    def test_the_prefs_file_can_no_longer_supply_one(self):
        """A JSON file next to the exe is no better than a literal inside it."""
        with mock.patch.object(api, "prefs_path", lambda: os.devnull):
            prefs = api.load_prefs()
        self.assertNotIn("admin_username", prefs)
        self.assertNotIn("admin_password", prefs)

    def test_typed_credentials_still_work(self):
        seen = {}

        def _request(method, path, *, body=None, **kw):
            seen["path"] = path
            seen["body"] = dict(body or {})
            return {"ok": True, "data": {"token": "T"}}

        with mock.patch.object(api, "_request", _request):
            self.assertEqual(api.admin_login("someone", "typed now"), "T")
        self.assertEqual(seen["path"], "/api/auth/admin/login")
        self.assertEqual(seen["body"]["username"], "someone")


class NoShippedPathCanAskWithoutACredential(unittest.TestCase):
    def test_no_module_calls_admin_login_with_no_argument(self):
        offenders = []
        for path in _shipped_sources():
            for lineno, positional, keywords in _admin_login_calls(path):
                if positional >= 2:
                    continue
                if positional == 1 and "password" in keywords:
                    continue
                if {"username", "password"} <= keywords:
                    continue
                offenders.append(f"{os.path.relpath(path, ROOT)}:{lineno}")
        self.assertEqual(
            offenders, [],
            "these call admin_login without an explicit username and password, "
            "which is how a shop PC signed itself in as the vendor: "
            + ", ".join(offenders),
        )

    def test_the_admin_session_never_invents_a_token(self):
        admin_session.clear()
        self.addCleanup(admin_session.clear)
        with self.assertRaises(api.AdminCredentialRequired):
            admin_session.token()
        self.assertFalse(admin_session.is_signed_in())

    def test_a_signed_in_session_keeps_the_token_and_not_the_password(self):
        admin_session.clear()
        self.addCleanup(admin_session.clear)
        with mock.patch.object(api, "admin_login", lambda u, p: "TOKEN"):
            admin_session.sign_in("someone", "typed now")
        self.assertEqual(admin_session.token(), "TOKEN")
        blob = repr(vars(admin_session))
        self.assertNotIn("typed now", blob, "the password was kept in memory")

    def test_a_session_does_not_outlive_its_window(self):
        import time

        admin_session.clear()
        self.addCleanup(admin_session.clear)
        with mock.patch.object(api, "admin_login", lambda u, p: "TOKEN"):
            admin_session.sign_in("someone", "typed now")
        later = time.time() + admin_session.SESSION_TTL_SEC + 60
        with mock.patch.object(time, "time", lambda: later):
            self.assertFalse(admin_session.is_signed_in())
            with self.assertRaises(api.AdminCredentialRequired):
                admin_session.token()

    def test_nothing_is_written_to_disk(self):
        """A token on disk is a credential on disk, with a longer life."""
        with open(os.path.join(ROOT, "core", "admin_session.py"), encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = getattr(node.func, "id", "") or getattr(node.func, "attr", "")
                self.assertNotIn(
                    name, ("open", "dump", "write_text", "save_prefs"),
                    "admin_session writes something down",
                )


class TheUnattendedPathsNeedNothing(unittest.TestCase):
    """The three that ran by themselves, and what they do instead."""

    def test_the_launch_time_store_link_pairs_with_the_shops_own_key(self):
        from core import server_live as live

        def _boom(*a, **k):
            raise AssertionError("the administrator API was used")

        with mock.patch("core.server_api.admin_login", _boom), \
             mock.patch("core.server_api.list_remote_stores", _boom), \
             mock.patch("core.server_api.create_store", _boom), \
             mock.patch("core.store_manager.get_active_store_key", return_value="Store_A"), \
             mock.patch("core.store_manager.get_active_display_name", return_value="A Medical"), \
             mock.patch("core.store_link.get_local_android_key", return_value="SC-AAAA"), \
             mock.patch("core.server_api._pc_device_id", return_value="pc"), \
             mock.patch.object(live, "_record_store_adoption"), \
             mock.patch.object(live, "_load_adoptions", return_value={}), \
             mock.patch("core.server_api.ensure_store_session",
                        return_value={"token": "T", "store_id": "a",
                                      "store_name": "A Medical"}) as paired:
            live.ensure_active_store_on_server()
        self.assertEqual(paired.call_args.kwargs["android_key"], "SC-AAAA")

    def test_looking_up_an_sc_key_asks_with_the_key(self):
        """It used to scan the whole account's store list as the administrator."""
        from core import store_link as sl

        def _boom(*a, **k):
            raise AssertionError("the administrator API was used")

        with mock.patch("core.server_api.admin_login", _boom), \
             mock.patch("core.server_api.list_remote_stores", _boom), \
             mock.patch("core.server_api._pc_device_id", return_value="pc"), \
             mock.patch("core.server_api.pair_store",
                        return_value={"store": {"store_id": "a",
                                                "store_name": "A Medical",
                                                "android_key": "SC-AAAA"}}):
            found = sl.lookup_key("SC-AAAA")
        self.assertEqual(found["store_name"], "A Medical")

    def test_rotating_a_key_refuses_before_it_touches_anything_local(self):
        from core import server_live as live
        from core import store_link as sl

        admin_session.clear()
        self.addCleanup(admin_session.clear)
        saved = []
        with mock.patch.object(sl, "_save_local", lambda k, *a, **kw: saved.append(k)), \
             mock.patch.object(live, "ensure_active_store_on_server") as linked:
            with self.assertRaises(api.AdminCredentialRequired):
                sl.regenerate_android_key("A Medical")
        self.assertEqual(saved, [], "a made-up key was written over the real one")
        self.assertFalse(linked.called)


if __name__ == "__main__":
    unittest.main()
