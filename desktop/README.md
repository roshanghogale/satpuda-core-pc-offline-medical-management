# Satpuda Core — desktop UI (Tauri)

Offline Win10/11 shell. Local Python data engine starts **with the window** (127.0.0.1 only).

The classic Tk app (`python main.py`) remains the full production UI until screens are ported.

## Run

```bash
cd desktop
npm install
npm run tauri:dev
```

No separate API terminal. Close the window to stop the local engine.

## Notes

- Engine script: repo-root `run_desktop_api.py`
- Startup log: `config/desktop_api.log`
- Details: see `../DESKTOP_UI.md`
