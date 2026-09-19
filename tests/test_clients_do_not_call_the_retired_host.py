"""The VPS moved, and a new build alone does not move an installed shop.

_DEFAULT_BASE in core/server_api.py is only the fallback. Every shop that is
already running has server_api_prefs.json in AppData, and the value in that file
wins -- so shipping a build with a new default would leave every existing shop
calling srv1892850, which today is a bridge and tomorrow is nothing.

The rule these tests pin is deliberately narrow:

  * a stored base whose HOST is the retired one is rewritten to the new host,
    keeping scheme, port and path, and the change is written back to disk with a
    record of what it replaced -- a dead name gets corrected, visibly;
  * every other stored value is returned exactly as written, including the
    Cloudflare tunnel name, which is slower but reaches the same server and is
    therefore the escape hatch a shop switches to by hand. Silently overruling a
    value the owner typed is not migration, it is a bug with a changelog entry.

And the speed is the reason any of this exists: measured from a shop in
Maharashtra, tunnel ~460ms per call against direct ~85ms. That is why the
default is a bare hostname and not the friendly one.
"""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import server_api  # noqa: E402

RETIRED = "https://srv1892850.hstgr.cloud"
DIRECT = "https://srv1970994.hstgr.cloud"
TUNNEL = "https://api.satpudacore.online"


class _PrefsDir:
    """server_api pointed at a scratch AppData -- never the real one."""

    def __init__(self, prefs: dict | None):
        self._tmp = tempfile.TemporaryDirectory()
        self._patch = mock.patch.object(
            server_api, "_appdata_dir", return_value=self._tmp.name
        )
        self.path = os.path.join(self._tmp.name, "server_api_prefs.json")
        if prefs is not None:
            with open(self.path, "w", encoding="utf-8") as fh:
                json.dump(prefs, fh)

    def __enter__(self):
        self._patch.start()
        server_api._migration_written = False
        server_api.clear_failover()
        return self

    def __exit__(self, *exc):
        self._patch.stop()
        server_api._migration_written = False
        server_api.clear_failover()
        self._tmp.cleanup()
        return False

    def on_disk(self) -> dict:
        with open(self.path, encoding="utf-8") as fh:
            return json.load(fh)


class TheBuildDefaultNamesTheNewServer(unittest.TestCase):
    def test_default_is_the_direct_host_not_the_retired_one(self):
        self.assertEqual(server_api._DEFAULT_BASE, DIRECT)
        self.assertNotIn("srv1892850", server_api._DEFAULT_BASE)

    def test_the_tunnel_is_still_reachable_as_a_named_fallback(self):
        # Removing this constant would remove the only way a shop keeps running
        # when the direct host is down.
        self.assertEqual(server_api._TUNNEL_BASE, TUNNEL)


class AnInstalledShopIsMovedOffTheRetiredHost(unittest.TestCase):
    def test_a_stored_retired_host_is_rewritten(self):
        with _PrefsDir({"api_base": RETIRED}):
            self.assertEqual(server_api.api_base(), DIRECT)

    def test_the_rewrite_is_written_back_with_a_record_of_what_it_replaced(self):
        with _PrefsDir({"api_base": RETIRED}) as p:
            server_api.api_base()
            saved = p.on_disk()
        self.assertEqual(saved["api_base"], DIRECT)
        self.assertEqual(saved["api_base_migrated_from"], RETIRED)
        self.assertTrue(saved.get("api_base_migrated_on"))

    def test_port_and_path_survive_the_rewrite(self):
        self.assertEqual(
            server_api.migrate_api_base("https://srv1892850.hstgr.cloud:8443/core"),
            "https://srv1970994.hstgr.cloud:8443/core",
        )

    def test_a_bare_hostname_is_still_recognised(self):
        self.assertEqual(
            server_api.migrate_api_base("srv1892850.hstgr.cloud"),
            "https://srv1970994.hstgr.cloud",
        )

    def test_other_keys_in_the_file_are_not_disturbed(self):
        with _PrefsDir({"api_base": RETIRED, "admin_username": "shivkrupa"}) as p:
            server_api.api_base()
            saved = p.on_disk()
        self.assertEqual(saved["admin_username"], "shivkrupa")

    def test_no_prefs_file_means_the_new_default(self):
        with _PrefsDir(None):
            self.assertEqual(server_api.api_base(), DIRECT)


class AValueSomebodyChoseIsNeverOverruled(unittest.TestCase):
    def test_the_tunnel_is_left_exactly_as_written(self):
        with _PrefsDir({"api_base": TUNNEL}) as p:
            self.assertEqual(server_api.api_base(), TUNNEL)
            self.assertEqual(p.on_disk()["api_base"], TUNNEL)
            self.assertNotIn("api_base_migrated_from", p.on_disk())

    def test_a_lan_address_is_left_alone(self):
        with _PrefsDir({"api_base": "http://192.168.31.74:8080/api"}):
            self.assertEqual(server_api.api_base(), "http://192.168.31.74:8080/api")

    def test_the_new_host_is_not_touched_twice(self):
        with _PrefsDir({"api_base": DIRECT}) as p:
            self.assertEqual(server_api.api_base(), DIRECT)
            self.assertNotIn("api_base_migrated_from", p.on_disk())

    def test_a_lookalike_host_is_not_rewritten(self):
        # Only the exact retired host. A subdomain of it, or a name that merely
        # contains the digits, belongs to somebody else's decision.
        for other in (
            "https://staging.srv1892850.hstgr.cloud",
            "https://srv1892850-backup.hstgr.cloud",
        ):
            self.assertEqual(server_api.migrate_api_base(other), other)


