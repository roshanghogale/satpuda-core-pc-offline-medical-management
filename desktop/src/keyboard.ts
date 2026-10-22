/**
 * Global page navigation shortcuts + form Enter/arrow focus chains
 * (matches Tk KeyboardRegistry + core.focus_chain.wire_focus_ring).
 */

export type PageId =
  | 'home'
  | 'sales'
  | 'purchase'
  | 'inventory'
  | 'sales_history'
  | 'purchase_history'
  | 'returns'
  | 'payment'
  | 'general_products'
  | 'settings'

export const PAGE_NAV: { id: PageId; key: string; label: string; ready: boolean }[] = [
  { id: 'home', key: '0', label: 'Home', ready: true },
  { id: 'sales', key: '1', label: 'Sales', ready: true },
  { id: 'purchase', key: '2', label: 'Purchase', ready: true },
  { id: 'inventory', key: '3', label: 'Inventory', ready: true },
  { id: 'sales_history', key: '4', label: 'Sales History', ready: true },
  { id: 'purchase_history', key: '5', label: 'Purchase History', ready: true },
  { id: 'returns', key: '6', label: 'Returns', ready: true },
  { id: 'payment', key: '7', label: 'Payments', ready: true },
  { id: 'settings', key: '8', label: 'Settings', ready: true },
]

const KEY_TO_PAGE: Record<string, PageId> = {
  '`': 'home',
  '0': 'home',
  '1': 'sales',
  '2': 'purchase',
  '3': 'inventory',
  '4': 'sales_history',
  '5': 'purchase_history',
  '6': 'returns',
  '7': 'payment',
  '8': 'settings',
}

export function isTypingTarget(el: EventTarget | null): boolean {
  if (!(el instanceof HTMLElement)) return false
  const tag = el.tagName
  if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return true
  if (el.isContentEditable) return true
  if (el.getAttribute('role') === 'combobox') return true
  if (el.closest('.modern-combo, .two-step-med, input, textarea, select')) {
    return true
  }
  return false
}

export function bindPageShortcuts(onNavigate: (page: PageId) => void): () => void {
  const handler = (e: KeyboardEvent) => {
    if (e.ctrlKey || e.altKey || e.metaKey) return
    // A dialog is asking a question; a bare letter belongs to it, not to the
    // navigator behind it. Without this, a key pressed over an open alert
    // navigated the page out from under the dialog -- and every page stays
    // mounted, so the dialog was left stranded on top of a screen that had
    // already changed. bindEscapeBlur has had this guard all along.
    if (document.querySelector('.modal-backdrop')) return
    if (isTypingTarget(e.target)) return
    const key = e.key === 'Dead' ? '' : e.key
    const page = KEY_TO_PAGE[key]
    if (!page) return
    e.preventDefault()
    onNavigate(page)
  }
  window.addEventListener('keydown', handler)
  return () => window.removeEventListener('keydown', handler)
}

function navOrder(n: HTMLElement) {
  return parseInt(n.getAttribute('data-nav-order') || '0', 10) || 0
}

function isNavEnabled(n: HTMLElement) {
  if (n.hasAttribute('disabled')) return false
  if ((n as HTMLInputElement).disabled) return false
  if (n.getAttribute('aria-disabled') === 'true') return false
  if (n.tabIndex === -1 && !n.hasAttribute('data-nav-order')) return false
  const style = window.getComputedStyle(n)
  if (style.display === 'none' || style.visibility === 'hidden') return false
  return true
}

function chainOf(el: HTMLElement | null): string {
  if (!el) return ''
  const own = el.getAttribute('data-nav-chain')
  if (own) return own
  const host = el.closest<HTMLElement>('[data-nav-chain]')
  return host?.getAttribute('data-nav-chain') || ''
}

function collectNavNodes(root: ParentNode, chain: string): HTMLElement[] {
  const nodes = Array.from(
    root.querySelectorAll<HTMLElement>('[data-nav-order]'),
  ).filter(isNavEnabled)
  const filtered = chain
    ? nodes.filter((n) => chainOf(n) === chain)
    : nodes.filter((n) => !chainOf(n) || chainOf(n) === 'app')
  filtered.sort((a, b) => navOrder(a) - navOrder(b))
  return filtered
}

function focusNav(el: HTMLElement | null | undefined) {
  if (!el) return
  el.focus()
  if (
    el instanceof HTMLInputElement &&
    (el.type === 'text' ||
      el.type === 'number' ||
      el.type === 'search' ||
      el.type === 'tel' ||
      el.type === 'date' ||
      !el.type)
  ) {
    try {
      el.select()
    } catch {
      /* ignore */
    }
  }
  el.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
}

function cursorAtStart(el: HTMLElement): boolean {
  if (!(el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement)) {
    return true
  }
  try {
    const start = el.selectionStart
    return start === null || start <= 0
  } catch {
    return true
  }
}

function cursorAtEnd(el: HTMLElement): boolean {
  if (!(el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement)) {
    return true
  }
  try {
    const end = el.selectionEnd
    const len = el.value?.length ?? 0
    return end === null || end >= len
  } catch {
    return true
  }
}

function fireNavAction(el: HTMLElement, action: string) {
  el.dispatchEvent(
    new CustomEvent('satpuda-nav-action', {
      bubbles: true,
      detail: { action },
    }),
  )
}

