/** Shared Satpuda chrome — panels, fields, tables, buttons. */

import { useLayoutEffect, useEffect, useMemo, useRef, useState } from 'react'
import type { CSSProperties, MouseEvent as ReactMouseEvent, ReactNode, Ref } from 'react'
import { useDesktopUiPrefs } from '../desktopUiPrefsContext'
import { StatusGlyph, statusIconUrl } from '../statusIcons'

export type RowStyle = {
  status?: string
  label?: string
  icon?: string
  icon_src?: string
  badge_text?: string
  color?: string
  full_row?: boolean
  full_row_text?: boolean
  badge?: boolean
  border?: string | null
  display_style?: string
} | null

export function PageRoot({
  children,
  className = '',
  navChain,
}: {
  children: ReactNode
  className?: string
  navChain?: string
}) {
  return (
    <div
      className={`desktop-page ${className}`.trim()}
      data-nav-chain={navChain}
    >
      {children}
    </div>
  )
}

export function Panel({
  title,
  children,
  className = '',
  headRight,
  table,
  bare,
}: {
  title?: string
  children: ReactNode
  className?: string
  headRight?: ReactNode
  table?: boolean
  /** Skip panel-body wrapper (summary footers, custom layouts). */
  bare?: boolean
}) {
  return (
    <section className={`panel ${table ? 'table-panel' : ''} ${className}`.trim()}>
      {title ? (
        <div className={table ? 'table-title' : 'panel-head'}>
          <h2>{title}</h2>
          {headRight}
        </div>
      ) : null}
      {table || bare ? children : <div className="panel-body">{children}</div>}
    </section>
  )
}

/** @deprecated Prefer Panel — kept for Settings / list pages mid-migration */
export function SectionFrame({
  title,
  children,
  className = '',
}: {
  title: string
  children: ReactNode
  className?: string
}) {
  return (
    <Panel title={title} className={`filter-panel ${className}`.trim()}>
      {children}
    </Panel>
  )
}

export function FilterBar({ children }: { children: ReactNode }) {
  return <div className="page-filter-bar">{children}</div>
}

/** Size a table viewport to N visible data rows (Settings → Layout & Lists). */
export function applyVisibleRowHeight(
  wrap: HTMLElement | null,
  visibleRows?: number,
) {
  if (!wrap) return
  if (!visibleRows || visibleRows < 1) {
    wrap.style.removeProperty('height')
    wrap.style.removeProperty('max-height')
    wrap.style.removeProperty('--visible-rows')
    return
  }
  const head =
    wrap.querySelector('thead tr') || wrap.querySelector('thead')
  const row = wrap.querySelector('tbody tr')
  const headH = head?.getBoundingClientRect().height || 32
  const rowH = row?.getBoundingClientRect().height || 28
  const capped = Math.round(headH + visibleRows * rowH)
  wrap.style.setProperty('--visible-rows', String(visibleRows))
  // max-height (not fixed height): shrinks when the window is short so overflow
  // scroll still works instead of clipping under the summary bar.
  wrap.style.removeProperty('height')
  // One rule everywhere: the row count is a CAP, which is what "how many rows
  // are visible" means. List pages used to take it as a FLOOR instead -- the
  // table grew into whatever space the panel offered -- so on Inventory, Sales
  // History and Purchase History the number could only ever make the list
  // taller. A shop that set 5 to fit a short counter screen saw no change at
  // all, and the caption underneath said "5 visible" while showing twenty.
  // max-height, not height, so a short window still scrolls instead of
  // clipping under the summary bar.
  wrap.style.maxHeight = `${capped}px`
}

export function CappedTableWrap({
  visibleRows,
  className = '',
  children,
}: {
  visibleRows: number
  className?: string
  children: ReactNode
}) {
  const ref = useRef<HTMLDivElement>(null)
  useLayoutEffect(() => {
    applyVisibleRowHeight(ref.current, visibleRows)
  })
  return (
    <div
      ref={ref}
      className={['table-scroll', 'table-rows-capped', className]
        .filter(Boolean)
        .join(' ')}
    >
      {children}
    </div>
  )
}

