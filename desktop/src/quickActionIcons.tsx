/** Home quick action button icons (white glyphs on colored buttons). */

import type { ReactNode } from 'react'

function IconBase({
  children,
  size = 16,
}: {
  children: ReactNode
  size?: number
}) {
  return (
    <svg
      className="qa-icon-svg"
      width={size}
      height={size}
      viewBox="0 0 16 16"
      aria-hidden
    >
      {children}
    </svg>
  )
}

export function QuickActionIcon({
  actionKey,
  size = 16,
}: {
  actionKey: string
  size?: number
}) {
  const stroke = {
    fill: 'none',
    stroke: 'currentColor',
    strokeWidth: 1.6,
    strokeLinecap: 'round' as const,
    strokeLinejoin: 'round' as const,
  }

  switch (actionKey) {
    case 'new_bill':
      return (
        <IconBase size={size}>
          <path d="M8 3.5v9M4.5 8h7" {...stroke} />
        </IconBase>
      )
    case 'new_purchase':
      return (
        <IconBase size={size}>
          <path d="M3.5 5.5h9v7.5a1 1 0 0 1-1 1h-7a1 1 0 0 1-1-1V5.5z" {...stroke} />
          <path d="M5.5 5.5V4.2a1.2 1.2 0 0 1 1.2-1.2h2.6a1.2 1.2 0 0 1 1.2 1.2V5.5" {...stroke} />
        </IconBase>
      )
    case 'search_medicine':
      return (
        <IconBase size={size}>
          <circle cx="7.2" cy="7.2" r="3.2" {...stroke} />
          <path d="M10 10 12.5 12.5" {...stroke} />
        </IconBase>
      )
    case 'contacts':
      return (
        <IconBase size={size}>
          <circle cx="8" cy="5.8" r="2.2" {...stroke} />
          <path d="M4.2 12.8c.6-2.2 2.2-3.3 3.8-3.3s3.2 1.1 3.8 3.3" {...stroke} />
        </IconBase>
      )
    case 'ledger':
    case 'export_sales':
      return (
        <IconBase size={size}>
          <path d="M3.5 12V8.5M7 12V5.5M10.5 12V7M14 12V4" {...stroke} />
        </IconBase>
      )
    case 'export_purchases':
      return (
        <IconBase size={size}>
          <path d="M8 3v7.5M5.5 7.5 8 10l2.5-2.5" {...stroke} />
          <path d="M4 12.5h8" {...stroke} />
        </IconBase>
      )
    case 'export_inventory':
      return (
        <IconBase size={size}>
          <path d="M3 5.5 8 3l5 2.5V11L8 13.5 3 11V5.5z" {...stroke} />
          <path d="M8 8 8 13.5" {...stroke} />
        </IconBase>
      )
    case 'export_all':
      return (
        <IconBase size={size}>
          <path d="M3.5 4.5h9v8h-9z" {...stroke} />
          <path d="M6 7.5h4M6 9.5h4" {...stroke} />
        </IconBase>
      )
    case 'alerts':
      return (
        <IconBase size={size}>
          <path d="M8 3.2a3.3 3.3 0 0 1 3.3 3.3c0 2.8 1 3.8 1 3.8H3.7s1-1 1-3.8A3.3 3.3 0 0 1 8 3.2z" {...stroke} />
          <path d="M6.8 12.8h2.4" {...stroke} />
        </IconBase>
      )
    case 'general_products':
      return (
        <IconBase size={size}>
          <path d="M4.5 4.5h7v7h-7z" {...stroke} />
          <path d="M6.2 8h3.6" {...stroke} />
        </IconBase>
      )
    default:
      return (
        <IconBase size={size}>
          <circle cx="8" cy="8" r="3.5" {...stroke} />
        </IconBase>
      )
  }
}
