import type { PageId } from '../keyboard'
import { PAGE_NAV } from '../keyboard'

export function ComingSoonPage({ page }: { page: PageId }) {
  const item = PAGE_NAV.find((p) => p.id === page)
  return (
    <div className="coming-soon">
      <h2 style={{ margin: '0 0 8px' }}>{item?.label || page}</h2>
      <p className="muted" style={{ margin: 0 }}>
        Same workflows as the Python app — this screen will be ported next
        without changing the look.
      </p>
      <p className="muted" style={{ marginTop: 8 }}>
        Shortcut: <kbd>{item?.key ?? '?'}</kbd>
      </p>
    </div>
  )
}
