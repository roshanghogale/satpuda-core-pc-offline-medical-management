/** Settings -> Appearance -> Banner width, shared by Home and Settings.
 *
 *  The setting used to be pixels, and a pixel width bigger than the panel the
 *  banner lives in means nothing: the banner cannot be wider than its panel.
 *  On the owner's 1440x900 screen that panel is 1202 px, so his 1200 and the
 *  1500 default drew the same picture -- he changed the number, saved, and saw
 *  nothing. The width is now a share of that panel (10-100 %), so every value
 *  draws a different banner on any screen.
 *
 *  A value an older build saved in pixels (home_banner_size) is still drawn in
 *  pixels until the shop types a percentage: nothing already saved changes
 *  size on update.
 */

export const BANNER_PCT_MIN = 10
export const BANNER_PCT_MAX = 100
/** Fired as the operator types, so Home redraws before Save Appearance. */
export const BANNER_PREVIEW_EVENT = 'satpuda:home-banner-preview'

let measuredFrame = 0

/** Home reports the width it really has for the banner (px). */
export function rememberBannerFrameWidth(px: number) {
  if (px > 0) measuredFrame = Math.round(px)
}

/** The width Home has for the banner. Measured by Home when it has been laid
 *  out; before that, the stylesheet's own arithmetic: .content padding 14+14,
 *  Quick Actions 200, the gap 8 and the panel's two borders. */
export function bannerFrameWidth(): number {
  if (measuredFrame > 0) return measuredFrame
  const vw = typeof window !== 'undefined' ? window.innerWidth : 1280
  return Math.max(300, vw - 238)
}

export function clampBannerPct(n: number): number {
  return Math.max(BANNER_PCT_MIN, Math.min(BANNER_PCT_MAX, Math.round(n)))
}

export type BannerPctInput =
  | { kind: 'empty' }
  | { kind: 'junk' }
  | { kind: 'low'; value: number }
  | { kind: 'high'; value: number }
  | { kind: 'ok'; value: number }

/** What the operator has typed so far. Never coerces: "" is empty, not 0. */
export function parseBannerPct(text: string): BannerPctInput {
  const t = String(text ?? '').trim().replace(/%$/, '').trim()
  if (!t) return { kind: 'empty' }
  if (!/^\d+$/.test(t)) return { kind: 'junk' }
  const value = Number(t)
  if (value < BANNER_PCT_MIN) return { kind: 'low', value }
  if (value > BANNER_PCT_MAX) return { kind: 'high', value }
  return { kind: 'ok', value }
}

/** A saved pixel width read as the share of this panel it actually draws. */
export function legacyPxToPct(px: number, frame: number): number {
  if (!(px > 0) || !(frame > 0)) return BANNER_PCT_MAX
  return clampBannerPct((Math.min(px, frame) / frame) * 100)
}

/** The value --home-banner-width gets on .banner-frame. */
export function bannerCssWidth(pct: number, legacyPx: number): string {
  if (pct >= BANNER_PCT_MIN) return `${clampBannerPct(pct)}%`
  const px = Math.max(300, Math.min(4000, Number(legacyPx) || 1500))
  return `${px}px`
}

export function previewBannerPct(pct: number) {
  window.dispatchEvent(
    new CustomEvent(BANNER_PREVIEW_EVENT, { detail: { pct } }),
  )
}
