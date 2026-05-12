"""
core/alert_colors.py
────────────────────
Per-theme contrast-correct alert colors.
Colors chosen based on each theme's actual bg/fg/inputbg values.
"""

# Per-theme overrides: (success, warning, danger, info)
# Dark themes  → lighter/brighter tones so text is visible on dark bg
# Light themes → darker/saturated tones so text is visible on white/light bg
_THEME_ALERT_COLORS = {
    # ── Dark themes ───────────────────────────────────────────────────────
    'superhero': {'success': '#5cb85c', 'warning': '#f0ad4e', 'danger': '#d9534f', 'info': '#5bc0de'},
    'darkly':    {'success': '#00bc8c', 'warning': '#f39c12', 'danger': '#e74c3c', 'info': '#3498db'},
    'cyborg':    {'success': '#77b300', 'warning': '#ff8800', 'danger': '#cc0000', 'info': '#2a9fd6'},
    'solar':     {'success': '#44aca4', 'warning': '#d05e2f', 'danger': '#d95092', 'info': '#3f98d7'},
    'vapor':     {'success': '#3af180', 'warning': '#ffbd05', 'danger': '#e34b54', 'info': '#1da2f2'},
    # ── Light themes ──────────────────────────────────────────────────────
    'cosmo':     {'success': '#1a7a00', 'warning': '#c45a00', 'danger': '#cc0030', 'info': '#1a5faa'},
    'flatly':    {'success': '#0e8a6e', 'warning': '#b07000', 'danger': '#c0392b', 'info': '#1a6fa8'},
    'litera':    {'success': '#017a50', 'warning': '#b07800', 'danger': '#b03030', 'info': '#0e7a8a'},
    'minty':     {'success': '#2a9d6e', 'warning': '#c07000', 'danger': '#d04020', 'info': '#2a7a8a'},
    'morph':     {'success': '#1e8a10', 'warning': '#a07000', 'danger': '#c01010', 'info': '#2040c0'},
    'pulse':     {'success': '#0a8a3a', 'warning': '#b07800', 'danger': '#cc1010', 'info': '#006aaa'},
    'sandstone': {'success': '#5a8a20', 'warning': '#b05010', 'danger': '#b03030', 'info': '#1a5080'},
    'simplex':   {'success': '#2a7000', 'warning': '#a06000', 'danger': '#8a0000', 'info': '#006a9a'},
    'united':    {'success': '#1a7a30', 'warning': '#b06000', 'danger': '#b02020', 'info': '#0a7a8a'},
    'yeti':      {'success': '#2a7a4a', 'warning': '#b07000', 'danger': '#c03010', 'info': '#006a8a'},
    'cerculean': {'success': '#3a7a10', 'warning': '#b05000', 'danger': '#aa1010', 'info': '#1a4a80'},
    'journal':   {'success': '#1a7a30', 'warning': '#8a7000', 'danger': '#c04000', 'info': '#1a4a80'},
    'lumen':     {'success': '#1a7a20', 'warning': '#b05800', 'danger': '#cc1010', 'info': '#0a6a8a'},
}

_DEFAULT_DARK  = {'success': '#5cb85c', 'warning': '#f0ad4e', 'danger': '#d9534f', 'info': '#5bc0de'}
_DEFAULT_LIGHT = {'success': '#1a7a30', 'warning': '#b06000', 'danger': '#b03030', 'info': '#0a6a8a'}

_DARK_THEMES = {
    'superhero','darkly','cyborg','solar','vapor',
    # custom dark
    'crimson','rose','navy','forest','midnight',
}

# Muted color per theme (for 'gray' text)
_THEME_MUTED = {
    'superhero': '#ABB6C2', 'darkly': '#ADB5BD', 'cyborg': '#ADAFAE',
    'solar': '#A9BDBD',     'vapor': '#bfb6cd',
    'cosmo': '#7E8081',     'flatly': '#95a5a6',  'litera': '#adb5bd',
    'minty': '#5a5a5a',     'morph': '#7B8AB8',   'pulse': '#444444',
    'sandstone': '#8e8c84', 'simplex': '#858e96',  'united': '#aea79f',
    'yeti': '#707070',      'cerculean': '#a9b4be','journal': '#aaaaaa',
    'lumen': '#919191',
    # custom dark
    'crimson':  '#c08090', 'rose':    '#c090a8', 'navy':    '#8090b8',
    'forest':   '#80b090', 'midnight':'#a090c8',
    # custom light
    'crimson-light': '#a06070', 'rose-light':   '#b06080',
    'navy-light':    '#6080b0', 'forest-light':  '#508060',
    'amber-light':   '#a07040',
}

