/** The same ordering the engine uses, on the client side.
 *
 *  core/name_search_rank.py ranks what the database returns; this ranks what a
 *  dropdown holds in memory. The two must agree, or the same query looks
 *  different depending on whether the list came from a search or was already
 *  loaded.
 *
 *    1. names that START with what was typed        MECOVET, MELONEX
 *    2. names where a WORD starts with it           AMOXY M, VITAMIN M
 *    3. names that merely contain it somewhere      AMOXY, CALCIMAX
 *    4. alphabetical within each of those groups
 */

export const STARTS_WITH = 0
export const WORD_STARTS_WITH = 1
export const CONTAINS = 2
export const NO_MATCH = 3

/** Characters that separate words, so "AMOXY-M" and "AMOXY 500 M" both count
 *  as having a word starting with "M". Mirrors _WORD_BREAKS in the Python. */
const WORD_BREAKS = ' -/(),.+&_*[]{}:;\'"\\|'

export function matchRank(name: string, query: string): number {
  const q = (query || '').trim().toLowerCase()
  if (!q) return STARTS_WITH
  const n = (name || '').trim().toLowerCase()
  if (!n) return NO_MATCH
  if (n.startsWith(q)) return STARTS_WITH
  // Every occurrence has to be looked at, not just the first. "AMOXY-M" hits on
  // the m of AMOXY before it reaches the M that is its own word, and taking the
  // first hit alone ranked it as a mere contains.
  let pos = n.indexOf(q)
  if (pos < 0) return NO_MATCH
  while (pos >= 0) {
    if (pos > 0 && WORD_BREAKS.includes(n[pos - 1])) return WORD_STARTS_WITH
    pos = n.indexOf(q, pos + 1)
  }
  return CONTAINS
}

/** Order rows for a dropdown by how well their text answers the query.
 *
 *  Within a group the order is alphabetical, matching rank_names() in the
 *  Python. Breaking ties on arrival order instead left the shop with "starts
 *  with M" rows in whatever sequence the caller happened to hold them.
 *
 *  Pass keepOrderWithinGroup when the caller's own sequence carries meaning the
 *  alphabet would destroy -- a most-recent-first list, say. */
export function rankByName<T>(
  rows: readonly T[],
  query: string,
  text: (row: T) => string,
  keepOrderWithinGroup = false,
): T[] {
  const q = (query || '').trim()
  if (!q) return rows.slice()
  return rows
    .map((row, i) => ({ row, i, r: matchRank(text(row), q), t: text(row).trim().toLowerCase() }))
    .sort((a, b) => {
      if (a.r !== b.r) return a.r - b.r
      if (!keepOrderWithinGroup && a.t !== b.t) return a.t < b.t ? -1 : 1
      return a.i - b.i
    })
    .map((x) => x.row)
}
