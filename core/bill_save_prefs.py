"""Sales bill save folder and PDF layout — stored in bill_print_settings.json."""

import os
import sys

# PDF layout when saving a sale (F5) / Print Sales single copy.
PDF_LAYOUT_TWO_COPIES = "two_copies"
PDF_LAYOUT_ONE_BOTTOM = "one_bottom"
PDF_LAYOUT_ONE_TOP = "one_top"
PDF_LAYOUT_ONE_A6 = "one_a6_landscape"

_PDF_LAYOUT_VALUES = (PDF_LAYOUT_TWO_COPIES, PDF_LAYOUT_ONE_BOTTOM, PDF_LAYOUT_ONE_TOP, PDF_LAYOUT_ONE_A6)


def _config_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.join(
            os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
            "VeterinaryApp",
        )
    return os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "config",
    )


def _legacy_save_dir_path() -> str:
    return os.path.join(_config_dir(), "sales_bill_save_dir.txt")


def _legacy_pdf_layout_path() -> str:
    return os.path.join(_config_dir(), "sales_bill_pdf_layout.txt")


def _migrate_legacy_files() -> None:
    """One-time import from old .txt files into bill_print_settings.json."""
    from core.bill_config import DEFAULT_BILL_PRINT_SETTINGS, SETTINGS_PATH, save_bill_print_settings
    import json

    merged = dict(DEFAULT_BILL_PRINT_SETTINGS)
    try:
        if os.path.isfile(SETTINGS_PATH):
            with open(SETTINGS_PATH, encoding="utf-8-sig") as fh:
                stored = json.load(fh)
            if isinstance(stored, dict):
                merged.update(stored)
    except Exception:
        pass

    changed = False

    if not (merged.get("pdf_save_layout") or "").strip():
        try:
            path = _legacy_pdf_layout_path()
            if os.path.exists(path):
                raw = open(path, encoding="utf-8").read().strip().lower()
                if raw in _PDF_LAYOUT_VALUES:
                    merged["pdf_save_layout"] = raw
                    changed = True
        except Exception:
            pass

    if not (merged.get("sales_bill_save_dir") or "").strip():
        try:
            path = _legacy_save_dir_path()
            if os.path.exists(path):
                raw = open(path, encoding="utf-8").read().strip()
                if raw and os.path.isdir(raw):
                    merged["sales_bill_save_dir"] = raw
                    changed = True
        except Exception:
            pass

    if changed:
        save_bill_print_settings(merged)


def load_pdf_save_layout() -> str:
    """How saved PDFs are laid out (from bill_print_settings.json)."""
    _migrate_legacy_files()
    try:
        from core.bill_config import load_bill_print_settings
        raw = str(load_bill_print_settings().get("pdf_save_layout") or "").strip().lower()
        if raw in _PDF_LAYOUT_VALUES:
            return raw
    except Exception:
        pass
    return PDF_LAYOUT_TWO_COPIES


def save_pdf_save_layout(layout: str) -> None:
    val = (layout or "").strip().lower()
    if val not in _PDF_LAYOUT_VALUES:
        val = PDF_LAYOUT_TWO_COPIES
    from core.bill_config import load_bill_print_settings, save_bill_print_settings
    merged = load_bill_print_settings()
    merged["pdf_save_layout"] = val
    save_bill_print_settings(merged)


def pdf_save_layout_label(layout: str) -> str:
    if layout == PDF_LAYOUT_ONE_TOP:
        return "1 copy — top half only (A5 portrait, upright)"
    if layout == PDF_LAYOUT_ONE_BOTTOM:
        return "1 copy — bottom half only (A5 portrait, upright)"
    if layout == PDF_LAYOUT_ONE_A6:
        return "1 copy — A6 horizontal (148×105 mm; same bill as A5 bottom half)"
    return "2 copies — vertical, scissors cut in middle (default)"


def pdf_save_layout_from_label(label: str) -> str:
    """Map settings combo label back to layout key."""
    text = (label or "").strip()
    for val in _PDF_LAYOUT_VALUES:
        if pdf_save_layout_label(val) == text:
            return val
    return PDF_LAYOUT_TWO_COPIES


def apply_f5_save_pdf_layout(settings: dict | None = None) -> dict:
    """Layout for F5 / Save Bill — same as Print Sales 1 (Pharmacy Profile)."""
    from core.bill_config import get_print_slot_settings, load_bill_print_settings, apply_print_bill_layout

    base = get_print_slot_settings(load_bill_print_settings(), 1)
    if settings:
        base.update(settings)
    slot_cfg = (load_bill_print_settings().get("print_slot_1") or {})
    copies = int(slot_cfg.get("copies") or base.get("bill_copies") or 1) if isinstance(slot_cfg, dict) else 1
    return apply_print_bill_layout(base, print_slot_copies=copies)


