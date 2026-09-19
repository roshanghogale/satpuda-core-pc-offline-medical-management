"""Execute in-page voice actions — UI thread only for exports and dialogs."""
from __future__ import annotations

from core.voice.voice_log import voice_log

_ACTION_TO_REPORT = {
    "export_current_view": "export_current_view",
    "export_sales_register": "export_sales_register",
    "export_schedule_report": "export_schedule_report",
    "export_monthly_summary": "export_monthly_summary",
    "export_daily_summary": "export_daily_summary",
    "export_customer_due": "export_customer_due",
    "export_doctor_sales": "export_doctor_sales",
    "export_payment_mode": "export_payment_mode",
    "export_all_data": "export_all_data",
    "export_sales_data": "export_sales_data",
    "export_purchases_data": "export_purchases_data",
    "export_inventory_data": "export_inventory_data",
}

_EXPORT_ACTION_IDS = frozenset(_ACTION_TO_REPORT) | frozenset({
    "export_menu",
    "export_generic",
})


def _finish(app, on_complete, ok: bool, message: str, *, speak: bool = True):
    voice_log(f"Page action result: {message}")
    msg = (message or "").strip()
    if speak and msg:
        from core.voice.tts import speak_action_result
        app.root.after(0, lambda: speak_action_result(msg))
    if callable(on_complete):
        app.root.after(0, lambda: on_complete(ok, message))


def _settings_page(app):
    page = getattr(app, "_settings_page", None)
    if page is None:
        try:
            page = app._ensure_settings_page()
        except Exception:
            return None
    return page


def _database_tab(app):
    page = _settings_page(app)
    return getattr(page, "_database", None) if page else None


def _import_tab(app):
    page = _settings_page(app)
    return getattr(page, "_import", None) if page else None


def _run_export_ui(app, action_id: str, on_complete, params=None):
    from core.export_manager import set_voice_export_format
    from core.export_prefs import load_default_export_format
    from core.voice.export_ui import run_export_action
    from core.voice.voice_export_flow import get_export_flow

    from core.voice.voice_dialog import arm_voice_dialog_hints

    get_export_flow().cancel()
    params = params or {}
    fmt = params.get("export_format") or load_default_export_format()
    set_voice_export_format(fmt)
    arm_voice_dialog_hints()
    report_key = _ACTION_TO_REPORT.get(action_id, action_id)
    try:
        ok, msg = run_export_action(app, report_key)
        _finish(app, on_complete, ok, msg)
    except Exception as exc:
        _finish(app, on_complete, False, f"Export failed. {exc}")


def run_page_voice_action(app, action_id: str, params: dict | None = None, on_complete=None) -> bool:
    action_id = (action_id or "").strip()
    params = params or {}

    if action_id in _EXPORT_ACTION_IDS:
        app.root.after(0, lambda: _run_export_ui(app, action_id, on_complete, params))
        return True

    workers = {
        "save": _worker_save,
        "close_dialog": _worker_close_dialog,
        "cancel_dialog": _worker_close_dialog,
        "cancel_all_alerts": _worker_cancel_all,
        "open_web_purchase": _worker_open_web_purchase,
        "switch_store": _worker_switch_store,
        "close_app": _worker_close_app,
        "start_listening": _worker_start_listening,
        "stop_listening": _worker_stop_listening,
        "turn_off_mic": _worker_turn_off_mic,
        "apply_list_filter": _worker_apply_list_filter,
    }
    worker = workers.get(action_id)
    if worker is None:
        return False
    _main_thread_actions = frozenset({
        "close_dialog", "cancel_dialog", "cancel_all_alerts",
        "close_app", "start_listening", "stop_listening", "turn_off_mic",
        "apply_list_filter",
    })
    if action_id == "apply_list_filter":
        app.root.after(0, lambda: worker(app, on_complete, params))
        return True
    if action_id in _main_thread_actions:
        app.root.after(0, lambda: worker(app, on_complete))
        return True
    app.root.after(0, lambda: worker(app, on_complete))
    return True


