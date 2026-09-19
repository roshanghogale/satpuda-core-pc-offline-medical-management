"""Collect read-only UI context for the App Tutor (current screen, dialog, selection)."""
from __future__ import annotations

from typing import Any, Dict, List


def _safe_str(value) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _settings_context(app) -> Dict[str, str]:
    out = {"settings_tab": "", "settings_section": ""}
    page = getattr(app, "_settings_page", None)
    if page is None:
        return out
    try:
        nb = page._notebook
        out["settings_tab"] = _safe_str(nb.tab(nb.select(), "text"))
    except Exception:
        pass
    try:
        tab_obj = page._current_tab_object()
        if tab_obj is not None:
            sec = getattr(tab_obj, "_active_section", None)
            if sec is not None:
                out["settings_section"] = _safe_str(sec)
            elif hasattr(tab_obj, "TAB_NAME"):
                out["settings_section"] = _safe_str(getattr(tab_obj, "TAB_NAME", ""))
    except Exception:
        pass
    return out


def _returns_context(app) -> Dict[str, str]:
    kind = _safe_str(getattr(app, "_returns_last_kind", "")) or "sales"
    labels = {
        "sales": "Sales Return",
        "purchase": "Purchase Return",
        "disposal": "Return / Write-off",
    }
    return {"returns_kind": kind, "returns_label": labels.get(kind, kind)}


def _billing_context(app) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "sales_tab_count": 0,
        "sales_active_tab": 0,
        "sales_tabs": [],
        "active_customer": "",
        "active_item_count": 0,
    }
    page = getattr(app, "_billing_page", None)
    if page is None:
        return out
    try:
        if hasattr(page, "_save_active_tab"):
            page._save_active_tab()
    except Exception:
        pass
    tabs = getattr(page, "_doc_tabs", None) or []
    active = int(getattr(page, "_active_tab_idx", 0) or 0)
    out["sales_tab_count"] = len(tabs)
    out["sales_active_tab"] = active + 1
    summaries: List[str] = []
    for i, tab in enumerate(tabs):
        label = _safe_str(tab.get("label")) or f"Sale {i + 1}"
        customer = _safe_str(tab.get("customer_name"))
        items = tab.get("selected_medicines") or []
        n_items = len(items)
        if customer:
            line = f"{label} - {customer}"
            if n_items:
                line += f", {n_items} item{'s' if n_items != 1 else ''}"
        else:
            line = f"{label} - no customer yet"
            if n_items:
                line += f", {n_items} item{'s' if n_items != 1 else ''}"
        if i == active:
            line += " (active)"
        summaries.append(line)
    out["sales_tabs"] = summaries
    try:
        out["active_customer"] = _safe_str(page.customer_name.get())
        out["active_item_count"] = len(getattr(page, "selected_medicines", []) or [])
    except Exception:
        pass
    return out


def _purchase_context(app) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "purchase_tab_count": 0,
        "purchase_active_tab": 0,
        "purchase_tabs": [],
        "active_supplier": "",
        "active_line_count": 0,
    }
    page = getattr(app, "_purchase_page", None)
    if page is None:
        return out
    try:
        if hasattr(page, "_save_active_tab"):
            page._save_active_tab()
    except Exception:
        pass
    tabs = getattr(page, "_doc_tabs", None) or []
    active = int(getattr(page, "_active_tab_idx", 0) or 0)
    out["purchase_tab_count"] = len(tabs)
    out["purchase_active_tab"] = active + 1
    summaries: List[str] = []
    for i, tab in enumerate(tabs):
        label = _safe_str(tab.get("label")) or f"Purchase {i + 1}"
        supplier = _safe_str(tab.get("supplier_name"))
        items = tab.get("purchase_items") or []
        n_items = len(items)
        if supplier:
            line = f"{label} - {supplier}"
            if n_items:
                line += f", {n_items} line{'s' if n_items != 1 else ''}"
        else:
            line = f"{label} - no supplier yet"
            if n_items:
                line += f", {n_items} line{'s' if n_items != 1 else ''}"
        if i == active:
            line += " (active)"
        summaries.append(line)
    out["purchase_tabs"] = summaries
    try:
        out["active_supplier"] = _safe_str(page.supplier_name.get())
        out["active_line_count"] = len(getattr(page, "purchase_items", []) or [])
    except Exception:
        pass
    return out


