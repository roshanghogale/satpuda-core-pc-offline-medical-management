/** The store's business mode (D:\projects\WHOLESALE_DESIGN.md): GET /api/wholesale/state ->
 *  {allowed, mode}. Anything missing or failing reads as retail, so retail screens never change. */
import { useEffect, useState } from 'react'
import { getApiBase } from '../api'

export type BusinessMode = 'retail' | 'wholesale'

export type BusinessModeState = { allowed: boolean; mode: BusinessMode; isWholesale: boolean }

const RETAIL: BusinessModeState = { allowed: false, mode: 'retail', isWholesale: false }

export async function fetchBusinessMode(): Promise<BusinessModeState> {
  try {
    const res = await fetch(`${getApiBase()}/api/wholesale/state`)
    if (!res.ok) return RETAIL
    const d = (await res.json()) as { allowed?: boolean; mode?: string }
    const allowed = d.allowed === true
    const mode: BusinessMode = allowed && d.mode === 'wholesale' ? 'wholesale' : 'retail'
    return { allowed, mode, isWholesale: mode === 'wholesale' }
  } catch {
    return RETAIL
  }
}

export function useBusinessMode(): BusinessModeState {
  const [state, setState] = useState<BusinessModeState>(RETAIL)
  useEffect(() => {
    let alive = true
    void fetchBusinessMode().then((s) => {
      if (alive) setState(s)
    })
    return () => {
      alive = false
    }
  }, [])
  return state
}
