/** Settings > Data & System > This PC on the LAN (core/lan/service.py).
 *
 *  Off (default): this PC works alone, as before.
 *  Main PC: the other PCs of the shop connect to this one over the LAN cable / switch and
 *    work on the same data at the same moment; this PC keeps syncing with the server.
 *  Counter PC: works on the Main PC's data; prints on its own printer; when the Main PC is
 *    off it bills on its own copy and joins up by itself later.
 */

import { useCallback, useEffect, useState } from 'react'
import { getApiBase } from '../../api'
import { Field, Frame, Help, Note, PanelTitle, SaveBtn } from './SettingsChrome'

type LanDevice = {
  id: string
  name: string
  paired_at?: string
  last_seen?: string | null
  seconds_ago?: number | null
  connected: boolean
  ip?: string
}

type CounterStatus = {
  paired: boolean
  main_host: string
  main_port: number
  main_name: string
  main_reachable: boolean
  last_ok?: string | null
  down_seconds?: number
  last_error?: string
  pending_code?: boolean
  pair_error?: string
  waiting_for_main?: number
  banner?: string
}

type LanStatus = {
  ok: boolean
  role: 'off' | 'main' | 'counter'
  configured_role?: string
  port: number
  discovery_port: number
  addresses: string[]
  pc_name?: string
  shop?: string
  error?: string
  pairing?: { code: string; expires_in: number }
  devices?: LanDevice[]
  listening?: boolean
  firewall?: boolean | null
  relay_waiting?: number
  counter?: CounterStatus
}

type FoundMain = { host: string; port: number; shop: string; pc: string; same_shop: boolean }

