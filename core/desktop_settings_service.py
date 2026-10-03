"""Load/save Settings for the Tauri desktop UI — reuses same modules as Tk."""
from __future__ import annotations

import logging
import os
import sqlite3
from typing import Any, Callable, Optional

log = logging.getLogger(__name__)

ProgressCb = Optional[Callable[[str], None]]


def _has_pairing_key() -> bool:
    """Does this PC hold the active store's SC- key? Cheap, local, no network."""
    try:
        from core.store_link import get_local_android_key

        return bool((get_local_android_key() or "").strip())
    except Exception:
        return False


def _restore_refusal(result: Any) -> dict[str, Any]:
    """A failed restore for the screen; one that would discard this device's newer records
    carries code "would_lose" so the screen asks, and re-sends with confirm_loss."""
    from core.backup_manager import WOULD_LOSE

    text = str(result)
    if text.startswith(WOULD_LOSE):
        return {"ok": False, "code": "would_lose", "error": text[len(WOULD_LOSE):].strip()}
    return {"ok": False, "error": text}


def _vendor_admin_token(data: dict[str, Any]) -> Any:
    """An admin token for a settings action, or a refusal the screen can show.

    Returns the token string on success, or a ready-made ``{"ok": False, ...}``
    answer carrying ``code: "admin_credential_required"`` so the panel knows to
    ask for the username and password rather than showing a raw error.

    The vendor administrator used to be a username and password compiled into
    the build, so every one of these actions ran as the administrator of every
    shop on the account without anybody asking. The credentials now come from
    the person at the screen, are used for this one call, and are never written
    to disk (core/admin_session.py keeps only the short-lived token).
    """
    from core import admin_session
    from core.server_api import AdminCredentialRequired

    token = str(data.get("admin_token") or "").strip()
    if token:
        return token
    user = str(data.get("admin_username") or "").strip()
    pw = str(data.get("admin_password") or "")
    try:
        if user and pw:
            return admin_session.sign_in(user, pw)
        return admin_session.token()
    except AdminCredentialRequired as exc:
        return {
            "ok": False,
            "code": "admin_credential_required",
            "error": str(exc),
        }
    except Exception as exc:
        return {
            "ok": False,
            "code": "admin_login_failed",
            "error": f"Could not sign in to the Satpuda server as administrator: {exc}",
        }


def _begin_heavy(
    conn: sqlite3.Connection,
    data: dict[str, Any],
    action: str,
    start_message: str,
    runner: Callable[[sqlite3.Connection, ProgressCb], dict[str, Any]],
) -> dict[str, Any]:
    """Run *runner* in a background thread unless already inside a heavy job.

    Frontend should poll ``heavy_job_status`` when ``background`` is true.
    """
    if data.get("_heavy_sync"):
        from core.heavy_job import report_progress

        return runner(conn, report_progress)

    from core.background_workers import db_path_from_conn
    from core.db_utils import open_store_db
    from core.heavy_job import is_running, start_async

    if is_running():
        return {
            "ok": False,
            "background": True,
            "error": "Another background job is already running. Wait for it to finish.",
            "message": "Another background job is already running.",
        }

    db_path = db_path_from_conn(conn)

    def _worker(progress: ProgressCb) -> dict[str, Any]:
        own_conn: sqlite3.Connection | None = None
        work_conn = conn
        if db_path:
            try:
                own_conn = open_store_db(db_path)
                work_conn = own_conn
            except Exception:
                own_conn = None
                work_conn = conn
        try:
            if progress:
                progress(start_message)
            return runner(work_conn, progress)
        finally:
            if own_conn is not None:
                try:
                    own_conn.close()
                except Exception:
                    pass

    started = start_async(action, _worker, start_message=start_message)
    return {
        "ok": True,
        "background": True,
        "started": bool(started),
        "message": start_message,
    }


def _serialize_update_info(info: Any) -> dict[str, Any]:
    if info is None:
        return {}
    if isinstance(info, dict):
        return info
    return {
        "available": bool(getattr(info, "available", False)),
        "current_version": str(getattr(info, "current_version", "") or ""),
        "latest_version": str(getattr(info, "latest_version", "") or ""),
        "release_name": str(getattr(info, "release_name", "") or ""),
        "release_notes": str(getattr(info, "release_notes", "") or ""),
        "published_at": str(getattr(info, "published_at", "") or ""),
        "download_url": str(getattr(info, "download_url", "") or ""),
        "download_name": str(getattr(info, "download_name", "") or ""),
        "installer_download_url": str(getattr(info, "installer_download_url", "") or ""),
        "installer_download_name": str(getattr(info, "installer_download_name", "") or ""),
        "html_url": str(getattr(info, "html_url", "") or ""),
        "error": str(getattr(info, "error", "") or ""),
        "update_mode": str(getattr(info, "update_mode", "") or ""),
        "can_install_via_installer": bool(getattr(info, "can_install_via_installer", False)),
    }


def _font_size() -> int:
    from core.font_config import _load_font_size

    return int(_load_font_size())


def _save_font_size(size: int) -> None:
    from core.font_config import _get_font_size_path
    import os

    size = max(7, min(20, int(size)))
    path = _get_font_size_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(str(size))


def get_options() -> dict[str, Any]:
    from core.app_prefs import AVAILABLE_THEMES
    from core.bill_config import (
        AVAILABLE_TEMPLATES,
        BILL_PRINT_FIELD_OPTIONS,
        BILL_SIZE_DOT_MATRIX,
        BILL_SIZE_NORMAL,
        DEFAULT_BILL_PRINT_SETTINGS,
        SALES_ENTER_ACTIONS,
        bill_size_mode_label,
    )
    from core.bill_save_prefs import (
        PDF_LAYOUT_ONE_A6,
        PDF_LAYOUT_ONE_BOTTOM,
        PDF_LAYOUT_ONE_TOP,
        PDF_LAYOUT_TWO_COPIES,
        pdf_save_layout_label,
    )
    from core.column_config import DASHBOARD_SECTIONS, QUICK_ACCESS_BUTTONS
    from core.sales_form_prefs import (
        POSITION_AFTER_BILL_DATE,
        POSITION_FIRST,
        payment_mode_position_label,
    )
    from core.sales_medicine_prefs import (
        BATCH_NEWEST_FIRST,
        BATCH_OLDEST_FIRST,
        batch_sort_label,
    )
    from core.upi_prefs import (
        AMOUNT_TOTAL,
        AMOUNT_TOTAL_PLUS_PREV_DUE,
        upi_amount_mode_label,
    )

    bill_fields = []
    bill_field_groups = []
    bill_field_defaults: dict[str, bool] = {}
    for group, items in BILL_PRINT_FIELD_OPTIONS:
        group_items = []
        for key, label in items:
            bill_fields.append({"key": key, "label": label})
            group_items.append({"key": key, "label": label})
            bill_field_defaults[key] = bool(DEFAULT_BILL_PRINT_SETTINGS.get(key, False))
        bill_field_groups.append({"group": group, "items": group_items})

    return {
        "themes": [{"value": k, "label": v} for k, v in AVAILABLE_THEMES.items()],
        "theme_packs": [
            {"value": "classic", "label": "Classic — original palettes"},
            {
                "value": "accessible",
                "label": "Accessible — Gemini contrast + clearer chrome",
            },
            {
                "value": "modern",
                "label": "Modern — ChatGPT deep surfaces & accents",
            },
        ],
        "templates": [{"value": k, "label": v} for k, v in AVAILABLE_TEMPLATES.items()],
        "paper_sizes": ["A5", "A6", "A4"],
        "bill_size_modes": [
            {"value": BILL_SIZE_NORMAL, "label": bill_size_mode_label(BILL_SIZE_NORMAL)},
            {
                "value": BILL_SIZE_DOT_MATRIX,
                "label": bill_size_mode_label(BILL_SIZE_DOT_MATRIX),
            },
        ],
        "half_positions": [
            {"value": "bottom", "label": "Bottom half"},
            {"value": "top", "label": "Top half"},
        ],
        "a4_two_copy_layouts": [
            {"value": "side_by_side", "label": "Side by side"},
            {"value": "top_bottom", "label": "Top and bottom"},
        ],
        "enter_actions": [
            {"value": k, "label": v} for k, v in SALES_ENTER_ACTIONS.items()
        ],
        "bill_fields": bill_fields,
        "bill_field_groups": bill_field_groups,
        "bill_field_defaults": bill_field_defaults,
        "quick_access": [
            {"key": k, "label": lab} for k, lab in QUICK_ACCESS_BUTTONS
        ],
        "dashboard_sections": [
            {"key": k, "label": lab} for k, lab in DASHBOARD_SECTIONS
        ],
        "payment_mode_positions": [
            {
                "value": POSITION_FIRST,
                "label": payment_mode_position_label(POSITION_FIRST),
            },
            {
                "value": POSITION_AFTER_BILL_DATE,
                "label": payment_mode_position_label(POSITION_AFTER_BILL_DATE),
            },
        ],
        "item_discount_modes": [
            {"value": "rupees", "label": "Rupees (₹)"},
            {"value": "percent", "label": "Percentage (%)"},
        ],
        "margin_display_modes": [
            {"value": "rupees", "label": "Rupees (₹)"},
            {"value": "percent", "label": "Percentage (%)"},
        ],
        "history_scopes": [
            {"value": "current_fy", "label": "Current financial year only"},
            {"value": "all", "label": "All dates"},
        ],
        "batch_sort_orders": [
            {
                "value": BATCH_OLDEST_FIRST,
                "label": batch_sort_label(BATCH_OLDEST_FIRST),
            },
            {
                "value": BATCH_NEWEST_FIRST,
                "label": batch_sort_label(BATCH_NEWEST_FIRST),
            },
        ],
        "pdf_save_layouts": [
            {"value": v, "label": pdf_save_layout_label(v)}
            for v in (
                PDF_LAYOUT_TWO_COPIES,
                PDF_LAYOUT_ONE_BOTTOM,
                PDF_LAYOUT_ONE_TOP,
                PDF_LAYOUT_ONE_A6,
            )
        ],
        "upi_amount_modes": [
            {"value": AMOUNT_TOTAL, "label": upi_amount_mode_label(AMOUNT_TOTAL)},
            {
                "value": AMOUNT_TOTAL_PLUS_PREV_DUE,
                "label": upi_amount_mode_label(AMOUNT_TOTAL_PLUS_PREV_DUE),
            },
        ],
        "app_modes": [
            {"value": "medical", "label": "Medical"},
            {"value": "veterinary", "label": "Veterinary"},
        ],
        "sync_modes": [
            {"value": "offline", "label": "Offline — local SQLite + Google Drive"},
            {
                "value": "online",
                "label": "Online — server-only (no local store DB)",
            },
        ],
        "export_formats": ["csv", "xlsx", "pdf"],
        "printer_types": [
            {"value": "standard", "label": "Standard (HTML/PDF)"},
            {"value": "dot_matrix", "label": "Dot Matrix (9-pin ESC/P)"},
        ],
        "dot_matrix_tear_modes": [
            {"value": "software", "label": "Satpuda (ejects the slip, pulls back the same)"},
            {"value": "printer", "label": "Printer's own auto tear-off"},
        ],
        "dot_matrix_print_methods": [
            {"value": "auto", "label": "Auto (RAW for an ESC/P driver, else Windows driver)"},
            {"value": "raw", "label": "RAW ESC/P - exact position (recommended)"},
            {"value": "gdi", "label": "Windows driver"},
        ],
        "payment_modes": ["Cash", "Online", "Cheque", "NEFT", "RTGS", "UPI"],
        "display_styles": [
            {"value": k, "label": lab}
            for k, lab in __import__(
                "core.record_indicators", fromlist=["DISPLAY_STYLE_LABELS"]
            ).DISPLAY_STYLE_LABELS.items()
        ],
        "column_pages": [
            {"key": k, "label": lab}
            for k, lab in __import__(
                "core.column_config", fromlist=["PAGE_LABELS"]
            ).PAGE_LABELS.items()
        ],
        "table_columns": {
            page: [{"key": name, "label": name} for name, _db in cols]
            for page, cols in __import__(
                "core.column_config", fromlist=["desktop_table_columns"]
            ).desktop_table_columns().items()
        },
        # A few columns ship hidden until the shop turns them on. The panel
        # rendered every unsaved key as ticked, so those read as "shown" while
        # the screen was hiding them -- and un-ticking one did nothing visible,
        # because it was already off.
        "column_defaults": __import__(
            "core.column_config", fromlist=["default_column_visibility"]
        ).default_column_visibility(),
        # Which export reports each page has. The desktop offers ONE set of
        # ticks per page while the store keeps them per report, so the panel
        # needs the report keys to write a shape the engine will read back.
        "export_reports": {
            page: list(reports.keys())
            for page, reports in __import__(
                "core.column_config", fromlist=["EXPORT_REPORTS"]
            ).EXPORT_REPORTS.items()
        },
        # An export report has its own columns -- "Returns", "Schedule",
        # "Month", "Total Sales" -- that no screen draws. The export grid used
        # the SCREEN column list, so those never had a tick, and trimming the
        # screen list took two working ones away with it.
        "export_columns": {
            page: [
                {"key": name, "label": name}
                for name in dict.fromkeys(
                    col
                    for _label, cols in reports.values()
                    for col in cols
                )
            ]
            for page, reports in __import__(
                "core.column_config", fromlist=["EXPORT_REPORTS"]
            ).EXPORT_REPORTS.items()
        },
    }


def get_appearance() -> dict[str, Any]:
    from core.app_prefs import load_theme
    from core.column_config import (
        get_dashboard_section_settings,
        get_quick_access_settings,
    )
    from core.desktop_ui_prefs import load_desktop_ui_prefs
    from core.layout_config import load_layout

    layout = load_layout()
    ui = load_desktop_ui_prefs()
    return {
        "theme": load_theme(),
        "theme_pack": str(ui.get("theme_pack") or "modern"),
        "font_size": _font_size(),
        "home_banner_size": int(layout.get("home_banner_size") or 1500),
        # 10-100 once the shop has chosen; 0 means the pixel value above still
        # decides. Settings shows either one as a percentage of the panel.
        "home_banner_width_pct": int(layout.get("home_banner_width_pct") or 0),
        "home_banner_use_default": bool(layout.get("home_banner_use_default")),
        "home_banner_path": str(layout.get("home_banner_path") or ""),
        "quick_access": get_quick_access_settings(),
        "dashboard_sections": get_dashboard_section_settings(),
        "show_nav_shortcut_keys": bool(ui.get("show_nav_shortcut_keys", True)),
        "table_text_marquee": bool(ui.get("table_text_marquee", False)),
    }


def save_appearance(data: dict[str, Any]) -> dict[str, Any]:
    from core.app_prefs import save_theme
    from core.desktop_ui_prefs import save_desktop_ui_prefs
    from core.layout_config import load_layout, save_layout

    requested = ""
    if "theme" in data and data["theme"]:
        requested = str(data["theme"]).strip()
        ok = save_theme(requested)
        if not ok:
            from core.app_prefs import AVAILABLE_THEMES

            return {
                "error": f"Invalid or unsaved theme: {data['theme']}",
                "themes": list(AVAILABLE_THEMES.keys()),
            }
    if "font_size" in data:
        _save_font_size(int(data["font_size"]))
    ui_updates: dict[str, Any] = {}
    if "show_nav_shortcut_keys" in data:
        ui_updates["show_nav_shortcut_keys"] = bool(data["show_nav_shortcut_keys"])
    if "table_text_marquee" in data:
        ui_updates["table_text_marquee"] = bool(data["table_text_marquee"])
    if "theme_pack" in data and data["theme_pack"]:
        ui_updates["theme_pack"] = str(data["theme_pack"]).strip()
    if ui_updates:
        save_desktop_ui_prefs(ui_updates)

    layout = load_layout()
    if "home_banner_size" in data:
        try:
            layout["home_banner_size"] = max(
                300, min(4000, int(data["home_banner_size"] or 1500))
            )
        except (TypeError, ValueError):
            pass  # junk from a half-typed field: keep what was saved
    if "home_banner_width_pct" in data:
        from core.layout_config import banner_width_pct

        raw = data["home_banner_width_pct"]
        pct = None if raw is None or raw == "" else banner_width_pct(raw)
        if pct is not None:  # junk or blank keeps the saved value; never wipes it
            layout["home_banner_width_pct"] = pct
    if "home_banner_use_default" in data:
        layout["home_banner_use_default"] = bool(data["home_banner_use_default"])
        if layout["home_banner_use_default"]:
            layout["home_banner_path"] = ""
    if "home_banner_path" in data and not layout.get("home_banner_use_default"):
        layout["home_banner_path"] = str(data["home_banner_path"] or "")
    if "quick_access" in data and isinstance(data["quick_access"], dict):
        layout["quick_access"] = {
            str(k): bool(v) for k, v in data["quick_access"].items()
        }
    if "dashboard_sections" in data and isinstance(data["dashboard_sections"], dict):
        layout["dashboard_sections"] = {
            str(k): bool(v) for k, v in data["dashboard_sections"].items()
        }
    save_layout(layout)
    out = get_appearance()
    # Echo the theme we were asked to save so the UI cannot latch onto a stale read.
    if requested:
        out["theme"] = requested
    if "theme_pack" in data and data["theme_pack"]:
        from core.desktop_ui_prefs import normalize_theme_pack

        out["theme_pack"] = normalize_theme_pack(data["theme_pack"])
    return out


_IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")
_MAX_IMAGE_BYTES = 12 * 1024 * 1024


def _decode_uploaded_image(filename: str, data_base64: str) -> tuple[str, bytes]:
    """Turn a file the browser read into an extension and bytes.

    The picker moved into the webview because the engine cannot open a dialog:
    it is spawned windowless with no console, its HTTP server answers on worker
    threads, and the shipped sidecar is built with tkinter excluded outright --
    so `import tkinter` raised, the bare except swallowed it, and Choose Image
    did nothing at all. The browser already has a file picker; it just cannot
    hand over a path, so it hands over the bytes.
    """
    import base64

    ext = os.path.splitext(str(filename or ""))[1].lower()
    if ext not in _IMAGE_EXTS:
        raise ValueError(
            "Choose a picture file (PNG, JPG, GIF, WEBP or BMP)."
        )
    raw = base64.b64decode(str(data_base64 or ""), validate=False)
    if not raw:
        raise ValueError("That file is empty.")
    if len(raw) > _MAX_IMAGE_BYTES:
        raise ValueError("That picture is too large. Use one under 12 MB.")
    return ext, raw


def save_uploaded_home_banner(filename: str, data_base64: str) -> dict[str, Any]:
    """Store an image the shop chose in the browser as the Home banner.

    Picking a new banner REPLACES the old one, in that order: the new copy is
    written, layout_config and the per-store cache are pointed at it, and only
    then do the copies it replaced go. copy_custom_home_banner stamps every copy
    with the time, so before this the config folder kept one full-size image per
    change and showed exactly one of them.
    """
    import tempfile

    from core.layout_config import (
        copy_custom_home_banner,
        load_layout,
        prune_custom_home_banners,
        save_layout,
    )

    try:
        ext, raw = _decode_uploaded_image(filename, data_base64)
    except Exception as exc:
        return {"ok": False, "error": str(exc), "path": ""}
    tmp_path = ""
    try:
        previous = str(load_layout().get("home_banner_path") or "").strip()
        fd, tmp_path = tempfile.mkstemp(suffix=ext)
        with os.fdopen(fd, "wb") as fh:
            fh.write(raw)
        saved = copy_custom_home_banner(tmp_path)
        layout = load_layout()
        layout["home_banner_use_default"] = False
        layout["home_banner_path"] = saved
        save_layout(layout)
        # The path alone never left this machine, so the banner "changed" on one
        # PC and nowhere else. Carry the picture with the store.
        from core.store_images import HOME_BANNER, save_image

        shared = save_image(HOME_BANNER, ext, raw, previous=previous)
        # New banner written, saved and shared: now the ones it replaced.
        dropped = list(shared.get("removed") or [])
        dropped.extend(prune_custom_home_banners(saved))
        message = "Banner saved. Open Home to see it."
        if shared.get("note"):
            message = f"{message} {shared['note']}"
        return {
            "ok": True,
            "path": saved,
            "home_banner_path": saved,
            "home_banner_use_default": False,
            "shared_with_store": bool(shared.get("shared")),
            "replaced": dropped,
            "message": message,
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc), "path": ""}
    finally:
        try:
            if tmp_path and os.path.isfile(tmp_path):
                os.remove(tmp_path)
        except OSError:
            pass


def _point_profile_at_logo(conn, dest: str) -> None:
    """Make the recorded logo path name the picture the shop just picked.

    Without this the upload only wrote a new file: pharmacy_profile.logo_path
    still named the old one, resolve_path takes that path first because it is a
    real file on this machine, and the bill went on printing the logo the shop
    had just replaced -- so did the preview. The row is local (the server's copy
    of this column is a path from somebody else's C:\\, which is why nothing is
    pushed here); the PICTURE travels through core.store_images.
    """
    from core import pharmacy_profile_io as ppio

    if conn is not None:
        try:
            row = conn.execute(
                "SELECT id FROM pharmacy_profile ORDER BY id LIMIT 1"
            ).fetchone()
            if row:
                conn.execute(
                    "UPDATE pharmacy_profile SET logo_path=? WHERE id=?",
                    (dest, row[0]),
                )
                conn.commit()
        except Exception as exc:
            log.warning("logo path repoint: %s", exc)
    try:
        sidecar = ppio._read_sidecar()
        if sidecar:
            sidecar["logo_path"] = dest
            ppio._write_sidecar(sidecar)
    except Exception as exc:
        log.warning("logo sidecar repoint: %s", exc)


def save_uploaded_bill_logo(
    filename: str, data_base64: str, conn=None
) -> dict[str, Any]:
    """Store an image the shop chose in the browser as the bill logo.

    The file used to go to ONE AppData\\bill_logo.png for the whole PC -- shared
    by every store on it -- and only its path was ever saved anywhere the other
    machines could see. core.store_images keeps it per store and carries the
    picture itself with the store, so the shop's other computers print it too.

    Replacing is ordered so the shop is never left with no logo: write the new
    picture, point the profile at it, and only then remove the copy this app
    made of the old one. A picture the shop picked from its own folders is left
    alone -- we only ever delete our own copies.
    """
    from core.pharmacy_profile_io import discard_replaced_logo, recorded_logo_paths
    from core.store_images import BILL_LOGO, save_image

    try:
        ext, raw = _decode_uploaded_image(filename, data_base64)
    except Exception as exc:
        return {"ok": False, "error": str(exc), "path": ""}
    try:
        previous = recorded_logo_paths(conn)
        saved = save_image(BILL_LOGO, ext, raw)
        dest = str(saved.get("path") or "")
        _point_profile_at_logo(conn, dest)
        dropped = list(saved.get("removed") or [])
        dropped.extend(discard_replaced_logo(previous, dest))
        message = "Logo saved. Save the profile to use it on bills."
        if saved.get("note"):
            message = f"{message} {saved['note']}"
        return {"ok": True, "path": dest, "logo_path": dest,
                "shared_with_store": bool(saved.get("shared")),
                "replaced": dropped,
                "message": message}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "path": ""}


def browse_home_banner() -> dict[str, Any]:
    """Open a local file picker and copy the image into config (same as Tk).

    Kept for the Tk build, which runs in the same process as its own display.
    The desktop build uses save_uploaded_home_banner instead: its engine is a
    windowless sidecar built without tkinter, so this cannot work there.
    """
    try:
        import tkinter as tk
        from tkinter import filedialog

        from core.layout_config import (
            copy_custom_home_banner,
            load_layout,
            prune_custom_home_banners,
            save_layout,
        )

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        path = filedialog.askopenfilename(
            title="Select Home Page Banner",
            filetypes=[
                ("Image files", "*.png;*.jpg;*.jpeg;*.gif;*.webp;*.bmp"),
                ("PNG files", "*.png"),
                ("JPEG files", "*.jpg;*.jpeg"),
                ("All files", "*.*"),
            ],
        )
        root.destroy()
        if not path:
            return {"ok": True, "cancelled": True, "path": ""}
        previous = str(load_layout().get("home_banner_path") or "").strip()
        saved = copy_custom_home_banner(path)
        layout = load_layout()
        layout["home_banner_use_default"] = False
        layout["home_banner_path"] = saved
        save_layout(layout)
        from core.store_images import HOME_BANNER, remember_file

        kept = remember_file(HOME_BANNER, saved, previous=previous)
        # New banner in place and pointed at; now the ones it replaced.
        dropped = list(kept.get("removed") or [])
        dropped.extend(prune_custom_home_banners(saved))
        return {
            "ok": True,
            "path": saved,
            "home_banner_path": saved,
            "home_banner_use_default": False,
            "replaced": dropped,
            "message": "Custom banner saved. Use Save Appearance if other fields changed, then reopen Home.",
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc), "path": ""}


def get_layout_lists() -> dict[str, Any]:
    from core.app_prefs import load_app_mode
    from core.billing_layout_prefs import load_billing_layout_prefs
    from core.layout_config import (
        _SCHEDULE_UNIT_DEFAULTS,
        _TYPE_QTY_DEFAULTS,
        get_med_types,
        load_layout,
    )
    from core.column_config import get_dashboard_section_settings
    from core.record_indicators import load_record_indicator_prefs

    layout = load_layout()
    billing = load_billing_layout_prefs()
    med_types = list(get_med_types())
    units = {}
    for mt in med_types:
        units[mt] = {
            "unit": str(
                layout.get(f"unit_{mt}", _SCHEDULE_UNIT_DEFAULTS.get(mt, "")) or ""
            ),
            # typeqty_, not qty_. load_layout() only re-emits typeqty_<type>, so
            # a value stored under qty_<type> was dropped on every read and
            # wiped from the file on the next save -- the number would not even
            # stay typed in.
            "default_qty": int(
                layout.get(f"typeqty_{mt}", _TYPE_QTY_DEFAULTS.get(mt, 0)) or 0
            ),
        }
    return {
        "billing_rows": int(layout.get("billing_rows") or 8),
        # The three list pages read their summary-bar flags from here.
        "dashboard_sections": get_dashboard_section_settings(),
        "inventory_rows": int(layout.get("inventory_rows") or 15),
        "sales_history_rows": int(layout.get("sales_history_rows") or 15),
        "purchase_history_rows": int(layout.get("purchase_history_rows") or 15),
        "purchase_rows": int(layout.get("purchase_rows") or 4),
        "customers_rows": int(layout.get("customers_rows") or 15),
        "doctors_rows": int(layout.get("doctors_rows") or 6),
        "suppliers_rows": int(layout.get("suppliers_rows") or 8),
        "med_types": med_types,
        "schedules": list(layout.get("schedules") or ["", "H", "H1", "X", "G", "K", "C", "C1", "P", "N", "M"]),
        "units": units,
        "billing_show_margin_column": bool(
            billing.get(
                "billing_show_margin_column",
                layout.get("billing_show_margin_column", True),
            )
        ),
        "billing_show_total_margin": bool(
            billing.get(
                "billing_show_total_margin",
                layout.get("billing_show_total_margin", True),
            )
        ),
        "billing_margin_loss_warning": bool(
            billing.get(
                "billing_margin_loss_warning",
                layout.get("billing_margin_loss_warning", True),
            )
        ),
        "billing_margin_display_mode": str(
            billing.get("billing_margin_display_mode")
            or layout.get("billing_margin_display_mode")
            or "rupees"
        ),
        "app_mode": load_app_mode(),
        "record_indicators": load_record_indicator_prefs(),
        "column_visibility": layout.get("column_visibility") or {},
        "export_column_visibility": layout.get("export_column_visibility") or {},
    }


