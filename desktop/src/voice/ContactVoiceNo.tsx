/** Settings > Contacts: the "Voice no." column on the Doctors / Customers / Suppliers /
 * Villages lists. A row's number is its voice shortcut: in a list voice opened
 * ("doctors dakhav") the owner just says "ek, don, teen". The numbers live in
 * the voice service (GET / POST /shortcuts), the same list Settings > Shortcuts
 * > Voice Commands edits; with the service down the column hides itself.
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  MAX_SHORTCUTS,
  SHORTCUTS_EVENT,
  shortcutKindWord,
  voiceSetShortcut,
  voiceShortcuts,
  type ShortcutKind,
  type ShortcutMap,
} from './voiceClient'
import { useVoiceEnabled } from './voiceEnabled'

const FIRST = 10
const norm = (s: string) => String(s ?? '').trim().replace(/\s+/g, ' ').toUpperCase()

export type ContactShortcuts = {
  /** null while loading or with the service down. */
  map: ShortcutMap | null
  down: boolean
  saving: boolean
  status: { ok: boolean; text: string } | null
  /** The row's number (0: none). */
  numberOf: (kind: ShortcutKind, name: string) => number
  /** Put `name` on n; name "" clears n. */
  put: (kind: ShortcutKind, n: number, name: string) => Promise<void>
  reload: () => void
}

export function useContactShortcuts(): ContactShortcuts {
  const [map, setMap] = useState<ShortcutMap | null>(null)
  const [down, setDown] = useState(false)
  const [saving, setSaving] = useState(false)
  const [status, setStatus] = useState<{ ok: boolean; text: string } | null>(null)
  // Voice switched off for this store (admin panel): no column, no "service band" note.
  const voiceOn = useVoiceEnabled().enabled

  const reload = useCallback(() => {
    let alive = true
    if (!voiceOn) {
      setMap(null)
      setDown(false)
      return () => {
        alive = false
      }
    }
    voiceShortcuts()
      .then((r) => {
        if (!alive) return
        setMap(r.shortcuts)
        setDown(false)
      })
      .catch(() => {
        if (!alive) return
        setMap(null)
        setDown(true)
      })
    return () => {
      alive = false
    }
  }, [voiceOn])

  useEffect(() => reload(), [reload])

  // "Doctor shortcut 2 Joshi" said aloud, or the Voice Commands table: show it here too.
  useEffect(() => {
    const on = (e: Event) => {
      const m = (e as CustomEvent<ShortcutMap>).detail
      if (m && voiceOn) {
        setMap(m)
        setDown(false)
      }
    }
    window.addEventListener(SHORTCUTS_EVENT, on)
    return () => window.removeEventListener(SHORTCUTS_EVENT, on)
  }, [voiceOn])

  // kind -> NAME -> n
  const index = useMemo(() => {
    const out: Record<string, Map<string, number>> = {}
    if (!map) return out
    for (const [kind, rows] of Object.entries(map)) {
      const m = new Map<string, number>()
      for (const [n, name] of Object.entries(rows || {})) m.set(norm(name), Number(n))
      out[kind] = m
    }
    return out
  }, [map])

  const numberOf = useCallback(
    (kind: ShortcutKind, name: string) => index[kind]?.get(norm(name)) || 0,
    [index],
  )

  const put = useCallback(async (kind: ShortcutKind, n: number, name: string) => {
    if (!Number.isInteger(n) || n < 1 || n > MAX_SHORTCUTS) return
    setSaving(true)
    const who = shortcutKindWord(kind)
    try {
      const m = await voiceSetShortcut(kind, n, name)
      setMap(m)
      setStatus({ ok: true, text: name ? `${who} ${n} = ${name}` : `${who} ${n} rikama` })
    } catch (e) {
      setStatus({ ok: false, text: `Voice no. save zala nahi: ${e instanceof Error ? e.message : e}` })
    } finally {
      setSaving(false)
    }
  }, [])

  return useMemo(
    () => ({ map, down, saving, status, numberOf, put, reload }),
    [map, down, saving, status, numberOf, put, reload],
  )
}

/** The filter toggle, the service-down note and the last save's result, above a list. */
export function VoiceNoBar({
  sc,
  kind,
  only,
  onOnly,
}: {
  sc: ContactShortcuts
  kind: ShortcutKind
  only: boolean
  onOnly: (on: boolean) => void
}) {
  if (sc.down) {
    return (
      <p className="muted" style={{ margin: '2px 0 6px', fontSize: 12 }}>
        Voice no.: voice service band aahe (127.0.0.1:47811), mhanun column lapavla.{' '}
        <button type="button" className="settings-link-btn" onClick={() => sc.reload()}>
          Parat baga
        </button>
      </p>
    )
  }
  if (!sc.map) return null
  const count = Object.keys(sc.map[kind] || {}).length
  return (
    <div className="settings-inline-actions" style={{ margin: '2px 0 6px', fontSize: 12, alignItems: 'center' }}>
      <label style={{ display: 'inline-flex', gap: 4, alignItems: 'center', cursor: 'pointer' }}>
        <input type="checkbox" checked={only} onChange={(e) => onOnly(e.target.checked)} />
        Fakt voice numbers ({count})
      </label>
      {sc.saving ? <span className="muted">Saving…</span> : null}
      {!sc.saving && sc.status ? (
        <span className={sc.status.ok ? 'muted' : 'error'}>{sc.status.text}</span>
      ) : null}
    </div>
  )
}

/** One row's number: "—", 1..10, and 11..30 behind "more". A number another row holds
 *  shows that row's name; choosing it moves the number here. */
export function VoiceNoSelect({
  sc,
  kind,
  name,
}: {
  sc: ContactShortcuts
  kind: ShortcutKind
  name: string
}) {
  const current = sc.numberOf(kind, name)
  const [more, setMore] = useState(false)
  const rows = sc.map?.[kind] || {}
  const last = more || current > FIRST ? MAX_SHORTCUTS : FIRST
  const label = (n: number) => {
    const holder = rows[String(n)]
    return holder && norm(holder) !== norm(name) ? `${n} (${holder})` : String(n)
  }
  return (
    <select
      className="voice-no-select"
      style={{ width: 64, padding: '1px 2px', fontSize: 12 }}
      value={current ? String(current) : ''}
      disabled={sc.saving || !name.trim()}
      title="Voice shortcut number: voice ne ughadlelya yaadit ha number bola"
      onChange={(e) => {
        const v = e.target.value
        if (v === 'more') {
          setMore(true)
          return
        }
        if (!v) {
          if (current) void sc.put(kind, current, '')
          return
        }
        const n = Number(v)
        if (n !== current) void sc.put(kind, n, name.trim())
      }}
    >
      <option value="">—</option>
      {Array.from({ length: last }, (_, i) => i + 1).map((n) => (
        <option key={n} value={String(n)}>
          {label(n)}
        </option>
      ))}
      {last < MAX_SHORTCUTS ? <option value="more">more…</option> : null}
    </select>
  )
}

/** Rows with a number only, in number order (the "Fakt voice numbers" view). */
export function onlyNumbered<T>(
  rows: T[],
  sc: ContactShortcuts,
  kind: ShortcutKind,
  nameOf: (r: T) => string,
): T[] {
  return rows
    .map((r) => ({ r, n: sc.numberOf(kind, nameOf(r)) }))
    .filter((x) => x.n > 0)
    .sort((a, b) => a.n - b.n)
    .map((x) => x.r)
}
