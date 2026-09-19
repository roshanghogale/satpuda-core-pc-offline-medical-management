"""Eager imports so PyInstaller bundles every desktop API service module.

Handlers in core.desktop_api import many services lazily inside request
handlers; without this preload, folder builds can miss modules (Returns page
404/500 while Sales still works).
"""
from __future__ import annotations


def preload_desktop_services() -> None:
    imports = (
        "core.desktop_returns_service",
        "core.desktop_general_products_service",
        "core.desktop_alert_service",
        "core.desktop_startup_service",
        "core.desktop_file_import_service",
        "core.desktop_mobile_import_service",
        "core.desktop_login_service",
        "core.desktop_license_service",
        "core.alert_monitoring_service",
        "core.stock_disposal_service",
        "core.online_guard",
        "core.server_api",
        "core.server_live",
        "core.server_sync",
        "core.server_entity_sync",
        "core.sync_coordinator",
        "core.backup_manager",
    )
    for name in imports:
        try:
            __import__(name)
        except Exception:
            pass
