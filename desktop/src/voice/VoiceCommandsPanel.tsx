/** Settings -> Shortcuts -> Voice Commands.
 *
 * The list is the voice service's own (GET /guide.json), so what is shown here
 * is what the parser understands today -- never a copy that drifts. When the
 * service is not running the panel says so instead of showing an empty list.
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { Frame, Note, PanelTitle } from '../pages/settings/SettingsChrome'
import { VOICE_BASE } from './voiceClient'
import { VoiceShortcutsPanel } from './VoiceShortcutsPanel'
import { VoiceLevelPanel } from './VoiceLevelPanel'
import { VoicePackPanel } from './VoicePackPanel'
import { useVoiceEnabled, voiceSourceNote } from './voiceEnabled'

type GuideJob = { label: string; sentences: string[] }
type GuideGroup = { group: string; jobs: GuideJob[] }

export function VoiceCommandsPanel() {
  const [groups, setGroups] = useState<GuideGroup[] | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const [q, setQ] = useState('')
  const sw = useVoiceEnabled()

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    const ctrl = new AbortController()
    const timer = window.setTimeout(() => ctrl.abort(), 5000)
    try {
      const res = await fetch(`${VOICE_BASE}/guide.json`, { signal: ctrl.signal })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const body = (await res.json()) as { groups?: GuideGroup[] }
      setGroups(Array.isArray(body.groups) ? body.groups : [])
    } catch (e) {
      setGroups(null)
      setError(
        e instanceof Error && e.name !== 'AbortError' && !/fetch/i.test(e.message)
          ? e.message
          : 'no answer',
      )
    } finally {
      window.clearTimeout(timer)
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  const shown = useMemo(() => {
    const words = q.toLowerCase().split(/\s+/).filter(Boolean)
    if (!groups) return []
    if (!words.length) return groups
    const hit = (text: string) => {
      const t = text.toLowerCase()
      return words.every((w) => t.includes(w))
    }
    return groups
      .map((g) => ({
        group: g.group,
        jobs: (g.jobs || [])
          .map((j) => {
            // A group or job name that matches keeps every sentence under it.
            if (hit(`${g.group} ${j.label}`)) return j
            return { ...j, sentences: (j.sentences || []).filter(hit) }
          })
          .filter((j) => j.sentences.length || hit(`${g.group} ${j.label}`)),
      }))
      .filter((g) => g.jobs.length)
  }, [groups, q])

  const total = useMemo(
    () =>
      (groups || []).reduce(
        (n, g) => n + (g.jobs || []).reduce((m, j) => m + (j.sentences || []).length, 0),
        0,
      ),
    [groups],
  )

  return (
    <>
      <PanelTitle>🎤 Voice Commands</PanelTitle>
      <Note>
        Tap F1 (or the Voice ON button) and just speak; hold F1 (or the mic
        button) to talk once; Ctrl+K lets you type it instead. Anything that
        saves or prints asks “Yes / No” first; a popup answers “yes”, “no” and
        “band kar”.
      </Note>
      <p className="muted" style={{ margin: '0 0 8px', fontSize: 12 }}>Voice: {voiceSourceNote(sw)}</p>
      <VoicePackPanel />
      <VoiceLevelPanel />
      <VoiceShortcutsPanel />
      <div className="settings-inline-actions" style={{ marginBottom: 8 }}>
        <input
          className="settings-input"
          style={{ minWidth: 260 }}
          placeholder="Shodha: expired, ledger, printer, aaj…"
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
        <button type="button" className="btn btn-neutral btn-sm" onClick={() => void load()} disabled={loading}>
          {loading ? 'Loading…' : 'Reload'}
        </button>
        {groups ? (
          <span className="muted">
            {total} sentences in {groups.length} groups
          </span>
        ) : null}
      </div>

      {error ? (
        <p className="error">
          The voice service is not running (127.0.0.1:47811 — {error}), so the
          command list cannot be shown. Start the voice service and press Reload.
        </p>
      ) : null}
      {!error && groups && !groups.length ? (
        <p className="muted">The voice service sent an empty command list.</p>
      ) : null}
      {groups && groups.length && !shown.length ? (
        <p className="muted">Nothing matches “{q}”.</p>
      ) : null}

      {shown.map((g) => (
        <Frame key={g.group} title={g.group}>
          <div className="settings-table-wrap">
            <table className="settings-table">
              <tbody>
                {g.jobs.map((j, i) => (
                  <tr key={`${g.group}-${j.label}-${i}`}>
                    <td style={{ fontWeight: 600, width: '26%', verticalAlign: 'top' }}>{j.label}</td>
                    <td>
                      {(j.sentences || []).map((s, k) => (
                        <div key={k}>“{s}”</div>
                      ))}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Frame>
      ))}
    </>
  )
}
