/** Retail or wholesale: the store's business mode (GET /api/wholesale/state).
 *
 *  `allowed` is the server's switch for this store; `mode` the store's own choice. A store
 *  the server no longer allows reads as retail. Any failure reads as retail, so a screen
 *  never turns wholesale by accident. */
import { useEffect, useState } from 'react'
import { getApiBase } from '../api'

export type BusinessMode = 'retail' | 'wholesale'

export type BusinessModeState = {
  allowed: boolean
  mode: BusinessMode
  isWholesale: boolean
  /** false only until the first answer arrives (a screen can wait instead of flashing
   *  the retail form in a wholesale store). */
  ready?: boolean
}

const RETAIL: BusinessModeState = { allowed: false, mode: 'retail', isWholesale: false, ready: true }
let lastKnown: BusinessModeState | null = null

/** Dispatched on window after the mode is switched, so every screen reads it again. */
export const BUSINESS_MODE_EVENT = 'satpuda:business-mode-changed'

export async function fetchBusinessMode(): Promise<BusinessModeState> {
  try {
    const res = await fetch(`${getApiBase()}/api/wholesale/state`)
    if (!res.ok) return RETAIL
    const data = (await res.json().catch(() => ({}))) as { allowed?: boolean; mode?: string }
    const allowed = Boolean(data.allowed)
    const mode: BusinessMode = allowed && data.mode === 'wholesale' ? 'wholesale' : 'retail'
    lastKnown = { allowed, mode, isWholesale: mode === 'wholesale', ready: true }
    return lastKnown
  } catch {
    return RETAIL
  }
}

export function useBusinessMode(): BusinessModeState {
  const [state, setState] = useState<BusinessModeState>(
    () => lastKnown ?? { ...RETAIL, ready: false },
  )
  useEffect(() => {
    let alive = true
    const load = () => {
      void fetchBusinessMode().then((s) => {
        if (alive) setState(s)
      })
    }
    load()
    window.addEventListener(BUSINESS_MODE_EVENT, load)
    return () => {
      alive = false
      window.removeEventListener(BUSINESS_MODE_EVENT, load)
    }
  }, [])
  return state
}
