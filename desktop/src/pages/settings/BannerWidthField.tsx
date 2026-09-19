/** Settings -> Appearance -> Home Banner -> width, typed straight in.
 *
 *  The old field was <input type="number" value={Number(...)}>: clearing it
 *  turned "" into 0, the spinner was the only comfortable way to use it, and
 *  the number was pixels -- which above the panel's own width drew nothing new.
 *  This one keeps exactly what the operator types, previews every valid value
 *  on Home as he types, checks the range when he leaves the box, and says what
 *  the banner will really be drawn at. Save Appearance persists it.
 */

import { useEffect, useRef, useState } from 'react'
import { bannerUrl } from '../../api'
import {
  BANNER_PCT_MAX,
  BANNER_PCT_MIN,
  bannerFrameWidth,
  clampBannerPct,
  legacyPxToPct,
  parseBannerPct,
  previewBannerPct,
} from '../../homeBanner'
import { Field, Note } from './SettingsChrome'

export function BannerWidthField({
  appearance,
  setAppearance,
}: {
  appearance: Record<string, unknown>
  setAppearance: React.Dispatch<React.SetStateAction<Record<string, unknown>>>
}) {
  const savedPct = Number(appearance.home_banner_width_pct) || 0
  const legacyPx = Number(appearance.home_banner_size) || 1500
  const isLegacy = savedPct < BANNER_PCT_MIN
  const frame = bannerFrameWidth()
  const inForce = isLegacy ? legacyPxToPct(legacyPx, frame) : clampBannerPct(savedPct)

  const [text, setText] = useState(String(inForce))
  const editing = useRef(false)
  const [src] = useState(() => bannerUrl())
  const [aspect, setAspect] = useState(0)
  const [imgOk, setImgOk] = useState(true)

  // A reload after Save (or a window resize) changes what is in force; follow
  // it -- but never while he is typing.
  useEffect(() => {
    if (!editing.current) setText(String(inForce))
  }, [inForce])

  const input = parseBannerPct(text)

  const apply = (pct: number) => {
    setAppearance((a) => ({ ...a, home_banner_width_pct: pct }))
    previewBannerPct(pct)
  }

  const onChange = (raw: string) => {
    editing.current = true
    setText(raw)
    const p = parseBannerPct(raw)
    if (p.kind === 'ok') apply(p.value)
    else if (p.kind === 'high') apply(BANNER_PCT_MAX)
    // 'low' is usually half a number ("5" on the way to "50"): wait for more.
  }

  const commit = () => {
    editing.current = false
    const p = parseBannerPct(text)
    if (p.kind === 'ok') setText(String(p.value))
    else if (p.kind === 'high') {
      apply(BANNER_PCT_MAX)
      setText(String(BANNER_PCT_MAX))
    } else if (p.kind === 'low') {
      apply(BANNER_PCT_MIN)
      setText(String(BANNER_PCT_MIN))
    } else {
      setText(String(inForce)) // empty or not a number: back to what is in force
    }
  }

  const shownPct =
    input.kind === 'ok'
      ? input.value
      : input.kind === 'high'
        ? BANNER_PCT_MAX
        : inForce
  // Untouched old pixel value: drawn at exactly those pixels, up to the panel.
  const drawnPx =
    isLegacy && input.kind === 'ok' && input.value === inForce
      ? Math.min(legacyPx, frame)
      : Math.round((frame * shownPct) / 100)
  const drawnTall = aspect > 0 ? Math.round(drawnPx * aspect) : 0

  const problem =
    input.kind === 'empty'
      ? `Type a number from ${BANNER_PCT_MIN} to ${BANNER_PCT_MAX}.`
      : input.kind === 'junk'
        ? `Numbers only, ${BANNER_PCT_MIN} to ${BANNER_PCT_MAX}.`
        : input.kind === 'low'
          ? `At least ${BANNER_PCT_MIN}. Leaving the box sets ${BANNER_PCT_MIN}.`
          : input.kind === 'high'
            ? `At most ${BANNER_PCT_MAX}. It is drawn at ${BANNER_PCT_MAX}%.`
            : ''

  return (
    <>
      <Field label={`Banner width (% of the space on Home, ${BANNER_PCT_MIN}–${BANNER_PCT_MAX})`}>
        <span className="settings-inline-row">
          <input
            className="settings-input"
            type="text"
            inputMode="numeric"
            autoComplete="off"
            spellCheck={false}
            aria-invalid={input.kind !== 'ok'}
            style={{ width: 80 }}
            value={text}
            onChange={(e) => onChange(e.target.value)}
            onBlur={commit}
            onKeyDown={(e) => {
              if (e.key === 'Enter') commit()
            }}
          />
          <span>%</span>
        </span>
      </Field>
      {problem ? (
        <Note>
          <strong>{problem}</strong>
        </Note>
      ) : null}
      <Note>
        Home draws the banner <strong>{drawnPx} px</strong> wide
        {drawnTall ? <> and about {drawnTall} px tall</> : null} — {shownPct}% of
        the {frame} px beside Quick Actions. It changes on Home as you type; Save
        Appearance keeps it.
        {isLegacy ? (
          <>
            {' '}
            Saved by an older version as {legacyPx} px
            {legacyPx > frame
              ? `, wider than the ${frame} px there is — so every value above ${frame} drew this same full-width banner`
              : ''}
            . Type a percentage to change it.
          </>
        ) : null}
      </Note>
      {imgOk ? (
        <div
          aria-hidden="true"
          title="Preview: the dashed box is the space the banner has on Home"
          style={{
            maxWidth: 420,
            border: '1px dashed var(--border)',
            borderRadius: 6,
            padding: 4,
            margin: '4px 0 8px',
            background: 'var(--surface-sunken)',
          }}
        >
          <img
            src={src}
            alt=""
            style={{
              display: 'block',
              width: `${shownPct}%`,
              height: 'auto',
              margin: '0 auto',
            }}
            onLoad={(e) => {
              const im = e.currentTarget
              if (im.naturalWidth > 0) setAspect(im.naturalHeight / im.naturalWidth)
            }}
            onError={() => setImgOk(false)}
          />
        </div>
      ) : null}
    </>
  )
}
