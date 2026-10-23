"""The in-app updater and builds published INTO THE SAME TAG (Oct 2026).

The owner republishes new builds into one release tag (v1.0.11 had its files
replaced several times). "APP_VERSION == latest tag" is then not "up to date":
the release's <zip>.sha256 is compared with the sha256 the installer recorded
in <install>\\app\\.satpuda-install.json.
"""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

from core import github_updater as gu

SHA_A = "a" * 64
SHA_B = "b" * 64
ZIP = "SatpudaCore_Desktop_Win10.zip"


def _release(tag, *, prerelease=False, sha_asset=True, zip_asset=True):
    assets = []
    if zip_asset:
        assets.append({"name": ZIP, "browser_download_url": "https://example.invalid/" + ZIP,
                       "state": "uploaded", "size": 123})
    if sha_asset:
        assets.append({"name": ZIP + ".sha256",
                       "browser_download_url": "https://example.invalid/" + ZIP + ".sha256",
                       "state": "uploaded", "size": 95})
    assets.append({"name": "SatpudaCoreInstaller.exe",
                   "browser_download_url": "https://example.invalid/SatpudaCoreInstaller.exe",
                   "state": "uploaded", "size": 2000000})
    return {"tag_name": tag, "prerelease": prerelease, "draft": False, "assets": assets,
            "name": tag, "body": "", "published_at": "2026-10-23T09:00:00Z",
            "html_url": "https://example.invalid/releases"}


class Decision(unittest.TestCase):
    """newer_build_available: the pure rule."""

    def test_same_sha_is_up_to_date(self):
        self.assertFalse(gu.newer_build_available("v1.0.11", SHA_A, {"tag": "v1.0.11", "sha256": SHA_A}))
        self.assertFalse(gu.newer_build_available("v1.0.11", SHA_A.upper(), {"tag": "v1.0.11", "sha256": SHA_A}))

    def test_different_sha_is_a_newer_build(self):
        self.assertTrue(gu.newer_build_available("v1.0.11", SHA_B, {"tag": "v1.0.11", "sha256": SHA_A}))

    def test_unknown_installed_build_offers_the_update(self):
        self.assertTrue(gu.newer_build_available("v1.0.11", SHA_B, {"tag": "v1.0.11", "sha256": ""}))
        self.assertTrue(gu.newer_build_available("v1.0.11", SHA_B, {"tag": "v1.0.11"}))
        self.assertTrue(gu.newer_build_available("v1.0.11", SHA_B, None))
        self.assertTrue(gu.newer_build_available("v1.0.11", SHA_B, {"tag": "v1.0.10", "sha256": SHA_B}))

    def test_nothing_published_offers_nothing(self):
        self.assertFalse(gu.newer_build_available("v1.0.11", "", {"tag": "v1.0.11", "sha256": SHA_A}))
        self.assertFalse(gu.newer_build_available("v1.0.11", "", None))
        self.assertFalse(gu.newer_build_available("v1.0.11", "not-a-hash", None))


