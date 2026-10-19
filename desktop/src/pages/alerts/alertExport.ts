/** One way out for every alert list: a file (PDF / Excel / CSV, or one PDF per supplier)
 *  or paper (normal printer, dot matrix A4 / A5), through the engine's
 *  /api/settings/alerts/export (core/desktop_export_service.export_alert_sections).
 *  Files land in the shop's Exports folder under a dated name (core/saved_files). */
import { saveFileAndShow, showSavedFile } from '../../savedFiles'
import { exportAlerts } from '../../settingsApi'

export type AlertExportHow = 'pdf' | 'xlsx' | 'csv' | 'pdf_split' | 'printer' | 'dm_a4' | 'dm_a5'

export type AlertSection = { title: string; columns: string[]; rows: unknown[][]; group_by?: string }

/** Runs the export; returns what to tell the shop, throws with the engine's reason. */
export async function runAlertExport(opts: {
  title: string
  sections: AlertSection[]
  how: AlertExportHow
  pageLayout?: 'portrait' | 'landscape'
  fileName?: string
}): Promise<string> {
  const { how } = opts
  const sections = opts.sections.filter((s) => s.rows.length)
  if (!sections.length) throw new Error('Nothing to export — the list is empty.')
  const printTo = how === 'printer' ? 'printer' : how.startsWith('dm_') ? 'dot_matrix' : ''
  const res = await exportAlerts({
    title: opts.title,
    sections,
    format: printTo ? undefined : how === 'pdf_split' ? 'pdf' : (how as 'pdf' | 'xlsx' | 'csv'),
    print_to: printTo as '' | 'dot_matrix' | 'printer',
    paper: how === 'dm_a5' ? 'A5' : 'A4',
    page_layout: opts.pageLayout || 'landscape',
    per_supplier_files: how === 'pdf_split',
    file_name: opts.fileName,
  })
  if (!res.ok) throw new Error(res.error || 'Export did not work.')
  if (res.files?.length) {
    showSavedFile({
      path: res.path || res.files[0].path,
      message: `${res.files.length} PDF file(s), one per supplier, saved in:\n${res.folder || ''}`,
    })
    return `${res.files.length} supplier PDF(s) saved.`
  }
  if (res.content_base64 && res.filename) {
    const saved = await saveFileAndShow(res.filename, res.mime || 'application/octet-stream', res.content_base64)
    return `Saved ${saved?.file_name || res.filename}`
  }
  return res.message || 'Sent to the printer.'
}
