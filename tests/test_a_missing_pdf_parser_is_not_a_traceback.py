"""A PDF bill must never fail with a package name on the screen.

camelot and tabula-py are the 2nd and 3rd table parsers behind pdfplumber. Both
import pandas at module scope, no spec has ever bundled pandas, and the engine
build now excludes them outright so camelot cannot drag opencv (111 MB) in with
it. So in a shipped engine `import camelot` raises ModuleNotFoundError -- every
time, forever.

That was already caught, so it was never a traceback. What it WAS is a message
written for the developer and shown to the shopkeeper:

    No purchase rows were found in this PDF. Parser messages:
    camelot is not installed; tabula-py is not installed

There is no pip inside a PyInstaller folder. Nobody behind a counter can act on
that sentence. These tests pin the two things that matter: the chain degrades
all the way to the built-in text reader without raising, and what the shop is
finally told is something the shop can do.
"""
import importlib
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class _ExcludedFromTheBuild:
    """Reproduce what PyInstaller's excludes= do at runtime.

    An excluded package is not absent from disk, it is absent from the frozen
    archive: the import simply raises ModuleNotFoundError. Blocking the name in
    sys.meta_path gives the real `import camelot` statement the real failure,
    so these tests exercise the actual except-clause rather than a stub.
    """

    def __init__(self, *names):
        self.names = set(names)

    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".")[0] in self.names:
            raise ModuleNotFoundError(f"No module named {fullname!r}", name=fullname)
        return None

    def __enter__(self):
        self._saved = {n: sys.modules.pop(n, None) for n in list(self.names)}
        for name in list(sys.modules):
            if name.split(".")[0] in self.names:
                self._saved[name] = sys.modules.pop(name)
        sys.meta_path.insert(0, self)
        return self

    def __exit__(self, *exc):
        sys.meta_path.remove(self)
        for name, mod in self._saved.items():
            if mod is not None:
                sys.modules[name] = mod
        return False


# A syntactically real PDF with one uncompressed text stream and no table.
# This is the shape that sends the importer down the whole fallback chain.
_TEXTONLY_PDF = (
    b"%PDF-1.4\n"
    b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
    b"3 0 obj<</Type/Page/Parent 2 0 R/Contents 4 0 R>>endobj\n"
    b"4 0 obj<</Length 44>>\n"
    b"stream\n"
    b"BT (TAX INVOICE) Tj T* (No item table here) Tj ET\n"
    b"endstream\n"
    b"endobj\n"
    b"trailer<</Root 1 0 R>>\n"
    b"%%EOF\n"
)

_PACKAGE_WORDS = (
    "camelot",
    "tabula",
    "pdfplumber",
    "cv2",
    "opencv",
    "pandas",
    "is not installed",
    "ModuleNotFoundError",
    "ImportError",
    "Traceback",
)


