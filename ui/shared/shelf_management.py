import tkinter as tk
try:
    import ttkbootstrap as ttk
    from ttkbootstrap.constants import *
except ImportError:
    from tkinter import ttk
from tkinter import messagebox, simpledialog
import sqlite3
import re
from core.font_config import *


class ShelfManagementPage:
    def __init__(self, parent, conn):
        self.conn = conn
        self.cursor = conn.cursor()
        self.parent = parent
        self._selected_location = None   # long-format location string of selected node
        self._selected_type = None       # 'rack' | 'section' | 'box'
        self._selected_id = None         # DB id of selected node
        self._build_ui()
        self._load_tree()
        self._load_location_setting()

    # ── helpers ──────────────────────────────────────────────────────────

    def _loc(self, rack_name, section_name=None, box_name=None):
        """Build long-format location string from names."""
        def num(n):
            m = re.findall(r'\d+', str(n))
            return m[0] if m else str(n)
        if box_name is not None:
            return f"rack{num(rack_name)}section{num(section_name)}box{num(box_name)}"
        if section_name is not None:
            return f"rack{num(rack_name)}section{num(section_name)}"
        return f"rack{num(rack_name)}"

    def _fmt(self, location):
        """Convert long location to short display form."""
        if not location:
            return ''
        nums = re.findall(r'\d+', location)
        if 'box' in location and len(nums) >= 3:
            return f"r{nums[0]}s{nums[1]}b{nums[2]}"
        if len(nums) >= 2:
            return f"r{nums[0]}s{nums[1]}"
        return location

    def _med_count(self, location, prefix=False):
        """Count medicines at a location (prefix=True uses LIKE for subtree)."""
        try:
            if prefix:
                self.cursor.execute(
                    "SELECT COUNT(*) FROM medicines WHERE location LIKE ?",
                    (location + '%',))
            else:
                self.cursor.execute(
                    "SELECT COUNT(*) FROM medicines WHERE location = ?",
                    (location,))
            return self.cursor.fetchone()[0]
        except Exception:
            return 0

    # ── UI build ─────────────────────────────────────────────────────────

    def _build_ui(self):
        """Build the full split-pane layout."""
        outer = ttk.Frame(self.parent)
        outer.pack(fill=tk.BOTH, expand=True)

        # ── Left pane ────────────────────────────────────────────────────
        left = ttk.Frame(outer, width=320)
        left.pack(side=tk.LEFT, fill=tk.BOTH, padx=(8, 4), pady=8)
        left.pack_propagate(False)

        # Title
        ttk.Label(left, text="Shelf Structure",
                  font=(FONT_FAMILY, FONT_SIZE_SECTION_TITLE, 'bold')).pack(
            anchor='w', pady=(0, 4))

        # ── Toolbar ──────────────────────────────────────────────────────
        tb = ttk.Frame(left)
        tb.pack(fill=tk.X, pady=(0, 4))

        # Name entry (shared for add operations)
        self._name_var = tk.StringVar()
        self._name_entry = ttk.Entry(tb, textvariable=self._name_var, width=14)
        self._name_entry.pack(side=tk.LEFT, padx=(0, 4))
        self._name_entry.bind('<Return>', lambda e: self._toolbar_add())

        # Add Rack button (always visible)
        self._btn_add_rack = ttk.Button(tb, text="+ Rack",
                                        command=self._add_rack, width=7)
        self._btn_add_rack.pack(side=tk.LEFT, padx=2)

        # Add Section (visible when rack selected)
        self._btn_add_section = ttk.Button(tb, text="+ Section",
                                           command=self._add_section, width=9)
        self._btn_add_section.pack(side=tk.LEFT, padx=2)

        # Add Box (visible when section selected)
        self._btn_add_box = ttk.Button(tb, text="+ Box",
                                       command=self._add_box, width=7)
        self._btn_add_box.pack(side=tk.LEFT, padx=2)

        # Rename button
        self._btn_rename = ttk.Button(tb, text="✏ Rename",
                                      command=self._rename_selected, width=9)
        self._btn_rename.pack(side=tk.RIGHT, padx=2)

        # Delete button
        self._btn_delete = ttk.Button(tb, text="🗑 Delete",
                                      command=self._delete_selected, width=9)
        self._btn_delete.pack(side=tk.RIGHT, padx=2)

        # ── Shelf tree ───────────────────────────────────────────────────
        tree_frame = ttk.Frame(left)
        tree_frame.pack(fill=tk.BOTH, expand=True)

        self._tree = ttk.Treeview(tree_frame,
                                  columns=('type', 'id', 'loc', 'count'),
                                  show='tree',
                                  selectmode='browse',
                                  style='Large.Treeview')
        self._tree.column('#0', width=270, stretch=True)

        vsb = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL,
                            command=self._tree.yview)
        self._tree.configure(yscrollcommand=vsb.set)
        self._tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)

        self._tree.bind('<<TreeviewSelect>>', self._on_tree_select)
        self._tree.bind('<Double-1>', self._on_tree_double)
        self._tree.bind('<F2>', lambda e: self._rename_selected())
        self._tree.bind('<Delete>', lambda e: self._delete_selected())

        from core.tree_action_menu import setup_tree_actions
        self._action_menu = setup_tree_actions(
            self.parent,
            self._tree,
            actions=[],
            actions_factory=self._context_action_items,
            on_double=self._on_tree_double,
            escape_to=self._name_entry,
        )
        self._ctx_menu = self._action_menu.ctx_menu

        # ── Show-location checkbox ────────────────────────────────────────
        self._show_loc_var = tk.BooleanVar()
        ttk.Checkbutton(left,
                        text="Show Location column in Inventory & Billing",
                        variable=self._show_loc_var,
                        command=self._save_location_setting).pack(
            anchor='w', pady=(6, 0))

        # ── Right pane placeholder (will be filled in next step) ─────────
        self._right = ttk.Frame(outer)
        self._right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True,
                         padx=(4, 8), pady=8)
        self._show_right_placeholder()

        # Initial toolbar state
        self._refresh_toolbar()

    def _show_right_placeholder(self):
        """Show hint text in right pane when nothing is selected."""
        for w in self._right.winfo_children():
            w.destroy()
        ttk.Label(self._right,
                  text="← Select a Rack, Section or Box\nto manage medicines",
                  font=(FONT_FAMILY, FONT_SIZE_SECTION_TITLE),
                  justify='center').place(relx=0.5, rely=0.45, anchor='center')

    # ── Toolbar state ─────────────────────────────────────────────────────

    def _refresh_toolbar(self):
        """Enable/disable toolbar buttons based on current selection."""
        t = self._selected_type
        state_section = tk.NORMAL if t == 'rack'    else tk.DISABLED
        state_box     = tk.NORMAL if t == 'section' else tk.DISABLED
        state_action  = tk.NORMAL if t is not None  else tk.DISABLED

        self._btn_add_section.config(state=state_section)
        self._btn_add_box.config(state=state_box)
        self._btn_delete.config(state=state_action)
        self._btn_rename.config(state=state_action)

    def _context_action_items(self):
        """Same options as the right-click menu (for Enter key popup)."""
        t = self._selected_type
        items = []
        if t == "rack":
            items.append(("+ Add Section", self._add_section))
        elif t == "section":
            items.append(("+ Add Box", self._add_box))
        if t is not None:
            if items:
                items.append("---")
            items.extend([
                ("Rename", self._rename_selected),
                ("Delete", self._delete_selected),
            ])
        return items

    def _show_context_menu(self, event):
        """Show right-click context menu on the tree."""
        item = self._tree.identify_row(event.y)
        if not item:
            return
        self._tree.selection_set(item)
        # _on_tree_select fires via <<TreeviewSelect>> but call manually to be safe
        vals = self._tree.item(item, 'values')
        if not vals:
            return
        t = vals[0]

        self._ctx_menu.delete(0, tk.END)
        if t == 'rack':
            self._ctx_menu.add_command(label="+ Add Section",
                                       command=self._add_section)
        elif t == 'section':
            self._ctx_menu.add_command(label="+ Add Box",
                                       command=self._add_box)
        self._ctx_menu.add_separator()
        self._ctx_menu.add_command(label="✏  Rename",
                                   command=self._rename_selected)
        self._ctx_menu.add_command(label="🗑  Delete",
                                   command=self._delete_selected)
        self._ctx_menu.post(event.x_root, event.y_root)

    def _toolbar_add(self):
        """Enter key in name entry — smart add based on selection."""
        t = self._selected_type
        if t is None:
            self._add_rack()
        elif t == 'rack':
            self._add_section()
        elif t == 'section':
            self._add_box()

    # ── Tree loading ──────────────────────────────────────────────────────

    def _load_tree(self):
        """Reload the entire shelf tree from DB."""
        # Remember expanded nodes
        expanded = {self._tree.item(n, 'text')
                    for n in self._tree.get_children('')
                    if self._tree.item(n, 'open')}

        for n in self._tree.get_children(''):
            self._tree.delete(n)

        self.cursor.execute("SELECT id, name FROM racks ORDER BY name")
        for rack_id, rack_name in self.cursor.fetchall():
            rack_loc = self._loc(rack_name)
            rack_count = self._med_count(rack_loc, prefix=True)
            rack_label = f"🗄  {rack_name}  ({rack_count})"
            rack_node = self._tree.insert(
                '', tk.END, text=rack_label,
                values=('rack', rack_id, rack_loc, rack_count),
                open=(rack_label in expanded or True))

            self.cursor.execute(
                "SELECT id, name FROM sections WHERE rack_id=? ORDER BY name",
                (rack_id,))
            for sec_id, sec_name in self.cursor.fetchall():
                sec_loc = self._loc(rack_name, sec_name)
                sec_count = self._med_count(sec_loc, prefix=True)
                sec_label = f"📂  {sec_name}  ({sec_count})"
                sec_node = self._tree.insert(
                    rack_node, tk.END, text=sec_label,
                    values=('section', sec_id, sec_loc, sec_count),
                    open=True)

                self.cursor.execute(
                    "SELECT id, name FROM boxes WHERE section_id=? ORDER BY name",
                    (sec_id,))
                for box_id, box_name in self.cursor.fetchall():
                    box_loc = self._loc(rack_name, sec_name, box_name)
                    box_count = self._med_count(box_loc)
                    box_label = f"📦  {box_name}  ({box_count})"
                    self._tree.insert(
                        sec_node, tk.END, text=box_label,
                        values=('box', box_id, box_loc, box_count))

    # ── Tree events ───────────────────────────────────────────────────────

    def _on_tree_select(self, event=None):
        sel = self._tree.selection()
        if not sel:
            self._selected_type = None
            self._selected_id = None
            self._selected_location = None
            self._refresh_toolbar()
            self._show_right_placeholder()
            return
        vals = self._tree.item(sel[0], 'values')
        if not vals:
            return
        self._selected_type = vals[0]
        self._selected_id = int(vals[1])
        self._selected_location = vals[2]
        self._refresh_toolbar()
        # Right pane will be built in next step
        self._show_right_placeholder()

    def _on_tree_double(self, event=None):
        """Double-click on tree node triggers rename."""
        # Only rename if click is on the text label, not the expand arrow
        if event:
            region = self._tree.identify_region(event.x, event.y)
            if region not in ('cell', 'tree'):
                return
        self._rename_selected()

    # ── CRUD: Rename ──────────────────────────────────────────────────────

    def _rename_selected(self):
        """Inline rename for the selected rack / section / box."""
        if self._selected_type is None:
            return
        t   = self._selected_type
        iid = self._selected_id
        loc = self._selected_location

        # Get current name from DB
        if t == 'rack':
            self.cursor.execute("SELECT name FROM racks WHERE id=?", (iid,))
        elif t == 'section':
            self.cursor.execute("SELECT name FROM sections WHERE id=?", (iid,))
        else:
            self.cursor.execute("SELECT name FROM boxes WHERE id=?", (iid,))
        row = self.cursor.fetchone()
        if not row:
            return
        old_name = row[0]

        new_name = simpledialog.askstring(
            f"Rename {t.title()}",
            f"New name for {t} '{old_name}':",
            initialvalue=old_name,
            parent=self.parent)
        if not new_name or new_name.strip() == old_name:
            return
        new_name = new_name.strip()

        try:
            if t == 'rack':
                # Build old and new location prefixes
                old_prefix = self._loc(old_name)
                new_prefix = self._loc(new_name)
                # Update all medicine locations that start with old prefix
                self.cursor.execute(
                    "SELECT id, location FROM medicines "
                    "WHERE location LIKE ?", (old_prefix + '%',))
                for mid, mloc in self.cursor.fetchall():
                    new_mloc = new_prefix + mloc[len(old_prefix):]
                    self.cursor.execute(
                        "UPDATE medicines SET location=? WHERE id=?",
                        (new_mloc, mid))
                self.cursor.execute(
                    "UPDATE racks SET name=? WHERE id=?", (new_name, iid))

            elif t == 'section':
                # Need rack name to rebuild location
                self.cursor.execute(
                    "SELECT r.name FROM racks r "
                    "JOIN sections s ON s.rack_id=r.id WHERE s.id=?", (iid,))
                rack_name = self.cursor.fetchone()[0]
                old_prefix = self._loc(rack_name, old_name)
                new_prefix = self._loc(rack_name, new_name)
                self.cursor.execute(
                    "SELECT id, location FROM medicines "
                    "WHERE location LIKE ?", (old_prefix + '%',))
                for mid, mloc in self.cursor.fetchall():
                    new_mloc = new_prefix + mloc[len(old_prefix):]
                    self.cursor.execute(
                        "UPDATE medicines SET location=? WHERE id=?",
                        (new_mloc, mid))
                self.cursor.execute(
                    "UPDATE sections SET name=? WHERE id=?", (new_name, iid))

            elif t == 'box':
                # Need rack + section names
                self.cursor.execute(
                    "SELECT r.name, s.name FROM racks r "
                    "JOIN sections s ON s.rack_id=r.id "
                    "JOIN boxes b ON b.section_id=s.id WHERE b.id=?", (iid,))
                rack_name, sec_name = self.cursor.fetchone()
                old_loc = self._loc(rack_name, sec_name, old_name)
                new_loc = self._loc(rack_name, sec_name, new_name)
                self.cursor.execute(
                    "UPDATE medicines SET location=? WHERE location=?",
                    (new_loc, old_loc))
                self.cursor.execute(
                    "UPDATE boxes SET name=? WHERE id=?", (new_name, iid))

            self.conn.commit()
            # Update selected location to reflect new name
            self._selected_location = self._selected_location.replace(
                loc, self._selected_location)  # will be refreshed by _load_tree
            self._load_tree()
            # Rebuild right pane if it was open
            if self._selected_type in ('section', 'box'):
                # Recalculate new location after rename
                if t == 'rack':
                    pass  # rack selected → right pane shows hint anyway
                else:
                    # Re-select the renamed node by finding it in the tree
                    self._reselect_after_rename(new_name, t)
        except Exception as e:
            self.conn.rollback()
            messagebox.showerror("Error", f"Failed to rename: {e}")

    def _reselect_after_rename(self, new_name, node_type):
        """After rename, find and re-select the renamed node in the tree."""
        emoji = {'rack': '🗄', 'section': '📂', 'box': '📦'}.get(node_type, '')
        for rack_node in self._tree.get_children(''):
            if node_type == 'rack':
                text = self._tree.item(rack_node, 'text')
                if f'  {new_name}  ' in text:
                    self._tree.selection_set(rack_node)
                    self._tree.see(rack_node)
                    return
            for sec_node in self._tree.get_children(rack_node):
                if node_type == 'section':
                    text = self._tree.item(sec_node, 'text')
                    if f'  {new_name}  ' in text:
                        self._tree.selection_set(sec_node)
                        self._tree.see(sec_node)
                        return
                for box_node in self._tree.get_children(sec_node):
                    if node_type == 'box':
                        text = self._tree.item(box_node, 'text')
                        if f'  {new_name}  ' in text:
                            self._tree.selection_set(box_node)
                            self._tree.see(box_node)
                            return

    # ── CRUD: Rack ────────────────────────────────────────────────────────

    def _add_rack(self):
        name = self._name_var.get().strip()
        if not name:
            name = simpledialog.askstring("Add Rack", "Rack name:",
                                          parent=self.parent)
        if not name:
            return
        name = name.strip()
        if not name:
            return
        try:
            self.cursor.execute("INSERT INTO racks (name) VALUES (?)", (name,))
            self.conn.commit()
            self._name_var.set('')
            self._load_tree()
        except sqlite3.IntegrityError:
            messagebox.showerror("Duplicate", f"Rack '{name}' already exists.")
        except Exception as e:
            messagebox.showerror("Error", str(e))

    # ── CRUD: Section ─────────────────────────────────────────────────────

    def _add_section(self):
        if self._selected_type != 'rack':
            messagebox.showwarning("Select Rack",
                                   "Please select a Rack first.")
            return
        name = self._name_var.get().strip()
        if not name:
            name = simpledialog.askstring("Add Section", "Section name:",
                                          parent=self.parent)
        if not name:
            return
        name = name.strip()
        if not name:
            return
        try:
            self.cursor.execute(
                "INSERT INTO sections (rack_id, name) VALUES (?, ?)",
                (self._selected_id, name))
            self.conn.commit()
            self._name_var.set('')
            self._load_tree()
        except sqlite3.IntegrityError:
            messagebox.showerror("Duplicate",
                                 f"Section '{name}' already exists in this rack.")
        except Exception as e:
            messagebox.showerror("Error", str(e))

    # ── CRUD: Box ─────────────────────────────────────────────────────────

    def _add_box(self):
        if self._selected_type != 'section':
            messagebox.showwarning("Select Section",
                                   "Please select a Section first.")
            return
        name = self._name_var.get().strip()
        if not name:
            name = simpledialog.askstring("Add Box", "Box name:",
                                          parent=self.parent)
        if not name:
            return
        name = name.strip()
        if not name:
            return
        try:
            self.cursor.execute(
                "INSERT INTO boxes (section_id, name) VALUES (?, ?)",
                (self._selected_id, name))
            self.conn.commit()
            self._name_var.set('')
            self._load_tree()
        except sqlite3.IntegrityError:
            messagebox.showerror("Duplicate",
                                 f"Box '{name}' already exists in this section.")
        except Exception as e:
            messagebox.showerror("Error", str(e))

    # ── CRUD: Delete ──────────────────────────────────────────────────────

    def _delete_selected(self):
        if self._selected_type is None:
            return
        t = self._selected_type
        item_id = self._selected_id
        loc = self._selected_location

        label = self._tree.item(self._tree.selection()[0], 'text').strip()
        if not messagebox.askyesno(
                "Confirm Delete",
                f"Delete {t} '{label}' and all its contents?\n"
                "Medicines will be unassigned from this location."):
            return

        try:
            if t == 'rack':
                # Clear all medicines whose location starts with this rack prefix
                self.cursor.execute(
                    "UPDATE medicines SET location='' WHERE location LIKE ?",
                    (loc + '%',))
                # Delete all boxes → sections → rack (cascade via FK or manual)
                self.cursor.execute(
                    "DELETE FROM boxes WHERE section_id IN "
                    "(SELECT id FROM sections WHERE rack_id=?)", (item_id,))
                self.cursor.execute(
                    "DELETE FROM sections WHERE rack_id=?", (item_id,))
                self.cursor.execute(
                    "DELETE FROM racks WHERE id=?", (item_id,))

            elif t == 'section':
                self.cursor.execute(
                    "UPDATE medicines SET location='' WHERE location LIKE ?",
                    (loc + '%',))
                self.cursor.execute(
                    "DELETE FROM boxes WHERE section_id=?", (item_id,))
                self.cursor.execute(
                    "DELETE FROM sections WHERE id=?", (item_id,))

            elif t == 'box':
                self.cursor.execute(
                    "UPDATE medicines SET location='' WHERE location=?",
                    (loc,))
                self.cursor.execute(
                    "DELETE FROM boxes WHERE id=?", (item_id,))

            self.conn.commit()
            self._selected_type = None
            self._selected_id = None
            self._selected_location = None
            self._load_tree()
            self._refresh_toolbar()
            self._show_right_placeholder()
        except Exception as e:
            self.conn.rollback()
            messagebox.showerror("Error", f"Failed to delete: {e}")

    # ── Location setting ──────────────────────────────────────────────────

    def _load_location_setting(self):
        try:
            self.cursor.execute(
                "CREATE TABLE IF NOT EXISTS shelf_settings "
                "(id INTEGER PRIMARY KEY, show_location INTEGER DEFAULT 0)")
            self.cursor.execute(
                "SELECT show_location FROM shelf_settings LIMIT 1")
            row = self.cursor.fetchone()
            self._show_loc_var.set(bool(row[0]) if row else False)
        except Exception:
            pass

    def _save_location_setting(self):
        try:
            self.cursor.execute(
                "INSERT OR REPLACE INTO shelf_settings (id, show_location) "
                "VALUES (1, ?)", (int(self._show_loc_var.get()),))
            self.conn.commit()
        except Exception as e:
            print(f"Error saving location setting: {e}")

    # ── Right pane: medicine assignment ──────────────────────────────────

    def _build_right_pane(self, location, label):
        """Build the medicine assignment panel for the given location."""
        for w in self._right.winfo_children():
            w.destroy()

        # ── Header ───────────────────────────────────────────────────────
        hdr = ttk.Frame(self._right)
        hdr.pack(fill=tk.X, pady=(0, 6))
        ttk.Label(hdr,
                  text=f"📍  {label}  —  {self._fmt(location)}",
                  font=(FONT_FAMILY, FONT_SIZE_SECTION_TITLE, 'bold')).pack(
            side=tk.LEFT)

        # ── Two-column layout ─────────────────────────────────────────────
        cols = ttk.Frame(self._right)
        cols.pack(fill=tk.BOTH, expand=True)
        cols.columnconfigure(0, weight=1)
        cols.columnconfigure(2, weight=1)

        # ── Left list: Assigned ───────────────────────────────────────────
        assigned_frame = ttk.LabelFrame(cols, text="Assigned to this location")
        assigned_frame.grid(row=0, column=0, sticky='nsew', padx=(0, 4))

        # Search bar
        a_search_var = tk.StringVar()
        a_sf = ttk.Frame(assigned_frame)
        a_sf.pack(fill=tk.X, padx=6, pady=(6, 2))
        ttk.Label(a_sf, text="🔍").pack(side=tk.LEFT)
        ttk.Entry(a_sf, textvariable=a_search_var, width=18).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=(4, 0))

        assigned_tree = ttk.Treeview(
            assigned_frame,
            columns=('name', 'batch', 'stock'),
            show='headings',
            height=14,
            style='Large.Treeview')
        assigned_tree.heading('name',  text='Medicine')
        assigned_tree.heading('batch', text='Batch')
        assigned_tree.heading('stock', text='Stock')
        assigned_tree.column('name',  width=160, stretch=True)
        assigned_tree.column('batch', width=80,  stretch=False)
        assigned_tree.column('stock', width=55,  stretch=False)

        a_vsb = ttk.Scrollbar(assigned_frame, orient=tk.VERTICAL,
                               command=assigned_tree.yview)
        assigned_tree.configure(yscrollcommand=a_vsb.set)
        assigned_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True,
                           padx=(6, 0), pady=(2, 6))
        a_vsb.pack(side=tk.LEFT, fill=tk.Y, pady=(2, 6), padx=(0, 4))

        # ── Centre arrow buttons ──────────────────────────────────────────
        btn_col = ttk.Frame(cols)
        btn_col.grid(row=0, column=1, padx=6)
        # vertical centering spacer
        ttk.Label(btn_col, text='').pack(expand=True, fill=tk.BOTH)

        try:
            btn_assign = ttk.Button(btn_col, text="◀  Assign",
                                    bootstyle="success", width=10)
            btn_remove = ttk.Button(btn_col, text="Remove  ▶",
                                    bootstyle="danger",  width=10)
        except Exception:
            btn_assign = ttk.Button(btn_col, text="◀  Assign", width=10)
            btn_remove = ttk.Button(btn_col, text="Remove  ▶", width=10)

        btn_assign.pack(pady=6)
        btn_remove.pack(pady=6)
        ttk.Label(btn_col, text='').pack(expand=True, fill=tk.BOTH)

        # ── Right list: Unassigned ────────────────────────────────────────
        unassigned_frame = ttk.LabelFrame(cols, text="Unassigned medicines")
        unassigned_frame.grid(row=0, column=2, sticky='nsew', padx=(4, 0))

        u_search_var = tk.StringVar()
        u_sf = ttk.Frame(unassigned_frame)
        u_sf.pack(fill=tk.X, padx=6, pady=(6, 2))
        ttk.Label(u_sf, text="🔍").pack(side=tk.LEFT)
        ttk.Entry(u_sf, textvariable=u_search_var, width=18).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=(4, 0))

        unassigned_tree = ttk.Treeview(
            unassigned_frame,
            columns=('name', 'batch', 'stock'),
            show='headings',
            height=14,
            style='Large.Treeview')
        unassigned_tree.heading('name',  text='Medicine')
        unassigned_tree.heading('batch', text='Batch')
        unassigned_tree.heading('stock', text='Stock')
        unassigned_tree.column('name',  width=160, stretch=True)
        unassigned_tree.column('batch', width=80,  stretch=False)
        unassigned_tree.column('stock', width=55,  stretch=False)

        u_vsb = ttk.Scrollbar(unassigned_frame, orient=tk.VERTICAL,
                               command=unassigned_tree.yview)
        unassigned_tree.configure(yscrollcommand=u_vsb.set)
        unassigned_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True,
                             padx=(6, 0), pady=(2, 6))
        u_vsb.pack(side=tk.LEFT, fill=tk.Y, pady=(2, 6), padx=(0, 4))

        # ── Status bar ────────────────────────────────────────────────────
        self._status_var = tk.StringVar()
        ttk.Label(self._right, textvariable=self._status_var,
                  font=(FONT_FAMILY, FONT_SIZE_SUPPORTING_TEXT),
                  foreground='gray').pack(anchor='w', pady=(4, 0))

        # ── Data loading ──────────────────────────────────────────────────

        def _load(a_search='', u_search=''):
            # Assigned
            for row in assigned_tree.get_children():
                assigned_tree.delete(row)
            self.cursor.execute(
                "SELECT id, name, batch_no, stock_qty FROM medicines "
                "WHERE location=? ORDER BY name",
                (location,))
            a_rows = self.cursor.fetchall()
            a_s = a_search.lower()
            for mid, name, batch, stock in a_rows:
                if a_s and a_s not in name.lower() and a_s not in (batch or '').lower():
                    continue
                assigned_tree.insert('', tk.END,
                                     values=(name, batch or '', stock),
                                     tags=(mid,))

            # Unassigned
            for row in unassigned_tree.get_children():
                unassigned_tree.delete(row)
            self.cursor.execute(
                "SELECT id, name, batch_no, stock_qty FROM medicines "
                "WHERE (location='' OR location IS NULL) ORDER BY name")
            u_rows = self.cursor.fetchall()
            u_s = u_search.lower()
            sw, co = [], []
            for mid, name, batch, stock in u_rows:
                if u_s:
                    nl, bl = name.lower(), (batch or '').lower()
                    if nl.startswith(u_s) or bl.startswith(u_s):
                        sw.append((mid, name, batch, stock))
                    elif u_s in nl or u_s in bl:
                        co.append((mid, name, batch, stock))
                else:
                    sw.append((mid, name, batch, stock))
            for mid, name, batch, stock in sw + co:
                unassigned_tree.insert('', tk.END,
                                       values=(name, batch or '', stock),
                                       tags=(mid,))

            # Update status
            a_total = len(assigned_tree.get_children())
            u_total = len(unassigned_tree.get_children())
            self._status_var.set(
                f"{a_total} assigned  •  {u_total} unassigned shown")

        # ── Actions ───────────────────────────────────────────────────────

        def _assign(event=None):
            """Move selected unassigned medicine → this location."""
            sel = unassigned_tree.selection()
            if not sel:
                return
            mid = unassigned_tree.item(sel[0], 'tags')[0]
            try:
                self.cursor.execute(
                    "UPDATE medicines SET location=? WHERE id=?",
                    (location, mid))
                self.conn.commit()
                _load(a_search_var.get(), u_search_var.get())
                self._load_tree()
            except Exception as e:
                messagebox.showerror("Error", str(e))

        def _remove(event=None):
            """Remove selected assigned medicine from this location."""
            sel = assigned_tree.selection()
            if not sel:
                return
            mid = assigned_tree.item(sel[0], 'tags')[0]
            try:
                self.cursor.execute(
                    "UPDATE medicines SET location='' WHERE id=?", (mid,))
                self.conn.commit()
                _load(a_search_var.get(), u_search_var.get())
                self._load_tree()
            except Exception as e:
                messagebox.showerror("Error", str(e))

        # Wire buttons
        btn_assign.config(command=_assign)
        btn_remove.config(command=_remove)

        # Double-click: unassigned → assign; assigned → remove
        unassigned_tree.bind('<Double-1>', _assign)
        assigned_tree.bind('<Double-1>',   _remove)

        # Delete key on assigned list removes
        assigned_tree.bind('<Delete>', _remove)

        # Search live filter
        a_search_var.trace_add('write',
            lambda *_: _load(a_search_var.get(), u_search_var.get()))
        u_search_var.trace_add('write',
            lambda *_: _load(a_search_var.get(), u_search_var.get()))

        # Keyboard shortcut hints
        ttk.Label(self._right,
                  text="Tip: Double-click to assign/remove  •  Delete key removes from location",
                  font=(FONT_FAMILY, FONT_SIZE_SUPPORTING_TEXT),
                  foreground='gray').pack(anchor='w')

        _load()

    # ── Override _on_tree_select to build right pane ──────────────────────

    def _on_tree_select(self, event=None):
        sel = self._tree.selection()
        if not sel:
            self._selected_type = None
            self._selected_id = None
            self._selected_location = None
            self._refresh_toolbar()
            self._show_right_placeholder()
            return

        vals = self._tree.item(sel[0], 'values')
        if not vals:
            return

        self._selected_type = vals[0]
        self._selected_id = int(vals[1])
        self._selected_location = vals[2]
        self._refresh_toolbar()

        # Build right pane for section or box only
        # (rack has no direct location — show placeholder with hint)
        if self._selected_type == 'rack':
            for w in self._right.winfo_children():
                w.destroy()
            ttk.Label(self._right,
                      text="Select a Section or Box\nto assign medicines",
                      font=(FONT_FAMILY, FONT_SIZE_SECTION_TITLE),
                      justify='center').place(
                relx=0.5, rely=0.45, anchor='center')
        else:
            raw = self._tree.item(sel[0], 'text').strip()
            # Strip emoji prefix and count suffix for clean label
            label = raw.split('  ')[1] if '  ' in raw else raw
            self._build_right_pane(self._selected_location, label)
