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
            if p.startswith("/sha/"):
                h = p[len("/sha/"):]
                return self._send(200, (h + "  " + ZIP).encode())
            if p.startswith("/dl/"):
                aid = int(p.split("/")[2])
                with gh.lock:
                    data = gh.serve_override(aid) if gh.serve_override else None
                    if data is None and not gh.serve_override:
                        current = gh.release.get("id") if gh.release.get("zip") is not None else None
                        data = gh.served.get(aid) if aid == current else None
                if data is None:
                    return self._send(404, b"Not Found")
                rng = self.headers.get("Range")
                if rng and rng.startswith("bytes="):
                    a, _, b = rng[6:].partition("-")
                    a = int(a)
                    b = int(b) if b else len(data) - 1
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

    def run_worker(self, wait_secs=20, extra=()):
        app_root = os.path.join(self.tmp, "Satpuda Core")
        args = [POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                "-File", WORKER,
                "-AppRoot", app_root,
                "-CacheDir", os.path.join(self.tmp, "cache"),
                "-CancelFile", os.path.join(self.tmp, "cancel.flag"),
                "-Repo", REPO, "-Tag", TAG, "-AssetName", ZIP,
                "-ApiBase", f"http://127.0.0.1:{self.port}",
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
        self.assertIn("GitHub is updating this release", out)
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