def _selection_hint(app, screen_id: str) -> str:
    try:
        if screen_id == "sales_history":
            page = getattr(app, "_sales_history_page", None)
            tree = getattr(page, "tree", None) if page else None
        elif screen_id == "purchase_history":
            page = getattr(app, "_purchase_history_page", None)
            tree = getattr(page, "tree", None) if page else None
        elif screen_id == "inventory":
            page = getattr(app, "_inventory_page", None)
            tree = getattr(page, "inventory_tree", None) if page else None
        elif screen_id == "returns_sales":
            page = getattr(app, "_sales_return_page", None)
            tree = getattr(page, "hist_tree", None) if page else None
        elif screen_id == "returns_purchase":
            page = getattr(app, "_purchase_return_page", None)
            tree = getattr(page, "hist_tree", None) if page else None
        else:
            return ""
        if tree is None:
            return ""
        sel = tree.selection()
        if not sel:
            return ""
        vals = tree.item(sel[0], "values")
        if not vals:
            return ""
        return " | ".join(_safe_str(v) for v in vals[:3] if _safe_str(v))
    except Exception:
        return ""


def _map_nav_to_screen_id(active_nav: str, app) -> str:
    nav = _safe_str(active_nav).replace("\U0001f3e0 ", "").strip()
    mapping = {
        "Home": "home",
        "Sales": "sales_billing",
        "Purchase": "purchase",
        "Inventory": "inventory",
        "Sales History": "sales_history",
        "Purchase History": "purchase_history",
        "Returns": "returns",
        "Settings": "settings",
    }
    if nav == "Returns":
        rk = _safe_str(getattr(app, "_returns_last_kind", "")) or "sales"
        if rk == "purchase":
            return "returns_purchase"
        if rk == "disposal":
            return "returns_disposal"
        return "returns_sales"
    if nav == "Settings":
        st = _settings_context(app)
        tab = st.get("settings_tab", "").lower()
        sec = st.get("settings_section", "").lower()
        if sec in ("admin", "danger"):
            return "settings_data"
        if "import" in tab:
            return "settings_import"
        if "payment" in tab:
            return "settings_payment"
        if "ledger" in tab:
            return "settings_ledger"
        if "reorder" in tab:
            return "settings_reorder"
        if "alert" in tab:
            return "settings_alerts"
        if "shelf" in tab:
            return "settings_shelf"
        if "data" in tab or "management" in tab or "system" in tab:
            return "settings_data"
        if "layout" in tab or "appearance" in tab:
            return "settings_layout"
        if "contact" in tab:
            return "settings_contacts"
        if "pharmacy" in tab:
            return "settings_pharmacy"
        if "sales" in tab and "billing" in tab:
            return "settings_sales_billing"
        if "shortcut" in tab:
            return "settings_shortcuts"
        return "settings"
    return mapping.get(nav, "overview")