def pdf_save_layout_combo_values() -> tuple[str, ...]:
    return tuple(pdf_save_layout_label(val) for val in _PDF_LAYOUT_VALUES)


def single_copy_pdf_layout(layout: str | None = None) -> str:
    """Normalize a stored layout to a valid single-copy option."""
    val = (layout or load_pdf_save_layout()).strip().lower()
    if val in (PDF_LAYOUT_ONE_BOTTOM, PDF_LAYOUT_ONE_A6):
        return val
    return PDF_LAYOUT_ONE_BOTTOM


def load_sales_bill_save_dir() -> str:
    """Custom folder for saved sales bills; empty = use Downloads."""
    _migrate_legacy_files()
    try:
        from core.bill_config import load_bill_print_settings
        raw = str(load_bill_print_settings().get("sales_bill_save_dir") or "").strip()
        if raw and os.path.isdir(raw):
            return raw
    except Exception:
        pass
    return ""


def save_sales_bill_save_dir(directory: str) -> str:
    """Persist folder path in bill_print_settings.json."""
    from core.bill_config import load_bill_print_settings, save_bill_print_settings
    raw = (directory or "").strip()
    if raw:
        raw = os.path.abspath(os.path.normpath(raw))
        os.makedirs(raw, exist_ok=True)
        if not os.path.isdir(raw):
            raise ValueError(f"Could not create folder: {raw}")
    merged = load_bill_print_settings()
    merged["sales_bill_save_dir"] = raw
    save_bill_print_settings(merged)
    return raw


def _is_foreign_os_path(raw: str) -> bool:
    """True for a path that belongs to a different OS than the one running.

    The saved setting is shared between machines, so a Windows shop's
    "C:\\Users\\...\\Documents" travelled to a Mac and os.makedirs happily
    created a LITERAL folder called "C:\\Users\\rosha\\OneDrive\\Documents"
    inside the app directory. Bills were written there and nobody could find
    them. Reject the path instead and fall through to the real Documents folder.
    """
    raw = (raw or "").strip()
    if not raw:
        return False
    looks_windows = (len(raw) > 1 and raw[1] == ":") or raw.startswith("\\\\")
    if sys.platform == "win32":
        return not looks_windows and raw.startswith("/")
    return looks_windows


def resolve_sales_bill_save_dir() -> str:
    """Directory to write bill files; creates custom folder when configured."""
    custom = load_sales_bill_save_dir()
    if _is_foreign_os_path(custom):
        custom = ""
    if custom:
        try:
            os.makedirs(custom, exist_ok=True)
            return custom
        except OSError:
            pass
    # Also accept a configured path that exists on disk but failed isdir check earlier.
    try:
        from core.bill_config import load_bill_print_settings
        raw = str(load_bill_print_settings().get("sales_bill_save_dir") or "").strip()
        if raw and not _is_foreign_os_path(raw):
            path = os.path.abspath(os.path.normpath(raw))
            os.makedirs(path, exist_ok=True)
            return path
    except Exception:
        pass
    candidates = []
    if sys.platform == "win32":
        userprofile = os.environ.get("USERPROFILE", "")
        if userprofile:
            candidates.append(os.path.join(userprofile, "Downloads"))
    home = os.path.expanduser("~")
    candidates.extend([
        os.path.join(home, "Downloads"),
        os.path.join(home, "download"),
    ])
    for path in candidates:
        if path and os.path.isdir(path):
            return path
    fallback = os.path.join(home, "Downloads")
    os.makedirs(fallback, exist_ok=True)
    return fallback


def resolve_supplier_doc_save_dir() -> str:
    """Purchase return / reorder PDFs — subfolder under sales bill save dir."""
    path = os.path.join(resolve_sales_bill_save_dir(), "supplier_docs")
    os.makedirs(path, exist_ok=True)
    return path


def resolve_sales_report_save_dir() -> str:
    """Schedule / sales export PDFs — reports subfolder under document save folder."""
    base = resolve_sales_bill_save_dir()
    path = os.path.join(base, "reports")
    try:
        os.makedirs(path, exist_ok=True)
    except OSError:
        pass
    return path


def schedule_report_save_folder_hint() -> str:
    """Human-readable folder path for schedule report PDFs."""
    return resolve_sales_report_save_dir()
