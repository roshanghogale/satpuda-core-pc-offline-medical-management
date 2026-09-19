//! Satpuda desktop shell — starts the local Python data engine with the window
//! (127.0.0.1 only). Same offline model as the classic EXE; no cloud server.

use std::io::{Read, Write};
use std::net::{SocketAddr, TcpStream};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Mutex;
use std::time::{Duration, Instant};

use tauri::{AppHandle, Manager, RunEvent, State};

const API_HOST: &str = "127.0.0.1";
const API_PORT: u16 = 8765;
/// Must match core.desktop_api.API_REVISION (settings + inventory/history pages).
const REQUIRED_API_REVISION: u32 = 55;

struct ApiState {
    /// Child we spawned (kill on exit). None if engine was already running.
    child: Mutex<Option<Child>>,
    /// Serialize spawn+wait so setup() and JS invoke cannot kill each other.
    boot: Mutex<()>,
}

fn repo_root() -> PathBuf {
    let manifest = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let root = manifest.join("../..");
    root.canonicalize().unwrap_or(root)
}

/// Folder containing SatpudaCore_Desktop.exe (release) or target/debug (dev).
fn app_root() -> PathBuf {
    std::env::current_exe()
        .ok()
        .and_then(|p| p.parent().map(|d| d.to_path_buf()))
        .unwrap_or_else(repo_root)
}

/// %LOCALAPPDATA%\VeterinaryApp — the app's own data folder, and the same one
/// core/frozen_appdata_setup.py:_appdata_dir() resolves, fallback included.
fn app_data_dir() -> PathBuf {
    let base = std::env::var_os("LOCALAPPDATA")
        .map(PathBuf::from)
        .or_else(|| std::env::var_os("HOME").map(PathBuf::from))
        .or_else(|| std::env::var_os("USERPROFILE").map(PathBuf::from))
        .unwrap_or_else(|| PathBuf::from("."));
    base.join("VeterinaryApp")
}

/// Where the engine's stdout/stderr go.
///
/// NOT inside the installed folder, and that is the whole point. This used to
/// be `<engine dir>\config\desktop_api.log`, i.e. inside `app\`, and it cost
/// twice:
///
///   * the shell holds the file open for the engine's entire run, and the
///     updater upgrades by RENAMING `app\` — Windows allows that over a
///     running .exe but not over a file held with no sharing, so an in-place
///     update failed with Access Denied on this one file;
///   * launching the app once on the build machine before zipping put that
///     machine's log inside the folder shipped to every shop.
///
/// %LOCALAPPDATA%\VeterinaryApp is where the store databases already live, it
/// is per-user writable, and the updater never touches it. `root` is still
/// taken so a dev checkout that cannot reach AppData falls back to beside the
/// engine rather than failing to start.
fn engine_log_path(root: &Path) -> PathBuf {
    let dir = app_data_dir().join("logs");
    if std::fs::create_dir_all(&dir).is_ok() {
        return dir.join("desktop_api.log");
    }
    root.join("config").join("desktop_api.log")
}

fn http_get_local(path: &str) -> Option<String> {
    http_get_local_timeout(path, Duration::from_secs(2))
}

fn http_get_local_timeout(path: &str, read_timeout: Duration) -> Option<String> {
    let Ok(addr) = format!("{API_HOST}:{API_PORT}").parse::<SocketAddr>() else {
        return None;
    };
    let mut stream =
        TcpStream::connect_timeout(&addr, Duration::from_millis(800)).ok()?;
    let _ = stream.set_read_timeout(Some(read_timeout));
    let _ = stream.set_write_timeout(Some(Duration::from_secs(2)));
    let req = format!(
        "GET {path} HTTP/1.1\r\nHost: {API_HOST}:{API_PORT}\r\nConnection: close\r\n\r\n"
    );
    stream.write_all(req.as_bytes()).ok()?;
    let mut buf = String::new();
    stream.read_to_string(&mut buf).ok()?;
    let parts: Vec<&str> = buf.splitn(2, "\r\n\r\n").collect();
    if parts.len() < 2 {
        return None;
    }
    Some(parts[1].to_string())
}