def _screen_ui_facts(screen_id: str) -> str:
    """Hard facts the tutor must not contradict."""
    facts = {
      "sales_billing": (
          "UI FACTS (Sales): ONE scrollable screen. No popup for customer or medicine (normal billing). "
          "Customer Information panel is inline: Customer Name, phone, address, doctor. NO Add/+ for customer; saved on Save Sales (F5) only. "
          "Medicine Selection panel is inline: Medicine, Quantity, Disc, Add Medicine. "
          "Add Medicine adds the filled/selected line to Selected Medicines table. It does NOT open a window or dialog. "
          "Sale 1 tab already open. NO Add New tab button. Ctrl+Shift+N/W. Buttons: Add Medicine (green), Save Sales (F5)."
      ),
      "purchase": (
          "UI FACTS (Purchase): ONE scrollable screen. No popup for supplier or medicine. "
          "LEFT Supplier Information panel inline: Supplier Name, Address, Phone, GSTIN, DL. NO Add/+; supplier saved on Save Purchase (F5) only. "
          "RIGHT Medicine Details panel inline: Medicine Name, Type, qty, batch, expiry, rate, MRP, etc. "
          "Add Medicine copies filled Medicine Details into items table. It does NOT open a window. Not a separate new-medicine form. "
          "Purchase 1 tab already open. NO Add New tab button. Ctrl+Shift+N/W. Buttons: Add Medicine, Save Purchase (F5)."
      ),

      "inventory": (
          "UI FACTS (Inventory): Inline page. Search and Filter applies LIVE — no Apply Filter button. "
          "Ctrl+Shift+C clears filters. Edit Medicine and View Details are dialogs. "
          "Add Medicine does not exist here — use context Reorder to open Settings Reorder."
      ),
      "sales_history": (
          "UI FACTS (Sales History): Inline list. Date filters need Apply Filter button. "
          "Edit Bill opens fullscreen window (Update Sale F5). View Bill Details is read-only dialog. "
          "Export opens report picker then format dialog."
      ),
      "purchase_history": (
          "UI FACTS (Purchase History): Inline list. Apply Filter for dates. "
          "Edit Purchase is fullscreen (Update Purchase). No separate view-only dialog. "
          "Double-click opens edit."
      ),
      "returns": (
          "UI FACTS (Returns): Three sub-pages — Sales Return, Purchase Return, Write-off. "
          "Sales return qty is inline. Purchase return uses small Return qty dialog per item. "
          "Save Return (F5) commits; adding to list does not save."
      ),
      "returns_sales": (
          "UI FACTS (Sales Return): Inline steps. Save Return (F5) only commits. Medicine search limited to last N days."
      ),
      "returns_purchase": (
          "UI FACTS (Purchase Return): Qty dialog Return — {medicine}. Credit to Supplier label. Save Return (F5) commits."
      ),
      "returns_disposal": (
          "UI FACTS (Write-off): Supplier tabs, Submit this supplier. Mixed return and write-off by purchase record."
      ),
      "settings": (
          "UI FACTS (Settings): 13 tabs — Pharmacy Profile, Contacts, Shelf Management, Appearance, "
          "Layout and Lists, Sales and Billing, Import, Alert and Monitoring, Data and System, "
          "Payment, Ledger, Reorder, Shortcuts. Left Sections sidebar in many tabs (F4)."
      ),
      "settings_payment": (
          "UI FACTS (Payment tab): Toggle Supplier Payment | Customer Payment. Inline forms. Save Payment (F5)."
      ),
      "settings_ledger": (
          "UI FACTS (Ledger tab): Toggle Supplier Ledger | Customer Ledger. From/To, View Report. Inline."
      ),
      "settings_reorder": (
          "UI FACTS (Reorder tab): Pending Orders | New Order. Mark Received does NOT add stock — use Open Purchase first."
      ),
      "settings_alerts": (
          "UI FACTS (Alerts): Sub-tabs Low Stock, Out of Stock, Expired, Near Expiry, Customer Dues. "
          "Expired tab: Remove All Expired. Refresh All at top."
      ),
      "settings_data": (
          "UI FACTS (Data and System): Export Data buttons, My Assist (Satpuda AI toggle), backup, stores."
      ),

      "home": (
          "UI FACTS (Home): + New Bill and New Purchase are in the left Quick Actions column only. "
          "Satpuda AI opens from the floating ? button at bottom-right, not the top bar."
      ),
    }
    return facts.get(screen_id, "")


def collect_app_context(app) -> Dict[str, Any]:
    active_nav = _safe_str(getattr(app, "active_nav", "")) or "Home"
    screen_id = _map_nav_to_screen_id(active_nav, app)
    ctx: Dict[str, Any] = {
        "active_nav": active_nav,
        "screen_id": screen_id,
        "screen_label": active_nav.replace("\U0001f3e0 ", "").strip() or "Home",
        "selection": _selection_hint(app, screen_id),
        "dialog": None,
    }
    if screen_id.startswith("settings"):
        ctx.update(_settings_context(app))
    if screen_id.startswith("returns"):
        ctx.update(_returns_context(app))
    if screen_id == "sales_billing":
        ctx.update(_billing_context(app))
    if screen_id == "purchase":
        ctx.update(_purchase_context(app))
    try:
        cache = getattr(app, "_page_cache", None)
        if cache and getattr(cache, "_visible_key", None) == "general_products":
            ctx["screen_id"] = "general_products"
            ctx["screen_label"] = "General Products"
    except Exception:
        pass
    return ctx


