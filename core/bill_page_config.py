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


def get_classic_layout(paper_size: str = "A5") -> dict:
    """Return page + box dimensions in mm strings for classic template."""
    paper = (paper_size or "A5").upper()
    if paper == "A4":
        body_w, body_h = 297.0, 210.0
        page_css = "@page { size: 297mm 210mm; margin: 0; }"
        scale = 1.0
        toolbar_hint = "A4 Landscape"
        layout_mm = dict(_A4_LAYOUT_MM)
    else:
        body_w, body_h = 210.0, 148.0
        page_css = "@page { size: 210mm 148mm; margin: 0; }"
        toolbar_hint = "A5 Landscape"
        # A5 prints two rotated copies on one sheet; use near-full half-page geometry
        # so the invoice border expands and outside whitespace stays minimal.
        scale = 1.0
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
        "body_pad": "0.8mm" if paper == "A5" else "1.2mm",
    }


def print_toolbar_html(hint: str = "A5 Landscape") -> str:
    return f"""
<div class="print-toolbar">
  <span class="hint">{hint} · margins none/minimum · 100% scale · 2 bills per sheet</span>
  <button type="button" class="print-btn" onclick="window.print()">&#128424; Print Bill</button>
</div>
"""


PRINT_TOOLBAR_CSS = """
  .print-toolbar {
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
  }
  .print-toolbar .hint { color: #fff; font-size: 11pt; }
  .print-btn {
    padding: 10px 28px;
    font-size: 13pt;
    font-weight: bold;
    background: #fff;
    color: #1565c0;
    border: none;
    border-radius: 6px;
    cursor: pointer;
  }
  .print-btn:hover { background: #f0f4ff; }
  @media print {
    .print-toolbar { display: none !important; }
    .bill-page {
      width: 100% !important;
      height: 100% !important;
      display: flex !important;
      padding: 0.8mm !important;
      box-shadow: none !important;
    }
  }
  @media screen {
    html.preview-mode, html.preview-mode body {
      width: auto !important;
      height: auto !important;
      max-width: none !important;
      max-height: none !important;
      overflow: auto !important;
      background: #e8e8e8 !important;
      flex-direction: column !important;
      padding: 0 !important;
    }
    html.preview-mode .bill-page {
      margin: 8px auto;
      display: flex;
      align-items: stretch;
      padding: 0.8mm;
      background: #fff;
      box-shadow: 0 2px 12px rgba(0,0,0,0.15);
    }
  }
"""

# Back-compat aliases
PRINT_TOOLBAR_HTML = print_toolbar_html("A5 Landscape")
