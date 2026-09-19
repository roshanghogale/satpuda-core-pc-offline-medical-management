"""What a release build may and may not carry. One definition, six specs.

This lived only in SatpudaEngine_Folder.spec, so the five classic Tk specs kept
shipping one shop's Drive backup folder, the developer's printer, and a counter's
learned distributor names to whoever installed them. Copying the rule into each
spec would have been the same bug waiting to happen again, so the specs import
it from here.

CLEAN is the default on purpose: forgetting the flag must give the SAFE build,
never the leaky one. Set SATPUDA_BUILD=paired to make a build for one named shop.
"""
import os

CLEAN_RELEASE = os.environ.get("SATPUDA_BUILD", "clean").strip().lower() != "paired"

# This machine's or one shop's own state. Never product defaults.
SHOP_IDENTITY = {
    # This file pairs a Drive folder with a STORE NAME, and the name is what
    # must not ship. The folder itself is the vendor's own and every shop needs
    # it, so it travels separately in config/drive_backup_folder.dat (see
    # VENDOR_CREDENTIALS below). Excluding this one used to exclude both, and
    # backup died on every fresh install for five days before anybody noticed.
    "config/backup_config.dat",         # a specific shop's Drive backup folder
    "config/import_learned.json",       # distributor names learned at one counter
    "config/printer_settings.json",     # the developer's own printer
    "config/bill_print_settings.json",  # ...and their own Documents path
    "config/activation.dat",            # an activated shop keeps this in AppData
    "config/expiry.dat",                # and Online mode reads expiry from the server
    # The SIGNED licence, and the worst of the three to ship. It is bound to the
    # machine that fetched it, so a copy in the build would fail verification on
    # every shop it reached -- which reads as "this licence has been tampered
    # with" and blocks the till. It belongs in AppData on one PC and nowhere else.
    "config/license.seal",
    "config/app_mode.txt",              # load_app_mode has its own default
    # Whatever the engine last saved on the machine that ran the build. Run from
    # source, layout_config._get_config_dir IS this repo's config/, so the
    # builder's own banner width, Quick Access, column choices and type list rode
    # along -- the owner's 1.0.2 carried "home_banner_size": 1000. It only seeds
    # AppData when that has no file, and every default is built in code:
    # frozen_appdata_setup writes "{}" and repair_layout_config_file fills it.
    "config/layout_config.txt",
}

# The vendor's OWN credentials, and they are part of the product.
#
# These were briefly excluded and that was wrong: it broke two features.
#   * config/gemini_api_key.txt -- core/gemini_bill_config.py:84 falls back to
#     the bundled key, so removing it leaves bill IMAGE IMPORT with no key at
#     all until every shop types one in by hand.
#   * config/backup_creds.dat -- core/backup_manager.py builds the Drive
#     Credentials entirely out of this file, client_id and client_secret
#     included. Without it NO store can back up to Drive.
#   * config/drive_backup_folder.dat -- the other half of the same pair: the ONE
#     Drive parent folder every shop backs into, under a per-store subfolder
#     created at runtime. It was lost with backup_config.dat in the 2026-09-11
#     clean-release change and nothing has shipped a backup destination since,
#     so a FRESH install did no backup at all -- not Drive, not pendrive -- and
#     said nothing. It holds a folder id and no store name, so there is no
#     shop's identity in it. scripts/audit_release_folder.py now REFUSES a clean
#     build that does not carry it.
# They are the vendor's accounts, deliberately shared with every copy. That is a
# product decision, not an accident, and it is not a spec's to reverse.
#
# What must never ship is a particular SHOP's identity -- the set above.
VENDOR_CREDENTIALS = {
    "config/backup_creds.dat",
    "config/drive_backup_folder.dat",
    "config/gemini_api_key.txt",
    "config/firebase_service_account.json",
    "service_account.json",
    "oauth_client.json",
}

# Leaving one of these out does not tighten a build, it breaks a feature -- and
# `existing()` drops an absent source file without failing, so a build machine
# that is missing one ships a quietly crippled product. release_datas() shouts.
REQUIRED_IN_EVERY_BUILD = {
    "config/backup_creds.dat",
    "config/drive_backup_folder.dat",
}


def clean_release(pairs):
    """Drop one shop's identity. The vendor's own credentials stay."""
    if not CLEAN_RELEASE:
        return list(pairs)
    kept = []
    for src, dest in pairs:
        key = str(src).replace("\\", "/")
        if key in SHOP_IDENTITY:
            print(f"[spec] CLEAN build: leaving out {src}")
            continue
        kept.append((src, dest))
    return kept


def existing(pairs):
    """Drop data entries whose source file is absent.

    Several config files are only created on first run (config/app_mode.txt is
    one). PyInstaller treats a missing source as a hard error, so the whole
    build died on a file the app happily creates itself.
    """
    kept = []
    for src, dest in pairs:
        if os.path.exists(src):
            kept.append((src, dest))
        else:
            key = str(src).replace("\\", "/")
            if key in REQUIRED_IN_EVERY_BUILD:
                # Not optional. This is how the Drive destination went missing:
                # the build machine's copy of the file was gone, the line stayed
                # in the spec, and the build succeeded with a product that could
                # not back anything up.
                print(
                    f"[spec] *** MISSING REQUIRED FILE: {src} -- this build will "
                    f"ship without it and the feature that needs it will not work. "
                    f"Run: python scripts/make_vendor_drive_folder.py"
                )
            else:
                print(f"[spec] skipping absent optional data file: {src}")
    return kept


def release_datas(pairs):
    """The one call a spec needs: shop identity out, missing files out."""
    return existing(clean_release(pairs))
