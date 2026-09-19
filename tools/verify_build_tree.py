#!/usr/bin/env python3
"""Refuse to build when the Windows build repo is not this tree.

WHAT WENT WRONG
The desktop release is built on the Windows PC from D:\\satpuda-build\\mac2, an
incremental copy of this tree that nothing ever verified. On 2026-09-14 the copy
was wrong in the one direction nobody watches for -- OLDER:

    widgets/activation_dialog.py   Mac 2026-08-13 32750 B | build repo 2026-08-07 31411 B
    widgets/searchable_combo.py    Mac 2026-08-26 21333 B | build repo 2026-08-06 19321 B

The rest of widgets/ had been refreshed at 02:36 that morning, so the sync ran and
silently skipped these two. engine_modules.txt confirms widgets.activation_dialog
is frozen into the shipped engine, so every shop got the pre-server, Firebase-era
activation dialog: it calls mark_pending_bootstrap() and never calls
ensure_online_store_link(), i.e. activation never creates the store on the server.
That is the owner's "cannot sign up a new shop / licence not found / no way out".

A stale file is invisible to every check the build already has. PyInstaller
happily freezes an August module; the tests here pass because they run against
THIS tree, not the one the build reads; the zip's .sha256 only proves the zip
matches itself. So the gate has to be its own step, and it has to run BEFORE the
build, comparing the two trees file by file.

WHAT IT CHECKS
Every file a build actually consumes: core/, ui/, widgets/, bill_templates/,
desktop/src/, installer/, main.py, build_release_filter.py (the specs import it)
and every *.spec at the root. Not tests/, not logs, not caches -- see SELECTOR.

HOW TO USE IT (the command the next build must run first)

    cd "/Volumes/Extreme SSD/projects/mac2" && python3 tools/verify_build_tree.py check

It hashes this tree, runs tools/hash_build_tree.ps1 on the build PC over ssh
(read-only: Get-ChildItem and Get-FileHash, nothing written, nothing started),
compares, and exits 0 ONLY if the two trees are identical. Anything else prints
every offending path and exits non-zero. Do not build on a non-zero exit; re-copy
the listed files to the PC and run it again until it says CLEAN.

Other modes:
    python3 tools/verify_build_tree.py manifest                 # this tree -> stdout
    python3 tools/verify_build_tree.py manifest --root DIR --out FILE
    python3 tools/verify_build_tree.py compare MAC.manifest BUILD.manifest
    python3 tools/verify_build_tree.py check --pc-manifest FILE  # no ssh; compare a file the PC produced

Exit codes:  0 = identical (safe to build)   2 = drift found (do not build)
             3 = could not decide (ssh failed, manifest truncated, rules mismatch)
Exit 3 is a refusal too. A gate that cannot see the other tree must never say yes.
"""
from __future__ import annotations

import argparse
import base64
import datetime as _dt
import fnmatch
import gzip
import hashlib
import os
import subprocess
import sys

# --------------------------------------------------------------- SELECTOR ---
# What a build consumes. This block MUST stay byte-identical to the selector in
# tools/hash_build_tree.ps1: both sides hash it and the manifests carry the hash,
# so a PC running an older copy of the runner is refused instead of compared.
# tests/test_a_stale_build_tree_is_refused.py fails if the two ever drift apart.
# v2 (2026-09-16): the three root modules every .spec imports and the vendor's
# Drive folder id were outside v1, so the gate could print CLEAN on a tree that
# would ship an engine with no QR encoder (pyinstaller_extra_bundle.py pins
# qrcode) or a build with nowhere to back up (config/drive_backup_folder.dat).
# Only that ONE config file is listed: the rest of config/ is the dev machine's
# own shop identity and is meant to differ.
SELECTOR_VERSION = "2"
DIRS = ("bill_templates", "core", "desktop/src", "installer", "ui", "widgets")
FILES = ("build_release_filter.py", "main.py", "pyinstaller_extra_bundle.py",
         "pyinstaller_tk_bundle.py", "pyinstaller_win7_runtime.py",
         "config/drive_backup_folder.dat")
GLOBS = ("*.spec",)
SKIP_DIRS = (".git", ".idea", ".vscode", "__MACOSX", "__pycache__", "build",
             "dist", "node_modules", "target", "venv", ".venv")