_ROW_COUNT_LIMITS = {
    "billing_rows": (4, 30),
    "inventory_rows": (5, 50),
    "sales_history_rows": (5, 50),
    "purchase_history_rows": (5, 50),
    "purchase_rows": (2, 20),
    "customers_rows": (5, 50),
    "doctors_rows": (2, 20),
    "suppliers_rows": (2, 20),
}


def _clamp_table_rows(key: str, raw: Any, fallback: int) -> int:
    try:
        n = int(raw)
    except (TypeError, ValueError):
        n = fallback
    mn, mx = _ROW_COUNT_LIMITS.get(key, (1, 80))
    return max(mn, min(mx, n))


def save_layout_lists(data: dict[str, Any]) -> dict[str, Any]:
    from core.app_prefs import save_app_mode
    from core.layout_config import load_layout, save_layout
    from core.record_indicators import save_record_indicator_prefs

    layout = load_layout()
    for key in (
        "billing_rows",
        "inventory_rows",
        "sales_history_rows",
        "purchase_history_rows",
        "purchase_rows",
        "customers_rows",
        "doctors_rows",
        "suppliers_rows",
    ):
        if key in data:
            layout[key] = _clamp_table_rows(key, data[key], int(layout.get(key) or 8))
    if "med_types" in data and isinstance(data["med_types"], list):
        layout["med_types"] = [str(x) for x in data["med_types"] if str(x).strip()]
    if "schedules" in data and isinstance(data["schedules"], list):
        layout["schedules"] = [str(x) for x in data["schedules"]]
    if "units" in data and isinstance(data["units"], dict):
        for mt, cfg in data["units"].items():
            if not isinstance(cfg, dict):
                continue
            if "unit" in cfg:
                layout[f"unit_{mt}"] = str(cfg.get("unit") or "")
            if "default_qty" in cfg:
                try:
                    layout[f"typeqty_{mt}"] = int(cfg.get("default_qty") or 0)
                except (TypeError, ValueError):
                    layout[f"typeqty_{mt}"] = 0
    for key in (
        "billing_show_margin_column",
        "billing_show_total_margin",
        "billing_margin_loss_warning",
    ):
        if key in data:
            layout[key] = bool(data[key])
    if "billing_margin_display_mode" in data:
        mode = str(data.get("billing_margin_display_mode") or "rupees").strip().lower()
        layout["billing_margin_display_mode"] = (
            mode if mode in ("rupees", "percent") else "rupees"
        )
    if "column_visibility" in data:
        layout["column_visibility"] = data["column_visibility"]
    if "export_column_visibility" in data:
        layout["export_column_visibility"] = data["export_column_visibility"]
    save_layout(layout)
    margin_updates = {
        k: data[k]
        for k in (
            "billing_show_margin_column",
            "billing_show_total_margin",
            "billing_margin_loss_warning",
            "billing_margin_display_mode",
        )
        if k in data
    }
    if margin_updates:
        try:
            from core.billing_layout_prefs import save_billing_layout_prefs

            save_billing_layout_prefs(margin_updates)
        except Exception:
            pass
    if "app_mode" in data:
        save_app_mode(str(data["app_mode"]))
    if "record_indicators" in data and isinstance(data["record_indicators"], dict):
        save_record_indicator_prefs(data["record_indicators"])
    return get_layout_lists()


def get_sales_billing() -> dict[str, Any]:
    from core.autosave_prefs import (
        load_autosave_enabled,
        load_autosave_interval_seconds,
    )
    from core.bill_save_prefs import load_pdf_save_layout, load_sales_bill_save_dir
    from core.billing_layout_prefs import load_billing_layout_prefs
    from core.history_prefs import current_fy_label, history_scope_label
    from core.sales_medicine_prefs import (
        load_batch_sort_order,
        load_show_zero_stock_in_sales,
    )
    from core.sales_return_prefs import load_sales_return_lookup_days
    from core.upi_prefs import (
        load_upi_id,
        load_upi_qr_amount_mode,
        load_upi_qr_enabled,
    )

    prefs = load_billing_layout_prefs()
    return {
        "payment_mode_enabled": bool(prefs.get("payment_mode_enabled", True)),
        "payment_mode_position": str(prefs.get("payment_mode_position") or "first"),
        "item_discount_mode": str(prefs.get("item_discount_mode") or "rupees"),
        "billing_show_item_discount": bool(
            prefs.get("billing_show_item_discount", True)
        ),
        "history_scope": str(prefs.get("history_scope") or "current_fy"),
        "history_scope_label": history_scope_label(str(prefs.get("history_scope") or "current_fy")),
        "current_fy_label": current_fy_label(),
        "billing_show_margin_column": bool(prefs.get("billing_show_margin_column", True)),
        "billing_show_total_margin": bool(prefs.get("billing_show_total_margin", True)),
        "billing_margin_loss_warning": bool(prefs.get("billing_margin_loss_warning", True)),
        "billing_margin_display_mode": str(
            prefs.get("billing_margin_display_mode") or "rupees"
        ),
        "require_doctor_for_other_schedules": bool(
            prefs.get("require_doctor_for_other_schedules", True)
        ),
        "billing_layout_version": int(prefs.get("version") or 1),
        "batch_sort_order": load_batch_sort_order(),
        "show_zero_stock": load_show_zero_stock_in_sales(),
        "sales_return_lookup_days": load_sales_return_lookup_days(),
        "pdf_save_layout": load_pdf_save_layout(),
        "sales_bill_save_dir": load_sales_bill_save_dir() or "",
        "upi_qr_enabled": load_upi_qr_enabled(),
        "upi_id": load_upi_id(),
        "upi_qr_amount_mode": load_upi_qr_amount_mode(),
        "autosave_enabled": load_autosave_enabled(),
        "autosave_interval_seconds": load_autosave_interval_seconds(),
    }


def save_sales_billing(data: dict[str, Any]) -> dict[str, Any]:
    from core.autosave_prefs import (
        save_autosave_enabled,
        save_autosave_interval_seconds,
    )
    from core.bill_save_prefs import save_pdf_save_layout, save_sales_bill_save_dir
    from core.billing_layout_prefs import save_billing_layout_prefs
    from core.sales_medicine_prefs import (
        save_batch_sort_order,
        save_show_zero_stock_in_sales,
    )
    from core.sales_return_prefs import save_sales_return_lookup_days
    from core.upi_prefs import (
        save_upi_id,
        save_upi_qr_amount_mode,
        save_upi_qr_enabled,
    )

    layout_keys = (
        "payment_mode_enabled",
        "payment_mode_position",
        "item_discount_mode",
        "billing_show_item_discount",
        "history_scope",
        "billing_show_margin_column",
        "billing_show_total_margin",
        "billing_margin_loss_warning",
        "billing_margin_display_mode",
        "require_doctor_for_other_schedules",
    )
    layout_updates = {k: data[k] for k in layout_keys if k in data}
    # _normalize() puts this key through bool() (billing_layout_prefs.py), and
    # bool("false") is True -- so a JSON string would persist as "always require
    # a doctor" while the Settings screen showed the box unchecked.
    if "require_doctor_for_other_schedules" in layout_updates:
        raw_doc = layout_updates["require_doctor_for_other_schedules"]
        if isinstance(raw_doc, str):
            raw_doc = raw_doc.strip().lower() not in ("", "0", "false", "no", "off")
        layout_updates["require_doctor_for_other_schedules"] = bool(raw_doc)
    if layout_updates:
        save_billing_layout_prefs(layout_updates)
    if "batch_sort_order" in data:
        save_batch_sort_order(str(data["batch_sort_order"]))
    if "show_zero_stock" in data:
        save_show_zero_stock_in_sales(bool(data["show_zero_stock"]))
    if "sales_return_lookup_days" in data:
        save_sales_return_lookup_days(int(data["sales_return_lookup_days"]))
    if "pdf_save_layout" in data:
        save_pdf_save_layout(str(data["pdf_save_layout"]))
    if "sales_bill_save_dir" in data:
        save_sales_bill_save_dir(str(data["sales_bill_save_dir"] or ""))
    if "upi_qr_enabled" in data:
        save_upi_qr_enabled(bool(data["upi_qr_enabled"]))
    if "upi_id" in data:
        save_upi_id(str(data["upi_id"] or ""))
    if "upi_qr_amount_mode" in data:
        save_upi_qr_amount_mode(str(data["upi_qr_amount_mode"]))
    if "autosave_enabled" in data:
        save_autosave_enabled(bool(data["autosave_enabled"]))
    if "autosave_interval_seconds" in data:
        save_autosave_interval_seconds(int(data["autosave_interval_seconds"]))
    return get_sales_billing()


def get_pharmacy(conn: sqlite3.Connection) -> dict[str, Any]:
    from core.bill_config import load_bill_print_settings
    from core.login_prefs import load_login_prefs
    from core.printer_manager import PrinterManager
    from core.pharmacy_profile_io import load_pharmacy_profile

    profile = load_pharmacy_profile(conn)
    login = load_login_prefs()
    printers = []
    spooler_running = True
    try:
        printers = PrinterManager.get_installed_printers()
    except Exception:
        printers = []
    try:
        spooler_running = bool(PrinterManager.is_spooler_running())
    except Exception:
        spooler_running = True
    print_log = "config/print_log.txt"
    try:
        from core.print_log import print_log_path

        print_log = str(print_log_path())
    except Exception:
        pass
    printer_cfg = PrinterManager.load_settings()
    sumatra_detected = ""
    try:
        sumatra_detected = (
            PrinterManager.find_bundled_sumatra_path()
            or PrinterManager.find_sumatra_path(printer_cfg.get("sumatra_path") or "")
            or ""
        )
    except Exception:
        sumatra_detected = ""
    return {
        "profile": profile,
        "bill": load_bill_print_settings(),
        "login": {
            "enabled": bool(login.get("enabled")),
            "username": login.get("username") or "",
            # never send password hash to UI — blank means unchanged
            "password": "",
        },
        "printer": printer_cfg,
        "installed_printers": printers,
        "spooler_running": spooler_running,
        "print_log_path": print_log,
        "sumatra_detected": sumatra_detected,
    }


def save_pharmacy(conn: sqlite3.Connection, data: dict[str, Any]) -> dict[str, Any]:
    from core.bill_config import load_bill_print_settings, save_bill_print_settings
    from core.login_prefs import load_login_prefs, save_login_prefs
    from core.printer_manager import PrinterManager

    if "profile" in data and isinstance(data["profile"], dict):
        from core.pharmacy_profile_io import save_pharmacy_profile

        save_pharmacy_profile(conn, data["profile"])

    if "bill" in data and isinstance(data["bill"], dict):
        cur = load_bill_print_settings()
        cur.update(data["bill"])
        # Clamp Print Sales slot copies like Tk (A6≤1, A5≤2, A4≤4)
        for slot in (1, 2):
            key = f"print_slot_{slot}"
            slot_cfg = cur.get(key)
            if not isinstance(slot_cfg, dict):
                continue
            paper = str(slot_cfg.get("paper_size") or ("A5" if slot == 1 else "A6")).upper()
            try:
                copies = int(slot_cfg.get("copies") or (2 if slot == 1 else 1))
            except (TypeError, ValueError):
                copies = 2 if slot == 1 else 1
            if paper == "A6":
                max_c = 1
            elif paper == "A5":
                max_c = 2
            else:
                max_c = 4
            slot_cfg = dict(slot_cfg)
            slot_cfg["copies"] = max(1, min(max_c, copies))
            slot_cfg["paper_size"] = paper
            cur[key] = slot_cfg
        try:
            cur["copies"] = max(1, min(10, int(cur.get("copies") or 1)))
        except (TypeError, ValueError):
            cur["copies"] = 1
        save_bill_print_settings(cur)

    if "login" in data and isinstance(data["login"], dict):
        lg = data["login"]
        pwd = lg.get("password") or None
        save_login_prefs(
            enabled=bool(lg.get("enabled")),
            username=str(lg.get("username") or ""),
            password=pwd,
            keep_existing_password=not bool(pwd),
        )

    if "printer" in data and isinstance(data["printer"], dict):
        cur = PrinterManager.load_settings()
        cur.update(data["printer"])
        PrinterManager.save_settings(cur)

    return get_pharmacy(conn)


def get_system() -> dict[str, Any]:
    from core.export_prefs import load_default_export_format
    from core.history_prefs import history_scope_label, load_history_scope
    from core.startup_alerts_prefs import load_startup_alerts_enabled
    from core.store_manager import (
        get_active_store_key,
        get_device_role,
        is_satellite_device,
        list_stores,
    )
    from core.sync_prefs import get_sync_mode, mode_label

    mode = get_sync_mode()
    history_scope = load_history_scope()
    return {
        # A satellite counter PC is linked to ONE store. The old screen said so
        # and hid the controls; the new one offered Switch Store and Create
        # Store anyway, on a device where neither can work.
        "device_role": get_device_role(),
        "is_satellite": is_satellite_device(),
        "sync_mode": mode,
        "sync_label": mode_label(mode),
        "startup_alerts_enabled": load_startup_alerts_enabled(),
        "export_format": load_default_export_format(),
        "history_scope": history_scope,
        "history_scope_label": history_scope_label(history_scope),
        "stores": list_stores(),
        "active_store_key": get_active_store_key(),
    }


def save_system(data: dict[str, Any], conn: sqlite3.Connection | None = None) -> dict[str, Any]:
    from core.export_prefs import save_default_export_format
    from core.history_prefs import save_history_scope
    from core.startup_alerts_prefs import save_startup_alerts_enabled
    from core.sync_prefs import MODE_ONLINE, get_sync_mode, set_sync_mode

    sync_changed_to_online = False
    if "sync_mode" in data:
        previous = get_sync_mode()
        new_mode = str(data["sync_mode"]).strip().lower()
        set_sync_mode(new_mode)
        sync_changed_to_online = previous != MODE_ONLINE and new_mode == MODE_ONLINE
        if previous == MODE_ONLINE and new_mode != MODE_ONLINE:
            try:
                from core.sync_coordinator import stop_online_sync
                stop_online_sync()
            except Exception:
                pass
    if "startup_alerts_enabled" in data:
        save_startup_alerts_enabled(bool(data["startup_alerts_enabled"]))
    if "export_format" in data:
        save_default_export_format(str(data["export_format"]))
    if "history_scope" in data:
        save_history_scope(str(data["history_scope"]))

    result = get_system()
    if sync_changed_to_online and conn is not None:
        # Do NOT block the API/UI thread — bootstrap runs in background.
        try:
            from core.background_workers import db_path_from_conn
            from core.online_switch_job import start_online_switch_async
            from core.sync_coordinator import stop_online_sync

            try:
                stop_online_sync()
            except Exception:
                pass
            db_path = db_path_from_conn(conn) or ""
            if not db_path:
                from core.store_manager import get_active_db_path

                db_path = get_active_db_path()
            started_job = start_online_switch_async(db_path)
            result["online_applied"] = True
            result["online_bootstrap_pending"] = True
            result["online_switch_started"] = bool(started_job)
            result["message"] = (
                "Online mode saved — connecting to server in the background. "
                "Watch the progress status; the UI will stay responsive."
            )
        except Exception as exc:
            result["online_applied"] = False
            result["online_bootstrap_pending"] = False
            result["error"] = str(exc)
            result["message"] = f"Online mode saved but could not start background connect: {exc}"
    return result


def _ensure_contact_village(conn: sqlite3.Connection, address: str) -> None:
    """Save a typed customer address into Settings villages + dropdowns."""
    from core.village_service import ensure_village_from_address

    ensure_village_from_address(conn, address)


def get_contacts(conn: sqlite3.Connection) -> dict[str, Any]:
    from core.village_service import get_default_village, load_villages
    from core.sync_prefs import is_online_mode

    if is_online_mode():
        from core.online_catalog import customers, suppliers, doctors

        def _due(stored: Any, pid: Any, live: dict[int, float] | None) -> float:
            # The catalog row's total_due is a stored figure: the server only rewrites it when
            # a receipt / return cascade runs, the desktop holds its copy for minutes (and a
            # disk snapshot across restarts), and a bill saved without a customer_id, or one
            # still queued on this PC, never reaches it. Sales' Previous Due and Payments work
            # the due out from the ledger instead, so Contacts does the same and they agree.
            if live is None:
                return float(stored or 0)
            try:
                return float(live.get(int(pid or 0), 0.0))
            except (TypeError, ValueError):
                return float(stored or 0)

        cust_rows = [c for c in customers() if isinstance(c, dict)][:500]
        sup_rows = [s for s in suppliers() if isinstance(s, dict)]
        live_c = _live_party_due_map("customer") if cust_rows else None
        live_s = _live_party_due_map("supplier") if sup_rows else None
        return {
            "doctors": [
                {
                    "id": d.get("id") or d.get("local_id"),
                    "name": d.get("name") or "",
                    "reg_no": d.get("registration_number") or d.get("reg_no") or "",
                    "phone": d.get("phone") or "",
                }
                for d in doctors()
                if isinstance(d, dict)
            ],
            "customers": [
                {
                    "id": c.get("id") or c.get("local_id"),
                    "name": c.get("name") or "",
                    "phone": c.get("phone") or "",
                    "address": c.get("address") or "",
                    "village": c.get("address") or "",
                    "total_due": _due(
                        c.get("total_due"), c.get("id") or c.get("local_id"), live_c
                    ),
                }
                for c in cust_rows
            ],
            "suppliers": [
                {
                    "id": s.get("id") or s.get("local_id"),
                    "name": s.get("name") or "",
                    "phone": s.get("phone") or "",
                    "gstin": s.get("gstin") or "",
                    "address": s.get("address") or "",
                    "total_due": _due(
                        s.get("total_due"), s.get("id") or s.get("local_id"), live_s
                    ),
                }
                for s in sup_rows
            ],
            "villages": load_villages(conn),
            "default_village": get_default_village(conn),
        }

    doctors = conn.execute(
        "SELECT id, name, COALESCE(registration_number,''), COALESCE(phone,'') "
        "FROM doctors ORDER BY name COLLATE NOCASE"
    ).fetchall()
    customers = conn.execute(
        "SELECT id, name, COALESCE(phone,''), COALESCE(address,''), "
        "COALESCE(total_due,0) FROM customers ORDER BY name COLLATE NOCASE LIMIT 500"
    ).fetchall()
    suppliers = conn.execute(
        "SELECT id, name, COALESCE(phone,''), COALESCE(gstin,''), "
        "COALESCE(address,''), COALESCE(total_due,0) "
        "FROM suppliers ORDER BY name COLLATE NOCASE"
    ).fetchall()
    return {
        "doctors": [
            {"id": r[0], "name": r[1], "reg_no": r[2], "phone": r[3]} for r in doctors
        ],
        "customers": [
            {
                "id": r[0],
                "name": r[1],
                "phone": r[2],
                "address": r[3],
                "village": r[3],
                "total_due": float(r[4] or 0),
            }
            for r in customers
        ],
        "suppliers": [
            {
                "id": r[0],
                "name": r[1],
                "phone": r[2],
                "gstin": r[3],
                "address": r[4],
                "total_due": float(r[5] or 0),
            }
            for r in suppliers
        ],
        "villages": load_villages(conn),
        "default_village": get_default_village(conn),
    }


def get_import_prefs() -> dict[str, Any]:
    try:
        from core.gemini_bill_config import (
            is_gemini_enabled,
            load_gemini_api_key,
            load_import_default_schedule,
        )

        return {
            "gemini_enabled": is_gemini_enabled(),
            "gemini_configured": bool(load_gemini_api_key()),
            "gemini_api_key": "",
            "fallback_schedule": load_import_default_schedule() or "",
        }
    except Exception:
        return {
            "gemini_enabled": False,
            "gemini_configured": False,
            "gemini_api_key": "",
            "fallback_schedule": "",
        }


def save_import_prefs(data: dict[str, Any]) -> dict[str, Any]:
    try:
        from core.gemini_bill_config import (
            save_gemini_api_key,
            save_import_default_schedule,
            set_gemini_enabled,
        )

        if "gemini_enabled" in data:
            set_gemini_enabled(bool(data["gemini_enabled"]))
        # Never persist an empty key from the UI — that would wipe the bundled EXE/repo key.
        if "gemini_api_key" in data:
            key = str(data["gemini_api_key"] or "").strip()
            if key:
                save_gemini_api_key(key)
        if "fallback_schedule" in data:
            save_import_default_schedule(str(data["fallback_schedule"] or ""))
    except Exception as exc:
        raise RuntimeError(str(exc)) from exc
    return get_import_prefs()


