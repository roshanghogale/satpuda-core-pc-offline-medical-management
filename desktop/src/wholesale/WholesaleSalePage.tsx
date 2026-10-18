/** The Sales screen of a store in wholesale mode: a B2B tax invoice to another medical.
 *
 *  Party must be a customer (its GSTIN, DL numbers, state, rate tier and credit terms are
 *  shown); lines are whole packs + free packs at Rate A / B / C (GST excluded, added on
 *  top), with Disc % and Scheme %. The engine does every sum (core/wholesale_sale.py):
 *  this page only sends what is typed and shows what comes back. Retail billing is the
 *  untouched SalesPage. */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { AppNavigate } from '../App'
import {
  fetchBillPreview,
  fetchSalesBillHint,
  printSalesBill,
  saveBillPdf,
  type MedicineBatch,
} from '../pagesApi'
import { todayLocal } from '../localDate'
import { showSavedFile } from '../savedFiles'
import { BillPreviewDialog } from '../pages/BillPreviewDialog'
import { TwoStepMedicinePicker } from '../pages/TwoStepMedicinePicker'
import { ModernCombo } from '../pages/ModernCombo'
import { ActionBtn, Field, PageRoot, Panel, SelectWrap } from '../pages/pageChrome'
import {
  buildWholesaleLine,
  calcWholesale,
  fetchParty,
  loadWholesale,
  saveWholesale,
  searchParties,
  type PartyInfo,
  type PartyRow,
  type WholesaleCalc,
  type WholesaleLine,
} from './saleApi'

type Line = WholesaleLine & { key: string }

type Form = {
  partyText: string
  partyId: number | null
  billDate: string
  transport: string
  lrNo: string
  ewayNo: string
  paymentMode: 'Due' | 'Cash' | 'Online'
  cash: string
  online: string
  items: Line[]
  editingSaleId: number | null
  editingBillNo: string
}

const NAV = 'wholesale-sale'

function blankForm(): Form {
  return {
    partyText: '',
    partyId: null,
    billDate: todayLocal(),
    transport: '',
    lrNo: '',
    ewayNo: '',
    paymentMode: 'Due',
    cash: '',
    online: '',
    items: [],
    editingSaleId: null,
    editingBillNo: '',
  }
}

function money(v: number | undefined | null) {
  return Number(v || 0).toFixed(2)
}

function num(v: string | number) {
  const x = Number(String(v).replace(/,/g, ''))
  return Number.isFinite(x) ? x : 0
}

let keySeq = 0
const nextKey = () => `wl${++keySeq}`

