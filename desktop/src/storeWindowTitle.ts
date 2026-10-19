/** The open store's name as the window title -- on every page, with all the room it needs.
 *
 *  The top bar used to carry the store name beside the status, the Retail | Wholesale switch
 *  and the theme button; there was not enough room for all of them (owner, 19 Oct 2026). The
 *  name now heads the window (Tauri title bar; the tab title in a browser) and the Home page.
 */
import { useEffect } from 'react'
import { useBusinessMode } from './wholesale/mode'

export const APP_TITLE = 'Satpuda Core'
/** What the window is called with no store open (tauri.conf.json). */
const DEFAULT_TITLE = 'Satpuda Core Private Limited'

/** "Roshan Wholesale — Satpuda Core"; a wholesale store whose name does not say so is
 *  marked "(Wholesale)". */
export function storeWindowTitle(storeName: string, wholesale = false): string {
  const name = String(storeName || '').trim()
  if (!name) return DEFAULT_TITLE
  const kind = wholesale && !/wholesale/i.test(name) ? ' (Wholesale)' : ''
  return `${name}${kind} — ${APP_TITLE}`
}

function isTauri(): boolean {
  return typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window
}

export function useStoreWindowTitle(storeName: string): void {
  const { isWholesale } = useBusinessMode()
  const title = storeWindowTitle(storeName, isWholesale)
  useEffect(() => {
    document.title = title
    if (!isTauri()) return
    let cancelled = false
    void import('@tauri-apps/api/window')
      .then(({ getCurrentWindow }) => {
        if (!cancelled) return getCurrentWindow().setTitle(title)
      })
      .catch(() => undefined) // an older shell without the permission keeps its own title
    return () => {
      cancelled = true
    }
  }, [title])
}
