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
import { GST_HELP } from './gstReportsHelp'

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
  { key: 'sum', label: 'Saransh (sopa)', tables: [] },
  { key: 'hsnfix', label: 'HSN bhara', tables: [] },
  { key: '3b', label: 'GSTR-3B + julvni', tables: ['3B Summary', 'Tally'] },
  { key: 'reg', label: 'Sales register', tables: ['Sales GST Register'] },
  { key: 'b2b', label: 'B2B', tables: ['b2b'] },
  { key: 'b2c', label: 'B2CS / B2CL', tables: ['b2cs', 'b2cl'] },
  { key: 'cdn', label: 'Credit notes', tables: ['cdnr', 'cdnur'] },
  { key: 'hsn', label: 'HSN', tables: ['hsn(b2b)', 'hsn(b2c)'] },
  { key: 'docs', label: 'Documents', tables: ['docs'] },
  { key: 'itc', label: 'Purchase ITC', tables: ['Purchase ITC Register', 'Purchase Rate-wise'] },
  { key: 'checks', label: 'Checks', tables: ['Checks'] },
  { key: 'filed', label: 'Filed / julvni', tables: [] },
]

type Filed = { key: string; from: string; to: string; saved_at: string; note: string; bills: number; taxable: number; tax: number }
type Compared = {
  filed: Filed
  same: boolean
  tables: { table: string; field: string; filed: number; now: number; difference: number }[]
  bills: { bill_no: string; what: string; filed_taxable: number; filed_tax: number; now_taxable: number; now_tax: number }[]
}

