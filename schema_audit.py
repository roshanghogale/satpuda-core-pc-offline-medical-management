"""
schema_audit.py
Run: python schema_audit.py
Checks DB init schema vs live DB, and every page query column access.
"""
import sqlite3, re, sys
sys.path.insert(0, '.')

conn = sqlite3.connect('veterinary.db')
cur  = conn.cursor()

def live_cols(t):
    cur.execute(f'PRAGMA table_info({t})')
    return [r[1] for r in cur.fetchall()]

SALES = live_cols('sales')
SI    = live_cols('sales_items')
PURCH = live_cols('purchases')
PI    = live_cols('purchase_items')
CUST  = live_cols('customers')
SUP   = live_cols('suppliers')
MED   = live_cols('medicines')
CP    = live_cols('customer_payments')
SP    = live_cols('supplier_payments')

passes = 0
fails  = 0

def ok(msg):
    global passes
    passes += 1
    print(f'  PASS  {msg}')

def fail(msg):
    global fails
    fails += 1
    print(f'  FAIL  {msg}')

def section(title):
    print(f'\n=== {title} ===')

# ── 1. _TABLES vs live DB ─────────────────────────────────────────────────────
section('DB INIT: _TABLES vs live DB')

from core.db_setup import _TABLES

def extract_defined_cols(sql):
    m = re.search(r'CREATE TABLE IF NOT EXISTS (\w+)', sql)
    if not m: return None, []
    tname = m.group(1)
    depth=0; start=-1; end=-1
    for i, ch in enumerate(sql):
        if ch == '(':
            if depth == 0: start = i
            depth += 1
        elif ch == ')':
            depth -= 1
            if depth == 0: end = i; break
    inner = sql[start+1:end]
    cols = []
    for part in inner.split(','):
        p = part.strip()
        if not p: continue
        up = p.upper()
        if up.startswith('FOREIGN KEY') or up.startswith('UNIQUE') or up.startswith('CHECK'):
            continue
        w = p.split()[0]
        if w: cols.append(w)
    return tname, cols

defined = {}
for sql in _TABLES:
    t, c = extract_defined_cols(sql)
    if t: defined[t] = c

for tname, live in [
    ('customers',     CUST),
    ('suppliers',     SUP),
    ('sales',         SALES),
    ('purchases',     PURCH),
    ('purchase_items',PI),
    ('sales_items',   SI),
    ('medicines',     MED),
]:
    defn = defined.get(tname, [])
    if not defn:
        print(f'  SKIP  {tname} not in _TABLES')
        continue
    extra_live = [c for c in live if c not in defn]
    extra_defn = [c for c in defn if c not in live]
    if extra_live or extra_defn:
        fail(f'_TABLES {tname}: live_only={extra_live}  defn_only={extra_defn}')
    else:
        ok(f'_TABLES {tname} ({len(live)} cols match)')

# ── 2. billing_service.py: save_new_bill INSERT ───────────────────────────────
section('billing_service.py: save_new_bill INSERT into sales')
for c in ['bill_no','customer_id','bill_date','total_amount','discount','discount_pct',
          'rounding','amount_paid','cash_paid','online_paid','doctor_name',
          'previous_due','previous_credit','due_amount','credit_amount',
          'total_due','bill_cleared','account_cleared']:
    if c in SALES: ok(f'sales.{c}')
    else: fail(f'sales.{c} MISSING')

# ── 3. billing_service.py: update_existing_bill UPDATE ───────────────────────
section('billing_service.py: update_existing_bill UPDATE sales')
for c in ['total_amount','discount','discount_pct','rounding','amount_paid',
          'cash_paid','online_paid','doctor_name','due_amount','credit_amount',
          'total_due','bill_cleared']:
    if c in SALES: ok(f'sales.{c}')
    else: fail(f'sales.{c} MISSING')