export function Field({
  label,
  children,
  className = '',
  optional,
  hint,
}: {
  label: ReactNode
  children: ReactNode
  className?: string
  optional?: boolean
  hint?: string
}) {
  return (
    <div className={`field ${className}`.trim()}>
      <label className="field-label">
        {label}
        {optional ? <span className="opt"> (optional)</span> : null}
        {hint ? <span className="hintsmall"> {hint}</span> : null}
      </label>
      {children}
    </div>
  )
}

export function SelectWrap({ children }: { children: ReactNode }) {
  return <div className="select-wrap">{children}</div>
}

export function ComboWrap({ children }: { children: ReactNode }) {
  return <div className="combo-wrap">{children}</div>
}

export function DocTabBar({
  tabs,
  active,
  onSelect,
  onAdd,
  onCloseTab,
  hint,
  right,
}: {
  tabs: string[]
  active: number
  onSelect?: (i: number) => void
  onAdd?: () => void
  onCloseTab?: (i: number) => void
  hint?: string
  right?: ReactNode
}) {
  return (
    <div className="doctabbar">
      <div className="doctabs">
        {tabs.map((t, i) => (
          <span
            key={`${t}-${i}`}
            className={`doctab${i === active ? ' active' : ''}`}
          >
            <button
              type="button"
              className="doctab-label"
              onClick={() => onSelect?.(i)}
              title={t}
            >
              <span className="dot" />
              {t}
            </button>
            {onCloseTab && tabs.length > 1 ? (
              <button
                type="button"
                className="doctab-close"
                title="Close bill (F4)"
                aria-label={`Close ${t}`}
                onClick={(e) => {
                  e.stopPropagation()
                  onCloseTab(i)
                }}
              >
                ×
              </button>
            ) : null}
          </span>
        ))}
        {onAdd ? (
          <button type="button" className="doctab-add" onClick={onAdd}>
            +
          </button>
        ) : null}
      </div>
      <div className="doctabbar-right">
        {hint ? <span className="doctab-hint">{hint}</span> : null}
        {right}
      </div>
    </div>
  )
}

export type DocToolItem = {
  label: string
  detail?: string
  kbd?: string
  variant?: 'primary' | 'neutral' | 'warning'
  disabled?: boolean
  onClick: () => void
}

