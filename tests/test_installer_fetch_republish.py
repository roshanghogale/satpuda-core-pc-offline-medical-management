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
import ssl
import threading
import time
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


def ps_quote(s: str) -> str:
    """A PowerShell single-quoted literal - PSQuote in SatpudaCore.iss."""
    quotes = "'" + chr(0x2018) + chr(0x2019) + chr(0x201A) + chr(0x201B)
    out = []
    for ch in s:
        out.append(ch * 2 if ch in quotes else ch)
    return "'" + "".join(out) + "'"


# What WorkerParams in SatpudaCore.iss builds: no -File (a Group Policy
# execution policy of AllSigned or Restricted blocks an unsigned -File script
# whatever -ExecutionPolicy says), but a -Command that refuses Constrained
# Language Mode up front and then DOT-SOURCES the script text, so its top-level
# variables and $script: are one scope exactly as with -File.
BOOTSTRAP = ("$ErrorActionPreference='Stop'; "
             "if ($ExecutionContext.SessionState.LanguageMode -ne 'FullLanguage') {{ "
             "'E|CLM|PowerShell on this computer is locked to ' + $ExecutionContext.SessionState.LanguageMode + "
             "' (an AppLocker or Device Guard policy)'; exit 2 }}; "
             ". ([scriptblock]::Create([IO.File]::ReadAllText({script}))) {args}")


