/** Fetch helpers for migrated desktop pages — same local DB as Tk. */

import { getApiBase } from './api'
import type { AlertNavigatePayload } from './settingsApi'

export type RowStyle = {
  status?: string
  label?: string
  icon?: string
  icon_src?: string
  badge_text?: string
  color?: string
  full_row?: boolean
  full_row_text?: boolean
  badge?: boolean
  border?: string | null
  display_style?: string
} | null

export type TablePayload = {
  columns: string[]
  rows: unknown[][]
  row_styles?: RowStyle[]
  row_ids?: number[]
  summary: Record<string, number | string>
  types?: string[]
  schedules?: string[]
  sort_options?: string[]
  history_scope?: string
  fy_label?: string
  filter_from?: string
  filter_to?: string
  default_fy_applied?: boolean
  filter_choices?: {
    customers?: string[]
    suppliers?: string[]
    medicines?: string[]
  }
  /** Set when an Online read failed rather than genuinely returning no rows.
   *  Online mode keeps no data on this PC, so a broken link to the store on the
   *  server used to render as a clean, working, completely empty screen. */
  server_error?: string
  /** Set when the range holds more bills than the list shows (Sales History). The page
   *  used to stop at 500 rows and total those alone without saying so. */
  rows_note?: string
}

export type MedicineSuggestion = {
  id: number
  name: string
  batch: string
  stock: number
  mrp: number
  rate: number
  expiry: string
  unit: string
  type: string
  schedule?: string
  location?: string
  gst_percent?: number
}

export type MedicineBatch = MedicineSuggestion & {
  available: number
}

export type CustomerDetail = {
  id: number
  name: string
  phone: string
  address: string
  due: number
  credit?: number
}

export type SalesLinePayload = {
  id: number
  name: string
  batch: string
  expiry: string
  qty: number
  rate: number
  amount: number
  original_amount: number
  medicine_discount: number
  schedule: string
  type: string
  display_type?: string
  gst_percent: number
  location: string
  unit: string
  mrp: number
  /** Strip / pack list MRP (before per-tablet divide). */
  list_mrp?: number
  /** Inventory purchase rate (strip or unit). */
  purchase_rate?: number
  margin?: number
  margin_pct?: number
  /** Pack divisor Classic's margin uses (tablets per strip, else 1). */
  margin_div?: number
}

export type SalesCalcResult = {
  ok: boolean
  error?: string
  summary: {
    subtotal: number
    discount_amount: number
    discount_pct: number
    pre_round_total: number
    total_amount: number
  }
  payment: {
    amount_paid: number
    due_amount: number
    credit_amount: number
    need_to_pay: number
    /** Per-bill bookkeeping: previous due plus this bill, less what was paid.
     *  A customer's standing credit is clamped at their previous due here, so
     *  spare credit does NOT reduce it. This is the figure that gets stored. */
    total_due: number
    /** Credit already on the customer's account that this bill uses up. */
    credit_applied?: number
    /** total_due with that credit counted — what to actually collect. */
    net_total_due?: number
  }
  cash_paid: number
  online_paid: number
  previous_due: number
  previous_credit: number
  rounding: number
  is_due: boolean
  warnings: string[]
  gst_label: string
}

export type SalesSaveResult = {
  ok: boolean
  error?: string
  code?: string
  need_confirm?: boolean
  messages?: string[]
  bill_no?: string
  sale_id?: number
  customer_id?: number
  merged_counter?: boolean
  customer_due?: number
  customer_credit?: number
  next_bill_hint?: string
  /** Set only once a file is confirmed on disk (the print paths). */
  pdf_path?: string
  /** The resolved folder the bill is written to. Always set on save. */
  pdf_dir?: string
  /** What looks mistyped; the bill was saved regardless. */
  warnings?: string[]
}

export type SalesFormDefaults = {
  next_bill_hint: string
  customers: string[]
  customer_details?: CustomerDetail[]
  doctors: string[]
  villages: string[]
  default_village?: string
  medicines?: MedicineSuggestion[]
  payment_modes: string[]
  form: {
    customer: string
    phone: string
    doctor: string
    address: string
    bill_date: string
    payment_mode: string
    items: unknown[]
    subtotal: number
    discount: number
    gst: number
    total: number
    cash: number
    due: number
  }
}

export type PurchaseFormDefaults = {
  suppliers: {
    id?: number
    name: string
    phone?: string
    address?: string
    gstin?: string
    dl?: string
    due?: number
    credit?: number
  }[]
  medicine_types: string[]
  schedules?: string[]
  medicines?: MedicineSuggestion[]
  form: {
    supplier: string
    phone: string
    address: string
    gstin: string
    dl: string
    purchase_date: string
    bill_number: string
    items: unknown[]
    subtotal: number
    gst: number
    discount: number
    total: number
    paid: number
    due: number
  }
}

export type ReturnsBundle = {
  sales_returns: {
    count: number
    columns?: string[]
    rows: unknown[][]
    row_ids?: number[]
  }
  purchase_returns: {
    count: number
    columns?: string[]
    rows: unknown[][]
    row_ids?: number[]
  }
  writeoffs: {
    count: number
    columns?: string[]
    rows: unknown[][]
    row_ids?: number[]
  }
}

