"""Activation asks two things: the shop name, and Online or Offline.

THE OWNER'S WORDS. "activation fakt store name ani mode vicharel bs ani jr
installer asel tr installer install chya agodarch he vicharel mhanaje nantr
takaychi garaj nahi direct activate" -- activation asks only the store name and
the mode; and when there is an installer it asks those two BEFORE installing, so
afterwards nothing is typed and the app activates straight away.

WHAT WAS DROPPED, AND WHY IT COST NOTHING. The old screen asked for five things.
Three of them were never secrets:

  * the username and the password are ``_MASTER_USERNAME`` and
    ``_MASTER_PASSWORD`` in core/license_manager.py -- two constants compiled
    into every copy of the build, readable with a text editor;
  * the device key is this machine's OWN hardware fingerprint, which the app
    computes itself. The screen made the shopkeeper open an Administrator
    overlay -- whose credentials are also hardcoded, in the front end this time
    -- copy the key out, and paste it back into the same window.

So the three-factor form proved that the person at the keyboard could read the
screen in front of him. Nothing downstream lost a value it used to get from the
shopkeeper: the trial path never asked for any of them, and the long form, which
does, is still there for the three cases that need it.

The tests below pin the two answers end to end -- the screen, the handler, both
modes, and the installer's file.
"""
import io
import json
import os
import re
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core import desktop_license_service as dls  # noqa: E402
from core import trial_activation as ta  # noqa: E402


# The server's rules for a shop name -- cleanStoreName() in the server's
# src/services/provisionService.js. Two copies of them ship in this repo (the
# activation screen and the installer wizard) so a name the server would refuse
# is refused where it is typed. TheThreeCopiesAgree below holds them together.
SERVER_NAME_MIN = 2
SERVER_NAME_MAX = 60

SERVER_STORE = {
    "id": 77,
    "store_id": "trial_0c4e19b7aa31",
    "store_key": "Trial_0C4E19B7AA31",
    "store_name": "Roshan Medical",
    "android_key": "SC-99AA88BB",
}
SERVER_LICENSE = {
    "is_active": True,
    "activation_date": "2026-09-09",
    "expiry_enabled": True,
    "expiry_date": "2026-09-12",
    "apply_expiry_check": True,
    "access_allowed": True,
    "trial_days": 3,
}


def _source(rel: str) -> str:
    with io.open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
        return fh.read()


