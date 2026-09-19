"""Voice export — opens the same on-screen dialogs as Ctrl+E / Export buttons."""
from __future__ import annotations

from core.voice.voice_log import voice_log


def _database_tab(app, *, open_export_section: bool = False):
    if open_export_section:
        try:
            app.nav_click(
                lambda: app.open_settings("Management", management_sub="export"),
                "Settings",
            )
        except Exception as exc:
            voice_log(f"Open export section failed: {exc}")
    page = getattr(app, "_settings_page", None)
    if page is None:
        try:
            page = app._ensure_settings_page()
        except Exception:
            return None
    return getattr(page, "_database", None)


def _ensure_sales_history(app):
    page = getattr(app, "_sales_history_page", None)
    if page is not None:
        return page
    try:
        app.nav_click(app.open_sales_history, "Sales History")
    except Exception:
        pass
    return getattr(app, "_sales_history_page", None)


def _ensure_purchase_history(app):
    page = getattr(app, "_purchase_history_page", None)
    if page is not None:
        return page
    try:
        app.nav_click(app.open_purchase_history, "Purchase History")
    except Exception:
        pass
    return getattr(app, "_purchase_history_page", None)


def _ensure_inventory(app):
    page = getattr(app, "_inventory_page", None)
    if page is not None:
        return page
    try:
        app.nav_click(app.open_inventory, "Inventory")
    except Exception:
        pass
    return getattr(app, "_inventory_page", None)


def open_export_menu(app) -> tuple[bool, str]:
    """Mirror Ctrl+E — show export option dialog on screen."""
    nav = getattr(app, "active_nav", None) or ""

    if nav == "🏠 Home":
        hb = getattr(app, "_home_keyboard_bindings", None)
        if hb is not None and getattr(hb, "on_ctrl_e", None):
            hb.on_ctrl_e()
            return True, ""

    page_map = {
        "Inventory": ("_inventory_page", "_export_menu"),
        "Sales History": ("_sales_history_page", "_export_menu"),
        "Purchase History": ("_purchase_history_page", "_export_menu"),
    }
    spec = page_map.get(nav)
    if spec:
        attr, method = spec
        page = getattr(app, attr, None)
        if page is not None and hasattr(page, method):
            getattr(page, method)()
            return True, ""

    if nav in ("Contacts", "Customers"):
        for attr in ("_customers_page", "_contacts_page"):
            page = getattr(app, attr, None)
            if page is not None and hasattr(page, "_export_menu"):
                page._export_menu()
                return True, ""

    db = _database_tab(app, open_export_section=True)
    if db is not None:
        from core.export_manager import show_export_option_dialog
        show_export_option_dialog(
            app.root,
            "Export Data",
            [
                ("Export Sales", db.export_sales),
                ("Export Purchases", db.export_purchases),
                ("Export Inventory", db.export_inventory),
                ("Export All", db.export_all),
            ],
            width=420,
        )
        return True, ""

    from core.export_manager import show_export_option_dialog
    from core.voice.voice_export_flow import reports_for_app

    reports = reports_for_app(app)
    if not reports:
        return False, "No export available on this page. Open Sales History, Inventory, or Home first."

    options = []
    for report in reports:
        rid = report.get("id", "")
        label = report.get("label", rid)
        options.append((label, lambda r=rid: run_export_action(app, r)))

    show_export_option_dialog(app.root, "Export Reports", options, width=400, height=380)
    return True, ""


