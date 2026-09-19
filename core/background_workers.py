"""Small helpers for offloading heavy work from the Tk main thread."""
from __future__ import annotations

import queue
import threading
from typing import Callable, TypeVar

T = TypeVar("T")


def run_in_thread(
    target: Callable[[], T],
    *,
    name: str = "BackgroundWorker",
    on_success: Callable[[T], None] | None = None,
    on_error: Callable[[BaseException], None] | None = None,
    root=None,
) -> threading.Thread:
    """Run target in a daemon thread; optional UI-thread callbacks via root.after."""

    def _deliver(fn, arg):
        if fn is None:
            return
        if root is None:
            fn(arg)
            return

        def _run():
            try:
                fn(arg)
            except Exception:
                pass

        try:
            tk_root = root.winfo_toplevel()
            if not tk_root.winfo_exists():
                return
            tk_root.after(0, _run)
        except Exception:
            try:
                root.after(0, _run)
            except Exception:
                pass

    def _work():
        try:
            result = target()
        except BaseException as exc:
            if on_error is not None:
                _deliver(on_error, exc)
            return
        if on_success is not None:
            _deliver(on_success, result)

    thread = threading.Thread(target=_work, daemon=True, name=name)
    thread.start()
    return thread


class UiProgressJob:
    """
    Poll a queue from a background worker while showing progress on the UI thread.
    Same pattern as bill OCR / master medicine import.
    """

    def __init__(self, parent, *, title: str, poll_ms: int = 120):
        self._parent = parent
        self._title = title
        self._poll_ms = poll_ms
        self._queue: queue.Queue = queue.Queue()
        self._finished = __import__("tkinter").BooleanVar(master=parent, value=False)
        self._result: dict = {}
        self._top = None
        self._status = None

    def run(self, worker: Callable[[Callable[[str], None]], T]) -> T:
        import tkinter as tk
        from tkinter import ttk

        top = tk.Toplevel(self._parent)
        self._top = top
        top.title(self._title)
        top.transient(self._parent)
        top.resizable(False, False)
        try:
            top.grab_set()
        except Exception:
            pass
        frame = ttk.Frame(top, padding=20)
        frame.pack(fill=tk.BOTH, expand=True)
        self._status = ttk.Label(frame, text="Starting…", wraplength=420, justify=tk.LEFT)
        self._status.pack(anchor=tk.W, pady=(0, 12))
        bar = ttk.Progressbar(frame, mode="indeterminate", length=380)
        bar.pack(fill=tk.X)
        bar.start(12)
        top.update_idletasks()
        x = self._parent.winfo_rootx() + (self._parent.winfo_width() // 2) - (top.winfo_width() // 2)
        y = self._parent.winfo_rooty() + (self._parent.winfo_height() // 2) - (top.winfo_height() // 2)
        top.geometry(f"+{max(0, x)}+{max(0, y)}")

        def _poll():
            try:
                while True:
                    msg = self._queue.get_nowait()
                    if self._status is not None:
                        self._status.configure(text=str(msg))
            except queue.Empty:
                pass
            if self._finished.get():
                try:
                    top.grab_release()
                    top.destroy()
                except Exception:
                    pass
                return
            top.after(self._poll_ms, _poll)

        def _thread():
            try:
                self._result["value"] = worker(self._queue.put)
            except Exception as exc:
                self._result["error"] = exc
            finally:
                self._parent.after(0, lambda: self._finished.set(True))

        threading.Thread(target=_thread, daemon=True, name=self._title).start()
        _poll()
        self._parent.wait_variable(self._finished)
        if "error" in self._result:
            raise self._result["error"]
        return self._result.get("value")

    def run_async(
        self,
        worker: Callable[[Callable[[str], None]], T],
        *,
        on_complete: Callable[[T], None] | None = None,
        on_error: Callable[[BaseException], None] | None = None,
    ) -> None:
        """Like run(), but does not block the main loop with wait_variable."""
        import tkinter as tk
        from tkinter import ttk

        top = tk.Toplevel(self._parent)
        self._top = top
        top.title(self._title)
        top.transient(self._parent)
        top.resizable(False, False)
        try:
            top.grab_set()
        except Exception:
            pass
        frame = ttk.Frame(top, padding=20)
        frame.pack(fill=tk.BOTH, expand=True)
        self._status = ttk.Label(frame, text="Starting…", wraplength=420, justify=tk.LEFT)
        self._status.pack(anchor=tk.W, pady=(0, 12))
        bar = ttk.Progressbar(frame, mode="indeterminate", length=380)
        bar.pack(fill=tk.X)
        bar.start(12)
        top.update_idletasks()
        x = self._parent.winfo_rootx() + (self._parent.winfo_width() // 2) - (top.winfo_width() // 2)
        y = self._parent.winfo_rooty() + (self._parent.winfo_height() // 2) - (top.winfo_height() // 2)
        top.geometry(f"+{max(0, x)}+{max(0, y)}")

        def _finish():
            err = self._result.get("error")
            val = self._result.get("value")
            try:
                top.grab_release()
                top.destroy()
            except Exception:
                pass
            if err is not None:
                if on_error is not None:
                    on_error(err)
                return
            if on_complete is not None:
                on_complete(val)

        def _poll():
            try:
                while True:
                    msg = self._queue.get_nowait()
                    if self._status is not None:
                        self._status.configure(text=str(msg))
            except queue.Empty:
                pass
            if self._finished.get():
                self._parent.after(0, _finish)
                return
            top.after(self._poll_ms, _poll)

        def _thread():
            try:
                self._result["value"] = worker(self._queue.put)
            except Exception as exc:
                self._result["error"] = exc
            finally:
                self._parent.after(0, lambda: self._finished.set(True))

        threading.Thread(target=_thread, daemon=True, name=self._title).start()
        _poll()


def db_path_from_conn(conn) -> str:
    try:
        row = conn.execute("PRAGMA database_list").fetchone()
        return row[2] if row else ""
    except Exception:
        return ""


def paint_busy_dialog(parent, title: str, message: str = "Please wait…"):
    """Show a busy dialog and force-paint it. Caller must destroy() the return value.

    Does not grab the UI — nested validation messageboxes must still work.
    """
    import tkinter as tk
    from tkinter import ttk

    root = parent
    try:
        root = parent.winfo_toplevel()
    except Exception:
        pass
    top = tk.Toplevel(root)
    top.title(title)
    try:
        top.transient(root)
    except Exception:
        pass
    top.resizable(False, False)
    frame = ttk.Frame(top, padding=20)
    frame.pack(fill=tk.BOTH, expand=True)
    ttk.Label(frame, text=message, wraplength=420, justify=tk.LEFT).pack(
        anchor=tk.W, pady=(0, 12)
    )
    bar = ttk.Progressbar(frame, mode="indeterminate", length=380)
    bar.pack(fill=tk.X)
    bar.start(12)
    try:
        top.update_idletasks()
        x = root.winfo_rootx() + (root.winfo_width() // 2) - (top.winfo_width() // 2)
        y = root.winfo_rooty() + (root.winfo_height() // 2) - (top.winfo_height() // 2)
        top.geometry(f"+{max(0, x)}+{max(0, y)}")
        top.lift()
        top.update()
    except Exception:
        try:
            top.update_idletasks()
        except Exception:
            pass
    return top


def run_on_ui_with_busy(parent, title: str, work: Callable[[], T], *, message: str = "Please wait…") -> T:
    """Force-paint a busy dialog, then run work on the UI thread (for SQLite/Tk-bound saves)."""
    top = paint_busy_dialog(parent, title, message)
    try:
        return work()
    finally:
        try:
            top.destroy()
        except Exception:
            pass


def run_with_progress(
    parent,
    title: str,
    worker: Callable[[Callable[[str], None]], T],
    *,
    on_complete: Callable[[T], None] | None = None,
    on_error: Callable[[BaseException], None] | None = None,
) -> None:
    """Show a progress dialog without blocking the Tk event loop."""
    UiProgressJob(parent, title=title).run_async(worker, on_complete=on_complete, on_error=on_error)