async function getJson<T>(path: string): Promise<T> {
  const res = await fetch(`${getApiBase()}${path}`)
  const data = await res.json().catch(() => ({}))
  if (!res.ok) {
    const err = data as { error?: string; path?: string }
    const msg = err.error || `HTTP ${res.status}`
    throw new Error(err.path ? `${msg} (${err.path})` : msg)
  }
  return data as T
}

function qs(
  params: Record<string, string | number | boolean | undefined>,
): string {
  const sp = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === '') continue
    sp.set(k, String(v))
  }
  const s = sp.toString()
  return s ? `?${s}` : ''
}

export function fetchInventory(
  opts: {
    q?: string
    type?: string
    stock?: string
    expiry?: string
    schedule?: string
    sort?: string
    low?: boolean
  } = {},
) {
  return getJson<TablePayload>(
    `/api/inventory${qs({
      q: opts.q,
      type: opts.type,
      stock: opts.stock,
      expiry: opts.expiry,
      schedule: opts.schedule,
      sort: opts.sort,
      low: opts.low ? 1 : 0,
    })}`,
  )
}

export function fetchSalesHistory(
  opts: {
    q?: string
    from?: string
    to?: string
    medicine?: string
    batch?: string
    customer?: string
    due?: string
    schedule?: string
    sort?: string
  } = {},
) {
  return getJson<TablePayload>(
    `/api/sales/history${qs({
      q: opts.q,
      from: opts.from,
      to: opts.to,
      medicine: opts.medicine,
      batch: opts.batch,
      customer: opts.customer,
      due: opts.due,
      schedule: opts.schedule,
      sort: opts.sort,
    })}`,
  )
}

export function fetchPurchaseHistory(
  opts: {
    q?: string
    from?: string
    to?: string
    supplier?: string
    due?: string
    schedule?: string
    medicine?: string
    batch?: string
    sort?: string
  } = {},
) {
  return getJson<TablePayload>(
    `/api/purchase/history${qs({
      q: opts.q,
      from: opts.from,
      to: opts.to,
      supplier: opts.supplier,
      due: opts.due,
      schedule: opts.schedule,
      medicine: opts.medicine,
      batch: opts.batch,
      sort: opts.sort,
    })}`,
  )
}

export type InventoryMedicine = {
  id: number
  name: string
  type: string
  batch: string
  expiry: string
  stock_qty: number
  stock_strips: number
  extra_tablets: number
  unit: string
  mrp: number
  rate: number
  mrp_tab?: number
  rate_tab?: number
  manufacturer: string
  schedule: string
  content_drug: string
  hsn_code: string
  location?: string
  is_strip: boolean
  tablets_per_stripe: number
}

export type InventoryHistoryLine = {
  date: string
  bill: string
  party: string
  qty?: number
  free_qty?: number
  total_display?: string
  qty_display?: string
  rate: number
  amount: number
}

export function fetchInventoryMedicine(id: number) {
  return getJson<{
    ok: boolean
    error?: string
    medicine?: InventoryMedicine
    medicine_types?: string[]
    schedules?: string[]
    purchase_history?: InventoryHistoryLine[]
    sales_history?: InventoryHistoryLine[]
    purchase_summary?: string
    sales_summary?: string
    qty_column_label?: string
  }>(`/api/inventory/medicine${qs({ id })}`)
}

export function updateInventoryMedicine(body: Record<string, unknown>) {
  return postJson<{ ok: boolean; error?: string; medicine_id?: number }>(
    '/api/inventory/medicine/update',
    body,
  )
}

export function deleteInventoryMedicine(id: number) {
  return postJson<{ ok: boolean; error?: string }>(
    '/api/inventory/medicine/delete',
    { id },
  )
}

export function fetchSalesForm() {
  return getJson<SalesFormDefaults>('/api/sales/form')
}

/** Next bill number in the financial year of `billDate` (a back-dated bill takes the old year's). */
export function fetchSalesBillHint(billDate: string) {
  return getJson<{ next_bill_hint: string; bill_date: string }>(
    `/api/sales/form${qs({ hint_only: 1, bill_date: billDate })}`,
  )
}

export function fetchPurchaseForm() {
  return getJson<PurchaseFormDefaults>('/api/purchase/form')
}

export function fetchReturnsSummary() {
  return getJson<ReturnsBundle>('/api/returns/summary')
}

export type SalesReturnBill = {
  sale_id: number
  bill_no: string
  bill_date: string
  customer: string
  label: string
}

export type ReturnLineItem = {
  medicine_id: number
  name: string
  batch: string
  orig_qty: number
  remaining_qty: number
  qty?: number
  rate: number
  amount?: number
  type?: string
  is_tablet?: boolean
  tablets_per_stripe?: number
  unit?: string
  /** Already returned on this medicine (the whole bill's lines of it). */
  returned_qty?: number
  /** >1 when an old bill carried this medicine on several lines. */
  merged_lines?: number
}

