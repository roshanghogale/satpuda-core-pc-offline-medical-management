# Desktop screens: design rules (Phase 3, 18 Oct 2026)

The owner sent 27 screenshots of the desktop app (Settings tabs and list pages) and asked for a
redesign: boxes on top of each other (Sales Print Buttons), a grid running off the right edge
with a horizontal scrollbar (Thresholds), cards cutting their own inputs (Medicine Units),
scroll boxes inside scroll boxes (Popup & Thresholds, Stores), walls of help text (Paper &
Copies, Printer Setup), bare tables.

First pass is commit 5ddf92d (look at it): `desktop/src/styles.css` block "Settings, 18 Oct
2026 redesign", `pages/settings/SettingsChrome.tsx` (`Help`, `SaveBar`), ThresholdsPanel and the
Medicine Units table. Everything else follows the same rules.

## Rules

1. **No horizontal scrollbar, no box inside a box that scrolls.** The page scrolls; frames and
   tables inside it grow. A table may cap its height only when it is the page's main list
   (history pages), never inside a Settings frame.
2. **Fields.** `<Field>` is "label | box" (label 210px, box up to 380px). In a row of several
   fields (`.settings-form-row`), a grid, a card or a narrow panel the label sits above the box
   (already automatic for `.settings-form-row` / `.settings-grid-3` / `.settings-field.stacked`).
   Never put a `<Field>` inside a fixed narrow cell. Numbers are short boxes (≈96px), right
   aligned; names and paths are long boxes.
3. **Many similar values (per medicine type, per column, per page) = a table**
   (`.settings-type-table`): one row per thing, small boxes. A find box when there are > 12 rows.
4. **Checkboxes**: a grid of 3 columns (`.settings-check-grid-3`), grouped under small
   headings; no single checkbox floating at the far right.
5. **Text**: one short sentence under a frame title (`<Note>`). Anything longer, or technical
   (paper geometry, dot matrix offsets, how copies work, Sumatra), goes in `<Help>` ("How this
   works", folded). Never delete the information, fold it.
6. **Grouping**: related fields in one `<Frame>` with a plain title. Rarely used / expert
   settings (dot matrix offsets, tear-off, debug log path) in a frame titled "Advanced" whose
   body is folded (`<Help title="Advanced…">` or a `<details>`), so the common settings come
   first.
7. **Save**: the panel's main Save is `<SaveBar>` (sticky at the bottom, shows "Saved." or the
   error beside it). Small inline saves that belong to one field (Save interval, Set PIN) stay
   inline `<SaveBtn>`.
8. **Buttons in a row**: primary action first, the same height, `gap: 8px`; destructive actions
   (Remove, Delete, Danger Zone) red outline, never next to Save without space.
9. **Lists/tables** (settings tables and the list pages): header row sticky, uppercase 10px
   header, zebra rows, numbers right-aligned with tabular figures, actions as small buttons
   (not underlined links) in the last column, an empty state in words ("No payments in these
   dates.") instead of a lone "No rows".
10. **Filters bar on list pages**: one row (wraps on narrow screens): search box first, dates,
    then dropdowns, then the action buttons on the right; labels above the boxes; the summary
    figures (count, total, due) as a strip of small cards above the table.
11. **Light and dark themes** both: only use the CSS variables (`--surface`, `--border`,
    `--text-secondary`, `--accent`, ...), never fixed colours, except black on printed bills.
12. **Keep every behaviour**: same settings keys, same saves, same keyboard shortcuts and
    `data-nav-*` attributes, same voice hooks. This is layout only. Do not rename what the
    owner already knows (tab and section names stay).

## How to work

- Put new CSS in your own file `desktop/src/styles/<area>.css` and import it from the panel /
  page file you own (`import '../../styles/<area>.css'`), so parallel work never edits the same
  stylesheet. Reuse the classes in `styles.css`; do not change them.
- `npx tsc --noEmit` in `desktop/` must pass. Do not start dev servers or the engine (port 8765
  belongs to the voice service); screens are checked by the main session afterwards.
