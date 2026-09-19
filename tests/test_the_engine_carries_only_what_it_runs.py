"""The desktop engine must not ship runtimes it cannot reach.

A shipped Win10 engine was 635.5 MB. PyInstaller's own module graph -- not grep
-- showed where 285 MB of that went, and none of it could ever run:

    run_desktop_api.py -> core.desktop_api -> core.desktop_settings_service
      -> core.voice (its __init__ imports the whole assistant)
      -> core.voice.recognizer -> faster_whisper
      -> av / ctranslate2 / onnxruntime / tokenizers -> huggingface_hub -> hf_xet

in a build with include_whisper=False and include_voice=False, because those
flags only ever filtered hiddenimports and the graph follows imports written
inside functions. And separately, camelot -> opencv (111.3 MB) for the 2nd PDF
table parser, which cannot run either: camelot imports pandas at module scope
and pandas has been in this spec's excludes since it was written.

The same audit proved what must STAY. googleapiclient is Drive backup, the
bundled master medicine DB is the product, and numpy / grpc / cryptography /
PIL / pdfplumber are all genuinely reached. Cutting those is how this project
once shipped a build that broke bill image import and Drive backup for every
shop, so they are pinned here too.
"""
import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

ENGINE_SPEC = os.path.join(ROOT, "SatpudaEngine_Folder.spec")

# Proved unreachable, with the MB each cost the shipped engine.
DEAD_WEIGHT = {
    "cv2": 111.3,
    "av": 65.4,
    "ctranslate2": 58.8,
    "onnxruntime": 34.6,
    "hf_xet": 9.1,
    "tokenizers": 7.2,
    "sounddevice": 0.3,
    "pyttsx3": 0.3,
}

# Proved reachable. Removing any of these breaks a feature a shop uses.
MUST_STAY = (
    "googleapiclient",
    "numpy",
    "grpc",
    "cryptography",
    "PIL",
    "pdfplumber",
    # Both QRs the product draws: the UPI code on the bill and the Mobile
    # Import address (core/qr_image.py). Trimming it silently prints a bill
    # with no pay code and leaves the Import panel with no picture.
    "qrcode",
)


def _spec_text():
    with open(ENGINE_SPEC, encoding="utf-8") as fh:
        return fh.read()


class TheEngineSpecAsksForTheSlimBuild(unittest.TestCase):
    def test_voice_and_whisper_are_off(self):
        text = _spec_text()
        self.assertRegex(text, r"include_whisper\s*=\s*False")
        self.assertRegex(text, r"include_voice\s*=\s*False")

    def test_the_spec_routes_its_excludes_through_bundle_excludes(self):
        """Excludes are the only lever that works: these packages are reached
        through real function-level imports, so dropping them from
        hiddenimports leaves them in the graph."""
        text = _spec_text()
        self.assertIn("bundle_excludes(", text)
        self.assertRegex(text, r"excludes=\[[^\]]*\]\s*\+\s*_extra_excludes")

    def test_pandas_is_still_excluded(self):
        """This is WHY camelot and tabula-py are dead. If pandas ever comes
        back, the reasoning in pyinstaller_extra_bundle.py needs revisiting."""
        self.assertRegex(_spec_text(), r'excludes=\[[^\]]*"pandas"')


class WhatTheBuildLeavesOut(unittest.TestCase):
    def setUp(self):
        import pyinstaller_extra_bundle as peb

        self.peb = peb
        self.excludes = peb.bundle_excludes(
            include_heavy_ocr=False, include_whisper=False, include_voice=False
        )

    def test_every_proved_dead_package_is_named(self):
        for name, mb in sorted(DEAD_WEIGHT.items(), key=lambda kv: -kv[1]):
            with self.subTest(package=name, mb=mb):
                self.assertIn(
                    name, self.excludes,
                    f"{name} ({mb} MB) is back in the engine",
                )

    def test_the_whole_whisper_chain_goes_not_just_its_entry_point(self):
        """Excluding faster_whisper alone leaves av/ctranslate2/onnxruntime in
        the graph, reached through faster_whisper's own submodules."""
        for name in ("faster_whisper", "ctranslate2", "av", "onnxruntime",
                     "tokenizers", "huggingface_hub", "hf_xet"):
            self.assertIn(name, self.excludes, name)

    def test_nothing_the_product_needs_is_excluded(self):
        for name in MUST_STAY:
            with self.subTest(package=name):
                self.assertNotIn(
                    name, self.excludes,
                    f"{name} is proved reachable -- excluding it breaks a feature",
                )

    def test_the_dead_pdf_parsers_do_not_ride_on_the_ocr_flag(self):
        """They used to. Turning local OCR back on some day would have quietly
        returned 111 MB of opencv for a parser that still cannot run, because
        pandas would still be excluded. The two decisions are unrelated."""
        with_ocr = self.peb.bundle_excludes(
            include_heavy_ocr=True, include_whisper=False, include_voice=False
        )
        self.assertIn("camelot", with_ocr)
        self.assertIn("cv2", with_ocr)

    def test_a_build_that_wants_the_table_parsers_can_still_ask(self):
        opted_in = self.peb.bundle_excludes(
            include_whisper=False, include_voice=False,
            include_pdf_table_parsers=True,
        )
        self.assertNotIn("camelot", opted_in)
        self.assertNotIn("cv2", opted_in)

    def test_a_build_that_wants_voice_keeps_the_speech_runtime(self):
        full = self.peb.bundle_excludes(include_whisper=True, include_voice=True)
        for name in ("faster_whisper", "av", "sounddevice", "pyttsx3"):
            self.assertNotIn(name, full, name)


