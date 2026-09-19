"""Generate desktop/src/themes.generated.css from CUSTOM_THEMES (+ alert_colors).

Outputs:
  - Classic: [data-theme='…']  (unchanged mapping)
  - Accessible pack: [data-theme-pack='accessible'][data-theme='…']
  - Modern pack: [data-theme-pack='modern'][data-theme='…']

Light themes (classic): muted from alert_colors, `light` soft panel.
Dark themes (classic): blended muted + lightened accent text.
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core.alert_colors import (  # noqa: E402
    _THEME_ALERT_COLORS,
    _THEME_MUTED,
    _DEFAULT_LIGHT,
)
from core.custom_themes import CUSTOM_THEMES  # noqa: E402

OUT = os.path.join(ROOT, "desktop", "src", "themes.generated.css")


def _hex_to_rgb(h: str) -> tuple[int, int, int]:
    h = (h or "").strip().lstrip("#")
    if len(h) == 3:
        h = "".join(ch * 2 for ch in h)
    if len(h) != 6:
        return (128, 128, 128)
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _rgb_to_hex(r: int, g: int, b: int) -> str:
    return (
        f"#{max(0, min(255, r)):02x}"
        f"{max(0, min(255, g)):02x}"
        f"{max(0, min(255, b)):02x}"
    )


def _mix(a: str, b: str, t: float) -> str:
    ra, ga, ba = _hex_to_rgb(a)
    rb, gb, bb = _hex_to_rgb(b)
    return _rgb_to_hex(
        int(ra + (rb - ra) * t),
        int(ga + (gb - ga) * t),
        int(ba + (bb - ba) * t),
    )


def _lum(h: str) -> float:
    r, g, b = [c / 255 for c in _hex_to_rgb(h)]

    def lin(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = lin(r), lin(g), lin(b)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _ratio(a: str, b: str) -> float:
    L1, L2 = _lum(a), _lum(b)
    hi, lo = max(L1, L2), min(L1, L2)
    return (hi + 0.05) / (lo + 0.05)


def _ensure_contrast(fg: str, bg: str, target: float = 4.5) -> str:
    if _ratio(fg, bg) >= target:
        return fg
    toward = "#ffffff" if _lum(bg) < 0.5 else "#000000"
    best = fg
    for i in range(1, 25):
        cand = _mix(fg, toward, i / 24)
        best = cand
        if _ratio(cand, bg) >= target:
            return cand
    return best


def _muted_for(name: str, typ: str, fg: str, bg: str) -> str:
    if typ == "light":
        m = _THEME_MUTED.get(name)
        if m:
            return m
        return _ensure_contrast(_mix(fg, bg, 0.45), bg, 4.5)
    return _ensure_contrast(_mix(fg, bg, 0.38), bg, 4.5)


def _hint_for(muted: str, bg: str, typ: str) -> str:
    if typ == "light":
        return _ensure_contrast(_mix(muted, bg, 0.22), bg, 4.5)
    return _ensure_contrast(_mix(muted, bg, 0.18), bg, 4.0)


def _accent_text(primary: str, surface: str, typ: str) -> str:
    if typ == "dark":
        return _ensure_contrast(_mix(primary, "#ffffff", 0.42), surface, 4.5)
    return _ensure_contrast(primary, surface, 4.5)


def _alerts(name: str, typ: str, c: dict) -> dict[str, str]:
    if typ == "light":
        alerts = _THEME_ALERT_COLORS.get(name) or _DEFAULT_LIGHT
        return {
            "success": alerts.get("success") or c["success"],
            "info": alerts.get("info") or c["info"],
            "warning": alerts.get("warning") or c["warning"],
            "danger": alerts.get("danger") or c["danger"],
        }
    return {
        "success": c["success"],
        "info": c["info"],
        "warning": c["warning"],
        "danger": c["danger"],
    }


def _classic_tokens(name: str, defn: dict) -> dict[str, str]:
    c = defn["colors"]
    typ = defn.get("type", "dark")
    surface = c["inputbg"]
    if typ == "light":
        soft = c.get("light") or _mix(c["bg"], "#ffffff", 0.2)
        label_accent = c["secondary"]
    else:
        soft = c["active"]
        label_accent = None

    muted = _muted_for(name, typ, c["fg"], c["bg"])
    hint = _hint_for(muted, c["bg"], typ)
    accent = _accent_text(c["primary"], surface, typ)
    if typ == "dark":
        label_accent = accent
    al = _alerts(name, typ, c)

    return {
        "type": typ,
        "bg": c["bg"],
        "surface": c["inputbg"],
        "surface_2": c["active"],
        "light": soft,
        "border": c["border"],
        "primary": c["primary"],
        "secondary": c["secondary"],
        "fg": c["fg"],
        "fg_muted": muted,
        "fg_hint": hint,
        "accent_text": accent,
        "label_accent": label_accent or accent,
        "success": al["success"],
        "info": al["info"],
        "warning": al["warning"],
        "danger": al["danger"],
        "selectbg": c["selectbg"],
        "selectfg": c["selectfg"],
        "inputbg": c["inputbg"],
        "inputfg": c["inputfg"],
    }


def _accessible_tokens(_name: str, classic: dict[str, str]) -> dict[str, str]:
    """Gemini-style: clearer chrome + high-contrast status on every theme."""
    t = dict(classic)
    typ = classic["type"]
    if typ == "dark":
        # Slightly lift surfaces so navy chrome is visibly different from Classic
        t["bg"] = _mix(classic["bg"], "#ffffff", 0.06)
        t["surface"] = _mix(classic["surface"], "#ffffff", 0.10)
        t["surface_2"] = _mix(classic["surface_2"], "#ffffff", 0.12)
        t["inputbg"] = t["surface"]
        t["light"] = t["surface_2"]
        t["border"] = _mix(classic["border"], "#ffffff", 0.14)
        t["primary"] = _ensure_contrast(
            _mix(classic["primary"], "#ffffff", 0.22), t["surface"], 3.2
        )
        t["selectbg"] = t["primary"]
        t["secondary"] = _mix(classic["secondary"], "#ffffff", 0.12)
        t["fg"] = _ensure_contrast(_mix(classic["fg"], "#ffffff", 0.08), t["bg"], 7.0)
        t["fg_muted"] = _muted_for("", "dark", t["fg"], t["bg"])
        t["fg_hint"] = _hint_for(t["fg_muted"], t["bg"], "dark")
        t["accent_text"] = _accent_text(t["primary"], t["surface"], "dark")
        t["label_accent"] = t["accent_text"]
        t["success"] = "#38c172"
        t["warning"] = "#f59e0b"
        t["info"] = "#38bdf8"
        t["danger"] = _ensure_contrast(
            _mix(classic["danger"], "#ffffff", 0.18), t["surface"], 3.5
        )
    else:
        # Cooler, higher-contrast light chrome (readable on every family)
        t["bg"] = "#f8fafc"
        t["surface"] = _mix(classic["surface"], "#e2e8f0", 0.35)
        t["surface_2"] = _mix(classic["surface_2"], "#cbd5e1", 0.28)
        t["light"] = t["surface"]
        t["inputbg"] = "#ffffff"
        t["border"] = _mix(classic["border"], "#64748b", 0.22)
        t["primary"] = _ensure_contrast(
            _mix(classic["primary"], "#000000", 0.12), t["surface"], 4.8
        )
        t["selectbg"] = t["primary"]
        t["secondary"] = _ensure_contrast(
            _mix(classic["secondary"], "#000000", 0.08), t["bg"], 4.5
        )
        t["fg"] = _ensure_contrast(classic["fg"], t["bg"], 8.0)
        t["fg_muted"] = _muted_for("", "light", t["fg"], t["bg"])
        t["fg_hint"] = _hint_for(t["fg_muted"], t["bg"], "light")
        t["accent_text"] = _accent_text(t["primary"], t["surface"], "light")
        t["label_accent"] = t["secondary"]
        t["success"] = "#15803d"
        t["warning"] = "#b45309"
        t["info"] = "#0284c7"
        t["danger"] = "#dc2626"
    return t


def _modern_tokens(classic: dict[str, str]) -> dict[str, str]:
    """ChatGPT-style: deeper dark surfaces / softer light panels + vivid status."""
    t = dict(classic)
    typ = classic["type"]
    if typ == "dark":
        t["bg"] = _mix(classic["bg"], "#000000", 0.38)
        t["surface"] = _mix(classic["surface"], "#000000", 0.30)
        t["surface_2"] = _mix(classic["surface_2"], "#000000", 0.22)
        t["inputbg"] = t["surface"]
        t["light"] = t["surface_2"]
        t["border"] = _mix(classic["border"], t["surface"], 0.55)
        t["primary"] = _ensure_contrast(
            _mix(classic["primary"], "#ffffff", 0.32), t["surface"], 3.5
        )
        t["selectbg"] = t["primary"]
        t["secondary"] = _mix(classic["secondary"], "#ffffff", 0.16)
        t["fg"] = _ensure_contrast(_mix(classic["fg"], "#ffffff", 0.06), t["bg"], 7.0)
        t["fg_muted"] = _muted_for("", "dark", t["fg"], t["bg"])
        t["fg_hint"] = _hint_for(t["fg_muted"], t["bg"], "dark")
        t["accent_text"] = _accent_text(t["primary"], t["surface"], "dark")
        t["label_accent"] = t["accent_text"]
        t["success"] = "#22c55e"
        t["warning"] = "#f59e0b"
        t["danger"] = "#ef4444"
        t["info"] = "#38bdf8"
    else:
        # Soft warm-white panels + stronger primary (clearly not Classic white)
        t["bg"] = _mix("#ffffff", classic["surface_2"], 0.12)
        t["surface"] = _mix(classic["surface"], "#ffffff", 0.25)
        t["surface_2"] = _mix(classic["surface_2"], classic["primary"], 0.08)
        t["light"] = t["surface"]
        t["inputbg"] = "#ffffff"
        t["border"] = _mix(classic["border"], classic["primary"], 0.18)
        t["primary"] = _ensure_contrast(
            _mix(classic["primary"], "#000000", 0.14), t["surface"], 5.0
        )
        t["selectbg"] = t["primary"]
        t["secondary"] = _mix(classic["secondary"], "#000000", 0.10)
        t["fg"] = _ensure_contrast(classic["fg"], t["bg"], 8.0)
        t["fg_muted"] = _muted_for("", "light", t["fg"], t["bg"])
        t["fg_hint"] = _hint_for(t["fg_muted"], t["bg"], "light")
        t["accent_text"] = _accent_text(t["primary"], t["surface"], "light")
        t["label_accent"] = t["secondary"]
        t["success"] = "#22c55e"
        t["warning"] = "#f59e0b"
        t["danger"] = "#ef4444"
        t["info"] = "#38bdf8"
    return t


def _emit_block(selector: str, t: dict[str, str]) -> list[str]:
    return [
        f"{selector} {{",
        f"  color-scheme: {t['type']};",
        f"  --bg: {t['bg']};",
        f"  --surface: {t['surface']};",
        f"  --surface-2: {t['surface_2']};",
        f"  --light: {t['light']};",
        f"  --border: {t['border']};",
        f"  --primary: {t['primary']};",
        f"  --secondary: {t['secondary']};",
        f"  --fg: {t['fg']};",
        f"  --fg-muted: {t['fg_muted']};",
        f"  --fg-hint: {t['fg_hint']};",
        f"  --accent-text: {t['accent_text']};",
        f"  --label-accent: {t['label_accent']};",
        f"  --success: {t['success']};",
        f"  --info: {t['info']};",
        f"  --warning: {t['warning']};",
        f"  --danger: {t['danger']};",
        f"  --selectbg: {t['selectbg']};",
        f"  --selectfg: {t['selectfg']};",
        f"  --inputbg: {t['inputbg']};",
        f"  --inputfg: {t['inputfg']};",
        "}",
        "",
    ]


def main() -> None:
    lines = [
        "/* Auto-generated from core/custom_themes.py + core/alert_colors.py */",
        "/* Run: python scripts/gen_desktop_themes_css.py */",
        "/* Classic: [data-theme]. Packs: [data-theme-pack=accessible|modern]. */",
        "/* Accessible: lifted chrome + high-contrast status. */",
        "/* Modern: deeper dark / soft light panels + vivid status. */",
        "",
    ]

    classic_all: dict[str, dict[str, str]] = {}
    for name, defn in CUSTOM_THEMES.items():
        classic = _classic_tokens(name, defn)
        classic_all[name] = classic
        lines.extend(_emit_block(f"[data-theme='{name}']", classic))

    lines.append("/* ── Theme pack: Accessible (Gemini contrast + clearer chrome) ── */")
    lines.append("")
    for name, classic in classic_all.items():
        acc = _accessible_tokens(name, classic)
        lines.extend(
            _emit_block(
                f"[data-theme-pack='accessible'][data-theme='{name}']",
                acc,
            )
        )

    lines.append("/* ── Theme pack: Modern (ChatGPT surfaces/status) ── */")
    lines.append("")
    for name, classic in classic_all.items():
        mod = _modern_tokens(classic)
        lines.extend(
            _emit_block(f"[data-theme-pack='modern'][data-theme='{name}']", mod)
        )

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    print(f"Wrote {len(CUSTOM_THEMES)} classic themes + packs to {OUT}")


if __name__ == "__main__":
    main()
