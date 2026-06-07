"""Keyboard navigation for Settings tabs with left section sidebars."""
from __future__ import annotations

from core.keyboard_registry import KeyboardRegistry


def _sidebar_index_for_tab(tab, buttons, section_order):
    """Pick the sidebar button index for the currently visible section."""
    section = getattr(tab, '_active_section', None)
    if section and section in section_order:
        try:
            return section_order.index(section)
        except ValueError:
            pass
    order = getattr(tab, '_section_order', section_order)
    for i, sid in enumerate(order):
        if sid in getattr(tab, '_nav_buttons', {}):
            try:
                btn = tab._nav_buttons[sid]
                style = str(btn.cget('bootstyle') or '')
                if 'primary' in style:
                    return i
            except Exception:
                pass
    return 0


def focus_settings_sidebar(tab, buttons=None, section_order=None):
    """F4: focus the section sidebar on the active section button."""
    buttons = buttons or getattr(tab, '_section_buttons', [])
    section_order = section_order or getattr(tab, '_section_order', [])
    nav_map = getattr(tab, '_nav_buttons', {})
    if not buttons:
        return None
    idx = _sidebar_index_for_tab(tab, buttons, section_order)
    KeyboardRegistry.set_sidebar_nav_context(buttons, nav_map, section_order)
    return KeyboardRegistry.focus_sidebar_button(buttons, idx)


def _bind_key_recursive(widget, sequence, handler):
    seen = set()

    def walk(w):
        wid = str(w)
        if wid in seen:
            return
        seen.add(wid)
        try:
            w.bind(sequence, handler, add='+')
            for child in w.winfo_children():
                walk(child)
        except Exception:
            pass

    walk(widget)


def bind_settings_page_keys(settings_page):
    """Bind F4 on the settings page container so it always reaches the sidebar."""
    parent = settings_page.parent

    def _on_f4(event=None):
        if settings_page.focus_active_tab_sidebar():
            return 'break'
        return None

    for seq in ('<F4>', '<KeyPress-F4>'):
        _bind_key_recursive(parent, seq, _on_f4)
        parent.bind(seq, _on_f4, add='+')


def wire_settings_section_nav(tab, nav_buttons: dict, section_order: list, show_section_fn):
    """
    Wire F4 sidebar focus, ↑↓/Enter on section buttons, Alt+1..N section jump.
    tab must have `.outer` frame (top-level tab container).
    """
    buttons = [nav_buttons[s] for s in section_order if s in nav_buttons]
    tab._section_buttons = buttons
    tab._section_order = section_order
    tab._nav_buttons = nav_buttons
    tab._show_section_fn = show_section_fn
    tab._focus_sidebar = lambda: focus_settings_sidebar(tab)

    def sidebar_nav(event):
        return KeyboardRegistry.handle_sidebar_nav(event, buttons)

    for i, btn in enumerate(buttons):
        try:
            btn.configure(takefocus=True)
        except Exception:
            pass

        def _sync_index(idx=i):
            KeyboardRegistry.set_sidebar_index(idx)

        btn.bind('<FocusIn>', lambda e, fn=_sync_index: fn(), add='+')
        for seq in ('<Up>', '<Down>', '<Return>', '<KP_Enter>', '<Escape>'):
            btn.bind(seq, sidebar_nav, add='+')

    outer = getattr(tab, 'outer', None)
    if outer is not None:
        def _on_f4(event=None):
            tab._focus_sidebar()
            return 'break'

        for seq in ('<F4>', '<KeyPress-F4>'):
            _bind_key_recursive(outer, seq, _on_f4)
            outer.bind(seq, _on_f4, add='+')

        def _outer_sidebar_nav(event):
            if not KeyboardRegistry.sidebar_nav_active():
                try:
                    focused = outer.winfo_toplevel().focus_get()
                except Exception:
                    return None
                if focused not in buttons:
                    return None
            return sidebar_nav(event)

        for seq in ('<Up>', '<Down>', '<Return>', '<KP_Enter>', '<Escape>'):
            outer.bind(seq, _outer_sidebar_nav, add='+')

        for i, section_id in enumerate(section_order[:9]):
            def make_alt(idx, sid):
                def handler(event=None):
                    show_section_fn(sid)
                    KeyboardRegistry.set_sidebar_nav_context(buttons, nav_buttons, section_order)
                    KeyboardRegistry.focus_sidebar_button(buttons, idx)
                    return 'break'
                return handler

            try:
                outer.bind(f'<Alt-Key-{i + 1}>', make_alt(i, section_id), add='+')
            except Exception:
                pass

    return buttons, tab._focus_sidebar


def bindings_for_sectioned_tab(tab, first_focus=None, f2_target=None):
    """Build PageBindings for a settings tab with section sidebar."""
    from core.keyboard_registry import PageBindings

    buttons = getattr(tab, '_section_buttons', [])
    focus_sidebar = getattr(tab, '_focus_sidebar', None)
    if not callable(focus_sidebar):
        focus_sidebar = lambda: focus_settings_sidebar(tab)

    return PageBindings(
        page_id=getattr(tab, 'TAB_NAME', 'settings_section'),
        first_focus=first_focus,
        f2_target=f2_target,
        sidebar_buttons=buttons,
        on_f4_sidebar=focus_sidebar,
    )