def format_context_for_prompt(ctx: Dict[str, Any]) -> str:
    """Short line shown in the Satpuda AI header chip."""
    label = _safe_str(ctx.get("screen_label")) or "Home"
    if ctx.get("sales_tab_count", 0) > 0:
        n = int(ctx["sales_tab_count"])
        active = int(ctx.get("sales_active_tab") or 1)
        parts = [f"Sales \u00b7 {n} tab{'s' if n != 1 else ''}"]
        cust = _safe_str(ctx.get("active_customer"))
        if cust:
            parts.append(cust)
        elif n > 1:
            parts.append(f"tab {active} active")
        return " \u00b7 ".join(parts)
    if ctx.get("purchase_tab_count", 0) > 0:
        n = int(ctx["purchase_tab_count"])
        active = int(ctx.get("purchase_active_tab") or 1)
        parts = [f"Purchase \u00b7 {n} tab{'s' if n != 1 else ''}"]
        sup = _safe_str(ctx.get("active_supplier"))
        if sup:
            parts.append(sup)
        elif n > 1:
            parts.append(f"tab {active} active")
        return " \u00b7 ".join(parts)
    if ctx.get("settings_tab"):
        return f"{label} \u00b7 {ctx['settings_tab']}"
    if ctx.get("returns_label"):
        return f"{label} \u00b7 {ctx['returns_label']}"
    return label


def format_context_for_llm(ctx: Dict[str, Any]) -> str:
    """Rich context sent to Gemini — assume user is already on this screen."""
    screen_id = ctx.get("screen_id", "overview")
    lines = [
        f"Screen: {ctx.get('screen_label', 'Unknown')} (id={screen_id}).",
        "The user is already on this screen. Do not tell them to open or navigate to it.",
    ]
    ui_facts = _screen_ui_facts(screen_id)
    if ui_facts:
        lines.append(ui_facts)
    if ctx.get("settings_tab"):
        lines.append(f"Settings tab: {ctx['settings_tab']}.")
    if ctx.get("settings_section"):
        lines.append(f"Settings section: {ctx['settings_section']}.")
    if ctx.get("returns_label"):
        lines.append(f"Returns page: {ctx['returns_label']}.")
    if ctx.get("sales_tab_count", 0) > 0:
        n = int(ctx["sales_tab_count"])
        lines.append(f"There are {n} open sales tab(s). Active tab is #{ctx.get('sales_active_tab', 1)}.")
        for summary in ctx.get("sales_tabs") or []:
            lines.append(f"  - {summary}")
        cust = _safe_str(ctx.get("active_customer"))
        if cust:
            lines.append(f"Customer on the active tab: {cust}.")
        items = int(ctx.get("active_item_count") or 0)
        if items:
            lines.append(f"Active tab has {items} medicine line(s) in the cart.")
    if ctx.get("purchase_tab_count", 0) > 0:
        n = int(ctx["purchase_tab_count"])
        lines.append(f"There are {n} open purchase tab(s). Active tab is #{ctx.get('purchase_active_tab', 1)}.")
        for summary in ctx.get("purchase_tabs") or []:
            lines.append(f"  - {summary}")
        sup = _safe_str(ctx.get("active_supplier"))
        if sup:
            lines.append(f"Supplier on the active tab: {sup}.")
        lines_count = int(ctx.get("active_line_count") or 0)
        if lines_count:
            lines.append(f"Active tab has {lines_count} purchase line(s).")
    if ctx.get("selection"):
        lines.append(f"List selection: {ctx['selection']}.")
    return "\n".join(lines)
