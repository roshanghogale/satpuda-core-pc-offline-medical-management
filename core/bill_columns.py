"""
Read a supplier bill's columns by what they hold, not only by where they sit.

Every distributor prints a different table. The same Marg ERP bill came in with
the batch number in the maker column because a fixed left-to-right reader
counted one token too many on a line whose pack was blank. A bill reader that
trusts positions breaks on the next format; this one:

1. finds the header row by its words (MRP, HSN, Description, Pack, MFG, Batch,
   Exp., Qty, Free, Rate, Dis%, GST%, Amount -- and the many ways bills spell
   them), for CSV / Excel rows, PDF tables and plain PDF text lines alike;
2. maps the columns by those headers, then checks every column by the SHAPE of
   its values (a batch has digits, an expiry is a month and year, an HSN is 4-8
   digits, a GST rate is 0/5/12/18/28, a maker code has no digits) and by the
   bill's own arithmetic (qty x rate, less any discount, is the amount), and
   swaps columns that a header or an AI reader put in the wrong place;
3. for PDF text, where columns are only spaces apart and the medicine name has
   spaces of its own, reads each line from both ends: the fixed-shape fields
   from the right (amount, GST, discount, rate, free, qty, expiry, batch) and
   from the left (serial, MRP, HSN), leaving name + pack + maker in the middle,
   which are then split by their shapes (a pack is 4'S / 10S / 100ML / 1VIAL /
   23GM / 1KG; a maker is the short code after it);
4. marks every value it could not prove (``check_fields``) so the Purchase page
   can show the shop which cells to look at, instead of guessing silently.

Nothing here knows any supplier by name.
"""
from __future__ import annotations

import re
from itertools import combinations, permutations
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

# ---------------------------------------------------------------------------
# Value shapes
# ---------------------------------------------------------------------------

GST_RATES = (0.0, 0.1, 0.25, 3.0, 5.0, 12.0, 18.0, 28.0, 40.0)
GST_HALF_RATES = (0.05, 0.125, 1.5, 2.5, 6.0, 9.0, 14.0, 20.0)

MISSING_MARKERS = frozenset({"", "-", "--", "**", "*", "NA", "N/A", "NIL", "NONE", "XX"})

_MONTHS = "JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|SEPT|OCT|NOV|DEC"

_EXPIRY_RES = (
    # 2/29, 02/29, 02-2029, 2.29
    re.compile(r"^(0?[1-9]|1[0-2])[/\-.](\d{2}|20\d{2})$"),
    # 01/02/2029, 1-2-29 (day first)
    re.compile(r"^(0?[1-9]|[12]\d|3[01])[/\-.](0?[1-9]|1[0-2])[/\-.](\d{2}|20\d{2})$"),
    # FEB-29, FEB/2029, FEB29, FEB'29
    re.compile(r"^(" + _MONTHS + r")[A-Z]*[\-/ ']?(\d{2}|20\d{2})$"),
    # 2029-02, 2029/02/01
    re.compile(r"^(20\d{2})[/\-](0?[1-9]|1[0-2])(?:[/\-]\d{1,2})?$"),
)
# 01022029 (Marg CSV DDMMYYYY) -- only where a column says it is an expiry.
_EXPIRY_COMPACT_RE = re.compile(r"^(0[1-9]|[12]\d|3[01])(0[1-9]|1[0-2])(20\d{2})$")

# Pack: 4'S 10S 10*S 1X10 1*10 100ML 23GM 1KG 1VIAL 5 ML 1 S 300MG 1LTR 2.5KG
_PACK_UNITS = (
    r"(?:'S|`S|’S|\*S|S|ML|M\.L\.?|GMS|GM|GRM|G|KGS|KG|MG|MCG|LTRS|LTR|LT|L|"
    r"VIALS|VIAL|AMPS|AMP|TABS|TAB|CAPS|CAP|PCS|PC|NOS|NO|N|P|STRIPS|STRIP|"
    r"SACHETS|SACHET|DOSES|DOSE|BOTTLES|BOTTLE|BTL|BOT|BO|PIC|PKT|JAR|TUBE|BOX)"
)
_PACK_RE = re.compile(
    r"^(?:\d+(?:\.\d+)?\s*" + _PACK_UNITS + r"\.?"
    r"|\d+\s*[X×*]\s*\d+(?:\.\d+)?\s*(?:" + _PACK_UNITS + r")?\.?)$",
    re.IGNORECASE,
)
# A pack and a maker code printed with no space between them: 450GMVET, 300MGVIR.
_PACK_MFG_JAMMED_RE = re.compile(
    r"^(\d+(?:\.\d+)?(?:'S|ML|GMS|GM|KG|MG|LTR|VIAL|AMP))([A-Z]{2,6})$",
    re.IGNORECASE,
)
# Dosage-form words: a pack only when the product name already says it too
# ("PAN-40MG IV INJ IV": the second IV is the pack).
_FORM_WORDS = frozenset({
    "IV", "IM", "INJ", "VIAL", "AMP", "TAB", "TABS", "CAP", "CAPS", "SYP", "SYR",
    "OINT", "CRM", "CREAM", "GEL", "DT", "LIQ", "SUSP", "DROPS", "BOLUS", "POWDER",
    "PDR", "SPRAY", "LOTION", "SOAP", "STRIP",
})


def clean(value: Any) -> str:
    if value is None:
        return ""
    try:
        if value != value:  # NaN
            return ""
    except Exception:
        pass
    text = str(value).replace("\r", " ").replace("\n", " ").replace(" ", " ")
    return re.sub(r"\s+", " ", text).strip()


