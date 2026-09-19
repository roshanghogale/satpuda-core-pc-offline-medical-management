"""Say what a finished build is about to hand to a shop.

The leak that prompted this was not exotic: a build carried another shop's Drive
backup folder, that shop's learned distributor names, and 857 KB of a real
supplier list with GSTINs and drug licence numbers in it. Nobody saw, because
nothing looked.

This walks the WHOLE assembled folder -- not just config/ -- and reports:
  * live credentials, by filename
  * any shop name it can find in text files

Exit 1 when a CLEAN build carries either. A paired build (SATPUDA_BUILD=paired)
prints the same list and exits 0, because there the shop's own files belong.

    python scripts/audit_release_folder.py <folder> [--shop "Name Of The Shop"]
"""
from __future__ import annotations

import os
import re
import sys

# The vendor's own accounts, shipped with every copy on purpose. The Drive
# backup credentials and the Gemini key ARE the product: without them no store
# can back up and bill image import has no key. Reported so the list is honest,
# never a reason to refuse a build.
VENDOR_CREDENTIALS = {
    "backup_creds.dat",
    "drive_backup_folder.dat",
    "gemini_api_key.txt",
    "firebase_service_account.json",
    "service_account.json",
    "server_service_account.json",
    "oauth_client.json",
}

# An audit that only looks for what must not be there passes a build that is
# missing what MUST be there. Both of these are halves of one feature: the OAuth
# token and the Drive parent folder every shop backs into. The folder went
# missing on 2026-09-11 and every build since shipped a product that could not
# back up at all on a fresh install -- Drive or pendrive -- while this audit
# printed "clean". A clean build without them is refused now.
REQUIRED_NAMES = {
    "backup_creds.dat": (
        "Google Drive credentials — without this no store can back up. "
        "Check config/backup_creds.dat exists on the build machine."
    ),
    "drive_backup_folder.dat": (
        "the Drive backup DESTINATION — without this a fresh install has "
        "nowhere to back up to and silently does nothing. "
        "Run: python scripts/make_vendor_drive_folder.py"
    ),
}

IDENTITY_NAMES = {
    "backup_config.dat",
    "import_learned.json",
    "printer_settings.json",
    "bill_print_settings.json",
    "activation.dat",
    "expiry.dat",
    "catalog.json",
    "launcher.html",
}

TEXT_EXT = {".html", ".json", ".txt", ".js", ".css", ".md"}
# A shop's name is the giveaway, and so is a drug licence or a GST number.
PATTERNS = [
    ("GSTIN", re.compile(rb'"gstin"\s*:\s*"[0-9A-Z]{10,}')),
    ("drug licence", re.compile(rb'"dl_numbers?"\s*:\s*"[^"]{4,}')),
]

MAX_SCAN = 40 * 1024 * 1024

# Deliberate sample data. sample_import.json ships the example invoice the
# import screen is documented against -- "Vetcare Pharma Pvt Ltd", GSTIN
# 27AABCV1234F1Z5, phone 9876543210: invented, and meant to be there. It is
# still scanned for a real shop's NAME, only its made-up GSTIN is forgiven.
SAMPLE_FILES = {"sample_import.json"}


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: audit_release_folder.py <folder> [--shop NAME]")
        return 2
    root = os.path.abspath(sys.argv[1])
    shop = ""
    if "--shop" in sys.argv:
        i = sys.argv.index("--shop")
        if i + 1 < len(sys.argv):
            shop = sys.argv[i + 1].strip()
    paired = os.environ.get("SATPUDA_BUILD", "clean").strip().lower() == "paired"

    if not os.path.isdir(root):
        print(f"[audit] not a folder: {root}")
        return 2

    secrets: list[str] = []
    identity: list[str] = []
    personal: list[str] = []
    # An assembled build always has a config/ folder (every spec ships
    # config/... datas). Without one this is being pointed at something else --
    # a staging folder, a test fixture -- and "you are missing a product file"
    # would be noise, so the required-file check only runs on a real build.
    is_build = False

    for dirpath, _dirs, files in os.walk(root):
        if os.path.basename(dirpath).lower() == "config":
            is_build = True
        for name in files:
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, root)
            if name in VENDOR_CREDENTIALS:
                secrets.append(rel)
            if name in IDENTITY_NAMES:
                identity.append(rel)
            ext = os.path.splitext(name)[1].lower()
            if ext not in TEXT_EXT:
                continue
            try:
                if os.path.getsize(full) > MAX_SCAN:
                    continue
                with open(full, "rb") as fh:
                    blob = fh.read()
            except OSError:
                continue
            if name not in SAMPLE_FILES:
                for label, rx in PATTERNS:
                    if rx.search(blob):
                        personal.append(f"{rel}  ({label})")
                        break
            if shop:
                needle = shop.encode("utf-8", "replace").lower()
                if needle and needle in blob.lower():
                    personal.append(f"{rel}  (names '{shop}')")

    print("=" * 62)
    print(f"  RELEASE AUDIT — {'PAIRED' if paired else 'CLEAN'} build")
    print(f"  {root}")
    print("=" * 62)

    def show(title: str, rows: list[str]) -> None:
        print(f"  {title}: {len(rows)}")
        for r in sorted(set(rows))[:25]:
            print(f"     {r}")

    show("vendor credentials (expected — the product needs these)", secrets)
    show("SHOP / machine state", identity)
    show("personal or shop data inside a file", personal)

    shipped = {os.path.basename(r) for r in secrets}
    missing: list[tuple[str, str]] = []
    if is_build:
        missing = [(n, why) for n, why in sorted(REQUIRED_NAMES.items())
                   if n not in shipped]
        print(f"  required vendor files MISSING: {len(missing)}")
        for name, why in missing:
            print(f"     {name} — {why}")
    else:
        print("  required vendor files: not checked (no config/ — not a build folder)")

    if paired:
        print("  paired build — the shop files above are expected.")
        print("  Check the shop named is the right one.")
        return 0
    # Two ways to fail. A SHOP's identity must not be there; the vendor's own
    # credentials must be. Excluding the latter once killed Drive backup for
    # every store and left bill image import with no key, and the audit that was
    # supposed to catch bad builds called it clean.
    if identity or personal:
        print()
        print("  REFUSED: this build carries another shop's data.")
        return 1
    if missing:
        print()
        print("  REFUSED: this build is missing a file the product needs.")
        return 1
    print("  clean — no shop's identity in this build.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