def run_export_action(app, action_id: str) -> tuple[bool, str]:
    """Run one export — uses on-screen dialogs; voice hints speak when each opens."""
    action_id = (action_id or "").strip()

    if action_id in ("export_menu", "export_generic"):
        return open_export_menu(app)

    if action_id == "export_all_data":
        db = _database_tab(app, open_export_section=True)
        if db is None:
            return False, "Export panel not available."
        db.export_all()
        return True, ""

    mgmt = {
        "export_sales_data": ("export_sales", "Export Sales"),
        "export_purchases_data": ("export_purchases", "Export Purchases"),
        "export_inventory_data": ("export_inventory", "Export Inventory"),
    }
    if action_id in mgmt:
        method_name, label = mgmt[action_id]
        db = _database_tab(app, open_export_section=True)
        if db is None:
            return False, "Export panel not available."
        method = getattr(db, method_name, None)
        if not callable(method):
            return False, f"{label} is not available."
        method()
        return True, ""

    page = _ensure_sales_history(app)
    if page is not None and action_id in (
        "current_view",
        "export_current_view",
        "sales_register",
        "export_sales_register",
        "monthly_summary",
        "export_monthly_summary",
        "daily_summary",
        "export_daily_summary",
        "customer_due",
        "export_customer_due",
        "doctor_sales",
        "export_doctor_sales",
        "payment_mode",
        "export_payment_mode",
        "schedule_report",
        "export_schedule_report",
    ):
        return _run_sales_history_export(page, action_id)

    page = _ensure_purchase_history(app)
    if page is not None:
        purchase_map = {
            "current_view": page._export_current_view,
            "export_current_view": page._export_current_view,
            "purchase_register": page._export_purchase_register,
            "monthly_summary": page._export_monthly_summary,
            "supplier_due": page._export_supplier_due,
            "gst_purchase": page._export_gst_purchase,
        }
        fn = purchase_map.get(action_id)
        if fn is not None:
            fn()
            return True, ""

    page = _ensure_inventory(app)
    if page is not None:
        inv_map = {
            "current_view": page._export_current_view,
            "export_current_view": page._export_current_view,
            "stock_statement": page._export_stock_statement,
            "near_expiry": page._export_near_expiry,
            "expired_stock": page._export_expired,
            "schedule_stock": page._export_schedule_stock,
        }
        fn = inv_map.get(action_id)
        if fn is not None:
            fn()
            return True, ""

    if action_id in ("current_view", "export_current_view"):
        return False, "Open Sales History, Purchase History, or Inventory first."

    return False, f"Export '{action_id}' is not available on this page."


def _run_sales_history_export(page, action_id: str) -> tuple[bool, str]:
    from ui.sales import sales_history_exports as exp

    parent = page.parent
    cursor = page.cursor
    fd = lambda: page.from_date.get().strip()
    td = lambda: page.to_date.get().strip()
    sf = lambda: page.schedule_filter.get()

    if action_id in ("current_view", "export_current_view"):
        page._export_current_view()
        return True, ""

    if action_id in ("schedule_report", "export_schedule_report"):
        exp.export_schedule_report(parent, cursor, fd, td, sf)
        return True, ""

    runners = {
        "sales_register": lambda: exp.export_sales_register(parent, cursor, fd, td),
        "export_sales_register": lambda: exp.export_sales_register(parent, cursor, fd, td),
        "monthly_summary": lambda: exp.export_monthly_summary(parent, cursor, fd, td),
        "export_monthly_summary": lambda: exp.export_monthly_summary(parent, cursor, fd, td),
        "daily_summary": lambda: exp.export_daily_summary(parent, cursor, fd, td),
        "export_daily_summary": lambda: exp.export_daily_summary(parent, cursor, fd, td),
        "customer_due": lambda: exp.export_customer_due(parent, cursor),
        "export_customer_due": lambda: exp.export_customer_due(parent, cursor),
        "doctor_sales": lambda: exp.export_doctor_sales(parent, cursor, fd, td),
        "export_doctor_sales": lambda: exp.export_doctor_sales(parent, cursor, fd, td),
        "payment_mode": lambda: exp.export_payment_mode(parent, cursor, fd, td),
        "export_payment_mode": lambda: exp.export_payment_mode(parent, cursor, fd, td),
    }
    runner = runners.get(action_id)
    if runner is None:
        return False, "Unknown sales export."
    runner()
    return True, ""
