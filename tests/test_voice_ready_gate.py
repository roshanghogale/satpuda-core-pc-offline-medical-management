"""Voice is ready only when the store is allowed AND the whole pack is installed (19 Oct 2026).

The owner: voice must not load while the voice pack is not downloaded, and the floating
voice button must not show until the full pack is there. The engine answers one
"ready" (GET /api/voice/ready); until it says ready no voice service is started, so no
model is loaded and no command list is read.
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import core.voice_pack as vp  # noqa: E402


def whole_pack(home: str, *, skip: str = "", marker: str = '{"version": "v1"}') -> str:
    cur = os.path.join(home, "current")
    for rel in vp.WHOLE:
        if rel == skip:
            continue
        path = os.path.join(cur, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(marker if rel == "pack.json" else "x")
    return cur


class _Home(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.home, True)
        env = mock.patch.dict(os.environ, {"SATPUDA_VOICE_PACK_DIR": self.home})
        env.start()
        self.addCleanup(env.stop)
        saved = dict(vp._state)
        self.addCleanup(lambda: (vp._state.clear(), vp._state.update(saved)))
        vp._set(state="idle", error="")
        self.addCleanup(setattr, vp, "_service", None)


class TheGate(_Home):
    def test_allowed_and_whole_pack_is_ready(self):
        whole_pack(self.home)
        r = vp.voice_ready(allowed=True)
        self.assertTrue(r["ready"])
        self.assertEqual("ready", r["reason"])

    def test_not_allowed_is_not_ready_even_with_the_pack(self):
        whole_pack(self.home)
        r = vp.voice_ready(allowed=False)
        self.assertFalse(r["ready"])
        self.assertEqual("not_allowed", r["reason"])

    def test_no_pack_is_not_ready(self):
        r = vp.voice_ready(allowed=True)
        self.assertFalse(r["ready"])
        self.assertEqual("not_installed", r["reason"])

    def test_first_download_running_is_not_ready(self):
        vp._set(state="downloading", done=10, total=100)
        r = vp.voice_ready(allowed=True)
        self.assertFalse(r["ready"])
        self.assertEqual("installing", r["reason"])

    def test_half_copied_pack_is_not_ready(self):
        whole_pack(self.home, skip=os.path.join("voice", "voice_service.py"))
        r = vp.voice_ready(allowed=True)
        self.assertFalse(r["ready"])
        self.assertEqual("broken", r["reason"])
        self.assertIsNone(vp.installed())

    def test_pack_without_its_marker_is_not_ready(self):
        whole_pack(self.home, skip="pack.json")     # extracted, never finished
        self.assertFalse(vp.voice_ready(allowed=True)["ready"])

    def test_damaged_marker_is_not_ready(self):
        whole_pack(self.home, marker="{not json")
        self.assertFalse(vp.voice_ready(allowed=True)["ready"])

    def test_an_update_downloading_keeps_the_whole_old_pack_ready(self):
        whole_pack(self.home)
        vp._set(state="downloading", done=10, total=100)
        self.assertTrue(vp.voice_ready(allowed=True)["ready"])

    def test_the_switch_is_asked_when_not_told(self):
        whole_pack(self.home)
        with mock.patch("core.voice_switch.voice_status", return_value={"enabled": True}):
            self.assertTrue(vp.voice_ready()["ready"])
        with mock.patch("core.voice_switch.voice_status", return_value={"enabled": False}):
            self.assertFalse(vp.voice_ready()["ready"])


class NothingStartsWhenNotReady(_Home):
    def _start(self, allowed: bool):
        with mock.patch("core.voice_switch.voice_status", return_value={"enabled": allowed}), \
                mock.patch.object(vp, "service_running", return_value=False), \
                mock.patch.object(vp.subprocess, "Popen") as popen:
            vp.start_service()
        return popen

    def test_no_pack_no_service(self):
        self._start(True).assert_not_called()

    def test_not_allowed_no_service(self):
        whole_pack(self.home)
        self._start(False).assert_not_called()

    def test_half_pack_no_service(self):
        whole_pack(self.home, skip="pack.json")
        self._start(True).assert_not_called()

    def test_ready_starts_the_service(self):
        whole_pack(self.home)
        self._start(True).assert_called_once()

    def test_autostart_without_a_pack_asks_nothing_and_starts_nothing(self):
        with mock.patch("core.voice_switch.voice_status") as switch, \
                mock.patch.object(vp, "start_service") as start, \
                mock.patch.object(vp.threading, "Thread") as thread:
            vp.autostart()
            target = thread.call_args.kwargs["target"]
            target()                                 # run the background job here
        switch.assert_not_called()
        start.assert_not_called()

    def test_autostart_not_allowed_starts_nothing(self):
        whole_pack(self.home)
        with mock.patch("core.voice_switch.voice_status", return_value={"enabled": False}), \
                mock.patch.object(vp, "start_service") as start, \
                mock.patch.object(vp.threading, "Thread") as thread:
            vp.autostart()
            thread.call_args.kwargs["target"]()
        start.assert_not_called()

    def test_autostart_ready_starts(self):
        whole_pack(self.home)
        with mock.patch("core.voice_switch.voice_status", return_value={"enabled": True}), \
                mock.patch.object(vp, "start_service") as start, \
                mock.patch.object(vp.threading, "Thread") as thread:
            vp.autostart()
            thread.call_args.kwargs["target"]()
        start.assert_called_once()

    def test_install_refused_when_the_store_is_not_allowed(self):
        with mock.patch("core.voice_switch.voice_status", return_value={"enabled": False}), \
                mock.patch.object(vp.threading, "Thread") as thread:
            s = vp.install()
        thread.assert_not_called()
        self.assertIn("band", s["error"])


class PausedDownload(_Home):
    def test_a_paused_download_says_how_much_is_kept(self):
        zip_path = os.path.join(self.home, "SatpudaVoicePack.zip")
        with open(zip_path, "wb") as fh:
            fh.truncate(1000)
        with open(zip_path + ".parts", "w", encoding="utf-8") as fh:
            json.dump([100, 50, 0, 25], fh)
        self.assertEqual(175, vp.partial_bytes())

    def test_nothing_kept_is_zero(self):
        self.assertEqual(0, vp.partial_bytes())


if __name__ == "__main__":
    unittest.main()
