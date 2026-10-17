/**
 * Saving reports, exports and bill PDFs (owner, 17 Oct 2026).
 *
 * These used to leave as a browser download -- a blob link clicked inside the
 * webview. Nothing said where the file went (Downloads, silently) and on some PCs
 * nothing came out, so the shop saw only "Print" and no way to save. Now the
 * engine writes the file into the shop's reports folder and answers with the
 * path; the SavedFileHost dialog says where it is and offers Open / Show in
 * folder. If the engine cannot be asked (an old engine), the old download is
 * the fallback.
 */
import { getApiBase } from './api'

export type SavedFile = {
  path: string
  file_name?: string
  folder?: string
  message?: string
}

export const FILE_SAVED_EVENT = 'satpuda:file-saved'

async function post<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${getApiBase()}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body ?? {}),
  })
  const data = (await res.json().catch(() => ({}))) as T & { error?: string }
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`)
  return data
}

/** Tell the shop where a file went (the dialog is mounted once, in App). */
export function showSavedFile(file: SavedFile) {
  if (!file?.path) return
  window.dispatchEvent(new CustomEvent<SavedFile>(FILE_SAVED_EVENT, { detail: file }))
}

function browserDownload(filename: string, mime: string, contentBase64: string) {
  const bin = atob(contentBase64)
  const bytes = new Uint8Array(bin.length)
  for (let i = 0; i < bin.length; i += 1) bytes[i] = bin.charCodeAt(i)
  const url = URL.createObjectURL(new Blob([bytes], { type: mime }))
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
}

/** Save a file the engine produced as base64 into the reports folder, then show it. */
export async function saveFileAndShow(
  filename: string,
  mime: string,
  contentBase64: string,
): Promise<SavedFile | null> {
  try {
    const saved = await post<SavedFile & { ok?: boolean }>('/api/files/save', {
      filename,
      content_base64: contentBase64,
    })
    if (saved?.path) {
      showSavedFile(saved)
      return saved
    }
  } catch {
    /* an engine without /api/files/save: fall back to the download below */
  }
  browserDownload(filename, mime, contentBase64)
  return null
}

/** The same for text the page built itself (a CSV), as UTF-8. */
export function saveTextAndShow(filename: string, mime: string, text: string) {
  const bytes = new TextEncoder().encode(text)
  let bin = ''
  for (let i = 0; i < bytes.length; i += 0x8000) {
    bin += String.fromCharCode(...bytes.subarray(i, i + 0x8000))
  }
  return saveFileAndShow(filename, mime, btoa(bin))
}

export function openSavedFile(path: string) {
  return post<{ ok: boolean; error?: string }>('/api/files/open', { path })
}

export function showSavedFileInFolder(path: string) {
  return post<{ ok: boolean; error?: string }>('/api/files/reveal', { path })
}