class _Activation:
    """One activation with every side effect held in memory and counted."""

    def __init__(self, *, error=None, response=None):
        self.error = error
        self.response = response if response is not None else {
            "token": "jwt.for.the.new.store",
            "store": dict(SERVER_STORE),
            "license": dict(SERVER_LICENSE),
        }
        self.calls = []
        self.sync_modes = []
        self.saved_keys = []
        self.adoptions = []
        self.paired = []
        self.licence_cached = []
        self.activation_written = []
        self.stores_created = []

    def _provision(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.response

    def __enter__(self):
        def _forbidden(*_a, **_k):
            raise AssertionError(
                "activation reached the vendor administrator API; two questions "
                "must not need a credential that ships inside the build"
            )

        self.patches = [
            mock.patch("core.server_api.provision_trial", self._provision),
            mock.patch("core.server_api.admin_login", _forbidden),
            mock.patch("core.server_api.create_store", _forbidden),
            mock.patch("core.server_live.ensure_active_store_on_server", _forbidden),
            mock.patch("core.server_api.save_session", lambda key, data: None),
            mock.patch("core.server_api.ensure_store_session",
                       lambda **kw: self.paired.append(kw)),
            mock.patch("core.store_link._save_local",
                       lambda key, store_key="": self.saved_keys.append((key, store_key))),
            mock.patch("core.server_live._record_store_adoption",
                       lambda store_key, remote: self.adoptions.append((store_key, remote))),
            mock.patch("core.store_manager.setup_initial_store_on_activation",
                       lambda name: self.stores_created.append(name)
                       or {"store_key": "Store_Roshan_Medical"}),
            mock.patch("core.store_manager.get_active_store_key",
                       return_value="Store_Roshan_Medical"),
            mock.patch("core.sync_prefs.set_sync_mode",
                       lambda mode: self.sync_modes.append(mode)),
            mock.patch("core.trial_activation.already_set_up", return_value=False),
            mock.patch("core.license_manager.save_activation_date_local",
                       lambda day=None: day or "2026-09-09"),
            mock.patch("core.license_manager._cache_server_license_locally",
                       lambda lic: self.licence_cached.append(lic)),
            mock.patch("core.license_manager._write_activation",
                       lambda hw: self.activation_written.append(hw)),
            mock.patch("core.license_manager._write_hw_cache", lambda hw: None),
            mock.patch("core.license_manager._get_hardware_hash", return_value="d" * 64),
            mock.patch("core.trial_activation.machine_id", return_value="e" * 64),
            mock.patch("core.trial_activation.device_id", return_value="pc-cafebabe9876"),
        ]
        for p in self.patches:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in reversed(self.patches):
            p.stop()
        return False


class TheScreenAsksTwoThings(unittest.TestCase):
    """The first screen a fresh install shows, read out of its own source."""

    def setUp(self):
        body = _source("desktop/src/pages/ActivationDialog.tsx")
        start = body.index("{simple ? (")
        end = body.index("\n          ) : (", start)
        self.first_screen = body[start:end]
        self.whole = body

    def test_it_asks_for_the_shop_name_and_the_mode_and_nothing_else(self):
        self.assertIn("Shop Name", self.first_screen)
        self.assertIn("setupMode", self.first_screen)
        # Three inputs: the name, and one radio for each mode.
        self.assertEqual(
            self.first_screen.count("<input"), 3,
            "the first screen has grown a field; it may ask two things and no more",
        )

    def test_the_three_dropped_fields_are_not_on_it(self):
        for gone in ("Username", "Password", "Device Key", "deviceKey", "activation-eye"):
            self.assertNotIn(
                gone, self.first_screen,
                f"{gone} is back on the first screen -- it is derivable or a "
                "constant compiled into the build, so asking for it proves nothing",
            )

    def test_both_answers_are_sent(self):
        self.assertIn("provisionTrial(name, setupMode)", self.whole)

    def test_a_pc_that_already_has_a_shop_is_sent_to_the_long_form(self):
        """An already activated shop must not be offered a new trial."""
        self.assertIn(
            "if (s.has_registry || s.expiry_reactivation) setSimple(false)", self.whole
        )

    def test_the_long_form_is_still_reachable_and_still_complete(self):
        """Three cases still need it, so none of it was deleted."""
        self.assertIn("I already have an account", self.whole)
        for kept in ("Username", "Password", "Device Key", "Add Store (Drive)"):
            self.assertIn(kept, self.whole)

    def test_the_name_is_checked_before_the_round_trip(self):
        self.assertIn("storeNameProblem(name)", self.whole)


class TheHandlerCarriesBothAnswers(unittest.TestCase):
    def test_the_mode_reaches_the_activation(self):
        seen = {}

        def _run(name, sync_mode=""):
            seen["name"] = name
            seen["mode"] = sync_mode
            return {"ok": True}

        with mock.patch("core.trial_activation.activate_trial", _run):
            dls.activate_trial({"store_name": "Roshan Medical", "sync_mode": "offline"})
        self.assertEqual(seen, {"name": "Roshan Medical", "mode": "offline"})

    def test_a_missing_mode_is_online(self):
        self.assertEqual(ta.normalize_sync_mode(""), "online")
        self.assertEqual(ta.normalize_sync_mode("nonsense"), "online")
        self.assertEqual(ta.normalize_sync_mode("OFFLINE"), "offline")
        self.assertEqual(ta.normalize_sync_mode(" offline\n"), "offline")


class OfflineIsAnAnswerAndNotAFailure(unittest.TestCase):
    def test_offline_ends_in_offline_mode(self):
        with _Activation() as run:
            result = ta.activate_trial("Roshan Medical", sync_mode="offline")
        self.assertTrue(result["ok"], result.get("error"))
        self.assertEqual(result["sync_mode"], "offline")
        self.assertEqual(run.sync_modes, ["offline"])

    def test_offline_still_puts_the_shop_on_the_owners_trials_page(self):
        """One call, so a new shop is visible and can be switched off."""
        with _Activation() as run:
            ta.activate_trial("Roshan Medical", sync_mode="offline")
        self.assertEqual(len(run.calls), 1)
        self.assertEqual(run.saved_keys, [("SC-99AA88BB", "Store_Roshan_Medical")])
        self.assertEqual(len(run.adoptions), 1)

    def test_offline_takes_the_servers_trial_expiry_like_everyone_else(self):
        """This test used to assert the OPPOSITE, and the opposite was a hole.

        The old rule was that an Offline trial must NOT import the server's date,
        because ``expiry.dat`` was the authority Offline and writing a three-day
        date into it would stop the till on day four with no way past -- the
        Administrator panel and the vendor's tool are both behind the screen that
        would be blocking. The reasoning was sound about the danger and wrong
        about the price: the consequence was that an Offline trial never expired
        at all. Choosing Offline in the installer was a permanent free licence.

        What changed is the authority. The signed blob decides an Offline licence
        now (core/license_seal.py), it is fetched in the same call that grants the
        trial, and it carries the server's own date -- so the date arrives whether
        or not this mirror is written. The mirror is written too, in both modes,
        because it is what the older Settings screens read and a blank one would
        show a different date from the one being enforced.

        And the way past is no longer behind the block: a licence that says no is
        re-fetched from the server before the shop is turned away, so extending
        the date in the admin panel and connecting the internet is enough.
        """
        with _Activation() as run:
            ta.activate_trial("Roshan Medical", sync_mode="offline")
        self.assertEqual(len(run.licence_cached), 1)
        self.assertEqual(run.licence_cached[0]["expiry_date"], "2026-09-12")

        with _Activation() as run:
            ta.activate_trial("Roshan Medical", sync_mode="online")
        self.assertEqual(len(run.licence_cached), 1)

    def test_activation_stores_the_signed_licence_in_both_modes(self):
        """The one moment an Offline shop is known to have internet.

        It must not be allowed to pass without the shop coming away with a blob
        it can be held to, or the whole scheme has a hole shaped like "choose
        Offline".
        """
        stored = []
        with _Activation() as run, \
             mock.patch("core.license_seal.store_seal",
                        lambda blob, fresh=False: stored.append((blob, fresh))):
            ta.activate_trial("Roshan Medical", sync_mode="offline")
        self.assertEqual(len(stored), 1)
        self.assertTrue(stored[0][1], "the blob must be stored as freshly served")

    def test_offline_writes_the_file_offline_mode_actually_gates_on(self):
        """needs_activation() Offline is is_activated(), and that is this file."""
        with _Activation() as run:
            ta.activate_trial("Roshan Medical", sync_mode="offline")
        self.assertEqual(run.activation_written, ["d" * 64])

    def test_offline_does_not_put_an_android_key_on_screen(self):
        """The SC- key joins a phone to an ONLINE store. It is kept, not shown."""
        with _Activation() as run:
            result = ta.activate_trial("Roshan Medical", sync_mode="offline")
        self.assertFalse(result["show_key"])
        self.assertEqual(result["android_key"], "SC-99AA88BB")
        self.assertEqual(run.saved_keys[0][0], "SC-99AA88BB")

    def test_offline_does_not_pair_over_the_network(self):
        with _Activation() as run:
            ta.activate_trial("Roshan Medical", sync_mode="offline")
        self.assertEqual(run.paired, [])

        with _Activation() as run:
            ta.activate_trial("Roshan Medical", sync_mode="online")
        self.assertEqual(len(run.paired), 1)

    def test_a_flat_network_still_activates_an_offline_shop(self):
        """The PC that chooses Offline is the PC with no internet.

        The three factors that used to stand in for this are gone, so refusing
        here would mean that computer cannot be activated at all. The sign-up is
        best effort; the till opens either way.
        """
        with _Activation(error=OSError("no route to host")) as run:
            result = ta.activate_trial("Roshan Medical", sync_mode="offline")
        self.assertTrue(result["ok"], result.get("error"))
        self.assertEqual(result["sync_mode"], "offline")
        self.assertEqual(run.sync_modes, ["offline"])
        self.assertEqual(run.stores_created, ["Roshan Medical"])
        self.assertEqual(run.activation_written, ["d" * 64])
        self.assertIn("no route to host", result["server_note"])
        # Nothing was invented on the server's behalf.
        self.assertEqual(run.saved_keys, [])
        self.assertEqual(run.licence_cached, [])

    def test_a_flat_network_still_refuses_an_online_shop(self):
        """Online promised the server. Ask first, change nothing until it says yes."""
        with _Activation(error=OSError("no route to host")) as run:
            result = ta.activate_trial("Roshan Medical", sync_mode="online")
        self.assertFalse(result["ok"])
        self.assertEqual(run.sync_modes, [])
        self.assertEqual(run.stores_created, [])
        self.assertEqual(run.activation_written, [])


class AnAlreadyRunningShopIsNotDisturbed(unittest.TestCase):
    def test_a_shop_activated_offline_is_not_a_fresh_install(self):
        """The guard used to ask only about the SC- pairing key.

        An Offline shop has no such key, so a stale provision.json -- a re-run
        installer, a restored profile -- would have run
        ``setup_initial_store_on_activation`` and moved the active store out from
        under a till with a year of bills in it.
        """
        with mock.patch("core.store_link.get_local_android_key", return_value=""), \
             mock.patch("core.license_manager.is_activated", return_value=True), \
             mock.patch("core.store_manager.has_registry", return_value=True):
            self.assertTrue(ta.already_set_up())

    def test_a_half_finished_install_is_still_allowed_to_finish(self):
        """A store folder alone is not a shop; refusing it would strand the PC."""
        with mock.patch("core.store_link.get_local_android_key", return_value=""), \
             mock.patch("core.license_manager.is_activated", return_value=False), \
             mock.patch("core.trial_activation.offline_licence_lapsed",
                        return_value=False), \
             mock.patch("core.store_manager.has_registry", return_value=True):
            self.assertFalse(ta.already_set_up())

    def test_an_offline_shop_whose_licence_has_lapsed_is_still_a_shop(self):
        """On the expiry day activation.dat is DELETED, on purpose.

        ``check_expiry`` removes it to force the vendor's re-activation, so from
        that morning ``is_activated()`` is False on a PC with a year of bills --
        and the guard stopped recognising it as somebody's till. An /ALLUSERS
        install asks its two questions in the administrator's empty profile and
        leaves the answers in ProgramData for every user to read, so the shop
        can meet a handoff file on exactly the day it is least able to survive
        one: the active store would be switched to a new empty one, and the till
        would open blank once the vendor extended the date.
        """
        with mock.patch("core.store_link.get_local_android_key", return_value=""), \
             mock.patch("core.license_manager.is_activated", return_value=False), \
             mock.patch("core.trial_activation.offline_licence_lapsed",
                        return_value=True), \
             mock.patch("core.store_manager.has_registry", return_value=True):
            self.assertTrue(ta.already_set_up())

    def test_reading_the_lapse_does_not_wipe_the_activation(self):
        """It must read expiry.dat, never ask ``check_expiry``.

        check_expiry answers the question by deleting activation.dat. Calling it
        from here -- on a launch that has not even reached the licence check --
        would do that damage itself.
        """
        from datetime import date, timedelta

        yesterday = str(date.today() - timedelta(days=1))
        with mock.patch("core.license_manager._is_online_mode", return_value=False), \
             mock.patch("core.license_manager.is_expiry_check_applied", return_value=True), \
             mock.patch("core.license_manager._read_expiry",
                        return_value={"enabled": True, "expiry_date": yesterday}), \
             mock.patch("core.license_manager.check_expiry") as never, \
             mock.patch("core.license_manager._invalidate_activation_for_reauth") as wipe:
            self.assertTrue(ta.offline_licence_lapsed())
        never.assert_not_called()
        wipe.assert_not_called()

    def test_a_licence_still_running_is_not_a_lapse(self):
        from datetime import date, timedelta

        tomorrow = str(date.today() + timedelta(days=1))
        with mock.patch("core.license_manager._is_online_mode", return_value=False), \
             mock.patch("core.license_manager.is_expiry_check_applied", return_value=True), \
             mock.patch("core.license_manager._read_expiry",
                        return_value={"enabled": True, "expiry_date": tomorrow}):
            self.assertFalse(ta.offline_licence_lapsed())

    def test_a_pc_with_no_expiry_file_at_all_is_not_a_lapse(self):
        """A genuinely fresh install has no expiry.dat -- it must stay fresh."""
        with mock.patch("core.license_manager._is_online_mode", return_value=False), \
             mock.patch("core.license_manager.is_expiry_check_applied", return_value=True), \
             mock.patch("core.license_manager._read_expiry", return_value={}):
            self.assertFalse(ta.offline_licence_lapsed())

    def test_a_paired_pc_is_refused_before_anything_moves(self):
        with _Activation() as run:
            with mock.patch("core.trial_activation.already_set_up", return_value=True):
                result = ta.activate_trial("Roshan Medical", sync_mode="offline")
        self.assertFalse(result["ok"])
        self.assertEqual(run.calls, [])
        self.assertEqual(run.sync_modes, [])
        self.assertEqual(run.stores_created, [])


class TheInstallerHandsOverBothAnswers(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.shared = tempfile.mkdtemp()
        self._p = mock.patch.object(ta, "_appdata_dir", return_value=self.tmp)
        self._p.start()
        self._env = mock.patch.dict(os.environ, {"PROGRAMDATA": self.shared})
        self._env.start()
        ta._RAN_THIS_PROCESS = False
        ta._LAST_FAILURE = {}

    def tearDown(self):
        self._env.stop()
        self._p.stop()
        ta._RAN_THIS_PROCESS = False
        ta._LAST_FAILURE = {}

    def _write_mine(self, text):
        with io.open(os.path.join(self.tmp, "provision.json"), "w", encoding="utf-8") as fh:
            fh.write(text)

    def _write_shared(self, text):
        folder = os.path.join(self.shared, "SatpudaCore")
        os.makedirs(folder, exist_ok=True)
        with io.open(os.path.join(folder, "provision.json"), "w", encoding="utf-8") as fh:
            fh.write(text)
        return os.path.join(folder, "provision.json")

    def test_the_json_carries_the_mode(self):
        self._write_mine(json.dumps({"store_name": "Roshan Medical", "sync_mode": "offline"}))
        found = ta.pending_provision()
        self.assertEqual(found["store_name"], "Roshan Medical")
        self.assertEqual(found["sync_mode"], "offline")

    def test_a_utf8_bom_and_a_devanagari_name_survive(self):
        """SaveStringsToUTF8File writes a BOM; the shop names are often not English."""
        with io.open(os.path.join(self.tmp, "provision.json"), "w",
                     encoding="utf-8-sig") as fh:
            fh.write('{"store_name": "रोशन मेडिकल", "sync_mode": "online"}')
        self.assertEqual(
            ta.pending_provision()["store_name"],
            "रोशन मेडिकल",
        )

    def test_two_plain_lines_work_too(self):
        self._write_mine("Roshan Medical\noffline\n")
        found = ta.pending_provision()
        self.assertEqual(found["store_name"], "Roshan Medical")
        self.assertEqual(found["sync_mode"], "offline")

    def test_a_file_with_no_mode_means_online(self):
        self._write_mine('{"store_name": "Roshan Medical"}')
        self.assertEqual(ta.pending_provision()["sync_mode"], "online")

    # ── A file is not a person typing ─────────────────────────────────────
    def test_a_half_written_file_is_not_read_as_a_shop_name(self):
        """The installer can be killed between opening the file and closing it.

        ``{"store_name": "Roshan Medical", "sync_mo`` is not JSON -- but as the
        first LINE of a text file it was a perfectly acceptable 41-character
        shop name, and the mode, being unreadable, came out Online. Offline
        never reaches the server, so nothing downstream would have refused it:
        the store folder would simply have been named after the fragment.
        """
        self._write_mine('{"store_name": "Roshan Medical", "sync_mo')
        self.assertEqual(ta.pending_provision(), {})

    def test_a_file_of_rubbish_is_not_a_shop_name(self):
        self._write_mine("\x00\x00\x01\x02")
        self.assertEqual(ta.pending_provision(), {})

    def test_a_name_the_server_would_refuse_never_leaves_the_file(self):
        self._write_mine(json.dumps({"store_name": "x" * (SERVER_NAME_MAX + 1)}))
        self.assertEqual(ta.pending_provision(), {})
        self._write_mine(json.dumps({"store_name": "R"}))
        self.assertEqual(ta.pending_provision(), {})
        self._write_mine(json.dumps({"store_name": "  ---  "}))
        self.assertEqual(ta.pending_provision(), {})

    def test_a_good_name_still_survives_the_check(self):
        self._write_mine(json.dumps({"store_name": "  Roshan   Medical  ",
                                     "sync_mode": "offline"}))
        found = ta.pending_provision()
        self.assertEqual(found["store_name"], "Roshan Medical")
        self.assertEqual(found["sync_mode"], "offline")

    def test_an_unreadable_file_starts_nothing_and_is_left_alone(self):
        """No trial, no network call -- and the two-question screen instead."""
        self._write_mine('{"store_name": "Roshan Medi')
        calls = []
        with mock.patch.object(ta, "activate_trial",
                               lambda n, sync_mode="": calls.append(n)), \
             mock.patch.object(ta, "already_set_up", return_value=False):
            result = ta.run_pending_provision()
        self.assertEqual(calls, [])
        self.assertFalse(result["ran"])

    def test_the_mode_reaches_the_activation_and_the_file_is_retired(self):
        self._write_mine('{"store_name": "Roshan Medical", "sync_mode": "offline"}')
        seen = []
        with mock.patch.object(ta, "activate_trial",
                               lambda n, sync_mode="": seen.append((n, sync_mode))
                               or {"ok": True}), \
             mock.patch.object(ta, "already_set_up", return_value=False):
            result = ta.run_pending_provision()
        self.assertEqual(seen, [("Roshan Medical", "offline")])
        self.assertTrue(result["ran"])
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "provision.json")))
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "provision.done")))

    def test_a_refusal_hands_both_answers_back_to_the_screen(self):
        self._write_mine('{"store_name": "Roshan Medical", "sync_mode": "offline"}')
        with mock.patch.object(
            ta, "activate_trial",
            lambda n, sync_mode="": {"ok": False, "error": "Too many sign-ups"},
        ), mock.patch.object(ta, "already_set_up", return_value=False):
            result = ta.run_pending_provision()
        self.assertEqual(result["store_name"], "Roshan Medical")
        self.assertEqual(result["sync_mode"], "offline")

    def _status(self):
        """One /api/license/status read on an un-activated PC."""
        with mock.patch("core.license_manager.needs_activation", return_value=True), \
             mock.patch("core.license_manager.check_expiry", return_value=False), \
             mock.patch("core.license_manager.is_activated", return_value=False), \
             mock.patch("core.license_manager.prepare_device_key", lambda: None), \
             mock.patch("core.license_manager.get_device_key_path",
                        return_value=os.path.join(self.tmp, "nope.key")), \
             mock.patch("core.store_manager.has_registry", return_value=False), \
             mock.patch("core.store_manager.get_active_display_name", return_value=""):
            return dls.get_license_status()

    def test_the_status_read_carries_them_to_the_screen(self):
        self._write_mine('{"store_name": "Roshan Medical", "sync_mode": "offline"}')
        with mock.patch.object(
            ta, "activate_trial",
            lambda n, sync_mode="": {"ok": False, "error": "Too many sign-ups"},
        ), mock.patch.object(ta, "already_set_up", return_value=False):
            status = self._status()
        self.assertEqual(status["provision_error"], "Too many sign-ups")
        self.assertEqual(status["provision_store_name"], "Roshan Medical")
        self.assertEqual(status["provision_sync_mode"], "offline")

    def test_the_screens_own_read_gets_them_too_not_just_the_boot_check(self):
        """The read that RUNS the sign-up is not the read that shows the answer.

        desktop/src/App.tsx reads the licence to decide whether to put the
        activation screen up at all; the screen then mounts and reads the status
        again for itself. The provision runs once per process, so the refusal
        used to be spent on the boot check and the screen came up with a blank
        name, no mode and no explanation -- while the file that held both
        answers had already been retired.
        """
        self._write_mine('{"store_name": "Roshan Medical", "sync_mode": "offline"}')
        with mock.patch.object(
            ta, "activate_trial",
            lambda n, sync_mode="": {"ok": False, "error": "Too many sign-ups"},
        ), mock.patch.object(ta, "already_set_up", return_value=False):
            boot = self._status()          # App.tsx
            screen = self._status()        # ActivationDialog
            again = self._status()         # and every poll after it
        for read in (boot, screen, again):
            self.assertEqual(read["provision_error"], "Too many sign-ups")
            self.assertEqual(read["provision_store_name"], "Roshan Medical")
            self.assertEqual(read["provision_sync_mode"], "offline")

    def test_a_pc_with_no_installer_file_is_told_nothing(self):
        """No file, no refusal, no sentence on a perfectly ordinary first run."""
        status = self._status()
        self.assertEqual(status["provision_error"], "")
        self.assertEqual(status["provision_store_name"], "")

    def test_a_sign_up_that_worked_leaves_no_complaint_behind(self):
        self._write_mine('{"store_name": "Roshan Medical", "sync_mode": "online"}')
        with mock.patch.object(ta, "activate_trial",
                               lambda n, sync_mode="": {"ok": True}), \
             mock.patch.object(ta, "already_set_up", return_value=False):
            first = self._status()
            second = self._status()
        self.assertEqual(first["provision_error"], "")
        self.assertEqual(second["provision_error"], "")

    # ── The other Windows user ────────────────────────────────────────────
    def test_the_shared_copy_is_read_when_this_user_has_none(self):
        """An /ALLUSERS install writes it: {localappdata} was the admin's folder."""
        self._write_shared('{"store_name": "Roshan Medical", "sync_mode": "online"}')
        found = ta.pending_provision()
        self.assertEqual(found["store_name"], "Roshan Medical")
        self.assertEqual(found["shared"], "1")

    def test_this_users_own_file_wins(self):
        self._write_shared('{"store_name": "Somebody Elses Shop"}')
        self._write_mine('{"store_name": "Roshan Medical"}')
        found = ta.pending_provision()
        self.assertEqual(found["store_name"], "Roshan Medical")
        self.assertEqual(found["shared"], "")

    def test_a_user_who_has_been_through_it_never_reads_the_shared_copy(self):
        """ProgramData is shared, and a standard user often cannot delete it."""
        self._write_shared('{"store_name": "Roshan Medical"}')
        with io.open(os.path.join(self.tmp, "provision.done"), "w", encoding="utf-8") as fh:
            fh.write("")
        self.assertEqual(ta.pending_provision(), {})

    def test_using_the_shared_copy_is_recorded_in_this_users_own_folder(self):
        path = self._write_shared('{"store_name": "Roshan Medical", "sync_mode": "online"}')
        seen = []
        with mock.patch.object(ta, "activate_trial",
                               lambda n, sync_mode="": seen.append((n, sync_mode))
                               or {"ok": True}), \
             mock.patch.object(ta, "already_set_up", return_value=False):
            ta.run_pending_provision()
        self.assertEqual(seen, [("Roshan Medical", "online")])
        # The marker is this user's; the shared file is removed when it can be.
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "provision.done")))
        self.assertFalse(os.path.exists(path))

    def test_a_stale_file_never_touches_a_shop_that_is_already_here(self):
        self._write_mine('{"store_name": "Somebody Elses Shop", "sync_mode": "online"}')
        calls = []
        with mock.patch.object(ta, "activate_trial",
                               lambda n, sync_mode="": calls.append(n)), \
             mock.patch.object(ta, "already_set_up", return_value=True):
            ta.run_pending_provision()
        self.assertEqual(calls, [])
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "provision.json")))