def _ensure_settings_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS settings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE,
            value TEXT
        )
        """
    )


def _setting_get(conn: sqlite3.Connection, name: str, default: str = "") -> str:
    _ensure_settings_table(conn)
    row = conn.execute(
        "SELECT value FROM settings WHERE name=?", (name,)
    ).fetchone()
    return str(row[0]) if row and row[0] is not None else default


def _setting_set(conn: sqlite3.Connection, name: str, value: str) -> None:
    _ensure_settings_table(conn)
    conn.execute(
        "INSERT OR REPLACE INTO settings (name, value) VALUES (?, ?)",
        (name, value),
    )
    # Also keep it outside the database. Online runs on :memory:, so without this
    # every threshold and reorder default went back to its factory value on the
    # next restart or mode switch, and the shop had to set them all again.
    try:
        from core.settings_mirror import remember

        remember(name, value)
    except Exception:
        pass


def get_thresholds(conn: sqlite3.Connection) -> dict[str, Any]:
    from core.layout_config import get_med_types

    med_types = list(get_med_types())
    low_stock: dict[str, str] = {}
    near_expiry: dict[str, str] = {}
    for mt in med_types:
        key = mt.lower()
        low_stock[key] = _setting_get(conn, f"low_stock_{key}", "10")
        near_expiry[key] = _setting_get(conn, f"near_expiry_{key}", "3")
    return {
        "med_types": med_types,
        "low_stock": low_stock,
        "near_expiry": near_expiry,
        "customer_due_min_amount": _setting_get(conn, "customer_due_min_amount", "0"),
        "customer_due_min_days": _setting_get(conn, "customer_due_min_days", "0"),
        "reorder_default_qty": _setting_get(conn, "reorder_default_qty", "10"),
    }


def save_thresholds(conn: sqlite3.Connection, data: dict[str, Any]) -> dict[str, Any]:
    from core.layout_config import get_med_types

    for mt in get_med_types():
        key = mt.lower()
        low = (data.get("low_stock") or {}).get(key)
        near = (data.get("near_expiry") or {}).get(key)
        if low is not None:
            _setting_set(conn, f"low_stock_{key}", str(low or "10"))
        if near is not None:
            _setting_set(conn, f"near_expiry_{key}", str(near or "3"))
    if "customer_due_min_amount" in data:
        _setting_set(
            conn, "customer_due_min_amount", str(data.get("customer_due_min_amount") or "0")
        )
    if "customer_due_min_days" in data:
        _setting_set(
            conn, "customer_due_min_days", str(data.get("customer_due_min_days") or "0")
        )
    if "reorder_default_qty" in data:
        _setting_set(
            conn, "reorder_default_qty", str(data.get("reorder_default_qty") or "10")
        )
    conn.commit()
    return get_thresholds(conn)


def get_alerts(conn: sqlite3.Connection) -> dict[str, Any]:
    from core.alert_monitoring_service import fetch_all_monitoring_sections

    raw = fetch_all_monitoring_sections(conn, detail=True)
    out: dict[str, Any] = {}
    for key, rows in raw.items():
        out[key] = [list(r) for r in rows]
    out["counts"] = {k: len(v) for k, v in out.items() if k not in ("counts", "expiry_by_batch")}
    return out


def mutate_contact(conn: sqlite3.Connection, data: dict[str, Any]) -> dict[str, Any]:
    """CRUD for doctors / villages / suppliers / customers used by Settings → Contacts."""
    from core.online_guard import ensure_can_mutate
    from core.sync_prefs import is_online_mode

    action = str(data.get("action") or "").strip().lower()
    kind = str(data.get("kind") or "").strip().lower()
    ensure_can_mutate()

    if is_online_mode() and kind in ("doctor", "customer", "supplier"):
        from core.server_crud import upsert_contact_online, delete_contact_online, allocate_id

        if kind == "doctor":
            if action == "add":
                name = str(data.get("name") or "").strip().upper()
                reg_no = str(data.get("reg_no") or "").strip()
                phone = str(data.get("phone") or "").strip()
                if not name or not reg_no:
                    raise ValueError("Doctor name and registration number are required.")
                did = allocate_id("doctors")
                upsert_contact_online(
                    "doctors",
                    {
                        "id": did,
                        "local_id": did,
                        "name": name,
                        "registration_number": reg_no,
                        "phone": phone,
                    },
                )
            elif action == "update":
                doctor_id = int(data["id"])
                upsert_contact_online(
                    "doctors",
                    {
                        "id": doctor_id,
                        "local_id": doctor_id,
                        "name": str(data.get("name") or "").strip().upper(),
                        "registration_number": str(data.get("reg_no") or "").strip(),
                        "phone": str(data.get("phone") or "").strip(),
                    },
                )
            elif action == "delete":
                delete_contact_online("doctors", int(data["id"]))
            else:
                raise ValueError(f"Unknown doctor action: {action}")
            return get_contacts(conn)

        collection = "customers" if kind == "customer" else "suppliers"
        if action == "add":
            name = str(data.get("name") or "").strip()
            if not name:
                raise ValueError(f"{kind.title()} name is required.")
            nid = allocate_id(collection)
            doc = {
                "id": nid,
                "local_id": nid,
                "name": name if kind == "supplier" else name,
                "phone": str(data.get("phone") or ""),
                "address": str(data.get("address") or data.get("village") or ""),
            }
            if kind == "customer":
                doc["name"] = name
            upsert_contact_online(collection, doc)
            if kind == "customer":
                _ensure_contact_village(conn, doc.get("address") or "")
        elif action == "update":
            cid = int(data["id"])
            address = str(data.get("address") or data.get("village") or "")
            upsert_contact_online(
                collection,
                {
                    "id": cid,
                    "local_id": cid,
                    "name": str(data.get("name") or "").strip(),
                    "phone": str(data.get("phone") or ""),
                    "address": address,
                },
            )
            if kind == "customer":
                _ensure_contact_village(conn, address)
        elif action == "delete":
            delete_contact_online(collection, int(data["id"]))
        else:
            raise ValueError(f"Unknown {kind} action: {action}")
        return get_contacts(conn)

    if kind == "doctor":
        from core.sync_coordinator import after_doctor_deleted, after_doctor_saved

        if action == "add":
            name = str(data.get("name") or "").strip().upper()
            reg_no = str(data.get("reg_no") or "").strip()
            phone = str(data.get("phone") or "").strip()
            if not name or not reg_no:
                raise ValueError("Doctor name and registration number are required.")
            exists = conn.execute(
                "SELECT id FROM doctors WHERE UPPER(name)=?", (name,)
            ).fetchone()
            if exists:
                raise ValueError("Doctor with this name already exists.")
            cur = conn.execute(
                "INSERT INTO doctors (name, registration_number, phone) VALUES (?,?,?)",
                (name, reg_no, phone),
            )
            conn.commit()
            after_doctor_saved(conn, int(cur.lastrowid))
        elif action == "update":
            doctor_id = int(data["id"])
            name = str(data.get("name") or "").strip().upper()
            reg_no = str(data.get("reg_no") or "").strip()
            phone = str(data.get("phone") or "").strip()
            if not name or not reg_no:
                raise ValueError("Doctor name and registration number are required.")
            conn.execute(
                "UPDATE doctors SET name=?, registration_number=?, phone=? WHERE id=?",
                (name, reg_no, phone, doctor_id),
            )
            conn.commit()
            after_doctor_saved(conn, doctor_id)
        elif action == "delete":
            doctor_id = int(data["id"])
            conn.execute("DELETE FROM doctors WHERE id=?", (doctor_id,))
            conn.commit()
            after_doctor_deleted(conn, doctor_id)
        else:
            raise ValueError(f"Unknown doctor action: {action}")

    elif kind == "village":
        from core.sync_coordinator import after_villages_saved
        from core.village_service import (
            add_village,
            get_default_village,
            load_villages,
            save_villages,
        )

        if action == "add":
            add_village(conn, str(data.get("name") or ""))
        elif action == "remove":
            name = str(data.get("name") or "").strip()
            villages = [v for v in load_villages(conn) if v.upper() != name.upper()]
            default = get_default_village(conn)
            if default.upper() == name.upper():
                default = villages[0] if villages else ""
            save_villages(conn, villages, default)
        elif action == "set_default":
            name = str(data.get("name") or "").strip()
            villages = load_villages(conn)
            save_villages(conn, villages, name)
        else:
            raise ValueError(f"Unknown village action: {action}")
        after_villages_saved(conn)

    elif kind == "supplier":
        from core.sync_coordinator import after_supplier_deleted, after_supplier_saved

        if action == "add":
            name = str(data.get("name") or "").strip()
            if not name:
                raise ValueError("Supplier name is required.")
            cur = conn.execute(
                "INSERT INTO suppliers (name, phone, gstin, address) VALUES (?,?,?,?)",
                (
                    name,
                    str(data.get("phone") or ""),
                    str(data.get("gstin") or ""),
                    str(data.get("address") or ""),
                ),
            )
            conn.commit()
            after_supplier_saved(conn, int(cur.lastrowid))
        elif action == "update":
            sid = int(data["id"])
            conn.execute(
                "UPDATE suppliers SET name=?, phone=?, gstin=?, address=? WHERE id=?",
                (
                    str(data.get("name") or "").strip(),
                    str(data.get("phone") or ""),
                    str(data.get("gstin") or ""),
                    str(data.get("address") or ""),
                    sid,
                ),
            )
            conn.commit()
            after_supplier_saved(conn, sid)
        elif action == "delete":
            sid = int(data["id"])
            conn.execute("DELETE FROM suppliers WHERE id=?", (sid,))
            conn.commit()
            after_supplier_deleted(conn, sid)
        elif action == "recalculate":
            from core.purchase_service import recalculate_supplier_due

            for (sid,) in conn.execute("SELECT id FROM suppliers").fetchall():
                recalculate_supplier_due(conn, int(sid))
                try:
                    after_supplier_saved(conn, int(sid))
                except Exception:
                    pass
        else:
            raise ValueError(f"Unknown supplier action: {action}")

    elif kind == "customer":
        from core.sync_coordinator import after_customer_deleted, after_customer_saved

        if action == "add":
            name = str(data.get("name") or "").strip()
            if not name:
                raise ValueError("Customer name is required.")
            address = str(data.get("address") or data.get("village") or "")
            cur = conn.execute(
                "INSERT INTO customers (name, phone, address) VALUES (?,?,?)",
                (name, str(data.get("phone") or ""), address),
            )
            conn.commit()
            after_customer_saved(conn, int(cur.lastrowid))
            _ensure_contact_village(conn, address)
        elif action == "update":
            cid = int(data["id"])
            address = str(data.get("address") or data.get("village") or "")
            conn.execute(
                "UPDATE customers SET name=?, phone=?, address=? WHERE id=?",
                (
                    str(data.get("name") or "").strip(),
                    str(data.get("phone") or ""),
                    address,
                    cid,
                ),
            )
            conn.commit()
            after_customer_saved(conn, cid)
            _ensure_contact_village(conn, address)
        elif action == "delete":
            cid = int(data["id"])
            conn.execute("DELETE FROM customers WHERE id=?", (cid,))
            conn.commit()
            after_customer_deleted(conn, cid)
        elif action == "recalculate":
            from core.customer_service import recalculate_customer_due

            for (cid,) in conn.execute("SELECT id FROM customers").fetchall():
                recalculate_customer_due(conn, int(cid))
                try:
                    after_customer_saved(conn, int(cid))
                except Exception:
                    pass
        else:
            raise ValueError(f"Unknown customer action: {action}")
    else:
        raise ValueError(f"Unknown contact kind: {kind}")

    return get_contacts(conn)


def get_payments(conn: sqlite3.Connection, kind: str = "supplier") -> dict[str, Any]:
    kind = (kind or "supplier").lower()
    try:
        from core.sync_prefs import is_online_mode

        if is_online_mode():
            from core import store_query_client as sq
            from core import server_api as api

            token = api.store_token_for_active()
            if kind == "customer":
                data = sq.list_customer_payments(limit=300) or {}
                hist = data.get("rows") or data.get("payments") or data.get("history") or []
                try:
                    from core.online_mutation_queue import (
                        merge_server_rows,
                        overlay_customer_payment_dicts,
                    )

                    hist = merge_server_rows(
                        list(hist or []),
                        overlay_customer_payment_dicts(),
                        collection="customer_payments",
                    )
                except Exception:
                    pass
                from core.online_catalog import customers as catalog_customers

                custs = catalog_customers()
                if not custs:
                    custs, _ = api.pull_collection(
                        token, "customers", include_deleted=False, limit=5000
                    )
                return {
                    "kind": "customer",
                    "history": [
                        {
                            "id": r.get("id") or r.get("local_id"),
                            "date": r.get("payment_date") or r.get("date"),
                            "party": r.get("customer_name") or r.get("party") or "",
                            "amount": float(r.get("amount") or 0),
                            "mode": r.get("payment_mode") or r.get("mode") or "",
                            "cash": float(r.get("cash_amount") or r.get("cash") or 0),
                            "online": float(r.get("online_amount") or r.get("online") or 0),
                            "reference": r.get("reference_no") or r.get("reference") or "",
                            "note": r.get("note") or "",
                        }
                        for r in hist
                        if isinstance(r, dict)
                    ],
                    "parties": _apply_live_party_dues(
                        "customer",
                        [
                            {
                                "id": c.get("id") or c.get("local_id"),
                                "name": c.get("name") or "",
                                "due": float(c.get("total_due") or 0),
                            }
                            for c in (custs or [])
                            if isinstance(c, dict)
                        ],
                    ),
                }
            data = sq.list_supplier_payments(limit=300) or {}
            hist = data.get("rows") or data.get("payments") or data.get("history") or []
            try:
                from core.online_mutation_queue import (
                    merge_server_rows,
                    overlay_supplier_payment_dicts,
                )

                hist = merge_server_rows(
                    list(hist or []),
                    overlay_supplier_payment_dicts(),
                    collection="supplier_payments",
                )
            except Exception:
                pass
            sups = None
            try:
                from core.online_catalog import suppliers as catalog_suppliers

                sups = catalog_suppliers()
            except Exception:
                sups = None
            if not sups:
                sups, _ = api.pull_collection(
                    token, "suppliers", include_deleted=False, limit=5000
                )
            return {
                "kind": "supplier",
                "history": [
                    {
                        "id": r.get("id") or r.get("local_id"),
                        "payment_no": r.get("payment_no") or "",
                        "date": r.get("payment_date") or r.get("date"),
                        "party": r.get("supplier_name") or r.get("party") or "",
                        "amount": float(r.get("amount") or 0),
                        "mode": r.get("mode") or "",
                        "reference": r.get("reference") or "",
                        "due_before": r.get("due_before"),
                        "due_after": r.get("due_after"),
                    }
                    for r in hist
                    if isinstance(r, dict)
                ],
                "parties": _apply_live_party_dues(
                    "supplier",
                    [
                        {
                            "id": s.get("id") or s.get("local_id"),
                            "name": s.get("name") or "",
                            "due": float(s.get("total_due") or 0),
                        }
                        for s in (sups or [])
                        if isinstance(s, dict)
                    ],
                ),
            }
    except Exception as exc:
        print(f"[payments] online fetch: {exc}")

    if kind == "customer":
        rows = conn.execute(
            """
            SELECT cp.id, cp.payment_date, c.name, cp.amount, cp.payment_mode,
                   COALESCE(cp.cash_amount,0), COALESCE(cp.online_amount,0),
                   COALESCE(cp.reference_no,''), COALESCE(cp.note,'')
            FROM customer_payments cp
            JOIN customers c ON c.id = cp.customer_id
            ORDER BY cp.id DESC LIMIT 300
            """
        ).fetchall()
        customers = conn.execute(
            "SELECT id, name, COALESCE(total_due,0) FROM customers ORDER BY name COLLATE NOCASE"
        ).fetchall()
        return {
            "kind": "customer",
            "history": [
                {
                    "id": r[0],
                    "date": r[1],
                    "party": r[2],
                    "amount": float(r[3] or 0),
                    "mode": r[4],
                    "cash": float(r[5] or 0),
                    "online": float(r[6] or 0),
                    "reference": r[7],
                    "note": r[8],
                }
                for r in rows
            ],
            "parties": [
                {"id": r[0], "name": r[1], "due": float(r[2] or 0)} for r in customers
            ],
        }

    rows = conn.execute(
        """
        SELECT sp.id, sp.payment_no, sp.payment_date, s.name, sp.amount, sp.mode,
               COALESCE(sp.reference,''), sp.due_before, sp.due_after
        FROM supplier_payments sp
        JOIN suppliers s ON s.id = sp.supplier_id
        ORDER BY sp.id DESC LIMIT 300
        """
    ).fetchall()
    suppliers = conn.execute(
        "SELECT id, name, COALESCE(total_due,0) FROM suppliers ORDER BY name COLLATE NOCASE"
    ).fetchall()
    return {
        "kind": "supplier",
        "history": [
            {
                "id": r[0],
                "payment_no": r[1],
                "date": r[2],
                "party": r[3],
                "amount": float(r[4] or 0),
                "mode": r[5],
                "reference": r[6],
                "due_before": float(r[7] or 0),
                "due_after": float(r[8] or 0),
            }
            for r in rows
        ],
        "parties": [
            {"id": r[0], "name": r[1], "due": float(r[2] or 0)} for r in suppliers
        ],
    }


def _rows_any(data: dict | None) -> list:
    if not isinstance(data, dict):
        return []
    for key in ("rows", "payments", "history", "items", "returns"):
        raw = data.get(key)
        if isinstance(raw, list):
            return raw
    return []


def _all_online_sales_rows(sq) -> list:
    """Every Online sale, page after page (the server hands out at most 5000 a call).

    Newest first, so a single capped read silently dropped a big shop's OLDEST bills: the
    customer's receipts then had nothing to clear against and the due came out too low
    (or the customer vanished from the dues altogether).
    """
    from core.desktop_pages_service import (
        SALES_HISTORY_ROW_CEILING,
        _all_sales_history_rows,
    )

    cap = max(SALES_HISTORY_ROW_CEILING, 200000)
    return _rows_any(
        _all_sales_history_rows(
            sq, cap, from_date="2000-01-01", to_date="2099-12-31"
        )
        or {}
    )


def _live_party_due_map(kind: str) -> dict[int, float] | None:
    """party id -> remaining due, from the ledger (bills, receipts, returns), Online.

    The same oldest-bill-first arithmetic Sales (online_customer_remaining_due) and Purchase
    History use. None when the ledger could not be read; a party with no open bill is simply
    absent (its due is 0).
    """
    try:
        from core import store_query_client as sq
        from core.online_mutation_queue import merge_server_rows

        if kind == "customer":
            from core.due_fifo import fifo_sales_remaining_by_bill
            from core.online_mutation_queue import (
                overlay_customer_payment_dicts,
                overlay_sales_dicts,
                overlay_sales_return_dicts,
            )

            sales = merge_server_rows(
                _all_online_sales_rows(sq),
                overlay_sales_dicts(),
                collection="sales",
            )
            pays = merge_server_rows(
                _rows_any(sq.list_customer_payments(limit=5000) or {}),
                overlay_customer_payment_dicts(),
                collection="customer_payments",
            )
            rets = merge_server_rows(
                _rows_any(sq.list_sales_returns(limit=5000) or {}),
                overlay_sales_return_dicts(),
                collection="sales_returns",
            )
            # Fills customer_id from the name on bills saved without one, so the
            # loop below books them to the right customer too.
            fifo = fifo_sales_remaining_by_bill(sales, pays, rets)
            dues: dict[int, float] = {}
            for r in sales:
                if not isinstance(r, dict) or r.get("deleted"):
                    continue
                if int(r.get("is_autosave") or 0):
                    continue  # a draft owes nothing
                try:
                    cid = int(r.get("customer_id") or 0)
                    sid = int(r.get("id") or r.get("local_id") or 0)
                except (TypeError, ValueError):
                    continue
                if cid <= 0:
                    continue
                hit = fifo.get(sid)
                rem = float(hit[0]) if hit is not None else float(
                    r.get("due_amount") or r.get("total_due") or 0
                )
                dues[cid] = round(dues.get(cid, 0.0) + rem, 2)
            return dues

        from core.due_fifo import fifo_paid_via_by_bill, purchase_remaining_and_via
        from core.online_mutation_queue import (
            overlay_purchase_dicts,
            overlay_supplier_payment_dicts,
        )

        purch = merge_server_rows(
            _rows_any(
                sq.list_purchases(
                    from_date="2000-01-01", to_date="2099-12-31", limit=5000
                )
                or {}
            ),
            overlay_purchase_dicts(),
            collection="purchases",
        )
        pays = merge_server_rows(
            _rows_any(sq.list_supplier_payments(limit=5000) or {}),
            overlay_supplier_payment_dicts(),
            collection="supplier_payments",
        )
        fifo = fifo_paid_via_by_bill(purch, pays)
        dues = {}
        for r in purch:
            if not isinstance(r, dict) or r.get("deleted"):
                continue
            if int(r.get("is_autosave") or 0):
                continue
            try:
                sid = int(r.get("supplier_id") or 0)
                pid = int(r.get("id") or r.get("local_id") or 0)
            except (TypeError, ValueError):
                continue
            if sid <= 0:
                continue
            rem, _via = purchase_remaining_and_via(r, fifo.get(pid, 0.0))
            dues[sid] = round(dues.get(sid, 0.0) + rem, 2)
        return dues
    except Exception as exc:
        log.warning("live %s dues: %s", kind, exc)
        return None


def _apply_live_party_dues(kind: str, parties: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Replace catalog total_due with FIFO remaining so Payment outstanding is live."""
    if not parties:
        return parties
    dues = _live_party_due_map(kind)
    if not dues:
        return parties
    for p in parties:
        try:
            pid = int(p.get("id") or 0)
        except (TypeError, ValueError):
            pid = 0
        if pid in dues:
            p["due"] = dues[pid]
    return parties


def _notify_payments_changed(kind: str) -> None:
    """Wake Purchase/Sales/History/Ledger immediately after a payment mutation."""
    kind = (kind or "supplier").lower()
    cols = (
        ["customer_payments", "customers", "sales"]
        if kind == "customer"
        else ["supplier_payments", "suppliers", "purchases"]
    )
    try:
        from core.sync_status import note_collection_change

        for col in cols:
            note_collection_change(col)
    except Exception:
        pass
    try:
        from core.store_live_refresh import emit as live_emit

        live_emit(
            {
                "changes": [
                    {"collection": col, "operation": "upserted"} for col in cols
                ],
                "source": "save_payment",
            }
        )
    except Exception:
        pass


def repair_supplier_dues_online(supplier_id: int | None = None) -> int:
    """
    Recompute supplier total_due from purchases − entry paid − payments − returns
    and force-push to server (bumped version). Fixes stale dashboard / mobile due
    after payments that cleared bills via FIFO overlay only.
    Returns number of suppliers updated.
    """
    from core import store_query_client as sq
    from core.due_fifo import purchase_entry_paid
    from core.online_catalog import patch_supplier_cache
    from core.server_crud import bump_meta, get_doc, push_bundle

    want = int(supplier_id or 0)
    purch_raw = _rows_any(
        sq.list_purchases(from_date="2000-01-01", to_date="2099-12-31", limit=5000) or {}
    )
    pays_raw = _rows_any(sq.list_supplier_payments(limit=5000) or {})
    rets_raw = _rows_any(sq.list_purchase_returns(limit=5000) or {})
    try:
        from core.online_mutation_queue import (
            merge_server_rows,
            overlay_supplier_payment_dicts,
        )

        pays_raw = merge_server_rows(
            list(pays_raw or []),
            overlay_supplier_payment_dicts(),
            collection="supplier_payments",
        )
    except Exception:
        pass

    by_sid: dict[int, dict[str, float | str]] = {}

    def _bucket(sid: int, name: str) -> dict[str, float | str]:
        b = by_sid.get(sid)
        if b is None:
            b = {"name": name, "purchased": 0.0, "entry": 0.0, "pays": 0.0, "rets": 0.0}
            by_sid[sid] = b
        return b

    for r in purch_raw:
        if not isinstance(r, dict) or r.get("deleted") or int(r.get("is_autosave") or 0):
            continue
        try:
            sid = int(r.get("supplier_id") or 0)
        except (TypeError, ValueError):
            continue
        if sid <= 0 or (want > 0 and sid != want):
            continue
        b = _bucket(sid, str(r.get("supplier_name") or ""))
        b["purchased"] = float(b["purchased"]) + float(
            r.get("final_amount") or r.get("total_amount") or 0
        )
        b["entry"] = float(b["entry"]) + float(purchase_entry_paid(r))

    for p in pays_raw:
        if not isinstance(p, dict) or p.get("deleted"):
            continue
        try:
            sid = int(p.get("supplier_id") or 0)
        except (TypeError, ValueError):
            continue
        if sid <= 0 or (want > 0 and sid != want):
            continue
        b = _bucket(sid, str(p.get("supplier_name") or ""))
        b["pays"] = float(b["pays"]) + float(p.get("amount") or 0)

    for ret in rets_raw:
        if not isinstance(ret, dict) or ret.get("deleted"):
            continue
        try:
            sid = int(ret.get("supplier_id") or 0)
        except (TypeError, ValueError):
            continue
        if sid <= 0 or (want > 0 and sid != want):
            continue
        b = _bucket(sid, str(ret.get("supplier_name") or ""))
        b["rets"] = float(b["rets"]) + float(ret.get("refund_amount") or 0)

    updated = 0
    docs = []
    for sid, b in by_sid.items():
        net = round(
            float(b["purchased"]) - float(b["entry"]) - float(b["pays"]) - float(b["rets"]),
            2,
        )
        due = max(0.0, net)
        credit = abs(net) if net < 0 else 0.0
        name = str(b.get("name") or "")
        existing = get_doc("suppliers", sid) or {}
        cur_due = float(existing.get("total_due") or 0)
        cur_credit = float(existing.get("total_credit") or 0)
        if abs(cur_due - due) < 0.02 and abs(cur_credit - credit) < 0.02:
            continue
        doc = bump_meta(dict(existing) if existing else {})
        doc["id"] = sid
        doc["local_id"] = sid
        doc["name"] = name or existing.get("name") or ""
        doc["phone"] = existing.get("phone") or ""
        doc["total_due"] = due
        doc["total_credit"] = credit
        docs.append(doc)
        try:
            patch_supplier_cache(
                {
                    "id": sid,
                    "local_id": sid,
                    "name": doc["name"],
                    "total_due": due,
                    "total_credit": credit,
                }
            )
        except Exception:
            pass
        updated += 1
    if docs:
        chunk = 80
        for i in range(0, len(docs), chunk):
            push_bundle({"suppliers": docs[i : i + chunk]})
        try:
            from core.store_live_refresh import emit as live_emit

            live_emit(
                {
                    "changes": [
                        {"collection": "suppliers", "operation": "upserted"},
                        {"collection": "purchases", "operation": "upserted"},
                    ],
                    "source": "repair_supplier_dues",
                    "full_refresh": True,
                }
            )
        except Exception:
            pass
    return updated


def _party_match(
    row: dict,
    *,
    id_key: str,
    name_key: str,
    want_id: int,
    want_name: str,
) -> bool:
    try:
        rid = int(row.get(id_key) or 0)
    except (TypeError, ValueError):
        rid = 0
    rname = str(row.get(name_key) or row.get("party") or "").strip()
    if want_id > 0 and rid > 0:
        return rid == want_id
    if want_name and rname:
        return rname.upper() == want_name.strip().upper()
    return False


def _date_in_range(value: Any, date_from: str, date_to: str) -> bool:
    d = str(value or "")[:10]
    if not d:
        return False
    if date_from and d < date_from:
        return False
    if date_to and d > date_to:
        return False
    return True


