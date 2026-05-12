"""
core/custom_themes.py
─────────────────────
All 20 Satpuda Core themes — fully custom, high-contrast, production-ready.
Injects into ttkbootstrap.themes.user.USER_THEMES before window creation.

10 Dark  + 10 Light  (perfectly paired, same count)
────────────────────────────────────────────────────
Dark:   steel · charcoal · crimson · rose · navy · forest · midnight · amber · teal · violet
Light:  steel · charcoal · crimson · rose · navy · forest · midnight · amber · teal · violet
"""

CUSTOM_THEMES = {

    # ══════════════════════════════════════════════════════════════════════
    # DARK THEMES  (10)
    # ══════════════════════════════════════════════════════════════════════

    # 1. Steel — cool blue-grey dark (replaces superhero)
    'steel-dark': {
        'type': 'dark',
        'colors': {
            'primary':   '#4e9af1',   # bright sky blue
            'secondary': '#6c757d',
            'success':   '#5cb85c',
            'info':      '#5bc0de',
            'warning':   '#f0ad4e',
            'danger':    '#e05c5c',
            'light':     '#e8edf2',
            'dark':      '#0f1923',
            'bg':        '#1b2838',   # steam-dark blue-grey
            'fg':        '#c7d5e0',
            'selectbg':  '#4e9af1',
            'selectfg':  '#ffffff',
            'border':    '#2a475e',
            'inputfg':   '#c7d5e0',
            'inputbg':   '#243447',
            'active':    '#2a475e',
        }
    },

    # 2. Charcoal — pure dark grey (replaces darkly)
    'charcoal-dark': {
        'type': 'dark',
        'colors': {
            'primary':   '#375a7f',   # muted blue
            'secondary': '#444444',
            'success':   '#00bc8c',
            'info':      '#3498db',
            'warning':   '#f39c12',
            'danger':    '#e74c3c',
            'light':     '#adb5bd',
            'dark':      '#1a1a1a',
            'bg':        '#222222',
            'fg':        '#e0e0e0',
            'selectbg':  '#375a7f',
            'selectfg':  '#ffffff',
            'border':    '#3a3a3a',
            'inputfg':   '#e0e0e0',
            'inputbg':   '#2d2d2d',
            'active':    '#2e4a6a',
        }
    },

    # 3. Crimson — deep blood red dark
    'crimson-dark': {
        'type': 'dark',
        'colors': {
            'primary':   '#e8365d',   # vivid crimson
            'secondary': '#9b1a2a',
            'success':   '#3ddc84',
            'info':      '#29b6f6',
            'warning':   '#ffca28',
            'danger':    '#ff5252',
            'light':     '#f5e6e8',
            'dark':      '#160a0c',
            'bg':        '#1e0d10',
            'fg':        '#f5e0e3',
            'selectbg':  '#9b1a2a',
            'selectfg':  '#ffffff',
            'border':    '#4a1520',
            'inputfg':   '#f5e0e3',
            'inputbg':   '#2d1318',
            'active':    '#7a1020',
        }
    },

    # 4. Rose — hot pink / magenta dark
    'rose-dark': {
        'type': 'dark',
        'colors': {
            'primary':   '#f06292',   # bright rose
            'secondary': '#ad1457',
            'success':   '#66bb6a',
            'info':      '#4dd0e1',
            'warning':   '#ffa726',
            'danger':    '#ef5350',
            'light':     '#fce4ec',
            'dark':      '#160810',
            'bg':        '#1e0d18',
            'fg':        '#fce4ec',
            'selectbg':  '#c2185b',
            'selectfg':  '#ffffff',
            'border':    '#4a1530',
            'inputfg':   '#fce4ec',
            'inputbg':   '#2d1222',
            'active':    '#880e4f',
        }
    },

    # 5. Navy — deep ocean blue dark
    'navy-dark': {
        'type': 'dark',
        'colors': {
            'primary':   '#5b9bd5',   # clear blue
            'secondary': '#2c5f8a',
            'success':   '#26a69a',
            'info':      '#42a5f5',
            'warning':   '#ffa726',
            'danger':    '#ef5350',
            'light':     '#e3f2fd',
            'dark':      '#080f1a',
            'bg':        '#0d1b2e',
            'fg':        '#dce8f5',
            'selectbg':  '#1e4080',
            'selectfg':  '#ffffff',
            'border':    '#1a3560',
            'inputfg':   '#dce8f5',
            'inputbg':   '#122040',
            'active':    '#1a3a70',
        }
    },

    # 6. Forest — deep green dark
    'forest-dark': {
        'type': 'dark',
        'colors': {
            'primary':   '#4caf50',   # vivid green
            'secondary': '#2e7d32',
            'success':   '#69f0ae',
            'info':      '#40c4ff',
            'warning':   '#ffee58',
            'danger':    '#ff5252',
            'light':     '#e8f5e9',
            'dark':      '#081408',
            'bg':        '#0d1f0e',
            'fg':        '#e0f2e1',
            'selectbg':  '#2e7d32',
            'selectfg':  '#ffffff',
            'border':    '#1b4a1c',
            'inputfg':   '#e0f2e1',
            'inputbg':   '#122a13',
            'active':    '#1b5e20',
        }
    },

    # 7. Midnight — pure black + violet dark
    'midnight-dark': {
        'type': 'dark',
        'colors': {
            'primary':   '#9c6fe4',   # soft violet
            'secondary': '#5e35b1',
            'success':   '#26a69a',
            'info':      '#29b6f6',
            'warning':   '#ffd54f',
            'danger':    '#ef5350',
            'light':     '#ede7f6',
            'dark':      '#080808',
            'bg':        '#0e0e12',
            'fg':        '#f0eeff',
            'selectbg':  '#6d28d9',
            'selectfg':  '#ffffff',
            'border':    '#2a2040',
            'inputfg':   '#f0eeff',
            'inputbg':   '#18161e',
            'active':    '#4a1d96',
        }
    },

    # 8. Amber — warm orange-gold dark
    'amber-dark': {
        'type': 'dark',
        'colors': {
            'primary':   '#ffb300',   # amber gold
            'secondary': '#e65100',
            'success':   '#66bb6a',
            'info':      '#4dd0e1',
            'warning':   '#ffd54f',
            'danger':    '#ef5350',
            'light':     '#fff8e1',
            'dark':      '#1a1000',
            'bg':        '#1e1500',
            'fg':        '#fff8e1',
            'selectbg':  '#e65100',
            'selectfg':  '#ffffff',
            'border':    '#4a3000',
            'inputfg':   '#fff8e1',
            'inputbg':   '#2a1e00',
            'active':    '#bf360c',
        }
    },

    # 9. Teal — cyan-teal dark
    'teal-dark': {
        'type': 'dark',
        'colors': {
            'primary':   '#26c6da',   # bright teal
            'secondary': '#00838f',
            'success':   '#66bb6a',
            'info':      '#42a5f5',
            'warning':   '#ffa726',
            'danger':    '#ef5350',
            'light':     '#e0f7fa',
            'dark':      '#001a1e',
            'bg':        '#00202a',
            'fg':        '#e0f7fa',
            'selectbg':  '#00838f',
            'selectfg':  '#ffffff',
            'border':    '#004a55',
            'inputfg':   '#e0f7fa',
            'inputbg':   '#002d38',
            'active':    '#006064',
        }
    },

    # 10. Violet — deep purple dark
    'violet-dark': {
        'type': 'dark',
        'colors': {
            'primary':   '#ce93d8',   # soft lavender
            'secondary': '#7b1fa2',
            'success':   '#66bb6a',
            'info':      '#4dd0e1',
            'warning':   '#ffa726',
            'danger':    '#ef5350',
            'light':     '#f3e5f5',
            'dark':      '#120018',
            'bg':        '#180020',
            'fg':        '#f3e5f5',
            'selectbg':  '#7b1fa2',
            'selectfg':  '#ffffff',
            'border':    '#3a0050',
            'inputfg':   '#f3e5f5',
            'inputbg':   '#22002e',
            'active':    '#4a0072',
        }
    },

    # ══════════════════════════════════════════════════════════════════════
    # LIGHT THEMES  (10)
    # ══════════════════════════════════════════════════════════════════════

    # 1. Steel — clean blue-grey light (replaces cosmo/yeti)
    'steel-light': {
        'type': 'light',
        'colors': {
            'primary':   '#2780e3',   # clear blue
            'secondary': '#5a6a7a',
            'success':   '#2e8b57',
            'info':      '#1a7aaa',
            'warning':   '#c87000',
            'danger':    '#cc2200',
            'light':     '#f0f4f8',
            'dark':      '#1a2530',
            'bg':        '#f8fafc',
            'fg':        '#1a2530',
            'selectbg':  '#2780e3',
            'selectfg':  '#ffffff',
            'border':    '#c8d8e8',
            'inputfg':   '#1a2530',
            'inputbg':   '#ffffff',
            'active':    '#e0ecf8',
        }
    },

    # 2. Charcoal — warm grey light (replaces flatly)
    'charcoal-light': {
        'type': 'light',
        'colors': {
            'primary':   '#3a5a7a',   # slate blue
            'secondary': '#6c757d',
            'success':   '#1a8a60',
            'info':      '#1a6fa8',
            'warning':   '#b07000',
            'danger':    '#c0392b',
            'light':     '#f5f5f5',
            'dark':      '#2c2c2c',
            'bg':        '#ffffff',
            'fg':        '#2c2c2c',
            'selectbg':  '#3a5a7a',
            'selectfg':  '#ffffff',
            'border':    '#d0d0d0',
            'inputfg':   '#2c2c2c',
            'inputbg':   '#fafafa',
            'active':    '#e8e8e8',
        }
    },

    # 3. Crimson — red accent light
    'crimson-light': {
        'type': 'light',
        'colors': {
            'primary':   '#c0182e',   # deep crimson
            'secondary': '#7a0010',
            'success':   '#1a7a40',
            'info':      '#1a5aaa',
            'warning':   '#b06000',
            'danger':    '#b01020',
            'light':     '#fff0f2',
            'dark':      '#1e0a0c',
            'bg':        '#ffffff',
            'fg':        '#1e0a0c',
            'selectbg':  '#c0182e',
            'selectfg':  '#ffffff',
            'border':    '#f0c0c8',
            'inputfg':   '#1e0a0c',
            'inputbg':   '#fff8f9',
            'active':    '#fde8ea',
        }
    },

    # 4. Rose — pink accent light
    'rose-light': {
        'type': 'light',
        'colors': {
            'primary':   '#c2185b',   # deep rose
            'secondary': '#7a0040',
            'success':   '#2e7d32',
            'info':      '#0277bd',
            'warning':   '#e65100',
            'danger':    '#b71c1c',
            'light':     '#fff0f5',
            'dark':      '#1a0818',
            'bg':        '#ffffff',
            'fg':        '#1a0818',
            'selectbg':  '#c2185b',
            'selectfg':  '#ffffff',
            'border':    '#f8bbd0',
            'inputfg':   '#1a0818',
            'inputbg':   '#fff5f8',
            'active':    '#fce4ec',
        }
    },

    # 5. Navy — blue accent light
    'navy-light': {
        'type': 'light',
        'colors': {
            'primary':   '#1a3a8a',   # deep navy
            'secondary': '#0d2060',
            'success':   '#1a6a40',
            'info':      '#0a5a9a',
            'warning':   '#a06000',
            'danger':    '#aa1010',
            'light':     '#f0f4ff',
            'dark':      '#0a1030',
            'bg':        '#ffffff',
            'fg':        '#0a1030',
            'selectbg':  '#1a3a8a',
            'selectfg':  '#ffffff',
            'border':    '#c0d0f0',
            'inputfg':   '#0a1030',
            'inputbg':   '#f5f8ff',
            'active':    '#dce8ff',
        }
    },

    # 6. Forest — green accent light
    'forest-light': {
        'type': 'light',
        'colors': {
            'primary':   '#1b5e20',   # deep forest
            'secondary': '#0a3a10',
            'success':   '#2e7d32',
            'info':      '#006064',
            'warning':   '#e65100',
            'danger':    '#b71c1c',
            'light':     '#f0fdf0',
            'dark':      '#0a1a0a',
            'bg':        '#ffffff',
            'fg':        '#0a1a0a',
            'selectbg':  '#1b5e20',
            'selectfg':  '#ffffff',
            'border':    '#c8e6c9',
            'inputfg':   '#0a1a0a',
            'inputbg':   '#f5fdf5',
            'active':    '#dcedc8',
        }
    },

    # 7. Midnight — violet accent light
    'midnight-light': {
        'type': 'light',
        'colors': {
            'primary':   '#4a148c',   # deep violet
            'secondary': '#2a0060',
            'success':   '#1a6a40',
            'info':      '#0a5a9a',
            'warning':   '#a06000',
            'danger':    '#aa1010',
            'light':     '#f5f0ff',
            'dark':      '#100820',
            'bg':        '#ffffff',
            'fg':        '#100820',
            'selectbg':  '#4a148c',
            'selectfg':  '#ffffff',
            'border':    '#d8c8f0',
            'inputfg':   '#100820',
            'inputbg':   '#faf5ff',
            'active':    '#ede7f6',
        }
    },

    # 8. Amber — warm gold accent light
    'amber-light': {
        'type': 'light',
        'colors': {
            'primary':   '#b45309',   # deep amber
            'secondary': '#7c2d00',
            'success':   '#1a6a40',
            'info':      '#0a5a9a',
            'warning':   '#92400e',
            'danger':    '#aa1010',
            'light':     '#fffbf0',
            'dark':      '#1c1000',
            'bg':        '#ffffff',
            'fg':        '#1c1000',
            'selectbg':  '#b45309',
            'selectfg':  '#ffffff',
            'border':    '#fde68a',
            'inputfg':   '#1c1000',
            'inputbg':   '#fffdf5',
            'active':    '#fef3c7',
        }
    },

    # 9. Teal — cyan accent light
    'teal-light': {
        'type': 'light',
        'colors': {
            'primary':   '#00696f',   # deep teal
            'secondary': '#004a50',
            'success':   '#1a6a40',
            'info':      '#0a5a9a',
            'warning':   '#a06000',
            'danger':    '#aa1010',
            'light':     '#f0fffe',
            'dark':      '#001a1e',
            'bg':        '#ffffff',
            'fg':        '#001a1e',
            'selectbg':  '#00696f',
            'selectfg':  '#ffffff',
            'border':    '#b2dfdb',
            'inputfg':   '#001a1e',
            'inputbg':   '#f5ffff',
            'active':    '#e0f2f1',
        }
    },

    # 10. Violet — purple accent light
    'violet-light': {
        'type': 'light',
        'colors': {
            'primary':   '#6a1b9a',   # deep purple
            'secondary': '#4a0070',
            'success':   '#1a6a40',
            'info':      '#0a5a9a',
            'warning':   '#a06000',
            'danger':    '#aa1010',
            'light':     '#fdf5ff',
            'dark':      '#120018',
            'bg':        '#ffffff',
            'fg':        '#120018',
            'selectbg':  '#6a1b9a',
            'selectfg':  '#ffffff',
            'border':    '#e1bee7',
            'inputfg':   '#120018',
            'inputbg':   '#fdf8ff',
            'active':    '#f3e5f5',
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