# Per-theme alert colors for custom themes
_THEME_ALERT_COLORS.update({
    # custom dark
    'crimson':  {'success': '#3ddc84', 'warning': '#ffca28', 'danger': '#ff6b6b', 'info': '#29b6f6'},
    'rose':     {'success': '#4caf50', 'warning': '#ff9800', 'danger': '#ff5252', 'info': '#00bcd4'},
    'navy':     {'success': '#10b981', 'warning': '#fbbf24', 'danger': '#f87171', 'info': '#38bdf8'},
    'forest':   {'success': '#4ade80', 'warning': '#facc15', 'danger': '#f87171', 'info': '#22d3ee'},
    'midnight': {'success': '#10b981', 'warning': '#f59e0b', 'danger': '#ef4444', 'info': '#06b6d4'},
    # custom light
    'crimson-light': {'success': '#198754', 'warning': '#fd7e14', 'danger': '#c41e3a', 'info': '#0d6efd'},
    'rose-light':    {'success': '#388e3c', 'warning': '#f57c00', 'danger': '#d81b60', 'info': '#0288d1'},
    'navy-light':    {'success': '#059669', 'warning': '#d97706', 'danger': '#dc2626', 'info': '#1e40af'},
    'forest-light':  {'success': '#16a34a', 'warning': '#ca8a04', 'danger': '#dc2626', 'info': '#0891b2'},
    'amber-light':   {'success': '#15803d', 'warning': '#b45309', 'danger': '#dc2626', 'info': '#0369a1'},
})

# Tree row tag colors per theme
_THEME_TREE_TAGS = {
    # dark themes: subtle tinted rows with light text
    'superhero': {'due_bg':'#4a1a1a','due_fg':'#ff9999','cleared_bg':'#1a3a1a','cleared_fg':'#99dd99','partial_bg':'#3a3010','partial_fg':'#ffd080'},
    'darkly':    {'due_bg':'#4a1a1a','due_fg':'#ff9999','cleared_bg':'#1a3a1a','cleared_fg':'#99dd99','partial_bg':'#3a3010','partial_fg':'#ffd080'},
    'cyborg':    {'due_bg':'#3a0a0a','due_fg':'#ff8888','cleared_bg':'#0a2a0a','cleared_fg':'#88cc88','partial_bg':'#2a2000','partial_fg':'#ffcc66'},
    'solar':     {'due_bg':'#3a1010','due_fg':'#ff9999','cleared_bg':'#0a2a1a','cleared_fg':'#88ddaa','partial_bg':'#2a2010','partial_fg':'#ffcc88'},
    'vapor':     {'due_bg':'#3a0a1a','due_fg':'#ff88aa','cleared_bg':'#0a2a1a','cleared_fg':'#88ffcc','partial_bg':'#2a2000','partial_fg':'#ffdd44'},
    # light themes: Bootstrap-style pastel rows with dark text
    'cosmo':     {'due_bg':'#f8d7da','due_fg':'#721c24','cleared_bg':'#d4edda','cleared_fg':'#155724','partial_bg':'#fff3cd','partial_fg':'#856404'},
    'flatly':    {'due_bg':'#f8d7da','due_fg':'#721c24','cleared_bg':'#d4edda','cleared_fg':'#155724','partial_bg':'#fff3cd','partial_fg':'#856404'},
    'litera':    {'due_bg':'#f8d7da','due_fg':'#721c24','cleared_bg':'#d4edda','cleared_fg':'#155724','partial_bg':'#fff3cd','partial_fg':'#856404'},
    'minty':     {'due_bg':'#ffe0e0','due_fg':'#8b0000','cleared_bg':'#d4f5e9','cleared_fg':'#0a5c36','partial_bg':'#fff8d6','partial_fg':'#7a5c00'},
    'morph':     {'due_bg':'#f0d0d8','due_fg':'#800020','cleared_bg':'#cce8d8','cleared_fg':'#0a4a28','partial_bg':'#f0e8c0','partial_fg':'#604000'},
    'pulse':     {'due_bg':'#f8d7da','due_fg':'#721c24','cleared_bg':'#d4edda','cleared_fg':'#155724','partial_bg':'#fff3cd','partial_fg':'#856404'},
    'sandstone': {'due_bg':'#f8d7da','due_fg':'#721c24','cleared_bg':'#d4edda','cleared_fg':'#155724','partial_bg':'#fff3cd','partial_fg':'#856404'},
    'simplex':   {'due_bg':'#f8d7da','due_fg':'#721c24','cleared_bg':'#d4edda','cleared_fg':'#155724','partial_bg':'#fff3cd','partial_fg':'#856404'},
    'united':    {'due_bg':'#f8d7da','due_fg':'#721c24','cleared_bg':'#d4edda','cleared_fg':'#155724','partial_bg':'#fff3cd','partial_fg':'#856404'},
    'yeti':      {'due_bg':'#f8d7da','due_fg':'#721c24','cleared_bg':'#d4edda','cleared_fg':'#155724','partial_bg':'#fff3cd','partial_fg':'#856404'},
    'cerculean': {'due_bg':'#f8d7da','due_fg':'#721c24','cleared_bg':'#d4edda','cleared_fg':'#155724','partial_bg':'#fff3cd','partial_fg':'#856404'},
    'journal':   {'due_bg':'#f8d7da','due_fg':'#721c24','cleared_bg':'#d4edda','cleared_fg':'#155724','partial_bg':'#fff3cd','partial_fg':'#856404'},
    'lumen':     {'due_bg':'#f8d7da','due_fg':'#721c24','cleared_bg':'#d4edda','cleared_fg':'#155724','partial_bg':'#fff3cd','partial_fg':'#856404'},
}
_DEFAULT_TREE_LIGHT = {'due_bg':'#f8d7da','due_fg':'#721c24','cleared_bg':'#d4edda','cleared_fg':'#155724','partial_bg':'#fff3cd','partial_fg':'#856404'}
_DEFAULT_TREE_DARK  = {'due_bg':'#4a1a1a','due_fg':'#ff9999','cleared_bg':'#1a3a1a','cleared_fg':'#99dd99','partial_bg':'#3a3010','partial_fg':'#ffd080'}

