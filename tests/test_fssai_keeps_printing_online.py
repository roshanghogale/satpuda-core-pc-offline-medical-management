"""A shop that was printing its FSSAI number must keep printing it Online.

"Print FSSAI on sale bills" was saved for years and never read -- the templates
printed the number whenever one existed. When the checkbox was made real,
db_setup._migrate_fssai_print_flag turned it on once for every shop that had a
number and had never ticked the box, so nothing changed on their bills.

That migration is an UPDATE against the local pharmacy_profile table. Online
there is no local table to migrate: the profile comes from the store server, and
a shop whose server copy carries show_fssai_on_bill FALSE -- pushed from a build
where the flag was write-only -- silently lost the FSSAI line from its bills,
while the same shop Offline kept printing it. A drug licence number vanishing
from a bill is not a change to make quietly.

Same rule, same one-shot: the legacy default applies until the shop saves the
profile itself, and from then on the checkbox is theirs. It only reads -- it
never pushes a corrected profile back to a live store.
"""
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import pharmacy_profile_io as ppio  # noqa: E402

WITH_NUMBER_FLAG_OFF = {
    "name": "ZZ TEST MEDICAL", "address": "Main Road", "phone": "900",
    "fssai_number": "12345678901234", "show_fssai_on_bill": False,
}


class TheLegacyDefault(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self._p = mock.patch("core.pharmacy_profile_io._appdata_dir", return_value=self.dir)
        self._p.start()
        self._k = mock.patch("core.pharmacy_profile_io._active_store_key",
                             return_value="ZZ_Test")
        self._k.start()

    def tearDown(self):
        self._p.stop()
        self._k.stop()

    def test_a_shop_with_a_number_and_the_flag_off_keeps_printing(self):
        out = ppio._apply_fssai_legacy_default(dict(WITH_NUMBER_FLAG_OFF))
        self.assertTrue(
            out["show_fssai_on_bill"],
            "the FSSAI line disappeared from this shop's bills Online",
        )

    def test_a_shop_with_no_number_is_left_alone(self):
        out = ppio._apply_fssai_legacy_default(
            dict(WITH_NUMBER_FLAG_OFF, fssai_number="")
        )
        self.assertFalse(out["show_fssai_on_bill"])

    def test_a_shop_that_already_prints_is_untouched(self):
        src = dict(WITH_NUMBER_FLAG_OFF, show_fssai_on_bill=True)
        self.assertEqual(ppio._apply_fssai_legacy_default(src), src)

    def test_the_input_is_not_mutated(self):
        src = dict(WITH_NUMBER_FLAG_OFF)
        ppio._apply_fssai_legacy_default(src)
        self.assertFalse(src["show_fssai_on_bill"], "the caller's dict was rewritten")

    def test_once_the_shop_has_chosen_the_default_stops(self):
        ppio._record_fssai_choice()
        out = ppio._apply_fssai_legacy_default(dict(WITH_NUMBER_FLAG_OFF))
        self.assertFalse(
            out["show_fssai_on_bill"],
            "the shop unticked the box and the default turned it back on",
        )

    def test_the_marker_is_per_store(self):
        ppio._record_fssai_choice()
        with mock.patch("core.pharmacy_profile_io._active_store_key",
                        return_value="Some_Other_Store"):
            out = ppio._apply_fssai_legacy_default(dict(WITH_NUMBER_FLAG_OFF))
        self.assertTrue(out["show_fssai_on_bill"], "one shop's choice silenced another's")

    def test_it_never_writes_to_the_server(self):
        # The whole point of doing this at read time. The suite blocks sockets,
        # so a push would raise; assert the intent explicitly all the same.
        with mock.patch("core.pharmacy_profile_io.save_pharmacy_profile") as push:
            ppio._apply_fssai_legacy_default(dict(WITH_NUMBER_FLAG_OFF))
        push.assert_not_called()


if __name__ == "__main__":
    unittest.main()
