/** Shared pieces of the alert screens (owner, 19 Oct 2026: "the Alert & Monitoring popups
 *  are not good enough -- make them like the Export UI"): the alerts window (startup, the
 *  while-running re-check and Home -> Alerts) and Settings -> Alert & Monitoring.
 *
 *  - AlertGrid: a table with a sticky header, number columns right-aligned, an action on
 *    every row, and (Expired / Near Expiry) rows grouped under the supplier the batch was
 *    bought from, each supplier with its count and value at purchase rate and MRP.
 *  - MenuButton: "Export ▾" / "Print ▾".
 *
 *  Every figure comes from the engine (core/alert_monitoring_service, startup_alerts_data);
 *  the grouping here only adds up the rows on screen, as core/alert_supplier_groups does
 *  for the files.
 */
import { useEffect, useMemo, useRef, useState, type ReactNode, type Ref } from 'react'
import '../../styles/alerts.css'

export const UNKNOWN_SUPPLIER = 'Unknown supplier'
export const VALUE_RATE = 'Value (P.Rate)'
export const VALUE_MRP = 'Value (MRP)'
const SUPPLIER_COLUMNS = ['Supplier', 'Supplier Name']

/** Money columns: two decimals, right-aligned. */
const MONEY = /^(P\.Rate|MRP|Rate|Value|Due Amount|Total Amount|Paid Amount|Selling Rate|Purchase Rate)/
/** Count columns: right-aligned as they are. */
const COUNT = /^(Qty|Stock|Current Stock|Quantity Expired|Available Qty|Days|Remaining Days|Due Days)/

/** Narrower headings for the long column names (the files keep the full names). */
const SHORT_HEAD: Record<string, string> = {
  'Days Expired': 'Days ago',
  'Days Left': 'Days left',
  [VALUE_RATE]: 'Value @ rate',
  [VALUE_MRP]: 'Value @ MRP',
}

export function isMoneyCol(c: string) {
  return MONEY.test(c)
}

export function isNumCol(c: string) {
  return MONEY.test(c) || COUNT.test(c)
}

