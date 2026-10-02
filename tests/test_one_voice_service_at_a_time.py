"""One voice service, not two (2 Oct 2026).

The engine's autostart and the voice bar's "start" came 4 s apart, before the first
service had loaded its model and opened its port; both were started and ran at 2.7 GB
each. A service this engine started and that is still alive is now waited for.
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import core.voice_pack as vp  # noqa: E402


class StartingTwice(unittest.TestCase):
    def setUp(self):
        self.addCleanup(setattr, vp, "_service", None)

    def test_a_service_still_loading_is_not_started_again(self):
        loading = mock.Mock()
        loading.poll.return_value = None            # alive, port not open yet
        vp._service = loading
        with mock.patch.object(vp, "service_running", return_value=False), \
                mock.patch.object(vp, "_spawn_service") as spawn, \
                mock.patch.object(vp, "status", return_value={}):
            vp.start_service()
        spawn.assert_not_called()

    def test_a_service_that_ended_is_started_again(self):
        ended = mock.Mock()
        ended.poll.return_value = 1
        vp._service = ended
        with mock.patch.object(vp, "service_running", return_value=False), \
                mock.patch.object(vp, "_spawn_service", return_value={}) as spawn:
            vp.start_service()
        spawn.assert_called_once()

    def test_one_answering_already_is_left_alone(self):
        with mock.patch.object(vp, "service_running", return_value=True), \
                mock.patch.object(vp, "_spawn_service") as spawn, \
                mock.patch.object(vp, "status", return_value={}):
            vp.start_service()
        spawn.assert_not_called()


if __name__ == "__main__":
    unittest.main()
