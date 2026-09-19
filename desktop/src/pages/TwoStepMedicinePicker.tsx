import { useEffect, useMemo, useRef, useState } from 'react'
import {
  fetchMedicineBatches,
  fetchMedicineNames,
  type MedicineBatch,
  type MedicineNameRow,
} from '../pagesApi'

type Props = {
  billDate: string
  /** Qty already on this bill by medicine id (batch list). */
  reserved: Record<string, number>
  navChain?: string
  navOrder?: number
  value: string
  onValueChange: (name: string) => void
  onBatchPicked: (batch: MedicineBatch) => void
  inputRef?: React.RefObject<HTMLInputElement | null>
  /** Enter on an empty name (classic: jump to overall discount). */
  onEmptyEnter?: () => void
}

/**
 * Two-step medicine picker:
 * Step 1 — name list · Step 2 — batch list
 * Stock in the lists is reduced by qty already on this bill.
 */
export function TwoStepMedicinePicker({
  billDate,
  reserved,
  navChain = 'sales',
  navOrder = 8,
  value,
  onValueChange,
  onBatchPicked,
  inputRef,
  onEmptyEnter,
}: Props) {
  const wrapRef = useRef<HTMLDivElement | null>(null)
  const localInputRef = useRef<HTMLInputElement | null>(null)
  const setInputRef = (el: HTMLInputElement | null) => {
    localInputRef.current = el
    if (inputRef && 'current' in inputRef) {
      ;(inputRef as React.MutableRefObject<HTMLInputElement | null>).current =
        el
    }
  }

  const [names, setNames] = useState<MedicineNameRow[]>([])
  const [batches, setBatches] = useState<MedicineBatch[]>([])
  const [step, setStep] = useState<0 | 1 | 2>(0)
  const [hi1, setHi1] = useState(0)
  const [hi2, setHi2] = useState(0)
  const [loading, setLoading] = useState(false)
  /** While resolving batches for a picked name, ignore name-search effect. */
  const pickingRef = useRef(false)
  const loadingRef = useRef(false)
  const nameReqRef = useRef(0)
  const batchReqRef = useRef(0)
  const setLoadingBoth = (v: boolean) => {
    loadingRef.current = v
    setLoading(v)
  }

  /** Hide rows with nothing left. The stock figure is already net.
   *
   *  This used to subtract `reservedByName` from `n.stock` — a SECOND time.
   *  The request at line 112 sends the same reserved map to the engine, and
   *  both branches of core.desktop_sales_service.list_medicine_names take it
   *  off before answering (its own docstring: "stock shown is reduced"). So a
   *  medicine with 363 in stock, with 4 put on the bill, was answered as 359
   *  and then shown as 355. The shop watched four units disappear twice.
   *
   *  Step 2 (the batch list, line 202) never had this bug: it uses the
   *  server's `available` and only filters on it. Step 1 now matches. */
  const displayNames = useMemo(
    () => names.filter((n) => (Number(n.stock) || 0) > 0),
    [names],
  )

  const stepRef = useRef(step)
  const namesRef = useRef(displayNames)
  const batchesRef = useRef(batches)
  const hi1Ref = useRef(hi1)
  const hi2Ref = useRef(hi2)
  stepRef.current = step
  namesRef.current = displayNames
  batchesRef.current = batches
  hi1Ref.current = hi1
  hi2Ref.current = hi2

  const closeList = () => {
    pickingRef.current = false
    setStep(0)
    setNames([])
    setBatches([])
    setHi1(0)
    setHi2(0)
    hi1Ref.current = 0
    hi2Ref.current = 0
  }

  useEffect(() => {
    if (step !== 1) return
    if (pickingRef.current) return
    const req = ++nameReqRef.current
    const t = window.setTimeout(() => {
      void (async () => {
        setLoadingBoth(true)
        try {
          const res = await fetchMedicineNames(value.trim(), {
            bill_date: billDate,
            limit: 40,
            reserved,
          })
          if (req !== nameReqRef.current || stepRef.current !== 1) return
          setNames(res.names || [])
          setHi1(0)
          hi1Ref.current = 0
        } catch {
          if (req !== nameReqRef.current || stepRef.current !== 1) return
          setNames([])
        } finally {
          if (req === nameReqRef.current) setLoadingBoth(false)
        }
      })()
    }, 160)
    return () => window.clearTimeout(t)
  }, [value, billDate, step])

  useEffect(() => {
    if (value.trim()) return
    if (document.activeElement !== localInputRef.current) return
    if (step !== 0) return
    openStep1()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value])

  useEffect(() => {
    if (step === 0) return
    const sel = wrapRef.current?.querySelector<HTMLElement>(
      '.two-step-row.active',
    )
    sel?.scrollIntoView({ block: 'nearest' })
  }, [hi1, hi2, step])

  useEffect(() => {
    const onDoc = (e: MouseEvent) => {
      if (!wrapRef.current?.contains(e.target as Node)) {
        closeList()
      }
    }
    document.addEventListener('mousedown', onDoc)
    return () => document.removeEventListener('mousedown', onDoc)
  }, [])

  const openStep1 = () => {
    pickingRef.current = false
    setBatches([])
    setStep(1)
    const req = ++nameReqRef.current
    setLoadingBoth(true)
    void fetchMedicineNames(value.trim(), {
      bill_date: billDate,
      limit: 40,
      reserved,
    })
      .then((res) => {
        if (req !== nameReqRef.current || stepRef.current !== 1) return
        setNames(res.names || [])
        setHi1(0)
        hi1Ref.current = 0
      })
      .catch(() => {
        if (req !== nameReqRef.current || stepRef.current !== 1) return
        setNames([])
      })
      .finally(() => {
        if (req === nameReqRef.current) setLoadingBoth(false)
      })
  }

  const pickName = async (row: MedicineNameRow) => {
    pickingRef.current = true
    nameReqRef.current += 1
    onValueChange(row.name)
    setNames([])
    setBatches([])
    setStep(2)
    setHi2(0)
    hi2Ref.current = 0
    const req = ++batchReqRef.current
    setLoadingBoth(true)
    try {
      const res = await fetchMedicineBatches(row.name, {
        bill_date: billDate,
        reserved,
      })
      if (req !== batchReqRef.current) return
      // Hide OOS rows client-side (server also filters expired / zero).
      const list = (res.batches || []).filter((b) => {
        const a = Number(b.available)
        const stock = Number(b.stock) || 0
        const avail = Number.isFinite(a) ? a : stock
        return avail > 0
      })
      setBatches(list)
      setHi2(0)
      hi2Ref.current = 0
      if (list.length === 1) {
        pickingRef.current = false
        onBatchPicked(list[0])
        closeList()
      } else {
        // 0 or many: keep step 2 open (empty message if none).
        setStep(2)
        // Keep keyboard focus on the medicine input for arrow/enter.
        window.setTimeout(() => localInputRef.current?.focus(), 0)
      }
    } catch {
      if (req !== batchReqRef.current) return
      setBatches([])
      setStep(2)
      window.setTimeout(() => localInputRef.current?.focus(), 0)
    } finally {
      if (req === batchReqRef.current) {
        pickingRef.current = false
        setLoadingBoth(false)
      }
    }
  }

  const pickBatch = (b: MedicineBatch) => {
    onBatchPicked(b)
    closeList()
  }

  const onKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Escape') {
      if (stepRef.current > 0) {
        e.preventDefault()
        e.stopPropagation()
        closeList()
      }
      return
    }

    if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') return

    if (e.key === 'ArrowDown') {
      if (stepRef.current === 0) return
      e.preventDefault()
      e.stopPropagation()
      const s = stepRef.current
      if (s === 1) {
        const n = namesRef.current
        if (!n.length) return
        const next = Math.min(n.length - 1, hi1Ref.current + 1)
        hi1Ref.current = next
        setHi1(next)
        return
      }
      if (s === 2) {
        const n = batchesRef.current
        if (!n.length) return
        const next = Math.min(n.length - 1, hi2Ref.current + 1)
        hi2Ref.current = next
        setHi2(next)
      }
      return
    }

    if (e.key === 'ArrowUp') {
      if (stepRef.current === 0) return
      e.preventDefault()
      e.stopPropagation()
      const s = stepRef.current
      if (s === 1) {
        const next = Math.max(0, hi1Ref.current - 1)
        hi1Ref.current = next
        setHi1(next)
      }
      if (s === 2) {
        const next = Math.max(0, hi2Ref.current - 1)
        hi2Ref.current = next
        setHi2(next)
      }
      return
    }

    if (e.key === 'Enter') {
      const typed = (localInputRef.current?.value ?? value).trim()
      if (!typed) {
        e.preventDefault()
        e.stopPropagation()
        closeList()
        onEmptyEnter?.()
        return
      }
      const s = stepRef.current
      if (s === 1 && namesRef.current[hi1Ref.current]) {
        e.preventDefault()
        e.stopPropagation()
        void pickName(namesRef.current[hi1Ref.current])
        return
      }
      if (s === 2 && batchesRef.current[hi2Ref.current]) {
        e.preventDefault()
        e.stopPropagation()
        pickBatch(batchesRef.current[hi2Ref.current])
        return
      }
    }

    // Typing while on step 2 returns to name search.
    if (
      stepRef.current === 2 &&
      e.key.length === 1 &&
      !e.ctrlKey &&
      !e.metaKey &&
      !e.altKey
    ) {
      pickingRef.current = false
      setStep(1)
      setBatches([])
    }
  }

  const listOpen = step > 0
  const availOf = (b: MedicineBatch) => {
    const a = Number(b.available)
    if (Number.isFinite(a)) return a
    return Number(b.stock) || 0
  }

  return (
    <div className="two-step-med" ref={wrapRef}>
      <input
        ref={setInputRef}
        type="text"
        value={value}
        role="combobox"
        aria-expanded={listOpen}
        aria-autocomplete="list"
        data-nav-order={navOrder}
        data-nav-chain={navChain}
        data-nav-enter={listOpen ? undefined : 'med-next'}
        data-nav-skip-enter={listOpen ? '1' : undefined}
        placeholder="Search medicine…"
        autoComplete="off"
        onChange={(e) => {
          pickingRef.current = false
          onValueChange(e.target.value)
          setStep(1)
          setBatches([])
        }}
        onFocus={() => {
          if (stepRef.current === 2) return
          if (!value.trim()) openStep1()
        }}
        onBlur={(e) => {
          const next = e.relatedTarget as Node | null
          if (next && wrapRef.current?.contains(next)) return
          // Keep open while async batch fetch is in flight (focus may flicker).
          if (pickingRef.current || loadingRef.current) return
          closeList()
        }}
        onKeyDown={onKeyDown}
      />
      {step === 1 && (
        <div className="two-step-drop" role="listbox">
          <div className="two-step-head">
            <span>Medicine</span>
            <span>Stock</span>
            <span>MRP</span>
            <span>Sch</span>
          </div>
          {loading && !displayNames.length ? (
            <div className="two-step-empty muted">Searching…</div>
          ) : null}
          {!loading && !displayNames.length ? (
            <div className="two-step-empty muted">Type to search medicines</div>
          ) : null}
          {displayNames.map((n, i) => (
            <button
              key={n.name}
              type="button"
              className={`two-step-row${i === hi1 ? ' active' : ''}`}
              onMouseEnter={() => {
                hi1Ref.current = i
                setHi1(i)
              }}
              onMouseDown={(e) => e.preventDefault()}
              onClick={() => void pickName(n)}
            >
              <span className="med-name">{n.name}</span>
              <span className="mono">{n.stock}</span>
              <span className="mono">{Number(n.mrp).toFixed(2)}</span>
              <span>{n.schedule || '—'}</span>
            </button>
          ))}
        </div>
      )}
      {step === 2 && (
        <div className="two-step-drop two-step-drop-batch" role="listbox">
          <div className="two-step-head two-step-head-batch">
            <span>Batch</span>
            <span>Expiry</span>
            <span>Avail</span>
            <span>MRP</span>
            <span>Sch</span>
          </div>
          {loading && !batches.length ? (
            <div className="two-step-empty muted">Loading batches…</div>
          ) : null}
          {!loading && !batches.length ? (
            <div className="two-step-empty muted">
              No batches available for this medicine
            </div>
          ) : null}
          {batches.map((b, i) => (
            <button
              key={`${b.id}-${b.batch}-${b.expiry}`}
              type="button"
              className={`two-step-row two-step-row-batch${i === hi2 ? ' active' : ''}`}
              onMouseEnter={() => {
                hi2Ref.current = i
                setHi2(i)
              }}
              onMouseDown={(e) => e.preventDefault()}
              onClick={() => pickBatch(b)}
            >
              <span className="mono">{b.batch || '(none)'}</span>
              <span className="mono">{b.expiry || '—'}</span>
              <span className="mono">{availOf(b)}</span>
              <span className="mono">{Number(b.mrp).toFixed(2)}</span>
              <span>{b.schedule || '—'}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
