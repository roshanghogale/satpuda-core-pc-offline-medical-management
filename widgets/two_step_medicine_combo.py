import tkinter as tk
from tkinter import ttk
from core.font_config import *

class TwoStepMedicineCombo(ttk.Frame):
    def __init__(self, master, conn, width=60, *args, **kwargs):
        super().__init__(master, *args, **kwargs)
        
        self.conn = conn
        self.cursor = conn.cursor()
        self.width = width
        self.selected_medicine = None
        self.next_focus_widget = None
        
        # Step 1: Medicine name selection
        self.step1_var = tk.StringVar()
        self.step1_entry = ttk.Entry(self, textvariable=self.step1_var, width=width)
        self.step1_entry.pack(fill=tk.X)
        
        # Step 1 treeview (medicine names) — defer toplevel lookup until after pack/grid
        step1_columns = ('name', 'pack_size', 'stock', 'mrp', 'manufacturer')
        self._step1_columns = step1_columns
        self.step1_tree = None  # created lazily in _init_trees()
        self._step2_columns = ('batch', 'pack', 'expiry', 'stock', 'rate', 'mrp', 'manufacturer', 'schedule')
        self.step2_tree = None  # created lazily in _init_trees()
        self.after(0, self._init_trees)
        self.step1_visible = False
        self.step2_visible = False
        self.medicine_names = []
        self.filtered_medicines = []
        self.variants = []
        self._filter_pending = False   # debounce flag
        # Bind events — do NOT trace step1_var; drive filtering from key events only
        self.step1_entry.bind("<KeyRelease>", self.on_step1_key)
        self.step1_entry.bind("<Down>", self.on_step1_down)
        self.step1_entry.bind("<Up>", self.on_step1_up)
        self.step1_entry.bind("<Return>", self.on_step1_return)
        self.step1_entry.bind("<Tab>", self.on_step1_tab)
        self.step1_entry.bind("<FocusIn>", self.on_step1_focus_in)
        self.step1_entry.bind("<FocusOut>", self.on_step1_focus_out)
        self.step1_entry.bind("<Escape>", self.on_step1_escape)
        # Close dropdowns when clicking outside
        # Bind click-outside on the toplevel so each instance checks its own widgets
        self.winfo_toplevel().bind("<Button-1>", self.on_click_outside, add="+")
        self.load_medicine_names()
        self.after(0, self._bind_global_return)
        # Hide dropdowns when this widget is hidden or destroyed
        self.bind("<Unmap>", lambda e: (self.hide_step1(), self.hide_step2()))
        self.bind("<Destroy>", lambda e: (self.hide_step1(), self.hide_step2()))
        self.after(0, self._bind_ancestor_unmap)

    def _bind_global_return(self):
        """Bind global Return to the correct toplevel after widget is placed"""
        try:
            self.winfo_toplevel().bind("<Return>", self.on_global_return, add="+")
        except tk.TclError:
            pass

    def _bind_ancestor_unmap(self):
        """Walk up the widget tree and bind <Unmap> on every ancestor frame."""
        try:
            w = self.master
            toplevel = self.winfo_toplevel()
            while w and w is not toplevel:
                w.bind("<Unmap>", lambda e: (self.hide_step1(), self.hide_step2()), add="+")
                w = w.master
        except Exception:
            pass

    def _init_trees(self):
        """Create floating treeviews after widget is fully placed in hierarchy"""
        toplevel = self.winfo_toplevel()
        step1_columns = self._step1_columns
        
        # Configure style safely
        try:
            style = ttk.Style()
            style.configure("Treeview", borderwidth=0)
            style.configure("Treeview.Heading", borderwidth=0)
        except Exception:
            pass

        self.step1_tree = ttk.Treeview(toplevel, columns=step1_columns, show='headings', height=10)
        for col in step1_columns:
            self.step1_tree.column(col, anchor='w')
        self.step1_tree.heading('name', text='Medicine Name')
        self.step1_tree.heading('pack_size', text='Pack Size')
        self.step1_tree.heading('stock', text='Stock')
        self.step1_tree.heading('mrp', text='MRP')
        self.step1_tree.heading('manufacturer', text='Manufacturer')
        self.step1_tree.column('name', width=200, minwidth=50)
        self.step1_tree.column('pack_size', width=80, minwidth=50)
        self.step1_tree.column('stock', width=60, minwidth=50)
        self.step1_tree.column('mrp', width=80, minwidth=50)
        self.step1_tree.column('manufacturer', width=120, minwidth=50)
        self.step1_tree.place_forget()

        step2_columns = self._step2_columns
        self.step2_tree = ttk.Treeview(toplevel, columns=step2_columns, show='headings', height=8)
        self.step2_tree.heading('batch', text='Batch')
        self.step2_tree.heading('pack', text='Pack')
        self.step2_tree.heading('expiry', text='Expiry')
        self.step2_tree.heading('stock', text='Stock')
        self.step2_tree.heading('rate', text='Rate')
        self.step2_tree.heading('mrp', text='MRP')
        self.step2_tree.heading('manufacturer', text='Manufacturer')
        self.step2_tree.heading('schedule', text='Sch')
        self.step2_tree.column('batch', width=80, minwidth=50)
        self.step2_tree.column('pack', width=60, minwidth=50)
        self.step2_tree.column('expiry', width=70, minwidth=50)
        self.step2_tree.column('stock', width=60, minwidth=50)
        self.step2_tree.column('rate', width=70, minwidth=50)
        self.step2_tree.column('mrp', width=70, minwidth=50)
        self.step2_tree.column('manufacturer', width=100, minwidth=50)
        self.step2_tree.column('schedule', width=40, minwidth=30)
        self.step2_tree.place_forget()

        self.step1_tree.bind("<Return>", self.on_step1_select)
        self.step1_tree.bind("<Double-Button-1>", self.on_step1_select)
        self.step1_tree.bind("<ButtonRelease-1>", self.on_step1_click)
        self.step1_tree.bind("<Escape>", lambda e: self.hide_step1())
        self.step2_tree.bind("<Return>", self.on_step2_select)
        self.step2_tree.bind("<Key-Return>", self.on_step2_select)
        self.step2_tree.bind("<Double-Button-1>", self.on_step2_select)
        self.step2_tree.bind("<ButtonRelease-1>", self.on_step2_click)
        self.step2_tree.bind("<Escape>", lambda e: self.hide_step2())
        self.step2_tree.bind("<Up>", self.on_step2_up)
        self.step2_tree.bind("<Down>", self.on_step2_down)
        self.step2_tree.bind("<KeyPress>", self.on_step2_key)
        
    def load_medicine_names(self):
        """No-op — medicine names come entirely from medicines_master via live SQL."""
        self.medicine_names = []
        self._purchased_names_lower = set()

    def _query_master(self, search: str, limit: int = 50):
        """Query medicines (inventory) with case-insensitive prefix+contains search.
        Shows only medicines that exist in the local inventory.
        Empty search returns first `limit` names alphabetically."""
        try:
            if not search or not search.strip():
                self.cursor.execute(
                    "SELECT DISTINCT name, manufacturer, mrp, type, unit "
                    "FROM medicines "
                    "ORDER BY name COLLATE NOCASE LIMIT ?", (limit,))
                rows = self.cursor.fetchall()
            else:
                s = search.strip()
                self.cursor.execute(
                    "SELECT DISTINCT name, manufacturer, mrp, type, unit "
                    "FROM medicines "
                    "WHERE name LIKE ? COLLATE NOCASE "
                    "ORDER BY name COLLATE NOCASE LIMIT ?",
                    (f"{s}%", limit))
                prefix = self.cursor.fetchall()
                contains = []
                if len(prefix) < limit:
                    prefix_names = {r[0].lower() for r in prefix}
                    self.cursor.execute(
                        "SELECT DISTINCT name, manufacturer, mrp, type, unit "
                        "FROM medicines "
                        "WHERE name LIKE ? COLLATE NOCASE "
                        "  AND name NOT LIKE ? COLLATE NOCASE "
                        "ORDER BY name COLLATE NOCASE LIMIT ?",
                        (f"%{s}%", f"{s}%", limit - len(prefix)))
                    contains = [r for r in self.cursor.fetchall()
                                if r[0].lower() not in prefix_names]
                rows = prefix + contains

            return [{
                'name': r[0], 'pack_info': r[4] or '',
                'type': r[3] or '', 'unit': r[4] or '',
                'stock': 0, 'mrp': r[2] or 0,
                'manufacturer': r[1] or '', 'source': 'inventory'
            } for r in rows]
        except Exception:
            return []
    
    def on_step1_change(self, *args):
        """Filter from medicines_master only — case-insensitive."""
        if not hasattr(self, 'step1_entry') or self.step1_tree is None:
            return

        search = self.step1_var.get().strip()

        for item in self.step1_tree.get_children():
            self.step1_tree.delete(item)

        self.filtered_medicines = self._query_master(search, limit=50)

        if not self.filtered_medicines:
            self.hide_step1()
            return

        for med in self.filtered_medicines:
            self.step1_tree.insert('', tk.END, values=(
                f" {med['name']}",
                f" {med['pack_info']}",
                ' —',
                f" ₹{med['mrp']:.1f}" if med['mrp'] else ' —',
                f" {med['manufacturer']}"
            ))

        self.show_step1()
        children = self.step1_tree.get_children()
        if children:
            self.step1_tree.selection_set(children[0])
            self.step1_tree.focus(children[0])
    
    def on_step1_focus_in(self, event):
        """Show dropdown immediately on focus using current entry text."""
        self.after(10, self._do_filter)

    def on_step1_key(self, event):
        """Trigger filter on every key except navigation keys."""
        if event.keysym in ("Up", "Down", "Return", "Escape", "Tab"):
            return
        # Cancel any pending filter and schedule a fresh one
        if self._filter_pending:
            try:
                self.after_cancel(self._filter_pending)
            except Exception:
                pass
        self._filter_pending = self.after(80, self._do_filter)

    def _do_filter(self):
        """Read current text from entry and refresh the dropdown."""
        self._filter_pending = False
        if self.step1_tree is None:
            return
        # Read directly from the widget — always current, never stale
        search = self.step1_entry.get().strip()
        self.on_step1_change(search)

    def on_step1_change(self, search=''):
        """Populate step1 tree from medicines_master filtered by `search`."""
        if self.step1_tree is None:
            return

        for item in self.step1_tree.get_children():
            self.step1_tree.delete(item)

        self.filtered_medicines = self._query_master(search.strip(), limit=50)

        if not self.filtered_medicines:
            self.hide_step1()
            return

        for med in self.filtered_medicines:
            self.step1_tree.insert('', tk.END, values=(
                f" {med['name']}",
                f" {med['pack_info']}",
                ' —',
                f" ₹{med['mrp']:.1f}" if med['mrp'] else ' —',
                f" {med['manufacturer']}"
            ))

        self.show_step1()
        children = self.step1_tree.get_children()
        if children:
            self.step1_tree.selection_set(children[0])
            self.step1_tree.focus(children[0])
    
    def on_step1_escape(self, event):
        if self.step2_visible:
            self.hide_step2()
            return "break"
        if self.step1_visible:
            self.hide_step1()
            return "break"
        return None

    def on_step1_focus_out(self, event):
        self.after(100, self._check_focus_and_hide_step1)

    def on_step1_down(self, event):
        if self.step1_visible:
            children = self.step1_tree.get_children()
            if children:
                current = self.step1_tree.selection()
                if current:
                    current_idx = children.index(current[0])
                    next_idx = min(current_idx + 1, len(children) - 1)
                else:
                    next_idx = 0
                
                self.step1_tree.selection_set(children[next_idx])
                self.step1_tree.focus(children[next_idx])
                self.step1_tree.see(children[next_idx])
        return "break"
    
    def on_step1_return(self, event):
        """Handle Enter in step1 entry"""
        if self.step1_visible:
            current = self.step1_tree.selection()
            if current:
                self.select_medicine_name_from_tree(current[0])
            else:
                children = self.step1_tree.get_children()
                if children:
                    self.select_medicine_name_from_tree(children[0])
        else:
            # If step1 not visible, show it first
            self.on_step1_change()
        return "break"
    
    def on_step1_up(self, event):
        if self.step1_visible:
            children = self.step1_tree.get_children()
            if children:
                current = self.step1_tree.selection()
                if current:
                    current_idx = children.index(current[0])
                    prev_idx = max(current_idx - 1, 0)
                else:
                    prev_idx = 0
                
                self.step1_tree.selection_set(children[prev_idx])
                self.step1_tree.focus(children[prev_idx])
                self.step1_tree.see(children[prev_idx])
        return "break"
    

    
    def on_step1_select(self, event):
        """Handle selection from step1 tree"""
        current = self.step1_tree.selection()
        if current:
            self.select_medicine_name_from_tree(current[0])
        return "break"
    

    
    def on_step1_click(self, event):
        """Handle click on step1 tree"""
        current = self.step1_tree.selection()
        if current:
            self.select_medicine_name_from_tree(current[0])
    
    def select_medicine_name_from_tree(self, item_id):
        """Select medicine name from tree and show variants."""
        values = self.step1_tree.item(item_id)['values']
        if values:
            medicine_name = values[0].strip()
            self.step1_var.set(medicine_name)
            self.hide_step1()
            self.load_variants(medicine_name)
    
    def load_variants(self, medicine_name):
        """Load in-stock variants for selected medicine.
        If none in stock, show a placeholder row so user can still proceed."""
        self.cursor.execute("""
            SELECT id, name, batch_no, expiry_date, stock_qty, mrp, rate,
                   manufacturer, schedule, type, COALESCE(unit,'1') as unit
            FROM medicines WHERE name = ? AND stock_qty > 0
            ORDER BY expiry_date ASC
        """, (medicine_name,))
        rows = self.cursor.fetchall()

        # If no in-stock rows, check if it exists with 0 stock (still selectable)
        if not rows:
            self.cursor.execute("""
                SELECT id, name, batch_no, expiry_date, stock_qty, mrp, rate,
                       manufacturer, schedule, type, COALESCE(unit,'1') as unit
                FROM medicines WHERE name = ?
                ORDER BY expiry_date ASC LIMIT 5
            """, (medicine_name,))
            rows = self.cursor.fetchall()

        self.variants = []
        for row in rows:
            med_id, name, batch, expiry, stock, mrp, rate, manufacturer, schedule, med_type, unit = row
            expiry_display = expiry[:7] if expiry else 'N/A'
            pack_size = f"1*{unit}" if med_type and med_type.lower() in ['tablet','bolus'] else unit
            self.variants.append({
                'id': med_id, 'name': name, 'batch': batch,
                'pack_size': pack_size, 'expiry_display': expiry_display,
                'expiry': expiry, 'stock': int(stock or 0),
                'mrp': mrp or 0, 'rate': rate or 0,
                'manufacturer': manufacturer or 'N/A',
                'schedule': schedule or ''
            })

        if self.variants:
            self.show_step2()
        else:
            # Medicine is from master only — no purchase record yet
            # Auto-select with blank batch so user fills details in purchase form
            self.selected_medicine = {
                'id': None, 'name': medicine_name, 'batch': '',
                'pack_size': '', 'expiry_display': '', 'expiry': '',
                'stock': 0, 'mrp': 0, 'rate': 0,
                'manufacturer': '', 'schedule': ''
            }
            self.step1_entry.event_generate('<<ComboboxSelected>>')
            if callable(self.next_focus_widget):
                self.next_focus_widget()
    
    def show_step2(self):
        """Show step2 tree with variants"""
        if not self.variants or self.step2_tree is None:
            return
            
        # Clear existing items
        for item in self.step2_tree.get_children():
            self.step2_tree.delete(item)
        
        # Add variants to tree with separators
        for variant in self.variants:
            self.step2_tree.insert('', tk.END, values=(
                f" {variant['batch']}", f" {variant['pack_size']}", f" {variant['expiry_display']}",
                f" {variant['stock']}", f" ₹{variant['rate']:.1f}", f" ₹{variant['mrp']:.1f}",
                f" {variant['manufacturer']}", f" {variant['schedule']}"
            ))
        
        # Position step2 tree
        try:
            x = self.step1_entry.winfo_rootx() - self.winfo_toplevel().winfo_rootx()
            y = self.step1_entry.winfo_rooty() - self.winfo_toplevel().winfo_rooty() + self.step1_entry.winfo_height()
            
            self.step2_tree.place(x=x, y=y, width=max(self.step1_entry.winfo_width() + 220, 620), height=200)
            self.step2_tree.tkraise()
            self.step2_tree.focus_force()
            
            # Ensure selection works
            self.step2_tree.after(10, lambda: self.step2_tree.focus_force())
            
            # Select first variant and set focus properly
            children = self.step2_tree.get_children()
            if children:
                self.step2_tree.selection_set(children[0])
                self.step2_tree.focus(children[0])
                self.step2_tree.see(children[0])
                
            self.step2_visible = True
        except tk.TclError:
            pass
    
    def on_step2_select(self, event):
        """Handle selection from step2 tree"""
        current = self.step2_tree.selection()
        if current:
            self.select_variant_from_tree(current[0])
        else:
            # If no selection, select first item
            children = self.step2_tree.get_children()
            if children:
                self.select_variant_from_tree(children[0])
        return "break"
    
    def on_step2_click(self, event):
        """Handle click on step2 tree"""
        current = self.step2_tree.selection()
        if current:
            self.select_variant_from_tree(current[0])
    
    def on_step1_tab(self, event):
        """Handle Tab to move to step2 if visible"""
        if self.step2_visible:
            self.step2_tree.focus_set()
            children = self.step2_tree.get_children()
            if children:
                self.step2_tree.selection_set(children[0])
                self.step2_tree.focus(children[0])
        return "break"
    
    def on_step2_key(self, event):
        """Handle key press in step2"""
        if event.keysym == "Return":
            current = self.step2_tree.selection()
            if current:
                self.select_variant_from_tree(current[0])
            return "break"
        return None
    
    def on_step2_down(self, event):
        """Handle down arrow in step2"""
        children = self.step2_tree.get_children()
        current = self.step2_tree.selection()
        if current and children:
            current_idx = children.index(current[0])
            next_idx = min(current_idx + 1, len(children) - 1)
            self.step2_tree.selection_set(children[next_idx])
            self.step2_tree.focus(children[next_idx])
            self.step2_tree.see(children[next_idx])
        return "break"
    
    def on_step2_up(self, event):
        """Handle up arrow in step2 - go back to step1 if at top"""
        children = self.step2_tree.get_children()
        current = self.step2_tree.selection()
        if current and children:
            current_idx = children.index(current[0])
            if current_idx == 0:  # At top
                self.hide_step2()
                self.step1_entry.focus_set()
                return "break"
            else:
                prev_idx = max(current_idx - 1, 0)
                self.step2_tree.selection_set(children[prev_idx])
                self.step2_tree.focus(children[prev_idx])
                self.step2_tree.see(children[prev_idx])
        return "break"
    
    def select_variant_from_tree(self, item_id):
        """Select specific variant from tree and complete selection"""
        values = self.step2_tree.item(item_id)['values']
        if not values:
            return
            
        # Find the variant by index position (avoids batch string mismatch)
        try:
            item_index = self.step2_tree.get_children().index(item_id)
            selected_variant = self.variants[item_index] if item_index < len(self.variants) else None
        except (ValueError, IndexError):
            selected_variant = None
                
        if not selected_variant:
            return
            
        self.selected_medicine = selected_variant
        
        # Update display to show selected variant
        display_text = f"{selected_variant['name']} | B:{selected_variant['batch']} | Exp:{selected_variant['expiry'][:7]} | Stock:{selected_variant['stock']}"
        self.step1_var.set(display_text)
        
        self.hide_step2()
        
        # Trigger selection event
        self.step1_entry.event_generate('<<ComboboxSelected>>')
        
        # Move to next field
        if callable(self.next_focus_widget):
            self.next_focus_widget()
        elif self.next_focus_widget:
            self.next_focus_widget.focus()
    
    def show_step1(self):
        """Show step1 tree"""
        if self.step1_tree is None:
            return
        if not self.step1_visible and self.step1_tree.get_children():
            try:
                x = self.step1_entry.winfo_rootx() - self.winfo_toplevel().winfo_rootx()
                y = self.step1_entry.winfo_rooty() - self.winfo_toplevel().winfo_rooty() + self.step1_entry.winfo_height()
                
                self.step1_tree.place(x=x, y=y, width=max(self.step1_entry.winfo_width() + 200, 600))
                self.step1_tree.tkraise()
                self.step1_visible = True
            except tk.TclError:
                pass
    
    def hide_step1(self):
        """Hide step1 tree"""
        try:
            if self.step1_tree:
                self.step1_tree.place_forget()
        except tk.TclError:
            pass
        self.step1_visible = False
    
    def hide_step2(self):
        """Hide step2 tree"""
        try:
            if self.step2_tree:
                self.step2_tree.place_forget()
        except tk.TclError:
            pass
        self.step2_visible = False
    
    def on_global_return(self, event):
        """Handle global Return key for step2 tree"""
        if self.step2_visible and event.widget == self.step2_tree:
            current = self.step2_tree.selection()
            if current:
                self.select_variant_from_tree(current[0])
                return "break"
        return None
    
    def on_click_outside(self, event):
        """Hide trees when clicking outside this combo's widgets"""
        w = event.widget
        if w is self.step1_entry or w is self.step1_tree or w is self.step2_tree:
            return
        try:
            self.hide_step1()
            self.hide_step2()
        except tk.TclError:
            pass
    
    def _check_focus_and_hide_step1(self):
        """Check focus and hide step1 if needed"""
        try:
            focused = self.focus_get()
            if (focused != self.step1_entry and 
                focused != self.step1_tree and 
                focused != self.step2_tree):
                self.hide_step1()
                self.hide_step2()
        except:
            self.hide_step1()
            self.hide_step2()
    
    def get(self):
        """Get current selection"""
        return self.step1_var.get()
    
    def set(self, value):
        """Set current value"""
        self.step1_var.set(value)
    
    def focus(self):
        """Set focus to entry"""
        self.step1_entry.focus()
    
    def bind(self, event, callback):
        """Bind event to entry"""
        self.step1_entry.bind(event, callback)
    
    def get_selected_medicine(self):
        """Get the selected medicine data"""
        return self.selected_medicine
    
    def destroy(self):
        """Clean up when widget is destroyed"""
        try:
            if self.step1_tree:
                self.step1_tree.place_forget()
            if self.step2_tree:
                self.step2_tree.place_forget()
        except tk.TclError:
            pass
        super().destroy()