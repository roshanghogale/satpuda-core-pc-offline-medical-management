/**
 * Doctor margin rules (owner, 22 + 23 Oct 2026): which medicines give a doctor a margin, and how.
 *
 * Rules for "All doctors" and, where a doctor is different, that doctor's own (a type the doctor
 * has no rule for follows All doctors). Per medicine type, one of two ways (or none):
 *   On profit (groups)   only the products picked into a group count (tick boxes, Select all,
 *                        search, stock; a product can be limited to some of its batches), each
 *                        group with its own margin: MRP − rate (the owner's Excel), % of MRP, or
 *                        a fixed amount per unit. A group can be "All products of this type".
 *                        A product in no group is not in the report.
 *   On total sales (%)   X% of the total sale value of every product of those types.
 * Kept by the engine (core/doctor_margin_rules.py, rules version 2) as one shop setting, synced
 * to every device.
 */

import { useEffect, useMemo, useState } from 'react'
import { dmCall, money } from './DoctorMarginRulesReport'

type Kind = 'mrp_rate' | 'pct_mrp' | 'fixed' | 'none'
type Margin = { kind: Kind; value: number }
type Item = { product: string; batches: string[] }
type Group = { name: string; margin: Margin; all: boolean; items: Item[] }
type TypeMode = 'none' | 'groups' | 'sales'
type TypeRule = { type: string; mode: TypeMode; groups: Group[] }
type Basis = 'purchase' | 'purchase_free'
type RuleSet = { doctor: string; rate_basis: Basis | null; sales_pct: number | null; types: TypeRule[] }
type Rules = { version: number; rate_basis: Basis; updated: string; sets: RuleSet[] }

const MODE_TEXT: Record<TypeMode, string> = {
  none: 'No margin',
  groups: 'On profit (groups)',
  sales: 'On total sales (%)',
}

type Product = {
  product: string
  type: string
  stock: number
  pack: string
  company: string
  contents: string
  batches: { batch: string; stock: number; expiry: string; mrp: number }[]
}

const KIND_TEXT: Record<Kind, string> = {
  mrp_rate: 'MRP − rate',
  pct_mrp: '% of MRP',
  fixed: '₹ per unit',
  none: 'No margin',
}

function lc(s: string) {
  return s.trim().toLowerCase()
}

function marginText(m: Margin | null) {
  if (!m || m.kind === 'none') return 'No margin'
  if (m.kind === 'pct_mrp') return `${m.value}% of MRP`
  if (m.kind === 'fixed') return `₹${money(m.value)} / unit`
  return 'MRP − rate'
}

function pctText(p: number) {
  return `${Number((Number(p) || 0).toFixed(4))}%`
}

function typeSummary(r: TypeRule | undefined, salesPct: number) {
  if (!r || r.mode === 'none') return 'No margin'
  if (r.mode === 'sales') return `${pctText(salesPct)} of total sales`
  if (r.groups.length === 1 && r.groups[0].all) return `All products: ${marginText(r.groups[0].margin)}`
  const picked = r.groups.reduce((n, g) => n + (g.all ? 0 : g.items.length), 0)
  return `${r.groups.length} group(s), ${picked} product(s)${r.groups.some((g) => g.all) ? ' + all products' : ''}`
}

function MarginEdit({ value, onChange, allowNone }: { value: Margin | null; onChange: (m: Margin | null) => void; allowNone?: boolean }) {
  const kind: Kind = value?.kind || 'none'
  return (
    <span className="dmr-margin">
      <select
        className="settings-input"
        value={kind}
        aria-label="Margin kind"
        onChange={(e) => {
          const k = e.target.value as Kind
          onChange(k === 'none' ? (allowNone ? { kind: 'none', value: 0 } : null) : { kind: k, value: k === 'mrp_rate' ? 0 : value?.value || 0 })
        }}
      >
        <option value="mrp_rate">{KIND_TEXT.mrp_rate}</option>
        <option value="pct_mrp">{KIND_TEXT.pct_mrp}</option>
        <option value="fixed">{KIND_TEXT.fixed}</option>
        {allowNone ? <option value="none">{KIND_TEXT.none}</option> : null}
      </select>
      {kind === 'pct_mrp' || kind === 'fixed' ? (
        <input
          type="number"
          className="settings-input dm-num"
          min={0}
          max={kind === 'pct_mrp' ? 100 : undefined}
          step={kind === 'pct_mrp' ? 0.5 : 0.01}
          value={value?.value ?? 0}
          aria-label={kind === 'pct_mrp' ? 'Percent of MRP' : 'Rupees per unit'}
          onChange={(e) => onChange({ kind, value: Number(e.target.value) || 0 })}
        />
      ) : null}
    </span>
  )
}