export function DocToolsDialog({
  open,
  title,
  onClose,
  tabs,
  active,
  onSelect,
  onAdd,
  onCloseTab,
  addLabel = 'New tab',
  hint,
  tools = [],
}: {
  open: boolean
  title: string
  onClose: () => void
  tabs: string[]
  active: number
  onSelect?: (i: number) => void
  onAdd?: () => void
  onCloseTab?: (i: number) => void
  addLabel?: string
  hint?: string
  tools?: DocToolItem[]
}) {
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault()
        onClose()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onClose])

  if (!open) return null
  return (
    <div
      className="modal-backdrop"
      role="presentation"
      onClick={onClose}
    >
      <div
        className="modal-card modal-card-tools"
        role="dialog"
        aria-modal="true"
        aria-label={title}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="modal-head">
          <h2>{title}</h2>
          <button
            type="button"
            className="btn btn-neutral btn-sm"
            onClick={onClose}
          >
            Close
          </button>
        </div>
        <div className="modal-body tools-dialog-body">
          <section className="tools-section">
            <h3>Open bills</h3>
            <div className="tools-tab-list">
              {tabs.map((t, i) => {
                const current = i === active
                const canClose = tabs.length > 1
                return (
                  <div
                    key={`${t}-${i}`}
                    className={`tools-tab-row${current ? ' active' : ''}`}
                  >
                    <button
                      type="button"
                      className="tools-tab-main"
                      onClick={() => onSelect?.(i)}
                    >
                      <span className="tools-tab-dot" />
                      <span className="tools-tab-name">{t}</span>
                      {current ? (
                        <span className="tools-tab-badge">Current</span>
                      ) : (
                        <span className="tools-tab-open">Switch</span>
                      )}
                    </button>
                    <button
                      type="button"
                      className="tools-tab-close"
                      disabled={!canClose}
                      title={
                        canClose
                          ? 'Close this bill'
                          : 'At least one bill must stay open'
                      }
                      aria-label={
                        canClose ? `Close ${t}` : 'Cannot close the last bill'
                      }
                      onClick={() => {
                        if (canClose) onCloseTab?.(i)
                      }}
                    >
                      ×
                    </button>
                  </div>
                )
              })}
            </div>
            {onAdd ? (
              <button type="button" className="btn btn-ghost-accent tools-add-btn" onClick={onAdd}>
                + {addLabel}
              </button>
            ) : null}
          </section>
          {tools.length ? (
            <section className="tools-section">
              <h3>Tools</h3>
              <div className="tools-tile-grid">
                {tools.map((tool) => (
                  <button
                    key={tool.label}
                    type="button"
                    className={`tools-tile tools-tile-${tool.variant || 'neutral'}`}
                    disabled={tool.disabled}
                    onClick={tool.onClick}
                  >
                    <span className="tools-tile-label">{tool.label}</span>
                    {tool.detail ? (
                      <span className="tools-tile-detail">{tool.detail}</span>
                    ) : null}
                    {tool.kbd ? <span className="kbd">{tool.kbd}</span> : null}
                  </button>
                ))}
              </div>
            </section>
          ) : null}
          {hint ? <p className="tools-hint">{hint}</p> : null}
        </div>
      </div>
    </div>
  )
}

function formatCell(v: unknown): string {
  if (v === null || v === undefined) return ''
  if (typeof v === 'number') {
    return Number.isInteger(v) ? String(v) : v.toFixed(2)
  }
  return String(v)
}

/** Match Tk inventory tree column widths (`ui/inventory/inventory.py`). */
export const INVENTORY_COLUMN_WIDTHS: Record<string, number> = {
  Name: 180,
  Type: 58,
  Batch: 90,
  Expiry: 75,
  'Days Left': 70,
  Stock: 65,
  Unit: 55,
  MRP: 60,
  'MRP/Tab': 60,
  Rate: 60,
  'Rate/Tab': 60,
  Manufacturer: 110,
  'Supplier Name': 150,
  Schedule: 75,
  Location: 90,
  Status: 130,
  __ind__: 28,
  __status__: 130,
}

/** Match Tk sales history list (`ui/sales/sales_history.py`). */
export const SALES_HISTORY_COLUMN_WIDTHS: Record<string, number> = {
  'Bill No': 85,
  Date: 100,
  Customer: 150,
  Phone: 105,
  Doctor: 120,
  Schedule: 90,
  'Total Amount': 100,
  Discount: 90,
  'Amount Paid': 100,
  'Cash Paid': 90,
  'Online Paid': 90,
  'Previous Due': 100,
  'Due Amount': 100,
  'Credit Amount': 100,
  'Total Due': 100,
  Status: 130,
  __ind__: 28,
  __status__: 130,
}

/** Match Tk purchase history list (`ui/purchase/purchase_history.py`). */
export const PURCHASE_HISTORY_COLUMN_WIDTHS: Record<string, number> = {
  'Purchase No': 48,
  'Bill No': 110,
  Date: 95,
  Supplier: 160,
  Phone: 105,
  'Final Amount': 110,
  'Paid at Entry': 105,
  'Cash Paid': 90,
  'Online Paid': 95,
  'Paid via Payment': 115,
  Returns: 80,
  'Entry Due': 90,
  Status: 90,
  Items: 55,
  __ind__: 28,
  __status__: 150,
}