class TheThreeCopiesOfTheNameRuleAgree(unittest.TestCase):
    """The screen, the installer and the server must refuse the same names.

    The server's copy is the one that decides -- ``cleanStoreName`` in
    ``src/services/provisionService.js``. The other two exist so the shopkeeper
    is told at the moment he types, which in the installer is before a 200 MB
    download rather than after it.
    """

    def test_the_desktop_screen_uses_the_servers_limits(self):
        body = _source("desktop/src/storeName.ts")
        self.assertIn(f"STORE_NAME_MIN = {SERVER_NAME_MIN}", body)
        self.assertIn(f"STORE_NAME_MAX = {SERVER_NAME_MAX}", body)
        self.assertIn(r"[\p{L}\p{N}]", body)

    def test_the_installer_uses_the_servers_limits(self):
        body = _source("installer/SatpudaCore.iss")
        self.assertIn(f"STORE_NAME_MIN = {SERVER_NAME_MIN};", body)
        self.assertIn(f"STORE_NAME_MAX = {SERVER_NAME_MAX};", body)

    def test_the_installer_asks_before_it_downloads(self):
        """Page order is Inno's, so this pins the two facts that decide it.

        The questions page is anchored to the folder page, which is before the
        Ready page; the 200 MB download runs from ``PrepareToInstall``, which
        Inno calls after it. So the shopkeeper is told about a bad name in the
        second he types it, not when the download has finished.
        """
        body = _source("installer/SatpudaCore.iss")
        self.assertIn("CreateCustomPage(wpSelectDir", body)
        prepare = body[body.index("function PrepareToInstall"):]
        self.assertIn("Result := DoFetch;", prepare)
        # And the answers are handed over only once the install has succeeded.
        post = body[body.index("procedure CurStepChanged"):]
        self.assertIn("WriteProvisionFile;", post)
        self.assertIn("ssPostInstall", post[: post.index("WriteProvisionFile;")])


