"""An Online supplier payment never writes a purchase document at all.

After an Online supplier payment the PC used to work the payment down the supplier's bills
(FIFO) and push every bill with its new due.

First it built those documents from the purchases LIST, whose rows carry only a few columns,
and the server stored every header column the client left out as 0: on 2026-09-11 that blanked
total_amount, subtotal and total_gst on 99/FY2026-27 and 105/FY2026-27 (store 4, Rs 7,117.98)
0.7 s after supplier payment SP11 (F1).

The fix for that pushed each bill's full stored copy instead, read with get_doc and bumped to
version + 1 with a new updated_at. That opened a race: another device's edit of one of those
bills that landed at the same version with an older updated_at was overwritten by the stale
header (totals back to before the edit, lines still edited, the bill's due and the supplier's
due wrong), and an edit stamped before the push but arriving after it was skipped whole.

The server does not need the PC's copy. upsertSupplierPayment runs
cascadeSupplierAfterLedgerChange in the same transaction as the payment: it recomputes every
purchase's due, due_amount, total_due and both cleared flags from the ledger and bumps only
the bills whose figures changed. So the PC now pushes no purchase: no header can be blanked,
overwritten or skipped by a payment.

The store query client and the server client are patched; nothing reaches a server.
"""
from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from contextlib import ExitStack
from unittest import mock

from core import desktop_settings_service, online_mutation_queue  # noqa: E402

HEADER = ("purchase_no", "bill_number", "subtotal", "total_gst", "cgst", "sgst",
          "total_amount", "overall_discount", "rounding", "need_to_pay", "final_amount",
          "gst_calc_method", "fy_start_year", "fy_serial", "amount_paid_at_entry")


def _stored(pid, no, date_s, final, gst, version=1, updated_at="2026-09-01T04:00:00.000Z"):
    return {
        "id": pid, "local_id": pid, "purchase_no": no, "bill_number": f"INV-{pid}",
        "supplier_id": 279, "supplier_name": "S PHARMA", "purchase_date": date_s,
        "subtotal": round(final - gst, 2), "total_gst": gst, "cgst": gst / 2, "sgst": gst / 2,
        "total_amount": final, "overall_discount": 0, "rounding": 0.02, "need_to_pay": final,
        "final_amount": final, "gst_calc_method": "discount_after_gst",
        "fy_start_year": 2026, "fy_serial": int(no.split("/")[0]),
        "amount_paid_at_entry": 0, "cash_paid_at_entry": 0, "online_paid_at_entry": 0,
        "due_amount": final, "total_due": final, "account_cleared": 0, "bill_cleared": 0,
        "version": version, "updated_at": updated_at, "client_uuid": f"cu-{pid}",
        "items": [{"medicine_id": 1, "qty": 1, "rate": final, "item_amount": final}],
    }


def _store():
    return {
        2979: _stored(2979, "64/FY2026-27", "2026-08-10", 2392.0, 113.9),
        3014: _stored(3014, "99/FY2026-27", "2026-08-28", 3389.98, 161.42),
        3020: _stored(3020, "105/FY2026-27", "2026-09-03", 3728.0, 177.52),
    }


def _listed(store):
    # What the purchases list sends: a handful of columns, no header money.
    return [
        {k: row[k] for k in ("id", "supplier_id", "supplier_name", "purchase_date",
                             "final_amount", "amount_paid_at_entry", "due_amount")}
        for row in store.values()
    ]


def _accept(existing, incoming):
    """server-live upsertHelper.shouldAcceptIncoming."""
    if not existing:
        return True
    ev, iv = int(existing.get("version") or 1), int(incoming.get("version") or 1)
    if iv != ev:
        return iv > ev
    return str(incoming.get("updated_at") or "") > str(existing.get("updated_at") or "")


class _Payment(unittest.TestCase):
    PAYMENTS = [{"id": 11, "supplier_id": 279, "amount": 2392.0}]

    def setUp(self):
        folder = tempfile.mkdtemp(prefix="fifo_header_")
        self.addCleanup(shutil.rmtree, folder, True)
        self.server = _store()
        self.pushed: list[dict] = []
        self.purchase_reads: list[int] = []
        self.supplier_cache: list[dict] = []

        def get_doc(collection, local_id):
            if collection == "purchases":
                self.purchase_reads.append(int(local_id))
                held = self.server.get(int(local_id))
                return dict(held) if held else None
            return {"id": int(local_id), "name": "S PHARMA", "version": 2}

        def push(bundle):
            # The server's own last-write-wins rule, so a stale push does what it does live.
            for doc in bundle.get("purchases") or []:
                self.pushed.append(dict(doc))
                pid = int(doc["id"])
                if _accept(self.server.get(pid), doc):
                    merged = dict(self.server.get(pid) or {})
                    merged.update({k: v for k, v in doc.items() if v is not None})
                    self.server[pid] = merged
            return {}

        stack = ExitStack()
        self.addCleanup(stack.close)
        for patch in (
            mock.patch("core.store_query_client.list_purchases",
                       side_effect=lambda **kw: {"rows": [dict(r) for r in _listed(self.server)]}),
            mock.patch("core.store_query_client.list_supplier_payments",
                       side_effect=lambda **kw: {"rows": [dict(p) for p in self.PAYMENTS]}),
            mock.patch("core.store_query_client.list_purchase_returns", return_value={"rows": []}),
            mock.patch("core.server_crud.get_doc", side_effect=get_doc),
            mock.patch("core.server_crud.push_bundle", side_effect=push),
            mock.patch("core.server_crud._device_id", return_value="dev"),
            mock.patch("core.store_live_refresh.emit", return_value=None),
            mock.patch("core.online_catalog.patch_supplier_cache",
                       side_effect=lambda doc: self.supplier_cache.append(dict(doc))),
            mock.patch.object(online_mutation_queue, "_queue_path",
                              return_value=os.path.join(folder, "store.json")),
        ):
            stack.enter_context(patch)

    def pay(self):
        desktop_settings_service._fifo_clear_supplier_purchases_online(279, "S PHARMA")


