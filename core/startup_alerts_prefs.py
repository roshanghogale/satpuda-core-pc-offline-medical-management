"""Preferences for daily startup alert dialogs (low stock, expiry, due)."""

import os
import sys
from datetime import date


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


def _enabled_path() -> str:
    return os.path.join(_config_dir(), "startup_alerts_enabled.txt")


def _snooze_path() -> str:
    return os.path.join(_config_dir(), "startup_alerts_snooze.txt")


def load_startup_alerts_enabled() -> bool:
    path = _enabled_path()
    try:
        if os.path.exists(path):
            return open(path, encoding="utf-8").read().strip().lower() not in (
                "0", "false", "no", "off",
            )
    except Exception:
        pass
    return True


def save_startup_alerts_enabled(enabled: bool) -> None:
    os.makedirs(_config_dir(), exist_ok=True)
    with open(_enabled_path(), "w", encoding="utf-8") as f:
        f.write("1" if enabled else "0")


def snooze_startup_alerts_for_today() -> None:
    os.makedirs(_config_dir(), exist_ok=True)
    with open(_snooze_path(), "w", encoding="utf-8") as f:
        f.write(date.today().isoformat())


def _snoozed_until_today() -> bool:
    path = _snooze_path()
    try:
        if os.path.exists(path):
            raw = open(path, encoding="utf-8").read().strip()
            return raw == date.today().isoformat()
    except Exception:
        pass
    return False


def should_show_startup_alerts() -> bool:
    if not load_startup_alerts_enabled():
        return False
    if _snoozed_until_today():
        return False
    return True


# ── Popup preferences beyond on/off (Settings -> Alert & Monitoring -> Popup) ──
# Every key here is read by desktop_startup_service.get_startup_alerts; none is
# decorative.

import json  # noqa: E402

CATEGORY_KEYS = ("low_stock", "out_of_stock", "near_expiry", "expired", "customer_due")
# While the app is open, look again this often and pop up ONLY rows not already
# shown today (a batch newly under its minimum, one that just crossed into the
# near-expiry window or expired). 0 = only at startup.
DEFAULT_RECHECK_MINUTES = 60
MAX_RECHECK_MINUTES = 24 * 60

# Which columns identify a row, per tab, for "already shown today".
_ROW_KEY_COLS = {
    "Low Stock Alerts": (0, 2),      # name, batch
    "Out of Stock": (0, 1),          # name, pack
    "Near Expiry Alerts": (0, 2),    # name, batch
    "Expired Medicines": (0, 2),     # name, batch
    "Customer Due Alerts": (0, 1),   # customer, phone
}


def _prefs_path() -> str:
    return os.path.join(_config_dir(), "startup_alerts_prefs.json")


def _seen_path() -> str:
    return os.path.join(_config_dir(), "startup_alerts_seen.json")


def _read_json(path: str) -> dict:
    try:
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {}


def _write_json(path: str, data: dict) -> None:
    os.makedirs(_config_dir(), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp, path)


def clear_snooze() -> None:
    try:
        os.remove(_snooze_path())
    except FileNotFoundError:
        pass


def load_alert_popup_prefs() -> dict:
    raw = _read_json(_prefs_path())
    cats = raw.get("categories") if isinstance(raw.get("categories"), dict) else {}
    try:
        recheck = int(raw.get("recheck_minutes", DEFAULT_RECHECK_MINUTES))
    except (TypeError, ValueError):
        recheck = DEFAULT_RECHECK_MINUTES
    return {
        "enabled": load_startup_alerts_enabled(),
        "snoozed_today": _snoozed_until_today(),
        "categories": {k: bool(cats.get(k, True)) for k in CATEGORY_KEYS},
        "recheck_minutes": max(0, min(recheck, MAX_RECHECK_MINUTES)),
    }


def save_alert_popup_prefs(data: dict) -> dict:
    raw = _read_json(_prefs_path())
    if "enabled" in data:
        save_startup_alerts_enabled(bool(data["enabled"]))
    if isinstance(data.get("categories"), dict):
        cats = dict(raw.get("categories") or {})
        for k, v in data["categories"].items():
            if k in CATEGORY_KEYS:
                cats[k] = bool(v)
        raw["categories"] = cats
    if "recheck_minutes" in data:
        try:
            minutes = int(float(data["recheck_minutes"]))
        except (TypeError, ValueError):
            raise ValueError("Re-check interval must be a whole number of minutes.")
        raw["recheck_minutes"] = max(0, min(minutes, MAX_RECHECK_MINUTES))
    if data.get("clear_snooze"):
        clear_snooze()
    _write_json(_prefs_path(), raw)
    return load_alert_popup_prefs()


def _row_key(title: str, row) -> str:
    cols = _ROW_KEY_COLS.get(title, (0,))
    vals = [str(row[i]) if i < len(row) else "" for i in cols]
    return title + "|" + "|".join(v.strip().upper() for v in vals)


def _seen_today() -> set:
    data = _read_json(_seen_path())
    if data.get("date") != date.today().isoformat():
        return set()
    return set(data.get("keys") or [])


def mark_alerts_seen(tabs) -> None:
    keys = _seen_today()
    for t in tabs or []:
        for r in t.get("rows") or []:
            keys.add(_row_key(t.get("title", ""), r))
    try:
        _write_json(_seen_path(), {"date": date.today().isoformat(), "keys": sorted(keys)})
    except Exception:
        pass


def unseen_alerts(tabs) -> list:
    """Only the rows not already shown today -- the re-check never repeats itself."""
    seen = _seen_today()
    out = []
    for t in tabs or []:
        rows = [r for r in t.get("rows") or [] if _row_key(t.get("title", ""), r) not in seen]
        if rows:
            out.append({**t, "rows": rows})
    return out
