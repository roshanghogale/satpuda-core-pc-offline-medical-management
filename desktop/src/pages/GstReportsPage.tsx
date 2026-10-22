/** Settings > GST Reports (owner, 22 Oct 2026: "in the settings ... simple, not that much
 *  options"). A short list in plain words -- the sale / purchase registers in Marg's GST upload
 *  layout, GSTR-1 and GSTR-3B summaries, HSN, and for a composition dealer (Bill of Supply,
 *  composition) the composition summary instead of GSTR-1 / 3B. A month (or custom dates),
 *  and Excel / PDF / Print.
 *
 *  Every figure comes from the engine (core/gst_screen.py on core/gst_reports.py), which
 *  takes each bill's tax out the way the printed bill does -- the tables add up to the bills.
 */
import { useEffect, useMemo, useState } from 'react'
import { getApiBase } from '../api'
import { saveFileAndShow } from '../savedFiles'
import '../styles/gst-reports.css'

type Cell = string | number
type Section = {
  title: string
  columns: string[]
  rows: Cell[][]
  total: Cell[] | null
  num_cols: number[]
  header: string[][] | null
  marg: boolean
}
type Report = {
  ok?: boolean
  error?: string
  key: string
  title: string
  period: { from: string; to: string }
  shop: { name: string; gstin: string; state: string; state_name: string; gst_enabled: boolean }
  sections: Section[]
  notes: string[]
  counts: { bills: number; credit_notes: number; purchases: number; purchase_returns: number }
  checks: number
}
type Item = { key: string; title: string; hint: string }

const pad = (n: number) => String(n).padStart(2, '0')
const iso = (d: Date) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`

function monthBounds(ym: string) {
  const [y, m] = ym.split('-').map(Number)
  return { from: iso(new Date(y, m - 1, 1)), to: iso(new Date(y, m, 0)) }
}

function lastMonth() {
  const d = new Date()
  d.setDate(1)
  d.setMonth(d.getMonth() - 1)
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}`
}

const dmy = (s: string) => (s && s.length >= 10 ? `${s.slice(8, 10)}-${s.slice(5, 7)}-${s.slice(0, 4)}` : s)

/** Counts, rates and quantities are shown as they are; money with two decimals. */
const PLAIN = /^(Rate|Records|Bills|Credit Notes|Total Quantity|Total Number|Cancelled)/

