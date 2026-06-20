use std::collections::VecDeque;
use std::io::{BufRead, BufReader};
use std::path::PathBuf;
use std::process::{Command, Stdio};
use std::sync::atomic::{AtomicBool, AtomicU32, AtomicU64, Ordering};
use std::sync::Mutex;
use std::time::{Duration, Instant};

use serde::Serialize;
use tauri::{AppHandle, Emitter, Manager, State, WebviewWindow};

#[derive(Serialize)]
struct Connection {
    host: String,
    port: u16,
    token: String,
}

/// Load the project root `.env` (the one beside `backend/`) so the overlay can reach the
/// backend out of the box on the same machine. Real environment variables take precedence;
/// `dotenvy::from_path` does not override what is already set.
fn load_env() {
    if let Ok(mut dir) = std::env::current_dir() {
        loop {
            let candidate = dir.join(".env");
            if candidate.is_file() {
                let _ = dotenvy::from_path(&candidate);
                return;
            }
            if !dir.pop() {
                return;
            }
        }
    }
}

#[tauri::command]
fn get_connection() -> Connection {
    Connection {
        host: std::env::var("INTERVIEWR_HOST").unwrap_or_else(|_| "127.0.0.1".into()),
        port: std::env::var("INTERVIEWR_PORT")
            .ok()
            .and_then(|p| p.parse().ok())
            .unwrap_or(8765),
        token: std::env::var("INTERVIEWR_AUTH_TOKEN").unwrap_or_default(),
    }
}

#[tauri::command]
fn quit(app: tauri::AppHandle) {
    app.exit(0);
}

// Backend sidecar: the overlay owns the FastAPI engine as a child process so "apply settings"
// is a supervised restart, not a manual one. We use plain std::process (no tauri-plugin-shell,
// so no extra capabilities) and manage the lifecycle here. Bundling a Python runtime for
// distribution is a separate future installer concern; this launches the project venv.

/// How many recent engine stderr lines we keep to explain a failure to the user.
const STDERR_TAIL_LINES: usize = 25;
/// A crash this soon after spawn means the engine never came up (vs. ran a while then died).
const INSTANT_CRASH_SECS: u64 = 5;
/// Consecutive instant crashes before we stop respawning and surface a hard error.
const MAX_INSTANT_CRASHES: u32 = 3;

#[derive(Default)]
struct EngineState {
    /// PID of the live engine (== its process-group id on Unix), or None when down.
    child_pid: Mutex<Option<u32>>,
    /// Does the overlay want the engine alive? Cleared right before an intentional kill so the
    /// monitor can tell a planned restart/quit from a crash.
    should_run: AtomicBool,
    /// True only when this overlay spawned the engine (vs. a manually-run/external one).
    manage_engine: AtomicBool,
    /// Bumped on an intentional restart so the outgoing child's monitor won't respawn it.
    generation: AtomicU64,
    /// Consecutive instant-crash count for the crash-loop guard.
    instant_failures: AtomicU32,
    /// Ring buffer of the engine's most recent stderr lines.
    stderr_tail: Mutex<VecDeque<String>>,
    /// Last fatal failure message, if any. Stored as well as emitted because the initial spawn
    /// can fail before the webview has attached its `engine://failed` listener (events emitted
    /// then are dropped); the frontend polls this to recover that case.
    last_error: Mutex<Option<String>>,
}

#[derive(Clone, Serialize)]
struct EngineFailed {
    message: String,
}

/// Walk up from the working dir to find the backend (the dir containing `run_pipeline.py`).
/// Deliberately not keyed on `.env`: the engine boots in setup mode without one, and the wizard
/// is what creates `.env` in the first place.
fn find_backend_dir() -> Option<PathBuf> {
    let mut dir = std::env::current_dir().ok()?;
    loop {
        let nested = dir.join("backend");
        if nested.join("run_pipeline.py").is_file() {
            return Some(nested);
        }
        if dir.join("run_pipeline.py").is_file() {
            return Some(dir); // launched from inside backend/ itself
        }
        if !dir.pop() {
            return None;
        }
    }
}

