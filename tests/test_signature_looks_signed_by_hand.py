"""The signature looks signed by hand on every bill (owner, 18 Oct 2026).

Each print gets its own small tilt, size and place; the Sales Tabs & Tools switch turns the
picture on and off (the bill setting print_signature_image).
"""
import random
import re
import unittest
from unittest import mock

from core import bill_signature_look as look


class EachPrintItsOwn(unittest.TestCase):
    def _parts(self, style):
        m = re.search(r"translate\((-?[\d.]+)%, (-?[\d.]+)%\) rotate\((-?[\d.]+)deg\) scale\(([\d.]+)\)", style)
        self.assertIsNotNone(m, style)
        return [float(x) for x in m.groups()]

    def test_always_a_little_and_never_much(self):
        rng = random.Random(7)
        for _ in range(500):
            dx, dy, tilt, size = self._parts(look.natural_style(rng))
            self.assertLessEqual(abs(tilt), look.MAX_TILT_DEG)
            self.assertTrue(look.SIZE_RANGE[0] <= size <= look.SIZE_RANGE[1])
            self.assertLessEqual(abs(dx), look.MAX_SHIFT_PCT)
            self.assertTrue(look.UP_DOWN_PCT[0] <= dy <= look.UP_DOWN_PCT[1])

    def test_two_prints_differ(self):
        self.assertNotEqual(look.natural_style(), look.natural_style())

    def test_up_and_down_and_sizes_that_show(self):
        rng = random.Random(11)
        parts = [self._parts(look.natural_style(rng)) for _ in range(200)]
        self.assertTrue(any(p[1] < -8 for p in parts) and any(p[1] > 4 for p in parts))
        sizes = [p[3] for p in parts]
        self.assertGreater(max(sizes) - min(sizes), 0.25)
        self.assertGreater(sum(1 for p in parts if abs(p[2]) >= look.MIN_TILT_DEG) / len(parts), 0.6)

    def test_left_and_right_both_happen(self):
        rng = random.Random(3)
        tilts = [self._parts(look.natural_style(rng))[2] for _ in range(50)]
        self.assertTrue(any(t < 0 for t in tilts) and any(t > 0 for t in tilts))


class TheSwitch(unittest.TestCase):
    def test_off_and_on(self):
        saved = {}
        with mock.patch("core.bill_config.load_bill_print_settings", side_effect=lambda: dict(saved)), \
             mock.patch("core.bill_config.save_bill_print_settings", side_effect=lambda s: saved.update(s)), \
             mock.patch("core.bill_context._signature_to_base64", return_value="data:image/png;base64,AA"):
            self.assertTrue(look.signature_switch()["on"])
            self.assertFalse(look.set_signature_switch(False)["on"])
            self.assertFalse(saved["print_signature_image"])
            res = look.set_signature_switch(True)
            self.assertTrue(res["on"] and res["uploaded"])


class OnTheBill(unittest.TestCase):
    def test_the_picture_carries_its_own_look(self):
        from types import SimpleNamespace

        from bill_templates.classic import _signature

        ctx = SimpleNamespace(store_name="Test Medical", signature_src="data:image/png;base64,AA")
        html = _signature(ctx, {"signature_position": "center"})
        self.assertRegex(html, r'class="sign-img-cover"[^>]*style="transform: translate\(')
        pinned = _signature(ctx, {"signature_position": "center", "signature_look": "transform: none;"})
        self.assertIn('style="transform: none;"', pinned)
        off = _signature(ctx, {"signature_position": "center", "print_signature_image": False})
        self.assertNotIn("sign-img-cover", off)


if __name__ == "__main__":
    unittest.main()