class TheFallbackChainDegrades(unittest.TestCase):
    def setUp(self):
        self.importer = importlib.import_module("core.purchase_importer")
        # A real pdfplumber (present on the build machine, absent on this Mac)
        # can leave the fixture open after it throws, and Windows will not
        # delete an open file -- so the directory goes with ignore_errors.
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        self.pdf = os.path.join(self.tmp.name, "bill.pdf")
        with open(self.pdf, "wb") as fh:
            fh.write(_TEXTONLY_PDF)
        # Diagnostics are appended to a log; keep that out of the repo.
        self.log_dir = os.path.join(self.tmp.name, "config")
        os.makedirs(self.log_dir, exist_ok=True)
        real_config_dir = self.importer._config_dir
        self.importer._config_dir = lambda: self.log_dir
        self.addCleanup(setattr, self.importer, "_config_dir", real_config_dir)

    def _parse(self, *blocked):
        """Parse the fixture with those packages excluded, as in a real build."""
        with _ExcludedFromTheBuild(*blocked):
            with self.assertRaises(Exception) as caught:
                self.importer.parse_purchase_pdf(self.pdf)
        return caught.exception

    def _log_lines(self):
        path = os.path.join(self.log_dir, "purchase_import.log")
        if not os.path.isfile(path):
            return []
        with open(path, encoding="utf-8") as fh:
            return [line for line in fh.read().splitlines() if line.strip()]

    def test_a_pdf_with_no_table_raises_the_import_error_not_the_python_one(self):
        exc = self._parse("camelot", "tabula", "cv2")
        self.assertIsInstance(
            exc, self.importer.InvoiceParseError,
            f"the PDF path escaped as {type(exc).__name__}: {exc}",
        )

    def test_the_message_never_names_a_python_package(self):
        message = str(self._parse("camelot", "tabula", "cv2")).lower()
        for word in _PACKAGE_WORDS:
            self.assertNotIn(
                word.lower(), message,
                f"the shop is being shown {word!r}: {message}",
            )

    def test_the_message_says_what_the_shop_can_do_instead(self):
        message = str(self._parse("camelot", "tabula", "cv2")).lower()
        self.assertIn("csv", message)
        self.assertIn("photo", message)
        self.assertIn("hand", message)

    def test_it_falls_through_camelot_and_tabula_to_the_built_in_reader(self):
        """Degrading means the NEXT parser actually runs, not that it gives up."""
        reached = []
        real = self.importer._parse_pdf_with_builtin_text

        def spy(path):
            reached.append(path)
            return real(path)

        self.importer._parse_pdf_with_builtin_text = spy
        try:
            self._parse("camelot", "tabula", "cv2")
        finally:
            self.importer._parse_pdf_with_builtin_text = real
        self.assertEqual(
            reached, [self.pdf],
            "the built-in text reader was never reached -- the chain stopped at "
            "a parser this build does not carry",
        )

    def test_a_damaged_install_missing_pdfplumber_says_so_in_plain_words(self):
        """pdfplumber IS shipped. If it is gone the install is broken, and that
        is something a shop can act on -- but still without a package name."""
        message = str(self._parse("pdfplumber", "camelot", "tabula", "cv2"))
        self.assertIn("Reinstall", message)
        for word in _PACKAGE_WORDS:
            self.assertNotIn(word.lower(), message.lower())

    def test_the_diagnostics_are_kept_in_the_log_not_thrown_away(self):
        """Hiding 'pdfplumber failed: NoneType is not iterable' from the shop
        must not hide it from support."""
        self._parse("camelot", "tabula", "cv2")
        lines = self._log_lines()
        self.assertTrue(lines, "nothing was written to purchase_import.log")
        entry = json.loads(lines[-1])
        self.assertEqual(entry["status"], "pdf_unreadable")
        self.assertEqual(entry["source_path"], self.pdf)
        self.assertIn("camelot", entry["parsers_not_in_this_build"])


class TheMessageItself(unittest.TestCase):
    def setUp(self):
        self.importer = importlib.import_module("core.purchase_importer")

    def test_absent_optional_parsers_are_never_mentioned(self):
        msg = self.importer.pdf_no_rows_message(
            issues=[], missing_parsers=["camelot", "tabula-py"]
        )
        self.assertNotIn("camelot", msg)
        self.assertNotIn("tabula", msg)
        self.assertIn("CSV", msg)

    def test_a_python_exception_never_reaches_the_dialog(self):
        """This is the exact string the build machine produced."""
        msg = self.importer.pdf_no_rows_message(
            issues=["pdfplumber failed: 'NoneType' object is not iterable"]
        )
        self.assertNotIn("pdfplumber", msg)
        self.assertNotIn("NoneType", msg)

    def test_a_locked_pdf_gets_the_one_sentence_a_shop_can_act_on(self):
        for said in ("file has not been decrypted",
                     "PDF is password protected",
                     "encrypted document"):
            with self.subTest(said=said):
                msg = self.importer.pdf_no_rows_message(issues=[said])
                self.assertIn("password", msg.lower())
                self.assertIn("distributor", msg.lower())


if __name__ == "__main__":
    unittest.main()
