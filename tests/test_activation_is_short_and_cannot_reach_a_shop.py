"""Activation: three days, one field, and no way into somebody else's shop.

Two changes meet here.

THE TRIAL IS THREE DAYS, NOT TEN. The number that decides it lives on the server
(``DEFAULT_EXPIRY_DAYS`` in ``src/services/licenseService.js``) and now travels
with every licence as ``trial_days``. The desktop used to hold a second copy and
push a date computed from it -- a push the server has been throwing away for a
while, since ``routes/auth.js`` strips ``expiry_date`` from a device's PUT so an
expired store cannot un-expire itself. So the desktop's copy could drift without
anyone noticing, and a build saying "10 days" over a licence the server ends in 3
is a shop that believes it has a week it does not have.

A SHOP ALREADY RUNNING MUST NOT SUDDENLY BE EXPIRED. Shortening the window is
only safe because nothing recomputes an expiry that already exists: the server
writes it through ``COALESCE(expiry_date, …)``, and the desktop no longer
computes one at all. The tests below pin the desktop half; ``provision-test.mjs``
in the server patch pins the server half against a real database.

ACTIVATION IS ONE FIELD. A fresh install types the shop name and presses Start.
That path goes to a public endpoint and carries no credential -- which matters
more than it sounds, because the OLD path activates as the vendor
ADMINISTRATOR: ``server_live.ensure_active_store_on_server`` calls
``api.admin_login()`` with a username and password compiled into the build, and
then, when the store key does not match, offers to adopt a server store whose
NAME matches. Those two facts together are the way a stranger reaches a real
shop. The trial path must touch neither of them, and these tests hold it to that.
"""
import io
import os
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core import desktop_license_service as dls  # noqa: E402
from core import trial_activation as ta  # noqa: E402


SERVER_STORE = {
    "id": 41,
    "store_id": "trial_9f2c1ab07d5e",
    "store_key": "Trial_9F2C1AB07D5E",
    "store_name": "Roshan Medical",
    "app_mode": "online",
    "android_key": "SC-A1B2C3D4",
}
SERVER_LICENSE = {
    "is_active": True,
    "activation_date": "2026-09-09",
    "expiry_enabled": True,
    "expiry_date": "2026-09-12",
    "apply_expiry_check": True,
    "access_allowed": True,
    "server_date": "2026-09-09",
    "trial_days": 3,
    "is_trial": True,
}


