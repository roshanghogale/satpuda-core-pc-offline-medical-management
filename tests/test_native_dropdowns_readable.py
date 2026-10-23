"""Native drop-down lists stay readable (owner's PC, 23 Oct 2026).

Settings > Sales & Billing > Default doctor opened as an empty black box in the desktop app
(Tauri / WebView2, Windows 11). The list a native <select> opens is drawn outside the page from
the options' own colours, and those were transparent. Every theme now colours the options with
its input colours, and the Default doctor box is the app's own list (ModernCombo), which is
drawn in the page.
"""
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _src(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


class NativeDropDowns(unittest.TestCase):
    def test_options_carry_the_themes_input_colours(self):
        css = _src("desktop", "src", "satpuda.css")
        m = re.search(r"select option,\s*select optgroup\s*\{([^}]*)\}", css)
        self.assertIsNotNone(m, "select option rule")
        self.assertIn("background-color: var(--inputbg, var(--surface))", m.group(1))
        self.assertIn("color: var(--inputfg, var(--text-primary))", m.group(1))

    def test_every_theme_defines_the_input_colours(self):
        css = _src("desktop", "src", "themes.generated.css")
        blocks = re.findall(r"\[data-theme[^{]*\{([^}]*)\}", css)
        self.assertGreaterEqual(len(blocks), 60)
        for b in blocks:
            self.assertIn("--inputbg:", b)
            self.assertIn("--inputfg:", b)

    def test_default_doctor_is_the_apps_own_list(self):
        src = _src("desktop", "src", "pages", "settings", "PrefsPanels.tsx")
        field = src[src.index("function DefaultDoctorField"): src.index("function pickKeys")]
        self.assertIn("<ModernCombo", field)
        self.assertNotRegex(field, r"<select[\s/]")


if __name__ == "__main__":
    unittest.main()
