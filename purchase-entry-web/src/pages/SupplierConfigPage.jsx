import React, { useState, useRef, useEffect } from 'react'
import {
  getSuppliers, upsertSupplier, saveSuppliers,
  getConfigs, saveConfig,
  ALL_MEDICINE_FIELDS, DEFAULT_FIELD_ORDER
} from '../store.js'
import styles from './SupplierConfigPage.module.css'

const EMPTY_SUP = { name:'', address:'', phone:'', gstin:'', dl_numbers:'' }
const SUP_FIELDS = [
  { key:'name',       label:'Supplier Name *' },
  { key:'address',    label:'Address'         },
  { key:'phone',      label:'Phone'           },
  { key:'gstin',      label:'GSTIN'           },
  { key:'dl_numbers', label:'DL Numbers'      },
]

export default function SupplierConfigPage() {
  const [suppliers, setSuppliers] = useState(getSuppliers())
  const [selected,  setSelected]  = useState(null)   // supplier name
  const [form,      setForm]      = useState(EMPTY_SUP)
  const [isNew,     setIsNew]     = useState(false)
  const [order,     setOrder]     = useState([...DEFAULT_FIELD_ORDER])
  const [savedSup,  setSavedSup]  = useState(false)
  const [savedCfg,  setSavedCfg]  = useState(false)
  const dragIdx = useRef(null)
  const formRefs = useRef({})

  function reload() { const s = getSuppliers(); setSuppliers(s); return s }

  function selectSupplier(name) {
    const sup = suppliers.find(s => s.name === name)
    setSelected(name)
    setForm({ ...EMPTY_SUP, ...sup })
    setIsNew(false)
    setSavedSup(false); setSavedCfg(false)
    const configs = getConfigs()
    setOrder(configs[name] ? [...configs[name]] : [...DEFAULT_FIELD_ORDER])
    setTimeout(() => formRefs.current['name']?.focus(), 30)
  }

  function startNew() {
    setSelected(null)
    setForm(EMPTY_SUP)
    setIsNew(true)
    setSavedSup(false); setSavedCfg(false)
    setOrder([...DEFAULT_FIELD_ORDER])
    setTimeout(() => formRefs.current['name']?.focus(), 30)
  }

  function deleteSupplier(name) {
    if (!window.confirm(`Delete supplier "${name}"?`)) return
    const next = suppliers.filter(s => s.name !== name)
    saveSuppliers(next)
    setSuppliers(next)
    if (selected === name) { setSelected(null); setForm(EMPTY_SUP); setIsNew(false) }
  }

  function saveSupplier() {
    if (!form.name.trim()) { formRefs.current['name']?.focus(); return }
    upsertSupplier(form)
    const next = reload()
    setSelected(form.name)
    setIsNew(false)
    setSavedSup(true)
    setTimeout(() => setSavedSup(false), 2000)
  }

  function saveFieldConfig() {
    const name = form.name.trim() || selected
    if (!name) return
    saveConfig(name, order)
    setSavedCfg(true)
    setTimeout(() => setSavedCfg(false), 2000)
  }

  function onFormEnter(key) {
    const keys = SUP_FIELDS.map(f => f.key)
    const i = keys.indexOf(key)
    if (i < keys.length - 1) formRefs.current[keys[i + 1]]?.focus()
    else saveSupplier()
  }

  // drag-reorder
  function onDragStart(i) { dragIdx.current = i }
  function onDrop(i) {
    if (dragIdx.current === null || dragIdx.current === i) return
    const next = [...order]
    const [m] = next.splice(dragIdx.current, 1)
    next.splice(i, 0, m)
    setOrder(next); dragIdx.current = null; setSavedCfg(false)
  }
  function moveUp(i)   { if (i===0) return; const n=[...order];[n[i-1],n[i]]=[n[i],n[i-1]];setOrder(n);setSavedCfg(false) }
  function moveDown(i) { if (i===order.length-1) return; const n=[...order];[n[i],n[i+1]]=[n[i+1],n[i]];setOrder(n);setSavedCfg(false) }
  function resetDefault() { setOrder([...DEFAULT_FIELD_ORDER]); setSavedCfg(false) }

  return (
    <div className={styles.page}>
      <div className={styles.pageTitle}>⚙ Settings — Suppliers &amp; Field Layout</div>

      <div className={styles.layout}>

        {/* ── Left: supplier list ── */}
        <div className={styles.sidebar}>
          <div className={styles.sidebarHeader}>
            <span>Suppliers</span>
            <button className={styles.newBtn} onClick={startNew}>+ New</button>
          </div>
          {suppliers.length === 0 && <div className={styles.empty}>No suppliers yet.</div>}
          {suppliers.map(s => (
            <div
              key={s.name}
              className={`${styles.supItem} ${selected === s.name ? styles.supActive : ''}`}
            >
              <span className={styles.supItemName} onClick={() => selectSupplier(s.name)}>{s.name}</span>
              <button className={styles.delBtn} onClick={() => deleteSupplier(s.name)} title="Delete">🗑</button>
            </div>
          ))}
        </div>

        {/* ── Right: form + field config ── */}
        <div className={styles.right}>
          {!selected && !isNew ? (
            <div className={styles.empty}>← Select a supplier or click "+ New"</div>
          ) : (
            <>
              {/* supplier details form */}
              <div className={styles.card}>
                <div className={styles.cardTitle}>
                  {isNew ? '➕ New Supplier' : `✏ Edit: ${selected}`}
                </div>
                <div className={styles.formGrid}>
                  {SUP_FIELDS.map(({ key, label }) => (
                    <div key={key} className={styles.formField}>
                      <label>{label}</label>
                      <input
                        ref={r => formRefs.current[key] = r}
                        value={form[key] || ''}
                        onChange={e => setForm(p => ({ ...p, [key]: e.target.value }))}
                        onKeyDown={e => e.key === 'Enter' && onFormEnter(key)}
                        className={styles.input}
                        placeholder={label}
                      />
                    </div>
                  ))}
                </div>
                <button
                  className={`${styles.saveBtn} ${savedSup ? styles.saved : ''}`}
                  onClick={saveSupplier}
                >
                  {savedSup ? '✔ Saved!' : 'Save Supplier'}
                </button>
              </div>

              {/* field order config */}
              <div className={styles.card}>
                <div className={styles.cardTitle}>
                  Medicine Field Order
                  <span className={styles.cardHint}> — drag or use ↑↓ to reorder</span>
                </div>
                <div className={styles.fieldList}>
                  {order.map((key, i) => {
                    const meta = ALL_MEDICINE_FIELDS.find(f => f.key === key)
                    return (
                      <div
                        key={key}
                        className={styles.fieldRow}
                        draggable
                        onDragStart={() => onDragStart(i)}
                        onDragOver={e => e.preventDefault()}
                        onDrop={() => onDrop(i)}
                      >
                        <span className={styles.dragHandle}>⠿</span>
                        <span className={styles.fieldPos}>{i + 1}</span>
                        <span className={styles.fieldLabel}>{meta?.label}</span>
                        <span className={styles.fieldKey}>{key}</span>
                        <div className={styles.arrows}>
                          <button onClick={() => moveUp(i)}   disabled={i === 0}>↑</button>
                          <button onClick={() => moveDown(i)} disabled={i === order.length - 1}>↓</button>
                        </div>
                      </div>
                    )
                  })}
                </div>
                <div className={styles.cfgActions}>
                  <button className={styles.resetBtn} onClick={resetDefault}>Reset to Default</button>
                  <button
                    className={`${styles.saveBtn} ${savedCfg ? styles.saved : ''}`}
                    onClick={saveFieldConfig}
                  >
                    {savedCfg ? '✔ Saved!' : 'Save Field Order'}
                  </button>
                </div>
              </div>
            </>
          )}
        </div>

      </div>
    </div>
  )
}