class CheckForUpdate(unittest.TestCase):
    """check_for_update end to end, GitHub and the marker faked."""

    def setUp(self):
        patches = [
            mock.patch.object(gu, "mark_checked_today", lambda: None),
            mock.patch.object(gu, "get_skipped_version", lambda: ""),
            mock.patch("core.install_updater.resolve_installer_exe", lambda *a, **k: ""),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def _check(self, release, *, record=None, remote_sha=SHA_A, current="1.0.11", frozen=True):
        sha_calls = []

        def fake_sha(url, timeout=15):
            sha_calls.append(url)
            return remote_sha if url else ""

        with mock.patch.object(gu, "_fetch_latest_release", lambda: release), \
             mock.patch.object(gu, "installed_build_record", lambda: record), \
             mock.patch.object(gu, "_fetch_sha256", fake_sha), \
             mock.patch.object(sys, "frozen", frozen, create=True):
            info = gu.check_for_update(current)
        return info, sha_calls

    def test_same_tag_same_sha_none(self):
        info, _ = self._check(_release("v1.0.11"), record={"tag": "v1.0.11", "sha256": SHA_A})
        self.assertFalse(info.available)
        self.assertFalse(info.same_version_build)
        self.assertEqual(info.error, "")

    def test_same_tag_different_sha_update(self):
        info, calls = self._check(_release("v1.0.11"), record={"tag": "v1.0.11", "sha256": SHA_B})
        self.assertTrue(info.available)
        self.assertTrue(info.same_version_build)
        self.assertEqual(info.latest_sha256, SHA_A)
        self.assertEqual(calls, ["https://example.invalid/" + ZIP + ".sha256"])
        self.assertIn("newer build", gu.format_release_summary(info))

    def test_same_tag_unknown_installed_sha_update(self):
        info, _ = self._check(_release("v1.0.11"), record={"tag": "v1.0.11", "sha256": ""})
        self.assertTrue(info.available)
        self.assertTrue(info.same_version_build)
        info, _ = self._check(_release("v1.0.11"), record=None, frozen=True)
        self.assertTrue(info.available)

    def test_same_tag_no_sidecar_on_release_none(self):
        """No sidecar, or the zip missing (a build half way through being
        published): nothing to compare with, so nothing is offered."""
        info, calls = self._check(_release("v1.0.11", sha_asset=False),
                                  record={"tag": "v1.0.11", "sha256": SHA_B})
        self.assertFalse(info.available)
        info, calls = self._check(_release("v1.0.11", zip_asset=False),
                                  record={"tag": "v1.0.11", "sha256": SHA_B})
        self.assertFalse(info.available)

    def test_running_from_source_without_marker_never_nags(self):
        info, calls = self._check(_release("v1.0.11"), record=None, frozen=False)
        self.assertFalse(info.available)
        self.assertEqual(calls, [])

    def test_newer_tag_update(self):
        info, calls = self._check(_release("v1.0.12"), record={"tag": "v1.0.11", "sha256": SHA_A})
        self.assertTrue(info.available)
        self.assertFalse(info.same_version_build)
        self.assertEqual(calls, [])   # a newer version does not need the checksum

    def test_older_tag_none(self):
        info, _ = self._check(_release("v1.0.10"), record={"tag": "v1.0.11", "sha256": SHA_B})
        self.assertFalse(info.available)

    def test_prerelease_ignored(self):
        info, _ = self._check(_release("v1.1.0-beta.11", prerelease=True),
                              record={"tag": "v1.0.11", "sha256": SHA_A})
        self.assertFalse(info.available)
        # ... even one whose number equals ours, and whatever its checksum
        info, _ = self._check(_release("v1.0.11", prerelease=True),
                              record={"tag": "v1.0.11", "sha256": SHA_B})
        self.assertFalse(info.available)

    def test_after_the_update_it_stops(self):
        """The startup prompt must not loop: once the installer has written the
        new build into the marker, the same release offers nothing."""
        before, _ = self._check(_release("v1.0.11"), record={"tag": "v1.0.11", "sha256": SHA_B})
        after, _ = self._check(_release("v1.0.11"), record={"tag": "v1.0.11", "sha256": SHA_A})
        self.assertTrue(before.available)
        self.assertFalse(after.available)


class LatestReleaseFallback(unittest.TestCase):
    def test_fallback_list_never_returns_a_prerelease(self):
        import urllib.error

        def fake_api(url, timeout=25):
            if url == gu.GITHUB_API_LATEST:
                raise urllib.error.HTTPError(url, 404, "nf", hdrs=None, fp=None)
            return [_release("v1.1.0-beta.11", prerelease=True)]

        with mock.patch.object(gu, "_api_request", fake_api):
            with self.assertRaises(urllib.error.HTTPError):
                gu._fetch_latest_release()


class InstalledBuildRecord(unittest.TestCase):
    def test_reads_the_marker_written_by_the_installer(self):
        with tempfile.TemporaryDirectory() as root:
            app = os.path.join(root, "app")
            os.makedirs(os.path.join(app, "engine"))
            # As ConvertTo-Json writes it in Windows PowerShell 5.1: CRLF, may carry a BOM.
            text = json.dumps({"tag": "v1.0.11", "sha256": SHA_A, "bytes": 1}, indent=4)
            with open(os.path.join(app, gu.INSTALL_MARKER_NAME), "w", encoding="utf-8-sig", newline="\r\n") as fh:
                fh.write(text)
            exe = os.path.join(app, "engine", "SatpudaEngine.exe")
            with mock.patch.object(sys, "frozen", True, create=True), \
                 mock.patch.object(sys, "executable", exe):
                rec = gu.installed_build_record()
        self.assertIsNotNone(rec)
        self.assertEqual(rec["tag"], "v1.0.11")
        self.assertEqual(rec["sha256"], SHA_A)


if __name__ == "__main__":
    unittest.main()
