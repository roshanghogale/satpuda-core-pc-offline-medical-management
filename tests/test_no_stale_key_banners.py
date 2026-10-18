"""The two red "no key" boxes after activation (owner, 18 Oct 2026).

A background read or link attempt made while the PC is being activated -- before the shop's
SC- key is saved -- records "No Android store key" / "not connected to a shop". Those stayed
on the Home screen after a successful activation. Once the key is there they are stale.
"""
import unittest
from unittest import mock

from core import desktop_api


class StaleNoKeyMessages(unittest.TestCase):
    def _texts(self, key, link_msg, catalog_msg):
        with mock.patch("core.sync_prefs.is_online_mode", return_value=True), \
                mock.patch("core.store_link.get_local_android_key", return_value=key), \
                mock.patch("core.server_live.last_link_error", return_value=link_msg), \
                mock.patch("core.online_catalog.last_error", return_value=catalog_msg):
            return desktop_api._store_link_error_text(), desktop_api._catalog_error_text()

    LINK = ('This computer is not connected to a shop on the Satpuda server (local store "A"). '
            "Open Set Up and enter the shop's SC- key.")
    CAT = "No Android store key. Pair this PC with the server first."

    def test_hidden_once_the_key_is_saved(self):
        self.assertEqual(self._texts("SC-TEST0001", self.LINK, self.CAT), ("", ""))

    def test_still_shown_while_there_is_no_key(self):
        self.assertEqual(self._texts("", self.LINK, self.CAT), (self.LINK, self.CAT))

    def test_other_errors_are_never_hidden(self):
        other = "The server did not answer (timed out)."
        self.assertEqual(self._texts("SC-TEST0001", other, other), (other, other))


if __name__ == "__main__":
    unittest.main()
