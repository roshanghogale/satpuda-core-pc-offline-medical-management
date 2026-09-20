import { useEffect, useMemo, useState } from 'react'
import {
  deleteInventoryMedicine,
  fetchInventoryMedicine,
  updateInventoryMedicine,
  type InventoryHistoryLine,
  type InventoryMedicine,
} from '../pagesApi'
import { AlertDialog, type AlertState } from './SalesDialogs'
import {
  DataTable,
  MED_PURCHASE_HISTORY_WIDTHS,
  MED_SALES_HISTORY_WIDTHS,
} from './pageChrome'

type Mode = 'edit' | 'view'

function fmtPrice(v: number | string | undefined) {
  const n = Number(v)
  if (!Number.isFinite(n)) return ''
  if (Math.abs(n - Math.round(n * 100) / 100) < 1e-9) return n.toFixed(2)
  return String(Number(n.toFixed(4)))
}

function parseTps(unit: string) {
  const m = String(unit || '').match(/(\d+)/)
  const n = m ? Number(m[1]) : 1
  return Number.isFinite(n) && n > 0 ? n : 1
}

/** Heuristic strip types — server also decides on save via layout_config. */
function looksStrip(type: string, unit: string) {
  const t = (type || '').toLowerCase()
  const u = (unit || '').toLowerCase()
  if (/\d/.test(unit) && (t.includes('tablet') || t.includes('capsule') || u.includes('tab'))) {
    return true
  }
  return Boolean(unit && /\d+\s*[x×]/i.test(unit))
}