/** Custom searchable lists own ArrowUp/Down + Enter while open / focused. */
function isComboField(el: HTMLElement): boolean {
  if (el.getAttribute('role') === 'combobox') return true
  if (el.closest('.modern-combo, .two-step-med')) return true
  return false
}

function isComboListOpen(el: HTMLElement): boolean {
  const host = el.closest('.modern-combo, .two-step-med')
  if (!host) return el.getAttribute('aria-expanded') === 'true'
  if (host.querySelector('.modern-combo-drop, .two-step-drop')) return true
  if (el.getAttribute('aria-expanded') === 'true') return true
  return false
}

/**
 * Enter advances focus along data-nav-order within the same data-nav-chain.
 * data-nav-action on a field/button runs that action instead of moving focus.
 * data-nav-skip-enter="1" leaves Enter alone (e.g. textarea).
 */
export function bindEnterFocusChain(root: ParentNode = document): () => void {
  const handler = (e: KeyboardEvent) => {
    if (e.key !== 'Enter' || e.ctrlKey || e.altKey || e.metaKey) return
    const active = document.activeElement
    if (!(active instanceof HTMLElement)) return
    if (active.tagName === 'TEXTAREA') return
    if (active.getAttribute('data-nav-skip-enter') === '1') return
    // Let ModernCombo / two-step medicine list handle Enter to select the row.
    if (isComboField(active) && isComboListOpen(active)) return

    // Buttons / action elements: activate
    const action =
      active.getAttribute('data-nav-action') ||
      active.closest<HTMLElement>('[data-nav-action]')?.getAttribute('data-nav-action')
    if (action && (active.tagName === 'BUTTON' || active.getAttribute('data-nav-action'))) {
      e.preventDefault()
      if (active.tagName === 'BUTTON') {
        ;(active as HTMLButtonElement).click()
      } else {
        fireNavAction(active, action)
      }
      return
    }

    // Field-level Enter action (e.g. disc → add, online → save)
    const fieldAction = active.getAttribute('data-nav-enter')
    if (fieldAction) {
      e.preventDefault()
      fireNavAction(active, fieldAction)
      return
    }

    const chain = chainOf(active)
    const nodes = collectNavNodes(root, chain)
    if (!nodes.length) return
    const idx = nodes.indexOf(active)
    if (idx < 0) return
    e.preventDefault()
    const next = nodes[(idx + 1) % nodes.length]
    focusNav(next)
  }
  window.addEventListener('keydown', handler)
  return () => window.removeEventListener('keydown', handler)
}

/**
 * Arrow Up/Down/Left/Right cycle fields like Tk wire_focus_ring.
 * Left/Right only leave a text field when the caret is at the edge.
 * Up/Down never leave a combobox — those keys move the dropdown highlight.
 */
export function bindArrowFocusNav(root: ParentNode = document): () => void {
  const handler = (e: KeyboardEvent) => {
    if (e.ctrlKey || e.altKey || e.metaKey) return
    if (
      e.key !== 'ArrowUp' &&
      e.key !== 'ArrowDown' &&
      e.key !== 'ArrowLeft' &&
      e.key !== 'ArrowRight'
    ) {
      return
    }
    const active = document.activeElement
    if (!(active instanceof HTMLElement)) return
    if (!active.hasAttribute('data-nav-order')) return
    if (active.getAttribute('data-nav-skip-arrows') === '1') return

    // Combobox list open: ↑↓ navigate rows (component handles them).
    // When closed, ↑↓ move between form fields as usual.
    if (
      (e.key === 'ArrowUp' || e.key === 'ArrowDown') &&
      isComboField(active) &&
      isComboListOpen(active)
    ) {
      return
    }

    // Native select: let Up/Down change options unless at ends with Alt
    if (active.tagName === 'SELECT' && (e.key === 'ArrowUp' || e.key === 'ArrowDown')) {
      // Still allow arrow-nav between fields when Shift held
      if (!e.shiftKey) return
    }

    if (e.key === 'ArrowLeft' && !cursorAtStart(active)) return
    if (e.key === 'ArrowRight' && !cursorAtEnd(active)) return

    const chain = chainOf(active)
    const nodes = collectNavNodes(root, chain)
    if (nodes.length < 2) return
    const idx = nodes.indexOf(active)
    if (idx < 0) return

    const nextIdx =
      e.key === 'ArrowDown' || e.key === 'ArrowRight'
        ? (idx + 1) % nodes.length
        : (idx - 1 + nodes.length) % nodes.length

    e.preventDefault()
    focusNav(nodes[nextIdx])
  }
  window.addEventListener('keydown', handler)
  return () => window.removeEventListener('keydown', handler)
}

/** Escape clears focus / closes lightweight overlays when not in a modal. */
export function bindEscapeBlur(root: ParentNode = document): () => void {
  const handler = (e: KeyboardEvent) => {
    if (e.key !== 'Escape') return
    if (document.querySelector('.modal-backdrop')) return
    const active = document.activeElement
    if (!(active instanceof HTMLElement)) return
    if (!root.contains(active)) return
    if (active.hasAttribute('data-nav-order')) {
      e.preventDefault()
      active.blur()
    }
  }
  window.addEventListener('keydown', handler)
  return () => window.removeEventListener('keydown', handler)
}
