"""The voice pack reaches another PC faster (3 Oct 2026).

On the owner's line one connection to GitHub gave 1.5 MB/s (16 minutes for the pack)
and four gave 2.8 MB/s. A pack already copied to the PC or a pendrive needs no download
at all. And the model is prepared while the screen says so, not at the first start.
"""
import io
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
import zipfile
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import core.voice_pack as vp  # noqa: E402

DATA = bytes((i * 7 + i // 251) % 256 for i in range(1_000_003))


class _Resp(io.BytesIO):
    def __init__(self, body: bytes, status: int):
        super().__init__(body)
        self.status = status
        self.headers = {"Content-Length": str(len(body))}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def fake_server(data: bytes, *, ranges=True, fail_once_at=None):
    calls = []
    failed = set()
    lock = threading.Lock()

    def urlopen(req, timeout=0):
        rng = req.headers.get("Range")
        with lock:
            calls.append(rng)
        if not ranges or not rng:
            return _Resp(data, 200)
        a, b = rng.split("=")[1].split("-")
        a, b = int(a), int(b)
        body = data[a:b + 1]
        if fail_once_at is not None and a == fail_once_at and a not in failed:
            failed.add(a)
            body = body[: len(body) // 3]          # the line drops part-way
        return _Resp(body, 206)
    return urlopen, calls


class _Tmp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        vp._cancel.clear()
        p = mock.patch.object(vp.time, "sleep", lambda s: None)
        p.start()
        self.addCleanup(p.stop)


class FourConnections(_Tmp):
    def test_the_file_is_whole_and_four_ranges_were_asked(self):
        dest = os.path.join(self.tmp, "SatpudaVoicePack.zip")
        urlopen, calls = fake_server(DATA)
        with mock.patch.object(vp.urllib.request, "urlopen", urlopen):
            vp._download_parallel("https://example.test/p.zip", dest, len(DATA))
        self.assertEqual(DATA, open(dest, "rb").read())
        self.assertEqual(4, len(calls))
        self.assertFalse(os.path.exists(dest + ".parts"))

    def test_a_dropped_piece_is_fetched_again_from_where_it_stopped(self):
        dest = os.path.join(self.tmp, "p.zip")
        second = len(DATA) // 4
        urlopen, calls = fake_server(DATA, fail_once_at=second)
        with mock.patch.object(vp.urllib.request, "urlopen", urlopen):
            vp._download_parallel("https://example.test/p.zip", dest, len(DATA))
        self.assertEqual(DATA, open(dest, "rb").read())
        self.assertEqual(5, len(calls))

    def test_a_server_without_ranges_gets_one_connection(self):
        dest = os.path.join(self.tmp, "p.zip")
        urlopen, _ = fake_server(DATA, ranges=False)
        with mock.patch.object(vp.urllib.request, "urlopen", urlopen), \
                mock.patch.object(vp, "_download") as single:
            vp._download_parallel("https://example.test/p.zip", dest, len(DATA))
        single.assert_called_once()

    def test_a_broken_download_resumes(self):
        dest = os.path.join(self.tmp, "p.zip")
        with open(dest, "wb") as fh:                 # piece 0 done, the rest not
            fh.write(DATA[: len(DATA) // 4] + b"\0" * (len(DATA) - len(DATA) // 4))
        with open(dest + ".parts", "w") as fh:
            json.dump([len(DATA) // 4, 0, 0, 0], fh)
        urlopen, calls = fake_server(DATA)
        with mock.patch.object(vp.urllib.request, "urlopen", urlopen):
            vp._download_parallel("https://example.test/p.zip", dest, len(DATA))
        self.assertEqual(DATA, open(dest, "rb").read())
        self.assertEqual(3, len(calls))              # piece 0 was not asked again


def tiny_pack(path: str) -> None:
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("SatpudaVoicePack/python/pythonw.exe", b"MZ")
        z.writestr("SatpudaVoicePack/voice/voice_service.py", b"# service")


class APackAlreadyHere(_Tmp):
    def test_found_in_downloads_by_its_size(self):
        dl = os.path.join(self.tmp, "Downloads")
        os.makedirs(dl)
        pack = os.path.join(dl, "SatpudaVoicePack (1).zip")
        tiny_pack(pack)
        with mock.patch.object(vp, "_local_dirs", return_value=[dl]):
            self.assertEqual(pack, vp.find_local_pack(os.path.getsize(pack)))
            self.assertEqual("", vp.find_local_pack(os.path.getsize(pack) + 1))   # another version

    def test_installed_without_downloading_and_the_copy_is_kept(self):
        home = os.path.join(self.tmp, "home")
        os.makedirs(home)
        pack = os.path.join(self.tmp, "SatpudaVoicePack.zip")
        tiny_pack(pack)
        manifest = {"version": "v9", "size": os.path.getsize(pack), "url": "u", "sha256": ""}
        with mock.patch.dict(os.environ, {"SATPUDA_VOICE_PACK_DIR": home}), \
                mock.patch.object(vp, "_get_json", return_value=manifest), \
                mock.patch.object(vp, "_local_dirs", return_value=[self.tmp]), \
                mock.patch.object(vp, "_download_parallel") as download, \
                mock.patch.object(vp, "_prepare_model") as prepare, \
                mock.patch.object(vp, "start_service"), \
                mock.patch.object(vp, "stop_service"):
            vp._install_worker()
        download.assert_not_called()
        prepare.assert_called_once()
        self.assertEqual("ready", vp._state["state"], vp._state.get("error"))
        self.assertTrue(os.path.isfile(os.path.join(home, "current", "voice", "voice_service.py")))
        self.assertTrue(os.path.isfile(pack))

    def test_offline_with_a_copy_still_installs(self):
        home = os.path.join(self.tmp, "home")
        os.makedirs(home)
        pack = os.path.join(self.tmp, "SatpudaVoicePack.zip")
        tiny_pack(pack)
        with mock.patch.dict(os.environ, {"SATPUDA_VOICE_PACK_DIR": home}), \
                mock.patch.object(vp, "_get_json", side_effect=OSError("no internet")), \
                mock.patch.object(vp, "_local_dirs", return_value=[self.tmp]), \
                mock.patch.object(vp, "_prepare_model"), \
                mock.patch.object(vp, "start_service"), \
                mock.patch.object(vp, "stop_service"):
            vp._install_worker()
        self.assertEqual("ready", vp._state["state"], vp._state.get("error"))


if __name__ == "__main__":
    unittest.main()
