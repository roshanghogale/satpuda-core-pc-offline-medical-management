/** Where the floating voice button sits, and where its popup opens (19 Oct 2026, owner).
 *
 *  - The button can be dragged anywhere: hold the left mouse button on it and drop it.
 *    A press that moves less than DRAG_PX is a click (it opens the popup).
 *  - Its place is remembered on this PC (localStorage; a convenience only) and kept
 *    inside the window when the window gets smaller.
 *  - The popup opens next to the button on the side that has room: below a button near
 *    the top, above one near the bottom, to the left of one near the right edge -- and
 *    never off the screen.
 *  - The popup can be dragged by its header (until it is made small again).
 *
 *  The maths are plain functions (no React, no DOM) so they can be checked on their own.
 */

export type Pt = { x: number; y: number }
export type Size = { w: number; h: number }
export type Box = { left: number; top: number; width: number; height: number }

/** Movement (px) that makes a press a drag rather than a click. */
export const DRAG_PX = 5
/** Gap kept from the window's edges. */
export const EDGE = 8
/** Gap between the button and its popup. */
export const GAP = 8
/** Where the button sits until it is moved: bottom-right, as before. */
export const HOME_MARGIN = 14

const KEY = 'satpuda.voice.fab.v1'

/** Keep a box of `size` with its top-left at `p` inside the view. */
export function clampPoint(p: Pt, size: Size, view: Size, edge = EDGE): Pt {
  const maxX = Math.max(edge, view.w - size.w - edge)
  const maxY = Math.max(edge, view.h - size.h - edge)
  return {
    x: Math.round(Math.min(Math.max(p.x, edge), maxX)),
    y: Math.round(Math.min(Math.max(p.y, edge), maxY)),
  }
}

/** The button's top-left: where it was dropped (kept inside the view), else bottom-right. */
export function fabPoint(saved: Pt | null, size: Size, view: Size): Pt {
  if (!saved) return clampPoint({ x: view.w - size.w - HOME_MARGIN, y: view.h - size.h - HOME_MARGIN }, size, view)
  return clampPoint(saved, size, view)
}

export type Placement = Pt & { below: boolean; toLeft: boolean; maxH: number }

/** Where the popup goes for a button at `btn`: the side with room, never off the screen.
 *
 *  Vertically: below when it fits below and the button is in the top half (or it only fits
 *  below), above when it fits above, otherwise the side with more room (the popup scrolls
 *  inside `maxH`). Horizontally: a button in the right half opens to the LEFT (popup's right
 *  edge on the button's right edge), one in the left half to the right; then kept inside.
 */
export function placePopup(btn: Box, pop: Size, view: Size, edge = EDGE, gap = GAP): Placement {
  const roomBelow = view.h - (btn.top + btn.height) - gap - edge
  const roomAbove = btn.top - gap - edge
  const upperHalf = btn.top + btn.height / 2 < view.h / 2
  const fitsBelow = pop.h <= roomBelow
  const fitsAbove = pop.h <= roomAbove
  let below: boolean
  if (fitsBelow && (upperHalf || !fitsAbove)) below = true
  else if (fitsAbove) below = false
  else below = roomBelow > roomAbove
  const maxH = Math.max(120, Math.floor(below ? roomBelow : roomAbove))
  const h = Math.min(pop.h, maxH)
  let y = below ? btn.top + btn.height + gap : btn.top - gap - h
  const toLeft = btn.left + btn.width / 2 >= view.w / 2
  let x = toLeft ? btn.left + btn.width - pop.w : btn.left
  const c = clampPoint({ x, y }, { w: pop.w, h }, view, edge)
  x = c.x
  y = c.y
  return { x, y, below, toLeft, maxH }
}

/** Has a press moved far enough to be a drag? */
export const isDrag = (from: Pt, to: Pt, px = DRAG_PX) => Math.hypot(to.x - from.x, to.y - from.y) >= px

const okPt = (v: unknown): v is Pt =>
  !!v && typeof v === 'object' && Number.isFinite((v as Pt).x) && Number.isFinite((v as Pt).y)

/** The dropped place on this PC, or null (never moved / storage blocked). */
export function loadFabPoint(): Pt | null {
  try {
    const raw = window.localStorage.getItem(KEY)
    const v = raw ? (JSON.parse(raw) as unknown) : null
    return okPt(v) ? { x: v.x, y: v.y } : null
  } catch {
    return null
  }
}

export function saveFabPoint(p: Pt | null) {
  try {
    if (p) window.localStorage.setItem(KEY, JSON.stringify({ x: Math.round(p.x), y: Math.round(p.y) }))
    else window.localStorage.removeItem(KEY)
  } catch {
    /* not remembered: fine */
  }
}