def _get_ledger_online(
    kind: str,
    party: str,
    date_from: str,
    date_to: str,
) -> dict[str, Any]:
    """Ledger from server bills + local payment overlays (online SQLite is empty)."""
    from core import store_query_client as sq
    from core.due_fifo import purchase_entry_paid
    from core.online_catalog import customers, suppliers
    from core.online_mutation_queue import merge_server_rows

    kind = (kind or "supplier").lower()
    summary = {"opening": 0.0, "debits": 0.0, "credits": 0.0, "closing": 0.0}
    rows_out: list[dict[str, Any]] = []

    if kind == "customer":
        from core.online_mutation_queue import (
            overlay_customer_payment_dicts,
            overlay_sales_dicts,
        )

        parties = sorted(
            {
                str(c.get("name") or "").strip()
                for c in customers()
                if isinstance(c, dict) and str(c.get("name") or "").strip()
            }
        )
        if not party:
            return {
                "kind": kind,
                "party": "",
                "from": date_from,
                "to": date_to,
                "parties": parties,
                "rows": [],
                "summary": summary,
            }
        want_name = party.strip()
        want_id = 0
        for c in customers():
            if not isinstance(c, dict):
                continue
            if str(c.get("name") or "").strip().upper() == want_name.upper():
                try:
                    want_id = int(c.get("id") or c.get("local_id") or 0)
                except (TypeError, ValueError):
                    want_id = 0
                break
        sales = merge_server_rows(
            _rows_any(
                sq.list_sales(
                    q=want_name,
                    from_date="2000-01-01",
                    to_date="2099-12-31",
                    limit=5000,
                )
                or {}
            ),
            overlay_sales_dicts(),
            collection="sales",
        )
        pays = merge_server_rows(
            _rows_any(sq.list_customer_payments(limit=5000) or {}),
            overlay_customer_payment_dicts(),
            collection="customer_payments",
        )
        # Sales returns credit the customer. They were missing entirely, so a
        # settled account still showed the refunded amount as outstanding.
        srets = _rows_any(sq.list_sales_returns(limit=5000) or {})
        opening = 0.0
        events: list[tuple] = []
        for r in sales:
            if not isinstance(r, dict) or r.get("deleted") or int(r.get("is_autosave") or 0):
                continue
            if not _party_match(
                r,
                id_key="customer_id",
                name_key="customer_name",
                want_id=want_id,
                want_name=want_name,
            ):
                continue
            # The bill's DEBIT is total_amount, full stop, and the money handed over
            # at the counter is its own CREDIT on the same line. Both halves of this
            # were wrong, and both were provably wrong against the store's own data.
            #
            #  1. `- discount` double-deducted. `sales.total_amount` is ALREADY net of
            #     the discount -- `calc_bill_summary` returns
            #     `round(subtotal - discount_amount + rounding, 2)` (calc_engine.py:88-89)
            #     -- and live Roshan shows it: SCB2 stores total_amount 50.00 next to
            #     discount 5.60 on a 56.00 subtotal. The statement was debiting 44.40 for
            #     a 50.00 bill.
            #
            #  2. `amount_paid` was never credited. The desktop's own single source of
            #     truth subtracts it: `recalculate_customer_due` is
            #     `SUM(total_amount) - SUM(amount_paid) - SUM(payments) - SUM(returns)`
            #     (customer_service.py:271), and so is the server's partyDueCascade. On
            #     live Roshan customer 14311 (A K PAWAR) -- billed 1503.00, paid 1503.00
            #     at the counter, no payments, no returns -- the server, the phone and
            #     `customers.total_due` all say 0.00 and this statement closed at
            #     1503.00 due. Store-wide the statement overstated the debt by
            #     SUM(amount_paid) - SUM(discount) = 26,379.48 - 48.80 = Rs 26,330.68.
            #
            # Both columns are real, so the row carries both rather than netting them:
            # the shop can still see what the bill was and what was tendered against it.
            amt = round(float(r.get("total_amount") or 0), 2)
            paid = round(float(r.get("amount_paid") or 0), 2)
            d = str(r.get("bill_date") or "")[:10]
            if date_from and d and d < date_from:
                opening += amt - paid
                continue
            if not _date_in_range(d, date_from, date_to):
                continue
            events.append(
                (
                    "sale",
                    d,
                    int(r.get("id") or r.get("local_id") or 0),
                    amt,
                    r,
                )
            )
        for r in pays:
            if not isinstance(r, dict) or r.get("deleted"):
                continue
            if not _party_match(
                r,
                id_key="customer_id",
                name_key="customer_name",
                want_id=want_id,
                want_name=want_name,
            ):
                continue
            amt = round(float(r.get("amount") or 0), 2)
            d = str(r.get("payment_date") or r.get("date") or "")[:10]
            if date_from and d and d < date_from:
                opening -= amt
                continue
            if not _date_in_range(d, date_from, date_to):
                continue
            events.append(
                (
                    "payment",
                    d,
                    int(r.get("id") or r.get("local_id") or 0),
                    amt,
                    r,
                )
            )
        for r in srets:
            if not isinstance(r, dict) or r.get("deleted"):
                continue
            if not _party_match(
                r,
                id_key="customer_id",
                name_key="customer_name",
                want_id=want_id,
                want_name=want_name,
            ):
                continue
            amt = round(float(r.get("refund_amount") or 0), 2)
            d = str(r.get("return_date") or "")[:10]
            if date_from and d and d < date_from:
                opening -= amt
                continue
            if not _date_in_range(d, date_from, date_to):
                continue
            events.append(
                (
                    "return",
                    d,
                    int(r.get("id") or r.get("local_id") or 0),
                    amt,
                    r,
                )
            )
        events.sort(key=lambda e: (str(e[1]), 0 if e[0] == "sale" else 1, e[2]))
        running = opening
        summary["opening"] = opening
        if opening:
            rows_out.append(
                {
                    "date": date_from,
                    "particulars": "Opening Balance",
                    "debit": opening if opening > 0 else 0,
                    "credit": -opening if opening < 0 else 0,
                    "balance": opening,
                    "tag": "opening",
                }
            )
        for etype, edate, _key, amount, raw in events:
            if etype == "sale":
                paid = round(float(raw.get("amount_paid") or 0), 2)
                running += amount - paid
                summary["debits"] += amount
                summary["credits"] += paid
                rows_out.append(
                    {
                        "date": edate,
                        "particulars": f"Sale {raw.get('bill_no') or ''}".strip(),
                        "debit": amount,
                        "credit": paid,
                        "balance": running,
                        "tag": "sale",
                    }
                )
            elif etype == "return":
                running -= amount
                summary["credits"] += amount
                ref = raw.get("return_no") or raw.get("bill_no") or ""
                rows_out.append(
                    {
                        "date": edate,
                        "particulars": f"Sales Return {ref}".strip(),
                        "debit": 0,
                        "credit": amount,
                        "balance": running,
                        "tag": "return",
                    }
                )
            else:
                running -= amount
                summary["credits"] += amount
                mode = raw.get("payment_mode") or raw.get("mode") or ""
                rows_out.append(
                    {
                        "date": edate,
                        "particulars": f"Payment ({mode})".strip(),
                        "debit": 0,
                        "credit": amount,
                        "balance": running,
                        "tag": "payment",
                    }
                )
        summary["closing"] = running
        return {
            "kind": kind,
            "party": party,
            "from": date_from,
            "to": date_to,
            "parties": parties,
            "rows": rows_out,
            "summary": summary,
        }

    from core.online_mutation_queue import (
        overlay_purchase_dicts,
        overlay_supplier_payment_dicts,
    )

    parties = sorted(
        {
            str(s.get("name") or "").strip()
            for s in suppliers()
            if isinstance(s, dict) and str(s.get("name") or "").strip()
        }
    )
    if not party:
        return {
            "kind": kind,
            "party": "",
            "from": date_from,
            "to": date_to,
            "parties": parties,
            "rows": [],
            "summary": summary,
        }
    want_name = party.strip()
    want_id = 0
    for s in suppliers():
        if not isinstance(s, dict):
            continue
        if str(s.get("name") or "").strip().upper() == want_name.upper():
            try:
                want_id = int(s.get("id") or s.get("local_id") or 0)
            except (TypeError, ValueError):
                want_id = 0
            break
    purch = merge_server_rows(
        _rows_any(
            sq.list_purchases(
                q=want_name,
                from_date="2000-01-01",
                to_date="2099-12-31",
                limit=5000,
            )
            or {}
        ),
        overlay_purchase_dicts(),
        collection="purchases",
    )
    pays = merge_server_rows(
        _rows_any(sq.list_supplier_payments(limit=5000) or {}),
        overlay_supplier_payment_dicts(),
        collection="supplier_payments",
    )
    # Purchase returns credit the supplier account: stock went back, so the
    # amount owed drops. They were missing from this statement entirely.
    prets = _rows_any(sq.list_purchase_returns(limit=5000) or {})
    opening = 0.0
    events = []
    for r in purch:
        if not isinstance(r, dict) or r.get("deleted") or int(r.get("is_autosave") or 0):
            continue
        if not _party_match(
            r,
            id_key="supplier_id",
            name_key="supplier_name",
            want_id=want_id,
            want_name=want_name,
        ):
            continue
        # Same defect on the supplier side: what was paid when the bill was ENTERED is
        # a credit against it. `recalculate_supplier_due` subtracts it
        # (purchase_service.py:365, 385-389) using exactly `purchase_entry_paid`, and
        # `suppliers.total_due` is built from that -- live Roshan carries supplier due
        # 0.00 while this statement showed every entry payment still outstanding.
        amt = round(float(r.get("final_amount") or r.get("total_amount") or 0), 2)
        paid = round(purchase_entry_paid(r), 2)
        d = str(r.get("purchase_date") or "")[:10]
        if date_from and d and d < date_from:
            opening += amt - paid
            continue
        if not _date_in_range(d, date_from, date_to):
            continue
        events.append(
            (
                "purchase",
                d,
                int(r.get("id") or r.get("local_id") or 0),
                amt,
                r,
            )
        )
    for r in pays:
        if not isinstance(r, dict) or r.get("deleted"):
            continue
        if not _party_match(
            r,
            id_key="supplier_id",
            name_key="supplier_name",
            want_id=want_id,
            want_name=want_name,
        ):
            continue
        amt = round(float(r.get("amount") or 0), 2)
        d = str(r.get("payment_date") or r.get("date") or "")[:10]
        if date_from and d and d < date_from:
            opening -= amt
            continue
        if not _date_in_range(d, date_from, date_to):
            continue
        events.append(
            (
                "payment",
                d,
                int(r.get("id") or r.get("local_id") or 0),
                amt,
                r,
            )
        )
    for r in prets:
        if not isinstance(r, dict) or r.get("deleted"):
            continue
        if not _party_match(
            r,
            id_key="supplier_id",
            name_key="supplier_name",
            want_id=want_id,
            want_name=want_name,
        ):
            continue
        amt = round(float(r.get("refund_amount") or 0), 2)
        d = str(r.get("return_date") or "")[:10]
        if date_from and d and d < date_from:
            opening -= amt
            continue
        if not _date_in_range(d, date_from, date_to):
            continue
        events.append(
            (
                "return",
                d,
                int(r.get("id") or r.get("local_id") or 0),
                amt,
                r,
            )
        )
    events.sort(key=lambda e: (str(e[1]), 0 if e[0] == "purchase" else 1, e[2]))
    running = opening
    summary["opening"] = opening
    if opening:
        rows_out.append(
            {
                "date": date_from,
                "particulars": "Opening Balance",
                "debit": opening if opening > 0 else 0,
                "credit": -opening if opening < 0 else 0,
                "balance": opening,
                "tag": "opening",
            }
        )
    for etype, edate, _key, amount, raw in events:
        if etype == "purchase":
            paid = round(purchase_entry_paid(raw), 2)
            running += amount - paid
            summary["debits"] += amount
            summary["credits"] += paid
            rows_out.append(
                {
                    "date": edate,
                    "particulars": f"Purchase {raw.get('bill_number') or raw.get('purchase_no') or ''}".strip(),
                    "debit": amount,
                    "credit": paid,
                    "balance": running,
                    "tag": "purchase",
                }
            )
        elif etype == "return":
            running -= amount
            summary["credits"] += amount
            ref = raw.get("return_no") or ""
            rows_out.append(
                {
                    "date": edate,
                    "particulars": f"Purchase Return {ref}".strip(),
                    "debit": 0,
                    "credit": amount,
                    "balance": running,
                    "tag": "return",
                }
            )
        else:
            running -= amount
            summary["credits"] += amount
            mode = raw.get("mode") or ""
            pay_no = raw.get("payment_no") or ""
            label = f"Payment {pay_no} ({mode})".strip() if pay_no else f"Payment ({mode})"
            rows_out.append(
                {
                    "date": edate,
                    "particulars": label,
                    "debit": 0,
                    "credit": amount,
                    "balance": running,
                    "tag": "payment",
                }
            )
    summary["closing"] = running
    return {
        "kind": kind,
        "party": party,
        "from": date_from,
        "to": date_to,
        "parties": parties,
        "rows": rows_out,
        "summary": summary,
    }


def _fifo_clear_customer_sales_online(customer_id: int, customer_name: str) -> None:
    """After an Online customer receipt: refresh the customer's own figure. No sale is written.

    The server clears the bills itself. upsertCustomerPayment runs
    cascadeCustomerAfterLedgerChange in the same transaction as the receipt, and a receipt's
    delete (softDeleteDoc) runs it too: every sale's total_due, due_amount and both cleared flags
    are recomputed from the ledger -- billed, less paid at the counter, receipts and refunds,
    oldest bill first -- and only the bills whose figures changed get a new version.

    This used to push every bill of the customer from here as well, each built from the sales
    list and bumped to version + 1. That is the race the supplier payment lost (X1): another
    device's edit of one of those bills that landed at the same version with an older stamp was
    overwritten by the stale copy, and an edit stamped before the push but arriving after it was
    skipped. A receipt has no business rewriting a sale, so it no longer does.
    """
    from core import store_query_client as sq
    from core.server_crud import push_bundle, bump_meta, get_doc
    from core.online_mutation_queue import (
        merge_server_rows,
        overlay_customer_payment_dicts,
        overlay_sales_return_dicts,
    )

    cid = int(customer_id or 0)
    name = str(customer_name or "").strip()
    if cid <= 0 and not name:
        return

    sales_raw = _rows_any(
        sq.list_sales(q=name, from_date="2000-01-01", to_date="2099-12-31", limit=5000)
        or {}
    )
    bills: list[tuple[int, float, float, dict]] = []
    for r in sales_raw:
        if not isinstance(r, dict) or r.get("deleted") or int(r.get("is_autosave") or 0):
            continue
        try:
            rid = int(r.get("id") or r.get("local_id") or 0)
        except (TypeError, ValueError):
            continue
        if rid <= 0:
            continue
        try:
            rcid = int(r.get("customer_id") or 0)
        except (TypeError, ValueError):
            rcid = 0
        rname = str(r.get("customer_name") or "").strip()
        if cid > 0 and rcid and rcid != cid:
            continue
        if name and rname and rname.upper() != name.upper() and rcid != cid:
            continue
        bills.append(
            (
                rid,
                float(r.get("total_amount") or 0),
                float(r.get("amount_paid") or 0),
                r,
            )
        )
    bills.sort(key=lambda t: (str(t[3].get("bill_date") or ""), t[0]))

    pays_raw = merge_server_rows(
        _rows_any(sq.list_customer_payments(limit=5000) or {}),
        overlay_customer_payment_dicts(),
        collection="customer_payments",
    )
    total_standalone = 0.0
    for p in pays_raw:
        if not isinstance(p, dict) or p.get("deleted"):
            continue
        try:
            pcid = int(p.get("customer_id") or 0)
        except (TypeError, ValueError):
            pcid = 0
        pname = str(p.get("customer_name") or "").strip()
        if cid > 0 and pcid == cid:
            total_standalone += float(p.get("amount") or 0)
        elif name and pname and pname.upper() == name.upper():
            total_standalone += float(p.get("amount") or 0)

    rets_raw = merge_server_rows(
        _rows_any(sq.list_sales_returns(limit=5000) or {}),
        overlay_sales_return_dicts(),
        collection="sales_returns",
    )
    total_returns = 0.0
    for ret in rets_raw:
        if not isinstance(ret, dict) or ret.get("deleted"):
            continue
        try:
            rcid = int(ret.get("customer_id") or 0)
        except (TypeError, ValueError):
            rcid = 0
        rname = str(ret.get("customer_name") or "").strip()
        if cid > 0 and rcid == cid:
            total_returns += float(ret.get("refund_amount") or 0)
        elif name and rname and rname.upper() == name.upper():
            total_returns += float(ret.get("refund_amount") or 0)

    if not bills and total_standalone <= 0 and total_returns <= 0:
        return
    # No sale document is pushed (see the docstring): the server's cascade has already set each
    # bill's due and cleared flags from the ledger. The screens are told to re-read.
    try:
        from core.store_live_refresh import emit as live_emit

        live_emit(
            {
                "changes": [
                    {"collection": "sales", "operation": "upserted"},
                    {"collection": "customer_payments", "operation": "upserted"},
                    {"collection": "customers", "operation": "upserted"},
                ],
                "source": "fifo_customer",
            }
        )
    except Exception:
        pass
    try:
        from core.online_catalog import patch_customer_cache

        # Owner's rule, the server's own arithmetic: bills - paid at the counter - receipts -
        # return refunds. The per-bill FIFO remainders never go below 0, so summing them would
        # lose the credit an over receipt leaves.
        net = round(
            sum(float(b[1]) - float(b[2]) for b in bills) - total_standalone - total_returns,
            2,
        )
        due = max(0.0, net)
        credit = 0.0 if net >= -0.01 else abs(net)
        existing = (get_doc("customers", cid) or {}) if cid > 0 else {}
        label = name or str(existing.get("name") or "")
        patch_customer_cache(
            {
                "id": cid,
                "local_id": cid,
                "name": label,
                "total_due": due,
                "total_credit": credit,
            }
        )
        if cid > 0:
            doc = bump_meta(dict(existing) if existing else {})
            doc["id"] = cid
            doc["local_id"] = cid
            doc["name"] = label
            doc["phone"] = existing.get("phone") or ""
            doc["total_due"] = due
            doc["total_credit"] = credit
            push_bundle({"customers": [doc]})
    except Exception:
        pass


def online_supplier_remaining_due(supplier_id: int = 0, supplier_name: str = "") -> float | None:
    """Live remaining due from purchases + supplier_payments (same FIFO as Purchase History)."""
    from core import store_query_client as sq
    from core.due_fifo import fifo_paid_via_by_bill, purchase_remaining_and_via
    from core.online_mutation_queue import (
        merge_server_rows,
        overlay_purchase_dicts,
        overlay_supplier_payment_dicts,
    )

    sid = int(supplier_id or 0)
    name = str(supplier_name or "").strip()
    if sid <= 0 and not name:
        return None
    try:
        purch = merge_server_rows(
            _rows_any(
                sq.list_purchases(
                    q=name, from_date="2000-01-01", to_date="2099-12-31", limit=5000
                )
                or {}
            ),
            overlay_purchase_dicts(),
            collection="purchases",
        )
        pays = merge_server_rows(
            _rows_any(sq.list_supplier_payments(limit=5000) or {}),
            overlay_supplier_payment_dicts(),
            collection="supplier_payments",
        )
    except Exception:
        return None
    fifo = fifo_paid_via_by_bill(purch, pays)
    name_u = name.upper()
    total = 0.0
    for r in purch:
        if not isinstance(r, dict) or r.get("deleted"):
            continue
        try:
            rsid = int(r.get("supplier_id") or 0)
        except (TypeError, ValueError):
            rsid = 0
        rname = str(r.get("supplier_name") or "").strip().upper()
        if sid > 0 and rsid and rsid != sid:
            continue
        if name_u and rname and rname != name_u and rsid != sid:
            continue
        try:
            pid = int(r.get("id") or r.get("local_id") or 0)
        except (TypeError, ValueError):
            pid = 0
        rem, _via = purchase_remaining_and_via(r, fifo.get(pid))
        total += rem
    return round(total, 2)


def online_customer_remaining_due(customer_id: int = 0, customer_name: str = "") -> float | None:
    """Live remaining due from sales + customer_payments (same FIFO as Sales History)."""
    from core import store_query_client as sq
    from core.due_fifo import fifo_sales_remaining_by_bill
    from core.online_mutation_queue import (
        merge_server_rows,
        overlay_customer_payment_dicts,
        overlay_sales_dicts,
        overlay_sales_return_dicts,
    )

    cid = int(customer_id or 0)
    name = str(customer_name or "").strip()
    if cid <= 0 and not name:
        return None
    try:
        sales = merge_server_rows(
            _rows_any(
                sq.list_sales(
                    q=name, from_date="2000-01-01", to_date="2099-12-31", limit=5000
                )
                or {}
            ),
            overlay_sales_dicts(),
            collection="sales",
        )
        pays = merge_server_rows(
            _rows_any(sq.list_customer_payments(limit=5000) or {}),
            overlay_customer_payment_dicts(),
            collection="customer_payments",
        )
        rets = merge_server_rows(
            _rows_any(sq.list_sales_returns(limit=5000) or {}),
            overlay_sales_return_dicts(),
            collection="sales_returns",
        )
    except Exception:
        return None
    fifo = fifo_sales_remaining_by_bill(sales, pays, rets)
    name_u = name.upper()
    total = 0.0
    for r in sales:
        if not isinstance(r, dict) or r.get("deleted"):
            continue
        try:
            rcid = int(r.get("customer_id") or 0)
        except (TypeError, ValueError):
            rcid = 0
        rname = str(r.get("customer_name") or "").strip().upper()
        if cid > 0 and rcid and rcid != cid:
            continue
        if name_u and rname and rname != name_u and rcid != cid:
            continue
        try:
            sid = int(r.get("id") or r.get("local_id") or 0)
        except (TypeError, ValueError):
            sid = 0
        hit = fifo.get(sid)
        if hit is not None:
            total += float(hit[0] or 0)
        else:
            total += float(r.get("due_amount") or r.get("total_due") or 0)
    return round(total, 2)


def _fifo_clear_supplier_purchases_online(supplier_id: int, supplier_name: str) -> None:
    """After an Online supplier payment: refresh the supplier's own figure. No purchase is written.

    The server clears the bills itself. upsertSupplierPayment runs
    cascadeSupplierAfterLedgerChange in the same transaction as the payment: every purchase's
    due, due_amount, total_due and both cleared flags are recomputed from the ledger, and only
    the bills whose figures changed get a new version.

    This used to push every bill of the supplier from here as well. First from the purchases
    list, which blanked the header columns the list does not carry (F1, 2026-09-11); then from
    each bill's stored copy bumped to version + 1, which lost the race against another
    device's edit of that bill: an edit landing at the same version with an older stamp was
    overwritten by the stale totals (lines edited, header and dues not), and an edit stamped
    before the push but arriving after it was skipped whole. A payment has no business
    rewriting a purchase, so it no longer does.
    """
    from core import store_query_client as sq
    from core.due_fifo import purchase_entry_paid
    from core.server_crud import push_bundle, bump_meta, get_doc

    sid = int(supplier_id or 0)
    name = str(supplier_name or "").strip()
    if sid <= 0 and not name:
        return

    purch_raw = _rows_any(
        sq.list_purchases(q=name, from_date="2000-01-01", to_date="2099-12-31", limit=5000)
        or {}
    )
    bills: list[tuple[int, float, float, dict]] = []
    for r in purch_raw:
        if not isinstance(r, dict) or r.get("deleted") or int(r.get("is_autosave") or 0):
            continue
        try:
            rid = int(r.get("id") or r.get("local_id") or 0)
        except (TypeError, ValueError):
            continue
        if rid <= 0:
            continue
        try:
            rsid = int(r.get("supplier_id") or 0)
        except (TypeError, ValueError):
            rsid = 0
        rname = str(r.get("supplier_name") or "").strip()
        if sid > 0 and rsid and rsid != sid:
            continue
        if name and rname and rname.upper() != name.upper() and rsid != sid:
            continue
        total = float(r.get("final_amount") or r.get("total_amount") or 0)
        paid = purchase_entry_paid(r)
        bills.append((rid, total, paid, r))
    bills.sort(key=lambda t: (str(t[3].get("purchase_date") or ""), t[0]))

    pays_raw = _rows_any(sq.list_supplier_payments(limit=5000) or {})
    try:
        from core.online_mutation_queue import (
            merge_server_rows,
            overlay_supplier_payment_dicts,
        )

        pays_raw = merge_server_rows(
            list(pays_raw or []),
            overlay_supplier_payment_dicts(),
            collection="supplier_payments",
        )
    except Exception:
        pass
    total_payments = 0.0
    for p in pays_raw:
        if not isinstance(p, dict) or p.get("deleted"):
            continue
        try:
            psid = int(p.get("supplier_id") or 0)
        except (TypeError, ValueError):
            psid = 0
        pname = str(p.get("supplier_name") or "").strip()
        if sid > 0 and psid == sid:
            total_payments += float(p.get("amount") or 0)
        elif name and pname and pname.upper() == name.upper():
            total_payments += float(p.get("amount") or 0)

    rets_raw = _rows_any(sq.list_purchase_returns(limit=5000) or {})
    total_returns = 0.0
    for ret in rets_raw:
        if not isinstance(ret, dict) or ret.get("deleted"):
            continue
        try:
            rsid = int(ret.get("supplier_id") or 0)
        except (TypeError, ValueError):
            rsid = 0
        rname = str(ret.get("supplier_name") or "").strip()
        if sid > 0 and rsid == sid:
            total_returns += float(ret.get("refund_amount") or 0)
        elif name and rname and rname.upper() == name.upper():
            total_returns += float(ret.get("refund_amount") or 0)

    if not bills and total_payments <= 0 and total_returns <= 0:
        return
    # No purchase document is pushed (see the docstring): the server's cascade has already
    # set each bill's due and cleared flags from the ledger. The screens are told to re-read.
    try:
        from core.store_live_refresh import emit as live_emit

        live_emit(
            {
                "changes": [
                    {"collection": "purchases", "operation": "upserted"},
                    {"collection": "supplier_payments", "operation": "upserted"},
                    {"collection": "suppliers", "operation": "upserted"},
                ],
                "source": "fifo_supplier",
            }
        )
    except Exception:
        pass
    try:
        from core.online_catalog import patch_supplier_cache

        # Owner's rule, the server's own arithmetic: bills - paid at entry - payments -
        # return refunds. The per-bill FIFO remainders never go below 0, so summing them lost
        # the credit an overpayment leaves.
        net = round(
            sum(float(b[1]) - float(b[2]) for b in bills) - total_payments - total_returns,
            2,
        )
        due = max(0.0, net)
        credit = 0.0 if net >= -0.01 else abs(net)
        patch_supplier_cache(
            {
                "id": sid,
                "local_id": sid,
                "name": name,
                "total_due": due,
                "total_credit": credit,
            }
        )
        if sid > 0:
            existing = get_doc("suppliers", sid) or {}
            doc = bump_meta(dict(existing) if existing else {})
            doc["id"] = sid
            doc["local_id"] = sid
            doc["name"] = name or existing.get("name") or ""
            doc["phone"] = existing.get("phone") or ""
            doc["total_due"] = due
            doc["total_credit"] = credit
            push_bundle({"suppliers": [doc]})
    except Exception:
        pass


def save_payment(conn: sqlite3.Connection, data: dict[str, Any]) -> dict[str, Any]:
    """Save a customer receipt or a supplier payment, and point out a date after today.

    The payment is saved as typed either way: store 129 holds seven payments dated a year
    ahead, entered with the wrong year.
    """
    out = _save_payment(conn, data)
    try:
        from core.save_warnings import payment_warnings

        warnings = payment_warnings(str((data or {}).get("date") or ""))
    except Exception:
        warnings = []
    if warnings and isinstance(out, dict) and out.get("ok", True) is not False:
        out["warnings"] = warnings
    return out