# ── 4. billing_service.py: _insert_items_and_update_stock ────────────────────
section('billing_service.py: _insert_items_and_update_stock INSERT into sales_items')
for c in ['sale_id','medicine_id','qty','rate','gst_percent','amount',
          'item_discount','cost_price']:
    if c in SI: ok(f'sales_items.{c}')
    else: fail(f'sales_items.{c} MISSING')

# ── 5. bill_edit.py: _load_sale_data SELECT positions ────────────────────────
section('bill_edit.py: _load_sale_data SELECT result positions')
# Query returns 18 columns (0-indexed):
# [0]s.id [1]s.bill_no [2]s.customer_id [3]s.bill_date [4]s.total_amount
# [5]s.discount [6]s.amount_paid [7]cash_paid [8]online_paid
# [9]s.previous_due [10]s.total_due [11]s.due_amount [12]s.credit_amount
# [13]s.doctor_name [14]s.created_at [15]c.name [16]c.phone
# [17]COALESCE(s.previous_credit,0)
query_result = [
    's.id','s.bill_no','s.customer_id','s.bill_date','s.total_amount',
    's.discount','s.amount_paid','s.cash_paid','s.online_paid',
    's.previous_due','s.total_due','s.due_amount','s.credit_amount',
    's.doctor_name','s.created_at','c.name','c.phone','s.previous_credit'
]
code_reads = {
    9:  ('previous_due',   's.previous_due'),
    17: ('previous_credit','s.previous_credit'),
    15: ('c.name',         'c.name'),
    16: ('c.phone',        'c.phone'),
    13: ('doctor_name',    's.doctor_name'),
    5:  ('discount',       's.discount'),
    7:  ('cash_paid',      's.cash_paid'),
    8:  ('online_paid',    's.online_paid'),
}
for idx, (label, expected_col) in code_reads.items():
    actual = query_result[idx] if idx < len(query_result) else 'OUT_OF_RANGE'
    bare_exp = expected_col.replace('s.','').replace('c.','')
    bare_act = actual.replace('s.','').replace('c.','')
    if bare_exp == bare_act:
        ok(f'_load_sale_data[{idx}] = {label}')
    else:
        fail(f'_load_sale_data[{idx}] expected={label} got={actual}')

# ── 6. bill_edit.py: sales_items SELECT (si.*) positions ─────────────────────
section('bill_edit.py: sales_items si.* SELECT positions')
# si.* expands to SI columns [0..8], then appended:
# [9]m.name [10]m.batch_no [11]m.expiry_date [12]m.type [13]m.schedule
# [14]m.gst_percent [15]COALESCE(si.item_discount,0)
full_si = SI + ['m.name','m.batch_no','m.expiry_date','m.type','m.schedule',
                'm.gst_percent','COALESCE(si.item_discount,0)']
si_reads = {
    2:  ('medicine_id',       'medicine_id'),
    9:  ('name',              'm.name'),
    10: ('batch',             'm.batch_no'),
    11: ('expiry',            'm.expiry_date'),
    3:  ('qty',               'qty'),
    4:  ('rate',              'rate'),
    6:  ('amount',            'amount'),
    13: ('schedule',          'm.schedule'),
    12: ('type',              'm.type'),
    14: ('gst_percent',       'm.gst_percent'),
    15: ('medicine_discount', 'COALESCE(si.item_discount,0)'),
}
for idx, (label, expected_col) in si_reads.items():
    actual = full_si[idx] if idx < len(full_si) else 'OUT_OF_RANGE'
    bare_exp = expected_col.replace('m.','').replace('si.','').split('(')[-1].split(',')[0]
    bare_act = str(actual).replace('m.','').replace('si.','').split('(')[-1].split(',')[0]
    if bare_exp == bare_act:
        ok(f'si_items[{idx}] = {label}')
    else:
        fail(f'si_items[{idx}] expected={label}({expected_col}) got={actual}')

