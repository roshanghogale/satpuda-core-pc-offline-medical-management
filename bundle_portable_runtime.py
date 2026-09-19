"""Post-build portable folder bundler (no zip unless --zip)."""
from __future__ import annotations

import argparse
import os
import shutil

ROOT = os.path.dirname(os.path.abspath(__file__))
DIST = os.path.join(ROOT, "dist")
TOOLS = os.path.join(ROOT, "tools")
REDIST = os.path.join(ROOT, "redist")
VC_REDIST_X86 = os.path.join(REDIST, "vc_redist.x86.exe")
ZIP_DIR = os.path.join(ROOT, "release_zips")

from pyinstaller_win7_runtime import copy_runtime_to_folder


def _copy_sumatra(dist_dir: str, *, win7: bool) -> list[str]:
    copied: list[str] = []
    dest_tools = os.path.join(dist_dir, "tools")
    os.makedirs(dest_tools, exist_ok=True)
    candidates = ["SumatraPDF32.exe", "SumatraPDF.exe"] if win7 else ["SumatraPDF64.exe", "SumatraPDF32.exe"]
    for name in candidates:
        src = os.path.join(TOOLS, name)
        if os.path.isfile(src):
            shutil.copy2(src, os.path.join(dest_tools, name))
            copied.append(name)
            if win7 and name == "SumatraPDF32.exe":
                break
    return copied


def _bundle_vc_redist(dist_dir: str) -> None:
    if not os.path.isfile(VC_REDIST_X86):
        return
    shutil.copy2(VC_REDIST_X86, os.path.join(dist_dir, "vc_redist.x86.exe"))
    with open(os.path.join(dist_dir, "Install_VC_Runtime.bat"), "w", encoding="utf-8") as fh:
        fh.write("@echo off\r\necho Installing VC++ x86 runtime...\r\nvc_redist.x86.exe /install /quiet /norestart\r\necho Done.\r\npause\r\n")


def _write_readme(dist_dir: str, folder_name: str) -> None:
    win7 = "Win7" in folder_name
    lines = [
        f"Satpuda Core - {folder_name}",
        "",
        "Copy this ENTIRE folder. Required: _internal and tools subfolders.",
        "",
    ]
    if win7:
        lines += ["Win7 SP1 required. Runtime DLLs are inside _internal.", "If startup fails, run Install_VC_Runtime.bat as Admin once.", ""]
    lines += ["Print: tools\\SumatraPDF32.exe", "Data: %LOCALAPPDATA%\\VeterinaryApp", ""]
    with open(os.path.join(dist_dir, "READ_ME_FIRST.txt"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))


def _verify_internal(dist_dir: str) -> None:
    internal = os.path.join(dist_dir, "_internal")
    required = ("python38.dll", "ucrtbase.dll", "api-ms-win-crt-runtime-l1-1-0.dll", "MSVCP140.dll", "VCRUNTIME140.dll")
    missing = [n for n in required if not os.path.isfile(os.path.join(internal, n))]
    if missing:
        print(f"  WARNING _internal missing: {', '.join(missing)}")
    else:
        api = sum(1 for n in os.listdir(internal) if n.lower().startswith("api-ms-win-crt") and n.lower().endswith(".dll"))
        print(f"  _internal OK ({api} api-ms-win-crt DLLs)")


def bundle_folder(folder_name: str) -> None:
    dist_dir = os.path.join(DIST, folder_name)
    if not os.path.isdir(dist_dir):
        print(f"SKIP missing: {dist_dir}")
        return
    win7 = "Win7" in folder_name
    print(f"\n=== {folder_name} ===")
    print(f"  runtime DLL copies: {copy_runtime_to_folder(dist_dir)}")
    _verify_internal(dist_dir)
    print(f"  Sumatra: {_copy_sumatra(dist_dir, win7=win7)}")
    if win7:
        _bundle_vc_redist(dist_dir)
    _write_readme(dist_dir, folder_name)
    print(f"  ready: {dist_dir}")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--zip", action="store_true")
    p.add_argument("--win7-only", action="store_true")
    args = p.parse_args()
    folders = ("SatpudaCore_Win7",) if args.win7_only else ("SatpudaCore_Win7", "SatpudaCore_Win10")
    for f in folders:
        bundle_folder(f)
    if args.zip:
        _zip_folder("SatpudaCore_Win7", "SatpudaCore_Win7_Portable.zip")
        if not args.win7_only:
            _zip_folder("SatpudaCore_Win10", "SatpudaCore_Win10_Portable.zip")
    print("\nDone.")
    return 0


def _zip_folder(folder_name: str, zip_name: str) -> None:
    import zipfile
    dist_dir = os.path.join(DIST, folder_name)
    if not os.path.isdir(dist_dir):
        return
    os.makedirs(ZIP_DIR, exist_ok=True)
    zip_path = os.path.join(ZIP_DIR, zip_name)
    if os.path.isfile(zip_path):
        os.remove(zip_path)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for root, _dirs, files in os.walk(dist_dir):
            for name in files:
                full = os.path.join(root, name)
                arc = os.path.join(folder_name, os.path.relpath(full, dist_dir))
                zf.write(full, arc)
    mb = os.path.getsize(zip_path) / (1024 * 1024)
    print(f"  zipped: {zip_path} ({mb:.1f} MB)")


if __name__ == "__main__":
    raise SystemExit(main())
