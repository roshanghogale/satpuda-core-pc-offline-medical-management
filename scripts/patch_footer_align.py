from pathlib import Path
p = Path('core/dot_matrix_print.py')
t = p.read_text(encoding='utf-8')
old_left = '''def _footer_left_lines(ctx, settings: dict) -> list[str]:
    lines: list[str] = []
    if _setting(settings, "show_recovery_wish", True):
        wish = (
            getattr(ctx, "recovery_wish_line", None)
            or settings.get("recovery_wish_line", "I WISH FOR YOUR SPEEDY RECOVERY.")
        )
        text = _ascii_safe(wish).strip()
        if text:
            wrapped = textwrap.wrap(text, width=FOOT_LEFT_W) or [text[:FOOT_LEFT_W]]
            if _resolve_paper(settings) == "A6":
                wrapped = wrapped[:2]
            lines.extend(wrapped)
    if _setting(settings, "show_store_dl", True) and ctx.dl_no:
        lines.append(f"DL No. : {ctx.dl_no}"[:FOOT_LEFT_W])
    if _setting(settings, "show_store_phone", True) and ctx.phone:
        lines.append(f"Phone  : {ctx.phone}"[:FOOT_LEFT_W])
    return lines or [""]


def _signature_lines(ctx, settings: dict) -> list[str]:
    if not _setting(settings, "show_signature", True) or not ctx.store_name:
        return ["", "", ""]
    sign_l = _label(settings, "signature_caption", "SIGN OF Q.P.")
    for_line = f"For {_ascii_safe(ctx.store_name).upper()}"
    return ["", for_line[:FOOT_RIGHT_W], _center(sign_l, FOOT_RIGHT_W)]'''
new_left = '''def _footer_left_lines(ctx, settings: dict) -> list[str]:
    lines: list[str] = []
    if _setting(settings, "show_recovery_wish", True):
        wish = (
            getattr(ctx, "recovery_wish_line", None)
            or settings.get("recovery_wish_line", "I WISH FOR YOUR SPEEDY RECOVERY.")
        )
        text = _ascii_safe(wish).strip()
        if text:
            wrapped = textwrap.wrap(text, width=FOOT_LEFT_W) or [text[:FOOT_LEFT_W]]
            if _resolve_paper(settings) == "A6":
                wrapped = wrapped[:1]
            lines.extend(wrapped)
    lines.append("")
    if _setting(settings, "show_store_dl", True) and ctx.dl_no:
        lines.append(f"DL No. : {ctx.dl_no}"[:FOOT_LEFT_W])
    if _setting(settings, "show_store_phone", True) and ctx.phone:
        lines.append(f"Phone  : {ctx.phone}"[:FOOT_LEFT_W])
    return lines or [""]


def _signature_lines(ctx, settings: dict) -> list[str]:
    if not _setting(settings, "show_signature", True) or not ctx.store_name:
        return ["", "", ""]
    sign_l = _label(settings, "signature_caption", "SIGN OF Q.P.")
    for_line = f"For {_ascii_safe(ctx.store_name).upper()}"
    return [for_line[:FOOT_RIGHT_W], "", "", _center(sign_l, FOOT_RIGHT_W)]'''
if old_left not in t:
    raise SystemExit('block not found')
p.write_text(t.replace(old_left, new_left), encoding='utf-8', newline='\n')
from core.dot_matrix_print import format_test_page
print(format_test_page(''))
