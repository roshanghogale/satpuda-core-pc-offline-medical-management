"""
generate_backup_guide_pdf.py
Run this on your machine to generate the Google Drive Backup Setup Guide PDF.
Usage: python generate_backup_guide_pdf.py
"""

import os
import sys

def main():
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import mm
        from reportlab.lib import colors
        from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer,
                                        Table, TableStyle, HRFlowable, PageBreak)
        from reportlab.lib.enums import TA_LEFT, TA_CENTER
    except ImportError:
        import subprocess
        subprocess.check_call([sys.executable, '-m', 'pip', 'install', 'reportlab'])
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import mm
        from reportlab.lib import colors
        from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer,
                                        Table, TableStyle, HRFlowable, PageBreak)
        from reportlab.lib.enums import TA_LEFT, TA_CENTER

    dst = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       'Google_Drive_Backup_Guide.pdf')

    doc = SimpleDocTemplate(dst, pagesize=A4,
                            leftMargin=20*mm, rightMargin=20*mm,
                            topMargin=20*mm, bottomMargin=20*mm)

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle('Title', parent=styles['Title'],
                                 fontSize=22, textColor=colors.HexColor('#1a73e8'),
                                 spaceAfter=4*mm, alignment=TA_CENTER)
    subtitle_style = ParagraphStyle('Subtitle', parent=styles['Normal'],
                                    fontSize=11, textColor=colors.HexColor('#555555'),
                                    spaceAfter=8*mm, alignment=TA_CENTER)
    h1_style = ParagraphStyle('H1', parent=styles['Heading1'],
                               fontSize=14, textColor=colors.HexColor('#1a73e8'),
                               spaceBefore=6*mm, spaceAfter=3*mm)
    h2_style = ParagraphStyle('H2', parent=styles['Heading2'],
                               fontSize=12, textColor=colors.HexColor('#333333'),
                               spaceBefore=4*mm, spaceAfter=2*mm)
    body_style = ParagraphStyle('Body', parent=styles['Normal'],
                                fontSize=10, leading=16, spaceAfter=2*mm)
    code_style = ParagraphStyle('Code', parent=styles['Normal'],
                                fontSize=9, fontName='Courier',
                                backColor=colors.HexColor('#f4f4f4'),
                                borderColor=colors.HexColor('#cccccc'),
                                borderWidth=0.5, borderPad=4,
                                spaceAfter=3*mm, leading=14)
    note_style = ParagraphStyle('Note', parent=styles['Normal'],
                                fontSize=9, textColor=colors.HexColor('#856404'),
                                backColor=colors.HexColor('#fff3cd'),
                                borderColor=colors.HexColor('#ffc107'),
                                borderWidth=0.5, borderPad=4,
                                spaceAfter=3*mm, leading=14)
    warn_style = ParagraphStyle('Warn', parent=styles['Normal'],
                                fontSize=9, textColor=colors.HexColor('#721c24'),
                                backColor=colors.HexColor('#f8d7da'),
                                borderColor=colors.HexColor('#f5c6cb'),
                                borderWidth=0.5, borderPad=4,
                                spaceAfter=3*mm, leading=14)
    ok_style = ParagraphStyle('OK', parent=styles['Normal'],
                              fontSize=9, textColor=colors.HexColor('#155724'),
                              backColor=colors.HexColor('#d4edda'),
                              borderColor=colors.HexColor('#c3e6cb'),
                              borderWidth=0.5, borderPad=4,
                              spaceAfter=3*mm, leading=14)

    def h1(text): return Paragraph(text, h1_style)
    def h2(text): return Paragraph(text, h2_style)
    def p(text):  return Paragraph(text, body_style)
    def code(text): return Paragraph(text, code_style)
    def note(text): return Paragraph("<b>NOTE:</b> " + text, note_style)
    def warn(text): return Paragraph("<b>WARNING:</b> " + text, warn_style)
    def ok(text):   return Paragraph("<b>OK:</b> " + text, ok_style)
    def sp(h=4):  return Spacer(1, h*mm)
    def hr():     return HRFlowable(width='100%', thickness=0.5,
                                    color=colors.HexColor('#dddddd'), spaceAfter=3*mm)

    def tbl(data, col_widths=None):
        t = Table(data, colWidths=col_widths)
        t.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#1a73e8')),
            ('TEXTCOLOR',  (0,0), (-1,0), colors.white),
            ('FONTNAME',   (0,0), (-1,0), 'Helvetica-Bold'),
            ('FONTSIZE',   (0,0), (-1,-1), 9),
            ('ROWBACKGROUNDS', (0,1), (-1,-1),
             [colors.HexColor('#f7f7f7'), colors.white]),
            ('GRID', (0,0), (-1,-1), 0.3, colors.HexColor('#cccccc')),
            ('PADDING', (0,0), (-1,-1), 5),
            ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ]))
        return t

    story = []

    # ── Cover ──────────────────────────────────────────────────────────────────
    story += [
        sp(10),
        Paragraph("SatpudaCore", title_style),
        Paragraph("Google Drive Backup -- Complete Setup Guide", subtitle_style),
        Paragraph("<b>CONFIDENTIAL -- For Developer Use Only</b>", warn_style),
        hr(), sp(4),
        p("This guide explains how to set up automatic Google Drive backup for every store "
          "running SatpudaCore. Backups run silently every 1 hour when internet is connected. "
          "The store owner sees nothing."),
        sp(2),
        tbl([
            ['What', 'Detail'],
            ['Backup frequency', 'Every 1 hour while app is open'],
            ['On app open', 'Immediate backup on launch'],
            ['On app close', 'Final backup before exit'],
            ['No internet', 'Pendrive backup still runs; Drive skipped, retried next hour'],
            ['Drive error', 'Warning dialog shown to user'],
            ['Files kept per day', 'First 2 + Last 2 of each day; middle ones deleted'],
            ['Retention period', 'Files older than 3 years deleted automatically'],
            ['Pendrive backup', 'Auto-detected; copies to SatpudaCore_Backup\\ on USB drive'],
            ['Storage cost', 'Free (15 GB Gmail quota)'],
            ['Store owner visibility', 'None -- completely hidden'],
        ], col_widths=[80*mm, 100*mm]),
        PageBreak(),
    ]

    # ── Phase 1 ────────────────────────────────────────────────────────────────
    story += [
        h1("Phase 1 -- One-Time Google Cloud Setup"),
        note("Do this ONCE on your personal machine. Never repeat unless you create a new project."),
        sp(2),

        h2("Step 1 -- Create a Personal Gmail Account"),
        p("Use a <b>personal @gmail.com account</b> -- not a work or school account."),
        p("1. Go to <b>accounts.google.com</b> -> Create account -> For personal use"),
        p("2. Suggested email: <b>satpudabackupadmin@gmail.com</b>"),
        p("3. Save the email and password in your own secure records"),
        warn("Do NOT use a Google Workspace / company / school account. "
             "They block the required features."),
        sp(2),

        h2("Step 2 -- Create Google Cloud Project"),
        p("1. Go to <b>console.cloud.google.com</b> -- sign in with the Gmail from Step 1"),
        p("2. Click <b>Select a project</b> (top left) -> <b>New Project</b>"),
        p("3. Project name: <b>SatpudaCore Backup</b> -> Click <b>Create</b>"),
        p("4. Wait a few seconds -> select the new project from the dropdown at the top"),
        sp(2),

        h2("Step 3 -- Enable Google Drive API"),
        p("1. Left menu -> <b>APIs &amp; Services -> Library</b>"),
        p("2. Search: <b>Google Drive API</b>"),
        p("3. Click it -> Click <b>Enable</b>"),
        sp(2),

        h2("Step 4 -- Configure OAuth Consent Screen"),
        p("1. Left menu -> <b>APIs &amp; Services -> OAuth consent screen</b>"),
        p("2. Select <b>External</b> -> Click <b>Create</b>"),
        p("3. App name: <b>SatpudaCore Backup</b>"),
        p("4. User support email: your Gmail address"),
        p("5. Developer contact email: your Gmail address"),
        p("6. Click <b>Save and Continue</b> through all steps until the last page"),
        p("7. On the last page click <b>Back to Dashboard</b>"),
        p("8. Scroll down to <b>Test users</b> section -> Click <b>+ Add Users</b>"),
        p("9. Enter your Gmail address -> Click <b>Save</b>"),
        note("The app stays in Testing mode forever for personal use. "
             "No Google verification needed. Up to 100 test users supported."),
        sp(2),

        h2("Step 5 -- Create OAuth2 Client ID and Download JSON"),
        p("1. Left menu -> <b>APIs &amp; Services -> Credentials</b>"),
        p("2. Click <b>+ Create Credentials -> OAuth 2.0 Client ID</b>"),
        p("3. Application type: <b>Desktop app</b>"),
        p("4. Name: <b>SatpudaBackup</b> -> Click <b>Create</b>"),
        p("5. A dialog appears -> Click <b>Download JSON</b>"),
        p("6. The file downloads with a long auto-generated name like:"),
        code("client_secret_123456789-abcdefghij.apps.googleusercontent.com.json"),
        p("7. <b>Rename</b> this file to exactly:"),
        code("oauth_client.json"),
        p("8. <b>Move or copy</b> it to the project root folder (same folder as main.py):"),
        code("d:\\satpuda medical store app\\satpuda core\\mac2\\oauth_client.json"),
        warn("The file MUST be named exactly oauth_client.json and placed in the project "
             "root folder. If the name is wrong or the location is wrong, the next step will fail."),
        sp(2),

        h2("Step 6 -- Install Required Python Packages"),
        p("Open Command Prompt and run:"),
        code("pip install google-api-python-client google-auth google-auth-oauthlib"),
        p("Wait for installation to complete. You should see: Successfully installed ..."),
        sp(2),

        h2("Step 7 -- Generate Encrypted Token (Run Once)"),
        p("Open Command Prompt, navigate to the project folder, and run:"),
        code("cd \"d:\\satpuda medical store app\\satpuda core\\mac2\""),
        code("python generate_oauth_token.py"),
        p("What happens step by step:"),
        p("1. A browser window opens automatically"),
        p("2. Sign in with your Gmail account (the same one from Step 1)"),
        p("3. You may see a warning screen: <b>Google hasn't verified this app</b>"),
        p("4. Click <b>Advanced</b> (bottom left of the warning screen)"),
        p("5. Click <b>Go to SatpudaCore Backup (unsafe)</b>"),
        p("6. Click <b>Allow</b> on the permissions screen"),
        p("7. Browser shows: <b>The authentication flow has completed. "
          "You may close this window.</b>"),
        p("8. Back in Command Prompt you see:"),
        code("Done! backup_creds.dat saved to: ...\\config\\backup_creds.dat\n"
             "This file contains your encrypted OAuth2 refresh token.\n"
             "Bundle it with every EXE build -- it never expires."),
        ok("This token never expires as long as you do not revoke access. "
           "You never need to run this script again unless you change your Gmail account. "
           "backup_creds.dat is automatically bundled in every EXE you build."),
        sp(2),

        h2("Step 8 -- Create Drive Backup Folder"),
        p("1. Go to <b>drive.google.com</b> -- same Gmail account"),
        p("2. Click <b>+ New -> Folder</b>"),
        p("3. Name it: <b>SatpudaCore Backups</b> -> Click <b>Create</b>"),
        p("4. Double-click to open the folder"),
        p("5. Look at the URL in your browser address bar:"),
        code("https://drive.google.com/drive/folders/1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74ogVE"),
        p("6. Copy everything after <b>/folders/</b> -- that is your <b>FOLDER_ID</b>"),
        p("7. Save this ID -- you use the SAME folder ID for every store activation"),
        note("This is the SAME folder ID for ALL stores forever. "
             "Subfolders per store are created automatically inside it."),
        PageBreak(),
    ]

    # ── Phase 2 ────────────────────────────────────────────────────────────────
    story += [
        h1("Phase 2 -- Per Store Setup (At Activation Time)"),
        note("Do this ONCE per store when you visit to activate the software. Takes 30 seconds."),
        sp(2),

        h2("Step 9 -- Write Backup Config on Store's Machine"),
        p("When you are at the store's machine, open Command Prompt in the project folder:"),
        code("cd \"d:\\satpuda medical store app\\satpuda core\\mac2\""),
        p("Run this command -- replace the folder ID and store name with real values:"),
        code("python setup_store_backup.py YOUR_FOLDER_ID \"Store Name Here\""),
        p("Example with the actual folder ID:"),
        code("python setup_store_backup.py 1hNbs7Kxe36V41Ohlo0fc7LxIOk7OiKhC \"Satpuda Vet Nagpur\""),
        p("You should see:"),
        code("backup_config.dat written successfully.\n"
             "  Store : Satpuda Vet Nagpur\n"
             "  Folder: 1hNbs7Kxe36V41Ohlo0fc7LxIOk7OiKhC\n"
             "Backups will start silently on next app launch."),
        ok("The store owner sees nothing. Backups start automatically on next app open."),
        sp(2),

        h2("Step 10 -- Verify Backup Works"),
        p("Test the backup manually before leaving the store:"),
        code("python -c \"from core.backup_manager import run_backup_now; run_backup_now()\""),
        p("Then check the log:"),
        code("type config\\backup_log.txt"),
        p("Last line should show:"),
        code("INFO  Backup OK - SatpudaCore_2026-04-24_10-30.db.gz -> Satpuda Vet Nagpur"),
        p("Also verify on Drive: go to <b>drive.google.com</b> -> "
          "<b>SatpudaCore Backups -> Store_Satpuda_Vet_Nagpur</b> -- the .db.gz file should be there."),
        sp(2),

        h2("Store Record -- Keep This Safe"),
        tbl([
            ['Store Name', 'Folder ID (same for all)', 'Activation Date', 'Notes'],
            ['', '1hNbs7Kxe36V41Ohlo0fc7LxIOk7OiKhC', '', ''],
            ['', '1hNbs7Kxe36V41Ohlo0fc7LxIOk7OiKhC', '', ''],
            ['', '1hNbs7Kxe36V41Ohlo0fc7LxIOk7OiKhC', '', ''],
            ['', '1hNbs7Kxe36V41Ohlo0fc7LxIOk7OiKhC', '', ''],
            ['', '1hNbs7Kxe36V41Ohlo0fc7LxIOk7OiKhC', '', ''],
        ], col_widths=[50*mm, 65*mm, 35*mm, 30*mm]),
        PageBreak(),
    ]

    # ── Phase 3 ────────────────────────────────────────────────────────────────
    story += [
        h1("Phase 3 -- How Backup Works in the App"),
        sp(2),

        h2("Backup Schedule"),
        tbl([
            ['Trigger', 'When', 'Notes'],
            ['App opens', 'Immediately on launch', 'New file with current timestamp'],
            ['Every 1 hour', 'While app is running', 'New file each time'],
            ['App closes', 'Final backup before exit (blocking)', 'Skipped if last backup was < 5 min ago'],
            ['Manual', 'Settings -> Database -> Backup Now button', 'Skipped if last backup was < 5 min ago'],
        ], col_widths=[40*mm, 70*mm, 70*mm]),
        sp(2),
        note("Each backup creates a NEW file with its own timestamp -- old files are NEVER "
             "overwritten or replaced. Open at 9:00 = SatpudaCore_..._09-00.db.gz. "
             "Close at 9:02 = skipped (within 5-min dedup window). "
             "Close at 9:10 = SatpudaCore_..._09-10.db.gz (separate file)."),
        sp(4),

        h2("Internet Check"),
        p("Before every backup, the app checks internet by trying to connect to 4 different servers:"),
        p("8.8.8.8:53 (Google DNS)  |  1.1.1.1:53 (Cloudflare)  |  "
          "google.com:80  |  google.com:443"),
        p("If ALL four fail -> backup is silently skipped. No error shown. Retried next hour."),
        sp(2),

        h2("Drive Connection Error"),
        p("If internet is available but Google Drive connection fails (token issue, "
          "network block, etc.) -> a warning dialog is shown to the user:"),
        note("Automatic backup could not connect to Google Drive.\n"
             "Your data is safe locally. Backup will retry in 1 hour."),
        sp(2),

        h2("File Retention -- 4 Files Per Day, 3-Year Limit"),
        p("After each upload, the app keeps <b>4 files per day</b> and deletes the middle ones:"),
        p("- File 1: First backup of the day (app opens in morning)"),
        p("- File 2: Second backup of the day (1 hour after open)"),
        p("- File 3: Second-last backup of the day"),
        p("- File 4: Last backup of the day (app closes in evening)"),
        p("- All middle files between File 2 and File 3 are deleted automatically"),
        p("- <b>Files older than 3 years are permanently deleted</b> from Drive and pendrive"),
        p("Example of what you see on Drive for one store:"),
        code("Store_Satpuda_Vet_Nagpur/\n"
             "  SatpudaCore_2026-04-26_09-00.db.gz   <- 1st of day\n"
             "  SatpudaCore_2026-04-26_10-00.db.gz   <- 2nd of day\n"
             "  SatpudaCore_2026-04-26_17-00.db.gz   <- 2nd last of day\n"
             "  SatpudaCore_2026-04-26_18-00.db.gz   <- last of day\n"
             "  SatpudaCore_2026-04-25_09-00.db.gz\n"
             "  ... (4 files per day, kept for 3 years)"),
        p("Storage estimate: 4 files x 500 KB x 365 days x 3 years = ~2.1 GB per store. "
          "15 GB free Gmail storage handles ~7 stores."),
        sp(2),

        h2("Manual Backup Button"),
        p("Location: <b>Settings -> Database tab -> Google Drive Backup section -> Backup Now</b>"),
        p("Shows result after clicking:"),
        p("- Backup successful!"),
        p("- No internet connection."),
        p("- Backup not configured. (run setup_store_backup.py first)"),
        p("- Backup failed. Check backup_log.txt."),
        sp(2),

        h2("Restoring a Backup"),
        p("1. Log into your Gmail -> drive.google.com"),
        p("2. Open SatpudaCore Backups -> Store folder"),
        p("3. Download the latest .db.gz file"),
        p("4. Open Command Prompt and run:"),
        code("python -c \""
             "import gzip, shutil; "
             "open('veterinary.db','wb').write(gzip.open('SatpudaCore_xxx.db.gz','rb').read())"
             "\""),
        p("5. Open veterinary.db with <b>DB Browser for SQLite</b> -- all data is there"),
        PageBreak(),
    ]

    # ── Phase 3B: File Naming, Restore & Pendrive ──────────────────────────────
    story += [
        h1("Phase 3B -- Backup File Naming, Full Restore & Pendrive Backup"),
        sp(2),

        h2("How to Read the Backup File Name"),
        p("Every backup file follows this exact format:"),
        code("SatpudaCore_YYYY-MM-DD_HH-MM.db.gz"),
        p("Example:  <b>SatpudaCore_2026-04-24_10-30.db.gz</b>"),
        tbl([
            ['Part', 'Meaning', 'Example'],
            ['SatpudaCore', 'Fixed prefix -- always present', 'SatpudaCore'],
            ['YYYY-MM-DD', 'Date backup was taken (Year-Month-Day)', '2026-04-24 = 24 April 2026'],
            ['HH-MM', 'Time backup was taken (24-hour clock)', '10-30 = 10:30 AM'],
            ['.db.gz', 'Gzip-compressed SQLite database file', '--'],
        ], col_widths=[40*mm, 90*mm, 50*mm]),
        note("The date and time in the filename are taken from the store machine's local clock "
             "at the moment the backup ran."),
        sp(4),

        h2("Step-by-Step: Download from Google Drive"),
        p("1. Open <b>drive.google.com</b> in your browser and sign in with the backup Gmail"),
        p("2. Navigate to <b>SatpudaCore Backups</b> folder"),
        p("3. Open the subfolder named <b>Store_&lt;StoreName&gt;</b> "
           "(e.g. Store_Satpuda_Vet_Nagpur)"),
        p("4. Find the file you want -- pick the most recent, or a specific date if needed"),
        p("5. Right-click the file -> <b>Download</b>"),
        p("6. The .db.gz file saves to your Downloads folder"),
        sp(4),

        h2("Step-by-Step: Extract the .db.gz File"),
        p("The file is gzip-compressed. You must extract it to get <b>veterinary.db</b>."),
        sp(2),
        p("<b>Option A -- 7-Zip (recommended, free):</b>"),
        p("1. Install 7-Zip from <b>https://www.7-zip.org</b> if not already installed"),
        p("2. Right-click the downloaded .db.gz file"),
        p("3. Choose  7-Zip -> Extract Here"),
        p("4. You get a file named  <b>veterinary.db</b>"),
        sp(2),
        p("<b>Option B -- Windows 11 built-in:</b>"),
        p("1. Right-click the .db.gz file -> Extract All"),
        p("2. You get  <b>veterinary.db</b>"),
        sp(2),
        p("<b>Option C -- Python command (if Python is installed):</b>"),
        code("python -c \"import gzip,shutil; "
             "shutil.copyfileobj(gzip.open('SatpudaCore_2026-04-24_10-30.db.gz','rb'), "
             "open('veterinary.db','wb'))\""),
        p("Replace the filename with your actual downloaded file name."),
        sp(4),

        h2("Step-by-Step: Restore the Database"),
        warn("Always rename the old database file BEFORE replacing it. "
             "Never delete it directly. This lets you go back if something goes wrong."),
        p("1. <b>Close the app completely</b> -- make sure it is NOT running"),
        p("2. Go to the folder where <b>VeterinaryManagementSystem.exe</b> is located"),
        p("3. You will see the current  <b>veterinary.db</b>  file there"),
        p("4. Rename the current file as a safety copy:"),
        code("veterinary.db  ->  veterinary_old_backup.db"),
        p("5. Copy the extracted  <b>veterinary.db</b>  into that same folder"),
        p("6. Double-click <b>VeterinaryManagementSystem.exe</b> to start the app"),
        p("7. Verify your data is restored correctly"),
        p("8. Once confirmed, you can delete  veterinary_old_backup.db"),
        sp(4),

        h2("Pendrive (USB) Backup -- Automatic"),
        p("The app automatically detects any connected pendrive (USB drive) and copies "
          "the backup to it -- <b>no setup needed, works even without internet</b>."),
        sp(2),
        tbl([
            ['What', 'Detail'],
            ['Detection method', 'Scans drive letters D: to Z: for removable drives (Windows)'],
            ['Trigger', 'Every backup cycle (every 1 hour), even without internet'],
            ['Folder created on pendrive', 'SatpudaCore_Backup\\Store_<StoreName>\\'],
            ['Retention on pendrive', 'Same as Drive: 3-year limit + first 2 / last 2 per day'],
            ['Multiple pendrives', 'First detected removable drive is used'],
            ['No pendrive connected', 'Silently skipped, no error'],
        ], col_widths=[65*mm, 115*mm]),
        sp(2),
        p("Example folder structure on the pendrive:"),
        code("E:\\SatpudaCore_Backup\\\n"
             "  Store_Satpuda_Vet_Nagpur\\\n"
             "    SatpudaCore_2026-04-24_08-00.db.gz\n"
             "    SatpudaCore_2026-04-24_10-30.db.gz\n"
             "    SatpudaCore_2026-04-25_09-00.db.gz"),
        sp(2),
        p("<b>To restore from pendrive:</b> Follow the same Extract and Restore steps above. "
          "Just pick the .db.gz file directly from the pendrive instead of downloading from Drive."),
        note("The pendrive must have enough free space. Each backup file is typically "
             "under 1 MB (compressed). 4 files/day x 365 days x 3 years = ~4,380 files = ~4 GB max."),
        PageBreak(),
    ]

    # ── Phase 4 ────────────────────────────────────────────────────────────────
    story += [
        h1("Phase 4 -- Building the EXE"),
        sp(2),

        h2("Files Bundled in EXE"),
        tbl([
            ['File', 'Purpose'],
            ['config/backup_creds.dat', 'Encrypted OAuth2 token -- your Gmail credentials'],
            ['config/backup_config.dat', 'Written at activation -- store folder ID (in AppData)'],
            ['config/backup_log.txt', 'Auto-created silent log -- never shown to user'],
        ], col_widths=[70*mm, 110*mm]),
        sp(4),

        h2("Build Command"),
        code("build_exe.bat"),
        p("backup_creds.dat is already listed in both .spec files and gets bundled automatically."),
        sp(2),

        h2("Complete Checklist"),
        tbl([
            ['When', 'What', 'Command / Action'],
            ['Once ever', 'Create Gmail + Cloud project', 'console.cloud.google.com'],
            ['Once ever', 'Enable Drive API', 'APIs & Services -> Library'],
            ['Once ever', 'Configure OAuth consent screen', 'Add yourself as test user'],
            ['Once ever', 'Create OAuth2 Client ID', 'Download JSON file'],
            ['Once ever', 'Rename JSON file', 'Rename to oauth_client.json'],
            ['Once ever', 'Move JSON to project folder', 'Same folder as main.py'],
            ['Once ever', 'Install packages', 'pip install google-api-python-client google-auth google-auth-oauthlib'],
            ['Once ever', 'Generate token', 'python generate_oauth_token.py'],
            ['Once ever', 'Create Drive folder', 'drive.google.com -> New Folder'],
            ['Once ever', 'Copy folder ID', 'From Drive URL after /folders/'],
            ['Per store', 'Write backup config', 'python setup_store_backup.py FOLDER_ID "Name"'],
            ['Per store', 'Verify backup', 'Check backup_log.txt and Drive'],
            ['Each build', 'Build EXE', 'build_exe.bat (auto-bundles creds)'],
        ], col_widths=[28*mm, 60*mm, 92*mm]),
        sp(6),
        hr(),
        Paragraph("SatpudaCore  |  Google Drive Backup Guide  |  CONFIDENTIAL",
                  ParagraphStyle('Footer', parent=styles['Normal'],
                                 fontSize=8, textColor=colors.grey,
                                 alignment=TA_CENTER)),
    ]

    doc.build(story)
    print(f"PDF created: {dst}")

if __name__ == '__main__':
    main()