export function MedicineEditDialog({
  medicineId,
  mode,
  onClose,
  onSaved,
  onDeleted,
  onRequestEdit,
}: {
  medicineId: number | null
  mode: Mode
  onClose: () => void
  onSaved?: () => void
  onDeleted?: () => void
  onRequestEdit?: () => void
}) {
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [alert, setAlert] = useState<AlertState | null>(null)
  const [types, setTypes] = useState<string[]>([])
  const [schedules, setSchedules] = useState<string[]>([])
  const [purchaseHistory, setPurchaseHistory] = useState<InventoryHistoryLine[]>([])
  const [salesHistory, setSalesHistory] = useState<InventoryHistoryLine[]>([])
  const [purchaseSummary, setPurchaseSummary] = useState('')
  const [salesSummary, setSalesSummary] = useState('')
  const [qtyColumnLabel, setQtyColumnLabel] = useState('Qty')
  const [form, setForm] = useState({
    name: '',
    type: '',
    batch: '',
    expiry: '',
    stock_strips: '0',
    unit: '',
    extra_tablets: '0',
    mrp: '',
    rate: '',
    mrp_tab: '',
    rate_tab: '',
    manufacturer: '',
    schedule: '',
    content_drug: '',
    hsn_code: '',
    // Where the shop got this stock. Written by a purchase, by the Opening
    // Stock page, by the loader app on the phone, or typed here. Reference
    // only: nothing about it reaches the supplier ledger.
    supplier_name: '',
    is_strip: false,
  })

  const readOnly = mode === 'view'
  const title = mode === 'view' ? 'Medicine Details' : 'Edit Medicine'

  useEffect(() => {
    if (!medicineId) return
    let cancelled = false
    const run = async () => {
      setLoading(true)
      setError('')
      try {
        const res = await fetchInventoryMedicine(medicineId)
        if (cancelled) return
        if (!res.ok || !res.medicine) {
          setError(res.error || 'Medicine not found.')
          return
        }
        const m = res.medicine
        setTypes(res.medicine_types || [])
        setSchedules(res.schedules || [])
        setPurchaseHistory(res.purchase_history || [])
        setSalesHistory(res.sales_history || [])
        setPurchaseSummary(res.purchase_summary || '')
        setSalesSummary(res.sales_summary || '')
        setQtyColumnLabel(res.qty_column_label || 'Qty')
        applyMedicine(m)
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e))
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    void run()
    return () => {
      cancelled = true
    }
  }, [medicineId])

  const purchaseHistoryColumns = useMemo(
    () => ['Date', 'Bill', 'Supplier', 'Qty', 'Free', 'Total', 'Rate', 'Amount'],
    [],
  )
  const salesHistoryColumns = useMemo(
    () => ['Date', 'Bill', 'Customer', qtyColumnLabel, 'Rate', 'Amount'],
    [qtyColumnLabel],
  )
  const salesHistoryWidths = useMemo(
    () => ({ ...MED_SALES_HISTORY_WIDTHS, [qtyColumnLabel]: 110 }),
    [qtyColumnLabel],
  )
  const purchaseHistoryRows = useMemo(
    () =>
      purchaseHistory.map((row) => [
        row.date,
        row.bill,
        row.party,
        row.qty ?? '',
        row.free_qty ?? 0,
        row.total_display ?? '',
        row.rate,
        row.amount,
      ]),
    [purchaseHistory],
  )
  const salesHistoryRows = useMemo(
    () =>
      salesHistory.map((row) => [
        row.date,
        row.bill,
        row.party,
        row.qty_display ?? row.qty ?? '',
        row.rate,
        row.amount,
      ]),
    [salesHistory],
  )

  const applyMedicine = (m: InventoryMedicine) => {
    const strip = Boolean(m.is_strip)
    setForm({
      name: m.name || '',
      type: m.type || '',
      batch: m.batch || '',
      expiry: m.expiry || '',
      stock_strips: String(strip ? m.stock_strips : m.stock_qty ?? 0),
      unit: m.unit || '',
      extra_tablets: String(m.extra_tablets ?? 0),
      mrp: fmtPrice(m.mrp),
      rate: fmtPrice(m.rate),
      mrp_tab: strip ? fmtPrice(m.mrp_tab) : '',
      rate_tab: strip ? fmtPrice(m.rate_tab) : '',
      manufacturer: m.manufacturer || '',
      supplier_name: m.supplier_name || '',
      schedule: m.schedule || '',
      content_drug: m.content_drug || '',
      hsn_code: m.hsn_code || '',
      is_strip: strip,
    })
  }

  const patch = (partial: Partial<typeof form>) => {
    setForm((f) => {
      const next = { ...f, ...partial }
      const strip = looksStrip(next.type, next.unit)
      next.is_strip = strip
      return next
    })
  }

  const syncTabFromStrip = (next = form) => {
    if (!looksStrip(next.type, next.unit)) return next
    const tps = parseTps(next.unit)
    const mrp = Number(next.mrp) || 0
    const rate = Number(next.rate) || 0
    return {
      ...next,
      is_strip: true,
      mrp_tab: fmtPrice(mrp / tps),
      rate_tab: fmtPrice(rate / tps),
    }
  }

  const syncStripFromTab = (field: 'mrp_tab' | 'rate_tab', value: string) => {
    setForm((f) => {
      if (!looksStrip(f.type, f.unit)) return { ...f, [field]: value }
      const tps = parseTps(f.unit)
      const next = { ...f, [field]: value, is_strip: true }
      if (field === 'mrp_tab') next.mrp = fmtPrice((Number(value) || 0) * tps)
      if (field === 'rate_tab') next.rate = fmtPrice((Number(value) || 0) * tps)
      return next
    })
  }

  const isStrip = useMemo(
    () => looksStrip(form.type, form.unit) || form.is_strip,
    [form.type, form.unit, form.is_strip],
  )

  const save = async () => {
    if (!medicineId || readOnly) return
    if (!form.name.trim()) {
      setAlert({
        title: 'Missing Information',
        message: 'Name is required.',
        kind: 'warning',
      })
      return
    }
    setSaving(true)
    setError('')
    try {
      const res = await updateInventoryMedicine({
        id: medicineId,
        name: form.name.trim(),
        type: form.type,
        batch: form.batch,
        expiry: form.expiry,
        stock_strips: Number(form.stock_strips) || 0,
        stock_qty: Number(form.stock_strips) || 0,
        extra_tablets: Number(form.extra_tablets) || 0,
        unit: form.unit,
        mrp: Number(form.mrp) || 0,
        rate: Number(form.rate) || 0,
        manufacturer: form.manufacturer,
        supplier_name: form.supplier_name,
        schedule: form.schedule,
        content_drug: form.content_drug,
        hsn_code: form.hsn_code,
      })
      if (!res.ok) {
        setAlert({
          title: 'Update Failed',
          message: res.error || 'Could not update medicine.',
          kind: 'error',
        })
        return
      }
      onSaved?.()
      onClose()
    } catch (e) {
      setAlert({
        title: 'Error',
        message: e instanceof Error ? e.message : String(e),
        kind: 'error',
      })
    } finally {
      setSaving(false)
    }
  }

  const confirmDelete = () => {
    if (!medicineId) return
    setAlert({
      title: 'Delete Medicine',
      message:
        'Hide this medicine from inventory? (Same as classic — soft delete / hide.)',
      kind: 'confirm',
      confirmLabel: 'Delete',
      cancelLabel: 'Cancel',
      onConfirm: () => {
        void (async () => {
          try {
            const res = await deleteInventoryMedicine(medicineId)
            if (!res.ok) {
              setAlert({
                title: 'Delete Failed',
                message: res.error || 'Could not delete.',
                kind: 'error',
              })
              return
            }
            onDeleted?.()
            onClose()
          } catch (e) {
            setAlert({
              title: 'Error',
              message: e instanceof Error ? e.message : String(e),
              kind: 'error',
            })
          }
        })()
      },
    })
  }

  if (!medicineId) return null

  return (
    <>
      <div className="modal-backdrop" role="presentation" onClick={onClose}>
        <div
          className={`modal-card modal-card-form${readOnly ? ' modal-card-med-view' : ''}`}
          role="dialog"
          aria-modal="true"
          aria-labelledby="med-edit-title"
          onClick={(e) => e.stopPropagation()}
        >
          <div className="modal-head">
            <h2 id="med-edit-title">{title}</h2>
            <button type="button" className="icon-btn" onClick={onClose} aria-label="Close">
              ✕
            </button>
          </div>
          <div className="modal-body">
            {loading ? <p className="muted">Loading…</p> : null}
            {error ? <p className="error-text">{error}</p> : null}
            {!loading && !error ? (
              <div className="med-edit-grid">
                <label className="field">
                  <span className="field-label">Name</span>
                  <input
                    className="settings-input"
                    value={form.name}
                    disabled={readOnly}
                    onChange={(e) => patch({ name: e.target.value })}
                    autoFocus={!readOnly}
                  />
                </label>
                <label className="field">
                  <span className="field-label">Type</span>
                  <select
                    className="settings-input"
                    value={form.type}
                    disabled={readOnly}
                    onChange={(e) => {
                      const type = e.target.value
                      setForm((f) =>
                        syncTabFromStrip({
                          ...f,
                          type,
                          is_strip: looksStrip(type, f.unit),
                        }),
                      )
                    }}
                  >
                    <option value="">—</option>
                    {types.map((t) => (
                      <option key={t} value={t}>
                        {t}
                      </option>
                    ))}
                    {form.type && !types.includes(form.type) ? (
                      <option value={form.type}>{form.type}</option>
                    ) : null}
                  </select>
                </label>
                <label className="field">
                  <span className="field-label">Batch No</span>
                  <input
                    className="settings-input"
                    value={form.batch}
                    disabled={readOnly}
                    onChange={(e) => patch({ batch: e.target.value })}
                  />
                </label>
                <label className="field">
                  <span className="field-label">Expiry (MM/YY)</span>
                  <input
                    className="settings-input"
                    value={form.expiry}
                    disabled={readOnly}
                    placeholder="MM/YY"
                    onChange={(e) => patch({ expiry: e.target.value })}
                  />
                </label>
                <label className="field">
                  <span className="field-label">{isStrip ? 'Strips' : 'Stock Qty'}</span>
                  <input
                    className="settings-input"
                    type="number"
                    value={form.stock_strips}
                    disabled={readOnly}
                    onChange={(e) => patch({ stock_strips: e.target.value })}
                  />
                </label>
                <label className="field">
                  <span className="field-label">{isStrip ? 'Tablets/Strip' : 'Unit'}</span>
                  <input
                    className="settings-input"
                    value={form.unit}
                    disabled={readOnly}
                    onChange={(e) => {
                      const unit = e.target.value
                      setForm((f) =>
                        syncTabFromStrip({
                          ...f,
                          unit,
                          is_strip: looksStrip(f.type, unit),
                        }),
                      )
                    }}
                  />
                </label>
                {isStrip ? (
                  <label className="field">
                    <span className="field-label">Extra Tablets</span>
                    <input
                      className="settings-input"
                      type="number"
                      value={form.extra_tablets}
                      disabled={readOnly}
                      onChange={(e) => patch({ extra_tablets: e.target.value })}
                    />
                  </label>
                ) : null}
                <label className="field">
                  <span className="field-label">{isStrip ? 'MRP (per strip)' : 'MRP'}</span>
                  <input
                    className="settings-input"
                    type="number"
                    step="any"
                    value={form.mrp}
                    disabled={readOnly}
                    onChange={(e) => {
                      const mrp = e.target.value
                      setForm((f) => syncTabFromStrip({ ...f, mrp }))
                    }}
                  />
                </label>
                <label className="field">
                  <span className="field-label">{isStrip ? 'Rate (per strip)' : 'Rate'}</span>
                  <input
                    className="settings-input"
                    type="number"
                    step="any"
                    value={form.rate}
                    disabled={readOnly}
                    onChange={(e) => {
                      const rate = e.target.value
                      setForm((f) => syncTabFromStrip({ ...f, rate }))
                    }}
                  />
                </label>
                {isStrip ? (
                  <>
                    <label className="field">
                      <span className="field-label">MRP (per tablet)</span>
                      <input
                        className="settings-input"
                        type="number"
                        step="any"
                        value={form.mrp_tab}
                        disabled={readOnly}
                        onChange={(e) => syncStripFromTab('mrp_tab', e.target.value)}
                      />
                    </label>
                    <label className="field">
                      <span className="field-label">Rate (per tablet)</span>
                      <input
                        className="settings-input"
                        type="number"
                        step="any"
                        value={form.rate_tab}
                        disabled={readOnly}
                        onChange={(e) => syncStripFromTab('rate_tab', e.target.value)}
                      />
                    </label>
                  </>
                ) : null}
                <label className="field">
                  <span className="field-label">Manufacturer</span>
                  <input
                    className="settings-input"
                    value={form.manufacturer}
                    disabled={readOnly}
                    onChange={(e) => patch({ manufacturer: e.target.value })}
                  />
                </label>
                <label className="field">
                  <span className="field-label">Supplier</span>
                  <input
                    className="settings-input"
                    value={form.supplier_name}
                    disabled={readOnly}
                    placeholder="Where this stock came from"
                    onChange={(e) => patch({ supplier_name: e.target.value })}
                  />
                </label>
                <label className="field">
                  <span className="field-label">Schedule</span>
                  <select
                    className="settings-input"
                    value={form.schedule}
                    disabled={readOnly}
                    onChange={(e) => patch({ schedule: e.target.value })}
                  >
                    <option value="">—</option>
                    {schedules.map((s) => (
                      <option key={s} value={s}>
                        {s}
                      </option>
                    ))}
                    {form.schedule && !schedules.includes(form.schedule) ? (
                      <option value={form.schedule}>{form.schedule}</option>
                    ) : null}
                  </select>
                </label>
                <label className="field med-edit-span2">
                  <span className="field-label">Content / Drug</span>
                  <textarea
                    className="settings-input med-edit-textarea"
                    rows={2}
                    value={form.content_drug}
                    disabled={readOnly}
                    onChange={(e) => patch({ content_drug: e.target.value })}
                  />
                </label>
                <label className="field">
                  <span className="field-label">HSN Code</span>
                  <input
                    className="settings-input"
                    value={form.hsn_code}
                    disabled={readOnly}
                    onChange={(e) => patch({ hsn_code: e.target.value })}
                  />
                </label>
              </div>
            ) : null}
            {readOnly && !loading && !error ? (
              <>
                <section className="med-history-section">
                  <h3 className="med-history-title">
                    Purchase History
                    {purchaseSummary ? ` — ${purchaseSummary}` : ''}
                  </h3>
                  <DataTable
                    columns={purchaseHistoryColumns}
                    rows={purchaseHistoryRows}
                    columnWidths={MED_PURCHASE_HISTORY_WIDTHS}
                    tableWrapClassName="med-history-scroll"
                    empty="No purchase records for this batch."
                  />
                </section>
                <section className="med-history-section">
                  <h3 className="med-history-title">
                    Sales History
                    {salesSummary ? ` — ${salesSummary}` : ''}
                  </h3>
                  <DataTable
                    columns={salesHistoryColumns}
                    rows={salesHistoryRows}
                    columnWidths={salesHistoryWidths}
                    tableWrapClassName="med-history-scroll"
                    empty="No sales records for this batch."
                  />
                </section>
              </>
            ) : null}
          </div>
          <div className="modal-foot">
            {mode === 'edit' ? (
              <>
                <button
                  type="button"
                  className="btn-neutral"
                  onClick={confirmDelete}
                  disabled={saving || loading}
                >
                  Delete
                </button>
                <button type="button" className="btn-neutral" onClick={onClose}>
                  Cancel
                </button>
                <button
                  type="button"
                  className="btn-primary"
                  onClick={() => void save()}
                  disabled={saving || loading || Boolean(error)}
                >
                  {saving ? 'Saving…' : 'Update'}
                </button>
              </>
            ) : (
              <>
                <button type="button" className="btn-neutral" onClick={onClose}>
                  Close
                </button>
                {onRequestEdit ? (
                  <button
                    type="button"
                    className="btn-primary"
                    onClick={onRequestEdit}
                  >
                    Edit
                  </button>
                ) : null}
              </>
            )}
          </div>
        </div>
      </div>
      <AlertDialog alert={alert} onClose={() => setAlert(null)} />
    </>
  )
}