SKIP_EXTS = (".bak", ".log", ".orig", ".pyc", ".pyo", ".rej", ".swp", ".tmp")
SKIP_NAMES = (".DS_Store", "Thumbs.db", "desktop.ini")
SKIP_PREFIXES = ("._", "~$")
# Paths that live under a build-input directory but are output, not input.
SKIP_PATHS = ("installer/Output",)

MANIFEST_BEGIN = "#satpuda-build-manifest-begin v1"
MANIFEST_END = "#satpuda-build-manifest-end"

DEFAULT_HOST = "192.168.31.74"
DEFAULT_USER = "win10"
DEFAULT_KEY = "~/.ssh/satpuda_win"
DEFAULT_BUILD_ROOT = r"D:\satpuda-build\mac2"

EXIT_CLEAN = 0
EXIT_DRIFT = 2
EXIT_CANNOT_DECIDE = 3

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class GateError(Exception):
    """The gate could not decide. Always a refusal, never a pass."""


def selector_text() -> str:
    return "\n".join([
        f"selector-version={SELECTOR_VERSION}",
        "dirs=" + ",".join(DIRS),
        "files=" + ",".join(FILES),
        "globs=" + ",".join(GLOBS),
        "skip-dirs=" + ",".join(SKIP_DIRS),
        "skip-exts=" + ",".join(SKIP_EXTS),
        "skip-names=" + ",".join(SKIP_NAMES),
        "skip-prefixes=" + ",".join(SKIP_PREFIXES),
        "skip-paths=" + ",".join(SKIP_PATHS),
    ])


def selector_id() -> str:
    return hashlib.sha256(selector_text().encode("utf-8")).hexdigest()


def is_skipped(rel: str) -> bool:
    """Same rules as Test-Skipped in hash_build_tree.ps1 (case-insensitive)."""
    low = rel.lower()
    for p in SKIP_PATHS:
        if low == p.lower() or low.startswith(p.lower() + "/"):
            return True
    parts = rel.split("/")
    lowered_dirs = {d.lower() for d in SKIP_DIRS}
    for seg in parts[:-1]:
        if seg.lower() in lowered_dirs:
            return True
    name = parts[-1]
    if any(name.startswith(p) for p in SKIP_PREFIXES):
        return True
    if name.lower() in {n.lower() for n in SKIP_NAMES}:
        return True
    ext = os.path.splitext(name)[1].lower()
    return bool(ext) and ext in SKIP_EXTS


def iter_build_inputs(root: str, warnings=None):
    """Every path a build consumes, relative to root, with '/' separators."""
    seen = set()
    warnings = warnings if warnings is not None else []
    for d in DIRS:
        base = os.path.join(root, *d.split("/"))
        if not os.path.isdir(base):
            warnings.append("missing-dir " + d)
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [x for x in dirnames
                           if x.lower() not in {s.lower() for s in SKIP_DIRS}]
            for fn in filenames:
                full = os.path.join(dirpath, fn)
                rel = os.path.relpath(full, root).replace(os.sep, "/")
                if is_skipped(rel) or rel in seen:
                    continue
                seen.add(rel)
                yield rel, full
    for f in FILES:
        full = os.path.join(root, *f.split("/"))
        if os.path.isfile(full):
            if f not in seen:
                seen.add(f)
                yield f, full
        else:
            warnings.append("missing-file " + f)
    for pattern in GLOBS:
        for fn in sorted(os.listdir(root)):
            full = os.path.join(root, fn)
            if not os.path.isfile(full) or not fnmatch.fnmatch(fn, pattern):
                continue
            if is_skipped(fn) or fn in seen:
                continue
            seen.add(fn)
            yield fn, full


def sha256_file(path: str) -> tuple:
    h = hashlib.sha256()
    size = 0
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            h.update(chunk)
    return h.hexdigest(), size


def build_manifest(root: str, side: str = "mac") -> str:
    root = os.path.abspath(root)
    rows = []
    warnings = []
    for rel, full in iter_build_inputs(root, warnings):
        try:
            digest, size = sha256_file(full)
            mtime = int(os.path.getmtime(full))
        except OSError as exc:
            raise GateError(f"cannot read build input {rel}: {exc}") from exc
        rows.append((rel, f"{digest}\t{size}\t{mtime}\t{rel}"))
    rows.sort(key=lambda pair: pair[0])
    generated = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    lines = [
        MANIFEST_BEGIN,
        "#selector " + selector_id(),
        "#side " + side,
        "#root " + root,
        "#generated " + generated,
    ]
    lines += ["#warn " + w for w in warnings]
    lines += [row for _, row in rows]
    lines.append(f"{MANIFEST_END} {len(rows)}")
    return "\n".join(lines) + "\n"