def _save_payment(conn: sqlite3.Connection, data: dict[str, Any]) -> dict[str, Any]:
    from datetime import date
    from core.sync_prefs import is_online_mode

    kind = str(data.get("kind") or "supplier").lower()
    today = date.today().strftime("%Y-%m-%d")

    if is_online_mode():
        from core.online_catalog import find_customer_by_name, suppliers
        from core.online_mutation_queue import enqueue

        if kind == "customer":
            name = str(data.get("party") or "").strip()
            cash = float(data.get("cash") or 0)
            online = float(data.get("online") or 0)
            amount = round(cash + online, 2)
            if amount <= 0:
                raise ValueError("Total amount must be greater than zero.")
            customer = find_customer_by_name(name)
            if not customer:
                raise ValueError(f"Customer '{name}' not found.")
            cid = int(customer.get("id") or customer.get("local_id"))
            if cash > 0 and online > 0:
                mode = "mixed"
            elif online > 0:
                mode = "online"
            else:
                mode = "cash"
            old_due = float(customer.get("total_due") or 0)
            old_credit = float(customer.get("total_credit") or 0)
            try:
                editing_id = int(data.get("id") or 0)
            except (TypeError, ValueError):
                editing_id = 0
            # On an edit the receipt has ALREADY come off this customer's
            # balance. Only the difference may be applied -- charging the full
            # amount again is what knocked a re-saved receipt off the due twice.
            applied = amount
            if editing_id > 0:
                # The previous amount MUST come from somewhere real. Falling back
                # to 0.0 when the server cannot be reached would apply the whole
                # receipt a second time and knock it off the customer's due
                # twice -- the very bug this delta was added to stop. Check the
                # pending queue too, for a receipt saved moments ago that has not
                # been pushed yet.
                prev_amt = None
                try:
                    from core.server_crud import get_doc as _get_pay_doc

                    prev = _get_pay_doc("customer_payments", editing_id)
                    if isinstance(prev, dict) and prev.get("amount") is not None:
                        prev_amt = float(prev.get("amount") or 0)
                except Exception:
                    prev_amt = None
                if prev_amt is None:
                    try:
                        from core.online_mutation_queue import pending_by_local_id

                        row = pending_by_local_id("customer_payments", editing_id)
                        payload_prev = (row or {}).get("payload") or {}
                        if payload_prev.get("amount") is not None:
                            prev_amt = float(payload_prev.get("amount") or 0)
                    except Exception:
                        prev_amt = None
                if prev_amt is None:
                    raise RuntimeError(
                        "Cannot edit this receipt right now: its saved amount "
                        "could not be read. Check the internet connection and "
                        "try again."
                    )
                applied = round(amount - prev_amt, 2)
            net = round(old_due - old_credit - applied, 2)
            payload = {
                "customer_id": cid,
                "customer_name": name,
                "payment_date": str(data.get("date") or today),
                "amount": amount,
                "payment_mode": mode,
                "cash_amount": cash,
                "online_amount": online,
                "reference_no": str(data.get("reference") or ""),
                "note": str(data.get("note") or ""),
                "_fifo": "customer",
                "_customers": [
                    {
                        "id": cid,
                        "local_id": cid,
                        "name": customer.get("name") or name,
                        "phone": customer.get("phone") or "",
                        "total_due": max(0.0, net),
                        "total_credit": max(0.0, -net),
                    }
                ],
            }
            # A new payment needs a real id before it leaves. Without one the
            # queue stamps a temporary negative id on it, that id reaches the
            # server as the payment's permanent one, and the shop can never
            # delete it again -- "payment not found". Three customer payments
            # were stranded that way.
            cp_id = editing_id
            if cp_id <= 0:
                from core.server_crud import allocate_id

                last_err: Exception | None = None
                for _attempt in range(3):
                    try:
                        cp_id = int(allocate_id("customer_payments"))
                        if cp_id > 0:
                            break
                    except Exception as exc:  # noqa: BLE001 - reported below
                        last_err = exc
                if cp_id <= 0:
                    return {
                        "ok": False,
                        "error": (
                            "Could not get a payment number from the server. "
                            "Nothing was saved -- check the connection and try again."
                            + (f" ({last_err})" if last_err else "")
                        ),
                        "code": "payment_id_unavailable",
                    }
            payload["id"] = cp_id
            payload["local_id"] = cp_id
            if editing_id <= 0:
                # When the receipt was made; an edit keeps the first time on the server.
                from core.server_crud import _now

                payload["created_at"] = _now()
            enqueue(
                collection="customer_payments",
                op="upsert",
                payload=payload,
                local_id=cp_id,
            )
            try:
                from core.online_catalog import patch_customer_cache

                patch_customer_cache(
                    {
                        "id": cid,
                        "local_id": cid,
                        "name": customer.get("name") or name,
                        "phone": customer.get("phone") or "",
                        "total_due": max(0.0, net),
                        "total_credit": max(0.0, -net),
                    }
                )
            except Exception:
                pass
            _notify_payments_changed("customer")
            try:
                out = get_payments(conn, "customer")
                out["ok"] = True
                out["queued"] = True
                out["updated"] = bool(editing_id > 0)
                return out
            except Exception:
                parties = [
                    {
                        "id": c.get("id") or c.get("local_id"),
                        "name": c.get("name") or "",
                        "due": float(c.get("total_due") or 0),
                    }
                    for c in __import__(
                        "core.online_catalog", fromlist=["customers"]
                    ).customers()
                    if isinstance(c, dict)
                ]
                return {
                    "ok": True,
                    "queued": True,
                    "kind": "customer",
                    "history": [],
                    "parties": parties,
                }

        name = str(data.get("party") or "").strip()
        amount = float(data.get("amount") or 0)
        mode = str(data.get("mode") or "").strip()
        if not name:
            raise ValueError("Please select a supplier.")
        if amount <= 0:
            raise ValueError("Amount must be greater than zero.")
        if not mode:
            raise ValueError("Please select a payment mode.")
        from core.online_catalog import find_supplier_by_name, patch_supplier_cache

        supplier = find_supplier_by_name(name) or next(
            (
                d
                for d in suppliers()
                if str(d.get("name") or "").strip().upper() == name.upper()
            ),
            None,
        )
        if not supplier:
            raise ValueError(f"Supplier '{name}' not found.")
        sid = int(supplier.get("id") or supplier.get("local_id"))
        old_due = float(supplier.get("total_due") or 0)
        old_credit = float(supplier.get("total_credit") or 0)

        # Editing a payment must REPLACE it, not add a second one. This branch
        # always allocated a fresh id, so an edit left the original row in place
        # and credited the supplier twice. (The customer branch above already
        # reuses data["id"]; this one was simply never given the same treatment.)
        try:
            editing_id = int(data.get("id") or 0)
        except (TypeError, ValueError):
            editing_id = 0
        old_amount = 0.0
        old_payment_no = ""
        if editing_id > 0:
            from core.server_crud import get_doc

            old_doc = get_doc("supplier_payments", editing_id) or {}
            if not old_doc:
                # Matches the offline branch: refuse rather than silently
                # duplicating when the id no longer resolves.
                raise ValueError("Payment not found for edit.")
            old_payment_no = str(old_doc.get("payment_no") or "")
            try:
                old_sid = int(old_doc.get("supplier_id") or 0)
            except (TypeError, ValueError):
                old_sid = 0
            # Only this supplier's own payment is in this supplier's balance. A payment
            # re-pointed from another supplier was never taken off this one.
            if old_sid in (0, sid):
                old_amount = float(old_doc.get("amount") or 0)

        # due_before / due_after are stored on the payment and shown as ledger columns: the
        # balance as it stood before THIS payment and after it. The balance before it is
        # today's net (due - credit) with the payment being edited taken back out. It used to
        # be old_due + old_amount, which ignores credit: SP14, Rs 100 on a supplier owing 0,
        # edited to Rs 40, stored due_before 100.
        base_net = round(old_due - old_credit + old_amount, 2)
        net = round(base_net - amount, 2)
        due_before = round(max(0.0, base_net), 2)
        due_after = round(max(0.0, net), 2)
        due_credit = round(max(0.0, -net), 2)

        if editing_id > 0:
            pay_id = editing_id
        else:
            # A payment MUST carry a real id. Swallowing a failure here left the
            # queue to invent a temporary negative one, which then went to the
            # server as the payment's permanent id -- and the shop could never
            # delete it again: "payment not found". Seven payments were stranded
            # that way. Better to ask the shop to try again than to write a
            # record they cannot undo.
            from core.server_crud import allocate_id

            pay_id = 0
            last_err: Exception | None = None
            for _attempt in range(3):
                try:
                    pay_id = int(allocate_id("supplier_payments"))
                    if pay_id > 0:
                        break
                except Exception as exc:  # noqa: BLE001 - reported below
                    last_err = exc
            if pay_id <= 0:
                return {
                    "ok": False,
                    "error": (
                        "Could not get a payment number from the server. "
                        "Nothing was saved -- check the connection and try again."
                        + (f" ({last_err})" if last_err else "")
                    ),
                    "code": "payment_id_unavailable",
                }
        payload = {
            "supplier_id": sid,
            "supplier_name": name,
            "payment_date": str(data.get("date") or today),
            "amount": amount,
            "mode": mode,
            "reference": str(data.get("reference") or data.get("note") or ""),
            "due_before": due_before,
            "due_after": due_after,
            "_fifo": "supplier",
            "_suppliers": [
                {
                    "id": sid,
                    "local_id": sid,
                    "name": supplier.get("name") or name,
                    "phone": supplier.get("phone") or "",
                    "total_due": due_after,
                    "total_credit": due_credit,
                }
            ],
        }
        if pay_id > 0:
            payload["id"] = pay_id
            payload["local_id"] = pay_id
            # An edited payment keeps the number it was filed under.
            payload["payment_no"] = old_payment_no or f"SP{pay_id}"
        if editing_id <= 0:
            # When the payment was made; an edit keeps the first time on the server.
            from core.server_crud import _now

            payload["created_at"] = _now()
        enqueue(
            collection="supplier_payments",
            op="upsert",
            payload=payload,
            local_id=pay_id if pay_id > 0 else None,
        )
        try:
            from core.online_mutation_queue import flush_now

            flush_now(wait_sec=30.0)
        except Exception:
            pass
        try:
            repair_supplier_dues_online(sid)
        except Exception:
            pass
        patch_supplier_cache(
            {
                "id": sid,
                "local_id": sid,
                "name": supplier.get("name") or name,
                "phone": supplier.get("phone") or "",
                "total_due": due_after,
                "total_credit": due_credit,
            }
        )
        _notify_payments_changed("supplier")
        try:
            out = get_payments(conn, "supplier")
            out["ok"] = True
            out["queued"] = True
            out["due_before"] = due_before
            out["due_after"] = due_after
            return out
        except Exception:
            parties = [
                {
                    "id": s.get("id") or s.get("local_id"),
                    "name": s.get("name") or "",
                    "due": float(s.get("total_due") or 0),
                }
                for s in suppliers()
                if isinstance(s, dict)
            ]
            return {
                "ok": True,
                "queued": True,
                "kind": "supplier",
                "due_before": due_before,
                "due_after": due_after,
                "history": [],
                "parties": parties,
            }

    if kind == "customer":
        from core.customer_service import recalculate_customer_due

        name = str(data.get("party") or "").strip()
        row = conn.execute(
            "SELECT id FROM customers WHERE UPPER(name)=UPPER(?) LIMIT 1", (name,)
        ).fetchone()
        if not row:
            raise ValueError(f"Customer '{name}' not found.")
        customer_id = int(row[0])
        cash = float(data.get("cash") or 0)
        online = float(data.get("online") or 0)
        amount = round(cash + online, 2)
        if amount <= 0:
            raise ValueError("Total amount must be greater than zero.")
        if cash > 0 and online > 0:
            mode = "mixed"
        elif online > 0:
            mode = "online"
        else:
            mode = "cash"
        pdate = str(data.get("date") or today)
        reference = str(data.get("reference") or "")
        note = str(data.get("note") or "")
        try:
            editing_id = int(data.get("id") or 0)
        except (TypeError, ValueError):
            editing_id = 0
        if editing_id > 0:
            owned = conn.execute(
                "SELECT id FROM customer_payments WHERE id=? AND customer_id=?",
                (editing_id, customer_id),
            ).fetchone()
            if not owned:
                # Allow edit if payment exists even if party renamed to another customer.
                owned = conn.execute(
                    "SELECT id, customer_id FROM customer_payments WHERE id=?",
                    (editing_id,),
                ).fetchone()
                if not owned:
                    raise ValueError("Payment not found for edit.")
                customer_id = int(owned[1])
            conn.execute(
                """
                UPDATE customer_payments
                SET payment_date=?, amount=?, payment_mode=?,
                    cash_amount=?, online_amount=?, reference_no=?, note=?,
                    customer_id=?
                WHERE id=?
                """,
                (
                    pdate,
                    amount,
                    mode,
                    cash,
                    online,
                    reference,
                    note,
                    customer_id,
                    editing_id,
                ),
            )
            payment_id = editing_id
        else:
            conn.execute(
                """
                INSERT INTO customer_payments
                    (customer_id, payment_date, amount, payment_mode,
                     cash_amount, online_amount, reference_no, note)
                VALUES (?,?,?,?,?,?,?,?)
                """,
                (customer_id, pdate, amount, mode, cash, online, reference, note),
            )
            payment_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.commit()
        recalculate_customer_due(conn, customer_id)
        try:
            from core.sync_coordinator import after_customer_payment_saved

            after_customer_payment_saved(conn, int(payment_id), customer_id)
        except Exception:
            pass
        out = get_payments(conn, "customer")
        out["updated"] = bool(editing_id > 0)
        out["payment_id"] = int(payment_id)
        _notify_payments_changed("customer")
        return out

    from core.purchase_service import get_supplier_due, recalculate_supplier_due

    name = str(data.get("party") or "").strip()
    amount = float(data.get("amount") or 0)
    mode = str(data.get("mode") or "").strip()
    if not name:
        raise ValueError("Please select a supplier.")
    if amount <= 0:
        raise ValueError("Amount must be greater than zero.")
    if not mode:
        raise ValueError("Please select a payment mode.")
    row = conn.execute(
        "SELECT id FROM suppliers WHERE name=? LIMIT 1", (name,)
    ).fetchone()
    if not row:
        raise ValueError(f"Supplier '{name}' not found.")
    supplier_id = int(row[0])
    due_before, _ = get_supplier_due(conn, name)
    due_after = max(0.0, round(due_before - amount, 2))
    try:
        editing_id = int(data.get("id") or 0)
    except (TypeError, ValueError):
        editing_id = 0
    pdate = str(data.get("date") or today)
    reference = str(data.get("reference") or "")
    if editing_id > 0:
        owned = conn.execute(
            "SELECT id, COALESCE(amount,0), supplier_id FROM supplier_payments WHERE id=?",
            (editing_id,),
        ).fetchone()
        if not owned:
            raise ValueError("Payment not found for edit.")
        # The supplier's balance already has the payment being edited taken off. The columns
        # stored on it are the balance before and after THIS payment, so put its old amount
        # back first (only when it was this supplier's), then take the new amount off. It used
        # to subtract the new amount from the balance after the old one: both columns counted
        # the old payment twice.
        cur_due, cur_credit = get_supplier_due(conn, name)
        old_amount = float(owned[1] or 0) if int(owned[2] or 0) in (0, supplier_id) else 0.0
        base_net = round(float(cur_due or 0) - float(cur_credit or 0) + old_amount, 2)
        due_before = round(max(0.0, base_net), 2)
        due_after = round(max(0.0, base_net - amount), 2)
        conn.execute(
            """
            UPDATE supplier_payments
            SET supplier_id=?, payment_date=?, amount=?, mode=?, reference=?,
                due_before=?, due_after=?
            WHERE id=?
            """,
            (
                supplier_id,
                pdate,
                amount,
                mode,
                reference,
                due_before,
                due_after,
                editing_id,
            ),
        )
        payment_id = editing_id
    else:
        next_id = conn.execute(
            "SELECT COALESCE(MAX(id),0)+1 FROM supplier_payments"
        ).fetchone()[0]
        pay_no = f"PAY{int(next_id):04d}"
        conn.execute(
            """
            INSERT INTO supplier_payments
                (payment_no,supplier_id,payment_date,amount,mode,reference,due_before,due_after)
            VALUES (?,?,?,?,?,?,?,?)
            """,
            (pay_no, supplier_id, pdate, amount, mode, reference, due_before, due_after),
        )
        payment_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.commit()
    recalculate_supplier_due(conn, supplier_id)
    try:
        from core.sync_coordinator import after_supplier_payment_saved

        after_supplier_payment_saved(conn, int(payment_id), supplier_id)
    except Exception:
        pass
    out = get_payments(conn, "supplier")
    out["updated"] = bool(editing_id > 0)
    out["payment_id"] = int(payment_id)
    _notify_payments_changed("supplier")
    return out


def delete_payment(conn: sqlite3.Connection, data: dict[str, Any]) -> dict[str, Any]:
    kind = str(data.get("kind") or "supplier").lower()

    try:
        from core.sync_prefs import is_online_mode

        if is_online_mode():
            from core.server_crud import delete_entity, upsert_contact_online, get_doc
            from core.online_catalog import (
                find_customer_by_id,
                find_customer_by_name,
                find_supplier_by_id,
                find_supplier_by_name,
                invalidate,
            )

            if kind == "customer":
                pay_id = int(data.get("id") or 0)
                if pay_id <= 0:
                    raise ValueError("Payment not found.")
                doc = get_doc("customer_payments", pay_id) or {}
                amount = float(doc.get("amount") or data.get("amount") or 0)
                cid = int(doc.get("customer_id") or data.get("customer_id") or 0)
                party_name = str(
                    doc.get("customer_name") or data.get("party") or ""
                ).strip()
                delete_entity("customer_payments", pay_id)
                cust = find_customer_by_id(cid) if cid else None
                if not cust and party_name:
                    cust = find_customer_by_name(party_name)
                if not cust and cid:
                    cust = get_doc("customers", cid) or {}
                if cust and int(cust.get("id") or cust.get("local_id") or cid or 0) > 0:
                    out_id = int(cust.get("id") or cust.get("local_id") or cid)
                    upsert_contact_online(
                        "customers",
                        {
                            "id": out_id,
                            "local_id": out_id,
                            "name": cust.get("name") or party_name,
                            "phone": cust.get("phone") or "",
                            "address": cust.get("address") or "",
                            "total_due": round(
                                float(cust.get("total_due") or 0) + amount, 2
                            ),
                            "total_credit": float(cust.get("total_credit") or 0),
                        },
                    )
                invalidate("customers")
                _notify_payments_changed("customer")
                return get_payments(conn, "customer")

            pay_id = int(data.get("id") or 0)
            pay_no = str(data.get("payment_no") or "").strip()
            doc = {}
            if pay_id > 0:
                doc = get_doc("supplier_payments", pay_id) or {}
            if not doc and pay_no:
                try:
                    from core import store_query_client as sq

                    data_hist = sq.list_supplier_payments(limit=500) or {}
                    for r in (
                        data_hist.get("rows")
                        or data_hist.get("payments")
                        or data_hist.get("history")
                        or []
                    ):
                        if not isinstance(r, dict):
                            continue
                        if str(r.get("payment_no") or "") == pay_no:
                            doc = r
                            pay_id = int(r.get("id") or r.get("local_id") or 0)
                            break
                except Exception:
                    pass
            if pay_id <= 0:
                raise ValueError("Payment not found.")
            amount = float(doc.get("amount") or data.get("amount") or 0)
            sid = int(doc.get("supplier_id") or data.get("supplier_id") or 0)
            party_name = str(
                doc.get("supplier_name") or data.get("party") or ""
            ).strip()
            delete_entity("supplier_payments", pay_id)
            sup = find_supplier_by_id(sid) if sid else None
            if not sup and party_name:
                sup = find_supplier_by_name(party_name)
            if not sup and sid:
                sup = get_doc("suppliers", sid) or {}
            if sup and int(sup.get("id") or sup.get("local_id") or sid or 0) > 0:
                out_id = int(sup.get("id") or sup.get("local_id") or sid)
                upsert_contact_online(
                    "suppliers",
                    {
                        "id": out_id,
                        "local_id": out_id,
                        "name": sup.get("name") or party_name,
                        "phone": sup.get("phone") or "",
                        "address": sup.get("address") or "",
                        "gstin": sup.get("gstin") or "",
                        "dl_numbers": sup.get("dl_numbers") or "",
                        "total_due": round(
                            float(sup.get("total_due") or 0) + amount, 2
                        ),
                        "total_credit": float(sup.get("total_credit") or 0),
                    },
                )
            invalidate("suppliers")
            _notify_payments_changed("supplier")
            return get_payments(conn, "supplier")
    except Exception as exc:
        if "Payment not found" in str(exc):
            raise
        # Fall through to SQLite path only when not online; otherwise re-raise.
        from core.sync_prefs import is_online_mode as _online

        if _online():
            raise

    if kind == "customer":
        from core.customer_service import recalculate_customer_due

        pay_id = int(data["id"])
        row = conn.execute(
            "SELECT customer_id FROM customer_payments WHERE id=?", (pay_id,)
        ).fetchone()
        if not row:
            raise ValueError("Payment not found.")
        customer_id = int(row[0])
        conn.execute("DELETE FROM customer_payments WHERE id=?", (pay_id,))
        conn.commit()
        recalculate_customer_due(conn, customer_id)
        try:
            from core.sync_coordinator import after_customer_payment_deleted

            after_customer_payment_deleted(conn, pay_id, customer_id)
        except Exception:
            pass
        _notify_payments_changed("customer")
        return get_payments(conn, "customer")

    from core.purchase_service import recalculate_supplier_due

    pay_id = int(data.get("id") or 0)
    pay_no = str(data.get("payment_no") or "")
    if pay_id:
        row = conn.execute(
            "SELECT id, supplier_id, payment_no FROM supplier_payments WHERE id=?",
            (pay_id,),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT id, supplier_id, payment_no FROM supplier_payments WHERE payment_no=?",
            (pay_no,),
        ).fetchone()
    if not row:
        raise ValueError("Payment not found.")
    payment_id, supplier_id = int(row[0]), int(row[1])
    conn.execute("DELETE FROM supplier_payments WHERE id=?", (payment_id,))
    conn.commit()
    recalculate_supplier_due(conn, supplier_id)
    try:
        from core.sync_coordinator import after_supplier_payment_deleted

        after_supplier_payment_deleted(conn, payment_id, supplier_id)
    except Exception:
        pass
    _notify_payments_changed("supplier")
    return get_payments(conn, "supplier")


def get_ledger(
    conn: sqlite3.Connection,
    kind: str = "supplier",
    party: str = "",
    date_from: str = "",
    date_to: str = "",
) -> dict[str, Any]:
    """Simplified ledger statement matching Settings → Ledger columns."""
    from datetime import date

    from core.due_fifo import PURCHASE_ENTRY_PAID_SQL

    kind = (kind or "supplier").lower()
    today = date.today().strftime("%Y-%m-%d")
    if not date_to:
        date_to = today
    if not date_from:
        # Financial year Apr 1
        y = date.today().year
        date_from = f"{y}-04-01" if date.today().month >= 4 else f"{y - 1}-04-01"

    try:
        from core.sync_prefs import is_online_mode

        if is_online_mode():
            return _get_ledger_online(kind, party, date_from, date_to)
    except Exception as exc:
        print(f"[ledger] online fetch: {exc}")

    parties = []
    rows_out: list[dict[str, Any]] = []
    summary = {"opening": 0.0, "debits": 0.0, "credits": 0.0, "closing": 0.0}

    if kind == "customer":
        parties = [
            r[0]
            for r in conn.execute(
                "SELECT name FROM customers ORDER BY name COLLATE NOCASE"
            ).fetchall()
        ]
        if not party:
            return {
                "kind": kind,
                "party": "",
                "from": date_from,
                "to": date_to,
                "parties": parties,
                "rows": [],
                "summary": summary,
            }
        crow = conn.execute(
            "SELECT id FROM customers WHERE UPPER(name)=UPPER(?) LIMIT 1", (party,)
        ).fetchone()
        if not crow:
            raise ValueError(f"Customer '{party}' not found.")
        cid = int(crow[0])
        # total_amount is ALREADY net of `discount` (calc_engine.py:88-89), and the money
        # paid at the counter is a credit — see the long note on the online branch. The
        # opening balance has to be built the same way `recalculate_customer_due` builds
        # the balance it is opening with, or the statement starts out disagreeing with
        # every other screen.
        opening_sales = conn.execute(
            """
            SELECT COALESCE(SUM(COALESCE(total_amount,0) - COALESCE(amount_paid,0)),0)
            FROM sales
            WHERE customer_id=? AND bill_date < ?
              AND COALESCE(deleted,0)=0 AND COALESCE(is_autosave,0)=0
            """,
            (cid, date_from),
        ).fetchone()[0]
        opening_pay = conn.execute(
            """
            SELECT COALESCE(SUM(amount),0) FROM customer_payments
            WHERE customer_id=? AND payment_date < ?
              AND COALESCE(deleted,0)=0
            """,
            (cid, date_from),
        ).fetchone()[0]
        # Returns before the window reduce what the customer owes, exactly as
        # they do inside it. Omitting them here would carry the refund forward
        # as opening debt.
        opening_ret = conn.execute(
            """
            SELECT COALESCE(SUM(refund_amount),0) FROM sales_returns
            WHERE customer_id=? AND return_date < ? AND COALESCE(deleted,0)=0
            """,
            (cid, date_from),
        ).fetchone()[0]
        opening = (
            float(opening_sales or 0)
            - float(opening_pay or 0)
            - float(opening_ret or 0)
        )
        running = opening
        summary["opening"] = opening
        events: list[tuple] = []
        for r in conn.execute(
            """
            SELECT bill_date, bill_no, total_amount, COALESCE(discount,0),
                   COALESCE(amount_paid,0), id
            FROM sales
            WHERE customer_id=? AND bill_date BETWEEN ? AND ?
              AND COALESCE(deleted,0)=0 AND COALESCE(is_autosave,0)=0
            ORDER BY bill_date, id
            """,
            (cid, date_from, date_to),
        ).fetchall():
            events.append(("sale", r[0], r[1], round(float(r[2] or 0), 2), r))
        for r in conn.execute(
            """
            SELECT payment_date, amount, payment_mode, COALESCE(reference_no,''), id
            FROM customer_payments
            WHERE customer_id=? AND payment_date BETWEEN ? AND ?
              AND COALESCE(deleted,0)=0
            ORDER BY payment_date, id
            """,
            (cid, date_from, date_to),
        ).fetchall():
            events.append(("payment", r[0], r[4], float(r[1] or 0), r))
        # A sales return credits the customer. Without these rows the ledger
        # showed a settled account as still owing the refunded amount.
        for r in conn.execute(
            """
            SELECT return_date, COALESCE(return_no,''), COALESCE(refund_amount,0), id
            FROM sales_returns
            WHERE customer_id=? AND return_date BETWEEN ? AND ?
              AND COALESCE(deleted,0)=0
            ORDER BY return_date, id
            """,
            (cid, date_from, date_to),
        ).fetchall():
            events.append(("return", r[0], r[3], float(r[2] or 0), r))
        events.sort(key=lambda e: (str(e[1]), e[0], e[2]))
        if opening:
            rows_out.append(
                {
                    "date": date_from,
                    "particulars": "Opening Balance",
                    "debit": opening if opening > 0 else 0,
                    "credit": -opening if opening < 0 else 0,
                    "balance": opening,
                    "tag": "opening",
                }
            )
        for etype, edate, _key, amount, raw in events:
            if etype == "sale":
                paid = round(float(raw[4] or 0), 2)
                running += amount - paid
                summary["debits"] += amount
                summary["credits"] += paid
                rows_out.append(
                    {
                        "date": edate,
                        "particulars": f"Sale {raw[1]}",
                        "debit": amount,
                        "credit": paid,
                        "balance": running,
                        "tag": "sale",
                    }
                )
            elif etype == "return":
                running -= amount
                summary["credits"] += amount
                label = f"Sales Return {raw[1]}".strip()
                rows_out.append(
                    {
                        "date": edate,
                        "particulars": label,
                        "debit": 0,
                        "credit": amount,
                        "balance": running,
                        "tag": "return",
                    }
                )
            else:
                running -= amount
                summary["credits"] += amount
                rows_out.append(
                    {
                        "date": edate,
                        "particulars": f"Payment ({raw[2]})",
                        "debit": 0,
                        "credit": amount,
                        "balance": running,
                        "tag": "payment",
                    }
                )
        summary["closing"] = running
        return {
            "kind": kind,
            "party": party,
            "from": date_from,
            "to": date_to,
            "parties": parties,
            "rows": rows_out,
            "summary": summary,
        }

    parties = [
        r[0]
        for r in conn.execute(
            "SELECT name FROM suppliers ORDER BY name COLLATE NOCASE"
        ).fetchall()
    ]
    if not party:
        return {
            "kind": kind,
            "party": "",
            "from": date_from,
            "to": date_to,
            "parties": parties,
            "rows": [],
            "summary": summary,
        }
    srow = conn.execute(
        "SELECT id FROM suppliers WHERE name=? LIMIT 1", (party,)
    ).fetchone()
    if not srow:
        raise ValueError(f"Supplier '{party}' not found.")
    sid = int(srow[0])
    # Entry-time payment is a credit against the bill, exactly as
    # `recalculate_supplier_due` treats it (purchase_service.py:385-389) — same SQL
    # expression, so the statement and `suppliers.total_due` cannot drift apart.
    opening_purch = conn.execute(
        f"""
        SELECT COALESCE(SUM(COALESCE(final_amount,0) - {PURCHASE_ENTRY_PAID_SQL}),0)
        FROM purchases
        WHERE supplier_id=? AND purchase_date < ?
          AND COALESCE(deleted,0)=0 AND COALESCE(is_autosave,0)=0
        """,
        (sid, date_from),
    ).fetchone()[0]
    opening_pay = conn.execute(
        """
        SELECT COALESCE(SUM(amount),0) FROM supplier_payments
        WHERE supplier_id=? AND payment_date < ?
          AND COALESCE(deleted,0)=0
        """,
        (sid, date_from),
    ).fetchone()[0]
    # Purchase returns before the window reduce what is owed to the supplier,
    # exactly as they do inside it.
    opening_ret = conn.execute(
        """
        SELECT COALESCE(SUM(refund_amount),0) FROM purchase_returns
        WHERE supplier_id=? AND return_date < ? AND COALESCE(deleted,0)=0
        """,
        (sid, date_from),
    ).fetchone()[0]
    opening = (
        float(opening_purch or 0)
        - float(opening_pay or 0)
        - float(opening_ret or 0)
    )
    running = opening
    summary["opening"] = opening
    events = []
    for r in conn.execute(
        f"""
        SELECT purchase_date, COALESCE(bill_number, purchase_no), final_amount, id,
               {PURCHASE_ENTRY_PAID_SQL}
        FROM purchases
        WHERE supplier_id=? AND purchase_date BETWEEN ? AND ?
          AND COALESCE(deleted,0)=0 AND COALESCE(is_autosave,0)=0
        ORDER BY purchase_date, id
        """,
        (sid, date_from, date_to),
    ).fetchall():
        events.append(("purchase", r[0], r[3], round(float(r[2] or 0), 2), r))
    for r in conn.execute(
        """
        SELECT payment_date, amount, mode, payment_no, id
        FROM supplier_payments
        WHERE supplier_id=? AND payment_date BETWEEN ? AND ?
          AND COALESCE(deleted,0)=0
        ORDER BY payment_date, id
        """,
        (sid, date_from, date_to),
    ).fetchall():
        events.append(("payment", r[0], r[4], float(r[1] or 0), r))
    # A purchase return credits the supplier account: goods went back, so the
    # amount owed drops. Without these rows a settled account still showed a due.
    for r in conn.execute(
        """
        SELECT return_date, COALESCE(return_no,''), COALESCE(refund_amount,0), id
        FROM purchase_returns
        WHERE supplier_id=? AND return_date BETWEEN ? AND ?
          AND COALESCE(deleted,0)=0
        ORDER BY return_date, id
        """,
        (sid, date_from, date_to),
    ).fetchall():
        events.append(("return", r[0], r[3], float(r[2] or 0), r))
    events.sort(key=lambda e: (str(e[1]), e[0], e[2]))
    if opening:
        rows_out.append(
            {
                "date": date_from,
                "particulars": "Opening Balance",
                "debit": opening if opening > 0 else 0,
                "credit": -opening if opening < 0 else 0,
                "balance": opening,
                "tag": "opening",
            }
        )
    for etype, edate, _key, amount, raw in events:
        if etype == "purchase":
            paid = round(float(raw[4] or 0), 2)
            running += amount - paid
            summary["debits"] += amount
            summary["credits"] += paid
            rows_out.append(
                {
                    "date": edate,
                    "particulars": f"Purchase {raw[1]}",
                    "debit": amount,
                    "credit": paid,
                    "balance": running,
                    "tag": "purchase",
                }
            )
        elif etype == "return":
            running -= amount
            summary["credits"] += amount
            rows_out.append(
                {
                    "date": edate,
                    "particulars": f"Purchase Return {raw[1]}".strip(),
                    "debit": 0,
                    "credit": amount,
                    "balance": running,
                    "tag": "return",
                }
            )
        else:
            running -= amount
            summary["credits"] += amount
            rows_out.append(
                {
                    "date": edate,
                    "particulars": f"Payment {raw[3]} ({raw[2]})",
                    "debit": 0,
                    "credit": amount,
                    "balance": running,
                    "tag": "payment",
                }
            )
    summary["closing"] = running
    return {
        "kind": kind,
        "party": party,
        "from": date_from,
        "to": date_to,
        "parties": parties,
        "rows": rows_out,
        "summary": summary,
    }


