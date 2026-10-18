/** Retail | Wholesale switch beside the Online status (only when the server allows wholesale),
 *  and the WHOLESALE badge while the store is on it. Switching asks first, saves the store
 *  setting (POST /api/wholesale/mode) and reloads the app, so every page starts in the new mode.
 */
import { useState } from 'react'
import { setBusinessMode, useBusinessMode, type BusinessMode } from './mode'
import './wholesale.css'

export function WholesaleSwitch() {
  const { allowed, mode, checked } = useBusinessMode()
  const [busy, setBusy] = useState(false)

  if (!checked || !allowed) return null

  async function pick(next: BusinessMode) {
    if (next === mode || busy) return
    const ask =
      next === 'wholesale'
        ? 'Switch the whole store to Wholesale?\n\nEvery PC and phone of this store will bill as wholesale (tax invoice, Rate A / B / C, no MRP selling). The app reloads.'
        : 'Switch the whole store back to Retail?\n\nEvery PC and phone of this store will bill as retail again. Wholesale rates and party details are kept. The app reloads.'
    if (!window.confirm(ask)) return
    setBusy(true)
    try {
      await setBusinessMode(next)
      window.location.reload()
    } catch (e) {
      window.alert(`Could not switch: ${e instanceof Error ? e.message : String(e)}`)
      setBusy(false)
    }
  }

  return (
    <>
      <span
        className="ws-switch"
        role="group"
        aria-label="Store mode"
        title="Retail or wholesale billing for the whole store"
      >
        <button
          type="button"
          className={mode === 'retail' ? 'on' : undefined}
          aria-pressed={mode === 'retail'}
          disabled={busy}
          onClick={() => void pick('retail')}
        >
          Retail
        </button>
        <button
          type="button"
          className={mode === 'wholesale' ? 'on' : undefined}
          aria-pressed={mode === 'wholesale'}
          disabled={busy}
          onClick={() => void pick('wholesale')}
        >
          Wholesale
        </button>
      </span>
      {mode === 'wholesale' ? (
        <span className="ws-badge" title="This store bills as wholesale">
          WHOLESALE
        </span>
      ) : null}
    </>
  )
}
