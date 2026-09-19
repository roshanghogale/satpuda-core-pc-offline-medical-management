"""A build may not start from a tree that is not this one.

The 2026-09-14 desktop release shipped widgets/activation_dialog.py dated
2026-08-07 -- a month older than the Mac's -- because the Windows build repo
D:\\satpuda-build\\mac2 is an incremental copy that nothing ever verified. The
rest of widgets/ was refreshed at 02:36 that morning, so the sync ran and quietly
skipped that one file. The shipped dialog is the pre-server Firebase version: it
calls mark_pending_bootstrap() and never calls ensure_online_store_link(), so
activation never creates the store on the server. Every shop that tried to sign up
landed on "licence not found" with no way out. widgets/searchable_combo.py went
the same way.

Nothing in the build could have caught it. PyInstaller freezes whatever module it
finds; this test suite runs against the Mac tree, not the tree the build reads;
the zip's .sha256 only proves the zip matches itself.

So these tests pin the gate that now has to run first, and above all they pin the
part that is easy to get wrong: it must FAIL LOUDLY, and it must fail even when it
cannot see the other tree. A gate that answers "fine" when ssh is down, when the
manifest arrives cut in half, or when the two sides disagree about which files
even count, is worse than no gate -- it is the same silence that shipped August's
dialog, with a green tick on top.
"""
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

import verify_build_tree as gate  # noqa: E402

TOOL = os.path.join(ROOT, "tools", "verify_build_tree.py")
RUNNER = os.path.join(ROOT, "tools", "hash_build_tree.ps1")

# 2026-08-07 10:14 UTC and 2026-08-13 03:06 UTC: the real pair of mtimes.
AUGUST_7 = 1786097640
AUGUST_13 = 1786604760
SEPTEMBER_13 = 1789317960          # the last time the build repo was written to
SEPTEMBER_16 = 1789576200          # after the last sync: ordinary unsynced work