fn api_is_current() -> bool {
    let Some(body) = http_get_local("/api/health") else {
        return false;
    };
    // Require numeric revision only — older engines may advertise settings
    // routes but lack inventory/history page APIs.
    let Ok(v) = serde_json::from_str::<serde_json::Value>(&body) else {
        return false;
    };
    let Some(rev) = v.get("revision").and_then(|x| x.as_u64()) else {
        return false;
    };
    if rev < REQUIRED_API_REVISION as u64 {
        return false;
    }
    // Python 3.8 cannot load PEP 604 annotations (`str | None`).
    if let Some(minor) = v.get("python_minor").and_then(|x| x.as_u64()) {
        if minor < 10 {
            return false;
        }
    }
    true
}

fn wait_for_current_api(timeout: Duration) -> bool {
    let start = Instant::now();
    while start.elapsed() < timeout {
        if api_is_current() {
            return true;
        }
        std::thread::sleep(Duration::from_millis(250));
    }
    api_is_current()
}

fn kill_port_listeners_windows(port: u16) {
    #[cfg(windows)]
    {
        let out = Command::new("netstat")
            .args(["-ano", "-p", "TCP"])
            .output();
        let Ok(out) = out else {
            return;
        };
        let text = String::from_utf8_lossy(&out.stdout);
        let needle = format!("127.0.0.1:{port}");
        let mut pids = std::collections::BTreeSet::new();
        for line in text.lines() {
            if !line.contains(&needle) || !line.contains("LISTENING") {
                continue;
            }
            if let Some(pid) = line.split_whitespace().last() {
                if let Ok(n) = pid.parse::<u32>() {
                    pids.insert(n);
                }
            }
        }
        for pid in pids {
            let _ = Command::new("taskkill")
                .args(["/PID", &pid.to_string(), "/F"])
                .stdout(Stdio::null())
                .stderr(Stdio::null())
                .status();
        }
    }
    #[cfg(not(windows))]
    {
        let _ = port;
    }
}

fn apply_no_window(_cmd: &mut Command) {
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        _cmd.creation_flags(CREATE_NO_WINDOW);
    }
}

fn python_at_least_310(prog: &str, prefix: &[&str]) -> bool {
    let mut cmd = Command::new(prog);
    for a in prefix {
        cmd.arg(a);
    }
    cmd.args([
        "-c",
        "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)",
    ]);
    cmd.stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null());
    apply_no_window(&mut cmd);
    cmd.status().map(|s| s.success()).unwrap_or(false)
}

fn localappdata_pythons() -> Vec<PathBuf> {
    let mut out = Vec::new();
    let Ok(local) = std::env::var("LOCALAPPDATA") else {
        return out;
    };
    for ver in ["Python313", "Python312", "Python311", "Python310"] {
        let p = PathBuf::from(&local)
            .join("Programs")
            .join("Python")
            .join(ver)
            .join("python.exe");
        if p.is_file() {
            out.push(p);
        }
    }
    out
}

fn spawn_engine_with_logs(cmd: &mut Command, log_path: &Path) -> Result<Child, String> {
    let log_dir = log_path
        .parent()
        .ok_or_else(|| "invalid log path".to_string())?;
    std::fs::create_dir_all(log_dir).map_err(|e| format!("Cannot create {}: {e}", log_dir.display()))?;

    let log_file = std::fs::File::create(log_path)
        .map_err(|e| format!("Cannot write {}: {e}", log_path.display()))?;
    let err_file = log_file
        .try_clone()
        .map_err(|e| format!("log clone failed: {e}"))?;

    cmd.stdin(Stdio::null())
        .stdout(Stdio::from(log_file))
        .stderr(Stdio::from(err_file));

    apply_no_window(cmd);

    cmd.spawn()
        .map_err(|e| format!("engine spawn failed: {e}"))
}

/// Filename of the bundled, PyInstaller-built engine for this platform.
///
/// Windows produces SatpudaEngine.exe; macOS and Linux produce an extensionless
/// binary. Hard-coding the .exe meant a macOS build could never find its own
/// bundled engine and always fell through to whatever `python3` happened to be
/// on PATH -- fine on a developer machine, impossible to ship.
fn engine_binary_name() -> &'static str {
    if cfg!(windows) {
        "SatpudaEngine.exe"
    } else {
        "SatpudaEngine"
    }
}

fn spawn_sidecar_engine(root: &Path) -> Result<Child, String> {
    spawn_sidecar_engine_at(&root.join("engine"))
}

