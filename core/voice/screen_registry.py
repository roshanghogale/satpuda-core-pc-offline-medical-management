"""Screen registry — aliases only; COMMAND_PREFIXES in command_parser apply to all."""

SCREEN_ENTRIES = [
    {
        "id": "home",
        "label": "Home",
        "aliases": [
            "home", "dashboard", "main screen", "main page", "home screen",
            "dashbord", "main", "hom", "hoam", "hone", "go home",
        ],
        "nav": {"type": "main", "method": "show_welcome", "nav_text": "🏠 Home"},
    },
    {
        "id": "sales",
        "label": "Sales",
        "aliases": [
            "sales", "sale", "sales entry", "billing", "invoice entry", "invoice",
            "saels", "bill",
        ],
        "nav": {"type": "main", "method": "open_billing", "nav_text": "Sales"},
    },
    {
        "id": "purchase",
        "label": "Purchase",
        "aliases": [
            "purchase", "purchase entry", "buying", "stock purchase",
            "purchas", "perchase", "purcahse",
        ],
        "nav": {"type": "main", "method": "open_purchase", "nav_text": "Purchase"},
    },
    {
        "id": "inventory",
        "label": "Inventory",
        "aliases": [
            "inventory", "stock", "medicine stock", "stock management",
            "inventory management", "inventry", "inventori",
        ],
        "nav": {"type": "main", "method": "open_inventory", "nav_text": "Inventory"},
    },
    {
        "id": "sales_history",
        "label": "Sales History",
        "aliases": [
            "sales history", "sale history", "sales historic", "sale historic",
            "invoice history", "billing history", "sales records",
            "sales histroy", "sale histroy", "histroy", "histry",
        ],
        "nav": {"type": "main", "method": "open_sales_history", "nav_text": "Sales History"},
    },
    {
        "id": "purchase_history",
        "label": "Purchase History",
        "aliases": [
            "purchase history", "purchases history", "purchase historic",
            "sale history purchase", "purchase records", "purchase register",
            "buying history", "purchase histroy",
            "parker history", "parking history",
        ],
        "nav": {"type": "main", "method": "open_purchase_history", "nav_text": "Purchase History"},
    },
    {
        "id": "returns",
        "label": "Returns",
        "aliases": [
            "returns", "return", "returns page", "return page", "retrns", "retuns",
        ],
        "nav": {"type": "main", "method": "open_returns", "nav_text": "Returns"},
    },
    {
        "id": "sales_return",
        "label": "Sales Return",
        "aliases": [
            "sales return", "sales returns", "sale return", "sale returns",
            "return sales", "returns sales", "customer return", "sales refund",
        ],
        "nav": {"type": "returns", "kind": "sales", "nav_text": "Returns"},
    },
    {
        "id": "purchase_return",
        "label": "Purchase Return",
        "aliases": [
            "purchase return", "purchase returns", "purchases return",
            "return purchase", "returns purchase", "supplier return",
            "vendor return", "supplier returns",
        ],
        "nav": {"type": "returns", "kind": "purchase", "nav_text": "Returns"},
    },
    {
        "id": "settings",
        "label": "Settings",
        "aliases": [
            "settings", "setting", "preferences", "configuration",
            "application settings", "config",
        ],
        "nav": {"type": "main", "method": "open_settings", "nav_text": "Settings"},
    },
    {
        "id": "pharmacy_profile",
        "label": "Pharmacy Profile",
        "aliases": [
            "pharmacy profile", "medical profile", "store profile", "shop profile",
            "farmacy profile", "pharma profile", "pharmacy",
        ],
        "nav": {
            "type": "settings_tab", "tab": "Pharmacy Profile", "section": "profile",
            "section_attr": "_pharmacy", "section_method": "select_section",
        },
    },
    {
        "id": "bill_print_style",
        "label": "Bill Print Style",
        "aliases": [
            "bill print style", "invoice print style", "print style", "bill design",
            "bill print", "print",
        ],
        "nav": {
            "type": "settings_tab", "tab": "Pharmacy Profile", "section": "bill_template",
            "section_attr": "_pharmacy", "section_method": "select_section",
        },
    },
    {
        "id": "contacts",
        "label": "Contacts",
        "aliases": ["contacts", "contact", "contacs"],
        "nav": {"type": "settings_tab", "tab": "Contacts"},
    },
    {
        "id": "doctors",
        "label": "Doctors",
        "aliases": ["doctors", "doctor", "doctor list", "doctor management", "docter"],
        "nav": {"type": "contacts", "subtab": "Doctors"},
    },
    {
        "id": "customers",
        "label": "Customers",
        "aliases": [
            "customers", "customer list", "customer management", "custmer",
        ],
        "nav": {"type": "contacts", "subtab": "Customers"},
    },
    {
        "id": "suppliers",
        "label": "Suppliers",
        "aliases": [
            "suppliers", "supplier list", "suppliers list",
            "vendor list", "vendors", "supplier management",
            "suplier", "supliers", "suppliar",
        ],
        "nav": {"type": "contacts", "subtab": "Suppliers"},
    },
    {
        "id": "shelf_management",
        "label": "Shelf Management",
        "aliases": [
            "shelf management", "shelves", "shelf", "rack management", "racks",
        ],
        "nav": {"type": "settings_tab", "tab": "Shelf Management"},
    },
    {
        "id": "appearance",
        "label": "Appearance",
        "aliases": ["appearance", "appearence", "ui settings", "display settings"],
        "nav": {"type": "settings_tab", "tab": "Appearance"},
    },
    {
        "id": "layout_lists",
        "label": "Layout & Lists",
        "aliases": [
            "layout and lists", "layout lists", "list layout", "column settings",
            "table layout", "list settings",
        ],
        "nav": {"type": "settings_tab", "tab": "Layout & Lists"},
    },
    {
        "id": "theme",
        "label": "Theme",
        "aliases": [
            "theme", "theme settings", "dark mode", "light mode", "theam", "thme",
        ],
        "nav": {
            "type": "settings_tab", "tab": "Appearance", "section": "theme",
            "section_attr": "_layout", "section_method": "_show_section",
        },
    },
    {
        "id": "font_size",
        "label": "Font Size",
        "aliases": ["font size", "text size", "font settings", "font", "fonts"],
        "nav": {
            "type": "settings_tab", "tab": "Appearance", "section": "font",
            "section_attr": "_layout", "section_method": "_show_section",
        },
    },
    {
        "id": "home_banner",
        "label": "Home Banner",
        "aliases": ["home banner", "banner settings", "dashboard banner", "banner"],
        "nav": {
            "type": "settings_tab", "tab": "Appearance", "section": "banner",
            "section_attr": "_layout", "section_method": "_show_section",
        },
    },
    {
        "id": "quick_access",
        "label": "Quick Access",
        "aliases": ["quick access", "quick menu", "shortcuts menu"],
        "nav": {
            "type": "settings_tab", "tab": "Appearance", "section": "quick_access",
            "section_attr": "_layout", "section_method": "_show_section",
        },
    },
    {
        "id": "dashboard_sections",
        "label": "Dashboard Sections",
        "aliases": [
            "dashboard sections", "dashboard widgets", "dashboard layout", "dashboard",
        ],
        "nav": {
            "type": "settings_tab", "tab": "Appearance", "section": "dashboard",
            "section_attr": "_layout", "section_method": "_show_section",
        },
    },
    {
        "id": "column_visibility",
        "label": "Column Visibility",
        "aliases": [
            "column visibility", "table columns", "column settings", "columns",
            "colomn", "collumn",
        ],
        "nav": {
            "type": "settings_tab", "tab": "Layout & Lists", "section": "columns",
            "section_attr": "_layout", "section_method": "open_section",
        },
    },
    {
        "id": "table_row_counts",
        "label": "Table Row Counts",
        "aliases": [
            "table rows", "table row counts", "row count", "row settings", "rows",
        ],
        "nav": {
            "type": "settings_tab", "tab": "Layout & Lists", "section": "rows",
            "section_attr": "_layout", "section_method": "open_section",
        },
    },
    {
        "id": "medicine_units",
        "label": "Medicine Units",
        "aliases": ["medicine units", "units", "unit settings", "unit"],
        "nav": {
            "type": "settings_tab", "tab": "Layout & Lists", "section": "units",
            "section_attr": "_layout", "section_method": "open_section",
        },
    },
    {
        "id": "schedules",
        "label": "Schedules",
        "aliases": ["schedules", "schedule", "medicine schedules", "schedule settings"],
        "nav": {
            "type": "settings_tab", "tab": "Layout & Lists", "section": "schedules",
            "section_attr": "_layout", "section_method": "open_section",
        },
    },
    {
        "id": "medicine_types",
        "label": "Medicine Types",
        "aliases": ["medicine types", "drug types", "product types", "med types"],
        "nav": {
            "type": "settings_tab", "tab": "Layout & Lists", "section": "med_types",
            "section_attr": "_layout", "section_method": "open_section",
        },
    },
    {
        "id": "thresholds",
        "label": "Thresholds",
        "aliases": ["thresholds", "threshold", "stock thresholds", "limits"],
        "nav": {
            "type": "settings_tab", "tab": "Layout & Lists", "section": "thresholds",
            "section_attr": "_layout", "section_method": "open_section",
        },
    },
    {
        "id": "app_mode",
        "label": "App Mode",
        "aliases": ["app mode", "application mode", "software mode"],
        "nav": {
            "type": "settings_tab", "tab": "Layout & Lists", "section": "app_mode",
            "section_attr": "_layout", "section_method": "open_section",
        },
    },
    {
        "id": "import",
        "label": "Import",
        "aliases": ["import", "import section", "importing"],
        "nav": {"type": "settings_tab", "tab": "Import"},
    },
    {
        "id": "purchase_bill_import",
        "label": "Purchase Bill Import",
        "aliases": [
            "purchase bill import", "purchase bill", "bill import", "invoice import",
        ],
        "nav": {
            "type": "settings_tab", "tab": "Import", "section": "purchase_bill",
            "section_attr": "_import", "section_method": "show_section",
        },
    },
    {
        "id": "web_entry",
        "label": "Web Entry",
        "aliases": ["web entry", "online entry", "website entry", "web enter"],
        "nav": {
            "type": "settings_tab", "tab": "Import", "section": "web",
            "section_attr": "_import", "section_method": "show_section",
        },
    },
    {
        "id": "import_data",
        "label": "Import Data",
        "aliases": ["import data", "data import", "bulk import"],
        "nav": {
            "type": "settings_tab", "tab": "Import", "section": "file_import",
            "section_attr": "_import", "section_method": "show_section",
        },
    },
    {
        "id": "mobile_import",
        "label": "Mobile Import",
        "aliases": ["mobile import", "phone import", "device import", "mobile"],
        "nav": {
            "type": "settings_tab", "tab": "Import", "section": "mobile",
            "section_attr": "_import", "section_method": "show_section",
        },
    },
    {
        "id": "management",
        "label": "Data & System",
        "aliases": [
            "management", "managment", "manage",
            "data and system", "data system", "data & system", "system data",
        ],
        "nav": {"type": "settings_tab", "tab": "Data & System"},
    },
    {
        "id": "store_management",
        "label": "Store Management",
        "aliases": [
            "store management", "shop management", "medical management", "stores",
            "stores and startup alerts", "startup alerts",
        ],
        "nav": {
            "type": "settings_tab", "tab": "Data & System", "section": "stores",
            "section_attr": "_database", "section_method": "show_section",
        },
    },
    {
        "id": "my_assist",
        "label": "My Assist",
        "aliases": [
            "my assist", "my assistant", "voice assistant", "voice settings",
            "assistant settings", "assist settings", "satpuda settings",
            "voice help", "command reference", "voice commands",
        ],
        "nav": {
            "type": "settings_tab",
            "tab": "Data & System",
            "section": "my_assist",
            "section_attr": "_database",
            "section_method": "show_section",
        },
    },
    {
        "id": "app_updates",
        "label": "App Updates",
        "aliases": [
            "app updates", "app update", "software updates", "update manager",
            "updates", "update page", "updates page",
        ],
        "nav": {
            "type": "settings_tab", "tab": "Data & System", "section": "updates",
            "section_attr": "_database", "section_method": "show_section",
        },
    },
    {
        "id": "export_data",
        "label": "Export Data",
        "aliases": ["export data", "data export", "backup export", "export"],
        "nav": {
            "type": "settings_tab", "tab": "Data & System", "section": "export",
            "section_attr": "_database", "section_method": "show_section",
        },
    },
    {
        "id": "google_drive_backup",
        "label": "Google Drive Backup",
        "aliases": [
            "google drive backup", "google drive", "drive backup", "cloud backup", "backup",
        ],
        "nav": {
            "type": "settings_tab", "tab": "Data & System", "section": "backup",
            "section_attr": "_database", "section_method": "show_section",
        },
    },
    {
        "id": "administrator",
        "label": "Administrator",
        "aliases": ["administrator", "admin", "admin settings", "admins"],
        "nav": {
            "type": "settings_tab", "tab": "Data & System", "section": "admin",
            "section_attr": "_database", "section_method": "show_section",
        },
    },
    {
        "id": "danger_zone",
        "label": "Danger Zone",
        "aliases": ["danger zone", "danger area", "critical settings", "reset section"],
        "nav": {
            "type": "settings_tab", "tab": "Data & System", "section": "danger",
            "section_attr": "_database", "section_method": "show_section",
        },
    },
    {
        "id": "payment",
        "label": "Payment",
        "aliases": ["payment", "payments", "paymnt"],
        "nav": {"type": "main", "method": "open_payment", "nav_text": "Payment"},
    },
    {
        "id": "supplier_payment",
        "label": "Supplier Payment",
        "aliases": [
            "supplier payment", "vendor payment", "pay supplier", "supplier pay",
            "suppliers payment", "supplier",
        ],
        "nav": {"type": "payment", "kind": "supplier", "nav_text": "Payment"},
    },
    {
        "id": "customer_payment",
        "label": "Customer Payment",
        "aliases": [
            "customer payment", "customer collection", "receive payment",
            "customer pay", "customers payment", "customer",
        ],
        "nav": {"type": "payment", "kind": "customer", "nav_text": "Payment"},
    },
    {
        "id": "ledger",
        "label": "Ledger",
        "aliases": ["ledger", "ledgr", "leder", "accounts"],
        "nav": {"type": "settings_tab", "tab": "Ledger"},
    },
    {
        "id": "supplier_ledger",
        "label": "Supplier Ledger",
        "aliases": [
            "supplier ledger", "vendor ledger", "supplier account", "supplier ledge",
            "suppliers ledger", "supplier link", "supplier leader", "supplier engine",
            "supplier leisure",
        ],
        "nav": {
            "type": "settings_tab", "tab": "Ledger", "section": "supplier",
            "section_attr": "_ledger", "section_method": "_show",
        },
    },
    {
        "id": "customer_ledger",
        "label": "Customer Ledger",
        "aliases": [
            "customer ledger", "customer account", "customer ledge",
            "customers ledger", "customer link", "customer leader", "customer engine",
            "customer leisure",
        ],
        "nav": {
            "type": "settings_tab", "tab": "Ledger", "section": "customer",
            "section_attr": "_ledger", "section_method": "_show",
        },
    },
    {
        "id": "shortcuts",
        "label": "Shortcuts",
        "aliases": [
            "shortcuts", "shortcut", "keyboard shortcuts", "hotkeys", "hotkey",
        ],
        "nav": {"type": "settings_tab", "tab": "⌨ Shortcuts"},
    },
    {
        "id": "sales_billing_settings",
        "label": "Sales & Billing",
        "aliases": [
            "sales and billing", "sales billing", "sales billing settings",
            "billing settings", "sales settings",
        ],
        "nav": {"type": "settings_tab", "tab": "Sales & Billing"},
    },
    {
        "id": "sales_screen_settings",
        "label": "Sales Screen Settings",
        "aliases": ["sales screen", "sales screen settings", "sales screen layout"],
        "nav": {
            "type": "settings_tab", "tab": "Sales & Billing", "section": "billing_layout",
            "section_attr": "_sales", "section_method": "show_section",
        },
    },
    {
        "id": "batch_picker_settings",
        "label": "Batch Picker Settings",
        "aliases": ["batch picker", "batch picker settings"],
        "nav": {
            "type": "settings_tab", "tab": "Sales & Billing", "section": "batch_picker",
            "section_attr": "_sales", "section_method": "show_section",
        },
    },
    {
        "id": "sales_return_settings",
        "label": "Sales Return Settings",
        "aliases": ["sales return settings", "return settings"],
        "nav": {
            "type": "settings_tab", "tab": "Sales & Billing", "section": "sales_return",
            "section_attr": "_sales", "section_method": "show_section",
        },
    },
    {
        "id": "sales_bill_save_settings",
        "label": "Sales Bill Save",
        "aliases": ["sales bill save", "bill save settings", "bill save folder"],
        "nav": {
            "type": "settings_tab", "tab": "Sales & Billing", "section": "sales_bills",
            "section_attr": "_sales", "section_method": "show_section",
        },
    },
    {
        "id": "upi_payment_settings",
        "label": "UPI Payment QR",
        "aliases": ["upi payment", "upi qr", "upi settings", "upi payment qr"],
        "nav": {
            "type": "settings_tab", "tab": "Sales & Billing", "section": "upi_payment",
            "section_attr": "_sales", "section_method": "show_section",
        },
    },
    {
        "id": "autosave_settings",
        "label": "Autosave Settings",
        "aliases": ["autosave", "auto save", "autosave settings"],
        "nav": {
            "type": "settings_tab", "tab": "Sales & Billing", "section": "autosave",
            "section_attr": "_sales", "section_method": "show_section",
        },
    },
    {
        "id": "alert_monitoring",
        "label": "Alert & Monitoring",
        "aliases": [
            "alert and monitoring", "alerts and monitoring", "alert monitoring",
            "alerts monitoring", "alerts", "monitoring", "alert page",
        ],
        "nav": {"type": "settings_tab", "tab": "Alert & Monitoring"},
    },
    {
        "id": "low_stock_alerts",
        "label": "Low Stock Alerts",
        "aliases": ["low stock", "low stock alerts", "low stock monitoring"],
        "nav": {
            "type": "settings_tab", "tab": "Alert & Monitoring", "section": "low_stock",
            "section_attr": "_alerts", "section_method": "show_section",
        },
    },
    {
        "id": "out_of_stock_alerts",
        "label": "Out of Stock Alerts",
        "aliases": ["out of stock", "out of stock alerts", "oos alerts"],
        "nav": {
            "type": "settings_tab", "tab": "Alert & Monitoring", "section": "out_of_stock",
            "section_attr": "_alerts", "section_method": "show_section",
        },
    },
    {
        "id": "expired_alerts",
        "label": "Expired Alerts",
        "aliases": ["expired", "expired alerts", "expired medicines"],
        "nav": {
            "type": "settings_tab", "tab": "Alert & Monitoring", "section": "expired",
            "section_attr": "_alerts", "section_method": "show_section",
        },
    },
    {
        "id": "near_expiry_alerts",
        "label": "Near Expiry Alerts",
        "aliases": ["near expiry", "near expiry alerts", "expiring soon"],
        "nav": {
            "type": "settings_tab", "tab": "Alert & Monitoring", "section": "near_expiry",
            "section_attr": "_alerts", "section_method": "show_section",
        },
    },
    {
        "id": "customer_due_alerts",
        "label": "Customer Due Alerts",
        "aliases": ["customer due alerts", "due alerts", "customer dues monitoring"],
        "nav": {
            "type": "settings_tab", "tab": "Alert & Monitoring", "section": "customer_due",
            "section_attr": "_alerts", "section_method": "show_section",
        },
    },
    {
        "id": "reorder_settings",
        "label": "Reorder",
        "aliases": [
            "reorder", "reorder page", "reorder settings", "pending orders",
            "new order",
        ],
        "nav": {"type": "settings_tab", "tab": "Reorder"},
    },
    {
        "id": "reorder_pending",
        "label": "Pending Orders",
        "aliases": ["pending orders", "reorder pending"],
        "nav": {
            "type": "settings_tab", "tab": "Reorder", "section": "pending",
            "section_attr": "_reorder", "section_method": "_show",
        },
    },
    {
        "id": "reorder_new",
        "label": "New Order",
        "aliases": ["new order", "create reorder", "new reorder"],
        "nav": {
            "type": "settings_tab", "tab": "Reorder", "section": "new",
            "section_attr": "_reorder", "section_method": "_show",
        },
    },
]
