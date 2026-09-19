"""Voice hints and dialog registration for on-screen export / schedule pickers."""

from __future__ import annotations

import threading

_voice_dialog_hints = False
_modal_lock = threading.Lock()
_modal_depth = 0


def arm_voice_dialog_hints() -> None:
    global _voice_dialog_hints
    _voice_dialog_hints = True


def consume_voice_dialog_hints() -> bool:
    global _voice_dialog_hints
    armed = _voice_dialog_hints
    _voice_dialog_hints = False
    return armed


def speak_voice_dialog_hint(text: str) -> None:
    if not text:
        return
    from core.voice.tts import speak
    speak(text)


def note_modal_open() -> None:
    """Called when a grab/modal dialog is shown — safe for voice thread to poll."""
    global _modal_depth
    with _modal_lock:
        _modal_depth += 1


def note_modal_close() -> None:
    global _modal_depth
    with _modal_lock:
        _modal_depth = max(0, _modal_depth - 1)


def is_voice_modal_open() -> bool:
    with _modal_lock:
        return _modal_depth > 0


def track_modal_dialog(dlg) -> None:
    """Increment modal depth until this dialog is destroyed."""
    if dlg is None or getattr(dlg, "_satpuda_modal_tracked", False):
        return
    dlg._satpuda_modal_tracked = True
    note_modal_open()

    def _on_destroy(event):
        if event.widget is dlg:
            note_modal_close()

    try:
        dlg.bind("<Destroy>", _on_destroy, add="+")
    except Exception:
        note_modal_close()


def register_schedule_dialog(
    dlg,
    *,
    mode,
    chk_vars: dict,
    schedules: list,
    on_export,
    on_select_all,
    on_clear,
    on_print=None,
) -> None:
    dlg._satpuda_voice_kind = "schedule_report"
    dlg._satpuda_voice_ctx = {
        "mode": mode,
        "chk_vars": chk_vars,
        "schedules": list(schedules),
        "on_export": on_export,
        "on_print": on_print or on_export,
        "on_select_all": on_select_all,
        "on_clear": on_clear,
    }


def register_export_option_dialog(dlg, lb, options: list) -> None:
    dlg._satpuda_voice_kind = "export_option"
    dlg._satpuda_voice_ctx = {
        "lb": lb,
        "options": [(str(label), fn) for label, fn in (options or [])],
    }


def register_export_format_dialog(dlg, *, fmt_var, on_export, on_cancel) -> None:
    dlg._satpuda_voice_kind = "export_format"
    dlg._satpuda_voice_ctx = {
        "fmt_var": fmt_var,
        "on_export": on_export,
        "on_cancel": on_cancel,
    }


def register_print_all_period_dialog(dlg, *, on_select_bills, on_cancel, paper_var) -> None:
    dlg._satpuda_voice_kind = "print_all_period"
    dlg._satpuda_voice_ctx = {
        "on_select_bills": on_select_bills,
        "on_cancel": on_cancel,
        "paper_var": paper_var,
    }


def register_print_all_select_dialog(
    dlg,
    *,
    on_print,
    on_select_all,
    on_clear,
    on_cancel,
) -> None:
    dlg._satpuda_voice_kind = "print_all_select"
    dlg._satpuda_voice_ctx = {
        "on_print": on_print,
        "on_select_all": on_select_all,
        "on_clear": on_clear,
        "on_cancel": on_cancel,
    }


def register_messagebox_dialog(dlg, *, buttons: list) -> None:
    """buttons: list of (label, callback) e.g. [('Yes', fn), ('No', fn)]."""
    dlg._satpuda_voice_kind = "messagebox"
    dlg._satpuda_voice_ctx = {"buttons": list(buttons or [])}