class TheServerThatHasNotBeenUpdatedYet(unittest.TestCase):
    """The one dependency this screen has that the owner still has to deploy.

    ``/api/provision/trial`` is new. Until the server patch is applied the route
    does not exist, and Express answers a route it does not have with an HTML
    page -- which the transport hands up verbatim as the "message". A fresh
    install that chooses Online therefore has no way forward at all that day, so
    the least it can do is say why, in words the shopkeeper can repeat down a
    telephone, and point at the answer that does still work.
    """

    NOT_FOUND_PAGE = (
        "<!DOCTYPE html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\">\n"
        "<title>Error</title>\n</head>\n<body>\n"
        "<pre>Cannot POST /api/provision/trial</pre>\n</body>\n</html>\n"
    )

    def _refusal(self, status, body=""):
        from core.server_api import ServerHttpError

        return ServerHttpError(status, body)

    def test_a_missing_endpoint_is_not_reported_as_a_web_page(self):
        for status in (404, 405, 501):
            note = ta._friendly(self._refusal(status, self.NOT_FOUND_PAGE))
            self.assertNotIn("<", note, f"HTTP {status} put markup on the screen")
            self.assertNotIn("DOCTYPE", note)
            self.assertIn("has not been updated", note)

    def test_it_names_the_answer_that_still_works(self):
        """Offline activates without the server, so say so while it is true."""
        note = ta._friendly(self._refusal(404, self.NOT_FOUND_PAGE))
        self.assertIn("Offline", note)

    def test_the_offline_shopkeeper_is_not_told_to_choose_offline(self):
        note = ta._friendly(self._refusal(404, self.NOT_FOUND_PAGE), offline=True)
        self.assertIn("has not been updated", note)
        self.assertNotIn("Choose Offline", note)

    def test_offline_still_activates_against_a_server_without_the_endpoint(self):
        with _Activation(error=self._refusal(404, self.NOT_FOUND_PAGE)) as run:
            result = ta.activate_trial("Roshan Medical", sync_mode="offline")
        self.assertTrue(result["ok"], "an Offline shop was refused by a missing route")
        self.assertEqual(run.stores_created, ["Roshan Medical"])
        self.assertIn("has not been updated", result["server_note"])

    def test_online_is_refused_but_told_why(self):
        with _Activation(error=self._refusal(404, self.NOT_FOUND_PAGE)) as run:
            result = ta.activate_trial("Roshan Medical", sync_mode="online")
        self.assertFalse(result["ok"])
        self.assertIn("has not been updated", result["error"])
        self.assertEqual(run.stores_created, [], "the PC was changed by a refusal")

    def test_a_real_refusal_still_speaks_for_itself(self):
        """Only the missing route is rewritten; the server's own words stand."""
        note = ta._friendly(
            self._refusal(429, "This computer has already been given a free trial.")
        )
        self.assertEqual(note, "This computer has already been given a free trial.")


