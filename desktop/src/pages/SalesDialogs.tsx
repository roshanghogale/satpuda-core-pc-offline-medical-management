import { useEffect, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'

type AlertKind = 'info' | 'warning' | 'error' | 'confirm'

export type AlertState = {
  title: string
  message: string
  kind?: AlertKind
  confirmLabel?: string
  cancelLabel?: string
  /** A third answer, shown between Cancel and the confirm button.
   *
   *  Some questions genuinely have three answers and collapsing them into two
   *  prompts in a row is how the wrong one gets clicked. A refused duplicate
   *  purchase is one: open the bill that is already saved, save this one as a
   *  second purchase anyway, or go back to the page. */
  altLabel?: string
  onAlt?: () => void
  onConfirm?: () => void
  onCancel?: () => void
  /** Restore focus after the dialog closes (OK / Enter / Esc / backdrop). */
  focusAfterClose?: () => void
}

export function AlertDialog({
  alert,
  onClose,
}: {
  alert: AlertState | null
  onClose: () => void
}) {
  const kind = alert?.kind || 'info'
  const isConfirm = Boolean(
    alert && (kind === 'confirm' || Boolean(alert.onConfirm)),
  )

  useEffect(() => {
    if (!alert) return
    const finish = (fn?: () => void) => {
      const after = alert.focusAfterClose
      fn?.()
      onClose()
      if (after) window.setTimeout(after, 30)
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape' && e.key !== 'Enter') return
      e.preventDefault()
      e.stopPropagation()
      if (e.key === 'Escape') {
        finish(() => alert.onCancel?.())
        return
      }
      if (isConfirm) {
        const fn = alert.onConfirm
        const after = alert.focusAfterClose
        onClose()
        fn?.()
        if (after) window.setTimeout(after, 30)
        return
      }
      finish(() => alert.onConfirm?.())
    }
    window.addEventListener('keydown', onKey, true)
    return () => window.removeEventListener('keydown', onKey, true)
  }, [alert, onClose, isConfirm])

  if (!alert) return null

  const closeWith = (fn?: () => void) => {
    const after = alert.focusAfterClose
    fn?.()
    onClose()
    if (after) window.setTimeout(after, 30)
  }

  const node = (
    <div
      className="modal-backdrop"
      role="presentation"
      onClick={() => closeWith(() => alert.onCancel?.())}
    >
      <div
        className="modal-card"
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="sales-alert-title"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="modal-head">
          <h2 id="sales-alert-title">{alert.title}</h2>
          <button
            type="button"
            className="icon-btn"
            onClick={() => closeWith(() => alert.onCancel?.())}
            aria-label="Close"
          >
            ✕
          </button>
        </div>
        <div className="modal-body">
          <p style={{ whiteSpace: 'pre-wrap', margin: 0 }}>{alert.message}</p>
        </div>
        <div className="modal-foot">
          {isConfirm ? (
            <>
              <button
                type="button"
                className="btn-neutral"
                onClick={() => closeWith(() => alert.onCancel?.())}
              >
                {alert.cancelLabel || 'Cancel'}
              </button>
              {alert.altLabel ? (
                <button
                  type="button"
                  className="btn-neutral"
                  onClick={() => {
                    const fn = alert.onAlt
                    const after = alert.focusAfterClose
                    onClose()
                    fn?.()
                    if (after) window.setTimeout(after, 30)
                  }}
                >
                  {alert.altLabel}
                </button>
              ) : null}
              <button
                type="button"
                className={kind === 'error' ? 'btn-danger' : 'btn-primary'}
                onClick={() => {
                  const fn = alert.onConfirm
                  const after = alert.focusAfterClose
                  onClose()
                  fn?.()
                  if (after) window.setTimeout(after, 30)
                }}
                autoFocus
              >
                {alert.confirmLabel || 'OK'}
              </button>
            </>
          ) : (
            <button
              type="button"
              className="btn-primary"
              onClick={() => closeWith(() => alert.onConfirm?.())}
              autoFocus
            >
              {alert.confirmLabel || 'OK'}
            </button>
          )}
        </div>
      </div>
    </div>
  )

  return createPortal(node, document.body)
}

export function RecentSalesDialog({
  open,
  sales,
  onClose,
  onPick,
}: {
  open: boolean
  sales: {
    id: number
    bill_no: string
    bill_date: string
    customer: string
    total: number
  }[]
  onClose: () => void
  onPick: (id: number) => void
}) {
  if (!open) return null
  return (
    <div className="modal-backdrop" role="presentation" onClick={onClose}>
      <div
        className="modal-card modal-card-wide"
        role="dialog"
        aria-modal="true"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="modal-head">
          <h2>Recent Sales</h2>
          <button type="button" className="icon-btn" onClick={onClose}>
            ✕
          </button>
        </div>
        <div className="modal-body">
          {sales.length === 0 ? (
            <p className="muted">No recent sales.</p>
          ) : (
            <div className="table-scroll">
              <table className="sat-table">
                <thead>
                  <tr>
                    <th>Bill</th>
                    <th>Date</th>
                    <th>Customer</th>
                    <th className="num">Total</th>
                  </tr>
                </thead>
                <tbody>
                  {sales.map((s) => (
                    <tr
                      key={s.id}
                      style={{ cursor: 'pointer' }}
                      onClick={() => onPick(s.id)}
                    >
                      <td className="mono">{s.bill_no}</td>
                      <td className="mono">{s.bill_date}</td>
                      <td>{s.customer}</td>
                      <td className="num mono">
                        ₹
                        {s.total.toLocaleString('en-IN', {
                          minimumFractionDigits: 2,
                        })}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
        <div className="modal-foot">
          <button type="button" className="btn-neutral" onClick={onClose}>
            Close
          </button>
        </div>
      </div>
    </div>
  )
}

export type RecoveredSale = {
  token: string
  sale_id: number
  bill_no: string
  bill_date: string
  counter: boolean
  customer_name: string
  items: number
  total: number
  stale: boolean
  restorable: boolean
  /** Only the form was kept: its Bill Date refused a line, so no bill was written. */
  held?: boolean
}

/** What the counter sees when an unfinished sale is found on reopening.
 *
 *  Never a silent restore. Autosave writes a REAL bill, so by the time this
 *  appears the money is already on the customer's account and the stock has
 *  already moved — the operator has to be told that, in those words, and then
 *  choose: carry on with the sale (Resume) or take it back off the books
 *  (Discard). Quietly refilling the form would hide a charge; quietly deleting
 *  the bill would hide a reversal. */
export function RecoveredSalesDialog({
  open,
  sales,
  busy,
  onResume,
  onDiscard,
  onClose,
}: {
  open: boolean
  sales: RecoveredSale[]
  busy: string
  onResume: (rec: RecoveredSale) => void
  onDiscard: (rec: RecoveredSale) => void
  onClose: () => void
}) {
  if (!open || sales.length === 0) return null
  const rupees = (n: number) =>
    `₹${(Number(n) || 0).toLocaleString('en-IN', {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    })}`

  const node = (
    <div className="modal-backdrop" role="presentation">
      <div
        className="modal-card modal-card-wide"
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="sales-recover-title"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="modal-head">
          <h2 id="sales-recover-title">
            {sales.length === 1
              ? 'An unfinished sale was recovered'
              : `${sales.length} unfinished sales were recovered`}
          </h2>
        </div>
        <div className="modal-body">
          <p style={{ margin: '0 0 10px' }}>
            {sales.every((s) => s.held) ? (
              <>
                {sales.length === 1 ? 'This sale was' : 'These sales were'} not
                saved as a bill: a medicine on {sales.length === 1 ? 'it' : 'them'} had
                not come in, or had expired, by the Bill Date. Only the form was kept.
                Resume to correct and save, or discard to clear it.
              </>
            ) : (
              <>
                {sales.length === 1 ? 'This bill is' : 'These bills are'} already
                saved — the stock and the customer&apos;s balance already moved.
                Resume to finish {sales.length === 1 ? 'it' : 'them'}, or discard to
                take {sales.length === 1 ? 'it' : 'them'} back off the books.
                {sales.some((s) => s.held)
                  ? ' A row marked "Not saved" is a form only: no bill, stock or balance moved.'
                  : ''}
              </>
            )}
          </p>
          <div className="table-scroll">
            <table className="sat-table">
              <thead>
                <tr>
                  <th>Bill</th>
                  <th>Customer</th>
                  <th className="num">Items</th>
                  <th className="num">Amount</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {sales.map((s) => (
                  <tr key={s.token}>
                    <td className="mono">
                      {s.held ? 'Not saved' : s.bill_no || s.sale_id}
                      {s.stale ? (
                        <div className="muted" style={{ fontSize: 11 }}>
                          from {s.bill_date}
                        </div>
                      ) : null}
                    </td>
                    <td>
                      {s.counter
                        ? "Counter sale (today's counter bill)"
                        : s.customer_name || '—'}
                    </td>
                    <td className="num mono">{s.items}</td>
                    <td className="num mono">{rupees(s.total)}</td>
                    <td style={{ whiteSpace: 'nowrap' }}>
                      <button
                        type="button"
                        className="btn-primary"
                        disabled={Boolean(busy) || !s.restorable}
                        onClick={() => onResume(s)}
                      >
                        {busy === s.token ? 'Working…' : 'Resume'}
                      </button>{' '}
                      <button
                        type="button"
                        className="btn-danger"
                        disabled={Boolean(busy)}
                        onClick={() => onDiscard(s)}
                      >
                        Discard
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
        <div className="modal-foot">
          <span className="muted" style={{ marginRight: 'auto', fontSize: 12 }}>
            Not now keeps {sales.length === 1 ? 'it' : 'them'} — an unfinished
            bill stays claimable and shows as Unfinished in History.
          </span>
          <button
            type="button"
            className="btn-neutral"
            onClick={onClose}
            disabled={Boolean(busy)}
          >
            Not now
          </button>
        </div>
      </div>
    </div>
  )

  return createPortal(node, document.body)
}

export function QuickSaleDialog({
  open,
  types,
  packDefaults,
  onClose,
  onSubmit,
}: {
  open: boolean
  types: string[]
  /** Tablets-per-strip the shop configured for each type. */
  packDefaults?: Record<string, string>
  onClose: () => void
  onSubmit: (data: {
    name: string
    type: string
    batch: string
    pack_size: string
    qty: string
    rate: string
    mrp: string
    schedule: string
  }) => void
}) {
  const firstType = types[0] || 'Tablet'
  const [medType, setMedType] = useState(firstType)
  const [pack, setPack] = useState(packDefaults?.[firstType] ?? '10')
  // Re-seed each time it opens. The state initialisers run once, on the very
  // first render -- when types and packDefaults are still empty -- so the box
  // opened on a hardcoded 10 for the default type and only picked up the
  // shop's own pack size if the counter changed the type and changed it back.
  useEffect(() => {
    if (!open) return
    setMedType(firstType)
    setPack(packDefaults?.[firstType] ?? '10')
  }, [open, firstType, packDefaults])
  if (!open) return null
  return (
    <div className="modal-backdrop" role="presentation" onClick={onClose}>
      <div
        className="modal-card"
        role="dialog"
        aria-modal="true"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="modal-head">
          <h2>Add Medicine (No Stock)</h2>
          <button type="button" className="icon-btn" onClick={onClose}>
            ✕
          </button>
        </div>
        <form
          className="modal-body"
          onSubmit={(e) => {
            e.preventDefault()
            const fd = new FormData(e.currentTarget)
            onSubmit({
              name: String(fd.get('name') || ''),
              type: String(fd.get('type') || 'Tablet'),
              batch: String(fd.get('batch') || ''),
              pack_size: String(fd.get('pack_size') || pack || '10'),
              qty: String(fd.get('qty') || '1'),
              rate: String(fd.get('rate') || ''),
              mrp: String(fd.get('mrp') || ''),
              schedule: String(fd.get('schedule') || ''),
            })
          }}
        >
          <div className="form-grid" style={{ gridTemplateColumns: '1fr 1fr' }}>
            <label>
              Name *
              <input name="name" required autoFocus />
            </label>
            <label>
              Type *
              <select
                name="type"
                value={medType}
                onChange={(e) => {
                  const v = e.target.value
                  setMedType(v)
                  // The shop's own tablets-per-strip for this type, instead of
                  // a hardcoded 10 retyped on every no-stock line.
                  setPack(packDefaults?.[v] ?? '10')
                }}
              >
                {(types.length ? types : ['Tablet']).map((t) => (
                  <option key={t} value={t}>
                    {t}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Batch *
              <input name="batch" required />
            </label>
            <label>
              Pack / tabs-per-strip
              <input
                name="pack_size"
                value={pack}
                onChange={(e) => setPack(e.target.value)}
                className="mono"
              />
            </label>
            <label>
              Qty *
              <input name="qty" defaultValue="1" required className="mono" />
            </label>
            <label>
              Rate ₹ *
              <input name="rate" required className="mono" />
            </label>
            <label>
              MRP (optional)
              <input name="mrp" className="mono" />
            </label>
            <label>
              Schedule (optional)
              <input name="schedule" />
            </label>
          </div>
          <p className="hint-text" style={{ marginTop: 8 }}>
            Creates inventory on Save (same as classic quick-sale).
          </p>
          <div className="modal-foot" style={{ padding: '12px 0 0' }}>
            <button type="button" className="btn-neutral" onClick={onClose}>
              Cancel
            </button>
            <button type="submit" className="btn-primary">
              Add to Bill
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

export function RecentPurchasesDialog({
  open,
  purchases,
  onClose,
  onPick,
}: {
  open: boolean
  purchases: {
    id: number
    purchase_no: string
    purchase_date: string
    supplier: string
    total: number
  }[]
  onClose: () => void
  onPick: (id: number) => void
}) {
  if (!open) return null
  return (
    <div className="modal-backdrop" role="presentation" onClick={onClose}>
      <div
        className="modal-card modal-card-wide"
        role="dialog"
        aria-modal="true"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="modal-head">
          <h2>Recent Purchases</h2>
          <button type="button" className="icon-btn" onClick={onClose}>
            ✕
          </button>
        </div>
        <div className="modal-body">
          {purchases.length === 0 ? (
            <p className="muted">No recent purchases.</p>
          ) : (
            <div className="table-scroll">
              <table className="sat-table">
                <thead>
                  <tr>
                    <th>Purchase</th>
                    <th>Date</th>
                    <th>Supplier</th>
                    <th className="num">Total</th>
                  </tr>
                </thead>
                <tbody>
                  {purchases.map((p) => (
                    <tr
                      key={p.id}
                      style={{ cursor: 'pointer' }}
                      onClick={() => onPick(p.id)}
                    >
                      <td className="mono">{p.purchase_no}</td>
                      <td className="mono">{String(p.purchase_date)}</td>
                      <td>{p.supplier}</td>
                      <td className="num mono">
                        ₹
                        {p.total.toLocaleString('en-IN', {
                          minimumFractionDigits: 2,
                        })}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
        <div className="modal-foot">
          <button type="button" className="btn-neutral" onClick={onClose}>
            Close
          </button>
        </div>
      </div>
    </div>
  )
}

/** Optional helper for typed children in footers */
export function DialogActions({ children }: { children: ReactNode }) {
  return <div className="modal-foot">{children}</div>
}

function moneyInr(n: number) {
  return `₹${n.toLocaleString('en-IN', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`
}

export function GstSlabDialog({
  open,
  calc,
  gstMethod,
  importSummary,
  onClose,
}: {
  open: boolean
  calc: Record<string, unknown> | null
  gstMethod: string
  importSummary?: Record<string, unknown> | null
  onClose: () => void
}) {
  if (!open) return null
  const slabs = Array.isArray(calc?.slab_breakdown)
    ? (calc!.slab_breakdown as Record<string, unknown>[])
    : []
  if (!calc || !slabs.length) {
    return (
      <div className="modal-backdrop" role="presentation" onClick={onClose}>
        <div
          className="modal-card"
          role="alertdialog"
          onClick={(e) => e.stopPropagation()}
        >
          <div className="modal-head">
            <h2>GST Slab Table</h2>
            <button type="button" className="icon-btn" onClick={onClose}>
              ✕
            </button>
          </div>
          <div className="modal-body">
            <p style={{ margin: 0 }}>
              No GST slab data yet.
              {'\n'}Add purchase items and apply discount first.
            </p>
          </div>
          <div className="modal-foot">
            <button type="button" className="btn-primary" onClick={onClose}>
              OK
            </button>
          </div>
        </div>
      </div>
    )
  }

  const methodLabel =
    gstMethod === 'discount_before_gst'
      ? 'Discount applied before GST (per slab)'
      : 'Discount applied after GST (inclusive)'
  const gross = Number(calc.gross_subtotal ?? calc.gross_total ?? 0) || 0
  const disc = Number(calc.discount_amount ?? calc.overall_discount ?? 0) || 0
  const subtotal = Number(calc.subtotal ?? 0) || 0
  const sorted = [...slabs].sort(
    (a, b) => Number(a.gst_pct || 0) - Number(b.gst_pct || 0),
  )
  let totGross = 0
  let totDisc = 0
  let totTaxable = 0
  let totCgst = 0
  let totSgst = 0
  let totGst = 0
  const rows = sorted.map((slab) => {
    const pct = Number(slab.gst_pct || 0)
    const g = Number(slab.gross || 0)
    const d = Number(slab.discount || 0)
    const t = Number(slab.taxable || 0)
    const c = Number(slab.cgst || 0)
    const s = Number(slab.sgst || 0)
    const gt = Number(slab.total_gst || 0)
    totGross += g
    totDisc += d
    totTaxable += t
    totCgst += c
    totSgst += s
    totGst += gt
    const classLabel =
      Math.abs(pct - Math.round(pct)) < 0.001
        ? `GST ${Math.round(pct)}%`
        : `GST ${pct}%`
    return { classLabel, g, d, t, c, s, gt }
  })

  const cgst = Number(calc.cgst ?? 0) || 0
  const sgst = Number(calc.sgst ?? 0) || 0
  const preRound = Number(calc.pre_round_total ?? 0) || 0
  const rounding = Number(calc.rounding ?? 0) || 0
  const totalAmt = Number(calc.total_amount ?? 0) || 0
  const expenditure = Number(calc.expenditure ?? 0) || 0
  const finalAmt =
    Number(calc.final_amount ?? totalAmt + expenditure) || totalAmt

  const lines = [
    `Subtotal (after discount): ${moneyInr(subtotal)}`,
    `Total GST: CGST ${moneyInr(cgst)} + SGST ${moneyInr(sgst)} = ${moneyInr(cgst + sgst)}`,
    `Before rounding: ${moneyInr(preRound)}`,
  ]
  if (Math.abs(rounding) > 0.001) {
    lines.push(`Round off: ${moneyInr(rounding)}`)
  }
  lines.push(`Bill total: ${moneyInr(totalAmt)}`)
  if (Math.abs(expenditure) > 0.001) {
    lines.push(`Delivery charges: ${moneyInr(expenditure)}`)
    lines.push(`Final amount: ${moneyInr(finalAmt)}`)
  } else {
    lines.push(`Payable amount: ${moneyInr(totalAmt)}`)
  }
  if (importSummary) {
    const supplierNet = Number(importSummary.invoice_total || 0) || 0
    if (supplierNet > 0) {
      const diff = Math.round((totalAmt - supplierNet) * 100) / 100
      if (Math.abs(diff) <= 0.02) {
        lines.push(`Supplier bill net: ${moneyInr(supplierNet)} (matches)`)
      } else {
        lines.push(
          `Supplier bill net: ${moneyInr(supplierNet)} (difference ${moneyInr(diff)})`,
        )
      }
    }
  }

  return (
    <div className="modal-backdrop" role="presentation" onClick={onClose}>
      <div
        className="modal-card modal-card-wide"
        role="dialog"
        aria-modal="true"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="modal-head">
          <h2>GST Slab Calculation</h2>
          <button type="button" className="icon-btn" onClick={onClose}>
            ✕
          </button>
        </div>
        <div className="modal-body">
          <p style={{ margin: '0 0 4px', fontWeight: 600 }}>{methodLabel}</p>
          <p className="muted" style={{ margin: '0 0 12px' }}>
            Medicine gross {moneyInr(gross)} · Overall discount {moneyInr(disc)}{' '}
            · Taxable subtotal {moneyInr(subtotal)}
          </p>
          <div className="table-scroll">
            <table className="sat-table">
              <thead>
                <tr>
                  <th>CLASS (GST %)</th>
                  <th className="num">TOT. AMT.</th>
                  <th className="num">DISC.</th>
                  <th className="num">TAXABLE</th>
                  <th className="num">CGST</th>
                  <th className="num">SGST</th>
                  <th className="num">TOT. GST</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.classLabel}>
                    <td>{r.classLabel}</td>
                    <td className="num mono">{moneyInr(r.g)}</td>
                    <td className="num mono">{moneyInr(r.d)}</td>
                    <td className="num mono">{moneyInr(r.t)}</td>
                    <td className="num mono">{moneyInr(r.c)}</td>
                    <td className="num mono">{moneyInr(r.s)}</td>
                    <td className="num mono">{moneyInr(r.gt)}</td>
                  </tr>
                ))}
                <tr>
                  <td>
                    <strong>TOTAL</strong>
                  </td>
                  <td className="num mono">
                    <strong>{moneyInr(totGross)}</strong>
                  </td>
                  <td className="num mono">
                    <strong>{moneyInr(totDisc)}</strong>
                  </td>
                  <td className="num mono">
                    <strong>{moneyInr(totTaxable)}</strong>
                  </td>
                  <td className="num mono">
                    <strong>{moneyInr(totCgst)}</strong>
                  </td>
                  <td className="num mono">
                    <strong>{moneyInr(totSgst)}</strong>
                  </td>
                  <td className="num mono">
                    <strong>{moneyInr(totGst)}</strong>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
          <div style={{ marginTop: 12 }}>
            <h3 style={{ margin: '0 0 6px', fontSize: 14 }}>Bill totals</h3>
            {lines.map((line) => (
              <p key={line} style={{ margin: '2px 0' }} className="mono">
                {line}
              </p>
            ))}
          </div>
        </div>
        <div className="modal-foot">
          <button type="button" className="btn-primary" onClick={onClose}>
            Close
          </button>
        </div>
      </div>
    </div>
  )
}
