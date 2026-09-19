import { useEffect, useRef, useState } from 'react'
import { fetchSyncStatus } from '../api'
import type { PageId } from '../keyboard'
import {
  pageAffectedBySync,
  PAYMENTS_CHANGED_EVENT,
  DATA_CHANGED_EVENT,
} from '../syncRefresh'

const POLL_MS = 10000
const DEBOUNCE_MS = 400

/** Poll sync status (fed by DataChangeBus → note_collection_change) and bump refresh nonce. */
export function useSyncRefreshPoller(opts: {
  enabled: boolean
  page: PageId
  online: boolean
  onStatusLine?: (line: string) => void
  /** Called every poll with the online-catalog read failure ("" when fine). */
  onCatalogError?: (message: string) => void
  /** Called every poll with the store-link failure ("" when fine). */
  onStoreLinkError?: (message: string) => void
}) {
  const { enabled, page, online } = opts
  // Held in refs, not read from the closure: these arrive as inline arrows, so
  // naming them in the effect's dependency list tore down and rebuilt the
  // 10-second interval on every render of the shell.
  const onStatusLineRef = useRef(opts.onStatusLine)
  onStatusLineRef.current = opts.onStatusLine
  const onCatalogErrorRef = useRef(opts.onCatalogError)
  onCatalogErrorRef.current = opts.onCatalogError
  const onStoreLinkErrorRef = useRef(opts.onStoreLinkError)
  onStoreLinkErrorRef.current = opts.onStoreLinkError
  const [syncRefreshNonce, setSyncRefreshNonce] = useState(0)
  const pendingColsRef = useRef(new Set<string>())
  const debounceRef = useRef<number | null>(null)
  const pageRef = useRef(page)
  pageRef.current = page

  useEffect(() => {
    // When navigating to a page that still has pending collections, refresh now.
    if (!enabled) return
    const cols = [...pendingColsRef.current]
    if (cols.length && pageAffectedBySync(page, cols)) {
      pendingColsRef.current.clear()
      setSyncRefreshNonce((n) => n + 1)
    }
  }, [enabled, page])

  useEffect(() => {
    if (!enabled) return
    let cancelled = false

    const flush = () => {
      debounceRef.current = null
      const cols = [...pendingColsRef.current]
      if (!cols.length) return
      if (pageAffectedBySync(pageRef.current, cols)) {
        pendingColsRef.current.clear()
        setSyncRefreshNonce((n) => n + 1)
      }
      // Keep pending cols if the active page is unaffected — apply on next navigation.
    }

    const scheduleFlush = () => {
      if (debounceRef.current != null) {
        window.clearTimeout(debounceRef.current)
      }
      debounceRef.current = window.setTimeout(flush, DEBOUNCE_MS)
    }

    const poll = async () => {
      try {
        const s = await fetchSyncStatus()
        if (cancelled) return
        onStatusLineRef.current?.(s.status_line || '')
        onCatalogErrorRef.current?.(s.catalog_error || '')
        onStoreLinkErrorRef.current?.(s.store_link_error || '')
        if (!online) return
        const cols = (s.refresh_collections || []).filter(Boolean)
        if (!cols.length) return
        // 'all' still remounts, but empty/no-op polls no longer emit it from sync_engine.
        cols.forEach((c) => pendingColsRef.current.add(c))
        scheduleFlush()
      } catch {
        /* sync status optional */
      }
    }

    const onPaymentsChanged = () => {
      setSyncRefreshNonce((n) => n + 1)
    }
    const onDataChanged = (ev: Event) => {
      const cols = (ev as CustomEvent<{ collections?: string[] }>).detail
        ?.collections
      if (Array.isArray(cols) && cols.length) {
        cols.filter(Boolean).forEach((c) => pendingColsRef.current.add(c))
      } else {
        pendingColsRef.current.add('medicines')
        pendingColsRef.current.add('purchases')
      }
      flush()
    }
    window.addEventListener(PAYMENTS_CHANGED_EVENT, onPaymentsChanged)
    window.addEventListener(DATA_CHANGED_EVENT, onDataChanged)

    void poll()
    const intervalMs = online ? POLL_MS : 15000
    const timer = window.setInterval(poll, intervalMs)
    return () => {
      cancelled = true
      window.clearInterval(timer)
      window.removeEventListener(PAYMENTS_CHANGED_EVENT, onPaymentsChanged)
      window.removeEventListener(DATA_CHANGED_EVENT, onDataChanged)
      if (debounceRef.current != null) {
        window.clearTimeout(debounceRef.current)
        debounceRef.current = null
      }
    }
  }, [enabled, online])

  return syncRefreshNonce
}
