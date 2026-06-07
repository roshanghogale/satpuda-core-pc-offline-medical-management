"""
Single source of truth for application keyboard shortcuts.

Pages register a PageBindings instance when shown; global keys dispatch to it.
"""
from __future__ import annotations

import os
import time
import tkinter as tk
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from core.focus_chain import focus_tree, safe_focus

# Set True to log every keypress and whether registry actions ran (stdout + config/keyboard.log).
DEBUG_KEYBOARD = True

_ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KEYBOARD_LOG_FILE = os.path.join(_ROOT_DIR, 'config', 'keyboard.log')

INPUT_CLASSES = frozenset({
    'Entry', 'TEntry', 'TCombobox', 'Text', 'Spinbox', 'TSpinbox', 'Listbox',
})

TREEVIEW_CLASS = 'Treeview'

_INVALID_BIND_KEYSYMS = frozenset({'asciigrave', 'backquote'})
_grave_bind_cache: Optional[tuple] = None


def _widget_class(w) -> str:
    try:
        return w.winfo_class()
    except Exception:
        return ''


def _is_date_entry(widget) -> bool:
    try:
        return 'DateEntry' in type(widget).__name__
    except Exception:
        return False


def _widget_blocks_navigation(widget) -> bool:
    """True when page-digit / letter nav must not steal keys from the widget."""
    if widget is None:
        return False
    cls_name = _widget_class(widget)
    if cls_name in INPUT_CLASSES:
        return True
    if _is_date_entry(widget):
        return True
    try:
        parent = widget.master
        if parent is not None:
            if getattr(parent, 'entry', None) is widget:
                return True
            if getattr(parent, 'step1_entry', None) is widget:
                return True
    except Exception:
        pass
    return False


def is_input_focused(root) -> bool:
    try:
        w = root.focus_get()
        return w is not None and _widget_blocks_navigation(w)
    except Exception:
        return False


def is_treeview_focused(root) -> bool:
    try:
        w = root.focus_get()
        return w is not None and _widget_class(w) == TREEVIEW_CLASS
    except Exception:
        return False


@dataclass
class PageBindings:
    """Keyboard handlers for the active page."""
    page_id: str = ''
    first_focus: Optional[Callable] = None
    on_f5: Optional[Callable] = None
    on_f6: Optional[Callable] = None
    on_end: Optional[Callable] = None
    on_ctrl_p: Optional[Callable] = None
    on_ctrl_e: Optional[Callable] = None
    on_ctrl_f: Optional[Callable] = None
    on_ctrl_enter: Optional[Callable] = None
    on_ctrl_shift_c: Optional[Callable] = None
    f2_target: Any = None
    f3_target: Any = None
    on_shift_f2: Optional[Callable] = None
    sub_keys: Dict[str, Callable] = field(default_factory=dict)
    on_escape_extra: Optional[Callable] = None
    sidebar_buttons: List[Any] = field(default_factory=list)
    on_f4_sidebar: Optional[Callable] = None


