"""
Generate expiry.dat — today + 5 days demo expiry.
Run before every EXE build.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from datetime import date, timedelta
from core.license_manager import write_expiry, _expiry_path

expiry_date = str(date.today() + timedelta(days=5))
write_expiry(expiry_date, enabled=True)

path = _expiry_path()
print(f"expiry.dat written to: {path}")
print(f"Today      : {date.today()}")
print(f"Expires on : {expiry_date}  (5 days from today)")
