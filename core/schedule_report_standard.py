"""Schedule Report for a standard (laser / inkjet) printer: a proper A4 report.

A separate template from the dot matrix one, sharing nothing with it -- no Courier, no 8 pt,
no Classic / Sign reshape, no dot matrix margins or commands. The dot matrix layout printed on
a laser printer came out as a tiny compressed table with half the page empty, and its fixed
column widths added up to more than the page, so the Bill No column was squeezed to nothing
and printed one character per line.

Here every column is sized from what it actually holds, the page turns landscape only when the
table needs the width, the table fills the page width, the header row repeats on every page and
a row is never split across two pages.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from typing import Any

# A4 less the page margins, in mm.
PAGE_MARGIN_MM = 8.0
PORTRAIT_WIDTH_MM = 210.0 - 2 * PAGE_MARGIN_MM
LANDSCAPE_WIDTH_MM = 297.0 - 2 * PAGE_MARGIN_MM

# Font sizes tried, most readable first, and the page each is tried on.
CANDIDATES: tuple[tuple[str, float], ...] = (
    ("portrait", 10.0),
    ("portrait", 9.5),
    ("landscape", 10.0),
    ("landscape", 9.5),
    ("landscape", 9.0),
    ("landscape", 8.5),
)

PAD_MM = 1.5            # left + right padding of a cell, each

NUMERIC = {"qty", "rate", "amount", "mrp", "total"}

# Not on the laser register (owner, 6 Oct 2026): the money columns go, and a blank column
# for the pharmacist's handwritten signature takes their place at the far right.
DROPPED = {"rate", "amount"}
SIGN_HEADER = "Pharmacist Sign"
SIGN_MIN_MM = 32.0      # never narrower than a signature
SIGN_WANT_MM = 52.0     # what it gets first out of the space left over
SHORT = {"sr", "date", "bill no", "batch", "expiry", "schedule", "date / bill", "batch/expiry"}
WRAP_CAP = {"content/drug": 34, "medicine": 30, "customer": 26, "doctor": 26}


@dataclass
class Col:
    name: str
    kind: str            # "num" | "short" | "text"
    need: float          # width in em it needs on one line (short / num), or wants (text)
    least: float         # width in em it can be squeezed to (text); == need otherwise
    full: float = 0.0    # width in em of its longest value on one line


def _w(c: "Col", which: str, pt: float) -> float:
    """A column's width in mm: the sign column is sized in mm, the others from their text."""
    if c.kind == "sign":
        return SIGN_MIN_MM
    return _mm(getattr(c, which), pt)


def em_width(text: str, *, bold: bool = False) -> float:
    """Width of a string in Arial, in em (font size = 1), close enough to size columns."""
    w = 0.0
    for ch in text:
        if ch.isdigit():
            w += 0.556
        elif ch.isupper():
            w += 0.86 if ch in "MW" else (0.3 if ch in "IJ" else 0.72)
        elif ch.islower():
            w += 0.23 if ch in "ijl" else (0.3 if ch in "ftr" else (0.83 if ch in "mw" else 0.53))
        elif ch == " ":
            w += 0.278
        elif ch in "()/.,:;-'":
            w += 0.32
        else:
            w += 0.6
    return w * (1.1 if bold else 1.03)


def _plain(v: Any) -> str:
    return " ".join(str("" if v is None else v).split())


def _fmt_date(v: Any) -> str:
    s = _plain(v)[:10]
    try:
        return datetime.strptime(s, "%Y-%m-%d").strftime("%d/%m/%Y")
    except ValueError:
        return _plain(v)


def _kind(name: str) -> str:
    n = name.strip().lower()
    if n in NUMERIC:
        return "num"
    if n in SHORT:
        return "short"
    return "text"