class TheServerThatIsBroken(unittest.TestCase):
    """A 500 is not "the server said no". On live it is EVERY sign-up.

    ``POST /api/provision/trial`` has answered 500 since the day the feature
    shipped: assertWithinLimits sends an untyped parameter and Postgres refuses
    the statement at parse time (42P08). The client had no case for a 5xx, so
    the only thing a shopkeeper ever saw was the error middleware's own
    sentence -- "Something went wrong on the server. Quote reference E… to
    support." -- which tells him nothing he can do and does not mention that
    Offline would work this minute.
    """

    # The exact body live returns, 116 bytes of it.
    BODY = (
        "Something went wrong on the server. Quote reference E7f3a91c to support."
    )

    def _refusal(self, status=500, body=""):
        from core.server_api import ServerHttpError

        return ServerHttpError(status, body or self.BODY)

    def test_a_server_fault_is_not_reported_as_a_bare_reference_number(self):
        note = ta._friendly(self._refusal())
        self.assertNotEqual(note, self.BODY)
        self.assertIn("server", note.lower())

    def test_it_says_the_computer_is_not_at_fault(self):
        note = ta._friendly(self._refusal())
        self.assertIn("not on this computer", note)

    def test_the_online_sentence_names_the_answer_that_still_works(self):
        note = ta._friendly(self._refusal())
        self.assertIn("Offline", note)

    def test_the_offline_shopkeeper_is_not_told_to_choose_offline(self):
        note = ta._friendly(self._refusal(), offline=True)
        self.assertNotIn("Choose Offline", note)
        self.assertIn("set up here", note)

    def test_the_support_reference_is_kept_at_the_end(self):
        note = ta._friendly(self._refusal())
        self.assertTrue(note.rstrip().endswith("E7f3a91c."), note)

    def test_a_body_with_no_reference_does_not_invent_one(self):
        note = ta._friendly(self._refusal(body="Internal Server Error"))
        self.assertNotIn("reference", note.lower())

    def test_every_5xx_that_means_the_same_thing_is_treated_the_same(self):
        for status in (500, 502, 504):
            self.assertIn("not on this computer", ta._friendly(self._refusal(status)))

    def test_offline_still_activates_when_the_server_is_broken(self):
        with _Activation(error=self._refusal()) as run:
            result = ta.activate_trial("Roshan Medical", sync_mode="offline")
        self.assertTrue(result["ok"], "an Offline shop was refused by a broken server")
        self.assertEqual(run.stores_created, ["Roshan Medical"])
        self.assertIn("not on this computer", result["server_note"])

    def test_online_is_refused_and_told_what_to_do_instead(self):
        with _Activation(error=self._refusal()) as run:
            result = ta.activate_trial("Roshan Medical", sync_mode="online")
        self.assertFalse(result["ok"])
        self.assertIn("Offline", result["error"])
        self.assertEqual(run.stores_created, [], "the PC was changed by a refusal")


