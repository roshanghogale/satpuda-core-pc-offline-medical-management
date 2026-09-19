import { useState } from 'react'
import { systemAction } from '../settingsApi'

type Props = {
  onResolved: () => void
}

async function runMigrate(action: 'online_migrate_push' | 'online_migrate_wipe') {
  const res = await systemAction({ action })
  if (!res.background) {
    if (res.ok === false) {
      throw new Error(String(res.error || res.message || 'Migrate failed'))
    }
    return res
  }
  const started = Date.now()
  while (Date.now() - started < 30 * 60 * 1000) {
    await new Promise((r) => setTimeout(r, 500))
    const st = await systemAction({ action: 'heavy_job_status' })
    if (st.done) {
      if (st.ok === false) {
        throw new Error(String(st.error || st.message || 'Migrate failed'))
      }
      return st
    }
  }
  throw new Error('Migrate is still running. Check Settings → Data & System.')
}

export function OnlineMigrateDialog({ onResolved }: Props) {
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [progress, setProgress] = useState('')

  const run = async (action: 'online_migrate_push' | 'online_migrate_wipe') => {
    setError('')
    setBusy(true)
    setProgress(
      action === 'online_migrate_push'
        ? 'Pushing local data to the server…'
        : 'Removing local database…',
    )
    try {
      await runMigrate(action)
      onResolved()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
      setProgress('')
    }
  }

  return (
    <div className="modal-backdrop activation-backdrop" role="presentation">
      <div
        className="modal-card modal-card-wide"
        role="dialog"
        aria-modal="true"
        aria-label="Local data found — Online is server-only"
      >
        <div className="modal-head">
          <h2>Local data found — Online is server-only</h2>
        </div>
        <div className="modal-body">
          <p className="note">
            This PC still has a local store database. Online mode uses the
            server only. Choose:
          </p>
          <ul className="note">
            <li>
              <strong>Push to Server</strong> — upload local data, then delete
              the local DB
            </li>
            <li>
              <strong>Delete Local</strong> — discard local data and use server
              data only
            </li>
          </ul>
          {progress ? <p className="note">{progress}</p> : null}
          {error ? <p className="error">{error}</p> : null}
        </div>
        <div className="modal-foot">
          <button
            type="button"
            className="btn btn-primary"
            disabled={busy}
            onClick={() => void run('online_migrate_push')}
          >
            {busy ? 'Working…' : 'Push to Server'}
          </button>
          <button
            type="button"
            className="btn btn-neutral"
            disabled={busy}
            onClick={() => {
              // This deletes the only other copy of the shop's history. It sat
              // one unconfirmed click away from a "pushed to server" message
              // that was printed whether or not the push had verified, so a
              // shop could be told it was safe and then destroy the evidence.
              const ok = window.confirm(
                'Delete the local database?\n\n' +
                  'This removes the only copy on this PC. Do it only after ' +
                  '"Push to Server" has finished and reported success.\n\n' +
                  'If you are not sure the server has everything, press Cancel ' +
                  'and push again.',
              )
              if (ok) void run('online_migrate_wipe')
            }}
          >
            Delete Local
          </button>
        </div>
      </div>
    </div>
  )
}
