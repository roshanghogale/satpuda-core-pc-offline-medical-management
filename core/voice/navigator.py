"""Bind screen registry entries to app navigation actions."""

from core.voice.screen_registry import SCREEN_ENTRIES


def build_screen_actions(app):
    """Create {screen_id: callable} map for the running application."""
    actions = {}
    for entry in SCREEN_ENTRIES:
        screen_id = entry["id"]
        actions[screen_id] = lambda e=entry: _execute_nav(app, e)
    return actions


def _settings_open_kwargs(entry):
    """Map registry nav entry to open_settings() keyword arguments."""
    nav = entry.get("nav") or {}
    tab = nav.get("tab")
    section = nav.get("section")
    kwargs = {"select_tab": tab}
    if not section:
        return kwargs

    section_attr = nav.get("section_attr")
    if section_attr == "_payment":
        kwargs["payment_sub"] = section
    elif section_attr == "_ledger":
        kwargs["ledger_sub"] = section
    elif section_attr == "_database":
        kwargs["management_sub"] = section
    elif section_attr == "_import":
        kwargs["import_sub"] = section
    elif section_attr == "_pharmacy":
        kwargs["pharmacy_sub"] = section
    elif section_attr == "_layout":
        kwargs["layout_sub"] = section
    elif section_attr == "_sales":
        kwargs["sales_billing_sub"] = section
    elif section_attr == "_alerts":
        kwargs["alerts_sub"] = section
    elif section_attr == "_reorder":
        kwargs["reorder_sub"] = section
    return kwargs


def _execute_nav(app, entry):
    nav = entry.get("nav") or {}
    nav_type = nav.get("type")

    if nav_type == "main":
        method_name = nav.get("method")
        nav_text = nav.get("nav_text")
        method = getattr(app, method_name, None)
        if not callable(method):
            return False
        if nav_text:
            app.nav_click(method, nav_text)
        else:
            method()
        return True

    if nav_type == "returns":
        kind = nav.get("kind", "sales")
        nav_text = nav.get("nav_text", "Returns")
        app.nav_click(app.open_returns, nav_text)

        def _show_return():
            show_fn = getattr(app, "_returns_show", None)
            if callable(show_fn):
                show_fn(kind)

        app.root.after(120, _show_return)
        return True

    if nav_type == "payment":
        kind = nav.get("kind", "supplier")
        nav_text = nav.get("nav_text", "Payment")
        app.nav_click(lambda: app.open_payment(kind), nav_text)
        return True

    if nav_type == "contacts":
        subtab = nav.get("subtab", "Customers")
        app.nav_click(lambda: app.open_contacts(subtab), "Settings")
        return True

    if nav_type == "settings_tab":
        kwargs = _settings_open_kwargs(entry)

        def _open_settings():
            app.open_settings(**kwargs)

        app.nav_click(_open_settings, "Settings")
        return True

    return False


def open_screen(app, entry) -> bool:
    return _execute_nav(app, entry)
