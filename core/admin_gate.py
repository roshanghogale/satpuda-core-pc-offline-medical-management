"""Administrator PIN for the few actions that can lose or expose a shop's data.

Creating a store, deleting one, and pointing this PC at a different store are not
everyday tasks -- and getting them wrong hands one shop another shop's ledger.
They belong behind a PIN that the owner sets, separate from the ordinary
settings a counter assistant uses all day.

Switching between stores is the exception: many shops want an assistant to be
able to do that and nothing else, so it is allowed WITHOUT the PIN unless the
owner turns that on. The PIN itself is stored only as a salted hash.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import sqlite3
from typing import Any, Optional

_PIN_HASH = "admin_pin_hash"
_PIN_SALT = "admin_pin_salt"
_SWITCH_NEEDS_PIN = "admin_store_switch_needs_pin"

# Every action that can move a shop's data somewhere it does not belong.
PROTECTED_ACTIONS = frozenset({
    "create_store",
    # Connecting this PC to a different store on the server hands it that
    # shop's entire ledger. It belongs with the other store actions.
    "join_server_store",
    "delete_store",
    "rename_store",
    "switch_store",
})


class AdminPinRequired(PermissionError):
    """The action needs the administrator PIN, and none (or a wrong one) came."""

    def __init__(self, action: str, *, wrong: bool = False):
        self.action = action
        self.wrong = wrong
        super().__init__(
            "Wrong administrator PIN."
            if wrong
            else "This action needs the administrator PIN."
        )


def _get(conn: sqlite3.Connection, name: str, default: str = "") -> str:
    try:
        row = conn.execute(
            "SELECT value FROM settings WHERE name=?", (name,)
        ).fetchone()
    except Exception:
        return default
    if not row or row[0] is None:
        return default
    return str(row[0])


def _set(conn: sqlite3.Connection, name: str, value: str) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS settings (name TEXT PRIMARY KEY, value TEXT)"
    )
    conn.execute(
        "INSERT OR REPLACE INTO settings (name, value) VALUES (?, ?)", (name, value)
    )
    # Online runs on an in-memory database, so the PIN would be forgotten at
    # every exit without this.
    try:
        from core.settings_mirror import remember

        remember(name, value)
    except Exception:
        pass


def _hash(pin: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256", str(pin).encode("utf-8"), bytes.fromhex(salt), 120_000
    ).hex()


def is_pin_set(conn: sqlite3.Connection) -> bool:
    return bool(_get(conn, _PIN_HASH)) and bool(_get(conn, _PIN_SALT))


def switch_needs_pin(conn: sqlite3.Connection) -> bool:
    """Whether changing the active store also needs the PIN. Off by default."""
    return str(_get(conn, _SWITCH_NEEDS_PIN, "0")).strip() in ("1", "true", "yes")


def set_switch_needs_pin(conn: sqlite3.Connection, needed: bool) -> None:
    _set(conn, _SWITCH_NEEDS_PIN, "1" if needed else "0")
    conn.commit()


def verify_pin(conn: sqlite3.Connection, pin: str) -> bool:
    stored, salt = _get(conn, _PIN_HASH), _get(conn, _PIN_SALT)
    if not stored or not salt:
        return False
    try:
        return hmac.compare_digest(stored, _hash(str(pin or ""), salt))
    except Exception:
        return False


def set_pin(conn: sqlite3.Connection, new_pin: str, *, current_pin: str = "") -> None:
    """Set or change the PIN. Changing one requires the current PIN."""
    new_pin = str(new_pin or "").strip()
    if len(new_pin) < 4:
        raise ValueError("The administrator PIN must be at least 4 characters.")
    if is_pin_set(conn) and not verify_pin(conn, current_pin):
        raise AdminPinRequired("set_pin", wrong=True)
    salt = os.urandom(16).hex()
    _set(conn, _PIN_SALT, salt)
    _set(conn, _PIN_HASH, _hash(new_pin, salt))
    conn.commit()


def clear_pin(conn: sqlite3.Connection, current_pin: str) -> None:
    """Remove the PIN, leaving the actions open again."""
    if is_pin_set(conn) and not verify_pin(conn, current_pin):
        raise AdminPinRequired("clear_pin", wrong=True)
    _set(conn, _PIN_HASH, "")
    _set(conn, _PIN_SALT, "")
    conn.commit()


def requires_pin(conn: sqlite3.Connection, action: str) -> bool:
    """Does this action need the PIN right now?"""
    if action not in PROTECTED_ACTIONS:
        return False
    if not is_pin_set(conn):
        # No PIN configured: nothing to enforce, and a shop that never set one
        # must not be locked out of its own stores.
        return False
    if action == "switch_store":
        return switch_needs_pin(conn)
    return True


def check(conn: sqlite3.Connection, action: str, data: dict[str, Any]) -> None:
    """Raise unless this action is allowed. Call before doing the work."""
    if not requires_pin(conn, action):
        return
    supplied = str((data or {}).get("admin_pin") or "")
    if not supplied:
        raise AdminPinRequired(action)
    if not verify_pin(conn, supplied):
        raise AdminPinRequired(action, wrong=True)


def status(conn: sqlite3.Connection) -> dict[str, Any]:
    """What the Administrator screen needs to render itself."""
    return {
        "pin_set": is_pin_set(conn),
        "store_switch_needs_pin": switch_needs_pin(conn),
        "protected_actions": sorted(PROTECTED_ACTIONS),
    }
