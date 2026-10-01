/** Settings tab / section structure — mirrors ui/settings (Tk). Field UIs are API-driven. */

export type SettingsTabId =
  | 'pharmacy'
  | 'contacts'
  | 'shelf'
  | 'appearance'
  | 'layout_lists'
  | 'sales_billing'
  | 'import'
  | 'alerts'
  | 'data_system'
  | 'payment'
  | 'ledger'
  | 'reorder'
  | 'shortcuts'

export type SectionDef = {
  id: string
  label: string
}

export type TabDef = {
  id: SettingsTabId
  label: string
  /** Ctrl+digit while on Settings (null = click / Ctrl+Tab only) — matches Tk */
  ctrlDigit: number | null
  layout: 'sections' | 'split' | 'toggle' | 'nested' | 'scroll'
  sections?: SectionDef[]
  toggles?: { id: string; label: string }[]
  nestedTabs?: { id: string; label: string }[]
}

/** Tk Ctrl map: 1 Pharmacy … 5 Layout, 6 Import (skips Sales), 7 Alerts, 8 Data, 9 Payment, 0 Ledger */
export const SETTINGS_TABS: TabDef[] = [
  {
    id: 'pharmacy',
    label: 'Pharmacy Profile',
    ctrlDigit: 1,
    layout: 'sections',
    sections: [
      { id: 'profile', label: 'Pharmacy Profile' },
      { id: 'bill_template', label: 'Bill Template' },
      { id: 'bill_fields', label: 'Bill Fields' },
      { id: 'bill_text', label: 'Bill Text Lines' },
      { id: 'bill_logo', label: 'Bill Logo' },
      { id: 'bill_paper', label: 'Paper & Copies' },
      { id: 'bill_sales', label: 'Sales Print Buttons' },
      { id: 'printer', label: 'Printer Setup' },
    ],
  },
  {
    id: 'contacts',
    label: 'Contacts',
    ctrlDigit: 2,
    layout: 'sections',
    sections: [
      { id: 'doctors', label: 'Doctors' },
      { id: 'customers', label: 'Customers' },
      { id: 'villages', label: 'Villages' },
      { id: 'suppliers', label: 'Suppliers' },
    ],
  },
  {
    id: 'shelf',
    label: 'Shelf Management',
    ctrlDigit: 3,
    layout: 'split',
    sections: [
      { id: 'structure', label: 'Shelf Structure' },
      { id: 'medicines', label: 'Medicines at location' },
    ],
  },
  {
    id: 'appearance',
    label: 'Appearance',
    ctrlDigit: 4,
    layout: 'sections',
    sections: [
      { id: 'theme', label: 'Theme' },
      { id: 'font', label: 'Font Size' },
      { id: 'banner', label: 'Home Banner' },
      { id: 'quick_access', label: 'Quick Access' },
      { id: 'dashboard', label: 'Dashboard Sections' },
    ],
  },
  {
    id: 'layout_lists',
    label: 'Layout & Lists',
    ctrlDigit: 5,
    layout: 'sections',
    sections: [
      { id: 'columns', label: 'Column Visibility' },
      { id: 'rows', label: 'Table Row Counts' },
      { id: 'med_types', label: 'Medicine Types' },
      { id: 'units', label: 'Medicine Units' },
      { id: 'schedules', label: 'Schedules' },
      { id: 'thresholds', label: 'Thresholds & Reorder' },
      { id: 'sales_margin', label: 'Sales Margin Display' },
      { id: 'record_indicators', label: 'Record Indicators' },
      { id: 'app_mode', label: 'App Mode' },
    ],
  },
  {
    id: 'sales_billing',
    label: 'Sales & Billing',
    ctrlDigit: null,
    layout: 'sections',
    sections: [
      { id: 'billing_layout', label: 'Billing Layout & FY' },
      { id: 'batch_picker', label: 'Batch Picker' },
      { id: 'sales_return', label: 'Sales Return' },
      { id: 'sales_bills', label: 'Sales Bill Save' },
      { id: 'upi_payment', label: 'UPI Payment QR' },
      { id: 'autosave', label: 'Autosave' },
    ],
  },
  {
    id: 'import',
    label: 'Import',
    ctrlDigit: 6,
    layout: 'sections',
    sections: [
      { id: 'purchase_bill', label: 'Purchase Bill' },
      { id: 'web', label: 'Web Entry' },
      { id: 'file_import', label: 'Import Data' },
      { id: 'mobile', label: 'Mobile Import' },
      { id: 'opening_stock', label: 'Opening Stock' },
    ],
  },
  {
    id: 'alerts',
    label: 'Alert & Monitoring',
    ctrlDigit: 7,
    layout: 'nested',
    nestedTabs: [
      { id: 'low', label: 'Low Stock' },
      { id: 'out', label: 'Out of Stock' },
      { id: 'expired', label: 'Expired' },
      { id: 'near', label: 'Near Expiry' },
      { id: 'dues', label: 'Customer Dues' },
      { id: 'setup', label: 'Popup & Thresholds' },
    ],
  },
  {
    id: 'data_system',
    label: 'Data & System',
    ctrlDigit: 8,
    layout: 'sections',
    sections: [
      { id: 'stores', label: 'Stores & Startup Alerts' },
      { id: 'export', label: 'Export Data' },
      { id: 'maintenance', label: 'Data Maintenance' },
      { id: 'backup', label: 'Google Drive Backup' },
      { id: 'updates', label: 'App Updates' },
      { id: 'my_assist', label: 'My Assist' },
      { id: 'admin', label: 'Administrator' },
      { id: 'danger', label: 'Danger Zone' },
    ],
  },
  {
    id: 'payment',
    label: 'Payment',
    ctrlDigit: 9,
    layout: 'toggle',
    toggles: [
      { id: 'supplier', label: 'Supplier Payment' },
      { id: 'customer', label: 'Customer Payment' },
    ],
  },
  {
    id: 'ledger',
    label: 'Ledger',
    ctrlDigit: 0,
    layout: 'toggle',
    toggles: [
      { id: 'supplier', label: 'Supplier Ledger' },
      { id: 'customer', label: 'Customer Ledger' },
    ],
  },
  {
    id: 'reorder',
    label: 'Reorder',
    ctrlDigit: null,
    layout: 'toggle',
    toggles: [
      { id: 'pending', label: 'Pending Orders' },
      { id: 'new', label: 'New Order' },
      { id: 'by_supplier', label: 'Load by Supplier' },
      { id: 'defaults', label: 'Reorder Defaults…' },
    ],
  },
  {
    id: 'shortcuts',
    label: '⌨ Shortcuts',
    ctrlDigit: null,
    layout: 'sections',
    sections: [
      { id: 'cheatsheet', label: 'Keyboard Shortcuts' },
      { id: 'voice', label: 'Voice Commands' },
    ],
  },
]
