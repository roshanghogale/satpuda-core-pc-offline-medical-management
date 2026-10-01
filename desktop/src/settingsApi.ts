import { getApiBase } from './api'

export type Opt = { value: string; label: string }
export type KeyLabel = { key: string; label: string }

export type SettingsBundle = {
  options: {
    themes: Opt[]
    theme_packs?: Opt[]
    templates: Opt[]
    paper_sizes: string[]
    bill_size_modes: Opt[] | string[]
    half_positions: Opt[]
    a4_two_copy_layouts: Opt[]
    enter_actions: Opt[]
    bill_fields: KeyLabel[]
    bill_field_groups?: { group: string; items: KeyLabel[] }[]
    bill_field_defaults?: Record<string, boolean>
    quick_access: KeyLabel[]
    dashboard_sections: KeyLabel[]
    payment_mode_positions: Opt[]
    item_discount_modes?: Opt[]
    margin_display_modes?: Opt[]
    history_scopes?: Opt[]
    batch_sort_orders: Opt[]
    pdf_save_layouts: Opt[]
    upi_amount_modes: Opt[]
    app_modes: Opt[]
    sync_modes: Opt[]
    export_formats: string[]
    printer_types: Opt[]
    dot_matrix_print_methods?: Opt[]
    dot_matrix_tear_modes?: Opt[]
    payment_modes?: string[]
    display_styles?: Opt[]
    column_pages?: { key: string; label: string }[]
    table_columns?: Record<string, KeyLabel[]>
    /** Which columns ship hidden until the shop turns them on. */
    column_defaults?: Record<string, Record<string, boolean>>
    /** Export report keys per page — the store keeps ticks per report. */
    export_reports?: Record<string, string[]>
    /** Every column any export report on that page emits (not the screen list). */
    export_columns?: Record<string, KeyLabel[]>
  }
  appearance: Record<string, unknown>
  layout_lists: Record<string, unknown>
  sales_billing: Record<string, unknown>
  system: Record<string, unknown>
  import: Record<string, unknown>
  pharmacy?: {
    profile: Record<string, unknown>
    bill: Record<string, unknown>
    login: Record<string, unknown>
    printer: Record<string, unknown>
    installed_printers: string[]
    spooler_running?: boolean
    print_log_path?: string
    sumatra_detected?: string
  }
  contacts?: {
    doctors: { id: number; name: string; reg_no: string; phone: string }[]
    customers: {
      id: number
      name: string
      phone: string
      village: string
      address?: string
      total_due: number
    }[]
    suppliers: {
      id: number
      name: string
      phone: string
      gstin: string
      address?: string
      total_due: number
    }[]
    villages: string[]
    default_village: string
  }
  thresholds?: {
    med_types: string[]
    low_stock: Record<string, string>
    near_expiry: Record<string, string>
    customer_due_min_amount: string
    customer_due_min_days: string
    reorder_default_qty: string
  }
  alerts?: {
    counts: Record<string, number>
    low_stock: unknown[][]
    out_of_stock: unknown[][]
    expired: unknown[][]
    near_expiry: unknown[][]
    customer_due: unknown[][]
    /** Every stocked batch with an expiry (past or future), for the Month / Year filter:
     *  name, batch, expiry, days left, qty, supplier, bill, purchase date, expiry ISO. */
    expiry_by_batch?: unknown[][]
  }
  shelf?: {
    racks: {
      id: number
      name: string
      sections: {
        id: number
        name: string
        boxes: { id: number; name: string }[]
      }[]
    }[]
    show_location?: boolean
    assigned?: { id: number; name: string; batch: string; location: string }[]
    unassigned?: { id: number; name: string; batch: string; stock: number }[]
    error?: string
  }
  reorder?: {
    groups: Record<string, unknown>[]
    suppliers: Record<string, unknown>[]
    reorder_default_qty: string
  }
  payments_supplier?: PaymentBundle
  payments_customer?: PaymentBundle
}

export type PaymentBundle = {
  kind: string
  history: Record<string, unknown>[]
  parties: { id: number; name: string; due: number }[]
  /** What looks mistyped (a date after today); the payment was saved regardless. */
  warnings?: string[]
}

export type LedgerBundle = {
  kind: string
  party: string
  from: string
  to: string
  parties: string[]
  rows: {
    date: string
    particulars: string
    debit: number
    credit: number
    balance: number
    tag: string
  }[]
  summary: {
    opening: number
    debits: number
    credits: number
    closing: number
  }
}

