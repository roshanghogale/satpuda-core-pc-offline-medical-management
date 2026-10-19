/** The alerts window: at startup, on the while-running re-check ("New Alerts"), from
 *  Home -> Alerts and from Settings -> Alert & Monitoring -> Test the popup.
 *
 *  Redesigned 19 Oct 2026 in the style of the Export window (owner: "the Alert & Monitoring
 *  popups are not good enough"): a header with the counts, the alert kinds on the left with
 *  badges, a search / filter row, one table with a sticky header and an action on every
 *  row, and one bar at the bottom (Export ▾, Print ▾, Reorder / Return, Skip Today, Close).
 *  Expired and Near Expiry are grouped by the supplier each batch was bought from (on by
 *  default) with every supplier's count and value, and export supplier-wise.
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import type { AppNavigate } from '../App'
import type { StartupAlertTab } from '../pagesApi'
import { snoozeStartupAlerts, startupAlertAction } from '../pagesApi'
import { applyAlertNavigation } from '../settingsApi'
import {
  AlertGrid,
  AlertSearch,
  GroupSwitch,
  MenuButton,
  VALUE_MRP,
  VALUE_RATE,
  groupBySupplier,
  matchRows,
  rupees,
  sumColumn,
  supplierIndex,
  supplierLabel,
  type MenuItem,
} from './alerts/AlertBoard'
import { runAlertExport, type AlertExportHow, type AlertSection } from './alerts/alertExport'

type Props = {
  open: boolean
  tabs: StartupAlertTab[]
  onClose: () => void
  onNavigate: AppNavigate
  /** "Startup Alerts", "New Alerts" (the while-running re-check) or "Alerts" (Home). */
  title?: string
  /** The alert read failed (Online: the store). Shown instead of silence. */
  error?: string
}

/** The engine names the column to group by (Expired / Near Expiry: "Supplier"). */
type Tab = StartupAlertTab & { group_by?: string }

/** Expired / Near Expiry: columns left off the window to fit 1280 px (still exported). */
const EXPIRY_HIDDEN = new Set(['Type'])

const REORDER_TABS = new Set(['Low Stock Alerts', 'Out of Stock'])
const RETURN_TABS = new Set(['Near Expiry Alerts', 'Expired Medicines'])

const TAB_META: Record<string, { short: string; tone: string; settings: string }> = {
  'Low Stock Alerts': { short: 'Low Stock', tone: 'warn', settings: 'low' },
  'Out of Stock': { short: 'Out of Stock', tone: 'bad', settings: 'out' },
  'Near Expiry Alerts': { short: 'Near Expiry', tone: 'warn', settings: 'near' },
  'Expired Medicines': { short: 'Expired', tone: 'bad', settings: 'expired' },
  'Customer Due Alerts': { short: 'Customer Due', tone: 'due', settings: 'dues' },
}

function meta(title: string) {
  return TAB_META[title] || { short: title, tone: 'info', settings: 'low' }
}

function todayText() {
  return new Date().toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' })
}

function BellIcon() {
  return (
    <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden>
      <path
        d="M12 3a6 6 0 0 0-6 6v3.6L4.4 15.4A1 1 0 0 0 5.3 17h13.4a1 1 0 0 0 .9-1.6L18 12.6V9a6 6 0 0 0-6-6Zm0 18a2.5 2.5 0 0 0 2.4-2h-4.8A2.5 2.5 0 0 0 12 21Z"
        fill="currentColor"
      />
    </svg>
  )
}

