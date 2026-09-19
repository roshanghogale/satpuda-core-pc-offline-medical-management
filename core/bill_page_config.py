"""Page dimensions and base print CSS for classic GST bill."""

# A5 landscape — two rotated bills per sheet (default for pharmacy counter printers)
CLASSIC_BODY_W = "210mm"
CLASSIC_BODY_H = "148mm"

CLASSIC_PAGE_CSS = """
  @page {
    size: 210mm 148mm;
    margin: 0;
  }
"""

# Reference layout tuned for A4 landscape (297×210); scaled for A5 via get_classic_layout()
_A4_LAYOUT_MM = {
    "inv_w": 137.0,
    "inv_h": 91.0,
    "top_h": 27.0,
    "shop_col": 67.0,
    "table_h": 39.0,
    "meta_lbl": 19.0,
    "bottom_row1": 12.0,
    "totals_col": 46.0,
    "cut_sep": 8.0,
    "for_shop_mb": 10.0,
    "th_h": 5.0,
}


def get_pdf_save_layout(pdf_layout: str = "two_copies") -> dict:
    """A5 portrait — upright bills filling each half (no 90° rotation)."""
    layout_mm = {
        "inv_w": 140.0,
        "inv_h": 96.0,
        "top_h": 26.0,
        "shop_col": 66.0,
        "table_h": 38.0,
        "meta_lbl": 19.0,
        "bottom_row1": 11.0,
        "totals_col": 44.0,
        "cut_sep": 6.0,
        "for_shop_mb": 9.0,
        "th_h": 4.8,
    }

    def mm(key: str) -> str:
        return f"{layout_mm[key]:.2f}mm"

    layout_key = (pdf_layout or "two_copies").lower()
    if layout_key == "one_a6_landscape":
        copies = 1
        hint = "A6 Landscape — 1 upright copy (same as A5 bottom half)"
        body_w, body_h = "148mm", "105mm"
        page_css = "@page { size: 148mm 105mm; margin: 0; }"
        paper_size = "A6"
    elif layout_key in ("one_bottom", "one_top"):
        copies = 1
        hint = (
            "A5 Portrait — 1 upright copy, top half"
            if layout_key == "one_top"
            else "A5 Portrait — 1 upright copy, bottom half"
        )
        body_w, body_h = "148mm", "210mm"
        page_css = "@page { size: 148mm 210mm; margin: 0; }"
        paper_size = "A5"
    else:
        copies = 2
        hint = "A5 Portrait — 2 upright copies, cut centre"
        body_w, body_h = "148mm", "210mm"
        page_css = "@page { size: 148mm 210mm; margin: 0; }"
        paper_size = "A5"

    return {
        "paper_size": paper_size,
        "body_w": body_w,
        "body_h": body_h,
        "page_css": page_css,
        "toolbar_hint": hint,
        "pdf_copies": copies,
        "pdf_layout": layout_key if layout_key in (
            "two_copies", "one_bottom", "one_top", "one_a6_landscape",
        ) else "two_copies",
        "inv_w": mm("inv_w"),
        "inv_h": mm("inv_h"),
        "top_h": mm("top_h"),
        "shop_col": mm("shop_col"),
        "table_h": mm("table_h"),
        "meta_lbl": mm("meta_lbl"),
        "bottom_row1": mm("bottom_row1"),
        "totals_col": mm("totals_col"),
        "cut_sep": mm("cut_sep"),
        "for_shop_mb": mm("for_shop_mb"),
        "th_h": mm("th_h"),
        "body_pad": "0.6mm",
    }


def _parse_mm_value(raw: str) -> float:
    text = str(raw or "").strip().lower().replace("mm", "")
    try:
        return float(text)
    except ValueError:
        return 0.0


def pdf_paper_mm_from_settings(settings: dict | None) -> tuple[float, float] | None:
    """Return (width_mm, height_mm) for headless Chrome when saving bill PDFs."""
    if not settings or not settings.get("for_pdf_save"):
        return None
    layout_key = settings.get("pdf_save_layout")
    if not layout_key:
        try:
            from core.bill_save_prefs import load_pdf_save_layout
            layout_key = load_pdf_save_layout()
        except Exception:
            layout_key = "two_copies"
    layout = get_pdf_save_layout(str(layout_key or "two_copies"))
    width = _parse_mm_value(layout.get("body_w", ""))
    height = _parse_mm_value(layout.get("body_h", ""))
    if width <= 0 or height <= 0:
        return None
    return width, height