struct EngineCmd {
    python: PathBuf,
    dir: PathBuf,
}

/// Resolve the interpreter + working dir to launch `run_pipeline.py`. Prefers the project venv;
/// both are overridable for packaged builds / non-standard layouts.
fn engine_command() -> Result<EngineCmd, String> {
    let dir = match std::env::var("INTERVIEWR_BACKEND_DIR") {
        Ok(d) if !d.trim().is_empty() => PathBuf::from(d),
        _ => find_backend_dir().ok_or_else(|| {
            "could not locate the backend (no run_pipeline.py found above the working dir; \
             set INTERVIEWR_BACKEND_DIR)"
                .to_string()
        })?,
    };
    if !dir.is_dir() {
        return Err(format!("backend dir not found: {}", dir.display()));
    }
    let python = match std::env::var("INTERVIEWR_BACKEND_PYTHON") {
        Ok(p) if !p.trim().is_empty() => PathBuf::from(p),
        _ => {
            let venv = if cfg!(windows) {
                dir.join(".venv/Scripts/python.exe")
            } else {
                dir.join(".venv/bin/python")
            };
            if venv.is_file() {
                venv
            } else {
                PathBuf::from(if cfg!(windows) { "python" } else { "python3" })
            }
        }
    };
    Ok(EngineCmd { python, dir })
}

/// Is something already serving the engine port? If so we don't spawn a second one (lets the
/// maintainer keep running the backend by hand in dev).
fn engine_reachable() -> bool {
    use std::net::{TcpStream, ToSocketAddrs};
    let host = std::env::var("INTERVIEWR_HOST").unwrap_or_else(|_| "127.0.0.1".into());
    let port: u16 = std::env::var("INTERVIEWR_PORT")
        .ok()
        .and_then(|p| p.parse().ok())
        .unwrap_or(8765);
    let Ok(addrs) = format!("{host}:{port}").to_socket_addrs() else {
        return false;
    };
    addrs.into_iter().any(|addr| TcpStream::connect_timeout(&addr, Duration::from_millis(300)).is_ok())
}

/// Kill the engine's whole process group so the `pw-record` capture subprocess dies with it.
/// We shell out to `kill` (matching this file's other shell-outs) so we never need the Child
/// handle, which the monitor thread owns for `wait()`.
#[cfg(unix)]
fn kill_group(pid: u32) {
    let _ = Command::new("kill").args(["-TERM", &format!("-{pid}")]).status();
    std::thread::sleep(Duration::from_millis(500));
    let _ = Command::new("kill").args(["-KILL", &format!("-{pid}")]).status();
}

#[cfg(windows)]
fn kill_group(pid: u32) {
    let _ = Command::new("taskkill")
        .args(["/PID", &pid.to_string(), "/T", "/F"])
        .status();
}

/// Record a fatal engine failure: store it (for the startup race) and emit it (for a live UI).
fn report_failure(app: &AppHandle, message: String) {
    *app.state::<EngineState>().last_error.lock().unwrap() = Some(message.clone());
    let _ = app.emit("engine://failed", EngineFailed { message });
}

/// Build a user-facing failure message from the engine's recent stderr.
fn engine_error_message(state: &EngineState) -> String {
    let tail = state.stderr_tail.lock().unwrap();
    if tail.is_empty() {
        "The engine keeps crashing on startup. Check the terminal logs for details.".to_string()
    } else {
        let lines: Vec<&str> = tail.iter().map(String::as_str).collect();
        format!("The engine keeps crashing on startup:\n\n{}", lines.join("\n"))
    }
}

