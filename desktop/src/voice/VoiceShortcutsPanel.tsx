/** Settings -> Shortcuts -> Voice Commands -> Doctor / Customer / Supplier / Village shortcuts.
 *
 * "Doctor 2" (or "doctor dropdown don") puts the doctor saved on number 2 on
 * the bill; in a list voice opened, a bare "don" picks number 2. The numbers
 * live in the voice service (GET / POST /shortcuts), so this table, the "Voice
 * no." column in Settings > Contacts and "doctor shortcut 2 Joshi" said
 * aloud all edit the same list. Doctor and supplier names come from the
 * service; customer and village names from the Sales form's own lists, and only
 * a name from those lists can be put on a number.
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { Frame } from '../pages/settings/SettingsChrome'
import { fetchSalesForm } from '../pagesApi'
import {
  MAX_SHORTCUTS,
  SHORTCUTS_EVENT,
  SHORTCUT_KINDS,
  shortcutKindWord,
  voiceSaveShortcuts,
  voiceShortcuts,
  type ShortcutKind,
  type ShortcutMap,
} from './voiceClient'

const FIRST_ROWS = 10
const MORE_ROWS = 5
const MAX_LISTED = 40

const TAB_TITLE: Record<ShortcutKind, string> = {
  doctor: 'Doctors',
  customer: 'Customers',
  supplier: 'Suppliers',
  village: 'Gaon',
}

const norm = (s: string) => s.trim().replace(/\s+/g, ' ').toUpperCase()

export function VoiceShortcutsPanel() {
  const [map, setMap] = useState<ShortcutMap | null>(null)
  const [doctors, setDoctors] = useState<string[]>([])
  const [suppliers, setSuppliers] = useState<string[]>([])
  const [villages, setVillages] = useState<string[] | null>(null)
  const [tab, setTab] = useState<ShortcutKind>('doctor')
  const [customers, setCustomers] = useState<string[] | null>(null)
  const [customerError, setCustomerError] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [status, setStatus] = useState<{ ok: boolean; text: string } | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const r = await voiceShortcuts()
      setMap(r.shortcuts)
      setDoctors(r.doctors)
      setSuppliers(r.suppliers)
    } catch (e) {
      setMap(null)
      setError(e instanceof Error && e.name !== 'AbortError' && !/fetch/i.test(e.message) ? e.message : 'no answer')
    } finally {
      setLoading(false)
    }
    try {
      const f = await fetchSalesForm()
      setCustomers(Array.isArray(f.customers) ? f.customers.filter(Boolean) : [])
      setVillages(Array.isArray(f.villages) ? f.villages.map((v) => String(v ?? '').trim()).filter(Boolean) : [])
      setCustomerError('')
    } catch (e) {
      setCustomers(null)
      setVillages(null)
      setCustomerError(e instanceof Error ? e.message : String(e))
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  // "Doctor shortcut 2 Joshi" said while this is open: show it.
  useEffect(() => {
    const on = (e: Event) => {
      const m = (e as CustomEvent<ShortcutMap>).detail
      if (m) setMap(m)
    }
    window.addEventListener(SHORTCUTS_EVENT, on)
    return () => window.removeEventListener(SHORTCUTS_EVENT, on)
  }, [])

  /** Put `name` on number n ("" clears it); a name keeps only one number. Saves the whole map. */
  const put = useCallback(
    async (kind: ShortcutKind, n: number, name: string) => {
      if (!map) return
      const next: ShortcutMap = {
        doctor: { ...map.doctor },
        customer: { ...map.customer },
        supplier: { ...map.supplier },
        village: { ...map.village },
      }
      const rows = next[kind]
      const moved: string[] = []
      if (name) {
        for (const [k, v] of Object.entries(rows)) {
          if (k !== String(n) && norm(v) === norm(name)) {
            delete rows[k]
            moved.push(k)
          }
        }
        rows[String(n)] = name
      } else {
        delete rows[String(n)]
      }
      const before = map
      setMap(next)
      setSaving(true)
      try {
        const saved = await voiceSaveShortcuts(next)
        setMap(saved)
        const who = shortcutKindWord(kind)
        setStatus({
          ok: true,
          text: name
            ? `Saved: ${who} ${n} = ${name}${moved.length ? ` (number ${moved.join(', ')} varun kadhla)` : ''}`
            : `Saved: ${who} ${n} rikama`,
        })
      } catch (e) {
        setMap(before)
        setStatus({ ok: false, text: `Save zala nahi: ${e instanceof Error ? e.message : e}` })
      } finally {
        setSaving(false)
      }
    },
    [map],
  )

  return (
    <Frame title="Voice shortcuts (Doctor / Customer / Supplier / Gaon)">
      <p className="settings-note">
        Bola: “doctor 2” kivha “doctor dropdown don” — tya numbercha doctor lagel. Voice ne ughadlelya
        yaadit (“doctors dakhav”) nusta “ek, don, teen” bola. Voice ne theva: “doctor shortcut 2 Joshi”.
        Settings › Contacts madhe pan “Voice no.” column aahe.
      </p>
      {error ? (
        <div className="settings-inline-actions" style={{ marginBottom: 8 }}>
          <p className="error" style={{ margin: 0 }}>
            The voice service is not running (127.0.0.1:47811 — {error}), so the shortcuts cannot be
            shown or saved. Start the voice service and press Reload.
          </p>
          <button type="button" className="btn btn-neutral btn-sm" onClick={() => void load()} disabled={loading}>
            {loading ? 'Loading…' : 'Reload'}
          </button>
        </div>
      ) : null}
      {!map && !error ? <p className="muted">Loading…</p> : null}
      {map ? (
        <>
          <div className="settings-inline-actions" role="tablist" style={{ marginBottom: 6 }}>
            {SHORTCUT_KINDS.map((k) => (
              <button
                key={k}
                type="button"
                role="tab"
                aria-selected={tab === k}
                className={`btn btn-sm ${tab === k ? 'btn-primary' : 'btn-neutral'}`}
                onClick={() => setTab(k)}
              >
                {TAB_TITLE[k]} ({Object.keys(map[k] || {}).length})
              </button>
            ))}
          </div>
          {tab === 'doctor' ? (
            <ShortcutTable
              key="doctor"
              kind="doctor"
              title="Doctors"
              rows={map.doctor}
              names={doctors}
              emptyNote={doctors.length ? '' : 'Voice service kade doctoranchi yaadi nahi.'}
              disabled={saving}
              onPut={put}
            />
          ) : null}
          {tab === 'customer' ? (
            <ShortcutTable
              key="customer"
              kind="customer"
              title="Customers"
              rows={map.customer}
              names={customers || []}
              emptyNote={
                customers === null
                  ? `Grahakanchi yaadi ali nahi${customerError ? ` (${customerError})` : ''}.`
                  : customers.length
                    ? ''
                    : 'Grahakanchi yaadi rikami aahe.'
              }
              disabled={saving || customers === null}
              onPut={put}
            />
          ) : null}
          {tab === 'supplier' ? (
            <ShortcutTable
              key="supplier"
              kind="supplier"
              title="Suppliers"
              rows={map.supplier}
              names={suppliers}
              emptyNote={suppliers.length ? '' : 'Voice service kade supplierchi yaadi nahi.'}
              disabled={saving}
              onPut={put}
            />
          ) : null}
          {tab === 'village' ? (
            <ShortcutTable
              key="village"
              kind="village"
              title="Gaon (Villages)"
              rows={map.village}
              names={villages || []}
              emptyNote={
                villages === null
                  ? `Gavanchi yaadi ali nahi${customerError ? ` (${customerError})` : ''}.`
                  : villages.length
                    ? ''
                    : 'Gavanchi yaadi rikami aahe.'
              }
              disabled={saving || villages === null}
              onPut={put}
            />
          ) : null}
          <div className="settings-inline-actions" style={{ marginTop: 6 }}>
            <button type="button" className="btn btn-neutral btn-sm" onClick={() => void load()} disabled={loading}>
              {loading ? 'Loading…' : 'Reload'}
            </button>
            {saving ? <span className="muted">Saving…</span> : null}
            {!saving && status ? (
              <span className={status.ok ? 'muted' : 'error'}>{status.text}</span>
            ) : null}
          </div>
        </>
      ) : null}
    </Frame>
  )
}