async function jsonFetch<T>(
  path: string,
  init?: RequestInit,
  /** Return a refusal that names itself with a `code`, instead of throwing.
   *  Opt-in per call on purpose: this helper backs every settings read and
   *  write, and a blanket carve-out would turn "Database not open" into a
   *  value the caller treats as a successful save. */
  passTypedErrors = false,
): Promise<T> {
  const res = await fetch(`${getApiBase()}${path}`, {
    headers: { 'Content-Type': 'application/json', ...(init?.headers || {}) },
    ...init,
  })
  const data = await res.json().catch(() => ({}))
  if (!res.ok) {
    const typed = data as { ok?: boolean; code?: string; error?: string }
    if (passTypedErrors && typed.ok === false && typeof typed.code === 'string' && typed.code) {
      return data as T
    }
    throw new Error((data as { error?: string }).error || `HTTP ${res.status}`)
  }
  return data as T
}

export function fetchSettingsBundle() {
  return jsonFetch<SettingsBundle>('/api/settings/bundle')
}

export function fetchContacts() {
  return jsonFetch<NonNullable<SettingsBundle['contacts']>>(
    '/api/settings/contacts',
  )
}

export type AlertCategoryKey =
  | 'low_stock'
  | 'out_of_stock'
  | 'near_expiry'
  | 'expired'
  | 'customer_due'

/** core/startup_alerts_prefs.load_alert_popup_prefs + get_thresholds. */
export type AlertPopupPrefs = {
  enabled: boolean
  snoozed_today: boolean
  categories: Record<AlertCategoryKey, boolean>
  recheck_minutes: number
  thresholds: NonNullable<SettingsBundle['thresholds']> | null
}

export function fetchAlertPrefs() {
  return jsonFetch<AlertPopupPrefs>('/api/settings/alert_prefs')
}

export function saveAlertPrefs(body: Record<string, unknown>) {
  return jsonFetch<AlertPopupPrefs>('/api/settings/alert_prefs', {
    method: 'PUT',
    body: JSON.stringify(body),
  })
}

export function fetchAlerts() {
  return jsonFetch<NonNullable<SettingsBundle['alerts']>>(
    '/api/settings/alerts',
  )
}

export function fetchShelf() {
  return jsonFetch<NonNullable<SettingsBundle['shelf']>>('/api/settings/shelf')
}

export function fetchReorder() {
  return jsonFetch<NonNullable<SettingsBundle['reorder']>>(
    '/api/settings/reorder',
  )
}

export function saveSettingsSection(
  section:
    | 'appearance'
    | 'layout_lists'
    | 'sales_billing'
    | 'pharmacy'
    | 'system'
    | 'import'
    | 'thresholds',
  body: Record<string, unknown>,
) {
  return jsonFetch<Record<string, unknown>>(
    `/api/settings/${section}`,
    { method: 'PUT', body: JSON.stringify(body) },
    // Only 'system'. Going Online can be refused with 409
    // store_name_taken_on_server, and that refusal carries the name and id of
    // the store already holding the name -- everything the "connect to that
    // store instead" dialog needs. Thrown as a bare Error, all of it was lost.
    section === 'system',
  )
}

export function mutateContact(body: Record<string, unknown>) {
  return jsonFetch<SettingsBundle['contacts']>('/api/settings/contacts', {
    method: 'PUT',
    body: JSON.stringify(body),
  })
}

export function fetchPayments(kind: 'supplier' | 'customer') {
  return jsonFetch<PaymentBundle>(`/api/settings/payments?kind=${kind}`)
}

export function savePayment(body: Record<string, unknown>) {
  return jsonFetch<PaymentBundle>('/api/settings/payments', {
    method: 'PUT',
    body: JSON.stringify(body),
  })
}

export function deletePayment(body: Record<string, unknown>) {
  return jsonFetch<PaymentBundle>('/api/settings/payments/delete', {
    method: 'PUT',
    body: JSON.stringify(body),
  })
}

export function fetchLedger(params: {
  kind: 'supplier' | 'customer'
  party: string
  from: string
  to: string
}) {
  const q = new URLSearchParams({
    kind: params.kind,
    party: params.party,
    from: params.from,
    to: params.to,
  })
  return jsonFetch<LedgerBundle>(`/api/settings/ledger?${q}`)
}

export function mutateShelf(body: Record<string, unknown>) {
  return jsonFetch<SettingsBundle['shelf']>('/api/settings/shelf', {
    method: 'PUT',
    body: JSON.stringify(body),
  })
}

export function systemAction(body: Record<string, unknown>) {
  return jsonFetch<Record<string, unknown>>('/api/settings/system/action', {
    method: 'PUT',
    body: JSON.stringify(body),
  })
}

export function importAction(body: Record<string, unknown>) {
  return jsonFetch<Record<string, unknown>>('/api/settings/import/action', {
    method: 'PUT',
    body: JSON.stringify(body),
  })
}

