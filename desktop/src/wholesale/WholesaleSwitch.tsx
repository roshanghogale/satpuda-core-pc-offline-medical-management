/** Retail | Wholesale switch beside the Online status, and the WHOLESALE badge in a wholesale
 *  store. Wholesale is a SEPARATE store (its own medicines, rates, purchases, sales and stock):
 *  the switch OPENS the other store (POST /api/wholesale/mode, core/wholesale_store_switch.py).
 *  The store switch box (components/StoreSwitchDialog.tsx -- the same box as Settings > Stores
 *  > Switch) asks first, shows the progress while an offline-first PC makes that store's own
 *  copy, then restarts the app.
 */
import { beginStoreSwitch, useStoreSwitchOpen } from '../components/StoreSwitchDialog'
import { switchStore, useBusinessMode, type BusinessMode } from './mode'
import './wholesale.css'

export function WholesaleSwitch() {
  const { allowed, mode, checked, companionName, storeName } = useBusinessMode()
  const busy = useStoreSwitchOpen()

  if (!checked || (!allowed && mode !== 'wholesale')) return null

  function pick(next: BusinessMode) {
    if (next === mode || busy) return
    const other = companionName || (next === 'wholesale' ? 'the wholesale store' : 'the retail store')
    beginStoreSwitch({
      target: other,
      kind: next,
      current: storeName,
      confirm:
        'Wholesale is a separate store with its own medicines, rates, purchases, sales and stock. ' +
        'The app restarts in the other store.',
      start: async () => {
        const res = await switchStore(next)
        return {
          restart: res.reload || Boolean(res.running),
          running: res.running,
          progress: res.progress,
          storeName: res.store_name || undefined,
          kind: res.mode,
        }
      },
    })
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
          onClick={() => pick('retail')}
        >
          Retail
        </button>
        <button
          type="button"
          className={mode === 'wholesale' ? 'on' : undefined}
          aria-pressed={mode === 'wholesale'}
          disabled={busy}
          onClick={() => pick('wholesale')}
        >
          Wholesale
        </button>
      </span>
      {mode === 'wholesale' ? (
        <span className="ws-badge" title={storeName ? `${storeName} is the wholesale store` : 'This is the wholesale store'}>
          WHOLESALE
        </span>
      ) : null}
    </>
  )
}
