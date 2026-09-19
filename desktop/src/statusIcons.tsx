/** Status glyphs for list badges — danger / warning / success styles. */

import { getApiBase } from './api'

type GlyphKind =
  | 'danger_x'
  | 'danger_bang'
  | 'warning_bang'
  | 'warning_stock'
  | 'success_check'
  | 'info_plus'
  | 'return'
  | 'stop'

type GlyphSpec = {
  bg: string
  fg: string
  kind: GlyphKind
}

const GLYPHS: Record<string, GlyphSpec> = {
  due: { bg: '#9B0000', fg: '#fff', kind: 'danger_bang' },
  partial: { bg: '#9A5500', fg: '#fff', kind: 'warning_bang' },
  cleared: { bg: '#006B1A', fg: '#fff', kind: 'success_check' },
  credit: { bg: '#003D8F', fg: '#fff', kind: 'info_plus' },
  low_stock: { bg: '#7A4F00', fg: '#fff', kind: 'warning_stock' },
  out_of_stock: { bg: '#7A0000', fg: '#fff', kind: 'danger_x' },
  near_expiry: { bg: '#8A6500', fg: '#fff', kind: 'warning_bang' },
  expired: { bg: '#5A0070', fg: '#fff', kind: 'stop' },
  purchase_return: { bg: '#005A60', fg: '#fff', kind: 'return' },
  sales_return: { bg: '#9A0048', fg: '#fff', kind: 'return' },
}

const DEFAULT_GLYPH: GlyphSpec = {
  bg: '#444',
  fg: '#fff',
  kind: 'warning_bang',
}

function GlyphShape({ kind, fg }: { kind: GlyphKind; fg: string }) {
  switch (kind) {
    case 'danger_x':
      return (
        <path
          d="M5.4 5.4 10.6 10.6M10.6 5.4 5.4 10.6"
          fill="none"
          stroke={fg}
          strokeWidth="1.7"
          strokeLinecap="round"
        />
      )
    case 'danger_bang':
    case 'warning_bang':
      return (
        <>
          <path
            d="M8 4.2v4.5"
            fill="none"
            stroke={fg}
            strokeWidth="1.7"
            strokeLinecap="round"
          />
          <circle cx="8" cy="11.2" r="0.85" fill={fg} />
        </>
      )
    case 'warning_stock':
      return (
        <>
          <path
            d="M8 4.4v4.2"
            fill="none"
            stroke={fg}
            strokeWidth="1.7"
            strokeLinecap="round"
          />
          <path
            d="M4.8 12.1h6.4"
            fill="none"
            stroke={fg}
            strokeWidth="1.7"
            strokeLinecap="round"
          />
        </>
      )
    case 'success_check':
      return (
        <path
          d="M5.1 8.1 7.1 10.1 10.9 6.1"
          fill="none"
          stroke={fg}
          strokeWidth="1.7"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      )
    case 'info_plus':
      return (
        <>
          <path
            d="M8 4.8v6.4M5.2 8h5.6"
            fill="none"
            stroke={fg}
            strokeWidth="1.7"
            strokeLinecap="round"
          />
        </>
      )
    case 'return':
      return (
        <>
          <path
            d="M10.6 8H6.2"
            fill="none"
            stroke={fg}
            strokeWidth="1.7"
            strokeLinecap="round"
          />
          <path
            d="M8.2 5.8 6.1 8l2.1 2.2"
            fill="none"
            stroke={fg}
            strokeWidth="1.7"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </>
      )
    case 'stop':
      return (
        <rect
          x="5.4"
          y="5.4"
          width="5.2"
          height="5.2"
          rx="0.8"
          fill="none"
          stroke={fg}
          strokeWidth="1.5"
        />
      )
    default:
      return null
  }
}

export function StatusGlyph({
  status,
  size = 16,
  title,
}: {
  status: string
  size?: number
  title?: string
}) {
  const spec = GLYPHS[status] || DEFAULT_GLYPH
  return (
    <svg
      className="ri-status-svg"
      width={size}
      height={size}
      viewBox="0 0 16 16"
      aria-hidden={title ? undefined : true}
      role={title ? 'img' : undefined}
    >
      {title ? <title>{title}</title> : null}
      <rect x="0.5" y="0.5" width="15" height="15" rx="3.5" fill={spec.bg} />
      <GlyphShape kind={spec.kind} fg={spec.fg} />
    </svg>
  )
}

/** The engine's status/<name>.png files, copied into the demo build.
 *
 *  Falling back to the inline SVG glyph worked, but it is NOT the badge a shop
 *  sees on a real PC -- and the demo exists to show the real thing. These are
 *  the same PNGs assets/status/ serves, shipped as static files so an <img>
 *  can reach them with no engine behind it.
 */
const DEMO_STATUS_ICONS: Record<string, string> = {
  due: 'status_due.png',
  partial: 'status_partial.png',
  cleared: 'status_cleared.png',
  credit: 'status_credit.png',
  low_stock: 'status_low_stock.png',
  out_of_stock: 'status_out_of_stock.png',
  near_expiry: 'status_near_expiry.png',
  expired: 'status_expired.png',
  purchase_return: 'status_purchase_return.png',
  sales_return: 'status_sales_return.png',
}

export function statusIconUrl(status: string, iconSrc?: string | null) {
  // The demo has no engine to serve these, and they are <img> tags so they
  // never reach the fetch interceptor. Point them at the copies that ship with
  // the build; anything unrecognised still falls back to the inline SVG glyph.
  if (import.meta.env.VITE_DEMO === '1') {
    const file = DEMO_STATUS_ICONS[status]
    return file ? `./demo-assets/status/${file}` : null
  }
  if (iconSrc) return `${getApiBase()}${iconSrc}`
  if (!status) return null
  return `${getApiBase()}/api/brand/status-icon?status=${encodeURIComponent(status)}`
}
