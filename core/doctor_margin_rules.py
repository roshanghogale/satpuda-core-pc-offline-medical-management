"""Doctor margin RULES (owner, 22 + 23 Oct 2026): which medicines give a doctor a margin, and how.

Two ways to give a doctor his margin (owner, 23 Oct 2026), chosen PER MEDICINE TYPE:

  on profit       the owner's Excel: for a period, per doctor, product / quantity / MRP / rate /
                  total value with total value = (MRP - rate) x quantity, and a TOTAL. ONLY the
                  products picked into a group count ("only the selected group medicines not all
                  of them"): a product of that type in no group is not in the report.
  on total sales  the doctor gets X% of the total sale value of the chosen types: "if using mrp
                  total sales for all others selected types for 10 days is 80000 the doctor
                  margin is 10% from it". No purchase rate is involved.

This file is the rule model and the calculation (pure: the server / web and the phone port it
with parity vectors); core/doctor_margin_store.py saves the lines when a bill is saved and
core/doctor_margin_rules_report.py lays the report out.

The rules -- one store setting ``doctor_margin_rules`` (JSON), shop-wide and synced like the
other shop settings (offline-first triggers, store_kv_carry, the server's settings kv Online).

VERSION 2 (written from 23 Oct 2026):

  {version: 2, rate_basis: 'purchase' | 'purchase_free', updated,
   sets: [{doctor: '' (= All doctors, always first) | 'DR NAME',
           rate_basis: null (= as All doctors / the top one) | 'purchase' | 'purchase_free',
           sales_pct: number 0..100 (All doctors: never null, 0 when unset)
                      | null (a doctor's set: same % as All doctors),
           types: [{type, mode: 'none' | 'groups' | 'sales', groups: [group]}]}]}
  group  = {name, margin, all: false, items: [{product: 'ZIFI 1GM', batches: [] (= every batch) | ['B1', ..]}]}
         | {name, margin, all: true,  items: []}      every product of the type ("All products")
  margin = {kind: 'mrp_rate' | 'pct_mrp' | 'fixed' | 'none', value}

  mode none    the type gives no margin
  mode groups  ON PROFIT: only the products in its groups give a margin, each group's own
  mode sales   ON TOTAL SALES: every product of the type counts, at the set's sales_pct

  mrp_rate  (MRP - rate) per unit, the owner's Excel (the default)
  pct_mrp   value % of MRP per unit
  fixed     value rupees per unit
  none      no margin (a group of products that give nothing, inside a type that does)

VERSION 1 (22 Oct 2026) is still read; clean_rules() turns it into version 2:
  types[].mode 'type' (whole type, one margin)  -> mode 'groups' with ONE group
                                                   {name: 'All <type>', all: true, margin}
                                                   (the same figures)
  types[].rest (rest of type, a margin)         -> dropped: a product in no group gives no
                                                   margin now (the owner's request)
  everything else unchanged; sales_pct = 0 on All doctors, null on a doctor's set.

Which rule a sold line gets (doctor D, type T, product P, batch B):
  1. D's own set when it has a rule for T, else the All doctors rule for T, else none.
  2. mode 'none': nothing.  mode 'sales': the sales way at sales_pct of D's own set when it is
     a number, else All doctors' sales_pct.
  3. mode 'groups': the most specific group holding P wins --
       a batch-limited item naming B  >  a one-product group  >  a group of several products
       >  an "All products" group;
     the first such group when two tie. No group holds P: nothing (P is not in the report).

On profit -- the rate is the purchase rate of THAT batch (the medicine row), worked out here on
the fly and used only for the doctor's margin -- nothing is stored in the books and no sale,
purchase, stock value or Sales-screen margin reads it (owner, 22 Oct 2026: "this rate is only
for the doctor"):
  purchase       pack rate as billed x packs bought / units bought (free units left out)
  purchase_free  the same money / every unit received, free included:
                 Rs 100 for 10 + 5 free = Rs 100 / 15 = 6.6667 a unit
  no live purchase line for the batch: the batch's own purchase rate on the medicine row, then
  the cost the bill recorded at sale time ("estimated"); neither: no rate ("no_rate").
Units: per sale unit, the way sales count quantity (tablets for strip medicines).

On total sales -- a line's sale value is its amount as billed (qty x the sale rate, MRP based,
less the line's own discount: sales_items.amount); the bill's overall discount is not spread
over lines. Returned qty takes its share off: value = amount x net qty / qty sold. The
commission is worked out on the doctor's TOTAL per percentage (not line by line):
commission = round(sum of the lines' values x pct / 100, 2).

Returns of a bill reduce its lines' quantity (and so the margin / the sale value) for that doctor
and batch, shared over the bill's lines of that medicine in bill order.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

SETTING_NAME = "doctor_margin_rules"
# The periods recalculated on this PC, {version: 1, runs: [{from, to, at, rules_updated}]}: a
# shop setting synced like the rules, so the server's report (web, phone) works those bills
# out with the rules of the recalculation too (server src/services/doctorMarginRulesStore.js).
RECALC_SETTING = "doctor_margin_rules_recalc"
RECALC_KEEP = 200
RECALC_VERSION = 1
VERSION = 2
NO_DOCTOR = "(No doctor)"
RATE_BASES = ("purchase", "purchase_free")
MODES = ("none", "groups", "sales")
WAYS = ("profit", "sales")
KINDS = ("mrp_rate", "pct_mrp", "fixed", "none")
SALES_KIND = "sales_pct"  # the kind a total-sales line is saved with (kind_value = the %)
RATE_BASIS_TEXT = {
    "purchase": "Purchase rate",
    "purchase_free": "Purchase rate with free included",
}
KIND_TEXT = {"mrp_rate": "MRP - rate", "pct_mrp": "% of MRP", "fixed": "Fixed per unit", "none": "No margin",
             SALES_KIND: "% of total sales"}
MODE_TEXT = {"none": "No margin", "groups": "On profit (groups)", "sales": "On total sales (%)"}
FLAG_TEXT = {
    "estimated": "Rate not from a purchase",
    "no_rate": "No purchase rate",
    "rate_over_mrp": "Rate above MRP",
}


# ── small helpers ─────────────────────────────────────────────────────────────────────────

def norm_name(v: Any) -> str:
    """Product / doctor key: spaces squeezed, upper case."""
    return " ".join(str(v or "").split()).upper()


def norm_type(v: Any) -> str:
    return " ".join(str(v or "").split()).lower()


def norm_batch(v: Any) -> str:
    return " ".join(str(v or "").split()).upper()


def _f(v: Any, default: float = 0.0) -> float:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return default
    return x if x == x and x not in (float("inf"), float("-inf")) else default


def r2(v: float) -> float:
    return round(_f(v) + 0.0, 2) + 0.0


# ── the rule model ────────────────────────────────────────────────────────────────────────

def clean_margin(m: Any) -> dict[str, Any] | None:
    """{kind, value} or None (no margin given)."""
    if not isinstance(m, dict):
        return None
    kind = str(m.get("kind") or "").strip().lower()
    if kind not in KINDS:
        return None
    value = _f(m.get("value"))
    if kind == "pct_mrp" and not (0 <= value <= 100):
        raise ValueError("A % of MRP margin must be 0 to 100.")
    if kind == "fixed" and value < 0:
        raise ValueError("A fixed margin cannot be below 0.")
    return {"kind": kind, "value": value if kind in ("pct_mrp", "fixed") else 0}


def clean_pct(v: Any, *, allow_null: bool) -> float | None:
    """The total-sales %: 0..100; null / '' = not set (a doctor's set: as All doctors)."""
    if v is None or (isinstance(v, str) and not v.strip()):
        return None if allow_null else 0.0
    x = _f(v, -1.0)
    if not (0 <= x <= 100):
        raise ValueError("A % of total sales must be 0 to 100.")
    return x


def _clean_items(items: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: dict[str, dict[str, Any]] = {}
    for it in items or []:
        if isinstance(it, str):
            it = {"product": it}
        if not isinstance(it, dict):
            continue
        p = norm_name(it.get("product") or it.get("name"))
        if not p:
            continue
        batches = sorted({norm_batch(b) for b in (it.get("batches") or []) if norm_batch(b)})
        if p in seen:  # the same product twice in a group: one item, batches merged
            prev = seen[p]
            prev["batches"] = [] if not prev["batches"] or not batches else sorted(set(prev["batches"]) | set(batches))
            continue
        row = {"product": p, "batches": batches}
        seen[p] = row
        out.append(row)
    return out


def _clean_type_rule(t: Any) -> dict[str, Any] | None:
    if not isinstance(t, dict):
        return None
    name = " ".join(str(t.get("type") or "").split())
    if not name:
        return None
    mode = str(t.get("mode") or "none").strip().lower()
    out: dict[str, Any] = {"type": name, "mode": "none", "groups": []}
    if mode == "type":  # version 1 "whole type": one All-products group, the same figures
        out["mode"] = "groups"
        out["groups"].append({"name": f"All {name}", "margin": clean_margin(t.get("margin")) or {"kind": "mrp_rate", "value": 0},
                              "all": True, "items": []})
        return out
    if mode not in MODES:
        return out
    out["mode"] = mode
    if mode == "groups":
        # version 1 "rest" (rest of type) is not carried: a product in no group gives nothing.
        for i, g in enumerate(t.get("groups") or []):
            if not isinstance(g, dict):
                continue
            every = bool(g.get("all"))
            items = [] if every else _clean_items(g.get("items"))
            if not every and not items:
                continue
            out["groups"].append({
                "name": " ".join(str(g.get("name") or "").split()) or (f"All {name}" if every else f"Group {i + 1}"),
                "margin": clean_margin(g.get("margin")) or {"kind": "none", "value": 0},
                "all": every,
                "items": items,
            })
    return out


def clean_rules(data: Any) -> dict[str, Any]:
    """The rules, checked and in their one (version 2) shape; version 1 is read and turned
    into version 2 (see the top of this file). Raises ValueError with what to correct."""
    if isinstance(data, str):
        try:
            data = json.loads(data) if data.strip() else {}
        except ValueError:
            data = {}
    if not isinstance(data, dict):
        data = {}
    basis = str(data.get("rate_basis") or "purchase").strip().lower()
    sets: list[dict[str, Any]] = []
    seen: set[str] = set()
    for s in data.get("sets") or []:
        if not isinstance(s, dict):
            continue
        doctor = norm_name(s.get("doctor"))
        if doctor == NO_DOCTOR.upper() or doctor in seen:
            continue
        seen.add(doctor)
        types: list[dict[str, Any]] = []
        tseen: set[str] = set()
        for t in s.get("types") or []:
            rule = _clean_type_rule(t)
            if rule and norm_type(rule["type"]) not in tseen:
                tseen.add(norm_type(rule["type"]))
                types.append(rule)
        sb = str(s.get("rate_basis") or "").strip().lower()
        sets.append({"doctor": doctor, "rate_basis": sb if sb in RATE_BASES else None,
                     "sales_pct": clean_pct(s.get("sales_pct"), allow_null=doctor != ""), "types": types})
    sets.sort(key=lambda s: (s["doctor"] != "", s["doctor"]))
    if not sets or sets[0]["doctor"] != "":
        sets.insert(0, {"doctor": "", "rate_basis": None, "sales_pct": 0.0, "types": []})
    return {
        "version": VERSION,
        "rate_basis": basis if basis in RATE_BASES else "purchase",
        "updated": str(data.get("updated") or ""),
        "sets": sets,
    }


def empty_rules() -> dict[str, Any]:
    return clean_rules({})


class Resolver:
    """Which margin a doctor's sold line gets. Built once per report."""

    def __init__(self, rules: dict[str, Any]):
        self.rules = clean_rules(rules)
        self.default = self.rules["sets"][0]
        self.by_doctor = {s["doctor"]: s for s in self.rules["sets"][1:]}

    def _type_rule(self, doctor: str, med_type: str) -> tuple[dict[str, Any] | None, str]:
        t = norm_type(med_type)
        own = self.by_doctor.get(norm_name(doctor))
        for src, label in ((own, "doctor"), (self.default, "default")):
            if not src:
                continue
            for rule in src["types"]:
                if norm_type(rule["type"]) == t:
                    return rule, label
        return None, ""

    def rate_basis(self, doctor: str) -> str:
        own = self.by_doctor.get(norm_name(doctor))
        if own and own.get("rate_basis"):
            return own["rate_basis"]
        if self.default.get("rate_basis"):
            return self.default["rate_basis"]
        return self.rules["rate_basis"]

    def sales_pct(self, doctor: str) -> float:
        own = self.by_doctor.get(norm_name(doctor))
        if own and own.get("sales_pct") is not None:
            return float(own["sales_pct"])
        return float(self.default.get("sales_pct") or 0.0)

    def resolve(self, doctor: str, med_type: str, product: str, batch: str) -> dict[str, Any] | None:
        """The way this line gives a margin, None when it gives none:
             on profit       {way: 'profit', kind, value, rule, source}
             on total sales  {way: 'sales', kind: 'sales_pct', value: pct, rule, source}"""
        rule, source = self._type_rule(doctor, med_type)
        if not rule or rule["mode"] == "none":
            return None
        if rule["mode"] == "sales":
            return {"way": "sales", "kind": SALES_KIND, "value": self.sales_pct(doctor),
                    "rule": f"{rule['type']} (total sales)", "source": source}
        p, b = norm_name(product), norm_batch(batch)
        best: tuple[int, dict[str, Any]] | None = None
        for g in rule["groups"]:
            if g.get("all"):
                rank = 0
                if best is None or rank > best[0]:
                    best = (rank, g)
                continue
            for it in g["items"]:
                if it["product"] != p:
                    continue
                if it["batches"]:
                    if b not in it["batches"]:
                        continue
                    rank = 3
                else:
                    rank = 2 if len(g["items"]) == 1 else 1
                if best is None or rank > best[0]:
                    best = (rank, g)
        if best is None:
            return None  # not in any group: not in the report
        m = best[1]["margin"]
        if not m or m["kind"] == "none":
            return None
        return {"way": "profit", "kind": m["kind"], "value": m["value"],
                "rule": f"{rule['type']} > {best[1]['name']}", "source": source}


# ── the calculation ───────────────────────────────────────────────────────────────────────

def batch_rates(purchases: Any) -> dict[int, dict[str, float]]:
    """medicine_id -> {purchase, purchase_free} per sale unit, from live purchase lines.

    purchases: [{medicine_id, qty, free_qty, rate (per pack, as billed), units (per pack)}]"""
    acc: dict[int, list[float]] = {}
    for p in purchases or []:
        try:
            mid = int(p.get("medicine_id") or 0)
        except (TypeError, ValueError):
            continue
        if mid <= 0:
            continue
        units = max(1.0, _f(p.get("units"), 1.0) or 1.0)
        qty, free = max(0.0, _f(p.get("qty"))), max(0.0, _f(p.get("free_qty")))
        if qty + free <= 0:
            continue
        a = acc.setdefault(mid, [0.0, 0.0, 0.0])  # money, billed units, all units
        a[0] += _f(p.get("rate")) * qty
        a[1] += qty * units
        a[2] += (qty + free) * units
    out: dict[int, dict[str, float]] = {}
    for mid, (money, billed, got) in acc.items():
        if got <= 0:
            continue
        out[mid] = {
            "purchase": money / billed if billed > 0 else 0.0,
            "purchase_free": money / got,
        }
    return out


def has_rules(rules: Any) -> bool:
    """True when any set gives a margin on anything (else nothing is worked out or saved)."""
    for s in clean_rules(rules)["sets"]:
        for t in s["types"]:
            if t["mode"] == "sales":
                return True
            if t["mode"] == "groups" and any(g["margin"]["kind"] != "none" for g in t["groups"]):
                return True
    return False


def margin_per_unit(kind: str, value: float, mrp: float, rate: float, flags: list[str]) -> float:
    """Rupees one unit gives the doctor. flags gains 'rate_over_mrp' when MRP - rate is below 0."""
    if kind == "mrp_rate":
        if "no_rate" in flags:
            return 0.0
        unit = mrp - rate
        if unit < 0:
            if "rate_over_mrp" not in flags:
                flags.append("rate_over_mrp")
            return 0.0
        return unit
    if kind == "pct_mrp":
        return mrp * value / 100.0
    if kind == "fixed":
        return value
    return 0.0


def evaluate_line(res: Resolver, doctor: str, item: dict[str, Any], rates: dict[int, dict[str, float]],
                  basis: str) -> dict[str, Any] | None:
    """One sold line's margin figures per unit, None when it gives no margin.

    item: {medicine_id, name, type, batch, mrp_unit, med_rate_unit, cost_price}"""
    rule = res.resolve(doctor, item.get("type") or "", item.get("name") or "", item.get("batch") or "")
    if rule is None:
        return None
    mrp = max(0.0, _f(item.get("mrp_unit")))
    if rule["way"] == "sales":
        return {
            "way": "sales", "mrp": round(mrp, 4), "rate_purchase": 0.0, "rate_free": 0.0, "rate_basis": "",
            "rate": 0.0, "kind": SALES_KIND, "kind_value": rule["value"], "margin_unit": 0.0,
            "rule": rule["rule"], "flags": [],
        }
    mid = int(_f(item.get("medicine_id")))
    flags: list[str] = []
    if mid in rates:
        rp, rf = rates[mid]["purchase"], rates[mid]["purchase_free"]
    elif _f(item.get("med_rate_unit")) > 0:
        rp = rf = _f(item.get("med_rate_unit"))
        flags.append("estimated")
    elif _f(item.get("cost_price")) > 0:
        rp = rf = _f(item.get("cost_price"))
        flags.append("estimated")
    else:
        rp = rf = 0.0
        flags.append("no_rate")
    rate = rf if basis == "purchase_free" else rp
    unit = margin_per_unit(rule["kind"], rule["value"], mrp, rate, flags)
    return {
        "way": "profit",
        "mrp": round(mrp, 4),
        "rate_purchase": round(rp, 4),
        "rate_free": round(rf, 4),
        "rate_basis": basis,
        "rate": round(rate, 4),
        "kind": rule["kind"],
        "kind_value": rule["value"],
        "margin_unit": round(unit, 4),
        "rule": rule["rule"],
        "flags": flags,
    }


def with_basis(row: dict[str, Any], basis: str) -> dict[str, Any]:
    """A saved line seen with the other rate (the report's rate choice): the saved MRP, both
    saved rates and the saved rule; only which of the two rates is used changes. A total-sales
    line has no rate: as it is."""
    if basis not in RATE_BASES or basis == row.get("rate_basis") or row.get("way") == "sales":
        return row
    out = dict(row)
    flags = [f for f in (row.get("flags") or []) if f != "rate_over_mrp"]
    rate = _f(row.get("rate_free") if basis == "purchase_free" else row.get("rate_purchase"))
    unit = margin_per_unit(str(row.get("kind") or ""), _f(row.get("kind_value")), _f(row.get("mrp")), rate, flags)
    out.update(rate_basis=basis, rate=round(rate, 4), margin_unit=round(unit, 4),
               value=r2(round(unit, 4) * _f(row.get("qty"))), flags=flags)
    return out


def line_value(ln: dict[str, Any]) -> float:
    """A line's value at its net qty: the margin (on profit) or the sale value (total sales)."""
    qty = _f(ln.get("qty"))
    if ln.get("way") == "sales":
        sold = _f(ln.get("qty_sold"))
        return r2(_f(ln.get("amount")) * qty / sold) if sold > 0 else 0.0
    return r2(_f(ln.get("margin_unit")) * qty)


def apply_returns(lines: list[dict[str, Any]], returns: Any) -> None:
    """Spread a bill's returned qty (per medicine) over its lines in bill order; sets
    qty_returned, qty and value on each line (in place)."""
    back: dict[int, float] = {}
    for r in returns or []:
        mid = int(_f(r.get("medicine_id")))
        back[mid] = back.get(mid, 0.0) + max(0.0, _f(r.get("qty")))
    for ln in lines:
        mid = int(_f(ln.get("medicine_id")))
        sold = max(0.0, _f(ln.get("qty_sold")))
        took = min(sold, back.get(mid, 0.0))
        if took > 0:
            back[mid] -= took
        ln["qty_returned"] = round(took, 3)
        ln["qty"] = round(sold - took, 3)
        # Each line's value is rounded once; every total adds these, so sub-totals, doctor
        # totals and the TOTAL always equal the sum of the lines shown.
        ln["value"] = line_value(ln)


def bill_lines(res: Resolver, sale: dict[str, Any], rates: dict[int, dict[str, float]],
               rate_basis: str = "") -> list[dict[str, Any]]:
    """The margin lines of one bill (both ways), returns applied; lines that give no margin
    left out.

    sale items may carry the bill line's own figures (the total-sales way needs them):
      sale_rate (rate per unit as billed), amount (the line's amount, its discount taken off),
      disc (the line's discount in rupees), pack (the medicine's pack / unit text)"""
    doctor = norm_name(sale.get("doctor"))
    if not doctor:
        return []  # a bill without a doctor gives no one a margin
    basis = rate_basis if rate_basis in RATE_BASES else res.rate_basis(doctor)
    lines: list[dict[str, Any]] = []
    for n, it in enumerate(sale.get("items") or [], 1):
        sold = _f(it.get("qty"))
        ev = evaluate_line(res, doctor, it, rates, basis) if sold > 0 else None
        lines.append({
            "line_no": n,
            "sale_id": int(_f(sale.get("id"))),
            "sale_item_id": int(_f(it.get("id"))),
            "doctor": doctor,
            "date": str(sale.get("date") or "")[:10],
            "bill_no": str(sale.get("bill_no") or ""),
            "patient": str(sale.get("patient") or ""),
            "medicine_id": int(_f(it.get("medicine_id"))),
            "name": str(it.get("name") or ""),
            "type": str(it.get("type") or ""),
            "batch": str(it.get("batch") or ""),
            "pack": str(it.get("pack") or ""),
            "qty_sold": round(max(0.0, sold), 3),
            "sale_rate": round(_f(it.get("sale_rate")), 4),
            "disc": r2(_f(it.get("disc"))),
            "amount": r2(_f(it.get("amount"))),
            "way": ev["way"] if ev else "",
            "margin_unit": ev["margin_unit"] if ev else 0.0,
            "_ev": ev,
        })
    # Lines without a margin still take their share of a return first, as the bill has them.
    apply_returns(lines, sale.get("returns"))
    out = []
    for ln in lines:
        ev = ln.pop("_ev")
        if ev is None:
            continue
        ln.update({k: v for k, v in ev.items() if k != "margin_unit"})
        out.append(ln)
    return out


def _sort_key(r: dict[str, Any]) -> tuple:
    return (r["doctor"], r["name"], r["batch"], r["date"], r["bill_no"])


def assemble_profit(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """On-profit lines -> {rows, summary (the owner's Excel: per doctor, product, batch, MRP,
    rate), doctors, totals}. Every total adds the lines' own (already rounded) values."""
    rows = sorted(rows, key=_sort_key)
    groups: dict[tuple, dict[str, Any]] = {}
    for r in rows:
        k = (r["doctor"], r["name"], r["batch"], r["mrp"], r["rate"], r["margin_unit"])
        g = groups.get(k)
        if g is None:
            g = groups[k] = {
                "doctor": r["doctor"], "name": r["name"], "type": r["type"], "batch": r["batch"],
                "mrp": r["mrp"], "rate": r["rate"], "rate_basis": r["rate_basis"], "kind": r["kind"],
                "margin_unit": r["margin_unit"], "qty": 0.0, "value": 0.0, "rule": r["rule"], "flags": [],
            }
        g["qty"] = round(g["qty"] + r["qty"], 3)
        g["value"] = r2(g["value"] + r["value"])
        for f in r["flags"]:
            if f not in g["flags"]:
                g["flags"].append(f)
    doctors: list[dict[str, Any]] = []
    for d in sorted({r["doctor"] for r in rows}):
        mine = [r for r in rows if r["doctor"] == d]
        doctors.append({
            "doctor": d,
            "qty": round(sum(r["qty"] for r in mine), 3),
            "value": r2(sum(r["value"] for r in mine)),
            "lines": len(mine),
        })
    return {
        "rows": rows,
        "summary": list(groups.values()),
        "doctors": doctors,
        "totals": {"qty": round(sum(r["qty"] for r in rows), 3), "value": r2(sum(r["value"] for r in rows)),
                   "lines": len(rows)},
    }


def sales_products(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The "Customer-Wise Product-Batch Wise Sales" layout: per product (name + pack heading),
    its bill lines by customer, date, bill no, and the product's sub-total qty and value."""
    out: dict[tuple, dict[str, Any]] = {}
    for r in sorted(rows, key=lambda r: (r["name"], r.get("pack") or "", r["patient"], r["date"], r["bill_no"],
                                         r.get("line_no") or 0)):
        k = (r["name"], r.get("pack") or "")
        g = out.get(k)
        if g is None:
            g = out[k] = {"name": r["name"], "pack": r.get("pack") or "", "type": r["type"], "lines": [],
                          "qty": 0.0, "value": 0.0}
        g["lines"].append(r)
        g["qty"] = round(g["qty"] + r["qty"], 3)
        g["value"] = r2(g["value"] + r["value"])
    return list(out.values())


def assemble_sales(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Total-sales lines -> {rows, doctors: [{doctor, qty, value, lines, types, by_pct:
    [{pct, value, commission}], commission, products}], totals: {qty, value, lines, commission}}.

    The commission is worked out per doctor on the total of each % (a period can hold bills
    saved under two different %): commission = round(value x pct / 100, 2)."""
    rows = sorted(rows, key=_sort_key)
    doctors: list[dict[str, Any]] = []
    for d in sorted({r["doctor"] for r in rows}):
        mine = [r for r in rows if r["doctor"] == d]
        by_pct: list[dict[str, Any]] = []
        for pct in sorted({_f(r["kind_value"]) for r in mine}, reverse=True):
            value = r2(sum(r["value"] for r in mine if _f(r["kind_value"]) == pct))
            by_pct.append({"pct": pct, "value": value, "commission": r2(value * pct / 100.0)})
        types: list[str] = []
        for r in mine:
            if r["type"] and norm_type(r["type"]) not in {norm_type(t) for t in types}:
                types.append(r["type"])
        doctors.append({
            "doctor": d,
            "qty": round(sum(r["qty"] for r in mine), 3),
            "value": r2(sum(r["value"] for r in mine)),
            "lines": len(mine),
            "types": sorted(types, key=norm_type),
            "by_pct": by_pct,
            "commission": r2(sum(p["commission"] for p in by_pct)),
            "products": sales_products(mine),
        })
    return {
        "rows": rows,
        "doctors": doctors,
        "totals": {"qty": round(sum(r["qty"] for r in rows), 3), "value": r2(sum(r["value"] for r in rows)),
                   "lines": len(rows), "commission": r2(sum(d["commission"] for d in doctors))},
    }


def assemble(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Both ways' lines -> the on-profit report at the top level (rows, summary, doctors,
    totals -- as before), the total-sales report under 'sales', and per doctor the two added
    under 'grand': {doctors: [{doctor, profit, sales_value, commission, total}],
    totals: {profit, sales_value, commission, total}}."""
    profit = assemble_profit([r for r in rows if r.get("way", "profit") != "sales"])
    sales = assemble_sales([r for r in rows if r.get("way") == "sales"])
    pd = {d["doctor"]: d for d in profit["doctors"]}
    sd = {d["doctor"]: d for d in sales["doctors"]}
    grand = []
    for d in sorted(set(pd) | set(sd)):
        p = pd[d]["value"] if d in pd else 0.0
        s = sd.get(d)
        c = s["commission"] if s else 0.0
        grand.append({"doctor": d, "profit": p, "sales_value": s["value"] if s else 0.0,
                      "commission": c, "total": r2(p + c)})
    return {
        **profit,
        "sales": sales,
        "grand": {
            "doctors": grand,
            "totals": {"profit": profit["totals"]["value"], "sales_value": sales["totals"]["value"],
                       "commission": sales["totals"]["commission"],
                       "total": r2(profit["totals"]["value"] + sales["totals"]["commission"])},
        },
    }


def compute(sales: Any, purchases: Any, rules: Any, opts: dict[str, Any] | None = None) -> dict[str, Any]:
    """The report worked out straight from bills with the rules given (the saved table,
    core/doctor_margin_store.py, holds these same lines as they were at sale time).

    sales: [{id, bill_no, date, patient, doctor, items: [{id, medicine_id, name, type, batch, qty,
             mrp_unit, med_rate_unit, cost_price, sale_rate, amount, disc, pack}],
             returns: [{medicine_id, qty}]}]
             mrp_unit / med_rate_unit = the batch's MRP / purchase rate per sale unit
    opts:  {doctors: [..] (empty = every doctor), rate_basis: None | 'purchase' | 'purchase_free'}
    """
    opts = opts or {}
    res = Resolver(rules)
    want = {norm_name(d) for d in (opts.get("doctors") or []) if norm_name(d)}
    forced = str(opts.get("rate_basis") or "").strip().lower()
    rates = batch_rates(purchases)
    rows: list[dict[str, Any]] = []
    for s in sales or []:
        if want and norm_name(s.get("doctor")) not in want:
            continue
        rows.extend(r for r in bill_lines(res, s, rates, forced) if r["qty"] > 0)
    return assemble(rows)


# ── keeping the rules (the store's settings, synced) ──────────────────────────────────────

def _online() -> bool:
    try:
        from core.sync_prefs import is_online_mode

        return bool(is_online_mode())
    except Exception:
        return False


def _server_value() -> Any:
    from core import server_api as api
    from core.server_live import _token
    from core.store_images import _kv_rows

    res = api._request("GET", f"/api/sync/settings/kv?prefix={SETTING_NAME}", token=_token(), timeout=15)
    if not isinstance(res, dict) or not res.get("ok"):
        raise RuntimeError("Server varun doctor margin rules vachta ale nahit.")
    for row in _kv_rows(res.get("data")):
        if str(row.get("name") or "") == SETTING_NAME:
            return row.get("value")
    return ""


def load_rules(conn) -> dict[str, Any]:
    if _online():
        raw = _server_value()
    else:
        from core.desktop_settings_service import _setting_get

        raw = _setting_get(conn, SETTING_NAME, "")
    try:
        return clean_rules(raw)
    except ValueError:
        return empty_rules()


def save_rules(conn, data: Any) -> dict[str, Any]:
    """Replace the rules (checked first; always written as version 2). Returns what was kept."""
    rules = clean_rules(data)
    rules["updated"] = datetime.now().isoformat(timespec="seconds")
    raw = json.dumps(rules, ensure_ascii=False, separators=(",", ":"))
    if _online():
        from core.server_live import push_settings_kv

        push_settings_kv(SETTING_NAME, raw)
    else:
        from core.desktop_settings_service import _setting_set

        _setting_set(conn, SETTING_NAME, raw)
        conn.commit()
    return rules


def note_recalculate(conn, from_date: str, to_date: str, rules: dict[str, Any]) -> dict[str, str]:
    """Keep "this period was recalculated now, with these rules" in the shop settings (the
    last RECALC_KEEP runs). Recalculate runs on this PC's own books (Offline / offline-first),
    so the row is written here and travels with the shop settings."""
    from core.desktop_settings_service import _setting_get, _setting_set

    try:
        data = json.loads(_setting_get(conn, RECALC_SETTING, "") or "{}")
    except ValueError:
        data = {}
    runs = [r for r in (data.get("runs") or []) if isinstance(r, dict)] if isinstance(data, dict) else []
    run = {"from": str(from_date)[:10], "to": str(to_date)[:10],
           "at": datetime.now().isoformat(timespec="seconds"), "rules_updated": str(rules.get("updated") or "")}
    runs.append(run)
    _setting_set(conn, RECALC_SETTING, json.dumps({"version": RECALC_VERSION, "runs": runs[-RECALC_KEEP:]},
                                                  ensure_ascii=False, separators=(",", ":")))
    conn.commit()
    return run
