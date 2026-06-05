"""
generate_oauth_token.py
-----------------------
Run this ONCE on YOUR machine to authorize your personal Gmail
and generate config/backup_creds.dat for the app.

Steps:
1. Go to console.cloud.google.com
2. APIs & Services -> Credentials -> Create Credentials -> OAuth 2.0 Client ID
3. Application type: Desktop app -> Name: SatpudaBackup -> Create
4. Download the JSON -> save as oauth_client.json in this folder
5. Run: python generate_oauth_token.py
6. Browser opens -> sign in with your Gmail -> Allow access
7. config/backup_creds.dat is created -> bundle this with the app

You only need to do this ONCE. The refresh token never expires
as long as you don't revoke access.
"""

import os
import sys
import json

def main():
    client_file = 'oauth_client.json'
    if not os.path.exists(client_file):
        print(f"ERROR: {client_file} not found.")
        print("Download it from Google Cloud Console:")
        print("  APIs & Services -> Credentials -> OAuth 2.0 Client IDs -> Download JSON")
        print(f"  Save it as {client_file} in this folder.")
        sys.exit(1)

    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        import subprocess
        subprocess.check_call([sys.executable, '-m', 'pip', 'install',
                               'google-auth-oauthlib'])
        from google_auth_oauthlib.flow import InstalledAppFlow

    SCOPES = ['https://www.googleapis.com/auth/drive']

    print("Opening browser for Google authorization...")
    print("Sign in with YOUR personal Gmail and click Allow.")
    print()

    flow = InstalledAppFlow.from_client_secrets_file(client_file, SCOPES)
    creds = flow.run_local_server(port=0)

    token_data = {
        'token':         creds.token,
        'refresh_token': creds.refresh_token,
        'client_id':     creds.client_id,
        'client_secret': creds.client_secret,
    }

    dst = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       'config', 'backup_creds.dat')
    os.makedirs(os.path.dirname(dst), exist_ok=True)

    with open(dst, 'wb') as f:
        # Use the same encryption path as backup_manager so the app can decrypt it.
        from core.backup_manager import _encrypt_dict
        f.write(_encrypt_dict(token_data))

    print(f"Done! backup_creds.dat saved to: {dst}")
    print()
    print("This file contains your encrypted OAuth2 refresh token.")
    print("Bundle it with every EXE build — it never expires.")

if __name__ == '__main__':
    main()
