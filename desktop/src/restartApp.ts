import { invoke } from '@tauri-apps/api/core'

function isTauri(): boolean {
  return typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window
}

/** Full app relaunch (Tauri). Falls back to location.reload in browser-only. */
export async function restartDesktopApp(): Promise<void> {
  if (isTauri()) {
    await invoke('restart_app')
    return
  }
  window.location.reload()
}
