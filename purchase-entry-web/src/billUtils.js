function itemHasData(it) {
  const name = (it.medicine_name || '').trim()
  if (!name) return false
  const qty = parseFloat(it.qty) || 0
  return qty > 0
}

export function buildJSON(bills) {
  return {
    bills: bills.map(bill => {
      const items = (bill.items || []).filter(itemHasData)
      const discount = parseFloat(bill.overall_discount) || 0
      const gstMethod = bill.gst_calc_method || 'discount_after_gst'
      const summary = calcPurchaseSummary(items, discount, 0, gstMethod)

      return {
        supplier: {
          name:       bill.supplier_name   || '',
          address:    bill.supplier_address|| '',
          phone:      bill.supplier_phone  || '',
          gstin:      bill.supplier_gstin  || '',
          dl_numbers: bill.supplier_dl     || '',
        },
        purchase_date:    bill.purchase_date    || '',
        bill_number:      bill.bill_number      || '',
        gst_calc_method:  gstMethod,
        overall_discount: discount,
        overall_discount_pct: parseFloat(bill.overall_discount_pct) || 0,
        cash_paid:        parseFloat(bill.cash_paid)        || 0,
        online_paid:      parseFloat(bill.online_paid)      || 0,
        amount_paid:      (parseFloat(bill.cash_paid) || 0) + (parseFloat(bill.online_paid) || 0),
        items: items.map((it, idx) => ({
          medicine_name:      it.medicine_name      || '',
          type:               it.type               || '',
          batch_no:           it.batch_no           || '',
          expiry_date:        it.expiry_date         || '',
          qty:                parseFloat(it.qty)            || 0,
          tablets_per_stripe: ['tablet','bolus'].includes((it.type||'').toLowerCase()) ? (parseInt(it.quantity_value) || 1) : 1,
          free_qty:           parseFloat(it.free_qty)        || 0,
          rate:               parseFloat(it.rate)            || 0,
          mrp:                parseFloat(it.mrp)             || 0,
          gst_percent:        parseFloat(it.gst_percent)     || 0,
          gst_amount:         summary.lines[idx]?.gstAmt ?? 0,
          hsn_code:           it.hsn_code           || '',
          manufacturer:       it.manufacturer       || '',
          schedule:           it.schedule           || '',
          content_drug:       it.content_drug       || '',
          item_discount:      parseFloat(it.item_discount)   || 0,
          quantity_value:     it.quantity_value     || '1',
          is_tax_inclusive:   gstMethod === 'discount_after_gst',
        }))
      }
    })
  }
}

export function emptyBill() {
  return {
    supplier_name: '', supplier_address: '', supplier_phone: '',
    supplier_gstin: '', supplier_dl: '',
    purchase_date: today(), bill_number: '',
    gst_calc_method: 'discount_after_gst',
    overall_discount: 0, overall_discount_pct: 0, cash_paid: 0, online_paid: 0,
    items: [emptyItem()],
  }
}

export function emptyItem() {
  return {
    medicine_name: '', type: '', batch_no: '', expiry_date: '',
    qty: '', free_qty: '0',
    rate: '', mrp: '', gst_percent: '0', item_discount: '0',
    hsn_code: '', manufacturer: '', schedule: '',
    content_drug: '', quantity_value: '1',
  }
}

function today() {
  return new Date().toISOString().slice(0, 10)
}

// ── Centralized Purchase Engine (mirrors core/pharmacy_purchase_calc.py) ─────

export function round2(x) {
  const s = (Number(x) * 100).toFixed(10)
  return Math.floor(parseFloat(s) + 0.5) / 100
}

function roundUp2(x) {
  const n = Number(x)
  if (n <= 0) return 0
  return Math.ceil(n * 100 - 1e-9) / 100
}

function gstMethodToTaxMode(method) {
  return (method || 'discount_after_gst') === 'discount_before_gst' ? 'exclusive' : 'inclusive'
}