/// Launch the engine and supervise it. A stderr-reader thread tees logs and fills the ring
/// buffer; a monitor thread waits on the child and, on an unexpected exit, respawns it (with a
/// crash-loop guard) or surfaces `engine://failed`.
fn spawn_engine(app: &AppHandle) -> Result<(), String> {
    let cmd = engine_command()?;
    let mut command = Command::new(&cmd.python);
    command
        .arg("run_pipeline.py")
        .current_dir(&cmd.dir)
        .stdin(Stdio::null())
        .stdout(Stdio::inherit())
        .stderr(Stdio::piped());
    #[cfg(unix)]
    {
        use std::os::unix::process::CommandExt;
        command.process_group(0); // own group: killing -pid reaps pw-record too
    }

    let mut child = command
        .spawn()
        .map_err(|e| format!("failed to launch engine ({}): {e}", cmd.python.display()))?;
    let pid = child.id();

    let state = app.state::<EngineState>();
    *state.child_pid.lock().unwrap() = Some(pid);
    *state.last_error.lock().unwrap() = None; // a fresh start clears any prior failure
    let my_gen = state.generation.load(Ordering::SeqCst);
    let spawned_at = Instant::now();

    if let Some(stderr) = child.stderr.take() {
        let app_err = app.clone();
        std::thread::spawn(move || {
            let state = app_err.state::<EngineState>();
            for line in BufReader::new(stderr).lines().map_while(Result::ok) {
                eprintln!("[engine] {line}");
                let mut tail = state.stderr_tail.lock().unwrap();
                tail.push_back(line);
                while tail.len() > STDERR_TAIL_LINES {
                    tail.pop_front();
                }
            }
        });
    }

    let app_mon = app.clone();
    std::thread::spawn(move || {
        let _ = child.wait();
        let elapsed = spawned_at.elapsed();
        let state = app_mon.state::<EngineState>();
        {
            let mut cur = state.child_pid.lock().unwrap();
            if *cur == Some(pid) {
                *cur = None;
            }
        }
        // Planned restart (stale generation) or quit (should_run cleared): don't respawn.
        let intentional = !state.should_run.load(Ordering::SeqCst)
            || state.generation.load(Ordering::SeqCst) != my_gen;
        if intentional {
            return;
        }
        let failures = if elapsed < Duration::from_secs(INSTANT_CRASH_SECS) {
            state.instant_failures.fetch_add(1, Ordering::SeqCst) + 1
        } else {
            state.instant_failures.store(0, Ordering::SeqCst);
            0
        };
        if failures >= MAX_INSTANT_CRASHES {
            report_failure(&app_mon, engine_error_message(&state));
            return;
        }
        std::thread::sleep(Duration::from_millis(500));
        if let Err(e) = spawn_engine(&app_mon) {
            report_failure(&app_mon, e);
        }
    });
    Ok(())
}

/// Kill the running engine on app teardown so it never outlives the overlay.
fn stop_engine(state: &EngineState) {
    state.should_run.store(false, Ordering::SeqCst);
    let pid = *state.child_pid.lock().unwrap();
    if let Some(pid) = pid {
        kill_group(pid);
    }
}

/// Intentional kill + clean respawn (used to apply settings). Blocking; runs off the main thread.
fn do_restart(app: &AppHandle) -> Result<(), String> {
    let state = app.state::<EngineState>();
    if !state.manage_engine.load(Ordering::SeqCst) {
        return Err("not_managed".to_string());
    }
    state.generation.fetch_add(1, Ordering::SeqCst); // invalidate the outgoing child's monitor
    state.instant_failures.store(0, Ordering::SeqCst);
    let pid = *state.child_pid.lock().unwrap();
    if let Some(pid) = pid {
        kill_group(pid);
    }
    let deadline = Instant::now() + Duration::from_secs(8);
    while state.child_pid.lock().unwrap().is_some() && Instant::now() < deadline {
        std::thread::sleep(Duration::from_millis(100));
    }
    state.should_run.store(true, Ordering::SeqCst);
    spawn_engine(app)
}

