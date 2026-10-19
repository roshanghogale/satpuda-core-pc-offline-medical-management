/** The Export / Print box of every alert list (owner, 19 Oct 2026: "these options should be
 *  in each Alert & Monitoring list according to their requirements"). The Export button of
 *  Settings -> Alert & Monitoring and of the alerts window both open it.
 *
 *  Two clear choices for the list on screen, worded for that list:
 *    - Expired / Near Expiry, Low / Out of Stock: "Export list" or "Export list by supplier"
 *      (supplier name, then its medicines and a total) -- for returns, for reorders;
 *    - Customer Dues: "Export list" or "Export list by village / area" (area name, then its
 *      customers and the total due).
 *  "All lists" applies each list's own grouping. Formats: Excel, PDF, PDF per supplier /
 *  area (one file each), CSV, normal printer, dot matrix A4 / A5. The engine does the
 *  cutting (core/desktop_export_service.export_alert_sections, section.group_by).
 */
import { useEffect, useRef, useState } from 'react'
import { groupIndex, groupKindOf, groupRows, isBatchList, type GroupKind } from './AlertBoard'
import { runAlertExport, type AlertExportHow, type AlertSection } from './alertExport'
import '../../styles/alerts.css'

export type ExportList = {
  key: string
  /** short name: "Low Stock" */
  title: string
  /** what this list exports, ungrouped: title (with any period), columns, rows */
  section: AlertSection
  /** the engine's group column: "Supplier" / "Area" ('' = never grouped) */
  groupBy?: string
}

type Props = {
  lists: ExportList[]
  /** key of the list on screen */
  current: string
  /** line under the heading ("Alert & Monitoring · Oct 2026") */
  subtitle?: string
  /** title / file name of an all-lists export */
  allTitle?: string
  allFileName?: string
  /** the screen is grouped: the box starts on "by supplier / area" */
  startGrouped?: boolean
  onClose: () => void
  onDone: (msg: string) => void
}

function kindOf(l: ExportList): GroupKind | '' {
  const k = groupKindOf(l.groupBy)
  return k && groupIndex(l.section.columns, k) >= 0 ? k : ''
}

function isDues(l: ExportList | undefined): boolean {
  return Boolean(l && kindOf(l) === 'area')
}