function show(v: Cell, col: string) {
  if (typeof v !== 'number') return String(v ?? '')
  if (PLAIN.test(col)) return String(v)
  return v.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

function Table({ s }: { s: Section }) {
  const num = new Set(s.num_cols)
  const wide = (c: string) => /^(What|Detail|Nature of Supplies|Details|Description)$/.test(c)
  if (!s.rows.length) return <div className="gst-empty">Nothing in this period.</div>
  return (
    <div className="gst-table-wrap">
      <table className="sat-table">
        <thead>
          {(s.header || [s.columns]).map((h, i) => (
            <tr key={i} className={i ? 'gst-h2' : ''}>
              {h.map((c, j) => (
                <th key={j} className={num.has(j) ? 'num' : ''}>{c}</th>
              ))}
            </tr>
          ))}
        </thead>
        <tbody>
          {s.rows.map((r, i) => (
            <tr key={i}>
              {r.map((v, j) => (
                <td key={j} className={num.has(j) ? 'num' : wide(s.columns[j]) ? 'gst-wrap' : ''}>
                  {show(v, s.columns[j] || '')}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
        {s.total ? (
          <tfoot>
            <tr>
              {s.total.map((v, j) => (
                <th key={j} className={num.has(j) ? 'num' : ''}>
                  {j === 0 && v === '' ? 'Total' : show(v, s.columns[j] || '')}
                </th>
              ))}
            </tr>
          </tfoot>
        ) : null}
      </table>
    </div>
  )
}

export function GstReportsPage({ active }: { active: boolean }) {
  const [items, setItems] = useState<Item[]>([])
  const [key, setKey] = useState('sale')
  const [checking, setChecking] = useState(false)
  const [custom, setCustom] = useState(false)
  const [month, setMonth] = useState(lastMonth())
  const [from, setFrom] = useState(monthBounds(lastMonth()).from)
  const [to, setTo] = useState(monthBounds(lastMonth()).to)
  const [report, setReport] = useState<Report | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [msg, setMsg] = useState('')
  const [nonce, setNonce] = useState(0)

  const range = useMemo(() => (custom ? { from, to } : monthBounds(month)), [custom, month, from, to])
  const shownKey = checking ? 'check_bills' : key

  // The list depends on the bill heading (composition or not): read it each time the tab opens.
  useEffect(() => {
    if (!active) return
    fetch(`${getApiBase()}/api/reports/gst/catalogue`)
      .then((r) => r.json())
      .then((d) => {
        if (d.ok === false) throw new Error(d.error || 'Could not read the GST report list')
        const list = d.reports as Item[]
        setItems(list)
        setKey((k) => (list.some((i) => i.key === k) ? k : list[0]?.key || 'sale'))
      })
      .catch((e) => setErr(e instanceof Error ? e.message : String(e)))
  }, [active])

  useEffect(() => {
    if (!active) return
    let alive = true
    setBusy(true)
    setErr('')
    const q = new URLSearchParams({ report: shownKey, from: range.from, to: range.to })
    fetch(`${getApiBase()}/api/reports/gst/screen?${q}`)
      .then(async (res) => {
        const d = (await res.json().catch(() => ({}))) as Report
        if (!res.ok || d.ok === false) throw new Error(d.error || `HTTP ${res.status}`)
        if (alive) setReport(d)
      })
      .catch((e) => {
        if (alive) {
          setReport(null)
          setErr(e instanceof Error ? e.message : String(e))
        }
      })
      .finally(() => {
        if (alive) setBusy(false)
      })
    return () => {
      alive = false
    }
  }, [active, shownKey, range.from, range.to, nonce])

  async function exportAs(format: 'xlsx' | 'pdf' | 'print' | 'json') {
    setBusy(true)
    setErr('')
    setMsg('')
    try {
      const body: Record<string, unknown> = { report: shownKey, from: range.from, to: range.to,
        page_layout: 'landscape' }
      if (format === 'print') body.print_to = 'printer'
      else body.format = format
      const res = await fetch(`${getApiBase()}/api/reports/gst/screen/export`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      const d = (await res.json().catch(() => ({}))) as {
        ok?: boolean; error?: string; message?: string; filename?: string; mime?: string; content_base64?: string
      }
      if (!res.ok || d.ok === false) throw new Error(d.error || `HTTP ${res.status}`)
      if (d.content_base64 && d.filename) {
        const saved = await saveFileAndShow(d.filename, d.mime || 'application/octet-stream', d.content_base64)
        setMsg(saved ? `Saved ${saved.file_name || d.filename} in ${saved.folder || ''}` : `Saved ${d.filename}`)
      } else {
        setMsg(d.message || 'Sent to the printer')
      }
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const current = items.find((i) => i.key === key)
  const shop = report?.shop
  const ready = Boolean(report) && report?.key === shownKey

  return (
    <div className="gst-page">
      <div className="gst-top">
        {custom ? (
          <>
            <input className="settings-input" type="date" value={from} aria-label="From"
              onChange={(e) => setFrom(e.target.value)} />
            <input className="settings-input" type="date" value={to} aria-label="To"
              onChange={(e) => setTo(e.target.value)} />
          </>
        ) : (
          <input className="settings-input" type="month" value={month} aria-label="Month"
            onChange={(e) => setMonth(e.target.value)} />
        )}
        <button type="button" className="gst-link" onClick={() => setCustom((c) => !c)}>
          {custom ? 'Whole month' : 'Custom dates'}
        </button>
        <span className="vl-dim">
          {dmy(range.from)} – {dmy(range.to)}
          {report ? ` · ${report.counts.bills} bills · ${report.counts.purchases} purchases` : ''}
        </span>
        <button type="button" className="btn btn-neutral" disabled={busy} onClick={() => setNonce((n) => n + 1)}>
          {busy ? 'Loading…' : 'Refresh'}
        </button>
        <div className="gst-shop">
          {shop?.name ? <div>{shop.name}</div> : null}
          {shop?.gstin ? (
            <div>
              GSTIN <strong>{shop.gstin}</strong> {shop.state_name ? `(${shop.state_name})` : ''}
            </div>
          ) : shop ? (
            <div className="vl-dim">No GSTIN yet (Settings → Pharmacy)</div>
          ) : null}
        </div>
      </div>

      <div className="gst-main">
        <nav className="gst-list" aria-label="GST reports">
          {items.map((i) => (
            <button key={i.key} type="button" className={`gst-item${i.key === key && !checking ? ' active' : ''}`}
              onClick={() => {
                setKey(i.key)
                setChecking(false)
                setMsg('')
              }}>
              <span>{i.title}</span>
              <span className="gst-hint">{i.hint}</span>
            </button>
          ))}
        </nav>

        <section className="gst-body">
          <div className="gst-head">
            <h2>{checking ? 'Bills to check' : current?.title || 'GST Reports'}</h2>
            {checking ? (
              <button type="button" className="gst-link" onClick={() => setChecking(false)}>
                ← Back to {current?.title}
              </button>
            ) : report && report.checks && ready ? (
              <button type="button" className="gst-link" onClick={() => setChecking(true)}>
                Check bills ({report.checks})
              </button>
            ) : null}
            <div className="gst-actions">
              <button type="button" className="btn btn-primary" disabled={busy || !ready} onClick={() => void exportAs('xlsx')}>
                Excel
              </button>
              <button type="button" className="btn btn-neutral" disabled={busy || !ready} onClick={() => void exportAs('pdf')}>
                PDF
              </button>
              <button type="button" className="btn btn-neutral" disabled={busy || !ready} onClick={() => void exportAs('print')}>
                Print
              </button>
              {shownKey === 'gstr1' ? (
                <button type="button" className="btn btn-neutral" disabled={busy || !ready}
                  title="The file the GST portal / offline tool takes for GSTR-1"
                  onClick={() => void exportAs('json')}>
                  GSTR-1 JSON
                </button>
              ) : null}
            </div>
          </div>
          {err ? <div className="vb-warn gst-msg">{err}</div> : null}
          {msg ? <div className="settings-hint gst-msg">{msg}</div> : null}
          <div className="gst-scroll">
            {ready && report ? (
              <>
                {report.sections.map((s, i) => (
                  <div key={`${s.title}-${i}`} className="gst-section">
                    {report.sections.length > 1 ? <h3>{s.title}</h3> : null}
                    <Table s={s} />
                  </div>
                ))}
                {report.notes.map((n) => (
                  <div key={n} className="gst-note">{n}</div>
                ))}
              </>
            ) : busy ? (
              <div className="gst-empty">Loading…</div>
            ) : null}
          </div>
        </section>
      </div>
    </div>
  )
}
