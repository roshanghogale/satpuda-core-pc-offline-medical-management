/** Retail | Wholesale switch beside the Online status, and the WHOLESALE badge in a wholesale
 *  store. Wholesale is a SEPARATE store (its own medicines, rates, purchases, sales and stock):
 *  the switch OPENS the other store (POST /api/wholesale/mode, core/wholesale_store_switch.py).
 *  It asks first, shows the progress while an offline-first PC makes that store's own copy,
 *  then restarts the app as Settings > Stores > Switch does.
 */
import { useEffect, useRef, useState } from 'react'
import { restartDesktopApp } from '../restartApp'
import { fetchSwitchProgress, switchStore, useBusinessMode, type BusinessMode } from './mode'
import './wholesale.css'
import './wholesale-switch.css'

const POLL_MS = 800

export function WholesaleSwitch() {
  const { allowed, mode, checked, companionName } = useBusinessMode()
  const [busy, setBusy] = useState(false)
  const [steps, setSteps] = useState<string[]>([])
  const [opening, setOpening] = useState('')
  const alive = useRef(true)

  useEffect(() => {
    alive.current = true
    return () => {
      alive.current = false
    }
  }, [])

  if (!checked || (!allowed && mode !== 'wholesale')) return null

  async function waitForCopy(path?: string): Promise<void> {
    for (;;) {
      await new Promise((r) => window.setTimeout(r, POLL_MS))
      if (!alive.current) return
      let p
      try {
        p = await fetchSwitchProgress(path)
      } catch {
        continue // the engine is busy reopening the store; ask again
      }
      setSteps(p.steps)
      if (!p.running) {
        if (p.error) throw new Error(p.error)
        return
      }
    }
  }

  async function pick(next: BusinessMode) {
    if (next === mode || busy) return
    const other = companionName || (next === 'wholesale' ? 'the wholesale store' : 'the retail store')
    const ask = `Open ${other}? Wholesale is a separate store with its own medicines, rates, purchases, sales and stock.`
    if (!window.confirm(ask)) return
    setBusy(true)
    setOpening(other)
    setSteps([])
    try {
      const res = await switchStore(next)
      if (res.store_name) setOpening(res.store_name)
      if (res.running) await waitForCopy(res.progress)
      if (res.reload || res.running) {
        setSteps((s) => [...s, 'Restarting…'])
        await restartDesktopApp()
        return
      }
      setBusy(false)
      setOpening('')
    } catch (e) {
      window.alert(`Could not open ${other}: ${e instanceof Error ? e.message : String(e)}`)
      setBusy(false)
      setOpening('')
      setSteps([])
    }
  }

  return (
    <>
      <span
        className="ws-switch"
        role="group"
        aria-label="Retail or wholesale store"
        title={companionName ? `Opens ${companionName}, a separate store` : 'Retail or wholesale store'}
      >
        <button
          type="button"
          className={mode === 'retail' ? 'on' : undefined}
          aria-pressed={mode === 'retail'}
          disabled={busy || (mode === 'wholesale' && !allowed)}
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
        <span className="ws-badge" title="This is the wholesale store">
          WHOLESALE
        </span>
      ) : null}
      {busy ? (
        <div className="ws-switch-progress" role="status" aria-live="polite">
          <div className="ws-switch-progress-box">
            <strong>Opening {opening}…</strong>
            {steps.length ? (
              <ol>
                {steps.slice(-6).map((s, i) => (
                  <li key={`${i}-${s}`}>{s}</li>
                ))}
              </ol>
            ) : (
              <p>Please wait. Do not close the app.</p>
            )}
          </div>
        </div>
      ) : null}
    </>
  )
}
