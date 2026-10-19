import React, { useState, useEffect, useRef, useCallback, memo } from 'react'
import { createPortal } from 'react-dom'
import {
  buildJSON, emptyBill, emptyItem, calcAmount, calcPurchaseSummary, round2, typeExpiry, normalizeExpiry, expiryProblem,
  suggestRates, rateAMissing,
} from '../billUtils.js'
import {
  getApiBase, getSuppliers, upsertSupplier, saveSupplierToServer, getFieldOrderForSupplier, ALL_MEDICINE_FIELDS,
  getMedicineTypes, getSchedules, loadRuntimeCatalog,
  WHOLESALE_MEDICINE_FIELDS, WHOLESALE_FIELD_ORDER, sendToPc,
} from '../store.js'
import {
  searchBuiltInMedicines, isMedicineIndexReady, getMedicineIndexCount, loadBuiltInMedicines,
  addMedicineNamesToIndex,
  mergeInventoryNames,
} from '../medicineIndex.js'
import styles from './PurchasePage.module.css'

function fieldMeta(key, wholesale = false) {
  return (wholesale ? WHOLESALE_MEDICINE_FIELDS : ALL_MEDICINE_FIELDS).find(f => f.key === key)
}

const RATE_KEYS = ['rate_a', 'rate_b', 'rate_c']

// ── Medicine: one input + dropdown (visible on focus); ↑↓ navigate, Enter select / next field ─
const MedicineCombo = memo(function MedicineCombo({ value, onChange, onEnter, inputRef }) {
  const [open, setOpen] = useState(false)
  const [results, setResults] = useState([])
  const [highlight, setHighlight] = useState(0)
  const [ready, setReady] = useState(isMedicineIndexReady())
  const wrapRef = useRef(null)
  const inputElRef = useRef(null)
  const listRef = useRef(null)
  const timerRef = useRef(null)
  const [listPos, setListPos] = useState(null)

  useEffect(() => {
    loadBuiltInMedicines().then(() => setReady(true))
  }, [])

  const runSearch = useCallback((q) => {
    if (!isMedicineIndexReady()) return
    const names = searchBuiltInMedicines(q, 200)
    setResults(names)
    setHighlight(0)
  }, [])

  const queueSearch = useCallback((q) => {
    clearTimeout(timerRef.current)
    timerRef.current = setTimeout(() => runSearch(q), 200)
  }, [runSearch])

  const updateListPosition = useCallback(() => {
    const el = inputElRef.current
    if (!el) return
    const r = el.getBoundingClientRect()
    // Never wider than what is left of the screen: on a phone 300px from the box's left
    // edge ran off the right side.
    const room = Math.max(160, window.innerWidth - r.left - 8)
    setListPos({
      top: r.bottom + 2,
      left: r.left,
      width: Math.min(Math.max(r.width, 300), room),
    })
  }, [])

  useEffect(() => {
    if (!open) {
      setListPos(null)
      return
    }
    updateListPosition()
    const onScroll = () => updateListPosition()
    const onWheel = () => setOpen(false)
    window.addEventListener('scroll', onScroll, true)
    window.addEventListener('resize', onScroll)
    window.addEventListener('wheel', onWheel, { passive: true, capture: true })
    return () => {
      window.removeEventListener('scroll', onScroll, true)
      window.removeEventListener('resize', onScroll)
      window.removeEventListener('wheel', onWheel, true)
    }
  }, [open, updateListPosition, results])

  useEffect(() => {
    if (!open || !listRef.current) return
    const el = listRef.current.children[highlight]
    el?.scrollIntoView({ block: 'nearest' })
  }, [highlight, open, results])

  function pick(name) {
    onChange(name)
    setOpen(false)
  }

  function handleFocus() {
    setOpen(true)
    runSearch(value || '')
  }

  function handleBlur(e) {
    if (wrapRef.current?.contains(e.relatedTarget)) return
    setTimeout(() => setOpen(false), 150)
  }

  function handleChange(text) {
    onChange(text)
    setOpen(true)
    queueSearch(text)
  }

  function handleKeyDown(e) {
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      if (!open) {
        setOpen(true)
        runSearch(value || '')
        return
      }
      if (results.length) {
        setHighlight(h => Math.min(h + 1, results.length - 1))
      }
      return
    }
    if (e.key === 'ArrowUp') {
      e.preventDefault()
      if (results.length) {
        setHighlight(h => Math.max(h - 1, 0))
      }
      return
    }
    if (e.key === 'Escape') {
      setOpen(false)
      return
    }
    if (e.key === 'Enter') {
      e.preventDefault()
      if (open && results.length > 0) {
        pick(results[highlight])
        return
      }
      setOpen(false)
      onEnter()
    }
  }

  useEffect(() => {
    function onDocDown(e) {
      if (wrapRef.current?.contains(e.target)) return
      if (listRef.current?.contains(e.target)) return
      setOpen(false)
    }
    document.addEventListener('mousedown', onDocDown, true)
    return () => document.removeEventListener('mousedown', onDocDown, true)
  }, [])

  const showList = open && ready && listPos

  const listPortal = showList && createPortal(
    <div
      ref={listRef}
      className={styles.medDropdownPortal}
      role="listbox"
      style={{
        position: 'fixed',
        top: listPos.top,
        left: listPos.left,
        width: listPos.width,
      }}
    >
      {results.length === 0 ? (
        <div className={styles.medHint}>
          {(value || '').trim()
            ? 'No matches — try different spelling.'
            : `Type to search ${getMedicineIndexCount().toLocaleString()} medicines`}
        </div>
      ) : (
        results.map((name, i) => (
          <div
            key={`${name}-${i}`}
            role="option"
            aria-selected={i === highlight}
            className={`${styles.medDropItem} ${i === highlight ? styles.medDropItemActive : ''}`}
            onMouseDown={e => { e.preventDefault(); pick(name) }}
            onMouseEnter={() => setHighlight(i)}
          >
            {name}
          </div>
        ))
      )}
    </div>,
    document.body,
  )

  return (
    <>
    <div ref={wrapRef} className={styles.medComboWrap}>
      <input
        ref={el => {
          inputElRef.current = el
          if (typeof inputRef === 'function') inputRef(el)
          else if (inputRef) inputRef.current = el
        }}
        type="text"
        value={value || ''}
        onChange={e => handleChange(e.target.value)}
        onFocus={handleFocus}
        onBlur={handleBlur}
        onKeyDown={handleKeyDown}
        placeholder={ready ? 'Type medicine name…' : 'Loading medicines…'}
        className={styles.cellInput}
        autoComplete="off"
        spellCheck={false}
        disabled={!ready}
      />
    </div>
    {listPortal}
    </>
  )
})

