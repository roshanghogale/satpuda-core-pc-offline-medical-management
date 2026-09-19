/** Settings → Appearance → Dashboard Sections.
 *
 * Four checkboxes — Home stats, and the summary bar on Inventory, Sales History
 * and Purchase History — that a shop unticks so the day's takings and dues are
 * not on screen while customers are at the counter. They round-tripped
 * perfectly: the panel wrote them, the engine stored them, the panel read them
 * back. Nothing ever rendered with them. The old Tk screens honour all four,
 * so this is a setting the shop has used before and expects to work.
 */

import { useEffect, useState } from 'react'
import { getApiBase } from './api'
import { fetchSettingsBundle } from './settingsApi'
import { LAYOUT_ROW_EVENT } from './layoutRows'

/** Dispatched after Save Appearance (and after the banner is browsed/reset). */
export const APPEARANCE_EVENT = 'satpuda:home-banner-changed'

export type DashboardSectionKey =
  | 'home_dashboard'
  | 'inventory_summary'
  | 'sales_summary'
  | 'purchase_summary'

type Flags = Partial<Record<DashboardSectionKey, boolean>>

let cache: Flags | null = null
let inflight: Promise<Flags> | null = null

function parse(data: Record<string, unknown> | null | undefined): Flags {
  const raw = (data?.dashboard_sections ?? null) as Record<string, unknown> | null
  if (!raw || typeof raw !== 'object') return {}
  const out: Flags = {}
  for (const [k, v] of Object.entries(raw)) out[k as DashboardSectionKey] = Boolean(v)
  return out
}

export function rememberDashboardSections(data: Record<string, unknown>) {
  cache = parse(data)
}

export async function loadDashboardSections(force = false): Promise<Flags> {
  if (!force && cache) return cache
  if (!force && inflight) return inflight
  inflight = (async () => {
    try {
      const res = await fetch(`${getApiBase()}/api/settings/layout_lists`)
      if (res.ok) {
        cache = parse((await res.json().catch(() => ({}))) as Record<string, unknown>)
        return cache
      }
    } catch {
      /* fall through to the bundle */
    }
    try {
      const bundle = await fetchSettingsBundle()
      cache = parse(bundle.layout_lists as Record<string, unknown>)
      return cache
    } catch {
      // An unreadable setting must not hide a panel the shop is used to.
      cache = {}
      return cache
    }
  })()
  try {
    return await inflight
  } finally {
    inflight = null
  }
}

/** True unless the shop has explicitly turned this section off. */
export function useDashboardSection(key: DashboardSectionKey): boolean {
  const [on, setOn] = useState(() => cache?.[key] !== false)

  useEffect(() => {
    let live = true
    const apply = (flags: Flags) => {
      // `!== false`, never truthiness: an absent key means "show it".
      if (live) setOn(flags[key] !== false)
    }
    void loadDashboardSections().then(apply)
    const onChange = () => {
      cache = null
      void loadDashboardSections(true).then(apply)
    }
    window.addEventListener(LAYOUT_ROW_EVENT, onChange)
    window.addEventListener(APPEARANCE_EVENT, onChange)
    return () => {
      live = false
      window.removeEventListener(LAYOUT_ROW_EVENT, onChange)
      window.removeEventListener(APPEARANCE_EVENT, onChange)
    }
  }, [key])

  return on
}