def parse_manifest(text: str, label: str) -> dict:
    """Return {'selector', 'side', 'root', 'files': {rel: (sha, size, mtime)}}.

    Tolerates whatever ssh/PowerShell wraps around the output (CLIXML progress
    records have shown up before) but refuses anything truncated: no end marker,
    or a count that does not match, means we did not see the whole tree.
    """
    started = False
    ended = False
    declared = None
    meta = {"selector": None, "side": None, "root": None}
    files = {}
    for raw in text.splitlines():
        line = raw.rstrip("\r")
        if not started:
            if line.strip() == MANIFEST_BEGIN:
                started = True
            continue
        if ended:
            continue
        if line.startswith(MANIFEST_END):
            ended = True
            rest = line[len(MANIFEST_END):].strip()
            try:
                declared = int(rest)
            except ValueError as exc:
                raise GateError(f"{label}: unreadable end marker {line!r}") from exc
            continue
        if line.startswith("#fatal"):
            raise GateError(f"{label}: {line[1:].strip()}")
        if line.startswith("#"):
            for key in ("selector", "side", "root"):
                if line.startswith("#" + key + " "):
                    meta[key] = line[len(key) + 2:].strip()
            continue
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) != 4:
            raise GateError(f"{label}: bad manifest line {line!r}")
        digest, size, mtime, rel = parts
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise GateError(f"{label}: bad hash in line {line!r}")
        try:
            entry = (digest, int(size), int(mtime))
        except ValueError as exc:
            raise GateError(f"{label}: bad size/mtime in line {line!r}") from exc
        if rel in files:
            raise GateError(f"{label}: {rel} listed twice")
        files[rel] = entry
    if not started:
        raise GateError(f"{label}: no manifest in the output "
                        "(the runner did not run, or ssh swallowed it)")
    if not ended:
        raise GateError(f"{label}: manifest is truncated -- no end marker. "
                        "Treat as drift, not as clean.")
    if declared != len(files):
        raise GateError(f"{label}: manifest says {declared} files, "
                        f"{len(files)} arrived -- output was cut")
    if not meta["selector"]:
        raise GateError(f"{label}: manifest carries no selector id")
    return {"selector": meta["selector"], "side": meta["side"] or label,
            "root": meta["root"] or "?", "files": files}


def compare_manifests(mac: dict, build: dict) -> dict:
    """Classify every difference.

    Every class below refuses the build. They are split only so the operator can
    see WHICH kind of wrong this is, because one kind is far more dangerous:

      silently_stale  the build repo's copy is older than the Mac's, AND the Mac's
                      copy was already that old when the last sync ran (its mtime
                      is at or before the newest file in the build repo). So the
                      sync had every chance to deliver this file and did not. This
                      is exactly widgets/activation_dialog.py, and nothing else in
                      the build notices it.
      not_yet_copied  the build repo is older, but the Mac file was edited after
                      the last sync -- ordinary work that has not been copied yet.
      content_differs same path, different bytes, and the PC copy is NOT older.
                      Someone edited the build repo directly, or clocks disagree.
    """
    if mac["selector"] != build["selector"]:
        raise GateError(
            "the two manifests were produced by DIFFERENT selector rules "
            f"({mac['selector'][:12]} vs {build['selector'][:12]}). The build PC is "
            "running an old copy of tools/hash_build_tree.ps1 -- copy the current "
            "one over and re-run. Refusing to compare.")
    mac_files, build_files = mac["files"], build["files"]
    # When the build repo was last written to. Used only to tell the two "older on
    # the PC" cases apart; it never decides pass/fail.
    last_sync = max((m for _, _, m in build_files.values()), default=0)
    report = {
        "silently_stale": [], "not_yet_copied": [], "missing_in_build": [],
        "content_differs": [], "extra_in_build": [], "case_clash": [],
        "checked": len(mac_files), "mac_root": mac["root"], "build_root": build["root"],
        "last_sync": last_sync,
    }
    lower_build = {}
    for rel in build_files:
        lower_build.setdefault(rel.lower(), []).append(rel)
    for rel, (digest, size, mtime) in sorted(mac_files.items()):
        if rel not in build_files:
            twins = [x for x in lower_build.get(rel.lower(), []) if x != rel]
            if twins:
                report["case_clash"].append((rel, twins[0]))
            else:
                report["missing_in_build"].append((rel, size, mtime <= last_sync))
            continue
        b_digest, b_size, b_mtime = build_files[rel]
        if b_digest == digest:
            continue
        row = (rel, size, mtime, b_size, b_mtime)
        if b_mtime >= mtime:
            report["content_differs"].append(row)
        elif mtime <= last_sync:
            report["silently_stale"].append(row)
        else:
            report["not_yet_copied"].append(row)
    for rel in sorted(build_files):
        if rel not in mac_files and not any(rel == t for _, t in report["case_clash"]):
            report["extra_in_build"].append((rel, build_files[rel][1]))
    report["clean"] = not any(report[k] for k in
                              ("silently_stale", "not_yet_copied", "missing_in_build",
                               "content_differs", "extra_in_build", "case_clash"))
    return report


