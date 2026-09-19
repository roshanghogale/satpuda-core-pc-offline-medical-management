"""A build must not carry a shop's identity, or anybody's credentials.

One did. It was handed over with another shop's Google Drive backup folder baked
in, that shop's learned distributor names, 857 KB of a real supplier list
carrying twelve GSTINs and nineteen drug licence numbers, and this developer
machine's printer and Documents path -- alongside five live keys. The owner
noticed the store name on screen; nothing else was looking.

Two of those were not merely present but ACTIVE:

  * the bundled catalog.json was copied over the shop's own supplier list on
    every launch, unconditionally;
  * a differing Drive folder id in the bundle overwrote the shop's own on every
    start, keeping the local store NAME so the screen still looked right while
    the backups went somewhere else.

These pin the fixes.
"""
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


class TheShopsOwnCatalogIsNeverOverwritten(unittest.TestCase):
    def test_the_copy_list_does_not_include_the_runtime_catalog(self):
        """catalog.json is written from the shop's OWN database at runtime."""
        with open(os.path.join(ROOT, "core/web_purchase_server.py"), encoding="utf-8") as fh:
            body = fh.read()
        start = body.index("def prepare_web_purchase_root(")
        end = body.index("\ndef ", start + 10)
        block = "\n".join(
            ln for ln in body[start:end].splitlines()
            if not ln.lstrip().startswith("#")
        )
        self.assertIn("'index.html', 'medicines.json'", block)
        self.assertNotIn(
            "'catalog.json'", block,
            "the bundled catalog is being copied over the shop's own again",
        )
        self.assertNotIn(
            "'launcher.html'", block,
            "launcher.html carried real suppliers' GSTINs and licence numbers",
        )

    def test_those_two_files_are_not_in_the_repo(self):
        for rel in ("web_app/catalog.json", "web_app/launcher.html"):
            self.assertFalse(
                os.path.exists(os.path.join(ROOT, rel)),
                f"{rel} is back in the repo and will be bundled again",
            )


class AnInstalledShopsBackupFolderIsItsOwn(unittest.TestCase):
    def test_a_differing_bundled_folder_id_is_not_applied(self):
        from core import backup_manager as bm

        with tempfile.TemporaryDirectory() as tmp:
            bundled = os.path.join(tmp, "bundled.dat")
            installed = os.path.join(tmp, "installed.dat")
            bm._write_config_file(bundled, "FOLDER-FROM-THE-BUILD", "Some Other Shop")
            bm._write_config_file(installed, "FOLDER-THIS-SHOP-CHOSE", "This Shop")

            with open(installed, "rb") as fh:
                before = fh.read()

            # (filename, bundled path, installed path, force)
            reseed = bm._should_reseed_backup_file(
                "backup_config.dat", bundled, installed, False
            )
            self.assertFalse(reseed, "the shop's file was marked for replacement")

            with open(installed, "rb") as fh:
                after = fh.read()
            self.assertEqual(
                after, before,
                "the shop's Drive folder was rewritten by the build — its backups "
                "would go to somebody else's folder while the screen kept showing "
                "its own store name",
            )
            self.assertEqual(
                bm._decrypt_dict(after).get("folder_id"), "FOLDER-THIS-SHOP-CHOSE"
            )