export type CtxMenuState = {
  x: number
  y: number
  medicineId: number
  rowIndex: number
} | null

export function RowContextMenu({
  menu,
  items,
  onClose,
}: {
  menu: { x: number; y: number } | null
  items: { label: string; danger?: boolean; separator?: boolean; onClick?: () => void }[]
  onClose: () => void
}) {
  useEffect(() => {
    if (!menu) return
    const close = () => onClose()
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('click', close)
    window.addEventListener('scroll', close, true)
    window.addEventListener('keydown', onKey)
    return () => {
      window.removeEventListener('click', close)
      window.removeEventListener('scroll', close, true)
      window.removeEventListener('keydown', onKey)
    }
  }, [menu, onClose])

  if (!menu) return null
  const maxX = typeof window !== 'undefined' ? window.innerWidth - 180 : menu.x
  const maxY = typeof window !== 'undefined' ? window.innerHeight - 160 : menu.y
  const left = Math.min(menu.x, maxX)
  const top = Math.min(menu.y, maxY)

  return (
    <div
      className="ctx-menu"
      style={{ left, top }}
      role="menu"
      onClick={(e) => e.stopPropagation()}
      onContextMenu={(e) => e.preventDefault()}
    >
      {items.map((it, i) =>
        it.separator ? (
          <div key={`sep-${i}`} className="ctx-sep" />
        ) : (
          <button
            key={it.label}
            type="button"
            className={`ctx-item${it.danger ? ' danger' : ''}`}
            role="menuitem"
            onClick={() => {
              onClose()
              it.onClick?.()
            }}
          >
            {it.label}
          </button>
        ),
      )}
    </div>
  )
}