def run_voice_export_params(app, export_params: dict, on_complete=None) -> bool:
    """Legacy flow completion — now opens UI export for the chosen report."""
    report_id = (export_params or {}).get("report_id") or "export_generic"
    action_id = report_id
    for k, v in _ACTION_TO_REPORT.items():
        if v == report_id:
            action_id = k
            break
    app.root.after(0, lambda: _run_export_ui(app, action_id, on_complete, export_params))
    return True


def _worker_save(app, on_complete):
    try:
        from core.keyboard_registry import KeyboardRegistry
        KeyboardRegistry._on_f5(None)
        _finish(app, on_complete, True, "Save command sent.")
    except Exception as exc:
        _finish(app, on_complete, False, f"Save failed. {exc}")


def _worker_close_dialog(app, on_complete):
    from core.dialog_escape import close_active_dialog
    if close_active_dialog(app.root):
        _finish(app, on_complete, True, "Dialog closed.")
    else:
        _finish(app, on_complete, False, "No dialog to close.")


def _worker_cancel_all(app, on_complete):
    from core.voice.dialog_voice import try_dialog_voice
    result = try_dialog_voice(app, "cancel all", allow_without_wake=True)
    if result:
        _finish(app, on_complete, True, result.get("message", "Cancelled."))
    else:
        _finish(app, on_complete, False, "Nothing to cancel.")


def _worker_open_web_purchase(app, on_complete):
    try:
        app.nav_click(
            lambda: app.open_settings("Import", import_sub="web"),
            "Settings",
        )
        tab = _import_tab(app)
        if tab is None or not hasattr(tab, "_open_web_purchase"):
            _finish(app, on_complete, False, "Web purchase entry is not available.")
            return
        tab._open_web_purchase()
        _finish(app, on_complete, True, "Opening web purchase entry in your browser.")
    except Exception as exc:
        _finish(app, on_complete, False, f"Web purchase failed. {exc}")


def _worker_close_app(app, on_complete):
    _finish(app, on_complete, True, "Closing application.")
    app.root.after(400, app._on_close)


def _sync_voice_ui(app):
    app.root.after(0, app._update_voice_nav_button)


def _worker_start_listening(app, on_complete):
    assistant = getattr(app, "_voice_assistant", None)
    if assistant is None:
        _finish(app, on_complete, False, "Voice assistant is not available.")
        return
    if assistant.enabled and not assistant.in_standby:
        _finish(app, on_complete, True, "Already listening.")
        return
    if assistant.start_listening():
        _sync_voice_ui(app)
        _finish(app, on_complete, True, "Microphone on. Listening for commands.")
    else:
        _finish(app, on_complete, False, "Could not turn on the microphone.")


def _worker_stop_listening(app, on_complete):
    assistant = getattr(app, "_voice_assistant", None)
    if assistant is None or not assistant.enabled:
        _finish(app, on_complete, False, "Microphone is off.")
        return
    if assistant.in_standby:
        _finish(app, on_complete, True, "Already paused. Say Hey Satpuda to listen.")
        return
    assistant.stop_listening()
    _sync_voice_ui(app)
    _finish(app, on_complete, True, "Listening paused.", speak=False)


def _worker_turn_off_mic(app, on_complete):
    assistant = getattr(app, "_voice_assistant", None)
    if assistant is None or not assistant.enabled:
        _finish(app, on_complete, True, "Microphone is already off.")
        return
    assistant.turn_off_mic()
    _sync_voice_ui(app)
    _finish(app, on_complete, True, "Microphone turned off.")


def _worker_apply_list_filter(app, on_complete, params=None):
    from core.voice.list_filter_voice import apply_list_filter

    ok, msg = apply_list_filter(app, params or {})
    _finish(app, on_complete, ok, msg)


def _worker_switch_store(app, on_complete):
    try:
        app.nav_click(
            lambda: app.open_settings("Management", management_sub="stores"),
            "Settings",
        )
        db = _database_tab(app)
        if db is None or not hasattr(db, "_switch_selected_store"):
            _finish(app, on_complete, False, "Store management is not available.")
            return
        db._switch_selected_store()
        _finish(app, on_complete, True, "Switching store. Confirm in the dialog on screen.")
    except Exception as exc:
        _finish(app, on_complete, False, f"Switch store failed. {exc}")
