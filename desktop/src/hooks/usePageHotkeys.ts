import { createContext, useContext, useEffect, type RefObject } from 'react'
import { isTypingTarget } from '../keyboard'

/**
 * Whether the page this hook is used on is the one on screen.
 *
 * Every page in this app stays MOUNTED once visited and is hidden with
 * display:none, so a window-level keydown listener keeps answering from behind
 * whatever the shop is actually looking at. The seven routed pages pass
 * `enabled` themselves; the panels nested inside Settings could not, because
 * nothing handed the flag down to them -- so pressing F5 to save a bill also
 * ran the hidden Contacts or Ledger save. This carries it down instead.
 */
export const PageActiveContext = createContext(true)

/** Scroll a table section into view and focus the first interactive cell. */
export function focusTableSection(
  ref: RefObject<HTMLElement | null>,
  opts?: { preferInput?: boolean },
): boolean {
  const el = ref.current
  if (!el) return false
  el.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
  if (opts?.preferInput !== false) {
    const input = el.querySelector<HTMLElement>(
      'input:not([disabled]), button:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])',
    )
    if (input) {
      input.focus()
      return true
    }
  }
  if (el.tabIndex < 0) el.tabIndex = -1
  el.focus()
  return true
}

/** Bind page keyboard shortcuts matching classic Tk PageBindings. */
export function usePageHotkeys(opts: {
  onExport?: () => void
  onPrintLast?: () => void
  onFocusFilter?: () => void
  onApplyFilter?: () => void
  onClearFilter?: () => void
  onSave?: () => void
  onClear?: () => void
  onF2?: () => void
  onF3?: () => void
  onLetter?: (key: string) => void
  enabled?: boolean
}) {
  const {
    onExport,
    onPrintLast,
    onFocusFilter,
    onApplyFilter,
    onClearFilter,
    onSave,
    onClear,
    onF2,
    onF3,
    onLetter,
  } = opts

  // Not `enabled = true`: a panel that says nothing should inherit whether its
  // page is on screen, not assume it is.
  const pageActive = useContext(PageActiveContext)
  const enabled = opts.enabled ?? pageActive

  useEffect(() => {
    if (!enabled) return
    const onKey = (e: KeyboardEvent) => {
      const typing = isTypingTarget(e.target)

      if (e.key === 'F5' || (e.ctrlKey && e.key.toLowerCase() === 'g')) {
        if (onSave && !e.altKey && !e.metaKey) {
          e.preventDefault()
          onSave()
          return
        }
      }
      if (e.key === 'F6' && onClear && !e.ctrlKey && !e.altKey) {
        e.preventDefault()
        onClear()
        return
      }
      if (e.key === 'F2' && onF2 && !e.ctrlKey && !e.altKey && !e.metaKey) {
        e.preventDefault()
        onF2()
        return
      }
      if (e.key === 'F3' && onF3 && !e.ctrlKey && !e.altKey && !e.metaKey) {
        e.preventDefault()
        onF3()
        return
      }

      if (e.ctrlKey && !e.altKey && !e.metaKey) {
        const key = e.key.toLowerCase()
        if (key === 'e' && onExport) {
          e.preventDefault()
          onExport()
          return
        }
        if (key === 'p' && onPrintLast) {
          e.preventDefault()
          onPrintLast()
          return
        }
        if (key === 'f' && onFocusFilter) {
          e.preventDefault()
          onFocusFilter()
          return
        }
        // `key` is already lower-cased above, so comparing it to the
        // capitalised 'Enter' could never match and Ctrl+Enter has been dead
        // since it was written -- while the on-screen help still advertises it
        // and the classic screen still honours it.
        if (key === 'enter' && onApplyFilter) {
          e.preventDefault()
          onApplyFilter()
          return
        }
        if (e.shiftKey && key === 'c' && onClearFilter) {
          e.preventDefault()
          onClearFilter()
          return
        }
      }

      if (!typing && !e.ctrlKey && !e.altKey && !e.metaKey && onLetter) {
        const k = e.key.toLowerCase()
        if (['b', 'p', 'i', 'e', 's', 'w'].includes(k)) {
          e.preventDefault()
          onLetter(k)
        }
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [
    enabled,
    onExport,
    onPrintLast,
    onFocusFilter,
    onApplyFilter,
    onClearFilter,
    onSave,
    onClear,
    onF2,
    onF3,
    onLetter,
  ])
}

export function focusPageFilter(selector = '[data-page-filter="primary"]') {
  const el = document.querySelector<HTMLElement>(selector)
  if (!el) return
  el.focus()
  if (el instanceof HTMLInputElement) {
    try {
      el.select()
    } catch {
      /* ignore */
    }
  }
}
