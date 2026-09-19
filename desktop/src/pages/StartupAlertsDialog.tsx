import { useEffect, useMemo, useState } from 'react'
import type { AppNavigate } from '../App'
import type { StartupAlertTab } from '../pagesApi'
import { snoozeStartupAlerts, startupAlertAction } from '../pagesApi'
import { applyAlertNavigation } from '../settingsApi'
import { DataTable } from './pageChrome'

type Props = {
  open: boolean
  tabs: StartupAlertTab[]
  onClose: () => void
  onNavigate: AppNavigate
  /** "Startup Alerts", or "New Alerts" for the while-running re-check. */
  title?: string
  /** The alert read failed (Online: the store). Shown instead of silence. */
  error?: string
}

const REORDER_TABS = new Set(['Low Stock Alerts', 'Out of Stock'])
const RETURN_TABS = new Set(['Near Expiry Alerts', 'Expired Medicines'])

const TAB_SHORT_LABELS: Record<string, string> = {
  'Low Stock Alerts': 'Low Stock',
  'Out of Stock': 'Out of Stock',
  'Near Expiry Alerts': 'Near Expiry',
  'Expired Medicines': 'Expired',
  'Customer Due Alerts': 'Customer Due',
}

function tabShortLabel(title: string): string {
  return TAB_SHORT_LABELS[title] || title
}

function downloadBase64File(
  filename: string,
  mime: string,
  contentBase64: string,
) {
  const bin = atob(contentBase64)
  const bytes = new Uint8Array(bin.length)
  for (let i = 0; i < bin.length; i += 1) bytes[i] = bin.charCodeAt(i)
  const blob = new Blob([bytes], { type: mime })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
}