/** Match Tk medicine view dialog history trees (`ui/inventory/inventory_dialogs.py`). */
export const MED_PURCHASE_HISTORY_WIDTHS: Record<string, number> = {
  Date: 90,
  Bill: 80,
  Supplier: 160,
  Qty: 70,
  Free: 55,
  Total: 90,
  Rate: 70,
  Amount: 80,
}

export const MED_SALES_HISTORY_WIDTHS: Record<string, number> = {
  Date: 90,
  Bill: 80,
  Customer: 160,
  Rate: 70,
  Amount: 80,
}

function distributeFixedColumnWidths(
  columns: string[],
  columnWidths: Record<string, number>,
  containerWidth: number,
): { widths: number[]; fillContainer: boolean } {
  const bases = columns.map((c) => columnWidths[c] ?? 100)
  if (!columns.length || containerWidth <= 0) {
    return { widths: bases, fillContainer: false }
  }
  const totalBase = bases.reduce((a, b) => a + b, 0)
  if (containerWidth <= totalBase) {
    return { widths: bases, fillContainer: false }
  }
  const perCol = (containerWidth - totalBase) / columns.length
  return {
    widths: bases.map((b) => Math.round(b + perCol)),
    fillContainer: true,
  }
}

/** Ellipsis when clipped; pause only when text start is left-aligned, then scroll RTL. */
function OverflowMarquee({
  text,
  className,
}: {
  text: string
  className?: string
}) {
  const wrapRef = useRef<HTMLSpanElement>(null)
  const measureRef = useRef<HTMLSpanElement>(null)
  const trackRef = useRef<HTMLSpanElement>(null)
  const segmentRef = useRef<HTMLSpanElement>(null)
  const prefs = useDesktopUiPrefs()
  const marqueeEnabled = Boolean(prefs.table_text_marquee)
  const [overflow, setOverflow] = useState(false)
  const [segmentWidth, setSegmentWidth] = useState(0)

  useEffect(() => {
    const wrap = wrapRef.current
    const measure = measureRef.current
    if (!wrap || !measure) return
    const check = () => {
      const extra = measure.scrollWidth - wrap.clientWidth
      setOverflow(extra > 2)
    }
    check()
    const ro = new ResizeObserver(check)
    ro.observe(wrap)
    return () => ro.disconnect()
  }, [text])

  useEffect(() => {
    const segment = segmentRef.current
    if (!overflow || !segment) {
      setSegmentWidth(0)
      return
    }
    const measureSegment = () => {
      setSegmentWidth(segment.offsetWidth)
    }
    measureSegment()
    const ro = new ResizeObserver(measureSegment)
    ro.observe(segment)
    return () => ro.disconnect()
  }, [overflow, text])

  useEffect(() => {
    const track = trackRef.current
    if (!marqueeEnabled || !overflow || !track || segmentWidth <= 0) return

    const pauseMs = 4000
    const scrollMs = Math.max(2000, (segmentWidth / 28) * 1000)
    const totalMs = pauseMs + scrollMs
    const pauseEnd = pauseMs / totalMs

    const anim = track.animate(
      [
        { transform: 'translateX(0)', offset: 0 },
        { transform: 'translateX(0)', offset: pauseEnd },
        { transform: `translateX(${-segmentWidth}px)`, offset: 1 },
      ],
      {
        duration: totalMs,
        iterations: Infinity,
        easing: 'linear',
      },
    )

    return () => anim.cancel()
  }, [marqueeEnabled, overflow, segmentWidth, text])

  if (!text) return null

  const showMarquee = marqueeEnabled && overflow

  return (
    <span
      ref={wrapRef}
      className={`cell-marquee${showMarquee ? ' cell-marquee-active' : ''}${className ? ` ${className}` : ''}`}
      title={overflow ? text : undefined}
    >
      <span ref={measureRef} className="cell-marquee-measure" aria-hidden>
        {text}
      </span>
      {showMarquee ? (
        <span ref={trackRef} className="cell-marquee-track" aria-label={text}>
          <span ref={segmentRef} className="cell-marquee-segment">
            {text}
          </span>
          <span className="cell-marquee-segment" aria-hidden>
            {text}
          </span>
        </span>
      ) : (
        <span className="cell-marquee-inner">{text}</span>
      )}
    </span>
  )
}