export type LoadedSalesReturnBill = {
  ok: boolean
  error?: string
  sale_id?: number
  bill_no?: string
  bill_date?: string
  discount?: number
  customer?: string
  customer_id?: number
  bill_total?: number
  bill_paid?: number
  bill_due?: number
  previous_due?: number
  previous_credit?: number
  items?: ReturnLineItem[]
}

export type PurchaseReturnBill = {
  purchase_id: number
  bill_label: string
  purchase_date: string
  supplier: string
  label: string
}

export type LoadedPurchaseReturnBill = {
  ok: boolean
  error?: string
  purchase_id?: number
  bill_label?: string
  purchase_date?: string
  supplier?: string
  supplier_id?: number
  bill_total?: number
  bill_paid?: number
  bill_due?: number
  previous_due?: number
  previous_credit?: number
  items?: ReturnLineItem[]
}

export type GeneralProduct = {
  id: number
  name: string
  rate: number
  mrp: number
}

export type ReorderPrefill = {
  supplier_name: string
  medicine_name: string
  pack_size: string
  quantity: number
  rate: number
  order_id: number
  order_no?: string
}

export function searchSalesReturnBills(q = '', medicine = '') {
  return getJson<{ ok: boolean; bills: SalesReturnBill[] }>(
    `/api/returns/sales/bills${qs({ q, medicine })}`,
  )
}

export function loadSalesReturnBill(saleId: number) {
  return getJson<LoadedSalesReturnBill>(
    `/api/returns/sales/bill${qs({ sale_id: saleId })}`,
  )
}

export function saveSalesReturn(body: Record<string, unknown>) {
  return postJson<{
    ok: boolean
    error?: string
    return_id?: number
    return_no?: string
    refund_amount?: number
    refund_payout?: number
    settle_mode?: string
    queued?: boolean
  }>('/api/returns/sales/save', body)
}

export function deleteSalesReturn(id: number) {
  return postJson<{
    ok: boolean
    error?: string
    queued?: boolean
    // Set when the paired refund payment could not be checked on the server.
    // The return went; the money it paid out may still be on the customer.
    warning?: string
  }>('/api/returns/sales/delete', { id })
}

export function searchPurchaseReturnBills(q = '') {
  return getJson<{ ok: boolean; purchases: PurchaseReturnBill[] }>(
    `/api/returns/purchase/search${qs({ q })}`,
  )
}

export function loadPurchaseReturnBill(purchaseId: number) {
  return getJson<LoadedPurchaseReturnBill>(
    `/api/returns/purchase/load${qs({ purchase_id: purchaseId })}`,
  )
}

export function savePurchaseReturn(body: Record<string, unknown>) {
  return postJson<{
    ok: boolean
    error?: string
    return_id?: number
    return_no?: string
    refund_amount?: number
  }>('/api/returns/purchase/save', body)
}

export function deletePurchaseReturn(id: number) {
  return postJson<{ ok: boolean; error?: string; queued?: boolean }>(
    '/api/returns/purchase/delete',
    { id },
  )
}

/** Edit a saved purchase return: the old one is taken back and the corrected
 *  lines are saved as a new return on the same bill (see replace_purchase_return). */
export function replacePurchaseReturn(body: Record<string, unknown>) {
  return postJson<{
    ok: boolean
    error?: string
    return_id?: number
    return_no?: string
    refund_amount?: number
    replaced_return_no?: string
    restored?: boolean
  }>('/api/returns/purchase/replace', body)
}

export function fetchPurchaseReturnDetails(returnId: number) {
  return getJson<{
    ok: boolean
    error?: string
    id?: number
    return_no?: string
    return_date?: string
    purchase_no?: string
    supplier?: string
    refund_amount?: number
    reason?: string
    purchase_id?: number
    supplier_id?: number
    items?: {
      medicine_id: number
      name: string
      batch: string
      qty: number
      rate: number
      amount: number
    }[]
  }>(`/api/returns/purchase/return${qs({ return_id: returnId })}`)
}

export function savePurchaseReturnPdf(returnId: number) {
  return postJson<{
    ok: boolean
    error?: string
    path?: string
    pdf_path?: string
    return_no?: string
  }>('/api/returns/purchase/return/pdf', { return_id: returnId })
}

export function lookupDisposalMedicine(body: Record<string, unknown>) {
  return postJson<{
    ok: boolean
    error?: string
    medicine_id?: number
    name?: string
    batch?: string
    stock_qty?: number
    type?: string
    purchase?: Record<string, unknown>
  }>('/api/returns/disposal/lookup', body)
}

export function submitDisposal(body: Record<string, unknown>) {
  return postJson<{
    ok: boolean
    error?: string
    disposal_nos?: string[]
    errors?: string[]
  }>('/api/returns/disposal/submit', body)
}

export function fetchGeneralProducts(q = '') {
  return getJson<{ ok: boolean; products: GeneralProduct[] }>(
    `/api/general-products${qs({ q })}`,
  )
}

export function saveGeneralProduct(body: Record<string, unknown>) {
  return postJson<{ ok: boolean; error?: string; id?: number }>(
    '/api/general-products/save',
    body,
  )
}

export function deleteGeneralProduct(id: number) {
  return postJson<{ ok: boolean; error?: string; id?: number }>(
    '/api/general-products/delete',
    { id },
  )
}

