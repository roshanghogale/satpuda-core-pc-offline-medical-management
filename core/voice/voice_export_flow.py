"""Multi-step voice export wizard — list reports, schedules, formats."""
from __future__ import annotations

import re
import time

from core.voice.voice_log import voice_log

_FLOW = None
_FORMAT_OPTIONS = [
    ("CSV", "csv"),
    ("Excel", "xlsx"),
    ("PDF or HTML", "pdf"),
]


def get_export_flow():
    global _FLOW
    if _FLOW is None:
        _FLOW = VoiceExportFlow()
    return _FLOW


class VoiceExportFlow:
    def __init__(self):
        self.active = False
        self.step = ""
        self.until = 0.0
        self.reports: list[dict] = []
        self.selected: dict | None = None
        self.schedule_choice: dict | None = None
        self.export_format: str | None = None
        self.app = None

    def is_active(self) -> bool:
        return self.active and time.time() < self.until

    def cancel(self):
        self.active = False
        self.step = ""
        self.reports = []
        self.selected = None
        self.schedule_choice = None
        self.export_format = None
        self.app = None

    def _extend(self, seconds: float = 30.0):
        self.until = time.time() + seconds

    def start(self, app, reports: list[dict]):
        self.active = True
        self.app = app
        self.reports = reports
        self.selected = None
        self.schedule_choice = None
        self.export_format = None
        self.step = "pick_report"
        self._extend()
        labels = [r.get("label", r.get("id", "")) for r in reports]
        spoken = ". ".join(labels[:8])
        if len(labels) > 8:
            spoken += f". And {len(labels) - 8} more."
        return f"Which export? Options are: {spoken}"

    def _match_report(self, text: str) -> dict | None:
        t = " ".join((text or "").lower().split())
        for report in self.reports:
            rid = report.get("id", "")
            label = (report.get("label") or "").lower()
            for alias in report.get("aliases", []) + [label, rid.replace("_", " ")]:
                a = " ".join(alias.lower().split())
                if not a:
                    continue
                if t == a or a in t or t in a:
                    return report
        if t in ("all", "everything", "first", "one"):
            return self.reports[0] if self.reports else None
        return None

    def _parse_schedules(self, text: str) -> dict | None:
        t = " ".join((text or "").lower().split())
        if not t:
            return None
        if re.search(r"\b(all schedules|all|everything)\b", t):
            return {"mode": "all", "schedules": [], "label": "All Schedules"}
        if re.search(r"\b(non[- ]?scheduled|unscheduled|no schedule)\b", t):
            return {"mode": "non_scheduled", "schedules": [], "label": "Non-Scheduled"}
        from core.layout_config import get_configured_schedules
        schedules = get_configured_schedules()
        picked = []
        for sch in schedules:
            if re.search(rf"\b{re.escape(sch.lower())}\b", t):
                picked.append(sch)
        if picked:
            return {"mode": "selected", "schedules": picked, "label": ", ".join(picked)}
        return None

    def _parse_format(self, text: str) -> str | None:
        from core.voice.page_action_registry import parse_voice_export_format
        fmt = parse_voice_export_format(text)
        if fmt:
            return fmt
        t = " ".join((text or "").lower().split())
        if t in ("one", "first", "csv"):
            return "csv"
        if t in ("two", "second", "excel", "xlsx"):
            return "xlsx"
        if t in ("three", "third", "pdf", "html"):
            return "pdf"
        return None

    def _format_prompt(self) -> str:
        return "Choose format: CSV, Excel, or PDF."

    def handle(self, app, text: str) -> tuple[bool, str, dict | None]:
        """Returns (handled, spoken_message, export_params or None)."""
        if not self.is_active():
            return False, "", None

        self.app = app
        self._extend()
        t = (text or "").strip()
        low = t.lower()
        if low in ("cancel", "stop", "never mind", "abort"):
            self.cancel()
            return True, "Export cancelled.", None

        if self.step == "pick_report":
            report = self._match_report(t)
            if report is None:
                labels = ", ".join(r.get("label", "") for r in self.reports[:6])
                return True, f"Did not catch that. Say one of: {labels}", None
            self.selected = report
            fmt = self._parse_format(t)
            if fmt:
                self.export_format = fmt
            if report.get("id") == "schedule_report":
                self.step = "pick_schedule"
                from core.layout_config import get_configured_schedules
                sch = ", ".join(get_configured_schedules()[:12]) or "H, H1, X"
                return True, f"Which schedule? Say all, non scheduled, or names like {sch}.", None
            if self.export_format:
                self.active = False
                return True, "", {
                    "report_id": report["id"],
                    "export_format": self.export_format,
                    "schedule_choice": None,
                }
            self.step = "pick_format"
            return True, self._format_prompt(), None

        if self.step == "pick_schedule":
            choice = self._parse_schedules(t)
            if choice is None:
                return True, "Say all schedules, non scheduled, or schedule names like H or X.", None
            self.schedule_choice = choice
            fmt = self._parse_format(t)
            if fmt:
                self.export_format = fmt
                self.active = False
                return True, "", {
                    "report_id": self.selected["id"],
                    "export_format": fmt,
                    "schedule_choice": choice,
                }
            self.step = "pick_format"
            return True, self._format_prompt(), None

        if self.step == "pick_format":
            fmt = self._parse_format(t)
            if fmt is None:
                return True, self._format_prompt(), None
            self.active = False
            return True, "", {
                "report_id": (self.selected or {}).get("id"),
                "export_format": fmt,
                "schedule_choice": self.schedule_choice,
            }

        return False, "", None


def reports_for_app(app) -> list[dict]:
    """Export options for the current page context."""
    nav = getattr(app, "active_nav", None) or ""
    reports = []

    def _add(rid, label, aliases=()):
        reports.append({"id": rid, "label": label, "aliases": list(aliases)})

    if nav == "Sales History":
        _add("current_view", "Current View", ("current view", "filtered view", "this view"))
        _add("sales_register", "Sales Register", ("sales register", "all bills"))
        _add("monthly_summary", "Monthly Summary", ("monthly",))
        _add("daily_summary", "Daily Summary", ("daily",))
        _add("customer_due", "Customer Due", ("customer due", "dues"))
        _add("doctor_sales", "Doctor Sales", ("doctor",))
        _add("payment_mode", "Payment Mode", ("payment mode",))
        _add("schedule_report", "Schedule Report", ("schedule", "h schedule", "scheduled"))
    elif nav == "Purchase History":
        _add("current_view", "Current View", ("current view",))
        _add("purchase_register", "Purchase Register", ("register", "all purchases"))
        _add("monthly_summary", "Monthly Summary", ("monthly",))
        _add("supplier_due", "Supplier Due", ("supplier due",))
        _add("gst_purchase", "GST Purchase", ("gst",))
    elif nav == "Inventory":
        _add("current_view", "Current View", ("current view",))
        _add("stock_statement", "Stock Statement", ("stock",))
        _add("near_expiry", "Near Expiry", ("expiry", "near expiry"))
        _add("expired_stock", "Expired Stock", ("expired",))
        _add("schedule_stock", "Schedule Stock", ("schedule stock",))
    elif nav in ("Contacts", "Customers"):
        _add("current_view", "Current View", ("current view",))
        _add("customer_list", "Customer List", ("list",))
        _add("customer_due_list", "Customer Due List", ("due list",))
    else:
        _add("export_all_data", "Export All Data", ("all", "everything"))
        _add("export_sales_data", "Export Sales", ("sales",))
        _add("export_purchases_data", "Export Purchases", ("purchases",))
        _add("export_inventory_data", "Export Inventory", ("inventory", "stock"))

    return reports