/// Restart the engine to apply new settings. Returns `not_managed` when an external/manual
/// engine owns the port, so the UI can fall back to asking the user to restart it.
#[tauri::command]
async fn restart_engine(app: AppHandle) -> Result<(), String> {
    match tauri::async_runtime::spawn_blocking(move || do_restart(&app)).await {
        Ok(inner) => inner,
        Err(e) => Err(format!("restart task failed: {e}")),
    }
}

/// Whether this overlay owns (and can restart) the engine.
#[tauri::command]
fn engine_managed(state: State<'_, EngineState>) -> bool {
    state.manage_engine.load(Ordering::SeqCst)
}

/// Save text to a user-chosen file via a native Save dialog. Returns false if the user cancels.
/// The webview's own `<a download>` is unreliable on WebKitGTK, so downloads go through here.
#[tauri::command]
async fn save_text(app: AppHandle, name: String, contents: String) -> Result<bool, String> {
    use tauri_plugin_dialog::DialogExt;
    let chosen = tauri::async_runtime::spawn_blocking(move || {
        app.dialog().file().set_file_name(&name).blocking_save_file()
    })
    .await
    .map_err(|e| e.to_string())?;
    let Some(file) = chosen else {
        return Ok(false); // user cancelled the dialog
    };
    let path = file.into_path().map_err(|e| e.to_string())?;
    std::fs::write(&path, contents).map_err(|e| e.to_string())?;
    Ok(true)
}

/// Last fatal engine failure, if any. Lets the frontend recover a failure emitted before its
/// `engine://failed` listener was attached (e.g. an initial-spawn error at startup).
#[tauri::command]
fn engine_error(state: State<'_, EngineState>) -> Option<String> {
    state.last_error.lock().unwrap().clone()
}

/// Toggle always-on-top. The toolkit call works on X11/Windows/macOS; on KDE/Wayland it is a
/// no-op, so we also drive a KWin "keep above" rule there.
#[tauri::command]
fn set_pin(window: WebviewWindow, on: bool) -> Result<bool, String> {
    let _ = window.set_always_on_top(on);
    #[cfg(target_os = "linux")]
    set_kwin_rule_bool("above", on)?;
    Ok(on)
}

// The overlay drives KWin window rules for two behaviors that an app cannot set on itself
// under Wayland: keep-above (`above`) and hide-from-screencast (`excludefromcapture`, KDE
// Plasma/KWin 6.6+; 6.7+ also excludes static screenshots). Both live in one rule matched on
// the overlay's window class. Property keys are verbatim from KWin's rulesettings.kcfg.

/// KWin rule identifiers, shared by all writers/readers so they never drift.
#[cfg(target_os = "linux")]
const KWIN_GROUP: &str = "b6a9f2c1-7e3d-4a8b-9c2e-0123456789ab";
#[cfg(target_os = "linux")]
const KWIN_FILE: &str = "kwinrulesrc";
/// Window class the KWin rule + capture script match on (the overlay's app_id / WM_CLASS).
#[cfg(target_os = "linux")]
const OVERLAY_WMCLASS: &str = "interviewr-overlay";
/// Persisted hide-from-capture intent. On 6.6 this rule key is inert (the script does the runtime
/// work and the frontend re-applies it at startup); on 6.7+ KWin honors it natively.
#[cfg(target_os = "linux")]
const KWIN_CAPTURE_KEY: &str = "excludefromcapture";

#[cfg(target_os = "linux")]
fn kwin_write(args: &[&str]) -> bool {
    use std::process::Command;
    ["kwriteconfig6", "kwriteconfig5"]
        .iter()
        .any(|bin| matches!(Command::new(bin).args(args).status(), Ok(s) if s.success()))
}

#[cfg(target_os = "linux")]
fn kwin_read(args: &[&str]) -> Option<String> {
    use std::process::Command;
    ["kreadconfig6", "kreadconfig5"].iter().find_map(|bin| {
        Command::new(bin)
            .args(args)
            .output()
            .ok()
            .filter(|o| o.status.success())
            .map(|o| String::from_utf8_lossy(&o.stdout).trim().to_string())
    })
}

