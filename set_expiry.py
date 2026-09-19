"""
set_expiry.py  -  expiry.dat, which is NO LONGER the licence
------------------------------------------------------------
READ THIS BEFORE USING IT. This tool writes ``expiry.dat``, and ``expiry.dat``
stopped deciding anything. The licence a shop is actually held to is the SIGNED
blob in ``license.seal``, issued by the Satpuda server and verified against a
public key compiled into the build -- see core/license_seal.py. Writing a date
here changes what some Settings screens display and changes NOTHING about when
the till stops.

That is deliberate, and it is the point of the change: a date that a file on the
shop's own PC could set was a date the shop could set for itself. So there is no
local way to extend a licence any more, and this tool is not a back door into
one.

TO ACTUALLY MOVE AN EXPIRY, one of two things:

  * the admin panel on the server -- change the store's expiry date there, then
    have the shop open the software with the internet connected. The app
    re-fetches a licence that says no before it turns the shop away, so one
    connection is enough; or
  * the software's own Administrator settings, which ask for the Satpuda
    administrator username and password, write the server record and the signed
    blob in the same call, and refuse the edit outright if the server cannot be
    reached.

What this tool is still good for is READING what a machine has
(``python set_expiry.py read``) while diagnosing something.

Run from the project root:

  Set expiry date:
      python set_expiry.py set 2025-12-31

  Disable expiry (app runs forever):
      python set_expiry.py disable

  Enable expiry (re-enable after disable):
      python set_expiry.py enable

  Read current expiry:
      python set_expiry.py read

  Set expiry to N days from today:
      python set_expiry.py days 30
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.license_manager import write_expiry, _read_expiry, all_expiry_paths
from datetime import date, timedelta


def _show_current():
    data = _read_expiry()
    paths = all_expiry_paths()
    if not data:
        print("  expiry.dat : NOT FOUND")
        for p in paths:
            print("    - " + p)
        print("  Status     : No restriction - app runs forever")
        return
    enabled = data.get('enabled', False)
    exp     = data.get('expiry_date', 'unknown')
    print("  expiry.dat locations:")
    for p in paths:
        mark = " (exists)" if os.path.exists(p) else " (missing)"
        print("    - " + p + mark)
    print("  Enabled    : " + str(enabled))
    print("  Expiry Date: " + str(exp))
    if enabled:
        try:
            exp_date = date.fromisoformat(exp)
            today    = date.today()
            diff     = (exp_date - today).days
            if diff > 0:
                print("  Status     : ACTIVE - " + str(diff) + " day(s) until expiry check on " + exp)
            elif diff == 0:
                print("  Status     : EXPIRES TODAY - activation required on next startup")
            else:
                print("  Status     : Past expiry date - no activation (only triggers on expiry day itself)")
        except Exception:
            print("  Status     : Invalid date format")
    else:
        print("  Status     : Disabled - app runs forever")


def main():
    args = sys.argv[1:]

    if not args or args[0] == 'read':
        print("\n-- Current Expiry Status ----------------------------------")
        _show_current()
        print()
        return

    cmd = args[0].lower()

    if cmd == 'set':
        if len(args) < 2:
            print("Usage: python set_expiry.py set YYYY-MM-DD")
            sys.exit(1)
        exp_str = args[1].strip()
        try:
            exp_date = date.fromisoformat(exp_str)
        except ValueError:
            print("ERROR: Invalid date '" + exp_str + "'. Use YYYY-MM-DD format (e.g. 2025-12-31)")
            sys.exit(1)
        write_expiry(exp_str, enabled=True)
        diff = (exp_date - date.today()).days
        print("\n  Expiry set to: " + exp_str)
        if diff > 0:
            print("  App will run for " + str(diff) + " more day(s).")
        elif diff == 0:
            print("  WARNING: Expiry is TODAY - app will be blocked on next startup.")
        else:
            print("  WARNING: Date is " + str(abs(diff)) + " day(s) in the PAST - app is already blocked.")
        print()

    elif cmd == 'days':
        if len(args) < 2:
            print("Usage: python set_expiry.py days N")
            sys.exit(1)
        try:
            n = int(args[1])
        except ValueError:
            print("ERROR: '" + args[1] + "' is not a valid number.")
            sys.exit(1)
        exp_date = date.today() + timedelta(days=n)
        exp_str  = str(exp_date)
        write_expiry(exp_str, enabled=True)
        print("\n  Expiry set to: " + exp_str + "  (" + str(n) + " days from today)")
        print()

    elif cmd == 'disable':
        data    = _read_expiry()
        exp_str = data.get('expiry_date', str(date.today()))
        write_expiry(exp_str, enabled=False)
        print("\n  Expiry DISABLED - app will run forever (no date restriction).")
        print()

    elif cmd == 'enable':
        data    = _read_expiry()
        exp_str = data.get('expiry_date', str(date.today() + timedelta(days=30)))
        write_expiry(exp_str, enabled=True)
        print("\n  Expiry ENABLED - expiry date: " + exp_str)
        print()

    else:
        print(__doc__)
        sys.exit(1)

    print("-- Updated Status -----------------------------------------")
    _show_current()
    print()


if __name__ == '__main__':
    main()