export function searchMedicines(q: string, limit = 40) {
  return getJson<{ medicines: MedicineSuggestion[] }>(
    `/api/medicines/search${qs({ q, limit })}`,
  )
}

export type MedicineNameRow = {
  name: string
  stock: number
  mrp: number
  unit: string
  type: string
  schedule: string
  batch_count: number
}

export function fetchMedicineNames(
  q: string,
  opts: {
    bill_date?: string
    limit?: number
    reserved?: Record<string, number>
  } = {},
) {
  return getJson<{ names: MedicineNameRow[] }>(
    `/api/medicines/names${qs({
      q,
      bill_date: opts.bill_date,
      limit: opts.limit ?? 40,
      reserved: opts.reserved ? JSON.stringify(opts.reserved) : undefined,
    })}`,
  )
}

async function postJson<T>(
  path: string,
  body: unknown,
  /**
   * Hand a TYPED engine refusal back to the caller instead of throwing.
   *
   * The engine answers a refusal it expects the screen to act on with
   * `{ok: false, code: "payment_required" | "print_failed" | …}`, and
   * desktop_api turns every `ok:false` into HTTP 400. Because this helper threw
   * on any non-409, the code-aware branches on the Sales page were unreachable:
   * "Payment Required" (which puts the cursor in the Cash box) and the
   * `savedButNotPrinted` recovery both fell through to a bare "Error" popup.
   * The counter read that as "nothing happened" and pressed Save again — which
   * on a print failure wrote the bill a SECOND time and deducted stock twice.
   *
   * Opt-in per call site, and only where the caller actually inspects `ok`, so
   * an endpoint whose screen assumes "no throw means success" is unaffected.
   */
  passTypedErrors = false,
): Promise<T> {
  const res = await fetch(`${getApiBase()}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body ?? {}),
  })
  const data = await res.json().catch(() => ({}))
  // 409 = discount loss confirm — still return payload to the caller
  if (!res.ok && res.status !== 409) {
    const err = data as { error?: string; path?: string; ok?: boolean; code?: string }
    if (passTypedErrors && err.ok === false && typeof err.code === 'string' && err.code) {
      return data as T
    }
    const msg = err.error || `HTTP ${res.status}`
    throw new Error(err.path ? `${msg} (${err.path})` : msg)
  }
  return data as T
}

export function fetchMedicineBatches(
  name: string,
  opts: {
    bill_date?: string
    reserved?: Record<string, number>
  } = {},
) {
  return getJson<{ name: string; batches: MedicineBatch[]; as_of?: string }>(
    `/api/medicines/batches${qs({
      name,
      bill_date: opts.bill_date,
      reserved: opts.reserved ? JSON.stringify(opts.reserved) : undefined,
    })}`,
  )
}

/** Live customer balance for Sales Previous Due (Classic verify_customer_due). */
export function lookupCustomerByName(name: string, force = true) {
  return getJson<{
    ok: boolean
    found: boolean
    customer: CustomerDetail | null
  }>(
    `/api/customers/lookup${qs({
      name,
      force: force ? 1 : 0,
    })}`,
  )
}

/** Live supplier balance for Purchase Previous Due (Classic load_supplier_details). */
export function lookupSupplierByName(name: string, force = true) {
  return getJson<{
    ok: boolean
    found: boolean
    supplier: {
      id: number
      name: string
      phone: string
      address: string
      gstin: string
      dl: string
      due: number
      credit: number
    } | null
  }>(
    `/api/suppliers/lookup${qs({
      name,
      force: force ? 1 : 0,
    })}`,
  )
}

export function buildSalesLine(body: {
  medicine_id: number
  qty: number
  medicine_discount?: number
  disc_pct?: number
  bill_date?: string
  doctor_name?: string
  customer_name?: string
  reserved?: Record<string, number>
  /** A line already on the bill keeps the GST % it was sold at. */
  gst_percent?: number
}) {
  return postJson<{
    ok: boolean
    error?: string
    code?: string
    line?: SalesLinePayload
    warnings?: string[]
    available?: number
    // Both call sites on the Sales page branch on `code` -- Expired Medicine,
    // Out of Stock, Doctor Required and the rest each get their own dialog and
    // their own cursor placement. desktop_api turns every ok:false into HTTP
    // 400, so without passTypedErrors this threw and every one of those
    // refusals collapsed into a bare "Error" popup with the raw message.
  }>('/api/sales/build-line', body, true)
}

export function calcSalesBill(body: Record<string, unknown>) {
  return postJson<SalesCalcResult>('/api/sales/calc', body)
}

export function saveSalesBill(body: Record<string, unknown>) {
  return postJson<SalesSaveResult>('/api/sales/save', body, true)
}

export function fetchRecentSales(limit = 5) {
  return getJson<{
    ok: boolean
    sales: {
      id: number
      bill_no: string
      bill_date: string
      customer: string
      total: number
    }[]
  }>(`/api/sales/recent${qs({ limit })}`)
}

export type SalesRuntimePrefs = {
  ok: boolean
  autosave_enabled: boolean
  autosave_interval_seconds: number
  payment_mode_enabled: boolean
  payment_mode_position?: string
  item_discount_mode?: 'rupees' | 'percent' | string
  billing_show_item_discount?: boolean
  billing_show_margin_column?: boolean
  billing_show_total_margin?: boolean
  billing_margin_loss_warning?: boolean
  billing_margin_display_mode?: 'rupees' | 'percent' | string
  show_location?: boolean
  column_visibility?: Record<string, boolean>
  upi_qr_enabled: boolean
  upi_id: string
  upi_qr_amount_mode: string
  medicine_types: string[]
  /** Per-type tablets-per-strip for the Quick Sale pack box. */
  medicine_type_pack_defaults?: Record<string, string>
  print_slot_1: {
    label: string
    key: string
    paper_size: string
    copies: number
  }
  print_slot_2: {
    label: string
    key: string
    paper_size: string
    copies: number
  }
  cash_online_enter_action?: string
  due_rounding_enter_action?: string
}

export type LoadedSale = {
  ok: boolean
  error?: string
  sale_id?: number
  bill_no?: string
  is_autosave?: boolean
  editing_sale_id?: number | null
  autosave_sale_id?: number | null
  /** Qty already returned against the bill, per medicine id. */
  returned_by_medicine?: Record<string, number>
  returns_note?: string
  returns_unread?: boolean
  form?: {
    customer_name: string
    customer_id?: number | null
    customer_phone: string
    customer_address: string
    doctor_name: string
    doctor_phone: string
    bill_date: string
    payment_mode: string
    cash_paid: number
    online_paid: number
    discount_pct: number
    discount_rs: number
    rounding: number
    previous_due: number
    previous_credit: number
    items: SalesLinePayload[]
  }
  edit_payment_snapshot?: {
    previous_due: number
    previous_credit: number
  }
}

export function fetchSalesRuntimePrefs() {
  return getJson<SalesRuntimePrefs>('/api/sales/prefs')
}

export function loadSaleById(id: number) {
  return getJson<LoadedSale>(`/api/sales/load${qs({ id })}`)
}

export function loadLastSale() {
  return getJson<LoadedSale>('/api/sales/last')
}

export function fetchBillPreview(saleId: number) {
  return getJson<{ ok: boolean; error?: string; bill_no?: string; html?: string }>(
    `/api/sales/bill/preview${qs({ sale_id: saleId })}`,
  )
}

export type BillDetails = {
  ok: boolean
  error?: string
  sale_id?: number
  bill_no?: string
  bill_date?: string
  customer?: string
  phone?: string
  doctor?: string
  total_amount?: number
  discount?: number
  amount_paid?: number
  cash_paid?: number
  online_paid?: number
  previous_due?: number
  due_amount?: number
  credit_amount?: number
  total_due?: number
  items?: {
    name: string
    batch: string
    type: string
    qty: number
    rate: number
    gst_percent: number
    amount: number
  }[]
}

export function fetchBillDetails(saleId: number) {
  return getJson<BillDetails>(`/api/sales/bill/details${qs({ sale_id: saleId })}`)
}

export function saveBillPdf(saleId: number) {
  return postJson<{ ok: boolean; error?: string; pdf_path?: string }>(
    '/api/sales/bill/pdf',
    { sale_id: saleId },
  )
}

export function deleteZeroStockMedicines() {
  return postJson<{ ok: boolean; hidden?: number; message?: string; error?: string }>(
    '/api/inventory/bulk-delete-zero',
    {},
  )
}

export function deleteExpiredMedicines() {
  return postJson<{ ok: boolean; hidden?: number; message?: string; error?: string }>(
    '/api/inventory/bulk-delete-expired',
    {},
  )
}

export function fetchInventoryReorderPrefill(medicineId: number) {
  return getJson<{
    ok: boolean
    error?: string
    prefill?: {
      medicine_name: string
      pack_size: string
      quantity: number
      unit_price: number
    }
  }>(`/api/inventory/reorder-prefill${qs({ medicine_id: medicineId })}`)
}

export type PurchaseImportPreview = {
  ok: boolean
  error?: string
  import_token?: string
  supplier_name?: string
  bill_number?: string
  purchase_date?: string
  parser?: string
  source_type?: string
  valid_count?: number
  invalid_count?: number
  lines?: {
    name: string
    batch: string
    qty: number
    rate: number
    valid: boolean
    issues?: string[]
  }[]
}

export function fetchPurchaseImportPreview(importToken: string) {
  return getJson<PurchaseImportPreview>(
    `/api/purchase/import/preview${qs({ import_token: importToken })}`,
  )
}

export function buildQuickSaleLine(body: Record<string, unknown>) {
  return postJson<{
    ok: boolean
    error?: string
    code?: string
    line?: SalesLinePayload & { quick_add?: boolean; id?: number | null }
    // The engine's refusal is an HTTP 400, so the typed body has to be let
    // through or the code below never sees code:'doctor_required' at all.
  }>('/api/sales/quick-line', body, true)
}

export function autosaveSalesBill(body: Record<string, unknown>) {
  return postJson<{
    ok: boolean
    skipped?: boolean
    reason?: string
    autosave_sale_id?: number
    autosave_token?: string
    counter?: boolean
    real_bill?: boolean
    bill_no?: string
    error?: string
    /** bill_discarded: the refused bill from this tab was discarded; nothing more is saved. */
    code?: string
    /** Why a tick wrote nothing (reason not_available_on_date). */
    messages?: string[]
  }>('/api/sales/autosave', body)
}

export function discardAutosave(
  autosave_sale_id: number,
  autosave_token?: string,
) {
  return postJson<{ ok: boolean; deleted?: boolean; code?: string; error?: string }>(
    '/api/sales/autosave/discard',
    { autosave_sale_id, autosave_token },
  )
}

/** An unfinished sale this device is still holding a REAL bill open for. */
export type AutosaveSession = {
  token: string
  sale_id: number
  bill_no: string
  bill_date: string
  counter: boolean
  customer_name: string
  items: number
  total: number
  updated_at: number
  /** Left over from an earlier day. */
  stale: boolean
  restorable: boolean
  /** Only the form was kept: its Bill Date refused a line, so no bill was written. */
  held?: boolean
}

/** Ask the ENGINE what this device was in the middle of.
 *
 *  Which bill a tab owns is money — autosave writes a real bill on the first
 *  tick — so it cannot live only in React state (a crash takes it) or only in
 *  localStorage (a cleared profile takes it, and the Tk billing page cannot
 *  read it). The engine keeps the durable record; this reads it back. */
export function listAutosaveSessions() {
  return postJson<{
    ok: boolean
    sessions?: AutosaveSession[]
    error?: string
  }>('/api/sales/autosave/sessions', {})
}

export function resumeAutosave(autosave_token: string, autosave_sale_id = 0) {
  return postJson<{
    ok: boolean
    code?: string
    error?: string
    autosave_token?: string
    autosave_sale_id?: number
    bill_no?: string
    counter?: boolean
    form?: LoadedSale['form']
  }>('/api/sales/autosave/resume', { autosave_token, autosave_sale_id })
}

export function printSalesBill(body: Record<string, unknown>) {
  return postJson<
    SalesSaveResult & {
      slot?: number
      mode?: string
      dot_matrix?: boolean
      html_path?: string
      pdf_path?: string
      pdf_dir?: string
      cleared?: boolean
    }
  >('/api/sales/print', body, true)
}

export type PurchaseLinePayload = {
  medicine_id?: number | null
  id?: number | null
  name: string
  type?: string
  batch?: string
  expiry?: string
  qty: number
  free_qty?: number
  rate: number
  mrp?: number
  gst_pct?: number
  discount_pct?: number
  hsn_code?: string
  manufacturer?: string
  schedule?: string
  content_drug?: string
  unit?: string
  tablets_per_stripe?: number
  item_amount?: number
  line_amount?: number
  line_taxable?: number
  line_gst_amt?: number
  amount?: number
  taxable?: number
  gst_amt?: number
  import_taxable?: number
  import_gst_amt?: number
  _preserve_line_totals?: boolean
}

export type PurchaseCalcResult = {
  ok: boolean
  error?: string
  calc?: Record<string, number | string | unknown>
  items?: PurchaseLinePayload[]
  previous_due?: number
  previous_credit?: number
  slab_breakdown?: unknown[]
  gst_calc_method?: string
  import_bill_mode?: boolean
}

export type PurchaseSaveResult = {
  ok: boolean
  error?: string
  code?: string
  purchase_no?: string
  purchase_id?: number
  supplier_id?: number
  supplier_due?: number
  supplier_credit?: number
  calc?: Record<string, unknown>
  /** What looks mistyped; the purchase was saved regardless. */
  warnings?: string[]
}

export type PurchaseRuntimePrefs = {
  ok: boolean
  autosave_enabled: boolean
  autosave_interval_seconds: number
  column_visibility?: Record<string, boolean>
  medicine_types: string[]
  schedules?: string[]
  type_meta?: Record<
    string,
    {
      strip: boolean
      measure_unit: string
      qty_label: string
      free_label: string
      pack_label: string
      is_vial: boolean
      is_vaccine: boolean
    }
  >
  app_mode?: string
  master_ready?: boolean
  master_count?: number
  gst_calc_methods: { value: string; label: string }[]
  default_gst_calc_method: string
}

export type LoadedPurchase = {
  ok: boolean
  error?: string
  purchase_id?: number
  purchase_no?: string
  is_autosave?: boolean
  editing_purchase_id?: number | null
  autosave_purchase_id?: number | null
  /** Qty already returned against the bill, per medicine id. */
  returned_by_medicine?: Record<string, number>
  returns_note?: string
  returns_unread?: boolean
  form?: {
    supplier_name: string
    supplier_id?: number | null
    supplier_address: string
    supplier_phone: string
    gstin: string
    dl: string
    bill_number: string
    purchase_date: string
    gst_calc_method: string
    overall_discount: number
    discount_pct?: number
    rounding: number
    expenditure: number
    cash_paid: number
    online_paid: number
    previous_due: number
    previous_credit: number
    items: PurchaseLinePayload[]
  }
  edit_payment_snapshot?: {
    previous_due: number
    previous_credit: number
  }
}

export function calcPurchaseBill(body: Record<string, unknown>) {
  return postJson<PurchaseCalcResult>('/api/purchase/calc', body)
}

export function savePurchaseBill(body: Record<string, unknown>) {
  return postJson<PurchaseSaveResult>('/api/purchase/save', body)
}

export function lookupPurchaseMedicine(name: string) {
  return postJson<{
    ok: boolean
    error?: string
    details?: {
      name: string
      type: string
      manufacturer: string
      hsn_code: string
      gst_percent: number
      mrp: number
      rate: number
      schedule: string
      content_drug: string
      batch_no: string
      expiry: string
      unit: string
      discount_pct: number
    }
  }>('/api/purchase/lookup-medicine', { name })
}

export function fetchRecentPurchases(limit = 5) {
  return getJson<{
    ok: boolean
    purchases: {
      id: number
      purchase_no: string
      purchase_date: string
      supplier: string
      total: number
    }[]
  }>(`/api/purchase/recent${qs({ limit })}`)
}

export function fetchPurchaseRuntimePrefs() {
  return getJson<PurchaseRuntimePrefs>('/api/purchase/prefs')
}

export function loadPurchaseById(id: number) {
  return getJson<LoadedPurchase>(`/api/purchase/load${qs({ id })}`)
}

export function loadLastPurchase() {
  return getJson<LoadedPurchase>('/api/purchase/last')
}

export function autosavePurchaseBill(body: Record<string, unknown>) {
  return postJson<{
    ok: boolean
    skipped?: boolean
    reason?: string
    autosave_purchase_id?: number
    purchase_no?: string
    error?: string
  }>('/api/purchase/autosave', body)
}

export function discardPurchaseAutosave(autosave_purchase_id: number) {
  return postJson<{ ok: boolean; deleted?: boolean }>(
    '/api/purchase/autosave/discard',
    { autosave_purchase_id },
  )
}

export type PurchaseImportCapabilities = {
  ok: boolean
  images_supported: boolean
  image_message: string
  accept: string
  hint: string
  filetypes: { label: string; exts: string[] }[]
}

export type PurchaseImportStartResult = {
  ok: boolean
  error?: string
  code?: string
  import_token?: string
  source_type?: string
  supplier_name?: string
  bill_number?: string
  purchase_date?: string
  valid_count?: number
  invalid_count?: number
  expected_item_count?: number
  page_count?: number
  may_need_more_pages?: boolean
  first_invalid?: { source_row?: number | string; issues?: string[] }
  preview_items?: { name: string; batch: string; qty: number; rate: number }[]
  confirmations?: {
    skip_invalid?: boolean
    item_count_mismatch?: boolean
    may_need_more_pages?: boolean
  }
}

export type PurchaseImportApplyResult = {
  ok: boolean
  error?: string
  code?: string
  need_confirm?: boolean
  message?: string
  import_token?: string
  replace_existing?: boolean
  items_imported?: number
  form?: {
    supplier_name: string
    supplier_address: string
    supplier_phone: string
    gstin: string
    dl: string
    bill_number: string
    purchase_date: string
    gst_calc_method: string
    overall_discount: number
    discount_pct?: number
    expenditure: number
    cash_paid: number
    online_paid: number
    items: PurchaseLinePayload[]
    edi_manual_supplier?: boolean
  }
  import_bill_mode?: boolean
  import_invoice_summary?: Record<string, unknown>
}

function fileToBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => {
      const result = String(reader.result || '')
      const comma = result.indexOf(',')
      resolve(comma >= 0 ? result.slice(comma + 1) : result)
    }
    reader.onerror = () => reject(reader.error || new Error('Read failed'))
    reader.readAsDataURL(file)
  })
}

export async function filesToImportPayload(files: FileList | File[]) {
  const list = Array.from(files)
  const out: { name: string; content_base64: string }[] = []
  for (const f of list) {
    out.push({ name: f.name, content_base64: await fileToBase64(f) })
  }
  return out
}

export function fetchPurchaseImportCapabilities() {
  return getJson<PurchaseImportCapabilities>(
    '/api/purchase/import/capabilities',
  )
}

export function pickPurchaseImportFiles() {
  return postJson<{
    ok: boolean
    paths?: string[]
    cancelled?: boolean
    error?: string
  }>('/api/purchase/import/pick', {})
}

export function startPurchaseImport(body: {
  files?: { name: string; content_base64: string }[]
  paths?: string[]
  progress_id?: string
}) {
  return postJson<PurchaseImportStartResult>(
    '/api/purchase/import/start',
    body,
  )
}

/** What stage the scan is on. Cosmetic: an older engine has no such route, and
 *  a poll must never surface an error over a running import. */
export async function fetchPurchaseImportProgress(progress_id: string) {
  try {
    return await getJson<{ ok: boolean; status?: string }>(
      `/api/purchase/import/progress${qs({ progress_id })}`,
    )
  } catch {
    return { ok: false, status: '' }
  }
}

export function applyPurchaseImport(body: Record<string, unknown>) {
  return postJson<PurchaseImportApplyResult>(
    '/api/purchase/import/apply',
    body,
  )
}

export function cancelPurchaseImport(import_token: string) {
  return postJson<{ ok: boolean }>('/api/purchase/import/cancel', {
    import_token,
  })
}

export function searchPurchaseMedicines(q: string, limit = 50) {
  return getJson<{
    ok: boolean
    medicines: { name: string; source?: string; id?: number }[]
    app_mode?: string
    master_ready?: boolean
  }>(`/api/purchase/medicines/search${qs({ q, limit })}`)
}

export function registerPurchaseMedicine(body: Record<string, unknown>) {
  return postJson<{ ok: boolean; upserted?: boolean; error?: string }>(
    '/api/purchase/register-medicine',
    body,
  )
}

export function deleteSalesBill(sale_id: number) {
  return postJson<{ ok: boolean; error?: string; sale_id?: number }>(
    '/api/sales/history/delete',
    { sale_id },
  )
}

export function deletePurchaseBill(purchase_id: number) {
  return postJson<{ ok: boolean; error?: string; purchase_id?: number; code?: string }>(
    '/api/purchase/history/delete',
    { purchase_id },
  )
}

export type PrintAllCandidate = {
  sale_id: number
  bill_no: string
  bill_date: string
  customer: string
  total: number
  schedules: string
  /** False when the server sent no per-bill schedule, so "" means UNKNOWN. */
  schedules_known?: boolean
  /** False when the bill cannot be printed yet; `reason` says why. */
  printable?: boolean
  reason?: string
}

export function fetchPrintAllCandidates(opts: {
  from: string
  to: string
  schedule?: string
  q?: string
  customer?: string
  medicine?: string
  batch?: string
  due?: string
}) {
  return getJson<{
    ok: boolean
    bills: PrintAllCandidate[]
    count: number
    error?: string
    /** Filters the picker cannot honour, named so the shop is not misled. */
    unapplied_filters?: string[]
  }>(`/api/sales/print-all/candidates${qs({ ...opts })}`)
}

export function printAllSalesBills(body: {
  from?: string
  to?: string
  sale_ids?: number[]
  paper?: string
  slot?: number
}) {
  return postJson<{
    ok: boolean
    pages?: number
    total?: number
    failures?: string[]
    message?: string
    error?: string
    code?: string
    // The engine's refusal is an HTTP 400, so the typed body has to be let
    // through or a half-printed batch arrives as a bare "HTTP 400".
  }>('/api/sales/print-all', body, true)
}

export type StartupAlertTab = {
  title: string
  columns: string[]
  rows: unknown[][]
  action_kind?: string | null
  action_label?: string | null
}

/**
 * force   -- "Test the popup now" (ignores on/off and Skip Today).
 * recheck -- the while-running check: only rows not already shown today.
 * ok=false carries `error`: a failed store read, never an empty popup.
 */
export function fetchStartupAlerts(opts?: { force?: boolean; recheck?: boolean }) {
  const q = opts?.force ? '?force=1' : opts?.recheck ? '?recheck=1' : ''
  return getJson<{
    ok: boolean
    show: boolean
    tabs: StartupAlertTab[]
    error?: string
    recheck_minutes?: number
  }>(`/api/startup/alerts${q}`)
}

export function snoozeStartupAlerts() {
  return postJson<{ ok: boolean }>('/api/startup/alerts/snooze', {})
}

export function startupAlertAction(body: Record<string, unknown>) {
  return postJson<{
    ok: boolean
    error?: string
    message?: string
    navigate?: AlertNavigatePayload
    filename?: string
    mime?: string
    content_base64?: string
    row_count?: number
  }>('/api/startup/alerts/action', body)
}

export type BulkPurchaseGroup = {
  purchase_id: number
  bill_number?: string
  supplier_name?: string
  supplier_id?: number
  reason?: string
  items: ReturnLineItem[]
}

export type BulkPurchasePrefill = {
  ok: boolean
  empty?: boolean
  error?: string
  purchase_groups?: BulkPurchaseGroup[]
  writeoff_lines?: {
    medicine_id: number
    medicine_name?: string
    batch_no?: string
    quantity?: number
    reason_tag?: string
  }[]
}

export function fetchBulkPurchasePrefill(includeExpired = true, includeNearExpiry = true) {
  const q = new URLSearchParams({
    include_expired: includeExpired ? '1' : '0',
    include_near_expiry: includeNearExpiry ? '1' : '0',
  })
  return getJson<BulkPurchasePrefill>(`/api/returns/bulk/prefill?${q}`)
}

export function saveBulkPurchaseReturn(body: Record<string, unknown>) {
  return postJson<{
    ok: boolean
    error?: string
    saved?: {
      return_no?: string
      return_id?: number
      supplier_name?: string
      bill_number?: string
      refund_amount?: number
    }[]
    errors?: string[]
  }>('/api/returns/bulk/save', body)
}

export type ExportFileResult = {
  ok: boolean
  filename?: string
  mime?: string
  format?: string
  content_base64?: string
  row_count?: number
  error?: string
  columns?: string[]
  rows?: unknown[][]
  title?: string
}

export function runExportFile(body: Record<string, unknown>) {
  return postJson<ExportFileResult>('/api/export/run', body)
}
