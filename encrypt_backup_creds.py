"""
encrypt_backup_creds.py
-----------------------
Run this ONCE on your machine after downloading service_account.json from Google Cloud.
Produces config/backup_creds.dat — bundle this with the app.

Usage:
    python encrypt_backup_creds.py path/to/service_account.json
"""

import sys
import os
import hashlib
import base64

_SECRET = b"Vm9ldGVyaW5hcnlBcHBTZWNyZXRLZXkyMDI2IQ=="

def encrypt_file(src: str, dst: str):
    from cryptography.fernet import Fernet
    raw = hashlib.sha256(_SECRET).digest()
    key = base64.urlsafe_b64encode(raw)
    f   = Fernet(key)
    with open(src, 'rb') as fp:
        data = fp.read()
    encrypted = f.encrypt(data)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    with open(dst, 'wb') as fp:
        fp.write(encrypted)
    print(f"Done. Encrypted credentials saved to: {dst}")

if __name__ == '__main__':
    src = sys.argv[1] if len(sys.argv) > 1 else 'service_account.json'
    dst = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       'config', 'backup_creds.dat')
    if not os.path.exists(src):
        print(f"ERROR: {src} not found.")
        sys.exit(1)
    encrypt_file(src, dst)
