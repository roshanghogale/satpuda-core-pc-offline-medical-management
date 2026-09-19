"""Patch PyInstaller loader for Windows 7 (AddDllDirectory / WinError 127)."""
from __future__ import annotations

import argparse
import os
import shutil

MARKER = "# SATPUDA_WIN7_PATCH"


def _loader_dir() -> str:
    import PyInstaller
    return os.path.join(os.path.dirname(PyInstaller.__file__), "loader")


def _patch_pyimod04(path: str) -> bool:
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    if MARKER in text:
        return False
    old = "    os.add_dll_directory(pywin32_system32_path)"
    new = (
        "    try:\n"
        "        os.add_dll_directory(pywin32_system32_path)\n"
        "    except OSError:\n"
        "        pass  " + MARKER + "\n"
    )
    if old not in text:
        raise RuntimeError("Unexpected pyimod04_pywin32.py: " + path)
    text = text.replace(old, new, 1)
    backup = path + ".satpuda_bak"
    if not os.path.isfile(backup):
        shutil.copy2(path, backup)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    return True


def _restore_pyimod04(path: str) -> bool:
    backup = path + ".satpuda_bak"
    if not os.path.isfile(backup):
        return False
    shutil.copy2(backup, path)
    os.remove(backup)
    return True


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--restore", action="store_true")
    args = p.parse_args()
    path = os.path.join(_loader_dir(), "pyimod04_pywin32.py")
    if args.restore:
        print("Restored" if _restore_pyimod04(path) else "No backup")
    else:
        print("Patched" if _patch_pyimod04(path) else "Already patched")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
