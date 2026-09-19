import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import {
  fetchBulkPurchasePrefill,
  saveBulkPurchaseReturn,
  savePurchaseReturnPdf,
  type BulkPurchasePrefill,
  type ReturnLineItem,
} from '../pagesApi'
import { AlertDialog, type AlertState } from './SalesDialogs'
import { partTabletProblem, tabletCountNote } from './tabletCount'

/**
 * Alt+X opens this popup from the Inventory page ("eXpired").
 *
 * Why Alt+X: Inventory binds Ctrl+E / Ctrl+F / Ctrl+Enter / Ctrl+Shift+C
 * (usePageHotkeys) and App binds Ctrl+P and the bare digits. Nothing in the app
 * reads X, with or without Alt; Settings reads Alt only with a digit; Alt+R is
 * the Sales Return popup. Alt+X is no WebView2 browser key (those are Ctrl+R /
 * F5 / Ctrl+F / F3 / Ctrl+G / Ctrl+P / F7 / F12 / Alt+arrows / Alt+Home), and
 * it cannot be AltGr (Windows sends that as Ctrl+Alt, which this refuses).
 * Matched on the physical key too, so a Marathi / Hindi layout still answers.
 */
export function isExpiredReturnKey(e: KeyboardEvent): boolean {
  if (!e.altKey || e.ctrlKey || e.metaKey || e.shiftKey) return false
  return e.code === 'KeyX' || e.key.toLowerCase() === 'x'
}

/** One returnable batch, flattened out of the purchase-bill groups. */
type Row = {
  key: string
  purchase_id: number
  bill_number: string
  supplier_id: number
  supplier_name: string
  medicine_id: number
  name: string
  batch: string
  expiry: string
  available: number
  rate: number
  type: string
  unit: string
  is_tablet: boolean
  tablets_per_stripe: number
}

type Saved = {
  return_id?: number
  return_no?: string
  supplier_name?: string
  bill_number?: string
  refund_amount?: number
}

type Props = {
  open: boolean
  onClose: () => void
  /** Stock changed: Inventory should reload. */
  onSaved?: () => void
  /** Open a saved return on Returns -> Purchase for editing. */
  onEditReturn?: (returnId: number) => void
}

