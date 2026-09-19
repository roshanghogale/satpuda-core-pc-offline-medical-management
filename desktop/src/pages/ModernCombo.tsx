import { useEffect, useId, useMemo, useRef, useState } from 'react'

import { matchRank, rankByName, NO_MATCH } from '../nameSearchRank'

export type ModernComboItem = {
  id: string
  label: string
  meta?: string
}

type Props = {
  value: string
  onChange: (value: string) => void
  /** Called when user picks a row (click / Enter). */
  onPick?: (item: ModernComboItem) => void
  items: ModernComboItem[]
  loading?: boolean
  placeholder?: string
  /** Show suggestions once typed length reaches this (0 = show on focus). Default 1. */
  minChars?: number
  /** Client-filter `items` by the typed value. */
  filterLocal?: boolean
  openOnFocus?: boolean
  disabled?: boolean
  className?: string
  inputRef?: React.RefObject<HTMLInputElement | null>
  navOrder?: number | string
  navChain?: string
  navEnter?: string
  listLabel?: string
  emptyText?: string
  maxVisible?: number
  /**
   * Enter with the list open picks the highlighted row.
   * Default true. Set false so Enter moves to the next field unless the user
   * arrowed to a row.
   */
  enterPicksHighlight?: boolean
  /** Fired on Enter (after an optional list pick). */
  onEnter?: (picked?: ModernComboItem) => void
  /** Fired when the field blurs (after the suggestion list closes). */
  onBlur?: () => void
  /** Mark as primary page filter for Ctrl+F focus. */
  pageFilter?: boolean
  /** The caller's order carries meaning the alphabet would destroy -- a
   *  newest-first list of bills, say. Matches are still ranked into groups; only
   *  the order inside a group is left alone. */
  keepOrder?: boolean
  /** Fired when the input receives focus. */
  onFocus?: () => void
}

/**
 * Modern searchable list dropdown.
 * Open: ↑/↓ highlight · Enter select · ←/→ leave field (list closes on blur).
 * Closed: ↑/↓/←/→ use normal form field navigation.
 */