def plan_columns(headers: list[str], rows: list[list[str]]) -> list[Col]:
    sr = max(em_width("Sr", bold=True), em_width(str(len(rows))))
    cols = [Col("Sr", "short", sr, sr)]
    for j, h in enumerate(headers):
        vals = [r[j] for r in rows if j < len(r)]
        longest = max([em_width(v) for v in vals] + [0.0])
        head_word = max([em_width(w, bold=True) for w in h.split()] + [1.0])
        kind = _kind(h)
        if h == SIGN_HEADER:
            cols.append(Col(h, "sign", 0.0, 0.0, 0.0))
            continue
        if kind in ("num", "short"):
            # On one line: the header may break at its space ("Bill No"), the values do not.
            need = max(head_word, longest, 1.2)
            cols.append(Col(h, kind, need, need, need))
        else:
            cap = WRAP_CAP.get(h.strip().lower(), 24) * 0.6
            word = min(10.0, max([em_width(w) for v in vals for w in v.split()] + [head_word]))
            want = max(word, min(cap, longest))
            cols.append(Col(h, "text", want, max(word, min(want, 7.0)), max(longest, head_word)))
    return cols


def _mm(em: float, font_pt: float) -> float:
    return em * font_pt * 0.3528 + 2 * PAD_MM + 0.5


def _allocate(cols: list[Col], pt: float, avail: float) -> list[float] | None:
    """Column widths in mm that fill `avail`, or None when even the squeezed table is wider."""
    if sum(_w(c, "least", pt) for c in cols) > avail:
        return None
    base = "need" if sum(_w(c, "need", pt) for c in cols) <= avail else "least"
    widths = [_w(c, base, pt) for c in cols]
    spare = avail - sum(widths)
    # The signature column first, to a comfortable width: it has the room the Rate and
    # Amount columns used to take.
    sign = [i for i, c in enumerate(cols) if c.kind == "sign"]
    if spare > 0 and sign:
        for i in sign:
            give = min(spare, max(0.0, SIGN_WANT_MM - widths[i]))
            widths[i] += give
            spare -= give
    # Then the text columns (they wrap less), up to what they want.
    text = [i for i, c in enumerate(cols) if c.kind == "text"]
    if spare > 0 and text and base == "least":
        wants = {i: _mm(cols[i].need, pt) - widths[i] for i in text}
        total_want = sum(wants.values())
        if total_want > 0:
            give = min(spare, total_want)
            for i in text:
                widths[i] += give * wants[i] / total_want
            spare = avail - sum(widths)
    # Then to the text columns whose longest value would still wrap, so a name fits on
    # one line before a column that already fits gets any wider.
    if spare > 0 and text:
        short_of = {i: max(0.0, _mm(cols[i].full, pt) - widths[i]) for i in text}
        total_short = sum(short_of.values())
        if total_short > 0:
            give = min(spare, total_short)
            for i in text:
                widths[i] += give * short_of[i] / total_short
            spare = avail - sum(widths)
    if spare > 0:
        grow = (text + sign) or list(range(len(cols)))
        weight = sum(widths[i] for i in grow) or 1.0
        for i in grow:
            widths[i] += spare * widths[i] / weight
    return widths


