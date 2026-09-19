"""A migration must never report success it did not achieve.

The Drive restore in Online mode restores, pushes to the server, verifies, and
only then deletes the local database. The verification worked -- it correctly
KEPT the local file when the server came up short -- but its verdict was thrown
away by the caller, which returned a hard-coded success saying "pushed to
server, and local DB deleted". The shop restarted, met "Local data found",
believed the migration had already worked, and pressed an unconfirmed
"Delete Local". The rows the server never accepted were then gone from the only
other copy.
"""
import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def src(rel: str) -> str:
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


class TestRestoreReportsTheTruth(unittest.TestCase):
    def setUp(self):
        self.s = src("core/desktop_settings_service.py")

    def test_the_push_result_is_read(self):
        self.assertIn('if not push_res.get("ok"):', self.s)

    def test_a_failed_push_is_reported_as_a_failure(self):
        i = self.s.index('if not push_res.get("ok"):')
        block = self.s[i:i + 700]
        self.assertIn('"ok": False', block)
        self.assertIn("NOT deleted", block)

    def test_success_does_not_claim_a_deletion_that_did_not_happen(self):
        # The message must depend on what was actually removed.
        self.assertIn("if removed", self.s)
        self.assertNotIn(
            'Backup restored, pushed to server, and local DB deleted', self.s
        )


class TestPushButtonRefusesTheEmptyShell(unittest.TestCase):
    def test_online_push_to_server_is_refused_with_a_reason(self):
        s = src("core/desktop_settings_service.py")
        self.assertIn('"code": "nothing_local_to_push"', s)
        i = s.index('"code": "nothing_local_to_push"')
        self.assertIn("nothing here to push", s[i - 600:i])


class TestVerifyIsHonestInOnlineMode(unittest.TestCase):
    def test_it_does_not_compare_against_an_empty_shell(self):
        s = src("core/desktop_settings_service.py")
        self.assertIn('"server_only": server_only', s)

    def test_the_duplicated_key_is_gone(self):
        s = src("core/desktop_settings_service.py")
        block = s[s.index('collections[col] = {'):][:220]
        self.assertEqual(block.count('"server": remote_n'), 1)


class TestDeleteLocalAsksFirst(unittest.TestCase):
    def test_the_button_confirms(self):
        # The LAST mention is the button's own handler; earlier ones are the
        # action's type declaration and the shared runner.
        s = src("desktop/src/pages/OnlineMigrateDialog.tsx")
        i = s.rindex("online_migrate_wipe")
        self.assertIn("window.confirm", s[max(0, i - 900):i + 200])

    def test_the_wording_names_the_risk(self):
        s = src("desktop/src/pages/OnlineMigrateDialog.tsx")
        self.assertIn("only copy on this PC", s)


if __name__ == "__main__":
    unittest.main(verbosity=1)