class KeyboardRegistry:
    _root: Optional[tk.Misc] = None
    _app: Any = None
    _active: Optional[PageBindings] = None
    _installed = False
    _sidebar_index = 0
    _settings_tab_map: Dict[int, Callable] = {}
    _sidebar_nav_active = False
    _sidebar_nav_buttons: List[Any] = []
    _sidebar_nav_map: Dict[str, Any] = {}
    _sidebar_nav_order: List[str] = []
    _alt_chord_used = False
    _page_digit_handlers: Dict[str, Callable] = {}
    _nav_bound = False
    _bound_sequences: List[str] = []
    _app_handlers: Dict[str, Callable] = {}
    _wired_pages: Dict[int, Dict[str, Any]] = {}
    _nav_mode: bool = False
    _shell_wired: bool = False
    _active_page_root: Any = None
    _active_page_bindings: Optional[PageBindings] = None
    _last_nav_keysym: str = ''
    _last_nav_at: float = 0.0

    _DIGIT_KEYSYMS = {
        '0': '0', '1': '1', '2': '2', '3': '3', '4': '4',
        '5': '5', '6': '6', '7': '7', '8': '8', '9': '9',
        'KP_0': '0', 'KP_1': '1', 'KP_2': '2', 'KP_3': '3',
        'KP_4': '4', 'KP_5': '5', 'KP_6': '6', 'KP_7': '7',
        'KP_8': '8', 'KP_9': '9',
    }

    _ALT_MODIFIER_KEYS = frozenset({
        'Alt_L', 'Alt_R', 'Meta_L', 'Meta_R',
        'Shift_L', 'Shift_R', 'Control_L', 'Control_R',
        'Caps_Lock', 'Num_Lock',
    })
    # Shift + Ctrl only — Windows Tk often sets 0x20000/0x0008 without Alt physically held.
    _NAV_BLOCK_STATE_MASK = 0x0001 | 0x0004
    _alt_key_down = False

    # ── registration ─────────────────────────────────────────────────────

    @classmethod
    def can_process_global_shortcut(cls, root=None) -> bool:
        """
        False when an editable control has focus so navigation keys type normally.
        Does not block action shortcuts (F5, Ctrl+E, etc.) — use only for nav keys.
        """
        root = root or cls._root
        if root is None:
            return True
        try:
            w = root.focus_get()
        except Exception:
            return True
        if w is None:
            return True
        return not _widget_blocks_navigation(w)

    @classmethod
    def _emit_log(cls, message: str):
        if not DEBUG_KEYBOARD:
            return
        print(message, flush=True)
        try:
            os.makedirs(os.path.dirname(KEYBOARD_LOG_FILE), exist_ok=True)
            with open(KEYBOARD_LOG_FILE, 'a', encoding='utf-8') as f:
                f.write(message + '\n')
        except Exception:
            pass

    @classmethod
    def _log_context(cls) -> str:
        page = cls._active.page_id if cls._active else '-'
        active_nav = getattr(cls._app, 'active_nav', '-') if cls._app else '-'
        focus = cls._get_focus_info()
        return (
            f'page={page} nav={active_nav!r} nav_mode={cls._nav_mode} '
            f'focus={focus}'
        )

    @classmethod
    def _get_focus_info(cls) -> str:
        try:
            w = cls._root.focus_get() if cls._root else None
            if w is None:
                return 'none'
            return f'{_widget_class(w)}({w})'
        except Exception:
            return 'unknown'

    @classmethod
    def _format_key(cls, event) -> str:
        keysym = getattr(event, 'keysym', '') or ''
        char = getattr(event, 'char', '') or ''
        try:
            state = int(getattr(event, 'state', 0))
        except (TypeError, ValueError):
            state = 0
        mods = []
        if state & 0x0001:
            mods.append('Shift')
        if state & 0x0004:
            mods.append('Ctrl')
        if cls._alt_key_down:
            mods.append('Alt')
        mod_s = '+'.join(mods)
        if mod_s:
            return f'{mod_s}+{keysym}(char={char!r})'
        return f'{keysym}(char={char!r})'

    @classmethod
    def _log_keypress(cls, event, source: str = 'registry'):
        if not DEBUG_KEYBOARD:
            return
        ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S.') + f'{int(time.time() * 1000) % 1000:03d}'
        cls._emit_log(
            f'[{ts}] KEY {cls._format_key(event)} | {cls._log_context()} | source={source}'
        )

    @classmethod
    def _log_action(
        cls,
        action: str,
        performed: bool,
        event=None,
        *,
        reason: str = '',
        source: str = '',
    ):
        if not DEBUG_KEYBOARD:
            return
        ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S.') + f'{int(time.time() * 1000) % 1000:03d}'
        status = 'PERFORMED' if performed else 'NOT PERFORMED'
        key_part = f' key={cls._format_key(event)} |' if event is not None else ''
        reason_part = f' reason={reason}' if reason else ''
        src_part = f' source={source}' if source else ''
        cls._emit_log(
            f'[{ts}] ACTION {action} | {status} |{key_part} {cls._log_context()}'
            f'{reason_part}{src_part}'
        )

    @classmethod
    def _navigation_block_reason(cls, event=None) -> str:
        if event is not None and cls._alt_key_down:
            return 'alt_held'
        if event is not None and cls.event_has_modifiers(event):
            return 'modifiers_held'
        if cls.sidebar_nav_active():
            return 'sidebar_nav'
        if cls._root is not None:
            try:
                from core.dialog_escape import grabbed_toplevel
                if grabbed_toplevel(cls._root) is not None:
                    return 'modal_grab'
            except Exception:
                pass
        if cls._nav_mode:
            return ''
        if not cls.can_process_global_shortcut():
            return 'input_focused'
        return 'nav_blocked'

    @classmethod
    def _debug_shortcut(cls, shortcut: str, event=None, performed: bool = True, reason: str = ''):
        cls._log_action(shortcut, performed, event, reason=reason)

    @classmethod
    def _on_log_all_keypress(cls, event):
        cls._log_keypress(event, source='bind_all')
        return None

    @classmethod
    def _safe_bind_all(cls, root: tk.Misc, seq: str, callback) -> bool:
        keysym = seq.strip('<>').split('-')[-1] if seq.startswith('<') else ''
        if keysym.lower() in _INVALID_BIND_KEYSYMS:
            return False
        try:
            root.bind_all(seq, callback, add='+')
            cls._bound_sequences.append(seq)
            return True
        except tk.TclError:
            return False

    @classmethod
    def _grave_bind_sequences(cls) -> tuple:
        """Backtick / Home key — only Tk-valid sequences for this platform."""
        if cls._grave_bind_cache is not None:
            return cls._grave_bind_cache
        root = cls._root
        candidates = ('<KeyPress-grave>', '<KeyPress-quoteleft>')
        valid = []
        if root is not None:
            for seq in candidates:
                keysym = seq.strip('<>').split('-')[-1].lower()
                if keysym in _INVALID_BIND_KEYSYMS:
                    continue
                try:
                    root.bind(seq, lambda e: None)
                    root.unbind(seq)
                    valid.append(seq)
                except tk.TclError:
                    pass
        cls._grave_bind_cache = tuple(valid)
        return cls._grave_bind_cache

    @classmethod
    def register_app_handlers(cls, **handlers):
        """App-wide fallbacks when the active page omits a handler."""
        cls._app_handlers.update(handlers)

    @classmethod
    def set_nav_mode(cls, enabled: bool):
        cls._nav_mode = bool(enabled)
        if DEBUG_KEYBOARD:
            cls._emit_log(f'[{datetime.now().strftime("%Y-%m-%d %H:%M:%S")}] nav_mode={cls._nav_mode}')

    @classmethod
    def finish_modal_session(cls):
        """
        Call after startup alerts / modal dialogs close so page shortcuts work.
        Esc on alert windows does not run main blur_to_nav — this restores nav.
        """
        root = cls._root
        if root is not None:
            try:
                grabbed = root.grab_current()
                if grabbed is not None:
                    try:
                        if grabbed.winfo_exists():
                            grabbed.grab_release()
                    except Exception:
                        pass
            except Exception:
                pass
        cls.set_nav_mode(True)
        cls.blur_to_nav()
        cls.refresh_active_page()

    @classmethod
    def refresh_active_page(cls):
        """Re-apply keyboard bindings on the current page widget tree."""
        app = cls._app
        if app is not None and getattr(app, 'active_nav', None) == '🏠 Home':
            inner = getattr(app, '_home_inner_frame', None)
            hb = getattr(app, '_home_keyboard_bindings', None)
            if inner is not None and hb is not None:
                try:
                    if inner.winfo_exists():
                        cls.wire_page(inner, hb)
                        return
                except Exception:
                    pass
        root = cls._active_page_root
        bindings = cls._active_page_bindings
        if root is not None and bindings is not None:
            try:
                if root.winfo_exists():
                    cls.wire_page(root, bindings)
            except Exception:
                pass

    @classmethod
    def register_page(cls, page_root, bindings: Optional[PageBindings] = None):
        """Bind every shortcut on this page's widget tree (call from each page)."""
        if bindings is None:
            bindings = getattr(page_root, '_keyboard_bindings', None)
        cls._active_page_root = page_root
        cls._active_page_bindings = bindings
        cls.wire_page(page_root, bindings)

    @classmethod
    def wire_shell(cls, root: tk.Misc, main_frame: tk.Misc, nav_frame: tk.Misc = None):
        """Bind navigation on nav bar + main_frame shell (page content wired separately)."""
        if cls._shell_wired:
            return
        cls._shell_wired = True
        try:
            root.configure(takefocus=1)
            main_frame.configure(takefocus=1)
        except Exception:
            pass
        if nav_frame is not None:
            cls._bind_nav_on_subtree(nav_frame, wire_id='shell_nav')
        seqs = cls._nav_bind_sequences()
        action_seqs = cls._action_bind_sequences()
        try:
            main_frame._kb_wire_id = 'shell_mf'
            for seq, handler in seqs + action_seqs:
                cls._safe_widget_bind(main_frame, seq, handler)
        except Exception:
            pass

    @classmethod
    def _nav_bind_sequences(cls) -> List[tuple]:
        """Navigation keys — use KeyPress (not KeyPress-N) to avoid double-fire."""
        seqs: List[tuple] = [
            ('<KeyPress>', cls._on_widget_nav_keypress),
        ]
        for seq in cls._grave_bind_sequences():
            seqs.append((seq, cls._on_grave_nav))
        return seqs

    @classmethod
    def _safe_widget_bind(cls, widget, seq: str, handler) -> bool:
        keysym = seq.strip('<>').split('-')[-1] if seq.startswith('<') else ''
        if keysym.lower() in _INVALID_BIND_KEYSYMS:
            return False
        try:
            widget.bind(
                seq,
                lambda e, h=handler: cls._nav_wrap(h, e),
                add='+',
            )
            return True
        except tk.TclError:
            return False

    @classmethod
    def _action_bind_sequences(cls) -> List[tuple]:
        return [
            ('<F5>', cls._on_f5),
            ('<KeyPress-F5>', cls._on_f5),
            ('<F6>', cls._on_f6),
            ('<KeyPress-F6>', cls._on_f6),
            ('<End>', lambda e: cls._run(cls._get_handler('on_end'), e, action='End')),
            ('<F2>', cls._on_f2),
            ('<F3>', cls._on_f3),
            ('<Shift-F2>', cls._on_shift_f2),
            ('<F4>', cls._on_f4),
            ('<KeyPress-F4>', cls._on_f4),
            ('<Control-g>', cls._on_ctrl_g),
            ('<Control-G>', cls._on_ctrl_g),
            ('<Control-p>', cls._on_ctrl_p),
            ('<Control-P>', cls._on_ctrl_p),
            ('<Control-e>', cls._on_ctrl_e),
            ('<Control-E>', cls._on_ctrl_e),
            ('<Control-f>', cls._on_ctrl_f),
            ('<Control-F>', cls._on_ctrl_f),
            ('<Control-Return>', cls._on_ctrl_enter),
            ('<Control-KP_Enter>', cls._on_ctrl_enter),
            ('<Control-Shift-C>', cls._on_ctrl_shift_c),
            ('<Control-Shift-c>', cls._on_ctrl_shift_c),
            ('<Control-Shift-KeyPress-C>', cls._on_ctrl_shift_c),
            ('<Control-Shift-KeyPress-c>', cls._on_ctrl_shift_c),
            ('<Alt_L>', cls._on_alt_press),
            ('<Alt_R>', cls._on_alt_press),
            ('<KeyRelease-Alt_L>', cls._on_alt_release),
            ('<KeyRelease-Alt_R>', cls._on_alt_release),
        ]

    @classmethod
    def _nav_wrap(cls, handler, event):
        try:
            result = handler(event)
        except TypeError:
            try:
                result = handler()
            except Exception:
                return 'break'
        except Exception:
            return 'break'
        return 'break' if result == 'break' else None

    @classmethod
    def _on_widget_nav_keypress(cls, event):
        """Per-widget KeyPress: digits + letters (runs before Entry default insert)."""
        if event.keysym in cls._ALT_MODIFIER_KEYS:
            return None
        if cls._alt_key_down:
            return None
        if cls.event_has_modifiers(event):
            cls._log_action(
                f'nav:{event.keysym}', False, event,
                reason='modifiers_held', source='widget_nav',
            )
            return None
        digit_key = cls._keysym_to_page_digit(event)
        if digit_key and digit_key in cls._page_digit_handlers:
            return cls._dispatch_page_digit(
                event, cls._page_digit_handlers[digit_key], digit_key)
        if not cls.should_handle_navigation(event):
            reason = cls._navigation_block_reason(event) or 'nav_blocked'
            cls._log_action(
                f'letter:{event.keysym.lower()}', False, event, reason=reason,
                source='widget_nav',
            )
            return None
        key = event.keysym.lower()
        if len(key) != 1 or not key.isalpha():
            return None
        fn = cls._resolve_sub_key(key)
        if fn:
            return cls._run(fn, event, action=f'nav:{key}', source='widget_nav')
        cls._log_action(
            f'nav:{key}', False, event, reason='no_handler', source='widget_nav',
        )
        return None

    @classmethod
    def _bind_nav_on_subtree(cls, root_widget, wire_id: str):
        seqs = cls._nav_bind_sequences()
        action_seqs = cls._action_bind_sequences()

        def _walk(widget):
            try:
                if not widget.winfo_exists():
                    return
            except Exception:
                return
            if getattr(widget, '_kb_wire_id', None) == wire_id:
                try:
                    for child in widget.winfo_children():
                        _walk(child)
                except Exception:
                    pass
                return
            widget._kb_wire_id = wire_id
            for seq, handler in seqs:
                cls._safe_widget_bind(widget, seq, handler)
            for seq, handler in action_seqs:
                cls._safe_widget_bind(widget, seq, handler)
            if _widget_blocks_navigation(widget):
                widget.bind(
                    '<FocusIn>',
                    lambda e: cls.set_nav_mode(False),
                    add='+',
                )
            try:
                for child in widget.winfo_children():
                    _walk(child)
            except Exception:
                pass

        _walk(root_widget)

    @classmethod
    def wire_page(cls, page_root, bindings: Optional[PageBindings] = None):
        """
        Bind shortcuts on every widget in the page tree so they run BEFORE
        Entry/Combobox class handlers (which would otherwise eat 0-9 / letters).
        """
        if page_root is None:
            return
        try:
            if not page_root.winfo_exists():
                return
        except Exception:
            return

        if bindings is None:
            bindings = getattr(page_root, '_keyboard_bindings', None)
        if bindings is None:
            bindings = cls.make_bindings(getattr(page_root, '_page_id', 'page'))

        cls.set_active(bindings)
        page_root._keyboard_bindings = bindings
        cls.unwire_page(page_root)

        wire_id = f'page_{id(page_root)}'
        cls._wired_pages[id(page_root)] = {
            'wire_id': wire_id,
            'page_root': page_root,
            'sequences': [s for s, _ in cls._nav_bind_sequences()]
                + [s for s, _ in cls._action_bind_sequences()],
        }
        cls._bind_nav_on_subtree(page_root, wire_id)

        def _rewire():
            try:
                if page_root.winfo_exists():
                    cls._bind_nav_on_subtree(page_root, wire_id)
            except Exception:
                pass

        page_root.after_idle(_rewire)
        page_root.after(400, _rewire)
        page_root.after(1000, _rewire)

    @classmethod
    def unwire_page(cls, page_root):
        info = cls._wired_pages.pop(id(page_root), None)
        if not info:
            return
        wire_id = info.get('wire_id')
        sequences = info.get('sequences', ())
        page_root = info.get('page_root', page_root)

        def _walk(widget):
            try:
                if not widget.winfo_exists():
                    return
            except Exception:
                return
            if getattr(widget, '_kb_wire_id', None) != wire_id:
                return
            for seq in sequences:
                try:
                    widget.unbind(seq)
                except Exception:
                    pass
            try:
                widget._kb_wire_id = None
            except Exception:
                pass
            try:
                for child in widget.winfo_children():
                    _walk(child)
            except Exception:
                pass

        try:
            _walk(page_root)
        except Exception:
            pass

    @classmethod
    def _tcl_to_event(cls, page_root, keysym, char, state, winpath):
        class _E:
            pass
        e = _E()
        e.keysym = keysym or ''
        e.char = char or ''
        try:
            e.state = int(state)
        except (TypeError, ValueError):
            e.state = 0
        try:
            e.widget = page_root.nametowidget(winpath) if winpath else page_root
        except Exception:
            e.widget = page_root
        return e

    @classmethod
    def _apply_page_tag(cls, widget, tag: str):
        try:
            if not widget.winfo_exists():
                return
        except Exception:
            return
        try:
            tags = list(widget.bindtags())
            tags = [t for t in tags if not (isinstance(t, str) and t.startswith('PageKeys_'))]
            if tag not in tags:
                widget.bindtags((tag,) + tuple(tags))
        except Exception:
            pass
        try:
            for child in widget.winfo_children():
                cls._apply_page_tag(child, tag)
        except Exception:
            pass

    @classmethod
    def _remove_page_tag(cls, widget, tag: str):
        try:
            if not widget.winfo_exists():
                return
        except Exception:
            return
        try:
            tags = [t for t in widget.bindtags() if t != tag]
            widget.bindtags(tuple(tags))
        except Exception:
            pass
        try:
            for child in widget.winfo_children():
                cls._remove_page_tag(child, tag)
        except Exception:
            pass

    @classmethod
    def _all_page_sequences(cls) -> List[tuple]:
        """Every shortcut bound on each page widget tree."""
        seqs: List[tuple] = [
            ('<F5>', cls._on_f5),
            ('<KeyPress-F5>', cls._on_f5),
            ('<F6>', cls._on_f6),
            ('<KeyPress-F6>', cls._on_f6),
            ('<End>', lambda e: cls._run(cls._get_handler('on_end'), e, action='End')),
            ('<F2>', cls._on_f2),
            ('<F3>', cls._on_f3),
            ('<Shift-F2>', cls._on_shift_f2),
            ('<F4>', cls._on_f4),
            ('<KeyPress-F4>', cls._on_f4),
            ('<Control-g>', cls._on_ctrl_g),
            ('<Control-G>', cls._on_ctrl_g),
            ('<Control-p>', cls._on_ctrl_p),
            ('<Control-P>', cls._on_ctrl_p),
            ('<Control-e>', cls._on_ctrl_e),
            ('<Control-E>', cls._on_ctrl_e),
            ('<Control-f>', cls._on_ctrl_f),
            ('<Control-F>', cls._on_ctrl_f),
            ('<Control-Return>', cls._on_ctrl_enter),
            ('<Control-KP_Enter>', cls._on_ctrl_enter),
            ('<Control-Shift-C>', cls._on_ctrl_shift_c),
            ('<Control-Shift-c>', cls._on_ctrl_shift_c),
            ('<Control-Shift-KeyPress-C>', cls._on_ctrl_shift_c),
            ('<Control-Shift-KeyPress-c>', cls._on_ctrl_shift_c),
            ('<Alt_L>', cls._on_alt_press),
            ('<Alt_R>', cls._on_alt_press),
            ('<KeyRelease-Alt_L>', cls._on_alt_release),
            ('<KeyRelease-Alt_R>', cls._on_alt_release),
            ('<KeyPress>', cls._on_page_keypress),
        ]
        for seq in cls._grave_bind_sequences():
            seqs.append((seq, cls._on_grave_nav))
        for d in '01234567':
            seqs.append((f'<KeyPress-{d}>', cls._on_digit_nav))
            seqs.append((f'<Key-{d}>', cls._on_digit_nav))
        for d in range(0, 8):
            seqs.append((f'<KeyPress-KP_{d}>', cls._on_digit_nav))
        for d in range(1, 10):
            seqs.append((f'<Control-Key-{d}>', cls._on_settings_ctrl_digit_event))
            seqs.append((f'<Control-KP_{d}>', cls._on_settings_ctrl_digit_event))
        return seqs

    @classmethod
    def _on_grave_nav(cls, event):
        handler = cls._page_digit_handlers.get('`')
        if handler:
            return cls._dispatch_page_digit(event, handler, '`')
        cls._log_action('nav:`', False, event, reason='no_handler', source='grave_nav')
        return None

    @classmethod
    def _on_digit_nav(cls, event):
        digit_key = cls._keysym_to_page_digit(event)
        if digit_key and digit_key in cls._page_digit_handlers:
            return cls._dispatch_page_digit(
                event, cls._page_digit_handlers[digit_key], digit_key)
        cls._log_action(
            f'nav:{digit_key or event.keysym}', False, event,
            reason='no_handler', source='digit_nav',
        )
        return None

    @classmethod
    def _on_page_keypress(cls, event):
        """Page-level KeyPress: digits, letters, Ctrl+Shift+C fallback."""
        if event.keysym in cls._ALT_MODIFIER_KEYS:
            return None
        if event.keysym.lower() == 'c' and cls._ctrl_shift_held(event):
            if cls._root is not None:
                from core.dialog_escape import grabbed_toplevel
                if grabbed_toplevel(cls._root) is not None:
                    return None
            return cls._on_ctrl_shift_c(event)
        if cls._alt_held(event):
            cls._alt_chord_used = True
            return None
        return cls._on_keypress_letter_nav(event)

    @classmethod
    def make_bindings(cls, page_id: str, **overrides) -> PageBindings:
        """Build PageBindings; page overrides win over app defaults."""
        fields = {f.name for f in PageBindings.__dataclass_fields__.values()}
        base = dict(page_id=page_id)
        for key, fn in cls._app_handlers.items():
            if key in fields and key not in overrides:
                base[key] = fn
        base.update(overrides)
        base['page_id'] = page_id
        if 'sub_keys' in overrides:
            merged_sub = dict(base.get('sub_keys') or {})
            merged_sub.update(overrides['sub_keys'])
            base['sub_keys'] = merged_sub
        return PageBindings(**{k: v for k, v in base.items() if k in fields})

    @classmethod
    def _get_handler(cls, attr: str) -> Optional[Callable]:
        b = cls._active
        if b is not None:
            fn = getattr(b, attr, None)
            if fn:
                return fn
        return cls._app_handlers.get(attr)

    @classmethod
    def _keysym_to_page_digit(cls, event) -> Optional[str]:
        char = getattr(event, 'char', '') or ''
        if event.keysym in ('grave', 'quoteleft') or char == '`':
            return '`'
        return cls._DIGIT_KEYSYMS.get(event.keysym)

    @classmethod
    def _resolve_sub_key(cls, key: str) -> Optional[Callable]:
        app = cls._app
        if app is None:
            return None
        active_nav = getattr(app, 'active_nav', None)

        if active_nav == 'Returns':
            show = getattr(app, '_returns_show', None)
            if callable(show):
                if key == 's':
                    return lambda: show('sales')
                if key == 'p':
                    return lambda: show('purchase')

        if active_nav == '🏠 Home':
            hb = getattr(app, '_home_keyboard_bindings', None)
            if hb and key in hb.sub_keys:
                return hb.sub_keys[key]

        b = cls._active
        if b and key in b.sub_keys:
            return b.sub_keys[key]

        if active_nav == 'Settings':
            page = getattr(app, '_settings_page', None)
            if page is not None:
                current = getattr(page, '_current_tab_object', None)
                if callable(current):
                    obj = current()
                    if obj is not None and hasattr(obj, 'get_keyboard_bindings'):
                        tb = obj.get_keyboard_bindings()
                        if key in tb.sub_keys:
                            return tb.sub_keys[key]
        return None

    @classmethod
    def install(cls, root: tk.Misc, app: Any):
        if cls._installed:
            return
        cls._root = root
        cls._app = app
        cls._grave_bind_cache = None
        cls._bind_globals(root)
        cls._bind_navigation(root)
        cls._safe_bind_all(root, '<KeyPress>', cls._on_log_all_keypress)
        cls._installed = True
        if DEBUG_KEYBOARD:
            cls._emit_log(
                f'[{datetime.now().strftime("%Y-%m-%d %H:%M:%S")}] '
                f'KeyboardRegistry installed — logging to {KEYBOARD_LOG_FILE}'
            )

    @classmethod
    def configure_navigation(cls, page_digits: Optional[Dict[str, Callable]] = None):
        """Register top-level page digit shortcuts (0,1,…,7,`)."""
        if page_digits is not None:
            cls._page_digit_handlers = dict(page_digits)
        if cls._root is not None and cls._installed:
            cls._bind_navigation(cls._root)

    @classmethod
    def should_handle_navigation(cls, event=None) -> bool:
        if event is not None and cls.event_has_modifiers(event):
            return False
        if cls.sidebar_nav_active():
            return False
        if cls._root is not None:
            try:
                from core.dialog_escape import grabbed_toplevel
                if grabbed_toplevel(cls._root) is not None:
                    return False
            except Exception:
                pass
        if cls._nav_mode:
            return True
        if not cls.can_process_global_shortcut():
            return False
        return True

    @classmethod
    def _bind_navigation(cls, root):
        if cls._nav_bound:
            return
        cls._nav_bound = True
        # Digits/letters: per-page wire_page + wire_shell only (NOT bind_all — prevents duplicates)
        for d in range(1, 10):
            cls._safe_bind_all(root, f'<Control-Key-{d}>', cls._on_settings_ctrl_digit_event)
            cls._safe_bind_all(root, f'<Control-KP_{d}>', cls._on_settings_ctrl_digit_event)

    @classmethod
    def _dispatch_page_digit_key(cls, event, digit: str):
        handler = cls._page_digit_handlers.get(digit)
        if handler:
            return cls._dispatch_page_digit(event, handler)
        return None

    @classmethod
    def _dispatch_page_digit(cls, event, handler, digit_key: str = ''):
        label = f'nav:{digit_key or cls._keysym_to_page_digit(event) or getattr(event, "keysym", "")}'
        if not cls.should_handle_navigation(event):
            reason = cls._navigation_block_reason(event) or 'nav_blocked'
            cls._log_action(label, False, event, reason=reason, source='page_digit')
            return None
        import time
        keysym = getattr(event, 'keysym', '') or ''
        now = time.time()
        if keysym and keysym == cls._last_nav_keysym and (now - cls._last_nav_at) < 0.2:
            cls._log_action(label, False, event, reason='debounce', source='page_digit')
            return 'break'
        cls._last_nav_keysym = keysym
        cls._last_nav_at = now
        return cls._run(handler, event, action=label, source='page_digit')

    @classmethod
    def _on_settings_ctrl_digit_event(cls, event):
        app = cls._app
        if app is None or getattr(app, 'active_nav', None) != 'Settings':
            cls._log_action(
                f'Ctrl+{event.keysym}', False, event, reason='not_settings_page',
            )
            return None
        try:
            digit = int(event.keysym)
        except ValueError:
            cls._log_action(
                f'Ctrl+{event.keysym}', False, event, reason='invalid_digit',
            )
            return None
        if cls.handle_settings_ctrl_digit(digit, event):
            return 'break'
        cls._log_action(f'Ctrl+{digit}', False, event, reason='no_handler')
        return None

    @classmethod
    def _on_keypress_letter_nav(cls, event):
        if event.keysym in cls._ALT_MODIFIER_KEYS:
            return None
        if cls._alt_key_down:
            return None
        if cls.event_has_modifiers(event):
            return None

        digit_key = cls._keysym_to_page_digit(event)
        if digit_key and digit_key in cls._page_digit_handlers:
            return cls._dispatch_page_digit(
                event, cls._page_digit_handlers[digit_key], digit_key)

        if not cls.should_handle_navigation(event):
            reason = cls._navigation_block_reason(event) or 'nav_blocked'
            cls._log_action(
                f'letter:{event.keysym.lower()}', False, event, reason=reason,
                source='letter_nav',
            )
            return None

        key = event.keysym.lower()
        if len(key) != 1 or not key.isalpha():
            return None

        fn = cls._resolve_sub_key(key)
        if fn:
            return cls._run(fn, event, action=f'nav:{key}', source='letter_nav')
        cls._log_action(
            f'nav:{key}', False, event, reason='no_handler', source='letter_nav',
        )
        return None

    @classmethod
    def set_active(cls, bindings: Optional[PageBindings]):
        cls._active = bindings
        cls._sidebar_index = 0

    @classmethod
    def event_has_modifiers(cls, event) -> bool:
        """True when Shift/Ctrl held — blocks plain digit/letter navigation."""
        try:
            return bool(event.state & cls._NAV_BLOCK_STATE_MASK)
        except Exception:
            return False

    @classmethod
    def _alt_held(cls, event) -> bool:
        """True only when Alt was pressed (not spurious Windows event.state bits)."""
        return cls._alt_key_down

    @classmethod
    def _ctrl_shift_held(cls, event) -> bool:
        try:
            state = event.state
            return bool(state & 0x0004) and bool(state & 0x0001)
        except Exception:
            return False

    @classmethod
    def set_sidebar_index(cls, index: int):
        cls._sidebar_index = max(0, int(index))

    @classmethod
    def set_sidebar_nav_context(
        cls,
        buttons: List[Any],
        nav_map: Optional[Dict[str, Any]] = None,
        section_order: Optional[List[str]] = None,
    ):
        cls._sidebar_nav_active = bool(buttons)
        cls._sidebar_nav_buttons = list(buttons or [])
        cls._sidebar_nav_map = dict(nav_map or {})
        cls._sidebar_nav_order = list(section_order or [])

    @classmethod
    def sidebar_nav_active(cls) -> bool:
        return cls._sidebar_nav_active

    @classmethod
    def clear_sidebar_nav_mode(cls) -> bool:
        if not cls._sidebar_nav_active:
            return False
        cls._sidebar_nav_active = False
        cls._sidebar_nav_buttons = []
        cls._sidebar_nav_map = {}
        cls._sidebar_nav_order = []
        return True

    @classmethod
    def _highlight_sidebar_button(cls, buttons: List[Any], index: int):
        order = cls._sidebar_nav_order
        nav_map = cls._sidebar_nav_map
        if order and nav_map:
            active_id = order[index % len(order)] if index < len(order) else None
            for sid, btn in nav_map.items():
                try:
                    btn.configure(bootstyle='primary' if sid == active_id else 'secondary')
                except Exception:
                    pass
            return
        for i, btn in enumerate(buttons):
            try:
                btn.configure(bootstyle='primary' if i == index else 'secondary')
            except Exception:
                pass

    @classmethod
    def focus_sidebar_button(cls, buttons: List[Any], index: int = 0):
        """Focus a settings section sidebar button (F4 / Alt+N)."""
        if not buttons:
            return None
        if not cls._sidebar_nav_buttons:
            cls.set_sidebar_nav_context(buttons)
        cls._sidebar_index = index % len(buttons)
        cls._sidebar_nav_active = True
        cls._highlight_sidebar_button(buttons, cls._sidebar_index)
        btn = buttons[cls._sidebar_index]
        try:
            btn.focus_set()
            btn.focus_force()
            btn.update_idletasks()
        except Exception:
            pass
        return 'break'

    @classmethod
    def consume_sidebar_nav_event(cls, event) -> bool:
        """Route ↑↓/Enter to the settings sidebar while sidebar mode is active."""
        if not cls._sidebar_nav_active or not cls._sidebar_nav_buttons:
            return False
        if event.keysym not in ('Up', 'Down', 'Return', 'KP_Enter'):
            return False
        if cls._root and not cls.can_process_global_shortcut():
            return False
        cls.handle_sidebar_nav(event, cls._sidebar_nav_buttons)
        return True

    @classmethod
    def set_settings_tab_map(cls, mapping: Dict[int, Callable]):
        cls._settings_tab_map = mapping

    @classmethod
    def active(cls) -> Optional[PageBindings]:
        return cls._active

    # ── dispatch helpers ─────────────────────────────────────────────────

    @classmethod
    def _run(cls, fn, event=None, *, action: str = '', source: str = ''):
        act = action or getattr(fn, '__name__', 'callback')
        if not fn:
            cls._log_action(act, False, event, reason='no_handler', source=source)
            return None
        try:
            if event is not None:
                try:
                    result = fn(event)
                except TypeError:
                    result = fn()
            else:
                result = fn()
            cls._log_action(act, True, event, source=source)
            return result if result else 'break'
        except Exception as ex:
            cls._log_action(act, False, event, reason=str(ex), source=source)
            return 'break'

    @classmethod
    def _focus_first(cls):
        b = cls._active
        if b and b.first_focus:
            if callable(b.first_focus):
                try:
                    result = b.first_focus()
                    if result is not None:
                        safe_focus(result)
                except Exception:
                    pass
            else:
                safe_focus(b.first_focus)
            return 'break'
        if cls._app and hasattr(cls._app, 'input_ctrl'):
            cls._app.input_ctrl._focus_first_widget(
                cls._app.input_ctrl._resolve_active_frame())
        return 'break'

    @classmethod
    def _focus_list(cls, target, *, action: str = 'focus_list', event=None):
        if target is None:
            cls._log_action(action, False, event, reason='no_target')
            return None
        if callable(target):
            return cls._run(target, event, action=action)
        if focus_tree(target):
            cls._log_action(action, True, event)
            return 'break'
        cls._log_action(action, False, event, reason='focus_failed')
        return None

    # ── global bindings ──────────────────────────────────────────────────

    @classmethod
    def _bind_globals(cls, root):
        bindings = [
            ('<F5>', cls._on_f5),
            ('<KeyPress-F5>', cls._on_f5),
            ('<F6>', cls._on_f6),
            ('<KeyPress-F6>', cls._on_f6),
            ('<End>', lambda e: cls._run(cls._get_handler('on_end'), e, action='End')),
            ('<F2>', cls._on_f2),
            ('<F3>', cls._on_f3),
            ('<Shift-F2>', cls._on_shift_f2),
            ('<Control-g>', cls._on_ctrl_g),
            ('<Control-G>', cls._on_ctrl_g),
            ('<Control-p>', cls._on_ctrl_p),
            ('<Control-P>', cls._on_ctrl_p),
            ('<Control-e>', cls._on_ctrl_e),
            ('<Control-E>', cls._on_ctrl_e),
            ('<Control-f>', cls._on_ctrl_f),
            ('<Control-F>', cls._on_ctrl_f),
            ('<Control-Return>', cls._on_ctrl_enter),
            ('<Control-KP_Enter>', cls._on_ctrl_enter),
            ('<Control-Shift-C>', cls._on_ctrl_shift_c),
            ('<Control-Shift-c>', cls._on_ctrl_shift_c),
            ('<Control-Shift-KeyPress-C>', cls._on_ctrl_shift_c),
            ('<Control-Shift-KeyPress-c>', cls._on_ctrl_shift_c),
            ('<Alt_L>', cls._on_alt_press),
            ('<Alt_R>', cls._on_alt_press),
            ('<KeyRelease-Alt_L>', cls._on_alt_release),
            ('<KeyRelease-Alt_R>', cls._on_alt_release),
            ('<KeyPress>', cls._on_keypress_alt_chord),
            ('<F4>', cls._on_f4),
            ('<KeyPress-F4>', cls._on_f4),
            ('<Return>', cls._on_sidebar_return),
            ('<KP_Enter>', cls._on_sidebar_return),
        ]
        for seq, fn in bindings:
            cls._safe_bind_all(root, seq, fn)

    @classmethod
    def _on_f2(cls, event):
        b = cls._active
        if b and b.f2_target is not None:
            r = cls._focus_list(b.f2_target, action='F2', event=event)
            if r:
                return r
        if cls._app and hasattr(cls._app, 'input_ctrl'):
            cls._log_action('F2', True, event, source='input_ctrl')
            return cls._app.input_ctrl._on_f2(event)
        cls._log_action('F2', False, event, reason='no_handler')
        return None

    @classmethod
    def _on_f3(cls, event):
        b = cls._active
        if b and b.f3_target is not None:
            return cls._focus_list(b.f3_target, action='F3', event=event)
        cls._log_action('F3', False, event, reason='no_handler')
        return None

    @classmethod
    def _on_shift_f2(cls, event):
        b = cls._active
        if b and b.on_shift_f2:
            return cls._run(b.on_shift_f2, event, action='Shift+F2')
        cls._log_action('Shift+F2', False, event, reason='no_handler')
        return None

    @classmethod
    def _on_f5(cls, event):
        fn = cls._get_handler('on_f5')
        if fn:
            return cls._run(fn, event, action='F5')
        cls._log_action('F5', False, event, reason='no_handler')
        return None

    @classmethod
    def _on_f6(cls, event):
        fn = cls._get_handler('on_f6')
        if fn:
            return cls._run(fn, event, action='F6')
        cls._log_action('F6', False, event, reason='no_handler')
        return None

    @classmethod
    def _on_ctrl_g(cls, event):
        fn = cls._get_handler('on_f5')
        if fn:
            return cls._run(fn, event, action='Ctrl+G')
        cls._log_action('Ctrl+G', False, event, reason='no_handler')
        return None

    @classmethod
    def _on_ctrl_p(cls, event):
        fn = cls._get_handler('on_ctrl_p')
        if fn:
            return cls._run(fn, event, action='Ctrl+P')
        cls._log_action('Ctrl+P', False, event, reason='no_handler')
        return None

    @classmethod
    def _on_ctrl_e(cls, event):
        fn = cls._get_handler('on_ctrl_e')
        if fn:
            return cls._run(fn, event, action='Ctrl+E')
        cls._log_action('Ctrl+E', False, event, reason='no_handler')
        return None

    @classmethod
    def _on_ctrl_f(cls, event):
        fn = cls._get_handler('on_ctrl_f')
        if fn:
            return cls._run(fn, event, action='Ctrl+F')
        cls._log_action('Ctrl+F', False, event, reason='no_handler')
        return None

    @classmethod
    def _on_ctrl_enter(cls, event):
        fn = cls._get_handler('on_ctrl_enter')
        if fn:
            return cls._run(fn, event, action='Ctrl+Enter')
        cls._log_action('Ctrl+Enter', False, event, reason='no_handler')
        return None

    @classmethod
    def _on_ctrl_shift_c(cls, event):
        fn = cls._get_handler('on_ctrl_shift_c')
        if fn:
            return cls._run(fn, event, action='Ctrl+Shift+C')
        cls._log_action('Ctrl+Shift+C', False, event, reason='no_handler')
        return None

    @classmethod
    def _on_alt_press(cls, event):
        """Alt down — wait for release; block Windows menu-bar prefix."""
        cls._alt_key_down = True
        cls._alt_chord_used = False
        return 'break'

    @classmethod
    def _on_alt_release(cls, event):
        """
        Lone Alt release focuses the first field.
        Always return break so Windows does not open the system/menu bar
        (which would steal ↑↓ after Alt).
        """
        if not cls._alt_chord_used:
            if cls._root:
                cls._root.after_idle(cls._focus_first)
            else:
                cls._focus_first()
            cls._log_action('Alt(release)', True, event, source='focus_first')
        else:
            cls._log_action('Alt(release)', False, event, reason='alt_chord_used')
        cls._alt_key_down = False
        cls._alt_chord_used = False
        return 'break'

    @classmethod
    def _on_keypress_alt_chord(cls, event):
        """Alt+Key tracking; Ctrl+Shift+C fallback (Windows Entry focus)."""
        if event.keysym in cls._ALT_MODIFIER_KEYS:
            return None
        if event.keysym.lower() == 'c' and cls._ctrl_shift_held(event):
            if cls._root is not None:
                from core.dialog_escape import grabbed_toplevel
                if grabbed_toplevel(cls._root) is not None:
                    return None
            return cls._on_ctrl_shift_c(event)
        if cls._alt_held(event):
            cls._alt_chord_used = True
        return None

    @classmethod
    def _on_sidebar_return(cls, event):
        # Treeview uses Enter to open the row action menu — do not auto-confirm.
        try:
            if event is not None and _widget_class(event.widget) == TREEVIEW_CLASS:
                return None
        except Exception:
            pass
        if cls._root is not None:
            from core.dialog_escape import grabbed_toplevel, confirm_active_dialog
            grabbed = grabbed_toplevel(cls._root)
            if grabbed is not None:
                try:
                    if (grabbed.title() or "").strip().lower() == "actions":
                        return None
                except Exception:
                    pass
                if confirm_active_dialog(cls._root):
                    return "break"
                return None
        if cls.consume_sidebar_nav_event(event):
            return "break"
        return None

    @classmethod
    def _on_f4(cls, event):
        if cls._try_settings_sidebar_f4():
            cls._log_action('F4', True, event, source='settings_sidebar')
            return 'break'
        b = cls._active
        if not b:
            cls._log_action('F4', False, event, reason='no_active_page')
            return None
        if b.on_f4_sidebar:
            return cls._run(b.on_f4_sidebar, event, action='F4')
        buttons = b.sidebar_buttons
        if buttons:
            cls._log_action('F4', True, event, source='focus_sidebar')
            return cls.focus_sidebar_button(buttons, cls._sidebar_index)
        cls._log_action('F4', False, event, reason='no_handler')
        return None

    @classmethod
    def _try_settings_sidebar_f4(cls) -> bool:
        app = cls._app
        if app is None:
            return False
        page = getattr(app, '_settings_page', None)
        if page is None:
            return False
        try:
            parent = page.parent
            if not parent.winfo_exists() or not parent.winfo_ismapped():
                return False
        except Exception:
            return False
        focus_fn = getattr(page, 'focus_active_tab_sidebar', None)
        if not callable(focus_fn):
            return False
        try:
            return bool(focus_fn())
        except Exception:
            return False

    @classmethod
    def handle_sidebar_nav(cls, event, buttons: List[Any]):
        """Up/Down/Return on settings section sidebar."""
        if not buttons:
            cls._log_action(f'sidebar:{event.keysym}', False, event, reason='no_buttons')
            return None
        key = event.keysym

        try:
            focused = event.widget
            if focused in buttons:
                cls._sidebar_index = buttons.index(focused)
        except Exception:
            pass

        if key == 'Up':
            cls._sidebar_index = (cls._sidebar_index - 1) % len(buttons)
            cls.focus_sidebar_button(buttons, cls._sidebar_index)
            cls._log_action('sidebar:Up', True, event)
            return 'break'
        if key == 'Down':
            cls._sidebar_index = (cls._sidebar_index + 1) % len(buttons)
            cls.focus_sidebar_button(buttons, cls._sidebar_index)
            cls._log_action('sidebar:Down', True, event)
            return 'break'
        if key == 'Escape':
            if cls.clear_sidebar_nav_mode():
                cls._log_action('sidebar:Esc', True, event)
                return 'break'
        if key in ('Return', 'KP_Enter'):
            try:
                buttons[cls._sidebar_index].invoke()
            except Exception:
                pass
            cls.clear_sidebar_nav_mode()
            cls._log_action('sidebar:Enter', True, event)
            return 'break'
        cls._log_action(f'sidebar:{key}', False, event, reason='unhandled')
        return None

    @classmethod
    def handle_sub_key(cls, key: str) -> bool:
        """Letter shortcuts when no input focused (legacy; prefer PageBindings.sub_keys)."""
        if not cls.should_handle_navigation():
            return False
        b = cls._active
        if not b or key not in b.sub_keys:
            return False
        cls._run(b.sub_keys[key])
        return True

    @classmethod
    def handle_settings_ctrl_digit(cls, digit: int, event=None) -> bool:
        fn = cls._settings_tab_map.get(digit)
        if fn:
            cls._run(fn, event, action=f'Ctrl+{digit}')
            return True
        return False

    @classmethod
    def audit_bindings(cls) -> List[Dict[str, str]]:
        """Return a static registry of global shortcuts (for verification scripts)."""
        rows = [
            {'shortcut': '` / grave', 'handler': '_dispatch_page_digit -> Home', 'page': 'global', 'status': 'OK'},
            {'shortcut': '0', 'handler': '_dispatch_page_digit -> Home', 'page': 'global', 'status': 'OK'},
            {'shortcut': '1', 'handler': '_dispatch_page_digit -> Sales', 'page': 'global', 'status': 'OK'},
            {'shortcut': '2', 'handler': '_dispatch_page_digit -> Purchase', 'page': 'global', 'status': 'OK'},
            {'shortcut': '3', 'handler': '_dispatch_page_digit -> Inventory', 'page': 'global', 'status': 'OK'},
            {'shortcut': '4', 'handler': '_dispatch_page_digit -> Sales History', 'page': 'global', 'status': 'OK'},
            {'shortcut': '5', 'handler': '_dispatch_page_digit -> Purchase History', 'page': 'global', 'status': 'OK'},
            {'shortcut': '6', 'handler': '_dispatch_page_digit -> Returns', 'page': 'global', 'status': 'OK'},
            {'shortcut': '7', 'handler': '_dispatch_page_digit -> Settings', 'page': 'global', 'status': 'OK'},
            {'shortcut': 'Ctrl+1..9', 'handler': '_on_settings_ctrl_digit_event', 'page': 'Settings', 'status': 'OK'},
            {'shortcut': 'Ctrl+F', 'handler': '_on_ctrl_f', 'page': 'active PageBindings', 'status': 'OK'},
            {'shortcut': 'Ctrl+E', 'handler': '_on_ctrl_e', 'page': 'active PageBindings', 'status': 'OK'},
            {'shortcut': 'Ctrl+G', 'handler': '_on_ctrl_g (=F5)', 'page': 'active PageBindings', 'status': 'OK'},
            {'shortcut': 'Ctrl+P', 'handler': '_on_ctrl_p', 'page': 'active PageBindings', 'status': 'OK'},
            {'shortcut': 'Ctrl+Shift+C', 'handler': '_on_ctrl_shift_c', 'page': 'active PageBindings', 'status': 'OK'},
            {'shortcut': 'Ctrl+Enter', 'handler': '_on_ctrl_enter', 'page': 'active PageBindings', 'status': 'OK'},
            {'shortcut': 'F2', 'handler': '_on_f2', 'page': 'active PageBindings', 'status': 'OK'},
            {'shortcut': 'F3', 'handler': '_on_f3', 'page': 'active PageBindings', 'status': 'OK'},
            {'shortcut': 'F4', 'handler': '_on_f4', 'page': 'Settings sidebar / PageBindings', 'status': 'OK'},
            {'shortcut': 'F5', 'handler': '_on_f5', 'page': 'active PageBindings', 'status': 'OK'},
            {'shortcut': 'F6', 'handler': '_on_f6', 'page': 'active PageBindings', 'status': 'OK'},
            {'shortcut': 'Shift+F2', 'handler': '_on_shift_f2', 'page': 'active PageBindings', 'status': 'OK'},
            {'shortcut': 'End', 'handler': 'on_end', 'page': 'active PageBindings', 'status': 'OK'},
            {'shortcut': 'Alt (release)', 'handler': '_focus_first', 'page': 'active PageBindings', 'status': 'OK'},
            {'shortcut': 'Esc', 'handler': 'main._on_escape + dialog_escape', 'page': 'global', 'status': 'OK'},
            {'shortcut': 'B/P/I/E', 'handler': '_on_keypress_letter_nav sub_keys', 'page': 'Home', 'status': 'OK'},
            {'shortcut': 'S/P', 'handler': '_on_keypress_letter_nav', 'page': 'Returns', 'status': 'OK'},
            {'shortcut': 'S/C', 'handler': 'PageBindings.sub_keys', 'page': 'Payment/Ledger', 'status': 'OK'},
        ]
        return rows

    @classmethod
    def blur_to_nav(cls):
        """Unfocus editable widgets so digit/letter navigation works."""
        if not cls._root:
            return
        cls.set_nav_mode(True)
        try:
            w = cls._root.focus_get()
            if w and _widget_class(w) == TREEVIEW_CLASS:
                w.selection_remove(w.selection())
        except Exception:
            pass
        try:
            w = cls._root.focus_get()
            if w and _widget_blocks_navigation(w):
                try:
                    w.tk.call('focus', '')
                except Exception:
                    pass
        except Exception:
            pass
        try:
            cls._root.focus_force()
        except Exception:
            pass
        try:
            if cls._app and hasattr(cls._app, 'main_frame'):
                mf = cls._app.main_frame
                try:
                    mf.configure(takefocus=1)
                    mf.focus_set()
                    mf.focus_force()
                except Exception:
                    pass
        except Exception:
            pass