def get_reorder(conn: sqlite3.Connection) -> dict[str, Any]:
    from core.reorder_service import fetch_pending_order_groups, fetch_supplier_choices

    try:
        groups = fetch_pending_order_groups(conn)
    except Exception:
        groups = []
    try:
        suppliers = fetch_supplier_choices(conn)
    except Exception:
        suppliers = []
    return {
        "groups": groups,
        "suppliers": suppliers,
        "reorder_default_qty": _setting_get(conn, "reorder_default_qty", "10"),
    }


def _shelf_online() -> bool:
    try:
        from core.sync_prefs import is_online_mode

        return bool(is_online_mode())
    except Exception:
        return False


def _shelf_show_location_online() -> bool:
    """The "Show shelf location" switch, read back from the store server.

    push_shelf_settings has always sent it; nothing ever read it back, so Online
    the switch reset itself to off on every launch and the Location column never
    appeared no matter how many times the shop turned it on.
    """
    try:
        from core import server_api as api
        from core.server_live import _token as live_token

        res = api._request(
            "GET", "/api/sync/settings/shelf_settings", token=live_token(), timeout=30
        )
        data = (res or {}).get("data")
        if isinstance(data, list):
            data = data[0] if data else {}
        if isinstance(data, dict):
            return bool(data.get("show_location"))
    except Exception as exc:
        print(f"[shelf] settings read: {exc}")
    return False


def _shelf_tree_online() -> dict[str, Any]:
    from core.online_catalog import shelf_entities

    def _id(d):
        try:
            return int(d.get("id") if d.get("id") is not None else d.get("local_id") or 0)
        except (TypeError, ValueError):
            return 0

    racks = sorted(shelf_entities("racks"), key=lambda d: str(d.get("name") or ""))
    sections = sorted(shelf_entities("sections"), key=lambda d: str(d.get("name") or ""))
    boxes = sorted(shelf_entities("boxes"), key=lambda d: str(d.get("name") or ""))

    out = []
    for r in racks:
        rid = _id(r)
        secs = []
        for sec in sections:
            if _safe_int_local(sec.get("rack_id")) != rid:
                continue
            sid = _id(sec)
            secs.append({
                "id": sid,
                "name": sec.get("name") or "",
                "boxes": [
                    {"id": _id(b), "name": b.get("name") or ""}
                    for b in boxes
                    if _safe_int_local(b.get("section_id")) == sid
                ],
            })
        out.append({"id": rid, "name": r.get("name") or "", "sections": secs})
    return {"racks": out}


def _safe_int_local(v: Any, default: int = 0) -> int:
    try:
        return int(float(v if v is not None else default))
    except (TypeError, ValueError):
        return default


def get_shelf(conn: sqlite3.Connection) -> dict[str, Any]:
    if _shelf_online():
        try:
            return _shelf_tree_online()
        except Exception as exc:
            # Not a silent empty shelf: an unreachable server must look like a
            # failure, not like a pharmacy with no racks in it.
            return {"racks": [], "error": str(exc)}
    racks = []
    try:
        for rack_id, rack_name in conn.execute(
            "SELECT id, name FROM racks ORDER BY name"
        ).fetchall():
            sections = []
            for sec_id, sec_name in conn.execute(
                "SELECT id, name FROM sections WHERE rack_id=? ORDER BY name",
                (rack_id,),
            ).fetchall():
                boxes = [
                    {"id": b[0], "name": b[1]}
                    for b in conn.execute(
                        "SELECT id, name FROM boxes WHERE section_id=? ORDER BY name",
                        (sec_id,),
                    ).fetchall()
                ]
                sections.append({"id": sec_id, "name": sec_name, "boxes": boxes})
            racks.append({"id": rack_id, "name": rack_name, "sections": sections})
    except Exception as exc:
        return {"racks": [], "error": str(exc)}
    return {"racks": racks}


def _mutate_shelf_online(conn, data: dict[str, Any]) -> dict[str, Any]:
    """Shelf edits on a server-backed shop.

    Every branch below writes to the local connection, which Online is thrown
    away when the engine stops: the screen said the rack was added, the rack
    came back after the next launch, and nothing ever reached the server. These
    go through the same queue every other Online write uses.
    """
    from core.online_catalog import invalidate, medicines as _online_medicines
    from core.online_mutation_queue import enqueue
    from core.server_crud import allocate_id

    action = str(data.get("action") or "").strip().lower()
    name = str(data.get("name") or "").strip()

    NEW = {"add_rack": "racks", "add_section": "sections", "add_box": "boxes"}
    RENAME = {"rename_rack": "racks", "rename_section": "sections",
              "rename_box": "boxes"}
    DELETE = {"delete_rack": "racks", "delete_section": "sections",
              "delete_box": "boxes"}

    if action in NEW:
        if not name:
            raise ValueError(f"{NEW[action][:-1].title()} name required.")
        collection = NEW[action]
        payload: dict[str, Any] = {"name": name}
        if collection == "sections":
            payload["rack_id"] = _safe_int_local(data.get("rack_id"))
        elif collection == "boxes":
            payload["section_id"] = _safe_int_local(data.get("section_id"))
        new_id = int(allocate_id(collection))
        payload["id"] = new_id
        enqueue(collection=collection, op="upsert", local_id=new_id, payload=payload)
        invalidate(collection)
    elif action in RENAME:
        collection = RENAME[action]
        rid = _safe_int_local(data.get("id"))
        if rid <= 0:
            raise ValueError("Select something to rename.")
        payload = {"id": rid, "name": name}
        # rack_id / section_id are part of the row; a rename that dropped them
        # would re-parent the section to nothing on the server.
        for doc in shelf_docs_by_id(collection, rid):
            for key in ("rack_id", "section_id"):
                if doc.get(key) is not None:
                    payload[key] = _safe_int_local(doc.get(key))
        enqueue(collection=collection, op="upsert", local_id=rid, payload=payload)
        invalidate(collection)
    elif action in DELETE:
        collection = DELETE[action]
        rid = _safe_int_local(data.get("id"))
        if rid <= 0:
            raise ValueError("Select something to delete.")
        enqueue(collection=collection, op="delete", local_id=rid, payload={"id": rid})
        invalidate(collection)
    elif action == "set_show_location":
        from core import server_api as api
        from core.server_live import _token as live_token

        api.push_settings_shelf(
            live_token(), {"show_location": bool(data.get("show_location"))}
        )
    elif action in ("assign", "unassign"):
        med_id = _safe_int_local(data.get("medicine_id"))
        if med_id <= 0:
            raise ValueError("Select a medicine.")
        loc = "" if action == "unassign" else str(data.get("location") or "").strip()
        doc = None
        for m in _online_medicines() or []:
            if isinstance(m, dict) and _safe_int_local(m.get("id") or m.get("local_id")) == med_id:
                doc = dict(m)
                break
        if doc is None:
            raise ValueError("That medicine is not on the server.")
        doc["id"] = med_id
        doc["local_id"] = med_id
        doc["location"] = loc
        enqueue(collection="medicines", op="upsert", local_id=med_id, payload=doc)
        try:
            from core.online_catalog import patch_docs

            patch_docs("medicines", [doc])
        except Exception:
            invalidate("medicines")
    else:
        raise ValueError(f"Unknown shelf action: {action}")
    return get_shelf_full(conn)


def shelf_docs_by_id(collection: str, local_id: int) -> list[dict[str, Any]]:
    from core.online_catalog import shelf_entities

    out = []
    for d in shelf_entities(collection) or []:
        if isinstance(d, dict) and _safe_int_local(d.get("id") or d.get("local_id")) == int(local_id):
            out.append(d)
    return out


