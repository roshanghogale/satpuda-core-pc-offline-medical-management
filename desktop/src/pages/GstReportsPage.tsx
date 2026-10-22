/** GST Reports screen (main window, key 9 -- owner, 22 Oct 2026): every GST report the way
 *  Marg lists them (A..W) plus GSTR-1 B2B / B2CL / documents, each a table in the
 *  government's column order with its totals; Excel / CSV / PDF / Print of the report on
 *  screen, the GSTR-1 JSON for the portal, Tally Prime and the CA zip.
 *
 *  Every figure comes from the engine (core/gst_screen.py on core/gst_reports.py), which
 *  takes each bill's tax out the way the printed bill does -- the tables add up to the bills.
 *  The old GST popup's tools (summary, HSN bhara, filed / julvni) are here as well.
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { getApiBase } from '../api'
import { isTypingTarget } from '../keyboard'
import { saveFileAndShow } from '../savedFiles'
import { FiledTab, HsnFixTab, SummaryTab } from './GstReportsTools'
import { GST_HELP } from './gstReportsHelp'
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
  letter: string
  title: string
  form: string
  partial: string
  period: { from: string; to: string }
  shop: { name: string; gstin: string; state: string; state_name: string; gst_enabled: boolean }
  sections: Section[]
  notes: string[]
  counts: { bills: number; credit_notes: number; purchases: number; purchase_returns: number }
  checks: number
}
type Item = { key: string; letter: string; group: string; title: string; form: string; partial: string }

/** The old popup's tools, shown by the screen itself (no table from the engine). */
const TOOLS: Item[] = [
  { key: 'summary', letter: '', group: 'Month', title: 'Saransh: GST to pay this month', form: 'Summary', partial: '' },
  { key: 'hsnfix', letter: '', group: 'Tools', title: 'HSN bhara (medicines without HSN)', form: 'HSN', partial: '' },
  { key: 'filed', letter: '', group: 'Tools', title: 'Filed / julvni (saved figures)', form: 'Filed', partial: '' },
]
const TOOL_KEYS = new Set(TOOLS.map((t) => t.key))
const GROUPS = ['Month', 'GSTR-1', 'GSTR-2', 'GSTR-3B', 'Composition', 'Annual', 'Tools']
const COMPOSITION = new Set(['composition_sale', 'composition_purchase', 'gstr4'])
const ANNUAL = new Set(['gstr9', 'gstr4'])
const GSTR1 = new Set(['upload', 'b2b', 'sale', 'sale_return', 'b2cs', 'b2cl', 'hsn_sale', 'cdnr',
  'hsn_sale_return', 'hsn_sale_net', 'docs', 'gstr1_club'])

const pad = (n: number) => String(n).padStart(2, '0')
const iso = (d: Date) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`

function monthBounds(ym: string) {
  const [y, m] = ym.split('-').map(Number)
  return { from: iso(new Date(y, m - 1, 1)), to: iso(new Date(y, m, 0)) }
}

/** Indian quarters: Q1 Apr-Jun ... Q4 Jan-Mar of the next calendar year. */
function quarterBounds(fyStart: number, q: number) {
  const startMonth = [3, 6, 9, 0][q - 1]
  const year = q === 4 ? fyStart + 1 : fyStart
  return { from: iso(new Date(year, startMonth, 1)), to: iso(new Date(year, startMonth + 3, 0)) }
}

function lastMonth() {
  const d = new Date()
  d.setDate(1)
  d.setMonth(d.getMonth() - 1)
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}`
}

function currentFy() {
  const d = new Date()
  return d.getMonth() >= 3 ? d.getFullYear() : d.getFullYear() - 1
}

const dmy = (s: string) => (s && s.length >= 10 ? `${s.slice(8, 10)}-${s.slice(5, 7)}-${s.slice(0, 4)}` : s)

/** Counts, rates and quantities are shown as they are; money with two decimals. */
const PLAIN = /^(Rate|Records|Bills|Credit Notes|Total Quantity|Total Number|Cancelled|Step|Rate of tax)/

