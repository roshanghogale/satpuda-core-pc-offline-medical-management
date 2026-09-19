/** Theme keys match core/app_setup.AVAILABLE_THEMES / theme_config.txt */

import { getApiBase } from './api'

const VALID =
  /^(steel|charcoal|crimson|rose|navy|forest|midnight|amber|teal|violet)-(dark|light)$/

export type ThemePack = 'classic' | 'accessible' | 'modern'

const PACKS = new Set<ThemePack>(['classic', 'accessible', 'modern'])

/** Monotonic counter so older in-flight saves cannot overwrite newer ones. */
let _persistSeq = 0

export function normalizeTheme(raw: string | null | undefined): string {
  const t = (raw || '').trim()
  if (t === 'satpuda-dark') return 'teal-dark'
  if (t === 'satpuda-light') return 'teal-light'
  if (VALID.test(t)) return t
  return 'navy-light'
}

export function normalizeThemePack(raw: string | null | undefined): ThemePack {
  const p = (raw || '').trim().toLowerCase()
  if (PACKS.has(p as ThemePack)) return p as ThemePack
  return 'modern'
}

export function applyTheme(themeKey: string) {
  const key = normalizeTheme(themeKey)
  document.documentElement.setAttribute('data-theme', key)
  localStorage.setItem('satpuda-app-theme', key)
  return key
}

export function applyThemePack(pack: string) {
  const key = normalizeThemePack(pack)
  document.documentElement.setAttribute('data-theme-pack', key)
  localStorage.setItem('satpuda-app-theme-pack', key)
  return key
}

export function loadStoredTheme(): string {
  // Prefer light navy as the product default when nothing valid is stored.
  const raw = localStorage.getItem('satpuda-app-theme')
  if (!raw || !String(raw).trim()) return 'navy-light'
  return normalizeTheme(raw)
}

/** Apply disk/meta theme and keep localStorage in sync (source of truth = theme_config). */
export function syncThemeFromDisk(themeKey: string) {
  return applyTheme(normalizeTheme(themeKey || 'navy-light'))
}

export function loadStoredThemePack(): ThemePack {
  return normalizeThemePack(localStorage.getItem('satpuda-app-theme-pack'))
}

/** Appearance font size 7–20 → CSS --ui-font-scale (1.0 = size 10). */
export function normalizeFontSize(raw: unknown): number {
  const n = Math.round(Number(raw))
  if (!Number.isFinite(n)) return 10
  return Math.max(7, Math.min(20, n))
}

export function applyFontSize(size: unknown): number {
  const n = normalizeFontSize(size)
  const scale = n / 10
  document.documentElement.style.setProperty('--ui-font-scale', String(scale))
  localStorage.setItem('satpuda-app-font-size', String(n))
  return n
}

export function loadStoredFontSize(): number {
  return normalizeFontSize(localStorage.getItem('satpuda-app-font-size') || 10)
}

/** Toggle dark↔light within the same color family (e.g. forest-dark ↔ forest-light). */
export function flipThemeVariant(themeKey: string): string {
  const key = normalizeTheme(themeKey)
  if (key.endsWith('-dark')) return key.replace(/-dark$/, '-light')
  return key.replace(/-light$/, '-dark')
}

export function themeVariantLabel(themeKey: string): string {
  return normalizeTheme(themeKey).endsWith('-light') ? 'Dark' : 'Light'
}

export function themePackLabel(pack: string): string {
  const p = normalizeThemePack(pack)
  if (p === 'accessible') return 'Accessible'
  if (p === 'modern') return 'Modern'
  return 'Classic'
}

/**
 * Persist theme to theme_config.txt (same file Tk uses).
 * Race-safe: only the latest call updates disk if several overlap.
 */
export async function persistTheme(themeKey: string): Promise<string> {
  const key = applyTheme(themeKey)
  const seq = ++_persistSeq
  try {
    const res = await fetch(`${getApiBase()}/api/settings/appearance`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ theme: key }),
    })
    const data = (await res.json().catch(() => ({}))) as {
      theme?: string
      error?: string
    }
    if (!res.ok) {
      throw new Error(data.error || `HTTP ${res.status}`)
    }
    // Another save started after us — keep our applied key, don't clobber.
    if (seq !== _persistSeq) return key
    const returned = data.theme ? normalizeTheme(String(data.theme)) : key
    // Never accept a different family/variant than we asked to save.
    if (returned !== key) {
      console.warn(
        `Theme save mismatch: requested ${key}, server returned ${returned}; re-applying ${key}`,
      )
      // Re-write the requested theme so disk cannot stay on the wrong variant.
      await fetch(`${getApiBase()}/api/settings/appearance`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ theme: key }),
      })
      return applyTheme(key)
    }
    return applyTheme(returned)
  } catch (e) {
    if (seq === _persistSeq) {
      console.warn('Theme save failed:', e)
    }
  }
  return key
}

/** Persist theme pack only (also used from Save Appearance). */
export async function persistThemePack(pack: string): Promise<ThemePack> {
  const key = applyThemePack(pack)
  try {
    const res = await fetch(`${getApiBase()}/api/settings/appearance`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ theme_pack: key }),
    })
    const data = (await res.json().catch(() => ({}))) as {
      theme_pack?: string
      error?: string
    }
    if (!res.ok) {
      throw new Error(data.error || `HTTP ${res.status}`)
    }
    const returned = data.theme_pack
      ? normalizeThemePack(String(data.theme_pack))
      : key
    return applyThemePack(returned)
  } catch (e) {
    console.warn('Theme pack save failed:', e)
  }
  return key
}
