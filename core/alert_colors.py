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
# due = red tint, cleared = green tint, partial = yellow tint
_THEME_TREE_TAGS = {
    # dark themes — subtle tinted rows, bright readable text
    'steel-dark':    {'due_bg':'#3a1a1a','due_fg':'#ff9999','cleared_bg':'#1a3a1a','cleared_fg':'#88dd88','partial_bg':'#3a3010','partial_fg':'#ffd080'},
    'charcoal-dark': {'due_bg':'#4a1a1a','due_fg':'#ff9999','cleared_bg':'#1a3a1a','cleared_fg':'#99dd99','partial_bg':'#3a3010','partial_fg':'#ffd080'},
    'crimson-dark':  {'due_bg':'#4a0a10','due_fg':'#ffaaaa','cleared_bg':'#0a3a1a','cleared_fg':'#88ee88','partial_bg':'#3a2a00','partial_fg':'#ffd080'},
    'rose-dark':     {'due_bg':'#4a0a20','due_fg':'#ffaacc','cleared_bg':'#0a3a1a','cleared_fg':'#88ee88','partial_bg':'#3a2a00','partial_fg':'#ffd080'},
    'navy-dark':     {'due_bg':'#1a1a4a','due_fg':'#aaaaff','cleared_bg':'#0a3a20','cleared_fg':'#88ee88','partial_bg':'#3a3010','partial_fg':'#ffd080'},
    'forest-dark':   {'due_bg':'#3a0a0a','due_fg':'#ff9999','cleared_bg':'#0a3a10','cleared_fg':'#88ff99','partial_bg':'#2a2a00','partial_fg':'#ffee66'},
    'midnight-dark': {'due_bg':'#2a0a3a','due_fg':'#cc99ff','cleared_bg':'#0a2a1a','cleared_fg':'#88ffcc','partial_bg':'#2a2000','partial_fg':'#ffdd44'},
    'amber-dark':    {'due_bg':'#3a1000','due_fg':'#ffaa88','cleared_bg':'#0a2a10','cleared_fg':'#88ee88','partial_bg':'#3a2a00','partial_fg':'#ffdd44'},
    'teal-dark':     {'due_bg':'#2a0a0a','due_fg':'#ff9999','cleared_bg':'#003a3a','cleared_fg':'#88ffee','partial_bg':'#2a2a00','partial_fg':'#ffee66'},
    'violet-dark':   {'due_bg':'#2a0a2a','due_fg':'#ffaaff','cleared_bg':'#0a2a1a','cleared_fg':'#88ffcc','partial_bg':'#2a2000','partial_fg':'#ffdd44'},
    # light themes — pastel tinted rows, dark readable text
    'steel-light':    {'due_bg':'#fde8e8','due_fg':'#7a1010','cleared_bg':'#e8f5e8','cleared_fg':'#1a5a1a','partial_bg':'#fff8e0','partial_fg':'#7a5000'},
    'charcoal-light': {'due_bg':'#f8d7da','due_fg':'#721c24','cleared_bg':'#d4edda','cleared_fg':'#155724','partial_bg':'#fff3cd','partial_fg':'#856404'},
    'crimson-light':  {'due_bg':'#fde0e3','due_fg':'#7a0010','cleared_bg':'#e0f5e8','cleared_fg':'#1a5a20','partial_bg':'#fff8e0','partial_fg':'#7a5000'},
    'rose-light':     {'due_bg':'#fce4ec','due_fg':'#7a0030','cleared_bg':'#e8f5e9','cleared_fg':'#1b5e20','partial_bg':'#fff8e1','partial_fg':'#7a4000'},
    'navy-light':     {'due_bg':'#fde8e8','due_fg':'#7a1010','cleared_bg':'#e0f0ff','cleared_fg':'#0a2a6a','partial_bg':'#fff8e0','partial_fg':'#7a5000'},
    'forest-light':   {'due_bg':'#fee2e2','due_fg':'#7f1d1d','cleared_bg':'#dcfce7','cleared_fg':'#14532d','partial_bg':'#fef9c3','partial_fg':'#713f12'},
    'midnight-light': {'due_bg':'#fde8f8','due_fg':'#4a0060','cleared_bg':'#e8f5e8','cleared_fg':'#1a5a20','partial_bg':'#fff8e0','partial_fg':'#7a5000'},
    'amber-light':    {'due_bg':'#fee2e2','due_fg':'#7f1d1d','cleared_bg':'#d1fae5','cleared_fg':'#065f46','partial_bg':'#fef3c7','partial_fg':'#92400e'},
    'teal-light':     {'due_bg':'#fde8e8','due_fg':'#7a1010','cleared_bg':'#e0faf8','cleared_fg':'#004a50','partial_bg':'#fff8e0','partial_fg':'#7a5000'},
    'violet-light':   {'due_bg':'#fde8f8','due_fg':'#4a0060','cleared_bg':'#e8f5e8','cleared_fg':'#1a5a20','partial_bg':'#fff8e0','partial_fg':'#7a5000'},
}

_DEFAULT_TREE_DARK  = {'due_bg':'#3a1a1a','due_fg':'#ff9999','cleared_bg':'#1a3a1a','cleared_fg':'#88dd88','partial_bg':'#3a3010','partial_fg':'#ffd080'}
_DEFAULT_TREE_LIGHT = {'due_bg':'#fde8e8','due_fg':'#7a1010','cleared_bg':'#e8f5e8','cleared_fg':'#1a5a1a','partial_bg':'#fff8e0','partial_fg':'#7a5000'}


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
