"""Execute Satpuda voice actions — real work + spoken results."""
from __future__ import annotations

import threading

from core.voice.voice_log import voice_log


def _database_tab(app):
    page = getattr(app, "_settings_page", None)
    if page is None:
        try:
            page = app._ensure_settings_page()
        except Exception:
            return None
    return getattr(page, "_database", None)


def _open_management(app, section: str):
    try:
        app.nav_click(
            lambda: app.open_settings("Management", management_sub=section),
            "Settings",
        )
    except Exception as exc:
        voice_log(f"Open management/{section} failed: {exc}", level="error")


def _speak_result(message: str):
    from core.voice.tts import speak_action_result
    speak_action_result(message)


def _section_for_action(action_id: str) -> str:
    if action_id in (
        "check_updates", "install_update", "open_installer", "reinstall_installer",
    ):
        return "updates"
    if action_id in ("backup_now", "sync_from_drive"):
        return "backup"
    return "updates"


def _ui_updates_idle(app) -> None:
    db = _database_tab(app)
    tab = getattr(db, "_updates_tab", None) if db else None
    if tab is not None:
        app.root.after(0, tab._set_busy, False)


def _ui_action_started(app, action_id: str) -> None:
    """Mirror the on-screen status shown when using Settings buttons."""
    db = _database_tab(app)
    if db is None:
        return

    def _apply():
        try:
            if action_id == "backup_now":
                db._backup_status_var.set("Backing up...")
            elif action_id == "check_updates":
                tab = getattr(db, "_updates_tab", None)
                if tab is not None:
                    tab._set_busy(True)
                    tab._status_var.set("Checking GitHub for the latest release…")
            elif action_id == "sync_from_drive":
                db._backup_status_var.set("Syncing from Drive...")
            elif action_id == "install_update":
                tab = getattr(db, "_updates_tab", None)
                if tab is not None:
                    tab._set_busy(True)
                    tab._status_var.set("Downloading update…")
            elif action_id == "open_installer":
                tab = getattr(db, "_updates_tab", None)
                if tab is not None:
                    tab._status_var.set("Opening installer…")
            elif action_id == "reinstall_installer":
                tab = getattr(db, "_updates_tab", None)
                if tab is not None:
                    tab._status_var.set("Reinstall installer…")
        except Exception as exc:
            voice_log(f"Action UI start failed: {exc}")

    app.root.after(0, _apply)


def run_voice_action(app, action_id: str, on_complete=None) -> bool:
    action_id = (action_id or "").strip()
    if not action_id:
        return False

    _open_management(app, _section_for_action(action_id))
    _ui_action_started(app, action_id)

    workers = {
        "check_updates": _worker_check_updates,
        "install_update": _worker_install_update,
        "open_installer": _worker_open_installer,
        "reinstall_installer": _worker_reinstall_installer,
        "backup_now": _worker_backup_now,
        "sync_from_drive": _worker_sync_from_drive,
    }
    worker = workers.get(action_id)
    if worker is None:
        voice_log(f"Unknown voice action: {action_id}")
        return False

    threading.Thread(
        target=worker,
        args=(app, on_complete),
        daemon=True,
        name=f"SatpudaAction-{action_id}",
    ).start()
    return True


def _finish(app, on_complete, ok: bool, message: str):
    voice_log(f"Voice action result: {message}")
    app.root.after(0, lambda: _speak_result(message))
    if callable(on_complete):
        app.root.after(0, lambda: on_complete(ok, message))


def _backup_voice_message(log_line: str) -> tuple[bool, str]:
    last = (log_line or "").strip()
    low = last.lower()
    if "backup ok" in low:
        return True, "Backup completed successfully."
    if "no internet" in low:
        return False, "Backup failed. No internet connection."
    if "backup_config.dat missing" in low:
        return False, "Backup is not configured yet."
    if "backup_creds.dat missing" in low:
        return False, "Backup credentials are missing or invalid."
    if "backup drive error" in low:
        if "timed out" in low or "10054" in low:
            return False, "Drive backup timed out. Try again on a stable connection."
        if "403" in last:
            return False, "No access to the Google Drive backup folder."
        if "404" in last or "not found" in low:
            return False, "Google Drive backup folder was not found."
        return False, "Google Drive backup failed. Check backup log for details."
    if "backup failed" in low:
        return False, "Backup failed. Check backup log for details."
    return True, "Backup finished. Check backup log if unsure."


def _update_voice_message(info) -> tuple[bool, str]:
    if info is None:
        return False, "Update check failed. Could not reach GitHub."
    if info.error and not info.available:
        err = (info.error or "").strip()
        if "internet" in err.lower() or "reach github" in err.lower():
            return False, "Could not check updates. Check your internet connection."
        if "rate limit" in err.lower():
            return False, "GitHub rate limit reached. Try again in a few minutes."
        return False, err[:120]
    if info.available:
        ver = info.latest_version or "new"
        if info.has_download:
            return True, (
                f"Update available. Version {ver} is ready. "
                f"Say download the update, or open installer."
            )
        return True, (
            f"Update {ver} is available, but no installer package was found. "
            f"Say reinstall installer, then try again."
        )
    current = info.current_version or "your version"
    return True, f"You are up to date. Version {current} is the latest."


