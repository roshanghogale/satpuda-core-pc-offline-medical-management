from pathlib import Path
p = Path("core/dot_matrix_print.py")
raw = p.read_bytes()
if raw.count(b"\x00") > len(raw) // 4:
    text = raw.decode("utf-16-le")
else:
    text = raw.decode("utf-8-sig", errors="replace")
text = text.replace('out += b"\\x1bE\\x01"', 'out += b"\\x1bE"')
bad = (
    "Use printer queue \"EPSON LX-310 ESC/P\" (not the Class Driver queue). "
    "Settings -> Printer Setup -> Print Sales 1 = EPSON LX-310 ESC/P."
)
good = (
    'Use printer queue "EPSON LX-310 ESC/P" (not the Class Driver queue). '
    'Settings -> Printer Setup -> Print Sales 1 = EPSON LX-310 ESC/P.'
)
text = text.replace(bad, good)
# also fix fully broken hint line variant
import re
text = re.sub(
    r'hint = \(\n        "Use printer queue "EPSON[^)]+\)\n    \)',
    "hint = (\n        '" + good.split(". ")[0] + ". '\n        '" + good.split(". ", 1)[1] + "'\n    )",
    text,
)
p.write_text(text, encoding="utf-8", newline="\n")
compile(text, "dot_matrix_print.py", "exec")
print("OK nulls", p.read_bytes().count(b"\x00"))