from pathlib import Path
p = Path("core/dot_matrix_print.py")
raw = p.read_bytes()
if raw.count(b"\x00") > len(raw) // 4:
    text = raw.decode("utf-16-le")
else:
    text = raw.decode("utf-8-sig", errors="replace")
text = text.replace('out += b"\\x1bE\\x01"', 'out += b"\\x1bE"')
old = '    out += b"\\x1bF"\n    out += b"\\x0c"'
new = '    out += b"\\x1bF"\n    out += b"\\r\\n" * 4\n    out += b"\\x0c"'
text = text.replace(old, new)
needle = "    payload = render_escp_document(text)\n    from core.printer_manager import PrinterManager"
repl = """    payload = render_escp_document(text)
    try:
        from core.print_log import print_log
        print_log(
            f'format_bill_text lines={len(text.splitlines())} payload_bytes={len(payload)} '
            f'printer=\"{printer_name}\" copies={copies}'
        )
    except Exception:
        pass
    from core.printer_manager import PrinterManager"""
if needle in text and "format_bill_text lines=" not in text:
    text = text.replace(needle, repl)
p.write_text(text, encoding="utf-8", newline="\n")
print("fixed nulls", p.read_bytes().count(b"\x00"))