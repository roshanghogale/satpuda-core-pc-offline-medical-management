"""
Run this script ONCE to generate the developer reference PDF.
Keep the PDF in a safe place — do NOT include it with the software.

Usage:
    python generate_developer_pdf.py
"""

import os
import sys


def generate():
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib import colors
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import mm
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
    except ImportError:
        print("Installing reportlab...")
        import subprocess
        subprocess.check_call([sys.executable, '-m', 'pip', 'install', 'reportlab'])
        from reportlab.lib.pagesizes import A4
        from reportlab.lib import colors
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import mm
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable

    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            'DEVELOPER_REFERENCE_CONFIDENTIAL.pdf')

    doc = SimpleDocTemplate(out_path, pagesize=A4,
                            leftMargin=20*mm, rightMargin=20*mm,
                            topMargin=20*mm, bottomMargin=20*mm)

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle('Title2', parent=styles['Title'],
                                 fontSize=20, textColor=colors.HexColor('#1a1a2e'),
                                 spaceAfter=6)
    h1 = ParagraphStyle('H1', parent=styles['Heading1'],
                        fontSize=13, textColor=colors.HexColor('#16213e'),
                        spaceBefore=14, spaceAfter=4)
    h2 = ParagraphStyle('H2', parent=styles['Heading2'],
                        fontSize=11, textColor=colors.HexColor('#0f3460'),
                        spaceBefore=10, spaceAfter=3)
    body = ParagraphStyle('Body2', parent=styles['Normal'],
                          fontSize=10, leading=16)
    warn = ParagraphStyle('Warn', parent=styles['Normal'],
                          fontSize=10, leading=16,
                          textColor=colors.HexColor('#c0392b'),
                          backColor=colors.HexColor('#fdecea'))
    code = ParagraphStyle('Code', parent=styles['Normal'],
                          fontSize=11, leading=18, fontName='Courier',
                          backColor=colors.HexColor('#f4f4f4'),
                          leftIndent=10, rightIndent=10)

    cell_body = ParagraphStyle('CellBody', parent=styles['Normal'],
                                fontSize=10, leading=14, wordWrap='LTR')
    cell_head = ParagraphStyle('CellHead', parent=styles['Normal'],
                                fontSize=10, leading=14, wordWrap='LTR',
                                textColor=colors.white, fontName='Helvetica-Bold')
    cell_bold = ParagraphStyle('CellBold', parent=styles['Normal'],
                                fontSize=10, leading=14, wordWrap='LTR',
                                fontName='Helvetica-Bold')

    def _wrap(val, style):
        return Paragraph(str(val), style) if not isinstance(val, Paragraph) else val

    def tbl(data, col_widths=None):
        wrapped = []
        for r_idx, row in enumerate(data):
            new_row = []
            for c_idx, cell in enumerate(row):
                if r_idx == 0:
                    new_row.append(_wrap(cell, cell_head))
                elif c_idx == 0:
                    new_row.append(_wrap(cell, cell_bold))
                else:
                    new_row.append(_wrap(cell, cell_body))
            wrapped.append(new_row)
        t = Table(wrapped, colWidths=col_widths or [80*mm, 90*mm])
        t.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#16213e')),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1),
             [colors.HexColor('#f9f9f9'), colors.white]),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#cccccc')),
            ('PADDING', (0, 0), (-1, -1), 7),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ]))
        return t

    story = []

    # ── Cover ──────────────────────────────────────────────────────────────────
    story.append(Spacer(1, 10*mm))
    story.append(Paragraph("🔐 DEVELOPER REFERENCE", title_style))
    story.append(Paragraph("Veterinary Management System — Confidential", styles['Heading2']))
    story.append(Spacer(1, 4*mm))
    story.append(HRFlowable(width="100%", thickness=2, color=colors.HexColor('#1a1a2e')))
    story.append(Spacer(1, 4*mm))
    story.append(Paragraph(
        "⚠ This document contains sensitive credentials. "
        "Do NOT share, print, or include with the software installation. "
        "Store securely (encrypted drive or password manager).",
        warn))
    story.append(Spacer(1, 8*mm))

    # ── 1. Master Activation Credentials ──────────────────────────────────────
    story.append(Paragraph("1. Master Activation Credentials", h1))
    story.append(Paragraph(
        "These are entered in the activation dialog when installing on a new device. "
        "All three fields must be correct to activate.", body))
    story.append(Spacer(1, 3*mm))
    story.append(tbl([
        ['Field', 'Value'],
        ['Username', 'RoshanMedicalManagerUserName'],
        ['Password', 'RoshanMedicalManagerPassword'],
        ['Device Key', '(read from device.key file — unique per device)'],
    ]))
    story.append(Spacer(1, 6*mm))

    # ── 2. Database Delete Password ────────────────────────────────────────────
    story.append(Paragraph("2. Database Delete Password", h1))
    story.append(Paragraph(
        "Required in Settings → Database → DELETE ALL TABLES. "
        "Permanently deletes all data. Use only when resetting a device.", body))
    story.append(Spacer(1, 3*mm))
    story.append(tbl([
        ['Field', 'Value'],
        ['Delete Password', 'RoshanDeleteDatabase'],
    ]))
    story.append(Spacer(1, 6*mm))

    # ── 3. How to Find device.key ──────────────────────────────────────────────
    story.append(Paragraph("3. How to Find device.key on Any Device", h1))
    story.append(Paragraph(
        "The device.key file is generated automatically on first run. "
        "It contains a unique hardware fingerprint for that device. "
        "You must copy its content and paste it into the Device Key field during activation.", body))
    story.append(Spacer(1, 3*mm))

    story.append(Paragraph("Method 1 — File Explorer (Easiest)", h2))
    steps1 = [
        "Press  Win + R  on the keyboard.",
        "Type exactly:   %LOCALAPPDATA%\\VeterinaryApp",
        "Press Enter — File Explorer opens the folder.",
        "Find the file named:   device.key",
        "Right-click → Open with → Notepad",
        "Select All (Ctrl+A) → Copy (Ctrl+C)",
        "Paste into the Device Key field in the activation dialog.",
    ]
    for i, s in enumerate(steps1, 1):
        story.append(Paragraph(f"  {i}.  {s}", body))
    story.append(Spacer(1, 3*mm))

    story.append(Paragraph("Method 2 — Address Bar", h2))
    story.append(Paragraph(
        "Open any File Explorer window. Click the address bar at the top. "
        "Paste the full path below and press Enter:", body))
    story.append(Spacer(1, 2*mm))
    story.append(Paragraph(
        "C:\\Users\\[username]\\AppData\\Local\\VeterinaryApp\\device.key", code))
    story.append(Spacer(1, 2*mm))
    story.append(Paragraph(
        "Replace [username] with the Windows login name of the device.", body))
    story.append(Spacer(1, 3*mm))

    story.append(Paragraph("Method 3 — Command Prompt", h2))
    story.append(Paragraph("Open Command Prompt and run:", body))
    story.append(Spacer(1, 2*mm))
    story.append(Paragraph(
        'type "%LOCALAPPDATA%\\VeterinaryApp\\device.key"', code))
    story.append(Spacer(1, 2*mm))
    story.append(Paragraph(
        "The 64-character key will be printed directly in the terminal. Copy it.", body))
    story.append(Spacer(1, 6*mm))

    # ── 4. Activation Steps ────────────────────────────────────────────────────
    story.append(Paragraph("4. Step-by-Step Activation (Each New Device)", h1))
    act_steps = [
        "Copy the .exe to the client device and run it.",
        "The activation dialog appears (dark screen with 3 fields).",
        "Open device.key using any method from Section 3 above.",
        "Copy the 64-character content of device.key.",
        "In the activation dialog, enter:",
        "      Username  →  RoshanMedicalManagerUserName",
        "      Password  →  RoshanMedicalManagerPassword",
        "      Device Key  →  (paste the copied content)",
        "Click 'Activate Software'.",
        "The app opens. device.key is automatically deleted.",
        "The device is now permanently activated — no prompt on future runs.",
    ]
    for i, s in enumerate(act_steps, 1):
        story.append(Paragraph(f"  {i}.  {s}", body))
    story.append(Spacer(1, 6*mm))

    # ── 5. Security Notes ──────────────────────────────────────────────────────
    story.append(Paragraph("5. Security Notes", h1))
    notes = [
        "device.key is deleted after successful activation — nothing sensitive remains.",
        "activation.dat is stored in AppData and contains an encrypted hardware hash.",
        "Copying the exe to another device generates a NEW device.key with different content.",
        "Copying exe + AppData to another device fails — hardware hash won't match.",
        "If a device is replaced/reinstalled, just run the exe again and re-activate.",
        "The master credentials are compiled into the exe — not visible as plain text.",
    ]
    for n in notes:
        story.append(Paragraph(f"  •  {n}", body))
    story.append(Spacer(1, 6*mm))

    # ── 6. AppData Folder Reference ───────────────────────────────────────────
    story.append(Paragraph("6. AppData Folder Contents", h1))
    story.append(tbl([
        ['File', 'Purpose'],
        ['device.key', 'Temporary — plain text hardware hash. Deleted after activation.'],
        ['activation.dat', 'Permanent — encrypted hardware hash. Proves device is licensed.'],
        ['expiry.dat', 'Optional — encrypted expiry restriction. Controls access duration.'],
        ['theme_config.txt', 'Stores selected UI theme.'],
        ['font_size.txt', 'Stores selected font size.'],
        ['layout_config.txt', 'Stores row counts and layout preferences.'],
        ['veterinary.db', 'SQLite database (if exe is run from AppData).'],
    ], col_widths=[55*mm, 115*mm]))
    story.append(Spacer(1, 8*mm))

    # ── 7. expiry.dat — Timed Access Control ─────────────────────────────────────────
    story.append(Paragraph("7. expiry.dat — Timed Access Control", h1))
    story.append(Paragraph(
        "expiry.dat is automatically created every time device.key is generated — "
        "that means on first run AND whenever the app detects a hardware mismatch "
        "(e.g. the exe was copied to a different machine). "
        "It sets a deadline after which the activation is automatically revoked and "
        "the device must be re-activated.", body))
    story.append(Spacer(1, 3*mm))

    story.append(Paragraph("What it contains (encrypted JSON)", h2))
    story.append(tbl([
        ['Field', 'Type', 'Description'],
        ['enabled', 'true / false',
         'If true, expiry is active. If false, the file is ignored and the app runs freely.'],
        ['expiry_date', 'YYYY-MM-DD',
         'The date on which access is revoked. On or after this date the activation is deleted.'],
    ], col_widths=[35*mm, 30*mm, 105*mm]))
    story.append(Spacer(1, 3*mm))

    story.append(Paragraph("Default behaviour", h2))
    story.append(Paragraph(
        "When device.key is first written, expiry.dat is created with "
        "enabled = true and expiry_date = today + 5 days. "
        "This gives the client 5 days to activate. "
        "You can replace this file at any time with any date you choose.", body))
    story.append(Spacer(1, 3*mm))

    story.append(Paragraph("How to READ the current expiry.dat content", h2))
    story.append(Paragraph(
        "Since expiry.dat is encrypted you cannot open it in Notepad. "
        "Use this command on the client machine or your own machine:", body))
    story.append(Paragraph(
        "python -c \"from core.license_manager import _read_expiry; "
        "print(_read_expiry())\"",
        code))
    story.append(Paragraph(
        "Output example:  {'enabled': True, 'expiry_date': '2026-12-31'}",
        body))
    story.append(Spacer(1, 3*mm))

    story.append(Paragraph("How to edit or replace expiry.dat", h2))
    story.append(Paragraph(
        "The file is encrypted with the same key as activation.dat, so you cannot "
        "edit it in Notepad. Use the helper script below on any machine that has "
        "the app source code or the Python environment:", body))
    story.append(Spacer(1, 2*mm))
    story.append(Paragraph(
        "python -c \"from core.license_manager import write_expiry; "
        "write_expiry('2026-12-31', enabled=True)\"",
        code))
    story.append(Spacer(1, 2*mm))
    story.append(Paragraph(
        "Replace 2026-12-31 with any date you want. "
        "Run this on the client machine (or copy the generated expiry.dat to the client's AppData folder).",
        body))
    story.append(Spacer(1, 3*mm))

    story.append(Paragraph("How to deploy expiry.dat to a client machine remotely", h2))
    story.append(Paragraph(
        "If you cannot run Python on the client machine, generate expiry.dat on YOUR machine "
        "and copy the file to the client:", body))
    exp_deploy = [
        "On YOUR machine, run the write_expiry command above with the desired date.",
        "The file is created at:  config\\expiry.dat  (in the project folder, dev mode)",
        "Copy that file to the client machine at:",
        "      %LOCALAPPDATA%\\VeterinaryApp\\expiry.dat",
        "      (e.g. C:\\Users\\NARENDRA\\AppData\\Local\\VeterinaryApp\\expiry.dat)",
        "The new expiry takes effect immediately on next app start.",
    ]
    for i, s in enumerate(exp_deploy, 1):
        story.append(Paragraph(f"  {i}.  {s}", body))
    story.append(Spacer(1, 3*mm))

    story.append(Paragraph("How to disable expiry (give permanent access)", h2))
    story.append(Paragraph(
        "Option 1 — Delete the file: simply delete expiry.dat from the AppData folder. "
        "The app will run without any time restriction.", body))
    story.append(Paragraph(
        "Option 2 — Set enabled=false:", body))
    story.append(Paragraph(
        "python -c \"from core.license_manager import write_expiry; "
        "write_expiry('2099-01-01', enabled=False)\"",
        code))
    story.append(Spacer(1, 3*mm))

    story.append(Paragraph("Where to find expiry.dat on the client machine", h2))
    story.append(Paragraph(
        "Same folder as device.key and activation.dat:", body))
    story.append(Paragraph(
        "%LOCALAPPDATA%\\VeterinaryApp\\expiry.dat", code))
    story.append(Spacer(1, 3*mm))

    story.append(Paragraph("What happens when expiry triggers", h2))
    exp_steps = [
        "App starts and calls check_expiry().",
        "current date >= expiry_date AND enabled=true → expiry triggers.",
        "activation.dat is deleted (device is de-licensed).",
        "expiry.dat is deleted.",
        "A fresh device.key is generated (with a new 5-day expiry.dat).",
        "The activation dialog appears — client must contact you for a new activation.",
    ]
    for i, s in enumerate(exp_steps, 1):
        story.append(Paragraph(f"  {i}.  {s}", body))
    story.append(Spacer(1, 8*mm))

    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor('#cccccc')))
    story.append(Spacer(1, 3*mm))
    story.append(Paragraph(
        "Generated by generate_developer_pdf.py  •  Veterinary Management System  •  CONFIDENTIAL",
        styles['Normal']))

    doc.build(story)
    print(f"\nPDF generated successfully:\n   {out_path}\n")
    print("Keep this file secure. Do NOT share it with anyone.\n")


if __name__ == '__main__':
    generate()