export function StartupAlertsDialog({ open, tabs, onClose, onNavigate, title, error }: Props) {
  const [tabIdx, setTabIdx] = useState(0)
  const [search, setSearch] = useState('')
  const [supplier, setSupplier] = useState('')
  /** grouping per tab; a tab the engine marks group_by starts grouped */
  const [groupOff, setGroupOff] = useState<Record<string, boolean>>({})
  const [selected, setSelected] = useState<number | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [msg, setMsg] = useState('')
  const searchRef = useRef<HTMLInputElement>(null)
  const all = tabs as Tab[]
  const active: Tab | undefined = all[tabIdx] || all[0]

  useEffect(() => {
    if (open) {
      setTabIdx(0)
      setGroupOff({})
    }
  }, [open, tabs])

  useEffect(() => {
    setSelected(null)
    setSearch('')
    setSupplier('')
    setErr('')
    setMsg('')
  }, [tabIdx, open])

  useEffect(() => setSelected(null), [search, supplier])

  const columns = useMemo(() => active?.columns || [], [active])
  const supAt = supplierIndex(columns)
  const canGroup = Boolean(active?.group_by) && supAt >= 0
  const grouped = canGroup && !groupOff[active?.title || '']

  const view = useMemo(() => {
    const rows = (active?.rows || []) as unknown[][]
    const idx = matchRows(rows, search).filter(
      (i) => !supplier || supplierLabel(rows[i]?.[supAt]) === supplier,
    )
    return idx.map((i) => rows[i])
  }, [active, search, supplier, supAt])

  const suppliers = useMemo(
    () => (canGroup ? groupBySupplier(columns, (active?.rows || []) as unknown[][]) : []),
    [canGroup, columns, active],
  )
  const viewGroups = useMemo(() => (canGroup ? groupBySupplier(columns, view) : []), [canGroup, columns, view])

  const stats = useMemo(() => {
    const out: { label: string; value: string; tone: string }[] = []
    const exp = all.find((t) => t.title === 'Expired Medicines')
    const near = all.find((t) => t.title === 'Near Expiry Alerts')
    const due = all.find((t) => t.title === 'Customer Due Alerts')
    if (exp?.columns.includes(VALUE_RATE)) {
      out.push({ label: 'Expired stock', value: rupees(sumColumn(exp.columns, exp.rows, VALUE_RATE)), tone: 'bad' })
    }
    if (near?.columns.includes(VALUE_RATE)) {
      out.push({ label: 'Near expiry', value: rupees(sumColumn(near.columns, near.rows, VALUE_RATE)), tone: 'warn' })
    }
    if (due) out.push({ label: 'Customer due', value: rupees(sumColumn(due.columns, due.rows, 'Due Amount')), tone: '' })
    return out
  }, [all])

  if (!open) return null

  if (error && !all.length) {
    return (
      <div className="modal-backdrop" role="presentation" onClick={onClose}>
        <div
          className="alx-card"
          style={{ height: 'auto', width: 'min(520px, 96vw)' }}
          role="alertdialog"
          aria-modal="true"
          aria-label="Alerts could not be loaded"
          onClick={(e) => e.stopPropagation()}
        >
          <div className="alx-head">
            <div className="alx-head-icon">
              <BellIcon />
            </div>
            <div className="alx-head-text">
              <h2>Alerts could not be loaded</h2>
              <p className="alx-sub">Stock, expiry and due warnings were not checked.</p>
            </div>
          </div>
          <div style={{ padding: 16 }}>
            <p className="form-error" style={{ marginTop: 0 }}>
              {error}
            </p>
            <p className="alx-dim" style={{ fontSize: 12 }}>
              Open Settings → Alert &amp; Monitoring once the connection is back.
            </p>
          </div>
          <div className="alx-foot" style={{ justifyContent: 'flex-end' }}>
            <button type="button" className="btn btn-primary" onClick={onClose}>
              Close
            </button>
          </div>
        </div>
      </div>
    )
  }
  if (!all.length || !active) return null
  const tab: Tab = active

  const m = meta(tab.title)
  const totalAlerts = all.reduce((n, t) => n + (t.rows?.length || 0), 0)
  const selectedRow = selected != null ? view[selected] : undefined
  const showBulkReorder = REORDER_TABS.has(tab.title)
  const showBulkReturn = RETURN_TABS.has(tab.title)

  async function runAction(action: string, extra?: Record<string, unknown>) {
    setBusy(true)
    setErr('')
    setMsg('')
    try {
      const res = await startupAlertAction({
        action,
        title: tab.title,
        action_kind: tab.action_kind,
        ...extra,
      })
      if (!res.ok) {
        setErr(res.error || 'Action failed.')
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

  async function rowAction(i?: number | null) {
    const values = i != null ? view[i] : selectedRow
    if (!values) {
      setErr('Select a row first.')
      return
    }
    await runAction('row_action', { values })
  }

  function sectionOf(t: Tab, rows: unknown[][]): AlertSection {
    return {
      title: meta(t.title).short,
      columns: t.columns || [],
      rows,
      group_by: t.group_by && !groupOff[t.title] ? t.group_by : '',
    }
  }

  async function exporting(job: () => Promise<string>) {
    setBusy(true)
    setErr('')
    setMsg('')
    try {
      setMsg(await job())
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  function doExport(how: AlertExportHow, which: 'tab' | 'all' = 'tab') {
    const filters = [search.trim() ? `“${search.trim()}”` : '', supplier].filter(Boolean).join(', ')
    void exporting(() =>
      runAlertExport({
        title: which === 'all' ? title || 'Alerts' : `${m.short}${filters ? ` — ${filters}` : ''}`,
        sections:
          which === 'all'
            ? all.map((t) => sectionOf(t, (t.rows || []) as unknown[][]))
            : [sectionOf(tab, view)],
        how,
        pageLayout: 'landscape',
        fileName: which === 'all' ? 'Alerts' : undefined,
      }),
    )
  }

  /** One supplier's batches as a PDF (the button on its heading). */
  function exportSupplier(name: string, rowsIdx: number[]) {
    const keep = columns.map((_, i) => i).filter((i) => i !== supAt)
    void exporting(() =>
      runAlertExport({
        title: `${m.short} — ${name}`,
        sections: [
          {
            title: `${m.short} — ${name}`,
            columns: keep.map((k) => columns[k]),
            rows: rowsIdx.map((i) => keep.map((k) => view[i]?.[k])),
          },
        ],
        how: 'pdf',
        pageLayout: 'landscape',
        fileName: `${m.short}_${name}`,
      }),
    )
  }

  async function snooze() {
    try {
      await snoozeStartupAlerts()
    } catch {
      /* ignore */
    }
    onClose()
  }

  const groupedHint = grouped ? 'by supplier' : ''
  const exportItems: MenuItem[] = [
    { key: 'h1', heading: `This list — ${m.short} (${view.length})` },
    { key: 'pdf', label: 'PDF', hint: groupedHint, disabled: !view.length, onPick: () => doExport('pdf') },
    { key: 'xlsx', label: 'Excel', hint: groupedHint, disabled: !view.length, onPick: () => doExport('xlsx') },
    { key: 'csv', label: 'CSV', hint: groupedHint, disabled: !view.length, onPick: () => doExport('csv') },
  ]
  if (grouped) {
    exportItems.push({
      key: 'split',
      label: 'PDF — one file per supplier',
      hint: `${viewGroups.length} file${viewGroups.length === 1 ? '' : 's'}`,
      disabled: !view.length,
      onPick: () => doExport('pdf_split'),
    })
  }
  exportItems.push(
    { key: 's1', separator: true },
    { key: 'h2', heading: `All lists (${totalAlerts})` },
    { key: 'all_pdf', label: 'All lists — PDF', onPick: () => doExport('pdf', 'all') },
    { key: 'all_xlsx', label: 'All lists — Excel', onPick: () => doExport('xlsx', 'all') },
  )
  const printItems: MenuItem[] = [
    { key: 'h', heading: `Print ${m.short}${grouped ? ' by supplier' : ''}` },
    { key: 'printer', label: 'Normal printer', hint: 'A4 landscape', onPick: () => doExport('printer') },
    { key: 'dm_a4', label: 'Dot matrix', hint: 'A4', onPick: () => doExport('dm_a4') },
    { key: 'dm_a5', label: 'Dot matrix', hint: 'A5', onPick: () => doExport('dm_a5') },
  ]

  const hasValue = columns.includes(VALUE_RATE)
  const valueRate = sumColumn(columns, view, VALUE_RATE)
  const valueMrp = sumColumn(columns, view, VALUE_MRP)

  return (
    <div className="modal-backdrop" role="presentation" onClick={onClose}>
      <div
        className="alx-card"
        role="dialog"
        aria-modal="true"
        aria-label={title || 'Startup alerts'}
        onClick={(e) => e.stopPropagation()}
        onKeyDown={(e) => {
          if (e.key === 'Escape') {
            e.preventDefault()
            onClose()
          } else if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
            if (!view.length) return
            e.preventDefault()
            const d = e.key === 'ArrowDown' ? 1 : -1
            setSelected((s) => Math.max(0, Math.min(view.length - 1, s == null ? 0 : s + d)))
          } else if (e.key === 'Enter' && selected != null && tab.action_kind) {
            e.preventDefault()
            void rowAction(selected)
          } else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'f') {
            e.preventDefault()
            searchRef.current?.focus()
          }
        }}
      >
        <header className="alx-head">
          <div className="alx-head-icon">
            <BellIcon />
          </div>
          <div className="alx-head-text">
            <h2>{title || 'Startup Alerts'}</h2>
            <p className="alx-sub">
              {totalAlerts} alert{totalAlerts === 1 ? '' : 's'} in {all.length} list{all.length === 1 ? '' : 's'} ·{' '}
              {todayText()}
            </p>
          </div>
          <div className="alx-stats">
            {stats.map((s) => (
              <div key={s.label} className={`alx-stat ${s.tone}`}>
                <b>{s.value}</b>
                <span>{s.label}</span>
              </div>
            ))}
          </div>
          <button type="button" className="alx-close" aria-label="Close" onClick={onClose}>
            ✕
          </button>
        </header>

        <div className="alx-main">
          <nav className="alx-side" role="tablist" aria-label="Alert lists">
            <div className="alx-side-head">Alerts</div>
            {all.map((t, i) => {
              const tm = meta(t.title)
              return (
                <button
                  key={t.title}
                  type="button"
                  role="tab"
                  aria-selected={i === tabIdx}
                  className={`alx-tab${i === tabIdx ? ' active' : ''}`}
                  onClick={() => setTabIdx(i)}
                >
                  <span className={`alx-dot ${tm.tone}`} />
                  <span className="alx-tab-label">{tm.short}</span>
                  <span className="alx-badge">{t.rows?.length || 0}</span>
                </button>
              )
            })}
            <div className="alx-side-foot">
              <button
                type="button"
                className="alx-link"
                onClick={() => {
                  onNavigate('settings', { settingsTab: 'alerts', settingsToggle: m.settings })
                  onClose()
                }}
              >
                Open Alert &amp; Monitoring →
              </button>
            </div>
          </nav>

          <section className="alx-pane" role="tabpanel" aria-label={m.short}>
            <div className="alx-pane-head">
              <h3>{m.short}</h3>
              <span className="alx-dim">
                {view.length} row{view.length === 1 ? '' : 's'}
                {canGroup ? ` · ${viewGroups.length} supplier${viewGroups.length === 1 ? '' : 's'}` : ''}
                {hasValue ? ` · ${rupees(valueRate)} at purchase rate · ${rupees(valueMrp)} at MRP` : ''}
              </span>
            </div>
            <div className="alx-tools">
              <AlertSearch value={search} onChange={setSearch} inputRef={searchRef} />
              {canGroup ? (
                <>
                  <GroupSwitch
                    on={grouped}
                    onChange={(on) => setGroupOff((p) => ({ ...p, [tab.title]: !on }))}
                  />
                  <select
                    className="alx-select"
                    value={supplier}
                    aria-label="Supplier"
                    onChange={(e) => setSupplier(e.target.value)}
                  >
                    <option value="">All suppliers ({suppliers.length})</option>
                    {suppliers.map((g) => (
                      <option key={g.supplier} value={g.supplier}>
                        {g.supplier} ({g.count})
                      </option>
                    ))}
                  </select>
                </>
              ) : null}
              {search || supplier ? (
                <span className="alx-count-note">
                  {view.length} of {tab.rows?.length || 0}
                </span>
              ) : null}
            </div>
            <AlertGrid
              columns={columns}
              rows={view}
              hidden={canGroup ? EXPIRY_HIDDEN : undefined}
              grouped={grouped}
              selected={selected}
              onSelect={setSelected}
              onActivate={(i) => {
                setSelected(i)
                if (tab.action_kind) void rowAction(i)
              }}
              rowActionLabel={tab.action_kind ? tab.action_label : null}
              onRowAction={(i) => {
                setSelected(i)
                void rowAction(i)
              }}
              groupActions={(g) => (
                <button
                  type="button"
                  className="alx-group-btn"
                  disabled={busy}
                  title={`PDF of ${g.supplier} only`}
                  onClick={() => exportSupplier(g.supplier, g.idx)}
                >
                  PDF
                </button>
              )}
              empty={search || supplier ? 'Nothing matches the search.' : 'No alerts in this list.'}
            />
          </section>
        </div>

        <footer className="alx-foot">
          <span className={`alx-foot-msg${err ? ' err' : msg ? ' ok' : ''}`} role={err ? 'alert' : undefined}>
            {err ||
              msg ||
              (busy
                ? 'Working…'
                : selectedRow
                  ? `Selected: ${String(selectedRow[0] ?? '')}${
                      tab.action_label ? ` — Enter or double-click: ${tab.action_label}` : ''
                    }`
                  : '↑ ↓ select · Enter acts · Ctrl+F search · Esc closes')}
          </span>
          <MenuButton label="Export" items={exportItems} disabled={busy} />
          <MenuButton label="Print" items={printItems} disabled={busy || !view.length} />
          {showBulkReorder || showBulkReturn ? <span className="alx-foot-sep" /> : null}
          {showBulkReorder ? (
            <button
              type="button"
              className="btn btn-neutral"
              disabled={busy}
              onClick={() => void runAction('bulk_reorder')}
            >
              Reorder by Supplier
            </button>
          ) : null}
          {showBulkReturn ? (
            <button
              type="button"
              className="btn btn-neutral"
              disabled={busy}
              onClick={() => void runAction('bulk_return')}
            >
              Return by Purchase
            </button>
          ) : null}
          <span className="alx-foot-sep" />
          <button
            type="button"
            className="btn btn-neutral"
            disabled={busy}
            title="Do not show these alerts again today"
            onClick={() => void snooze().catch(() => undefined)}
          >
            Skip Today
          </button>
          <button type="button" className="btn btn-primary" onClick={onClose}>
            Close
          </button>
        </footer>
      </div>
    </div>
  )
}
