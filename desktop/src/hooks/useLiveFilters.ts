import { useEffect, useRef } from 'react'

/**
 * Filters that apply themselves.
 *
 * On Inventory, Sales History and Purchase History every filter was a plain
 * `useState` with nothing subscribing to it, so choosing a Type or a Due status
 * re-rendered the control and stopped there. The list only moved when the shop
 * clicked "Apply Filter" — and the Ctrl+Enter shortcut that was supposed to
 * stand in for that click has never worked either (usePageHotkeys lowercases the
 * key and then compares it to the capitalised literal 'Enter'). So every look at
 * the day's takings cost two extra actions and the screen lied in between.
 *
 * Two rules, matching the classic Tk screens:
 *   * a discrete control — a dropdown, a date, a toggle — applies at once;
 *   * a text box waits for the typing to stop, 350 ms, as inventory.py does.
 */

/** Run `run` whenever one of `deps` changes, but never on the first render. */
export function useFilterEffect(run: () => void, deps: unknown[], enabled = true) {
  const mounted = useRef(false)
  const runRef = useRef(run)
  runRef.current = run
  useEffect(() => {
    if (!mounted.current) {
      mounted.current = true
      return
    }
    if (!enabled) return
    runRef.current()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps)
}

/**
 * As above, but waits for a pause in typing.
 *
 * 350 ms is the classic inventory search delay, so a burst of keystrokes costs
 * one query rather than one per letter.
 */
export function useDebouncedFilterEffect(
  run: () => void,
  deps: unknown[],
  delayMs = 350,
  enabled = true,
) {
  const mounted = useRef(false)
  const runRef = useRef(run)
  runRef.current = run
  useEffect(() => {
    if (!mounted.current) {
      mounted.current = true
      return
    }
    if (!enabled) return
    const t = window.setTimeout(() => runRef.current(), delayMs)
    return () => window.clearTimeout(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps)
}
