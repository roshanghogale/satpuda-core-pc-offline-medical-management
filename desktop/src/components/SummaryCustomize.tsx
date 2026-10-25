/** The small "Customize summary" button beside a page's summary: "Show summary" for the whole
 *  block and a tick per figure. Each tick is saved at once for the whole shop
 *  (pageSummaries.ts). When the summary is off, this button alone stays as the way back. */

import { useEffect, useRef, useState } from 'react'
import {
  isFieldShown,
  isSummaryShown,
  savePageSummaries,
  summaryPageDef,
  usePageSummaries,
  type PageSummaryChange,
} from '../pageSummaries'
import '../styles/summary-customize.css'

function GearIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      <path
        fill="currentColor"
        d="M19.14 12.94a7.07 7.07 0 0 0 0-1.88l2.03-1.58a.5.5 0 0 0 .12-.64l-1.92-3.32a.5.5 0 0 0-.6-.22l-2.39.96a7.03 7.03 0 0 0-1.63-.94l-.36-2.54a.5.5 0 0 0-.5-.42h-3.84a.5.5 0 0 0-.5.42l-.36 2.54c-.59.24-1.13.55-1.63.94l-2.39-.96a.5.5 0 0 0-.6.22L2.65 8.84a.5.5 0 0 0 .12.64l2.03 1.58a7.07 7.07 0 0 0 0 1.88l-2.03 1.58a.5.5 0 0 0-.12.64l1.92 3.32c.13.22.39.31.6.22l2.39-.96c.5.39 1.04.7 1.63.94l.36 2.54c.04.24.25.42.5.42h3.84c.25 0 .46-.18.5-.42l.36-2.54c.59-.24 1.13-.55 1.63-.94l2.39.96c.22.09.47 0 .6-.22l1.92-3.32a.5.5 0 0 0-.12-.64l-2.03-1.58ZM12 15.5A3.5 3.5 0 1 1 12 8.5a3.5 3.5 0 0 1 0 7Z"
      />
    </svg>
  )
}

export function SummaryCustomize({
  page,
  className = '',
}: {
  page: string
  className?: string
}) {
  const def = summaryPageDef(page)
  const prefs = usePageSummaries()
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const boxRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => {
      if (boxRef.current && !boxRef.current.contains(e.target as Node)) setOpen(false)
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.stopPropagation()
        setOpen(false)
      }
    }
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey, true)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey, true)
    }
  }, [open])

  if (!def) return null
  const shown = isSummaryShown(prefs, page)

  const save = async (change: PageSummaryChange[string]) => {
    setBusy(true)
    setError('')
    try {
      await savePageSummaries({ [page]: change })
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not save. Try again.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className={`sum-custom ${className}`.trim()} ref={boxRef}>
      <button
        type="button"
        className="sum-custom-btn"
        title="Customize summary"
        aria-label="Customize summary"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        <GearIcon />
        {shown ? null : <span>Summary off</span>}
      </button>
      {open ? (
        <div className="sum-custom-pop" role="dialog" aria-label="Customize summary">
          <div className="sum-custom-title">{def.label} summary</div>
          <label className="sum-custom-check sum-custom-all">
            <input
              type="checkbox"
              checked={shown}
              disabled={busy}
              onChange={(e) => void save({ hidden: !e.target.checked })}
            />
            <span>Show summary</span>
          </label>
          <div className="sum-custom-fields">
            {def.fields.map((f) => (
              <label key={f.key} className="sum-custom-check">
                <input
                  type="checkbox"
                  checked={isFieldShown(prefs, page, f.key)}
                  disabled={busy || !shown}
                  onChange={(e) => void save({ on: { [f.key]: e.target.checked } })}
                />
                <span>{f.label}</span>
              </label>
            ))}
          </div>
          <div className="sum-custom-note">
            Only hides it on screen. The same on every computer and phone of the shop.
          </div>
          {error ? <div className="sum-custom-error">{error}</div> : null}
        </div>
      ) : null}
    </div>
  )
}