function ShortcutTable({
  kind,
  title,
  rows,
  names,
  emptyNote,
  disabled,
  onPut,
}: {
  kind: ShortcutKind
  title: string
  rows: Record<string, string>
  names: string[]
  emptyNote: string
  disabled: boolean
  onPut: (kind: ShortcutKind, n: number, name: string) => void
}) {
  const highest = Object.keys(rows).reduce((m, k) => Math.max(m, Number(k) || 0), 0)
  const [count, setCount] = useState(FIRST_ROWS)
  const shownCount = Math.min(MAX_SHORTCUTS, Math.max(count, highest))

  return (
    <div style={{ flex: '1 1 320px', minWidth: 280 }}>
      <div style={{ fontWeight: 600, marginBottom: 4 }}>{title}</div>
      {emptyNote ? <p className="muted" style={{ margin: '0 0 4px' }}>{emptyNote}</p> : null}
      <div className="settings-table-wrap">
        <table className="settings-table">
          <tbody>
            {Array.from({ length: shownCount }, (_, i) => i + 1).map((n) => (
              <tr key={n}>
                <td style={{ width: 44, fontWeight: 600, textAlign: 'right' }}>{n}</td>
                <td>
                  <NamePick
                    id={`vsc-${kind}-${n}`}
                    value={rows[String(n)] || ''}
                    names={names}
                    disabled={disabled || !names.length}
                    onPick={(name) => onPut(kind, n, name)}
                  />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {shownCount < MAX_SHORTCUTS ? (
        <button
          type="button"
          className="btn btn-neutral btn-sm"
          style={{ marginTop: 4 }}
          onClick={() => setCount(Math.min(MAX_SHORTCUTS, shownCount + MORE_ROWS))}
        >
          + add more
        </button>
      ) : null}
    </div>
  )
}

/** A search box that only takes a name from `names` ("—" / empty clears the number). */
function NamePick({
  id,
  value,
  names,
  disabled,
  onPick,
}: {
  id: string
  value: string
  names: string[]
  disabled: boolean
  onPick: (name: string) => void
}) {
  const [draft, setDraft] = useState(value)
  const [bad, setBad] = useState(false)
  useEffect(() => {
    setDraft(value)
    setBad(false)
  }, [value])

  const byNorm = useMemo(() => {
    const m = new Map<string, string>()
    for (const n of names) if (!m.has(norm(n))) m.set(norm(n), n)
    return m
  }, [names])

  // The browser list shows at most MAX_LISTED names that match what is typed.
  const listed = useMemo(() => {
    const q = draft.trim().toLowerCase()
    if (!q || norm(draft) === norm(value)) return names.slice(0, MAX_LISTED)
    const starts: string[] = []
    const has: string[] = []
    for (const n of names) {
      const l = n.toLowerCase()
      if (l.startsWith(q)) starts.push(n)
      else if (l.includes(q)) has.push(n)
      if (starts.length >= MAX_LISTED) break
    }
    return [...starts, ...has].slice(0, MAX_LISTED)
  }, [draft, names, value])

  const commit = (text: string) => {
    const t = text.trim()
    if (!t || t === '—') {
      setDraft('')
      setBad(false)
      if (value) onPick('')
      return
    }
    const hit = byNorm.get(norm(t))
    if (!hit) {
      setBad(true)
      return
    }
    setBad(false)
    setDraft(hit)
    if (norm(hit) !== norm(value)) onPick(hit)
  }

  return (
    <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
      <input
        className="settings-input"
        style={{ flex: 1, minWidth: 180, ...(bad ? { borderColor: 'var(--danger, #d33)' } : {}) }}
        list={`${id}-list`}
        placeholder="—"
        value={draft}
        disabled={disabled}
        title={bad ? 'Ha naav yaadit nahi — yaadimadhun nivada' : undefined}
        onChange={(e) => {
          const v = e.target.value
          setDraft(v)
          setBad(false)
          // A pick from the browser's list comes without a typing inputType: save it now.
          const it = (e.nativeEvent as InputEvent).inputType
          if ((!it || it === 'insertReplacementText') && byNorm.has(norm(v))) commit(v)
        }}
        onBlur={() => {
          if (bad) return
          if (norm(draft) !== norm(value)) commit(draft)
        }}
        onKeyDown={(e) => {
          if (e.key === 'Enter') {
            e.preventDefault()
            commit(draft)
          } else if (e.key === 'Escape') {
            setDraft(value)
            setBad(false)
          }
        }}
      />
      <datalist id={`${id}-list`}>
        {listed.map((n) => (
          <option key={n} value={n} />
        ))}
      </datalist>
      {value ? (
        <button
          type="button"
          className="btn btn-neutral btn-sm"
          title="Ha number rikama kara"
          disabled={disabled}
          onClick={() => commit('')}
        >
          —
        </button>
      ) : null}
      {bad ? <span className="error" style={{ fontSize: 12 }}>yaadit nahi</span> : null}
    </div>
  )
}
