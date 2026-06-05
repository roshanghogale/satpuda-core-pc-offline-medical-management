"""
setup_store_backup.py
---------------------
Run this on the STORE'S MACHINE at activation time (before or after activation).
Writes encrypted backup_config.dat to AppData so backups start immediately.

Usage:
    python setup_store_backup.py <folder_id> <store_name>

Example:
    python setup_store_backup.py 1ABC123xyz "Satpuda Vet Nagpur"
"""

import sys

def main():
    if len(sys.argv) < 3:
        print("Usage: python setup_store_backup.py <folder_id> <store_name>")
        print('Example: python setup_store_backup.py 1ABC123xyz "Satpuda Vet Nagpur"')
        sys.exit(1)

    folder_id  = sys.argv[1].strip()
    store_name = sys.argv[2].strip()

    from core.backup_manager import write_bundled_backup_config
    write_bundled_backup_config(folder_id, store_name)
    print("backup_config.dat written for EXE bundle (config/backup_config.dat).")
    print(f"  Store : {store_name}")
    print(f"  Folder: {folder_id}")
    print("Rebuild the EXE so this store is embedded. Backups work on any PC after that.")

if __name__ == '__main__':
    main()