export function money(v: unknown): string {
  const n = Number(v)
  if (v === '' || v == null || !Number.isFinite(n)) return String(v ?? '')
  return n.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

export function rupees(v: number): string {
  return `₹${money(v)}`
}

export function supplierIndex(columns: string[]): number {
  for (const c of SUPPLIER_COLUMNS) {
    const i = columns.indexOf(c)
    if (i >= 0) return i
  }
  return -1
}

export function supplierLabel(v: unknown): string {
  const s = String(v ?? '').trim()
  return s || UNKNOWN_SUPPLIER
}

export type SupplierGroup = {
  supplier: string
  /** indexes into the rows given */
  idx: number[]
  count: number
  valueRate: number
  valueMrp: number
}

/** Rows cut by supplier, A-Z with "Unknown supplier" last (as the export cuts them). */
export function groupBySupplier(columns: string[], rows: unknown[][]): SupplierGroup[] {
  const at = supplierIndex(columns)
  const vr = columns.indexOf(VALUE_RATE)
  const vm = columns.indexOf(VALUE_MRP)
  const map = new Map<string, SupplierGroup>()
  rows.forEach((r, i) => {
    const name = supplierLabel(at >= 0 ? r[at] : '')
    let g = map.get(name)
    if (!g) {
      g = { supplier: name, idx: [], count: 0, valueRate: 0, valueMrp: 0 }
      map.set(name, g)
    }
    g.idx.push(i)
    g.count += 1
    if (vr >= 0) g.valueRate += Number(r[vr]) || 0
    if (vm >= 0) g.valueMrp += Number(r[vm]) || 0
  })
  return [...map.values()].sort((a, b) => {
    const ua = a.supplier === UNKNOWN_SUPPLIER ? 1 : 0
    const ub = b.supplier === UNKNOWN_SUPPLIER ? 1 : 0
    return ua - ub || a.supplier.localeCompare(b.supplier, 'en', { sensitivity: 'base' })
  })
}

export function sumColumn(columns: string[], rows: unknown[][], name: string): number {
  const i = columns.indexOf(name)
  if (i < 0) return 0
  return rows.reduce<number>((n, r) => n + (Number(r[i]) || 0), 0)
}

/** Small coloured pill for the "days" columns: expired / due soon read at a glance. */
function DaysPill({ col, value }: { col: string; value: unknown }) {
  const n = Number(value)
  if (!Number.isFinite(n) || value === '') return <>{String(value ?? '')}</>
  let tone = 'ok'
  if (/Expired/.test(col)) tone = 'bad'
  else if (/Days Left|Remaining/.test(col)) tone = n <= 30 ? 'bad' : n <= 60 ? 'warn' : 'ok'
  else if (/Due Days/.test(col)) tone = n >= 60 ? 'bad' : n >= 30 ? 'warn' : 'ok'
  return <span className={`alx-pill ${tone}`}>{n}</span>
}

type GridProps = {
  columns: string[]
  rows: unknown[][]
  /** columns not shown (still exported) */
  hidden?: Set<string>
  grouped?: boolean
  selected?: number | null
  onSelect?: (i: number) => void
  onActivate?: (i: number) => void
  /** label of the action button at the end of every row ("Return", "Reorder") */
  rowActionLabel?: string | null
  onRowAction?: (i: number) => void
  /** extra buttons on a supplier's heading (e.g. that supplier's PDF) */
  groupActions?: (g: SupplierGroup) => ReactNode
  empty?: string
  /** rows drawn at most (the rest are in the export) */
  limit?: number
}

/** The alert table: sticky header, row actions, optional supplier groups. */
export function AlertGrid({
  columns,
  rows,
  hidden,
  grouped,
  selected,
  onSelect,
  onActivate,
  rowActionLabel,
  onRowAction,
  groupActions,
  empty = 'Nothing here.',
  limit = 600,
}: GridProps) {
  const supAt = supplierIndex(columns)
  const groupOn = Boolean(grouped && supAt >= 0)
  const shown = useMemo(
    () =>
      columns
        .map((c, i) => ({ c, i }))
        .filter(({ c, i }) => !hidden?.has(c) && !(groupOn && i === supAt)),
    [columns, hidden, groupOn, supAt],
  )
  const groups = useMemo(() => (groupOn ? groupBySupplier(columns, rows) : []), [groupOn, columns, rows])
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({})
  const hasValue = columns.includes(VALUE_RATE) || columns.includes(VALUE_MRP)
  const actionCol = Boolean(rowActionLabel && onRowAction)
  const span = shown.length + (actionCol ? 1 : 0)
  const bodyRef = useRef<HTMLTableSectionElement>(null)

  useEffect(() => {
    if (selected == null || !bodyRef.current) return
    const tr = bodyRef.current.querySelector<HTMLTableRowElement>(`tr[data-i="${selected}"]`)
    tr?.scrollIntoView({ block: 'nearest' })
  }, [selected])

  let drawn = 0
  const renderRow = (i: number) => {
    if (drawn >= limit) return null
    drawn += 1
    const r = rows[i] || []
    return (
      <tr
        key={i}
        data-i={i}
        className={`alx-row${selected === i ? ' is-selected' : ''}`}
        onClick={() => onSelect?.(i)}
        onDoubleClick={() => onActivate?.(i)}
      >
        {shown.map(({ c, i: ci }) => (
          <td key={ci} className={isNumCol(c) ? 'num' : ci === 0 ? 'alx-name' : undefined}>
            {/^(Days|Remaining Days|Due Days)/.test(c) ? (
              <DaysPill col={c} value={r[ci]} />
            ) : isMoneyCol(c) ? (
              money(r[ci])
            ) : (
              String(r[ci] ?? '')
            )}
          </td>
        ))}
        {actionCol ? (
          <td className="alx-act" onClick={(e) => e.stopPropagation()}>
            <button type="button" className="alx-row-btn" onClick={() => onRowAction?.(i)}>
              {rowActionLabel}
            </button>
          </td>
        ) : null}
      </tr>
    )
  }

  const body: ReactNode[] = []
  if (!rows.length) {
    body.push(
      <tr key="empty">
        <td className="alx-empty" colSpan={span}>
          {empty}
        </td>
      </tr>,
    )
  } else if (groupOn) {
    for (const g of groups) {
      const shut = Boolean(collapsed[g.supplier])
      body.push(
        <tr key={`g:${g.supplier}`} className={`alx-group${g.supplier === UNKNOWN_SUPPLIER ? ' is-unknown' : ''}`}>
          <td colSpan={span}>
            <div className="alx-group-line">
              <button
                type="button"
                className="alx-group-toggle"
                aria-expanded={!shut}
                onClick={() => setCollapsed((p) => ({ ...p, [g.supplier]: !shut }))}
              >
                <span className={`alx-caret${shut ? ' shut' : ''}`} aria-hidden>
                  ▾
                </span>
                <span className="alx-group-name">{g.supplier}</span>
              </button>
              <span className="alx-group-count">
                {g.count} batch{g.count === 1 ? '' : 'es'}
              </span>
              {hasValue ? (
                <span className="alx-group-money">
                  <span title="Value at purchase rate">{rupees(g.valueRate)}</span>
                  <span className="alx-dim"> P.Rate · </span>
                  <span title="Value at MRP">{rupees(g.valueMrp)}</span>
                  <span className="alx-dim"> MRP</span>
                </span>
              ) : null}
              <span className="alx-grow" />
              {groupActions ? groupActions(g) : null}
            </div>
          </td>
        </tr>,
      )
      if (!shut) for (const i of g.idx) body.push(renderRow(i))
    }
  } else {
    rows.forEach((_, i) => body.push(renderRow(i)))
  }
  const cut = rows.length > limit

  return (
    <div className="alx-table-wrap">
      <table className="alx-table">
        <thead>
          <tr>
            {shown.map(({ c, i }) => (
              <th key={i} className={isNumCol(c) ? 'num' : undefined} title={c}>
                {SHORT_HEAD[c] || c}
              </th>
            ))}
            {actionCol ? <th className="alx-act" aria-label="Action" /> : null}
          </tr>
        </thead>
        <tbody ref={bodyRef}>{body}</tbody>
      </table>
      {cut ? (
        <div className="alx-more">
          First {limit} of {rows.length} rows shown — Export has every row.
        </div>
      ) : null}
    </div>
  )
}

export type MenuItem =
  | { key: string; label: string; hint?: string; disabled?: boolean; onPick: () => void }
  | { key: string; separator: true }
  | { key: string; heading: string }

/** A button that opens a small menu above or below it ("Export ▾"). */
export function MenuButton({
  label,
  items,
  disabled,
  className = 'btn btn-neutral',
  up = true,
}: {
  label: ReactNode
  items: MenuItem[]
  disabled?: boolean
  className?: string
  /** open upwards (bottom action bar) */
  up?: boolean
}) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!open) return
    const off = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    const esc = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.stopPropagation()
        setOpen(false)
      }
    }
    document.addEventListener('mousedown', off)
    document.addEventListener('keydown', esc, true)
    return () => {
      document.removeEventListener('mousedown', off)
      document.removeEventListener('keydown', esc, true)
    }
  }, [open])
  return (
    <div className="alx-menu-anchor" ref={ref}>
      <button
        type="button"
        className={className}
        disabled={disabled}
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
      >
        {label}
        <span className="alx-menu-caret" aria-hidden>
          ▾
        </span>
      </button>
      {open ? (
        <div className={`alx-menu${up ? ' up' : ''}`} role="menu">
          {items.map((it) =>
            'separator' in it ? (
              <div key={it.key} className="alx-menu-sep" role="separator" />
            ) : 'heading' in it ? (
              <div key={it.key} className="alx-menu-head">
                {it.heading}
              </div>
            ) : (
              <button
                key={it.key}
                type="button"
                role="menuitem"
                className="alx-menu-item"
                disabled={it.disabled}
                onClick={() => {
                  setOpen(false)
                  it.onPick()
                }}
              >
                <span>{it.label}</span>
                {it.hint ? <span className="alx-menu-hint">{it.hint}</span> : null}
              </button>
            ),
          )}
        </div>
      ) : null}
    </div>
  )
}

