import tkinter as tk

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
                    f"{APP_NAME} checks GitHub Releases for a newer version. "
                    "Updating replaces only the program EXE — your database, activation, "
                    "and backup settings stay on this PC."
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
            exe_name = "SatpudaCore.exe"
        ttk.Label(
            info,
            text=f"Installed version: v{APP_VERSION}  ({plat} — {exe_name})",
            font=(FONT_FAMILY, FONT_SIZE_LABELS, "bold"),
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
            text="Download && Install",
            command=self._install_update,
            state=tk.DISABLED,
        )
        self._download_btn.pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(
            actions, text="Open Releases Page", command=self._open_releases
        ).pack(side=tk.LEFT)

        self._progress = ttk.Progressbar(frame, mode="indeterminate", length=420)
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
                    "Publishing: attach BOTH SatpudaCore.exe (Win 10/11) and SatpudaCore_Win7.exe "
                    "to each GitHub Release. This PC downloads only the EXE that matches its build."
                ),
                wraplength=620,
                justify=tk.LEFT,
                font=(FONT_FAMILY, FONT_SIZE_SUPPORTING_TEXT),
                foreground="#666",
            ).pack(padx=pad, pady=(8, 16), anchor=tk.W)

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

    def _set_busy(self, busy: bool) -> None:
        state = tk.DISABLED if busy else tk.NORMAL
        self._check_btn.config(state=state)
        if busy:
            self._download_btn.config(state=tk.DISABLED)
            try:
                self._progress.start(12)
            except Exception:
                pass
        else:
            try:
                self._progress.stop()
            except Exception:
                pass

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
                    if info.has_download:
                        self._download_btn.config(state=tk.NORMAL)
                    else:
                        showwarning(
                            "Update Available",
                            info.error or "No EXE asset on the release.",
                            parent=self._parent(),
                        )
                else:
                    self._status_var.set(
                        f"You are up to date (v{info.current_version})."
                    )
                    self._set_notes("No newer release found on GitHub.")

            self._parent().after(0, _done)

        from core.github_updater import run_in_thread
        run_in_thread(_run)

    def _install_update(self) -> None:
        info = self._pending_info
        if not info or not info.has_download:
            showwarning(
                "Updates",
                "No downloadable update is ready. Check for updates first.",
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

        if not askyesno(
            "Install Update",
            f"Download and install v{info.latest_version}?\n\n"
            "The app will close briefly while the new EXE replaces the old one, "
            "then restart automatically.\n\n"
            "Your database and activation will not change.",
            parent=self._parent(),
        ):
            return

        self._set_busy(True)
        self._status_var.set("Downloading update…")

        def _run():
            err = ""
            path = ""
            try:
                from core.github_updater import download_update

                def _progress(read, total):
                    def _ui():
                        if total > 0:
                            pct = min(100, int(read * 100 / total))
                            self._status_var.set(f"Downloading… {pct}%")
                        else:
                            self._status_var.set("Downloading…")
                    self._parent().after(0, _ui)

                path = download_update(info, progress_cb=_progress)
            except Exception as exc:
                err = str(exc)

            def _done():
                self._set_busy(False)
                if err:
                    self._status_var.set(f"Download failed: {err}")
                    showerror("Update Failed", err, parent=self._parent())
                    return
                self._downloaded_path = path
                self._status_var.set("Download complete. Installing…")
                try:
                    from core.github_updater import apply_downloaded_update
                    apply_downloaded_update(path, parent=self._parent())
                except Exception as exc:
                    showerror("Update Failed", str(exc), parent=self._parent())

            self._parent().after(0, _done)

        from core.github_updater import run_in_thread
        run_in_thread(_run)
