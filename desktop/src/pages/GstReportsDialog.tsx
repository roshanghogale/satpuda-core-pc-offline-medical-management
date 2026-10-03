/** GST Reports: GSTR-1 (B2B, B2CL, B2CS, credit notes, HSN, documents), GSTR-3B, the sales GST
 *  register and the purchase ITC register, for a month, a quarter or any dates.
 *
 *  Every figure comes from the engine (core/gst_reports.py), which takes each bill's tax out the
 *  way the printed bill does -- so the tables add up to the bills, paisa for paisa. Bills it
 *  cannot be sure of (a rate taken from the medicine master, no HSN code ...) are listed under
 *  "Checks", never guessed.
 */
import { useEffect, useMemo, useState } from 'react'
import { createPortal } from 'react-dom'
import { getApiBase } from '../api'

type Section = { title: string; columns: string[]; rows: (string | number)[][] }
type Report = {
  ok?: boolean
  error?: string
  period: { from: string; to: string }
  shop: { name: string; gstin: string; state: string; state_name: string; gst_enabled: boolean }
  sections: Section[]
  checks: { what: string; where: string; detail: string }[]
  counts: { bills: number; credit_notes: number; purchases: number; purchase_returns: number }
}

const TABS: { key: string; label: string; tables: string[] }[] = [
  { key: '3b', label: 'GSTR-3B', tables: ['3B Summary'] },
  { key: 'reg', label: 'Sales register', tables: ['Sales GST Register'] },
  { key: 'b2b', label: 'B2B', tables: ['b2b'] },
  { key: 'b2c', label: 'B2CS / B2CL', tables: ['b2cs', 'b2cl'] },
  { key: 'cdn', label: 'Credit notes', tables: ['cdnr', 'cdnur'] },
  { key: 'hsn', label: 'HSN', tables: ['hsn(b2b)', 'hsn(b2c)'] },
  { key: 'docs', label: 'Documents', tables: ['docs'] },
  { key: 'itc', label: 'Purchase ITC', tables: ['Purchase ITC Register', 'Purchase Rate-wise'] },
  { key: 'checks', label: 'Checks', tables: ['Checks'] },
]

const pad = (n: number) => String(n).padStart(2, '0')
const iso = (d: Date) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`

function monthBounds(ym: string): { from: string; to: string } {
  const [y, m] = ym.split('-').map(Number)
  return { from: iso(new Date(y, m - 1, 1)), to: iso(new Date(y, m, 0)) }
}

/** Indian quarters: Q1 Apr-Jun ... Q4 Jan-Mar of the next calendar year. */
function quarterBounds(fyStart: number, q: number): { from: string; to: string } {
  const startMonth = [3, 6, 9, 0][q - 1]
  const year = q === 4 ? fyStart + 1 : fyStart
  return { from: iso(new Date(year, startMonth, 1)), to: iso(new Date(year, startMonth + 3, 0)) }
}

function lastMonth(): string {
  const d = new Date()
  d.setDate(1)
  d.setMonth(d.getMonth() - 1)
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}`
}

function currentFy(): number {
  const d = new Date()
  return d.getMonth() >= 3 ? d.getFullYear() : d.getFullYear() - 1
}

function downloadBase64(filename: string, mime: string, b64: string) {
  const bin = atob(b64)
  const bytes = new Uint8Array(bin.length)
  for (let i = 0; i < bin.length; i += 1) bytes[i] = bin.charCodeAt(i)
  const url = URL.createObjectURL(new Blob([bytes], { type: mime }))
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
}

const money = (v: unknown) =>
  typeof v === 'number' ? v.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : String(v ?? '')

const NUMERIC = /^(Rate|Taxable|IGST|CGST|SGST|Cess|Total|Bill Total|Bill Value|Invoice Value|Note Value|Integrated|Central|State\/UT|Taxable \/ Value)/