fn spawn_sidecar_engine_at(engine_dir: &Path) -> Result<Child, String> {
    let exe = engine_dir.join(engine_binary_name());
    if !exe.is_file() {
        return Err(format!(
            "Bundled engine missing:\n{}\nRebuild it with the desktop engine build script for this platform.",
            exe.display()
        ));
    }

    // engine_log_path prefers the app's own AppData folder, which is writable
    // and — unlike anything under the install folder — is never renamed by an
    // update. Its own fallback is beside the engine, which a macOS .app bundle
    // or a signed read-only build may refuse; the OS temp dir is the last
    // resort so a failing engine can still say why.
    let preferred = engine_log_path(engine_dir);
    let log_path = if preferred
        .parent()
        .map(|d| std::fs::create_dir_all(d).is_ok())
        .unwrap_or(false)
    {
        preferred
    } else {
        std::env::temp_dir().join("satpuda_desktop_api.log")
    };
    let mut cmd = Command::new(&exe);
    cmd.current_dir(engine_dir);

    let child = spawn_engine_with_logs(&mut cmd, &log_path)?;
    log::info!(
        "Started bundled data engine (pid {:?}), log {}",
        child.id(),
        log_path.display()
    );
    Ok(child)
}

fn try_spawn_python(
    prog: &str,
    prefix: &[&str],
    script: &Path,
    root: &Path,
    log_path: &Path,
) -> Result<Child, String> {
    if !python_at_least_310(prog, prefix) {
        return Err(format!("{prog} is missing or older than 3.10"));
    }
    let mut cmd = Command::new(prog);
    for a in prefix {
        cmd.arg(a);
    }
    cmd.arg(script).current_dir(root);
    let child = spawn_engine_with_logs(&mut cmd, log_path)?;
    log::info!(
        "Started local data engine via `{prog}` (pid {:?}), log {}",
        child.id(),
        log_path.display()
    );
    Ok(child)
}

fn spawn_python_api(root: &Path) -> Result<Child, String> {
    let script = root.join("run_desktop_api.py");
    if !script.is_file() {
        return Err(format!(
            "Local engine script missing:\n{}\nOpen the project folder that contains run_desktop_api.py.",
            script.display()
        ));
    }

    let log_path = engine_log_path(root);
    let mut last_err = String::from("No Python 3.10+ interpreter found (py/python).");

    for exe in localappdata_pythons() {
        match try_spawn_python(&exe.to_string_lossy(), &[], &script, root, &log_path) {
            Ok(child) => return Ok(child),
            Err(e) => last_err = e,
        }
    }

    // Prefer 3.10+ (PEP 604 `X | Y` types). `py -3` can still launch 3.8.
    let candidates: &[(&str, &[&str])] = &[
        ("py", &["-3.13"]),
        ("py", &["-3.12"]),
        ("py", &["-3.11"]),
        ("py", &["-3.10"]),
        ("python", &[]),
        ("python3", &[]),
        ("py", &["-3"]),
    ];
    for (prog, prefix) in candidates {
        match try_spawn_python(prog, prefix, &script, root, &log_path) {
            Ok(child) => return Ok(child),
            Err(e) => last_err = e,
        }
    }
    Err(last_err)
}

/// Where the bundled engine lives for this platform's app layout.
///
/// Windows keeps resources beside the .exe, so `<root>/engine` is right there.
/// A macOS .app does not: the binary sits in Contents/MacOS while bundled
/// resources go to Contents/Resources. Looking only beside the executable meant
/// the bundled app never found its own engine and silently fell back to a
/// developer python that is not present on a customer's Mac.
fn engine_dir(root: &Path) -> Option<PathBuf> {
    let beside = root.join("engine");
    if beside.join(engine_binary_name()).is_file() {
        return Some(beside);
    }
    if cfg!(target_os = "macos") {
        let resources = root.join("../Resources/engine");
        if resources.join(engine_binary_name()).is_file() {
            return Some(resources);
        }
    }
    None
}

fn spawn_data_engine(root: &Path) -> Result<Child, String> {
    if let Some(dir) = engine_dir(root) {
        return spawn_sidecar_engine_at(&dir);
    }
    let dev_root = repo_root();
    spawn_python_api(&dev_root)
}

