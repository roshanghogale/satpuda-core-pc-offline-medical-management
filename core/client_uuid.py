"""B4.1 — stable client_uuid for sales / purchases / medicines."""
from __future__ import annotations

import sqlite3
import uuid
from typing import Optional

CLIENT_UUID_TABLES = ("sales", "purchases", "medicines")


def ensure_client_uuid_schema(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()
    for table in CLIENT_UUID_TABLES:
        cur.execute(f"PRAGMA table_info({table})")
        cols = {str(r[1]) for r in cur.fetchall()}
        if "client_uuid" not in cols:
            cur.execute(f"ALTER TABLE {table} ADD COLUMN client_uuid TEXT")
        try:
            cur.execute(
                f"CREATE UNIQUE INDEX IF NOT EXISTS uq_{table}_client_uuid "
                f"ON {table}(client_uuid) WHERE client_uuid IS NOT NULL AND client_uuid != ''"
            )
        except Exception:
            pass
    try:
        conn.commit()
    except Exception:
        pass


def ensure_row_client_uuid(conn: sqlite3.Connection, table: str, row_id: int) -> Optional[str]:
    """Generate and persist client_uuid before first push. Returns uuid or None."""
    if table not in CLIENT_UUID_TABLES:
        return None
    ensure_client_uuid_schema(conn)
    cur = conn.cursor()
    cur.execute(f"SELECT client_uuid FROM {table} WHERE id=?", (int(row_id),))
    row = cur.fetchone()
    if not row:
        return None
    existing = (row[0] or "").strip()
    if existing:
        return existing
    # Medicines must use the DETERMINISTIC uuid, not a random one.
    #
    # server_crud._with_medicine_uuid already stamps the deterministic value, but
    # this generic helper stamped uuid4() for the same table -- so whichever path
    # touched a medicine first decided its identity. When the random one won,
    # Android's deterministic uuid could never match it and the server allocated a
    # SECOND local_id: two rows for one stock line, with the quantity split
    # between them. Sales and purchases are genuine events and stay random.
    if table == "medicines":
        cur.execute("SELECT name, batch_no FROM medicines WHERE id=?", (int(row_id),))
        med = cur.fetchone()
        name = (med[0] if med else "") or ""
        if str(name).strip():
            new_uuid = deterministic_medicine_uuid(name, (med[1] if med else "") or "")
        else:
            new_uuid = str(uuid.uuid4())
    else:
        new_uuid = str(uuid.uuid4())
    cur.execute(
        f"UPDATE {table} SET client_uuid=? WHERE id=? AND "
        f"(client_uuid IS NULL OR client_uuid='')",
        (new_uuid, int(row_id)),
    )
    try:
        conn.commit()
    except Exception:
        pass
    return new_uuid


def client_uuid_from_row(row: dict) -> Optional[str]:
    raw = row.get("client_uuid") if isinstance(row, dict) else None
    if raw is None:
        return None
    s = str(raw).strip()
    return s or None

# ── Deterministic identity for MEDICINES ─────────────────────────────────────
#
# Sales and purchases are distinct events, so a random uuid is right for them.
# A medicine is NOT an event: "ZZ PARACETAMOL 500 / batch ZZB1" is the same
# physical stock line no matter which device first records it.
#
# Both clients used UUID.randomUUID() for medicines, so when the PC created a
# medicine and Android later touched it, the uuids never matched,
# resolveLocalIdByClientUuid found nothing, and the server allocated a SECOND
# local_id — the duplicate-medicine bug (two rows, same name+batch, split stock).
#
# Deriving the uuid from the natural key makes both devices arrive at the same
# value independently, so the server's existing idempotency dedupes on its own
# and the name+batch "twin merge" workaround is not needed.
#
# Must byte-for-byte match Android's ClientUuid.forMedicine(), which uses
# java.util.UUID.nameUUIDFromBytes (MD5, version 3, IETF variant).

def medicine_identity_key(name: str, batch: str) -> str:
    n = " ".join(str(name or "").split()).upper()
    b = "".join(str(batch or "").split()).upper()
    return f"medicine|{n}|{b}"


def deterministic_medicine_uuid(name: str, batch: str) -> str:
    """Same algorithm as Java UUID.nameUUIDFromBytes(key.getBytes(UTF_8))."""
    import hashlib

    key = medicine_identity_key(name, batch)
    h = bytearray(hashlib.md5(key.encode("utf-8")).digest())
    h[6] = (h[6] & 0x0F) | 0x30   # version 3
    h[8] = (h[8] & 0x3F) | 0x80   # IETF variant
    return str(uuid.UUID(bytes=bytes(h)))