class TheTunnelKeepsTheShopRunning(unittest.TestCase):
    """Requirement: direct is faster, but an unreachable direct host is not a
    closed shop. health_ok() is the one place that decides."""

    @staticmethod
    def _server(*, up):
        def fake_request(method, path, *, body=None, token=None, timeout=120.0, base=None):
            if base in up:
                return {"ok": True}
            raise RuntimeError(f"Cannot reach server ({base})")

        return fake_request

    def test_the_tunnel_takes_over_when_the_direct_host_is_down(self):
        with _PrefsDir({"api_base": DIRECT}) as p:
            with mock.patch.object(
                server_api, "_request", side_effect=self._server(up={TUNNEL})
            ):
                self.assertTrue(server_api.health_ok(timeout=8.0))
                self.assertEqual(server_api.api_base(), TUNNEL)
            # The failover is a decision about this run, not about this shop.
            self.assertEqual(p.on_disk()["api_base"], DIRECT)
            self.assertEqual(server_api.configured_base(), DIRECT)

    def test_a_restart_goes_back_to_the_fast_host(self):
        with _PrefsDir({"api_base": DIRECT}):
            with mock.patch.object(
                server_api, "_request", side_effect=self._server(up={TUNNEL})
            ):
                server_api.health_ok(timeout=8.0)
            self.assertEqual(server_api.api_base(), TUNNEL)
            server_api.clear_failover()  # what a fresh process starts with
            self.assertEqual(server_api.api_base(), DIRECT)

    def test_it_comes_home_when_the_tunnel_goes_and_direct_returns(self):
        with _PrefsDir({"api_base": DIRECT}):
            with mock.patch.object(
                server_api, "_request", side_effect=self._server(up={TUNNEL})
            ):
                server_api.health_ok(timeout=8.0)
            self.assertEqual(server_api.api_base(), TUNNEL)
            with mock.patch.object(
                server_api, "_request", side_effect=self._server(up={DIRECT})
            ):
                self.assertTrue(server_api.health_ok(timeout=8.0))
            self.assertEqual(server_api.api_base(), DIRECT)

    def test_a_shop_pinned_to_the_tunnel_can_still_fall_back_to_direct(self):
        with _PrefsDir({"api_base": TUNNEL}):
            with mock.patch.object(
                server_api, "_request", side_effect=self._server(up={DIRECT})
            ):
                self.assertTrue(server_api.health_ok(timeout=8.0))
            self.assertEqual(server_api.api_base(), DIRECT)

    def test_an_impatient_probe_never_moves_the_shop_to_the_slow_tunnel(self):
        # Settings calls health_ok(timeout=0.4) only to colour a label. A 0.4s
        # blip must not cost the session ~375ms on every later call.
        with _PrefsDir({"api_base": DIRECT}):
            with mock.patch.object(
                server_api, "_request", side_effect=self._server(up={TUNNEL})
            ):
                self.assertFalse(server_api.health_ok(timeout=0.4))
            self.assertEqual(server_api.api_base(), DIRECT)

    def test_both_hosts_down_is_still_a_failure(self):
        with _PrefsDir({"api_base": DIRECT}):
            with mock.patch.object(
                server_api, "_request", side_effect=self._server(up=set())
            ):
                self.assertFalse(server_api.health_ok(timeout=8.0))
            self.assertEqual(server_api.api_base(), DIRECT)


class TheBuildDoesNotShipTheRetiredHostAnywhere(unittest.TestCase):
    def test_no_source_file_still_defaults_to_srv1892850(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        offenders = []
        for folder in ("core", "ui"):
            for dirpath, _dirs, files in os.walk(os.path.join(root, folder)):
                if "__pycache__" in dirpath:
                    continue
                for name in files:
                    if not name.endswith(".py"):
                        continue
                    full = os.path.join(dirpath, name)
                    with open(full, encoding="utf-8", errors="replace") as fh:
                        for lineno, line in enumerate(fh, 1):
                            if "srv1892850" not in line:
                                continue
                            # Naming it in a comment or in _RETIRED_HOSTS is the
                            # point; assigning it as a base is the bug.
                            if line.lstrip().startswith("#"):
                                continue
                            if "_RETIRED_HOSTS" in line:
                                continue
                            offenders.append(f"{full}:{lineno}: {line.strip()}")
        self.assertEqual(offenders, [], "retired host is still a live value")


if __name__ == "__main__":
    unittest.main()
