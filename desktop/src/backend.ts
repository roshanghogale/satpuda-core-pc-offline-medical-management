/**
 * Local offline data engine (Python on 127.0.0.1).
 * Started automatically by the Tauri shell — not a public web server.
 * Reads the same store DB + config files as python main.py (Tk).
 */

import { invoke } from '@tauri-apps/api/core'
import { fetchHealth, healthSupportsSettings } from './api'
import { IS_DEMO } from './demoMode'

let cachedReady = false
let cachedReadyAt = 0
const READY_CACHE_MS = 4000

function isTauri(): boolean {
  return typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window
}

/** Ask the shell to start the local engine if needed, then wait until settings API is current. */
export async function ensureLocalEngine(
  timeoutMs = 35000,
): Promise<{ ok: true } | { ok: false; error: string }> {
  // The demonstration copy has NO engine and never will: every /api/ call is
  // answered inside the browser from a recorded snapshot. Asking whether a
  // local engine is running, and whether its API revision is new enough, could
  // only ever fail -- and it did, the moment the revision moved past the one
  // the snapshot was recorded with. The shop saw "Stale local engine (missing
  // settings). Restart with npm run tauri:dev" on a sales demonstration, and
  // the page sat on "Loading banner..." for thirty-five seconds first.
  if (IS_DEMO) return { ok: true }

  if (cachedReady && Date.now() - cachedReadyAt < READY_CACHE_MS) {
    return { ok: true }
  }

  const markReady = () => {
    cachedReady = true
    cachedReadyAt = Date.now()
  }

  try {
    const h = await fetchHealth()
    if (h.ok && healthSupportsSettings(h)) {
      markReady()
      return { ok: true }
    }
  } catch {
    /* engine not up yet */
  }

  if (isTauri()) {
    try {
      await invoke<string>('ensure_desktop_api')
    } catch (e) {
      // Still try health — setup() may have started the engine.
      console.warn('ensure_desktop_api invoke:', e)
    }
  }

  const start = Date.now()
  let last = 'Waiting for local data…'
  while (Date.now() - start < timeoutMs) {
    if (cachedReady && Date.now() - cachedReadyAt < READY_CACHE_MS) {
      return { ok: true }
    }
    try {
      const h = await fetchHealth()
      if (h.ok && healthSupportsSettings(h)) {
        markReady()
        return { ok: true }
      }
      last = h.ok
        ? 'Stale local engine (missing settings). Restart the desktop window.'
        : 'Local engine not ready'
      // Ask Tauri again to replace a stale process.
      if (isTauri()) {
        try {
          await invoke<string>('ensure_desktop_api')
        } catch {
          /* ignore */
        }
      }
    } catch (e) {
      last = e instanceof Error ? e.message : String(e)
    }
    await new Promise((r) => setTimeout(r, 400))
  }
  return {
    ok: false,
    error: isTauri()
      ? `Local data engine not ready. ${last}`
      : `Open with npm run tauri:dev (not the browser alone). ${last}`,
  }
}