/** "Group by supplier" switch. */
export function GroupSwitch({ on, onChange }: { on: boolean; onChange: (on: boolean) => void }) {
  return (
    <label className={`alx-switch${on ? ' on' : ''}`}>
      <input type="checkbox" checked={on} onChange={(e) => onChange(e.target.checked)} />
      <span className="alx-switch-track" aria-hidden>
        <span className="alx-switch-knob" />
      </span>
      Group by supplier
    </label>
  )
}

/** Search box with its icon and a clear button. */
export function AlertSearch({
  value,
  onChange,
  placeholder = 'Search medicine, batch, supplier…',
  inputRef,
}: {
  value: string
  onChange: (v: string) => void
  placeholder?: string
  inputRef?: Ref<HTMLInputElement>
}) {
  return (
    <div className="alx-search">
      <svg viewBox="0 0 20 20" width="14" height="14" aria-hidden>
        <circle cx="8.5" cy="8.5" r="5.5" fill="none" stroke="currentColor" strokeWidth="2" />
        <path d="M13 13l4 4" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
      </svg>
      <input ref={inputRef} value={value} placeholder={placeholder} onChange={(e) => onChange(e.target.value)} />
      {value ? (
        <button type="button" className="alx-search-clear" aria-label="Clear search" onClick={() => onChange('')}>
          ✕
        </button>
      ) : null}
    </div>
  )
}

/** Rows whose text has every word of the search. */
export function matchRows(rows: unknown[][], q: string): number[] {
  const words = q.trim().toLowerCase().split(/\s+/).filter(Boolean)
  const out: number[] = []
  rows.forEach((r, i) => {
    if (!words.length) {
      out.push(i)
      return
    }
    const text = r.map((v) => String(v ?? '')).join(' ').toLowerCase()
    if (words.every((w) => text.includes(w))) out.push(i)
  })
  return out
}
