"""Billing layout prefs: FY scope, cash/due position, margin columns, item discount mode.

Persisted in SQLite ``settings`` (key ``billing_layout_prefs``) and mirrored to
legacy file/layout prefs so old PCs migrate automatically. Online mode pushes
the same JSON to the server KV store for sync across devices.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from typing import Any, Optional

SETTINGS_KEY = "billing_layout_prefs"
SERVER_KV_NAME = "billing_layout_prefs"

ITEM_DISCOUNT_RUPEES = "rupees"
ITEM_DISCOUNT_PERCENT = "percent"
_VALID_ITEM_DISCOUNT = frozenset({ITEM_DISCOUNT_RUPEES, ITEM_DISCOUNT_PERCENT})

MARGIN_DISPLAY_RUPEES = "rupees"
MARGIN_DISPLAY_PERCENT = "percent"
_VALID_MARGIN_DISPLAY = frozenset({MARGIN_DISPLAY_RUPEES, MARGIN_DISPLAY_PERCENT})

_DEFAULTS: dict[str, Any] = {
    "history_scope": "current_fy",
    "payment_mode_enabled": True,
    "payment_mode_position": "first",
    "item_discount_mode": ITEM_DISCOUNT_RUPEES,
    "billing_show_item_discount": True,
    "billing_show_margin_column": True,
    "billing_show_total_margin": True,
    # One-shot: existing stores had these defaulted off; first load turns them on.
    "sales_margin_ui_v2": False,
    "billing_margin_loss_warning": True,
    "billing_margin_display_mode": MARGIN_DISPLAY_RUPEES,
    # When False, doctor name is required only for H1 and X (not other schedules).
    "require_doctor_for_other_schedules": True,
    "version": 1,
}


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


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


def _legacy_file_path() -> str:
    return os.path.join(_config_dir(), "billing_layout_prefs.json")


def _get_db_conn(conn: Optional[sqlite3.Connection] = None) -> Optional[sqlite3.Connection]:
    if conn is not None:
        return conn
    try:
        from core.store_manager import get_active_db_path

        path = (get_active_db_path() or "").strip()
        if path and os.path.exists(path):
            return sqlite3.connect(path)
    except Exception:
        pass
    return None


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


def _read_sqlite(conn: sqlite3.Connection) -> Optional[dict[str, Any]]:
    try:
        _ensure_settings_table(conn)
        row = conn.execute(
            "SELECT value FROM settings WHERE name=?", (SETTINGS_KEY,)
        ).fetchone()
        if not row or row[0] is None:
            return None
        data = json.loads(row[0])
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _write_sqlite(conn: sqlite3.Connection, data: dict[str, Any]) -> None:
    _ensure_settings_table(conn)
    conn.execute(
        "INSERT INTO settings (name, value) VALUES (?, ?) "
        "ON CONFLICT(name) DO UPDATE SET value=excluded.value",
        (SETTINGS_KEY, json.dumps(data, ensure_ascii=False)),
    )
    try:
        conn.commit()
    except Exception:
        pass


def _read_legacy_file() -> Optional[dict[str, Any]]:
    path = _legacy_file_path()
    try:
        if os.path.exists(path):
            data = json.loads(open(path, encoding="utf-8").read())
            return data if isinstance(data, dict) else None
    except Exception:
        pass
    return None


def _write_legacy_file(data: dict[str, Any]) -> None:
    path = _legacy_file_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(data, ensure_ascii=False, indent=2))
    except Exception:
        pass


def _read_history_scope_file() -> Optional[str]:
    try:
        if getattr(sys, "frozen", False):
            base = os.path.join(
                os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
                "VeterinaryApp",
            )
            path = os.path.join(base, "history_scope.txt")
        else:
            path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "config",
                "history_scope.txt",
            )
        if os.path.exists(path):
            scope = open(path, encoding="utf-8").read().strip().lower()
            if scope in ("current_fy", "all"):
                return scope
    except Exception:
        pass
    return None


def _gather_legacy_sources() -> dict[str, Any]:
    """Build prefs from existing file/layout modules (first-run migration)."""
    out = dict(_DEFAULTS)
    scope = _read_history_scope_file()
    if scope:
        out["history_scope"] = scope
    try:
        from core.sales_form_prefs import (
            load_payment_mode_enabled,
            load_payment_mode_position,
        )

        out["payment_mode_enabled"] = load_payment_mode_enabled()
        out["payment_mode_position"] = load_payment_mode_position()
    except Exception:
        pass
    try:
        from core.layout_config import load_layout

        layout = load_layout()
        for key in (
            "billing_show_item_discount",
            "billing_show_margin_column",
            "billing_show_total_margin",
            "billing_margin_loss_warning",
        ):
            if key in layout:
                out[key] = bool(layout[key])
        if "item_discount_mode" in layout:
            out["item_discount_mode"] = layout["item_discount_mode"]
    except Exception:
        pass
    file_data = _read_legacy_file()
    if file_data:
        out.update({k: file_data[k] for k in _DEFAULTS if k in file_data})
    return out


def _normalize(data: dict[str, Any] | None) -> dict[str, Any]:
    base = dict(_DEFAULTS)
    if isinstance(data, dict):
        base.update(data)
    scope = str(base.get("history_scope") or "current_fy").strip().lower()
    if scope not in ("current_fy", "all"):
        scope = "current_fy"
    base["history_scope"] = scope
    base["payment_mode_enabled"] = bool(base.get("payment_mode_enabled", True))
    pos = str(base.get("payment_mode_position") or "first").strip().lower()
    if pos not in ("first", "after_bill_date"):
        pos = "first"
    base["payment_mode_position"] = pos
    mode = str(base.get("item_discount_mode") or ITEM_DISCOUNT_RUPEES).strip().lower()
    if mode not in _VALID_ITEM_DISCOUNT:
        mode = ITEM_DISCOUNT_RUPEES
    base["item_discount_mode"] = mode
    margin_mode = str(
        base.get("billing_margin_display_mode") or MARGIN_DISPLAY_RUPEES
    ).strip().lower()
    if margin_mode not in _VALID_MARGIN_DISPLAY:
        margin_mode = MARGIN_DISPLAY_RUPEES
    base["billing_margin_display_mode"] = margin_mode
    for key in (
        "billing_show_item_discount",
        "billing_show_margin_column",
        "billing_show_total_margin",
        "billing_margin_loss_warning",
        "require_doctor_for_other_schedules",
        "sales_margin_ui_v2",
    ):
        base[key] = bool(base.get(key, _DEFAULTS[key]))
    try:
        base["version"] = max(1, int(base.get("version") or 1))
    except (TypeError, ValueError):
        base["version"] = 1
    if not base.get("updated_at"):
        base["updated_at"] = _utcnow_iso()
    return base


def _mirror_to_legacy_modules(data: dict[str, Any]) -> None:
    """Keep old file-based prefs in sync so existing readers keep working."""
    try:
        from core.history_prefs import (
            HISTORY_SCOPE_ALL,
            HISTORY_SCOPE_CURRENT_FY,
            _config_path,
        )

        scope = data.get("history_scope") or HISTORY_SCOPE_CURRENT_FY
        if scope not in (HISTORY_SCOPE_CURRENT_FY, HISTORY_SCOPE_ALL):
            scope = HISTORY_SCOPE_CURRENT_FY
        path = _config_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(scope)
    except Exception:
        pass
    try:
        from core.sales_form_prefs import (
            save_payment_mode_enabled,
            save_payment_mode_position,
        )

        save_payment_mode_enabled(bool(data.get("payment_mode_enabled", True)))
        save_payment_mode_position(str(data.get("payment_mode_position") or "first"))
    except Exception:
        pass
    try:
        from core.layout_config import load_layout, save_layout

        layout = load_layout()
        changed = False
        for key in (
            "billing_show_item_discount",
            "billing_show_margin_column",
            "billing_show_total_margin",
            "billing_margin_loss_warning",
        ):
            val = bool(data.get(key, _DEFAULTS[key]))
            if layout.get(key) != val:
                layout[key] = val
                changed = True
        margin_mode = str(
            data.get("billing_margin_display_mode") or MARGIN_DISPLAY_RUPEES
        ).strip().lower()
        if margin_mode not in _VALID_MARGIN_DISPLAY:
            margin_mode = MARGIN_DISPLAY_RUPEES
        if layout.get("billing_margin_display_mode") != margin_mode:
            layout["billing_margin_display_mode"] = margin_mode
            changed = True
        disc_mode = str(data.get("item_discount_mode") or ITEM_DISCOUNT_RUPEES).strip().lower()
        if disc_mode not in _VALID_ITEM_DISCOUNT:
            disc_mode = ITEM_DISCOUNT_RUPEES
        if layout.get("item_discount_mode") != disc_mode:
            layout["item_discount_mode"] = disc_mode
            changed = True
        if changed:
            save_layout(layout)
    except Exception:
        pass
    _write_legacy_file(data)


def _push_server(data: dict[str, Any]) -> None:
    try:
        from core.sync_prefs import is_online_mode

        if not is_online_mode():
            return
        from core import server_live as live

        live.push_settings_kv(SERVER_KV_NAME, data)
    except Exception:
        pass


def _ensure_sales_margin_visible(data: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """Turn Sales margin column + Total Margin on once for existing stores."""
    if bool(data.get("sales_margin_ui_v2")):
        return data, False
    data["billing_show_margin_column"] = True
    data["billing_show_total_margin"] = True
    data["sales_margin_ui_v2"] = True
    data["updated_at"] = _utcnow_iso()
    return data, True


def load_billing_layout_prefs(
    conn: Optional[sqlite3.Connection] = None,
) -> dict[str, Any]:
    """Load prefs; migrate from legacy sources into SQLite on first use."""
    owned = conn is None
    db = _get_db_conn(conn)
    try:
        raw = _read_sqlite(db) if db is not None else None
        from_legacy = raw is None
        if raw is None:
            raw = _gather_legacy_sources()
        data = _normalize(raw)
        data, migrated = _ensure_sales_margin_visible(data)
        if from_legacy or migrated:
            data["updated_at"] = data.get("updated_at") or _utcnow_iso()
            if db is not None:
                try:
                    _write_sqlite(db, data)
                except Exception:
                    pass
            # Mirror once so old/new PCs share the same file + layout keys
            try:
                _mirror_to_legacy_modules(data)
            except Exception:
                _write_legacy_file(data)
        return data
    finally:
        if owned and db is not None:
            try:
                db.close()
            except Exception:
                pass


def save_billing_layout_prefs(
    updates: dict[str, Any],
    conn: Optional[sqlite3.Connection] = None,
    *,
    bump_version: bool = True,
    push_remote: bool = True,
) -> dict[str, Any]:
    current = load_billing_layout_prefs(conn)
    merged = dict(current)
    for key in _DEFAULTS:
        if key == "version":
            continue
        if key in updates:
            merged[key] = updates[key]
    # Allow explicit version from server apply
    if "version" in updates and not bump_version:
        try:
            merged["version"] = max(1, int(updates["version"]))
        except (TypeError, ValueError):
            pass
    if "updated_at" in updates and not bump_version:
        merged["updated_at"] = updates["updated_at"]
    data = _normalize(merged)
    if bump_version:
        data["version"] = int(current.get("version") or 1) + 1
        data["updated_at"] = _utcnow_iso()
    owned = conn is None
    db = _get_db_conn(conn)
    try:
        if db is not None:
            try:
                _write_sqlite(db, data)
            except Exception:
                pass
    finally:
        if owned and db is not None:
            try:
                db.close()
            except Exception:
                pass
    _mirror_to_legacy_modules(data)
    if push_remote:
        _push_server(data)
    return data


def apply_server_billing_layout_prefs(
    payload: Any,
    conn: Optional[sqlite3.Connection] = None,
) -> str:
    """Apply server KV / settings doc into local SQLite + legacy files."""
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except Exception:
            return "skipped"
    if not isinstance(payload, dict):
        return "skipped"
    # Server may wrap as {name, value} or raw prefs
    if "item_discount_mode" not in payload and "value" in payload:
        val = payload.get("value")
        if isinstance(val, str):
            try:
                val = json.loads(val)
            except Exception:
                return "skipped"
        if not isinstance(val, dict):
            return "skipped"
        payload = val
    local = load_billing_layout_prefs(conn)
    try:
        remote_ver = int(payload.get("version") or 0)
    except (TypeError, ValueError):
        remote_ver = 0
    local_ver = int(local.get("version") or 0)
    remote_at = str(payload.get("updated_at") or "")
    local_at = str(local.get("updated_at") or "")
    if remote_ver < local_ver:
        return "skipped"
    if remote_ver == local_ver and remote_at and local_at and remote_at <= local_at:
        return "skipped"
    save_billing_layout_prefs(
        payload, conn, bump_version=False, push_remote=False
    )
    return "applied"


def load_item_discount_mode(conn: Optional[sqlite3.Connection] = None) -> str:
    return str(
        load_billing_layout_prefs(conn).get("item_discount_mode") or ITEM_DISCOUNT_RUPEES
    )


def item_discount_is_percent(conn: Optional[sqlite3.Connection] = None) -> bool:
    return load_item_discount_mode(conn) == ITEM_DISCOUNT_PERCENT


def show_item_discount_field(conn: Optional[sqlite3.Connection] = None) -> bool:
    """Whether the per-medicine Disc ₹/% entry + column is visible on Sales."""
    return bool(load_billing_layout_prefs(conn).get("billing_show_item_discount", True))


def item_discount_mode_label(mode: str | None = None) -> str:
    m = (mode or load_item_discount_mode()).strip().lower()
    if m == ITEM_DISCOUNT_PERCENT:
        return "Percentage (%)"
    return "Rupees (₹)"


def convert_item_discount_input_to_rupees(
    raw_value: float,
    line_base: float,
    *,
    mode: str | None = None,
) -> float:
    """Convert UI discount input to stored rupee discount for the line."""
    m = (mode or load_item_discount_mode()).strip().lower()
    try:
        val = float(raw_value or 0)
    except (TypeError, ValueError):
        val = 0.0
    base = max(0.0, float(line_base or 0))
    if m == ITEM_DISCOUNT_PERCENT:
        rs = round(base * max(0.0, val) / 100.0, 2)
    else:
        rs = round(max(0.0, val), 2)
    if base > 0:
        rs = min(rs, base)
    return rs


def load_margin_display_mode(conn: Optional[sqlite3.Connection] = None) -> str:
    return str(
        load_billing_layout_prefs(conn).get("billing_margin_display_mode")
        or MARGIN_DISPLAY_RUPEES
    )


def margin_display_is_percent(conn: Optional[sqlite3.Connection] = None) -> bool:
    return load_margin_display_mode(conn) == MARGIN_DISPLAY_PERCENT


def margin_display_mode_label(mode: str | None = None) -> str:
    m = (mode or load_margin_display_mode()).strip().lower()
    if m == MARGIN_DISPLAY_PERCENT:
        return "Percentage (%)"
    return "Rupees (₹)"


_ALWAYS_REQUIRE_DOCTOR = frozenset({"H1", "X"})


def _norm_schedule_code(schedule: str | None) -> str:
    return (
        str(schedule or "")
        .strip()
        .upper()
        .replace(" ", "")
        .replace("-", "")
    )


def require_doctor_for_other_schedules(conn: Optional[sqlite3.Connection] = None) -> bool:
    """True (default): any non-empty schedule needs a doctor. False: only H1 and X."""
    return bool(
        load_billing_layout_prefs(conn).get("require_doctor_for_other_schedules", True)
    )


def schedule_requires_doctor(
    schedule: str | None,
    conn: Optional[sqlite3.Connection] = None,
    *,
    require_other: bool | None = None,
) -> bool:
    code = _norm_schedule_code(schedule)
    if not code:
        return False
    if code in _ALWAYS_REQUIRE_DOCTOR:
        return True
    if require_other is None:
        require_other = require_doctor_for_other_schedules(conn)
    return bool(require_other)


def sale_requires_doctor(
    medicines: list | None,
    conn: Optional[sqlite3.Connection] = None,
) -> bool:
    """True when this bill must have a doctor name before save."""
    require_other = require_doctor_for_other_schedules(conn)
    for m in medicines or []:
        if not isinstance(m, dict):
            continue
        sch = m.get("schedule")
        if schedule_requires_doctor(sch, conn, require_other=require_other):
            return True
    return False