export function ModernCombo({
  value,
  onChange,
  onPick,
  items,
  loading = false,
  placeholder,
  minChars = 1,
  filterLocal = false,
  keepOrder = false,
  openOnFocus = true,
  disabled,
  className,
  inputRef,
  navOrder,
  navChain,
  navEnter,
  listLabel = 'Suggestions',
  emptyText = 'No matches',
  maxVisible = 60,
  enterPicksHighlight = true,
  onEnter,
  pageFilter = false,
  onFocus,
  onBlur: onFieldBlur,
}: Props) {
  const wrapRef = useRef<HTMLDivElement | null>(null)
  const localRef = useRef<HTMLInputElement | null>(null)
  const listId = useId()
  const [open, setOpen] = useState(false)
  const [hi, setHi] = useState(0)
  const hiRef = useRef(0)
  const hiTouchedRef = useRef(false)
  hiRef.current = hi

  const setRefs = (el: HTMLInputElement | null) => {
    localRef.current = el
    if (inputRef && 'current' in inputRef) {
      ;(inputRef as React.MutableRefObject<HTMLInputElement | null>).current =
        el
    }
  }

  const filtered = useMemo(() => {
    const q = value.trim()
    let rows = items
    if (filterLocal && q) {
      // Keep matching on label OR meta -- a medicine is often found by its
      // batch, which rides along in meta.
      const ql = q.toLowerCase()
      rows = items.filter((it) =>
        `${it.label} ${it.meta || ''}`.toLowerCase().includes(ql),
      )
    }
    // Rank whatever survived, always. Typing "m" has to put the medicines that
    // START with m at the top; a plain substring match left them wherever the
    // list happened to hold them, so AMOXY sat above MECOVET. Sorting is stable,
    // so a list the server already ranked keeps its own order within each group.
    if (q) {
      rows = rankByName(rows, q, (it) => it.label, keepOrder)
      if (!filterLocal) {
        // Server-backed lists are not re-filtered here, but an entry that
        // answers the query in no way at all still belongs below a real match.
        const hits = rows.filter((it) => matchRank(it.label, q) !== NO_MATCH)
        if (hits.length) {
          rows = hits.concat(
            rows.filter((it) => matchRank(it.label, q) === NO_MATCH),
          )
        }
      }
    }
    return rows.slice(0, maxVisible)
  }, [items, filterLocal, value, maxVisible, keepOrder])

  const filteredRef = useRef(filtered)
  filteredRef.current = filtered

  const meetsMin =
    value.trim().length >= minChars || (openOnFocus && minChars === 0)
  const canShow = open && !disabled && meetsMin

  // Callers build `items` inline, so the array is a new object on every parent
  // render. Depending on its identity reset the highlight each time anything
  // else on the page changed -- the row the pharmacist had arrowed to jumped
  // back to the top mid-keystroke. Depend on what the list actually contains.
  const itemsKey = useMemo(
    () => `${items.length}|${items.map((i) => i.id).join('\u0001')}`,
    [items],
  )
  useEffect(() => {
    setHi(0)
    hiRef.current = 0
    hiTouchedRef.current = false
  }, [value, itemsKey])

  useEffect(() => {
    if (!canShow) return
    const row = wrapRef.current?.querySelector<HTMLElement>(
      '.modern-combo-row.active',
    )
    row?.scrollIntoView({ block: 'nearest' })
  }, [hi, canShow])

  useEffect(() => {
    const onDoc = (e: MouseEvent) => {
      if (!wrapRef.current?.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onDoc)
    return () => document.removeEventListener('mousedown', onDoc)
  }, [])

  const pick = (item: ModernComboItem) => {
    onChange(item.label)
    onPick?.(item)
    setOpen(false)
  }

  const onKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Escape') {
      if (canShow) {
        e.preventDefault()
        e.stopPropagation()
        setOpen(false)
      }
      return
    }

    // ←/→: never steal — form nav moves fields; blur closes the list.
    if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') return

    // ↑/↓ only while the list is open; when closed, form nav owns them.
    if (e.key === 'ArrowDown') {
      if (!canShow) return
      e.preventDefault()
      e.stopPropagation()
      const rows = filteredRef.current
      if (!rows.length) return
      const next = Math.min(rows.length - 1, hiRef.current + 1)
      hiRef.current = next
      hiTouchedRef.current = true
      setHi(next)
      return
    }

    if (e.key === 'ArrowUp') {
      if (!canShow) return
      e.preventDefault()
      e.stopPropagation()
      const next = Math.max(0, hiRef.current - 1)
      hiRef.current = next
      hiTouchedRef.current = true
      setHi(next)
      return
    }

    if (e.key === 'Enter') {
      const rows = filteredRef.current
      const idx = hiRef.current
      const highlighted = canShow ? rows[idx] : undefined
      const shouldPick =
        Boolean(highlighted) &&
        // An empty field is not a choice. Enter on a blank optional Doctor or
        // Village used to commit whichever name happened to sort first, and the
        // shop only found out when it was printed on the bill.
        value.trim().length > 0 &&
        (hiTouchedRef.current || enterPicksHighlight)
      if (shouldPick && highlighted) {
        e.preventDefault()
        e.stopPropagation()
        pick(highlighted)
        // onEnter runs AFTER the pick has been applied. Callers wire it to
        // "now search" while onPick sets the value; called in the same tick it
        // read the pre-pick state, so Enter searched for what had just been
        // replaced -- the second of two queries always won, with the old text.
        if (onEnter) {
          const chosen = highlighted
          window.setTimeout(() => onEnter(chosen), 0)
        }
        return
      }
      if (onEnter) {
        e.preventDefault()
        e.stopPropagation()
        setOpen(false)
        onEnter()
        return
      }
      if (navEnter) {
        e.preventDefault()
        e.stopPropagation()
        setOpen(false)
        localRef.current?.dispatchEvent(
          new CustomEvent('satpuda-nav-action', {
            bubbles: true,
            detail: { action: navEnter },
          }),
        )
        return
      }
      if (canShow) setOpen(false)
    }
  }

  return (
    <div
      className={`modern-combo${className ? ` ${className}` : ''}`}
      ref={wrapRef}
    >
      <input
        ref={setRefs}
        type="text"
        value={value}
        disabled={disabled}
        placeholder={placeholder}
        autoComplete="off"
        role="combobox"
        aria-expanded={canShow}
        aria-controls={listId}
        aria-autocomplete="list"
        data-nav-order={navOrder}
        data-nav-chain={navChain}
        data-nav-enter={canShow ? undefined : navEnter}
        data-nav-skip-enter={onEnter ? '1' : undefined}
        data-page-filter={pageFilter ? 'primary' : undefined}
        onChange={(e) => {
          onChange(e.target.value)
          setOpen(true)
        }}
        onFocus={() => {
          onFocus?.()
          if (openOnFocus) setOpen(true)
        }}
        onBlur={(e) => {
          const next = e.relatedTarget as Node | null
          if (next && wrapRef.current?.contains(next)) return
          setOpen(false)
          onFieldBlur?.()
        }}
        onKeyDown={onKeyDown}
      />
      {canShow ? (
        <div
          id={listId}
          className="modern-combo-drop"
          role="listbox"
          aria-label={listLabel}
        >
          <div className="modern-combo-head">
            <span>{listLabel}</span>
            {loading ? <span className="muted">Searching…</span> : null}
          </div>
          {!loading && filtered.length === 0 ? (
            <div className="modern-combo-empty muted">{emptyText}</div>
          ) : null}
          {filtered.map((item, i) => (
            <button
              key={item.id}
              type="button"
              role="option"
              aria-selected={i === hi}
              className={`modern-combo-row${i === hi ? ' active' : ''}`}
              onMouseEnter={() => {
                hiRef.current = i
                setHi(i)
              }}
              onMouseDown={(e) => e.preventDefault()}
              onClick={() => pick(item)}
            >
              <span className="modern-combo-label">{item.label}</span>
              {item.meta ? (
                <span className="modern-combo-meta muted">{item.meta}</span>
              ) : null}
            </button>
          ))}
        </div>
      ) : null}
    </div>
  )
}
