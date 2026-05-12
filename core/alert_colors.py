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


def get_alert_color(alert_type, theme=None):
    if theme is None:
        try:
            import ttkbootstrap as ttk
            theme = ttk.Style().theme_use()
        except Exception:
            pass
    palette = _THEME_ALERT_COLORS.get(theme)
    if palette is None:
        palette = _DEFAULT_DARK if theme in ('superhero','darkly','cyborg','solar','vapor') \
                  else _DEFAULT_LIGHT
    return palette.get(alert_type, '#000000')


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
