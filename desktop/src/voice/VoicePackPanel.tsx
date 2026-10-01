/** The voice pack on screen: the voice bar's card while the voice service is not
 *  there (download it, watch it come, try again), and the Settings > Voice block
 *  (version, update, remove). Both read the one copy in voicePack.ts.
 */
import { useEffect, useRef, useState } from 'react'
import { Frame } from '../pages/settings/SettingsChrome'
import {
  diskWarning,
  packBusy,
  packProgress,
  packSizeText,
  packStateLine,
  refreshVoicePack,
  useVoicePack,
  voicePackAction,
  type VoicePack,
} from './voicePack'

/** The online manifest is read once per app run for the download card (its size). */
let manifestAsked = false

/** The state line, a progress bar and Cancel while downloading. */
function PackProgress({ pack }: { pack: VoicePack }) {
  const frac = packProgress(pack)
  return (
    <div className="vp-busy">
      <div className="vl-dim">{packStateLine(pack)}</div>
      <div className="vp-bar">
        <span
          className={frac == null ? 'vp-bar-run' : undefined}
          style={frac == null ? undefined : { width: `${Math.round(frac * 100)}%` }}
        />
      </div>
      {pack.state === 'downloading' ? (
        <button type="button" className="vb-small" onClick={() => void voicePackAction('cancel')}>
          Cancel
        </button>
      ) : null}
    </div>
  )
}

/** "(mokli jaga 42 GB)". */
const freeText = (p: VoicePack) =>
  p.free_gb == null ? '' : ` (mokli jaga ${Math.round(p.free_gb * 10) / 10} GB)`

/** The voice bar while the voice service does not answer. `onReady`: ask the service again now. */
export function VoicePackCard({ onReady }: { onReady?: () => void }) {
  const { pack, failed } = useVoicePack()
  // One start per time the service was found missing (the card mounts each time).
  const startAsked = useRef(false)

  useEffect(() => {
    void refreshVoicePack().then((p) => {
      if (p && !p.installed && !p.available && !packBusy(p) && !manifestAsked) {
        manifestAsked = true
        void voicePackAction('check')
      }
    })
  }, [])

  // Installed but the service is not running: start it once; the bar's health check
  // takes over from there ("model load hot aahe" and then ready).
  useEffect(() => {
    if (!pack || startAsked.current) return
    if (pack.installed && !pack.running && !packBusy(pack) && pack.state !== 'error') {
      startAsked.current = true
      void voicePackAction('start')
    }
  }, [pack])

  // Installed and started by the pack: look at the service at once rather than in 4 s.
  const ready = !!pack && (pack.state === 'ready' || (pack.installed && pack.running))
  useEffect(() => {
    if (ready) onReady?.()
  }, [ready, onReady])

  // An engine without the pack API: the bar's own "service band aahe" says enough.
  if (!pack) return failed ? null : <div className="vp-card vl-dim">Voice tapasat aahe…</div>

  if (packBusy(pack)) {
    return (
      <div className="vp-card">
        <PackProgress pack={pack} />
      </div>
    )
  }
  if (pack.state === 'error') {
    return (
      <div className="vp-card">
        <div className="vb-warn">{pack.error || 'Voice download / install zala nahi'}</div>
        <button type="button" className="vb-small" onClick={() => void voicePackAction('install')}>
          Punha prayatna kara
        </button>
      </div>
    )
  }
  if (pack.installed) {
    return <div className="vp-card vl-dim">Voice suru hot aahe…</div>
  }
  const low = diskWarning(pack)
  return (
    <div className="vp-card">
      <div>
        Voice ya PC var nahi — Download kara ({packSizeText(pack)}){freeText(pack)}
      </div>
      {low ? <div className="vb-warn">{low}</div> : null}
      <button
        type="button"
        className="vb-small"
        disabled={!!low}
        onClick={() => void voicePackAction('install')}
      >
        Download ({packSizeText(pack)})
      </button>
    </div>
  )
}

/** Settings > Voice: which pack is here, update it, or take it off this PC. */
export function VoicePackPanel() {
  const { pack, failed } = useVoicePack()
  const [checked, setChecked] = useState(false)
  // "Voice kadhun taka" asks once more in place; it lets go by itself after a few seconds.
  const [armed, setArmed] = useState(false)

  useEffect(() => {
    void refreshVoicePack()
  }, [])

  useEffect(() => {
    if (!armed) return
    const t = window.setTimeout(() => setArmed(false), 6000)
    return () => window.clearTimeout(t)
  }, [armed])

  if (!pack) {
    return (
      <Frame title="Voice pack">
        <p className="muted">{failed ? `Engine kadun uttar nahi: ${failed}` : 'Loading…'}</p>
      </Frame>
    )
  }

  const busy = packBusy(pack)
  const newer = !!pack.available?.version && pack.available.version !== pack.version
  const low = diskWarning(pack)
  return (
    <Frame title="Voice pack">
      <div className="vl-box">
        <div>
          {pack.installed ? `Version ${pack.version || '?'}` : 'Install nahi'}
          {pack.installed ? (
            <span className="vl-dim"> · {pack.running ? 'Chalu aahe' : 'Band aahe'}</span>
          ) : null}
        </div>
        {pack.path ? <div className="vl-dim">{pack.path}</div> : null}
        {busy ? <PackProgress pack={pack} /> : null}
        {pack.state === 'error' && pack.error ? <div className="vb-warn">{pack.error}</div> : null}
        {low ? <div className="vb-warn">{low}</div> : null}
        {!busy ? (
          <div className="vl-row">
            <button
              type="button"
              className="vb-small"
              onClick={async () => {
                await voicePackAction('check')
                setChecked(true)
              }}
            >
              Update tapasa
            </button>
            {newer || (!pack.installed && pack.available) || pack.state === 'error' ? (
              <button
                type="button"
                className="vb-small"
                disabled={!!low}
                onClick={() => void voicePackAction('install')}
              >
                {pack.installed ? 'Update kara' : 'Download kara'} ({packSizeText(pack)})
              </button>
            ) : checked && pack.installed ? (
              <span className="vl-dim">Navin update nahi</span>
            ) : null}
            {pack.installed && !pack.running ? (
              <button type="button" className="vb-small" onClick={() => void voicePackAction('start')}>
                Suru kara
              </button>
            ) : null}
            {pack.installed ? (
              armed ? (
                <>
                  <button
                    type="button"
                    className="vb-small vp-danger"
                    onClick={() => {
                      setArmed(false)
                      void voicePackAction('remove')
                    }}
                  >
                    Ho, voice kadhun taka
                  </button>
                  <button type="button" className="vb-small" onClick={() => setArmed(false)}>
                    Nako
                  </button>
                </>
              ) : (
                <button type="button" className="vb-small" onClick={() => setArmed(true)}>
                  Voice kadhun taka
                </button>
              )
            ) : null}
          </div>
        ) : null}
        {newer && pack.available?.notes ? <div className="vl-dim">{pack.available.notes}</div> : null}
      </div>
    </Frame>
  )
}