def get_classic_layout(paper_size: str = "A5") -> dict:
    """Return page + box dimensions in mm strings for classic template."""
    paper = (paper_size or "A5").upper()
    if paper == "A4":
        body_w, body_h = 210.0, 297.0
        page_css = "@page { size: A4 portrait; margin: 0; }"
        toolbar_hint = "A4 Portrait"
        layout_mm = dict(_A4_LAYOUT_MM)
        body_pad = "1.2mm"
    else:
        body_w, body_h = 210.0, 148.0
        page_css = "@page { size: 210mm 148mm; margin: 0; }"
        toolbar_hint = "A5 Landscape"
        layout_mm = {
            "inv_w": 145.0,
            "inv_h": 99.0,
            "top_h": 28.0,
            "shop_col": 69.0,
            "table_h": 42.0,
            "meta_lbl": 20.0,
            "bottom_row1": 12.0,
            "totals_col": 46.0,
            "cut_sep": 6.0,
            "for_shop_mb": 10.0,
            "th_h": 5.0,
        }
        body_pad = "0.8mm"
    scale = 1.0

    def mm(key: str) -> str:
        return f"{round(layout_mm[key] * scale, 2)}mm"

    return {
        "paper_size": paper,
        "body_w": f"{int(body_w)}mm",
        "body_h": f"{int(body_h)}mm",
        "page_css": page_css,
        "toolbar_hint": toolbar_hint,
        "inv_w": mm("inv_w"),
        "inv_h": mm("inv_h"),
        "top_h": mm("top_h"),
        "shop_col": mm("shop_col"),
        "table_h": mm("table_h"),
        "meta_lbl": mm("meta_lbl"),
        "bottom_row1": mm("bottom_row1"),
        "totals_col": mm("totals_col"),
        "cut_sep": mm("cut_sep"),
        "for_shop_mb": mm("for_shop_mb"),
        "th_h": mm("th_h"),
        "body_pad": body_pad,
    }


def print_toolbar_html(hint: str = "A5 Landscape", copies_note: str = "") -> str:
    extra = f" · {copies_note}" if copies_note else ""
    return f"""
<div class="print-toolbar">
  <span class="hint">{hint}{extra} · minimum page margins · 100% scale</span>
  <button type="button" class="print-btn" onclick="window.print()">&#128424; Print Bill</button>
</div>
"""


def print_toolbar_css(body_pad: str = "0.8mm") -> str:
    pad = body_pad or "0.8mm"
    return f"""
  .print-toolbar {{
    display: flex;
    justify-content: center;
    align-items: center;
    gap: 12px;
    padding: 14px 16px;
    background: #1565c0;
    position: sticky;
    top: 0;
    z-index: 9999;
    box-shadow: 0 2px 8px rgba(0,0,0,0.25);
  }}
  .print-toolbar .hint {{ color: #fff; font-size: 11pt; }}
  .print-btn {{
    padding: 10px 28px;
    font-size: 13pt;
    font-weight: bold;
    background: #fff;
    color: #1565c0;
    border: none;
    border-radius: 6px;
    cursor: pointer;
  }}
  .print-btn:hover {{ background: #f0f4ff; }}
  @media print {{
    .print-toolbar {{ display: none !important; }}
    .bill-page {{
      width: 100% !important;
      height: 100% !important;
      display: flex !important;
      padding: {pad} !important;
      box-shadow: none !important;
    }}
  }}
  @media screen {{
    html.preview-mode, html.preview-mode body {{
      width: auto !important;
      height: auto !important;
      max-width: none !important;
      max-height: none !important;
      overflow: auto !important;
      background: #e8e8e8 !important;
      flex-direction: column !important;
      padding: 0 !important;
    }}
    html.preview-mode .bill-page {{
      margin: 8px auto;
      display: flex;
      align-items: stretch;
      padding: {pad};
      background: #fff;
      box-shadow: 0 2px 12px rgba(0,0,0,0.15);
    }}
  }}
"""


PRINT_TOOLBAR_CSS = print_toolbar_css()

# Back-compat aliases
PRINT_TOOLBAR_HTML = print_toolbar_html("A5 Landscape")