class TheSpecLeavesShopIdentityOut(unittest.TestCase):
    def test_a_clean_build_drops_one_shops_identity(self):
        import build_release_filter as brf

        # Default must be the SAFE build: forgetting the flag cannot leak.
        self.assertTrue(
            brf.CLEAN_RELEASE,
            "the default build is the leaky one -- forgetting the flag must be safe",
        )
        for name in (
            "config/backup_config.dat",
            "config/import_learned.json",
            "config/printer_settings.json",
            "config/bill_print_settings.json",
            "config/activation.dat",
            "config/expiry.dat",
        ):
            self.assertIn(
                name, brf.SHOP_IDENTITY,
                f"{name} is not excluded from a clean build",
            )

    def test_a_clean_build_actually_drops_them(self):
        import build_release_filter as brf

        pairs = [("config/backup_config.dat", "config"),
                 ("config/theme_config.txt", "config")]
        kept = [src for src, _ in brf.clean_release(pairs)]
        self.assertEqual(kept, ["config/theme_config.txt"])

    def test_every_spec_routes_its_datas_through_the_filter(self):
        """The rule lived in one spec, so the six classic ones shipped
        everything it excluded. Whoever adds a spec next inherits the rule."""
        import glob

        for path in sorted(glob.glob(os.path.join(ROOT, "*.spec"))):
            name = os.path.basename(path)
            with open(path, encoding="utf-8") as fh:
                spec = fh.read()
            with self.subTest(spec=name):
                self.assertIn(
                    "build_release_filter", spec,
                    f"{name} does not use the shared release filter",
                )
                self.assertRegex(
                    spec, r"datas=_(release_datas|existing)\(",
                    f"{name} builds its datas list without the filter",
                )

    def test_the_vendors_own_credentials_are_never_excluded(self):
        """Excluding these does not tighten anything -- it breaks the product.

        core/gemini_bill_config.py:84 falls back to the bundled Gemini key, so
        without it bill IMAGE IMPORT has no key at all. core/backup_manager.py
        builds the Drive Credentials -- client_id and client_secret included --
        entirely out of backup_creds.dat, so without it NO store can back up to
        Drive. They are the vendor's accounts, shared with every copy on
        purpose. This test exists because they were once removed and both
        features died.
        """
        import build_release_filter as brf

        for keep in (
            "backup_creds.dat",
            "gemini_api_key.txt",
            "master_medicine.db",
        ):
            self.assertFalse(
                any(keep in x for x in brf.SHOP_IDENTITY),
                f"{keep} is being dropped from builds — that breaks a feature",
            )
        # And they must still be listed as the vendor's, so the audit reports
        # them instead of refusing the build over them.
        self.assertIn("config/backup_creds.dat", brf.VENDOR_CREDENTIALS)
        self.assertIn("config/gemini_api_key.txt", brf.VENDOR_CREDENTIALS)

    def test_the_national_medicine_catalogue_is_still_shipped(self):
        """Excluding this would break every shop; it is the product, not state."""
        import build_release_filter as brf

        self.assertFalse(any("master_medicine.db" in x for x in brf.SHOP_IDENTITY))

    def test_the_firebase_keys_are_only_droppable_while_nothing_reads_them(self):
        """Three keys were shipped to every shop for a feature that is gone.

        Sync has been the Satpuda Core Server (Node + Postgres) for a long time.
        Nothing in the engine touches firestore or firebase_admin -- so the keys
        were pure exposure. If somebody brings Firebase back, this test fails
        and says: put the credential handling back before you ship it.
        """
        import ast

        seen: set[str] = set()
        stack = [os.path.join(ROOT, "run_desktop_api.py")]
        offenders: list[str] = []
        while stack:
            f = stack.pop()
            if f in seen or not os.path.isfile(f):
                continue
            seen.add(f)
            try:
                with open(f, encoding="utf-8", errors="replace") as fh:
                    body = fh.read()
                tree = ast.parse(body)
            except Exception:
                continue
            if "firestore" in body or "firebase_admin" in body:
                offenders.append(os.path.relpath(f, ROOT))
            for n in ast.walk(tree):
                names = []
                if isinstance(n, ast.Import):
                    names = [a.name for a in n.names]
                elif isinstance(n, ast.ImportFrom) and n.module:
                    names = [n.module]
                for m in names:
                    if m.startswith("core."):
                        stack.append(os.path.join(ROOT, m.replace(".", os.sep) + ".py"))

        self.assertGreater(len(seen), 50, "the import walk did not reach the engine")
        # Kept as a note, not as a reason to drop the key: the service account
        # is unused today, but it ships with the rest of the vendor's
        # credentials and removing it is not worth the risk it once caused.
        self.assertEqual(
            offenders, [],
            "Firebase is back in the engine, but the build no longer ships its "
            "service account. Either drop the dependency again, or decide "
            "deliberately how the key reaches a shop — do not simply put it "
            "back in every release.",
        )


class TheAuditRefusesALeakyFolder(unittest.TestCase):
    """The check that would have caught this before it left the building."""

    def _run(self, folder, shop=""):
        cmd = [sys.executable, os.path.join(ROOT, "scripts/audit_release_folder.py"), folder]
        if shop:
            cmd += ["--shop", shop]
        env = dict(os.environ)
        env.pop("SATPUDA_BUILD", None)
        r = subprocess.run(cmd, capture_output=True, text=True, env=env)
        return r.returncode, r.stdout

    def test_a_clean_folder_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "index.html"), "w", encoding="utf-8") as fh:
                fh.write("<html>nothing personal here</html>")
            code, out = self._run(tmp)
        self.assertEqual(code, 0, out)
        self.assertIn("clean", out)

    def test_a_vendor_credential_is_reported_but_does_not_refuse(self):
        """The Drive token and the Gemini key belong in every build."""
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "backup_creds.dat"), "wb") as fh:
                fh.write(b"x" * 40)
            code, out = self._run(tmp)
        self.assertEqual(code, 0, out)
        self.assertIn("backup_creds.dat", out)

    def test_another_shops_backup_folder_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "backup_config.dat"), "wb") as fh:
                fh.write(b"x" * 40)
            code, out = self._run(tmp)
        self.assertEqual(code, 1, out)
        self.assertIn("REFUSED", out)

    def test_another_shops_data_inside_a_file_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "page.html"), "w", encoding="utf-8") as fh:
                fh.write('{"name": "SOME OTHER SHOP", "gstin": "27ABCDE1234F1Z5"}')
            code, out = self._run(tmp)
        self.assertEqual(code, 1, out)
        self.assertIn("GSTIN", out)

    def test_a_named_shop_is_found_even_without_a_gstin(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "app.js"), "w", encoding="utf-8") as fh:
                fh.write("var last = 'shri swami samarth medical stores';")
            code, out = self._run(tmp, shop="Swami Samarth")
        self.assertEqual(code, 1, out)
        self.assertIn("Swami Samarth", out)

    def test_a_paired_build_reports_but_does_not_refuse(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "backup_config.dat"), "wb") as fh:
                fh.write(b"x" * 40)
            env = dict(os.environ, SATPUDA_BUILD="paired")
            r = subprocess.run(
                [sys.executable, os.path.join(ROOT, "scripts/audit_release_folder.py"), tmp],
                capture_output=True, text=True, env=env,
            )
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("PAIRED", r.stdout)


if __name__ == "__main__":
    unittest.main()
