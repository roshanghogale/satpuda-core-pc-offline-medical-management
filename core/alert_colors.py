"""
Theme-aware alert colors for the veterinary management system
"""

# Universal dark colors for all themes
ALERT_COLORS = {
    'success': '#1b5e20',
    'warning': '#bf360c',
    'danger': '#8b0000',
    'info': '#0d47a1'
}

def get_alert_color(alert_type, theme=None):
    """Get dark alert color for all themes"""
    return ALERT_COLORS.get(alert_type, '#000000')

def apply_alert_colors_to_theme():
    """Apply alert colors to current theme styles"""
    try:
        import ttkbootstrap as ttk
        style = ttk.Style()
        
        # Configure alert styles for current theme
        style.configure('Success.TLabel', foreground=get_alert_color('success'))
        style.configure('Warning.TLabel', foreground=get_alert_color('warning'))
        style.configure('Danger.TLabel', foreground=get_alert_color('danger'))
        style.configure('Info.TLabel', foreground=get_alert_color('info'))
        
        style.configure('Success.TButton', foreground=get_alert_color('success'))
        style.configure('Warning.TButton', foreground=get_alert_color('warning'))
        style.configure('Danger.TButton', foreground=get_alert_color('danger'))
        style.configure('Info.TButton', foreground=get_alert_color('info'))
    except:
        pass