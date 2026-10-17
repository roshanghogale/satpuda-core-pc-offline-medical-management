/**
 * Today's date on this PC, YYYY-MM-DD. `new Date().toISOString()` is the UTC date, which in
 * India is still yesterday until 05:30: a payment taken after midnight was dated the day
 * before (found 18 Oct 2026, 00:55).
 */
export function todayLocal(d: Date = new Date()): string {
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${d.getFullYear()}-${m}-${day}`
}