/** Product list of one type (image 13): tick boxes, Select all, search, stock; batches of the focused product. */
function ProductPicker({
  type,
  initial,
  others,
  onDone,
  onClose,
}: {
  type: string
  initial: Item[]
  /** product -> the other group it is already in (shown, still allowed). */
  others: Record<string, string>
  onDone: (items: Item[]) => void
  onClose: () => void
}) {
  const [products, setProducts] = useState<Product[] | null>(null)
  const [error, setError] = useState('')
  const [find, setFind] = useState('')
  const [sel, setSel] = useState<Record<string, string[]>>(() =>
    Object.fromEntries(initial.map((i) => [i.product, i.batches])),
  )
  const [focus, setFocus] = useState('')

  useEffect(() => {
    void dmCall<{ products: Product[] }>(`/api/reports/doctor-margin/products?type=${encodeURIComponent(type)}`)
      .then((r) => setProducts(r.products))
      .catch((e) => setError(e instanceof Error ? e.message : String(e)))
  }, [type])

  const shown = useMemo(() => {
    const q = find.trim().toUpperCase()
    return (products || []).filter((p) => !q || p.product.includes(q) || p.contents.toUpperCase().includes(q))
  }, [products, find])
  const allTicked = shown.length > 0 && shown.every((p) => p.product in sel)
  const focused = (products || []).find((p) => p.product === focus) || null
  const count = Object.keys(sel).length

  const toggle = (name: string, on: boolean) =>
    setSel((s) => {
      const n = { ...s }
      if (on) n[name] = n[name] || []
      else delete n[name]
      return n
    })

  return (
    <div className="modal-backdrop dmr-picker-backdrop" role="presentation" onClick={onClose}>
      <div
        className="modal-card dmr-picker"
        role="dialog"
        aria-modal="true"
        aria-label={`Product list — ${type}`}
        onClick={(e) => e.stopPropagation()}
        onKeyDown={(e) => {
          if (e.key === 'Escape') {
            e.stopPropagation()
            onClose()
          }
        }}
      >
        <div className="modal-head">
          <h2>Product list — {type}</h2>
          <button type="button" className="icon-btn" onClick={onClose} aria-label="Close">
            ✕
          </button>
        </div>
        <div className="modal-body dmr-picker-body">
          <div className="dmr-picker-left">
            <input
              className="settings-input"
              placeholder="Search product or contents…"
              value={find}
              autoFocus
              onChange={(e) => setFind(e.target.value)}
            />
            {error ? <p className="page-error">{error}</p> : null}
            <div className="dmr-picker-table">
              <table className="sat-table dm-table">
                <thead>
                  <tr>
                    <th className="dmr-tick">
                      <label title="Select all shown">
                        <input
                          type="checkbox"
                          checked={allTicked}
                          onChange={(e) =>
                            setSel((s) => {
                              const n = { ...s }
                              for (const p of shown) {
                                if (e.target.checked) n[p.product] = n[p.product] || []
                                else delete n[p.product]
                              }
                              return n
                            })
                          }
                        />
                        <span>All</span>
                      </label>
                    </th>
                    <th className="num">Stock</th>
                    <th>Product name</th>
                    <th>Pack</th>
                    <th>Company</th>
                    <th>Contents</th>
                  </tr>
                </thead>
                <tbody>
                  {products === null ? (
                    <tr>
                      <td colSpan={6} className="muted">
                        Loading…
                      </td>
                    </tr>
                  ) : !shown.length ? (
                    <tr>
                      <td colSpan={6} className="muted">
                        No {type} product{find ? ' matches' : ' in the medicine list'}.
                      </td>
                    </tr>
                  ) : (
                    shown.map((p) => (
                      <tr
                        key={p.product}
                        className={`${focus === p.product ? 'dmr-focus' : ''} ${p.product in sel ? 'dmr-ticked' : ''}`}
                        onClick={() => setFocus(p.product)}
                      >
                        <td className="dmr-tick">
                          <input
                            type="checkbox"
                            checked={p.product in sel}
                            aria-label={`Pick ${p.product}`}
                            onClick={(e) => e.stopPropagation()}
                            onChange={(e) => {
                              toggle(p.product, e.target.checked)
                              setFocus(p.product)
                            }}
                          />
                        </td>
                        <td className="num">{p.stock}</td>
                        <td className="dm-wrap">
                          {p.product}
                          {others[p.product] ? <span className="dmr-tag">in {others[p.product]}</span> : null}
                          {sel[p.product]?.length ? <span className="dmr-tag dmr-tag-batch">{sel[p.product].length} batch(es)</span> : null}
                        </td>
                        <td>{p.pack}</td>
                        <td className="dm-wrap">{p.company}</td>
                        <td className="dm-wrap dmr-contents">{p.contents}</td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          </div>
          <aside className="dmr-picker-side">
            {focused ? (
              <>
                <h3>{focused.product}</h3>
                <dl>
                  <dt>Type</dt>
                  <dd>{focused.type}</dd>
                  <dt>Pack</dt>
                  <dd>{focused.pack || '—'}</dd>
                  <dt>Company</dt>
                  <dd>{focused.company || '—'}</dd>
                  <dt>Stock</dt>
                  <dd>{focused.stock}</dd>
                </dl>
                <h4>Batches</h4>
                {!(focused.product in sel) ? (
                  <p className="muted">Tick the product to give it a margin; then choose its batches here.</p>
                ) : (
                  <>
                    <label className="dm-types-item">
                      <input
                        type="radio"
                        checked={!sel[focused.product].length}
                        onChange={() => setSel((s) => ({ ...s, [focused.product]: [] }))}
                      />
                      <span>All batches (also batches bought later)</span>
                    </label>
                    <label className="dm-types-item">
                      <input
                        type="radio"
                        checked={sel[focused.product].length > 0}
                        disabled={!focused.batches.length}
                        onChange={() =>
                          setSel((s) => ({ ...s, [focused.product]: focused.batches.slice(0, 1).map((b) => b.batch) }))
                        }
                      />
                      <span>Only these batches</span>
                    </label>
                    {sel[focused.product].length ? (
                      <div className="dmr-batches">
                        {focused.batches.map((b) => {
                          const on = sel[focused.product].includes(b.batch)
                          return (
                            <label key={b.batch} className="dm-types-item">
                              <input
                                type="checkbox"
                                checked={on}
                                onChange={(e) =>
                                  setSel((s) => {
                                    const cur = s[focused.product] || []
                                    const next = e.target.checked ? [...cur, b.batch] : cur.filter((x) => x !== b.batch)
                                    return { ...s, [focused.product]: next.length ? next : [] }
                                  })
                                }
                              />
                              <span className="dmr-batch-name">{b.batch}</span>
                              <span className="muted">
                                stock {b.stock}
                                {b.expiry ? ` · exp ${b.expiry.slice(0, 7)}` : ''} · MRP {money(b.mrp)}
                              </span>
                            </label>
                          )
                        })}
                      </div>
                    ) : null}
                  </>
                )}
              </>
            ) : (
              <p className="muted">Click a product to see its batches.</p>
            )}
          </aside>
        </div>
        <div className="modal-foot dm-foot">
          <span className="muted">{count} product(s) picked</span>
          <span className="dm-foot-gap" />
          <button type="button" className="btn btn-neutral" onClick={onClose}>
            Cancel
          </button>
          <button
            type="button"
            className="btn btn-primary"
            onClick={() => onDone(Object.entries(sel).map(([product, batches]) => ({ product, batches })))}
          >
            OK
          </button>
        </div>
      </div>
    </div>
  )
}

type Payload = { rules: Rules; doctors: string[]; types: string[] }

export function DoctorMarginRulesEditor({ onClose, onSaved }: { onClose: () => void; onSaved: () => void }) {
  const [data, setData] = useState<Payload | null>(null)
  const [rules, setRules] = useState<Rules | null>(null)
  const [setKey, setSetKey] = useState('')
  const [typeKey, setTypeKey] = useState('')
  const [picker, setPicker] = useState<{ group: number } | null>(null)
  const [addDoctor, setAddDoctor] = useState('')
  const [dirty, setDirty] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    void dmCall<Payload>('/api/reports/doctor-margin/rules')
      .then((p) => {
        setData(p)
        setRules(p.rules)
      })
      .catch((e) => setError(e instanceof Error ? e.message : String(e)))
  }, [])

  const types = useMemo(() => {
    const out: string[] = []
    const seen = new Set<string>()
    for (const t of [...(data?.types || []), ...(rules?.sets.flatMap((s) => s.types.map((r) => r.type)) || [])]) {
      if (t && !seen.has(lc(t))) {
        seen.add(lc(t))
        out.push(t)
      }
    }
    return out
  }, [data, rules])

  if (!rules || !data) {
    return (
      <div className="modal-backdrop dmr-editor-backdrop" role="presentation">
        <div className="modal-card dmr-editor" role="dialog" aria-modal="true" aria-label="Doctor margin rules">
          <div className="modal-head">
            <h2>Doctor margin rules</h2>
            <button type="button" className="icon-btn" onClick={onClose} aria-label="Close">
              ✕
            </button>
          </div>
          <div className="modal-body">{error ? <p className="page-error">{error}</p> : <p className="muted">Loading…</p>}</div>
        </div>
      </div>
    )
  }

  const set = rules.sets.find((s) => s.doctor === setKey) || rules.sets[0]
  const isDefault = set.doctor === ''
  const defaults = rules.sets[0]
  const ruleOf = (s: RuleSet, t: string) => s.types.find((r) => lc(r.type) === lc(t))

  const change = (fn: (r: Rules) => void) => {
    const next = JSON.parse(JSON.stringify(rules)) as Rules
    fn(next)
    setRules(next)
    setDirty(true)
  }
  const inSet = (r: Rules) => r.sets.find((s) => s.doctor === set.doctor)!

  const setTypeRule = (t: string, fn: (rule: TypeRule) => void) =>
    change((r) => {
      const s = inSet(r)
      let rule = ruleOf(s, t)
      if (!rule) {
        rule = { type: t, mode: 'none', groups: [] }
        s.types.push(rule)
      }
      fn(rule)
    })

  const setMode = (t: string, mode: TypeMode | 'default') => {
    if (mode === 'default') {
      change((r) => {
        const s = inSet(r)
        s.types = s.types.filter((x) => lc(x.type) !== lc(t))
      })
      return
    }
    setTypeRule(t, (rule) => {
      rule.mode = mode
    })
    setTypeKey(t)
  }

  const save = async () => {
    setBusy(true)
    setError('')
    try {
      const out = await dmCall<{ rules: Rules }>('/api/reports/doctor-margin/rules', { rules })
      setRules(out.rules)
      setDirty(false)
      onSaved()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const close = () => {
    if (dirty && !window.confirm('Close without saving the changed rules?')) return
    onClose()
  }

  const selRule = typeKey ? ruleOf(set, typeKey) : undefined
  const groupsRule = selRule && selRule.mode === 'groups' ? selRule : undefined
  // The % this set's total-sales types get: its own, or All doctors' when it has none.
  const salesPct = set.sales_pct ?? defaults.sales_pct ?? 0
  // Types on total sales for this set, its own and the ones it follows from All doctors.
  const salesTypes = types.filter((t) => {
    const r = ruleOf(set, t) || (isDefault ? undefined : ruleOf(defaults, t))
    return r?.mode === 'sales'
  })
  const profitTypes = types.filter((t) => {
    const r = ruleOf(set, t) || (isDefault ? undefined : ruleOf(defaults, t))
    return r?.mode === 'groups'
  })
  const freeDoctors = data.doctors.filter((d) => !rules.sets.some((s) => s.doctor === d))
  const otherGroups = (gi: number): Record<string, string> => {
    const out: Record<string, string> = {}
    groupsRule?.groups.forEach((g, i) => {
      if (i !== gi) for (const it of g.items) out[it.product] = g.name
    })
    return out
  }
  const emptyGroups = groupsRule?.groups.filter((g) => !g.all && !g.items.length).length || 0

  return (
    <div className="modal-backdrop dmr-editor-backdrop" role="presentation" onClick={close}>
      <div
        className="modal-card dmr-editor"
        role="dialog"
        aria-modal="true"
        aria-label="Doctor margin rules"
        onClick={(e) => e.stopPropagation()}
        onKeyDown={(e) => {
          if (e.key === 'Escape' && !picker) {
            e.stopPropagation()
            close()
          }
        }}
      >
        <div className="modal-head">
          <h2>Doctor margin rules</h2>
          <button type="button" className="icon-btn" onClick={close} aria-label="Close">
            ✕
          </button>
        </div>
        <div className="modal-body dmr-editor-body">
          <nav className="dmr-sets" aria-label="Rules for">
            <span className="dm-label">Rules for</span>
            {rules.sets.map((s) => (
              <button
                key={s.doctor || '*'}
                type="button"
                className={`dmr-set ${s.doctor === set.doctor ? 'on' : ''}`}
                onClick={() => {
                  setSetKey(s.doctor)
                  setTypeKey('')
                }}
              >
                <b>{s.doctor || 'All doctors'}</b>
                <span className="muted">
                  {s.doctor ? `${s.types.length} type(s) of its own` : 'default for every doctor'}
                </span>
              </button>
            ))}
            <div className="dmr-add-doctor">
              <select className="settings-input" value={addDoctor} onChange={(e) => setAddDoctor(e.target.value)}>
                <option value="">Doctor with different rules…</option>
                {freeDoctors.map((d) => (
                  <option key={d} value={d}>
                    {d}
                  </option>
                ))}
              </select>
              <button
                type="button"
                className="btn btn-neutral btn-sm"
                disabled={!addDoctor}
                onClick={() => {
                  change((r) => {
                    r.sets.push({ doctor: addDoctor, rate_basis: null, sales_pct: null, types: [] })
                  })
                  setSetKey(addDoctor)
                  setTypeKey('')
                  setAddDoctor('')
                }}
              >
                Add
              </button>
            </div>
          </nav>

          <section className="dmr-types">
            <div className="dmr-set-head">
              <h3>{isDefault ? 'All doctors' : set.doctor}</h3>
              <label className="dmr-basis">
                <span className="dm-label">Rate used for MRP − rate</span>
                <select
                  className="settings-input"
                  value={isDefault ? rules.rate_basis : set.rate_basis || ''}
                  onChange={(e) =>
                    change((r) => {
                      if (isDefault) r.rate_basis = e.target.value as Basis
                      else inSet(r).rate_basis = (e.target.value || null) as Basis | null
                    })
                  }
                >
                  {!isDefault ? <option value="">Same as all doctors</option> : null}
                  <option value="purchase">Purchase rate (as billed)</option>
                  <option value="purchase_free">Purchase rate with free included</option>
                </select>
              </label>
              <label className="dmr-basis dmr-pct">
                <span className="dm-label">% of total sales</span>
                <span className="dmr-pct-box">
                  <input
                    type="number"
                    className="settings-input dm-num"
                    min={0}
                    max={100}
                    step={0.5}
                    value={isDefault ? (set.sales_pct ?? 0) : (set.sales_pct ?? '')}
                    placeholder={isDefault ? '0' : `${defaults.sales_pct ?? 0} (all)`}
                    aria-label="Percent of total sales"
                    onChange={(e) =>
                      change((r) => {
                        const v = e.target.value
                        inSet(r).sales_pct = v === '' ? (isDefault ? 0 : null) : Math.min(100, Math.max(0, Number(v) || 0))
                      })
                    }
                  />
                  <span>%</span>
                </span>
              </label>
              {!isDefault ? (
                <span className="dmr-set-actions">
                  <button
                    type="button"
                    className="btn btn-neutral btn-sm"
                    title="Start this doctor from the All doctors rules"
                    onClick={() =>
                      change((r) => {
                        inSet(r).types = JSON.parse(JSON.stringify(r.sets[0].types)) as TypeRule[]
                      })
                    }
                  >
                    Copy All doctors rules
                  </button>
                  <button
                    type="button"
                    className="btn btn-neutral btn-sm"
                    onClick={() => {
                      change((r) => {
                        r.sets = r.sets.filter((s) => s.doctor !== set.doctor)
                      })
                      setSetKey('')
                      setTypeKey('')
                    }}
                  >
                    Remove this doctor's rules
                  </button>
                </span>
              ) : null}
            </div>
            <p className="dmr-hint">
              {isDefault
                ? 'Choose for each type one of two ways: On profit — only the products you put in groups, each group with its own margin; or On total sales — a % of the sale value of every product of the type.'
                : 'A type set to "Same as all doctors" follows the All doctors rules. Leave the % box empty to use the All doctors %.'}{' '}
              On profit, rate = the purchase rate of the batch sold (free included: ₹100 for 10 + 5 free = ₹6.67 a unit).
            </p>
            <div className="dmr-ways" aria-label="The two ways">
              <div className={`dmr-way ${profitTypes.length ? 'on' : ''}`}>
                <b>On profit (groups)</b>
                <span>{profitTypes.length ? profitTypes.join(', ') : 'no type'}</span>
              </div>
              <div className={`dmr-way ${salesTypes.length ? 'on' : ''}`}>
                <b>On total sales — {pctText(salesPct)}</b>
                <span>
                  {salesTypes.length
                    ? `${salesTypes.join(', ')}: ${pctText(salesPct)} of their total sale value (MRP rate as billed, less line discount, less returns)`
                    : 'no type'}
                </span>
              </div>
            </div>
            <div className="dmr-types-table">
              <table className="sat-table dm-table">
                <thead>
                  <tr>
                    <th>Type</th>
                    <th>Doctor margin</th>
                    <th>Margin</th>
                  </tr>
                </thead>
                <tbody>
                  {types.map((t) => {
                    const own = ruleOf(set, t)
                    const mode: TypeMode | 'default' = own ? own.mode : isDefault ? 'none' : 'default'
                    return (
                      <tr
                        key={t}
                        className={`${lc(typeKey) === lc(t) ? 'dmr-focus' : ''} ${mode !== 'none' && mode !== 'default' ? 'dmr-on' : ''}`}
                        onClick={() => setTypeKey(t)}
                      >
                        <td>
                          <b>{t}</b>
                        </td>
                        <td>
                          <select
                            className="settings-input"
                            value={mode}
                            aria-label={`${t} margin`}
                            onClick={(e) => e.stopPropagation()}
                            onChange={(e) => setMode(t, e.target.value as TypeMode | 'default')}
                          >
                            {!isDefault ? <option value="default">Same as all doctors</option> : null}
                            <option value="none">{MODE_TEXT.none}</option>
                            <option value="groups">{MODE_TEXT.groups}</option>
                            <option value="sales">{MODE_TEXT.sales}</option>
                          </select>
                        </td>
                        <td>
                          {mode === 'groups' && own ? (
                            <button type="button" className="btn btn-neutral btn-sm" onClick={() => setTypeKey(t)}>
                              {typeSummary(own, salesPct)} ›
                            </button>
                          ) : mode === 'sales' && own ? (
                            <span className="dmr-sales-tag">{typeSummary(own, salesPct)}</span>
                          ) : (
                            <span className="muted">{mode === 'default' ? typeSummary(ruleOf(defaults, t), salesPct) : '—'}</span>
                          )}
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          </section>

          <section className="dmr-groups">
            {groupsRule ? (
              <>
                <div className="dmr-groups-head">
                  <h3>{groupsRule.type}: on profit, by groups</h3>
                  <button
                    type="button"
                    className="btn btn-primary btn-sm"
                    onClick={() => {
                      setTypeRule(groupsRule.type, (r) =>
                        r.groups.push({ name: `Group ${r.groups.length + 1}`, margin: { kind: 'mrp_rate', value: 0 }, all: false, items: [] }),
                      )
                      setPicker({ group: groupsRule.groups.length })
                    }}
                  >
                    + Group
                  </button>
                </div>
                <p className="dmr-hint">
                  Only the products in these groups are in the report; any other {groupsRule.type} product is left out.
                  A product in a one-product group, or limited to some batches, wins over a bigger group; "All products" is
                  the weakest.
                </p>
                {emptyGroups ? <p className="page-error">A group without products is not kept — pick its products.</p> : null}
                {!groupsRule.groups.length ? (
                  <p className="muted">No group yet: press + Group and pick the products that give the doctor a margin.</p>
                ) : null}
                <div className="dmr-group-list">
                  {groupsRule.groups.map((g, gi) => (
                    <div key={gi} className="dmr-group">
                      <div className="dmr-group-row">
                        <input
                          className="settings-input dmr-group-name"
                          value={g.name}
                          aria-label="Group name"
                          onChange={(e) => setTypeRule(groupsRule.type, (r) => (r.groups[gi].name = e.target.value))}
                        />
                        <MarginEdit
                          value={g.margin}
                          allowNone
                          onChange={(m) => setTypeRule(groupsRule.type, (r) => (r.groups[gi].margin = m || { kind: 'none', value: 0 }))}
                        />
                        <label className="dmr-all" title={`Every ${groupsRule.type} product, also ones bought later`}>
                          <input
                            type="checkbox"
                            checked={Boolean(g.all)}
                            onChange={(e) =>
                              setTypeRule(groupsRule.type, (r) => {
                                r.groups[gi].all = e.target.checked
                                if (e.target.checked) r.groups[gi].items = []
                              })
                            }
                          />
                          <span>All products</span>
                        </label>
                        <button
                          type="button"
                          className="btn btn-neutral btn-sm"
                          disabled={Boolean(g.all)}
                          onClick={() => setPicker({ group: gi })}
                        >
                          Pick products…
                        </button>
                        <button
                          type="button"
                          className="icon-btn"
                          aria-label={`Delete ${g.name}`}
                          title="Delete group"
                          onClick={() => setTypeRule(groupsRule.type, (r) => r.groups.splice(gi, 1))}
                        >
                          ✕
                        </button>
                      </div>
                      <div className="dmr-items">
                        {g.all ? (
                          <span className="muted">Every {groupsRule.type} product (also ones bought later).</span>
                        ) : g.items.length ? (
                          g.items.map((it) => (
                            <span key={it.product} className="dmr-item">
                              {it.product}
                              {it.batches.length ? <em> · batch {it.batches.join(', ')}</em> : null}
                              <button
                                type="button"
                                aria-label={`Remove ${it.product}`}
                                onClick={() =>
                                  setTypeRule(groupsRule.type, (r) => {
                                    r.groups[gi].items = r.groups[gi].items.filter((x) => x.product !== it.product)
                                  })
                                }
                              >
                                ×
                              </button>
                            </span>
                          ))
                        ) : (
                          <span className="muted">No products yet.</span>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              </>
            ) : selRule && selRule.mode === 'sales' ? (
              <div className="dmr-sales-panel">
                <h3>{selRule.type}: on total sales</h3>
                <p>
                  The doctor gets <b>{pctText(salesPct)}</b> of the total sale value of every {selRule.type} product he
                  prescribed in the period — the amount on the bill (MRP-based rate × qty, less the line's discount), less
                  returns. No purchase rate is used.
                </p>
                <p className="muted">
                  Example: {salesTypes.join(', ') || selRule.type} sold for ₹80,000 in 10 days at {pctText(salesPct)} → ₹
                  {money((80000 * salesPct) / 100)}. Change the % in “% of total sales” above
                  {isDefault ? '' : ' (empty = the All doctors %)'}.
                </p>
              </div>
            ) : (
              <p className="muted dmr-groups-empty">
                Set a type to <b>On profit (groups)</b> to pick which of its products give a margin — some injections at
                one margin, one injection at another; products in no group give nothing. Or set it to <b>On total sales</b>{' '}
                to give the doctor a % of everything of that type he prescribed.
              </p>
            )}
          </section>
        </div>
        <div className="modal-foot dm-foot">
          {error ? <span className="page-error">{error}</span> : <span className="muted">{dirty ? 'Not saved yet.' : rules.updated ? `Saved ${rules.updated.replace('T', ' ')}` : ''}</span>}
          <span className="dm-foot-gap" />
          <button type="button" className="btn btn-neutral" onClick={close}>
            Close
          </button>
          <button type="button" className="btn btn-primary" disabled={busy || !dirty} onClick={() => void save()}>
            {busy ? 'Saving…' : 'Save rules'}
          </button>
        </div>
        {picker && groupsRule && groupsRule.groups[picker.group] ? (
          <ProductPicker
            type={groupsRule.type}
            initial={groupsRule.groups[picker.group].items}
            others={otherGroups(picker.group)}
            onClose={() => setPicker(null)}
            onDone={(items) => {
              const gi = picker.group
              setTypeRule(groupsRule.type, (r) => (r.groups[gi].items = items))
              setPicker(null)
            }}
          />
        ) : null}
      </div>
    </div>
  )
}
