from pathlib import Path
p = Path("core/dot_matrix_print.py")
t = p.read_text(encoding="utf-8")
old = '''    out += b"\\x1bF"
    if a6:
        out += b"\\x12"
    out += b"\\r\\n" * (2 if a6 else 4)
    out += b"\\x0c"
    return bytes(out)'''
new = '''    out += b"\\x1bF"
    if a6:
        out += b"\\x12"
    # Small tear feed only — no form feed (\\x0c) or extra blank lines (avoids 2+ empty pages).
    out += b"\\x1bJ\\x18"
    return bytes(out)'''
if old not in t:
    raise SystemExit('escp tail not found')
p.write_text(t.replace(old, new), encoding='utf-8', newline='\n')
print('dot_matrix ok', p.read_bytes().count(b'\x00'))