def to_num(value: Any) -> Optional[float]:
    """A number as a bill prints it (1,234.50 / 0% / Rs.12 / 5.00), else None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        try:
            v = float(value)
        except Exception:
            return None
        return None if v != v else v
    s = clean(value)
    if not s:
        return None
    s = re.sub(r"^(?:rs\.?|inr|₹)\s*", "", s, flags=re.IGNORECASE)
    s = s.replace(",", "").rstrip("%").strip()
    if s.endswith("/-"):
        s = s[:-2]
    if not re.fullmatch(r"-?(?:\d+\.?\d*|\.\d+)", s):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def num(value: Any) -> float:
    v = to_num(value)
    return float(v) if v is not None else 0.0


def is_missing(value: Any) -> bool:
    return clean(value).upper() in MISSING_MARKERS


def is_expiry(value: Any, *, allow_compact: bool = False) -> bool:
    if hasattr(value, "year") and hasattr(value, "month"):
        return True
    s = clean(value).upper()
    if not s or len(s) > 12:
        return False
    s = s.replace(" ", "")
    if any(rx.match(s) for rx in _EXPIRY_RES):
        return True
    return bool(allow_compact and _EXPIRY_COMPACT_RE.match(s))


def is_batch(value: Any) -> bool:
    """A batch number: 2-20 letters/digits (with - / .), not a date, not a price."""
    s = clean(value).upper()
    if not 2 <= len(s) <= 20 or is_missing(s):
        return False
    if is_expiry(s, allow_compact=False):
        return False
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9\-/.]*", s):
        return False
    if re.fullmatch(r"\d+\.\d{1,2}", s):  # 64.80 is a price
        return False
    return bool(re.search(r"\d", s))


def is_hsn(value: Any) -> bool:
    return bool(re.fullmatch(r"\d{4,8}", clean(value)))


def is_gst_rate(value: Any) -> bool:
    v = to_num(value)
    return v is not None and any(abs(v - r) < 0.01 for r in GST_RATES)


def is_mfg(value: Any) -> bool:
    """A maker code or company name: letters (with . & ' - spaces), no digits."""
    s = clean(value)
    if not s or len(s) > 45:
        return False
    if re.search(r"\d", s):
        return False
    return bool(re.fullmatch(r"[A-Za-z][A-Za-z .&'\-()/]*", s))


def is_pack(value: Any, *, allow_plain: bool = False) -> bool:
    s = clean(value)
    if not s or len(s) > 14:
        return False
    if _PACK_RE.match(s) or _PACK_RE.match(s.replace(" ", "")):
        return True
    if allow_plain and re.fullmatch(r"\d{1,3}", s) and 0 < int(s) <= 100:
        return True
    return False


def has_letters(value: Any) -> bool:
    return bool(re.search(r"[A-Za-z]", clean(value)))


# ---------------------------------------------------------------------------
# Header words
# ---------------------------------------------------------------------------

NUMERIC_FIELDS = frozenset({
    "mrp", "qty", "free_qty", "rate", "discount_pct", "disc_rupees", "disc",
    "gst_pct", "cgst_pct", "sgst_pct", "igst_pct", "cgst_amt", "sgst_amt",
    "gst_amt", "amount", "taxable", "rate_a", "rate_b", "rate_c", "ptr", "pts",
})
TEXT_FIELDS = frozenset({"name", "pack", "manufacturer"})
OPTIONAL_FIELDS = frozenset({
    "sr", "mrp", "hsn_code", "free_qty", "discount_pct", "disc_rupees", "disc",
    "gst_pct", "cgst_pct", "sgst_pct", "igst_pct", "cgst_amt", "sgst_amt",
    "gst_amt", "taxable", "rate_a", "rate_b", "rate_c", "mfg_date", "ignore",
    "ptr", "pts",
})

# Normalised header word (lower case, letters/digits/% only) -> field.
_HEADER_WORDS: Dict[str, str] = {}


def _add(field: str, *words: str) -> None:
    for w in words:
        _HEADER_WORDS[_hkey(w)] = field


def _hkey(text: Any) -> str:
    return re.sub(r"[^a-z0-9%]+", "", clean(text).lower())


_add("sr", "sr", "sr.", "sr no", "srno", "s no", "s.no", "sno", "sl", "sl no",
     "slno", "sn", "sn.", "s.n.", "no.", "#")
_add("mrp", "mrp", "m.r.p", "m.r.p.", "m r p", "mrp rs", "new mrp", "mrp/unit",
     "mrp per unit", "retail price")
_add("hsn_code", "hsn", "hsn code", "hsncode", "hsn/sac", "hsn sac", "sac",
     "hsn no", "hsn no.")
_add("name", "description", "desc", "particulars", "particular", "product",
     "products", "product name", "productname", "item", "items", "item name",
     "itemname", "item description", "name of product", "name of the product",
     "medicine", "medicine name", "name", "description of goods",
     "product description", "item details", "drug name")
_add("pack", "pack", "packing", "pkg", "pkg.", "pck", "pack size", "packsize",
     "pak", "size", "uom", "unit pack", "pk")
_add("manufacturer", "mfg", "mfg.", "mfr", "mfr.", "mfgr", "manufacturer",
     "manufacturer name", "company", "comp", "comp.", "co", "co.", "make",
     "mkr", "mfg name", "company name", "mfg co", "mfd by", "marketed by")
_add("batch", "batch", "batch no", "batch no.", "batchno", "batch number",
     "b.no", "b.no.", "b no", "bno", "lot", "lot no", "btch", "batch #")
_add("expiry", "exp", "exp.", "expiry", "exp date", "exp. date", "expdt",
     "exp dt", "exp.dt", "expiry date", "expd", "exp. dt.", "expiry dt",
     "use before")
_add("mfg_date", "mfg date", "mfg dt", "mfg.dt", "mfd", "mfd date", "mfg. date",
     "date of mfg", "mfgdt")
_add("qty", "qty", "qty.", "quantity", "qnty", "billed qty", "bill qty", "qty billed",
     "sale qty", "nos", "quan", "qtty")
_add("free_qty", "free", "free qty", "freeqty", "fqty", "f.qty", "fr", "fr.",
     "sch", "sch.", "sch qty", "schqty", "scheme", "scheme qty", "bonus", "schm",
     "free goods", "deal")
_add("rate", "rate", "rate.", "purchase rate", "pur rate", "pur. rate", "p.rate",
     "prate", "unit rate", "rate/unit", "rate per unit", "price", "unit price",
     "net rate", "our rate", "billing rate", "trade price", "tp")
_add("ptr", "ptr")
_add("pts", "pts")
_add("discount_pct", "dis%", "disc%", "disc %", "dis %", "discount%",
     "discount %", "d%", "cd%", "td%", "disc pct", "discount percent", "disc.%",
     "dis.%", "sch%", "spl disc%")
_add("disc", "dis", "dis.", "disc", "disc.", "discount")
_add("disc_rupees", "disc amt", "dis amt", "discount amt", "discount amount",
     "disc amount", "disc rs", "dis rs", "disc.amt", "disc value")
_add("gst_pct", "gst", "gst%", "gst %", "gst(%)", "gst rate", "tax", "tax%",
     "tax %", "tax rate", "igst", "igst%", "igst %", "gst.%", "vat%")
_add("cgst_pct", "cgst", "cgst%", "cgst %", "c.gst%", "cgst rate")
_add("sgst_pct", "sgst", "sgst%", "sgst %", "s.gst%", "sgst rate", "utgst%")
_add("cgst_amt", "cgst amt", "cgst rs", "cgst rs.", "cgst amount", "cgst value")
_add("sgst_amt", "sgst amt", "sgst rs", "sgst rs.", "sgst amount", "sgst value")
_add("gst_amt", "gst amt", "gst amount", "tax amt", "tax amount", "gst value",
     "igst amt", "igst amount", "gst rs")
_add("amount", "amount", "amount.", "amt", "amt.", "value", "net amt", "net amount",
     "netamt", "net value", "total", "line total", "net total", "net", "total amt",
     "total amount", "amount rs", "net amt.")
_add("taxable", "taxable", "taxable value", "taxable amt", "taxable amount",
     "gross amt", "gross amount", "goods value", "gross")
_add("rate_a", "rate a", "ratea", "rate-a")
_add("rate_b", "rate b", "rateb", "rate-b")
_add("rate_c", "rate c", "ratec", "rate-c")
_add("ignore", "loc", "location", "rack", "shelf", "remark", "remarks", "barcode",
     "item code", "code", "product code", "old mrp")


def header_field(cell: Any) -> str:
    """The field a header cell names ("" when it names none we know)."""
    raw = clean(cell)
    if not raw or len(raw) > 40:
        return ""
    key = _hkey(raw)
    if not key:
        return ""
    hit = _HEADER_WORDS.get(key)
    if hit:
        return hit
    # "Rate (Rs.)", "Qty (Nos)", "Batch No/Lot" -- try the words before a bracket / slash.
    head = re.split(r"[(\[/]", raw)[0]
    if head and head != raw:
        hit = _HEADER_WORDS.get(_hkey(head))
        if hit:
            return hit
    return ""


def _collapse_spaced_letters(line: str) -> str:
    """'D e s c r i p t i o n' -> 'Description' (letter-spaced header words)."""
    return re.sub(
        r"(?<![A-Za-z])((?:[A-Za-z] ){3,}[A-Za-z])(?![A-Za-z])",
        lambda m: m.group(1).replace(" ", ""),
        line,
    )


def text_header_fields(line: str) -> List[str]:
    """Fields named by a text header line, in order ([] when it is no header).

    Header words can be one to three tokens ("Name of Product", "Batch No",
    "Sch Qty"): the longest phrase that names a field wins.
    """
    s = _collapse_spaced_letters(clean(line))
    s = re.sub(r"\s+%", "%", s)
    tokens = s.split()
    if len(tokens) < 4 or len(tokens) > 40:
        return []
    fields: List[str] = []
    spans: List[Tuple[int, int]] = []
    i = 0
    unknown = 0
    while i < len(tokens):
        matched = False
        for span in (4, 3, 2, 1):
            if i + span > len(tokens):
                continue
            phrase = " ".join(tokens[i:i + span])
            f = header_field(phrase)
            if f:
                fields.append(f)
                spans.append((i, span))
                i += span
                matched = True
                break
        if not matched:
            unknown += 1
            fields.append("ignore" if len(tokens[i]) > 1 else "")
            spans.append((i, 1))
            i += 1
    # "... SGST CGST Amount": the longest phrase read the last two headings as
    # one ("CGST Amount") and left the bill without its Amount column.
    if fields and "amount" not in fields and spans[-1][1] > 1:
        start, span = spans[-1]
        last = header_field(tokens[start + span - 1])
        first = header_field(" ".join(tokens[start:start + span - 1]))
        if last == "amount" and first:
            fields[-1:] = [first, "amount"]
    fields = [f for f in fields if f != ""]
    known = [f for f in fields if f not in ("ignore", "")]
    if "name" not in known:
        return []
    if not ({"qty", "amount"} & set(known)):
        return []
    if len(set(known)) < 5 or unknown > len(known) // 2:
        return []
    return fields


def find_header_row(rows: Sequence[Sequence[Any]], limit: int = 60) -> Tuple[int, Dict[int, str]]:
    """Best header row in table rows -> (row index, {column index: field})."""
    best = (-1, {}, 0)
    for idx, row in enumerate(list(rows)[:limit]):
        colmap: Dict[int, str] = {}
        seen = set()
        for ci, cell in enumerate(row or []):
            f = header_field(cell)
            if not f or f == "ignore":
                continue
            if f in seen and f not in ("disc",):
                continue
            seen.add(f)
            colmap[ci] = f
        fields = set(colmap.values())
        if "name" not in fields:
            continue
        score = len(fields)
        score += 3 if {"qty", "amount"} & fields else 0
        score += 2 if "batch" in fields else 0
        if score > best[2]:
            best = (idx, colmap, score)
    if best[0] < 0 or best[2] < 7:
        return -1, {}
    return best[0], best[1]


# ---------------------------------------------------------------------------
# Line arithmetic
# ---------------------------------------------------------------------------

def _tol(qty: float, amount: float) -> float:
    return max(0.06, abs(qty) * 0.006, abs(amount) * 0.0015)


def amount_mode(
    qty: float,
    rate: float,
    amount: float,
    disc_pct: float = 0.0,
    disc_rs: float = 0.0,
    gst: float = 0.0,
) -> str:
    """How qty x rate makes the line amount on this bill ('' when it does not)."""
    if qty <= 0 or rate <= 0 or amount <= 0:
        return ""
    tol = _tol(qty, amount)
    base = qty * rate
    tries = [("plain", base)]
    if disc_pct > 0:
        tries.append(("disc_pct", base * (1 - disc_pct / 100.0)))
    if disc_rs > 0:
        tries.append(("disc_rs", base - disc_rs))
    if gst > 0:
        tries.append(("with_gst", base * (1 + gst / 100.0)))
        if disc_pct > 0:
            tries.append(("disc_pct_gst", base * (1 - disc_pct / 100.0) * (1 + gst / 100.0)))
        if disc_rs > 0:
            tries.append(("disc_rs_gst", (base - disc_rs) * (1 + gst / 100.0)))
    for label, expect in tries:
        if abs(expect - amount) <= tol:
            return label
    return ""


def _rec_amount_mode(rec: Dict[str, Any]) -> str:
    disc = num(rec.get("discount_pct"))
    disc_rs = num(rec.get("disc_rupees"))
    if rec.get("disc") not in (None, "") and not disc and not disc_rs:
        d = num(rec.get("disc"))
        disc, disc_rs = d, d
    gst = num(rec.get("gst_pct"))
    if not gst:
        gst = num(rec.get("cgst_pct")) + num(rec.get("sgst_pct"))
    return amount_mode(
        num(rec.get("qty")), num(rec.get("rate")), num(rec.get("amount")),
        disc, disc_rs, gst,
    )


# ---------------------------------------------------------------------------
# PDF text lines
# ---------------------------------------------------------------------------

def _token_fits(field: str, tok: str) -> bool:
    if field == "sr":
        return bool(re.fullmatch(r"\d{1,4}[.)]?", tok))
    if field == "hsn_code":
        return is_hsn(tok)
    if field in NUMERIC_FIELDS:
        return to_num(tok) is not None or tok in ("-", "--")
    if field in ("expiry", "mfg_date"):
        return is_expiry(tok) or is_missing(tok)
    if field == "batch":
        return is_batch(tok) or is_missing(tok) or bool(
            re.fullmatch(r"[A-Z][A-Z0-9\-/.]{1,19}", tok.upper())
        )
    return True


def _assign_numeric_block(
    fields: List[str], tokens: List[str], partial: Dict[str, Any]
) -> Optional[Dict[str, str]]:
    """Put a run of numeric tokens into a run of numeric columns.

    When the line has fewer numbers than the header has numeric columns, one or
    more optional columns were left blank; the bill's own arithmetic says which.
    """
    n, m = len(tokens), len(fields)
    if n == m:
        return dict(zip(fields, tokens))
    if n > m or n == 0:
        return None
    optional_idx = [i for i, f in enumerate(fields) if f in OPTIONAL_FIELDS]
    drop = m - n
    if len(optional_idx) < drop:
        return None
    best: Optional[Dict[str, str]] = None
    best_score = -1
    for gone in combinations(optional_idx, drop):
        kept = [f for i, f in enumerate(fields) if i not in gone]
        cand = dict(zip(kept, tokens))
        rec = dict(partial)
        rec.update(cand)
        score = 0
        if _rec_amount_mode(rec):
            score += 4
        if "gst_pct" in cand and is_gst_rate(cand["gst_pct"]):
            score += 1
        if num(rec.get("qty")) > 0:
            score += 1
        if score > best_score:
            best, best_score = cand, score
    return best


def _split_middle(tokens: List[str], soft_fields: List[str]) -> Tuple[List[str], Dict[str, str]]:
    """Split 'name [pack] [maker]' (in the header's order) using value shapes."""
    toks = list(tokens)
    out: Dict[str, str] = {}
    fields = list(soft_fields)
    pack_done = False
    for pos in range(len(fields) - 1, -1, -1):
        f = fields[pos]
        if len(toks) <= 1:
            break
        left = fields[pos - 1] if pos > 0 else "name"
        rightmost = pos == len(fields) - 1
        if f == "manufacturer":
            last = toks[-1]
            jam = _PACK_MFG_JAMMED_RE.match(last) if left == "pack" else None
            if jam:
                out["pack"], out["manufacturer"] = jam.group(1), jam.group(2)
                toks.pop()
                pack_done = True
                continue
            if left == "pack":
                cut = None
                for p in range(len(toks) - 2, 0, -1):
                    if is_pack(toks[p]) and 1 <= len(toks) - p - 1 <= 3:
                        cut = p
                        break
                if cut is not None:
                    out["manufacturer"] = " ".join(toks[cut + 1:])
                    toks = toks[:cut + 1]
                    continue
            if is_pack(last) and left == "pack":
                continue  # maker column blank; the pack is last
            mfg = [toks.pop()]
            while len(toks) > 1 and re.fullmatch(r"[A-Za-z&.]{1,2}", toks[-1]) and \
                    len(" ".join([toks[-1]] + mfg)) <= 8 and not is_pack(toks[-1]):
                mfg.insert(0, toks.pop())
            out["manufacturer"] = " ".join(mfg)
        elif f == "pack":
            if pack_done:
                continue
            last = toks[-1]
            if is_pack(last):
                out["pack"] = toks.pop()
            elif (
                len(toks) > 2 and re.fullmatch(r"[A-Za-z]", last)
                and re.fullmatch(r"\d{1,3}", toks[-2])
            ):
                out["pack"] = "{} {}".format(toks[-2], last)
                toks = toks[:-2]
            elif rightmost and len(toks) > 1 and is_pack(last, allow_plain=True):
                out["pack"] = toks.pop()
            elif last.upper() in _FORM_WORDS and last.upper() in (t.upper() for t in toks[:-1]):
                out["pack"] = toks.pop()
        else:
            if _token_fits(f, toks[-1]) and (f not in ("batch",) or is_batch(toks[-1])):
                out[f] = toks.pop()
    return toks, out


def parse_text_line(line: str, fields: Sequence[str]) -> Optional[Dict[str, Any]]:
    """One printed item line -> record, read with the header's column order."""
    fields = list(fields)
    if "name" not in fields:
        return None
    toks = clean(line).split()
    if len(toks) < 4:
        return None
    k = fields.index("name")
    head, tail = fields[:k], fields[k + 1:]
    rec: Dict[str, Any] = {}
    i = 0
    for f in head:
        if i >= len(toks) - 1:
            return None
        if f in TEXT_FIELDS:  # a maker code before the name: one token
            rec[f] = toks[i]
            i += 1
            continue
        if _token_fits(f, toks[i]):
            rec[f] = toks[i]
            i += 1
        elif f in OPTIONAL_FIELDS:
            continue
        else:
            return None

    j = len(toks)
    soft: List[str] = []
    rev = list(reversed(tail))
    idx = 0
    while idx < len(rev):
        f = rev[idx]
        if f in TEXT_FIELDS:
            soft = list(reversed(rev[idx:]))
            break
        if f in NUMERIC_FIELDS:
            block = []
            while idx < len(rev) and rev[idx] in NUMERIC_FIELDS:
                block.append(rev[idx])
                idx += 1
            block = list(reversed(block))
            run: List[str] = []
            while j - len(run) > i + 1 and len(run) < len(block):
                t = toks[j - len(run) - 1]
                if to_num(t) is None and t not in ("-", "--"):
                    break
                run.insert(0, t)
            assigned = _assign_numeric_block(block, run, rec)
            if assigned is None:
                return None
            rec.update(assigned)
            j -= len(run)
            continue
        if j > i + 1 and _token_fits(f, toks[j - 1]):
            rec[f] = toks[j - 1]
            j -= 1
        elif f not in OPTIONAL_FIELDS and f not in ("batch",):
            return None
        idx += 1
    middle = toks[i:j]
    if not middle:
        return None
    name_toks, extra = _split_middle(middle, soft)
    rec.update(extra)
    rec["name"] = " ".join(name_toks)
    for f in ("expiry", "batch"):
        if f in rec and is_missing(rec[f]):
            rec[f] = ""
    for f in list(rec):
        if f in NUMERIC_FIELDS and rec[f] in ("-", "--"):
            rec[f] = "0"
    if not has_letters(rec["name"]):
        return None
    if num(rec.get("qty")) <= 0 and num(rec.get("free_qty")) <= 0:
        return None
    if num(rec.get("rate")) <= 0 and num(rec.get("amount")) <= 0:
        return None
    if not (rec.get("batch") or rec.get("expiry")):
        return None
    return rec


def _resolve_disc_value(rec: Dict[str, Any]) -> None:
    """A plain 'Dis' / 'Disc' cell: percent or rupees, by the line's arithmetic."""
    if "disc" not in rec:
        return
    raw = rec.pop("disc")
    d = num(raw)
    if d <= 0 or clean(rec.get("discount_pct")) or clean(rec.get("disc_rupees")):
        return
    q, r, a = num(rec.get("qty")), num(rec.get("rate")), num(rec.get("amount"))
    if "%" in clean(raw) or amount_mode(q, r, a, disc_pct=d) in ("disc_pct", "disc_pct_gst"):
        rec["discount_pct"] = raw
    elif amount_mode(q, r, a, disc_rs=d) in ("disc_rs", "disc_rs_gst"):
        rec["disc_rupees"] = raw
    else:
        rec["disc_rupees"] = raw  # rate already net: shown, never applied twice


_FOOTER_WORDS = re.compile(
    r"\b(SUB\s*TOTAL|GRAND\s*TOTAL|NET\s*(?:AMT|AMOUNT|PAYABLE)|ROUND\s*OFF|"
    r"TAXABLE|CGST|SGST|IGST|DECLARATION|BANK|IFSC|RUPEES|E\.\s*&\s*O\.E)\b",
    re.IGNORECASE,
)


def records_from_text(raw_text: str) -> List[Dict[str, Any]]:
    """Item records from PDF text lines, read under the bill's own header line(s)."""
    records: List[Dict[str, Any]] = []
    fields: List[str] = []
    for row_no, raw_line in enumerate((raw_text or "").splitlines(), start=1):
        line = clean(raw_line)
        if not line:
            continue
        hdr = text_header_fields(line)
        if hdr:
            fields = hdr
            continue
        if not fields:
            continue
        if _FOOTER_WORDS.search(line) and not re.search(r"\d+\s*/\s*\d{2}\b", line):
            continue
        rec = parse_text_line(line, fields)
        if rec:
            _resolve_disc_value(rec)
            rec["source_row"] = row_no
            rec["_columns"] = "text-header"
            records.append(rec)
    return records


# ---------------------------------------------------------------------------
# Table rows (CSV / Excel / PDF tables)
# ---------------------------------------------------------------------------

def _fit(field: str, value: Any) -> Optional[bool]:
    """Does a cell look like this field? None when the cell is empty."""
    s = clean(value)
    if s == "" or is_missing(s):
        return None
    if field == "batch":
        return is_batch(s)
    if field == "expiry":
        return is_expiry(value, allow_compact=True)
    if field == "manufacturer":
        return is_mfg(s)
    if field == "pack":
        return is_pack(s, allow_plain=True) or (len(s) <= 12 and not is_expiry(s) and not is_batch(s))
    if field == "hsn_code":
        return is_hsn(re.sub(r"\D", "", s)) and len(re.sub(r"[\d\s.]", "", s)) == 0
    if field == "name":
        return has_letters(s) and len(s) >= 3
    if field == "gst_pct":
        return is_gst_rate(s)
    if field in NUMERIC_FIELDS:
        return to_num(s) is not None
    return True


def _fit_ratio(rows: Sequence[Sequence[Any]], col: int, field: str) -> float:
    hits = total = 0
    for row in rows:
        if col >= len(row):
            continue
        ok = _fit(field, row[col])
        if ok is None:
            continue
        total += 1
        hits += 1 if ok else 0
    return hits / total if total else 0.0


_TEXT_SWAP_FIELDS = ("batch", "manufacturer", "expiry", "pack", "hsn_code")


def _fix_text_columns(rows: Sequence[Sequence[Any]], colmap: Dict[int, str], notes: List[str]) -> Dict[int, str]:
    """Swap text columns whose values plainly belong to another mapped column."""
    cols = [c for c, f in colmap.items() if f in _TEXT_SWAP_FIELDS]
    if len(cols) < 2:
        return colmap
    current = sum(_fit_ratio(rows, c, colmap[c]) for c in cols)
    best_map, best_score = None, current
    fields = [colmap[c] for c in cols]
    for perm in permutations(fields):
        if list(perm) == fields:
            continue
        changed = sum(1 for a, b in zip(fields, perm) if a != b)
        score = sum(_fit_ratio(rows, c, f) for c, f in zip(cols, perm))
        # Each moved column must gain clearly: header words are usually right.
        if score > best_score + 0.4 * changed and score > current + 0.4 * changed:
            best_map, best_score = perm, score
    if best_map:
        fixed = dict(colmap)
        for c, f in zip(cols, best_map):
            if fixed[c] != f:
                notes.append("column {} holds {} values, not {}".format(c + 1, f, colmap[c]))
            fixed[c] = f
        return fixed
    return colmap


_MATH_FIELDS = ("qty", "free_qty", "rate", "mrp", "amount")


def _math_score(rows: Sequence[Sequence[Any]], colmap: Dict[int, str]) -> int:
    by_field = {f: c for c, f in colmap.items()}
    score = 0
    for row in rows:
        rec = {f: (row[c] if c < len(row) else "") for f, c in by_field.items()}
        if _rec_amount_mode(rec):
            score += 2
        mrp, rate = num(rec.get("mrp")), num(rec.get("rate"))
        if mrp and rate and mrp + 0.01 >= rate:
            score += 1
    return score


def _fix_number_columns(rows: Sequence[Sequence[Any]], colmap: Dict[int, str], notes: List[str]) -> Dict[int, str]:
    """Swap qty / free / rate / MRP / amount columns when the arithmetic says so."""
    cols = [c for c, f in colmap.items() if f in _MATH_FIELDS]
    if len(cols) < 3:
        return colmap
    base = _math_score(rows, colmap)
    best, best_score = None, base
    fields = [colmap[c] for c in cols]
    for perm in permutations(fields):
        if list(perm) == fields:
            continue
        trial = dict(colmap)
        for c, f in zip(cols, perm):
            trial[c] = f
        s = _math_score(rows, trial)
        if s > best_score:
            best, best_score = trial, s
    if best and best_score >= base + max(2, len(rows) // 2):
        for c in cols:
            if best[c] != colmap[c]:
                notes.append("column {} holds {} values, not {}".format(c + 1, best[c], colmap[c]))
        return best
    return colmap


def _resolve_disc_columns(rows: Sequence[Sequence[Any]], colmap: Dict[int, str]) -> Dict[int, str]:
    """A plain 'Disc' column: percent or rupees, by the arithmetic."""
    out = dict(colmap)
    for c, f in colmap.items():
        if f != "disc":
            continue
        pct_hits = rs_hits = 0
        by_field = {ff: cc for cc, ff in colmap.items()}
        for row in rows:
            rec = {ff: (row[cc] if cc < len(row) else "") for ff, cc in by_field.items()}
            d = num(rec.get("disc"))
            if d <= 0:
                continue
            q, r, a = num(rec.get("qty")), num(rec.get("rate")), num(rec.get("amount"))
            if amount_mode(q, r, a, disc_pct=d) == "disc_pct":
                pct_hits += 1
            if amount_mode(q, r, a, disc_rs=d) == "disc_rs":
                rs_hits += 1
        out[c] = "disc_rupees" if rs_hits > pct_hits else "discount_pct"
    return out


def infer_columns_by_shape(rows: Sequence[Sequence[Any]]) -> Dict[int, str]:
    """No header row: name each column from what its cells look like."""
    data = [r for r in rows if sum(1 for c in r if clean(c)) >= 6]
    if len(data) < 1:
        return {}
    ncol = max(len(r) for r in data)
    colmap: Dict[int, str] = {}
    used = set()

    def ratio(col: int, fn) -> float:
        vals = [clean(r[col]) for r in data if col < len(r) and clean(r[col])]
        if not vals:
            return 0.0
        return sum(1 for v in vals if fn(v)) / len(vals)

    def avg_len(col: int) -> float:
        vals = [clean(r[col]) for r in data if col < len(r) and clean(r[col])]
        return sum(len(v) for v in vals) / len(vals) if vals else 0.0

    def take(field: str, fn, threshold: float, prefer=None) -> None:
        cands = [(ratio(c, fn), c) for c in range(ncol) if c not in used]
        cands = [x for x in cands if x[0] >= threshold]
        if not cands:
            return
        if prefer:
            cands.sort(key=lambda x: (x[0], prefer(x[1])), reverse=True)
        else:
            cands.sort(reverse=True)
        colmap[cands[0][1]] = field
        used.add(cands[0][1])

    take("expiry", lambda v: is_expiry(v, allow_compact=True), 0.8)
    take("hsn_code", is_hsn, 0.8)
    take("name", lambda v: has_letters(v) and len(v) >= 4 and to_num(v) is None, 0.8, prefer=avg_len)
    take("batch", lambda v: is_batch(v) and has_letters(v), 0.6)
    if "batch" not in colmap.values():
        take("batch", is_batch, 0.8)
    take("pack", lambda v: is_pack(v), 0.7)
    take("manufacturer", lambda v: is_mfg(v) and len(v) <= 25, 0.8, prefer=lambda c: -avg_len(c))
    num_cols = [c for c in range(ncol) if c not in used and ratio(c, lambda v: to_num(v) is not None) >= 0.9]
    best, best_hits = None, 0
    for q, r, a in permutations(num_cols, 3):
        hits = sum(
            1 for row in data
            if amount_mode(num(row[q] if q < len(row) else 0), num(row[r] if r < len(row) else 0),
                           num(row[a] if a < len(row) else 0))
        )
        if hits > best_hits or (hits == best_hits and best and a > best[2]):
            best, best_hits = (q, r, a), hits
    if best and best_hits >= max(1, len(data) // 2):
        q, r, a = best
        colmap[q], colmap[r], colmap[a] = "qty", "rate", "amount"
        used.update(best)
        rest = [c for c in num_cols if c not in used]
        gst = [c for c in rest if ratio(c, is_gst_rate) >= 0.9 and ratio(c, lambda v: num(v) > 0) > 0]
        if gst:
            colmap[gst[-1]] = "gst_pct"
            used.add(gst[-1])
            rest.remove(gst[-1])
        mrp = [c for c in rest if all(
            num(row[c]) + 0.01 >= num(row[r]) for row in data if c < len(row) and r < len(row) and clean(row[c])
        ) and ratio(c, lambda v: num(v) > 0) >= 0.8]
        if mrp:
            colmap[mrp[0]] = "mrp"
            used.add(mrp[0])
            rest.remove(mrp[0])
        free = [c for c in rest if ratio(c, lambda v: float(num(v)).is_integer() or num(v) * 2 == int(num(v) * 2)) >= 0.9]
        if free:
            colmap[free[0]] = "free_qty"
    return colmap


def _explode_stacked(rows: Sequence[Sequence[Any]]) -> List[List[Any]]:
    """A PDF table that put a whole page of lines in one cell per column ('81.00\\n352.00'):
    one row per line, when every filled cell has the same number of lines."""
    out: List[List[Any]] = []
    for row in rows:
        cells = list(row or [])
        parts = [str(c).split("\n") if isinstance(c, str) else [c] for c in cells]
        counts = {len(p) for p, c in zip(parts, cells) if clean(c)}
        if len(counts) == 1 and next(iter(counts)) >= 2 and sum(1 for c in cells if clean(c)) >= 4:
            n = next(iter(counts))
            for k in range(n):
                out.append([p[k] if clean(c) else "" for p, c in zip(parts, cells)])
        else:
            out.append(cells)
    return out


def records_from_rows(rows: Sequence[Sequence[Any]]) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Item records from table rows: header words first, value shapes to check them."""
    rows = _explode_stacked([list(r or []) for r in rows])
    info: Dict[str, Any] = {"header_row": -1, "columns": {}, "notes": []}
    hdr_idx, colmap = find_header_row(rows)
    if hdr_idx >= 0:
        data_rows = rows[hdr_idx + 1:]
        info["source"] = "header"
    else:
        data_rows = rows
        colmap = infer_columns_by_shape(rows)
        info["source"] = "shape"
        if not colmap or "name" not in colmap.values():
            return [], info
    item_rows = [
        r for r in data_rows
        if sum(1 for c in r if clean(c)) >= 4 and not _looks_like_total_row(r)
        and not (find_header_row([r])[0] == 0)
    ]
    notes: List[str] = info["notes"]
    colmap = _resolve_disc_columns(item_rows, colmap)
    if len(item_rows) >= 2:  # one row is too little to overrule a header
        colmap = _fix_text_columns(item_rows, colmap, notes)
        colmap = _fix_number_columns(item_rows, colmap, notes)
    info["header_row"] = hdr_idx
    info["columns"] = dict(colmap)
    by_field = {f: c for c, f in colmap.items() if f not in ("ignore", "sr")}
    records: List[Dict[str, Any]] = []
    start = hdr_idx + 2 if hdr_idx >= 0 else 1
    for off, row in enumerate(data_rows):
        if row not in item_rows:
            continue
        rec: Dict[str, Any] = {"source_row": start + off, "_columns": info["source"]}
        for f, c in by_field.items():
            rec[f] = row[c] if c < len(row) else ""
        if rec.get("ptr") not in (None, "") and not clean(rec.get("rate")):
            rec["rate"] = rec["ptr"]
        name = clean(rec.get("name"))
        if not has_letters(name) or _is_total_name(name):
            continue
        if not any(clean(rec.get(k)) for k in ("batch", "qty", "rate", "amount")):
            continue
        records.append(rec)
    return records, info


def _is_total_name(name: str) -> bool:
    low = clean(name).lower()
    return bool(re.match(r"^(sub\s*|grand\s*|net\s*)?total\b", low)) or low in ("total", "totals")


def _looks_like_total_row(row: Sequence[Any]) -> bool:
    joined = " ".join(clean(c) for c in row).lower()
    if re.search(r"\b(grand total|sub total|subtotal|net payable|round off|amount in words)\b", joined):
        return True
    texts = [clean(c) for c in row if clean(c) and to_num(c) is None]
    return len(texts) == 1 and _is_total_name(texts[0])


# ---------------------------------------------------------------------------
# Checking one record (any source: text, table, EDI CSV, AI photo reader)
# ---------------------------------------------------------------------------

def _flag(rec: Dict[str, Any], field: str, why: str) -> None:
    fields = rec.setdefault("check_fields", [])
    if field not in fields:
        fields.append(field)
    notes = rec.setdefault("check_notes", {})
    notes.setdefault(field, why)


def check_record(
    rec: Dict[str, Any],
    *,
    bill_makers: Iterable[str] = (),
    strip_maker_from_name: bool = False,
) -> Dict[str, Any]:
    """Fix plainly swapped cells and mark the ones that cannot be proven.

    Adds ``check_fields`` (list of field names the shop should look at) and
    ``check_notes`` ({field: why}); ``fixed_fields`` lists what was swapped.

    ``strip_maker_from_name`` is for photo readers, which glue the MFG column
    onto the product name ("ABCDE SIGMA SET"). A printed text line never does --
    there a name may well end in a word that is also a maker code.
    """
    fixed: List[str] = rec.setdefault("fixed_fields", [])

    def swap(a: str, b: str) -> None:
        rec[a], rec[b] = rec.get(b, ""), rec.get(a, "")
        fixed.append("{}<->{}".format(a, b))

    batch, mfg = clean(rec.get("batch")), clean(rec.get("manufacturer"))
    expiry, pack = clean(rec.get("expiry")), clean(rec.get("pack"))

    # Batch and maker in each other's place.
    if batch and mfg and not is_batch(batch) and is_mfg(batch) and is_batch(mfg) and not is_mfg(mfg):
        swap("batch", "manufacturer")
    elif not batch and mfg and is_batch(mfg) and not is_mfg(mfg) and not is_pack(mfg):
        swap("batch", "manufacturer")
    batch, mfg = clean(rec.get("batch")), clean(rec.get("manufacturer"))
    # Batch and expiry in each other's place.
    if batch and expiry and is_expiry(batch) and not is_expiry(expiry, allow_compact=True) and is_batch(expiry):
        swap("batch", "expiry")
    elif not expiry and batch and is_expiry(batch):
        swap("batch", "expiry")
    # A pack in the maker column (maker blank).
    mfg = clean(rec.get("manufacturer"))
    if mfg and not pack and is_pack(mfg) and not is_mfg(mfg):
        swap("pack", "manufacturer")

    # The maker code also printed at the start / end of the name (photo readers do this).
    name = clean(rec.get("name"))
    mfg = clean(rec.get("manufacturer"))
    makers = {_mkey(m) for m in list(bill_makers) + [mfg] if _mkey(m)}
    if strip_maker_from_name and name and makers:
        toks = name.split()
        if len(toks) > 1 and _mkey(toks[0]) in makers:
            if not mfg:
                rec["manufacturer"] = toks[0]
            elif _mkey(toks[0]) != _mkey(mfg):
                rec["manufacturer"] = toks[0]
                _flag(rec, "manufacturer", "maker was printed with the name")
            rec["name"] = " ".join(toks[1:])
            fixed.append("maker-out-of-name")

    # GST and discount columns swapped (Disc% 5 / GST% 7.5 is no GST rate at all).
    g_raw, d_raw = rec.get("gst_pct"), rec.get("discount_pct")
    if clean(g_raw) and clean(d_raw):
        g, d = num(g_raw), num(d_raw)
        g_known = is_gst_rate(g) or any(abs(g - h) < 0.01 for h in GST_HALF_RATES)
        if not g_known and d > 0 and is_gst_rate(d) and g < 100:
            swap("gst_pct", "discount_pct")

    # The arithmetic: qty x rate (less discount) = amount.
    qty, rate, amount = num(rec.get("qty")), num(rec.get("rate")), num(rec.get("amount"))
    mrp, free = num(rec.get("mrp")), num(rec.get("free_qty"))
    if qty > 0 and rate > 0 and amount > 0 and not _rec_amount_mode(rec):
        fixed_math = False
        for a, b in (("rate", "mrp"), ("qty", "free_qty"), ("qty", "rate")):
            if not clean(rec.get(b)):
                continue
            trial = dict(rec)
            trial[a], trial[b] = rec.get(b), rec.get(a)
            if _rec_amount_mode(trial) and num(trial.get("qty")) > 0:
                if a == "rate" and num(trial.get("mrp")) and num(trial.get("mrp")) + 0.01 < num(trial.get("rate")):
                    continue
                swap(a, b)
                fixed_math = True
                break
        if not fixed_math:
            # A rate printed per strip while qty counts tablets (or the other way)
            # is for the shop to see; never guess it.
            for f in ("qty", "rate", "amount"):
                _flag(rec, f, "qty x rate does not make the amount")
    elif qty > 0 and rate <= 0 and amount <= 0:
        _flag(rec, "rate", "no rate or amount")

    mrp, rate = num(rec.get("mrp")), num(rec.get("rate"))
    if mrp > 0 and rate > 0 and mrp + 0.01 < rate:
        _flag(rec, "mrp", "MRP is below the purchase rate")

    # Shapes.
    batch = clean(rec.get("batch"))
    if batch and not is_missing(batch) and not is_batch(batch):
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9\-/.]{1,19}", batch):
            _flag(rec, "batch", "does not look like a batch number")
    expiry = rec.get("expiry")
    if clean(expiry) and not is_missing(expiry) and not is_expiry(expiry, allow_compact=True):
        _flag(rec, "expiry", "does not look like an expiry")
    hsn = clean(rec.get("hsn_code"))
    if hsn and not re.fullmatch(r"\d{4,8}", re.sub(r"\s", "", hsn)):
        _flag(rec, "hsn_code", "HSN should be 4-8 digits")
    g = rec.get("gst_pct")
    if clean(g) and not is_gst_rate(g) and not (
        clean(rec.get("cgst_pct")) or clean(rec.get("sgst_pct"))
    ):
        gv = num(g)
        if not any(abs(gv - h) < 0.01 for h in GST_HALF_RATES):
            _flag(rec, "gst_pct", "not a GST rate")
    mfg = clean(rec.get("manufacturer"))
    if mfg and re.search(r"\d", mfg):
        _flag(rec, "manufacturer", "a maker has no digits")
    if not fixed:
        rec.pop("fixed_fields", None)
    return rec


def _mkey(value: Any) -> str:
    return re.sub(r"[^A-Z0-9&]", "", clean(value).upper())


def check_records(
    records: List[Dict[str, Any]], *, strip_maker_from_name: bool = False
) -> List[Dict[str, Any]]:
    makers = [clean(r.get("manufacturer")) for r in records if clean(r.get("manufacturer"))]
    short = [m for m in makers if len(_mkey(m)) <= 6]
    for rec in records:
        check_record(rec, bill_makers=short, strip_maker_from_name=strip_maker_from_name)
    return records


# ---------------------------------------------------------------------------
# A photo reader's row, read twice
# ---------------------------------------------------------------------------

_CROSS_FIELDS = (
    "batch", "expiry", "qty", "free_qty", "rate", "mrp", "amount", "hsn_code",
    "manufacturer", "gst_pct",
)


def columns_to_fields(columns: Sequence[Any]) -> List[str]:
    """A photo reader's list of printed column headings -> field names."""
    out: List[str] = []
    for col in columns or []:
        out.append(header_field(col) or "ignore")
    return out


def _same(field: str, a: Any, b: Any) -> bool:
    if field in NUMERIC_FIELDS:
        x, y = to_num(a), to_num(b)
        if x is None or y is None:
            return clean(a) == clean(b)
        return abs(x - y) < 0.011
    if field == "expiry":
        from core.purchase_importer import normalize_expiry  # local: avoids a cycle

        return normalize_expiry(a) == normalize_expiry(b)
    return _mkey(a) == _mkey(b)


def cross_check_row(rec: Dict[str, Any], row_text: str, columns: Sequence[Any]) -> Dict[str, Any]:
    """Read the reader's own copy of the printed row under the printed headings and
    compare cell by cell with the fields it returned.

    A field the two readings disagree on is taken from the row text when only that
    reading makes qty x rate = amount; otherwise it is marked for the shop to check.
    Nothing is changed when the row text cannot be read under the headings.
    """
    fields = columns_to_fields(columns)
    if "name" not in fields or not clean(row_text):
        return rec
    parsed = parse_text_line(row_text, fields)
    if not parsed:
        return rec
    # The maker code glued onto the name: the row text has it in the MFG column
    # and the rest of the name is the same, so both readings agree on where it sits.
    p_mfg, p_name = clean(parsed.get("manufacturer")), clean(parsed.get("name"))
    r_name = clean(rec.get("name"))
    maker_before_name = (
        "manufacturer" in fields and fields.index("manufacturer") < fields.index("name")
    )
    r_mfg = _mkey(rec.get("manufacturer"))
    if (
        maker_before_name and p_mfg and p_name and r_name
        and _mkey(r_name).startswith(_mkey(p_mfg) + _mkey(p_name))
        # the structured reading has no maker, or a cut-short copy of this one
        # ("ABC" for ABCDE); a different maker means the row text lost a cell
        and (not r_mfg or _mkey(p_mfg).startswith(r_mfg))
    ):
        toks = r_name.split()
        if toks and _mkey(toks[0]) == _mkey(p_mfg):
            rec["name"] = " ".join(toks[1:])
            rec["manufacturer"] = p_mfg
            rec.setdefault("fixed_fields", []).append("maker-out-of-name")
    return reconcile_readings(rec, parsed, "row-text")


def reconcile_readings(rec: Dict[str, Any], other: Dict[str, Any], label: str) -> Dict[str, Any]:
    """Two readings of one bill line. Where they disagree, the other reading is
    taken only when it alone makes qty x rate = amount; otherwise the cell is
    marked for the shop."""
    rec_ok = bool(_rec_amount_mode(rec))
    trial = dict(rec)
    differs: List[str] = []
    for f in _CROSS_FIELDS:
        a, b = rec.get(f), other.get(f)
        if clean(b) == "" or clean(a) == "":
            continue
        if not _same(f, a, b):
            differs.append(f)
            trial[f] = b
    if not differs:
        return rec
    trial_ok = bool(_rec_amount_mode(trial))
    if trial_ok and not rec_ok:
        for f in differs:
            rec[f] = trial[f]
        rec.setdefault("fixed_fields", []).append(label + ":" + ",".join(differs))
        return rec
    for f in differs:
        _flag(rec, f, "the photo reads {} here, or {}".format(clean(rec.get(f)), clean(other.get(f))))
    return rec


def column_values_for_row(column_values: Any, index: int, count: int) -> Dict[str, Any]:
    """The index-th value of each column the photo reader read top to bottom on
    its own (only columns with one value per item row)."""
    out: Dict[str, Any] = {}
    if not isinstance(column_values, dict):
        return out
    for heading, values in column_values.items():
        f = header_field(heading)
        if not f or f == "ignore" or not isinstance(values, list) or len(values) != count:
            continue
        if 0 <= index < len(values):
            out[f] = values[index]
    return out


# ---------------------------------------------------------------------------
# Scoring: which reading of a bill is the right one
# ---------------------------------------------------------------------------

def _get(rec: Any, field: str) -> Any:
    if isinstance(rec, dict):
        return rec.get(field)
    return getattr(rec, field, None)


def record_quality(rec: Any) -> int:
    """Points for one line: the arithmetic holds and each value has its shape."""
    score = 0
    q = {f: _get(rec, f) for f in (
        "qty", "rate", "amount", "discount_pct", "gst_pct", "mrp", "batch",
        "expiry", "manufacturer", "pack", "name", "hsn_code", "free_qty",
    )}
    if num(q["qty"]) <= 0 and num(q["free_qty"]) <= 0:
        return -4  # a total / carried-forward / heading row, not a medicine
    d = num(q["discount_pct"])
    if amount_mode(num(q["qty"]), num(q["rate"]), num(q["amount"]), d, d, num(q["gst_pct"])):
        score += 4
    batch = clean(q["batch"])
    if batch and is_batch(batch):
        score += 1
    elif batch and not is_missing(batch) and not batch.upper().startswith("WITHOUT"):
        score -= 1
    if is_expiry(q["expiry"], allow_compact=True):
        score += 1
    if clean(q["gst_pct"]) == "" or is_gst_rate(q["gst_pct"]):
        score += 1
    mfg = clean(q["manufacturer"])
    if mfg:
        score += 1 if is_mfg(mfg) else -2
    pack = clean(q["pack"])
    if pack:
        score += 1 if is_pack(pack, allow_plain=True) else -2
    name = clean(q["name"])
    if has_letters(name):
        score += 1
    if name and re.search(r"\d(?:ML|GM|MG|KG|'S)[A-Z]{2,}$", name, flags=re.I):
        score -= 1
    hsn = clean(q["hsn_code"])
    if hsn:
        score += 1 if re.fullmatch(r"\d{4,8}", hsn) else -1
    mrp, rate = num(q["mrp"]), num(q["rate"])
    if mrp and rate and mrp + 0.01 < rate:
        score -= 1
    return score


def records_quality(records: Sequence[Any]) -> int:
    return sum(record_quality(r) for r in records or [])


def better_reading(candidates: Sequence[Tuple[str, Sequence[Any]]]) -> Tuple[str, Sequence[Any]]:
    """The candidate reading with the most lines that hold up (first wins a tie)."""
    best = ("", [])
    best_score = None
    for label, recs in candidates:
        if not recs:
            continue
        score = records_quality(recs)
        if best_score is None or score > best_score:
            best, best_score = (label, recs), score
    return best