def worker_command(args, policy="Bypass", prelude="", powershell=None):
    """powershell.exe argv that runs the worker as the installer does."""
    parts = []
    for a in args:
        if a.startswith("-") and a[1:].isalpha():
            parts.append(a)
        else:
            parts.append(ps_quote(a))
    cmd = prelude + BOOTSTRAP.format(script=ps_quote(WORKER), args=" ".join(parts))
    return [powershell or POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", policy, "-Command", cmd]


# The 32-bit PowerShell on 64-bit Windows (a 32-bit host can start the worker).
POWERSHELL32 = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                            "SysWOW64", "WindowsPowerShell", "v1.0", "powershell.exe")


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
        self.clock_skew = 0         # seconds added to every Date header this server sends

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

        def date_time_string(self, timestamp=None):
            # A PC whose clock is off is the same as an internet whose clock is off the other way.
            return super().date_time_string(time.time() + gh.clock_skew)

        def do_HEAD(self):
            # -ClockUrl: only the Date header is read.
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()

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

    def run_worker(self, wait_secs=20, extra=(), api_base=None, app_root=None, launch="command",
                   policy="Bypass", prelude="", settle="1", timeout=180, powershell=None):
        app_root = app_root or os.path.join(self.tmp, "Satpuda Core")
        self.app_root = app_root
        args = ["-AppRoot", app_root,
                "-CacheDir", os.path.join(self.tmp, "cache"),
                "-CancelFile", os.path.join(self.tmp, "cancel.flag"),
                "-Repo", REPO, "-Tag", TAG, "-AssetName", ZIP,
                "-ApiBase", api_base or f"http://127.0.0.1:{self.port}",
                "-UpdatePollSecs", "1", "-UpdateWaitSecs", str(wait_secs),
                "-AvSettleSecs", settle,
                "-ClockUrl", f"http://127.0.0.1:{self.port}/clock",
                "-LogFile", os.path.join(self.tmp, "install.log"),
                "-Segments", "4", *extra]
        if launch == "file":
            argv = [POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", policy, "-File", WORKER, *args]
        else:
            argv = worker_command(args, policy=policy, prelude=prelude, powershell=powershell)
        # The pipe is ASCII by contract; decode it strictly so a stray byte fails the test.
        p = subprocess.run(argv, capture_output=True, timeout=timeout)
        out = p.stdout.decode("ascii") + p.stderr.decode("utf-8", "replace")
        marker = os.path.join(app_root, "app", ".satpuda-install.json")
        rec = None
        if os.path.isfile(marker):
            with open(marker, encoding="utf-8-sig") as fh:
                rec = json.load(fh)
        return p.returncode, out, rec

    def install_log(self):
        with open(os.path.join(self.tmp, "install.log"), encoding="utf-8") as fh:
            return fh.read()

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

    # ---- other PCs (Oct 2026): policy, paths, disk, antivirus, clock -------

    def test_compat_report_opens_the_install_log(self):
        """One file to send: the first block of install.log says what the PC is."""
        data, sha = self._steady(51)
        rc, out, rec = self.run_worker()
        self._assert_installed(rc, out, rec, sha)
        log = self.install_log()
        for key in ("compat: ===== compatibility check", "compat: windows: ", "compat: arch: OS 64-bit=",
                    "compat: powershell 5.", "language FullLanguage", "compat: .NET 4.", "compat: tls: in use=",
                    "compat: clock: local ", "compat: user profile ", "compat: free space: ",
                    "compat: WebView2 Runtime ", "compat: VC++ 2015-2022 x64 runtime ", "compat: proxy: ",
                    "compat: ===== end of compatibility check"):
            self.assertIn(key, log)
        self.assertLess(log.index("compat: ===== end"), log.index("Looking up the download"))
        self.assertIn("Tls12", log)

    def test_group_policy_allsigned_does_not_block_the_worker(self):
        """Group Policy AllSigned/Restricted wins over -ExecutionPolicy Bypass. An effective
        policy of AllSigned (set here for the process, the same refusal) stops -File cold,
        and the installer's -Command launch still installs."""
        data, sha = self._steady(52)
        rc, out, rec = self.run_worker(launch="file", policy="AllSigned")
        self.assertNotEqual(rc, 0)
        self.assertIn("is not digitally signed", out)
        self.assertIsNone(rec)
        rc, out, rec = self.run_worker(policy="AllSigned")
        self._assert_installed(rc, out, rec, sha)
        rc2, out2, rec2 = self.run_worker(policy="Restricted")
        self.assertEqual(rc2, 0, out2)

    def test_file_launch_still_works(self):
        data, sha = self._steady(53)
        rc, out, rec = self.run_worker(launch="file")
        self._assert_installed(rc, out, rec, sha)

    @unittest.skipUnless(os.path.isfile(POWERSHELL32), "no 32-bit PowerShell on this Windows")
    def test_32bit_powershell_host(self):
        """A 32-bit PowerShell on 64-bit Windows: installs, and the report reads the real
        64-bit registry (Registry64 view) rather than the WOW64 copy."""
        data, sha = self._steady(68)
        rc, out, rec = self.run_worker(powershell=POWERSHELL32)
        self._assert_installed(rc, out, rec, sha)
        self.assertIn("PowerShell 64-bit=False", out)
        self.assertIn("compat: windows: Windows", out)

    def test_both_scripts_parse(self):
        for script in (WORKER, os.path.join(ROOT, "installer", "satpuda_update.ps1")):
            p = subprocess.run([POWERSHELL, "-NoProfile", "-NonInteractive", "-Command",
                                "$e=$null; [void][System.Management.Automation.Language.Parser]::ParseFile("
                                + ps_quote(script) + ", [ref]$null, [ref]$e); if ($e) { $e | % { $_.Message }; exit 1 }"],
                               capture_output=True, text=True)
            self.assertEqual(p.returncode, 0, script + ": " + p.stdout + p.stderr)
            with open(script, "rb") as fh:
                self.assertTrue(all(b < 128 for b in fh.read()), script + " must stay pure ASCII")

    def test_constrained_language_mode_is_named(self):
        """AppLocker / WDAC put PowerShell in Constrained Language Mode, where the worker
        cannot run at all. Said up front, not as a heap of type errors."""
        self._steady(54)
        rc, out, rec = self.run_worker(prelude="$ExecutionContext.SessionState.LanguageMode='ConstrainedLanguage'; ")
        self.assertEqual(rc, 2, out)
        self.assertIn("E|CLM|PowerShell on this computer is locked to ConstrainedLanguage", out)
        self.assertEqual(self.gh.api_calls, 0)

    def test_user_name_with_space_apostrophe_and_marathi(self):
        """C:\\Users\\NEW USER, C:\\Users\\D'Souza and a Devanagari user name, for the install
        folder AND the zip chosen in the browser. Installs; the pipe stays pure ASCII."""
        data, sha = self._steady(55)
        home = os.path.join(self.tmp, "Users", "NEW USER D'Souza \u0930\u094b\u0936\u0928")
        app_root = os.path.join(home, "AppData", "Local", "Programs", "Satpuda Core")
        os.makedirs(os.path.join(home, "Downloads"))
        path = os.path.join(home, "Downloads", ZIP)
        with open(path, "wb") as fh:
            fh.write(data)
        rc, out, rec = self.run_worker(app_root=app_root, extra=("-LocalZip", path))
        self.assertEqual(rc, 0, out)
        self.assertNotIn("E|", out)
        self.assertEqual(rec["sha256"], sha)
        self.assertTrue(os.path.isfile(os.path.join(app_root, "app", "SatpudaCore_Desktop.exe")))
        self.assertIn("?", out)                                   # the Devanagari, made ASCII on the pipe
        self.assertIn("\u0930\u094b\u0936\u0928", self.install_log())  # ...and kept in the log

    def test_not_enough_space_stops_before_downloading(self):
        self._steady(56)
        rc, out, rec = self.run_worker(extra=("-AssumeFreeMB", "40"))
        self.assertEqual(rc, 2, out)
        self.assertIn("E|DISK|Not enough free space on drive", out)
        self.assertIn("to download Satpuda Core", out)
        self.assertEqual(self.gh.dl_requests, 0)
        self.assertIsNone(rec)

    def test_not_enough_space_for_the_chosen_zip(self):
        data, sha = self._steady(57)
        path = self._browser_zip(data)
        rc, out, rec = self.run_worker(extra=("-LocalZip", path, "-AssumeFreeMB", "10"))
        self.assertEqual(rc, 2, out)
        self.assertIn("E|DISK|", out)
        self.assertIsNone(rec)
        self.assertTrue(os.path.isfile(path))
        self._no_copy_left()

    def test_too_deep_install_folder_is_refused_before_unpacking(self):
        lpe = subprocess.run([POWERSHELL, "-NoProfile", "-Command",
                              "(Get-ItemProperty 'HKLM:\\SYSTEM\\CurrentControlSet\\Control\\FileSystem').LongPathsEnabled"],
                             capture_output=True, text=True).stdout.strip()
        if lpe == "1":
            self.skipTest("LongPathsEnabled is on here; nothing to refuse")
        data, sha = self._steady(58)
        deep = os.path.join(self.tmp, "d" * 120, "e" * 80, "Satpuda Core")
        rc, out, rec = self.run_worker(app_root=deep)
        self.assertEqual(rc, 2, out)
        self.assertIn("E|LONGPATH|", out)
        self.assertIsNone(rec)
        self.assertFalse(os.path.isdir(os.path.join(deep, "app.staging")))

    def test_unexpected_error_is_reported_not_swallowed(self):
        """Something nobody planned for (here: the cache folder is a FILE) used to end the
        worker with exit 1 and nothing on the pipe."""
        self._steady(59)
        with open(os.path.join(self.tmp, "cache"), "w") as fh:
            fh.write("not a folder")
        rc, out, rec = self.run_worker()
        self.assertEqual(rc, 2, out)
        self.assertIn("E|INTERNAL|", out)
        self.assertRegex(out, r"E\|INTERNAL\|.*\(line \d+\)")

    def test_mark_of_the_web_on_the_browser_zip_does_not_reach_the_program(self):
        data, sha = self._steady(60)
        path = self._browser_zip(data)
        with open(path + ":Zone.Identifier", "w") as fh:
            fh.write("[ZoneTransfer]\nZoneId=3\n")
        rc, out, rec = self.run_worker(extra=("-LocalZip", path))
        self.assertEqual(rc, 0, out)
        exe = os.path.join(self.tmp, "Satpuda Core", "app", "SatpudaCore_Desktop.exe")
        self.assertTrue(os.path.isfile(exe))
        self.assertFalse(os.path.exists(exe + ":Zone.Identifier"))

    def test_port_in_use_is_reported(self):
        data, sha = self._steady(61)
        holder = socket.socket()
        holder.bind(("127.0.0.1", 0))
        holder.listen(1)
        port = holder.getsockname()[1]
        try:
            rc, out, rec = self.run_worker(extra=("-EnginePort", str(port)))
        finally:
            holder.close()
        self._assert_installed(rc, out, rec, sha)
        self.assertRegex(out, r"PORT\|Port %d, which Satpuda Core uses inside this computer, is already in use by python" % port)

    # -- antivirus that deletes what was just unpacked (Net Protector) --

    def _antivirus(self, names, mode):
        """A thread that deletes files the way Net Protector did: each named file under
        app.staging (and app) as soon as it is there, closed and whole. mode 'once' deletes
        the first one only; 'always' keeps at it."""
        stop = threading.Event()
        hits = []

        def run():
            while not stop.is_set():
                for d in ("app.staging", "app"):
                    for n in names:
                        p = os.path.join(self.tmp, "Satpuda Core", d, n)
                        if d == "app" and mode != "always-app":
                            continue
                        try:
                            if os.path.isfile(p) and os.path.getsize(p) > 0:
                                os.remove(p)
                                hits.append(p)
                                if mode == "once":
                                    return
                        except OSError:
                            pass
                time.sleep(0.005)

        t = threading.Thread(target=run, daemon=True)
        t.start()
        self.addCleanup(stop.set)
        return stop, hits

    def test_antivirus_deleting_the_exe_once_is_put_right(self):
        data, sha = self._steady(62)
        stop, hits = self._antivirus(["SatpudaCore_Desktop.exe"], "once")
        rc, out, rec = self.run_worker(settle="2")
        stop.set()
        self.assertEqual(len(hits), 1)
        self._assert_installed(rc, out, rec, sha)
        self.assertIn("disappeared right after they were unpacked", out)
        self.assertIn("the files unpacked again stayed in place", out)
        self.assertTrue(os.path.isfile(os.path.join(self.tmp, "Satpuda Core", "app", "SatpudaCore_Desktop.exe")))

    def test_antivirus_that_keeps_deleting_is_named_and_the_old_install_stays(self):
        old, old_sha = self._steady(63)
        rc, out, rec = self.run_worker()
        self._assert_installed(rc, out, rec, old_sha)
        new = make_zip(64)
        new_sha = hashlib.sha256(new).hexdigest()
        self.gh.release = {"zip": new, "sha": new_sha, "id": 164,
                           "updated": iso(datetime.now(timezone.utc) - timedelta(days=1))}
        stop, hits = self._antivirus(["SatpudaCore_Desktop.exe"], "always")
        rc, out, rec = self.run_worker(settle="2")
        stop.set()
        self.assertGreaterEqual(len(hits), 2)
        self.assertEqual(rc, 2, out)
        self.assertIn("E|AVBLOCK|Antivirus", out)
        self.assertIn("Add this folder to its exclusions: ", out)
        self.assertIn("SatpudaCore_Desktop.exe", out)
        # The shop still has the program it had.
        self.assertEqual(rec["sha256"], old_sha)
        self.assertTrue(os.path.isfile(os.path.join(self.tmp, "Satpuda Core", "app", "SatpudaCore_Desktop.exe")))
        self.assertFalse(os.path.isdir(os.path.join(self.tmp, "Satpuda Core", "app.staging")))
        # The zip is kept for the next try (after the exclusion is set).
        self.assertTrue(os.path.isfile(os.path.join(self.tmp, "cache", ZIP)))

    def test_zip_without_the_program_is_still_layout_not_antivirus(self):
        self.gh.release = {"zip": None, "sha": None, "id": 65}
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:
            z.writestr("SatpudaCore_Desktop_Win10/other.exe", os.urandom(2 * 1024 * 1024))
        path = self._browser_zip(buf.getvalue())
        rc, out, rec = self.run_worker(extra=("-LocalZip", path), wait_secs=2)
        self.assertEqual(rc, 2, out)
        self.assertIn("E|LAYOUT|", out)
        self.assertNotIn("Installing files", out)                # judged before anything was written

    # -- a PC clock far off, and HTTPS that is not trusted --

    def _https_github(self):
        """The same fake GitHub behind a self-signed certificate: every HTTPS request fails
        its certificate check, exactly as on a PC whose clock is years off."""
        openssl = shutil.which("openssl") or r"C:\Program Files\Git\usr\bin\openssl.exe"
        if not os.path.isfile(openssl) and not shutil.which("openssl"):
            self.skipTest("openssl not available to make a test certificate")
        cert, key = os.path.join(self.tmp, "c.pem"), os.path.join(self.tmp, "k.pem")
        r = subprocess.run([openssl, "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", key,
                            "-out", cert, "-subj", "/CN=127.0.0.1", "-days", "2"], capture_output=True)
        if r.returncode != 0:
            self.skipTest("openssl could not make a certificate: " + r.stderr.decode(errors="replace"))
        srv = QuietServer(("127.0.0.1", 0), make_handler(self.gh))
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(cert, key)
        srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        return f"https://127.0.0.1:{srv.server_address[1]}"

    def test_clock_years_off_is_named(self):
        self._steady(66)
        base = self._https_github()
        self.gh.clock_skew = -2 * 365 * 86400        # the internet says 2 years earlier = the PC is 2 years ahead
        t0 = time.time()
        rc, out, rec = self.run_worker(api_base=base, wait_secs=2,
                                       extra=("-DirectUrl", f"{base}/dl/{20 + 66}/{ZIP}"))
        self.assertEqual(rc, 2, out)
        self.assertIn("E|CLOCK|The date and time on this computer are wrong", out)
        self.assertLess(time.time() - t0, 60)          # no minute of pointless retries
        self.assertIn("network failure kind: TrustFailure", out)

    def test_untrusted_https_with_a_right_clock_is_tls(self):
        self._steady(67)
        base = self._https_github()
        rc, out, rec = self.run_worker(api_base=base, wait_secs=2,
                                       extra=("-DirectUrl", f"{base}/dl/{20 + 67}/{ZIP}"))
        self.assertEqual(rc, 2, out)
        self.assertIn("E|TLS|", out)
        self.assertNotIn("E|CLOCK|", out)


if __name__ == "__main__":
    unittest.main()
