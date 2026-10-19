/** The voice pack on screen.
 *
 *  - VoicePackPanel: Settings > Data & System > My Assist (the "Assistant" page). The ONLY
 *    place that offers the download (19 Oct 2026, owner): size, where it is kept, progress,
 *    pause / resume, update, remove. When it finishes, voice comes on by itself (voiceReady.ts):
 *    no restart.
 *  - VoicePackCard: the voice bar while the voice service is not answering. The bar is only
 *    there once the pack is installed, so the card only starts the service; it never offers
 *    the download.
 *  - VoiceNeedsPack: what the Voice Commands panel shows until voice is ready.
 *  All read the one copy in voicePack.ts.
 */
import { useEffect, useRef, useState } from 'react'
import { Frame } from '../pages/settings/SettingsChrome'
import {
  diskWarning,
  packBusy,
  packProgress,
  installLabel,
  packSizeText,
  packStateLine,
  refreshVoicePack,
  sizeText,
  useVoicePack,
  voicePackAction,
  type VoicePack,
} from './voicePack'
import { useVoiceReady } from './voiceReady'

/** Where the download lives: Settings > Data & System > My Assist. */
export const ASSISTANT_PLACE = 'Settings → Data & System → My Assist'

/** The online manifest is read once per app run (for the size shown before downloading). */
let manifestAsked = false

function askManifestOnce(p: VoicePack | null) {
  if (p && !p.installed && !p.available && !packBusy(p) && !manifestAsked) {
    manifestAsked = true
    void voicePackAction('check')
  }
}

/** The state line, a progress bar and Pause while downloading. */
function PackProgress({ pack }: { pack: VoicePack }) {
  const frac = packProgress(pack)
  return (
    <div className="vp-busy">
      <div className="vl-dim">
        {packStateLine(pack)}
        {frac != null ? ` · ${Math.round(frac * 100)}%` : ''}
      </div>
      <div className="vp-bar">
        <span
          className={frac == null ? 'vp-bar-run' : undefined}
          style={frac == null ? undefined : { width: `${Math.round(frac * 100)}%` }}
        />
      </div>
      {pack.state === 'downloading' ? (
        <button type="button" className="vb-small" onClick={() => void voicePackAction('cancel')}>
          Pause (resume later from here)
        </button>
      ) : null}
    </div>
  )
}

/** " · free on this PC: 42 GB". */
const freeText = (p: VoicePack) =>
  p.free_gb == null ? '' : ` · free on this PC: ${Math.round(p.free_gb * 10) / 10} GB`

/** The voice bar while the voice service does not answer. `onReady`: ask the service again now. */
export function VoicePackCard({ onReady }: { onReady?: () => void }) {
  const { pack, failed } = useVoicePack()
  // One start per time the service was found missing (the card mounts each time).
  const startAsked = useRef(false)

  useEffect(() => {
    void refreshVoicePack()
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
        <div className="vb-warn">{pack.error || 'Voice suru zala nahi'}</div>
        <button type="button" className="vb-small" onClick={() => void voicePackAction('start')}>
          Punha prayatna kara
        </button>
      </div>
    )
  }
  if (pack.installed) {
    return <div className="vp-card vl-dim">Voice suru hot aahe…</div>
  }
  // Not reached while the bar is only shown with the pack in; said plainly just in case.
  return <div className="vp-card vl-dim">Voice pack nahi — {ASSISTANT_PLACE}</div>
}

/** Voice Commands until voice is ready: why, and the way to the download. */
export function VoiceNeedsPack({ onOpenAssistant }: { onOpenAssistant?: () => void }) {
  const { pack } = useVoicePack()
  const gate = useVoiceReady()
  useEffect(() => {
    void refreshVoicePack().then(askManifestOnce)
  }, [])
  const size = packSizeText(pack)
  const busy = packBusy(pack)
  return (
    <div className="vp-card" style={{ maxWidth: 560, padding: 14, gap: 8, fontSize: 13 }}>
      {gate.checked && !gate.allowed ? (
        <div>Voice is switched off for this store (admin panel).</div>
      ) : (
        <div>
          Voice needs the voice pack ({size}). Download it from Settings → Assistant
          <span className="vl-dim"> ({ASSISTANT_PLACE})</span>.
        </div>
      )}
      {busy && pack ? <PackProgress pack={pack} /> : null}
      {onOpenAssistant ? (
        <button
          type="button"
          className="btn btn-primary btn-sm"
          style={{ justifySelf: 'start' }}
          onClick={onOpenAssistant}
        >
          Open Assistant settings
        </button>
      ) : null}
    </div>
  )
}