export function WholesaleSalePage({
  active,
  editSaleId = null,
  onEditConsumed,
}: {
  active: boolean
  editSaleId?: number | null
  onEditConsumed?: () => void
  onNavigate?: AppNavigate
  syncRefreshNonce?: number
  focusNonce?: number
}) {
  const [form, setForm] = useState<Form>(blankForm)
  const [info, setInfo] = useState<PartyInfo | null>(null)
  const [calc, setCalc] = useState<WholesaleCalc | null>(null)
  const [suggest, setSuggest] = useState<PartyRow[]>([])
  const [suggestOpen, setSuggestOpen] = useState(false)
  const [medText, setMedText] = useState('')
  const [hint, setHint] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [lastSaved, setLastSaved] = useState<{ id: number; billNo: string } | null>(null)
  const [preview, setPreview] = useState<{
    open: boolean
    html?: string
    billNo?: string
    loading?: boolean
    error?: string
    saleId?: number
  }>({ open: false })
  const partyRef = useRef<HTMLInputElement | null>(null)
  const medRef = useRef<HTMLInputElement | null>(null)
  const qtyRefs = useRef<Record<string, HTMLInputElement | null>>({})

  const patch = useCallback((p: Partial<Form>) => setForm((f) => ({ ...f, ...p })), [])
  const party = info?.party ?? null
  const tier = party?.rate_tier || 'A'

  // Invoice no. hint for the bill date.
  useEffect(() => {
    if (form.editingSaleId) return
    let alive = true
    fetchSalesBillHint(form.billDate)
      .then((r) => alive && setHint(r.next_bill_hint || ''))
      .catch(() => undefined)
    return () => {
      alive = false
    }
  }, [form.billDate, form.editingSaleId, lastSaved])

  // Party search.
  useEffect(() => {
    if (!suggestOpen) return
    let alive = true
    const t = window.setTimeout(() => {
      searchParties(form.partyText.trim())
        .then((r) => {
          if (!alive) return
          setSuggest(r.parties || [])
        })
        .catch(() => undefined)
    }, 120)
    return () => {
      alive = false
      window.clearTimeout(t)
    }
  }, [form.partyText, suggestOpen])

  // The picked party's details.
  useEffect(() => {
    if (!form.partyId) {
      setInfo(null)
      return
    }
    let alive = true
    fetchParty(form.partyId)
      .then((r) => alive && setInfo(r.ok ? r : null))
      .catch((e) => alive && setError(String((e as Error).message || e)))
    return () => {
      alive = false
    }
  }, [form.partyId])

  // Live totals from the engine.
  const calcBody = useMemo(
    () => ({
      customer_id: form.partyId || 0,
      bill_date: form.billDate,
      editing_sale_id: form.editingSaleId || 0,
      payment_mode: form.paymentMode,
      cash_paid: num(form.cash),
      online_paid: num(form.online),
      items: form.items.map((it) => ({
        id: it.id,
        qty: num(it.qty),
        free: num(it.free),
        rate: num(it.rate),
        disc_pct: num(it.disc_pct),
        scheme_pct: num(it.scheme_pct),
        gst_percent: it.gst_percent,
        hsn_code: it.hsn_code,
        rate_tier: it.rate_tier,
      })),
    }),
    [form],
  )
  useEffect(() => {
    let alive = true
    const t = window.setTimeout(() => {
      calcWholesale(calcBody)
        .then((r) => alive && r.ok && setCalc(r))
        .catch(() => undefined)
    }, 120)
    return () => {
      alive = false
      window.clearTimeout(t)
    }
  }, [calcBody])

  // A bill opened for edit from Sales History.
  useEffect(() => {
    if (!editSaleId) return
    let alive = true
    loadWholesale(editSaleId)
      .then((r) => {
        if (!alive) return
        onEditConsumed?.()
        if (!r.ok) {
          setError(r.error || 'Could not open the bill.')
          return
        }
        setForm({
          ...blankForm(),
          partyText: r.party?.name || '',
          partyId: r.customer_id || null,
          billDate: r.bill_date || todayLocal(),
          transport: r.transport || '',
          lrNo: r.lr_no || '',
          ewayNo: r.eway_bill_no || '',
          paymentMode: r.cash_paid > 0 ? 'Cash' : r.online_paid > 0 ? 'Online' : 'Due',
          cash: r.cash_paid ? String(r.cash_paid) : '',
          online: r.online_paid ? String(r.online_paid) : '',
          items: (r.items || []).map((it) => ({ ...it, key: nextKey() })),
          editingSaleId: r.sale_id,
          editingBillNo: r.bill_no,
        })
        setNotice(`Editing bill ${r.bill_no}.`)
        setError('')
      })
      .catch((e) => alive && setError(String((e as Error).message || e)))
    return () => {
      alive = false
    }
  }, [editSaleId, onEditConsumed])

  const pickParty = (row: PartyRow) => {
    patch({ partyText: row.name, partyId: row.id })
    setSuggestOpen(false)
    window.setTimeout(() => medRef.current?.focus(), 0)
  }

  /** Units already on this bill by medicine id, so the picker shows what is left. */
  const reserved = useMemo(() => {
    const out: Record<string, number> = {}
    for (const it of form.items) {
      const units = (num(it.qty) + num(it.free)) * (it.units_per_pack || 1)
      out[String(it.id)] = (out[String(it.id)] || 0) + units
    }
    return out
  }, [form.items])

  const onBatchPicked = async (b: MedicineBatch) => {
    setError('')
    try {
      const r = await buildWholesaleLine({ medicine_id: b.id, rate_tier: tier, qty: 1 })
      if (!r.ok || !r.line) {
        setError(r.error || 'Could not add the medicine.')
        return
      }
      const line: Line = { ...r.line, key: nextKey() }
      setForm((f) => ({ ...f, items: [...f.items, line] }))
      setMedText('')
      // No Rate A on the batch: a red message, not a quiet notice (owner, 18 Oct 2026).
      if ((r.line as { rate_missing?: boolean }).rate_missing) setError(String(r.line.warning || ''))
      else if (r.line.warning) setNotice(r.line.warning)
      window.setTimeout(() => {
        const el = qtyRefs.current[line.key]
        el?.focus()
        el?.select()
      }, 0)
    } catch (e) {
      setError(String((e as Error).message || e))
    }
  }

  const patchLine = (key: string, p: Partial<Line>) =>
    setForm((f) => ({
      ...f,
      items: f.items.map((it) => (it.key === key ? { ...it, ...p } : it)),
    }))

  const setRate = (it: Line, value: string) => {
    const v = num(value)
    // The chip names the tier only while the rate is that tier's own.
    let t = ''
    for (const k of ['A', 'B', 'C']) {
      if (it.tier_rates && it.tier_rates[k] > 0 && Math.abs(it.tier_rates[k] - v) < 0.005) {
        t = k
        if (k === tier) break
      }
    }
    patchLine(it.key, { rate: value as unknown as number, rate_tier: t })
  }

  const removeLine = (key: string) =>
    setForm((f) => ({ ...f, items: f.items.filter((it) => it.key !== key) }))

  const clearForm = () => {
    setForm(blankForm())
    setInfo(null)
    setCalc(null)
    setNotice('')
    setError('')
    window.setTimeout(() => partyRef.current?.focus(), 0)
  }

  const save = async (print: boolean) => {
    if (busy) return
    setError('')
    setNotice('')
    if (!form.partyId) {
      setError('Pick the party first.')
      partyRef.current?.focus()
      return
    }
    if (!form.items.length) {
      setError('Add medicines to the bill.')
      return
    }
    setBusy(true)
    try {
      const body = {
        ...calcBody,
        invoice_mode: 'wholesale',
        transport: form.transport,
        lr_no: form.lrNo,
        eway_bill_no: form.ewayNo,
      }
      const res = await saveWholesale(body)
      if (!res.ok || !res.sale_id) {
        setError(res.error || 'The bill was not saved.')
        return
      }
      const saved = { id: res.sale_id, billNo: res.bill_no || '' }
      setLastSaved(saved)
      const warn = (res.warnings || []).join(' ')
      clearForm()
      setNotice(`Bill ${saved.billNo} saved (Rs ${money(res.grand_total)}).${warn ? ' ' + warn : ''}`)
      if (print) await printBill(saved.id, saved.billNo)
    } catch (e) {
      setError(String((e as Error).message || e))
    } finally {
      setBusy(false)
    }
  }

  const printBill = async (saleId: number, billNo: string, slot: 1 | 2 = 1) => {
    try {
      const r = await printSalesBill({ sale_id: saleId, bill_no: billNo, slot })
      if (!r.ok) setError(r.error || 'Print failed.')
    } catch (e) {
      setError(String((e as Error).message || e))
    }
  }

  const openPreview = async (saleId: number) => {
    setPreview({ open: true, loading: true, saleId })
    try {
      const r = await fetchBillPreview(saleId)
      setPreview({
        open: true,
        saleId,
        html: r.html,
        billNo: r.bill_no,
        error: r.ok ? undefined : r.error,
      })
    } catch (e) {
      setPreview({ open: true, saleId, error: String((e as Error).message || e) })
    }
  }

  const savePdf = async (saleId: number) => {
    try {
      const r = await saveBillPdf(saleId)
      if (r.ok && r.pdf_path) showSavedFile({ path: r.pdf_path })
      else setError(r.error || 'PDF could not be created.')
    } catch (e) {
      setError(String((e as Error).message || e))
    }
  }

  // F5 save, F7 save and print, F9 new bill -- only while this page is on screen.
  useEffect(() => {
    if (!active) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'F5') {
        e.preventDefault()
        void save(false)
      } else if (e.key === 'F7') {
        e.preventDefault()
        void save(true)
      } else if (e.key === 'F9') {
        e.preventDefault()
        clearForm()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  })

  const lineCalc = (i: number) => calc?.lines?.[i]
  const intra = calc ? calc.intra_state : (info?.intra_state ?? true)
  const pos = calc?.place_of_supply || info?.place_of_supply || ''
  const posName = calc?.place_of_supply_name || info?.place_of_supply_name || ''

  return (
    <PageRoot className="sales-page wholesale-sale-page" navChain={NAV}>
      <Panel
        title={form.editingSaleId ? `Wholesale Bill ${form.editingBillNo} (edit)` : 'Wholesale Bill (Tax Invoice)'}
        headRight={
          <span className="muted">
            {form.editingSaleId ? '' : hint ? `Next invoice: ${hint}` : ''}
          </span>
        }
      >
        <div className="form-grid">
          <Field label="Party (customer)">
            <ModernCombo
              value={form.partyText}
              inputRef={partyRef}
              placeholder="Type the medical's name"
              minChars={0}
              keepOrder
              voiceField="customer"
              items={suggest.map((p) => ({
                id: String(p.id),
                label: p.name,
                meta: [p.phone, p.party_type === 'medical' ? 'MEDICAL' : ''].filter(Boolean).join(' · '),
              }))}
              onFocus={() => setSuggestOpen(true)}
              onChange={(v) => {
                patch({ partyText: v, partyId: null })
                setSuggestOpen(true)
              }}
              onPick={(item) => {
                const row = suggest.find((p) => String(p.id) === item.id)
                if (row) pickParty(row)
              }}
            />
          </Field>
          <Field label="Bill date">
            <input
              type="date"
              value={form.billDate}
              onChange={(e) => patch({ billDate: e.target.value || todayLocal() })}
            />
          </Field>
          <Field label="Due date">
            <input type="text" readOnly value={calc?.due_date || ''} className="mono" />
          </Field>
          <Field label="Transport" optional>
            <input type="text" value={form.transport} onChange={(e) => patch({ transport: e.target.value })} />
          </Field>
          <Field label="LR no." optional>
            <input type="text" value={form.lrNo} onChange={(e) => patch({ lrNo: e.target.value })} />
          </Field>
          <Field label="E-way bill no." optional>
            <input type="text" value={form.ewayNo} onChange={(e) => patch({ ewayNo: e.target.value })} />
          </Field>
        </div>

        {party ? (
          <div className="wholesale-party-strip" style={{ display: 'flex', flexWrap: 'wrap', gap: '4px 18px', marginTop: 6 }}>
            <span><b>GSTIN</b> {party.gstin || <i className="muted">none (unregistered)</i>}</span>
            <span><b>DL</b> {party.dl_numbers || <i className="muted">not on file</i>}</span>
            <span><b>State</b> {party.state_code ? `${party.state_name} (${party.state_code})` : <i className="muted">shop's state</i>}</span>
            <span><b>Rate</b> <span className="chip chip-H">{party.rate_tier}</span></span>
            <span><b>Credit</b> {party.credit_limit > 0 ? `Rs ${money(party.credit_limit)}` : 'no limit'} · {party.credit_days || 0} days</span>
            <span><b>Due now</b> Rs {money(party.total_due)}</span>
            <span><b>Place of supply</b> {pos ? `${posName} (${pos})` : '-'} · {intra ? 'CGST + SGST' : 'IGST'}</span>
          </div>
        ) : null}

        <div className="form-grid" style={{ marginTop: 8 }}>
          <Field label="Medicine">
            <TwoStepMedicinePicker
              billDate={form.billDate}
              reserved={reserved}
              navChain={NAV}
              value={medText}
              onValueChange={setMedText}
              onBatchPicked={(b) => void onBatchPicked(b)}
              inputRef={medRef}
            />
          </Field>
        </div>
      </Panel>

      <Panel title="Items" table headRight={<span className="muted">{form.items.length} item{form.items.length === 1 ? '' : 's'} · qty in strips (packs for syrups etc.)</span>}>
        <div className="table-wrap" style={{ overflowX: 'auto' }}>
          <table className="sat-table">
            <thead>
              <tr>
                <th>Sr</th>
                <th>Medicine</th>
                <th>Batch</th>
                <th>Exp</th>
                <th>HSN</th>
                <th className="numsm">Qty</th>
                <th className="numsm">Free</th>
                <th className="numsm">Rate</th>
                <th className="numsm">Disc %</th>
                <th className="numsm">Sch %</th>
                <th className="num">Taxable</th>
                <th className="numsm">GST %</th>
                <th className="num">GST</th>
                <th className="num">Amount</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {form.items.length === 0 ? (
                <tr>
                  <td colSpan={15} className="muted">Pick the party, then add medicines.</td>
                </tr>
              ) : (
                form.items.map((it, i) => {
                  const c = lineCalc(i)
                  const over = (num(it.qty) + num(it.free)) > (it.stock_packs || 0) + 1e-9 && !form.editingSaleId
                  return (
                    <tr key={it.key}>
                      <td>{i + 1}</td>
                      <td>{it.name}</td>
                      <td>{it.batch}</td>
                      <td>{it.expiry ? it.expiry.slice(5, 7) + '/' + it.expiry.slice(2, 4) : ''}</td>
                      <td>
                        <input type="text" className="mono" style={{ width: 80 }} value={it.hsn_code}
                          onChange={(e) => patchLine(it.key, { hsn_code: e.target.value })} />
                      </td>
                      <td>
                        <input ref={(el) => { qtyRefs.current[it.key] = el }} type="text" className="mono"
                          style={{ width: 52 }} value={String(it.qty)} title={`${it.stock_packs} pack(s) in stock`}
                          onChange={(e) => patchLine(it.key, { qty: e.target.value.replace(/[^0-9]/g, '') as unknown as number })} />
                        {over ? <div className="page-error" style={{ fontSize: 10 }}>stock {it.stock_packs}</div> : null}
                      </td>
                      <td>
                        <input type="text" className="mono" style={{ width: 44 }} value={String(it.free || '')}
                          onChange={(e) => patchLine(it.key, { free: e.target.value.replace(/[^0-9]/g, '') as unknown as number })} />
                      </td>
                      <td style={{ whiteSpace: 'nowrap' }}>
                        <input type="text" className="mono" style={{ width: 70 }} value={String(it.rate)}
                          onChange={(e) => setRate(it, e.target.value)} />
                        <span className={`chip ${it.rate_tier ? 'chip-H' : 'chip-none'}`} style={{ marginLeft: 4 }}
                          title={it.rate_tier ? `Rate ${it.rate_tier}` : 'Typed / base rate'}>
                          {it.rate_tier || '·'}
                        </span>
                      </td>
                      <td>
                        <input type="text" className="mono" style={{ width: 48 }} value={String(it.disc_pct || '')}
                          onChange={(e) => patchLine(it.key, { disc_pct: e.target.value as unknown as number })} />
                      </td>
                      <td>
                        <input type="text" className="mono" style={{ width: 48 }} value={String(it.scheme_pct || '')}
                          onChange={(e) => patchLine(it.key, { scheme_pct: e.target.value as unknown as number })} />
                      </td>
                      <td className="num mono">{money(c?.taxable)}</td>
                      <td className="numsm mono">{it.gst_percent}</td>
                      <td className="num mono" title={c ? (intra ? `CGST ${money(c.cgst)} + SGST ${money(c.sgst)}` : `IGST ${money(c.igst)}`) : ''}>
                        {money(c?.gst_amt)}
                      </td>
                      <td className="num mono">{money(c?.amount)}</td>
                      <td>
                        <button type="button" className="btn btn-ghost-accent btn-sm" onClick={() => removeLine(it.key)} title="Remove line">
                          ×
                        </button>
                      </td>
                    </tr>
                  )
                })
              )}
            </tbody>
          </table>
        </div>
      </Panel>

      <Panel title="Invoice Summary" className="summary-panel" bare>
        <div className="summary-grid">
          <div className="summary-col">
            <h3>Payment</h3>
            <div className="sfield">
              <label>Mode</label>
              <SelectWrap>
                <select
                  value={form.paymentMode}
                  onChange={(e) => {
                    const m = e.target.value as Form['paymentMode']
                    patch(m === 'Due' ? { paymentMode: m, cash: '', online: '' } : { paymentMode: m })
                  }}
                >
                  <option value="Due">Due (credit)</option>
                  <option value="Cash">Cash</option>
                  <option value="Online">Online</option>
                </select>
              </SelectWrap>
            </div>
            <div className="sfield">
              <label>Cash</label>
              <input type="text" className="mono" disabled={form.paymentMode === 'Due'} value={form.cash}
                onChange={(e) => patch({ cash: e.target.value })} />
            </div>
            <div className="sfield">
              <label>Online</label>
              <input type="text" className="mono" disabled={form.paymentMode === 'Due'} value={form.online}
                onChange={(e) => patch({ online: e.target.value })} />
            </div>
            {calc?.warnings?.length ? (
              <p className="page-error" style={{ marginTop: 6 }}>{calc.warnings.join(' ')}</p>
            ) : null}
          </div>

          <div className="summary-col">
            <h3>Totals</h3>
            <div className="totals-list">
              <div className="totals-row">
                <span className="label">Taxable</span>
                <span className="value">{money(calc?.taxable_total)}</span>
              </div>
              {intra ? (
                <>
                  <div className="totals-row">
                    <span className="label">CGST</span>
                    <span className="value">{money(calc?.cgst_total)}</span>
                  </div>
                  <div className="totals-row">
                    <span className="label">SGST</span>
                    <span className="value">{money(calc?.sgst_total)}</span>
                  </div>
                </>
              ) : (
                <div className="totals-row">
                  <span className="label">IGST</span>
                  <span className="value">{money(calc?.igst_total)}</span>
                </div>
              )}
              <div className="totals-row">
                <span className="label">Round off</span>
                <span className="value">{money(calc?.round_off)}</span>
              </div>
              <div className="totals-row divider emph amt">
                <span className="label">Grand Total</span>
                <span className="value">{money(calc?.grand_total)}</span>
              </div>
            </div>
            <p className="muted" style={{ marginTop: 4 }}>{calc?.amount_in_words || ''}</p>
          </div>

          <div className="summary-col actions-col">
            <ActionBtn label="Save" kbd="F5" disabled={busy} onClick={() => void save(false)} />
            <ActionBtn label="Save & Print" kbd="F7" disabled={busy} onClick={() => void save(true)} />
            <ActionBtn label="New Bill" kbd="F9" variant="secondary" onClick={clearForm} />
            {lastSaved ? (
              <>
                <ActionBtn label={`Preview ${lastSaved.billNo}`} variant="neutral" small onClick={() => void openPreview(lastSaved.id)} />
                <ActionBtn label={`Print ${lastSaved.billNo}`} variant="neutral" small onClick={() => void printBill(lastSaved.id, lastSaved.billNo)} />
                <ActionBtn label="Save PDF" variant="neutral" small onClick={() => void savePdf(lastSaved.id)} />
              </>
            ) : null}
          </div>
        </div>
        {error ? <p className="page-error">{error}</p> : null}
        {notice ? <p className="muted">{notice}</p> : null}
      </Panel>

      <BillPreviewDialog
        open={preview.open}
        billNo={preview.billNo}
        html={preview.html}
        loading={preview.loading}
        error={preview.error}
        saleId={preview.saleId}
        onClose={() => setPreview({ open: false })}
        onPrint={(slot) => {
          if (preview.saleId) void printBill(preview.saleId, preview.billNo || '', slot)
        }}
        onSavePdf={() => {
          if (preview.saleId) void savePdf(preview.saleId)
        }}
      />
    </PageRoot>
  )
}
