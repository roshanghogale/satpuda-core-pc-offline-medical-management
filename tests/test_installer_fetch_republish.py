"""installer/satpuda_fetch.ps1 against a fake GitHub that is half way through
re-publishing a build into the same tag (Oct 2026).

`gh release upload --clobber` deletes the old asset about a minute before the
new one has finished uploading. The worker must wait for it (and for its
.sha256) instead of failing, and must start again once if the file is replaced
while it is downloading.

A tiny threaded HTTP server on a free 127.0.0.1 port plays both the releases
API (-ApiBase) and the download host. Windows PowerShell 5.1 runs the real
worker into a temp folder; nothing outside that folder is touched.
"""
import hashlib
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
import zipfile
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORKER = os.path.join(ROOT, "installer", "satpuda_fetch.ps1")
POWERSHELL = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                          "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
REPO = "owner/fake"
TAG = "v9.9.9"
ZIP = "SatpudaCore_Desktop_Win10.zip"


def make_zip(seed: int) -> bytes:
    """A payload shaped like the real one (one top folder, the sentinel exe),
    5 MB so the 16-way segmented path runs. Same size for every seed."""
    rnd = hashlib.sha256(str(seed).encode()).digest()
    blob = (rnd * (5 * 1024 * 1024 // len(rnd) + 1))[: 5 * 1024 * 1024]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:
        for name, data in (("SatpudaCore_Desktop_Win10/SatpudaCore_Desktop.exe", b"MZ fake " + rnd),
                           ("SatpudaCore_Desktop_Win10/blob.bin", blob)):
            info = zipfile.ZipInfo(name, date_time=(2026, 10, 23, 12, 0, 0))
            z.writestr(info, data)
    return buf.getvalue()


def iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


class FakeGitHub:
    """State the handler reads. `script(n)` decides what the release looks like
    on the n-th API call: a dict {zip: bytes|None, sha: str|None, id, updated}."""

    def __init__(self):
        self.api_calls = 0
        self.lock = threading.Lock()
        self.release = {}
        self.on_api = None          # callable(n) -> None, may change self.release
        self.served = {}            # asset id -> bytes
        self.serve_override = None  # callable(asset_id) -> bytes|None (None = 404)
        # What an HTTPS-scanning antivirus / proxy does to Range requests:
        # None | '416' (every Range refused) | 'nocr' (206 without
        # Content-Range) | 'badtotal' (206 with a wrong total after the "/")
        self.range_mode = None
        self.fail_once = set()      # range starts that answer 404 the first time
        self.plain_gets = 0         # GETs of the zip without a Range header
        self.dl_requests = 0        # every request for the zip itself
        self.history = None         # sha256 list of every build of the tag (<zip>.sha256s), None = 404

    def release_json(self, base):
        r = self.release
        assets = []
        if r.get("zip") is not None:
            aid = r["id"]
            self.served[aid] = r["zip"]
            assets.append({"name": ZIP, "id": aid, "size": len(r["zip"]), "state": "uploaded",
                           "updated_at": r.get("updated", iso(datetime.now(timezone.utc) - timedelta(days=2))),
                           "browser_download_url": f"{base}/dl/{aid}/{ZIP}"})
        if r.get("sha") is not None:
            assets.append({"name": ZIP + ".sha256", "id": 999, "size": 95, "state": "uploaded",
                           "updated_at": iso(datetime.now(timezone.utc)),
                           "browser_download_url": f"{base}/sha/{r['sha']}"})
        return {"tag_name": TAG, "draft": False, "prerelease": False, "assets": assets}


def make_handler(gh: FakeGitHub):
    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def _send(self, code, body=b"", headers=None):
            self.send_response(code)
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if body:
                self.wfile.write(body)

        def do_GET(self):
            base = f"http://127.0.0.1:{self.server.server_address[1]}"
            p = self.path
            if p == f"/repos/{REPO}/releases/tags/{TAG}":
                with gh.lock:
                    gh.api_calls += 1
                    if gh.on_api:
                        gh.on_api(gh.api_calls)
                    body = json.dumps(gh.release_json(base)).encode()
                return self._send(200, body, {"Content-Type": "application/json"})
            if p == f"/download/{TAG}/{ZIP}.sha256s":
                if gh.history is None:
                    return self._send(404, b"Not Found")
                return self._send(200, chr(10).join(gh.history).encode())
            if p.startswith("/sha/"):
                h = p[len("/sha/"):]
                return self._send(200, (h + "  " + ZIP).encode())
            if p.startswith("/dl/"):
                aid = int(p.split("/")[2])
                with gh.lock:
                    gh.dl_requests += 1
                    data = gh.serve_override(aid) if gh.serve_override else None
                    if data is None and not gh.serve_override:
                        current = gh.release.get("id") if gh.release.get("zip") is not None else None
                        data = gh.served.get(aid) if aid == current else None
                if data is None:
                    return self._send(404, b"Not Found")
                rng = self.headers.get("Range")
                if not rng:
                    with gh.lock:
                        gh.plain_gets += 1
                if rng and rng.startswith("bytes="):
                    a, _, b = rng[6:].partition("-")
                    a = int(a)
                    b = int(b) if b else len(data) - 1
                    with gh.lock:
                        if a in gh.fail_once and a != 0:
                            gh.fail_once.discard(a)
                            return self._send(404, b"Not Found")
                    if gh.range_mode == "416":
                        return self._send(416, b"", {"Content-Range": f"bytes */{len(data)}"})
                    if gh.range_mode == "nocr":
                        b = min(b, len(data) - 1)
                        return self._send(206, data[a:b + 1])
                    if gh.range_mode == "badtotal":
                        b = min(b, len(data) - 1)
                        return self._send(206, data[a:b + 1],
                                          {"Content-Range": f"bytes {a}-{b}/{len(data) + 12345}"})
                    if a >= len(data):
                        return self._send(416, b"", {"Content-Range": f"bytes */{len(data)}"})
                    b = min(b, len(data) - 1)
                    return self._send(206, data[a:b + 1],
                                      {"Content-Range": f"bytes {a}-{b}/{len(data)}"})
                return self._send(200, data)
            return self._send(404, b"Not Found")

    return H


class QuietServer(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        pass   # a keep-alive connection the worker dropped is not a failure


@unittest.skipUnless(sys.platform == "win32" and os.path.isfile(POWERSHELL), "needs Windows PowerShell")
class FetchWorkerRepublish(unittest.TestCase):
    def setUp(self):
        self.gh = FakeGitHub()
        self.srv = QuietServer(("127.0.0.1", 0), make_handler(self.gh))
        self.port = self.srv.server_address[1]
        assert self.port != 8765
        self.thread = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self.thread.start()
        self.tmp = tempfile.mkdtemp(prefix="satpuda_fetch_test_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)

    def run_worker(self, wait_secs=20, extra=(), api_base=None):
        app_root = os.path.join(self.tmp, "Satpuda Core")
        args = [POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                "-File", WORKER,
                "-AppRoot", app_root,
                "-CacheDir", os.path.join(self.tmp, "cache"),
                "-CancelFile", os.path.join(self.tmp, "cancel.flag"),
                "-Repo", REPO, "-Tag", TAG, "-AssetName", ZIP,
                "-ApiBase", api_base or f"http://127.0.0.1:{self.port}",
                "-UpdatePollSecs", "1", "-UpdateWaitSecs", str(wait_secs),
                "-Segments", "4", *extra]
        p = subprocess.run(args, capture_output=True, text=True, timeout=180)
        marker = os.path.join(app_root, "app", ".satpuda-install.json")
        rec = None
        if os.path.isfile(marker):
            with open(marker, encoding="utf-8-sig") as fh:
                rec = json.load(fh)
        return p.returncode, p.stdout, rec

    def test_zip_appears_after_two_polls(self):
        new = make_zip(2)
        sha = hashlib.sha256(new).hexdigest()

        def on_api(n):
            # Polls 1 and 2: the old zip deleted, the new one still uploading.
            if n >= 3:
                self.gh.release = {"zip": new, "sha": sha, "id": 2,
                                   "updated": iso(datetime.now(timezone.utc))}
            else:
                self.gh.release = {"zip": None, "sha": None, "id": 1}
        self.gh.on_api = on_api

        rc, out, rec = self.run_worker()
        self.assertEqual(rc, 0, out)
        self.assertIn("GitHub is updating this release - trying again in", out)
        self.assertIn("OK|", out)
        self.assertGreaterEqual(self.gh.api_calls, 3)
        self.assertEqual(rec["tag"], TAG)
        self.assertEqual(rec["sha256"], sha)
        self.assertTrue(rec["sha256Verified"])
        self.assertEqual(rec["bytes"], len(new))

    def test_fresh_zip_waits_for_its_sidecar(self):
        new = make_zip(3)
        sha = hashlib.sha256(new).hexdigest()

        def on_api(n):
            self.gh.release = {"zip": new, "sha": sha if n >= 3 else None, "id": 3,
                               "updated": iso(datetime.now(timezone.utc))}
        self.gh.on_api = on_api

        rc, out, rec = self.run_worker()
        self.assertEqual(rc, 0, out)
        self.assertIn("its .sha256 is not there yet", out)
        self.assertGreaterEqual(self.gh.api_calls, 3)
        self.assertEqual(rec["sha256"], sha)
        self.assertTrue(rec["sha256Verified"])

    def test_old_release_without_sidecar_is_not_waited_for(self):
        old = make_zip(4)
        self.gh.release = {"zip": old, "sha": None, "id": 4,
                           "updated": iso(datetime.now(timezone.utc) - timedelta(days=3))}
        rc, out, rec = self.run_worker()
        self.assertEqual(rc, 0, out)
        self.assertNotIn("GitHub is updating this release", out)
        # Not published, but still recorded: the hash of what was installed.
        self.assertEqual(rec["sha256"], hashlib.sha256(old).hexdigest())
        self.assertFalse(rec["sha256Verified"])

    def test_gives_up_after_the_wait_with_the_old_message(self):
        self.gh.release = {"zip": None, "sha": None, "id": 5}
        rc, out, rec = self.run_worker(wait_secs=3)
        self.assertEqual(rc, 2, out)
        self.assertIn("E|NOASSET|", out)
        self.assertGreaterEqual(self.gh.api_calls, 3)
        self.assertIsNone(rec)

    def test_replaced_mid_download_starts_again_once(self):
        """The release says build B (id 7, its checksum) but the link still
        hands out build A's bytes for the first download - the asset was being
        swapped. The checksum fails, the release now shows another asset id,
        so the worker starts again and installs B."""
        a, b = make_zip(6), make_zip(7)
        self.assertEqual(len(a), len(b))
        sha_b = hashlib.sha256(b).hexdigest()
        self.gh.release = {"zip": b, "sha": sha_b, "id": 7,
                           "updated": iso(datetime.now(timezone.utc) - timedelta(days=1))}
        def on_api(n):
            # After the first round the asset id moves on (the re-upload landed).
            if n >= 2:
                self.gh.release = {"zip": b, "sha": sha_b, "id": 8,
                                   "updated": iso(datetime.now(timezone.utc))}
        self.gh.on_api = on_api

        def serve(aid):
            return a if aid == 7 else (b if aid == 8 else None)
        self.gh.serve_override = serve

        rc, out, rec = self.run_worker()
        self.assertEqual(rc, 0, out)
        self.assertIn("the zip was replaced", out)
        self.assertEqual(rec["sha256"], sha_b)
        self.assertEqual(rec["assetId"], "8")

    def test_deleted_mid_download_waits_for_the_new_one(self):
        """The direct asset 404s (deleted for the re-upload) right after the
        release listed it; the worker looks again, waits, and installs the new
        file."""
        new = make_zip(9)
        sha = hashlib.sha256(new).hexdigest()

        def on_api(n):
            if n == 1:
                self.gh.release = {"zip": new, "sha": sha, "id": 10,
                                   "updated": iso(datetime.now(timezone.utc) - timedelta(days=1))}
            elif n == 2:
                self.gh.release = {"zip": None, "sha": None, "id": 10}
            else:
                self.gh.release = {"zip": new, "sha": sha, "id": 11,
                                   "updated": iso(datetime.now(timezone.utc))}
        self.gh.on_api = on_api

        def serve(aid):
            return new if aid == 11 else None   # id 10 is already gone
        self.gh.serve_override = serve

        rc, out, rec = self.run_worker()
        self.assertEqual(rc, 0, out)
        self.assertIn("the release now shows another file", out)
        self.assertIn("starting again", out)
        self.assertEqual(rec["assetId"], "11")
        self.assertEqual(rec["sha256"], sha)

    def test_chunks_404_mid_download_restart(self):
        """The range probe still got the old file, then every chunk 404s (the
        asset was deleted under the 16 connections). The segmented download
        stops at once - no single-stream fallback, no NET failure - and the
        worker starts again on the new file."""
        new = make_zip(13)
        sha = hashlib.sha256(new).hexdigest()
        calls = {"12": 0}

        def on_api(n):
            if n == 1:
                self.gh.release = {"zip": new, "sha": sha, "id": 12,
                                   "updated": iso(datetime.now(timezone.utc) - timedelta(days=1))}
            else:
                self.gh.release = {"zip": new, "sha": sha, "id": 14,
                                   "updated": iso(datetime.now(timezone.utc))}
        self.gh.on_api = on_api

        def serve(aid):
            if aid == 12:
                calls["12"] += 1
                return new if calls["12"] == 1 else None   # only the probe gets through
            return new if aid == 14 else None
        self.gh.serve_override = serve

        rc, out, rec = self.run_worker()
        self.assertEqual(rc, 0, out)
        self.assertIn("deleted (404) mid-download", out)
        self.assertNotIn("falling back to a single connection", out)
        self.assertEqual(rec["assetId"], "14")
        self.assertEqual(rec["sha256"], sha)

    # ---- the release did NOT change: nothing may be called CHANGED --------

    def _steady(self, seed):
        data = make_zip(seed)
        sha = hashlib.sha256(data).hexdigest()
        self.gh.release = {"zip": data, "sha": sha, "id": 20 + seed,
                           "updated": iso(datetime.now(timezone.utc) - timedelta(days=1))}
        return data, sha

    def _assert_installed(self, rc, out, rec, sha):
        self.assertEqual(rc, 0, out)
        self.assertNotIn("E|", out)
        self.assertNotIn("starting again", out)
        self.assertEqual(rec["sha256"], sha)
        self.assertTrue(rec["sha256Verified"])

    def test_stale_cache_from_other_builds_is_never_resumed(self):
        """Chunks, .part and a finished zip left by earlier builds - one of
        ANOTHER size, and complete-looking junk chunks of the SAME size named
        the way the previous installer named them (no asset id) - must all be
        thrown away, not resumed."""
        data, sha = self._steady(1)
        cache = os.path.join(self.tmp, "cache")
        os.makedirs(cache)
        size = len(data)
        chunk = 2097152
        junk = b"\xee" * chunk
        for i in range(3):
            n = min(chunk, size - i * chunk)
            with open(os.path.join(cache, f"{ZIP}.{size}.c{chunk}.seg{i}"), "wb") as fh:
                fh.write(junk[:n])                       # same size, old naming
            with open(os.path.join(cache, f"{ZIP}.132766295.c{chunk}.seg{i}"), "wb") as fh:
                fh.write(junk[:1000])                    # another build's size
        with open(os.path.join(cache, ZIP + ".part"), "wb") as fh:
            fh.write(junk[:5000])
        with open(os.path.join(cache, ZIP), "wb") as fh:
            fh.write(b"\xee" * size)                    # a "finished" zip of the right size
        rc, out, rec = self.run_worker()
        self._assert_installed(rc, out, rec, sha)
        self.assertIn("clearing it", out)
        left = [f for f in os.listdir(cache) if ".seg" in f]
        self.assertEqual(left, [])

    def test_every_range_answered_416_falls_back_to_one_plain_get(self):
        data, sha = self._steady(2)
        self.gh.range_mode = "416"
        rc, out, rec = self.run_worker()
        self._assert_installed(rc, out, rec, sha)
        self.assertIn("release has NOT changed", out)
        self.assertGreaterEqual(self.gh.plain_gets, 1)

    def test_206_without_content_range(self):
        data, sha = self._steady(3)
        self.gh.range_mode = "nocr"
        rc, out, rec = self.run_worker()
        self._assert_installed(rc, out, rec, sha)

    def test_206_with_a_wrong_total(self):
        data, sha = self._steady(4)
        self.gh.range_mode = "badtotal"
        rc, out, rec = self.run_worker()
        self._assert_installed(rc, out, rec, sha)
        self.assertIn("release has NOT changed", out)

    def test_one_chunk_404_with_the_release_unchanged(self):
        data, sha = self._steady(5)
        self.gh.fail_once = {2097152}
        rc, out, rec = self.run_worker()
        self._assert_installed(rc, out, rec, sha)
        self.assertIn("release has NOT changed", out)

    # ---- a zip downloaded in the browser (-LocalZip) ----------------------
    # A shop PC whose antivirus breaks the worker's downloads while the
    # browser works (Net Protector, 25 Oct 2026). The shopkeeper downloads the
    # zip in the browser and the installer hands it to the worker.

    def _browser_zip(self, data, name=ZIP):
        downloads = os.path.join(self.tmp, "Downloads")
        os.makedirs(downloads, exist_ok=True)
        path = os.path.join(downloads, name)
        with open(path, "wb") as fh:
            fh.write(data)
        return path

    def _no_copy_left(self):
        cache = os.path.join(self.tmp, "cache")
        left = [f for f in os.listdir(cache) if f.endswith(".chosen")] if os.path.isdir(cache) else []
        self.assertEqual(left, [])

    def test_local_zip_matching_the_published_checksum_is_installed(self):
        data, sha = self._steady(31)
        path = self._browser_zip(data, ZIP[:-4] + " (1).zip")
        rc, out, rec = self.run_worker(extra=("-LocalZip", path))
        self.assertEqual(rc, 0, out)
        self.assertNotIn("E|", out)
        self.assertIn("chosen zip matches the published checksum", out)
        self.assertNotIn("UNVERIFIED|", out)
        self.assertIn("V|" + TAG, out)
        # Nothing was downloaded: the zip on the release was never asked for.
        self.assertEqual(self.gh.dl_requests, 0)
        # Recorded exactly like a download, with where it came from.
        self.assertEqual(rec["tag"], TAG)
        self.assertEqual(rec["sha256"], sha)
        self.assertTrue(rec["sha256Verified"])
        self.assertEqual(rec["bytes"], len(data))
        self.assertEqual(rec["assetId"], str(20 + 31))
        self.assertEqual(rec["source"], "local zip")
        exe = os.path.join(self.tmp, "Satpuda Core", "app", "SatpudaCore_Desktop.exe")
        self.assertTrue(os.path.isfile(exe))
        # The shopkeeper's own file is left alone; our copy is gone.
        self.assertTrue(os.path.isfile(path))
        self._no_copy_left()

    def test_local_zip_with_the_wrong_checksum_is_refused(self):
        self._steady(32)
        other = make_zip(33)                      # same size, another build
        path = self._browser_zip(other)
        rc, out, rec = self.run_worker(extra=("-LocalZip", path))
        self.assertEqual(rc, 2, out)
        self.assertIn("E|LOCALWRONG|", out)
        self.assertNotIn("OK|", out)
        self.assertIsNone(rec)
        self.assertFalse(os.path.isdir(os.path.join(self.tmp, "Satpuda Core", "app")))
        self.assertFalse(os.path.isdir(os.path.join(self.tmp, "Satpuda Core", "app.staging")))
        self.assertTrue(os.path.isfile(path))
        self._no_copy_left()

    def test_local_zip_of_an_earlier_build_of_the_tag_is_installed_and_said(self):
        """The same version was re-published since the shop downloaded the zip in the browser
        (v1.0.11, 25-26 Oct 2026): the zip is genuine, only older - installed, and said so."""
        self._steady(34)
        older = make_zip(35)
        self.gh.history = [hashlib.sha256(older).hexdigest(), hashlib.sha256(make_zip(36)).hexdigest()]
        path = self._browser_zip(older)
        rc, out, rec = self.run_worker(extra=("-LocalZip", path))
        self.assertEqual(rc, 0, out)
        self.assertNotIn("E|", out)
        self.assertIn("OLDERBUILD|", out)
        self.assertEqual(rec["sha256"], hashlib.sha256(older).hexdigest())
        self.assertTrue(rec["sha256Verified"])
        self.assertTrue(os.path.isfile(os.path.join(self.tmp, "Satpuda Core", "app", "SatpudaCore_Desktop.exe")))
        self._no_copy_left()

    def test_local_zip_not_in_the_list_of_builds_is_still_refused(self):
        self._steady(37)
        self.gh.history = [hashlib.sha256(make_zip(38)).hexdigest()]
        path = self._browser_zip(make_zip(39))
        rc, out, rec = self.run_worker(extra=("-LocalZip", path))
        self.assertEqual(rc, 2, out)
        self.assertIn("E|LOCALWRONG|", out)
        self.assertIsNone(rec)

    def test_local_zip_without_a_sidecar_is_installed_with_a_warning(self):
        """The release lists no .sha256: checked by its contents only, said
        loudly (W| and UNVERIFIED|), and recorded as not verified."""
        data = make_zip(34)
        self.gh.release = {"zip": data, "sha": None, "id": 34,
                           "updated": iso(datetime.now(timezone.utc) - timedelta(days=3))}
        path = self._browser_zip(data)
        rc, out, rec = self.run_worker(extra=("-LocalZip", path))
        self.assertEqual(rc, 0, out)
        self.assertIn("UNVERIFIED|", out)
        self.assertIn("W|The published checksum could not be read", out)
        self.assertEqual(self.gh.dl_requests, 0)
        self.assertEqual(rec["sha256"], hashlib.sha256(data).hexdigest())
        self.assertFalse(rec["sha256Verified"])
        self.assertEqual(rec["source"], "local zip")
        self._no_copy_left()

    def test_local_zip_with_github_unreachable_is_installed_with_a_warning(self):
        data = make_zip(35)
        path = self._browser_zip(data)
        dead = socket.socket()
        dead.bind(("127.0.0.1", 0))
        dead_port = dead.getsockname()[1]
        dead.close()                              # nothing listens there now
        rc, out, rec = self.run_worker(extra=("-LocalZip", path), api_base=f"http://127.0.0.1:{dead_port}")
        self.assertEqual(rc, 0, out)
        self.assertIn("UNVERIFIED|", out)
        self.assertEqual(rec["sha256"], hashlib.sha256(data).hexdigest())
        self.assertFalse(rec["sha256Verified"])
        self.assertEqual(rec["assetId"], "")

    def test_local_zip_of_another_size_is_refused_without_a_sidecar(self):
        self.gh.release = {"zip": make_zip(36), "sha": None, "id": 36,
                           "updated": iso(datetime.now(timezone.utc) - timedelta(days=3))}
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:
            z.writestr("SatpudaCore_Desktop_Win10/SatpudaCore_Desktop.exe", b"MZ" + os.urandom(2 * 1024 * 1024))
        path = self._browser_zip(buf.getvalue())
        rc, out, rec = self.run_worker(extra=("-LocalZip", path))
        self.assertEqual(rc, 2, out)
        self.assertIn("E|LOCALWRONG|", out)
        self.assertIsNone(rec)

    def test_local_zip_without_the_program_is_refused(self):
        """No checksum to compare with, and the zip does not hold the sentinel
        exe: refused before anything is swapped in."""
        self.gh.release = {"zip": None, "sha": None, "id": 37}
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:
            z.writestr("photos/holiday.jpg", os.urandom(2 * 1024 * 1024))
        path = self._browser_zip(buf.getvalue())
        rc, out, rec = self.run_worker(extra=("-LocalZip", path), wait_secs=2)
        self.assertEqual(rc, 2, out)
        self.assertIn("E|LAYOUT|", out)
        self.assertIsNone(rec)
        self.assertFalse(os.path.isdir(os.path.join(self.tmp, "Satpuda Core", "app.staging")))
        self._no_copy_left()

    def test_local_zip_that_is_not_there(self):
        rc, out, rec = self.run_worker(extra=("-LocalZip", os.path.join(self.tmp, "nope.zip")))
        self.assertEqual(rc, 2, out)
        self.assertIn("E|LOCALZIP|", out)

    # ---- connecting quickly ---------------------------------------------

    def test_ipv6_is_passed_over_when_ipv4_answers(self):
        """'localhost' is ::1 first, then 127.0.0.1; the fake GitHub listens
        on 127.0.0.1 only. The fast-connect helper skips ::1 (prefer IPv4)
        and the install goes through unchanged."""
        data, sha = self._steady(38)
        rc, out, rec = self.run_worker(api_base=f"http://localhost:{self.port}")
        self._assert_installed(rc, out, rec, sha)
        self.assertIn("fast connect:", out)
        has_v6 = any(ai[0] == socket.AF_INET6 for ai in socket.getaddrinfo("localhost", self.port))
        if has_v6:
            self.assertIn(f"skipping [::1]:{self.port} for localhost", out)

    def test_check_only_reports_the_latest_sha(self):
        """-CheckOnly's SHA| line, read by the maintenance page."""
        sha = "c" * 64
        gh = self.gh

        class Latest(make_handler(gh)):
            def do_GET(self):
                base = f"http://127.0.0.1:{self.server.server_address[1]}"
                if self.path == f"/repos/{REPO}/releases/latest":
                    body = json.dumps({"tag_name": "v9.9.9", "assets": [
                        {"name": ZIP + ".sha256", "browser_download_url": f"{base}/sha/{sha}"}]}).encode()
                    return self._send(200, body)
                if self.path.startswith(f"/repos/{REPO}/releases?"):
                    return self._send(200, b"[]")
                return super().do_GET()

        srv = QuietServer(("127.0.0.1", 0), Latest)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            out_file = os.path.join(self.tmp, "latest.txt")
            p = subprocess.run([POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                                "-File", WORKER, "-CheckOnly", "-AppRoot", self.tmp, "-CacheDir", self.tmp,
                                "-CancelFile", os.path.join(self.tmp, "never.flag"), "-Repo", REPO,
                                "-ApiBase", f"http://127.0.0.1:{srv.server_address[1]}",
                                "-OutFile", out_file],
                               capture_output=True, text=True, timeout=60)
        finally:
            srv.shutdown()
            srv.server_close()
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        with open(out_file, encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("LATEST|v9.9.9", text)
        self.assertIn("SHA|" + sha, text)


if __name__ == "__main__":
    unittest.main()