function show(v: Cell, col: string) {
  if (typeof v !== 'number') return String(v ?? '')
  if (PLAIN.test(col)) return String(v)
  return v.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

function Table({ s }: { s: Section }) {
  const num = new Set(s.num_cols)
  const wide = (c: string) => /^(What|Detail|Nature of Supplies|Details|Description|Hisob)$/.test(c)
  if (!s.rows.length) return <div className="gst-empty">Ya kalavadhit kahi nahi (nil)</div>
  return (
    <div className="gst-table-wrap">
      <table className="sat-table">
        <thead>
          {s.header ? (
            s.header.map((h, i) => (
              <tr key={i} className={i ? 'gst-h2' : ''}>
                {h.map((c, j) => (
                  <th key={j} className={num.has(j) ? 'num' : ''}>{c}</th>
                ))}
              </tr>
            ))
          ) : (
            <tr>
              {s.columns.map((c, j) => (
                <th key={j} className={num.has(j) ? 'num' : ''}>{c}</th>
              ))}
            </tr>
          )}
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
  const [key, setKey] = useState('upload')
  const [mode, setMode] = useState<'month' | 'quarter' | 'fy' | 'custom'>('month')
  const [month, setMonth] = useState(lastMonth())
  const [fy, setFy] = useState(currentFy())
  const [quarter, setQuarter] = useState(1)
  const [from, setFrom] = useState(monthBounds(lastMonth()).from)
  const [to, setTo] = useState(monthBounds(lastMonth()).to)
  const [compRate, setCompRate] = useState(1)
  const [report, setReport] = useState<Report | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [msg, setMsg] = useState('')
  const [printTo, setPrintTo] = useState<'printer' | 'dot_matrix'>('printer')
  const [layout, setLayout] = useState<'landscape' | 'portrait'>('landscape')
  const [help, setHelp] = useState(false)
  const [nonce, setNonce] = useState(0)

  const range = useMemo(() => {
    if (mode === 'month') return monthBounds(month)
    if (mode === 'quarter') return quarterBounds(fy, quarter)
    if (mode === 'fy') return { from: `${fy}-04-01`, to: `${fy + 1}-03-31` }
    return { from, to }
  }, [mode, month, fy, quarter, from, to])

  useEffect(() => {
    if (!active || items.length) return
    fetch(`${getApiBase()}/api/reports/gst/catalogue`)
      .then((r) => r.json())
      .then((d) => {
        if (d.ok === false) throw new Error(d.error || 'GST reports list milali nahi')
        setItems([...(d.reports as Item[]), ...TOOLS])
      })
      .catch((e) => setErr(e instanceof Error ? e.message : String(e)))
  }, [active, items.length])

  const all = items.length ? items : TOOLS
  const current = all.find((i) => i.key === key)
  const isTool = TOOL_KEYS.has(key)

  const pick = useCallback(
    (k: string) => {
      setKey(k)
      setMsg('')
      setErr('')
      // An annual return is for a financial year: pick it the first time one is opened.
      if (ANNUAL.has(k) && mode === 'month') setMode('fy')
    },
    [mode],
  )

  useEffect(() => {
    if (!active || isTool) return
    let alive = true
    setBusy(true)
    setErr('')
    const q = new URLSearchParams({ report: key, from: range.from, to: range.to, comp_rate: String(compRate) })
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
  }, [active, isTool, key, range.from, range.to, compRate, nonce])

  // Marg's letters: A..W open a report (not while typing, not over a dialog).
  useEffect(() => {
    if (!active) return
    const onKey = (e: KeyboardEvent) => {
      if (e.ctrlKey || e.altKey || e.metaKey || isTypingTarget(e.target)) return
      if (document.querySelector('.modal-backdrop')) return
      const k = e.key.length === 1 ? e.key.toUpperCase() : ''
      const hit = k && all.find((i) => i.letter === k)
      if (hit) {
        e.preventDefault()
        pick(hit.key)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [active, all, pick])

  async function save(url: string, body: Record<string, unknown>) {
    setBusy(true)
    setErr('')
    setMsg('')
    try {
      const res = await fetch(`${getApiBase()}${url}`, {
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
        setMsg(saved ? `${saved.file_name || d.filename} save zali: ${saved.folder || ''}` : `${d.filename} save zali`)
      } else {
        setMsg(d.message || 'Print la pathavle')
      }
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const exportAs = (format: 'xlsx' | 'csv' | 'pdf' | 'print' | 'json') => {
    const body: Record<string, unknown> = { report: key, from: range.from, to: range.to, comp_rate: compRate,
      page_layout: layout }
    if (format === 'print') body.print_to = printTo
    else body.format = format
    void save('/api/reports/gst/screen/export', body)
  }
  const oldExport = (format: 'tally' | 'ca_zip') =>
    void save('/api/reports/gst/export', { from: range.from, to: range.to, format })

  const shop = report?.shop
  const grouped = GROUPS.map((g) => ({ g, list: all.filter((i) => i.group === g) })).filter((x) => x.list.length)

  return (
    <div className="gst-page" data-nav-chain="gst">
      <div className="gst-top">
        <h1>GST Reports</h1>
        <select className="settings-input" value={mode} onChange={(e) => setMode(e.target.value as typeof mode)}
          aria-label="Kalavadhi">
          <option value="month">Mahina</option>
          <option value="quarter">Timahi (QRMP)</option>
          <option value="fy">Financial year</option>
          <option value="custom">Tarikh pasun – paryant</option>
        </select>
        {mode === 'month' ? (
          <input className="settings-input" type="month" value={month} onChange={(e) => setMonth(e.target.value)} />
        ) : null}
        {mode === 'quarter' || mode === 'fy' ? (
          <select className="settings-input" value={fy} onChange={(e) => setFy(Number(e.target.value))}>
            {[0, 1, 2, 3].map((k) => (
              <option key={k} value={currentFy() - k}>
                FY {currentFy() - k}-{String(currentFy() - k + 1).slice(2)}
              </option>
            ))}
          </select>
        ) : null}
        {mode === 'quarter' ? (
          <select className="settings-input" value={quarter} onChange={(e) => setQuarter(Number(e.target.value))}>
            <option value={1}>Q1 (Apr–Jun)</option>
            <option value={2}>Q2 (Jul–Sep)</option>
            <option value={3}>Q3 (Oct–Dec)</option>
            <option value={4}>Q4 (Jan–Mar)</option>
          </select>
        ) : null}
        {mode === 'custom' ? (
          <>
            <input className="settings-input" type="date" value={from} onChange={(e) => setFrom(e.target.value)} />
            <input className="settings-input" type="date" value={to} onChange={(e) => setTo(e.target.value)} />
          </>
        ) : null}
        {COMPOSITION.has(key) ? (
          <select className="settings-input" value={compRate} title="Composition rate (traders 1%)"
            onChange={(e) => setCompRate(Number(e.target.value))}>
            <option value={1}>Composition 1% (trader)</option>
            <option value={2}>Composition 2%</option>
            <option value={5}>Composition 5%</option>
            <option value={6}>Composition 6%</option>
          </select>
        ) : null}
        <span className="vl-dim">
          {dmy(range.from)} → {dmy(range.to)}
          {report && !isTool
            ? ` · ${report.counts.bills} bills · ${report.counts.credit_notes} returns · ${report.counts.purchases} purchases`
            : ''}
        </span>
        <button type="button" className="btn btn-neutral" disabled={busy} onClick={() => setNonce((n) => n + 1)}>
          {busy ? 'Mojat aahe…' : 'Punha moja'}
        </button>
        <button type="button" className="btn btn-neutral" onClick={() => setHelp((h) => !h)}>
          {help ? 'Madat band' : 'Madat (?)'}
        </button>
        <div className="gst-shop">
          {shop?.name ? <div>{shop.name}</div> : null}
          {shop?.gstin ? (
            <div>
              GSTIN <strong>{shop.gstin}</strong> {shop.state_name ? `(${shop.state_name})` : ''}
            </div>
          ) : shop ? (
            <div className="vl-dim">GSTIN bharlela nahi (Settings → Pharmacy)</div>
          ) : null}
        </div>
      </div>

      {help ? (
        <div className="settings-hint" style={{ padding: 10, border: '1px solid var(--border)', borderRadius: 6,
          maxHeight: '30vh', overflow: 'auto' }}>
          {GST_HELP.map((sec) => (
            <div key={sec.title} style={{ marginBottom: 8 }}>
              <strong>{sec.title}</strong>
              <ul style={{ margin: '4px 0 0 18px' }}>
                {sec.lines.map((l) => <li key={l}>{l}</li>)}
              </ul>
            </div>
          ))}
        </div>
      ) : null}

      <div className="gst-main">
        <nav className="gst-list" aria-label="GST reports">
          {grouped.map(({ g, list }) => (
            <div key={g}>
              <div className="gst-group">{g}</div>
              {list.map((i) => (
                <button key={i.key} type="button" className={`gst-item${i.key === key ? ' active' : ''}`}
                  title={`${i.form}${i.letter ? ` (key ${i.letter})` : ''}`} onClick={() => pick(i.key)}>
                  <span className="gst-letter">{i.letter}</span>
                  <span>{i.title}</span>
                </button>
              ))}
            </div>
          ))}
        </nav>

        <section className="gst-body">
          <div className="gst-head">
            {current?.letter ? <span className="gst-letter">{current.letter}</span> : null}
            <h2>{current?.title || 'GST Reports'}</h2>
            {current?.form ? <span className="gst-form">{current.form}</span> : null}
            {!isTool && current ? (
              <span className={`gst-badge${current.partial ? ' partial' : ''}`} title={current.partial || undefined}>
                {current.partial ? 'Partial' : 'Complete'}
              </span>
            ) : null}
            {!isTool ? (
              <div className="gst-actions">
                <button type="button" className="btn btn-primary" disabled={busy || !report} onClick={() => exportAs('xlsx')}>
                  Excel
                </button>
                <button type="button" className="btn btn-neutral" disabled={busy || !report} onClick={() => exportAs('csv')}>
                  CSV
                </button>
                <button type="button" className="btn btn-neutral" disabled={busy || !report} onClick={() => exportAs('pdf')}>
                  PDF
                </button>
                <select className="settings-input" value={printTo} aria-label="Printer"
                  onChange={(e) => setPrintTo(e.target.value as typeof printTo)}>
                  <option value="printer">Printer</option>
                  <option value="dot_matrix">Dot matrix</option>
                </select>
                <select className="settings-input" value={layout} aria-label="Page"
                  onChange={(e) => setLayout(e.target.value as typeof layout)}>
                  <option value="landscape">A4 aadva</option>
                  <option value="portrait">A4 ubha</option>
                </select>
                <button type="button" className="btn btn-neutral" disabled={busy || !report} onClick={() => exportAs('print')}>
                  Print
                </button>
                {GSTR1.has(key) ? (
                  <button type="button" className="btn btn-neutral" disabled={busy || !report}
                    title="GSTR-1 JSON (b2b, b2cl, b2cs, nil, cdnr, cdnur, hsn, doc_issue): portal / offline tool madhe upload"
                    onClick={() => exportAs('json')}>
                    GSTR-1 JSON
                  </button>
                ) : null}
                {key === 'upload' || key === 'gstr3b' ? (
                  <>
                    <button type="button" className="btn btn-neutral" disabled={busy}
                      title="Excel, PDF, GSTR-1 JSON, saransh ani Tally Prime files - ek zip, CA la pathvayla"
                      onClick={() => oldExport('ca_zip')}>
                      CA sathi zip
                    </button>
                    <button type="button" className="btn btn-neutral" disabled={busy}
                      title="Tally Prime: aadhi ledgers file, mag vouchers file import kara"
                      onClick={() => oldExport('tally')}>
                      Tally Prime
                    </button>
                  </>
                ) : null}
              </div>
            ) : null}
          </div>
          {err ? <div className="vb-warn gst-msg">{err}</div> : null}
          {msg ? <div className="settings-hint gst-msg">{msg}</div> : null}
          {current?.partial && !isTool ? (
            <div className="settings-hint gst-msg">Partial: {current.partial}</div>
          ) : null}
          {report && report.checks && !isTool && key !== 'checks' ? (
            <div className="vb-warn gst-msg" style={{ cursor: 'pointer' }} onClick={() => pick('checks')}>
              {report.checks} goshti tapasa (GST % bill var navhta, HSN nahi …) — Checks paha
            </div>
          ) : null}
          <div className="gst-scroll">
            {key === 'summary' ? <SummaryTab from={range.from} to={range.to} onGo={(t) => pick(t)} /> : null}
            {key === 'hsnfix' ? (
              <HsnFixTab from={range.from} to={range.to} onSaved={() => setNonce((n) => n + 1)} />
            ) : null}
            {key === 'filed' ? (
              <FiledTab from={range.from} to={range.to} onMsg={(m, bad) => (bad ? setErr(m) : setMsg(m))} />
            ) : null}
            {!isTool && report && report.key === key ? (
              <>
                {report.notes.length ? (
                  <ul className="gst-notes">
                    {report.notes.map((n) => <li key={n}>{n}</li>)}
                  </ul>
                ) : null}
                {report.sections.map((s, i) => (
                  <div key={`${s.title}-${i}`} className="gst-section">
                    {report.sections.length > 1 || !s.marg ? <h3>{s.title}</h3> : null}
                    <Table s={s} />
                  </div>
                ))}
              </>
            ) : null}
            {!isTool && !report && busy ? <div className="gst-empty">Report banat aahe…</div> : null}
          </div>
        </section>
      </div>
    </div>
  )
}
