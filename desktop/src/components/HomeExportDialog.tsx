import { useEffect, useState } from 'react'
import { fetchSettingsBundle, systemAction } from '../settingsApi'

type Props = {
  open: boolean
  onClose: () => void
}

export function HomeExportDialog({ open, onClose }: Props) {
  const [fmt, setFmt] = useState('csv')
  const [msg, setMsg] = useState('')
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)

  // Start from the shop's saved default rather than a hardcoded csv. Before,
  // this box opened on CSV and exporting then wrote CSV back over whatever the
  // shop had chosen in Settings.
  useEffect(() => {
    if (!open) return
    let cancelled = false
    void (async () => {
      try {
        const bundle = await fetchSettingsBundle()
        const saved = String(bundle?.system?.export_format || '')
        if (!cancelled && saved) setFmt(saved)
      } catch {
        /* keep the default */
      }
    })()
    return () => {
      cancelled = true
    }
  }, [open])

  if (!open) return null

  async function run(kind: string) {
    setBusy(true)
    setErr('')
    setMsg('')
    try {
      const res = await systemAction({ action: 'export', kind, format: fmt })
      if (res.ok === false && res.error) {
        setErr(String(res.error))
      } else {
        setMsg(String(res.path ? `Exported: ${res.path}` : 'Export complete.'))
      }
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="modal-backdrop" role="presentation" onClick={onClose}>
      <div
        className="modal-card"
        role="dialog"
        aria-modal="true"
        aria-label="Export data"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="modal-head">
          <h2>Export Data</h2>
          <button type="button" className="icon-btn" onClick={onClose}>
            ✕
          </button>
        </div>
        <div className="modal-body">
          <label className="settings-field">
            <span>Format</span>
            <select
              className="settings-input"
              value={fmt}
              onChange={(e) => setFmt(e.target.value)}
            >
              <option value="csv">CSV</option>
              <option value="xlsx">Excel</option>
              <option value="pdf">PDF</option>
            </select>
          </label>
          <div className="settings-inline-actions">
            {(
              [
                ['sales', 'Export Sales'],
                ['purchases', 'Export Purchases'],
                ['inventory', 'Export Inventory'],
                ['all', 'Export All'],
              ] as const
            ).map(([kind, label]) => (
              <button
                key={kind}
                type="button"
                className="settings-action-btn"
                disabled={busy}
                onClick={() => void run(kind)}
              >
                {label}
              </button>
            ))}
          </div>
          {msg ? <p className="settings-note">{msg}</p> : null}
          {err ? <p className="error">{err}</p> : null}
        </div>
      </div>
    </div>
  )
}