class TheInstallerScriptStillLexes(unittest.TestCase):
    """A brace-comment in Pascal does not nest, and that has bitten this file.

    ``{ ... {app}\\app ... }`` ends at the FIRST closing brace, and everything
    after it -- ordinary English -- is then handed to the compiler as code. Three
    comments in this script had that shape and would each have failed the very
    first build. Inno Setup is not on the build PC, so this test is the only
    thing standing between a comment and a compile error.
    """

    def _code_without_comments(self):
        src = _source("installer/SatpudaCore.iss")
        code = src[src.index("\n[Code]\n"):]
        out = []
        stray = []
        in_brace = False
        in_paren = False
        for line_no, line in enumerate(code.split("\n"), start=1):
            i = 0
            buf = ""
            while i < len(line):
                ch = line[i]
                if in_brace:
                    if ch == "}":
                        in_brace = False
                    i += 1
                    continue
                if in_paren:
                    if line.startswith("*)", i):
                        in_paren = False
                        i += 2
                        continue
                    i += 1
                    continue
                if ch == "'":
                    i += 1
                    while i < len(line):
                        if line[i] == "'":
                            if i + 1 < len(line) and line[i + 1] == "'":
                                i += 2
                                continue
                            i += 1
                            break
                        i += 1
                    buf += " @STR@ "
                    continue
                if ch == "{":
                    in_brace = True
                    i += 1
                    continue
                if line.startswith("(*", i):
                    in_paren = True
                    i += 2
                    continue
                if line.startswith("//", i):
                    break
                if ch == "}":
                    stray.append((line_no, line.strip()))
                    i += 1
                    continue
                buf += ch
                i += 1
            out.append(buf)
        return "\n".join(out), stray, in_brace

    def test_no_comment_ends_early(self):
        _, stray, _ = self._code_without_comments()
        self.assertEqual(
            stray, [],
            "a brace comment closed early -- everything after it goes to the "
            "compiler as code. Write the constant without its braces.",
        )

    def test_no_comment_runs_to_the_end_of_the_file(self):
        _, _, unterminated = self._code_without_comments()
        self.assertFalse(unterminated)

    def test_begin_and_end_balance(self):
        code, _, _ = self._code_without_comments()
        words = [w.lower() for w in re.findall(r"[A-Za-z_][A-Za-z_0-9]*", code)]
        opens = words.count("begin") + words.count("case") + words.count("try")
        self.assertEqual(opens, words.count("end"))
        self.assertEqual(code.count("("), code.count(")"))


if __name__ == "__main__":
    unittest.main()