export function StartupAlertsDialog({
  open,
  tabs,
  onClose,
  onNavigate,
  title,
  error,
}: Props) {
  const [tabIdx, setTabIdx] = useState(0)
  const [selectedIdx, setSelectedIdx] = useState<number | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [msg, setMsg] = useState('')
  const active = tabs[tabIdx] || tabs[0]

  const tabLabels = useMemo(
    () =>
      tabs.map((t) => ({
        title: t.title,
        short: tabShortLabel(t.title),
        count: t.rows?.length || 0,
      })),
    [tabs],
  )

  const displayRows = useMemo(() => {
    const rows = active?.rows || []
    return rows.length > 400 ? rows.slice(0, 400) : rows
  }, [active])

  useEffect(() => {
    setSelectedIdx(null)
    setErr('')
    setMsg('')
  }, [tabIdx, open])

  if (!open) return null
  if (error && !tabs.length) {
    return (
      <div className="modal-backdrop" role="presentation" onClick={onClose}>
        <div
          className="modal-card"
          role="alertdialog"
          aria-modal="true"
          aria-label="Alerts could not be loaded"
          onClick={(e) => e.stopPropagation()}
        >
          <div className="modal-head">
            <h2>Alerts could not be loaded</h2>
          </div>
          <div className="modal-body">
            <p className="form-error">{error}</p>
            <p className="muted">
              Stock, expiry and due warnings were not checked. Open Settings →
              Alert &amp; Monitoring once the connection is back.
            </p>
          </div>
          <div className="modal-foot">
            <button type="button" className="btn btn-primary" onClick={onClose}>
              Close
            </button>
          </div>
        </div>
      </div>
    )
  }
  if (!tabs.length || !active) return null

  const selectedRow =
    selectedIdx != null ? displayRows[selectedIdx] : undefined

  async function runAction(
    action: string,
    extra?: Record<string, unknown>,
  ) {
    setBusy(true)
    setErr('')
    setMsg('')
    try {
      const res = await startupAlertAction({
        action,
        title: active.title,
        action_kind: active.action_kind,
        ...extra,
      })
      if (!res.ok) {
        setErr(res.error || 'Action failed.')
        return
      }
      if (res.content_base64 && res.filename) {
        downloadBase64File(
          res.filename,
          res.mime || 'application/pdf',
          res.content_base64,
        )
        setMsg(`Exported ${res.row_count ?? 0} row(s) to PDF.`)
        return
      }
      if (res.navigate) {
        applyAlertNavigation(res.navigate, onNavigate as never)
        onClose()
        return
      }
      if (res.message) setMsg(res.message)
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  async function rowAction(row?: unknown[]) {
    const values = row || selectedRow
    if (!values) {
      setErr('Select a row first.')
      return
    }
    await runAction('row_action', { values })
  }

  async function exportPdf() {
    await runAction('export_pdf', {
      columns: active.columns,
      rows: active.rows,
    })
  }

  async function snooze() {
    try {
      await snoozeStartupAlerts()
    } catch {
      /* ignore */
    }
    onClose()
  }

  const showBulkReorder = REORDER_TABS.has(active.title)
  const showBulkReturn = RETURN_TABS.has(active.title)
  const truncated = (active.rows?.length || 0) > displayRows.length

  return (
    <div className="modal-backdrop" role="presentation" onClick={onClose}>
      <div
        className="modal-card modal-card-wide modal-card-alerts"
        role="dialog"
        aria-modal="true"
        aria-label={title || 'Startup alerts'}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="modal-head">
          <h2>{title || 'Startup Alerts'}</h2>
          <button type="button" className="icon-btn" onClick={onClose}>
            ✕
          </button>
        </div>
        <div className="startup-alert-tabbar" role="tablist" aria-label="Alert categories">
          {tabLabels.map((t, i) => (
            <button
              key={t.title}
              type="button"
              role="tab"
              aria-selected={i === tabIdx}
              className={`startup-alert-tab${i === tabIdx ? ' active' : ''}`}
              onClick={() => setTabIdx(i)}
            >
              {t.short} ({t.count})
            </button>
          ))}
        </div>
        <div className="modal-body">
          {truncated ? (
            <p className="muted" style={{ marginBottom: 8 }}>
              Showing first {displayRows.length} of {active.rows?.length} — Export
              PDF for the full list.
            </p>
          ) : null}
          <DataTable
            columns={active.columns || []}
            rows={displayRows}
            empty="No alerts in this tab"
            selectedRowIndex={selectedIdx}
            onRowClick={setSelectedIdx}
            onRowDoubleClick={(i) => {
              setSelectedIdx(i)
              void rowAction(displayRows[i] as unknown[])
            }}
          />
          {(active.action_kind || showBulkReorder || showBulkReturn) && (
            <div className="modal-inline-actions" style={{ marginTop: 12 }}>
              {active.action_kind && active.action_label ? (
                <button
                  type="button"
                  className="btn btn-secondary"
                  disabled={busy}
                  onClick={() => void rowAction()}
                >
                  {active.action_label}
                </button>
              ) : null}
              {showBulkReorder ? (
                <button
                  type="button"
                  className="btn btn-secondary"
                  disabled={busy}
                  onClick={() => void runAction('bulk_reorder')}
                >
                  Reorder by Supplier
                </button>
              ) : null}
              {showBulkReturn ? (
                <button
                  type="button"
                  className="btn btn-secondary"
                  disabled={busy}
                  onClick={() => void runAction('bulk_return')}
                >
                  Return by Purchase
                </button>
              ) : null}
            </div>
          )}
          {err ? <p className="form-error">{err}</p> : null}
          {msg ? <p className="form-note">{msg}</p> : null}
        </div>
        <div className="modal-foot">
          <button
            type="button"
            className="btn btn-neutral"
            disabled={busy}
            onClick={() => void snooze().catch(() => undefined)}
          >
            Skip Today
          </button>
          <button
            type="button"
            className="btn btn-neutral"
            disabled={busy}
            onClick={() => void exportPdf()}
          >
            Export PDF (current tab)
          </button>
          <button type="button" className="btn btn-primary" onClick={onClose}>
            Close
          </button>
        </div>
      </div>
    </div>
  )
}