def _sync_updates_ui(app, info):
    db = _database_tab(app)
    tab = getattr(db, "_updates_tab", None) if db else None
    if tab is None or info is None:
        return

    def _apply():
        try:
            from core.github_updater import format_release_summary
            if info.error and not info.available:
                tab._status_var.set(info.error)
                return
            if info.available:
                tab._pending_info = info
                tab._status_var.set(
                    f"Update available: v{info.latest_version} "
                    f"(you have v{info.current_version})"
                )
                tab._set_notes(format_release_summary(info))
                if info.has_download:
                    tab._download_btn.config(state="normal")
            else:
                tab._status_var.set(
                    f"Connected to GitHub — v{info.current_version} is the latest release."
                )
                notes = format_release_summary(info) if info.latest_version else ""
                tab._set_notes(notes or "No newer release found on GitHub.")
        except Exception as exc:
            voice_log(f"Updates UI sync failed: {exc}")

    app.root.after(0, _apply)


def _worker_check_updates(app, on_complete):
    try:
        from core.github_updater import check_for_update
        info = check_for_update()
        ok, msg = _update_voice_message(info)
        _sync_updates_ui(app, info)
        _ui_updates_idle(app)
        _finish(app, on_complete, ok, msg)
    except Exception as exc:
        _ui_updates_idle(app)
        _finish(app, on_complete, False, f"Update check failed. {exc}")


def _worker_install_update(app, on_complete):
    try:
        from core.github_updater import check_for_update, apply_update
        import sys

        info = check_for_update()
        _sync_updates_ui(app, info)
        if not info or not info.available or not info.can_install_via_installer:
            _ui_updates_idle(app)
            _finish(app, on_complete, False, "No update is ready. Check for updates first.")
            return
        if not getattr(sys, "frozen", False):
            _ui_updates_idle(app)
            _finish(app, on_complete, False, "Auto install works from the built app only. Open releases in browser.")
            tab = getattr(_database_tab(app), "_updates_tab", None)
            if tab is not None:
                app.root.after(0, tab._open_releases)
            return

        app.root.after(0, lambda: apply_update(info, parent=app.root))
        _ui_updates_idle(app)
        _finish(
            app, on_complete, True,
            f"Downloading and opening the installer for version {info.latest_version}.",
        )
    except Exception as exc:
        _ui_updates_idle(app)
        _finish(app, on_complete, False, f"Install failed. {exc}")


def _worker_open_installer(app, on_complete):
    try:
        from core.install_updater import resolve_installer_exe
        path = resolve_installer_exe() or ""
        if not path:
            _ui_updates_idle(app)
            _finish(
                app, on_complete, False,
                "Installer not found. Say reinstall installer to download it first.",
            )
            return
        tab = getattr(_database_tab(app), "_updates_tab", None)

        def _open():
            if tab is not None:
                tab._open_local_installer()
            else:
                import os
                import subprocess
                subprocess.Popen(
                    [path],
                    cwd=os.path.dirname(path),
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )

        app.root.after(0, _open)
        _ui_updates_idle(app)
        _finish(app, on_complete, True, "Opening the Satpuda installer.")
    except Exception as exc:
        _ui_updates_idle(app)
        _finish(app, on_complete, False, f"Could not open installer. {exc}")


def _worker_reinstall_installer(app, on_complete):
    try:
        tab = getattr(_database_tab(app), "_updates_tab", None)
        if tab is None:
            _ui_updates_idle(app)
            _finish(app, on_complete, False, "Could not open App Updates. Try from Settings.")
            return

        def _run():
            try:
                tab._reinstall_installer()
            except Exception as exc:
                voice_log(f"Reinstall installer UI failed: {exc}", level="error")

        app.root.after(0, _run)
        _ui_updates_idle(app)
        _finish(
            app, on_complete, True,
            "Starting installer reinstall. Confirm in the dialog if asked.",
        )
    except Exception as exc:
        _ui_updates_idle(app)
        _finish(app, on_complete, False, f"Reinstall failed. {exc}")


def _worker_backup_now(app, on_complete):
    try:
        from core.backup_manager import last_backup_log_message, run_backup_now
        # run_backup_now reports what it actually did; the log line is only the
        # fallback now (a release profile can turn logging off entirely).
        res = run_backup_now(manual=True)
        if isinstance(res, dict) and res.get("message"):
            ok, msg = bool(res.get("ok")), str(res["message"])
        else:
            ok, msg = _backup_voice_message(last_backup_log_message())
        db = _database_tab(app)
        if db is not None:
            display = msg.replace("Backup completed successfully.", "Backup successful!")
            app.root.after(0, lambda m=display: db._backup_status_var.set(m))
        _finish(app, on_complete, ok, msg)
    except Exception as exc:
        _finish(app, on_complete, False, f"Backup failed. {exc}")


def _worker_sync_from_drive(app, on_complete):
    db = _database_tab(app)
    if db is None or not hasattr(db, "_sync_from_drive"):
        _finish(app, on_complete, False, "Sync from Drive is not available.")
        return

    def _voice_done(ok: bool, msg: str):
        _finish(app, on_complete, ok, msg)

    app.root.after(0, lambda: db._sync_from_drive(voice=True, on_complete=_voice_done))
