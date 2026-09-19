"""Activation has a way in for a shop that already exists, and a way out when it fails.

THREE THINGS THAT WERE MISSING, and together they are the owner's report that a
new shop "cannot be signed up or activated at all, and the app ends on a blocked
screen with no way out".

1. NO WAY TO REACH A SHOP THE OWNER MADE HIMSELF. The admin panel creates a
   store and shows its SC- key; the desktop could only ever DISPLAY that key,
   never accept one. So "I already have a shop" meant the long three-factor
   form, whose Online step opens with ``api.admin_login()`` -- the vendor
   administrator, from a password compiled into the build -- and then matches a
   store by display NAME when the key does not match. Pairing with the key
   instead carries no credential and can reach exactly one store.

2. THE SIGNED LICENCE WAS A SIDE EFFECT. ``_record_server_activation`` recorded
   the activation date and hoped the opportunistic background refresh in
   ``get_expiry_state`` would fetch the blob. A PC closed before that worker
   finished was activated with no licence, and the first launch without internet
   showed "Licence not found".

3. THE BLOCKED SCREEN WAS A DEAD END. No buttons, and the self-healing re-check
   in App.tsx was gated on the OTHER screen.
"""
import os
import re
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core import desktop_license_service as dls  # noqa: E402

SERVER_STORE = {
    "id": 7,
    "store_id": "wadner_bh_11aa",
    "store_key": "Store_Mauli_Medical",
    "store_name": "Mauli Medical Wadner bh",
    "android_key": "SC-9F2C1AB0",
}


def _source(rel: str) -> str:
    with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
        return fh.read()


class _Paired:
    """A working /api/auth/pair, with every local side effect held in memory."""

    def __init__(self, test, *, store=None, error=None, license_row=None):
        self.test = test
        self.store = dict(store or SERVER_STORE)
        self.error = error
        self.license_row = license_row
        self.pair_calls = []
        self.saved_keys = []
        self.adoptions = []
        self.sync_modes = []
        self.stores_created = []
        self.put_bodies = []
        self.sealed = []
        self.has_key = ""
        self.has_registry = False

    def _pair(self, **kwargs):
        self.pair_calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return {"token": "jwt.for.this.store", "store": dict(self.store)}

    def __enter__(self):
        self._patches = [
            mock.patch("core.server_api.pair_store", self._pair),
            mock.patch("core.server_api.save_session", lambda k, s: None),
            mock.patch("core.server_api.store_token_for_active",
                       lambda *a, **k: "jwt.for.this.store"),
            mock.patch("core.server_api._pc_device_id", lambda: "pc-1"),
            mock.patch("core.store_link.get_local_android_key",
                       lambda *a, **k: self.has_key),
            mock.patch("core.store_link._save_local",
                       lambda key, store_key="": self.saved_keys.append((key, store_key))),
            mock.patch("core.store_manager.has_registry", lambda: self.has_registry),
            mock.patch("core.store_manager.get_active_store_key",
                       lambda: "Store_Mauli_Medical"),
            mock.patch("core.store_manager.setup_initial_store_on_activation",
                       lambda name: self.stores_created.append(name)),
            mock.patch("core.sync_prefs.set_sync_mode",
                       lambda mode: self.sync_modes.append(mode)),
            mock.patch("core.sync_prefs.get_sync_mode", lambda: "online"),
            mock.patch("core.server_live._record_store_adoption",
                       lambda key, remote: self.adoptions.append((key, dict(remote)))),
            mock.patch("core.license_manager.save_activation_date_local",
                       lambda day=None: "2026-09-16"),
            mock.patch("core.license_manager._cache_server_license_locally",
                       lambda lic: None),
            mock.patch("core.license_manager._get_hardware_hash", lambda **k: "hw"),
            mock.patch("core.license_manager._write_activation", lambda hw: None),
            mock.patch("core.license_manager._write_hw_cache", lambda hw: None),
            mock.patch("core.server_api.get_license",
                       lambda token: dict(self.license_row or {})),
            mock.patch("core.server_api.put_license", self._put),
            mock.patch("core.server_api.get_signed_license",
                       lambda token, binding, **k: {"signed": "SATPUDA1.blob.sig"}),
            mock.patch("core.license_seal.store_seal", self._store_seal),
            mock.patch("core.license_seal._binding_body", lambda: {"machine_id": "m"}),
        ]
        for p in self._patches:
            p.start()
        return self

    def _put(self, token, payload):
        self.put_bodies.append(dict(payload or {}))
        return {
            "activation_date": "2026-09-16",
            "expiry_date": "2026-09-19",
            "access_allowed": True,
            "is_active": True,
        }

    def _store_seal(self, blob, *, fresh=False):
        self.sealed.append((blob, fresh))
        return {"exp": "2026-09-19"}

    def __exit__(self, *exc):
        for p in reversed(self._patches):
            p.stop()
        return False


