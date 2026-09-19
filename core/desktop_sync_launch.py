"""Online sync bootstrap for the desktop data engine (mirrors main.py)."""
from __future__ import annotations

import threading
import time


def start_desktop_online_sync(_conn=None, _db_path: str = "") -> None:
    """Deferred live hints — same contract as main.py _start_server_sync."""

    def _worker() -> None:
        time.sleep(1.0)
        try:
            from core.build_features import is_server_sync_supported
            from core.sync_coordinator import start_online_sync, stop_online_sync
            from core.sync_prefs import is_online_mode

            if not is_server_sync_supported() or not is_online_mode():
                stop_online_sync()
                print("Desktop server sync skipped — Offline mode", flush=True)
                return

            try:
                from core.sync_bootstrap import clear_pending_bootstrap, mark_bootstrap_done

                clear_pending_bootstrap()
                mark_bootstrap_done()
            except Exception:
                pass

            # Same as main.py _start_server_sync: hints only, no SQLite apply.
            started = start_online_sync(
                None,
                on_change=_on_server_change,
                db_path=None,
                adopt_server_head=True,
                hints_only=True,
            )
            if started:
                try:
                    from core.sync_status import note_last_sync

                    note_last_sync("online")
                except Exception:
                    pass
                print(
                    "Desktop server sync started (hints-only, server-only Online)",
                    flush=True,
                )
            else:
                print("Desktop server sync failed to start (health/pair)", flush=True)
        except Exception as exc:
            print(f"Desktop sync launch failed: {exc}", flush=True)

    threading.Thread(
        target=_worker,
        daemon=True,
        name="DesktopServerSync",
    ).start()


def _on_server_change(collection: str) -> None:
    try:
        from core.sync_status import note_collection_change, note_last_sync

        note_collection_change(collection)
        note_last_sync("pull")
    except Exception:
        pass


def stop_desktop_online_sync() -> None:
    try:
        from core.sync_coordinator import stop_online_sync

        stop_online_sync()
    except Exception:
        pass
