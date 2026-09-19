from pathlib import Path
p = Path("core/dot_matrix_print.py")
t = p.read_text(encoding="utf-8")
old = '''    wrapped = textwrap.wrap(text, width=FOOT_LEFT_W) or [text[:FOOT_LEFT_W]]
    return wrapped'''
new = '''    wrapped = textwrap.wrap(text, width=FOOT_LEFT_W) or [text[:FOOT_LEFT_W]]
    if _resolve_paper(settings) == "A6":
        wrapped = wrapped[:3]
    return wrapped'''
if old not in t:
    raise SystemExit('recovery block not found')
p.write_text(t.replace(old, new), encoding='utf-8', newline='\n')
print('ok')
