"""
core/alert_colors.py
────────────────────
Per-theme contrast-correct alert, muted, and tree-tag colors.
Covers all 20 custom Satpuda Core themes (10 dark + 10 light).
"""

# ── Alert colors ──────────────────────────────────────────────────────────────
# Dark  → bright/vivid so text pops on dark bg
# Light → deep/saturated so text is readable on white bg

_THEME_ALERT_COLORS = {
    # ── Dark themes ───────────────────────────────────────────────────────
    'steel-dark':    {'success': '#5cb85c', 'warning': '#f0ad4e', 'danger': '#e05c5c', 'info': '#5bc0de'},
    'charcoal-dark': {'success': '#00bc8c', 'warning': '#f39c12', 'danger': '#e74c3c', 'info': '#3498db'},
    'crimson-dark':  {'success': '#3ddc84', 'warning': '#ffca28', 'danger': '#ff6b6b', 'info': '#29b6f6'},
    'rose-dark':     {'success': '#66bb6a', 'warning': '#ffa726', 'danger': '#f48fb1', 'info': '#4dd0e1'},
    'navy-dark':     {'success': '#26a69a', 'warning': '#ffa726', 'danger': '#ef5350', 'info': '#42a5f5'},
    'forest-dark':   {'success': '#69f0ae', 'warning': '#ffee58', 'danger': '#ff5252', 'info': '#40c4ff'},
    'midnight-dark': {'success': '#26a69a', 'warning': '#ffd54f', 'danger': '#ef5350', 'info': '#9c6fe4'},
    'amber-dark':    {'success': '#66bb6a', 'warning': '#ffb300', 'danger': '#ef5350', 'info': '#4dd0e1'},
    'teal-dark':     {'success': '#69f0ae', 'warning': '#ffa726', 'danger': '#ef5350', 'info': '#26c6da'},
    'violet-dark':   {'success': '#66bb6a', 'warning': '#ffa726', 'danger': '#ef5350', 'info': '#ce93d8'},
    # ── Light themes ──────────────────────────────────────────────────────
    'steel-light':    {'success': '#2e8b57', 'warning': '#c87000', 'danger': '#cc2200', 'info': '#1a7aaa'},
    'charcoal-light': {'success': '#1a8a60', 'warning': '#b07000', 'danger': '#c0392b', 'info': '#1a6fa8'},
    'crimson-light':  {'success': '#1a7a40', 'warning': '#b06000', 'danger': '#c0182e', 'info': '#1a5aaa'},
    'rose-light':     {'success': '#2e7d32', 'warning': '#e65100', 'danger': '#c2185b', 'info': '#0277bd'},
    'navy-light':     {'success': '#1a6a40', 'warning': '#a06000', 'danger': '#aa1010', 'info': '#1a3a8a'},
    'forest-light':   {'success': '#1b5e20', 'warning': '#e65100', 'danger': '#b71c1c', 'info': '#006064'},
    'midnight-light': {'success': '#1a6a40', 'warning': '#a06000', 'danger': '#aa1010', 'info': '#4a148c'},
    'amber-light':    {'success': '#1a6a40', 'warning': '#92400e', 'danger': '#aa1010', 'info': '#0a5a9a'},
    'teal-light':     {'success': '#1a6a40', 'warning': '#a06000', 'danger': '#aa1010', 'info': '#00696f'},
    'violet-light':   {'success': '#1a6a40', 'warning': '#a06000', 'danger': '#aa1010', 'info': '#6a1b9a'},
}

_DEFAULT_DARK  = {'success': '#5cb85c', 'warning': '#f0ad4e', 'danger': '#e05c5c', 'info': '#5bc0de'}
_DEFAULT_LIGHT = {'success': '#1a7a40', 'warning': '#b06000', 'danger': '#b03030', 'info': '#1a5aaa'}

_DARK_THEMES = {
    'steel-dark', 'charcoal-dark', 'crimson-dark', 'rose-dark', 'navy-dark',
    'forest-dark', 'midnight-dark', 'amber-dark', 'teal-dark', 'violet-dark',
}