async function filedAction(body: Record<string, unknown>) {
  const res = await fetch(`${getApiBase()}/api/reports/gst/filed`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  const d = await res.json().catch(() => ({}))
  if (!res.ok || d.ok === false) throw new Error(d.error || `HTTP ${res.status}`)
  return d
}

type Heads = { igst: number; cgst: number; sgst: number }
type MonthRow = {
  month: string; from: string; to: string; tax_on_sales: number; itc: number; brought_forward: number
  cash_to_pay: number; cash: Heads; carried_forward: number; carried: Heads
}
type Summary = {
  bills: number; sales_total: number; returns: number; returns_total: number; taxable_sales: number; nil_sales: number
  tax_on_sales: Heads; tax_on_sales_total: number; purchases: number; purchase_value: number; itc: Heads
  itc_total: number; no_itc_tax: number; cash_to_pay: number; carried_forward: number; months: MonthRow[]
  medicines_without_hsn: number; checks: number
}

const rs = (n: number) =>
  `₹${(Number(n) || 0).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`

function BigFigure({ label, value, note, strong }: { label: string; value: string; note?: string; strong?: boolean }) {
  return (
    <div className="panel" style={{ padding: '10px 14px', minWidth: 170, flex: '1 1 170px' }}>
      <div className="vl-dim" style={{ fontSize: 12 }}>{label}</div>
      <div style={{ fontSize: strong ? 26 : 20, fontWeight: 700, marginTop: 2 }}>{value}</div>
      {note ? <div className="vl-dim" style={{ fontSize: 11, marginTop: 2 }}>{note}</div> : null}
    </div>
  )
}

/** The period on one page: what was sold, the GST on it, the ITC, and what is left to pay. */
function SummaryTab({ from, to, onGo }: { from: string; to: string; onGo: (tab: string) => void }) {
  const [s, setS] = useState<Summary | null>(null)
  const [err, setErr] = useState('')
  useEffect(() => {
    let alive = true
    setS(null)
    setErr('')
    fetch(`${getApiBase()}/api/reports/gst/summary?${new URLSearchParams({ from, to })}`)
      .then((r) => r.json())
      .then((d) => {
        if (!alive) return
        if (d.ok === false) setErr(d.error || 'Saransh milala nahi')
        else setS(d as Summary)
      })
      .catch((e) => {
        if (alive) setErr(String(e))
      })
    return () => {
      alive = false
    }
  }, [from, to])
  if (err) return <div className="vb-warn" style={{ marginTop: 10 }}>{err}</div>
  if (!s) return <div className="vl-dim" style={{ marginTop: 10 }}>Saransh mojat aahe…</div>
  const heads = (h: Heads) =>
    `CGST ${rs(h.cgst)} · SGST ${rs(h.sgst)}${h.igst ? ` · IGST ${rs(h.igst)}` : ''}`
  return (
    <div style={{ marginTop: 10 }}>
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
        <BigFigure
          label="Bharaycha GST (cash)"
          value={rs(s.cash_to_pay)}
          strong
          note={s.cash_to_pay > 0 ? 'GSTR-3B sobat bharayche' : 'ya kalavadhit kahi bharayche nahi'}
        />
        <BigFigure label="Pudhchya mahinyat jaanara ITC" value={rs(s.carried_forward)} note="urlela input credit" />
        <BigFigure label="Vikri var GST" value={rs(s.tax_on_sales_total)} note={heads(s.tax_on_sales)} />
        <BigFigure label="ITC (kharedi varcha GST)" value={rs(s.itc_total)} note={heads(s.itc)} />
      </div>
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 8 }}>
        <BigFigure
          label="Vikri"
          value={rs(s.sales_total)}
          note={`${s.bills} bills · returns ${rs(s.returns_total)} (${s.returns})`}
        />
        <BigFigure label="Karpaatra vikri (taxable)" value={rs(s.taxable_sales)} note={`0% vikri ${rs(s.nil_sales)}`} />
        <BigFigure
          label="Kharedi"
          value={rs(s.purchase_value)}
          note={`${s.purchases} bills${s.no_itc_tax ? ` · GSTIN nasleli kharedi GST ${rs(s.no_itc_tax)} (ITC nahi)` : ''}`}
        />
      </div>
      {s.medicines_without_hsn ? (
        <div className="vb-warn" style={{ marginTop: 8, cursor: 'pointer' }} onClick={() => onGo('hsnfix')}>
          {s.medicines_without_hsn} aushadhanna HSN nahi — "HSN bhara" tab madhe ekdach bhara (june bills che report
          pan sudhartil)
        </div>
      ) : null}
      <h3 style={{ margin: '12px 0 6px' }}>Mahina-dar GST bharna (1 April pasun)</h3>
      <div className="table-scroll" style={{ maxHeight: '34vh' }}>
        <table className="sat-table">
          <thead>
            <tr>
              <th>Mahina</th>
              <th className="num">Vikri var GST</th>
              <th className="num">ITC</th>
              <th className="num">Aadhicha ITC</th>
              <th className="num">Bharayche (cash)</th>
              <th className="num">Pudhe jaanara ITC</th>
            </tr>
          </thead>
          <tbody>
            {s.months.map((m) => (
              <tr key={m.month}>
                <td>{m.month}</td>
                <td className="num">{rs(m.tax_on_sales)}</td>
                <td className="num">{rs(m.itc)}</td>
                <td className="num">{rs(m.brought_forward)}</td>
                <td className="num" style={{ fontWeight: 700 }}>{rs(m.cash_to_pay)}</td>
                <td className="num">{rs(m.carried_forward)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="vl-dim" style={{ fontSize: 11, marginTop: 6 }}>
        ITC kayadyachya kramane vaparla: IGST credit aadhi IGST, mag CGST / SGST; CGST credit CGST ani IGST var; SGST
        credit SGST ani IGST var. Portal varchya GSTR-2B shi julvun ghya.
      </div>
    </div>
  )
}

type HsnRow = { medicine_id: number; name: string; hsn: string; problem: string; bills: number; last_bill: string; rate: number }

/** Every medicine without a usable HSN, once, with a box to fill it in. */
function HsnFixTab({ from, to, onSaved }: { from: string; to: string; onSaved: () => void }) {
  const [rows, setRows] = useState<HsnRow[] | null>(null)
  const [typed, setTyped] = useState<Record<number, string>>({})
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState('')
  // A failed read is said, never shown as "every medicine has its HSN".
  const [loadErr, setLoadErr] = useState('')
  const load = async () => {
    setRows(null)
    setLoadErr('')
    try {
      const r = await fetch(`${getApiBase()}/api/reports/gst/hsn?${new URLSearchParams({ from, to })}`)
      const d = await r.json()
      if (!r.ok || d.ok === false) throw new Error(d.error || `HTTP ${r.status}`)
      setRows((d.medicines || []) as HsnRow[])
    } catch (e) {
      setLoadErr(e instanceof Error ? e.message : String(e))
    }
  }
  useEffect(() => {
    void load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [from, to])
  const ready = Object.entries(typed).filter(([, v]) => /^(\d{4}|\d{6}|\d{8})$/.test(v.trim()))
  async function save() {
    setBusy(true)
    setNote('')
    try {
      const res = await fetch(`${getApiBase()}/api/reports/gst/hsn`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ items: ready.map(([id, hsn]) => ({ medicine_id: Number(id), hsn: hsn.trim() })) }),
      })
      const d = await res.json().catch(() => ({}))
      setNote(
        d.failed?.length
          ? `${d.saved} save; ${d.failed.length} rahile: ${d.failed[0]?.error || ''}`
          : `${d.saved} save zale`,
      )
      setTyped({})
      // The list first, then the report behind it: both at once ran into each other.
      await load()
      onSaved()
    } catch (e) {
      setNote(String(e))
    } finally {
      setBusy(false)
    }
  }
  if (loadErr) {
    return (
      <div className="vb-warn" style={{ marginTop: 10, cursor: 'pointer' }} onClick={() => void load()}>
        HSN yaadi vachta aali nahi: {loadErr} — punha prayatna karayla ithe click kara
      </div>
    )
  }
  if (!rows) return <div className="vl-dim" style={{ marginTop: 10 }}>Yaadi banat aahe…</div>
  if (!rows.length) {
    return <div className="settings-hint" style={{ marginTop: 10 }}>Ya kalavadhitlya saglya aushadhanna HSN aahe.</div>
  }
  return (
    <div style={{ marginTop: 10 }}>
      <div className="settings-hint">
        {rows.length} aushadhanna HSN nahi. HSN (4, 6 kiva 8 ank) bhara ani Save daba — tya navachya saglya batch la
        lagto, ani june bills che GST report pan sudhartat. Aushadhanna sadharan 3004; kahi devices / cosmetics vegle.
      </div>
      <div className="settings-inline-row" style={{ gap: 6, margin: '6px 0' }}>
        <button type="button" className="btn btn-primary" disabled={busy || !ready.length} onClick={() => void save()}>
          {busy ? 'Save hot aahe…' : `Save (${ready.length})`}
        </button>
        {note ? <span className="vl-dim">{note}</span> : null}
      </div>
      <div className="table-scroll" style={{ maxHeight: '46vh' }}>
        <table className="sat-table">
          <thead>
            <tr>
              <th>Aushadh</th>
              <th>Aatacha HSN</th>
              <th className="num">GST %</th>
              <th className="num">Bills</th>
              <th>Shevatcha bill</th>
              <th>Navin HSN</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.medicine_id || r.name}>
                <td>{r.name}</td>
                <td>
                  {r.hsn || '—'} <span className="vl-dim">({r.problem})</span>
                </td>
                <td className="num">{r.rate}</td>
                <td className="num">{r.bills}</td>
                <td>{r.last_bill}</td>
                <td>
                  <input
                    className="settings-input"
                    style={{ width: 110 }}
                    inputMode="numeric"
                    disabled={!r.medicine_id}
                    value={typed[r.medicine_id] ?? ''}
                    placeholder="3004"
                    onChange={(e) =>
                      setTyped((t) => ({ ...t, [r.medicine_id]: e.target.value.replace(/\D/g, '').slice(0, 8) }))
                    }
                  />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

/** Filed / julvni: keep the figures given to the CA, and later see what changed since. */
function FiledTab({ from, to, onMsg }: { from: string; to: string; onMsg: (m: string, err?: boolean) => void }) {
  const [list, setList] = useState<Filed[]>([])
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [cmp, setCmp] = useState<Compared | null>(null)

  async function load() {
    try {
      const res = await fetch(`${getApiBase()}/api/reports/gst/filed`)
      const d = await res.json()
      if (d.ok === false) throw new Error(d.error)
      setList(d.filed || [])
    } catch (e) {
      onMsg(e instanceof Error ? e.message : String(e), true)
    }
  }
  useEffect(() => {
    void load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  async function act(body: Record<string, unknown>, after?: (d: Record<string, unknown>) => void) {
    setBusy(true)
    try {
      const d = await filedAction(body)
      after?.(d)
      await load()
    } catch (e) {
      onMsg(e instanceof Error ? e.message : String(e), true)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div style={{ marginTop: 10 }}>
      <div className="settings-hint">
        Return file kelyavar (kiva CA la dilyavar) he aakde jatan kara. Nantar "Aaj shi julva" dabla ki file kelyanantar konta
        bill badalla, kadhla kiva ushira nondla te disel — pudhchya return madhe durusti karta yete.
      </div>
      <div className="settings-inline-row" style={{ gap: 8, marginTop: 8 }}>
        <input className="settings-input" placeholder="Note (ux. GSTR-1 file kela, ARN …)" value={note}
          onChange={(e) => setNote(e.target.value)} style={{ minWidth: 280 }} />
        <button type="button" className="btn btn-primary" disabled={busy}
          onClick={() => void act({ action: 'save', from, to, note }, () => { setNote(''); onMsg(`${from} → ${to} che aakde jatan kele`) })}>
          {from} → {to} Filed mhanun jatan kara
        </button>
      </div>
      <table className="sat-table" style={{ marginTop: 10 }}>
        <thead>
          <tr><th>Kalavadhi</th><th>Jatan kele</th><th>Note</th><th className="num">Bills</th><th className="num">Taxable</th><th className="num">Tax</th><th /></tr>
        </thead>
        <tbody>
          {list.map((f) => (
            <tr key={f.key}>
              <td>{f.from} → {f.to}</td>
              <td>{f.saved_at.replace('T', ' ')}</td>
              <td>{f.note}</td>
              <td className="num">{f.bills}</td>
              <td className="num">{money(f.taxable)}</td>
              <td className="num">{money(f.tax)}</td>
              <td>
                <button type="button" className="btn btn-neutral" disabled={busy}
                  onClick={() => void act({ action: 'compare', key: f.key }, (d) => setCmp(d as unknown as Compared))}>
                  Aaj shi julva
                </button>{' '}
                <button type="button" className="btn btn-neutral" disabled={busy}
                  onClick={() => { if (window.confirm('Ha jatan kelela report kadhaycha?')) void act({ action: 'delete', key: f.key }) }}>
                  Kadha
                </button>
              </td>
            </tr>
          ))}
          {!list.length ? <tr><td colSpan={7} className="vl-dim">Ajun kahi jatan kele nahi</td></tr> : null}
        </tbody>
      </table>
      {cmp ? (
        <div style={{ marginTop: 12 }}>
          <h3 style={{ margin: '6px 0' }}>
            {cmp.filed.from} → {cmp.filed.to} (jatan {cmp.filed.saved_at.replace('T', ' ')}) vs aaj
          </h3>
          {cmp.same ? (
            <div className="settings-hint">Julte — file kelyanantar kahich badalle nahi.</div>
          ) : (
            <>
              <table className="sat-table">
                <thead><tr><th>Table</th><th>Field</th><th className="num">Filed</th><th className="num">Aaj</th><th className="num">Pharak</th></tr></thead>
                <tbody>
                  {cmp.tables.map((t, i) => (
                    <tr key={i}><td>{t.table}</td><td>{t.field}</td><td className="num">{money(t.filed)}</td>
                      <td className="num">{money(t.now)}</td><td className="num">{money(t.difference)}</td></tr>
                  ))}
                </tbody>
              </table>
              <table className="sat-table" style={{ marginTop: 8 }}>
                <thead><tr><th>Bill</th><th>Kay zale</th><th className="num">Filed taxable</th><th className="num">Filed tax</th><th className="num">Aaj taxable</th><th className="num">Aaj tax</th></tr></thead>
                <tbody>
                  {cmp.bills.map((b) => (
                    <tr key={b.bill_no + b.what}><td>{b.bill_no}</td><td>{b.what}</td>
                      <td className="num">{money(b.filed_taxable)}</td><td className="num">{money(b.filed_tax)}</td>
                      <td className="num">{money(b.now_taxable)}</td><td className="num">{money(b.now_tax)}</td></tr>
                  ))}
                </tbody>
              </table>
            </>
          )}
        </div>
      ) : null}
    </div>
  )
}

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

const NUMERIC = /^(Rakkam|Rate|Taxable|IGST|CGST|SGST|Cess|Total|Bill Total|Bill Value|Invoice Value|Note Value|Integrated|Central|State\/UT|Taxable \/ Value)/

export function GstReportsDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [mode, setMode] = useState<'month' | 'quarter' | 'custom'>('month')
  const [month, setMonth] = useState(lastMonth())
  const [fy, setFy] = useState(currentFy())
  const [quarter, setQuarter] = useState(1)
  const [from, setFrom] = useState(monthBounds(lastMonth()).from)
  const [to, setTo] = useState(monthBounds(lastMonth()).to)
  const [report, setReport] = useState<Report | null>(null)
  const [tab, setTab] = useState('sum')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [msg, setMsg] = useState('')
  const [printTo, setPrintTo] = useState<'printer' | 'dot_matrix'>('printer')
  const [layout, setLayout] = useState<'portrait' | 'landscape'>('landscape')
  // CSV / PDF / Print: every GST table (what the CA gets) or the tab on screen, as on Android.
  const [scope, setScope] = useState<'all' | 'tab'>('all')
  const [help, setHelp] = useState(false)

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

  async function exportAs(format: 'xlsx' | 'csv' | 'pdf' | 'json' | 'print' | 'tally' | 'ca_zip', tablesOnly: boolean) {
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
            <button type="button" className="btn btn-neutral" onClick={() => setHelp((h) => !h)}>
              {help ? 'Madat band' : 'Madat (?)'}
            </button>
          </div>
          {help ? (
            <div className="settings-hint" style={{ marginTop: 8, padding: 10, border: '1px solid var(--border, #ccd)', borderRadius: 6 }}>
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

          {err ? <div className="vb-warn" style={{ marginTop: 8 }}>{err}</div> : null}
          {msg ? <div className="settings-hint" style={{ marginTop: 8 }}>{msg}</div> : null}
          {report && !report.shop.gstin ? (
            <div className="settings-hint" style={{ marginTop: 8 }}>
              Dukanacha GSTIN bharlela nahi — sagle reports chaltat. GST nondani asel tar Settings → Pharmacy madhe bhara,
              mhanje GSTR-1 JSON var to yeil.
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

          {tab === 'sum' ? <SummaryTab from={range.from} to={range.to} onGo={setTab} /> : null}
          {tab === 'hsnfix' ? <HsnFixTab from={range.from} to={range.to} onSaved={() => void load()} /> : null}
          {tab === 'filed' ? (
            <FiledTab from={range.from} to={range.to} onMsg={(m, bad) => (bad ? setErr(m) : setMsg(m))} />
          ) : null}
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
          <button
            type="button"
            className="btn btn-primary"
            disabled={busy || !report}
            title="Excel, PDF, GSTR-1 JSON, saransh ani Tally Prime files - ek zip, CA la pathvayla"
            onClick={() => void exportAs('ca_zip', false)}
          >
            CA sathi sagle (zip)
          </button>
          <button
            type="button"
            className="btn btn-neutral"
            disabled={busy || !report}
            title="Tally Prime: aadhi ledgers file, mag vouchers file import kara (zip madhe kase te lihile aahe)"
            onClick={() => void exportAs('tally', false)}
          >
            Tally Prime
          </button>
          <button type="button" className="btn btn-neutral" disabled={busy || !report} onClick={() => void exportAs('xlsx', false)}>
            Excel (sagle tables)
          </button>
          <button
            type="button"
            className="btn btn-neutral"
            disabled={busy || !report}
            title="GST portal chya offline tool madhe ughadun tapasa, mag upload kara"
            onClick={() => void exportAs('json', false)}
          >
            GSTR-1 JSON
          </button>
          <select
            className="settings-input"
            value={scope}
            title="CSV, PDF ani Print sathi"
            onChange={(e) => setScope(e.target.value as typeof scope)}
          >
            <option value="all">Sagle GST reports</option>
            <option value="tab">Fakt ha tab ({shown.label})</option>
          </select>
          <button type="button" className="btn btn-neutral" disabled={busy || !report} onClick={() => void exportAs('csv', scope === 'tab')}>
            CSV
          </button>
          <button type="button" className="btn btn-neutral" disabled={busy || !report} onClick={() => void exportAs('pdf', scope === 'tab')}>
            PDF
          </button>
          <select className="settings-input" value={printTo} onChange={(e) => setPrintTo(e.target.value as typeof printTo)}>
            <option value="printer">Printer</option>
            <option value="dot_matrix">Dot matrix</option>
          </select>
          <select className="settings-input" value={layout} onChange={(e) => setLayout(e.target.value as typeof layout)}>
            <option value="landscape">A4 aadva</option>
            <option value="portrait">A4 ubha</option>
          </select>
          <button type="button" className="btn btn-neutral" disabled={busy || !report} onClick={() => void exportAs('print', scope === 'tab')}>
            Print
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