async function lanCall<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${getApiBase()}${path}`, body === undefined ? undefined : {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  const data = (await res.json().catch(() => ({}))) as T & { error?: string; ok?: boolean }
  if (!res.ok || data.ok === false) {
    throw new Error(data.error || `HTTP ${res.status}`)
  }
  return data
}

function ago(seconds?: number | null, iso?: string | null): string {
  if (seconds != null) {
    if (seconds < 5) return 'now'
    if (seconds < 90) return `${seconds} s ago`
    if (seconds < 5400) return `${Math.round(seconds / 60)} min ago`
    return `${Math.round(seconds / 3600)} h ago`
  }
  if (!iso) return 'never'
  try {
    return new Date(iso).toLocaleString()
  } catch {
    return iso
  }
}

const ROLE_TEXT: Record<string, string> = {
  off: 'Off — this PC works alone (as before)',
  main: 'Main PC — other PCs of the shop connect to this one',
  counter: 'Counter PC — works on the Main PC’s data',
}

export function LanPanel() {
  const [st, setSt] = useState<LanStatus | null>(null)
  const [role, setRole] = useState<'off' | 'main' | 'counter'>('off')
  const [port, setPort] = useState('')
  const [msg, setMsg] = useState('')
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  const [found, setFound] = useState<FoundMain[] | null>(null)
  const [host, setHost] = useState('')
  const [code, setCode] = useState('')

  const load = useCallback(async () => {
    try {
      const s = await lanCall<LanStatus>('/api/lan/status')
      setSt(s)
      return s
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
      return null
    }
  }, [])

  useEffect(() => {
    void load().then((s) => {
      if (s) {
        setRole(s.role)
        setPort(String(s.port || ''))
      }
    })
    const t = window.setInterval(() => void load(), 3000)
    return () => window.clearInterval(t)
  }, [load])

  const run = async (label: string, fn: () => Promise<unknown>) => {
    setBusy(true)
    setErr('')
    setMsg(`${label}…`)
    try {
      await fn()
      setMsg(`${label}: done.`)
      await load()
    } catch (e) {
      setMsg('')
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const applyRole = () =>
    run('Saving', async () => {
      const p = Number(port)
      const s = await lanCall<LanStatus>('/api/lan/role', {
        role,
        ...(Number.isFinite(p) && p > 0 ? { port: p } : {}),
      })
      setSt(s)
    })

  const findMain = () =>
    run('Looking for the Main PC on the LAN', async () => {
      const r = await lanCall<{ found: FoundMain[] }>('/api/lan/discover', {})
      setFound(r.found || [])
      const same = (r.found || []).filter((m) => m.same_shop)
      if (same.length === 1) setHost(`${same[0].host}:${same[0].port}`)
    })

  const pairNow = () =>
    run('Pairing with the Main PC', async () => {
      await lanCall('/api/lan/pair', { host: host.trim(), code: code.trim() })
      setCode('')
    })

  if (!st) {
    return (
      <>
        <PanelTitle>This PC on the LAN</PanelTitle>
        <p className="muted">{err || 'Loading…'}</p>
      </>
    )
  }

  const changed = role !== st.role || (role === 'main' && String(st.port) !== port)
  const c = st.counter

  return (
    <>
      <PanelTitle>This PC on the LAN</PanelTitle>
      <Note>
        Two or more PCs of this shop joined by a LAN cable or switch can work on the same
        data at the same moment, without internet: a sale on one shows on the other at once.
        One PC is the Main PC; the others are Counter PCs.
      </Note>

      <Frame title="This PC is">
        {(['off', 'main', 'counter'] as const).map((r) => (
          <label key={r} className="settings-field-check">
            <input
              type="radio"
              name="lan-role"
              checked={role === r}
              disabled={busy}
              onChange={() => setRole(r)}
            />
            <span>{ROLE_TEXT[r]}</span>
          </label>
        ))}
        {role === 'main' ? (
          <Field label="LAN port (counters use this)">
            <input
              className="settings-input"
              value={port}
              inputMode="numeric"
              onChange={(e) => setPort(e.target.value.replace(/[^0-9]/g, '').slice(0, 5))}
              style={{ width: 90 }}
            />
          </Field>
        ) : null}
        <div style={{ marginTop: 8 }}>
          <SaveBtn label="Save" saving={busy} disabled={!changed} onClick={() => void applyRole()} />
        </div>
        <Help>
          <p>
            <b>Main PC</b>: keep it switched on while the shop works. It syncs with the
            Satpuda server as now, and the Counter PCs connect to it. Each PC still prints
            on its own printer, and files saved on a Counter PC are saved on that PC.
          </p>
          <p>
            <b>Counter PC</b>: enter the Main PC&apos;s pairing code once. If the Main PC
            is off or the cable is out, this PC keeps billing on its own copy of the shop
            and shows a note at the top; when the Main PC is back, the bills made
            meanwhile join up by themselves — never twice.
          </p>
        </Help>
      </Frame>

      {st.error ? <div className="iset-statusbar err">{st.error}</div> : null}

      {st.role === 'main' ? (
        <>
          <Frame title="Connect a Counter PC">
            <p>
              On the Counter PC open this screen, pick <b>Counter PC</b>, and enter:
            </p>
            <div className="settings-check-grid-3" style={{ alignItems: 'baseline' }}>
              <div>
                <div className="muted">This PC&apos;s address</div>
                <div style={{ fontSize: '1.2em', fontWeight: 600 }}>
                  {(st.addresses || []).length
                    ? st.addresses.map((a) => `${a}:${st.port}`).join('  or  ')
                    : 'no network address yet — check the cable'}
                </div>
              </div>
              <div>
                <div className="muted">Pairing code</div>
                <div style={{ fontSize: '1.6em', fontWeight: 700, letterSpacing: 3 }}>
                  {st.pairing?.code || '— — —'}
                </div>
                {st.pairing?.code ? (
                  <div className="muted">valid {Math.ceil((st.pairing.expires_in || 0) / 60)} more min, once</div>
                ) : null}
              </div>
              <div>
                <SaveBtn
                  label="New pairing code"
                  saving={busy}
                  onClick={() => void run('New code', () => lanCall('/api/lan/pairing-code', {}))}
                />
              </div>
            </div>
            <Note>
              A Counter PC can also find this PC by itself on the LAN (by the shop name);
              the address is for when it cannot.
            </Note>
          </Frame>

          <Frame title="Counter PCs">
            {(st.devices || []).length === 0 ? (
              <p className="muted">No Counter PC paired yet.</p>
            ) : (
              <table className="settings-table">
                <thead>
                  <tr>
                    <th>PC</th>
                    <th>Status</th>
                    <th>Last seen</th>
                    <th>Address</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {(st.devices || []).map((d) => (
                    <tr key={d.id}>
                      <td>{d.name}</td>
                      <td>{d.connected ? 'Connected' : 'Not connected'}</td>
                      <td>{ago(d.seconds_ago, d.last_seen)}</td>
                      <td>{d.ip || ''}</td>
                      <td>
                        <button
                          type="button"
                          className="btn btn-neutral btn-sm"
                          disabled={busy}
                          onClick={() => {
                            if (!window.confirm(`Remove ${d.name}? It will need a new pairing code to connect again.`)) return
                            void run('Removing', () => lanCall('/api/lan/forget', { device_id: d.id }))
                          }}
                        >
                          Remove
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            {st.relay_waiting ? (
              <Note>
                {st.relay_waiting} change(s) handed over by Counter PCs are waiting to reach the
                Satpuda server (they go when this PC has internet).
              </Note>
            ) : null}
          </Frame>

          <Frame title="Windows Firewall">
            <p>
              {st.firewall === true
                ? 'Allowed: Counter PCs on this LAN can reach this PC.'
                : 'Counter PCs need Windows Firewall to let them reach this PC (only this LAN, ports ' +
                  `${st.port} and ${st.discovery_port}).`}
            </p>
            {st.firewall !== true ? (
              <SaveBtn
                label="Allow on this PC's firewall"
                saving={busy}
                onClick={() => void run('Asking Windows', () => lanCall('/api/lan/firewall', {}))}
              />
            ) : null}
            <Note>Windows asks for permission once. If Windows shows its own &quot;Allow access&quot; box, allow it.</Note>
          </Frame>
        </>
      ) : null}

      {st.role === 'counter' && c ? (
        <>
          <Frame title="Main PC">
            {c.paired ? (
              <>
                <p>
                  <b>{c.main_reachable ? 'Connected' : 'Not reachable'}</b> — {c.main_name || 'Main PC'} at{' '}
                  {c.main_host}:{c.main_port}
                  {c.main_reachable ? '' : ` (for ${ago(c.down_seconds)})`.replace(' ago', '')}
                </p>
                {!c.main_reachable ? (
                  <Note>
                    This PC is billing on its own copy. Its bills go to the Main PC by
                    themselves when it answers again.
                  </Note>
                ) : null}
                {c.waiting_for_main ? (
                  <Note>{c.waiting_for_main} change(s) made on this PC are waiting for the Main PC.</Note>
                ) : null}
                <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
                  <SaveBtn
                    label="Send / fetch now"
                    saving={busy}
                    disabled={!c.main_reachable}
                    onClick={() => void run('Joining up', () => lanCall('/api/lan/sync-now', {}))}
                  />
                  <SaveBtn
                    label="Forget this Main PC"
                    saving={busy}
                    onClick={() => {
                      if (!window.confirm('Forget the Main PC? This PC will work alone until it is paired again.')) return
                      void run('Forgetting', () => lanCall('/api/lan/unpair', {}))
                    }}
                  />
                </div>
              </>
            ) : (
              <p>Not paired with a Main PC yet.</p>
            )}
            {c.pair_error ? <div className="iset-statusbar err">{c.pair_error}</div> : null}
          </Frame>

          {!c.paired ? (
            <Frame title="Pair with the Main PC">
              <div style={{ display: 'flex', gap: 8, alignItems: 'flex-end', flexWrap: 'wrap' }}>
                <SaveBtn label="Find Main PC on the LAN" saving={busy} onClick={() => void findMain()} />
                {found && found.length === 0 ? (
                  <span className="muted">None answered — type its address instead.</span>
                ) : null}
              </div>
              {found && found.length ? (
                <div style={{ marginTop: 6 }}>
                  {found.map((m) => (
                    <label key={`${m.host}:${m.port}`} className="settings-field-check">
                      <input
                        type="radio"
                        name="lan-main"
                        disabled={!m.same_shop}
                        checked={host === `${m.host}:${m.port}`}
                        onChange={() => setHost(`${m.host}:${m.port}`)}
                      />
                      <span>
                        {m.shop || 'Shop'} — {m.pc} ({m.host}){m.same_shop ? '' : ' — a different shop'}
                      </span>
                    </label>
                  ))}
                </div>
              ) : null}
              <Field label="Main PC address (shown on the Main PC)">
                <input
                  className="settings-input"
                  value={host}
                  placeholder="e.g. 192.168.1.10:8775 — empty: find it"
                  onChange={(e) => setHost(e.target.value)}
                />
              </Field>
              <Field label="Pairing code (6 digits, shown on the Main PC)">
                <input
                  className="settings-input"
                  value={code}
                  inputMode="numeric"
                  maxLength={6}
                  onChange={(e) => setCode(e.target.value.replace(/[^0-9]/g, '').slice(0, 6))}
                  style={{ width: 110, letterSpacing: 3 }}
                />
              </Field>
              <SaveBtn
                label="Pair"
                saving={busy}
                disabled={code.length !== 6}
                onClick={() => void pairNow()}
              />
            </Frame>
          ) : null}
        </>
      ) : null}

      {msg ? <div className="iset-statusbar">{msg}</div> : null}
      {err ? <div className="iset-statusbar err">{err}</div> : null}
    </>
  )
}