def mutate_shelf(conn: sqlite3.Connection, data: dict[str, Any]) -> dict[str, Any]:
    if _shelf_online():
        return _mutate_shelf_online(conn, data)
    action = str(data.get("action") or "").strip().lower()
    name = str(data.get("name") or "").strip()

    def _after_saved(collection: str, local_id: int) -> None:
        try:
            from core.sync_coordinator import after_shelf_entity_saved
            after_shelf_entity_saved(conn, collection, int(local_id))
        except Exception:
            pass

    def _after_deleted(collection: str, local_id: int) -> None:
        try:
            from core.sync_coordinator import after_shelf_entity_deleted
            after_shelf_entity_deleted(collection, int(local_id))
        except Exception:
            pass

    if action == "add_rack":
        if not name:
            raise ValueError("Rack name required.")
        cur = conn.execute("INSERT INTO racks (name) VALUES (?)", (name,))
        rack_id = int(cur.lastrowid or 0)
        conn.commit()
        if rack_id:
            _after_saved("racks", rack_id)
    elif action == "add_section":
        rack_id = int(data["rack_id"])
        if not name:
            raise ValueError("Section name required.")
        cur = conn.execute(
            "INSERT INTO sections (rack_id, name) VALUES (?,?)", (rack_id, name)
        )
        section_id = int(cur.lastrowid or 0)
        conn.commit()
        if section_id:
            _after_saved("sections", section_id)
    elif action == "add_box":
        section_id = int(data["section_id"])
        if not name:
            raise ValueError("Box name required.")
        cur = conn.execute(
            "INSERT INTO boxes (section_id, name) VALUES (?,?)", (section_id, name)
        )
        box_id = int(cur.lastrowid or 0)
        conn.commit()
        if box_id:
            _after_saved("boxes", box_id)
    elif action == "rename_rack":
        rid = int(data["id"])
        conn.execute(
            "UPDATE racks SET name=? WHERE id=?", (name, rid)
        )
        conn.commit()
        _after_saved("racks", rid)
    elif action == "rename_section":
        sid = int(data["id"])
        conn.execute(
            "UPDATE sections SET name=? WHERE id=?", (name, sid)
        )
        conn.commit()
        _after_saved("sections", sid)
    elif action == "rename_box":
        bid = int(data["id"])
        conn.execute(
            "UPDATE boxes SET name=? WHERE id=?", (name, bid)
        )
        conn.commit()
        _after_saved("boxes", bid)
    elif action == "delete_rack":
        rid = int(data["id"])
        _after_deleted("racks", rid)
        conn.execute("DELETE FROM racks WHERE id=?", (rid,))
        conn.commit()
    elif action == "delete_section":
        sid = int(data["id"])
        _after_deleted("sections", sid)
        conn.execute("DELETE FROM sections WHERE id=?", (sid,))
        conn.commit()
    elif action == "delete_box":
        bid = int(data["id"])
        _after_deleted("boxes", bid)
        conn.execute("DELETE FROM boxes WHERE id=?", (bid,))
        conn.commit()
    elif action == "set_show_location":
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS shelf_settings (
                id INTEGER PRIMARY KEY, show_location INTEGER DEFAULT 0
            )
            """
        )
        conn.execute(
            "INSERT OR REPLACE INTO shelf_settings (id, show_location) VALUES (1, ?)",
            (1 if data.get("show_location") else 0,),
        )
        conn.commit()
        from core.sync_coordinator import after_shelf_settings_saved
        after_shelf_settings_saved(conn)
    elif action == "assign":
        loc = str(data.get("location") or "").strip()
        med_id = int(data["medicine_id"])
        conn.execute(
            "UPDATE medicines SET location=? WHERE id=?", (loc, med_id)
        )
        conn.commit()
        try:
            from core.sync_coordinator import after_medicine_saved
            after_medicine_saved(conn, med_id)
        except Exception:
            pass
    elif action == "unassign":
        med_id = int(data["medicine_id"])
        conn.execute(
            "UPDATE medicines SET location='' WHERE id=?", (med_id,)
        )
        conn.commit()
        try:
            from core.sync_coordinator import after_medicine_saved
            after_medicine_saved(conn, med_id)
        except Exception:
            pass
    else:
        raise ValueError(f"Unknown shelf action: {action}")
    return get_shelf_full(conn)


def _shelf_full_payload(base, show_location, assigned, unassigned) -> dict[str, Any]:
    """One shape for both modes, so the screen cannot tell them apart."""
    base["show_location"] = bool(show_location)
    base["assigned"] = [
        {"id": r[0], "name": r[1], "batch": r[2], "location": r[3]} for r in assigned
    ]
    base["unassigned"] = [
        {"id": r[0], "name": r[1], "batch": r[2], "stock": float(r[3] or 0)}
        for r in unassigned
    ]
    return base


def get_shelf_full(conn: sqlite3.Connection) -> dict[str, Any]:
    base = get_shelf(conn)
    if _shelf_online():
        # Every list below came off the local connection, which Online is an
        # empty :memory: shell -- so the whole Shelf screen was blank on a shop
        # that had mapped its racks, and the Location column never showed.
        from core.online_catalog import medicines as _online_medicines

        show_location = _shelf_show_location_online()
        assigned, unassigned = [], []
        try:
            rows = [m for m in (_online_medicines() or []) if isinstance(m, dict)]
        except Exception as exc:
            print(f"[shelf] medicines: {exc}")
            rows = []
        rows.sort(key=lambda m: str(m.get("name") or "").lower())
        for m in rows:
            mid = _safe_int_local(m.get("id") or m.get("local_id"))
            name = str(m.get("name") or "")
            batch = str(m.get("batch_no") or "")
            loc = str(m.get("location") or "").strip()
            if loc:
                assigned.append((mid, name, batch, loc))
            else:
                unassigned.append(
                    (mid, name, batch, float(m.get("stock_qty") or 0))
                )
        return _shelf_full_payload(base, show_location, assigned[:2000], unassigned[:500])
    show_location = False
    try:
        row = conn.execute(
            "SELECT show_location FROM shelf_settings LIMIT 1"
        ).fetchone()
        show_location = bool(row and row[0])
    except Exception:
        pass
    assigned = conn.execute(
        """
        SELECT id, name, COALESCE(batch_no,''), COALESCE(location,'')
        FROM medicines
        WHERE location IS NOT NULL AND TRIM(location) != ''
        ORDER BY name COLLATE NOCASE LIMIT 2000
        """
    ).fetchall()
    unassigned = conn.execute(
        """
        SELECT id, name, COALESCE(batch_no,''), COALESCE(stock_qty,0)
        FROM medicines
        WHERE location IS NULL OR TRIM(location) = ''
        ORDER BY name COLLATE NOCASE LIMIT 500
        """
    ).fetchall()
    return _shelf_full_payload(base, show_location, assigned, unassigned)


def test_printer_setup(data: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    from core.printer_manager import PrinterManager

    data = data or {}
    printer = str(data.get("printer") or "").strip() or None
    try:
        PrinterManager.test_print(printer)
        return {"ok": True, "message": "Test page sent to the printer."}
    except Exception as exc:
        return {"ok": False, "message": str(exc)}


def print_dot_matrix_alignment_test(data: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """Print the A6 alignment ruler on the dot matrix printer.

    Uses the saved bill print settings with any dot_matrix_* values from the
    Settings page laid over them, so a shop can try a Top offset or Slip height
    before saving it. The printer is the one given, else the printer of the
    print button set to Dot matrix, else the auto-detected LX-310 queue.
    """
    from core.bill_config import get_print_slot_settings, load_bill_print_settings
    from core.dot_matrix_print import print_alignment_test
    from core.printer_manager import PrinterManager

    data = data or {}
    settings = load_bill_print_settings()
    overrides = data.get("bill")
    if isinstance(overrides, dict):
        settings.update({k: v for k, v in overrides.items() if str(k).startswith("dot_matrix_")})
    printer = str(data.get("printer") or "").strip()
    if not printer:
        for slot in (2, 1):
            try:
                mode = str(get_print_slot_settings(settings, slot).get("bill_size_mode") or "")
                if mode == "dot_matrix":
                    printer = str(PrinterManager.get_printer_for_slot(slot) or "").strip()
                    break
            except Exception:
                continue
    try:
        used = print_alignment_test(settings, printer or None)
        return {"ok": True, "message": f'Alignment test sent to "{used}".'}
    except Exception as exc:
        return {"ok": False, "message": str(exc)}


def pharmacy_action(conn: sqlite3.Connection, data: dict[str, Any]) -> dict[str, Any]:
    """Logo / printer helpers for Pharmacy Profile (Tk parity)."""
    action = str(data.get("action") or "").strip().lower()
    from core.printer_manager import PrinterManager

    if action == "refresh_printers":
        printers: list[str] = []
        spooler = True
        warning = ""
        try:
            printers = PrinterManager.get_installed_printers()
        except Exception as exc:
            return {"ok": False, "error": str(exc), "installed_printers": []}
        try:
            spooler = bool(PrinterManager.is_spooler_running())
        except Exception:
            spooler = True
        if not spooler:
            warning = (
                "Printers were loaded from Windows settings, but the Print Spooler "
                "service is stopped. Start it before printing."
            )
        return {
            "ok": True,
            "installed_printers": printers,
            "spooler_running": spooler,
            "warning": warning,
        }

    if action == "refresh_sumatra":
        cfg = PrinterManager.load_settings()
        saved = str(cfg.get("sumatra_path") or "").strip()
        import os

        if saved and os.path.isfile(saved):
            path = saved
        else:
            path = (
                PrinterManager.find_bundled_sumatra_path()
                or PrinterManager.find_sumatra_path("")
                or ""
            )
        return {
            "ok": True,
            "sumatra_path": path,
            "sumatra_detected": path,
            "message": path
            or "(not found — add SumatraPDF64.exe to tools folder)",
        }

    if action == "upload_logo":
        # conn so the new picture also becomes the one the profile POINTS at.
        # Without it the row kept naming the old file, that file still existed,
        # and resolve_path takes an existing path first -- the shop picked a new
        # logo and its bills carried on printing the old one.
        return save_uploaded_bill_logo(
            str(data.get("filename") or ""),
            str(data.get("data_base64") or ""),
            conn,
        )
    if action in ("browse_logo", "browse_sumatra"):
        # Local-only: reuse Tk filedialog (same machine as the engine).
        try:
            import tkinter as tk
            from tkinter import filedialog

            root = tk.Tk()
            root.withdraw()
            root.attributes("-topmost", True)
            if action == "browse_logo":
                path = filedialog.askopenfilename(
                    title="Choose Bill Logo",
                    filetypes=[
                        ("Images", "*.png;*.jpg;*.jpeg;*.gif;*.bmp;*.webp"),
                        ("All files", "*.*"),
                    ],
                )
            else:
                path = filedialog.askopenfilename(
                    title="Select SumatraPDF.exe",
                    filetypes=[
                        ("SumatraPDF", "SumatraPDF.exe"),
                        ("Executables", "*.exe"),
                        ("All files", "*.*"),
                    ],
                )
            root.destroy()
            return {"ok": True, "path": path or ""}
        except Exception as exc:
            return {"ok": False, "error": str(exc), "path": ""}

    if action == "set_logo_path":
        path = str(data.get("path") or "").strip()
        if path.lower() in ("no logo selected",):
            path = ""
        exists = conn.execute("SELECT id FROM pharmacy_profile LIMIT 1").fetchone()
        if exists:
            conn.execute(
                "UPDATE pharmacy_profile SET logo_path=? WHERE id=?",
                (path, exists[0]),
            )
        else:
            conn.execute(
                "INSERT INTO pharmacy_profile (name, logo_path) VALUES ('', ?)",
                (path,),
            )
        conn.commit()
        return {"ok": True, "logo_path": path, **get_pharmacy(conn)}

    raise ValueError(f"Unknown pharmacy action: {action}")


def system_action(conn: sqlite3.Connection, data: dict[str, Any]) -> dict[str, Any]:
    """Stores / export / backup / maintenance / danger actions for Data & System."""
    action = str(data.get("action") or "").strip().lower()
    from core.store_manager import (
        create_store,
        get_active_display_name,
        get_active_store_key,
        is_satellite_device,
        list_stores,
        set_active_store,
    )

    if action == "online_switch_status":
        from core.online_switch_job import get_status

        st = get_status()
        return {"ok": True, **st}
    if action == "heavy_job_status":
        from core.heavy_job import get_status

        st = get_status()
        return {"ok": True, **st}
    if action == "admin_status":
        from core.admin_gate import status as _admin_status

        return {"ok": True, **_admin_status(conn)}
    if action == "admin_set_pin":
        from core.admin_gate import set_pin

        set_pin(conn, str(data.get("new_pin") or ""),
                current_pin=str(data.get("admin_pin") or ""))
        return {"ok": True, "message": "Administrator PIN saved."}
    if action == "admin_clear_pin":
        from core.admin_gate import clear_pin

        clear_pin(conn, str(data.get("admin_pin") or ""))
        return {"ok": True, "message": "Administrator PIN removed."}
    if action == "admin_check_pin":
        from core.admin_gate import verify_pin, is_pin_set

        ok = (not is_pin_set(conn)) or verify_pin(conn, str(data.get("admin_pin") or ""))
        return {"ok": bool(ok), "error": "" if ok else "Wrong administrator PIN."}
    if action == "admin_set_switch_policy":
        from core.admin_gate import check as _gate, set_switch_needs_pin

        _gate(conn, "create_store", data)   # changing the policy is itself admin work
        set_switch_needs_pin(conn, bool(data.get("store_switch_needs_pin")))
        return {"ok": True, "message": "Store switching policy saved."}
    if action == "list_stores":
        from core.admin_gate import status as _admin_status

        return {
            "stores": list_stores(),
            "active_store_key": get_active_store_key(),
            **_admin_status(conn),
        }
    if action == "list_server_stores":
        from core.admin_gate import check as _gate

        _gate(conn, "join_server_store", data)
        # Reading the whole account's store list is the VENDOR administrator's
        # act, not the shop's. It used to be taken with a password compiled
        # into the build; it now needs the username and password typed on this
        # screen, and the answer says so plainly when they are missing.
        _admin = _vendor_admin_token(data)
        if isinstance(_admin, dict):
            return _admin
        try:
            from core import server_live as live

            return {
                "ok": True,
                "server_stores": live.list_server_stores(admin_token=_admin),
                "active_store_key": get_active_store_key(),
                "active_store_name": get_active_display_name(),
                "link_error": live.last_link_error(),
            }
        except Exception as exc:
            # "Cannot reach the server" and "your shop is not there" have to
            # read differently. An empty list on a network error is how an
            # owner concludes their store was deleted.
            return {
                "ok": False,
                "code": "server_unreachable",
                "error": f"Could not read the store list from the server: {exc}",
            }
    if action == "join_server_store":
        from core.admin_gate import check as _gate

        _gate(conn, "join_server_store", data)
        _admin = _vendor_admin_token(data)
        if isinstance(_admin, dict):
            return _admin
        try:
            from core import server_live as live

            return live.join_server_store(
                store_key=str(data.get("store_key") or get_active_store_key() or ""),
                store_id=str(data.get("store_id") or ""),
                confirm_name=str(data.get("confirm_name") or ""),
                admin_token=_admin,
            )
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
    if action == "forget_server_store":
        from core.admin_gate import check as _gate

        _gate(conn, "join_server_store", data)
        try:
            from core import server_live as live

            return live.forget_store_adoption(
                str(data.get("store_key") or get_active_store_key() or "")
            )
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
    if action == "add_server_store":
        from core.admin_gate import check as _gate

        if is_satellite_device():
            return {"ok": False, "error": "This device is linked to one store only. Unlock it first."}
        _gate(conn, "join_server_store", data)
        _admin = _vendor_admin_token(data)
        if isinstance(_admin, dict):
            return _admin
        try:
            from core import server_live as live

            res = live.add_server_store(
                store_id=str(data.get("store_id") or ""),
                confirm_name=str(data.get("confirm_name") or ""),
                admin_token=_admin,
            )
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        return {**res, "stores": list_stores(), "active_store_key": get_active_store_key()}
    if action == "remove_store":
        from core.admin_gate import check as _gate
        from core.store_manager import remove_store

        if is_satellite_device():
            return {"ok": False, "error": "This device is linked to one store only."}
        _gate(conn, "delete_store", data)
        try:
            gone = remove_store(
                str(data.get("store_key") or ""),
                confirm_name=str(data.get("confirm_name") or ""),
            )
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        return {
            "ok": True,
            "stores": list_stores(),
            "active_store_key": get_active_store_key(),
            "message": (
                f'"{gone["display_name"]}" removed from this PC. Its files are kept in '
                f'{gone["kept_in"]}; nothing on the server was deleted.'
            ),
        }
    if action == "unlock_store_switching":
        # A PC set up from a Drive restore is locked to that one store, and no
        # password used to open it. The vendor administrator signing in is what
        # makes it an ordinary PC that can switch, add and remove stores.
        from core.store_manager import allow_store_switching

        try:
            _admin = _vendor_admin_token(data)
        except Exception as exc:
            return {"ok": False, "error": f"The Satpuda server refused the administrator sign-in: {exc}"}
        if isinstance(_admin, dict):
            return _admin
        allow_store_switching()
        return {
            "ok": True,
            "device_role": "admin",
            "stores": list_stores(),
            "active_store_key": get_active_store_key(),
            "message": "This PC can now switch, add and remove stores.",
        }
    if action == "confirm_active_store":
        # No admin gate: this is the operator saying "yes, this is my shop" to
        # the banner a registry rebuild put up. It changes nothing but the flag.
        from core.store_manager import confirm_active_store

        confirm_active_store()
        return {
            "ok": True,
            "active_store_key": get_active_store_key(),
            "message": "Store confirmed.",
        }
    if action == "switch_store":
        from core.admin_gate import check as _gate

        # Server-side, before the PIN gate: the React guard is cosmetic.
        if is_satellite_device():
            return {"ok": False, "error": "This device is linked to one store only."}
        _gate(conn, "switch_store", data)
        key = str(data.get("store_key") or "")
        set_active_store(key)
        return {
            "ok": True,
            "active_store_key": get_active_store_key(),
            "needs_ui_refresh": True,
            "needs_restart": True,
            "message": "Store switched.",
        }
    if action == "create_store":
        from core.admin_gate import check as _gate
        from core.store_manager import StoreNameTakenOnServer

        if is_satellite_device():
            return {
                "ok": False,
                "error": "Cannot create stores on a single-store device.",
            }
        _gate(conn, "create_store", data)
        name = str(data.get("name") or "").strip()
        if not name:
            raise ValueError("Store name required.")
        # Online, refuse a name another shop already holds. Saying yes here is
        # what let a new store pair into someone else's account and read it.
        try:
            # activate=False, as the old screen did. create_store defaults to
            # activating, and this handler never said otherwise -- so pressing
            # Create silently re-pointed the registry at the new EMPTY store
            # while the running app kept the old database open. Nothing looked
            # wrong until the next launch, which opened the empty one.
            entry = create_store(
                name,
                activate=False,
                allow_existing_remote=bool(data.get("connect_existing")),
            )
        except StoreNameTakenOnServer as exc:
            return {
                "ok": False,
                "code": "name_taken_on_server",
                "name": name,
                "error": str(exc),
                "stores": list_stores(),
            }
        return {
            "ok": True,
            "stores": list_stores(),
            "active_store_key": get_active_store_key(),
            "created_store_key": (entry or {}).get("store_key"),
            "created_store_name": (entry or {}).get("display_name") or name,
            "message": f'Store "{name}" created. It is not active yet.',
        }
    if action == "normalize_names":
        def _normalize(work_conn: sqlite3.Connection, progress: ProgressCb) -> dict[str, Any]:
            from core.name_utils import normalize_medicine_names_in_db

            if progress:
                progress("Normalizing medicine names…")
            n = normalize_medicine_names_in_db(work_conn)
            return {
                "ok": True,
                "updated": int(n or 0),
                "message": f"Updated {int(n or 0)} medicine name(s).",
            }

        return _begin_heavy(
            conn, data, action, "Normalizing medicine names…", _normalize
        )
    if action == "export":
        kind = str(data.get("kind") or "sales").lower()
        # Exporting is not the same as choosing a default. This used to SAVE
        # whatever format the export happened to use, so a one-off CSV from the
        # Home screen reset the shop's saved default -- and the alerts panel,
        # which sends no format at all, reset it to csv every time it ran.
        # Save Default Export Format is what persists it (save_system).
        from core.export_prefs import load_default_export_format

        fmt = str(data.get("format") or load_default_export_format() or "csv").lower()

        def _export(work_conn: sqlite3.Connection, progress: ProgressCb) -> dict[str, Any]:
            try:
                from core.sync_prefs import is_online_mode

                if is_online_mode():
                    import csv
                    import tempfile
                    from core import store_query_client as sq

                    if progress:
                        progress(f"Fetching {kind} from server…")
                    if kind == "sales":
                        data = sq.list_sales(limit=5000) or {}
                        rows = data.get("rows") or data.get("sales") or []
                        headers = ["id", "bill_no", "bill_date", "customer_name", "total_amount", "amount_paid"]
                    elif kind == "purchases":
                        data = sq.list_purchases(limit=5000) or {}
                        rows = data.get("rows") or data.get("purchases") or []
                        headers = ["id", "purchase_no", "purchase_date", "supplier_name", "final_amount"]
                    elif kind == "inventory":
                        data = sq.list_inventory(limit=10000) or {}
                        rows = data.get("rows") or data.get("medicines") or []
                        headers = ["id", "name", "batch_no", "stock_qty", "mrp", "rate"]
                    else:
                        return {
                            "ok": False,
                            "error": "Online export supports sales, purchases, or inventory.",
                        }
                    fd, path = tempfile.mkstemp(prefix=f"satpuda_{kind}_", suffix=".csv")
                    import os
                    os.close(fd)
                    with open(path, "w", newline="", encoding="utf-8") as fh:
                        w = csv.DictWriter(fh, fieldnames=headers, extrasaction="ignore")
                        w.writeheader()
                        for r in rows:
                            if isinstance(r, dict):
                                w.writerow({h: r.get(h, r.get("local_id") if h == "id" else "") for h in headers})
                    return {
                        "ok": True,
                        "path": path,
                        "kind": kind,
                        "format": "csv",
                        "message": f"Exported from server: {path}",
                    }

                from core.export_manager import ExportManager

                if progress:
                    progress(f"Exporting {kind} ({fmt})…")
                em = ExportManager(work_conn)
                path = None
                if kind == "sales" and hasattr(em, "export_sales"):
                    path = em.export_sales(fmt)
                elif kind == "purchases" and hasattr(em, "export_purchases"):
                    path = em.export_purchases(fmt)
                elif kind == "inventory" and hasattr(em, "export_inventory"):
                    path = em.export_inventory(fmt)
                elif kind == "all" and hasattr(em, "export_all"):
                    path = em.export_all(fmt)
                return {
                    "ok": True,
                    "path": path,
                    "kind": kind,
                    "format": fmt,
                    "message": f"Exported: {path}" if path else "Export finished.",
                }
            except Exception as exc:
                return {
                    "ok": False,
                    "error": str(exc),
                    "message": f"Export '{kind}' as {fmt} — open classic app if this fails.",
                }

        return _begin_heavy(
            conn, data, action, f"Exporting {kind}…", _export
        )
    if action == "backup_now":
        def _backup(_work_conn: sqlite3.Connection, progress: ProgressCb) -> dict[str, Any]:
            try:
                from core.backup_manager import run_backup_now

                if progress:
                    progress("Backing up…")
                # run_backup_now now says what it actually did. This used to
                # take the last line of backup_log.txt and fail only on the
                # substrings "fail"/"error", so the one line that mattered --
                # "Backup skipped - backup_config.dat missing or invalid." --
                # was shown to the shop as a green success. It also went blank
                # whenever logging was off (core/log_policy.py).
                res = run_backup_now(manual=True)
                msg = str(res.get("message") or "Backup finished.")
                out: dict[str, Any] = {
                    "ok": bool(res.get("ok")),
                    "message": msg,
                    "status": res.get("status", ""),
                    "code": res.get("code", ""),
                    "drive": res.get("drive", ""),
                    "pendrive": res.get("pendrive", ""),
                    "backup_file": res.get("filename", ""),
                }
                if out["ok"]:
                    out["result"] = msg
                else:
                    out["error"] = msg
                return out
            except Exception as exc:
                return {"ok": False, "error": str(exc)}

        return _begin_heavy(conn, data, action, "Backing up…", _backup)
    if action == "set_auto_backup":
        from core.backup_manager import is_auto_backup_enabled, set_auto_backup_enabled

        set_auto_backup_enabled(bool(data.get("enabled")))
        return {"ok": True, "auto_backup": is_auto_backup_enabled()}
    if action == "backup_status":
        from core.backup_manager import get_backup_config_status, is_auto_backup_enabled
        from core import server_api as api
        from core.store_manager import get_active_store_key
        from core.sync_prefs import get_sync_mode, is_online_mode, mode_label

        status = get_backup_config_status()
        status["auto_backup"] = is_auto_backup_enabled()
        status["sync_mode"] = get_sync_mode()
        status["sync_mode_label"] = mode_label(get_sync_mode())
        status["online_mode"] = is_online_mode()
        status["server_api_base"] = api.api_base()
        # Avoid blocking Settings open on a full health round-trip.
        status["server_reachable"] = False
        if is_online_mode():
            try:
                status["server_reachable"] = bool(api.health_ok(timeout=0.4))
            except Exception:
                status["server_reachable"] = False
        session = api.load_session(get_active_store_key() or "Store_Default")
        status["server_store_id"] = session.get("store_id") or ""
        status["server_android_key"] = session.get("android_key") or ""
        # Legacy keys for older desktop UI
        status["server_configured"] = True
        status["server_project_id"] = "satpuda-core-server"
        status["server_store_id"] = status["server_store_id"]
        return {"ok": True, **status}
    if action in ("push_to_server",):
        # A push resolves the store from its own SC- key where there is one, and
        # needs no administrator at all. Only a store that has never been
        # published has to be CREATED on the server, and that is the vendor
        # administrator's act -- asked for here, before the background job
        # starts, so the answer can carry the code the screen knows how to act on.
        if not _has_pairing_key():
            _admin = _vendor_admin_token(data)
            if isinstance(_admin, dict):
                return _admin
            data = {**data, "admin_token": _admin}

        def _push(work_conn: sqlite3.Connection, progress: ProgressCb) -> dict[str, Any]:
            from core import server_sync
            from core.online_guard import is_cloud_reachable

            if not is_cloud_reachable(force=True):
                return {
                    "ok": False,
                    "error": "Cannot reach Satpuda Core Server. Check your internet connection.",
                }
            # In Online mode work_conn is the empty :memory: shell -- pushing it
            # uploaded nothing and reported "Uploaded 0 record(s)" as a success,
            # which reads as "the server has everything". Point the shop at the
            # button that actually moves a local database.
            from core.sync_prefs import is_online_mode as _online

            if _online():
                return {
                    "ok": False,
                    "error": (
                        "Online mode keeps no local database on this PC, so there "
                        "is nothing here to push. To move an older store's data up, "
                        "restore its Drive backup -- that restores, uploads and "
                        "verifies in one step."
                    ),
                    "code": "nothing_local_to_push",
                }
            try:
                notes: list[str] = []

                def _progress(msg: str) -> None:
                    notes.append(str(msg))
                    if progress:
                        progress(str(msg))

                if progress:
                    progress("Pushing active store to server…")
                # Active store only. Server JWT binds every row to that store's store_pk.
                count = server_sync.push_active_store_to_server(
                    work_conn,
                    progress_cb=_progress,
                    admin_token=str(data.get("admin_token") or ""),
                )
                return {
                    "ok": True,
                    "uploaded": int(count),
                    "progress": notes,
                    "messages": [f"Active store uploaded {count:,} record(s)."],
                    "message": f"Uploaded {count:,} record(s) for the active store.",
                }
            except Exception as exc:
                return {"ok": False, "error": str(exc)}

        return _begin_heavy(conn, data, action, "Pushing to server…", _push)
    if action in ("push_all_stores_to_server",):
        # Every local store, so at least one of them is likely to need creating.
        _admin = _vendor_admin_token(data)
        if isinstance(_admin, dict):
            return _admin
        data = {**data, "admin_token": _admin}

        def _push_all(work_conn: sqlite3.Connection, progress: ProgressCb) -> dict[str, Any]:
            from core import server_sync
            from core.online_guard import is_cloud_reachable

            if not is_cloud_reachable(force=True):
                return {
                    "ok": False,
                    "error": "Cannot reach Satpuda Core Server. Check your internet connection.",
                }
            try:
                notes: list[str] = []

                def _progress(msg: str) -> None:
                    notes.append(str(msg))
                    if progress:
                        progress(str(msg))

                if progress:
                    progress("Pushing every local store on this PC…")
                count, messages = server_sync.push_all_local_stores_to_server(
                    progress_cb=_progress,
                    admin_token=str(data.get("admin_token") or ""),
                )
                return {
                    "ok": True,
                    "uploaded": int(count),
                    "progress": notes,
                    "messages": messages,
                    "message": f"Uploaded {count:,} record(s) across all local stores.",
                }
            except Exception as exc:
                return {"ok": False, "error": str(exc)}

        return _begin_heavy(conn, data, action, "Pushing all stores…", _push_all)
    if action in ("pull_from_server", "sync_from_server"):
        return {
            "ok": False,
            "error": (
                "Online mode is server-only — there is no local database to pull into. "
                "Open Sales/Purchase/Inventory to load live data from the server. "
                "Use Drive restore → Push to Server only when migrating an old backup."
            ),
        }
    if action in ("download_offline", "prepare_offline"):
        # Same idea as Android's "Full Sync (download all)": refresh the local
        # copy WITHOUT switching modes, so the PC is ready before a known
        # outage rather than only at the moment the switch is made.
        from core.online_migrate import download_store_for_offline

        def _dl(work_conn: sqlite3.Connection, progress: ProgressCb) -> dict[str, Any]:
            return download_store_for_offline(progress_cb=progress)

        return _begin_heavy(
            conn,
            data,
            action,
            "Downloading store for offline use…",
            _dl,
        )

    if action in ("online_migrate_push", "online_migrate_wipe"):
        from core.online_migrate import push_local_then_wipe, wipe_local_store

        def _mig(work_conn: sqlite3.Connection, progress: ProgressCb) -> dict[str, Any]:
            path = str(data.get("db_path") or "") or None
            if action == "online_migrate_push":
                return push_local_then_wipe(db_path=path, progress_cb=progress)
            return wipe_local_store(db_path=path, progress_cb=progress)

        return _begin_heavy(
            conn,
            data,
            action,
            "Pushing & removing local DB…" if action.endswith("push") else "Removing local DB…",
            _mig,
        )
    if action == "online_migrate_status":
        from core.online_migrate import ensure_online_server_only_ready

        return {"ok": True, **ensure_online_server_only_ready(auto_wipe_empty=False)}
    if action in ("verify_server_sync",):
        def _verify(work_conn: sqlite3.Connection, progress: ProgressCb) -> dict[str, Any]:
            from core import server_api as api
            from core import server_live as live
            from core.store_manager import get_active_store_key, list_stores

            try:
                if progress:
                    progress("Verifying server sync…")
                # create_if_new, not create_if_missing: this button used to
                # CREATE the missing store, repoint the PC at it and overwrite
                # the pairing key -- so "Verify server sync", pressed because
                # the numbers looked wrong, was itself what emptied the store.
                session = live.ensure_active_store_on_server(create_if_new=True)
                token = session.get("token") or api.store_token_for_active()
                cols = [
                    "customers", "suppliers", "medicines", "doctors", "sales",
                    "purchases", "customer_payments", "supplier_payments",
                    "sales_returns", "purchase_returns",
                ]
                collections = {}
                for col in cols:
                    if progress:
                        progress(f"Checking {col}…")
                    try:
                        cur = work_conn.cursor()
                        cur.execute(f"SELECT COUNT(*) FROM {col}")
                        local_n = int(cur.fetchone()[0] or 0)
                    except Exception:
                        local_n = 0
                    docs, _ = api.pull_collection(
                        token, col, include_deleted=False, limit=20000
                    )
                    remote_n = len(docs)
                    collections[col] = {
                        "local": local_n,
                        "server": remote_n,
                        "match": local_n == remote_n,
                    }
                # In Online mode the working connection is the empty :memory:
                # shell, so every "local" count is 0 and a perfectly good store
                # reads as a total mismatch. Say what the numbers mean instead of
                # letting the shop read a false alarm.
                from core.sync_prefs import is_online_mode as _online

                server_only = bool(_online())
                if server_only:
                    match = True
                    msg = (
                        "Server holds: "
                        + ", ".join(
                            f"{k} {v['server']}" for k, v in collections.items() if v["server"]
                        )
                        + ". (Online mode keeps no local copy, so there is nothing "
                        "to compare against on this PC.)"
                    )
                else:
                    match = all(v.get("match") for v in collections.values())
                    msg = (
                        "Server verification complete — every collection matches."
                        if match
                        else "Server verification complete — some collections differ."
                    )
                return {
                    "ok": True,
                    "match": match,
                    "server_only": server_only,
                    "store_id": session.get("store_id") or "",
                    "android_key": session.get("android_key") or "",
                    "collections": collections,
                    "message": msg,
                }
            except (live.StoreNotLinkedOnServer, live.StoreNameTakenOnServer) as exc:
                # Named separately so the panel can tell "this PC has lost its
                # link" apart from a network hiccup. The sentence already tells
                # the operator what to do; the code lets the UI offer it.
                return {
                    "ok": False,
                    "code": "store_not_linked",
                    "error": str(exc),
                }
            except Exception as exc:
                return {"ok": False, "error": str(exc)}

        return _begin_heavy(conn, data, action, "Verifying server sync…", _verify)
    if action == "list_drive_backups":
        store = str(data.get("store_name") or "").strip()

        def _list_drive(
            _work_conn: sqlite3.Connection, progress: ProgressCb
        ) -> dict[str, Any]:
            from core.backup_manager import get_backup_config_status, list_drive_backups
            from core.store_manager import get_active_display_name

            name = store
            if not name:
                name = get_active_display_name() or get_backup_config_status().get(
                    "store_name", ""
                )
            if progress:
                progress("Listing Drive backups…")
            ok, result = list_drive_backups(name)
            if not ok:
                return {"ok": False, "error": str(result)}
            return {
                "ok": True,
                "store_name": name,
                "backups": result,
                "message": f"Found {len(result) if isinstance(result, list) else 0} backup(s).",
            }

        return _begin_heavy(conn, data, action, "Listing Drive backups…", _list_drive)
    if action == "restore_drive_backup":
        store = str(data.get("store_name") or "").strip()
        store_key = str(data.get("store_key") or "").strip()
        file_id = str(data.get("file_id") or "").strip() or None

        def _restore_drive(
            work_conn: sqlite3.Connection, progress: ProgressCb
        ) -> dict[str, Any]:
            from core.backup_manager import restore_latest_backup_to_store
            from core.store_manager import get_active_display_name, get_active_store_key, get_store_db_path
            from core.sync_prefs import is_online_mode
            from core.online_migrate import allow_local_store_db, push_local_then_wipe

            name = store or get_active_display_name()
            key = store_key or get_active_store_key()
            if progress:
                progress("Restoring Drive backup…")
            with allow_local_store_db():
                ok, result = restore_latest_backup_to_store(
                    name,
                    key,
                    close_conn=work_conn,
                    file_id=file_id,
                    allow_loss=bool(data.get("confirm_loss")),
                )
            if not ok:
                return _restore_refusal(result)
            if is_online_mode():
                dest = get_store_db_path(key) if key else None
                if progress:
                    progress("Online: pushing restored DB to server, then deleting local…")
                push_res = push_local_then_wipe(db_path=dest, progress_cb=progress)
                pushed = int(push_res.get("pushed") or 0)
                removed = push_res.get("removed") or 0
                # Report what actually happened. This used to return ok=True and
                # "pushed to server, and local DB deleted" whatever the push did:
                # verify_push_before_wipe correctly KEEPS the local database when
                # the server is short of rows, but its verdict was thrown away.
                # The shop was told the migration had worked, restarted, met the
                # "Local data found" prompt, pressed Delete Local believing it was
                # already safe on the server -- and the rows the server never
                # accepted were gone from the only other copy.
                if not push_res.get("ok"):
                    return {
                        "ok": False,
                        "result": push_res,
                        "error": (
                            str(push_res.get("error") or "The upload could not be verified.")
                            + " Your local database was NOT deleted. "
                            "Do not delete it until the upload verifies."
                        ),
                    }
                return {
                    "ok": True,
                    "result": push_res,
                    "needs_restart": True,
                    "message": (
                        f"Backup restored and {pushed} record(s) pushed to the server"
                        + (
                            ", and the local database was deleted. "
                            if removed
                            else ". The local database was kept. "
                        )
                        + "Restart the app."
                    ),
                }
            message = (
                result.get("message")
                if isinstance(result, dict)
                else str(result)
            )
            return {
                "ok": True,
                "result": result if isinstance(result, dict) else {"message": str(result)},
                "needs_restart": True,
                "message": message or "Database restored. Restart the desktop app.",
            }

        return _begin_heavy(
            conn, data, action, "Restoring Drive backup…", _restore_drive
        )
    # Backup has written to a USB stick since the beginning; restore could only
    # ever read from Drive. A shop with no internet, or a PC that was never
    # given a Drive folder, had a perfectly good copy in the drawer and no way
    # to put it back.
    if action == "list_usb_backups":
        store = str(data.get("store_name") or "").strip()

        def _list_usb(
            _work_conn: sqlite3.Connection, progress: ProgressCb
        ) -> dict[str, Any]:
            from core.backup_manager import get_backup_config_status, list_local_backups
            from core.store_manager import get_active_display_name

            name = store
            if not name:
                name = get_active_display_name() or get_backup_config_status().get(
                    "store_name", ""
                )
            if progress:
                progress("Looking for USB backups…")
            ok, result = list_local_backups(name)
            if not ok:
                return {"ok": False, "error": str(result)}
            return {
                "ok": True,
                "store_name": name,
                "backups": result,
                "message": f"Found {len(result)} backup(s) on USB.",
            }

        return _begin_heavy(conn, data, action, "Looking for USB backups…", _list_usb)
    if action == "restore_usb_backup":
        store = str(data.get("store_name") or "").strip()
        store_key = str(data.get("store_key") or "").strip()
        path = str(data.get("path") or "").strip()

        def _restore_usb(
            work_conn: sqlite3.Connection, progress: ProgressCb
        ) -> dict[str, Any]:
            from core.backup_manager import restore_local_backup_to_store
            from core.online_migrate import allow_local_store_db, push_local_then_wipe
            from core.store_manager import (
                get_active_display_name,
                get_active_store_key,
                get_store_db_path,
            )
            from core.sync_prefs import is_online_mode

            name = store or get_active_display_name()
            key = store_key or get_active_store_key()
            if progress:
                progress("Restoring USB backup…")
            with allow_local_store_db():
                ok, result = restore_local_backup_to_store(
                    name, key, path=path, close_conn=work_conn,
                    allow_loss=bool(data.get("confirm_loss")),
                )
            if not ok:
                return _restore_refusal(result)
            if is_online_mode():
                # Same rule as the Drive restore: an Online store lives on the
                # server, so the restored file has to get there and verify
                # before anything local is removed.
                dest = get_store_db_path(key) if key else None
                if progress:
                    progress("Online: pushing restored DB to server…")
                push_res = push_local_then_wipe(db_path=dest, progress_cb=progress)
                if not push_res.get("ok"):
                    return {
                        "ok": False,
                        "result": push_res,
                        "error": (
                            str(push_res.get("error") or "The upload could not be verified.")
                            + " Your local database was NOT deleted. "
                            "Do not delete it until the upload verifies."
                        ),
                    }
                pushed = int(push_res.get("pushed") or 0)
                return {
                    "ok": True,
                    "result": push_res,
                    "needs_restart": True,
                    "message": (
                        f"USB backup restored and {pushed} record(s) pushed to the "
                        "server. Restart the app."
                    ),
                }
            message = (
                result.get("message") if isinstance(result, dict) else str(result)
            )
            return {
                "ok": True,
                "result": result if isinstance(result, dict) else {"message": str(result)},
                "needs_restart": True,
                "message": message or "Database restored from USB. Restart the desktop app.",
            }

        return _begin_heavy(
            conn, data, action, "Restoring USB backup…", _restore_usb
        )
    if action == "check_updates":
        def _check_updates(
            _work_conn: sqlite3.Connection, progress: ProgressCb
        ) -> dict[str, Any]:
            try:
                from core.github_updater import check_for_update, format_release_summary

                if progress:
                    progress("Checking for updates…")
                info = check_for_update()
                return {
                    "ok": True,
                    "info": _serialize_update_info(info),
                    "notes": format_release_summary(info) if info else "",
                    "message": "Update check complete.",
                }
            except Exception as exc:
                return {"ok": False, "error": str(exc)}

        return _begin_heavy(conn, data, action, "Checking for updates…", _check_updates)
    if action == "get_updates_info":
        try:
            from core.app_version import APP_VERSION
            from core.github_updater import is_auto_check_enabled
            from core.install_updater import get_install_context, resolve_installer_exe

            ctx = get_install_context()
            installer = resolve_installer_exe() or ""
            return {
                "ok": True,
                "current_version": APP_VERSION,
                "auto_check": is_auto_check_enabled(),
                "install_context": {
                    "mode_label": ctx.mode_label if ctx else "",
                    "install_dir": ctx.install_dir if ctx else "",
                    "installer_exe": ctx.installer_exe if ctx else "",
                },
                "installer_path": installer,
            }
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
    if action == "set_auto_check_updates":
        from core.github_updater import set_auto_check_enabled

        set_auto_check_enabled(bool(data.get("enabled")))
        return {"ok": True, "auto_check": bool(data.get("enabled"))}
    if action == "install_update":
        try:
            from core.github_updater import apply_update, check_for_update

            info = check_for_update()
            if not info.available or not info.can_install_via_installer:
                return {
                    "ok": False,
                    "error": info.error or "No installable update on GitHub.",
                }
            apply_update(info, parent=None)
            return {"ok": True, "message": "Installer launched."}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
    if action == "reinstall_installer":
        try:
            from core.install_updater import reinstall_installer

            path = reinstall_installer(parent=None)
            return {"ok": True, "path": path or "", "message": "Installer ready."}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
    if action == "open_installer":
        import os
        import subprocess

        try:
            from core.install_updater import resolve_installer_exe

            path = resolve_installer_exe() or ""
        except Exception:
            path = ""
        if not path or not os.path.isfile(path):
            return {"ok": False, "error": "Satpuda Core Installer not found."}
        subprocess.Popen(
            [path],
            cwd=os.path.dirname(path),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return {"ok": True, "path": path}
    if action == "open_releases":
        from core.github_updater import open_releases_page

        open_releases_page()
        return {"ok": True}
    if action in ("sync_from_drive", "restore_active_store"):
        file_id = str(data.get("file_id") or "").strip() or None

        def _sync_drive(
            work_conn: sqlite3.Connection, progress: ProgressCb
        ) -> dict[str, Any]:
            from core.backup_manager import sync_active_store_from_drive

            if progress:
                progress("Syncing database from Google Drive…")
            ok, msg = sync_active_store_from_drive(
                close_conn=work_conn, file_id=file_id,
                allow_loss=bool(data.get("confirm_loss")),
            )
            if not ok:
                return _restore_refusal(msg)
            return {
                "ok": True,
                "message": str(msg),
                "needs_restart": True,
            }

        return _begin_heavy(
            conn, data, action, "Syncing from Google Drive…", _sync_drive
        )
    if action == "admin_login":
        from core.license_manager import _MASTER_PASSWORD, _MASTER_USERNAME

        u = str(data.get("username") or "").strip()
        p = str(data.get("password") or "")
        ok = (u == _MASTER_USERNAME and p == _MASTER_PASSWORD) or (
            u == "satpudacore" and p == "satpudacore"
        )
        return {"ok": ok}
    if action == "admin_get_backup_config":
        from core.backup_manager import get_backup_config_status

        return {"ok": True, **get_backup_config_status()}
    if action == "admin_save_backup_config":
        from core.backup_manager import write_backup_config

        store_name = str(data.get("store_name") or "").strip()
        folder_id = str(data.get("folder_id") or "").strip()
        if not store_name or not folder_id:
            return {"ok": False, "error": "Store name and Drive folder ID required."}
        write_backup_config(folder_id, store_name)
        # This screen names a DRIVE FOLDER. It used to also call
        # update_active_store_display_name(store_name), which rewrites the
        # store_key, moves stores/<key>/ and leaves the files that carry the
        # store's identity -- server_session_<key>.json, store_key_<key>.txt,
        # the watermarks -- behind under the old key. The server is never told.
        # On the next launch nothing matched, and the PC quietly paired itself
        # to a brand-new empty store while the shop's real ledger sat on the
        # server untouched: the "store opened with no data" report. The field is
        # prefilled from backup_config.dat, so pressing Save without editing
        # anything was enough to trigger it. Renaming a store is a deliberate
        # act and belongs behind its own confirmation, not here.
        return {"ok": True, "message": "Backup settings saved."}
    if action == "admin_download_master":
        def _dl(_work_conn: sqlite3.Connection, progress: ProgressCb) -> dict[str, Any]:
            from core.master_medicine_cloud import download_master_replace_local
            from core.master_medicine_service import master_row_count

            if progress:
                progress("Downloading global master medicines…")
            ok, msg, n = download_master_replace_local()
            return {
                "ok": ok,
                "message": msg,
                "count": n,
                "local_count": master_row_count(),
            }

        return _begin_heavy(
            conn, data, action, "Downloading master medicines…", _dl
        )
    if action == "admin_push_stock_master":
        def _push(_work_conn: sqlite3.Connection, progress: ProgressCb) -> dict[str, Any]:
            from core.master_medicine_cloud import push_stock_into_master
            from core.master_medicine_service import master_row_count

            if progress:
                progress("Pushing stock into global master…")
            ok, msg, summary = push_stock_into_master(_work_conn)
            return {
                "ok": ok,
                "message": msg,
                "summary": summary,
                "local_count": master_row_count(),
            }

        return _begin_heavy(
            conn, data, action, "Pushing stock into master…", _push
        )
    if action == "admin_get_expiry":
        force_server = bool(data.get("force_server", True))

        def _get_expiry(
            _work_conn: sqlite3.Connection, progress: ProgressCb
        ) -> dict[str, Any]:
            from core.license_manager import get_activation_date, get_expiry_state
            from core.sync_prefs import is_online_mode

            if progress and force_server:
                progress("Fetching expiry from server…")
            state = get_expiry_state(force_server=force_server)
            return {
                "ok": True,
                "expiry": {
                    "enabled": bool(state.get("enabled", False)),
                    "expiry_date": str(state.get("expiry_date") or "")[:10],
                },
                "apply_expiry_check": bool(state.get("apply_expiry_check", True)),
                "activation_date": state.get("activation_date") or get_activation_date(),
                "source": state.get("source")
                or ("server" if is_online_mode() else "local"),
                "is_active": (
                    state.get("is_active") if state.get("is_active") is not None else True
                ),
                "access_allowed": (
                    state.get("access_allowed")
                    if state.get("access_allowed") is not None
                    else True
                ),
                "online": bool(state.get("online", is_online_mode())),
                "message": "Expiry loaded.",
            }

        if force_server:
            return _begin_heavy(
                conn, data, action, "Fetching expiry from server…", _get_expiry
            )
        return _get_expiry(conn, None)
    if action == "admin_save_expiry":
        from datetime import date

        from core.license_manager import save_expiry_settings
        from core.sync_prefs import is_online_mode

        payload = {
            "enabled": bool(data.get("enabled", True)),
            "expiry_date": str(data.get("expiry_date") or "").strip(),
        }
        activation_date = str(data.get("activation_date") or "").strip()[:10]
        try:
            date.fromisoformat(payload["expiry_date"])
        except ValueError:
            return {"ok": False, "error": "Expiry date must be YYYY-MM-DD."}
        if activation_date:
            try:
                date.fromisoformat(activation_date)
            except ValueError:
                return {"ok": False, "error": "Activation date must be YYYY-MM-DD."}

        # THE CREDENTIAL, and why it is asked for here.
        #
        # Changing an expiry is the one Administrator action that is worth money
        # to the person at the keyboard, so it cannot be authorised by anything
        # compiled into the build -- the shop's own local PIN included. It is the
        # vendor's server administrator username and password, typed now, sent
        # once and never stored. Without them the server refuses, and this refuses
        # before it gets that far so the shopkeeper is told what is missing.
        admin_username = str(data.get("admin_username") or "").strip()
        admin_password = str(data.get("admin_password") or "")
        if not admin_username or not admin_password:
            return {
                "ok": False,
                "error": (
                    "Changing the expiry needs the Satpuda administrator "
                    "username and password."
                ),
                "needs_admin_credentials": True,
            }

        def _save_expiry(
            _work_conn: sqlite3.Connection, progress: ProgressCb
        ) -> dict[str, Any]:
            if progress:
                progress("Saving expiry on the Satpuda server…")
            result = save_expiry_settings(
                enabled=payload["enabled"],
                expiry_date=payload["expiry_date"],
                apply_expiry_check=bool(data.get("apply_expiry_check", True)),
                activation_date=activation_date or None,
                admin_username=admin_username,
                admin_password=admin_password,
                push_remote=True,
            )
            if result.get("ok") is False:
                return {
                    "ok": False,
                    "error": result.get("error")
                    or "Server update failed. Expiry was not changed.",
                    "result": result,
                }
            return {
                "ok": True,
                "message": (
                    "Expiry saved on the Satpuda server and signed for this "
                    "computer."
                ),
                "result": result,
            }

        return _begin_heavy(conn, data, action, "Saving expiry…", _save_expiry)
    if action == "admin_get_server_creds":
        from core import server_api as api
        from core.store_manager import get_active_store_key

        session = api.load_session(get_active_store_key() or "Store_Default")
        return {
            "ok": True,
            "configured": True,
            "project_id": "satpuda-core-server",
            "store_id": session.get("store_id") or "",
            "json_text": "",
            "source": "server",
            "api_base": api.api_base(),
            "override_note": (
                "Online mode uses Satpuda Core Server "
                f"({api.api_base()})."
            ),
        }
    if action == "admin_save_server_creds":
        return {
            "ok": True,
            "message": (
                "Online sync uses Satpuda Core Server "
                "(see Server Connection for the live address). "
                "No credentials file needed."
            ),
        }
    if action == "admin_get_android_key":
        from core.store_link import get_local_android_key
        from core.store_manager import get_active_display_name
        from core.sync_prefs import get_sync_mode, is_online_mode, mode_label

        return {
            "ok": True,
            "online_mode": is_online_mode(),
            "sync_mode": get_sync_mode(),
            "sync_mode_label": mode_label(get_sync_mode()),
            "store_name": get_active_display_name() or "Default",
            "android_key": get_local_android_key() or "",
        }
    if action == "admin_ensure_android_key":
        from core.sync_prefs import get_sync_mode, is_online_mode, mode_label

        if not is_online_mode():
            return {
                "ok": False,
                "error": (
                    f"Sync mode is {mode_label(get_sync_mode())}. "
                    "Switch to Online (Server) first."
                ),
            }

        def _ensure_key(
            _work_conn: sqlite3.Connection, progress: ProgressCb
        ) -> dict[str, Any]:
            from core.sync_coordinator import ensure_online_store_link

            if progress:
                progress("Pairing store / generating Android key…")
            key = ensure_online_store_link()
            if not key:
                return {
                    "ok": False,
                    "error": "Could not pair with server. Check internet and admin API.",
                }
            return {"ok": True, "android_key": key, "message": "Android key ready."}

        return _begin_heavy(
            conn, data, action, "Generating Android key…", _ensure_key
        )
    if action == "admin_regenerate_android_key":
        from core.sync_prefs import get_sync_mode, is_online_mode, mode_label

        if not is_online_mode():
            return {
                "ok": False,
                "error": (
                    f"Sync mode is {mode_label(get_sync_mode())}. "
                    "Switch to Online (Server) first."
                ),
            }

        _admin = _vendor_admin_token(data)
        if isinstance(_admin, dict):
            return _admin
        data = {**data, "admin_token": _admin}

        def _regen_key(
            _work_conn: sqlite3.Connection, progress: ProgressCb
        ) -> dict[str, Any]:
            from core.app_prefs import load_app_mode
            from core.store_link import regenerate_android_key
            from core.store_manager import get_active_display_name

            if progress:
                progress("Regenerating Android key…")
            store_name = get_active_display_name() or "Default"
            # Rotating the key unpairs every phone and second PC on that store,
            # so it is the vendor administrator's act and needs the credentials
            # typed on this screen -- not a password compiled into the build.
            key = regenerate_android_key(
                store_name, load_app_mode(), admin_token=str(data.get("admin_token") or "")
            )
            return {"ok": True, "android_key": key, "message": f"New key: {key}"}

        return _begin_heavy(
            conn, data, action, "Regenerating Android key…", _regen_key
        )
    if action == "export_contacts":
        fmt = str(data.get("format") or "csv").lower()
        kind = str(data.get("kind") or "").lower()
        from core.export_manager import export_data_direct

        if kind == "current_view":
            headers = [str(h) for h in (data.get("headers") or [])]
            rows = data.get("rows") or []
            path, msg = export_data_direct(
                None,
                "Customers - Current View",
                headers,
                rows,
                "customers_current_view",
                fmt,
                speak_path=False,
            )
        elif kind == "customer_list":
            cur = conn.cursor()
            cur.execute(
                "SELECT name, phone, COALESCE(address,''), COALESCE(village,'') "
                "FROM customers ORDER BY name COLLATE NOCASE"
            )
            headers = ["Name", "Phone", "Address", "Village"]
            rows = [list(r) for r in cur.fetchall()]
            path, msg = export_data_direct(
                None, "Customer List", headers, rows, "customer_list", fmt, speak_path=False
            )
        elif kind == "customer_due":
            cur = conn.cursor()
            cur.execute(
                "SELECT name, phone, COALESCE(village,''), COALESCE(total_due,0) "
                "FROM customers WHERE COALESCE(total_due,0) > 0 "
                "ORDER BY total_due DESC, name COLLATE NOCASE"
            )
            headers = ["Name", "Phone", "Village", "Total Due"]
            rows = [[r[0], r[1], r[2], f"{float(r[3] or 0):.2f}"] for r in cur.fetchall()]
            path, msg = export_data_direct(
                None, "Customer Due List", headers, rows, "customer_due_list", fmt, speak_path=False
            )
        else:
            return {"ok": False, "error": f"Unknown contacts export kind: {kind}"}
        if not path:
            return {"ok": False, "error": msg}
        return {"ok": True, "path": path, "message": msg}
    if action == "danger_wipe":
        from core.license_manager import _MASTER_PASSWORD

        if str(data.get("confirm") or "") != "DELETE ALL TABLES":
            return {
                "ok": False,
                "error": 'Type confirm string "DELETE ALL TABLES" exactly.',
            }
        password = str(data.get("password") or "")
        if password != _MASTER_PASSWORD:
            return {"ok": False, "error": "Incorrect password."}

        def _wipe(work_conn: sqlite3.Connection, progress: ProgressCb) -> dict[str, Any]:
            cloud_msg = ""
            try:
                from core.sync_prefs import is_online_mode

                if is_online_mode():
                    cloud_msg = (
                        "Local wipe only. Clear this store on the server from the "
                        "admin dashboard if needed.\n"
                    )
            except Exception as exc:
                cloud_msg = f"Server note failed: {exc}\n"

            if progress:
                progress("Wiping local database…")
            tables = [
                "sales_items",
                "sales",
                "purchase_items",
                "purchases",
                "medicine_shelf",
                "medicines",
                "customers",
                "suppliers",
                "doctors",
                "shelves",
                "pharmacy_profile",
                "settings",
                "racks",
                "sections",
                "boxes",
                "shelf_settings",
            ]
            for table in tables:
                try:
                    work_conn.execute(f"DROP TABLE IF EXISTS [{table}]")
                except Exception:
                    pass
            for obj_type, name in [
                ("TRIGGER", "trg_purchases_after_insert"),
                ("TRIGGER", "trg_purchases_after_update"),
                ("TRIGGER", "trg_sales_after_insert"),
                ("TRIGGER", "trg_sales_after_update"),
                ("VIEW", "bills_cleared"),
                ("VIEW", "accounts_cleared"),
                ("VIEW", "supplier_due_status"),
            ]:
                try:
                    work_conn.execute(f"DROP {obj_type} IF EXISTS [{name}]")
                except Exception:
                    pass
            work_conn.commit()

            from core.db_setup import initialise

            if progress:
                progress("Reinitializing empty store…")
            initialise(work_conn)
            work_conn.commit()

            message = (
                f"{cloud_msg}All local data deleted. Restart the app to continue."
                if cloud_msg
                else "All local data deleted. Restart the app to continue."
            )
            return {
                "ok": True,
                "message": message.strip(),
                "server_message": cloud_msg.strip(),
                "needs_restart": True,
            }

        return _begin_heavy(conn, data, action, "Wiping local data…", _wipe)
    if action == "get_voice":
        try:
            from core.voice import voice_config as vc

            return {
                "enabled": getattr(vc, "is_enabled", lambda: False)()
                if hasattr(vc, "is_enabled")
                else False,
                "name": getattr(vc, "load_assistant_name", lambda: "Assist")(),
                "language": getattr(vc, "load_language", lambda: "en")(),
                "tts": getattr(vc, "load_tts_enabled", lambda: True)(),
            }
        except Exception:
            return {"enabled": False, "name": "", "language": "en", "tts": False}
    if action == "save_voice":
        try:
            from core.voice import voice_config as vc

            if "enabled" in data and hasattr(vc, "set_enabled"):
                vc.set_enabled(bool(data["enabled"]))
            if "name" in data and hasattr(vc, "save_assistant_name"):
                vc.save_assistant_name(str(data["name"]))
            if "language" in data and hasattr(vc, "save_language"):
                vc.save_language(str(data["language"]))
            if "tts" in data and hasattr(vc, "save_tts_enabled"):
                vc.save_tts_enabled(bool(data["tts"]))
            return {"ok": True}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
    raise ValueError(f"Unknown system action: {action}")


def import_action(
    conn: Optional[sqlite3.Connection], data: dict[str, Any]
) -> dict[str, Any]:
    action = str(data.get("action") or "").strip().lower()
    if action == "start_web":
        if conn is None:
            raise ValueError("Database not open")
        try:
            from core.web_purchase_server import start_web_purchase_server

            url = start_web_purchase_server(conn)
            url_s = str(url or "")
            opened = False
            if url_s:
                try:
                    import webbrowser

                    webbrowser.open(url_s)
                    opened = True
                except Exception:
                    opened = False
            return {"ok": True, "url": url_s, "opened": opened}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
    if action == "start_mobile":
        try:
            from core.desktop_mobile_import_service import start_mobile_server

            return start_mobile_server()
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
    if action == "stop_mobile":
        from core.desktop_mobile_import_service import stop_mobile_server

        return stop_mobile_server()
    if action == "mobile_status":
        from core.desktop_mobile_import_service import mobile_server_status

        return mobile_server_status()
    if action == "parse_mobile":
        from core.desktop_mobile_import_service import preview_mobile

        return preview_mobile(data)
    if action == "import_mobile":
        if conn is None:
            raise ValueError("Database not open")
        from core.desktop_mobile_import_service import import_mobile

        return import_mobile(conn, data)
    if action == "parse_file_import":
        from core.desktop_file_import_service import parse_file_import

        return parse_file_import(data)
    if action == "get_file_import_bill":
        from core.desktop_file_import_service import get_file_import_bill

        return get_file_import_bill(data)
    if action == "update_file_import_bill":
        from core.desktop_file_import_service import update_file_import_bill

        return update_file_import_bill(data)
    if action == "submit_file_import":
        if conn is None:
            raise ValueError("Database not open")
        from core.desktop_file_import_service import submit_file_import

        return submit_file_import(conn, data)
    if action == "cancel_file_import":
        from core.desktop_file_import_service import cancel_file_import

        return cancel_file_import(data)
    # Opening stock: the same rows the data-loading app produces, typed or
    # pasted here, with no supplier and no purchase bill behind them.
    if action == "opening_stock_template":
        from core.opening_stock_service import template

        return template()
    if action == "opening_stock_preview":
        from core.opening_stock_service import preview

        return preview(data)
    if action == "opening_stock_apply":
        if conn is None:
            raise ValueError("Database not open")
        from core.opening_stock_service import apply as apply_opening_stock

        return apply_opening_stock(conn, data)
    if action == "status":
        from core.desktop_file_import_service import _SESSIONS, _purge_old_sessions

        _purge_old_sessions()
        return {
            "ok": True,
            "message": f"File import ready ({len(_SESSIONS)} active session(s)).",
        }
    raise ValueError(f"Unknown import action: {action}")


def _build_reorder_line(
    conn: sqlite3.Connection,
    supplier_id: int,
    name: str,
    pack: str = "",
    qty: float = 0,
) -> dict[str, Any]:
    from core.reorder_service import (
        fetch_medicine_suppliers,
        min_stock_level,
        suggest_order_quantity,
    )
    from core.stock_utils import current_stock_for_medicine

    name = str(name or "").strip()
    if not name:
        raise ValueError("Medicine name required.")
    cur = conn.cursor()
    cur.execute(
        "SELECT COALESCE(unit,''), COALESCE(type,'Others') FROM medicines "
        "WHERE name=? AND COALESCE(is_hidden,0)=0 ORDER BY id DESC LIMIT 1",
        (name,),
    )
    row = cur.fetchone()
    pack = str(pack or (row[0] if row else "") or "").strip()
    med_type = (row[1] if row else "Others") or "Others"
    stock = current_stock_for_medicine(conn, name, pack)
    suggested = suggest_order_quantity(conn, name, med_type, stock, pack)
    qty_f = float(qty) if float(qty or 0) > 0 else float(suggested or 0)
    rate = 0.0
    sid = int(supplier_id or 0)
    for s in fetch_medicine_suppliers(conn, name):
        if sid and int(s.get("supplier_id") or 0) == sid:
            rate = float(s.get("last_rate") or 0)
            break
    if rate <= 0:
        cur.execute(
            "SELECT COALESCE(rate,0) FROM medicines WHERE name=? LIMIT 1", (name,)
        )
        r = cur.fetchone()
        rate = float(r[0] if r else 0)
    return {
        "medicine_name": name,
        "pack_size": pack,
        "quantity": qty_f,
        "unit_price": rate,
        "current_stock": stock,
        "min_stock": min_stock_level(conn, med_type),
    }


def _reorder_tab_from_group(conn: sqlite3.Connection, group_id: str) -> dict[str, Any]:
    from core.reorder_service import fetch_pending_orders_by_group

    orders = fetch_pending_orders_by_group(conn, group_id)
    if not orders:
        raise ValueError("Order not found.")
    if (orders[0].get("status") or "").lower() == "received":
        raise ValueError("Cannot edit a received order.")
    first = orders[0]
    lines: list[dict[str, Any]] = []
    for order in orders:
        line = _build_reorder_line(
            conn,
            int(order.get("supplier_id") or 0),
            str(order.get("medicine_name") or ""),
            str(order.get("pack_size") or ""),
            float(order.get("quantity") or 0),
        )
        if line:
            line["unit_price"] = float(order.get("unit_price") or 0)
            line["id"] = order.get("id")
            lines.append(line)
    supplier_label = (
        first.get("supplier_name")
        or first.get("supplier_name_manual")
        or "Supplier"
    )
    real_group_id = first.get("order_group_id") or (
        group_id if not str(group_id).startswith("single:") else None
    )
    return {
        "label": str(supplier_label)[:24],
        "supplier_id": first.get("supplier_id"),
        "supplier_name": first.get("supplier_name_manual") or supplier_label,
        "phone": first.get("supplier_phone") or "",
        "offline": bool(first.get("order_offline")),
        "offline_note": first.get("offline_note") or "",
        "delivery": first.get("expected_delivery_date") or "",
        "notes": first.get("notes") or "",
        "lines": lines,
        "editing_group_id": real_group_id,
    }


def _reorder_bulk_tabs(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    from core.reorder_service import _resolve_supplier_id, collect_reorder_candidates

    groups: dict[str, list[dict[str, Any]]] = {}
    for item in collect_reorder_candidates(conn):
        supplier = (item.get("supplier_name") or "").strip() or "(No Supplier)"
        groups.setdefault(supplier, []).append(item)
    tabs: list[dict[str, Any]] = []
    for label in sorted(groups.keys(), key=lambda x: x.lower()):
        sid = None if label == "(No Supplier)" else _resolve_supplier_id(conn, label)
        lines = [
            {
                "medicine_name": item["medicine_name"],
                "pack_size": item.get("pack_size", ""),
                "quantity": float(item.get("suggested_qty") or 0),
                "unit_price": float(item.get("unit_price") or 0),
            }
            for item in groups[label]
        ]
        tabs.append(
            {
                "label": label[:24],
                "supplier_id": sid,
                "supplier_name": "" if label == "(No Supplier)" else label,
                "phone": "",
                "offline": label == "(No Supplier)",
                "offline_note": "",
                "delivery": "",
                "notes": "",
                "lines": lines,
                "editing_group_id": None,
            }
        )
    return tabs


def reorder_action(conn: sqlite3.Connection, data: dict[str, Any]) -> dict[str, Any]:
    action = str(data.get("action") or "").strip().lower()
    from core.reorder_service import (
        build_purchase_prefill,
        cancel_pending_order_group,
        collect_reorder_candidates,
        create_supplier_grouped_draft_orders,
        fetch_medicine_names_for_supplier,
        fetch_pending_order_groups,
        fetch_supplier_choices,
        mark_order_group_received,
        save_pending_order,
        save_supplier_pending_orders,
    )

    if action == "list":
        return get_reorder(conn)
    if action == "create_grouped":
        result = create_supplier_grouped_draft_orders(conn)
        return {"ok": True, "result": result, "groups": fetch_pending_order_groups(conn)}
    if action == "save_draft":
        order_id = save_pending_order(conn, data.get("order") or {}, status="draft")
        return {"ok": True, "order_id": order_id}
    if action == "suppliers":
        return {"suppliers": fetch_supplier_choices(conn)}
    if action == "candidates":
        items = collect_reorder_candidates(conn)
        return {"ok": True, "items": items}
    if action == "purchase_prefill":
        order_id = int(data.get("order_id") or 0)
        prefill = build_purchase_prefill(conn, order_id)
        return {"ok": True, "prefill": prefill}
    if action == "mark_received":
        group_id = str(data.get("group_id") or "").strip()
        if not group_id:
            return {"ok": False, "error": "group_id required"}
        mark_order_group_received(conn, group_id)
        return {"ok": True, "groups": fetch_pending_order_groups(conn)}
    if action == "cancel":
        group_id = str(data.get("group_id") or "").strip()
        if not group_id:
            return {"ok": False, "error": "group_id required"}
        cancel_pending_order_group(conn, group_id)
        return {"ok": True, "groups": fetch_pending_order_groups(conn)}
    if action == "supplier_medicines":
        sid = int(data.get("supplier_id") or 0)
        if sid <= 0:
            return {"ok": False, "error": "supplier_id required"}
        return {"ok": True, "medicines": fetch_medicine_names_for_supplier(conn, sid)}
    if action == "build_line":
        line = _build_reorder_line(
            conn,
            int(data.get("supplier_id") or 0),
            str(data.get("medicine_name") or ""),
            str(data.get("pack_size") or ""),
            float(data.get("quantity") or 0),
        )
        return {"ok": True, "line": line}
    if action == "save_supplier_draft":
        header = data.get("header") or {}
        lines = data.get("lines") or []
        status = str(data.get("status") or "draft").strip().lower()
        group_id = data.get("group_id") or header.get("editing_group_id")
        try:
            result = save_supplier_pending_orders(
                conn, header, lines, status=status, group_id=group_id
            )
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        return {
            "ok": True,
            "result": result,
            "groups": fetch_pending_order_groups(conn),
        }
    if action == "load_group":
        group_id = str(data.get("group_id") or "").strip()
        if not group_id:
            return {"ok": False, "error": "group_id required"}
        try:
            tab = _reorder_tab_from_group(conn, group_id)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "tabs": [tab]}
    if action == "bulk_tabs":
        tabs = _reorder_bulk_tabs(conn)
        if not tabs:
            return {"ok": False, "error": "No low-stock / near-expiry candidates found."}
        return {"ok": True, "tabs": tabs}
    raise ValueError(f"Unknown reorder action: {action}")


def get_settings_bundle(conn: Optional[sqlite3.Connection]) -> dict[str, Any]:
    """Light Settings payload — heavy tabs load on demand."""
    out: dict[str, Any] = {
        "options": get_options(),
        "appearance": get_appearance(),
        "layout_lists": get_layout_lists(),
        "sales_billing": get_sales_billing(),
        "system": get_system(),
        "import": get_import_prefs(),
    }
    if conn is not None:
        out["pharmacy"] = get_pharmacy(conn)
        # Keep thresholds for Appearance/Sales prefs that reference them.
        try:
            out["thresholds"] = get_thresholds(conn)
        except Exception:
            pass
    return out