export function GstReportsDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [mode, setMode] = useState<'month' | 'quarter' | 'custom'>('month')
  const [month, setMonth] = useState(lastMonth())
  const [fy, setFy] = useState(currentFy())
  const [quarter, setQuarter] = useState(1)
  const [from, setFrom] = useState(monthBounds(lastMonth()).from)
  const [to, setTo] = useState(monthBounds(lastMonth()).to)
  const [report, setReport] = useState<Report | null>(null)
  const [tab, setTab] = useState('3b')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [msg, setMsg] = useState('')
  const [printTo, setPrintTo] = useState<'printer' | 'dot_matrix'>('printer')
  const [layout, setLayout] = useState<'portrait' | 'landscape'>('landscape')

  const range = useMemo(() => {
    if (mode === 'month') return monthBounds(month)
    if (mode === 'quarter') return quarterBounds(fy, quarter)
    return { from, to }
  }, [mode, month, fy, quarter, from, to])

  async function load() {
    setBusy(true)
    setErr('')
    setMsg('')
    try {
      const q = new URLSearchParams({ from: range.from, to: range.to })
      const res = await fetch(`${getApiBase()}/api/reports/gst?${q}`)
      const data = (await res.json().catch(() => ({}))) as Report
      if (!res.ok || data.ok === false) throw new Error(data.error || `HTTP ${res.status}`)
      setReport(data)
    } catch (e) {
      setReport(null)
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  useEffect(() => {
    if (open) void load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, range.from, range.to])

  async function exportAs(format: 'xlsx' | 'csv' | 'pdf' | 'json' | 'print', tablesOnly: boolean) {
    setBusy(true)
    setErr('')
    setMsg('')
    try {
      const tables = tablesOnly ? TABS.find((t) => t.key === tab)?.tables || [] : []
      const body: Record<string, unknown> = { from: range.from, to: range.to, tables, page_layout: layout }
      if (format === 'print') body.print_to = printTo
      else body.format = format
      const res = await fetch(`${getApiBase()}/api/reports/gst/export`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      const data = (await res.json().catch(() => ({}))) as {
        ok?: boolean; error?: string; message?: string; filename?: string; mime?: string; content_base64?: string
      }
      if (!res.ok || data.ok === false) throw new Error(data.error || `HTTP ${res.status}`)
      if (data.content_base64 && data.filename) {
        downloadBase64(data.filename, data.mime || 'application/octet-stream', data.content_base64)
        setMsg(`${data.filename} save zali (Downloads)`)
      } else {
        setMsg(data.message || 'Print la pathavle')
      }
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  if (!open) return null
  const shown = TABS.find((t) => t.key === tab) || TABS[0]
  const tables = (report?.sections || []).filter((s) => shown.tables.includes(s.title))
  const checks = report?.checks.length || 0

  const node = (
    <div className="modal-backdrop" role="presentation" onClick={onClose}>
      <div
        className="modal-card modal-card-wide gst-dialog"
        role="dialog"
        aria-modal="true"
        aria-label="GST Reports"
        style={{ width: 'min(1180px, 96vw)', maxHeight: '94vh', display: 'flex', flexDirection: 'column' }}
        onClick={(e) => e.stopPropagation()}
        onKeyDown={(e) => {
          if (e.key === 'Escape') {
            e.preventDefault()
            onClose()
          }
        }}
      >
        <div className="modal-head">
          <h2>
            GST Reports
            {report?.shop?.gstin ? ` — ${report.shop.gstin} (${report.shop.state_name})` : ''}
          </h2>
        </div>
        <div className="modal-body" style={{ overflow: 'auto' }}>
          <div className="settings-inline-row" style={{ flexWrap: 'wrap', gap: 8, alignItems: 'center' }}>
            <select className="settings-input" value={mode} onChange={(e) => setMode(e.target.value as typeof mode)}>
              <option value="month">Mahina (GSTR-1 / 3B)</option>
              <option value="quarter">Timahi (QRMP)</option>
              <option value="custom">Tarikh pasun – paryant</option>
            </select>
            {mode === 'month' ? (
              <input className="settings-input" type="month" value={month} onChange={(e) => setMonth(e.target.value)} />
            ) : null}
            {mode === 'quarter' ? (
              <>
                <select className="settings-input" value={fy} onChange={(e) => setFy(Number(e.target.value))}>
                  {[0, 1, 2, 3].map((k) => (
                    <option key={k} value={currentFy() - k}>
                      FY {currentFy() - k}-{String(currentFy() - k + 1).slice(2)}
                    </option>
                  ))}
                </select>
                <select className="settings-input" value={quarter} onChange={(e) => setQuarter(Number(e.target.value))}>
                  <option value={1}>Q1 (Apr–Jun)</option>
                  <option value={2}>Q2 (Jul–Sep)</option>
                  <option value={3}>Q3 (Oct–Dec)</option>
                  <option value={4}>Q4 (Jan–Mar)</option>
                </select>
              </>
            ) : null}
            {mode === 'custom' ? (
              <>
                <input className="settings-input" type="date" value={from} onChange={(e) => setFrom(e.target.value)} />
                <input className="settings-input" type="date" value={to} onChange={(e) => setTo(e.target.value)} />
              </>
            ) : null}
            <span className="vl-dim">
              {range.from} → {range.to}
              {report ? ` · ${report.counts.bills} bills · ${report.counts.credit_notes} returns · ${report.counts.purchases} purchases` : ''}
            </span>
            <button type="button" className="btn btn-neutral" disabled={busy} onClick={() => void load()}>
              {busy ? 'Mojat aahe…' : 'Punha moja'}
            </button>
          </div>

          {err ? <div className="vb-warn" style={{ marginTop: 8 }}>{err}</div> : null}
          {msg ? <div className="settings-hint" style={{ marginTop: 8 }}>{msg}</div> : null}
          {report && !report.shop.gstin ? (
            <div className="vb-warn" style={{ marginTop: 8 }}>
              Dukanacha GSTIN nahi — Settings → Pharmacy madhe bhara. Tyashivay GSTR-1 JSON banat nahi.
            </div>
          ) : null}
          {checks ? (
            <div className="vb-warn" style={{ marginTop: 8, cursor: 'pointer' }} onClick={() => setTab('checks')}>
              {checks} goshti tapasa (GST % bill var navhta, HSN nahi …) — "Checks" tab baghha
            </div>
          ) : null}

          <div className="settings-inline-row" style={{ gap: 4, marginTop: 10, flexWrap: 'wrap' }}>
            {TABS.map((t) => (
              <button
                key={t.key}
                type="button"
                className={`btn ${t.key === tab ? 'btn-primary' : 'btn-neutral'}`}
                onClick={() => setTab(t.key)}
              >
                {t.label}
                {t.key === 'checks' && checks ? ` (${checks})` : ''}
              </button>
            ))}
          </div>

          {tables.map((s) => (
            <div key={s.title} style={{ marginTop: 10 }}>
              {shown.tables.length > 1 ? <h3 style={{ margin: '6px 0' }}>{s.title}</h3> : null}
              {!s.rows.length ? (
                <div className="vl-dim">Ya kalavadhit kahi nahi</div>
              ) : (
                <div className="table-scroll" style={{ maxHeight: '48vh' }}>
                  <table className="sat-table">
                    <thead>
                      <tr>
                        {s.columns.map((c) => (
                          <th key={c} className={NUMERIC.test(c) ? 'num' : ''}>{c}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {s.rows.map((r, i) => (
                        <tr key={i}>
                          {r.map((v, j) => (
                            <td key={j} className={NUMERIC.test(s.columns[j] || '') ? 'num' : ''}>
                              {NUMERIC.test(s.columns[j] || '') ? money(v) : String(v ?? '')}
                            </td>
                          ))}
                        </tr>
                      ))}
                    </tbody>
                    {s.columns.some((c) => /^(Taxable|IGST|CGST|SGST|Integrated|Central|State\/UT)/.test(c)) &&
                    s.title !== '3B Summary' ? (
                      <tfoot>
                        <tr>
                          {s.columns.map((c, j) => (
                            <th key={c} className="num">
                              {j === 0
                                ? 'Ekun'
                                : /^(Taxable|IGST|CGST|SGST|Integrated|Central|State\/UT|Total GST|Note Value)/.test(c)
                                  ? money(s.rows.reduce((a, r) => a + (typeof r[j] === 'number' ? (r[j] as number) : 0), 0))
                                  : ''}
                            </th>
                          ))}
                        </tr>
                      </tfoot>
                    ) : null}
                  </table>
                </div>
              )}
            </div>
          ))}
        </div>
        <div className="modal-foot" style={{ flexWrap: 'wrap', gap: 6 }}>
          <button type="button" className="btn btn-primary" disabled={busy || !report} onClick={() => void exportAs('xlsx', false)}>
            Excel (sagle tables)
          </button>
          <button
            type="button"
            className="btn btn-neutral"
            disabled={busy || !report?.shop.gstin}
            title="GST portal chya offline tool madhe ughadun tapasa, mag upload kara"
            onClick={() => void exportAs('json', false)}
          >
            GSTR-1 JSON
          </button>
          <button type="button" className="btn btn-neutral" disabled={busy || !report} onClick={() => void exportAs('csv', true)}>
            CSV (ha tab)
          </button>
          <button type="button" className="btn btn-neutral" disabled={busy || !report} onClick={() => void exportAs('pdf', true)}>
            PDF (ha tab)
          </button>
          <select className="settings-input" value={printTo} onChange={(e) => setPrintTo(e.target.value as typeof printTo)}>
            <option value="printer">Printer</option>
            <option value="dot_matrix">Dot matrix</option>
          </select>
          <select className="settings-input" value={layout} onChange={(e) => setLayout(e.target.value as typeof layout)}>
            <option value="landscape">A4 aadva</option>
            <option value="portrait">A4 ubha</option>
          </select>
          <button type="button" className="btn btn-neutral" disabled={busy || !report} onClick={() => void exportAs('print', true)}>
            Print (ha tab)
          </button>
          <button type="button" className="btn btn-neutral" onClick={onClose}>
            Band (Esc)
          </button>
        </div>
      </div>
    </div>
  )
  return createPortal(node, document.body)
}