# Tree tags for custom themes
_THEME_TREE_TAGS.update({
    'crimson':  {'due_bg':'#4a0a10','due_fg':'#ff9999','cleared_bg':'#0a3a1a','cleared_fg':'#99dd99','partial_bg':'#3a2a00','partial_fg':'#ffd080'},
    'rose':     {'due_bg':'#4a0a20','due_fg':'#ffaacc','cleared_bg':'#0a3a1a','cleared_fg':'#99dd99','partial_bg':'#3a2a00','partial_fg':'#ffd080'},
    'navy':     {'due_bg':'#1a1a4a','due_fg':'#aaaaff','cleared_bg':'#0a3a1a','cleared_fg':'#99dd99','partial_bg':'#3a3010','partial_fg':'#ffd080'},
    'forest':   {'due_bg':'#3a0a0a','due_fg':'#ff9999','cleared_bg':'#0a3a10','cleared_fg':'#88ff99','partial_bg':'#2a2a00','partial_fg':'#ffee66'},
    'midnight': {'due_bg':'#2a0a3a','due_fg':'#cc99ff','cleared_bg':'#0a2a1a','cleared_fg':'#88ffcc','partial_bg':'#2a2000','partial_fg':'#ffdd44'},
    'crimson-light':  {'due_bg':'#f8d7da','due_fg':'#721c24','cleared_bg':'#d4edda','cleared_fg':'#155724','partial_bg':'#fff3cd','partial_fg':'#856404'},
    'rose-light':     {'due_bg':'#fce4ec','due_fg':'#880e4f','cleared_bg':'#e8f5e9','cleared_fg':'#1b5e20','partial_bg':'#fff8e1','partial_fg':'#e65100'},
    'navy-light':     {'due_bg':'#dbeafe','due_fg':'#1e3a8a','cleared_bg':'#d1fae5','cleared_fg':'#065f46','partial_bg':'#fef3c7','partial_fg':'#92400e'},
    'forest-light':   {'due_bg':'#fee2e2','due_fg':'#7f1d1d','cleared_bg':'#dcfce7','cleared_fg':'#14532d','partial_bg':'#fef9c3','partial_fg':'#713f12'},
    'amber-light':    {'due_bg':'#fee2e2','due_fg':'#7f1d1d','cleared_bg':'#d1fae5','cleared_fg':'#065f46','partial_bg':'#fef3c7','partial_fg':'#92400e'},
})


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
    """Return dict with due_bg/fg, cleared_bg/fg, partial_bg/fg for current theme."""
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
