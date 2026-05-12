"""
import_medicines_master.py
─────────────────────────
One-time import: reads medicines_master_with_cdsco.xlsx and loads it into
the 'medicines_master' table in veterinary.db.

Run once from the project root:
    python import_medicines_master.py

Safe to re-run — it drops and recreates the table each time.
"""
import os
import sys
import sqlite3
import re

try:
    import openpyxl
except ImportError:
    print("ERROR: openpyxl not installed.  Run:  pip install openpyxl")
    sys.exit(1)

# ── Paths ─────────────────────────────────────────────────────────────────────
BASE   = os.path.dirname(os.path.abspath(__file__))
XLSX   = os.path.join(BASE, 'assets', 'medicines_master_with_cdsco.xlsx')
DB     = os.path.join(BASE, 'veterinary.db')

if not os.path.exists(XLSX):
    print(f"ERROR: Excel file not found:\n  {XLSX}")
    sys.exit(1)

# ── Type detection from medicine name ─────────────────────────────────────────
_TYPE_KEYWORDS = [
    ('Tablet',    'Tablet'),
    ('Capsule',   'Capsule'),
    ('Syrup',     'Syrup'),
    ('Injection', 'Injection'),
    ('Ointment',  'Ointment'),
    ('Cream',     'Ointment'),
    ('Gel',       'Gel'),
    ('Drops',     'Syrup'),
    ('Powder',    'Powder'),
    ('Spray',     'Syrup'),
    ('Inhaler',   'Injection'),
    ('Solution',  'Syrup'),
    ('Suspension','Syrup'),
    ('Liniment',  'Liniment'),
    ('Bolus',     'Bolus'),
    ('Vaccine',   'Vaccine'),
]

def _detect_type(name: str, dosage_form: str) -> str:
    text = f"{name or ''} {dosage_form or ''}".lower()
    for kw, med_type in _TYPE_KEYWORDS:
        if kw.lower() in text:
            return med_type
    return 'Other'

# ── Load Excel ─────────────────────────────────────────────────────────────────
print(f"Loading Excel file …  (this may take 30–60 seconds for 387k rows)")
wb   = openpyxl.load_workbook(XLSX, read_only=True)
ws   = wb.active
rows = list(ws.iter_rows(values_only=True))
wb.close()

headers = rows[0]
# Col indices: 0=Name, 1=Manufacturer, 10=MRP, 11=Salt/Content, 16=DosageForm, 17=PackSize
print(f"Loaded {len(rows)-1:,} rows.  Importing into SQLite …")

# ── SQLite ─────────────────────────────────────────────────────────────────────
conn = sqlite3.connect(DB)
cur  = conn.cursor()

cur.execute("DROP TABLE IF EXISTS medicines_master")
cur.execute("""
    CREATE TABLE medicines_master (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        name         TEXT NOT NULL,
        manufacturer TEXT,
        mrp          REAL,
        content_drug TEXT,
        med_type     TEXT,
        pack_size    TEXT
    )
""")
cur.execute("CREATE INDEX IF NOT EXISTS idx_mm_name ON medicines_master(name COLLATE NOCASE)")

BATCH = 5000
batch = []
skipped = 0

for i, r in enumerate(rows[1:], 1):
    name = str(r[0]).strip() if r[0] else ''
    if not name or name.lower() == 'none':
        skipped += 1
        continue

    manufacturer = str(r[1]).strip()  if r[1]  else ''
    mrp_raw      = r[10]
    try:
        mrp = float(mrp_raw) if mrp_raw is not None else 0.0
    except (ValueError, TypeError):
        mrp = 0.0

    # Salt / content — clean up "nan" artifacts
    salt = str(r[11]).strip() if r[11] else ''
    salt = re.sub(r'\s*\+\s*nan\s*', '', salt).strip().strip('+').strip()

    dosage_form = str(r[16]).strip() if r[16] else ''
    pack_size   = str(r[17]).strip() if r[17] else ''
    med_type    = _detect_type(name, dosage_form)

    batch.append((name, manufacturer, mrp, salt, med_type, pack_size))

    if len(batch) >= BATCH:
        cur.executemany(
            "INSERT INTO medicines_master (name,manufacturer,mrp,content_drug,med_type,pack_size) VALUES (?,?,?,?,?,?)",
            batch)
        batch.clear()
        if i % 50000 == 0:
            print(f"  … {i:,} rows processed")

if batch:
    cur.executemany(
        "INSERT INTO medicines_master (name,manufacturer,mrp,content_drug,med_type,pack_size) VALUES (?,?,?,?,?,?)",
        batch)

conn.commit()

cur.execute("SELECT COUNT(*) FROM medicines_master")
total = cur.fetchone()[0]
conn.close()

print(f"\n✔  Done!  {total:,} medicines imported  ({skipped} skipped).")
print(f"   Database: {DB}")
print("\nNow restart the app — the Purchase page will auto-suggest from this master list.")
