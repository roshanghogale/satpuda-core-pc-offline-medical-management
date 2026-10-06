"""Print All on a dot matrix prints every bill on its own, with a printer reset in between.

The shop asked for exactly this: each bill printed completely, as its own Print would; after it
has finished, the printer's margin and print position reset; then the next bill from its proper
starting place -- never all the bills as one continuous document. A5 / A4 "two / four per sheet"
joined bills into one PDF, which on a dot matrix is one long run of paper.

Nothing here reaches a printer: the single-bill print, the queue wait and the reset are stubbed
and only the order of calls is checked.
"""
from __future__ import annotations

import unittest
from unittest import mock

from core import history_print
from core.printer_manager import PrinterManager


class _Calls:
    def __init__(self, fail_on: int = 0, stuck_after: int = 0):
        self.log: list[tuple] = []
        self.fail_on = fail_on
        self.stuck_after = stuck_after

    def bill(self, conn, sale_id, slot, *, db_path=None, settings_override=None,
             fallback_to_html=True):
        self.log.append(("bill", sale_id, slot, db_path, settings_override, fallback_to_html))
        if sale_id == self.fail_on:
            raise RuntimeError("printer offline")
        return "", None

    def wait(self, printer, *, timeout=90.0, poll=0.4):
        self.log.append(("wait", printer))
        last_bill = [c for c in self.log if c[0] == "bill"][-1][1]
        return "job stuck: out of paper" if last_bill == self.stuck_after else ""

    def reset(self, printer):
        self.log.append(("reset", printer))


class PrintAllOnDotMatrix(unittest.TestCase):
    def run_batch(self, calls: _Calls, ids, *, paper="A6", gdi=False):
        with mock.patch.object(PrinterManager, "is_dot_matrix_mode", return_value=True), \
             mock.patch.object(PrinterManager, "get_printer_for_slot", return_value="LX-310"), \
             mock.patch.object(PrinterManager, "resolve_dot_matrix_printer",
                               side_effect=lambda p=None: "EPSON LX-310 ESC/P"), \
             mock.patch.object(PrinterManager, "should_use_dot_matrix_gdi", return_value=gdi), \
             mock.patch.object(PrinterManager, "wait_for_print_queue", side_effect=calls.wait), \
             mock.patch.object(PrinterManager, "reset_dot_matrix", side_effect=calls.reset), \
             mock.patch("core.bill_output._print_bill_dot_matrix_with_slot", side_effect=calls.bill), \
             mock.patch("bill_templates.classic.render_classic_bill_html_multi",
                        side_effect=AssertionError("bills joined into one document")):
            return history_print.print_bills_batch(
                None, ids, paper=paper, slot=2, db_path="store.db"
            )

    def test_each_bill_then_wait_then_reset_in_order(self):
        calls = _Calls()
        printed, failures = self.run_batch(calls, [11, 12, 13])
        self.assertEqual((printed, failures), (3, []))
        self.assertEqual(
            [(c[0], c[1]) for c in calls.log],
            [("bill", 11), ("wait", "EPSON LX-310 ESC/P"), ("reset", "EPSON LX-310 ESC/P"),
             ("bill", 12), ("wait", "EPSON LX-310 ESC/P"), ("reset", "EPSON LX-310 ESC/P"),
             ("bill", 13), ("wait", "EPSON LX-310 ESC/P"), ("reset", "EPSON LX-310 ESC/P")],
        )

    def test_every_bill_is_its_own_print_with_its_own_settings(self):
        calls = _Calls()
        self.run_batch(calls, [11, 12])
        for c in calls.log:
            if c[0] == "bill":
                _, _, slot, db_path, override, fallback = c
                self.assertEqual(slot, 2)              # Print Sales 2, as F8
                self.assertEqual(db_path, "store.db")
                self.assertIsNone(override)            # the slot's own saved layout
                self.assertFalse(fallback)             # no popup per bill

    def test_a5_and_a4_are_still_one_bill_at_a_time(self):
        for paper in ("A5", "A4"):
            calls = _Calls()
            printed, failures = self.run_batch(calls, [1, 2, 3, 4], paper=paper)
            self.assertEqual((printed, failures), (4, []), paper)
            self.assertEqual([c[1] for c in calls.log if c[0] == "bill"], [1, 2, 3, 4])

    def test_a_failed_bill_stops_the_run_and_names_the_rest(self):
        calls = _Calls(fail_on=12)
        printed, failures = self.run_batch(calls, [11, 12, 13, 14])
        self.assertEqual(printed, 1)
        self.assertIn("Sale 12", failures[0])
        self.assertIn("2 bill(s) after it were not printed", failures[1])
        self.assertNotIn(13, [c[1] for c in calls.log if c[0] == "bill"])

    def test_a_stuck_queue_stops_before_the_next_bill(self):
        calls = _Calls(stuck_after=11)
        printed, failures = self.run_batch(calls, [11, 12])
        self.assertEqual(printed, 1)
        self.assertIn("out of paper", failures[0])
        self.assertEqual([c[0] for c in calls.log], ["bill", "wait"])

    def test_a_windows_driver_queue_is_not_sent_escp(self):
        calls = _Calls()
        printed, _ = self.run_batch(calls, [11, 12], gdi=True)
        self.assertEqual(printed, 2)
        self.assertNotIn("reset", [c[0] for c in calls.log])

    def test_the_reset_is_esc_at_and_nothing_else(self):
        sent = []
        with mock.patch.object(PrinterManager, "print_raw_escp",
                               side_effect=lambda data, printer, copies=1: sent.append(data)):
            PrinterManager.reset_dot_matrix("EPSON LX-310 ESC/P")
        self.assertEqual(sent, [b"\x1b@"])


