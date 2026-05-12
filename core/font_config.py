# Font Size Configuration for Veterinary Management System
# Modify these values to change font sizes throughout the application

import os
import sys

def _get_font_size_path():
    """Return path to font_size.txt — exe-aware."""
    if getattr(sys, 'frozen', False):
        return os.path.join(
            os.environ.get('LOCALAPPDATA', os.path.expanduser('~')),
            'VeterinaryApp', 'font_size.txt')
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'config', 'font_size.txt')

def _load_font_size():
    """Load base font size from settings file, default 10."""
    try:
        path = _get_font_size_path()
        if os.path.exists(path):
            val = int(open(path).read().strip())
            if 7 <= val <= 20:
                return val
    except Exception:
        pass
    return 10

_BASE = _load_font_size()

# Main Titles and Headers
FONT_SIZE_MAIN_TITLE      = _BASE + 18   # Main welcome title
FONT_SIZE_SECTION_TITLE   = _BASE + 2    # Section headings/subtitles
FONT_SIZE_LABELS          = _BASE        # Regular labels
FONT_SIZE_SUPPORTING_TEXT = _BASE + 1    # Version, help text, etc.

# Interactive Elements
FONT_SIZE_BUTTONS         = _BASE        # Regular buttons
FONT_SIZE_NAV_BUTTONS     = _BASE + 1    # Navigation buttons
FONT_SIZE_ENTRIES         = _BASE        # Entry fields and text inputs
FONT_SIZE_DROPDOWNS       = _BASE        # Combobox dropdowns

# Data Display
FONT_SIZE_TABLES          = _BASE - 1    # Treeview/table content
FONT_SIZE_TABLE_HEADERS   = _BASE        # Treeview/table headers

# System Defaults
FONT_SIZE_DEFAULT         = _BASE        # Default application font
FONT_FAMILY               = 'Segoe UI'   # Default font family
