/** Two-step export dialog + Schedule Report options (mirrors Tk). */

import { useEffect, useMemo, useState } from 'react'
import { getApiBase } from '../api'
import { exportRowsCsv } from './pageChrome'

export type ExportPage = 'inventory' | 'sales_history' | 'purchase_history'

type Option = { key: string; label: string }

type ScheduleLayout = { key: string; label: string }

type Props = {
  page: ExportPage
  open: boolean
  onClose: () => void
  from?: string
  to?: string
  /** Current on-screen table (for Current View). */
  currentColumns?: string[]
  currentRows?: unknown[][]
}

type Step = 'report' | 'schedule' | 'format'

async function fetchOptions(page: string): Promise<Option[]> {
  const res = await fetch(
    `${getApiBase()}/api/export/options?page=${encodeURIComponent(page)}`,
  )
  const data = await res.json()
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`)
  return (data.options || []) as Option[]
}

async function fetchSchedules() {
  const res = await fetch(`${getApiBase()}/api/export/schedules`)
  const data = await res.json()
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`)
  return data as {
    schedules: string[]
    layout: string
    layouts: ScheduleLayout[]
    dm_style?: string
    dm_borders?: boolean
    dm_styles?: ScheduleLayout[]
  }
}

async function runExport(body: Record<string, unknown>) {
  const res = await fetch(`${getApiBase()}/api/export/run`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  const data = await res.json()
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`)
  return data as {
    ok?: boolean
    error?: string
    columns: string[]
    rows: unknown[][]
    filename?: string
    title?: string
    content_base64?: string
    mime?: string
    printed?: boolean
    print_error?: string
    pdf_path?: string
    path?: string
  }
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

function todayIso() {
  return new Date().toISOString().slice(0, 10)
}

function fyBounds(year: number): { from: string; to: string } {
  const y = year || new Date().getFullYear()
  return { from: `${y}-04-01`, to: `${y + 1}-03-31` }
}

function monthBounds(ym: string): { from: string; to: string } {
  const [ys, ms] = ym.split('-')
  const y = Number(ys)
  const m = Number(ms)
  if (!y || !m) return { from: '', to: '' }
  const last = new Date(y, m, 0).getDate()
  return {
    from: `${y}-${String(m).padStart(2, '0')}-01`,
    to: `${y}-${String(m).padStart(2, '0')}-${String(last).padStart(2, '0')}`,
  }
}

export function ExportDialog({
  page,
  open,
  onClose,
  from = '',
  to = '',
  currentColumns = [],
  currentRows = [],
}: Props) {
  const [step, setStep] = useState<Step>('report')
  const [options, setOptions] = useState<Option[]>([])
  const [report, setReport] = useState('current_view')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const [schedules, setSchedules] = useState<string[]>([])
  const [layouts, setLayouts] = useState<ScheduleLayout[]>([])
  const [dmStyles, setDmStyles] = useState<ScheduleLayout[]>([])
  const [schMode, setSchMode] = useState<'all' | 'non_scheduled' | 'selected'>(
    'all',
  )
  const [schPicked, setSchPicked] = useState<Record<string, boolean>>({})
  const [pageLayout, setPageLayout] = useState('portrait')
  const [dmStyle, setDmStyle] = useState('classic')
  const [dmBorders, setDmBorders] = useState(true)
  // Print: which printer, and A4 vertical / horizontal (the Schedule register keeps its own layout)
  const [printTo, setPrintTo] = useState<'' | 'dot_matrix' | 'printer'>('')
  const [printPage, setPrintPage] = useState<'portrait' | 'landscape'>('portrait')
  // Schedule Report: PDF / Print first ask which print style -- a proper A4
  // report for a laser / inkjet printer, or the compact dot matrix one. Two
  // separate templates; the choice is never taken from the other.
  const [styleAsk, setStyleAsk] = useState<null | { format: 'pdf'; print: boolean }>(null)
  const [dateMode, setDateMode] = useState<
    'page' | 'year' | 'month' | 'custom'
  >('page')
  const [year, setYear] = useState(() => {
    const now = new Date()
    const y = now.getFullYear()
    return String(now.getMonth() >= 3 ? y : y - 1)
  })
  const [month, setMonth] = useState(
    `${new Date().getFullYear()}-${String(new Date().getMonth() + 1).padStart(2, '0')}`,
  )
  const [schFrom, setSchFrom] = useState(from || '')
  const [schTo, setSchTo] = useState(to || todayIso())

  useEffect(() => {
    if (!open) return
    setStep('report')
    setError('')
    setBusy(true)
    void fetchOptions(page)
      .then((opts) => {
        setOptions(opts)
        setReport(opts[0]?.key || 'current_view')
      })
      .catch((e) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setBusy(false))
  }, [open, page])

  useEffect(() => {
    if (!open) return
    setError('')
    if (from) setSchFrom(from)
    if (to) setSchTo(to)
    setDateMode(from || to ? 'page' : 'year')
    setSchMode('all')
  }, [open, from, to])

  useEffect(() => {
    if (!open || report !== 'schedule_report') return
    void fetchSchedules()
      .then((data) => {
        setSchedules(data.schedules || [])
        setLayouts(data.layouts || [])
        setPageLayout(data.layout || 'portrait')
        setDmStyles(data.dm_styles || [])
        setDmStyle(data.dm_style || 'classic')
        setDmBorders(data.dm_borders !== false)
        const init: Record<string, boolean> = {}
        for (const s of data.schedules || []) init[s] = false
        setSchPicked(init)
      })
      .catch((e) => setError(e instanceof Error ? e.message : String(e)))
  }, [open, report])

  const resolvedScheduleDates = useMemo(() => {
    if (dateMode === 'page') {
      return { from: (from || schFrom).trim(), to: (to || schTo || todayIso()).trim() }
    }
    if (dateMode === 'year') {
      const y = Number(year) || new Date().getFullYear()
      return fyBounds(y)
    }
    if (dateMode === 'month') return monthBounds(month)
    return { from: schFrom.trim(), to: (schTo.trim() || todayIso()) }
  }, [dateMode, year, month, schFrom, schTo, from, to])

  if (!open) return null

  const titles: Record<ExportPage, string> = {
    inventory: 'Export Inventory Reports',
    sales_history: 'Export Sales Reports',
    purchase_history: 'Export Purchase Reports',
  }

  const stepTitle =
    step === 'report'
      ? titles[page]
      : step === 'schedule'
        ? 'Schedule Report'
        : 'Export Format'

  const goNextFromReport = () => {
    if (report === 'schedule_report') {
      setStep('schedule')
      return
    }
    setStep('format')
  }

  const buildSchedulePayload = () => {
    const picked = Object.entries(schPicked)
      .filter(([, on]) => on)
      .map(([k]) => k)
    if (schMode === 'selected' && !picked.length) {
      throw new Error('Check at least one schedule, or choose All / Non-Scheduled.')
    }
    return {
      mode: schMode,
      schedules: schMode === 'selected' ? picked : [],
      from_date: resolvedScheduleDates.from,
      to_date: resolvedScheduleDates.to,
      page_layout: pageLayout,
      dm_style: dmStyle,
      dm_borders: dmBorders,
      label:
        schMode === 'all'
          ? 'All Schedules'
          : schMode === 'non_scheduled'
            ? 'Non-Scheduled'
            : picked.join(', '),
    }
  }

  const doDownload = async (
    format: 'csv' | 'excel' | 'pdf',
    opts?: { print?: boolean; style?: 'standard' | 'dot_matrix' },
  ) => {
    if (report === 'schedule_report' && format === 'pdf' && !opts?.style) {
      setStyleAsk({ format, print: Boolean(opts?.print) })
      return
    }
    const style = opts?.style
    // The style decides the printer for the Schedule Report.
    const target: '' | 'dot_matrix' | 'printer' =
      style === 'standard' ? 'printer' : style === 'dot_matrix' ? 'dot_matrix' : printTo
    setBusy(true)
    setError('')
    try {
      let schedule: Record<string, unknown> | undefined
      let exportFrom = from
      let exportTo = to
      if (report === 'schedule_report') {
        schedule = buildSchedulePayload()
        exportFrom = String(schedule.from_date || '')
        exportTo = String(schedule.to_date || '')
      }
      const data = await runExport({
        page,
        report,
        from: exportFrom,
        to: exportTo,
        columns: currentColumns,
        rows: currentRows,
        schedule,
        format: format === 'excel' ? 'xlsx' : format,
        print: Boolean(opts?.print),
        print_to: target,
        print_style: style || '',
        // the PDF file keeps its wide page; the page chosen under Print is for printing
        page_layout: report === 'schedule_report' ? pageLayout : opts?.print ? printPage : 'landscape',
      })
      if (opts?.print) {
        if (!data.ok && data.error) {
          setError(data.error)
          return
        }
        // The engine has always returned where it put the file; the alert
        // said only that it was "saved" and threw the path away.
        const savedTo = data.pdf_path || data.path || ''
        const where = savedTo ? `\n\nSaved to:\n${savedTo}` : ''
        const what = report === 'schedule_report' ? 'Schedule report' : 'Report'
        const note =
          (data.printed
            ? `${what} sent to the ${target === 'printer' ? 'printer' : target === 'dot_matrix' ? 'dot matrix' : 'printer (Settings)'}.`
            : data.print_error
              ? `Print failed: ${data.print_error}`
              : `${what} saved.`) + where
        setError('')
        window.alert(note)
        onClose()
        return
      }
      if (data.content_base64 && data.filename) {
        downloadBase64File(
          data.filename,
          data.mime ||
            (format === 'pdf'
              ? 'application/pdf'
              : format === 'excel'
                ? 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
                : 'text/csv;charset=utf-8'),
          data.content_base64,
        )
        onClose()
        return
      }
      if (!data.rows?.length) {
        setError('No records to export.')
        return
      }
      const base = data.filename || `${page}_export`
      exportRowsCsv(base, data.columns || [], data.rows)
      onClose()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const wide = step === 'schedule'

  return (
    <div className="modal-backdrop" role="presentation" onClick={onClose}>
      <div
        className={`modal-card${wide ? ' modal-card-wide' : ''}`}
        role="dialog"
        aria-modal="true"
        aria-label={stepTitle}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="modal-head">
          <h2>{stepTitle}</h2>
          <button type="button" className="icon-btn" onClick={onClose}>
            ✕
          </button>
        </div>
        <div className="modal-body">
          {error ? <p className="page-error">{error}</p> : null}
          {busy && !options.length ? <p className="muted">Loading…</p> : null}

          {step === 'report' ? (
            <div className="export-options">
              {options.map((o) => (
                <label key={o.key} className="export-option">
                  <input
                    type="radio"
                    name="export-report"
                    checked={report === o.key}
                    onChange={() => setReport(o.key)}
                  />
                  <span>{o.label}</span>
                </label>
              ))}
            </div>
          ) : null}

          {step === 'schedule' ? (
            <div className="export-schedule">
              <fieldset className="export-fieldset">
                <legend>Report period</legend>
                {from || to ? (
                  <label className="export-option">
                    <input
                      type="radio"
                      name="sch-date"
                      checked={dateMode === 'page'}
                      onChange={() => setDateMode('page')}
                    />
                    <span>
                      Same as page filter
                      {from || to ? ` (${from || '…'} → ${to || '…'})` : ''}
                    </span>
                  </label>
                ) : null}
                <label className="export-option">
                  <input
                    type="radio"
                    name="sch-date"
                    checked={dateMode === 'year'}
                    onChange={() => setDateMode('year')}
                  />
                  <span>Financial year (Apr–Mar)</span>
                  <input
                    className="settings-input"
                    style={{ width: 72, marginLeft: 8 }}
                    value={year}
                    onChange={(e) => setYear(e.target.value)}
                    disabled={dateMode !== 'year'}
                  />
                </label>
                <label className="export-option">
                  <input
                    type="radio"
                    name="sch-date"
                    checked={dateMode === 'month'}
                    onChange={() => setDateMode('month')}
                  />
                  <span>Month (YYYY-MM)</span>
                  <input
                    className="settings-input"
                    style={{ width: 100, marginLeft: 8 }}
                    value={month}
                    onChange={(e) => setMonth(e.target.value)}
                    disabled={dateMode !== 'month'}
                  />
                </label>
                <label className="export-option">
                  <input
                    type="radio"
                    name="sch-date"
                    checked={dateMode === 'custom'}
                    onChange={() => setDateMode('custom')}
                  />
                  <span>Custom dates</span>
                </label>
                {dateMode === 'custom' ? (
                  <div className="export-date-row">
                    <label>
                      From
                      <input
                        type="date"
                        className="settings-input"
                        value={schFrom}
                        onChange={(e) => setSchFrom(e.target.value)}
                      />
                    </label>
                    <label>
                      To
                      <input
                        type="date"
                        className="settings-input"
                        value={schTo}
                        onChange={(e) => setSchTo(e.target.value)}
                      />
                    </label>
                  </div>
                ) : null}
              </fieldset>

              <fieldset className="export-fieldset">
                <legend>Export style</legend>
                <label className="muted" style={{ display: 'block', marginBottom: 4 }}>
                  Page layout (PDF)
                </label>
                <select
                  className="settings-input"
                  value={pageLayout}
                  onChange={(e) => setPageLayout(e.target.value)}
                >
                  {(layouts.length
                    ? layouts
                    : [
                        {
                          key: 'portrait',
                          label: 'A4 Portrait (no rate/amount)',
                        },
                        {
                          key: 'landscape',
                          label: 'A4 Landscape (all columns)',
                        },
                        {
                          key: 'styled',
                          label:
                            'Styled Vertical (combined columns — Classic & Sign)',
                        },
                      ]
                  ).map((l) => (
                    <option key={l.key} value={l.key}>
                      {l.label}
                    </option>
                  ))}
                </select>
                <p className="muted" style={{ marginTop: 6, fontSize: 12 }}>
                  Styled Vertical uses combined columns. Classic / Sign presets
                  apply to both PDF and dot-matrix print.
                </p>
                <label className="muted" style={{ display: 'block', margin: '10px 0 4px' }}>
                  Dot matrix preset
                </label>
                <select
                  className="settings-input"
                  value={dmStyle}
                  onChange={(e) => setDmStyle(e.target.value)}
                >
                  {(dmStyles.length
                    ? dmStyles
                    : [
                        {
                          key: 'classic',
                          label: 'Classic (Batch + Expiry separate)',
                        },
                        {
                          key: 'sign',
                          label: 'Sign style (Batch/Expiry + Sign column)',
                        },
                      ]
                  ).map((l) => (
                    <option key={l.key} value={l.key}>
                      {l.label}
                    </option>
                  ))}
                </select>
                <label className="export-option" style={{ marginTop: 8 }}>
                  <input
                    type="checkbox"
                    checked={dmBorders}
                    onChange={(e) => setDmBorders(e.target.checked)}
                  />
                  <span>Dot matrix table borders</span>
                </label>
              </fieldset>

              <fieldset className="export-fieldset">
                <legend>Schedules to include</legend>
                <label className="export-option">
                  <input
                    type="radio"
                    name="sch-mode"
                    checked={schMode === 'all'}
                    onChange={() => setSchMode('all')}
                  />
                  <span>All Schedules</span>
                </label>
                <label className="export-option">
                  <input
                    type="radio"
                    name="sch-mode"
                    checked={schMode === 'non_scheduled'}
                    onChange={() => setSchMode('non_scheduled')}
                  />
                  <span>Non-Scheduled only</span>
                </label>
                <label className="export-option">
                  <input
                    type="radio"
                    name="sch-mode"
                    checked={schMode === 'selected'}
                    onChange={() => setSchMode('selected')}
                  />
                  <span>Selected schedules</span>
                </label>
                <div className="export-sch-list">
                  {schedules.length ? (
                    schedules.map((s) => (
                      <label key={s} className="export-option">
                        <input
                          type="checkbox"
                          checked={Boolean(schPicked[s])}
                          disabled={schMode !== 'selected'}
                          onChange={(e) =>
                            setSchPicked((prev) => ({
                              ...prev,
                              [s]: e.target.checked,
                            }))
                          }
                        />
                        <span>{s}</span>
                      </label>
                    ))
                  ) : (
                    <p className="muted">No schedules in layout settings.</p>
                  )}
                </div>
                <div className="export-sch-actions">
                  <button
                    type="button"
                    className="btn btn-neutral btn-sm"
                    onClick={() => {
                      setSchMode('selected')
                      setSchPicked(
                        Object.fromEntries(schedules.map((s) => [s, true])),
                      )
                    }}
                  >
                    Select all listed
                  </button>
                  <button
                    type="button"
                    className="btn btn-neutral btn-sm"
                    onClick={() =>
                      setSchPicked(
                        Object.fromEntries(schedules.map((s) => [s, false])),
                      )
                    }
                  >
                    Clear checks
                  </button>
                </div>
              </fieldset>
            </div>
          ) : null}

          {step === 'format' ? (
            <div className="export-options">
              <p className="muted" style={{ marginTop: 0 }}>
                Choose file format. Export columns follow Settings → Layout &amp;
                Lists → Export report columns.
              </p>
              <button
                type="button"
                className="btn btn-primary"
                style={{ width: '100%', justifyContent: 'center' }}
                disabled={busy}
                onClick={() => void doDownload('csv')}
              >
                CSV
              </button>
              <button
                type="button"
                className="btn btn-secondary"
                style={{ width: '100%', justifyContent: 'center' }}
                disabled={busy}
                onClick={() => void doDownload('excel')}
              >
                Excel
              </button>
              <button
                type="button"
                className="btn btn-neutral"
                style={{ width: '100%', justifyContent: 'center' }}
                disabled={busy}
                onClick={() => void doDownload('pdf')}
              >
                PDF
              </button>
              <fieldset className="export-fieldset" style={{ marginTop: 8 }}>
                <legend>Print</legend>
                {report === 'schedule_report' ? (
                  <p className="muted" style={{ marginTop: 0, fontSize: 12 }}>
                    Print asks for the style first: Standard / Laser (A4
                    report) or Dot Matrix.
                  </p>
                ) : (
                  <>
                    <label className="muted" style={{ display: 'block', marginBottom: 4 }}>
                      Printer
                    </label>
                    <select
                      className="settings-input"
                      value={printTo}
                      onChange={(e) => setPrintTo(e.target.value as '' | 'dot_matrix' | 'printer')}
                    >
                      <option value="">As set in Settings → Printer</option>
                      <option value="dot_matrix">Dot matrix</option>
                      <option value="printer">Normal printer (laser / inkjet)</option>
                    </select>
                  </>
                )}
                {report === 'schedule_report' ? null : (
                  <>
                    <label className="muted" style={{ display: 'block', margin: '10px 0 4px' }}>
                      Page
                    </label>
                    <select
                      className="settings-input"
                      value={printPage}
                      onChange={(e) => setPrintPage(e.target.value as 'portrait' | 'landscape')}
                    >
                      <option value="portrait">A4 vertical (portrait)</option>
                      <option value="landscape">A4 horizontal (landscape)</option>
                    </select>
                  </>
                )}
                <button
                  type="button"
                  className="btn btn-primary"
                  style={{ width: '100%', justifyContent: 'center', marginTop: 10 }}
                  disabled={busy}
                  onClick={() => void doDownload('pdf', { print: true })}
                >
                  Print
                </button>
              </fieldset>
            </div>
          ) : null}
        </div>
        <div className="modal-foot">
          {step === 'format' || step === 'schedule' ? (
            <button
              type="button"
              className="btn btn-neutral"
              onClick={() =>
                setStep(step === 'format' && report === 'schedule_report' ? 'schedule' : 'report')
              }
            >
              Back
            </button>
          ) : (
            <button type="button" className="btn btn-neutral" onClick={onClose}>
              Cancel
            </button>
          )}
          {step === 'report' || step === 'schedule' ? (
            <button
              type="button"
              className="btn btn-primary"
              disabled={busy || (step === 'report' && !report)}
              onClick={() => {
                setError('')
                if (step === 'report') goNextFromReport()
                else {
                  try {
                    buildSchedulePayload()
                    setStep('format')
                  } catch (e) {
                    setError(e instanceof Error ? e.message : String(e))
                  }
                }
              }}
            >
              Next
            </button>
          ) : null}
        </div>
      </div>
      {styleAsk ? (
        <div
          className="modal-backdrop"
          role="presentation"
          onClick={(e) => {
            e.stopPropagation()
            setStyleAsk(null)
          }}
        >
          <div
            className="modal-card"
            role="dialog"
            aria-modal="true"
            aria-label="Select Print Style"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="modal-head">
              <h2>Select Print Style</h2>
              <button type="button" className="icon-btn" onClick={() => setStyleAsk(null)}>
                ✕
              </button>
            </div>
            <div className="modal-body export-options">
              <button
                type="button"
                className="btn btn-primary"
                style={{ width: '100%', justifyContent: 'center' }}
                disabled={busy}
                onClick={() => {
                  const ask = styleAsk
                  setStyleAsk(null)
                  void doDownload(ask.format, { print: ask.print, style: 'standard' })
                }}
              >
                Standard / Laser Printer
              </button>
              <p className="muted" style={{ margin: '2px 0 10px', fontSize: 12 }}>
                A4 report: readable font, columns sized to their content, header
                on every page.
              </p>
              <button
                type="button"
                className="btn btn-secondary"
                style={{ width: '100%', justifyContent: 'center' }}
                disabled={busy}
                onClick={() => {
                  const ask = styleAsk
                  setStyleAsk(null)
                  void doDownload(ask.format, { print: ask.print, style: 'dot_matrix' })
                }}
              >
                Dot Matrix Printer
              </button>
              <p className="muted" style={{ margin: '2px 0 0', fontSize: 12 }}>
                Compact register (Classic / Sign preset){ask_print_note(styleAsk.print)}.
              </p>
            </div>
            <div className="modal-foot">
              <button type="button" className="btn btn-neutral" onClick={() => setStyleAsk(null)}>
                Cancel
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  )
}

function ask_print_note(print: boolean): string {
  return print ? ', printed on the dot matrix' : ''
}
