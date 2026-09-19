import tkinter as tk
import os

try:
    import ttkbootstrap as ttk
except ImportError:
    from tkinter import ttk

from core.app_version import APP_NAME, APP_VERSION
from core.font_config import *
from core.scroll_manager import make_scrollable
from core.themed_messagebox import showinfo, showwarning, showerror, askyesno


class UpdatesTab:
    """Standalone Updates tab, or embed via UpdatesTab.embed(parent, root)."""

    def __init__(self, notebook=None, parent=None, root=None, embedded=False):
        self._root_widget = root or parent or notebook
        self._embedded = embedded
        self._pending_info = None
        self._downloaded_path = ""
        self._local_installer_path = ""

        if parent is not None:
            frame = parent
        else:
            outer = ttk.Frame(notebook)
            notebook.add(outer, text="Updates")
            frame = make_scrollable(outer)
        self._build(frame, compact=embedded)

    @classmethod
    def embed(cls, parent, root_widget):
        return cls(parent=parent, root=root_widget, embedded=True)

    def _install_context(self):
        try:
            from core.install_updater import get_install_context
            return get_install_context()
        except Exception:
            return None

    def _build(self, frame, compact=False):
        if not compact:
            ttk.Label(
                frame,
                text="Application Updates",
                font=(FONT_FAMILY, FONT_SIZE_SECTION_TITLE, "bold"),
            ).pack(pady=(16, 6))

        pad = 12 if compact else 20
        if not compact:
            ttk.Label(
                frame,
                text=(
                    f"{APP_NAME} checks GitHub Releases for a newer app version. "
                    "Install Update opens Satpuda Core Installer if it is already on "
                    "this PC (install folder or desktop). If the installer is missing, "
                    "it is downloaded once to Local\\Programs\\Satpuda Core. "
                    "The installer then downloads and applies the app update. "
                    "Your database, activation, and backups stay on this PC."
                ),
                wraplength=620,
                justify=tk.LEFT,
                font=(FONT_FAMILY, FONT_SIZE_SUPPORTING_TEXT),
            ).pack(padx=pad, pady=(0, 12), anchor=tk.W)

        info = ttk.LabelFrame(frame, text="Version")
        info.pack(fill=tk.X, padx=pad, pady=8)
        try:
            from core.github_updater import expected_exe_name, platform_label
            plat = platform_label()
            exe_name = expected_exe_name()
        except Exception:
            plat = "Windows 10 / 11"
            exe_name = "SatpudaCore_Win10.exe"

        ctx = self._install_context()
        install_line = ""
        if ctx:
            install_line = f"\nInstall: {ctx.mode_label}"
            if ctx.install_dir:
                install_line += f"\nFolder: {ctx.install_dir}"
            if ctx.installer_exe:
                install_line += f"\nInstaller: {ctx.installer_exe}"
            elif ctx.desktop_shortcut:
                install_line += "\nInstaller: not found (app shortcut on desktop)"

        ttk.Label(
            info,
            text=f"Installed version: v{APP_VERSION}  ({plat} — {exe_name}){install_line}",
            font=(FONT_FAMILY, FONT_SIZE_LABELS, "bold"),
            justify=tk.LEFT,
        ).pack(anchor=tk.W, padx=12, pady=(10, 4))
        self._status_var = tk.StringVar(value="Click Check for Updates to contact GitHub.")
        ttk.Label(
            info,
            textvariable=self._status_var,
            wraplength=580,
            justify=tk.LEFT,
            font=(FONT_FAMILY, FONT_SIZE_SUPPORTING_TEXT),
        ).pack(anchor=tk.W, padx=12, pady=(0, 10))

        try:
            from core.github_updater import is_auto_check_enabled
            auto_on = is_auto_check_enabled()
        except Exception:
            auto_on = True
        self._auto_var = tk.BooleanVar(value=auto_on)
        ttk.Checkbutton(
            info,
            text="Check for updates automatically once per day",
            variable=self._auto_var,
            command=self._save_auto_pref,
        ).pack(anchor=tk.W, padx=12, pady=(0, 10))

        actions = ttk.Frame(frame)
        actions.pack(fill=tk.X, padx=pad, pady=8)
        self._check_btn = ttk.Button(
            actions, text="Check for Updates", command=self._check_updates
        )
        self._check_btn.pack(side=tk.LEFT, padx=(0, 8))
        self._download_btn = ttk.Button(
            actions,
            text="Install Update",
            command=self._install_update,
            state=tk.DISABLED,
        )
        self._download_btn.pack(side=tk.LEFT, padx=(0, 8))
        self._reinstall_btn = ttk.Button(
            actions,
            text="Reinstall Installer",
            command=self._reinstall_installer,
        )
        self._reinstall_btn.pack(side=tk.LEFT, padx=(0, 8))
        self._open_installer_btn = ttk.Button(
            actions,
            text="Open Installer",
            command=self._open_local_installer,
            state=tk.DISABLED,
        )
        self._open_installer_btn.pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(
            actions, text="Open Releases Page", command=self._open_releases
        ).pack(side=tk.LEFT)

        self._progress = ttk.Progressbar(frame, mode="determinate", length=420, maximum=100)
        self._progress.pack(padx=pad, pady=(4, 8), anchor=tk.W)

        notes = ttk.LabelFrame(frame, text="Release Notes")
        notes.pack(fill=tk.BOTH, expand=bool(not compact), padx=pad, pady=8)
        self._notes = tk.Text(
            notes,
            height=6 if compact else 14,
            wrap=tk.WORD,
            font=(FONT_FAMILY, FONT_SIZE_SUPPORTING_TEXT),
            state=tk.DISABLED,
        )
        self._notes.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        if not compact:
            ttk.Label(
                frame,
                text=(
                    "Publishing: attach SatpudaCoreInstaller.exe plus the app zip files "
                    "to each GitHub Release."
                ),
                wraplength=620,
                justify=tk.LEFT,
                font=(FONT_FAMILY, FONT_SIZE_SUPPORTING_TEXT),
                foreground="#666",
            ).pack(padx=pad, pady=(8, 16), anchor=tk.W)

        self._refresh_open_installer_btn()

    def _refresh_open_installer_btn(self) -> None:
        path = self._local_installer_path
        if not path or not os.path.isfile(path):
            try:
                from core.install_updater import resolve_installer_exe
                path = resolve_installer_exe() or ""
            except Exception:
                path = ""
        if path and os.path.isfile(path):
            self._local_installer_path = path
            self._open_installer_btn.config(state=tk.NORMAL)
        else:
            self._open_installer_btn.config(state=tk.DISABLED)

    def _open_local_installer(self) -> None:
        path = self._local_installer_path
        if not path or not os.path.isfile(path):
            try:
                from core.install_updater import resolve_installer_exe
                path = resolve_installer_exe() or ""
            except Exception:
                path = ""
        if not path or not os.path.isfile(path):
            showwarning(
                "Open Installer",
                "Satpuda Core Installer was not found.\n"
                "Use Reinstall Installer to download it first.",
                parent=self._parent(),
            )
            return
        try:
            import subprocess
            import sys

            subprocess.Popen(
                [path],
                cwd=os.path.dirname(path),
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            self._status_var.set(f"Opened installer: {path}")
        except Exception as exc:
            showerror("Open Installer", str(exc), parent=self._parent())

    def _parent(self):
        return self._root_widget.winfo_toplevel()

    def _set_notes(self, text: str) -> None:
        self._notes.config(state=tk.NORMAL)
        self._notes.delete("1.0", tk.END)
        self._notes.insert("1.0", text or "")
        self._notes.config(state=tk.DISABLED)

    def _save_auto_pref(self) -> None:
        try:
            from core.github_updater import set_auto_check_enabled
            set_auto_check_enabled(bool(self._auto_var.get()))
        except Exception as exc:
            showerror("Updates", str(exc), parent=self._parent())

    def _open_releases(self) -> None:
        try:
            from core.github_updater import open_releases_page
            open_releases_page()
        except Exception as exc:
            showerror("Updates", str(exc), parent=self._parent())

    def _set_busy(self, busy: bool, *, progress_mode: str = "idle") -> None:
        state = tk.DISABLED if busy else tk.NORMAL
        self._check_btn.config(state=state)
        if busy:
            self._download_btn.config(state=tk.DISABLED)
            self._reinstall_btn.config(state=tk.DISABLED)
            self._open_installer_btn.config(state=tk.DISABLED)
            if progress_mode == "download":
                try:
                    self._progress.stop()
                except Exception:
                    pass
                self._progress.config(mode="determinate", maximum=100, value=0)
            else:
                self._progress.config(mode="indeterminate", maximum=100, value=0)
                try:
                    self._progress.start(12)
                except Exception:
                    pass
        else:
            try:
                self._progress.stop()
            except Exception:
                pass
            self._progress.config(mode="determinate", maximum=100, value=0)
            self._reinstall_btn.config(state=tk.NORMAL)
            self._refresh_open_installer_btn()

    def _set_download_progress(self, pct: int, message: str = "") -> None:
        if message:
            self._status_var.set(message)
        try:
            self._progress.stop()
        except Exception:
            pass
        self._progress.config(mode="determinate", maximum=100, value=max(0, min(100, pct)))

    def _install_button_label(self, info) -> str:
        try:
            from core.install_updater import resolve_installer_exe
            if resolve_installer_exe():
                return "Install Update"
        except Exception:
            pass
        return "Download & Install"

    def _can_install(self, info) -> bool:
        return bool(info and info.available and info.can_install_via_installer)

    def _check_updates(self) -> None:
        self._set_busy(True)
        self._status_var.set("Checking GitHub for the latest release…")
        self._set_notes("")
        self._pending_info = None
        self._downloaded_path = ""
        self._download_btn.config(state=tk.DISABLED)

        def _run():
            try:
                from core.github_updater import check_for_update, format_release_summary
                info = check_for_update()
            except Exception as exc:
                info = None
                err = str(exc)
            else:
                err = ""

            def _done():
                self._set_busy(False)
                if info is None:
                    self._status_var.set(f"Update check failed: {err}")
                    return
                if info.error and not info.available:
                    self._status_var.set(info.error)
                    return
                if info.available:
                    self._pending_info = info
                    self._status_var.set(
                        f"Update available: v{info.latest_version} "
                        f"(you have v{info.current_version})"
                    )
                    self._set_notes(format_release_summary(info))
                    if self._can_install(info):
                        self._download_btn.config(
                            state=tk.NORMAL,
                            text=self._install_button_label(info),
                        )
                    else:
                        showwarning(
                            "Update Available",
                            info.error or "No update package on the release.",
                            parent=self._parent(),
                        )
                else:
                    self._status_var.set(
                        f"Connected to GitHub — v{info.current_version} is the latest release "
                        f"(GitHub: v{info.latest_version or info.current_version})."
                    )
                    notes = format_release_summary(info) if info.latest_version else ""
                    self._set_notes(notes or "No newer release found on GitHub.")

            self._parent().after(0, _done)

        from core.github_updater import run_in_thread
        run_in_thread(_run)

    def _install_update(self) -> None:
        info = self._pending_info
        if not info or not self._can_install(info):
            showwarning(
                "Updates",
                "No update is ready. Check for updates first.",
                parent=self._parent(),
            )
            return

        import sys
        if not getattr(sys, "frozen", False):
            showinfo(
                "Updates",
                "Auto-install works from the built EXE only.\n"
                "Opening the GitHub releases page in your browser.",
                parent=self._parent(),
            )
            self._open_releases()
            return

        try:
            from core.install_updater import resolve_installer_exe
            has_installer = bool(resolve_installer_exe())
        except Exception:
            has_installer = False

        if has_installer:
            prompt = (
                f"Open Satpuda Core Installer to update to v{info.latest_version}?\n\n"
                "The installer will download and install the latest app version.\n\n"
                "Your database and activation will not change."
            )
        else:
            prompt = (
                f"Download Satpuda Core Installer and update to v{info.latest_version}?\n\n"
                "The installer will be saved to Local\\Programs\\Satpuda Core, "
                "then opened to download and install the latest app version.\n\n"
                "Your database and activation will not change."
            )

        if not askyesno("Install Update", prompt, parent=self._parent()):
            return

        self._set_busy(True)
        self._status_var.set(
            "Opening Satpuda Core Installer…" if has_installer else "Downloading installer…"
        )

        def _run():
            err = ""
            try:
                from core.github_updater import apply_update

                def _stage(stage, payload):
                    msg = payload.get("message") if isinstance(payload, dict) else str(payload)
                    if msg:
                        self._status_var.set(str(msg))

                apply_update(
                    info,
                    parent=self._parent(),
                    progress_cb=lambda stage, payload: self._parent().after(
                        0, lambda: _stage(stage, payload)
                    ),
                )
            except Exception as exc:
                err = str(exc)

            def _done():
                self._set_busy(False)
                if err:
                    self._status_var.set(f"Update failed: {err}")
                    showerror("Update Failed", err, parent=self._parent())

            self._parent().after(0, _done)

        from core.github_updater import run_in_thread
        run_in_thread(_run)

    def _reinstall_installer(self) -> None:
        try:
            from core.install_updater import (
                INSTALLER_EXE,
                resolve_install_dir,
                search_installer_locations,
            )
        except Exception:
            INSTALLER_EXE = "SatpudaCoreInstaller.exe"

        try:
            locations = search_installer_locations()
        except Exception:
            locations = []

        if locations:
            loc_text = "\n\nFound on this PC:\n" + "\n".join(f"  • {p}" for p in locations)
        else:
            loc_text = (
                "\n\nNo installer found yet on desktop, Downloads, or "
                "Local\\Programs\\Satpuda Core."
            )

        folder = ""
        try:
            folder = resolve_install_dir()
        except Exception:
            pass

        prompt = (
            "Download the latest SatpudaCoreInstaller.exe from GitHub and "
            f"save it to:\n  {folder or 'Local\\\\Programs\\\\Satpuda Core'}\n\n"
            "This replaces any existing installer copy. If the app is already "
            "installed, the desktop shortcut for Satpuda Core will be recreated."
            f"{loc_text}\n\n"
            "Your database and activation will not change."
        )
        if not askyesno("Reinstall Installer", prompt, parent=self._parent()):
            return

        self._set_busy(True, progress_mode="download")
        self._status_var.set("Fetching latest installer from GitHub…")

        def _run():
            err = ""
            dest = ""
            app_shortcut = ""
            installer_shortcut = ""
            try:
                from core.github_updater import fetch_installer_download
                from core.install_updater import reinstall_installer

                url, name, fetch_err = fetch_installer_download()
                if fetch_err:
                    raise RuntimeError(fetch_err)

                self._parent().after(
                    0,
                    lambda: self._status_var.set(
                        f"Downloading {name or INSTALLER_EXE} from GitHub…"
                    ),
                )

                def _progress(read: int, total: int) -> None:
                    pct = min(100, int(read * 100 / total)) if total > 0 else 0
                    msg = (
                        f"Downloading installer… {pct}%"
                        if total
                        else "Downloading installer…"
                    )
                    self._parent().after(
                        0, lambda p=pct, m=msg: self._set_download_progress(p, m)
                    )

                self._parent().after(
                    0, lambda: self._status_var.set("Creating desktop shortcuts…"),
                )

                dest, app_shortcut, installer_shortcut = reinstall_installer(
                    url,
                    name,
                    create_shortcut=True,
                    open_after=False,
                    progress_cb=_progress,
                )
            except Exception as exc:
                err = str(exc)

            def _done():
                self._set_busy(False)
                if err:
                    self._status_var.set(f"Reinstall failed: {err}")
                    showerror("Reinstall Failed", err, parent=self._parent())
                    return
                self._local_installer_path = dest
                shortcut_note = ""
                if app_shortcut:
                    shortcut_note += f"\nApp shortcut:\n{app_shortcut}"
                if installer_shortcut:
                    shortcut_note += f"\nInstaller shortcut:\n{installer_shortcut}"
                self._set_download_progress(
                    100,
                    f"Installer ready: {dest}" + (f" | Shortcut created" if shortcut_note else ""),
                )
                self._refresh_open_installer_btn()
                showinfo(
                    "Reinstall Installer",
                    "Satpuda Core Installer has been downloaded and replaced.\n\n"
                    f"Saved to:\n{dest}"
                    f"{shortcut_note}\n\n"
                    "Click Open Installer to run it.",
                    parent=self._parent(),
                )

            self._parent().after(0, _done)

        from core.github_updater import run_in_thread
        run_in_thread(_run)