class TheScKeyPathReachesExactlyOneShop(unittest.TestCase):
    def test_the_first_press_only_looks_the_key_up(self):
        """Nothing on this computer moves on the strength of a paste."""
        with _Paired(self) as run:
            res = dls.pair_with_store_key({"android_key": "SC-9F2C1AB0"})
        self.assertTrue(res["ok"])
        self.assertTrue(res["confirm_required"])
        self.assertEqual(res["store_name"], "Mauli Medical Wadner bh")
        self.assertEqual(run.saved_keys, [])
        self.assertEqual(run.stores_created, [])
        self.assertEqual(run.sync_modes, [])
        self.assertEqual(run.adoptions, [])

    def test_the_second_press_connects_and_keeps_the_key(self):
        with _Paired(self) as run:
            res = dls.pair_with_store_key(
                {"android_key": "SC-9F2C1AB0", "confirm": True}
            )
        self.assertTrue(res["ok"])
        self.assertTrue(res["activated"])
        self.assertEqual(run.saved_keys, [("SC-9F2C1AB0", "Store_Mauli_Medical")])
        self.assertEqual(run.stores_created, ["Mauli Medical Wadner bh"])
        self.assertIn("online", run.sync_modes)

    def test_it_pairs_by_key_and_never_sends_a_name(self):
        """A name is what can reach a stranger's shop; a key cannot."""
        with _Paired(self) as run:
            dls.pair_with_store_key({"android_key": "SC-9F2C1AB0", "confirm": True})
        self.assertEqual(run.pair_calls[0]["android_key"], "SC-9F2C1AB0")
        self.assertNotIn("store_name", run.pair_calls[0])

    def test_it_carries_no_administrator_credential(self):
        def _boom(*a, **k):
            raise AssertionError("the vendor administrator was used")

        with _Paired(self), \
             mock.patch("core.server_api.admin_login", _boom), \
             mock.patch("core.server_api.list_remote_stores", _boom):
            res = dls.pair_with_store_key(
                {"android_key": "SC-9F2C1AB0", "confirm": True}
            )
        self.assertTrue(res["ok"])

    def test_the_pc_is_pinned_to_the_store_id_not_the_name(self):
        with _Paired(self) as run:
            dls.pair_with_store_key({"android_key": "SC-9F2C1AB0", "confirm": True})
        self.assertEqual(len(run.adoptions), 1)
        _key, remote = run.adoptions[0]
        self.assertEqual(remote["store_id"], "wadner_bh_11aa")

    def test_a_wrong_key_changes_nothing_at_all(self):
        from core.server_api import ServerHttpError

        with _Paired(self, error=ServerHttpError(401, "Invalid store key.")) as run:
            res = dls.pair_with_store_key(
                {"android_key": "SC-NOTAKEY", "confirm": True}
            )
        self.assertFalse(res["ok"])
        self.assertIn("Invalid store key", res["error"])
        self.assertEqual(run.saved_keys, [])
        self.assertEqual(run.stores_created, [])
        self.assertEqual(run.sync_modes, [])

    def test_an_empty_key_is_refused_without_a_round_trip(self):
        with _Paired(self) as run:
            res = dls.pair_with_store_key({"android_key": "   "})
        self.assertFalse(res["ok"])
        self.assertEqual(run.pair_calls, [])

    def test_a_pc_that_already_belongs_to_a_shop_is_refused(self):
        """Re-pointing a working till is the one accident worth refusing."""
        with _Paired(self) as run:
            run.has_key = "SC-SOMEONEELSE"
            res = dls.pair_with_store_key(
                {"android_key": "SC-9F2C1AB0", "confirm": True}
            )
        self.assertFalse(res["ok"])
        self.assertIn("already connected", res["error"])
        self.assertEqual(run.pair_calls, [])

    def test_a_shop_already_on_this_pc_is_linked_and_not_moved(self):
        """Online mode is server-only: flipping an Offline shop hides its books.

        A PC with a store folder but no pairing key is the shop this path is
        for -- activated Offline, no licence, stuck. It gets the link and the
        licence; its own database stays exactly where it is, and the mode is not
        changed underneath it.
        """
        with _Paired(self, license_row={"activation_date": "2026-05-01",
                                        "expiry_date": "2027-05-01"}) as run:
            run.has_registry = True
            res = dls.pair_with_store_key(
                {"android_key": "SC-9F2C1AB0", "confirm": True}
            )
        self.assertTrue(res["ok"])
        self.assertEqual(run.stores_created, [], "the shop's store was replaced")
        self.assertEqual(run.sync_modes, [], "an offline shop was flipped online")
        self.assertEqual(run.saved_keys, [("SC-9F2C1AB0", "Store_Mauli_Medical")])

    def test_a_licence_that_already_exists_is_not_restamped(self):
        """PUT activation_date = today would end a running shop in three days."""
        with _Paired(self, license_row={"activation_date": "2026-05-01",
                                        "expiry_date": "2027-05-01",
                                        "access_allowed": True}) as run:
            res = dls.pair_with_store_key(
                {"android_key": "SC-9F2C1AB0", "confirm": True}
            )
        self.assertEqual(run.put_bodies, [], "an existing licence was restamped")
        self.assertEqual(res["activation_date"], "2026-05-01")
        self.assertEqual(res["expiry_date"], "2027-05-01")

    def test_a_store_made_by_hand_gets_its_dates_set(self):
        """adminService.createStore leaves activation_date and expiry NULL."""
        with _Paired(self, license_row={"activation_date": None}) as run:
            res = dls.pair_with_store_key(
                {"android_key": "SC-9F2C1AB0", "confirm": True}
            )
        self.assertEqual(len(run.put_bodies), 1)
        self.assertEqual(run.put_bodies[0]["activation_date"], "2026-09-16")
        self.assertEqual(res["expiry_date"], "2026-09-19")

    def test_the_signed_licence_is_collected_on_the_spot(self):
        with _Paired(self, license_row={"activation_date": None}) as run:
            dls.pair_with_store_key({"android_key": "SC-9F2C1AB0", "confirm": True})
        self.assertEqual(run.sealed, [("SATPUDA1.blob.sig", True)])