function evaluateLineItems(items, billInclusive) {
  return items.map(it => {
    const qty = parseFloat(it.qty) || 0
    const rate = parseFloat(it.rate) || 0
    const discPct = parseFloat(it.item_discount) || 0
    const gstPct = parseFloat(it.gst_percent) || 0
    const inclusive = it.is_tax_inclusive != null ? !!it.is_tax_inclusive : billInclusive
    const gross = round2(qty * rate)
    const lineDisc = round2(gross * discPct / 100)
    const net = round2(gross - lineDisc)
    return {
      item: it,
      qty, rate, discPct, gstPct, inclusive,
      gross, lineDisc, net,
    }
  })
}

function allocateSlabDiscounts(rows, globalDisc) {
  globalDisc = round2(Math.max(0, globalDisc))
  const slabs = {}
  for (const row of rows) {
    const key = String(round2(row.gstPct))
    slabs[key] = round2((slabs[key] || 0) + row.net)
  }
  const keys = Object.keys(slabs).sort((a, b) => slabs[b] - slabs[a])
  const totalGross = round2(keys.reduce((s, k) => s + slabs[k], 0))
  const slabDisc = {}
  const slabBasis = { ...slabs }
  let remaining = globalDisc
  const lastKey = keys[keys.length - 1]
  for (const key of keys) {
    let disc = 0
    if (totalGross > 0 && globalDisc > 0) {
      if (key === lastKey) {
        disc = round2(remaining)
      } else {
        disc = round2(globalDisc * slabs[key] / totalGross)
        remaining = round2(remaining - disc)
      }
    }
    slabDisc[key] = disc
    slabBasis[key] = round2(Math.max(0, slabs[key] - disc))
  }
  return { slabs, slabDisc, slabBasis, totalGross }
}

function extractSlabTaxInclusive(basis, gstRate) {
  if (basis <= 0) return { taxable: 0, cgst: 0, sgst: 0, totalGst: 0 }
  if (gstRate <= 0) return { taxable: round2(basis), cgst: 0, sgst: 0, totalGst: 0 }
  const divisor = 1 + gstRate / 100
  const taxable = round2(basis / divisor)
  const totalGst = round2(basis - taxable)
  const half = roundUp2(totalGst / 2)
  return { taxable, cgst: half, sgst: half, totalGst: round2(half + half) }
}

function extractSlabTaxExclusive(basis, gstRate) {
  const taxable = round2(basis)
  if (taxable <= 0 || gstRate <= 0) return { taxable, cgst: 0, sgst: 0, totalGst: 0 }
  const halfRate = gstRate / 2
  const cgst = round2(taxable * halfRate / 100)
  const sgst = round2(taxable * halfRate / 100)
  return { taxable, cgst, sgst, totalGst: round2(cgst + sgst) }
}

function extractSlabTaxes(slabs, slabBasis, rows) {
  const bySlab = {}
  for (const row of rows) {
    const key = String(round2(row.gstPct))
    if (!bySlab[key]) bySlab[key] = []
    bySlab[key].push(row)
  }
  const breakdown = []
  for (const key of Object.keys(slabBasis).sort((a, b) => parseFloat(a) - parseFloat(b))) {
    const basisTotal = slabBasis[key]
    const grp = bySlab[key] || []
    let incNet = 0
    let excNet = 0
    for (const row of grp) {
      if (row.inclusive) incNet = round2(incNet + row.net)
      else excNet = round2(excNet + row.net)
    }
    const slabNet = round2(incNet + excNet) || slabs[key] || 0
    const incBasis = slabNet > 0 ? round2(basisTotal * incNet / slabNet) : 0
    const excBasis = round2(basisTotal - incBasis)
    const gstRate = parseFloat(key)
    let taxable = 0
    let cgst = 0
    let sgst = 0
    let totalGst = 0
    if (incBasis > 0) {
      const t = extractSlabTaxInclusive(incBasis, gstRate)
      taxable += t.taxable
      cgst += t.cgst
      sgst += t.sgst
      totalGst += t.totalGst
    }
    if (excBasis > 0) {
      const t = extractSlabTaxExclusive(excBasis, gstRate)
      taxable += t.taxable
      cgst += t.cgst
      sgst += t.sgst
      totalGst += t.totalGst
    }
    breakdown.push({ gstPct: gstRate, taxable: round2(taxable), cgst: round2(cgst), sgst: round2(sgst), totalGst: round2(totalGst) })
  }
  return breakdown
}

