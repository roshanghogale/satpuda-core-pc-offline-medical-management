/** The native "choose a folder" window (Tauri dialog plugin).
 *
 *  The engine cannot open one -- the shipped engine has no tkinter -- so the
 *  desktop shell does. Calls the plugin's own command, which is exactly what
 *  @tauri-apps/plugin-dialog's open() sends; the Rust side registers
 *  tauri_plugin_dialog and the capability allows dialog:allow-open.
 *  In a plain browser there is no picker and the text box stays the way in. */
import { invoke } from '@tauri-apps/api/core'

export function canPickFolder(): boolean {
  return typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window
}

/** The folder the shop chose, or null when the window was closed without one. */
export async function pickFolder(opts?: {
  startAt?: string
  title?: string
}): Promise<string | null> {
  if (!canPickFolder()) return null
  const res = await invoke<string | string[] | null>('plugin:dialog|open', {
    options: {
      directory: true,
      multiple: false,
      title: opts?.title || 'Choose a folder',
      defaultPath: (opts?.startAt || '').trim() || undefined,
    },
  })
  if (Array.isArray(res)) return res[0] || null
  return res || null
}
