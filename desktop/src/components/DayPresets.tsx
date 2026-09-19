import { ActionBtn } from '../pages/pageChrome'

/**
 * Seeing one day of history without fighting two date boxes.
 *
 * The history pages open with the whole financial year already sitting in From
 * and To, pushed there by the engine. To read one day the shop had to open the
 * native calendar twice, overwrite both pre-filled dates with the same day, and
 * then press Apply — and Clear did not help, because blank dates round-trip as
 * "the whole FY" and the engine writes them straight back.
 *
 * `from === to` has always been an exact one-day query on both the online and
 * the offline branch, so this is purely the control that was never built.
 */

function iso(d: Date) {
  // Local date, not UTC: toISOString() would hand a shop east of Greenwich
  // yesterday's bills after midnight.
  const m = `${d.getMonth() + 1}`.padStart(2, '0')
  const day = `${d.getDate()}`.padStart(2, '0')
  return `${d.getFullYear()}-${m}-${day}`
}

function shift(days: number) {
  const d = new Date()
  d.setDate(d.getDate() + days)
  return iso(d)
}

function monthStart() {
  const d = new Date()
  return iso(new Date(d.getFullYear(), d.getMonth(), 1))
}

export type DayPreset = {
  key: string
  label: string
  range: () => [string, string]
}

export const DAY_PRESETS: DayPreset[] = [
  { key: 'today', label: 'Today', range: () => [shift(0), shift(0)] },
  { key: 'yesterday', label: 'Yesterday', range: () => [shift(-1), shift(-1)] },
  { key: 'week', label: 'Last 7 days', range: () => [shift(-6), shift(0)] },
  { key: 'month', label: 'This month', range: () => [monthStart(), shift(0)] },
]

export function DayPresetRow({
  from,
  to,
  onPick,
  fyLabel,
  isDefault,
}: {
  from: string
  to: string
  /** Always sets BOTH dates: Export and Print All require the pair. */
  onPick: (from: string, to: string) => void
  fyLabel?: string
  /** True while the boxes hold the engine's default rather than a real choice. */
  isDefault?: boolean
}) {
  const active = DAY_PRESETS.find((p) => {
    const [f, t] = p.range()
    return f === from && t === to
  })

  return (
    <div className="day-presets">
      {DAY_PRESETS.map((p) => (
        <ActionBtn
          key={p.key}
          label={p.label}
          small
          variant={active?.key === p.key ? 'ghost-accent' : 'neutral'}
          onClick={() => {
            const [f, t] = p.range()
            onPick(f, t)
          }}
        />
      ))}
      {/* The pre-filled dates used to look like a step the shop had performed.
          Say where they came from. */}
      {isDefault && fyLabel ? (
        <span className="day-presets-note">Showing {fyLabel} (default)</span>
      ) : null}
    </div>
  )
}