/** Settings > My Assist: download the pack (the only place), or update / remove it. */
export function VoicePackPanel() {
  const { pack, failed } = useVoicePack()
  const gate = useVoiceReady()
  const [checked, setChecked] = useState(false)
  // "Remove" asks once more in place; it lets go by itself after a few seconds.
  const [armed, setArmed] = useState(false)
  // The pack finished while this panel was open: say voice is on now.
  const wasReady = useRef<boolean | null>(null)
  const [cameOn, setCameOn] = useState(false)

  useEffect(() => {
    void refreshVoicePack().then(askManifestOnce)
  }, [])

  useEffect(() => {
    if (wasReady.current === false && gate.ready) setCameOn(true)
    if (!gate.ready) setCameOn(false)
    wasReady.current = gate.ready
  }, [gate.ready])

  useEffect(() => {
    if (!armed) return
    const t = window.setTimeout(() => setArmed(false), 6000)
    return () => window.clearTimeout(t)
  }, [armed])

  if (!pack) {
    return (
      <Frame title="Voice pack">
        <p className="muted">{failed ? `Voice did not answer: ${failed}` : 'Loading…'}</p>
      </Frame>
    )
  }

  const busy = packBusy(pack)
  const newer = pack.installed && !!pack.available?.version && pack.available.version !== pack.version
  const low = diskWarning(pack)
  const size = packSizeText(pack)
  const paused = !pack.installed && !busy && pack.partial > 0
  const total = pack.available?.size || 0
  const notAllowed = gate.checked && !gate.allowed
  return (
    <Frame title="Voice pack">
      <div className="vl-box">
        <div>
          {pack.installed ? (
            <>
              <b>Installed</b> — version {pack.version || '?'}
              <span className="vl-dim"> · {pack.running ? 'voice service running' : 'voice service stopped'}</span>
            </>
          ) : (
            <>
              <b>Not downloaded.</b> Voice commands and the floating voice button come on once the whole
              pack ({size}) is on this PC.
            </>
          )}
        </div>
        {cameOn ? (
          <div style={{ color: '#30a46c' }}>
            Voice is ready — the voice button is on the screen now (no restart needed).
          </div>
        ) : null}
        {notAllowed ? (
          <div className="vb-warn">Voice is switched off for this store (admin panel): the pack cannot be used here.</div>
        ) : null}
        <div className="vl-dim">
          Size: {size}
          {freeText(pack)}
        </div>
        {pack.path ? <div className="vl-dim">Stored in: {pack.path}</div> : null}
        {pack.local && !pack.installed ? (
          <div className="vl-dim">A copy is already on this PC / pendrive: {pack.local} — installs without downloading.</div>
        ) : null}
        {busy ? <PackProgress pack={pack} /> : null}
        {paused ? (
          <div className="vl-dim">
            Paused — {sizeText(pack.partial)}{total ? ` of ${sizeText(total)}` : ''} already downloaded and kept.
          </div>
        ) : null}
        {pack.state === 'error' && pack.error ? <div className="vb-warn">{pack.error}</div> : null}
        {low ? <div className="vb-warn">{low}</div> : null}
        {!busy ? (
          <div className="vl-row">
            {!pack.installed ? (
              <button
                type="button"
                className="vb-small"
                disabled={!!low || notAllowed}
                onClick={() => void voicePackAction('install')}
              >
                {paused ? `Resume download (${size})` : installLabel(pack)}
              </button>
            ) : null}
            <button
              type="button"
              className="vb-small"
              onClick={async () => {
                await voicePackAction('check')
                setChecked(true)
              }}
            >
              Check for update
            </button>
            {newer ? (
              <button
                type="button"
                className="vb-small"
                disabled={!!low || notAllowed}
                onClick={() => void voicePackAction('install')}
              >
                {installLabel(pack, true)}
              </button>
            ) : checked && pack.installed ? (
              <span className="vl-dim">No new update</span>
            ) : null}
            {pack.installed && !pack.running && gate.ready ? (
              <button type="button" className="vb-small" onClick={() => void voicePackAction('start')}>
                Start voice
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
                    Yes, remove voice
                  </button>
                  <button type="button" className="vb-small" onClick={() => setArmed(false)}>
                    No
                  </button>
                </>
              ) : (
                <button type="button" className="vb-small" onClick={() => setArmed(true)}>
                  Remove voice from this PC
                </button>
              )
            ) : null}
          </div>
        ) : null}
        {!pack.installed && !pack.local ? (
          <div className="vl-dim">
            Faster: put SatpudaVoicePack.zip in Downloads / Desktop / a pendrive — it installs from there, no download.
          </div>
        ) : null}
        {newer && pack.available?.notes ? <div className="vl-dim">{pack.available.notes}</div> : null}
      </div>
    </Frame>
  )
}