fn ensure_engine_running(state: &ApiState) -> Result<&'static str, String> {
    let _boot = state
        .boot
        .lock()
        .map_err(|_| "engine boot lock poisoned".to_string())?;
    if api_is_current() {
        return Ok("ready");
    }

    // Stale or missing engine — free the port and start current code.
    {
        let mut guard = state
            .child
            .lock()
            .map_err(|_| "engine lock poisoned".to_string())?;
        if let Some(mut child) = guard.take() {
            let _ = child.kill();
            let _ = child.wait();
        }
    }
    kill_port_listeners_windows(API_PORT);
    std::thread::sleep(Duration::from_millis(400));

    {
        let mut guard = state
            .child
            .lock()
            .map_err(|_| "engine lock poisoned".to_string())?;
        let root = app_root();
        let child = spawn_data_engine(&root)?;
        *guard = Some(child);
    }

    if wait_for_current_api(Duration::from_secs(30)) {
        Ok("started")
    } else {
        let log = engine_log_path(&app_root());
        Err(format!(
            "Local data engine did not start (need settings API revision {REQUIRED_API_REVISION}).\nSee {}",
            log.display()
        ))
    }
}

#[tauri::command]
fn ensure_desktop_api(state: State<'_, ApiState>) -> Result<String, String> {
    ensure_engine_running(&state).map(|s| s.to_string())
}

#[tauri::command]
fn desktop_api_status() -> Result<bool, String> {
    Ok(api_is_current())
}

/// True while a deliberate restart is in flight, so the exit handler does not
/// mistake it for the shop closing up and take a closing backup. A store switch
/// prompts a restart, and backing up on each one would stall it for no reason.
static RESTARTING: AtomicBool = AtomicBool::new(false);

/// Relaunch the desktop shell (Python engine stops on Exit, then starts again).
#[tauri::command]
fn restart_app(app: AppHandle) {
    RESTARTING.store(true, Ordering::SeqCst);
    stop_engine(&app);
    app.restart();
}

fn stop_engine(app: &AppHandle) {
    if let Some(state) = app.try_state::<ApiState>() {
        if let Ok(mut guard) = state.child.lock() {
            if let Some(mut child) = guard.take() {
                let _ = child.kill();
                let _ = child.wait();
                log::info!("Stopped local data engine");
            }
        }
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .manage(ApiState {
            child: Mutex::new(None),
            boot: Mutex::new(()),
        })
        .invoke_handler(tauri::generate_handler![
            ensure_desktop_api,
            desktop_api_status,
            restart_app
        ])
        .setup(|app| {
            if cfg!(debug_assertions) {
                app.handle().plugin(
                    tauri_plugin_log::Builder::default()
                        .level(log::LevelFilter::Info)
                        .build(),
                )?;
            }

            // Never wait for Python on the UI thread — that marks the window
            // "Not Responding" for 10s+ while crates/imports finish.
            let handle = app.handle().clone();
            if let Err(e) = std::thread::Builder::new()
                .name("satpuda-engine-boot".into())
                .spawn(move || {
                    let state = handle.state::<ApiState>();
                    match ensure_engine_running(&state) {
                        Ok(status) => log::info!("Local data engine: {status}"),
                        Err(e) => log::error!("{e}"),
                    }
                })
            {
                log::error!("engine boot thread: {e}");
            }

            // Match classic Python main window: open maximized (zoomed).
            if let Some(win) = app.get_webview_window("main") {
                let _ = win.maximize();
                let _ = win.set_focus();
            }

            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building tauri application")
        .run(|app_handle, event| {
            if matches!(event, RunEvent::Exit | RunEvent::ExitRequested { .. }) {
                // Ask the engine for the closing backup of the day BEFORE
                // killing it. It cannot do this on its own: the kill below is a
                // SIGKILL/TerminateProcess, so no Python shutdown code runs on a
                // real quit and the close slot was never filled. The engine
                // makes the call idempotent, so both exit events are safe.
                //
                // Bounded on purpose. Offline this gzips one local file and is
                // quick; online it materialises the whole store from the server
                // first, so the budget has to be generous -- but it must stay a
                // budget. A shop shutting down at night must never be left with
                // a window that will not close, and a skipped backup is no
                // worse than today, where it never ran at all.
                //
                // NOT YET PROVEN ON A REAL QUIT: this is the one change here
                // that a dev terminal cannot exercise, because Ctrl-C takes a
                // different path entirely.
                if !RESTARTING.load(Ordering::SeqCst) {
                    let _ = http_get_local_timeout(
                        "/api/backup/close",
                        Duration::from_secs(60),
                    );
                }
                stop_engine(&app_handle);
            }
        });
}
