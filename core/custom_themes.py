"""
core/custom_themes.py
─────────────────────
Custom ttkbootstrap theme definitions for Satpuda Core.
Injects into ttkbootstrap.themes.user.USER_THEMES before window creation.

Custom themes (10 total):
  Dark  → crimson, rose, navy, forest, midnight
  Light → crimson-light, rose-light, navy-light, forest-light, amber-light
"""

CUSTOM_THEMES = {

    # ══════════════════════════════════════════════════════════════════════
    # DARK THEMES
    # ══════════════════════════════════════════════════════════════════════

    # Deep blood-red dark theme
    'crimson': {
        'type': 'dark',
        'colors': {
            'primary':   '#dc143c',
            'secondary': '#9b1a2a',
            'success':   '#3ddc84',
            'info':      '#29b6f6',
            'warning':   '#ffca28',
            'danger':    '#ff5252',
            'light':     '#f5e6e8',
            'dark':      '#1a0a0a',
            'bg':        '#1c0d0f',
            'fg':        '#f5e6e8',
            'selectbg':  '#8b0000',
            'selectfg':  '#ffffff',
            'border':    '#4a1a1a',
            'inputfg':   '#f5e6e8',
            'inputbg':   '#2d1215',
            'active':    '#a01030',
        }
    },

    # Hot pink / magenta rose dark theme
    'rose': {
        'type': 'dark',
        'colors': {
            'primary':   '#e91e63',
            'secondary': '#ad1457',
            'success':   '#4caf50',
            'info':      '#00bcd4',
            'warning':   '#ff9800',
            'danger':    '#f44336',
            'light':     '#fce4ec',
            'dark':      '#1a0814',
            'bg':        '#1c0d18',
            'fg':        '#f8e8f0',
            'selectbg':  '#c2185b',
            'selectfg':  '#ffffff',
            'border':    '#4a1a2a',
            'inputfg':   '#f8e8f0',
            'inputbg':   '#2d1220',
            'active':    '#d81b60',
        }
    },

    # Deep navy blue dark theme
    'navy': {
        'type': 'dark',
        'colors': {
            'primary':   '#4a90d9',
            'secondary': '#2c5f8a',
            'success':   '#10b981',
            'info':      '#38bdf8',
            'warning':   '#fbbf24',
            'danger':    '#f87171',
            'light':     '#e0e7ff',
            'dark':      '#0a0f1a',
            'bg':        '#0d1526',
            'fg':        '#e8eef8',
            'selectbg':  '#1e40af',
            'selectfg':  '#ffffff',
            'border':    '#1e3a5f',
            'inputfg':   '#e8eef8',
            'inputbg':   '#12182d',
            'active':    '#2563eb',
        }
    },

    # Deep forest green dark theme
    'forest': {
        'type': 'dark',
        'colors': {
            'primary':   '#22c55e',
            'secondary': '#16a34a',
            'success':   '#4ade80',
            'info':      '#22d3ee',
            'warning':   '#facc15',
            'danger':    '#f87171',
            'light':     '#d1fae5',
            'dark':      '#0a1a0f',
            'bg':        '#0d1f14',
            'fg':        '#e8f8f0',
            'selectbg':  '#15803d',
            'selectfg':  '#ffffff',
            'border':    '#1a4a2a',
            'inputfg':   '#e8f8f0',
            'inputbg':   '#122d1a',
            'active':    '#16a34a',
        }
    },

    # Pure black / midnight dark theme
    'midnight': {
        'type': 'dark',
        'colors': {
            'primary':   '#7c3aed',
            'secondary': '#5b21b6',
            'success':   '#10b981',
            'info':      '#06b6d4',
            'warning':   '#f59e0b',
            'danger':    '#ef4444',
            'light':     '#ede9fe',
            'dark':      '#09090b',
            'bg':        '#09090b',
            'fg':        '#fafafa',
            'selectbg':  '#6d28d9',
            'selectfg':  '#ffffff',
            'border':    '#27272a',
            'inputfg':   '#fafafa',
            'inputbg':   '#18181b',
            'active':    '#7c3aed',
        }
    },

    # ══════════════════════════════════════════════════════════════════════
    # LIGHT THEMES
    # ══════════════════════════════════════════════════════════════════════

    # Crimson red light theme
    'crimson-light': {
        'type': 'light',
        'colors': {
            'primary':   '#c41e3a',
            'secondary': '#8b0000',
            'success':   '#198754',
            'info':      '#0d6efd',
            'warning':   '#fd7e14',
            'danger':    '#dc3545',
            'light':     '#fff5f5',
            'dark':      '#212529',
            'bg':        '#ffffff',
            'fg':        '#212529',
            'selectbg':  '#c41e3a',
            'selectfg':  '#ffffff',
            'border':    '#f5c6cb',
            'inputfg':   '#212529',
            'inputbg':   '#fff5f5',
            'active':    '#f8d7da',
        }
    },

    # Rose pink light theme
    'rose-light': {
        'type': 'light',
        'colors': {
            'primary':   '#d81b60',
            'secondary': '#880e4f',
            'success':   '#388e3c',
            'info':      '#0288d1',
            'warning':   '#f57c00',
            'danger':    '#c62828',
            'light':     '#fce4ec',
            'dark':      '#212121',
            'bg':        '#ffffff',
            'fg':        '#212121',
            'selectbg':  '#d81b60',
            'selectfg':  '#ffffff',
            'border':    '#f8bbd0',
            'inputfg':   '#212121',
            'inputbg':   '#fff0f5',
            'active':    '#fce4ec',
        }
    },

    # Navy blue light theme
    'navy-light': {
        'type': 'light',
        'colors': {
            'primary':   '#1e40af',
            'secondary': '#1e3a8a',
            'success':   '#059669',
            'info':      '#0284c7',
            'warning':   '#d97706',
            'danger':    '#dc2626',
            'light':     '#eff6ff',
            'dark':      '#1e293b',
            'bg':        '#ffffff',
            'fg':        '#1e293b',
            'selectbg':  '#1e40af',
            'selectfg':  '#ffffff',
            'border':    '#bfdbfe',
            'inputfg':   '#1e293b',
            'inputbg':   '#f0f7ff',
            'active':    '#dbeafe',
        }
    },

    # Forest green light theme
    'forest-light': {
        'type': 'light',
        'colors': {
            'primary':   '#15803d',
            'secondary': '#14532d',
            'success':   '#16a34a',
            'info':      '#0891b2',
            'warning':   '#ca8a04',
            'danger':    '#dc2626',
            'light':     '#f0fdf4',
            'dark':      '#1f2937',
            'bg':        '#ffffff',
            'fg':        '#1f2937',
            'selectbg':  '#15803d',
            'selectfg':  '#ffffff',
            'border':    '#bbf7d0',
            'inputfg':   '#1f2937',
            'inputbg':   '#f0fdf4',
            'active':    '#dcfce7',
        }
    },

    # Warm amber / gold light theme
    'amber-light': {
        'type': 'light',
        'colors': {
            'primary':   '#b45309',
            'secondary': '#92400e',
            'success':   '#15803d',
            'info':      '#0369a1',
            'warning':   '#d97706',
            'danger':    '#dc2626',
            'light':     '#fffbeb',
            'dark':      '#1c1917',
            'bg':        '#ffffff',
            'fg':        '#1c1917',
            'selectbg':  '#b45309',
            'selectfg':  '#ffffff',
            'border':    '#fde68a',
            'inputfg':   '#1c1917',
            'inputbg':   '#fffbeb',
            'active':    '#fef3c7',
        }
    },
}


def register_custom_themes():
    """
    Inject all custom themes into ttkbootstrap's USER_THEMES dict.
    Must be called BEFORE creating the ttk.Window.
    """
    try:
        from ttkbootstrap.themes.user import USER_THEMES
        for name, definition in CUSTOM_THEMES.items():
            USER_THEMES[name] = definition
    except Exception as e:
        print(f"[custom_themes] registration failed: {e}")