#[cfg(target_os = "linux")]
fn kwin_reconfigure() {
    use std::process::Command;
    let reconfigure = |bin: &str| {
        Command::new(bin)
            .args(["org.kde.KWin", "/KWin", "reconfigure"])
            .status()
            .map(|s| s.success())
            .unwrap_or(false)
    };
    let _ = reconfigure("qdbus6") || reconfigure("qdbus");
}

/// Parse the running KWin's (major, minor) from `kwin_wayland/kwin_x11 --version` ("kwin 6.7.0").
#[cfg(target_os = "linux")]
fn kwin_version() -> Option<(u32, u32)> {
    use std::process::Command;
    for bin in ["kwin_wayland", "kwin_x11", "kwin"] {
        let Ok(out) = Command::new(bin).arg("--version").output() else {
            continue;
        };
        let text = format!(
            "{}{}",
            String::from_utf8_lossy(&out.stdout),
            String::from_utf8_lossy(&out.stderr)
        );
        if let Some(ver) = text.split_whitespace().find(|t| t.contains('.')) {
            let mut parts = ver.split('.');
            let maj = parts.next()?.parse::<u32>().ok()?;
            let min = parts.next().unwrap_or("0").parse::<u32>().ok()?;
            return Some((maj, min));
        }
    }
    None
}

/// The scriptable `excludeFromCapture` Window property exists in KWin 6.6+. We set it at runtime
/// via a transient KWin script (the same thing the "Hide from Screencast" menu action does), so
/// this works on 6.6, even though the *persistent* window rule only arrived in 6.7.
#[cfg(target_os = "linux")]
fn capture_supported() -> bool {
    matches!(kwin_version(), Some((maj, min)) if maj > 6 || (maj == 6 && min >= 6))
}

/// JS for a transient KWin script that flips `excludeFromCapture` on the overlay's window(s).
#[cfg(target_os = "linux")]
fn exclude_capture_script(on: bool) -> String {
    format!(
        "(function() {{\n\
         \x20 var on = {on};\n\
         \x20 var list = (typeof workspace.windowList === 'function') ? workspace.windowList() : workspace.clientList();\n\
         \x20 for (var i = 0; i < list.length; i++) {{\n\
         \x20   var w = list[i];\n\
         \x20   if (String(w.resourceClass) === '{OVERLAY_WMCLASS}') {{ w.excludeFromCapture = on; }}\n\
         \x20 }}\n\
         }})();\n"
    )
}

/// Load + run a one-shot KWin script over D-Bus, then stop it. KWin only loads scripts from a
/// file path, so write a uniquely-named temp file (KWin dedupes by plugin name/path otherwise).
#[cfg(target_os = "linux")]
fn kwin_run_script(js: &str) -> Result<(), String> {
    use std::process::Command;
    use std::time::{SystemTime, UNIX_EPOCH};

    let nanos = SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_nanos()).unwrap_or(0);
    let mut path = std::env::temp_dir();
    path.push(format!("interviewr-kwin-{nanos}.js"));
    std::fs::write(&path, js).map_err(|e| format!("write KWin script: {e}"))?;
    let path_str = path.to_string_lossy().to_string();
    let plugin = format!("interviewr-capture-{nanos}");

    let qdbus = |args: &[&str]| -> Option<String> {
        ["qdbus6", "qdbus"].iter().find_map(|bin| {
            Command::new(bin)
                .args(args)
                .output()
                .ok()
                .filter(|o| o.status.success())
                .map(|o| String::from_utf8_lossy(&o.stdout).trim().to_string())
        })
    };

    let result = (|| {
        let id = qdbus(&[
            "org.kde.KWin",
            "/Scripting",
            "org.kde.kwin.Scripting.loadScript",
            &path_str,
            &plugin,
        ])
        .ok_or_else(|| "KWin scripting unavailable (KDE Plasma required)".to_string())?;
        if id.starts_with('-') {
            return Err("KWin refused to load the capture script".to_string());
        }
        let obj = format!("/Scripting/Script{id}");
        qdbus(&["org.kde.KWin", &obj, "org.kde.kwin.Script.run"]);
        qdbus(&["org.kde.KWin", &obj, "org.kde.kwin.Script.stop"]);
        Ok(())
    })();

    let _ = std::fs::remove_file(&path);
    result
}