function money(n: number) {
  return `₹${n.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
}

function flatten(data: BulkPurchasePrefill | null): Row[] {
  const rows: Row[] = []
  for (const g of data?.purchase_groups || []) {
    for (const raw of g.items || []) {
      const it = raw as ReturnLineItem & { expiry_date?: string; expiry?: string }
      const available = Number(it.qty ?? it.remaining_qty ?? 0)
      if (!(available > 0)) continue
      rows.push({
        key: `${g.purchase_id}:${it.medicine_id}:${it.batch || ''}`,
        purchase_id: Number(g.purchase_id),
        bill_number: String(g.bill_number || ''),
        supplier_id: Number(g.supplier_id || 0),
        supplier_name: String(g.supplier_name || 'Unknown supplier'),
        medicine_id: Number(it.medicine_id),
        name: String(it.name || ''),
        batch: String(it.batch || ''),
        expiry: String(it.expiry_date || it.expiry || '').slice(0, 10),
        available,
        rate: Number(it.rate || 0),
        type: String(it.type || ''),
        unit: String(it.unit || ''),
        is_tablet: Boolean(it.is_tablet),
        tablets_per_stripe: Number(it.tablets_per_stripe || 1),
      })
    }
  }
  rows.sort(
    (a, b) =>
      a.supplier_name.localeCompare(b.supplier_name) ||
      a.name.localeCompare(b.name) ||
      a.batch.localeCompare(b.batch),
  )
  return rows
}

/** A line's quantity as a number; anything unreadable is 0 (and refused). */
function qtyNum(text: string | undefined) {
  const n = Number(text)
  return Number.isFinite(n) ? n : 0
}

/** The ceiling of one line: more than 0, at most the batch's stock on that
 *  bill. Same rule and wording as the checkbox table this popup replaced;
 *  checked when a line is added AND again at save (the stock may have been
 *  reloaded in between). */
export function expiredQtyProblem(r: Row, qtyText: string | undefined): string | null {
  const q = qtyNum(qtyText)
  if (!(q > 0) || q > r.available + 1e-9) {
    return `${r.name} (${r.batch || 'no batch'}): quantity must be more than 0 and at most ${r.available}.`
  }
  // Fractional strips are fine; a part tablet is not (owner, 2026-09-11).
  return partTabletProblem(r.name, q, r.is_tablet, r.tablets_per_stripe)
}

/** The save request: ONE return per purchase bill (the ledger ties a return
 *  to the bill the goods came in on), bills ordered by supplier. Unchanged
 *  from the checkbox table. */
export function expiredReturnGroups(
  pickedRows: Row[],
  picked: Record<string, string>,
  nearToo: boolean,
) {
  const byBill = new Map<number, Row[]>()
  for (const r of pickedRows) {
    const list = byBill.get(r.purchase_id) || []
    list.push(r)
    byBill.set(r.purchase_id, list)
  }
  return Array.from(byBill.values())
    .sort((a, b) => a[0].supplier_name.localeCompare(b[0].supplier_name))
    .map((list) => ({
      purchase_id: list[0].purchase_id,
      supplier_id: list[0].supplier_id,
      supplier_name: list[0].supplier_name,
      bill_number: list[0].bill_number,
      reason: nearToo ? 'Expired / near expiry stock' : 'Expired stock',
      items: list.map((r) => ({
        medicine_id: r.medicine_id,
        name: r.name,
        qty: qtyNum(picked[r.key]),
        rate: r.rate,
        type: r.type,
        unit: r.unit,
        pack_size: r.unit,
        is_tablet: r.is_tablet,
        tablets_per_stripe: r.tablets_per_stripe,
      })),
    }))
}

/**
 * Inventory -> Return expired, driven like the Sales Return popup: one search
 * box (supplier, medicine or batch) with the batches listed as you type, ↓ and
 * Enter to pick one, the quantity filled with what may go back, Enter to add
 * it to the running list, F5 to save, F6 to clear, Escape to close.
 *
 * What it saves has not changed: the list is saved as one return per purchase
 * bill, grouped by supplier -- each one can then be saved as PDF or edited.
 */
export function ExpiredReturnDialog({ open, onClose, onSaved, onEditReturn }: Props) {
  const [nearToo, setNearToo] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [rows, setRows] = useState<Row[]>([])
  const [q, setQ] = useState('')
  const [supplier, setSupplier] = useState('')
  /** The return list: batch key -> quantity. A line belongs to a row of the
   *  loaded stock; a reload that no longer has that batch drops the line. */
  const [picked, setPickedState] = useState<Record<string, string>>({})
  const [pickKey, setPickKey] = useState<string | null>(null)
  const [qtyText, setQtyText] = useState('')
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState<Saved[]>([])
  const [note, setNote] = useState('')
  const [alert, setAlert] = useState<AlertState | null>(null)

  // Read through refs: the window key handler and async loads outlive the
  // render that created them.
  const rowsRef = useRef<Row[]>([])
  rowsRef.current = rows
  const pickedRef = useRef<Record<string, string>>({})
  pickedRef.current = picked
  const alertRef = useRef<AlertState | null>(null)
  alertRef.current = alert
  // State alone lets a second click or a second F5 in the same moment through
  // before React re-renders the button disabled -- two returns to the same
  // supplier. The ref is set before the request goes out.
  const savingRef = useRef(false)
  const loadSeq = useRef(0)

  const searchRef = useRef<HTMLInputElement>(null)
  const hitsBodyRef = useRef<HTMLTableSectionElement>(null)
  const qtyRef = useRef<HTMLInputElement>(null)

  const errText = (e: unknown) => (e instanceof Error ? e.message : String(e))

  const setPicked = (next: Record<string, string>) => {
    pickedRef.current = next
    setPickedState(next)
  }

  const focusSearch = () => {
    const el = searchRef.current
    if (!el) return
    el.focus()
    try {
      el.select()
    } catch {
      /* ignore */
    }
  }

  const hitRows = () =>
    Array.from(hitsBodyRef.current?.querySelectorAll<HTMLElement>('tr[data-hit]') || [])

  const focusHit = (key?: string | null, after = false) => {
    const all = hitRows()
    const i = key != null ? all.findIndex((r) => r.dataset.key === key) : -1
    const row = i >= 0 ? all[after ? i + 1 : i] : after ? undefined : all[0]
    if (row) row.focus()
    else focusSearch()
  }

  const load = useCallback(async () => {
    // Two loads in flight (the near-expiry box ticked twice, or a save's
    // reload and Refresh): the LAST one asked for wins.
    const seq = ++loadSeq.current
    setLoading(true)
    setError('')
    try {
      const data = await fetchBulkPurchasePrefill(true, nearToo)
      if (seq !== loadSeq.current) return
      if (!data.ok) {
        setError(data.error || 'Could not load expired stock.')
        rowsRef.current = []
        setRows([])
        return
      }
      const fresh = flatten(data)
      rowsRef.current = fresh
      setRows(fresh)
      const keys = new Set(fresh.map((r) => r.key))
      const kept: Record<string, string> = {}
      let dropped = 0
      for (const [k, v] of Object.entries(pickedRef.current)) {
        if (keys.has(k)) kept[k] = v
        else dropped += 1
      }
      if (dropped) {
        pickedRef.current = kept
        setPickedState(kept)
        setNote(`${dropped} line(s) are no longer in the stock list and were taken off the return.`)
      }
    } catch (e) {
      if (seq === loadSeq.current) setError(errText(e))
    } finally {
      if (seq === loadSeq.current) setLoading(false)
    }
  }, [nearToo])

  const resetForm = () => {
    setPicked({})
    setQ('')
    setSupplier('')
    setPickKey(null)
    setQtyText('')
    setNote('')
    setError('')
    window.setTimeout(focusSearch, 0)
  }

  // Fresh popup on every open.
  useEffect(() => {
    if (!open) return
    resetForm()
    setSaved([])
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open])

  // Load on open, and again when "Include near expiry" changes.
  useEffect(() => {
    if (!open) return
    void load()
  }, [open, load])

  const suppliers = useMemo(
    () => Array.from(new Set(rows.map((r) => r.supplier_name))).sort(),
    [rows],
  )

  // Results as the counter types: supplier, medicine or batch.
  const shown = useMemo(() => {
    const needle = q.trim().toLowerCase()
    return rows.filter((r) => {
      if (supplier && r.supplier_name !== supplier) return false
      if (!needle) return true
      return (
        r.supplier_name.toLowerCase().includes(needle) ||
        r.name.toLowerCase().includes(needle) ||
        r.batch.toLowerCase().includes(needle)
      )
    })
  }, [rows, q, supplier])

  const pick = (r: Row) => {
    setPickKey(r.key)
    // Already on the return: show its quantity, Enter changes it. Otherwise
    // the whole batch -- all of it is what may go back.
    const cur = pickedRef.current[r.key]
    setQtyText(cur !== undefined ? cur : String(r.available))
    window.setTimeout(() => {
      qtyRef.current?.focus()
      try {
        qtyRef.current?.select()
      } catch {
        /* ignore */
      }
    }, 0)
  }

  const add = () => {
    const r = rowsRef.current.find((x) => x.key === pickKey)
    if (!r) {
      setAlert({
        title: 'Return expired',
        message: 'Pick a batch from the list first (↓ and Enter, or click).',
        kind: 'warning',
        focusAfterClose: focusSearch,
      })
      return
    }
    const problem = expiredQtyProblem(r, qtyText)
    if (problem) {
      setAlert({
        title: 'Return qty',
        message: problem,
        kind: 'warning',
        focusAfterClose: () => qtyRef.current?.focus(),
      })
      return
    }
    const had = pickedRef.current[r.key] !== undefined
    const qty = String(qtyNum(qtyText))
    setPicked({ ...pickedRef.current, [r.key]: qty })
    setPickKey(null)
    setQtyText('')
    setNote(`${had ? 'Changed' : 'Added'} ${r.name} (${r.batch || 'no batch'}) × ${qty}. Add more or save (F5).`)
    // On to the next batch in the list: expired stock is usually sent back
    // several batches at a time.
    window.setTimeout(() => focusHit(r.key, true), 0)
  }

  const removeLine = (key: string) => {
    const next = { ...pickedRef.current }
    delete next[key]
    setPicked(next)
  }

  const allShownOn = shown.length > 0 && shown.every((r) => picked[r.key] !== undefined)

  const toggleAllShown = () => {
    const next = { ...pickedRef.current }
    for (const r of shown) {
      if (allShownOn) delete next[r.key]
      else if (next[r.key] === undefined) next[r.key] = String(r.available)
    }
    setPicked(next)
  }

  const save = async () => {
    if (savingRef.current) return
    const cur = pickedRef.current
    const pickedRows = rowsRef.current.filter((r) => cur[r.key] !== undefined)
    if (!pickedRows.length) {
      setAlert({
        title: 'Return expired',
        message: 'Add at least one batch to the return.',
        kind: 'warning',
        focusAfterClose: focusSearch,
      })
      return
    }
    for (const r of pickedRows) {
      const problem = expiredQtyProblem(r, cur[r.key])
      if (problem) {
        setAlert({ title: 'Return qty', message: problem, kind: 'warning' })
        return
      }
    }
    const purchase_groups = expiredReturnGroups(pickedRows, cur, nearToo)
    savingRef.current = true
    setSaving(true)
    setError('')
    setNote('')
    try {
      const res = await saveBulkPurchaseReturn({ purchase_groups, writeoff_lines: [] })
      const done = res.saved || []
      if (done.length) {
        setSaved(done)
        setPicked({})
        setPickKey(null)
        setQtyText('')
        onSaved?.()
        void load()
      }
      if (!res.ok || (res.errors || []).length) {
        setError((res.errors || []).join('\n') || res.error || 'Some returns could not be saved.')
      } else {
        setNote(`Saved ${done.length} return(s) — PDF or Edit below.`)
      }
      window.setTimeout(focusSearch, 0)
    } catch (e) {
      setError(errText(e))
    } finally {
      savingRef.current = false
      setSaving(false)
    }
  }

  const savePdf = async (s: Saved) => {
    if (!s.return_id) return
    try {
      const res = await savePurchaseReturnPdf(Number(s.return_id))
      if (res.ok) setNote(`PDF saved: ${res.path || res.pdf_path || s.return_no || ''}`)
      else setError(res.error || 'Could not save PDF.')
    } catch (e) {
      setError(errText(e))
    }
  }

  const unsavedCount = () => Object.keys(pickedRef.current).length

  const editSaved = (s: Saved) => {
    if (!s.return_id || !onEditReturn) return
    const id = Number(s.return_id)
    const n = unsavedCount()
    if (n) {
      setAlert({
        title: 'Edit return',
        message: `${n} line(s) on this return are not saved. Drop them and open ${s.return_no || 'the return'}?`,
        kind: 'confirm',
        confirmLabel: 'Open',
        onConfirm: () => onEditReturn(id),
      })
      return
    }
    onEditReturn(id)
  }

  const requestClose = () => {
    if (savingRef.current) return
    const n = unsavedCount()
    if (n) {
      setAlert({
        title: 'Close Return expired',
        message: `${n} line(s) on this return are not saved. Close and drop them?`,
        kind: 'confirm',
        confirmLabel: 'Close',
        onConfirm: onClose,
      })
      return
    }
    onClose()
  }

  const actions = useRef({ save, resetForm, requestClose })
  actions.current = { save, resetForm, requestClose }

  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      // A message is up: it owns Enter / Escape (capture listener).
      if (alertRef.current) {
        if (/^F\d+$/.test(e.key)) e.preventDefault()
        return
      }
      if (e.key === 'F5' && !e.shiftKey && !e.ctrlKey && !e.altKey && !e.metaKey) {
        e.preventDefault()
        void actions.current.save()
        return
      }
      if (e.key === 'F6' && !e.ctrlKey && !e.altKey && !e.metaKey) {
        e.preventDefault()
        actions.current.resetForm()
        return
      }
      if (isExpiredReturnKey(e)) {
        e.preventDefault()
        focusSearch()
        return
      }
      // Other F-keys are WebView2's while nothing takes them: Shift+F5
      // reloads the window, F3 opens find, F7 caret browsing. Never with Alt:
      // Alt+F4 is the shop closing the window.
      if (/^F\d+$/.test(e.key) && !e.altKey) {
        e.preventDefault()
        return
      }
      if (e.key === 'Escape' && !e.defaultPrevented) {
        e.preventDefault()
        actions.current.requestClose()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open])

  if (!open) return null

  const pickedRows = rows.filter((r) => picked[r.key] !== undefined)
  const total = pickedRows.reduce((s, r) => s + qtyNum(picked[r.key]) * r.rate, 0)
  const bills = new Set(pickedRows.map((r) => r.purchase_id)).size
  const current = rows.find((r) => r.key === pickKey) || null

  const moveFocus = (el: HTMLElement, dir: 1 | -1) => {
    const all = hitRows()
    const next = all[all.indexOf(el) + dir]
    if (next) {
      next.focus()
      return true
    }
    return false
  }

  let lastSupplier = ''
  let lastLineSupplier = ''
  return createPortal(
    <div className="modal-backdrop" role="presentation">
      <div
        className="modal-card modal-card-wide modal-card-tall expired-return-dialog"
        role="dialog"
        aria-modal="true"
        aria-label="Return expired stock"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="modal-head">
          <h2>Return Expired Stock to Supplier</h2>
          <button type="button" className="icon-btn" aria-label="Close" onClick={requestClose}>
            ✕
          </button>
        </div>
        <div className="modal-body">
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
            <input
              ref={searchRef}
              className="settings-input"
              style={{ flex: '2 1 260px' }}
              placeholder="Search supplier, medicine or batch"
              aria-label="Search supplier, medicine or batch"
              value={q}
              onChange={(e) => setQ(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') {
                  e.preventDefault()
                  // One match: straight to its quantity. Otherwise into the list.
                  if (shown.length === 1) pick(shown[0])
                  else focusHit()
                } else if (e.key === 'ArrowDown') {
                  e.preventDefault()
                  focusHit()
                }
              }}
            />
            <select
              className="settings-input"
              value={supplier}
              onChange={(e) => setSupplier(e.target.value)}
              aria-label="Supplier"
            >
              <option value="">All suppliers</option>
              {suppliers.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
            <label style={{ display: 'flex', gap: 4, alignItems: 'center' }}>
              <input type="checkbox" checked={nearToo} onChange={(e) => setNearToo(e.target.checked)} />
              Include near expiry
            </label>
            <button type="button" className="btn btn-neutral btn-sm" onClick={() => void load()} disabled={loading}>
              {loading ? 'Loading…' : 'Refresh'}
            </button>
            <button
              type="button"
              className="btn btn-neutral btn-sm"
              onClick={toggleAllShown}
              disabled={!shown.length || saving}
            >
              {allShownOn ? 'Remove all shown' : `Add all shown (${shown.length})`}
            </button>
          </div>
          {error ? (
            <p className="note" style={{ color: 'var(--danger, #c0392b)', whiteSpace: 'pre-wrap' }}>
              {error}
            </p>
          ) : null}

          <div className="settings-table-wrap" style={{ maxHeight: '28vh', overflow: 'auto', marginTop: 6 }}>
            <table className="settings-table">
              <thead>
                <tr>
                  <th>Medicine</th>
                  <th>Batch</th>
                  <th>Expiry</th>
                  <th>Bill</th>
                  <th style={{ textAlign: 'right' }}>Available</th>
                  <th style={{ textAlign: 'right' }}>On return</th>
                  <th style={{ textAlign: 'right' }}>Rate</th>
                </tr>
              </thead>
              <tbody ref={hitsBodyRef}>
                {!shown.length && (
                  <tr>
                    <td colSpan={7} className="note">
                      {loading ? 'Loading…' : 'No expired stock matches.'}
                    </td>
                  </tr>
                )}
                {shown.map((r) => {
                  const head = r.supplier_name !== lastSupplier
                  lastSupplier = r.supplier_name
                  const on = picked[r.key]
                  return (
                    <Fragment key={r.key}>
                      {head && (
                        <tr>
                          <td colSpan={7} style={{ fontWeight: 600, paddingTop: 8 }}>
                            {r.supplier_name}
                          </td>
                        </tr>
                      )}
                      <tr
                        data-hit="1"
                        data-key={r.key}
                        tabIndex={0}
                        className={pickKey === r.key ? 'row-active' : ''}
                        style={{ cursor: 'pointer' }}
                        onClick={() => pick(r)}
                        onKeyDown={(e) => {
                          if (e.key === 'Enter') {
                            e.preventDefault()
                            pick(r)
                          } else if (e.key === 'ArrowDown') {
                            e.preventDefault()
                            moveFocus(e.currentTarget, 1)
                          } else if (e.key === 'ArrowUp') {
                            e.preventDefault()
                            if (!moveFocus(e.currentTarget, -1)) focusSearch()
                          }
                        }}
                      >
                        <td>{r.name}</td>
                        <td className="mono">{r.batch || '—'}</td>
                        <td className="mono">{r.expiry || '—'}</td>
                        <td className="mono">{r.bill_number || `#${r.purchase_id}`}</td>
                        <td style={{ textAlign: 'right' }}>{r.available}</td>
                        <td style={{ textAlign: 'right' }}>{on !== undefined ? on : ''}</td>
                        <td style={{ textAlign: 'right' }}>{money(r.rate)}</td>
                      </tr>
                    </Fragment>
                  )
                })}
              </tbody>
            </table>
          </div>

          <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', marginTop: 6 }}>
            <span style={{ flex: '1 1 240px' }}>
              {current
                ? `${current.name} · ${current.batch || 'no batch'} · bill ${current.bill_number || `#${current.purchase_id}`} · up to ${
                    tabletCountNote(current.available, current.is_tablet, current.tablets_per_stripe) || current.available
                  }` +
                  (picked[current.key] !== undefined ? ' · on the return — Enter changes it' : '')
                : 'Pick a batch above (↓ and Enter, or click).'}
            </span>
            <label style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
              Return Qty
              <input
                ref={qtyRef}
                className="settings-input"
                type="number"
                min={0}
                step="any"
                style={{ width: 90 }}
                disabled={!current}
                value={qtyText}
                onChange={(e) => setQtyText(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') {
                    e.preventDefault()
                    add()
                  } else if (e.key === 'Escape') {
                    // Back to the list, not out of the popup.
                    e.preventDefault()
                    focusHit(pickKey)
                  }
                }}
              />
              {current ? (
                <span className="mono" data-tablet-note="1">
                  {tabletCountNote(qtyNum(qtyText), current.is_tablet, current.tablets_per_stripe)}
                </span>
              ) : null}
            </label>
            <button type="button" className="btn btn-primary btn-sm" disabled={!current || saving} onClick={add}>
              Add to Return
            </button>
          </div>
          {note ? <p className="note">{note}</p> : null}

          {pickedRows.length ? (
            <div className="settings-table-wrap" style={{ maxHeight: '22vh', overflow: 'auto', marginTop: 6 }}>
              <table className="settings-table">
                <thead>
                  <tr>
                    <th>Returning</th>
                    <th>Batch</th>
                    <th>Bill</th>
                    <th style={{ textAlign: 'right' }}>Return Qty</th>
                    <th style={{ textAlign: 'right' }}>Rate</th>
                    <th style={{ textAlign: 'right' }}>Amount</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {pickedRows.map((r) => {
                    const head = r.supplier_name !== lastLineSupplier
                    lastLineSupplier = r.supplier_name
                    const qty = qtyNum(picked[r.key])
                    return (
                      <Fragment key={r.key}>
                        {head && (
                          <tr>
                            <td colSpan={7} style={{ fontWeight: 600, paddingTop: 6 }}>
                              {r.supplier_name}
                            </td>
                          </tr>
                        )}
                        <tr
                          tabIndex={0}
                          onKeyDown={(e) => {
                            if (e.key === 'Delete' || e.key === 'Backspace') {
                              e.preventDefault()
                              removeLine(r.key)
                            }
                          }}
                        >
                          <td>{r.name}</td>
                          <td className="mono">{r.batch || '—'}</td>
                          <td className="mono">{r.bill_number || `#${r.purchase_id}`}</td>
                          <td style={{ textAlign: 'right' }}>
                            {tabletCountNote(qty, r.is_tablet, r.tablets_per_stripe) || picked[r.key]}
                          </td>
                          <td style={{ textAlign: 'right' }}>{money(r.rate)}</td>
                          <td style={{ textAlign: 'right' }}>{money(qty * r.rate)}</td>
                          <td>
                            <button
                              type="button"
                              className="btn btn-neutral btn-sm"
                              disabled={saving}
                              onClick={() => removeLine(r.key)}
                            >
                              Remove
                            </button>
                          </td>
                        </tr>
                      </Fragment>
                    )
                  })}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="note">
              Type a medicine, batch or supplier, pick the batch (↓ and Enter, or click), check the
              quantity, Enter to add. F5 saves one return per purchase bill, grouped by supplier; save
              a PDF or edit any of them afterwards.
            </p>
          )}

          {saved.length > 0 && (
            <div style={{ marginTop: 10 }}>
              <p className="note" style={{ fontWeight: 600 }}>
                Saved returns
              </p>
              {saved.map((s, i) => (
                <div
                  key={`${s.return_id || s.return_no || i}`}
                  style={{ display: 'flex', gap: 8, alignItems: 'center', padding: '2px 0' }}
                >
                  <span className="mono" style={{ minWidth: 70 }}>
                    {s.return_no || '—'}
                  </span>
                  <span style={{ flex: 1 }}>
                    {s.supplier_name || ''}
                    {s.bill_number ? ` · bill ${s.bill_number}` : ''}
                    {s.refund_amount != null ? ` · ${money(Number(s.refund_amount))}` : ''}
                  </span>
                  <button type="button" className="btn btn-neutral btn-sm" disabled={!s.return_id} onClick={() => void savePdf(s)}>
                    PDF
                  </button>
                  <button
                    type="button"
                    className="btn btn-neutral btn-sm"
                    disabled={!s.return_id || !onEditReturn || saving}
                    onClick={() => editSaved(s)}
                  >
                    Edit
                  </button>
                </div>
              ))}
            </div>
          )}
          <AlertDialog alert={alert} onClose={() => setAlert(null)} />
        </div>
        <div className="modal-foot">
          <span className="note" style={{ marginRight: 'auto' }}>
            {pickedRows.length} line(s) · {bills} bill(s) · {money(total)}
          </span>
          <button type="button" className="btn btn-neutral" disabled={saving} onClick={requestClose}>
            Close <span className="kbd">Esc</span>
          </button>
          <button type="button" className="btn btn-neutral" disabled={saving} onClick={resetForm}>
            Clear <span className="kbd">F6</span>
          </button>
          <button type="button" className="btn btn-primary" disabled={saving} onClick={() => void save()}>
            {saving ? 'Saving…' : 'Save Return'} <span className="kbd">F5</span>
          </button>
        </div>
      </div>
    </div>,
    document.body,
  )
}
