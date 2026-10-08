"""The updater's version order, with prereleases (offline-first beta, Oct 2026)."""
import unittest

from core.github_updater import is_newer_version


class VersionOrder(unittest.TestCase):
    def test_plain_versions(self):
        self.assertTrue(is_newer_version("v1.0.9", "1.0.8"))
        self.assertFalse(is_newer_version("v1.0.8", "1.0.8"))
        self.assertTrue(is_newer_version("1.1", "1.0.8"))
        self.assertFalse(is_newer_version("1.0", "1.0.0"))

    def test_a_beta_is_newer_than_the_last_release_and_older_than_its_own_final(self):
        self.assertFalse(is_newer_version("v1.0.8", "1.1.0-beta.1"))   # beta PC stays on beta
        self.assertTrue(is_newer_version("v1.1.0", "1.1.0-beta.1"))    # ... until 1.1.0 is out
        self.assertTrue(is_newer_version("v1.1.0-beta.2", "1.1.0-beta.1"))
        self.assertTrue(is_newer_version("v1.1.0-beta.1", "1.0.8"))
        self.assertFalse(is_newer_version("v1.1.0-beta.1", "1.1.0"))


if __name__ == "__main__":
    unittest.main()
