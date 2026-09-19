"""
Divide bill OCR into three sections:
  1. Supplier & bill info (header)
  2. Totals & tax numbers (footer)
  3. Medicine line items (table body)
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

TABLE_HEADER_KEYS = (
    "HSN", "M.R.P", "MRP", "DESCRIPTION", "PRODUCT", "NAME OF PRODUCT",
    "PACK", "BATCH", "QTY", "RATE", "AMOUNT", "GST%", "SGST", "CGST",
)
FOOTER_KEYS = (
    "GRAND TOTAL", "NET PAYABLE", "NET AMT", "SUB TOTAL", "GROSS :", "GROSS:",
    "NO OF ITEMS", "CGST %", "CGST%", "ADD GST", "DECLARATION", "LEDGER BAL",
    "TAXABLE", "AMOUNT IN WORDS",
)


@dataclass
class BillScanSections:
    supplier_text: str = ""
    supplier_lines: List[str] = field(default_factory=list)
    totals_text: str = ""
    totals_lines: List[str] = field(default_factory=list)
    medicines_text: str = ""
    medicines_lines: List[str] = field(default_factory=list)

    @property
    def combined_text(self) -> str:
        parts = []
        if self.supplier_text.strip():
            parts.append("=== SUPPLIER & BILL INFO ===\n" + self.supplier_text.strip())
        if self.totals_text.strip():
            parts.append("=== TOTALS & TAX ===\n" + self.totals_text.strip())
        if self.medicines_text.strip():
            parts.append("=== MEDICINES ===\n" + self.medicines_text.strip())
        return "\n\n".join(parts)

    @property
    def all_table_lines(self) -> List[str]:
        return list(self.medicines_lines)


def _is_table_header_line(line: str) -> bool:
    u = line.upper()
    return sum(1 for k in TABLE_HEADER_KEYS if k in u) >= 3


def _is_footer_line(line: str) -> bool:
    u = line.upper()
    if re.search(r"G[O0]{1,2}SS\s*[:]", u) and re.search(r"\d+\.\d{2}", line):
        return True
    if "NET" in u and ("AMT" in u or "PAYABLE" in u):
        return True
    return any(k in u for k in FOOTER_KEYS)


def _is_page_break_line(line: str) -> bool:
    from core.bill_page_utils import is_bill_page_artifact_line

    return is_bill_page_artifact_line(line)


def _is_item_line(line: str) -> bool:
    s = line.strip()
    if not s or len(s) < 8:
        return False
    if _is_page_break_line(s):
        return False
    if _is_table_header_line(s):
        return False
    if _is_footer_line(s):
        return False
    if re.match(r"^\d{4,6}\b", s):
        return True
    if re.search(r"\b\d{4,6}\b", s) and re.search(r"\d+\.\d{2}", s):
        return True
    if re.search(r"[A-Z]{3,}", s, flags=re.I) and re.search(r"\d+\.\d{2}", s):
        return True
    return False


def classify_lines_to_sections(lines: Sequence[str]) -> BillScanSections:
    """Split OCR lines into supplier / medicines / totals."""
    supplier: List[str] = []
    medicines: List[str] = []
    totals: List[str] = []
    state = "supplier"

    for raw in lines:
        line = (raw or "").strip()
        if not line:
            continue
        if _is_page_break_line(line):
            continue
        if _is_table_header_line(line):
            state = "medicines"
            continue
        if state == "medicines" and _is_footer_line(line):
            state = "totals"
        if state == "supplier":
            if _is_item_line(line) and not _is_table_header_line(line):
                state = "medicines"
                medicines.append(line)
            else:
                supplier.append(line)
        elif state == "medicines":
            if _is_footer_line(line):
                state = "totals"
                totals.append(line)
            elif _is_item_line(line) or _looks_like_item_continuation(line, medicines):
                medicines.append(line)
            else:
                totals.append(line)
                state = "totals"
        else:
            totals.append(line)

    return BillScanSections(
        supplier_text="\n".join(supplier),
        supplier_lines=supplier,
        totals_text="\n".join(totals),
        totals_lines=totals,
        medicines_text="\n".join(medicines),
        medicines_lines=medicines,
    )


def _looks_like_item_continuation(line: str, medicines: List[str]) -> bool:
    if not medicines:
        return False
    if re.match(r"^\d{4,6}\b", line.strip()):
        return True
    u = line.upper()
    if any(k in u for k in FOOTER_KEYS):
        return False
    return bool(re.search(r"\d+\.\d{2}", line)) and len(line) < 120


def classify_text_sections(
    text: str,
    table_lines: Optional[Sequence[str]] = None,
) -> BillScanSections:
    lines = list(table_lines) if table_lines else (text or "").splitlines()
    return classify_lines_to_sections(lines)


def merge_section_scans(
    supplier_lines: List[str],
    totals_lines: List[str],
    medicines_lines: List[str],
) -> BillScanSections:
    return BillScanSections(
        supplier_text="\n".join(supplier_lines),
        supplier_lines=list(supplier_lines),
        totals_text="\n".join(totals_lines),
        totals_lines=list(totals_lines),
        medicines_text="\n".join(medicines_lines),
        medicines_lines=list(medicines_lines),
    )


# Vertical crop ratios (fraction of image height)
HEADER_FRAC: Tuple[float, float] = (0.0, 0.34)
MEDICINES_FRAC: Tuple[float, float] = (0.24, 0.76)
TOTALS_FRAC: Tuple[float, float] = (0.66, 1.0)