/// Ensure our single KWin window rule exists and matches the overlay by window class.
/// Idempotent. Returns Err if the KDE config tools are absent (i.e. not KDE Plasma).
#[cfg(target_os = "linux")]
fn ensure_kwin_rule() -> Result<(), String> {
    let set =
        |key: &str, val: &str| kwin_write(&["--file", KWIN_FILE, "--group", KWIN_GROUP, "--key", key, val]);
    if !set("Description", "interviewr overlay") {
        return Err("kwriteconfig not found (KDE Plasma required for window rules)".into());
    }
    set("wmclass", OVERLAY_WMCLASS);
    set("wmclassmatch", "1");
    set("wmclasscomplete", "false");

    let rules = kwin_read(&["--file", KWIN_FILE, "--group", "General", "--key", "rules"]).unwrap_or_default();
    if !rules.split(',').any(|r| r == KWIN_GROUP) {
        let merged =
            if rules.is_empty() { KWIN_GROUP.to_string() } else { format!("{rules},{KWIN_GROUP}") };
        let count = merged.split(',').count().to_string();
        kwin_write(&["--file", KWIN_FILE, "--group", "General", "--key", "rules", &merged]);
        kwin_write(&["--file", KWIN_FILE, "--group", "General", "--key", "count", &count]);
    }
    Ok(())
}

/// Force a boolean KWin rule property (`<key>` + `<key>rule`=2 Force) on our rule, then reload
/// KWin. KDE only (silently does nothing if kwriteconfig/qdbus are absent). Setting individual
/// keys does not clobber the rule's other properties, so pin and hide-from-capture coexist.
#[cfg(target_os = "linux")]
fn set_kwin_rule_bool(key: &str, on: bool) -> Result<(), String> {
    ensure_kwin_rule()?;
    let set =
        |k: &str, v: &str| kwin_write(&["--file", KWIN_FILE, "--group", KWIN_GROUP, "--key", k, v]);
    set(key, if on { "true" } else { "false" });
    set(&format!("{key}rule"), "2"); // 2 = Force
    kwin_reconfigure();
    Ok(())
}

/// Read a persisted boolean KWin rule property. None if unset (rule/key not established yet)
/// or the KDE read tools are absent.
#[cfg(target_os = "linux")]
fn kwin_rule_bool_state(key: &str) -> Option<bool> {
    let val = kwin_read(&["--file", KWIN_FILE, "--group", KWIN_GROUP, "--key", key])?;
    match val.as_str() {
        "true" => Some(true),
        "false" => Some(false),
        _ => None,
    }
}

/// Pin state the UI initializes its toggle from, so it never disagrees with the window.
/// On Linux this is the persisted KWin keep-above value; unset defaults to unpinned (the wizard
/// and a fresh install start unpinned until the user pins the panel).
#[tauri::command]
fn get_pin() -> bool {
    #[cfg(target_os = "linux")]
    return kwin_rule_bool_state("above").unwrap_or(false);
    #[cfg(not(target_os = "linux"))]
    return false;
}

/// Toggle stealth (hide from screen capture; KWin 6.6+, a no-op elsewhere). Persists the intent in
/// the `excludefromcapture` rule (remembered on 6.6, honored natively on 6.7+) and applies it now
/// via the runtime script for 6.6. Only PipeWire-portal capturers honor it; a privacy/accessibility
/// convenience, not an undetectability guarantee.
#[tauri::command]
fn set_stealth(on: bool) -> Result<bool, String> {
    #[cfg(target_os = "linux")]
    {
        set_kwin_rule_bool(KWIN_CAPTURE_KEY, on)?; // persist intent (+ native effect on 6.7+)
        if capture_supported() {
            kwin_run_script(&exclude_capture_script(on))?; // runtime effect now (6.6)
        }
        return Ok(on);
    }
    #[cfg(not(target_os = "linux"))]
    {
        let _ = on;
        return Ok(false);
    }
}

