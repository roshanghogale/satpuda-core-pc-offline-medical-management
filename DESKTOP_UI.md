# Modern desktop UI (Tauri) — how to run

Offline desktop app: the window starts a **local** Python data engine on `127.0.0.1` (same machine only). This is not a public web server — same idea as the classic EXE talking to its own SQLite DB.

Classic Tk app (`python main.py`) remains available; the Tauri desktop UI targets feature parity for daily billing work.

## Install once

1. Rust (`rustc --version` / `cargo --version`)
2. Node.js 18+
3. **Visual Studio 2022 Build Tools** with C++ workload — required for `link.exe`
4. Project Python deps (`python` or `py -3` on PATH)

```bash
cd desktop
npm install
```

## Every day (one command)

```bash
cd desktop
npm run tauri:dev
```

Or double-click / run: `scripts\run_desktop_ui.bat`

The shell auto-starts `run_desktop_api.py` and stops it when you close the window. You do **not** need a second terminal.

Optional log if startup fails: `config/desktop_api.log`

Tk app still works:

```bash
python main.py
```

## Keyboard — main navigation

| Key | Page |
|-----|------|
| `` ` `` or `0` | Home |
| `1` | Sales (Billing) |
| `2` | Purchase |
| `3` | Inventory |
| `4` | Sales History |
| `5` | Purchase History |
| `6` | Returns |
| `7` | Settings |

## Home letter shortcuts

| Key | Action |
|-----|--------|
| `B` | New Bill → Sales |
| `P` | New Purchase |
| `I` | Inventory |
| `E` / `Ctrl+E` | Export menu dialog |

## Settings

- **Ctrl+1 … Ctrl+0** — jump to settings tabs (Pharmacy, Contacts, … Ledger)
- **Ctrl+Tab / Ctrl+Shift+Tab** — next / previous settings tab
- **F4** — focus section sidebar
- **Alt+1…N** — jump to section on sectioned tabs
- **⌨ Shortcuts** tab — full cheatsheet (same content as classic Tk)

## Full shortcut reference

Open **Settings → ⌨ Shortcuts** for the complete list (Sales F-keys, multi-tab, Reorder, Returns, Payment, etc.).

## Build release folder

See `BUILD_COMMANDS.txt` — desktop bundle requires **API revision 25+** on the embedded Python engine.