def _pages(cols: list[Col], rows: list[list[str]], pt: float, widths: list[float],
           orient: str) -> float:
    """About how many pages the table takes: the row heights, from how often cells wrap."""
    page_h = (297.0 if orient == "portrait" else 210.0) - 22.0
    line = pt * 1.3 * 0.3528
    total = 30.0                                   # the report header on page 1
    for r in rows:
        lines = 1
        for c, w, v in zip(cols[1:], widths[1:], r):
            if c.kind != "text" or not v:
                continue
            inner = max(1.0, w - 2 * PAD_MM)
            lines = max(lines, int(-(-(em_width(v) * pt * 0.3528 * 1.08) // inner)))
        total += lines * line + 2 * 1.4 + 0.5
    return total / page_h


def fit(cols: list[Col], rows: list[list[str]] | None = None) -> tuple[str, float, list[float]]:
    """(orientation, font size, column widths in mm that fill the page width).

    Of the pages and font sizes that hold the table, the one that prints it on the fewest
    pages; a bigger font wins when it costs no more than a tenth more paper.
    """
    rows = rows or []
    tried = []
    for orient, pt in CANDIDATES:
        avail = PORTRAIT_WIDTH_MM if orient == "portrait" else LANDSCAPE_WIDTH_MM
        widths = _allocate(cols, pt, avail)
        if widths is not None:
            tried.append((_pages(cols, rows, pt, widths, orient), orient, pt, widths))
    if not tried:
        orient, pt = CANDIDATES[-1]
        widths = _allocate(cols, pt, 10_000.0) or [_mm(c.least, pt) for c in cols]
        scale = LANDSCAPE_WIDTH_MM / sum(widths)
        return orient, pt, [w * scale for w in widths]
    fewest = min(t[0] for t in tried)
    ok = [t for t in tried if t[0] <= max(fewest * 1.1, fewest + 0.15)]
    # CANDIDATES is in order of preference: portrait before landscape, big type first.
    _, orient, pt, widths = ok[0]
    return orient, pt, widths


def _esc(v: Any) -> str:
    return (str("" if v is None else v).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _store_lines(store: dict[str, Any] | None) -> tuple[str, str]:
    s = store or {}
    name = _plain(s.get("name"))
    bits = [_plain(s.get("address"))]
    if _plain(s.get("phone")):
        bits.append("Ph: " + _plain(s.get("phone")))
    if _plain(s.get("dl_number")):
        bits.append("D.L.: " + _plain(s.get("dl_number")))
    if _plain(s.get("gstin")):
        bits.append("GSTIN: " + _plain(s.get("gstin")))
    return name, "  |  ".join(b for b in bits if b)


def build_html(
    *,
    headers: list[str],
    rows: list[list[Any]],
    title: str,
    schedule_label: str,
    date_range: str,
    store: dict[str, Any] | None = None,
    printed_at: datetime | None = None,
) -> tuple[str, str]:
    """(html, orientation). Rows are the plain export rows, columns as exported."""
    keep = [j for j, h in enumerate(headers) if str(h).strip().lower() not in DROPPED]
    headers = [str(headers[j]) for j in keep] + [SIGN_HEADER]
    body = []
    for r in rows:
        cells = []
        for j, h in zip(keep, headers):
            v = r[j] if j < len(r) else ""
            cells.append(_fmt_date(v) if h.strip().lower() == "date" else _plain(v))
        body.append(cells + [""])                   # the pharmacist signs here, on paper

    cols = plan_columns(headers, body)
    orient, pt, widths = fit(cols, body)
    avail = sum(widths) or 1.0
    colgroup = "".join(f'<col style="width:{w / avail * 100:.2f}%">' for w in widths)

    def cls(c: Col) -> str:
        return {"num": "num", "short": "short", "sign": "sign"}.get(c.kind, "text")

    thead = "".join(f'<th class="{cls(c)}">{_esc(c.name)}</th>' for c in cols)
    trs = []
    for i, cells in enumerate(body, 1):
        tds = [f'<td class="short c">{i}</td>']
        for c, v in zip(cols[1:], cells):
            tds.append(f'<td class="{cls(c)}">{_esc(v) or "&nbsp;"}</td>')
        trs.append("<tr>" + "".join(tds) + "</tr>")

    # Totals: a last row of the body (a tfoot would repeat on every page).
    lower = [h.strip().lower() for h in headers]
    sums: dict[int, str] = {}
    for key, fmt in (("qty", "{:g}"), ("amount", "{:.2f}")):
        if key in lower:
            j = lower.index(key)
            try:
                total = sum(float(str(r[j]).replace(",", "") or 0) for r in body)
                sums[j] = fmt.format(total)
            except ValueError:
                pass
    if sums:
        first = min(sums)
        tds = [f'<td class="total-label" colspan="{first + 1}">Total</td>']
        for j in range(first, len(headers)):
            v = sums.get(j, "")
            tds.append(f'<td class="num">{_esc(v) or "&nbsp;"}</td>')
        trs.append('<tr class="total">' + "".join(tds) + "</tr>")

    name, sub = _store_lines(store)
    when = (printed_at or datetime.now()).strftime("%d/%m/%Y %I:%M %p")
    page_css = "A4 landscape" if orient == "landscape" else "A4 portrait"
    meta = [
        ("Schedule", schedule_label or "All"),
        ("Period", date_range or "-"),
        ("Entries", str(len(body))),
        ("Printed", when),
    ]
    meta_html = "".join(
        f'<span class="m"><span class="k">{_esc(k)}:</span> {_esc(v)}</span>' for k, v in meta
    )
    # CSS text, not HTML: only the quote and backslash need care.
    foot_title = (f"{name} - {title}" if name else title).replace("\\", " ").replace('"', "'")
    html = f"""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">
<title>{_esc(title)}</title>
<style>
@page {{
  size: {page_css};
  margin: 10mm {PAGE_MARGIN_MM}mm 12mm {PAGE_MARGIN_MM}mm;
  @bottom-left {{ content: "{foot_title}"; font: 7.5pt Arial, sans-serif; color: #555; }}
  @bottom-right {{ content: "Page " counter(page) " of " counter(pages); font: 7.5pt Arial, sans-serif; color: #555; }}
}}
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
html, body {{ background: #fff; }}
body {{ font-family: Arial, "Segoe UI", Helvetica, sans-serif; font-size: {pt}pt; color: #000;
  -webkit-print-color-adjust: exact; print-color-adjust: exact; }}
.head {{ text-align: center; margin-bottom: 3mm; }}
.shop {{ font-size: {pt + 5:g}pt; font-weight: 700; letter-spacing: .3pt; }}
.shop-sub {{ font-size: {max(pt - 1, 8):g}pt; color: #333; margin-top: .8mm; }}
h1 {{ font-size: {pt + 3:g}pt; font-weight: 700; margin-top: 2.5mm; padding-bottom: 1.5mm;
  border-bottom: 1.2pt solid #000; }}
.meta {{ display: flex; flex-wrap: wrap; justify-content: center; gap: 1mm 6mm;
  font-size: {max(pt - 0.5, 8):g}pt; margin: 2mm 0 3mm; }}
.meta .k {{ font-weight: 700; }}
table.grid {{ width: 100%; margin: 0 auto; border-collapse: collapse; table-layout: fixed; }}
.grid thead {{ display: table-header-group; }}
.grid th {{ background: #e6e6e6; border: .7pt solid #222; padding: 1.6mm {PAD_MM}mm;
  font-weight: 700; text-align: left; vertical-align: bottom; line-height: 1.2; }}
.grid td {{ border: .5pt solid #444; padding: 1.4mm {PAD_MM}mm; vertical-align: top;
  line-height: 1.3; }}
.grid tr {{ break-inside: avoid; page-break-inside: avoid; }}
.grid tbody tr:nth-child(even) td {{ background: #f6f6f6; }}
.grid .text {{ overflow-wrap: anywhere; }}
.grid td.short, .grid td.num {{ white-space: nowrap; }}
.grid .num {{ text-align: right; font-variant-numeric: tabular-nums; }}
.grid .c, .grid th:first-child {{ text-align: center; }}
.grid tr.total td {{ background: #fff; font-weight: 700; border-top: 1.2pt solid #000; }}
.grid td.total-label {{ text-align: right; }}
.end {{ text-align: center; font-size: {max(pt - 1.5, 7.5):g}pt; color: #555; margin-top: 2mm; }}
</style></head><body>
<div class="head">
  {f'<div class="shop">{_esc(name)}</div>' if name else ''}
  {f'<div class="shop-sub">{_esc(sub)}</div>' if sub else ''}
  <h1>{_esc(title)}</h1>
</div>
<div class="meta">{meta_html}</div>
<table class="grid"><colgroup>{colgroup}</colgroup>
<thead><tr>{thead}</tr></thead>
<tbody>{''.join(trs)}</tbody>
</table>
<div class="end">End of report - {len(body)} entr{'y' if len(body) == 1 else 'ies'}</div>
</body></html>"""
    return html, orient


def save_pdf(html: str, base_name: str, orientation: str) -> tuple[str | None, str]:
    """Saved where the schedule reports go (Documents save folder -> reports)."""
    from core.bill_save_prefs import resolve_sales_report_save_dir
    from core.document_output import _safe_doc_filename, save_html_as_pdf

    save_dir = resolve_sales_report_save_dir()
    base_path = os.path.join(save_dir, _safe_doc_filename(base_name))
    w, h = (297.0, 210.0) if orientation == "landscape" else (210.0, 297.0)
    return save_html_as_pdf(html, base_path, paper_width_mm=w, paper_height_mm=h)