def _age(older: int, newer: int) -> str:
    days = (newer - older) / 86400.0
    if days >= 1:
        return f"{days:.0f} day(s) older"
    return f"{(newer - older) / 3600.0:.1f} hour(s) older"


def _stamp(epoch: int) -> str:
    return _dt.datetime.fromtimestamp(epoch, _dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def format_report(report: dict) -> str:
    out = []
    if report["clean"]:
        out.append("BUILD TREE VERIFIED -- CLEAN")
        out.append(f"  {report['checked']} build inputs are byte-identical.")
        out.append(f"  mac:   {report['mac_root']}")
        out.append(f"  build: {report['build_root']}")
        out.append("  Safe to build.")
        return "\n".join(out)

    out.append("BUILD REFUSED -- the build repo is not this tree")
    out.append(f"  mac:   {report['mac_root']}")
    out.append(f"  build: {report['build_root']}")
    out.append(f"  {report['checked']} build inputs checked; build repo last written "
               f"{_stamp(report['last_sync'])}")
    if report["silently_stale"]:
        out.append("")
        out.append(f"SILENTLY STALE IN THE BUILD REPO ({len(report['silently_stale'])}) "
                   "-- THIS IS THE ONE THAT SHIPPED THE AUGUST ACTIVATION DIALOG.")
        out.append("  The PC holds an older copy AND the Mac's copy was already this old "
                   "when the")
        out.append("  last sync ran, so the sync skipped it. A build now freezes the old "
                   "file, and")
        out.append("  nothing else in the build would ever tell you.")
        for rel, size, mtime, b_size, b_mtime in report["silently_stale"]:
            out.append(f"    {rel}")
            out.append(f"        mac   {_stamp(mtime)}  {size} B")
            out.append(f"        build {_stamp(b_mtime)}  {b_size} B  <- {_age(b_mtime, mtime)}")
    if report["not_yet_copied"]:
        out.append("")
        out.append(f"NOT YET COPIED TO THE PC ({len(report['not_yet_copied'])}) "
                   "-- edited here after the last sync.")
        out.append("  Ordinary unsynced work, but the build would still ship the old "
                   "bytes. Copy them over.")
        for rel, size, mtime, b_size, b_mtime in report["not_yet_copied"]:
            out.append(f"    {rel}")
            out.append(f"        mac   {_stamp(mtime)}  {size} B")
            out.append(f"        build {_stamp(b_mtime)}  {b_size} B  <- {_age(b_mtime, mtime)}")
    if report["missing_in_build"]:
        out.append("")
        out.append(f"MISSING IN BUILD REPO ({len(report['missing_in_build'])}) "
                   "-- the build cannot see these at all.")
        for rel, size, was_there_to_copy in report["missing_in_build"]:
            note = "  <- existed before the last sync and was still never copied" \
                if was_there_to_copy else ""
            out.append(f"    {rel}  ({size} B on the Mac){note}")
    if report["content_differs"]:
        out.append("")
        out.append(f"CONTENT DIFFERS ({len(report['content_differs'])}) "
                   "-- same path, different bytes; the PC copy is NOT older.")
        out.append("  Someone edited the build repo directly, or the two clocks disagree. "
                   "Check before copying either way.")
        for rel, size, mtime, b_size, b_mtime in report["content_differs"]:
            out.append(f"    {rel}")
            out.append(f"        mac   {_stamp(mtime)}  {size} B")
            out.append(f"        build {_stamp(b_mtime)}  {b_size} B")
    if report["extra_in_build"]:
        out.append("")
        out.append(f"EXTRA IN BUILD REPO ({len(report['extra_in_build'])}) "
                   "-- deleted here, still on the PC, still shippable.")
        for rel, size in report["extra_in_build"]:
            out.append(f"    {rel}  ({size} B)")
    if report["case_clash"]:
        out.append("")
        out.append(f"SAME NAME, DIFFERENT CASE ({len(report['case_clash'])}) "
                   "-- Windows keeps one of these, the Mac keeps both.")
        for rel, twin in report["case_clash"]:
            out.append(f"    mac {rel}  vs  build {twin}")
    out.append("")
    out.append("DO NOT BUILD. Copy the listed files to the build PC (Mac -> PC for stale/"
               "missing/differing, delete the extras), then run this command again until "
               "it prints CLEAN.")
    return "\n".join(out)


def strip_ps_comments(text: str) -> str:
    """Drop comment and blank lines from the runner. Code only, same behaviour.

    Only so the whole thing fits on a Windows command line -- see remote_command.
    The comments are the point of the file on disk; they are simply not worth
    2 KB of the 8191 characters cmd.exe allows.
    """
    out = []
    in_block = False
    for line in text.splitlines():
        stripped = line.strip()
        if in_block:
            if "#>" in stripped:
                in_block = False
            continue
        if stripped.startswith("<#"):
            if "#>" not in stripped:
                in_block = True
            continue
        if not stripped or stripped.startswith("#"):
            continue
        out.append(line)
    return "\n".join(out) + "\n"


# cmd.exe refuses a command line over 8191 characters, and Windows sshd runs the
# remote command through cmd.exe.
WINDOWS_COMMAND_LIMIT = 8191


def remote_command(script: str, build_root: str) -> str:
    """The -EncodedCommand payload that runs the runner on the PC.

    Two earlier attempts failed, and both failure modes are worth remembering:

      * -EncodedCommand of the whole runner -> "The command line is too long."
      * the runner piped in over stdin, with the remote side doing
        [Console]::In.ReadToEnd() -> the bigger the script grew the more often the
        remote PowerShell never saw the text at all. It then sat blocked on the
        read forever, and each timed-out attempt left another idle powershell
        process on the build PC.

    So: no stdin at all. Gzip the comment-stripped runner, carry it inside the
    command as base64, and unpack it there. Nothing is written on the PC, and if
    this end dies the remote side still finishes on its own instead of hanging.
    """
    payload = "$Root = '{}'\n$SideName = 'build-repo'\n{}".format(
        build_root.replace("'", "''"), strip_ps_comments(script))
    try:
        blob = payload.encode("ascii")
    except UnicodeEncodeError as exc:
        raise GateError(
            "the runner has non-ASCII characters; it travels through a Windows "
            f"console and would arrive corrupted ({exc})") from exc
    packed = base64.b64encode(gzip.compress(blob, 9, mtime=0)).decode("ascii")
    unpack = (
        "$b=[Convert]::FromBase64String('" + packed + "');"
        "$m=New-Object IO.MemoryStream(,$b);"
        "$g=New-Object IO.Compression.GzipStream($m,"
        "[IO.Compression.CompressionMode]::Decompress);"
        "$r=New-Object IO.StreamReader($g);"
        "Invoke-Expression $r.ReadToEnd()"
    )
    encoded = base64.b64encode(unpack.encode("utf-16-le")).decode("ascii")
    if len(encoded) > WINDOWS_COMMAND_LIMIT - 200:
        raise GateError(
            f"the packed runner is {len(encoded)} characters and will not fit on a "
            "Windows command line. Run it on the PC instead:\n"
            "    powershell -NoProfile -ExecutionPolicy Bypass -File "
            "tools\\hash_build_tree.ps1 > build.manifest\n"
            "then compare here with:  verify_build_tree.py check --pc-manifest build.manifest")
    return encoded


def fetch_pc_manifest(host: str, user: str, key: str, build_root: str,
                      runner: str, timeout: int = 900) -> str:
    """Run the hashing runner on the PC over ssh. Read-only: it only hashes."""
    if not os.path.isfile(runner):
        raise GateError(f"runner not found: {runner}")
    with open(runner, "r", encoding="utf-8") as fh:
        script = fh.read()
    encoded = remote_command(script, build_root)
    cmd = [
        "ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15",
        "-i", os.path.expanduser(key), f"{user}@{host}",
        "powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded,
    ]
    try:
        proc = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True,
                              text=True, timeout=timeout)
    except FileNotFoundError as exc:
        raise GateError(f"ssh is not available: {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise GateError(f"the build PC did not answer within {timeout}s") from exc
    if proc.returncode != 0 and MANIFEST_BEGIN not in proc.stdout:
        raise GateError(
            f"ssh to {user}@{host} failed (exit {proc.returncode}). "
            "The gate cannot see the build tree, so it cannot pass it.\n"
            + (proc.stderr or "").strip()[:800])
    return proc.stdout


def _cmd_manifest(args) -> int:
    text = build_manifest(args.root, side=args.side)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text)
        count = len(parse_manifest(text, "this tree")["files"])
        print(f"wrote {args.out} ({count} build inputs)")
    else:
        sys.stdout.write(text)
    return EXIT_CLEAN