class ActivationStoresTheLicenceItself(unittest.TestCase):
    """Not "and the background thread will probably fetch it"."""

    def test_recording_the_activation_stores_the_signed_licence(self):
        with _Paired(self, license_row={"activation_date": None}) as run:
            out = dls._record_server_activation()
        self.assertEqual(run.sealed, [("SATPUDA1.blob.sig", True)])
        self.assertTrue(out["sealed"])
        self.assertEqual(out["seal_note"], "")

    def test_it_asks_exactly_once_and_marks_the_blob_fresh(self):
        """fresh=True sets the rollback ratchet instead of being judged by it."""
        with _Paired(self, license_row={"activation_date": None}) as run:
            dls._record_server_activation()
        self.assertEqual(len(run.sealed), 1)
        self.assertTrue(run.sealed[0][1])

    def test_a_blob_that_will_not_verify_here_is_reported_not_swallowed(self):
        with _Paired(self) as run:
            with mock.patch("core.license_seal.store_seal", lambda b, **k: None):
                out = dls._record_server_activation()
        self.assertFalse(out["sealed"])
        self.assertIn("verify", out["seal_note"].lower())

    def test_a_server_that_will_not_hand_it_over_says_so(self):
        with _Paired(self):
            with mock.patch("core.server_api.get_signed_license",
                            side_effect=RuntimeError("no route to host")):
                out = dls._record_server_activation()
        self.assertFalse(out["sealed"])
        self.assertIn("signed licence", out["seal_note"])

    def test_the_long_form_carries_the_warning_up_to_the_screen(self):
        """Silence here is what leaves a PC one offline launch from the block."""
        with _Paired(self) as run:
            with mock.patch("core.license_seal.store_seal", lambda b, **k: None), \
                 mock.patch("core.license_manager.attempt_activation",
                            lambda u, p, k: (True, "")), \
                 mock.patch("core.license_manager.is_activated", lambda: True), \
                 mock.patch("core.store_manager.get_active_display_name",
                            lambda: "Mauli Medical Wadner bh"), \
                 mock.patch("core.server_live.ensure_active_store_on_server",
                            lambda **k: {"android_key": "SC-9F2C1AB0"}):
                run.has_registry = True
                res = dls.activate_license({
                    "username": "u", "password": "p", "device_key": "k",
                    "store_name": "Mauli Medical Wadner bh", "sync_mode": "online",
                })
        self.assertTrue(res["ok"])
        self.assertIn("verify", res["server_note"].lower())