export function reorderAction(body: Record<string, unknown>) {
  return jsonFetch<Record<string, unknown>>('/api/settings/reorder/action', {
    method: 'PUT',
    body: JSON.stringify(body),
  })
}

export function testPrinter(printer?: string) {
  return jsonFetch<{ ok: boolean; message: string }>(
    '/api/settings/printer/test',
    {
      method: 'PUT',
      body: JSON.stringify(printer ? { printer } : {}),
    },
  )
}

/** Print the A6 dot matrix alignment ruler, using the values on screen. */
export function printAlignmentTest(bill: Record<string, unknown>) {
  return jsonFetch<{ ok: boolean; message: string }>(
    '/api/settings/printer/alignment-test',
    {
      method: 'PUT',
      body: JSON.stringify({ bill }),
    },
  )
}

export function pharmacyAction(body: Record<string, unknown>) {
  return jsonFetch<Record<string, unknown>>('/api/settings/pharmacy/action', {
    method: 'PUT',
    body: JSON.stringify(body),
  })
}

export function browseHomeBanner() {
  return jsonFetch<{
    ok?: boolean
    cancelled?: boolean
    path?: string
    home_banner_path?: string
    home_banner_use_default?: boolean
    error?: string
    message?: string
  }>('/api/settings/appearance/browse_banner', {
    method: 'PUT',
    body: JSON.stringify({}),
  })
}

/** Send a picture the shop chose in the webview.
 *
 *  The engine cannot open a file dialog: it is a windowless sidecar whose
 *  requests are served on worker threads, and the shipped build excludes
 *  tkinter outright — so asking it to browse did nothing at all. The browser
 *  has the picker; it just cannot hand over a path, so it hands over bytes.
 */
export function uploadHomeBanner(filename: string, dataBase64: string) {
  return jsonFetch<{
    ok?: boolean
    path?: string
    home_banner_path?: string
    home_banner_use_default?: boolean
    error?: string
    message?: string
  }>('/api/settings/appearance/browse_banner', {
    method: 'PUT',
    body: JSON.stringify({ filename, data_base64: dataBase64 }),
  })
}

/** Read a File the browser opened as base64, without its data: prefix. */
export function imageFileToBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => {
      const out = String(reader.result || '')
      const comma = out.indexOf(',')
      resolve(comma >= 0 ? out.slice(comma + 1) : out)
    }
    reader.onerror = () => reject(reader.error || new Error('Could not read that file.'))
    reader.readAsDataURL(file)
  })
}

export type AlertNavigatePayload = {
  page: string
  settingsTab?: string
  settingsToggle?: string
  reorderMedicinePrefill?: {
    medicine_name: string
    pack_size: string
    quantity: number
    unit_price: number
  }
  bulkReorderLoad?: boolean
  returnsTab?: string
  returnsBulkPrefill?: Record<string, unknown>
  disposalPrefill?: {
    medicine_name: string
    batch_no: string
    expiry_date: string
    available_qty: number
  }
}

export function applyAlertNavigation(
  nav: AlertNavigatePayload | undefined,
  onNavigate?: (page: string, payload?: Record<string, unknown>) => void,
) {
  if (!nav?.page || !onNavigate) return
  onNavigate(nav.page, {
    settingsTab: nav.settingsTab,
    settingsToggle: nav.settingsToggle,
    reorderMedicinePrefill: nav.reorderMedicinePrefill,
    bulkReorderLoad: nav.bulkReorderLoad,
    returnsTab: nav.returnsTab,
    returnsBulkPrefill: nav.returnsBulkPrefill,
    disposalPrefill: nav.disposalPrefill,
  })
}

/** Alert & Monitoring: a file (csv / xlsx / pdf) or paper (dot matrix / normal printer). */
export function exportAlerts(body: {
  title: string
  sections: { title: string; columns: string[]; rows: unknown[][] }[]
  format?: 'csv' | 'xlsx' | 'pdf'
  print_to?: '' | 'dot_matrix' | 'printer'
  paper?: 'A4' | 'A5'
  page_layout?: 'portrait' | 'landscape'
}) {
  return jsonFetch<{
    ok: boolean
    error?: string
    message?: string
    printed?: boolean
    filename?: string
    mime?: string
    content_base64?: string
  }>('/api/settings/alerts/export', { method: 'PUT', body: JSON.stringify(body) })
}

export function alertAction(body: Record<string, unknown>) {
  return jsonFetch<{
    ok: boolean
    error?: string
    message?: string
    alerts?: SettingsBundle['alerts']
    navigate?: AlertNavigatePayload
  }>('/api/settings/alerts/action', {
    method: 'PUT',
    body: JSON.stringify(body),
  })
}