export function computePurchaseInvoice(items, globalDisc = 0, rounding = 0, gstCalcMethod = 'discount_after_gst') {
  const billInclusive = gstMethodToTaxMode(gstCalcMethod) === 'inclusive'
  const rows = evaluateLineItems(items, billInclusive)
  const { slabs, slabDisc, slabBasis, totalGross } = allocateSlabDiscounts(rows, globalDisc)
  const taxRows = extractSlabTaxes(slabs, slabBasis, rows)

  let taxableTotal = 0
  let totalCgst = 0
  let totalSgst = 0
  let totalGst = 0
  for (const row of taxRows) {
    taxableTotal = round2(taxableTotal + row.taxable)
    totalCgst = round2(totalCgst + row.cgst)
    totalSgst = round2(totalSgst + row.sgst)
    totalGst = round2(totalGst + row.totalGst)
  }

  const hasExclusive = rows.some(r => !r.inclusive)
  const hasInclusive = rows.some(r => r.inclusive)
  let preRound
  if (hasInclusive && !hasExclusive) {
    preRound = round2(totalGross - globalDisc)
  } else {
    preRound = round2(taxableTotal + totalGst)
  }

  let roundOff = parseFloat(rounding) || 0
  let totalAmount = round2(preRound + roundOff)
  if (!roundOff) {
    const rounded = round2(Math.round(preRound))
    roundOff = round2(rounded - preRound)
    totalAmount = round2(preRound + roundOff)
  }

    const lines = rows.map((row, idx) => {
    const key = String(round2(row.gstPct))
    const slabGross = slabs[key] || 0
    const share = slabGross > 0 ? row.net / slabGross : 0
    const taxRow = taxRows.find(t => String(round2(t.gstPct)) === key) || { taxable: 0, totalGst: 0 }
    let itemTaxable = round2(taxRow.taxable * share)
    let itemGst = round2(taxRow.totalGst * share)
    const itemDisc = round2((slabDisc[key] || 0) * share)
    if (row.net > 0 && itemTaxable <= 0 && itemGst <= 0) {
      itemTaxable = round2(row.net - itemDisc)
    }
    return {
      taxable: itemTaxable,
      gstAmt: itemGst,
      itemDiscount: itemDisc,
      total: round2(itemTaxable + itemGst),
      idx,
    }
  })

  return {
    grossSubtotalNoGST: totalGross,
    subtotalNoGST: taxableTotal,
    totalGST: totalGst,
    cgst: totalCgst,
    sgst: totalSgst,
    discountAmount: round2(globalDisc),
    preRoundTotal: preRound,
    rounding: roundOff,
    totalAmount,
    slabBreakdown: taxRows,
    lines,
  }
}

export function calcPurchaseSummary(items, overallDiscount = 0, rounding = 0, gstCalcMethod = 'discount_after_gst') {
  return computePurchaseInvoice(items, overallDiscount, rounding, gstCalcMethod)
}

export function calcItemAmount(qty, rate, discountPct = 0) {
  return round2(qty * rate * (1 - discountPct / 100))
}

export function calcItemGST(qty, rate, gstPct, itemDiscountPct = 0) {
  const summary = computePurchaseInvoice(
    [{ qty, rate, item_discount: itemDiscountPct, gst_percent: gstPct }],
    0, 0, 'discount_before_gst',
  )
  return summary.totalGST
}

export function calcAmount(item) {
  return calcItemAmount(
    parseFloat(item.qty)  || 0,
    parseFloat(item.rate) || 0,
    parseFloat(item.item_discount) || 0
  )
}

export function calcGST(item) {
  return calcItemGST(
    parseFloat(item.qty)  || 0,
    parseFloat(item.rate) || 0,
    parseFloat(item.gst_percent) || 0,
    parseFloat(item.item_discount) || 0
  )
}

export function autoRound(amount) {
  const rounded = Math.floor(amount + 0.5)
  return round2(rounded - amount)
}