/// Persisted stealth preference for the UI to initialize from. Default OFF (opt-in). On 6.6
/// the frontend re-runs the script at startup to re-apply this across restarts.
#[tauri::command]
fn get_stealth() -> bool {
    #[cfg(target_os = "linux")]
    return kwin_rule_bool_state(KWIN_CAPTURE_KEY).unwrap_or(false);
    #[cfg(not(target_os = "linux"))]
    return false;
}

/// Whether the hide-from-capture toggle can actually work here (KWin 6.6+). The UI disables the
/// button when false instead of offering a control that silently does nothing.
#[tauri::command]
fn get_capture_supported() -> bool {
    #[cfg(target_os = "linux")]
    return capture_supported();
    #[cfg(not(target_os = "linux"))]
    return false;
}

/// Size the overlay relative to the monitor's logical resolution, so a fixed 420x600 isn't tiny
/// on a 4K panel. Logical size already folds in the OS display scaling, so this looks right on a
/// 1080p laptop and a HiDPI 4K alike. Clamped to sane bounds, then centered.
fn size_to_screen(win: &WebviewWindow) {
    let Ok(Some(monitor)) = win.current_monitor() else {
        return;
    };
    let scale = monitor.scale_factor();
    let phys = monitor.size();
    let logical_w = phys.width as f64 / scale;
    let logical_h = phys.height as f64 / scale;
    // Landscape: wider than tall.
    let w = (logical_w * 0.40).clamp(1000.0, 1200.0);
    let h = (logical_h * 0.42).clamp(700.0, 760.0);
    let _ = win.set_size(tauri::LogicalSize::new(w, h));
    let _ = win.center();
}

pub fn run() {
    // Always-on-top is enforced by a KWin window rule, not the toolkit: on native Wayland
    // GTK ignores keep-above, and forcing XWayland to dodge that costs HiDPI scaling and
    // normal window management. See scripts/install-kwin-rule.sh.
    load_env();
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_global_shortcut::Builder::new().build())
        .manage(EngineState::default())
        .invoke_handler(tauri::generate_handler![
            get_connection,
            set_pin,
            get_pin,
            set_stealth,
            get_stealth,
            get_capture_supported,
            restart_engine,
            engine_managed,
            engine_error,
            save_text,
            quit
        ])
        .setup(|app| {
            // Start unpinned: the setup wizard has no pin control, so the window must not trap
            // itself always-on-top. The panel's Pin toggle sets and persists the user's choice.
            if let Some(win) = app.get_webview_window("main") {
                let _ = win.set_always_on_top(false);
                size_to_screen(&win);
            }
            // Own the backend unless one is already serving the port or the user opted out.
            let handle = app.handle().clone();
            let state = handle.state::<EngineState>();
            let opted_out = std::env::var("INTERVIEWR_NO_SIDECAR")
                .map(|v| !v.trim().is_empty())
                .unwrap_or(false);
            if opted_out || engine_reachable() {
                state.manage_engine.store(false, Ordering::SeqCst);
            } else {
                state.manage_engine.store(true, Ordering::SeqCst);
                state.should_run.store(true, Ordering::SeqCst);
                if let Err(e) = spawn_engine(&handle) {
                    report_failure(&handle, e);
                }
            }
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while running tauri application")
        .run(|app_handle, event| {
            if matches!(event, tauri::RunEvent::ExitRequested { .. } | tauri::RunEvent::Exit) {
                stop_engine(&app_handle.state::<EngineState>());
            }
        });
}
