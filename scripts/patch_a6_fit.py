from pathlib import Path

p = Path("core/dot_matrix_print.py")
t = p.read_text(encoding="utf-8")

t = t.replace(
    "    try:\n        min_rows = max(1, int(settings.get(\"items_per_bill_page\") or 12))\n    except (TypeError, ValueError):\n        min_rows = 12",
    "    min_rows = _table_pad_rows(settings)",
)

old_render = '''def render_escp_document(text: str) -> bytes:
    out = bytearray()
    out += b"\\x1b@"
    out += b"\\x1bx\\x01"
    out += b"\\x1bP"
    out += b"\\x1b2"
    for line in text.splitlines():
        if line == _BOLD_ON:
            out += b"\\x1bE"
            continue
        if line == _BOLD_OFF:
            out += b"\\x1bF"
            continue
        out += _encode_line_with_bold(line)
        out += b"\\r\\n"
    out += b"\\x1bF"
    out += b"\\r\\n" * 4
    out += b"\\x0c"
    return bytes(out)'''

new_render = '''def render_escp_document(text: str, *, paper: str = "A5") -> bytes:
    paper_u = (paper or "A5").upper()
    a6 = paper_u == "A6"
    out = bytearray()
    out += b"\\x1b@"
    out += b"\\x1bx\\x01"
    if a6:
        out += b"\\x0f"
        out += b"\\x1b3\\x14"
    else:
        out += b"\\x1bP"
        out += b"\\x1b2"
    for line in text.splitlines():
        if line == _BOLD_ON:
            out += b"\\x1bE"
            continue
        if line == _BOLD_OFF:
            out += b"\\x1bF"
            continue
        out += _encode_line_with_bold(line)
        out += b"\\r\\n"
    out += b"\\x1bF"
    if a6:
        out += b"\\x12"
    out += b"\\r\\n" * (2 if a6 else 4)
    out += b"\\x0c"
    return bytes(out)'''

if old_render not in t:
    raise SystemExit("render block not found")
t = t.replace(old_render, new_render)

old_print = '''    text = format_bill_text(ctx, settings)
    payload = render_escp_document(text)
    try:
        from core.print_log import print_log
        print_log(
            f"format_bill_text lines={len(text.splitlines())} payload_bytes={len(payload)} "
            f'printer="{printer_name}" copies={copies}'
        )'''

new_print = '''    text = format_bill_text(ctx, settings)
    paper = _resolve_paper(settings)
    try:
        from core.bill_config import get_bill_size_pct
        scale_pct = get_bill_size_pct(settings)
    except Exception:
        scale_pct = 92.0
    payload = render_escp_document(text, paper=paper)
    try:
        from core.print_log import print_log
        print_log(
            f"format_bill_text lines={len(text.splitlines())} payload_bytes={len(payload)} "
            f'paper={paper} scale_pct={scale_pct} printer="{printer_name}" copies={copies}'
        )'''

if old_print not in t:
    raise SystemExit("print block not found")
t = t.replace(old_print, new_print)

t = t.replace(
    '''        _log(f'dot_matrix trying GDI text on "{printer}"')
        PrinterManager.print_text_gdi(text, printer, copies=copies)''',
    '''        _log(f'dot_matrix trying GDI text on "{printer}" paper={paper}')
        PrinterManager.print_text_gdi(
            text, printer, copies=copies, paper_size=paper, scale_pct=scale_pct,
        )''',
)

p.write_text(t, encoding="utf-8", newline="\n")
print("patched", p.read_bytes().count(b"\\x00"), "nulls")
