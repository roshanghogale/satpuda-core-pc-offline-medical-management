/** What each page's summary shows -- the shop's choice, the same on every PC and the phone.
 *
 * The owner turns a page's whole summary off, or single figures in it (25 Oct 2026):
 * the small "Customize summary" button beside each summary, or Settings → Layout & Lists →
 * Page Summaries. Kept by the engine as one shop-wide settings row (core/page_summary_prefs.py),
 * which offline-first and Online send to every PC and the phone of the shop.
 *
 * Hiding only hides on screen: the numbers, prints and exports never change. A page or figure
 * the row does not name is shown, so a shop that never touched it sees today's screens.
 */

import { useEffect, useState } from 'react'
import { getApiBase } from './api'

export type SummaryField = { key: string; label: string }
export type SummaryPageDef = { page: string; label: string; fields: SummaryField[] }

/** Every page with a summary, and its figures, in the order they show. The keys are the
 *  phone's too (screens there use the same names), so do not rename them. */
export const SUMMARY_PAGES: SummaryPageDef[] = [
  {
    page: 'home',
    label: 'Home Dashboard',
    fields: [
      { key: 'today_sales', label: 'Today Sales' },
      { key: 'today_collected', label: 'Today Collected' },
      { key: 'today_bills', label: 'Today Bills' },
      { key: 'customer_due', label: 'Customer Due' },
      { key: 'supplier_due', label: 'Supplier Due' },
      { key: 'stock_value', label: 'Stock Value' },
      { key: 'month_sales', label: 'Month Sales' },
      { key: 'month_collected', label: 'Month Collected' },
      { key: 'month_bills', label: 'Month Bills' },
      { key: 'year_sales', label: 'Year Sales' },
      { key: 'year_collected', label: 'Year Collected' },
      { key: 'year_bills', label: 'Year Bills' },
    ],
  },
  {
    page: 'sales_history',
    label: 'Sales History',
    fields: [
      { key: 'bills', label: 'Bills' },
      { key: 'total_sales', label: 'Total Sales' },
      { key: 'discount', label: 'Discount' },
      { key: 'amount_paid', label: 'Amount Paid' },
      { key: 'bill_due', label: 'Bill Due' },
      { key: 'customer_due', label: 'Customer Due' },
      { key: 'profit', label: 'Profit' },
      { key: 'returns', label: 'Returns' },
      { key: 'today_revenue', label: 'Today Revenue' },
      { key: 'today_cash', label: 'Today Cash' },
      { key: 'today_online', label: 'Today Online' },
      { key: 'month_revenue', label: 'Month Revenue' },
    ],
  },
  {
    page: 'purchase_history',
    label: 'Purchase History',
    fields: [
      { key: 'bills', label: 'Bills' },
      { key: 'final_amount', label: 'Final Amount' },
      { key: 'paid_at_entry', label: 'Paid at Entry' },
      { key: 'entry_due', label: 'Entry Due' },
      { key: 'supplier_due', label: 'Supplier Due' },
      { key: 'supplier_credit', label: 'Supplier Credit' },
    ],
  },
  {
    page: 'inventory',
    label: 'Inventory',
    fields: [
      { key: 'total_medicines', label: 'Total Medicines' },
      { key: 'low_stock', label: 'Low Stock' },
      { key: 'out_of_stock', label: 'Out of Stock' },
      { key: 'near_expiry', label: 'Near Expiry' },
      { key: 'expired', label: 'Expired' },
      { key: 'total_value', label: 'Total Value (MRP)' },
    ],
  },
  {
    page: 'returns',
    label: 'Returns',
    fields: [
      { key: 'sales_returns', label: 'Sales returns' },
      { key: 'purchase_returns', label: 'Purchase returns' },
      { key: 'write_offs', label: 'Write-offs' },
    ],
  },
  {
    page: 'alerts',
    label: 'Alert & Monitoring',
    fields: [
      { key: 'count', label: 'Rows / Bills / Batches' },
      { key: 'groups', label: 'Suppliers / Areas' },
      { key: 'value_rate', label: 'At purchase rate' },
      { key: 'value_mrp', label: 'At MRP' },
      { key: 'due', label: 'Due' },
    ],
  },
  {
    page: 'ledger',
    label: 'Ledger',
    fields: [
      { key: 'opening', label: 'Opening' },
      { key: 'debits', label: 'Debits' },
      { key: 'credits', label: 'Credits' },
      { key: 'closing', label: 'Closing' },
      { key: 'entries', label: 'Entries' },
    ],
  },
  {
    page: 'doctor_margin',
    label: 'Doctor Margin Report',
    fields: [
      { key: 'sale', label: 'Sale' },
      { key: 'purchase_cost', label: 'Purchase cost' },
      { key: 'profit', label: 'Profit' },
      { key: 'commission', label: 'Commission / Doctor margin' },
      { key: 'store_remaining', label: 'Store remaining' },
    ],
  },
  {
    page: 'doctor_margin_rules',
    label: 'Doctor Margin Report (by rules)',
    fields: [
      { key: 'doctors', label: 'Doctors' },
      { key: 'on_profit', label: 'On profit' },
      { key: 'total_sales', label: 'Total sales' },
      { key: 'commission', label: 'Commission on sales' },
      { key: 'doctors_margin', label: "Doctors' margin" },
    ],
  },
]

/** The Purchase screen's input boxes the shop may hide (owner, 30 Oct 2026), kept in the same
 *  shop-wide row as page "purchase_form" (only "off" is used). Supplier name, medicine name and
 *  quantity always show. A hidden box counts as empty on a NEW line; the ones the master list
 *  knows are filled from it when the line is added (PurchasePage.formValuesForAdd), and nothing
 *  is asked for. A line opened again keeps its own values. */