// ── Supplier dropdown (synced from desktop DB) ──────────────────────────────────
function SupplierSelect({ value, suppliers, onSelect, onEnter, inputRef, loadState }) {
  function handleChange(e) {
    const name = e.target.value
    const sup = suppliers.find(s => s.name === name)
    if (sup) onSelect(sup)
    onEnter()
  }

  const label =
    loadState === 'loading' ? 'Loading suppliers…'
    : loadState === 'error' ? 'Could not load — open from Settings with app running'
    : loadState === 'empty' ? 'No suppliers — add in desktop app then reopen Web Purchase Entry'
    : suppliers.length === 0 ? 'Reopen from Settings → Open Web Purchase Entry'
    : `-- Select supplier (${suppliers.length}) --`

  return (
    <select
      ref={inputRef}
      value={value || ''}
      onChange={handleChange}
      onKeyDown={e => e.key === 'Enter' && onEnter()}
      className={styles.input}
      style={{ flex: 1 }}
    >
      <option value="">{label}</option>
      {suppliers.map(s => (
        <option key={s.name} value={s.name}>{s.name}</option>
      ))}
    </select>
  )
}

export default function PurchasePage({
  suppliers: suppliersFromApp,
  schedules: schedulesFromApp,
  medicineTypes: medicineTypesFromApp,
  // Served by the PC's Mobile Import receiver to a phone: bills go to the PC with "Send to
  // PC" (it shows them for Import there) instead of Web Entry's Save All.
  phone = false,
  // The PC's open store is wholesale: strips, purchase rate per strip, Rate A / B / C.
  wholesale = false,
  postTo = '/mobile-import',
  margins = { A: 8, B: 10, C: 12 },
}) {
  const [bills, setBills]           = useState([emptyBill()])
  const [activeBill, setActiveBill] = useState(0)
  const [copied, setCopied]         = useState(false)
  const [saving, setSaving]         = useState(false)
  const [saveMsg, setSaveMsg]       = useState('')
  const discountSource = useRef('rupees')
  const [showAddSupplier, setShowAddSupplier] = useState(false)
  const [suppliers, setSuppliers]   = useState(getSuppliers)
  const [schedules, setSchedules]   = useState(getSchedules)
  const [medicineTypes, setMedicineTypes] = useState(getMedicineTypes)
  const [supplierLoad, setSupplierLoad] = useState('loading')
  // Rows of the active bill a wholesale send/save refused for having no Rate A.
  const [missingRows, setMissingRows] = useState([])
  const inputRefs = useRef({})

  const bill = bills[activeBill]

  useEffect(() => {
    if (Array.isArray(suppliersFromApp)) {
      setSuppliers(suppliersFromApp)
      setSupplierLoad(suppliersFromApp.length > 0 ? 'ok' : 'empty')
    }
  }, [suppliersFromApp])

  useEffect(() => {
    if (Array.isArray(schedulesFromApp) && schedulesFromApp.length > 0) {
      setSchedules(schedulesFromApp)
    }
  }, [schedulesFromApp])

  useEffect(() => {
    if (Array.isArray(medicineTypesFromApp) && medicineTypesFromApp.length > 0) {
      setMedicineTypes(medicineTypesFromApp)
    }
  }, [medicineTypesFromApp])

  useEffect(() => {
    loadRuntimeCatalog().then(result => {
      if (!result?.connected) {
        setSupplierLoad('error')
        return
      }
      setSuppliers(result.suppliers)
      setSchedules(result.schedules)
      setMedicineTypes(result.medTypes)
      setSupplierLoad(result.suppliers.length > 0 ? 'ok' : 'empty')
    })
  }, [])

  // global shortcuts
  useEffect(() => {
    function onKey(e) {
      if (e.ctrlKey && e.key === 'n') { e.preventDefault(); addBill() }
      if (e.ctrlKey && e.shiftKey && e.key === 'S') { e.preventDefault(); if (phone) sendAll(); else saveAll() }
      if (e.ctrlKey && !e.shiftKey && e.key === 's') { e.preventDefault(); copyJSON() }
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

  function grossBeforeOverallDiscount(items) {
    return calcPurchaseSummary(items || [], 0).grossSubtotalNoGST
  }

  function syncDiscountFields(bl, source, rawValue) {
    const gross = grossBeforeOverallDiscount(bl.items)
    if (source === 'rupees') {
      const rs = parseFloat(rawValue) || 0
      const pct = gross > 0 ? round2((rs * 100) / gross) : 0
      return {
        ...bl,
        overall_discount: rawValue,
        overall_discount_pct: rawValue === '' ? '' : String(pct),
      }
    }
    const pct = parseFloat(rawValue) || 0
    const rs = gross > 0 ? round2((gross * pct) / 100) : 0
    return {
      ...bl,
      overall_discount_pct: rawValue,
      overall_discount: rawValue === '' ? '' : String(rs),
    }
  }

  function updateBillDiscount(field, value) {
    discountSource.current = field === 'overall_discount' ? 'rupees' : 'pct'
    setBills(b => b.map((bl, i) => {
      if (i !== activeBill) return bl
      return syncDiscountFields(bl, discountSource.current, value)
    }))
  }

  function updateItem(rowIdx, field, value) {
    if (field === 'rate_a') setMissingRows(m => m.filter(i => i !== rowIdx))
    setBills(b => b.map((bl, i) => {
      if (i !== activeBill) return bl
      const items = bl.items.map((it, j) => j === rowIdx ? { ...it, [field]: value } : it)
      const next = { ...bl, items }
      if (discountSource.current === 'rupees') {
        return syncDiscountFields(next, 'rupees', String(next.overall_discount ?? ''))
      }
      return syncDiscountFields(next, 'pct', String(next.overall_discount_pct ?? ''))
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

  const fieldOrder = wholesale ? WHOLESALE_FIELD_ORDER : getFieldOrderForSupplier(bill.supplier_name)
  const scheduleOptions = [...new Set(
    schedules.filter(s => s != null && String(s).trim() !== '' && String(s) !== 'Non-Scheduled')
  )]

  function focusRef(key) { setTimeout(() => inputRefs.current[key]?.focus(), 20) }

  const HEADER_FIELDS = [
    'supplier_name','supplier_address','supplier_phone','supplier_gstin','supplier_dl',
    'purchase_date','bill_number','gst_calc_method','overall_discount','overall_discount_pct','cash_paid','online_paid'
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
    const json = JSON.stringify(buildJSON(bills, { wholesale }), null, 2)
    navigator.clipboard.writeText(json).then(() => {
      setCopied(true); setTimeout(() => setCopied(false), 2000)
    })
  }

  /** Wholesale: Rate A on every line, as the PC's own Purchase page asks. false when one is missing. */
  function rateAReady() {
    if (!wholesale) return true
    for (let i = 0; i < bills.length; i++) {
      const missing = rateAMissing(bills[i].items)
      if (missing.length) {
        setActiveBill(i)
        setMissingRows(missing)
        const names = missing.map(r => (bills[i].items[r].medicine_name || '').trim()).filter(Boolean)
        setSaveMsg(
          `Bill ${i + 1}: Rate A is required for ${names.slice(0, 3).join(', ')}`
          + (names.length > 3 ? ` and ${names.length - 3} more` : '')
          + '. Type it (per strip, GST extra) or press Fill Rate A/B/C.',
        )
        focusRef(`${i}-r${missing[0]}-rate_a`)
        return false
      }
    }
    setMissingRows([])
    return true
  }

  /** Empty Rate A / B / C of the active bill, from the purchase rate + the store's margins. */
  function fillRates() {
    setBills(b => b.map((bl, i) => {
      if (i !== activeBill) return bl
      const items = bl.items.map(it => {
        const s = suggestRates(it.rate, margins)
        const next = { ...it }
        for (const k of RATE_KEYS) {
          if (!((parseFloat(next[k]) || 0) > 0) && s[k]) next[k] = s[k]
        }
        return next
      })
      return { ...bl, items }
    }))
    setMissingRows([])
  }

  /** Phone: the bills go to the PC that served this page, which shows them for Import. */
  async function sendAll() {
    const ready = bills.filter(bl => (bl.items || []).some(it => (it.medicine_name || '').trim()))
    if (!ready.length) {
      setSaveMsg('Type at least one medicine first.')
      return
    }
    const noSupplier = bills.findIndex(bl =>
      (bl.items || []).some(it => (it.medicine_name || '').trim()) && !(bl.supplier_name || '').trim())
    if (noSupplier >= 0) {
      setActiveBill(noSupplier)
      setSaveMsg(`Bill ${noSupplier + 1}: choose or add the supplier first.`)
      focusRef(`${noSupplier}-h-supplier_name`)
      return
    }
    if (!rateAReady()) return
    setSaving(true)
    setSaveMsg('')
    const payload = {
      export_type: 'purchases',
      device_name: 'Phone (Purchase Entry page)',
      export_date: new Date().toISOString().slice(0, 10),
      store_kind: wholesale ? 'wholesale' : 'retail',
      ...buildJSON(bills, { wholesale }),
    }
    const res = await sendToPc(payload, postTo)
    setSaving(false)
    if (!res.ok) {
      setSaveMsg(`Not sent: ${res.error}. Bills are still here; try again.`)
      return
    }
    const n = payload.bills.length
    setSaveMsg(`Sent ${n} bill(s) to the PC. On the PC: Settings → Import → Mobile Import → Import.`)
    setBills([emptyBill()])
    setActiveBill(0)
  }

  async function saveAll() {
    if (!rateAReady()) return
    const apiBase = getApiBase()
    if (!apiBase) {
      setSaveMsg('Open Web Purchase Entry from Settings while the desktop app is running.')
      setTimeout(() => setSaveMsg(''), 6000)
      return
    }
    if (!window.confirm(`Save all ${bills.length} bill(s) directly to the database?`)) return

    setSaving(true)
    setSaveMsg('')
    try {
      const res = await fetch(`${apiBase}/api/purchases/save`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(buildJSON(bills, { wholesale })),
      })
      const data = await res.json().catch(() => ({}))
      if (!res.ok && res.status !== 207) {
        throw new Error(data.error || `Save failed (${res.status})`)
      }
      const saved = data.saved ?? 0
      const errs = data.errors || []
      // A bill the shop already has is skipped, not saved: one supplier bill is one
      // purchase (store 127 held the same bill as purchases 35 and 36, one second
      // apart). Say which, and keep the bills on screen -- clearing them here would
      // look exactly like a successful save of every one.
      const dupes = data.skipped_duplicate || []
      if (dupes.length) {
        const which = dupes
          .map(d => `${d.bill_number} (already purchase ${d.purchase_no || d.purchase_id})`)
          .join('; ')
        setSaveMsg(
          `Saved ${saved}/${bills.length}. Already saved, not saved again: ${which}`
          + (errs.length ? `. Errors: ${errs.join('; ')}` : ''),
        )
      } else if (errs.length) {
        setSaveMsg(`Saved ${saved}/${bills.length}. Errors: ${errs.join('; ')}`)
      } else {
        const savedNames = bills.flatMap(bl =>
          (bl.items || [])
            .map(it => (it.medicine_name || '').trim())
            .filter(Boolean)
        )
        if (Array.isArray(data.inventory_medicine_names) && data.inventory_medicine_names.length) {
          mergeInventoryNames(data.inventory_medicine_names)
        } else {
          addMedicineNamesToIndex(savedNames)
        }
        setSaveMsg(`Saved ${saved} purchase(s) successfully.`)
        setBills([emptyBill()])
        setActiveBill(0)
      }
    } catch (e) {
      setSaveMsg(e.message || 'Save failed. Is the desktop app running?')
    } finally {
      setSaving(false)
      setTimeout(() => setSaveMsg(''), 8000)
    }
  }

  const itemGross = grossBeforeOverallDiscount(bill.items)
  const purchaseSummary = calcPurchaseSummary(
    bill.items,
    parseFloat(bill.overall_discount) || 0,
    0,
    bill.gst_calc_method || 'discount_after_gst',
  )
  const grandSubtotal = purchaseSummary.grossSubtotalNoGST
  const grandGST      = purchaseSummary.totalGST
  const grandTotal    = purchaseSummary.totalAmount
  const liveDiscRs = parseFloat(bill.overall_discount) || 0
  const liveDiscPct = itemGross > 0
    ? round2((liveDiscRs * 100) / itemGross)
    : (parseFloat(bill.overall_discount_pct) || 0)

  return (
    <div className={styles.page}>

      <div className={styles.topBar}>
        <div className={styles.tabs}>
          {bills.map((b, i) => (
            <div key={i} className={`${styles.tab} ${i === activeBill ? styles.tabActive : ''}`}>
              <span onClick={() => setActiveBill(i)}>
                Bill {i + 1}{b.bill_number ? ` · ${b.bill_number}` : ''}
              </span>
              {bills.length > 1 &&
                <button type="button" className={styles.tabClose} onClick={() => removeBill(i)}>×</button>}
            </div>
          ))}
          <button type="button" className={styles.addBillBtn} onClick={addBill} title="Ctrl+N">+ New Bill</button>
        </div>
        <div className={styles.actionBar}>
          <button
            type="button"
            className={`${styles.copyBtn} ${copied ? styles.copied : ''}`}
            onClick={copyJSON}
            title="Ctrl+S"
          >
            {copied ? '✔ Copied!' : '📋 Copy JSON'}
          </button>
          {phone ? (
            <button
              type="button"
              className={styles.saveAllBtn}
              onClick={sendAll}
              disabled={saving}
              title="Send all bills to the PC; import them there (Ctrl+Shift+S)"
            >
              {saving ? 'Sending…' : `📤 Send ${bills.length} bill(s) to PC`}
            </button>
          ) : (
            <button
              type="button"
              className={styles.saveAllBtn}
              onClick={saveAll}
              disabled={saving}
              title="Save all bills to the desktop app database (Ctrl+Shift+S)"
            >
              {saving ? 'Saving…' : '💾 Save All'}
            </button>
          )}
          {saveMsg && <span className={styles.saveMsg}>{saveMsg}</span>}
        </div>
      </div>

      {/* supplier / header */}
      <section className={styles.section}>
        <div className={styles.sectionTitle}>Supplier &amp; Bill Details</div>
        <div className={styles.headerGrid}>

          <Field label={`Supplier Name * (${suppliers.length})`}>
            <div className={styles.supplierRow}>
              <SupplierSelect
                value={bill.supplier_name}
                suppliers={suppliers}
                onSelect={onSupplierSelect}
                onEnter={() => onHeaderEnter('supplier_name')}
                loadState={supplierLoad}
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

          <Field label="Overall Discount ₹">
            <input type="number" step="0.01" min="0"
              ref={r => inputRefs.current[`${activeBill}-h-overall_discount`] = r}
              value={bill.overall_discount}
              onInput={e => updateBillDiscount('overall_discount', e.target.value)}
              onChange={e => updateBillDiscount('overall_discount', e.target.value)}
              onKeyDown={e => e.key === 'Enter' && onHeaderEnter('overall_discount')}
              className={styles.input} />
            <span className={styles.hint}>→ {liveDiscPct}% of ₹{itemGross.toFixed(2)} subtotal</span>
          </Field>

          <Field label="Overall Discount %">
            <input type="number" step="0.01" min="0"
              ref={r => inputRefs.current[`${activeBill}-h-overall_discount_pct`] = r}
              value={bill.overall_discount_pct ?? '0'}
              onInput={e => updateBillDiscount('overall_discount_pct', e.target.value)}
              onChange={e => updateBillDiscount('overall_discount_pct', e.target.value)}
              onKeyDown={e => e.key === 'Enter' && onHeaderEnter('overall_discount_pct')}
              className={styles.input} />
            <span className={styles.hint}>→ ₹{liveDiscRs.toFixed(2)} off</span>
          </Field>

          <Field label={`GST Total ₹ (auto)`}>
            <input readOnly value={grandGST.toFixed(2)} className={styles.input}
              style={{background:'transparent', cursor:'default', opacity:0.7}} />
          </Field>

          <Field label="Cash Paid (₹)">
            <input type="number" step="0.01" min="0"
              ref={r => inputRefs.current[`${activeBill}-h-cash_paid`] = r}
              value={bill.cash_paid ?? ''}
              onChange={e => updateBillField('cash_paid', e.target.value)}
              onKeyDown={e => e.key === 'Enter' && onHeaderEnter('cash_paid')}
              className={styles.input} />
          </Field>

          <Field label="Online Paid (₹)">
            <input type="number" step="0.01" min="0"
              ref={r => inputRefs.current[`${activeBill}-h-online_paid`] = r}
              value={bill.online_paid ?? ''}
              onChange={e => updateBillField('online_paid', e.target.value)}
              onKeyDown={e => e.key === 'Enter' && onHeaderEnter('online_paid')}
              className={styles.input} />
            <span className={styles.hint}>
              Paid ₹{((parseFloat(bill.cash_paid) || 0) + (parseFloat(bill.online_paid) || 0)).toFixed(2)}
              {' · '}Due ₹{Math.max(0, grandTotal - ((parseFloat(bill.cash_paid) || 0) + (parseFloat(bill.online_paid) || 0))).toFixed(2)}
            </span>
          </Field>

        </div>
      </section>

      {/* medicine table */}
      <section className={styles.section}>
        <div className={styles.sectionTitle}>
          Medicine Items
          <span className={styles.catalogMeta}>
            {medicineTypes.length} types · {schedules.length} schedules · {getMedicineIndexCount().toLocaleString()} medicines (built-in)
          </span>
          {bill.supplier_name && !wholesale &&
            <span className={styles.fieldHint}> — layout for "{bill.supplier_name}"</span>}
        </div>

        <div className={styles.tableWrap}>
          <table className={styles.table}>
            <thead>
              <tr>
                <th>#</th>
                {fieldOrder.map(k => <th key={k}>{fieldMeta(k, wholesale)?.label}</th>)}
                <th>Amount (excl. GST)</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {bill.items.map((item, rowIdx) => (
                <tr key={rowIdx} className={styles.itemRow}>
                  <td className={styles.rowNum} data-label="#">{rowIdx + 1}</td>
                  {fieldOrder.map(fk => {
                    const meta   = fieldMeta(fk, wholesale)
                    const refKey = `${activeBill}-r${rowIdx}-${fk}`
                    const missing = fk === 'rate_a' && missingRows.includes(rowIdx)
                    const suggested = RATE_KEYS.includes(fk) ? suggestRates(item.rate, margins)[fk] : ''
                    return (
                      <td
                        key={fk}
                        data-label={meta?.label}
                        className={`${fk === 'medicine_name' ? styles.wideCell : ''} ${missing ? styles.missingCell : ''}`}
                      >
                        {fk === 'medicine_name' ? (
                          <MedicineCombo
                            value={item[fk] || ''}
                            onChange={v => updateItem(rowIdx, fk, v)}
                            onEnter={() => onItemEnter(rowIdx, fk)}
                            inputRef={r => { inputRefs.current[refKey] = r }}
                          />
                        ) : fk === 'expiry_date' ? (
                          <input
                            ref={r => inputRefs.current[refKey] = r}
                            type="text"
                            inputMode="numeric"
                            maxLength={7}
                            value={item[fk] || ''}
                            onChange={e => updateItem(rowIdx, fk, typeExpiry(e.target.value))}
                            onBlur={e => updateItem(rowIdx, fk, normalizeExpiry(e.target.value))}
                            onKeyDown={e => {
                              if (e.key !== 'Enter') return
                              updateItem(rowIdx, fk, normalizeExpiry(e.target.value))
                              onItemEnter(rowIdx, fk)
                            }}
                            onFocus={e => e.target.select()}
                            className={styles.cellInput}
                            placeholder="MM/YY"
                            title={expiryProblem(item[fk]) || 'Expiry as MM/YY'}
                          />
                        ) : meta?.type === 'select' ? (
                          <select
                            ref={r => inputRefs.current[refKey] = r}
                            value={item[fk] || ''}
                            onChange={e => updateItem(rowIdx, fk, e.target.value)}
                            onKeyDown={e => e.key === 'Enter' && onItemEnter(rowIdx, fk)}
                            className={`${styles.cellInput} ${styles.cellSelect}`}>
                            <option value="">{fk === 'schedule' ? '(none)' : '-- select --'}</option>
                            {(fk === 'schedule' ? scheduleOptions : medicineTypes).map(opt => (
                              <option key={opt} value={opt}>{opt}</option>
                            ))}
                          </select>
                        ) : (
                          <input
                            ref={r => inputRefs.current[refKey] = r}
                            type={meta?.type === 'number' ? 'number' : 'text'}
                            inputMode={meta?.type === 'number' ? 'decimal' : undefined}
                            value={item[fk] || ''}
                            onChange={e => updateItem(rowIdx, fk, e.target.value)}
                            onKeyDown={e => e.key === 'Enter' && onItemEnter(rowIdx, fk)}
                            onFocus={e => e.target.select()}
                            className={styles.cellInput}
                            placeholder={suggested ? `${suggested} (margin)` : meta?.label}
                          />
                        )}
                      </td>
                    )
                  })}
                  <td className={styles.amount} data-label="Amount (excl. GST)">₹{calcAmount(item).toFixed(2)}</td>
                  <td className={styles.removeCell}>
                    <button className={styles.removeRow} onClick={() => removeItem(rowIdx)}>×</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <div className={styles.tableFooter}>
          <div className={styles.footerBtns}>
            <button className={styles.addRowBtn} onClick={addItemRow}>+ Add Row</button>
            {wholesale && (
              <button
                className={styles.addRowBtn}
                onClick={fillRates}
                title={`Empty Rate A / B / C = purchase rate + ${margins.A}% / ${margins.B}% / ${margins.C}%`}
              >
                Fill Rate A/B/C
              </button>
            )}
          </div>
          <div className={styles.grandTotal}>
            Subtotal: ₹{grandSubtotal.toFixed(2)}
            &nbsp;−&nbsp;Disc: ₹{liveDiscRs.toFixed(2)} ({liveDiscPct}%)
            &nbsp;+&nbsp;GST: ₹{grandGST.toFixed(2)}
            &nbsp;=&nbsp;<strong>Total: ₹{grandTotal.toFixed(2)}</strong>
          </div>
        </div>
      </section>

      {showAddSupplier && (
        <AddSupplierModal
          onClose={() => setShowAddSupplier(false)}
          onSaved={async sup => {
            upsertSupplier(sup)
            // Phone: the bill carries the supplier and the PC adds it when the bill is
            // imported; the receiver has no supplier endpoint of its own.
            const res = phone ? { ok: true } : await saveSupplierToServer(sup)
            if (!res?.ok) {
              alert(res?.error || 'Could not save supplier to database. Check that the desktop app is running.')
            }
            setSuppliers(getSuppliers())
            onSupplierSelect(getSuppliers().find(s => s.name === sup.name) || sup)
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