# ── 7. sales_history_actions.py: view_bill_details ───────────────────────────
section('sales_history_actions.py: view_bill_details sale_row positions')
# sales_data rows from load_sales_history (17 cols, 0-indexed):
# [0]bill_date [1]c.name [2]c.phone [3]total_amount [4]discount [5]amount_paid
# [6]cash_paid [7]online_paid [8]previous_due [9]due_amount [10]credit_amount
# [11]total_due [12]bill_cleared [13]account_cleared [14]bill_no [15]s.id [16]doctor_name
sh_query = ['bill_date','c.name','c.phone','total_amount','discount','amount_paid',
            'cash_paid','online_paid','previous_due','due_amount','credit_amount',
            'total_due','bill_cleared','account_cleared','bill_no','s.id','doctor_name']
sh_reads = {15: ('sale_id match', 's.id'), 16: ('doctor', 'doctor_name'),
            6:  ('cash_paid',     'cash_paid'), 7: ('online_paid', 'online_paid')}
for idx, (label, expected_col) in sh_reads.items():
    actual = sh_query[idx] if idx < len(sh_query) else 'OUT_OF_RANGE'
    bare_exp = expected_col.replace('s.','')
    bare_act = actual.replace('s.','').replace('c.','')
    if bare_exp == bare_act:
        ok(f'sale_row[{idx}] = {label}')
    else:
        fail(f'sale_row[{idx}] expected={label}({expected_col}) got={actual}')

# ── 8. purchase_history_edit.py: _load_for_edit ──────────────────────────────
section('purchase_history_edit.py: _load_for_edit')
# Supplier query: name[0] address[1] phone[2] gstin[3] dl_numbers[4]
for i, c in enumerate(['name','address','phone','gstin','dl_numbers']):
    if c in SUP: ok(f'supplier query[{i}]={c}')
    else: fail(f'supplier query[{i}]={c} MISSING from suppliers')

# Purchase header query named cols — all must exist in purchases
for c in ['bill_number','overall_discount','rounding','amount_paid_at_entry',
          'previous_due','previous_credit','due','current_credit','purchase_date']:
    if c in PURCH: ok(f'purchases.{c}')
    else: fail(f'purchases.{c} MISSING')

# Purchase items query result positions:
# [0]medicine_id [1]m.name [2]type [3]batch_no [4]expiry_date [5]qty [6]free_qty
# [7]rate [8]gst_pct [9]mrp [10]manufacturer [11]schedule [12]item_amount
# [13]hsn_code [14]discount_pct
pi_q = ['medicine_id','m.name','type','batch_no','expiry_date','qty','free_qty',
        'rate','gst_pct','mrp','manufacturer','schedule','item_amount',
        'hsn_code','discount_pct']
pi_reads = {0:'medicine_id',1:'m.name',2:'type',3:'batch_no',4:'expiry_date',
            5:'qty',6:'free_qty',7:'rate',8:'gst_pct',9:'mrp',10:'manufacturer',
            11:'schedule',12:'item_amount',13:'hsn_code',14:'discount_pct'}
for idx, expected in pi_reads.items():
    actual = pi_q[idx] if idx < len(pi_q) else 'OUT_OF_RANGE'
    bare_exp = expected.replace('m.','')
    bare_act = actual.replace('m.','')
    if bare_exp == bare_act:
        ok(f'purchase_items query[{idx}]={expected}')
    else:
        fail(f'purchase_items query[{idx}] expected={expected} got={actual}')

# ── 9. customer_payment_tab.py ────────────────────────────────────────────────
section('customer_payment_tab.py: _on_edit_select SELECT positions')
# SELECT payment_date[0] cash_amount[1] online_amount[2] reference_no[3] note[4]
for i, c in enumerate(['payment_date','cash_amount','online_amount','reference_no','note']):
    if c in CP: ok(f'customer_payments[{i}]={c}')
    else: fail(f'customer_payments[{i}]={c} MISSING')

