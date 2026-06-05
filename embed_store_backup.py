"""
embed_store_backup.py
---------------------
Run before building the EXE to bake store backup settings into config/backup_config.dat.

Edit config/store_backup.build (see store_backup.build.example), then:
    python embed_store_backup.py

Or pass arguments:
    python embed_store_backup.py <folder_id> <store_name>
"""
import os
import sys

_BUILD_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           'config', 'store_backup.build')


def _load_build_file():
    if not os.path.isfile(_BUILD_FILE):
        return None, None
    folder_id = ''
    store_name = ''
    with open(_BUILD_FILE, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            if '=' in line:
                key, _, val = line.partition('=')
                key = key.strip().lower()
                val = val.strip().strip('"').strip("'")
                if key in ('folder_id', 'folderid', 'drive_folder_id'):
                    folder_id = val
                elif key in ('store_name', 'storename', 'name'):
                    store_name = val
            elif not folder_id:
                folder_id = line
            elif not store_name:
                store_name = line
    return folder_id, store_name


def main():
    if len(sys.argv) >= 3:
        folder_id = sys.argv[1].strip()
        store_name = sys.argv[2].strip()
    else:
        folder_id, store_name = _load_build_file()

    if not folder_id or not store_name:
        print("Store backup settings missing.")
        print()
        print("Option A — edit config/store_backup.build (copy from store_backup.build.example)")
        print("Option B — python embed_store_backup.py <folder_id> \"Store Name\"")
        sys.exit(1)

    from core.backup_manager import write_bundled_backup_config
    write_bundled_backup_config(folder_id, store_name)
    print(f"  Store : {store_name}")
    print(f"  Folder: {folder_id}")
    print("Ready to build EXE — backup config will be included automatically.")


if __name__ == '__main__':
    main()
