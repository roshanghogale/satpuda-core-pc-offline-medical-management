import type { PageId } from './keyboard'

/** Collections that should refresh each desktop page (matches classic Tk). */
const PAGE_COLLECTIONS: Partial<Record<PageId, Set<string>>> = {
  inventory: new Set(['medicines']),
  sales_history: new Set(['sales', 'customer_payments', 'sales_returns']),
  purchase_history: new Set(['purchases', 'supplier_payments', 'purchase_returns', 'suppliers']),
  sales: new Set(['medicines', 'customers', 'customer_payments', 'sales']),
  purchase: new Set(['suppliers', 'medicines', 'supplier_payments', 'purchases']),
  payment: new Set([
    'customer_payments',
    'supplier_payments',
    'sales',
    'purchases',
    'customers',
    'suppliers',
  ]),
  settings: new Set([
    'customer_payments',
    'supplier_payments',
    'sales',
    'purchases',
    'customers',
    'suppliers',
  ]),
  returns: new Set([
    'medicines',
    'sales',
    'sales_returns',
    'purchases',
    'purchase_returns',
    'stock_disposals',
    'customers',
    'customer_payments',
  ]),
  home: new Set([
    'medicines',
    'sales',
    'customer_payments',
    'sales_returns',
    'purchases',
    'supplier_payments',
    'purchase_returns',
  ]),
}

export const PAYMENTS_CHANGED_EVENT = 'satpuda:payments-changed'
export const VILLAGES_CHANGED_EVENT = 'satpuda:villages-changed'
export const DATA_CHANGED_EVENT = 'satpuda:data-changed'

export function dispatchDataChanged(collections: string[]) {
  window.dispatchEvent(
    new CustomEvent(DATA_CHANGED_EVENT, { detail: { collections } }),
  )
}

export function dispatchPaymentsChanged(kind: 'supplier' | 'customer') {
  const collections =
    kind === 'supplier'
      ? ['supplier_payments', 'suppliers', 'purchases']
      : ['customer_payments', 'customers', 'sales']
  window.dispatchEvent(
    new CustomEvent(PAYMENTS_CHANGED_EVENT, { detail: { kind, collections } }),
  )
}

export function dispatchVillagesChanged() {
  window.dispatchEvent(new CustomEvent(VILLAGES_CHANGED_EVENT))
}

export function pageAffectedBySync(page: PageId, collections: string[]): boolean {
  if (!collections.length) return false
  // Always treat 'all' as a full refresh trigger.
  if (collections.includes('all')) return true
  const need = PAGE_COLLECTIONS[page]
  if (!need) return false
  return collections.some((col) => need.has(col))
}