export const PURCHASE_FORM_PAGE = 'purchase_form'
export const PURCHASE_FORM_FIELDS: (SummaryField & { group: 'supplier' | 'medicine'; fromMaster?: boolean })[] = [
  { key: 'address', label: 'Address', group: 'supplier' },
  { key: 'phone', label: 'Phone', group: 'supplier' },
  { key: 'gstin', label: 'GSTIN', group: 'supplier' },
  { key: 'dl', label: 'DL Numbers', group: 'supplier' },
  { key: 'purchase_date', label: 'Purchase Date', group: 'supplier' },
  { key: 'bill_number', label: 'Bill Number', group: 'supplier' },
  { key: 'gst_method', label: 'GST Method', group: 'supplier' },
  { key: 'type', label: 'Type', group: 'medicine', fromMaster: true },
  { key: 'pack', label: 'Tabs/Strip (Pack)', group: 'medicine', fromMaster: true },
  { key: 'free', label: 'Free', group: 'medicine' },
  { key: 'hsn', label: 'HSN Code', group: 'medicine', fromMaster: true },
  { key: 'gst_pct', label: 'GST %', group: 'medicine', fromMaster: true },
  { key: 'mrp', label: 'MRP', group: 'medicine', fromMaster: true },
  { key: 'rate', label: 'Rate', group: 'medicine' },
  { key: 'discount', label: 'Discount %', group: 'medicine' },
  { key: 'manufacturer', label: 'Manufacturer', group: 'medicine', fromMaster: true },
  { key: 'batch', label: 'Batch No', group: 'medicine' },
  { key: 'expiry', label: 'Expiry', group: 'medicine' },
  { key: 'schedule', label: 'Schedule', group: 'medicine', fromMaster: true },
  { key: 'content', label: 'Content / Drug', group: 'medicine', fromMaster: true },
]

export type SummaryPageKey = (typeof SUMMARY_PAGES)[number]['page']

export function summaryPageDef(page: string): SummaryPageDef | undefined {
  return SUMMARY_PAGES.find((p) => p.page === page)
}

type PageEntry = { hidden?: boolean; off?: string[] }
export type PageSummaryPrefs = { version?: number; pages: Record<string, PageEntry> }

/** A change: only the pages / figures named change (the phone's own ones stay). */
export type PageSummaryChange = Record<string, { hidden?: boolean; on?: Record<string, boolean> }>

/** Fired after a save so every open page follows at once. */
export const PAGE_SUMMARIES_EVENT = 'satpuda:page-summaries-changed'

let cache: PageSummaryPrefs | null = null
let inflight: Promise<PageSummaryPrefs> | null = null

function clean(data: unknown): PageSummaryPrefs {
  const pages = (data as { pages?: unknown } | null)?.pages
  if (!pages || typeof pages !== 'object' || Array.isArray(pages)) return { pages: {} }
  return { pages: pages as Record<string, PageEntry> }
}

export async function loadPageSummaries(force = false): Promise<PageSummaryPrefs> {
  if (!force && cache) return cache
  if (inflight) return inflight
  inflight = (async () => {
    try {
      const res = await fetch(`${getApiBase()}/api/settings/page_summaries`)
      if (res.ok) {
        cache = clean(await res.json().catch(() => ({})))
        return cache
      }
    } catch {
      /* below */
    }
    // An unreadable setting must not hide a summary the shop is used to.
    return cache || { pages: {} }
  })()
  try {
    return await inflight
  } finally {
    inflight = null
  }
}

export async function savePageSummaries(change: PageSummaryChange): Promise<PageSummaryPrefs> {
  const res = await fetch(`${getApiBase()}/api/settings/page_summaries`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ pages: change }),
  })
  const data = await res.json().catch(() => ({}))
  if (!res.ok) {
    throw new Error((data as { error?: string }).error || 'Could not save. Try again.')
  }
  cache = clean(data)
  window.dispatchEvent(new Event(PAGE_SUMMARIES_EVENT))
  return cache
}

export function isSummaryShown(prefs: PageSummaryPrefs | null, page: string): boolean {
  return prefs?.pages?.[page]?.hidden !== true
}

export function isFieldShown(prefs: PageSummaryPrefs | null, page: string, field: string): boolean {
  const off = prefs?.pages?.[page]?.off
  return !(Array.isArray(off) && off.includes(field))
}

/** The shop's choice, kept current (a save here or in Settings, the app coming back to front). */
export function usePageSummaries(): PageSummaryPrefs | null {
  const [prefs, setPrefs] = useState<PageSummaryPrefs | null>(cache)
  useEffect(() => {
    let live = true
    const apply = (p: PageSummaryPrefs) => {
      if (live) setPrefs(p)
    }
    void loadPageSummaries().then(apply)
    const onSaved = () => {
      if (cache) apply(cache)
    }
    // Another PC or the phone may have changed it: read again when the window comes back.
    const onFocus = () => void loadPageSummaries(true).then(apply)
    window.addEventListener(PAGE_SUMMARIES_EVENT, onSaved)
    window.addEventListener('focus', onFocus)
    return () => {
      live = false
      window.removeEventListener(PAGE_SUMMARIES_EVENT, onSaved)
      window.removeEventListener('focus', onFocus)
    }
  }, [])
  return prefs
}

/** One page: is its summary on, and which figures. */
export function usePageSummary(page: string) {
  const prefs = usePageSummaries()
  return {
    show: isSummaryShown(prefs, page),
    field: (key: string) => isFieldShown(prefs, page, key),
  }
}