class WhatTheBuildKeeps(unittest.TestCase):
    def test_drives_discovery_document_survives_the_pruning(self):
        """googleapiclient stays, but 598 of its 600 discovery documents are
        dead weight (100.3 MB). Drive's is the one core/backup_manager.py
        reads; without it Drive backup dies with UnknownApiNameOrVersion."""
        import pyinstaller_extra_bundle as peb

        marker = "googleapiclient/discovery_cache/documents/"
        datas = [
            (marker + "drive.v3.json", "src"),
            (marker + "compute.v1.json", "src"),
            (marker + "youtube.v3.json", "src"),
            ("config/master_medicine.db", "src"),
        ]
        kept = [d for d, _ in peb.prune_discovery_documents(datas)]
        self.assertIn(marker + "drive.v3.json", kept)
        self.assertIn("config/master_medicine.db", kept)
        self.assertNotIn(marker + "compute.v1.json", kept)

    def test_the_master_medicine_db_is_still_bundled(self):
        """It is the product, not one shop's state. It reaches the build
        through bundle_extras -> _optional_build_secrets, so check there and
        not in the spec text, where its name never appears."""
        import build_release_filter as brf
        import pyinstaller_extra_bundle as peb

        self.assertIn("bundle_extras(", _spec_text())
        cwd = os.getcwd()
        os.chdir(ROOT)
        try:
            secrets = [src.replace("\\", "/") for src, _ in peb._optional_build_secrets()]
        finally:
            os.chdir(cwd)
        if not os.path.isfile(os.path.join(ROOT, "config/master_medicine.db")):
            self.skipTest("no master_medicine.db in this checkout")
        self.assertIn("config/master_medicine.db", secrets)
        self.assertFalse(
            any("master_medicine.db" in x for x in brf.SHOP_IDENTITY),
            "the national medicine catalogue is being dropped as shop identity",
        )

    def test_the_vendor_credentials_are_still_bundled(self):
        """Dropping these once broke bill image import and Drive backup for
        every shop. Five files; the shop identity is what must go, not these."""
        import build_release_filter as brf

        text = _spec_text()
        for name in ("service_account.json", "oauth_client.json",
                     "config/firebase_service_account.json"):
            self.assertIn(name, text, f"{name} left the engine spec")
        for name in brf.VENDOR_CREDENTIALS:
            self.assertNotIn(
                name, brf.SHOP_IDENTITY,
                f"{name} is a vendor credential being dropped as shop identity",
            )


class TheZipCarriesNeitherALogNorAnInstaller(unittest.TestCase):
    """Two things the published zip carried that it should not have."""

    def setUp(self):
        with open(os.path.join(ROOT, "build_desktop_win10_folder.bat"), encoding="utf-8") as fh:
            self.bat = fh.read()

    def test_the_msi_is_not_copied_into_the_folder_that_gets_zipped(self):
        """229 MB of a 473 MB download was a Tauri installer sitting inside the
        payload that the installer downloads."""
        self.assertNotIn(
            r"%OUT%\installer", self.bat,
            "the MSI is being staged inside the folder that gets zipped again",
        )

    def test_the_msi_is_still_produced_beside_it(self):
        """Not shipping it in the zip is not the same as not building it."""
        self.assertIn("SatpudaCore_Desktop_Win10_MSI", self.bat)
        self.assertRegex(self.bat, r'xcopy[^\n]*bundle\\msi[^\n]*%MSIOUT%')

    def test_the_build_machines_engine_log_folder_is_cleared(self):
        self.assertRegex(
            self.bat, r'rmdir /s /q "%OUT%\\engine\\config"',
            "engine\\config is not cleared, so a log written on the build "
            "machine still ships to every shop",
        )

    def test_the_shell_writes_its_engine_log_outside_the_install_folder(self):
        """The shell holds this file open for the engine's whole run, and the
        updater upgrades by RENAMING app\\ -- which Windows allows over a
        running .exe but not over a file held with no sharing. That single
        handle is what turned an in-place update into Access Denied."""
        with open(os.path.join(ROOT, "desktop/src-tauri/src/lib.rs"), encoding="utf-8") as fh:
            rust = fh.read()
        body = re.search(
            r"fn engine_log_path\(root: &Path\) -> PathBuf \{(.*?)\n\}",
            rust, re.S,
        )
        self.assertIsNotNone(body, "engine_log_path is gone or was renamed")
        self.assertIn("app_data_dir()", body.group(1))
        self.assertIn('join("logs")', body.group(1))
        self.assertIn('join("VeterinaryApp")', rust)


if __name__ == "__main__":
    unittest.main()
