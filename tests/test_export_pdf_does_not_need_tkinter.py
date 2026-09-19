"""Exporting a PDF from the desktop app must not need Tk.

The engine spec excludes tkinter on purpose -- the data engine is headless and
Tcl/Tk has no business in it. core/themed_messagebox.py already learned this the
hard way and guards its import ("printing a bill, saving a PDF -- then died with
ImportError and the desktop API answered 500").

core/export_manager.py did not, and it imported tkinter at module scope. Every
engine path that reaches into it for a Tk-FREE helper died on line 22, before a
single line of export code ran:

    /api/export/run  format=pdf        -> desktop_export_service.py:1773
    /api/export/run  print=true        -> same call
    /api/startup/alerts/action export_pdf -> desktop_startup_service.py:207
    Settings -> export contacts        -> desktop_settings_service.py:5484

Verified against the shipped Win10 engine, both answering 500:

    {"error": "/api/export/run failed: No module named 'tkinter'.",
     "kind": "ModuleNotFoundError"}
    {"error": "/api/startup/alerts/action failed: No module named 'tkinter'.",
     "kind": "ModuleNotFoundError"}

What the shop saw was "Export failed" on the PDF button of every page.
"""
import importlib
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

ENGINE_SPEC = os.path.join(ROOT, "SatpudaEngine_Folder.spec")


class _ExcludedFromTheBuild:
    """Reproduce what PyInstaller's excludes= do at runtime.

    An excluded package is not absent from disk, it is absent from the frozen
    archive: the import simply raises ModuleNotFoundError. Blocking the name in
    sys.meta_path gives the real `import tkinter` statement the real failure, so
    these tests exercise the module as the packaged engine actually loads it.
    """

    def __init__(self, *names):
        self.names = set(names)

    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".")[0] in self.names:
            raise ModuleNotFoundError(f"No module named {fullname!r}", name=fullname)
        return None

    def __enter__(self):
        self._saved = {}
        for name in list(sys.modules):
            root = name.split(".")[0]
            if root in self.names or name.startswith("core.export_manager"):
                self._saved[name] = sys.modules.pop(name)
        sys.meta_path.insert(0, self)
        return self

    def __exit__(self, *exc):
        sys.meta_path.remove(self)
        for name in list(sys.modules):
            if name.startswith("core.export_manager"):
                del sys.modules[name]
        for name, mod in self._saved.items():
            if mod is not None:
                sys.modules[name] = mod
        return False


class TheSpecStillLeavesTkOut(unittest.TestCase):
    """If tkinter ever comes back into the engine, the guard below is moot --
    but so is the 620 MB the slimming took off, so it should not."""

    def test_tkinter_is_excluded_from_the_engine(self):
        with open(ENGINE_SPEC, encoding="utf-8") as fh:
            text = fh.read()
        self.assertRegex(text, r'excludes=\[[^\]]*"tkinter"')
        self.assertRegex(text, r'excludes=\[[^\]]*"_tkinter"')


class ExportManagerLoadsWithoutTk(unittest.TestCase):
    def test_the_module_imports_at_all(self):
        with _ExcludedFromTheBuild("tkinter", "_tkinter", "ttkbootstrap"):
            em = importlib.import_module("core.export_manager")
            self.assertFalse(em.HAS_TK)
            self.assertTrue(callable(em._save_pdf_to_path))
            self.assertTrue(callable(em._save_all_pdf_to_path))
            self.assertTrue(callable(em.export_data_direct))

    def test_the_pdf_helper_the_engine_calls_actually_runs(self):
        """_save_pdf_to_path builds HTML and hands it to document_output. No Tk
        anywhere in it -- only the import at the top of the file stopped it."""
        with _ExcludedFromTheBuild("tkinter", "_tkinter", "ttkbootstrap"):
            em = importlib.import_module("core.export_manager")
            fd, path = tempfile.mkstemp(suffix=".pdf", prefix="export_no_tk_")
            os.close(fd)
            produced = ""
            try:
                produced = em._save_pdf_to_path(
                    path, "Stock Statement", ["Medicine", "Qty"],
                    [["AMOXYCILLIN 500MG CAP", 20]],
                )
                # A PDF when a browser is installed, the HTML fallback when not.
                # Either is a file; a ModuleNotFoundError is not.
                self.assertTrue(produced, "no export file was produced")
                self.assertTrue(os.path.isfile(produced), produced)
            finally:
                for p in (produced, path, path.replace(".pdf", ".html")):
                    if p and os.path.isfile(p):
                        try:
                            os.remove(p)
                        except OSError:
                            pass


class TheEngineEndpointsThatBrokeOnIt(unittest.TestCase):
    def test_startup_alert_pdf_export_does_not_die_on_an_import(self):
        with _ExcludedFromTheBuild("tkinter", "_tkinter", "ttkbootstrap"):
            svc = importlib.import_module("core.desktop_startup_service")
            result = svc.export_startup_alert_pdf({
                "title": "Low Stock Alerts",
                "columns": ["Medicine", "Stock"],
                "rows": [["AMOXYCILLIN 500MG CAP", "3"]],
            })
            self._assert_no_package_name(result.get("error", ""))

    def test_export_to_file_pdf_does_not_die_on_an_import(self):
        with _ExcludedFromTheBuild("tkinter", "_tkinter", "ttkbootstrap"):
            svc = importlib.import_module("core.desktop_export_service")
            try:
                result = svc.export_to_file(
                    None, "inventory", "current_view", "pdf",
                    current_columns=["Medicine", "Qty"],
                    current_rows=[["AMOXYCILLIN 500MG CAP", 20]],
                )
            except ModuleNotFoundError as exc:  # the bug, exactly
                self.fail(f"PDF export still needs a missing package: {exc}")
            except Exception:
                return  # a data/browser problem is not this test's business
            self._assert_no_package_name(result.get("error", ""))

    def _assert_no_package_name(self, message):
        low = str(message).lower()
        for word in ("tkinter", "no module named", "modulenotfounderror"):
            self.assertNotIn(
                word, low,
                f"a shop was shown a missing Python package: {message!r}",
            )


if __name__ == "__main__":
    unittest.main()