class PrintAllOnANormalPrinterIsUnchanged(unittest.TestCase):
    def test_a5_still_puts_two_bills_on_a_sheet(self):
        with mock.patch.object(PrinterManager, "is_dot_matrix_mode", return_value=False), \
             mock.patch("core.history_print.print_bills_dot_matrix_one_by_one",
                        side_effect=AssertionError("dot matrix path on a normal printer")), \
             mock.patch("core.bill_output._load_sale_data",
                        return_value=("B1", object(), [])), \
             mock.patch("bill_templates.classic.render_classic_bill_html_multi",
                        return_value="<html></html>") as multi, \
             mock.patch("core.bill_output._try_pdf_via_browser", return_value=True), \
             mock.patch.object(PrinterManager, "print_pdf_silently"), \
             mock.patch.object(PrinterManager, "get_printer_for_slot", return_value="HP"), \
             mock.patch("core.history_print.time.sleep"):
            pages, failures = history_print.print_bills_batch(None, [1, 2, 3, 4], paper="A5")
        self.assertEqual((pages, failures), (2, []))
        self.assertEqual(multi.call_count, 2)


class WaitingForTheQueue(unittest.TestCase):
    """wait_for_print_queue against a stand-in for win32print."""

    def fake(self, queues):
        import types

        state = {"n": 0}

        def enum_jobs(handle, first, count, level):
            i = min(state["n"], len(queues) - 1)
            state["n"] += 1
            return queues[i]

        return types.SimpleNamespace(
            OpenPrinter=lambda name: "h", ClosePrinter=lambda h: None, EnumJobs=enum_jobs,
        )

    def wait(self, queues, timeout=5.0):
        import sys

        with mock.patch.dict(sys.modules, {"win32print": self.fake(queues)}),              mock.patch.object(PrinterManager, "is_windows", return_value=True),              mock.patch("core.printer_manager.time.sleep"):
            return PrinterManager.wait_for_print_queue("LX", timeout=timeout, poll=0)

    def test_returns_once_the_bill_has_left_the_queue(self):
        busy = [{"pDocument": "Satpuda Bill", "Status": 0}]
        self.assertEqual(self.wait([busy, busy, []]), "")

    def test_a_stuck_job_is_reported(self):
        stuck = [{"pDocument": "Satpuda Bill", "Status": 0x40}]
        self.assertIn("out of paper", self.wait([stuck]))

    def test_still_busy_at_the_timeout_is_reported(self):
        busy = [{"pDocument": "Satpuda Bill", "Status": 0}]
        with mock.patch("core.printer_manager.time.monotonic", side_effect=[0.0, 0.5, 2.0, 9.0]):
            self.assertIn("still busy", self.wait([busy], timeout=1.0))


if __name__ == "__main__":
    unittest.main()