function StatusBadgeIcon({ style }: { style: NonNullable<RowStyle> }) {
  const status = style.status || ''
  const pngUrl = statusIconUrl(status, style.icon_src)
  const [usePng, setUsePng] = useState(Boolean(pngUrl))

  useEffect(() => {
    setUsePng(Boolean(pngUrl))
  }, [pngUrl, status])

  if (usePng && pngUrl) {
    return (
      <img
        className="ri-status-img"
        src={pngUrl}
        alt=""
        width={16}
        height={16}
        onError={() => setUsePng(false)}
      />
    )
  }
  if (status) {
    return <StatusGlyph status={status} size={16} title={style.label} />
  }
  if (style.icon) {
    return (
      <span className="ri-status-glyph" aria-hidden>
        {style.icon}
      </span>
    )
  }
  return null
}

export function DataTable({
  columns,
  rows,
  rowStyles,
  empty = 'No records',
  numericCols,
  columnWidths,
  tableWrapClassName,
  visibleRows,
  onRowContextMenu,
  onRowDoubleClick,
  onRowClick,
  selectedRowIndex,
}: {
  columns: string[]
  rows: unknown[][]
  rowStyles?: RowStyle[]
  empty?: string
  numericCols?: Set<number> | number[]
  columnWidths?: Record<string, number>
  tableWrapClassName?: string
  /** Settings → Layout & Lists visible row count (classic Treeview height). */
  visibleRows?: number
  onRowContextMenu?: (rowIndex: number, e: ReactMouseEvent) => void
  onRowDoubleClick?: (rowIndex: number) => void
  onRowClick?: (rowIndex: number) => void
  selectedRowIndex?: number | null
}) {
  const nums = Array.isArray(numericCols)
    ? new Set(numericCols)
    : numericCols || new Set<number>()

  const hasFixedCols = Boolean(columnWidths && Object.keys(columnWidths).length)
  const widthFor = (col: string) =>
    columnWidths?.[col] ?? (hasFixedCols ? 100 : undefined)

  const wrapRef = useRef<HTMLDivElement>(null)
  const [colLayout, setColLayout] = useState<{
    widths: number[]
    fillContainer: boolean
  } | null>(null)

  useLayoutEffect(() => {
    applyVisibleRowHeight(wrapRef.current, visibleRows)
  }, [visibleRows, rows.length, columns.length])

  useEffect(() => {
    if (!hasFixedCols || !columnWidths || !wrapRef.current) {
      setColLayout(null)
      return
    }
    const el = wrapRef.current
    const update = () => {
      setColLayout(
        distributeFixedColumnWidths(columns, columnWidths, el.clientWidth),
      )
    }
    update()
    const ro = new ResizeObserver(update)
    ro.observe(el)
    return () => ro.disconnect()
  }, [hasFixedCols, columns, columnWidths, rows.length])

  const resolvedWidths = useMemo(() => {
    if (!hasFixedCols) return []
    if (colLayout?.widths.length === columns.length) return colLayout.widths
    return columns.map((c) => widthFor(c) ?? 100)
  }, [hasFixedCols, colLayout, columns, columnWidths])

  const fillContainer = Boolean(colLayout?.fillContainer)

  const colClass = (col: string, i: number) => {
    const parts: string[] = []
    if (nums.has(i)) parts.push('num')
    if (col.toLowerCase() === 'type') parts.push('col-type')
    if (col === 'Name') parts.push('col-name')
    if (col === 'Customer' || col === 'Supplier') parts.push('col-party')
    if (col.toLowerCase() === 'status' || col === '__status__') {
      parts.push('col-status')
    }
    return parts.length ? parts.join(' ') : undefined
  }

  const interactive = Boolean(
    onRowDoubleClick || onRowContextMenu || onRowClick,
  )
  const wrapCls = [
    'table-scroll',
    'settings-table-wrap',
    visibleRows ? 'table-rows-capped' : '',
    tableWrapClassName,
  ]
    .filter(Boolean)
    .join(' ')

  return (
    <div className={wrapCls} ref={wrapRef}>
      <table
        className={`sat-table settings-table${hasFixedCols ? ' table-cols-fixed' : ''}${fillContainer ? ' table-cols-fill' : ''}`}
      >
        {hasFixedCols ? (
          <colgroup>
            {columns.map((c, i) => {
              const w = resolvedWidths[i] ?? widthFor(c)
              return (
                <col
                  key={c || `c${i}`}
                  style={w ? { width: `${w}px` } : undefined}
                />
              )
            })}
          </colgroup>
        ) : null}
        <thead>
          <tr>
            {columns.map((c, i) => (
              <th key={c || `c${i}`} className={colClass(c, i)}>
                {c}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.length === 0 ? (
            <tr>
              <td colSpan={Math.max(columns.length, 1)} className="muted">
                {empty}
              </td>
            </tr>
          ) : (
            rows.map((row, i) => {
              const style = rowStyles?.[i]
              const statusIdx = columns.findIndex(
                (c) => c.toLowerCase() === 'status',
              )
              const rowCls = [
                'data-row',
                style?.full_row ? 'ri-full-row' : '',
                style?.full_row_text ? 'ri-full-row-text' : '',
                style?.border === 'left' ? 'ri-border-left' : '',
                style?.border === 'right' ? 'ri-border-right' : '',
                interactive ? 'row-interactive' : '',
                selectedRowIndex === i ? 'row-selected' : '',
              ]
                .filter(Boolean)
                .join(' ')
              const rowStyle: CSSProperties | undefined = style?.color
                ? style.full_row
                  ? {
                      background: style.color,
                      color: '#fff',
                      ['--ri-color' as string]: style.color,
                    }
                  : style.full_row_text
                    ? {
                        color: style.color,
                        fontWeight: 600,
                        ['--ri-color' as string]: style.color,
                      }
                    : { ['--ri-color' as string]: style.color }
                : undefined
              return (
                <tr
                  key={i}
                  className={rowCls}
                  style={{
                    ...rowStyle,
                    cursor: interactive ? 'pointer' : undefined,
                  }}
                  onContextMenu={(e) => {
                    if (!onRowContextMenu) return
                    e.preventDefault()
                    onRowContextMenu(i, e)
                  }}
                  onClick={() => onRowClick?.(i)}
                  onDoubleClick={() => onRowDoubleClick?.(i)}
                >
                  {columns.map((col, j) => {
                    const isStatus = j === statusIdx || col === 'Status'
                    if (isStatus && style && (style.status || style.color)) {
                      const text =
                        style.badge_text ||
                        style.label ||
                        [style.icon, style.label].filter(Boolean).join(' ') ||
                        formatCell(row[j])
                      const badgeOnRow = Boolean(style.full_row)
                      return (
                        <td key={j} className={`ri-status-cell${colClass(col, j) ? ` ${colClass(col, j)}` : ''}`}>
                          <span
                            className={`ri-status-badge${badgeOnRow ? ' on-row' : ''}`}
                            style={
                              {
                                color: badgeOnRow ? '#fff' : style.color,
                                borderColor: badgeOnRow
                                  ? 'rgba(255,255,255,0.55)'
                                  : style.color,
                                background: badgeOnRow
                                  ? 'rgba(0,0,0,0.18)'
                                  : undefined,
                              } as CSSProperties
                            }
                          >
                            <StatusBadgeIcon style={style} />
                            {hasFixedCols ? (
                              <OverflowMarquee
                                text={text}
                                className="cell-marquee-in-badge"
                              />
                            ) : (
                              text
                            )}
                          </span>
                        </td>
                      )
                    }
                    const cls = colClass(col, j)
                    const cellText = formatCell(row[j])
                    const useMarquee = hasFixedCols && !nums.has(j) && col !== '__ind__'
                    return (
                      <td
                        key={j}
                        className={cls ? `${cls}${nums.has(j) ? ' mono' : ''}` : undefined}
                      >
                        {useMarquee ? (
                          <OverflowMarquee text={cellText} />
                        ) : (
                          cellText
                        )}
                      </td>
                    )
                  })}
                </tr>
              )
            })
          )}
        </tbody>
      </table>
    </div>
  )
}

export function ScheduleChip({ value }: { value?: string | null }) {
  const v = (value || '').trim().toUpperCase()
  if (!v || v === '—' || v === '-') {
    return <span className="chip chip-none">—</span>
  }
  if (v === 'H1') return <span className="chip chip-H1">H1</span>
  if (v === 'H') return <span className="chip chip-H">H</span>
  return <span className="chip chip-none">{v}</span>
}

export function SummaryBar({
  items,
}: {
  items: { label: string; value: string | number }[]
}) {
  return (
    <div className="page-summary-bar settings-inline-row">
      {items.map((it) => (
        <div key={it.label} className="page-summary-item">
          <span className="muted">{it.label}</span>
          <strong>{it.value}</strong>
        </div>
      ))}
    </div>
  )
}

export function ActionBar({ children }: { children: ReactNode }) {
  return <div className="settings-inline-actions">{children}</div>
}

export type BtnVariant =
  | 'primary'
  | 'secondary'
  | 'neutral'
  | 'ghost-accent'
  | 'warning'

export function ActionBtn({
  label,
  onClick,
  disabled,
  type = 'button',
  variant = 'primary',
  kbd,
  className = '',
  small,
  navOrder,
  navAction,
  navChain,
  buttonRef,
}: {
  label: ReactNode
  onClick?: () => void
  disabled?: boolean
  type?: 'button' | 'submit'
  variant?: BtnVariant
  kbd?: string
  className?: string
  small?: boolean
  navOrder?: number
  navAction?: string
  navChain?: string
  buttonRef?: Ref<HTMLButtonElement>
}) {
  return (
    <button
      ref={buttonRef}
      type={type}
      className={`btn btn-${variant}${small ? ' btn-sm' : ''} ${className}`.trim()}
      disabled={disabled}
      onClick={onClick}
      data-nav-order={navOrder}
      data-nav-action={navAction}
      data-nav-chain={navChain}
    >
      {label}
      {kbd ? <span className="kbd">{kbd}</span> : null}
    </button>
  )
}

export function Note({ children }: { children: ReactNode }) {
  return <p className="settings-note">{children}</p>
}

export function StatusLine({
  error,
  loading,
}: {
  error?: string
  loading?: boolean
}) {
  if (error) return <p className="page-error">{error}</p>
  if (loading) return <p className="muted">Loading…</p>
  return null
}

export function exportRowsCsv(
  filename: string,
  columns: string[],
  rows: unknown[][],
) {
  const esc = (v: unknown) => {
    const s = String(v ?? '')
    if (/[",\n\r]/.test(s)) return `"${s.replace(/"/g, '""')}"`
    return s
  }
  const lines = [
    columns.map(esc).join(','),
    ...rows.map((r) => columns.map((_, j) => esc(r[j])).join(',')),
  ]
  const blob = new Blob([lines.join('\r\n')], {
    type: 'text/csv;charset=utf-8',
  })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename.endsWith('.csv') ? filename : `${filename}.csv`
  a.click()
  URL.revokeObjectURL(url)
}