class TheBlockedScreenHasAWayOut(unittest.TestCase):
    """Read out of the sources, because the bug was in what they did not do."""

    def test_the_engine_offers_a_forced_recheck(self):
        seen = {}

        def _fetch(**kwargs):
            seen.update(kwargs)
            return None

        with mock.patch("core.license_seal.fetch_seal", _fetch), \
             mock.patch.object(dls, "get_license_status",
                               lambda: {"ok": True, "access_blocked": True}):
            out = dls.recheck_license()
        self.assertTrue(out["rechecked"])
        self.assertTrue(seen.get("force"), "the once-a-minute limit ignored a person")
        self.assertTrue(float(seen.get("wait") or 0) > 0, "it did not wait for an answer")

    def test_the_desktop_rechecks_while_the_block_screen_is_up(self):
        body = _source("desktop/src/App.tsx")
        self.assertIn("if (!licenseBlocked && !accessBlocked) return", body)
        self.assertIn("if (!lic.access_blocked) window.location.reload()", body)

    def test_the_block_screen_has_controls(self):
        body = _source("desktop/src/pages/ActivationDialog.tsx")
        start = body.index("export function LicenseAccessBlockedDialog")
        screen = body[start:]
        self.assertIn("Try again now", screen)
        self.assertIn("recheckLicense", screen)
        self.assertIn("pairWithStoreKey", screen)

    def test_the_full_form_offers_the_sc_key_too(self):
        """Where a lapsed starter window and an expired licence actually land.

        The status read sends both to the FULL form (has_registry or
        expiry_reactivation turns the two-question screen off), and that screen
        deliberately offers no way back -- so without this button the shop's own
        SC- key, which the engine accepts, could not be typed anywhere.
        """
        body = _source("desktop/src/pages/ActivationDialog.tsx")
        full = body[body.index("\n          ) : ("):]
        self.assertIn("I already have a shop", full)
        self.assertIn("setOverlay('storeKey')", full)

    def test_the_classic_gate_is_no_longer_a_message_box_and_exit(self):
        body = _source("main.py")
        self.assertIn("show_license_recovery_dialog", body)
        gate = body[body.index("def _run_expiry_check"):]
        self.assertNotIn("Licence not found\", NEEDS_INTERNET_MESSAGE", gate)

    def test_the_classic_screens_offer_the_sc_key(self):
        body = _source("widgets/activation_dialog.py")
        self.assertIn("def show_license_recovery_dialog", body)
        self.assertIn("def _pair_key_dialog", body)
        self.assertIn("I already have a shop - enter its SC- key", body)

    def test_the_tauri_setup_screen_offers_the_sc_key(self):
        body = _source("desktop/src/pages/ActivationDialog.tsx")
        self.assertIn("I already have a shop", body)
        self.assertIn("pairWithStoreKey", body)
        # And the two-question screen is still exactly two questions: the new
        # way in is a button, not a fourth field.
        start = body.index("{simple ? (")
        first = body[start:body.index("\n          ) : (", start)]
        self.assertEqual(first.count("<input"), 3)

    def test_the_engine_serves_both_new_routes(self):
        body = _source("core/desktop_api.py")
        for route in ("/api/license/pair-key", "/api/license/recheck"):
            self.assertEqual(
                body.count(f'"{route}"'), 2,
                f"{route} is not both advertised and handled",
            )


if __name__ == "__main__":
    unittest.main()