section('customer_payment_tab.py: _load_history SELECT positions')
# r[5] = cp.amount (total accumulator)
if 'amount' in CP: ok('customer_payments r[5]=amount exists')
else: fail('customer_payments amount MISSING')

# ── 10. purchase_service.py: save_purchase INSERT ────────────────────────────
section('purchase_service.py: save_purchase INSERT into purchases')
for c in ['purchase_no','supplier_id','purchase_date','bill_number',
          'subtotal','total_gst','cgst','sgst','total_amount',
          'overall_discount','rounding','need_to_pay','final_amount',
          'amount_paid','amount_paid_at_entry',
          'previous_due','previous_credit','due','current_credit','total_due',
          'bill_cleared','account_cleared','due_amount','credit_amount']:
    if c in PURCH: ok(f'purchases.{c}')
    else: fail(f'purchases.{c} MISSING')

# ── 11. purchase_service.py: _insert_items INSERT ────────────────────────────
section('purchase_service.py: _insert_items INSERT into purchase_items')
for c in ['purchase_id','medicine_id','qty','free_qty','type',
          'hsn_code','gst_pct','mrp','rate','manufacturer',
          'batch_no','expiry_date','schedule',
          'discount_pct','taxable','gst_amt','item_amount',
          'discount_percent','gst_value','amount']:
    if c in PI: ok(f'purchase_items.{c}')
    else: fail(f'purchase_items.{c} MISSING')

# ── 12. customer_service.py: recalculate_customer_due UPDATE ─────────────────
section('customer_service.py: recalculate_customer_due UPDATE customers')
for c in ['total_due','total_credit','last_updated']:
    if c in CUST: ok(f'customers.{c}')
    else: fail(f'customers.{c} MISSING')

# ── 13. purchase_service.py: recalculate_supplier_due UPDATE ─────────────────
section('purchase_service.py: recalculate_supplier_due UPDATE suppliers')
for c in ['total_due','total_credit']:
    if c in SUP: ok(f'suppliers.{c}')
    else: fail(f'suppliers.{c} MISSING')

# ── 14. _recreate_medicines: explicit SELECT cols ─────────────────────────────
section('db_setup.py: _recreate_medicines explicit SELECT cols')
for c in ['id','name','type','stock_qty','unit','gst_percent','mrp','rate',
          'manufacturer','batch_no','expiry_date','hsn_code','schedule',
          'location','content_drug','created_at']:
    if c in MED: ok(f'medicines.{c} preserved in rebuild')
    else: fail(f'medicines.{c} MISSING — would be lost in rebuild')

# ── 15. sales_history.py: load_sales_history query ───────────────────────────
section('sales_history.py: load_sales_history named columns')
for c in ['bill_date','total_amount','discount','amount_paid','cash_paid',
          'online_paid','previous_due','due_amount','credit_amount',
          'total_due','bill_cleared','account_cleared','bill_no','doctor_name']:
    if c in SALES: ok(f'sales.{c}')
    else: fail(f'sales.{c} MISSING')

# ── 16. purchase_history.py: _base_query named columns ───────────────────────
section('purchase_history.py: _base_query named columns')
for c in ['total_amount','amount_paid_at_entry','account_cleared','purchase_date']:
    if c in PURCH: ok(f'purchases.{c}')
    else: fail(f'purchases.{c} MISSING')

# ── 17. supplier_payments table columns used in payment_tab.py ───────────────
section('payment_tab.py: supplier_payments INSERT columns')
for c in ['payment_no','supplier_id','payment_date','amount','mode',
          'reference','due_before','due_after']:
    if c in SP: ok(f'supplier_payments.{c}')
    else: fail(f'supplier_payments.{c} MISSING')

conn.close()

print(f'\n{"="*50}')
print(f'TOTAL: {passes} PASS  |  {fails} FAIL')
if fails == 0:
    print('ALL CHECKS PASSED')
else:
    print(f'{fails} ISSUE(S) FOUND')
