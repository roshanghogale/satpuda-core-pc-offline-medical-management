"""
Font updater utility to apply font configurations to all UI elements
"""

from core.font_config import *

def apply_fonts_to_widget(widget, widget_type=None):
    """Apply appropriate font to a widget based on its type"""
    try:
        import ttkbootstrap as ttk
        
        if isinstance(widget, ttk.Treeview):
            # Update Treeview style with dynamic row height
            style = ttk.Style()
            screen_height = widget.winfo_screenheight()
            max_row_height = min(screen_height // 25, 50)
            table_row_height = min(max(FONT_SIZE_TABLES + 16, 28), max_row_height)
            style.configure('Large.Treeview', font=(FONT_FAMILY, FONT_SIZE_TABLES), rowheight=table_row_height)
            style.configure('Large.Treeview.Heading', font=(FONT_FAMILY, FONT_SIZE_TABLE_HEADERS, 'bold'))
            widget.configure(style='Large.Treeview')
        elif isinstance(widget, ttk.Label):
            widget.configure(style='Large.TLabel')
        elif isinstance(widget, ttk.Button):
            widget.configure(style='Large.TButton')
        elif isinstance(widget, ttk.Entry):
            widget.configure(style='Large.TEntry')
        elif isinstance(widget, ttk.Combobox):
            widget.configure(style='Large.TCombobox')
        elif isinstance(widget, ttk.LabelFrame):
            pass  # LabelFrame does not support style parameter in ttkbootstrap
            
    except ImportError:
        # Fallback for standard tkinter
        if hasattr(widget, 'configure'):
            try:
                if 'Treeview' in str(type(widget)):
                    from tkinter import ttk
                    style = ttk.Style()
                    screen_height = widget.winfo_screenheight()
                    max_row_height = min(screen_height // 25, 50)
                    table_row_height = min(max(FONT_SIZE_TABLES + 16, 28), max_row_height)
                    style.configure('Treeview', font=(FONT_FAMILY, FONT_SIZE_TABLES), rowheight=table_row_height)
                    style.configure('Treeview.Heading', font=(FONT_FAMILY, FONT_SIZE_TABLE_HEADERS, 'bold'))
                elif 'Label' in str(type(widget)):
                    widget.configure(font=(FONT_FAMILY, FONT_SIZE_LABELS))
                elif 'Button' in str(type(widget)):
                    widget.configure(font=(FONT_FAMILY, FONT_SIZE_BUTTONS))
                elif 'Entry' in str(type(widget)):
                    widget.configure(font=(FONT_FAMILY, FONT_SIZE_ENTRIES))
            except:
                pass

def update_all_fonts(root_widget):
    """Recursively update fonts for all child widgets"""
    apply_fonts_to_widget(root_widget)
    
    try:
        for child in root_widget.winfo_children():
            update_all_fonts(child)
    except:
        pass