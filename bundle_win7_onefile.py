"""Post-build helpers for Win7 single-file EXE."""
from __future__ import annotations
import os, shutil
ROOT = os.path.dirname(os.path.abspath(__file__))
DIST = os.path.join(ROOT, "dist")
TOOLS = os.path.join(ROOT, "tools")
REDIST = os.path.join(ROOT, "redist")
EXE = os.path.join(DIST, "SatpudaCore_Win7.exe")

def main() -> int:
    if not os.path.isfile(EXE):
        print(f"ERROR: missing {EXE}")
        return 1
    dest_tools = os.path.join(DIST, "tools")
    os.makedirs(dest_tools, exist_ok=True)
    for name in ("SumatraPDF32.exe", "SumatraPDF.exe"):
        src = os.path.join(TOOLS, name)
        if os.path.isfile(src):
            shutil.copy2(src, os.path.join(dest_tools, name))
            print(f"  tools/{name}")
            break
    vc = os.path.join(REDIST, "vc_redist.x86.exe")
    if os.path.isfile(vc):
        shutil.copy2(vc, os.path.join(DIST, "vc_redist.x86.exe"))
    mb = os.path.getsize(EXE) / (1024 * 1024)
    print(f"Ready: {EXE} ({mb:.1f} MB)")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