export function AlertExportBox({
  lists,
  current,
  subtitle,
  allTitle = 'Alert & Monitoring',
  allFileName,
  startGrouped = false,
  onClose,
  onDone,
}: Props) {
  const [which, setWhich] = useState<'current' | 'all'>('current')
  const [how, setHow] = useState<AlertExportHow>('xlsx')
  const [pageLayout, setPageLayout] = useState<'portrait' | 'landscape'>('landscape')
  const [grouped, setGrouped] = useState(startGrouped)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const boxRef = useRef<HTMLDivElement>(null)

  // Keys belong to the box while it is open (the alerts window under it acts on Esc / arrows).
  useEffect(() => {
    boxRef.current?.focus()
  }, [])

  const cur = lists.find((l) => l.key === current) || lists[0]
  const picked = which === 'all' ? lists : cur ? [cur] : []
  const groupable = picked.filter((l) => kindOf(l))
  const kinds = new Set(groupable.map((l) => kindOf(l)))
  const canGroup = groupable.length > 0
  const isGrouped = canGroup && (grouped || how === 'pdf_split')
  const sections: AlertSection[] = picked.map((l) => ({
    ...l.section,
    group_by: isGrouped && kindOf(l) ? l.groupBy : '',
  }))
  const total = sections.reduce((n, s) => n + s.rows.length, 0)
  // one file per supplier / area of every grouped list
  const groupCounts = groupable.map(
    (l) => groupRows(l.section.columns, l.section.rows, kindOf(l) as GroupKind).length,
  )
  const files = groupCounts.reduce((n, c) => n + c, 0)
  const single = which === 'current'
  const kindWord = kinds.size > 1 ? 'supplier / area' : kinds.has('area') ? 'area' : 'supplier'

  async function go() {
    setBusy(true)
    setError('')
    try {
      const note = await runAlertExport({
        title: single ? cur?.section.title || 'Alerts' : allTitle,
        sections: how === 'pdf_split' ? sections.filter((s) => s.group_by) : sections,
        how,
        pageLayout,
        fileName: single ? undefined : allFileName,
      })
      onDone(note)
      onClose()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const isPrint = how.startsWith('dm_') || how === 'printer'
  const showPage = how !== 'csv' && how !== 'xlsx' && how !== 'dm_a5'
  const formats: { key: AlertExportHow; label: string; sub: string }[] = [
    { key: 'xlsx', label: 'Excel', sub: '.xlsx' },
    { key: 'pdf', label: 'PDF', sub: isGrouped ? 'one file' : '.pdf' },
    ...(isGrouped
      ? [
          {
            key: 'pdf_split' as AlertExportHow,
            label: kinds.size > 1 ? 'PDF per group' : `PDF per ${kindWord}`,
            sub: `${files} file${files === 1 ? '' : 's'}`,
          },
        ]
      : []),
    { key: 'csv', label: 'CSV', sub: '.csv' },
    { key: 'printer', label: 'Print', sub: 'laser / inkjet' },
    { key: 'dm_a4', label: 'Dot matrix', sub: 'A4' },
    { key: 'dm_a5', label: 'Dot matrix', sub: 'A5' },
  ]
  const pickLayout = (on: boolean) => {
    setGrouped(on)
    if (!on && how === 'pdf_split') setHow('pdf')
  }

  // The words of the two layout cards follow the list (or lists) chosen.
  const dues = single && isDues(cur)
  const layoutLabel = single
    ? dues
      ? 'How the customers are listed'
      : 'How the medicines are listed'
    : 'How each list is laid out'
  const plain = single
    ? { b: 'Export list', s: dues ? 'One table of every customer with a due' : 'One table of all medicines' }
    : { b: 'Export lists', s: 'Each list as one table' }
  const byGroup: { b: string; s: string; t?: string } = single
    ? dues
      ? { b: 'Export list by village / area', s: 'Area name, then its customers and the total due' }
      : {
          b: 'Export list by supplier',
          // expiry: the supplier the batch came from; stock: the one last bought from
          s: 'Supplier name, then its medicines and a total',
          t:
            cur && isBatchList(cur.section.columns)
              ? 'Each batch under the supplier it was bought from (for returns)'
              : 'Each medicine under the supplier it was last bought from (for reorders)',
        }
    : kinds.size > 1
      ? { b: 'Export lists grouped', s: 'Stock and expiry by supplier, dues by village / area' }
      : kinds.has('area')
        ? { b: 'Export lists grouped', s: 'Dues by village / area, then its customers and the total due' }
        : { b: 'Export lists by supplier', s: 'Supplier name, then its medicines and a total' }

  const countNote = isGrouped
    ? single
      ? ` · ${files} ${kindWord === 'area' ? (files === 1 ? 'area' : 'areas') : files === 1 ? 'supplier' : 'suppliers'}`
      : ` · ${files} group${files === 1 ? '' : 's'}`
    : ''

  return (
    <div
      className="modal-backdrop"
      role="presentation"
      onClick={(e) => {
        e.stopPropagation()
        if (e.target === e.currentTarget) onClose()
      }}
      onKeyDown={(e) => {
        e.stopPropagation()
        if (e.key === 'Escape') {
          e.preventDefault()
          onClose()
        }
      }}
    >
      <div
        ref={boxRef}
        tabIndex={-1}
        className="alx-xp"
        role="dialog"
        aria-modal="true"
        aria-label="Export or print alerts"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="alx-xp-head">
          <div>
            <h2>Export / Print — {single ? cur?.title : 'all lists'}</h2>
            <p>{subtitle || 'Alert & Monitoring'}</p>
          </div>
          <button type="button" className="alx-close" onClick={onClose} aria-label="Close">
            ✕
          </button>
        </div>
        <div className="alx-xp-body">
          <div className="alx-xp-label">Which list</div>
          <div className="alx-xp-cards two">
            <button type="button" className={`alx-xp-card${which === 'current' ? ' on' : ''}`}
              onClick={() => setWhich('current')}>
              <b>This list</b>
              <span>
                {cur?.title} ({cur?.section.rows.length ?? 0})
              </span>
            </button>
            <button type="button" className={`alx-xp-card${which === 'all' ? ' on' : ''}`}
              onClick={() => setWhich('all')}>
              <b>All lists</b>
              <span>{lists.map((l) => l.title).join(', ')}</span>
            </button>
          </div>

          {canGroup ? (
            <>
              <div className="alx-xp-label">{layoutLabel}</div>
              <div className="alx-xp-cards two">
                <button type="button" className={`alx-xp-card${!isGrouped ? ' on' : ''}`}
                  onClick={() => pickLayout(false)}>
                  <b>{plain.b}</b>
                  <span>{plain.s}</span>
                  <i className="alx-xp-mini">
                    <em />
                    <em />
                    <em />
                  </i>
                </button>
                <button type="button" className={`alx-xp-card${isGrouped ? ' on' : ''}`}
                  title={byGroup.t} onClick={() => pickLayout(true)}>
                  <b>{byGroup.b}</b>
                  <span>{byGroup.s}</span>
                  <i className="alx-xp-mini grouped">
                    <strong />
                    <em />
                    <em />
                    <strong />
                    <em />
                  </i>
                </button>
              </div>
            </>
          ) : null}

          <div className="alx-xp-label">Format</div>
          <div className="alx-xp-formats">
            {formats.map((f) => (
              <button key={f.key} type="button" className={`alx-xp-fmt${how === f.key ? ' on' : ''}`}
                onClick={() => setHow(f.key)}>
                <b>{f.label}</b>
                <span>{f.sub}</span>
              </button>
            ))}
          </div>

          {showPage ? (
            <>
              <div className="alx-xp-label">Page</div>
              <div className="alx-xp-seg">
                <button type="button" className={pageLayout === 'portrait' ? 'on' : ''}
                  onClick={() => setPageLayout('portrait')}>
                  A4 vertical
                </button>
                <button type="button" className={pageLayout === 'landscape' ? 'on' : ''}
                  onClick={() => setPageLayout('landscape')}>
                  A4 horizontal
                </button>
              </div>
            </>
          ) : null}
          {error ? <p className="settings-error">{error}</p> : null}
        </div>
        <div className="alx-xp-foot">
          <span className="alx-xp-count">
            {total} rows
            {countNote}
            {single ? '' : ` · ${sections.length} lists`}
          </span>
          <button type="button" className="btn btn-neutral" onClick={onClose}>
            Cancel
          </button>
          <button type="button" className="btn btn-primary" disabled={busy || total === 0}
            onClick={() => void go()}>
            {busy ? 'Working…' : isPrint ? 'Print' : how === 'pdf_split' ? `Save ${files} PDFs` : 'Save file'}
          </button>
        </div>
      </div>
    </div>
  )
}