class ASupplierPaymentsBillPushes(_Payment):
    def test_no_purchase_document_is_pushed(self):
        self.pay()
        self.assertEqual(self.pushed, [], "a supplier payment pushed purchase documents")

    def test_no_bill_is_read_back_to_be_pushed(self):
        # One get_doc per bill of the supplier was the read side of the race window.
        self.pay()
        self.assertEqual(self.purchase_reads, [])

    def test_every_stored_header_is_left_exactly_as_it_was(self):
        before = _store()
        self.pay()
        for pid, held in before.items():
            for key in HEADER + ("version", "updated_at"):
                self.assertEqual(self.server[pid].get(key), held[key],
                                 f"purchase {pid} {key} changed")


class AnEditThatLandsDuringThePayment(_Payment):
    """The race the payment used to lose, with the server's own accept rule."""

    def test_an_edit_at_the_same_version_with_an_older_stamp_is_not_overwritten(self):
        # Another device's edit (stored v1 -> v2, stamped before the payment's own write)
        # reaches the server while the payment is being worked out: right after the payment
        # has read the bill, or -- when nothing is read -- before the payment finishes.
        edited = dict(self.server[3020], subtotal=3100.0, total_gst=155.0, total_amount=3255.0,
                      final_amount=3255.0, due_amount=3255.0, total_due=3255.0,
                      version=2, updated_at="2026-09-14T04:26:40.383Z")
        landed = []

        def get_doc(collection, local_id):
            if collection == "purchases":
                self.purchase_reads.append(int(local_id))
                held = dict(self.server[int(local_id)])
                if int(local_id) == 3020 and not landed:
                    self.server[3020] = dict(edited)
                    landed.append(True)
                return held
            return {"id": int(local_id), "name": "S PHARMA", "version": 2}

        with mock.patch("core.server_crud.get_doc", side_effect=get_doc):
            self.pay()
        if not landed:
            self.server[3020] = dict(edited)
        for key in ("subtotal", "total_gst", "total_amount", "final_amount"):
            self.assertEqual(self.server[3020][key], edited[key],
                             f"the payment put back the stale {key}")

    def test_an_edit_stamped_before_the_payment_but_arriving_after_is_not_skipped(self):
        self.pay()
        late = dict(self.server[3014], subtotal=3000.0, total_amount=3161.42,
                    final_amount=3161.42, version=2, updated_at="2026-09-14T04:26:40.000Z")
        accepted = _accept(self.server[3014], late)
        self.assertTrue(accepted, "the payment had moved the bill past an edit made before it")


class TheSuppliersOwnFiguresAfterPart_Full_AndOverPayment(_Payment):
    def cached(self):
        self.assertTrue(self.supplier_cache, "the supplier's own figure was not refreshed")
        last = self.supplier_cache[-1]
        return round(float(last["total_due"]), 2), round(float(last["total_credit"]), 2)

    def test_part_payment(self):
        self.PAYMENTS = [{"id": 11, "supplier_id": 279, "amount": 2392.0}]
        self.pay()
        self.assertEqual(self.cached(), (7117.98, 0.0))

    def test_full_payment(self):
        self.PAYMENTS = [{"id": 11, "supplier_id": 279, "amount": 9509.98}]
        self.pay()
        self.assertEqual(self.cached(), (0.0, 0.0))

    def test_over_payment(self):
        self.PAYMENTS = [{"id": 11, "supplier_id": 279, "amount": 10000.0}]
        self.pay()
        self.assertEqual(self.cached(), (0.0, 490.02))


class TheQueueFlushOfASupplierPayment(_Payment):
    """The only caller: the supplier_payments row the payment screen enqueues."""

    def test_the_flush_sends_the_payment_and_no_purchase(self):
        sent = []
        bundles = []

        def push(bundle):
            bundles.append({k: len(v) for k, v in bundle.items() if isinstance(v, list)})
            return {}

        row = {
            "collection": "supplier_payments", "op": "upsert", "local_id": 12,
            "payload": {"id": 12, "local_id": 12, "supplier_id": 279,
                        "supplier_name": "S PHARMA", "amount": 2392.0, "mode": "Cash",
                        "payment_date": "2026-09-14", "due_before": 9509.98,
                        "due_after": 7117.98, "_fifo": "supplier",
                        "_suppliers": [{"id": 279, "total_due": 7117.98, "total_credit": 0}]},
        }
        with mock.patch("core.sync_prefs.is_online_mode", return_value=True), \
                mock.patch("core.server_crud.upsert_payment_online",
                           side_effect=lambda col, doc: sent.append((col, doc)) or 12), \
                mock.patch("core.server_crud.push_bundle", side_effect=push):
            online_mutation_queue._flush_one(row)
        self.assertEqual([c for c, _ in sent], ["supplier_payments"])
        self.assertFalse(any("purchases" in b for b in bundles),
                         f"the flush pushed purchases: {bundles}")


if __name__ == "__main__":
    unittest.main()
