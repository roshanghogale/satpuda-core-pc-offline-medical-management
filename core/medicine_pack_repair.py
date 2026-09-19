"""Repair Online inventory rows stored as Tablet/Capsule with unit=1 but strip MRP."""
from __future__ import annotations

import logging
import re
from typing import Optional

log = logging.getLogger(__name__)

_SESSION_REPAIRED = False


def _norm_core(name: str) -> str:
    key = re.sub(r"[^A-Z0-9]+", "", str(name or "").upper())
    return re.sub(r"(TABLETS?|TABS?|CAPSULES?|CAPS?|MG|MCG)$", "", key)


def repair_unit1_strip_medicines(*, force: bool = False) -> int:
    """
    For strip-type rows with unit==1, copy pack size from a sibling TABLET row
    (e.g. UNIENZYME TAB → 15 from UNIENZYME TABLET). Returns count updated.
    """
    global _SESSION_REPAIRED
    if _SESSION_REPAIRED and not force:
        return 0
    try:
        from core.sync_prefs import is_online_mode
        if not is_online_mode():
            return 0
    except Exception:
        return 0

    from core.layout_config import (
        is_strip_count_type,
        parse_tablets_per_stripe,
        resolve_tablets_per_stripe,
    )
    from core.online_catalog import invalidate, medicines
    from core.server_crud import upsert_medicine_online

    rows = list(medicines() or [])
    by_core: dict[str, list[dict]] = {}
    for m in rows:
        core = _norm_core(m.get("name") or "")
        if len(core) < 4:
            continue
        by_core.setdefault(core, []).append(m)

    fixed = 0
    for m in rows:
        med_type = m.get("type") or ""
        unit = str(m.get("unit") or "1").strip() or "1"
        if not is_strip_count_type(med_type, unit):
            continue
        if parse_tablets_per_stripe(unit) > 1:
            continue
        name = m.get("name") or ""
        tps = resolve_tablets_per_stripe(unit, name=name, med_type=med_type)
        if tps <= 1:
            # Prefer explicit sibling with unit>1
            core = _norm_core(name)
            for sib in by_core.get(core, []):
                st = parse_tablets_per_stripe(sib.get("unit") or "1")
                if st > 1:
                    tps = st
                    break
        if tps <= 1:
            continue
        mid = int(m.get("id") or m.get("local_id") or 0)
        if mid <= 0:
            continue
        try:
            row = dict(m)
            row["id"] = mid
            row["local_id"] = mid
            row["unit"] = str(tps)
            upsert_medicine_online(row)
            fixed += 1
            log.info("Repaired medicine unit id=%s %r -> %s", mid, name, tps)
        except Exception as exc:
            log.warning("repair unit failed id=%s: %s", mid, exc)

    if fixed:
        invalidate("medicines")
    _SESSION_REPAIRED = True
    return fixed


def schedule_pack_repair(root=None) -> None:
    """Run pack repair once in a background thread (Online)."""
    try:
        from core.sync_prefs import is_online_mode
        if not is_online_mode():
            return
    except Exception:
        return
    from core.background_workers import run_in_thread

    run_in_thread(
        repair_unit1_strip_medicines,
        name="MedicinePackRepair",
        root=root,
    )
