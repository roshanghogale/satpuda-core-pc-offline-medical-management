"""
generate_github_release_guide_pdf.py
Full workflow: push code to GitHub → build EXEs → push updates → publish release.

Usage: python generate_github_release_guide_pdf.py
Output: GitHub_Release_Publish_Guide.pdf (project root)
"""

import os
import sys


def _ensure_reportlab():
    try:
        from reportlab.lib.pagesizes import A4  # noqa: F401
        return
    except ImportError:
        import subprocess
        subprocess.check_call([sys.executable, "-m", "pip", "install", "reportlab"])


def main():
    _ensure_reportlab()
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.lib import colors
    from reportlab.platypus import (
        SimpleDocTemplate,
        Paragraph,
        Spacer,
        Table,
        TableStyle,
        HRFlowable,
        PageBreak,
        ListFlowable,
        ListItem,
    )
    from reportlab.lib.enums import TA_CENTER

    dst = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "GitHub_Release_Publish_Guide.pdf",
    )

    doc = SimpleDocTemplate(
        dst,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "Title", parent=styles["Title"], fontSize=22,
        textColor=colors.HexColor("#1a73e8"), spaceAfter=4 * mm, alignment=TA_CENTER,
    )
    subtitle_style = ParagraphStyle(
        "Subtitle", parent=styles["Normal"], fontSize=11,
        textColor=colors.HexColor("#555555"), spaceAfter=8 * mm, alignment=TA_CENTER,
    )
    h1_style = ParagraphStyle(
        "H1", parent=styles["Heading1"], fontSize=14,
        textColor=colors.HexColor("#1a73e8"), spaceBefore=6 * mm, spaceAfter=3 * mm,
    )
    h2_style = ParagraphStyle(
        "H2", parent=styles["Heading2"], fontSize=12,
        textColor=colors.HexColor("#333333"), spaceBefore=4 * mm, spaceAfter=2 * mm,
    )
    body_style = ParagraphStyle(
        "Body", parent=styles["Normal"], fontSize=10, leading=16, spaceAfter=2 * mm,
    )
    code_style = ParagraphStyle(
        "Code", parent=styles["Normal"], fontSize=8.5, fontName="Courier",
        backColor=colors.HexColor("#f4f4f4"), borderColor=colors.HexColor("#cccccc"),
        borderWidth=0.5, borderPad=4, spaceAfter=3 * mm, leading=13,
    )
    note_style = ParagraphStyle(
        "Note", parent=styles["Normal"], fontSize=9, textColor=colors.HexColor("#856404"),
        backColor=colors.HexColor("#fff3cd"), borderColor=colors.HexColor("#ffc107"),
        borderWidth=0.5, borderPad=4, spaceAfter=3 * mm, leading=14,
    )
    warn_style = ParagraphStyle(
        "Warn", parent=styles["Normal"], fontSize=9, textColor=colors.HexColor("#721c24"),
        backColor=colors.HexColor("#f8d7da"), borderColor=colors.HexColor("#f5c6cb"),
        borderWidth=0.5, borderPad=4, spaceAfter=3 * mm, leading=14,
    )
    ok_style = ParagraphStyle(
        "OK", parent=styles["Normal"], fontSize=9, textColor=colors.HexColor("#155724"),
        backColor=colors.HexColor("#d4edda"), borderColor=colors.HexColor("#c3e6cb"),
        borderWidth=0.5, borderPad=4, spaceAfter=3 * mm, leading=14,
    )

    def h1(t): return Paragraph(t, h1_style)
    def h2(t): return Paragraph(t, h2_style)
    def p(t): return Paragraph(t, body_style)
    def code(t): return Paragraph(t.replace("\n", "<br/>"), code_style)
    def note(t): return Paragraph("<b>NOTE:</b> " + t, note_style)
    def warn(t): return Paragraph("<b>WARNING:</b> " + t, warn_style)
    def ok(t): return Paragraph("<b>OK:</b> " + t, ok_style)
    def sp(h=4): return Spacer(1, h * mm)
    def hr(): return HRFlowable(width="100%", thickness=0.5,
                                color=colors.HexColor("#dddddd"), spaceAfter=3 * mm)

    def tbl(data, col_widths=None):
        t = Table(data, colWidths=col_widths)
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1a73e8")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1),
             [colors.HexColor("#f7f7f7"), colors.white]),
            ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#cccccc")),
            ("PADDING", (0, 0), (-1, -1), 5),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ]))
        return t

    def steps(items):
        return ListFlowable(
            [ListItem(Paragraph(i, body_style), leftIndent=12) for i in items],
            bulletType="1", start="1",
        )

    repo = "roshanghogale/satpuda-core-pc-offline-medical-management"
    repo_https = f"https://github.com/{repo}.git"
    releases_url = f"https://github.com/{repo}/releases"
    project_folder = r"C:\Users\rosha\Downloads\mac2 (2)\mac2\mac2"

    story = []

    # ── Cover ──────────────────────────────────────────────────────────────
    story += [
        sp(8),
        Paragraph("Satpuda Core", title_style),
        Paragraph(
            "Complete GitHub + Release + Auto-Update Guide<br/>"
            "(Push code → Build EXEs → Publish release → Test update)",
            subtitle_style,
        ),
        Paragraph("<b>CONFIDENTIAL — For Developer Use Only</b>", warn_style),
        hr(), sp(2),
        p("Follow the sections <b>in order</b>. Each new version repeats Sections 3–7."),
        sp(2),
        tbl([
            ["Step", "What you do"],
            ["1", "Push project to GitHub (first time only)"],
            ["2", "Bump version in core/app_version.py"],
            ["3", "Build SatpudaCore.exe + SatpudaCore_Win7.exe"],
            ["4", "Push latest code to GitHub (git push)"],
            ["5", "Create GitHub Release — attach BOTH EXEs"],
            ["6", "Test Settings → Updates in the app"],
        ], col_widths=[18 * mm, 147 * mm]),
        sp(3),
        ok("EXEs are uploaded to GitHub <b>Releases</b>, not stored in git. "
           "dist\\*.exe is in .gitignore."),
        PageBreak(),
    ]

    # ── Section 1: Prerequisites ───────────────────────────────────────────
    story += [
        h1("1. Prerequisites (One-Time Setup)"),
        steps([
            "Install <b>Git for Windows</b>: https://git-scm.com/download/win",
            "Install <b>Python 3.13</b> (for Win10/11 EXE) and <b>Python 3.8</b> "
            "(for Win7 EXE — optional but recommended).",
            "Have a <b>GitHub account</b> logged in on your browser.",
            "Open <b>Command Prompt</b> or <b>PowerShell</b> as your user (not admin required).",
        ]),
        sp(2),
        h2("Verify Git works"),
        code("git --version"),
        p("You should see something like: git version 2.x.x"),
        sp(2),
        warn("Never commit secrets: oauth_client.json, service_account.json, "
             "backup_creds.dat, activation.dat, veterinary.db. "
             "They should stay local or in .gitignore."),
        PageBreak(),
    ]

    # ── Section 2: First-time push to GitHub ───────────────────────────────
    story += [
        h1("2. Push Project to GitHub (First Time Only)"),
        p("If the repo already exists on GitHub and your PC is linked, skip to Section 3."),
        sp(2),
        h2("2A — Create empty repo on GitHub (browser)"),
        steps([
            "Open https://github.com/new",
            "Repository name: <b>satpuda-core-pc-offline-medical-management</b>",
            "Visibility: <b>Public</b> (simplest for store auto-update; no token needed).",
            "Do <b>NOT</b> add README, .gitignore, or license (you already have code).",
            "Click <b>Create repository</b>.",
        ]),
        sp(2),
        h2("2B — Open project folder in terminal"),
        code(f'cd /d "{project_folder}"'),
        sp(2),
        h2("2C — Initialise git and push (copy-paste block)"),
        p("Replace the commit message if you like. Run line by line or as a block:"),
        code(
            "git init\n"
            "git branch -M main\n"
            f'git remote add origin {repo_https}\n'
            "git add .\n"
            'git status\n'
            "git commit -m \"Initial commit: Satpuda Core medical management app\"\n"
            "git push -u origin main"
        ),
        sp(2),
        note("If <b>git remote add origin</b> says remote already exists, use:<br/>"
             "<font face='Courier'>git remote set-url origin " + repo_https + "</font>"),
        sp(2),
        h2("2D — If GitHub asks for login"),
        p("Use <b>GitHub Personal Access Token</b> as the password (not your GitHub password)."),
        steps([
            "GitHub → Settings → Developer settings → Personal access tokens → Generate new token (classic).",
            "Scopes: check <b>repo</b>.",
            "Copy token; paste when git asks for password.",
        ]),
        sp(2),
        h2("2E — Repo already exists (your case)"),
        p("Your remote is already set. To push current work for the first time with this guide:"),
        code(
            f'cd /d "{project_folder}"\n'
            "git status\n"
            "git add .\n"
            'git commit -m "Add GitHub auto-update and release guide"\n'
            "git push origin main"
        ),
        sp(2),
        ok("After push, open https://github.com/" + repo + " — your code should appear."),
        PageBreak(),
    ]

    # ── Section 3: Bump version ────────────────────────────────────────────
    story += [
        h1("3. Bump Version (Every New Release)"),
        p("Example: releasing v1.0.1"),
        steps([
            "Open <b>core/app_version.py</b>",
            "Change: <font face='Courier'>APP_VERSION = \"1.0.1\"</font>",
            "Save the file.",
        ]),
        sp(2),
        note("GitHub release tag must be <b>v1.0.1</b> (letter v + same number as APP_VERSION)."),
        sp(2),
        warn("Build the EXE <b>after</b> bumping APP_VERSION so the EXE reports the correct version."),
        PageBreak(),
    ]

    # ── Section 4: Build both EXEs ─────────────────────────────────────────
    story += [
        h1("4. Build Both EXE Versions"),
        p("From the project folder:"),
        code(f'cd /d "{project_folder}"\nbuild_exe.bat'),
        p("Wait 5–10 minutes. At the end you should see:"),
        sp(1),
        tbl([
            ["Output file", "Path", "Target PCs"],
            ["SatpudaCore.exe", r"dist\SatpudaCore.exe", "Windows 8 / 10 / 11"],
            ["SatpudaCore_Win7.exe", r"dist\SatpudaCore_Win7.exe", "Windows 7 / 8 / 8.1"],
        ], col_widths=[45 * mm, 55 * mm, 65 * mm]),
        sp(2),
        h2("Verify files exist (PowerShell)"),
        code(
            f'cd /d "{project_folder}"\n'
            "dir dist\\SatpudaCore.exe\n"
            "dir dist\\SatpudaCore_Win7.exe"
        ),
        sp(2),
        warn("If SatpudaCore_Win7.exe is missing, install Python 3.8 and run build_exe.bat again. "
             "You can still publish Win10/11-only, but Win7 stores cannot auto-update."),
        sp(2),
        h2("Quick local test (recommended)"),
        code(f'cd /d "{project_folder}"\npython main.py'),
        p("Or run dist\\SatpudaCore.exe from a test folder with veterinary.db."),
        PageBreak(),
    ]

    # ── Section 5: Push code to GitHub ─────────────────────────────────────
    story += [
        h1("5. Push Code to GitHub (Every Release)"),
        p("Push source code <b>before</b> creating the GitHub Release. EXE files stay local — "
           "you upload them manually in Section 6."),
        sp(2),
        code(
            f'cd /d "{project_folder}"\n'
            "git status\n"
            "git add core/app_version.py core/github_updater.py\n"
            "git add ui/settings/settings_tabs/updates_tab.py ui/settings/settings.py main.py\n"
            "git add generate_github_release_guide_pdf.py VeterinaryApp.spec VeterinaryApp_Win7.spec\n"
            "git add .\n"
            'git commit -m "Release v1.0.1: auto-update and purchase import fixes"\n'
            "git push origin main"
        ),
        sp(2),
        note("Change the commit message and version in the message to match your release. "
             "<b>git add .</b> stages all changed files; review with <b>git status</b> first."),
        sp(2),
        warn("Do not force-push (git push --force) unless you know what you are doing."),
        sp(2),
        ok("Code on GitHub and EXE in dist\\ are separate: stores download EXE from Releases, not from git."),
        PageBreak(),
    ]

    # ── Section 6: Create GitHub Release ───────────────────────────────────
    story += [
        h1("6. Create GitHub Release (Attach EXEs)"),
        p("Browser steps — this is what makes auto-update work."),
        code(releases_url),
        steps([
            "Click <b>Draft a new release</b>.",
            "Tag: type <b>v1.0.1</b> → <b>Create new tag: v1.0.1 on publish</b>.",
            "Release title: <b>Satpuda Core v1.0.1</b>.",
            "Write release notes (shown in app when user checks for updates).",
            "Under <b>Attach binaries</b>, add BOTH files from dist\\:",
        ]),
        sp(1),
        code(
            r"dist\SatpudaCore.exe" + "\n" +
            r"dist\SatpudaCore_Win7.exe"
        ),
        sp(1),
        steps([
            "Uncheck <b>Set as a pre-release</b> (unless testing).",
            "Click <b>Publish release</b>.",
        ]),
        sp(2),
        tbl([
            ["Asset name (exact)", "Downloaded by"],
            ["SatpudaCore.exe", "PCs running SatpudaCore.exe (Win 10/11)"],
            ["SatpudaCore_Win7.exe", "PCs running SatpudaCore_Win7.exe (Win 7)"],
        ], col_widths=[65 * mm, 100 * mm]),
        sp(2),
        warn("File names must match exactly. Do not rename to SatpudaCore_v1.0.1.exe."),
        PageBreak(),
    ]

    # ── Section 7: Test auto-update ────────────────────────────────────────
    story += [
        h1("7. Test the Update Feature in the App"),
        h2("7A — Test on an OLD EXE (simulates a store PC)"),
        p("You need an EXE built with an <b>older</b> APP_VERSION (e.g. 1.0.0) while GitHub has v1.0.1."),
        steps([
            "Option A: Keep a copy of the previous dist\\SatpudaCore.exe before rebuilding.",
            "Option B: Temporarily set APP_VERSION = \"1.0.0\", build, test, then restore 1.0.1.",
            "Run the old EXE (not python main.py — auto-install only works from built EXE).",
            "Go to <b>Settings → Updates</b>.",
            "Click <b>Check for Updates</b> — should show v1.0.1 available.",
            "Click <b>Download &amp; Install</b> — confirm, app restarts with new EXE.",
            "Verify version line shows v1.0.1 and (Windows 10 / 11 — SatpudaCore.exe).",
        ]),
        sp(2),
        h2("7B — Test from python main.py (dev mode)"),
        p("Check for Updates works, but Install opens the browser instead of replacing files."),
        code(f'cd /d "{project_folder}"\npython main.py'),
        sp(2),
        h2("7C — What success looks like"),
        ok("Check for Updates finds the release; CGST/SGST and data unchanged after install; "
           "activation not requested again on same PC."),
        sp(2),
        h2("7D — First release ever"),
        p("If GitHub has no releases yet, the app shows: <i>No GitHub releases published yet.</i> "
           "Complete Section 6 first, then test again."),
        PageBreak(),
    ]

    # ── Section 8: Full command cheat sheet ────────────────────────────────
    story += [
        h1("8. Full Command Cheat Sheet (Copy-Paste)"),
        h2("First-time: push project to GitHub"),
        code(
            f'cd /d "{project_folder}"\n'
            "git init\n"
            "git branch -M main\n"
            f"git remote add origin {repo_https}\n"
            "git add .\n"
            'git commit -m "Initial commit: Satpuda Core"\n'
            "git push -u origin main"
        ),
        h2("Every new version (e.g. v1.0.1)"),
        code(
            "# 1) Edit core/app_version.py → APP_VERSION = \"1.0.1\"\n"
            f'cd /d "{project_folder}"\n'
            "build_exe.bat\n"
            "git add .\n"
            'git commit -m "Release v1.0.1"\n'
            "git push origin main\n"
            "# 2) Browser: create release v1.0.1, attach dist\\SatpudaCore.exe + dist\\SatpudaCore_Win7.exe\n"
            "# 3) Test: old EXE → Settings → Updates → Download & Install"
        ),
        sp(4),
        h1("9. Publishing Checklist"),
        tbl([
            ["Step", "Done?"],
            ["Git installed and repo on GitHub", "☐"],
            ["APP_VERSION bumped in core/app_version.py", "☐"],
            ["build_exe.bat completed", "☐"],
            ["dist\\SatpudaCore.exe exists", "☐"],
            ["dist\\SatpudaCore_Win7.exe exists", "☐"],
            ["git commit + git push origin main", "☐"],
            ["GitHub Release tag vX.Y.Z created", "☐"],
            ["Both EXEs attached to release", "☐"],
            ["Tested Check for Updates on Win10/11 EXE", "☐"],
            ["Tested on Win7 EXE (if used)", "☐"],
        ], col_widths=[140 * mm, 25 * mm]),
        sp(4),
        h1("10. Troubleshooting"),
        tbl([
            ["Problem", "Fix"],
            ["git push rejected", "Run git pull origin main first, resolve conflicts, push again."],
            ["Authentication failed", "Use GitHub Personal Access Token as password."],
            ["No releases published yet", "Complete Section 6 — Publish release with EXEs."],
            ["Update found, no download", "Attach the matching EXE name to the release."],
            ["Win7 gets wrong EXE", "Asset must be named SatpudaCore_Win7.exe exactly."],
            ["Store cannot reach GitHub", "Allow api.github.com and github.com on network."],
        ], col_widths=[55 * mm, 110 * mm]),
        sp(4),
        hr(),
        p("<i>Regenerate: python generate_github_release_guide_pdf.py</i>"),
    ]

    doc.build(story)
    print(f"PDF written: {dst}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
