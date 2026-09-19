# Before any desktop build: verify the build tree

## The command

```
cd "/Volumes/Extreme SSD/projects/mac2" && python3 tools/verify_build_tree.py check
```

Run it on the Mac, **first**, every time, before anyone starts
`build_desktop_win10_folder.bat` on the Windows PC.

It prints `BUILD TREE VERIFIED -- CLEAN` and exits 0 only when every file the
build consumes is byte-identical on the Mac and in `D:\satpuda-build\mac2`.
Anything else names the offending files and exits non-zero.

| exit | meaning | what to do |
|------|---------|------------|
| 0 | the two trees are identical | build |
| 2 | drift found | copy the listed files, run again until it says CLEAN. **Do not build.** |
| 3 | the gate could not decide (PC unreachable, output truncated, the two sides disagree about which files count) | fix that first. **Do not build.** A gate that cannot see the build tree must never pass it. |

## Why

The 2026-09-14 release shipped `widgets/activation_dialog.py` dated 2026-08-07 --
a month **older** than the Mac's copy -- because `D:\satpuda-build\mac2` is an
incremental copy of the Mac tree and nothing ever compared the two. The rest of
`widgets/` had been refreshed at 02:36 that morning, so the sync ran and quietly
skipped that one file. The shipped dialog is the pre-server Firebase version: it
calls `mark_pending_bootstrap()` and never calls `ensure_online_store_link()`, so
activation never creates the store on the server. That is the owner's "a new shop
cannot be signed up, and the app ends on licence-not-found with no way out".
`widgets/searchable_combo.py` shipped stale the same way.

Nothing in the build could have caught it: PyInstaller freezes whatever module it
finds, the test suite runs against the Mac tree rather than the tree the build
reads, and the zip's `.sha256` only proves the zip matches itself.

## What it compares

`core/`, `ui/`, `widgets/`, `bill_templates/`, `desktop/src/`, `installer/`,
`main.py`, `build_release_filter.py` (every spec imports it) and every `*.spec` at
the repo root. Caches, `__pycache__`, `._*`, editor droppings, logs and
`installer/Output/` are ignored -- the exact rules are the SELECTOR block at the
top of `tools/verify_build_tree.py`, mirrored in `tools/hash_build_tree.ps1`.
Both sides hash that block, so if only one of the two files is ever edited the
gate refuses to compare instead of comparing the wrong thing.

## How to read the report

* **SILENTLY STALE IN THE BUILD REPO** -- the PC holds an older copy *and* the
  Mac's copy was already that old when the last sync ran. The sync had every
  chance to deliver it and did not. This is the activation-dialog failure. Treat
  it as a broken sync, not as a file to hand-copy and forget.
* **NOT YET COPIED TO THE PC** -- edited on the Mac after the last sync. Ordinary
  unsynced work, but the build would still ship the old bytes.
* **MISSING IN BUILD REPO** -- the build cannot see the file at all.
* **CONTENT DIFFERS** -- same path, different bytes, PC copy not older: someone
  edited the build repo directly, or the clocks disagree. Look before copying.
* **EXTRA IN BUILD REPO** -- deleted on the Mac, still on the PC, still shippable.
* **SAME NAME, DIFFERENT CASE** -- Windows keeps one, the Mac keeps both.

## Other ways to run it

```
# no ssh: hash the PC tree there, compare here
python3 tools/verify_build_tree.py check --pc-manifest build.manifest

# this tree's manifest, for keeping next to a release
python3 tools/verify_build_tree.py manifest --out mac.manifest

# compare two manifests
python3 tools/verify_build_tree.py compare mac.manifest build.manifest
```

On the PC itself (only when the Mac cannot reach it), with the Mac's manifest
copied over:

```
tools\verify_build_tree.cmd C:\path\to\mac.manifest
```

## What it does to the build PC

Nothing. Over ssh it runs `tools/hash_build_tree.ps1`, which only lists
directories and hashes files: no writes, no build, no service, no `Set-Content`
unless a person passes `-Out` by hand. The gate never changes either tree -- it
only refuses.

## After a build

Keep the manifest that passed:

```
python3 tools/verify_build_tree.py manifest --out release-YYYY-MM-DD.manifest
```

It is the only record of what a given zip was actually built from. Reconstructing
the 2026-09-14 baseline afterwards took a Windows folder listing and a backup zip.
