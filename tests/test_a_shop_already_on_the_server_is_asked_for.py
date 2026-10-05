"""A shop already on the server is not given a second, empty store (5 Oct 2026).

Vaibhav's laptop was reinstalled, the shop's name was typed on the trial page, and the
server made store 142 beside the real 131 (134 and 137 the same way before it). Now:
  * the server answers 409 for a name it already holds, and the app says so instead of
    activating -- in both modes -- so the screen can offer the SC- key;
  * "this is a new shop" (confirm_new) still makes one;
  * the installer can take the SC- key, and the first launch pairs with that shop.
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import trial_activation as ta  # noqa: E402
from core.server_api import ServerHttpError  # noqa: E402


class TheInstallerHandoff(unittest.TestCase):
    def test_an_sc_key_is_read_without_a_name(self):
        got = ta._parse_provision('{"sc_key": "sc-1a2b3c4d", "store_name": "", "sync_mode": "online"}')
        self.assertEqual("SC-1A2B3C4D", got["sc_key"])
        self.assertEqual("online", got["sync_mode"])

    def test_a_malformed_key_is_not_a_key(self):
        self.assertEqual({}, ta._parse_provision('{"sc_key": "SC-12", "store_name": ""}'))
        got = ta._parse_provision('{"sc_key": "hello", "store_name": "Roshan Medical"}')
        self.assertNotIn("sc_key", got)
        self.assertEqual("Roshan Medical", got["store_name"])

    def test_the_first_launch_pairs_with_the_key_and_makes_no_trial(self):
        pending = {"sc_key": "SC-1A2B3C4D", "store_name": "", "sync_mode": "online", "path": "x"}
        with mock.patch.object(ta, "_RAN_THIS_PROCESS", False), \
                mock.patch.object(ta, "pending_provision", return_value=pending), \
                mock.patch.object(ta, "already_set_up", return_value=False), \
                mock.patch.object(ta, "_consume_provision_file") as consumed, \
                mock.patch.object(ta, "activate_trial") as trial, \
                mock.patch("core.desktop_license_service.pair_with_store_key",
                           return_value={"ok": True, "store_name": "VAIBHAV"}) as pair:
            res = ta.run_pending_provision()
        trial.assert_not_called()
        pair.assert_called_once_with({"android_key": "SC-1A2B3C4D", "confirm": True})
        self.assertTrue(res["ok"])
        self.assertEqual("paired", consumed.call_args[0][0])


class ANameAlreadyOnTheServer(unittest.TestCase):
    def _trial(self, mode, confirm_new=False, side_effect=None):
        with mock.patch.object(ta, "already_set_up", return_value=False), \
                mock.patch("core.server_api.provision_trial", side_effect=side_effect) as call, \
                mock.patch.object(ta, "_activate_offline_locally") as offline_local:
            res = ta.activate_trial("Vaibhav Medical & Gen Sto", sync_mode=mode, confirm_new=confirm_new)
        return res, call, offline_local

    def test_online_is_asked_not_activated(self):
        err = ServerHttpError(409, 'A shop called "Vaibhav Medical & Gen Sto" is already on Satpuda.')
        res, _, _ = self._trial("online", side_effect=err)
        self.assertFalse(res["ok"])
        self.assertEqual("name_exists", res["code"])
        self.assertIn("already on Satpuda", res["error"])

    def test_offline_is_asked_too_not_set_up_locally(self):
        err = ServerHttpError(409, "taken")
        res, _, offline_local = self._trial("offline", side_effect=err)
        offline_local.assert_not_called()
        self.assertEqual("name_exists", res["code"])

    def test_a_new_shop_is_said_out_loud(self):
        _, call, _ = self._trial("online", confirm_new=True, side_effect=RuntimeError("stop here"))
        self.assertTrue(call.call_args.kwargs["confirm_new"])
        _, call, _ = self._trial("online", side_effect=RuntimeError("stop here"))
        self.assertFalse(call.call_args.kwargs["confirm_new"])

    def test_the_screen_passes_the_answer_through(self):
        from core import desktop_license_service as dls

        with mock.patch("core.trial_activation.activate_trial", return_value={"ok": True}) as run:
            dls.activate_trial({"store_name": "X", "sync_mode": "online", "confirm_new": True})
            self.assertTrue(run.call_args.kwargs["confirm_new"])
            dls.activate_trial({"store_name": "X", "sync_mode": "online", "confirm_new": "yes"})
            self.assertFalse(run.call_args.kwargs["confirm_new"])


class TheServerRequest(unittest.TestCase):
    def test_confirm_new_is_sent_only_when_asked(self):
        from core import server_api as api

        sent = []
        with mock.patch.object(api, "_request", side_effect=lambda m, p, body=None, **k: sent.append(body) or {"ok": True, "data": {}}):
            api.provision_trial(store_name="A", device_id="d")
            api.provision_trial(store_name="A", device_id="d", confirm_new=True)
        self.assertNotIn("confirm_new", sent[0])
        self.assertIs(True, sent[1]["confirm_new"])


if __name__ == "__main__":
    unittest.main()