# ── Muted colors (for supporting text, hints) ─────────────────────────────────
_THEME_MUTED = {
    # dark — lighter muted tones visible on dark bg
    'steel-dark':    '#8aa8c0',
    'charcoal-dark': '#9a9a9a',
    'crimson-dark':  '#c08090',
    'rose-dark':     '#c090a8',
    'navy-dark':     '#7a9ac0',
    'forest-dark':   '#7ab090',
    'midnight-dark': '#9a88c8',
    'amber-dark':    '#c0a060',
    'teal-dark':     '#60b0b8',
    'violet-dark':   '#b090c8',
    # light — darker muted tones visible on white bg
    'steel-light':    '#6a8090',
    'charcoal-light': '#6c757d',
    'crimson-light':  '#906070',
    'rose-light':     '#a06080',
    'navy-light':     '#5070a0',
    'forest-light':   '#407050',
    'midnight-light': '#705090',
    'amber-light':    '#906040',
    'teal-light':     '#307070',
    'violet-light':   '#705090',
}

# ── Tree row tag colors ───────────────────────────────────────────────────────
# Solid dark backgrounds + white text for all themes (dark and light).
# red=danger/expired/due  yellow=warning/near-expiry/partial  green=ok/cleared
# Colors chosen so white text passes 4.5:1+ contrast on each bg.

_SOLID = {
    'red':    {'bg': '#8b1a1a', 'fg': '#ffffff'},   # dark crimson  — white 7.2:1
    'yellow': {'bg': '#7a5500', 'fg': '#ffffff'},   # dark amber    — white 7.8:1
    'green':  {'bg': '#1a6b2a', 'fg': '#ffffff'},   # dark forest   — white 7.0:1
}

_TREE_ROW = {
    'due_bg':     _SOLID['red']['bg'],
    'due_fg':     _SOLID['red']['fg'],
    'partial_bg': _SOLID['yellow']['bg'],
    'partial_fg': _SOLID['yellow']['fg'],
    'cleared_bg': _SOLID['green']['bg'],
    'cleared_fg': _SOLID['green']['fg'],
}

# Same solid palette for every theme — identity is in the accent, not the status rows
_THEME_TREE_TAGS = {t: _TREE_ROW for t in [
    'steel-dark','charcoal-dark','crimson-dark','rose-dark','navy-dark',
    'forest-dark','midnight-dark','amber-dark','teal-dark','violet-dark',
    'steel-light','charcoal-light','crimson-light','rose-light','navy-light',
    'forest-light','midnight-light','amber-light','teal-light','violet-light',
]}

_DEFAULT_TREE_DARK  = _TREE_ROW
_DEFAULT_TREE_LIGHT = _TREE_ROW


# ── Public API ────────────────────────────────────────────────────────────────

def _current_theme():
    try:
        import ttkbootstrap as ttk
        return ttk.Style().theme_use()
    except Exception:
        return None


def get_alert_color(alert_type, theme=None):
    if theme is None:
        theme = _current_theme()
    palette = _THEME_ALERT_COLORS.get(theme)
    if palette is None:
        palette = _DEFAULT_DARK if theme in _DARK_THEMES else _DEFAULT_LIGHT
    if alert_type == 'muted':
        return _THEME_MUTED.get(theme, '#888888')
    return palette.get(alert_type, '#000000')


def get_tree_tag_colors(theme=None):
    if theme is None:
        theme = _current_theme()
    clr = _THEME_TREE_TAGS.get(theme)
    if clr is None:
        clr = _DEFAULT_TREE_DARK if theme in _DARK_THEMES else _DEFAULT_TREE_LIGHT
    return clr


def get_muted_color(theme=None):
    if theme is None:
        theme = _current_theme()
    return _THEME_MUTED.get(theme, '#888888')


def apply_alert_colors_to_theme():
    try:
        import ttkbootstrap as ttk
        style = ttk.Style()
        theme = style.theme_use()
        palette = _THEME_ALERT_COLORS.get(theme, _DEFAULT_LIGHT)
        for alert, color in palette.items():
            style.configure(f'{alert.capitalize()}.TLabel',  foreground=color)
            style.configure(f'{alert.capitalize()}.TButton', foreground=color)
    except Exception:
        pass
