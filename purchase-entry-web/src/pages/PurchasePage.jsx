import React, { useState, useEffect, useRef, useCallback } from 'react'
import { buildJSON, emptyBill, emptyItem, calcAmount, calcGST } from '../billUtils.js'
import { getSuppliers, upsertSupplier, getFieldOrderForSupplier, ALL_MEDICINE_FIELDS, getMedicineTypes, getSchedules, searchMedicineNames } from '../store.js'
import styles from './PurchasePage.module.css'

function fieldMeta(key) { return ALL_MEDICINE_FIELDS.find(f => f.key === key) }

// ── Medicine name autocomplete ────────────────────────────────────────────────
function MedicineCombo({ value, onChange, onEnter, inputRef, placeholder }) {
  const [open, setOpen]       = useState(false)
  const [results, setResults] = useState([])
  const wrapRef = useRef()
  const timerRef = useRef()

  // Show on focus immediately, then debounce on keystrokes
  const search = useCallback((q) => {
    clearTimeout(timerRef.current)
    timerRef.current = setTimeout(() => {
      const found = searchMedicineNames(q, 50)
      setResults(found)
      setOpen(found.length > 0)
    }, q.length === 0 ? 0 : 120)  // instant on focus, debounced on typing
  }, [])

  function pick(name) {
    onChange(name)
    setOpen(false)
    setResults([])
    onEnter()
  }

  function handleKey(e) {
    if (e.key === 'Escape') { setOpen(false); return }
    if (e.key === 'Enter')  { setOpen(false); onEnter(); return }
    if ((e.key === 'ArrowDown' || e.key === 'ArrowUp') && open && results.length) {
      e.preventDefault()
      const list = wrapRef.current?.querySelector('.' + styles.dropdown)
      list?.firstChild?.focus()
    }
  }

  useEffect(() => {
    function handler(e) { if (wrapRef.current && !wrapRef.current.contains(e.target)) setOpen(false) }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [])

  return (
    <div ref={wrapRef} style={{ position:'relative', width:'100%' }}>
      <input
        ref={inputRef}
        value={value || ''}
        onChange={e => { onChange(e.target.value); search(e.target.value) }}
        onKeyDown={handleKey}
        onFocus={() => search(value || '')}
        placeholder={placeholder || 'Type medicine name…'}
        className={styles.cellInput}
        autoComplete="off"
      />
      {open && results.length > 0 && (
        <div className={styles.medDropdown}>
          {results.map((name, i) => (
            <div
              key={i}
              className={styles.medDropItem}
              tabIndex={0}
              onMouseDown={() => pick(name)}
              onKeyDown={e => {
                if (e.key === 'Enter') pick(name)
                if (e.key === 'ArrowDown') e.currentTarget.nextSibling?.focus()
                if (e.key === 'ArrowUp')  e.currentTarget.previousSibling?.focus()
                if (e.key === 'Escape')   setOpen(false)
              }}
            >
              {name}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// ── Supplier autocomplete dropdown ───────────────────────────────────────────────────
function SupplierCombo({ value, suppliers, onChange, onSelect, onEnter, inputRef }) {
  const [open, setOpen]   = useState(false)
  const [query, setQuery] = useState(value || '')
  const wrapRef = useRef()

  useEffect(() => { setQuery(value || '') }, [value])

  const filtered = query.trim()
    ? suppliers.filter(s => s.name.toLowerCase().includes(query.toLowerCase()))
    : suppliers

  function pick(sup) {
    setQuery(sup.name)
    setOpen(false)
    onSelect(sup)
  }

  function handleKey(e) {
    if (e.key === 'Enter') { setOpen(false); onEnter(); }
    if (e.key === 'Escape') setOpen(false)
  }

  // close on outside click
  useEffect(() => {
    function handler(e) { if (wrapRef.current && !wrapRef.current.contains(e.target)) setOpen(false) }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [])

  return (
    <div ref={wrapRef} style={{ position:'relative', flex:1 }}>
      <input
        ref={inputRef}
        value={query}
        onChange={e => { setQuery(e.target.value); onChange(e.target.value); setOpen(true) }}
        onFocus={() => setOpen(true)}
        onKeyDown={handleKey}
        placeholder="Type or select supplier…"
        className={styles.input}
        autoComplete="off"
      />
      {open && filtered.length > 0 && (
        <div className={styles.dropdown}>
          {filtered.map(s => (
            <div key={s.name} className={styles.dropItem} onMouseDown={() => pick(s)}>
              <span className={styles.dropName}>{s.name}</span>
              {s.gstin && <span className={styles.dropSub}>{s.gstin}</span>}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

export default function PurchasePage() {
  const [bills, setBills]           = useState([emptyBill()])
  const [activeBill, setActiveBill] = useState(0)
  const [copied, setCopied]         = useState(false)
  const [showAddSupplier, setShowAddSupplier] = useState(false)
  const [suppliers, setSuppliers]   = useState(getSuppliers())
  const schedules = getSchedules()
  const medicineTypes = getMedicineTypes()
  const inputRefs = useRef({})

  const bill = bills[activeBill]

  useEffect(() => { setSuppliers(getSuppliers()) }, [showAddSupplier])

  // global shortcuts
  useEffect(() => {
    function onKey(e) {
      if (e.ctrlKey && e.key === 'n') { e.preventDefault(); addBill() }
      if (e.ctrlKey && e.key === 's') { e.preventDefault(); copyJSON() }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  })

  function addBill() {
    setBills(b => { const next = [...b, emptyBill()]; setActiveBill(next.length - 1); return next })
  }

  function updateBillField(field, value) {
    setBills(b => b.map((bl, i) => i === activeBill ? { ...bl, [field]: value } : bl))
  }

  function updateItem(rowIdx, field, value) {
    setBills(b => b.map((bl, i) => {
      if (i !== activeBill) return bl
      const items = bl.items.map((it, j) => j === rowIdx ? { ...it, [field]: value } : it)
      return { ...bl, items }
    }))
  }

  function addItemRow() {
    setBills(b => b.map((bl, i) => i === activeBill ? { ...bl, items: [...bl.items, emptyItem()] } : bl))
  }

  function removeItem(rowIdx) {
    setBills(b => b.map((bl, i) => {
      if (i !== activeBill) return bl
      const items = bl.items.filter((_, j) => j !== rowIdx)
      return { ...bl, items: items.length ? items : [emptyItem()] }
    }))
  }

  function removeBill(idx) {
    if (bills.length === 1) { setBills([emptyBill()]); setActiveBill(0); return }
    const next = bills.filter((_, i) => i !== idx)
    setBills(next)
    setActiveBill(Math.min(activeBill, next.length - 1))
  }

  function onSupplierSelect(sup) {
    setBills(b => b.map((bl, i) => i !== activeBill ? bl : {
      ...bl,
      supplier_name:    sup.name       || bl.supplier_name,
      supplier_address: sup.address    || bl.supplier_address,
      supplier_phone:   sup.phone      || bl.supplier_phone,
      supplier_gstin:   sup.gstin      || bl.supplier_gstin,
      supplier_dl:      sup.dl_numbers || bl.supplier_dl,
    }))
  }

  function onSupplierType(name) {
    updateBillField('supplier_name', name)
  }

  const fieldOrder = getFieldOrderForSupplier(bill.supplier_name)

  function focusRef(key) { setTimeout(() => inputRefs.current[key]?.focus(), 20) }

  const HEADER_FIELDS = [
    'supplier_name','supplier_address','supplier_phone','supplier_gstin','supplier_dl',
    'purchase_date','bill_number','gst_calc_method','overall_discount','amount_paid'
  ]

  function onHeaderEnter(field) {
    const idx = HEADER_FIELDS.indexOf(field)
    if (idx < HEADER_FIELDS.length - 1) {
      focusRef(`${activeBill}-h-${HEADER_FIELDS[idx + 1]}`)
    } else {
      focusRef(`${activeBill}-r0-${fieldOrder[0]}`)
    }
  }

  function onItemEnter(rowIdx, fieldKey) {
    const fi = fieldOrder.indexOf(fieldKey)
    if (fi < fieldOrder.length - 1) {
      focusRef(`${activeBill}-r${rowIdx}-${fieldOrder[fi + 1]}`)
    } else {
      // last field — add new row and focus its first field
      const newRowIdx = rowIdx + 1
      setBills(b => b.map((bl, i) => {
        if (i !== activeBill) return bl
        // only add if last row; if row already exists just move
        if (newRowIdx >= bl.items.length) {
          return { ...bl, items: [...bl.items, emptyItem()] }
        }
        return bl
      }))
      focusRef(`${activeBill}-r${newRowIdx}-${fieldOrder[0]}`)
    }
  }

  function copyJSON() {
    const json = JSON.stringify(buildJSON(bills), null, 2)
    navigator.clipboard.writeText(json).then(() => {
      setCopied(true); setTimeout(() => setCopied(false), 2000)
    })
  }

  const grandSubtotal = bill.items.reduce((s, it) => s + calcAmount(it), 0)
  const grandGST      = bill.items.reduce((s, it) => s + calcGST(it), 0)
  const grandTotal    = grandSubtotal + grandGST - (parseFloat(bill.overall_discount) || 0)

  return (
    <div className={styles.page}>

      {/* tabs */}
      <div className={styles.tabs}>
        {bills.map((b, i) => (
          <div key={i} className={`${styles.tab} ${i === activeBill ? styles.tabActive : ''}`}>
            <span onClick={() => setActiveBill(i)}>
              Bill {i + 1}{b.bill_number ? ` · ${b.bill_number}` : ''}
            </span>
            {bills.length > 1 &&
              <button className={styles.tabClose} onClick={() => removeBill(i)}>×</button>}
          </div>
        ))}
        <button className={styles.addBillBtn} onClick={addBill} title="Ctrl+N">+ New Bill</button>
        <button className={`${styles.copyBtn} ${copied ? styles.copied : ''}`} onClick={copyJSON} title="Ctrl+S">
          {copied ? '✔ Copied!' : '📋 Copy JSON'}
        </button>
      </div>

      {/* supplier / header */}
      <section className={styles.section}>
        <div className={styles.sectionTitle}>Supplier &amp; Bill Details</div>
        <div className={styles.headerGrid}>

          <Field label="Supplier Name *">
            <div className={styles.supplierRow}>
              <SupplierCombo
                value={bill.supplier_name}
                suppliers={suppliers}
                onChange={onSupplierType}
                onSelect={onSupplierSelect}
                onEnter={() => onHeaderEnter('supplier_name')}
                inputRef={r => inputRefs.current[`${activeBill}-h-supplier_name`] = r}
              />
              <button className={styles.addSupBtn} onClick={() => setShowAddSupplier(true)} title="Add supplier">+</button>
            </div>
          </Field>

          {[
            ['supplier_address','Address','text'],
            ['supplier_phone','Phone','text'],
            ['supplier_gstin','GSTIN','text'],
            ['supplier_dl','DL Numbers','text'],
            ['purchase_date','Purchase Date','date'],
            ['bill_number','Bill / Invoice No','text'],
          ].map(([f, lbl, t]) => (
            <Field key={f} label={lbl}>
              <input
                ref={r => inputRefs.current[`${activeBill}-h-${f}`] = r}
                type={t} value={bill[f] || ''}
                onChange={e => updateBillField(f, e.target.value)}
                onKeyDown={e => e.key === 'Enter' && onHeaderEnter(f)}
                className={styles.input}
              />
            </Field>
          ))}

          <Field label="GST Method">
            <select
              ref={r => inputRefs.current[`${activeBill}-h-gst_calc_method`] = r}
              value={bill.gst_calc_method}
              onChange={e => updateBillField('gst_calc_method', e.target.value)}
              onKeyDown={e => e.key === 'Enter' && onHeaderEnter('gst_calc_method')}
              className={styles.input}>
              <option value="discount_before_gst">Discount Before GST</option>
              <option value="discount_after_gst">Discount After GST</option>
            </select>
          </Field>

          <Field label="Overall Discount ₹ (rupee amount off total)">
            <input type="number"
              ref={r => inputRefs.current[`${activeBill}-h-overall_discount`] = r}
              value={bill.overall_discount}
              onChange={e => updateBillField('overall_discount', e.target.value)}
              onKeyDown={e => e.key === 'Enter' && onHeaderEnter('overall_discount')}
              className={styles.input} />
          </Field>

          <Field label={`GST Total ₹ (auto)`}>
            <input readOnly value={grandGST.toFixed(2)} className={styles.input}
              style={{background:'transparent', cursor:'default', opacity:0.7}} />
          </Field>

          <Field label="Amount Paid (₹)">
            <input type="number"
              ref={r => inputRefs.current[`${activeBill}-h-amount_paid`] = r}
              value={bill.amount_paid}
              onChange={e => updateBillField('amount_paid', e.target.value)}
              onKeyDown={e => e.key === 'Enter' && onHeaderEnter('amount_paid')}
              className={styles.input} />
          </Field>

        </div>
      </section>

      {/* medicine table */}
      <section className={styles.section}>
        <div className={styles.sectionTitle}>
          Medicine Items
          {bill.supplier_name &&
            <span className={styles.fieldHint}> — layout for "{bill.supplier_name}"</span>}
        </div>

        <div className={styles.tableWrap}>
          <table className={styles.table}>
            <thead>
              <tr>
                <th>#</th>
                {fieldOrder.map(k => <th key={k}>{fieldMeta(k)?.label}</th>)}
                <th>Amount (excl. GST)</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {bill.items.map((item, rowIdx) => (
                <tr key={rowIdx}>
                  <td className={styles.rowNum}>{rowIdx + 1}</td>
                  {fieldOrder.map(fk => {
                    const meta   = fieldMeta(fk)
                    const refKey = `${activeBill}-r${rowIdx}-${fk}`
                    return (
                      <td key={fk}>
                        {fk === 'medicine_name' ? (
                          <MedicineCombo
                            value={item[fk] || ''}
                            onChange={v => updateItem(rowIdx, fk, v)}
                            onEnter={() => onItemEnter(rowIdx, fk)}
                            inputRef={r => inputRefs.current[refKey] = r}
                          />
                        ) : meta?.type === 'select' ? (
                          <select
                            ref={r => inputRefs.current[refKey] = r}
                            value={item[fk] || ''}
                            onChange={e => updateItem(rowIdx, fk, e.target.value)}
                            onKeyDown={e => e.key === 'Enter' && onItemEnter(rowIdx, fk)}
                            className={styles.cellInput}>
                            <option value="">--</option>
                            {medicineTypes.map(t => <option key={t} value={t}>{t}</option>)}
                          </select>
                        ) : fk === 'schedule' ? (
                          <select
                            ref={r => inputRefs.current[refKey] = r}
                            value={item[fk] || ''}
                            onChange={e => updateItem(rowIdx, fk, e.target.value)}
                            onKeyDown={e => e.key === 'Enter' && onItemEnter(rowIdx, fk)}
                            className={styles.cellInput}>
                            {schedules.map(s => <option key={s} value={s}>{s || '(none)'}</option>)}
                          </select>
                        ) : (
                          <input
                            ref={r => inputRefs.current[refKey] = r}
                            type={meta?.type === 'number' ? 'number' : 'text'}
                            value={item[fk] || ''}
                            onChange={e => updateItem(rowIdx, fk, e.target.value)}
                            onKeyDown={e => e.key === 'Enter' && onItemEnter(rowIdx, fk)}
                            onFocus={e => e.target.select()}
                            className={styles.cellInput}
                            placeholder={meta?.label}
                          />
                        )}
                      </td>
                    )
                  })}
                  <td className={styles.amount}>₹{calcAmount(item).toFixed(2)}</td>
                  <td>
                    <button className={styles.removeRow} onClick={() => removeItem(rowIdx)}>×</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <div className={styles.tableFooter}>
          <button className={styles.addRowBtn} onClick={addItemRow}>+ Add Row</button>
          <div className={styles.grandTotal}>
            Subtotal: ₹{grandSubtotal.toFixed(2)}
            &nbsp;+&nbsp;GST: ₹{grandGST.toFixed(2)}
            &nbsp;−&nbsp;Disc: ₹{(parseFloat(bill.overall_discount)||0).toFixed(2)}
            &nbsp;=&nbsp;<strong>Total: ₹{grandTotal.toFixed(2)}</strong>
          </div>
        </div>
      </section>

      {showAddSupplier && (
        <AddSupplierModal
          onClose={() => setShowAddSupplier(false)}
          onSaved={sup => {
            upsertSupplier(sup)
            setSuppliers(getSuppliers())
            onSupplierSelect(sup)
            setShowAddSupplier(false)
          }}
        />
      )}
    </div>
  )
}

function Field({ label, children }) {
  return (
    <div style={{ display:'flex', flexDirection:'column', gap:3 }}>
      <label style={{ fontSize:11, color:'#7a9ab5', fontWeight:600 }}>{label}</label>
      {children}
    </div>
  )
}

function AddSupplierModal({ onClose, onSaved }) {
  const [form, setForm] = useState({ name:'', address:'', phone:'', gstin:'', dl_numbers:'' })
  const refs  = useRef({})
  const fields = ['name','address','phone','gstin','dl_numbers']
  const labels = { name:'Name *', address:'Address', phone:'Phone', gstin:'GSTIN', dl_numbers:'DL Numbers' }

  useEffect(() => { refs.current['name']?.focus() }, [])
  useEffect(() => {
    function esc(e) { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', esc)
    return () => window.removeEventListener('keydown', esc)
  }, [])

  function onEnter(f) {
    const i = fields.indexOf(f)
    if (i < fields.length - 1) refs.current[fields[i + 1]]?.focus()
    else save()
  }

  function save() {
    if (!form.name.trim()) { refs.current['name']?.focus(); return }
    onSaved(form)
  }

  return (
    <div className={styles.overlay}>
      <div className={styles.modal}>
        <div className={styles.modalTitle}>Add New Supplier</div>
        {fields.map(f => (
          <div key={f} className={styles.modalField}>
            <label>{labels[f]}</label>
            <input
              ref={r => refs.current[f] = r}
              value={form[f]}
              onChange={e => setForm(p => ({ ...p, [f]: e.target.value }))}
              onKeyDown={e => e.key === 'Enter' && onEnter(f)}
              className={styles.input}
            />
          </div>
        ))}
        <div className={styles.modalBtns}>
          <button className={styles.saveBtn} onClick={save}>Save</button>
          <button className={styles.cancelBtn} onClick={onClose}>Cancel (Esc)</button>
        </div>
      </div>
    </div>
  )
}
