/** The wholesale sale screen's engine calls (core/wholesale_sale_api.py). */
import { getApiBase } from '../api'

export type WholesaleParty = {
  id: number
  name: string
  phone: string
  address: string
  party_type: string
  gstin: string
  legal_name: string
  dl_numbers: string
  state_code: string
  state_name: string
  rate_tier: 'A' | 'B' | 'C'
  credit_limit: number
  credit_days: number
  total_due: number
  total_credit: number
}

export type PartyRow = { id: number; name: string; phone: string; party_type: string }

export type WholesaleLine = {
  id: number
  name: string
  batch: string
  expiry: string
  hsn_code: string
  type: string
  unit: string
  units_per_pack: number
  stock_packs: number
  qty: number
  free: number
  rate: number
  /** The tier the rate came from ('' = the medicine's base rate). */
  rate_tier: string
  tier_rates?: Record<string, number>
  base_rate?: number
  disc_pct: number
  scheme_pct: number
  gst_percent: number
  mrp?: number
  warning?: string
}

export type CalcLine = WholesaleLine & {
  gross: number
  disc_amt: number
  scheme_amt: number
  taxable: number
  gst_amt: number
  cgst: number
  sgst: number
  igst: number
  amount: number
}

export type WholesaleCalc = {
  ok: boolean
  lines: CalcLine[]
  intra_state: boolean
  shop_state: string
  place_of_supply: string
  place_of_supply_name: string
  taxable_total: number
  cgst_total: number
  sgst_total: number
  igst_total: number
  gst_total: number
  round_off: number
  grand_total: number
  amount_in_words: string
  due_date: string
  warnings: string[]
  error?: string
}

export type PartyInfo = {
  ok: boolean
  party: WholesaleParty
  shop_state: string
  place_of_supply: string
  place_of_supply_name: string
  intra_state: boolean
  error?: string
}

export type LoadedWholesale = {
  ok: boolean
  error?: string
  code?: string
  sale_id: number
  bill_no: string
  bill_date: string
  party: WholesaleParty
  customer_id: number
  transport: string
  lr_no: string
  eway_bill_no: string
  due_date: string
  cash_paid: number
  online_paid: number
  total_amount: number
  items: WholesaleLine[]
}

export type WholesaleSaveResult = {
  ok: boolean
  error?: string
  code?: string
  bill_no?: string
  sale_id?: number
  grand_total?: number
  due_date?: string
  customer_due?: number
  next_bill_hint?: string
  warnings?: string[]
}

function qs(params: Record<string, string | number | undefined>) {
  const out = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== '') out.set(k, String(v))
  }
  const s = out.toString()
  return s ? `?${s}` : ''
}

async function call<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(
    `${getApiBase()}${path}`,
    body === undefined
      ? undefined
      : {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        },
  )
  const data = (await res.json().catch(() => ({}))) as T & { error?: string; ok?: boolean }
  // Refusals come back as data ({ok:false, error, code}) for the screen to show.
  if (!res.ok && data.ok !== false) {
    throw new Error(data.error || `HTTP ${res.status}`)
  }
  return data
}

export function searchParties(q: string) {
  return call<{ ok: boolean; parties: PartyRow[] }>(`/api/wholesale/sale/parties${qs({ q })}`)
}

export function fetchParty(customerId: number) {
  return call<PartyInfo>(`/api/wholesale/sale/party${qs({ customer_id: customerId })}`)
}

export function buildWholesaleLine(body: { medicine_id: number; rate_tier: string; qty?: number }) {
  return call<{ ok: boolean; line?: WholesaleLine; error?: string }>(
    '/api/wholesale/sale/line',
    body,
  )
}

export function calcWholesale(body: Record<string, unknown>) {
  return call<WholesaleCalc>('/api/wholesale/sale/calc', body)
}

export function saveWholesale(body: Record<string, unknown>) {
  return call<WholesaleSaveResult>('/api/wholesale/sale/save', body)
}

export function loadWholesale(saleId: number) {
  return call<LoadedWholesale>(`/api/wholesale/sale/load${qs({ sale_id: saleId })}`)
}
