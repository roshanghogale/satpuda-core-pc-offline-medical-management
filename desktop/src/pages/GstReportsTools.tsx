/** The GST screen's tools (core/gst_extras.py, core/gst_reports.py): the one-page summary
 *  with the month's GST to pay, the medicines without HSN, and the filed figures compared
 *  with today. They were tabs of the old GST Reports popup; the GST Reports screen
 *  (GstReportsPage.tsx) shows them now.
 */
import { useEffect, useState } from 'react'
import { getApiBase } from '../api'

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
export function SummaryTab({ from, to, onGo }: { from: string; to: string; onGo: (tab: string) => void }) {
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
export function HsnFixTab({ from, to, onSaved }: { from: string; to: string; onSaved: () => void }) {
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
export function FiledTab({ from, to, onMsg }: { from: string; to: string; onMsg: (m: string, err?: boolean) => void }) {
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

const money = (v: unknown) =>
  typeof v === 'number' ? v.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : String(v ?? '')
