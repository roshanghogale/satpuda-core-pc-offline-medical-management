"""(Re)create config/drive_backup_folder.dat — the destination every build ships.

Every shop backs up into ONE folder on the vendor's Google account, under a
per-store subfolder the engine creates at runtime. That parent folder used to
travel inside config/backup_config.dat, which also carries a STORE NAME, so the
clean-release rule classified the whole file as one shop's identity and stripped
it from every build. Since then a fresh install had no backup destination at all
and did nothing -- silently.

config/drive_backup_folder.dat holds the folder id and nothing else: no store
name, so there is no shop identity in it, and both build_release_filter.py and
scripts/audit_release_folder.py list it as a vendor credential that belongs in
every build.

Run it with no arguments to copy the folder id out of an existing
config/backup_config.dat (or this PC's %LOCALAPPDATA%\\VeterinaryApp copy), so
nobody has to read the id out loud or paste it anywhere:

    python scripts/make_vendor_drive_folder.py
    python scripts/make_vendor_drive_folder.py --from-folder-id <ID>
    python scripts/make_vendor_drive_folder.py --check

The id is never printed in full.
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core import backup_manager as bm  # noqa: E402


def _mask(folder_id: str) -> str:
    return f"{folder_id[:3]}…{folder_id[-2:]} ({len(folder_id)} chars)" if folder_id else "(none)"


def _folder_id_from_existing_config() -> tuple[str, str]:
    """(folder_id, where_it_came_from) from any backup_config.dat we can read."""
    candidates = [os.path.join(ROOT, "config", "backup_config.dat")]
    try:
        from core.license_manager import _appdata_dir

        candidates.append(os.path.join(_appdata_dir(), "backup_config.dat"))
    except Exception:
        pass
    for path in candidates:
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "rb") as fh:
                folder_id = (bm._decrypt_dict(fh.read()).get("folder_id") or "").strip()
        except Exception:
            continue
        if folder_id:
            return folder_id, path
    return "", ""


def main(argv: list[str]) -> int:
    target = bm._project_vendor_folder_path()

    if "--check" in argv:
        current = bm.read_vendor_drive_folder()
        print(f"  file    : {target}")
        print(f"  exists  : {os.path.isfile(target)}")
        print(f"  reads as: {_mask(current)}")
        return 0 if current else 1

    folder_id = ""
    source = "argument"
    if "--from-folder-id" in argv:
        i = argv.index("--from-folder-id")
        if i + 1 < len(argv):
            folder_id = argv[i + 1].strip()
    if not folder_id:
        folder_id, source = _folder_id_from_existing_config()

    if not folder_id:
        print(
            "  No Drive folder id found.\n"
            "  Pass it once with --from-folder-id <ID>, or run this on a PC that\n"
            "  already has a working backup_config.dat."
        )
        return 1

    bm.write_vendor_drive_folder(folder_id, target)
    print(f"  wrote  : {target}")
    print(f"  from   : {source}")
    print(f"  folder : {_mask(folder_id)}")
    print("  Commit this file and make sure the BUILD machine's copy has it too —")
    print("  scripts/audit_release_folder.py now refuses a clean build without it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
