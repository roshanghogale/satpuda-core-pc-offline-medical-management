/** Settings → Layout & Lists → Table Row Counts (classic Treeview height). */

import { useEffect, useState } from 'react'
import { getApiBase } from './api'
import { fetchSettingsBundle } from './settingsApi'

export const LAYOUT_ROW_EVENT = 'satpuda:layout-config-changed'

export const LAYOUT_ROW_DEFAULTS = {
  billing_rows: 8,
  inventory_rows: 15,
  sales_history_rows: 15,
  purchase_history_rows: 15,
  purchase_rows: 4,
  customers_rows: 15,
  doctors_rows: 6,
  suppliers_rows: 8,
} as const

export const LAYOUT_ROW_LIMITS: Record<
  keyof typeof LAYOUT_ROW_DEFAULTS,
  [number, number]
> = {
  billing_rows: [4, 30],
  inventory_rows: [5, 50],
  sales_history_rows: [5, 50],
  purchase_history_rows: [5, 50],
  purchase_rows: [2, 20],
  customers_rows: [5, 50],
  doctors_rows: [2, 20],
  suppliers_rows: [2, 20],
}

export type LayoutRowKey = keyof typeof LAYOUT_ROW_DEFAULTS

export type LayoutRowCounts = Record<LayoutRowKey, number>

function clampRow(key: LayoutRowKey, raw: unknown): number {
  const fallback = LAYOUT_ROW_DEFAULTS[key]
  const n = Number(raw)
  const v = Number.isFinite(n) ? Math.round(n) : fallback
  const [mn, mx] = LAYOUT_ROW_LIMITS[key]
  return Math.max(mn, Math.min(mx, v))
}

export function parseLayoutRowCounts(data: Record<string, unknown> | null | undefined): LayoutRowCounts {
  const out: LayoutRowCounts = { ...LAYOUT_ROW_DEFAULTS }
  if (!data) return out
  for (const key of Object.keys(LAYOUT_ROW_DEFAULTS) as LayoutRowKey[]) {
    if (key in data) out[key] = clampRow(key, data[key])
  }
  return out
}

let cache: LayoutRowCounts | null = null
let inflight: Promise<LayoutRowCounts> | null = null

export function peekLayoutRowCounts(): LayoutRowCounts {
  return cache || { ...LAYOUT_ROW_DEFAULTS }
}

export async function loadLayoutRowCounts(force = false): Promise<LayoutRowCounts> {
  if (!force && cache) return cache
  if (!force && inflight) return inflight
  inflight = (async () => {
    try {
      const res = await fetch(`${getApiBase()}/api/settings/layout_lists`)
      if (res.ok) {
        const data = (await res.json().catch(() => ({}))) as Record<string, unknown>
        cache = parseLayoutRowCounts(data)
        return cache
      }
    } catch {
      /* fall through to bundle */
    }
    try {
      const bundle = await fetchSettingsBundle()
      cache = parseLayoutRowCounts(bundle.layout_lists)
      return cache
    } catch {
      cache = { ...LAYOUT_ROW_DEFAULTS }
      return cache
    }
  })()
  try {
    return await inflight
  } finally {
    inflight = null
  }
}

export function rememberLayoutRowCounts(data: Record<string, unknown>) {
  cache = parseLayoutRowCounts(data)
}

export function useLayoutRowCount(key: LayoutRowKey): number {
  const [n, setN] = useState(() => peekLayoutRowCounts()[key])

  useEffect(() => {
    let live = true
    const apply = (rows: LayoutRowCounts) => {
      if (live) setN(rows[key])
    }
    void loadLayoutRowCounts().then(apply)
    const onChange = (ev: Event) => {
      const detail = (ev as CustomEvent<Record<string, unknown>>).detail
      if (detail && typeof detail === 'object') {
        rememberLayoutRowCounts(detail)
        apply(peekLayoutRowCounts())
        return
      }
      cache = null
      void loadLayoutRowCounts(true).then(apply)
    }
    window.addEventListener(LAYOUT_ROW_EVENT, onChange)
    return () => {
      live = false
      window.removeEventListener(LAYOUT_ROW_EVENT, onChange)
    }
  }, [key])

  return n
}