class _Provisioned:
    """A successful trial activation with every side effect held in memory."""

    def __init__(self, test, *, response=None, error=None):
        self.test = test
        self.response = response if response is not None else {
            "token": "jwt.for.the.store.it.just.made",
            "store": dict(SERVER_STORE),
            "license": dict(SERVER_LICENSE),
            "trial_days": 3,
        }
        self.error = error
        self.calls = []
        self.saved_keys = []
        self.adoptions = []
        self.sync_modes = []
        self.sessions = []

    def _provision(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.response

    def __enter__(self):
        def _forbidden(*_a, **_k):
            raise AssertionError(
                "the trial path reached the vendor administrator API; a fresh "
                "install must not need a credential that ships inside the build"
            )

        self.patches = [
            mock.patch("core.server_api.provision_trial", self._provision),
            mock.patch("core.server_api.admin_login", _forbidden),
            mock.patch("core.server_api.list_remote_stores", _forbidden),
            mock.patch("core.server_api.create_store", _forbidden),
            mock.patch(
                "core.server_live.ensure_active_store_on_server", _forbidden
            ),
            mock.patch("core.server_api.save_session",
                       lambda key, data: self.sessions.append((key, data))),
            mock.patch("core.server_api.ensure_store_session",
                       lambda **kw: self.sessions.append(("pair", kw))),
            mock.patch("core.store_link._save_local",
                       lambda key, store_key="": self.saved_keys.append((key, store_key))),
            mock.patch("core.server_live._record_store_adoption",
                       lambda store_key, remote: self.adoptions.append((store_key, remote))),
            mock.patch("core.store_manager.setup_initial_store_on_activation",
                       lambda name: {"store_key": "Store_Roshan_Medical"}),
            mock.patch("core.store_manager.get_active_store_key",
                       return_value="Store_Roshan_Medical"),
            mock.patch("core.sync_prefs.set_sync_mode",
                       lambda mode: self.sync_modes.append(mode)),
            mock.patch("core.trial_activation.already_set_up",
                       return_value=False),
            mock.patch("core.license_manager.save_activation_date_local",
                       lambda day=None: day or ""),
            mock.patch("core.license_manager._cache_server_license_locally",
                       lambda lic: None),
            mock.patch("core.license_manager._write_activation", lambda hw: None),
            mock.patch("core.license_manager._write_hw_cache", lambda hw: None),
            mock.patch("core.license_manager._get_hardware_hash", return_value="a" * 64),
            mock.patch("core.trial_activation.machine_id", return_value="b" * 64),
            mock.patch("core.trial_activation.device_id", return_value="pc-deadbeef1234"),
        ]
        for p in self.patches:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in reversed(self.patches):
            p.stop()
        return False


class TheTrialIsThreeDays(unittest.TestCase):
    def test_the_desktop_constant_is_three(self):
        self.assertEqual(dls.ONLINE_TRIAL_DAYS, 3)

    def test_the_number_reported_is_the_servers_not_the_builds(self):
        """A server that says 5 must be believed over the constant in this build."""
        with _Provisioned(self) as run:
            run.response["license"] = dict(SERVER_LICENSE, trial_days=5)
            result = ta.activate_trial("Roshan Medical")
        self.assertTrue(result["ok"], result.get("error"))
        self.assertEqual(result["trial_days"], 5)

    def test_the_expiry_shown_is_the_one_the_server_set(self):
        with _Provisioned(self) as run:
            result = ta.activate_trial("Roshan Medical")
        self.assertTrue(result["ok"], result.get("error"))
        self.assertEqual(result["expiry_date"], "2026-09-12")
        self.assertEqual(result["activation_date"], "2026-09-09")
        # Three days after activation, computed by nobody on this side.
        self.assertNotIn("timedelta", _source("core/desktop_license_service.py"))

    def test_the_desktop_no_longer_invents_an_expiry_date(self):
        """_trial_expiry_ymd is gone, and nothing pushes expiry_date any more.

        routes/auth.js deletes expiry_date from a device's PUT before it reaches
        the database, so this was already a no-op that could only make the screen
        and the server disagree about when the trial ends.
        """
        self.assertFalse(hasattr(dls, "_trial_expiry_ymd"))
        body = _source("core/desktop_license_service.py")
        record = body[body.index("def _record_server_activation"):]
        record = record[: record.index("\ndef ")]
        self.assertEqual(
            record.count("api.put_license("), 1,
            "there is a second licence PUT here; the removed one pushed an "
            "expiry date the server deletes before it reaches the database",
        )
        put = record[record.index("api.put_license("):]
        put = put[: put.index(")\n")]
        self.assertNotIn(
            "expiry_date", put,
            "the desktop is pushing an expiry date the server throws away",
        )


class AnAlreadyRunningShopIsNotExpiredByThis(unittest.TestCase):
    def test_a_shop_with_an_expiry_keeps_it_when_the_server_repeats_it(self):
        """The cache may fill a gap; it may not shorten what it is caching."""
        from core import license_manager as lm

        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(lm, "_appdata_dir", return_value=tmp):
                lm.write_expiry("2027-03-31", enabled=True)
                lm._cache_server_license_locally(
                    {"expiry_enabled": True, "expiry_date": "2027-03-31",
                     "apply_expiry_check": True, "trial_days": 3}
                )
                self.assertEqual(
                    str(lm._read_expiry().get("expiry_date")), "2027-03-31",
                    "a running shop's expiry was moved by the shorter trial",
                )

    def test_a_blank_server_expiry_never_wipes_a_real_one(self):
        from core import license_manager as lm

        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(lm, "_appdata_dir", return_value=tmp):
                lm.write_expiry("2027-03-31", enabled=True)
                lm._cache_server_license_locally(
                    {"expiry_enabled": True, "expiry_date": "", "trial_days": 3}
                )
                self.assertEqual(
                    str(lm._read_expiry().get("expiry_date")), "2027-03-31",
                )


class ATrialCanNeverReachAnExistingShop(unittest.TestCase):
    def test_activation_needs_no_credential_that_ships_in_the_build(self):
        """admin_login, list_remote_stores and create_store are all forbidden here.

        Every one of them is patched to raise. If the trial path reaches any of
        them this test fails, and it fails for the right reason: the vendor
        administrator password is compiled into core/server_api.py, so a path
        that needs it is a path every shopkeeper's copy of the app could use
        against every other shop on the account.
        """
        with _Provisioned(self) as run:
            result = ta.activate_trial("Roshan Medical")
        self.assertTrue(result["ok"], result.get("error"))
        self.assertEqual(len(run.calls), 1)

    def test_the_name_is_sent_as_a_label_and_the_identity_comes_back(self):
        """The PC does not choose the store's identity; the server invents it."""
        with _Provisioned(self) as run:
            ta.activate_trial("Roshan Medical")
        sent = run.calls[0]
        self.assertEqual(sent["store_name"], "Roshan Medical")
        self.assertNotIn("store_id", sent)
        self.assertNotIn("store_key", sent)
        self.assertNotIn("android_key", sent)

    def test_the_pc_is_pinned_to_the_store_id_the_server_made(self):
        """Without the pin, a later launch adopts a real shop with the same name.

        ensure_active_store_on_server falls back to matching on the display NAME
        when the key does not match. A trial called "Medical Store" would find
        whichever real shop is called that. The adoption record makes identity the
        server's random store_id instead, and the name stops deciding anything.
        """
        with _Provisioned(self) as run:
            ta.activate_trial("Roshan Medical")
        self.assertEqual(len(run.adoptions), 1)
        store_key, remote = run.adoptions[0]
        self.assertEqual(store_key, "Store_Roshan_Medical")
        self.assertEqual(remote["store_id"], "trial_9f2c1ab07d5e")

    def test_a_pc_that_already_has_a_store_is_refused(self):
        """No second trial on a machine that is already somebody's till."""
        with _Provisioned(self) as run:
            with mock.patch(
                "core.trial_activation.already_set_up", return_value=True
            ):
                result = ta.activate_trial("Roshan Medical")
        self.assertFalse(result["ok"])
        self.assertEqual(run.calls, [])

    def test_the_pairing_key_is_the_one_the_server_returned(self):
        with _Provisioned(self) as run:
            ta.activate_trial("Roshan Medical")
        self.assertEqual(run.saved_keys, [("SC-A1B2C3D4", "Store_Roshan_Medical")])

    def test_it_ends_in_online_mode(self):
        with _Provisioned(self) as run:
            ta.activate_trial("Roshan Medical")
        self.assertIn("online", run.sync_modes)

    def test_no_credential_is_written_into_the_trial_path(self):
        body = _source("core/trial_activation.py")
        for needle in ("admin_login", "admin_password", "satpudacore",
                       "_MASTER_PASSWORD", "list_remote_stores"):
            self.assertNotIn(
                needle, body.replace("api.admin_login()", ""),
                f"{needle} appears in the credential-free activation path",
            )

    def test_a_refusal_is_a_sentence_not_a_stack_trace(self):
        from core.server_api import ServerHttpError

        refusal = ServerHttpError(
            429, "This computer has already been given a free trial."
        )
        with _Provisioned(self, error=refusal) as run:
            result = ta.activate_trial("Roshan Medical")
        self.assertFalse(result["ok"])
        self.assertIn("already been given a free trial", result["error"])
        self.assertEqual(run.saved_keys, [])

    def test_a_refusal_leaves_the_pc_exactly_as_it_was(self):
        """Ask first; change nothing until the answer is yes.

        The order used to be the other way round, and a refused fresh install was
        left in Online mode with a local store and no store on the server. That PC
        then had a registry, so the screen stopped offering the one-field form and
        showed the long three-factor one instead -- to a shopkeeper whose only
        mistake was pressing Start while the day's per-address limit was full.
        """
        from core.server_api import ServerHttpError

        with _Provisioned(self, error=ServerHttpError(429, "Too many")) as run:
            result = ta.activate_trial("Roshan Medical")
        self.assertFalse(result["ok"])
        self.assertEqual(run.sync_modes, [], "the PC was switched to Online anyway")
        self.assertEqual(run.adoptions, [])
        self.assertEqual(run.sessions, [])


class TwoOverlappingActivationsProduceOneStore(unittest.TestCase):
    """Activation has to be single-flight, because it really can overlap.

    ``core/desktop_api.py`` serves on a ThreadingHTTPServer, and the activation
    screen polls ``get_license_status`` every few seconds -- which is what runs
    the installer's pending provision. The sign-up itself takes up to sixty
    seconds. So a second caller (the next poll, or the shopkeeper pressing Start
    on the form the first call has not answered yet) arrives while the first is
    still in flight, and without a lock both pass the "already connected?" test.

    The cost is specific, not theoretical: the server grants one trial per
    computer, so the second request spends this shop's only trial on a store the
    PC is not pinned to -- and the shopkeeper cannot sign up again without
    somebody clearing the throttle by hand.
    """

    def test_only_one_of_them_reaches_the_server(self):
        import threading
        import time

        connected = threading.Event()
        results = []
        with _Provisioned(self) as prov:
            granted = prov._provision

            def _slow_provision(**kwargs):
                # Wide enough that the second thread is certainly inside
                # activate_trial before the first has finished.
                time.sleep(0.2)
                out = granted(**kwargs)
                connected.set()
                return out

            with mock.patch("core.server_api.provision_trial", _slow_provision), \
                 mock.patch("core.trial_activation.already_set_up",
                            connected.is_set):
                threads = [
                    threading.Thread(target=lambda: results.append(
                        ta.activate_trial("Roshan Medical")))
                    for _ in range(2)
                ]
                for t in threads:
                    t.start()
                for t in threads:
                    t.join(20)
                self.assertFalse(any(t.is_alive() for t in threads), "activation deadlocked")

        self.assertEqual(
            len(prov.calls), 1,
            f"the endpoint was called {len(prov.calls)} times; this computer only "
            "gets one trial, so the extra call burns it on a store nothing uses",
        )
        self.assertEqual(len(results), 2)
        self.assertEqual(len([r for r in results if r.get("ok")]), 1,
                         "two concurrent activations both reported success")
        refused = [r for r in results if not r.get("ok")]
        self.assertIn("already connected", refused[0].get("error", ""))

    def test_the_installers_file_cannot_fire_twice_from_two_threads(self):
        import threading

        with tempfile.TemporaryDirectory() as tmp:
            with io.open(os.path.join(tmp, "provision.json"), "w", encoding="utf-8") as fh:
                fh.write('{"store_name": "Roshan Medical"}')
            ran = []
            with _Provisioned(self), \
                 mock.patch("core.trial_activation._appdata_dir", return_value=tmp):
                ta._RAN_THIS_PROCESS = False
                ta._LAST_FAILURE = {}
                try:
                    threads = [
                        threading.Thread(target=lambda: ran.append(ta.run_pending_provision()))
                        for _ in range(4)
                    ]
                    for t in threads:
                        t.start()
                    for t in threads:
                        t.join(20)
                finally:
                    ta._RAN_THIS_PROCESS = False
                    ta._LAST_FAILURE = {}
            self.assertEqual(len([r for r in ran if r.get("ran")]), 1,
                             "the installer's file was acted on more than once")


class TheMachineIdIsBlankWhenNothingCanBeRead(unittest.TestCase):
    def test_an_unreadable_machine_sends_no_fingerprint(self):
        """A hash of nothing is the SAME on every unreadable machine.

        Sending it would let the first such computer consume the only trial any
        of them could ever get, because the server counts one trial per
        fingerprint.
        """
        with mock.patch("core.license_manager._hardware_parts", return_value={}), \
             mock.patch("core.license_manager._identity_readable", return_value=False):
            self.assertEqual(ta.machine_id(), "")

    def test_a_readable_machine_sends_its_fingerprint(self):
        with mock.patch("core.license_manager._hardware_parts",
                        return_value={"cpu": "BFEBFBFF000806EA"}), \
             mock.patch("core.license_manager._identity_readable", return_value=True), \
             mock.patch("core.license_manager._get_hardware_hash", return_value="c" * 64):
            self.assertEqual(ta.machine_id(), "c" * 64)


class TheInstallersFileRunsOnceAndOnlyOnce(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._p = mock.patch.object(ta, "_appdata_dir", return_value=self.tmp)
        self._p.start()
        ta._RAN_THIS_PROCESS = False
        ta._LAST_FAILURE = {}

    def tearDown(self):
        self._p.stop()
        ta._RAN_THIS_PROCESS = False
        ta._LAST_FAILURE = {}

    def _write(self, text):
        with io.open(os.path.join(self.tmp, "provision.json"), "w", encoding="utf-8") as fh:
            fh.write(text)

    def test_a_json_file_gives_the_shop_name(self):
        self._write('{"store_name": "Roshan Medical"}')
        self.assertEqual(ta.pending_provision_name(), "Roshan Medical")

    def test_a_bare_name_on_one_line_also_works(self):
        self._write("Roshan Medical\n")
        self.assertEqual(ta.pending_provision_name(), "Roshan Medical")

    def test_no_file_means_no_network_call_at_all(self):
        """get_license_status is POLLED. An unguarded call here would hammer the
        endpoint into its own rate limit and then report the refusal to the shop.
        """
        calls = []
        with mock.patch.object(ta, "activate_trial",
                               lambda n, sync_mode="": calls.append((n, sync_mode))):
            for _ in range(5):
                ta.run_pending_provision()
        self.assertEqual(calls, [])

    def test_it_activates_once_and_retires_the_file(self):
        self._write('{"store_name": "Roshan Medical"}')
        calls = []

        def _run(name, sync_mode=""):
            calls.append((name, sync_mode))
            return {"ok": True}

        with mock.patch.object(ta, "activate_trial", _run), \
             mock.patch.object(ta, "already_set_up", return_value=False):
            first = ta.run_pending_provision()
            second = ta.run_pending_provision()

        self.assertEqual(calls, [("Roshan Medical", "online")])
        self.assertTrue(first.get("ran"))
        self.assertFalse(second.get("ran"))
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "provision.json")))
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "provision.done")))

    def test_a_failure_also_retires_the_file(self):
        """Otherwise the next launch tries again, and the one after that, until
        the server's per-address limit refuses a shop that would have succeeded.
        """
        self._write('{"store_name": "Roshan Medical"}')
        with mock.patch.object(ta, "activate_trial",
                               lambda n, sync_mode="": {"ok": False, "error": "Too many"}), \
             mock.patch.object(ta, "already_set_up", return_value=False):
            result = ta.run_pending_provision()
        self.assertTrue(result.get("ran"))
        self.assertFalse(result.get("ok"))
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "provision.json")))

    def test_a_pc_that_already_has_a_store_ignores_a_stale_file(self):
        self._write('{"store_name": "Somebody Elses Shop"}')
        calls = []
        with mock.patch.object(ta, "activate_trial",
                               lambda n, sync_mode="": calls.append(n)), \
             mock.patch.object(ta, "already_set_up", return_value=True):
            ta.run_pending_provision()
        self.assertEqual(calls, [])
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "provision.json")))


class TheStatusReadStillWorksWithoutAProvisionFile(unittest.TestCase):
    def test_status_carries_a_provision_error_field(self):
        with mock.patch("core.trial_activation.run_pending_provision",
                        return_value={"ok": False, "ran": False}), \
             mock.patch("core.license_manager.needs_activation", return_value=True), \
             mock.patch("core.license_manager.check_expiry", return_value=False), \
             mock.patch("core.license_manager.is_activated", return_value=False), \
             mock.patch("core.license_manager.prepare_device_key", lambda: None), \
             mock.patch("core.license_manager.get_device_key_path",
                        return_value=os.path.join(tempfile.gettempdir(), "nope.key")), \
             mock.patch("core.store_manager.has_registry", return_value=False), \
             mock.patch("core.store_manager.get_active_display_name", return_value=""):
            status = dls.get_license_status()
        self.assertIn("provision_error", status)
        self.assertEqual(status["provision_error"], "")


def _source(rel: str) -> str:
    with io.open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
        return fh.read()


if __name__ == "__main__":
    unittest.main()