def _cmd_compare(args) -> int:
    with open(args.mac_manifest, encoding="utf-8") as fh:
        mac = parse_manifest(fh.read(), "mac manifest")
    with open(args.build_manifest, encoding="utf-8") as fh:
        build = parse_manifest(fh.read(), "build manifest")
    report = compare_manifests(mac, build)
    print(format_report(report))
    return EXIT_CLEAN if report["clean"] else EXIT_DRIFT


def _cmd_check(args) -> int:
    mac = parse_manifest(build_manifest(args.root, side="mac"), "mac tree")
    if args.pc_manifest:
        with open(args.pc_manifest, encoding="utf-8") as fh:
            raw = fh.read()
    else:
        print(f"hashing {args.build_root} on {args.user}@{args.host} (read-only)...",
              file=sys.stderr)
        raw = fetch_pc_manifest(args.host, args.user, args.key, args.build_root,
                                args.runner)
    build = parse_manifest(raw, "build repo")
    if args.save_manifests:
        os.makedirs(args.save_manifests, exist_ok=True)
        with open(os.path.join(args.save_manifests, "mac.manifest"), "w",
                  encoding="utf-8") as fh:
            fh.write(build_manifest(args.root, side="mac"))
        with open(os.path.join(args.save_manifests, "build.manifest"), "w",
                  encoding="utf-8") as fh:
            fh.write(raw)
    report = compare_manifests(mac, build)
    print(format_report(report))
    return EXIT_CLEAN if report["clean"] else EXIT_DRIFT


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="verify_build_tree.py",
        description="Refuse to build when the Windows build repo is not this tree.")
    sub = parser.add_subparsers(dest="cmd")

    p_check = sub.add_parser("check", help="hash this tree, hash the PC's, compare")
    p_check.add_argument("--root", default=REPO_ROOT)
    p_check.add_argument("--host", default=DEFAULT_HOST)
    p_check.add_argument("--user", default=DEFAULT_USER)
    p_check.add_argument("--key", default=DEFAULT_KEY)
    p_check.add_argument("--build-root", default=DEFAULT_BUILD_ROOT)
    p_check.add_argument("--runner",
                         default=os.path.join(REPO_ROOT, "tools", "hash_build_tree.ps1"))
    p_check.add_argument("--pc-manifest", help="use this file instead of ssh")
    p_check.add_argument("--save-manifests", metavar="DIR")
    p_check.set_defaults(func=_cmd_check)

    p_man = sub.add_parser("manifest", help="print this tree's build-input manifest")
    p_man.add_argument("--root", default=REPO_ROOT)
    p_man.add_argument("--side", default="mac")
    p_man.add_argument("--out")
    p_man.set_defaults(func=_cmd_manifest)

    p_cmp = sub.add_parser("compare", help="compare two manifests")
    p_cmp.add_argument("mac_manifest")
    p_cmp.add_argument("build_manifest")
    p_cmp.set_defaults(func=_cmd_compare)

    argv = list(argv) if argv is not None else sys.argv[1:]
    if not argv:
        argv = ["check"]          # bare invocation is the gate, not a help screen
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return EXIT_CANNOT_DECIDE
    try:
        return args.func(args)
    except GateError as exc:
        print("BUILD REFUSED -- the gate could not decide:", file=sys.stderr)
        print(f"  {exc}", file=sys.stderr)
        return EXIT_CANNOT_DECIDE
    except OSError as exc:
        print(f"BUILD REFUSED -- {exc}", file=sys.stderr)
        return EXIT_CANNOT_DECIDE


if __name__ == "__main__":
    sys.exit(main())