def _write(root, rel, body, mtime=None):
    path = os.path.join(root, *rel.split("/"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(body)
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


def _tree(root, files):
    """files: {relpath: (body, mtime)}"""
    for rel, (body, mtime) in files.items():
        _write(root, rel, body, mtime)
    return root


def _manifests(case, mac_files, build_files):
    """Hash two throwaway trees and return the parsed manifests."""
    tmp = tempfile.TemporaryDirectory()
    case.addCleanup(tmp.cleanup)
    mac = _tree(os.path.join(tmp.name, "mac"), mac_files)
    build = _tree(os.path.join(tmp.name, "build"), build_files)
    return (
        gate.parse_manifest(gate.build_manifest(mac, side="mac"), "mac"),
        gate.parse_manifest(gate.build_manifest(build, side="build-repo"), "build"),
    )


ONE_GOOD_FILE = {
    "main.py": ("print('hi')\n", SEPTEMBER_13),
    "widgets/activation_dialog.py": ("ensure_online_store_link()\n", AUGUST_13),
    "core/billing_service.py": ("def bill(): pass\n", SEPTEMBER_13),
}


class AnIdenticalTreeIsAllowedToBuild(unittest.TestCase):
    def test_no_differences_means_clean_and_exit_zero(self):
        mac, build = _manifests(self, ONE_GOOD_FILE, ONE_GOOD_FILE)
        report = gate.compare_manifests(mac, build)
        self.assertTrue(report["clean"], gate.format_report(report))
        self.assertIn("CLEAN", gate.format_report(report))
        self.assertEqual(3, report["checked"])


class TheAugustActivationDialogIsCaught(unittest.TestCase):
    """The exact shape of the 2026-09-14 release, reproduced."""

    def setUp(self):
        build_side = dict(ONE_GOOD_FILE)
        build_side["widgets/activation_dialog.py"] = (
            "mark_pending_bootstrap()\n", AUGUST_7)
        self.mac, self.build = _manifests(self, ONE_GOOD_FILE, build_side)
        self.report = gate.compare_manifests(self.mac, self.build)

    def test_it_refuses_the_build(self):
        self.assertFalse(self.report["clean"])

    def test_the_stale_file_is_named_and_called_silently_stale(self):
        stale = [row[0] for row in self.report["silently_stale"]]
        self.assertEqual(["widgets/activation_dialog.py"], stale)
        self.assertEqual([], self.report["not_yet_copied"])

    def test_the_report_says_which_side_is_older_and_by_how_much(self):
        text = gate.format_report(self.report)
        self.assertIn("SILENTLY STALE", text)
        self.assertIn("widgets/activation_dialog.py", text)
        self.assertIn("2026-08-07", text)
        self.assertIn("2026-08-13", text)
        self.assertIn("DO NOT BUILD", text)

    def test_ordinary_unsynced_work_is_not_confused_with_it(self):
        """A file edited AFTER the last sync is a different, milder problem.

        If both read the same, the 20-odd files anyone has in flight bury the one
        file that matters and the operator learns to skim past the section.
        """
        mac_side = dict(ONE_GOOD_FILE)
        mac_side["core/billing_service.py"] = ("def bill(): return 1\n", SEPTEMBER_16)
        build_side = dict(ONE_GOOD_FILE)
        build_side["widgets/activation_dialog.py"] = (
            "mark_pending_bootstrap()\n", AUGUST_7)
        mac, build = _manifests(self, mac_side, build_side)
        report = gate.compare_manifests(mac, build)
        self.assertEqual(["widgets/activation_dialog.py"],
                         [r[0] for r in report["silently_stale"]])
        self.assertEqual(["core/billing_service.py"],
                         [r[0] for r in report["not_yet_copied"]])
        self.assertFalse(report["clean"])


class EveryOtherWayTheTreesCanDisagreeAlsoRefuses(unittest.TestCase):
    def test_a_file_missing_from_the_build_repo(self):
        build_side = {k: v for k, v in ONE_GOOD_FILE.items() if k != "core/billing_service.py"}
        mac, build = _manifests(self, ONE_GOOD_FILE, build_side)
        report = gate.compare_manifests(mac, build)
        self.assertEqual(["core/billing_service.py"],
                         [row[0] for row in report["missing_in_build"]])
        self.assertFalse(report["clean"])
        self.assertIn("MISSING IN BUILD REPO", gate.format_report(report))

    def test_a_file_deleted_here_but_still_on_the_pc_still_ships(self):
        build_side = dict(ONE_GOOD_FILE)
        build_side["core/old_firebase_sync.py"] = ("import firebase\n", AUGUST_7)
        mac, build = _manifests(self, ONE_GOOD_FILE, build_side)
        report = gate.compare_manifests(mac, build)
        self.assertEqual(["core/old_firebase_sync.py"],
                         [row[0] for row in report["extra_in_build"]])
        self.assertFalse(report["clean"])

    def test_the_pc_copy_being_newer_is_not_quietly_accepted(self):
        """Someone edited the build repo directly. Still not this tree."""
        build_side = dict(ONE_GOOD_FILE)
        build_side["core/billing_service.py"] = ("def bill(): return 2\n", SEPTEMBER_16)
        mac, build = _manifests(self, ONE_GOOD_FILE, build_side)
        report = gate.compare_manifests(mac, build)
        self.assertEqual(["core/billing_service.py"],
                         [row[0] for row in report["content_differs"]])
        self.assertFalse(report["clean"])

    def test_same_bytes_but_a_different_mtime_is_not_drift(self):
        """Copying changes timestamps. Only content decides pass or fail."""
        build_side = {rel: (body, SEPTEMBER_16) for rel, (body, _) in ONE_GOOD_FILE.items()}
        mac, build = _manifests(self, ONE_GOOD_FILE, build_side)
        self.assertTrue(gate.compare_manifests(mac, build)["clean"])


class ThingsThatAreNotBuildInputsAreLeftAlone(unittest.TestCase):
    def test_caches_editor_droppings_and_installer_output_are_ignored(self):
        noisy = dict(ONE_GOOD_FILE)
        noisy.update({
            "core/__pycache__/billing_service.cpython-313.pyc": ("x", AUGUST_7),
            "core/._billing_service.py": ("AppleDouble", AUGUST_7),
            "core/tutor_rules.py.tmp": ("", AUGUST_7),
            "core/.DS_Store": ("x", AUGUST_7),
            "installer/Output/SatpudaCoreInstaller.exe": ("MZ", AUGUST_7),
        })
        mac, build = _manifests(self, noisy, ONE_GOOD_FILE)
        report = gate.compare_manifests(mac, build)
        self.assertTrue(report["clean"], gate.format_report(report))
        self.assertEqual(3, report["checked"])

    def test_the_real_tree_selects_the_two_files_that_shipped_stale(self):
        """The gate is worthless if it does not look where the drift was."""
        rels = {rel for rel, _ in gate.iter_build_inputs(ROOT)}
        for must in ("widgets/activation_dialog.py", "widgets/searchable_combo.py",
                     "main.py", "core/server_api.py",
                     "desktop/src/pages/ActivationDialog.tsx",
                     "SatpudaEngine_Folder.spec"):
            self.assertIn(must, rels)
        self.assertTrue(any(r.startswith("installer/") for r in rels))
        self.assertFalse(any(r.startswith("tests/") for r in rels))
        self.assertFalse(any("__pycache__" in r for r in rels))


class AGateThatCannotSeeTheOtherTreeMustNotPass(unittest.TestCase):
    def test_a_truncated_manifest_is_an_error_not_a_pass(self):
        text = gate.build_manifest(ROOT)
        cut = "\n".join(text.splitlines()[:20])       # ssh died halfway
        with self.assertRaises(gate.GateError) as caught:
            gate.parse_manifest(cut, "build repo")
        self.assertIn("truncated", str(caught.exception))

    def test_a_manifest_whose_count_does_not_match_is_refused(self):
        lines = gate.build_manifest(ROOT).splitlines()
        lines = [ln for ln in lines if not ln.startswith(("#", "b"))][:5] + [
            gate.MANIFEST_BEGIN] + lines[-1:]
        with self.assertRaises(gate.GateError):
            gate.parse_manifest("\n".join(lines), "build repo")

    def test_output_with_no_manifest_at_all_is_refused(self):
        with self.assertRaises(gate.GateError) as caught:
            gate.parse_manifest("'powershell' is not recognized\n", "build repo")
        self.assertIn("no manifest", str(caught.exception))

    def test_powershell_wrapper_noise_around_a_whole_manifest_is_tolerated(self):
        """A real run once arrived wrapped in CLIXML progress records."""
        text = gate.build_manifest(ROOT)
        noisy = "#< CLIXML\n" + text + '<Objs Version="1.1.0.1"><Obj S="progress" /></Objs>\n'
        parsed = gate.parse_manifest(noisy, "build repo")
        self.assertIn("main.py", parsed["files"])

    def test_two_sides_that_selected_different_files_are_not_compared(self):
        mac, build = _manifests(self, ONE_GOOD_FILE, ONE_GOOD_FILE)
        build["selector"] = "0" * 64
        with self.assertRaises(gate.GateError) as caught:
            gate.compare_manifests(mac, build)
        self.assertIn("hash_build_tree.ps1", str(caught.exception))

    def test_an_unreachable_build_pc_exits_non_zero(self):
        """ssh to a host that cannot answer: refuse, never 'clean'."""
        with tempfile.TemporaryDirectory() as tmp:
            _tree(tmp, ONE_GOOD_FILE)
            proc = subprocess.run(
                [sys.executable, TOOL, "check", "--root", tmp,
                 "--host", "127.0.0.1", "--user", "nobody",
                 "--key", os.path.join(tmp, "no-such-key"),
                 "--build-root", "D:\\nowhere"],
                capture_output=True, text=True, timeout=120,
                env=dict(os.environ, SSH_AUTH_SOCK=""),
            )
        self.assertEqual(gate.EXIT_CANNOT_DECIDE, proc.returncode, proc.stdout + proc.stderr)
        self.assertIn("BUILD REFUSED", proc.stderr)
        self.assertNotIn("CLEAN", proc.stdout)


class TheCommandTheBuildRunsBehavesLikeAGate(unittest.TestCase):
    def test_compare_exits_two_on_drift_and_zero_when_identical(self):
        with tempfile.TemporaryDirectory() as tmp:
            mac_dir = _tree(os.path.join(tmp, "mac"), ONE_GOOD_FILE)
            build_side = dict(ONE_GOOD_FILE)
            build_side["widgets/activation_dialog.py"] = (
                "mark_pending_bootstrap()\n", AUGUST_7)
            build_dir = _tree(os.path.join(tmp, "build"), build_side)
            mac_man = os.path.join(tmp, "mac.manifest")
            build_man = os.path.join(tmp, "build.manifest")
            for root, out, side in ((mac_dir, mac_man, "mac"),
                                    (build_dir, build_man, "build-repo")):
                done = subprocess.run(
                    [sys.executable, TOOL, "manifest", "--root", root,
                     "--side", side, "--out", out],
                    capture_output=True, text=True, timeout=120)
                self.assertEqual(0, done.returncode, done.stderr)

            drifted = subprocess.run([sys.executable, TOOL, "compare", mac_man, build_man],
                                     capture_output=True, text=True, timeout=120)
            self.assertEqual(gate.EXIT_DRIFT, drifted.returncode)
            self.assertIn("widgets/activation_dialog.py", drifted.stdout)

            same = subprocess.run([sys.executable, TOOL, "compare", mac_man, mac_man],
                                  capture_output=True, text=True, timeout=120)
            self.assertEqual(gate.EXIT_CLEAN, same.returncode)
            self.assertIn("CLEAN", same.stdout)

    def test_a_refusal_never_exits_zero(self):
        self.assertNotEqual(0, gate.EXIT_DRIFT)
        self.assertNotEqual(0, gate.EXIT_CANNOT_DECIDE)


class TheTwoHalvesOfTheGateAgreeOnWhatCounts(unittest.TestCase):
    """The PC runner and this module must select the same files.

    They are written in different languages and can only be compared by their
    manifests, which carry a hash of the selector. If someone edits one list and
    not the other, every future run dies with "different selector rules" instead
    of comparing -- correct, but useless. Catch it here instead.
    """

    def setUp(self):
        with open(RUNNER, encoding="utf-8") as fh:
            self.ps1 = fh.read()

    def _ps_array(self, name):
        line = next(ln for ln in self.ps1.splitlines()
                    if ln.strip().startswith("$" + name))
        inside = line.split("@(", 1)[1].rsplit(")", 1)[0]
        return tuple(part.strip().strip("'") for part in inside.split(",") if part.strip())

    def test_every_list_matches(self):
        self.assertEqual(gate.DIRS, self._ps_array("Dirs"))
        self.assertEqual(gate.FILES, self._ps_array("Files"))
        self.assertEqual(gate.GLOBS, self._ps_array("Globs"))
        self.assertEqual(gate.SKIP_DIRS, self._ps_array("SkipDirs"))
        self.assertEqual(gate.SKIP_EXTS, self._ps_array("SkipExts"))
        self.assertEqual(gate.SKIP_NAMES, self._ps_array("SkipNames"))
        self.assertEqual(gate.SKIP_PREFIXES, self._ps_array("SkipPrefix"))
        self.assertEqual(gate.SKIP_PATHS, self._ps_array("SkipPaths"))

    def test_the_selector_text_is_built_the_same_way_on_both_sides(self):
        keys = [ln.split("=")[0] for ln in gate.selector_text().splitlines()]
        for key in keys:
            self.assertIn("'" + key + "=", self.ps1,
                          f"the runner does not put {key} in its selector text")
        self.assertEqual(len(keys), self.ps1.count("=' + ($") + self.ps1.count(
            "('selector-version=' + $SelectorVersion)"))

    def test_the_runner_only_reads(self):
        """It runs on the build PC. It must not write or start anything."""
        for forbidden in ("Remove-Item", "New-Item", "Start-Process", "Copy-Item",
                          "Move-Item", "Invoke-WebRequest", "robocopy", "xcopy",
                          "Out-File", "rmdir", "del "):
            self.assertNotIn(forbidden, self.ps1,
                             f"{forbidden} has no business in a read-only hash runner")
        # The single write is Set-Content, and only when the operator passes -Out.
        self.assertEqual(1, self.ps1.count("Set-Content"))
        self.assertIn("if ($Out) {", self.ps1)

    def test_the_runner_is_ascii_because_it_travels_through_a_windows_console(self):
        with open(RUNNER, "rb") as fh:
            fh.read().decode("ascii")


class TheRunnerActuallyReachesTheBuildPc(unittest.TestCase):
    """Two ways of getting the runner there failed. Both are pinned here.

    1. -EncodedCommand of the whole runner: "The command line is too long."
       Windows sshd runs the remote command through cmd.exe, which stops at 8191
       characters.
    2. Piping the runner in and reading it with [Console]::In.ReadToEnd(): the
       bigger the script grew, the more often the remote PowerShell never saw the
       text at all. It then blocked on the read forever and every timed-out
       attempt left another idle powershell process on the build PC.

    So the runner is gzipped into the command itself and no stdin is used. If
    someone adds enough to the runner to break that again, this test says so here
    instead of the gate hanging against the PC on release night.
    """

    def setUp(self):
        with open(RUNNER, encoding="utf-8") as fh:
            self.script = fh.read()

    def test_the_packed_command_fits_on_a_windows_command_line(self):
        encoded = gate.remote_command(self.script, r"D:\satpuda-build\mac2")
        self.assertLess(len(encoded), gate.WINDOWS_COMMAND_LIMIT)

    def test_an_oversized_runner_is_refused_with_a_way_out(self):
        bulk = "\n".join("$v%d = '%s'" % (i, os.urandom(16).hex()) for i in range(600))
        with self.assertRaises(gate.GateError) as caught:
            gate.remote_command(self.script + "\n" + bulk, "D:\\x")
        self.assertIn("--pc-manifest", str(caught.exception))

    def test_what_arrives_is_the_runner_with_the_root_set(self):
        import base64
        import gzip as _gzip
        encoded = gate.remote_command(self.script, r"D:\satpuda-build\mac2")
        command = base64.b64decode(encoded).decode("utf-16-le")
        packed = command.split("'", 2)[1]
        arrived = _gzip.decompress(base64.b64decode(packed)).decode("ascii")
        self.assertIn(r"$Root = 'D:\satpuda-build\mac2'", arrived)
        self.assertIn("Get-FileHash", arrived)
        self.assertIn("satpuda-build-manifest-begin", arrived)
        self.assertNotIn("Console]::In", arrived)

    def test_stripping_comments_leaves_the_code_alone(self):
        stripped = gate.strip_ps_comments(self.script)
        self.assertNotIn("WHY THIS EXISTS", stripped)          # the block comment
        for line in stripped.splitlines():
            self.assertFalse(line.lstrip().startswith("#"), line)
        for code in ("$SkipPaths", "Get-ChildItem", "Sort-Object",
                     "'#satpuda-build-manifest-begin v1'", "$SelectorId"):
            self.assertIn(code, stripped)

    def test_the_pc_side_runner_passes_the_refusal_on_to_the_build(self):
        with open(os.path.join(ROOT, "tools", "verify_build_tree.cmd"),
                  encoding="utf-8") as fh:
            cmd = fh.read()
        self.assertIn("verify_build_tree.py", cmd)
        self.assertIn("exit /b %GATE%", cmd)
        self.assertIn("DO NOT BUILD", cmd)


if __name__ == "__main__":
    unittest.main()
