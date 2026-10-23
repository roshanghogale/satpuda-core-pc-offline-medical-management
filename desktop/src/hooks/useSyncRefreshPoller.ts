import { useEffect, useRef, useState } from 'react'
import { fetchSyncStatus, type LanBrief } from '../api'
import type { PageId } from '../keyboard'
import {
  pageAffectedBySync,
  PAYMENTS_CHANGED_EVENT,
  DATA_CHANGED_EVENT,
} from '../syncRefresh'

const POLL_MS = 10000
/** A Main PC or Counter PC on the LAN (core/lan): a sale on the other PC shows within ~1.5 s. */
const LAN_POLL_MS = 1500
const DEBOUNCE_MS = 400

/** Poll sync status (fed by DataChangeBus → note_collection_change) and bump refresh nonce. */
export function useSyncRefreshPoller(opts: {
  enabled: boolean
  page: PageId
  online: boolean
  /** Offline-first: the background sync pulls other devices' changes in. */
  offlineFirst?: boolean
  onStatusLine?: (line: string) => void
  /** Called every poll with the online-catalog read failure ("" when fine). */
  onCatalogError?: (message: string) => void
  /** Called every poll with the store-link failure ("" when fine). */
  onStoreLinkError?: (message: string) => void
  /** Called every poll with this PC's place on the LAN (role, banner). */
  onLan?: (lan: LanBrief | null) => void
}) {
  const { enabled, page, online } = opts
  const offlineFirst = Boolean(opts.offlineFirst)
  // Held in refs, not read from the closure: these arrive as inline arrows, so
  // naming them in the effect's dependency list tore down and rebuilt the
  // 10-second interval on every render of the shell.
  const onStatusLineRef = useRef(opts.onStatusLine)
  onStatusLineRef.current = opts.onStatusLine
  const onCatalogErrorRef = useRef(opts.onCatalogError)
  onCatalogErrorRef.current = opts.onCatalogError
  const onStoreLinkErrorRef = useRef(opts.onStoreLinkError)
  onStoreLinkErrorRef.current = opts.onStoreLinkError
  const onLanRef = useRef(opts.onLan)
  onLanRef.current = opts.onLan
  // On the LAN (Main PC / Counter PC) the poll runs every 1.5 s instead of 10 s.
  const lanFastRef = useRef(false)
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
        const lan = s.lan || null
        lanFastRef.current = Boolean(lan && lan.role && lan.role !== 'off')
        onLanRef.current?.(lan)
        // Online: the server's hints. Offline-first: what the background sync pulled in from
        // other devices (core/offline_first/worker.py notes it). On the LAN: what the other
        // PCs saved (core/lan/hints.py). Plain Offline: nothing comes in.
        if (!online && !offlineFirst && !lanFastRef.current) return
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

    const intervalMs = online || offlineFirst ? POLL_MS : 15000
    let timer: number | null = null
    const loop = async () => {
      await poll()
      if (cancelled) return
      timer = window.setTimeout(loop, lanFastRef.current ? LAN_POLL_MS : intervalMs)
    }
    void loop()
    return () => {
      cancelled = true
      if (timer != null) window.clearTimeout(timer)
      window.removeEventListener(PAYMENTS_CHANGED_EVENT, onPaymentsChanged)
      window.removeEventListener(DATA_CHANGED_EVENT, onDataChanged)
      if (debounceRef.current != null) {
        window.clearTimeout(debounceRef.current)
        debounceRef.current = null
      }
    }
  }, [enabled, online, offlineFirst])

  return syncRefreshNonce
}
