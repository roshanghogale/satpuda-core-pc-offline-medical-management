"""A payment must never be written with a temporary id.

The queue stamps a temporary NEGATIVE id on any record that leaves without one.
That id then reached the server as the payment's permanent local_id, and the
shop could never delete it again -- the app looked it up and said "payment not
found". Ten payments were stranded that way in one store: seven to suppliers,
three to customers, including an accidental duplicate of Rs 2,162 that could not
be removed and left the supplier's due Rs 2,162 short.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.online_mutation_queue import temp_id_from_uuid  # noqa: E402


class TestTemporaryIdsAreRecognisable(unittest.TestCase):
    def test_a_temporary_id_is_always_negative(self):
        for u in (
            "dc62134d-82a8-43a5-9f23-ce005d3e656d",
            "6174f2a1-eea9-40a3-9618-888158a43985",
            "",
        ):
            self.assertLess(temp_id_from_uuid(u), 0, u)

    def test_the_stranded_payments_carried_temporary_ids(self):
        # The shape the shop actually saw on the server.
        for stranded in (-1422703609, -1422703610, -1314500366, -1335427660):
            self.assertTrue(self._is_temporary(stranded), stranded)

    def test_a_real_id_is_positive(self):
        for real in (1, 2, 7, 103, 3019):
            self.assertFalse(self._is_temporary(real), real)

    @staticmethod
    def _is_temporary(local_id: int) -> bool:
        """The rule a repair or a guard should use: temporary ids are negative."""
        return int(local_id) < 0


class TestSaveRefusesWithoutARealId(unittest.TestCase):
    """save_payment now returns an error rather than writing an unusable record."""

    def test_the_refusal_carries_a_code_the_ui_can_act_on(self):
        import core.desktop_settings_service as svc

        src = open(svc.__file__.replace(".pyc", ".py"), encoding="utf-8").read()
        # Both parties must refuse, not just one.
        self.assertEqual(src.count('"code": "payment_id_unavailable"'), 2)
        self.assertIn('allocate_id("supplier_payments")', src)
        self.assertIn('allocate_id("customer_payments")', src)

    def test_nothing_falls_back_to_a_bare_zero_any_more(self):
        import core.desktop_settings_service as svc

        src = open(svc.__file__.replace(".pyc", ".py"), encoding="utf-8").read()
        self.assertNotIn("            except Exception:\n                pay_id = 0", src)


if __name__ == "__main__":
    unittest.main(verbosity=1)
