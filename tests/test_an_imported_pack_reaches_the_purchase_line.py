"""An imported pack has to survive the trip to the Purchase screen.

Every liquid on the SHIWANI AGENCIES bill photo landed on the shelf with a
pack of 1: the bill said 500ML, 1LTR, 50GMS, and the medicine was saved with
unit "1". The parser was never at fault -- it read all nine packs. The pack was
dropped twice on the way to the shop:

  1. the Purchase page built its line from `unit`, a name the import never
     sends (it sends `quantity_value` and `pack`), so the fallback "1" won;
  2. opening that line to edit it filled the Pack Size box from
     tablets_per_stripe, which every non-strip line carries as 1 -- and Save
     wrote that 1 back over the pack.

Each step below mirrors one rule in PurchasePage.tsx, so the two halves of the
chain are pinned from the Python side even though the rules live in TypeScript.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.layout_config import is_strip_count_type  # noqa: E402
from core.purchase_importer import _add_quantity_metadata  # noqa: E402

# type, pack as printed on the bill, what the Purchase screen must show
BILL = [
    ("Liquid", "500ML", "500ML"),
    ("Liquid", "1LTR", "1LTR"),
    ("Liquid", "5LTR", "5LTR"),
    ("Powder", "50GMS", "50GMS"),
    ("Powder", "100GM", "100GM"),
    ("Injection", "30ML", "30ML"),
    # A strip type counts tablets, so its pack becomes that count.
    ("Tablet", "10'S", "10"),
    ("Capsule", "15 TAB", "15"),
]


def imported_line(med_type: str, pack: str) -> dict:
    """What the engine hands the Purchase page for one imported bill row."""
    item = {"type": med_type, "pack": pack, "qty": 1, "free_qty": 0}
    _add_quantity_metadata(item, pack)
    return {
        "type": med_type,
        "pack": item.get("pack", ""),
        "quantity_value": item.get("quantity_value", ""),
        "tablets_per_stripe": item.get("tablets_per_stripe"),
    }


def line_from_payload(payload: dict) -> dict:
    """PurchasePage.tsx lineFromPayload: an edit sends `unit`, an import does not."""
    strip = is_strip_count_type(str(payload.get("type") or ""))
    tps = max(1, int(payload.get("tablets_per_stripe") or 0) or 1)
    text = str(
        payload.get("unit")
        or payload.get("quantity_value")
        or payload.get("pack")
        or ""
    ).strip()
    return {
        "type": payload.get("type", ""),
        "pack": text or (str(tps) if strip else "1"),
        "tablets_per_stripe": tps if strip else 1,
    }


def pack_box_when_opened(line: dict) -> str:
    """PurchasePage.tsx startLineEdit: what the Pack Size box shows."""
    return str(line.get("pack") or line.get("tablets_per_stripe") or "1")


def saved_unit(line: dict, pack_box: str) -> str:
    """What Save writes: the box is the line's pack for a non-strip type."""
    strip = is_strip_count_type(str(line.get("type") or ""))
    if strip:
        return str(max(1, int(float(pack_box or 1))))
    return (pack_box or "1").strip()


class AnImportedPack(unittest.TestCase):

    def test_reaches_the_purchase_line_as_the_bill_printed_it(self):
        for med_type, pack, expected in BILL:
            with self.subTest(pack=pack):
                line = line_from_payload(imported_line(med_type, pack))
                self.assertEqual(line["pack"], expected)

    def test_is_still_there_when_the_line_is_opened_to_edit(self):
        for med_type, pack, expected in BILL:
            with self.subTest(pack=pack):
                line = line_from_payload(imported_line(med_type, pack))
                self.assertEqual(pack_box_when_opened(line), expected)

    def test_survives_opening_the_line_and_saving_it_again(self):
        for med_type, pack, expected in BILL:
            with self.subTest(pack=pack):
                line = line_from_payload(imported_line(med_type, pack))
                box = pack_box_when_opened(line)
                self.assertEqual(saved_unit(line, box), expected)

    def test_is_never_flattened_to_one(self):
        for med_type, pack, _expected in BILL:
            with self.subTest(pack=pack):
                line = line_from_payload(imported_line(med_type, pack))
                self.assertNotEqual(
                    pack_box_when_opened(line), "1",
                    "{} {} lost its pack".format(med_type, pack),
                )

    def test_a_line_with_no_pack_at_all_still_gets_a_usable_one(self):
        for med_type in ("Liquid", "Tablet"):
            with self.subTest(type=med_type):
                line = line_from_payload(imported_line(med_type, ""))
                self.assertEqual(pack_box_when_opened(line), "1")


if __name__ == "__main__":
    unittest.main()
