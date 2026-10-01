/** "Voice level": what the voice runs on, and the optional "Satpuda" wake word.
 *
 * Shown in the voice bar's settings menu (compact) and in Settings > Shortcuts >
 * Voice Commands. Everything comes from the voice service (GET / POST
 * /voice/config); a service without that endpoint shows nothing here at all.
 *
 * 2026-09-26: the three stars, the AI (LLM) engines, the compare mode and the
 * owner's-voice check are gone -- every one of them made a command slower or
 * turned the owner's own voice away. One fast mode is left: Whisper small + rules.
 */
import { useEffect, useState } from 'react'
import { Frame } from '../pages/settings/SettingsChrome'
import type { VoiceLevelConfig, VoiceLevelPatch } from './voiceClient'
import { reloadVoiceLevel, saveVoiceLevel, useVoiceLevel } from './voiceLevel'

const errText = (e: unknown) => (e instanceof Error ? e.message : String(e))

/** "16 GB RAM · 8 core / 16 thread · GPU: RTX 3050". */
function hardwareLine(hw: VoiceLevelConfig['hardware']): string {
  const parts: string[] = []
  if (hw.ram_gb != null) parts.push(`${Math.round(hw.ram_gb * 10) / 10} GB RAM`)
  if (hw.cores != null || hw.threads != null) {
    parts.push([hw.cores != null ? `${hw.cores} core` : '', hw.threads != null ? `${hw.threads} thread` : ''].filter(Boolean).join(' / '))
  }
  const gpu = typeof hw.gpu === 'string' ? hw.gpu.trim() : hw.gpu === true ? 'aahe' : ''
  parts.push(`GPU: ${gpu || 'nahi'}`)
  return parts.join(' · ')
}

export function VoiceLevelPanel({ compact = false }: { compact?: boolean }) {
  const { cfg, loading } = useVoiceLevel()
  const [saving, setSaving] = useState(false)
  const [status, setStatus] = useState<{ ok: boolean; text: string } | null>(null)

  useEffect(() => {
    void reloadVoiceLevel()
  }, [])

  if (!cfg) {
    if (compact || !loading) return null
    return (
      <Frame title="Voice level">
        <p className="muted">Loading…</p>
      </Frame>
    )
  }

  const save = async (patch: VoiceLevelPatch) => {
    setSaving(true)
    setStatus(null)
    try {
      await saveVoiceLevel(patch)
      setStatus({ ok: true, text: 'Saved' })
    } catch (e) {
      setStatus({ ok: false, text: `Save zala nahi: ${errText(e)}` })
      void reloadVoiceLevel()
    } finally {
      setSaving(false)
    }
  }

  const body = (
    <div className={`vl-box${compact ? ' vl-compact' : ''}`}>
      <div className="vl-hw">
        PC: {hardwareLine(cfg.hardware)}
        {cfg.whisper_model ? <span className="vl-dim"> · Whisper {cfg.whisper_model}</span> : null}
      </div>

      <label className="vl-check">
        <input
          type="checkbox"
          checked={cfg.wake_word}
          disabled={saving}
          onChange={(e) => void save({ wake_word: e.target.checked })}
        />
        “Satpuda” mhanunach aikel
        {cfg.wake_word && cfg.wake_words.length ? <span className="vl-dim"> ({cfg.wake_words.join(', ')})</span> : null}
      </label>

      {saving ? <div className="vl-dim">Saving…</div> : status ? (
        <div className={status.ok ? 'vl-dim' : 'vb-warn'}>{status.text}</div>
      ) : null}
      <div className="vl-dim">Fast mode: Whisper small + rules</div>
    </div>
  )

  return compact ? body : <Frame title="Voice level">{body}</Frame>
}
