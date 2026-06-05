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
      const summary = calcPurchaseSummary(items, discount)

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
        gst_calc_method:  bill.gst_calc_method  || 'discount_before_gst',
        overall_discount: discount,
        overall_discount_pct: parseFloat(bill.overall_discount_pct) || 0,
        amount_paid:      parseFloat(bill.amount_paid)      || 0,
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
          gst_amount:         summary.lines[idx]?.gstAmt ?? round2(calcGST(it)),
          hsn_code:           it.hsn_code           || '',
          manufacturer:       it.manufacturer       || '',
          schedule:           it.schedule           || '',
          content_drug:       it.content_drug       || '',
          item_discount:      parseFloat(it.item_discount)   || 0,
          quantity_value:     it.quantity_value     || '1',
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
    gst_calc_method: 'discount_before_gst',
    overall_discount: 0, overall_discount_pct: 0, amount_paid: 0,
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

// ── Centralized Calculation Engine (mirrors core/calc_engine.py) ─────────────

// Half-up rounding to 2 decimal places
export function round2(x) {
  const s = (x * 100).toFixed(10)
  return Math.floor(parseFloat(s) + 0.5) / 100
}

// Item-level
export function calcItemAmount(qty, rate, discountPct = 0) {
  return round2(qty * rate * (1 - discountPct / 100))
}

export function calcItemGST(qty, rate, gstPct, itemDiscountPct = 0) {
  return round2(calcItemAmount(qty, rate, itemDiscountPct) * gstPct / 100)
}

// Alias kept for backward compat
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

// Purchase summary (mirrors calc_purchase_summary)
export function calcPurchaseSummary(items, overallDiscount = 0, rounding = 0) {
  const prepared = items.map(it => {
    const base = calcItemAmount(
      parseFloat(it.qty) || 0,
      parseFloat(it.rate) || 0,
      parseFloat(it.item_discount) || 0
    )
    return { item: it, taxableBeforeOverall: base, gstPct: parseFloat(it.gst_percent) || 0 }
  })
  const grossSubtotalNoGST = round2(
    prepared.reduce((sum, row) => sum + row.taxableBeforeOverall, 0)
  )
  const discountAmount = round2(Math.min(Math.max(parseFloat(overallDiscount) || 0, 0), grossSubtotalNoGST))
  let remainingDiscount = discountAmount
  const taxableRows = prepared.filter(row => row.taxableBeforeOverall > 0)
  const lastTaxable = taxableRows[taxableRows.length - 1]
  const lines = []
  let totalGST = 0

  for (const row of prepared) {
    let itemDiscount = 0
    if (grossSubtotalNoGST > 0 && row.taxableBeforeOverall > 0) {
      if (row === lastTaxable) {
        itemDiscount = remainingDiscount
      } else {
        itemDiscount = round2(discountAmount * row.taxableBeforeOverall / grossSubtotalNoGST)
        remainingDiscount = round2(remainingDiscount - itemDiscount)
      }
    }

    const taxable = round2(Math.max(0, row.taxableBeforeOverall - itemDiscount))
    const gstAmt = round2(taxable * row.gstPct / 100)
    totalGST = round2(totalGST + gstAmt)
    lines.push({ taxable, gstAmt, itemDiscount, total: round2(taxable + gstAmt) })
  }
  const subtotalNoGST = round2(grossSubtotalNoGST - discountAmount)
  const totalAmount = round2(subtotalNoGST + totalGST + (parseFloat(rounding) || 0))
  return {
    grossSubtotalNoGST,
    subtotalNoGST,
    totalGST:      round2(totalGST),
    cgst:          round2(totalGST / 2),
    sgst:          round2(totalGST / 2),
    discountAmount: round2(discountAmount),
    totalAmount,
    lines,
  }
}

// Auto-rounding (mirrors auto_round)
export function autoRound(amount) {
  const rounded = Math.floor(amount + 0.5)
  return round2(rounded - amount)
}
