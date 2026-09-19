"""Refresh open list pages after local SQLite writes (sales, purchases, stock)."""
from __future__ import annotations

from typing import Optional


def _resolve_app(parent_widget=None):
    try:
        if parent_widget is not None:
            root = parent_widget.winfo_toplevel()
            return getattr(root, "_app_instance", None) or getattr(root, "_main_app", None)
    except Exception:
        pass
    return _APP_REF


_APP_REF = None


def register_app(app) -> None:
    """Called once from main.VeterinaryManagementSystem.__init__."""
    global _APP_REF
    _APP_REF = app


def refresh_open_pages(
    parent_widget=None,
    *,
    sales_history: bool = False,
    purchase_history: bool = False,
    inventory: bool = False,
    billing_medicines: bool = False,
    purchase_medicines: bool = False,
    alert_monitoring: bool = False,
    home: bool = False,
) -> None:
    """Reload cached pages that are already open — instant UI update after save."""
    app = _resolve_app(parent_widget)
    if app is None:
        return

    def _do():
        try:
            if sales_history:
                page = getattr(app, "_sales_history_page", None)
                if page is not None and hasattr(page, "load_sales_history"):
                    page.load_sales_history()
            if purchase_history:
                page = getattr(app, "_purchase_history_page", None)
                if page is not None and hasattr(page, "load_purchase_history"):
                    page.load_purchase_history()
            if inventory:
                page = getattr(app, "_inventory_page", None)
                if page is not None and hasattr(page, "load_inventory"):
                    page.load_inventory()
            if billing_medicines:
                page = getattr(app, "_billing_page", None)
                if page is not None:
                    combo = getattr(page, "medicine_combo", None)
                    if combo is not None and hasattr(combo, "load_medicine_names"):
                        combo.load_medicine_names()
                    elif hasattr(page, "refresh_medicines"):
                        page.refresh_medicines()
            if purchase_medicines:
                page = getattr(app, "_purchase_page", None)
                if page is not None and hasattr(page, "refresh_medicines"):
                    page.refresh_medicines()
            if alert_monitoring:
                settings = getattr(app, "_settings_page", None)
                alerts = getattr(settings, "_alerts", None) if settings is not None else None
                if alerts is not None and hasattr(alerts, "refresh"):
                    alerts.refresh()
            if home and getattr(app, "_home_built", False) and hasattr(app, "_refresh_home_stats"):
                app._refresh_home_stats()
            settings = getattr(app, "_settings_page", None)
            if settings is not None:
                try:
                    reorder = settings._reorder
                    page = getattr(reorder, "_page", None)
                    if page is not None and hasattr(page, "load_pending"):
                        page.load_pending()
                except Exception:
                    pass
        except Exception:
            pass

    try:
        root = getattr(app, "root", None)
        if root is not None:
            root.after(0, _do)
        else:
            _do()
    except Exception:
        _do()


def refresh_after_sale(parent_widget=None) -> None:
    # Online: avoid reloading Inventory + billing medicine list on every save
    # (stock already adjusted on server; catalog cache is updated lazily).
    try:
        from core.sync_prefs import is_online_mode

        if is_online_mode():
            refresh_open_pages(
                parent_widget,
                sales_history=True,
                inventory=False,
                billing_medicines=False,
                alert_monitoring=False,
                home=True,
            )
            return
    except Exception:
        pass
    refresh_open_pages(
        parent_widget,
        sales_history=True,
        inventory=True,
        billing_medicines=True,
        alert_monitoring=True,
        home=True,
    )


def refresh_after_purchase(parent_widget=None) -> None:
    # Online: skip full inventory/medicine list reload on every save (same as sales).
    try:
        from core.sync_prefs import is_online_mode

        if is_online_mode():
            refresh_open_pages(
                parent_widget,
                purchase_history=True,
                inventory=False,
                purchase_medicines=False,
                alert_monitoring=False,
                home=True,
            )
            return
    except Exception:
        pass
    refresh_open_pages(
        parent_widget,
        purchase_history=True,
        inventory=True,
        purchase_medicines=True,
        alert_monitoring=True,
        home=True,
    )