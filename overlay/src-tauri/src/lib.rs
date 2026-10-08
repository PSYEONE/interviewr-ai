mod appid;
mod shortcuts;

use std::collections::{HashMap, VecDeque};
use std::io::{BufRead, BufReader};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::atomic::{AtomicBool, AtomicU32, AtomicU64, Ordering};
use std::sync::{Mutex, OnceLock};
use std::time::{Duration, Instant};

use serde::Serialize;
use tauri::{AppHandle, Emitter, Manager, State, WebviewWindow};

#[derive(Serialize)]
struct Connection {
    host: String,
    port: u16,
    token: String,
}

/// Parsed `.env` values, kept in a map and never exported so the spawned engine can't inherit stale vars.
static ENV_FILE: OnceLock<HashMap<String, String>> = OnceLock::new();

fn load_env() {
    ENV_FILE.get_or_init(env_map);
}

/// Finds `.env` in the same order as config.py: override, checkout root, then the XDG config dir.
fn env_map() -> HashMap<String, String> {
    if let Some(dir) = std::env::var_os("INTERVIEWR_CONFIG_DIR") {
        if !dir.is_empty() {
            return parse_env_file(&PathBuf::from(dir).join(".env"));
        }
    }
    if let Ok(dir) = std::env::current_dir() {
        let found = read_env_file(dir);
        if !found.is_empty() {
            return found;
        }
    }
    parse_env_file(&xdg_config_dir().join(".env"))
}

/// `$XDG_CONFIG_HOME/interviewr` or `~/.config/interviewr`; relative XDG values are ignored.
fn xdg_config_dir() -> PathBuf {
    let base = std::env::var_os("XDG_CONFIG_HOME")
        .map(PathBuf::from)
        .filter(|p| p.is_absolute())
        .or_else(|| std::env::var_os("HOME").map(|h| PathBuf::from(h).join(".config")))
        .unwrap_or_else(|| PathBuf::from("/"));
    base.join("interviewr")
}

/// Parses the nearest `.env` at or above `dir` into a map.
fn read_env_file(mut dir: PathBuf) -> HashMap<String, String> {
    loop {
        let candidate = dir.join(".env");
        if candidate.is_file() {
            return parse_env_file(&candidate);
        }
        if !dir.pop() {
            return HashMap::new();
        }
    }
}

fn parse_env_file(path: &Path) -> HashMap<String, String> {
    match dotenvy::from_path_iter(path) {
        Ok(iter) => iter.flatten().collect(),
        Err(_) => HashMap::new(),
    }
}

/// Looks up a setting: real environment first, then `.env`.
fn cfg(key: &str) -> Option<String> {
    std::env::var(key).ok().or_else(|| {
        ENV_FILE
            .get()
            .and_then(|m| m.get(key))
            .filter(|v| !v.is_empty())
            .cloned()
    })
}

#[tauri::command]
fn get_connection() -> Connection {
    Connection {
        host: cfg("INTERVIEWR_HOST").unwrap_or_else(|| "127.0.0.1".into()),
        port: cfg("INTERVIEWR_PORT")
            .and_then(|p| p.parse().ok())
            .unwrap_or(8765),
        token: cfg("INTERVIEWR_AUTH_TOKEN").unwrap_or_default(),
    }
}

#[tauri::command]
fn quit(app: tauri::AppHandle) {
    app.exit(0);
}

// Engine sidecar: the overlay spawns and supervises the FastAPI engine as a child process.

/// How many recent engine stderr lines we keep to explain a failure to the user.
const STDERR_TAIL_LINES: usize = 25;
/// Crashes are counted inside this window, so a slow crash loop is caught as well as an instant one.
const CRASH_WINDOW_SECS: u64 = 60;
/// Crashes within the window before we stop respawning and surface a hard error.
const MAX_CRASHES: u32 = 3;

/// Whether the engine is crash-looping rather than having hit one bad run.
fn give_up(failures: u32, since_first: Duration) -> bool {
    failures >= MAX_CRASHES && since_first <= Duration::from_secs(CRASH_WINDOW_SECS)
}

#[derive(Default)]
struct EngineState {
    /// PID of the live engine (== its process-group id on Unix), or None when down.
    child_pid: Mutex<Option<u32>>,
    /// Whether the engine should be running; cleared before an intentional kill.
    should_run: AtomicBool,
    /// True only when this overlay spawned the engine (vs. a manually-run/external one).
    manage_engine: AtomicBool,
    /// Bumped on an intentional restart so the outgoing child's monitor won't respawn it.
    generation: AtomicU64,
    /// Crashes counted in the current window, for the crash-loop guard.
    recent_failures: AtomicU32,
    /// When the current crash window started.
    first_failure_at: Mutex<Option<Instant>>,
    /// Ring buffer of the engine's most recent stderr lines.
    stderr_tail: Mutex<VecDeque<String>>,
    /// Last fatal error, kept so the UI can fetch one emitted before it was listening.
    last_error: Mutex<Option<String>>,
}

#[derive(Clone, Serialize)]
struct EngineFailed {
    message: String,
}

/// Finds the backend by walking up from the cwd, then at the installed location.
fn find_backend_dir() -> Option<PathBuf> {
    walk_up_for_backend(std::env::current_dir().ok()?).or_else(installed_backend_dir)
}

fn walk_up_for_backend(mut dir: PathBuf) -> Option<PathBuf> {
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

/// Installed engine at `<prefix>/lib/interviewr/backend`, relative to this executable.
fn installed_backend_dir() -> Option<PathBuf> {
    let exe = std::env::current_exe().ok()?;
    let prefix = exe.parent()?.parent()?;
    let dir = prefix.join("lib").join("interviewr").join("backend");
    dir.join("run_pipeline.py").is_file().then_some(dir)
}

/// Engine interpreter: the checkout's `.venv`, else a packaged `python/` or `venv/` beside the backend.
fn venv_python(dir: &Path) -> Option<PathBuf> {
    let relative = if cfg!(windows) {
        [
            ".venv/Scripts/python.exe",
            "../python/Scripts/python.exe",
            "../venv/Scripts/python.exe",
        ]
    } else {
        [".venv/bin/python", "../python/bin/python", "../venv/bin/python"]
    };
    relative.iter().map(|p| dir.join(p)).find(|p| p.is_file())
}

struct EngineCmd {
    python: PathBuf,
    dir: PathBuf,
}

/// Interpreter and working dir for `run_pipeline.py`; both overridable via env.
fn engine_command() -> Result<EngineCmd, String> {
    let dir = match cfg("INTERVIEWR_BACKEND_DIR") {
        Some(d) if !d.trim().is_empty() => PathBuf::from(d),
        _ => find_backend_dir().ok_or_else(|| {
            "could not locate the backend (no run_pipeline.py found above the working dir; \
             set INTERVIEWR_BACKEND_DIR)"
                .to_string()
        })?,
    };
    if !dir.is_dir() {
        return Err(format!("backend dir not found: {}", dir.display()));
    }
    let python = match cfg("INTERVIEWR_BACKEND_PYTHON") {
        Some(p) if !p.trim().is_empty() => PathBuf::from(p),
        _ => venv_python(&dir)
            .unwrap_or_else(|| PathBuf::from(if cfg!(windows) { "python" } else { "python3" })),
    };
    Ok(EngineCmd { python, dir })
}

/// Whether an engine is already listening on the port (e.g. one started by hand).
fn engine_reachable() -> bool {
    use std::net::{TcpStream, ToSocketAddrs};
    let host = cfg("INTERVIEWR_HOST").unwrap_or_else(|| "127.0.0.1".into());
    let port: u16 = cfg("INTERVIEWR_PORT")
        .and_then(|p| p.parse().ok())
        .unwrap_or(8765);
    let Ok(addrs) = format!("{host}:{port}").to_socket_addrs() else {
        return false;
    };
    addrs.into_iter().any(|addr| TcpStream::connect_timeout(&addr, Duration::from_millis(300)).is_ok())
}

/// Kills the engine's process group so its `pw-record` child dies too.
#[cfg(unix)]
fn kill_group(pid: u32) {
    // `--` stops `-<pid>` being read as an option: without it procps kill on Ubuntu 24.04 reports
    // success for `-KILL -<pid>` but leaves the process running.
    let _ = Command::new("kill").args(["-TERM", "--", &format!("-{pid}")]).stderr(Stdio::null()).status();
    std::thread::sleep(Duration::from_millis(500));
    // Usually the group is already gone by now, so "No such process" is expected and hidden.
    let _ = Command::new("kill").args(["-KILL", "--", &format!("-{pid}")]).stderr(Stdio::null()).status();
}

/// Parent PID from a `/proc/<pid>/stat` line; the command name can contain spaces and brackets.
#[cfg(target_os = "linux")]
fn ppid_from_stat(stat: &str) -> Option<u32> {
    let rest = &stat[stat.rfind(')')? + 1..];
    rest.split_whitespace().nth(1)?.parse().ok()
}

/// Running engines this app started, found by parent PID, including any it lost track of.
#[cfg(target_os = "linux")]
fn own_engine_pids() -> Vec<u32> {
    let me = std::process::id();
    let Ok(entries) = std::fs::read_dir("/proc") else {
        return Vec::new();
    };
    entries
        .flatten()
        .filter_map(|e| e.file_name().to_str()?.parse::<u32>().ok())
        .filter(|pid| {
            let stat = std::fs::read_to_string(format!("/proc/{pid}/stat")).unwrap_or_default();
            let cmdline = std::fs::read(format!("/proc/{pid}/cmdline")).unwrap_or_default();
            ppid_from_stat(&stat) == Some(me)
                && String::from_utf8_lossy(&cmdline).contains("run_pipeline.py")
        })
        .collect()
}

#[cfg(not(target_os = "linux"))]
fn own_engine_pids() -> Vec<u32> {
    Vec::new()
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

/// Spawns the engine, logs its stderr, and respawns it on unexpected exit with a crash-loop guard.
fn spawn_engine(app: &AppHandle) -> Result<(), String> {
    let cmd = engine_command()?;
    let mut command = Command::new(&cmd.python);
    command
        .arg("run_pipeline.py")
        .current_dir(&cmd.dir)
        // Lets the engine exit on its own if the overlay dies without stopping it.
        .env("INTERVIEWR_PARENT_PID", std::process::id().to_string())
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
        let (failures, since_first) = {
            let mut first = state.first_failure_at.lock().unwrap();
            let window_start = match *first {
                Some(at) if at.elapsed() <= Duration::from_secs(CRASH_WINDOW_SECS) => at,
                _ => {
                    // Window lapsed (or this is the first crash): start counting again.
                    state.recent_failures.store(0, Ordering::SeqCst);
                    *first = Some(Instant::now());
                    Instant::now()
                }
            };
            (state.recent_failures.fetch_add(1, Ordering::SeqCst) + 1, window_start.elapsed())
        };
        eprintln!(
            "[overlay] engine {pid} exited after {}s ({failures} crashes in this minute)",
            elapsed.as_secs()
        );
        if give_up(failures, since_first) {
            report_failure(&app_mon, engine_error_message(&state));
            return;
        }
        // Another engine still holds the port, so a respawn could only fail to bind, forever.
        if engine_reachable() {
            report_failure(
                &app_mon,
                "Another engine is already using the port, so a new one can't start. \
                 Quit interviewr, then open it again."
                    .to_string(),
            );
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
    state.recent_failures.store(0, Ordering::SeqCst);
    *state.first_failure_at.lock().unwrap() = None;
    let tracked = *state.child_pid.lock().unwrap();
    // Stop every engine we started, not just the tracked one, so none is left holding the port.
    let mut pids = own_engine_pids();
    if let Some(pid) = tracked {
        if !pids.contains(&pid) {
            pids.push(pid);
        }
    }
    eprintln!("[overlay] restarting engine: tracked {tracked:?}, stopping {pids:?}");
    for pid in &pids {
        kill_group(*pid);
    }
    // Wait for the old engine to exit and release the port before starting the new one.
    let deadline = Instant::now() + Duration::from_secs(10);
    while (state.child_pid.lock().unwrap().is_some() || engine_reachable()) && Instant::now() < deadline {
        std::thread::sleep(Duration::from_millis(100));
    }
    if engine_reachable() {
        return Err("the old engine is still using the port; quit interviewr and open it again".to_string());
    }
    state.should_run.store(true, Ordering::SeqCst);
    spawn_engine(app)
}

/// Restarts the engine to apply settings; `not_managed` if an external engine owns the port.
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

/// Saves text through a native Save dialog (WebKitGTK downloads are unreliable); false if cancelled.
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

/// Last fatal engine error, for failures emitted before the UI was listening.
#[tauri::command]
fn engine_error(state: State<'_, EngineState>) -> Option<String> {
    state.last_error.lock().unwrap().clone()
}

/// Toggles always-on-top: the toolkit call, plus a KWin keep-above rule since Wayland ignores it.
#[tauri::command]
fn set_pin(window: WebviewWindow, on: bool) -> Result<bool, String> {
    let _ = window.set_always_on_top(on);
    #[cfg(target_os = "linux")]
    set_kwin_rule_bool("above", on)?;
    Ok(on)
}

// KWin window rules for what Wayland won't let an app set on itself: keep-above, capture exclusion, taskbar.

/// KWin rule identifiers, shared by all writers/readers so they never drift.
#[cfg(target_os = "linux")]
const KWIN_GROUP: &str = "b6a9f2c1-7e3d-4a8b-9c2e-0123456789ab";
#[cfg(target_os = "linux")]
const KWIN_FILE: &str = "kwinrulesrc";
/// Window class the KWin rule + capture script match on (the overlay's app_id / WM_CLASS).
#[cfg(target_os = "linux")]
const OVERLAY_WMCLASS: &str = "interviewr-overlay";
/// Rule key that persists hide-from-capture (applied natively on KWin 6.7+).
#[cfg(target_os = "linux")]
const KWIN_CAPTURE_KEY: &str = "excludefromcapture";
/// Rule key that hides the taskbar entry while stealth is on.
#[cfg(target_os = "linux")]
const KWIN_TASKBAR_KEY: &str = "skiptaskbar";

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

/// Tells KWin to reload its rules so changes apply immediately.
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
    // Fallback for systems without qdbus.
    let dbus_send = || {
        Command::new("dbus-send")
            .args([
                "--type=method_call",
                "--dest=org.kde.KWin",
                "/KWin",
                "org.kde.KWin.reconfigure",
            ])
            .status()
            .map(|s| s.success())
            .unwrap_or(false)
    };
    // Failure is ignored; the rule still applies at next login.
    let _ = reconfigure("qdbus6") || reconfigure("qdbus") || dbus_send();
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

/// Whether KWin can hide a window from capture at runtime (6.6+).
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

/// Loads, runs and stops a one-shot KWin script from a uniquely named temp file.
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

/// Creates or updates the overlay's KWin rule; errors if the KDE config tools are missing.
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

/// Forces a boolean property on the overlay's KWin rule, then reloads KWin.
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

/// Reads a boolean property from the overlay's KWin rule; None if unset or the tools are missing.
#[cfg(target_os = "linux")]
fn kwin_rule_bool_state(key: &str) -> Option<bool> {
    let val = kwin_read(&["--file", KWIN_FILE, "--group", KWIN_GROUP, "--key", key])?;
    match val.as_str() {
        "true" => Some(true),
        "false" => Some(false),
        _ => None,
    }
}

/// Persisted pin state for the UI toggle; unpinned by default.
#[tauri::command]
fn get_pin() -> bool {
    #[cfg(target_os = "linux")]
    return kwin_rule_bool_state("above").unwrap_or(false);
    #[cfg(not(target_os = "linux"))]
    return false;
}

/// Toggles stealth: hides the overlay from screen capture (KWin 6.6+) and from the taskbar.
#[tauri::command]
fn set_stealth(on: bool) -> Result<bool, String> {
    #[cfg(target_os = "linux")]
    {
        set_kwin_rule_bool(KWIN_CAPTURE_KEY, on)?; // persist (native on 6.7+)
        set_kwin_rule_bool(KWIN_TASKBAR_KEY, on)?; // hide the taskbar entry
        if capture_supported() {
            kwin_run_script(&exclude_capture_script(on))?; // apply now on 6.6
        }
        return Ok(on);
    }
    #[cfg(not(target_os = "linux"))]
    {
        let _ = on;
        return Ok(false);
    }
}

/// Persisted stealth preference for the UI; off by default.
#[tauri::command]
fn get_stealth() -> bool {
    #[cfg(target_os = "linux")]
    return kwin_rule_bool_state(KWIN_CAPTURE_KEY).unwrap_or(false);
    #[cfg(not(target_os = "linux"))]
    return false;
}

/// Whether the desktop lets an app pin itself: KDE drives a KWin rule, X11 allows keep-above.
#[cfg(target_os = "linux")]
fn pin_supported_from(kwin: Option<(u32, u32)>, session_type: Option<&str>) -> bool {
    kwin.is_some() || session_type.map_or(true, |s| !s.eq_ignore_ascii_case("wayland"))
}

#[cfg(target_os = "linux")]
fn pin_supported() -> bool {
    pin_supported_from(kwin_version(), std::env::var("XDG_SESSION_TYPE").ok().as_deref())
}

/// Links the app may open in the browser: its own repo and the donation pages in `lib/support.ts`.
const OPEN_LINK_ALLOWED: &[&str] = &[
    "https://github.com/PSYEONE/interviewr-ai/",
    "https://github.com/sponsors/PSYEONE/",
    "https://ko-fi.com/",
];

/// Whether `url` is one of the allowed links (a prefix match on whole path segments, so no look-alikes).
fn link_allowed(url: &str) -> bool {
    !url.chars().any(|c| c.is_whitespace() || c.is_control())
        && OPEN_LINK_ALLOWED
            .iter()
            .any(|prefix| url.starts_with(prefix) || url == prefix.trim_end_matches('/'))
}

/// Opens a donation or feedback page in the default browser, without waiting for it.
#[tauri::command]
fn open_link(url: String) -> Result<(), String> {
    if !link_allowed(&url) {
        return Err("link not allowed".to_string());
    }
    #[cfg(target_os = "linux")]
    let mut command = Command::new("xdg-open");
    #[cfg(target_os = "macos")]
    let mut command = Command::new("open");
    #[cfg(windows)]
    let mut command = {
        let mut c = Command::new("cmd");
        c.args(["/C", "start", ""]);
        c
    };
    let mut child = command
        .arg(&url)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .map_err(|e| format!("could not open the browser: {e}"))?;
    std::thread::spawn(move || {
        let _ = child.wait(); // reap it so it doesn't linger as a zombie
    });
    Ok(())
}

/// Whether always-on-top works here; the UI disables Pin otherwise (GNOME Wayland ignores it).
#[tauri::command]
fn get_pin_supported() -> bool {
    #[cfg(target_os = "linux")]
    return pin_supported();
    #[cfg(not(target_os = "linux"))]
    return true;
}

/// Whether hide-from-capture works on this KWin; the UI disables the button otherwise.
#[tauri::command]
fn get_capture_supported() -> bool {
    #[cfg(target_os = "linux")]
    return capture_supported();
    #[cfg(not(target_os = "linux"))]
    return false;
}

/// Sizes the window to the monitor's logical resolution and centres it.
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
    let h = (logical_h * 0.42).clamp(720.0, 760.0);
    let _ = win.set_size(tauri::LogicalSize::new(w, h));
    let _ = win.center();
}

/// Which mechanism can deliver a global shortcut here: "portal", "plugin" or "none".
#[tauri::command]
fn shortcuts_backend() -> &'static str {
    shortcuts::backend()
}

/// Bind (id, description) pairs with the compositor. Portal path only.
#[tauri::command]
async fn shortcuts_bind(app: AppHandle, shortcuts: Vec<(String, String)>) -> Result<(), String> {
    shortcuts::bind(&app, shortcuts).await
}

/// Open the desktop's own shortcut editor. Portal path only.
#[tauri::command]
async fn shortcuts_configure(app: AppHandle) -> Result<(), String> {
    shortcuts::configure(&app).await
}

/// (action id, human-readable trigger) for whatever the compositor has bound. Portal path only.
#[tauri::command]
async fn shortcuts_list(app: AppHandle) -> Result<Vec<(String, String)>, String> {
    shortcuts::list(&app).await
}

/// Why the desktop cannot identify us, or null when it can. Explains a refused bind.
#[tauri::command]
fn shortcuts_app_id_problem() -> Option<String> {
    appid::diagnose()
}

pub fn run() {
    // Must run before any portal call: the shortcuts portal identifies us by our systemd scope.
    appid::ensure_scope();
    // Keep a desktop entry for checkout builds, and clear it once the package provides one.
    appid::sync_desktop_entry(installed_backend_dir().is_some());
    load_env();
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_global_shortcut::Builder::new().build())
        .manage(EngineState::default())
        .manage(shortcuts::PortalState::default())
        .invoke_handler(tauri::generate_handler![
            get_connection,
            set_pin,
            get_pin,
            set_stealth,
            get_stealth,
            get_capture_supported,
            get_pin_supported,
            open_link,
            restart_engine,
            engine_managed,
            engine_error,
            save_text,
            shortcuts_backend,
            shortcuts_bind,
            shortcuts_configure,
            shortcuts_list,
            shortcuts_app_id_problem,
            quit
        ])
        .setup(|app| {
            // Start unpinned; the wizard also turns pin and stealth off, and the panel's toggles save the user's choice.
            if let Some(win) = app.get_webview_window("main") {
                let _ = win.set_always_on_top(false);
                size_to_screen(&win);
            }
            // Own the backend unless one is already serving the port or the user opted out.
            let handle = app.handle().clone();
            let state = handle.state::<EngineState>();
            let opted_out = cfg("INTERVIEWR_NO_SIDECAR").is_some_and(|v| !v.trim().is_empty());
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

#[cfg(test)]
mod env_tests {
    use super::{read_env_file, xdg_config_dir};
    use std::path::PathBuf;

    /// Reading `.env` must not export it, or the spawned engine would inherit stale settings.
    #[test]
    fn reading_the_env_file_does_not_set_process_variables() {
        let dir = std::env::temp_dir().join(format!("interviewr-env-{}", std::process::id()));
        std::fs::create_dir_all(&dir).expect("temp dir");
        std::fs::write(dir.join(".env"), "INTERVIEWR_TEST_ONLY_KEY=from_file\n").expect("write");

        let parsed = read_env_file(dir.clone());

        assert_eq!(parsed.get("INTERVIEWR_TEST_ONLY_KEY").map(String::as_str), Some("from_file"));
        assert!(
            std::env::var("INTERVIEWR_TEST_ONLY_KEY").is_err(),
            "reading .env must not export into the environment; the engine inherits it"
        );
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn a_missing_env_file_is_not_an_error() {
        let dir = std::env::temp_dir().join(format!("interviewr-noenv-{}", std::process::id()));
        std::fs::create_dir_all(&dir).expect("temp dir");
        // Walks to the filesystem root without finding one, which is a normal first run.
        assert!(read_env_file(dir.clone()).get("INTERVIEWR_TEST_ONLY_KEY").is_none());
        let _ = std::fs::remove_dir_all(&dir);
    }

    /// Must match config.py's XDG lookup so the overlay and engine read the same `.env`.
    #[test]
    fn the_xdg_config_dir_matches_the_engines() {
        // SAFETY: single-threaded test; the variables are restored before returning.
        let saved = (std::env::var_os("XDG_CONFIG_HOME"), std::env::var_os("HOME"));
        unsafe { std::env::set_var("HOME", "/home/someone") };

        unsafe { std::env::remove_var("XDG_CONFIG_HOME") };
        assert_eq!(xdg_config_dir(), PathBuf::from("/home/someone/.config/interviewr"));

        unsafe { std::env::set_var("XDG_CONFIG_HOME", "/custom/cfg") };
        assert_eq!(xdg_config_dir(), PathBuf::from("/custom/cfg/interviewr"));

        // Relative XDG values are ignored.
        unsafe { std::env::set_var("XDG_CONFIG_HOME", "relative/cfg") };
        assert_eq!(xdg_config_dir(), PathBuf::from("/home/someone/.config/interviewr"));

        unsafe {
            match saved.0 {
                Some(v) => std::env::set_var("XDG_CONFIG_HOME", v),
                None => std::env::remove_var("XDG_CONFIG_HOME"),
            }
            match saved.1 {
                Some(v) => std::env::set_var("HOME", v),
                None => std::env::remove_var("HOME"),
            }
        }
    }
}

/// Locating the engine in a checkout and in an installed layout.
#[cfg(test)]
mod layout_tests {
    use super::{venv_python, walk_up_for_backend};
    use std::path::PathBuf;

    /// Temp directory removed on drop.
    struct Scratch(PathBuf);

    impl Scratch {
        fn new(tag: &str) -> Self {
            let dir = std::env::temp_dir().join(format!(
                "interviewr-{tag}-{}-{:?}",
                std::process::id(),
                std::thread::current().id()
            ));
            std::fs::create_dir_all(&dir).expect("temp dir");
            Self(dir)
        }
        fn touch(&self, rel: &str) -> PathBuf {
            let path = self.0.join(rel);
            std::fs::create_dir_all(path.parent().expect("parent")).expect("mkdir");
            std::fs::write(&path, "").expect("write");
            path
        }
    }

    impl Drop for Scratch {
        fn drop(&mut self) {
            let _ = std::fs::remove_dir_all(&self.0);
        }
    }

    #[test]
    fn the_walk_finds_a_nested_backend() {
        let s = Scratch::new("nested");
        s.touch("backend/run_pipeline.py");
        assert_eq!(walk_up_for_backend(s.0.clone()), Some(s.0.join("backend")));
    }

    #[test]
    fn the_walk_finds_the_backend_from_inside_it() {
        let s = Scratch::new("inside");
        s.touch("backend/run_pipeline.py");
        assert_eq!(walk_up_for_backend(s.0.join("backend")), Some(s.0.join("backend")));
    }

    #[test]
    fn the_walk_climbs_out_of_a_subdirectory() {
        let s = Scratch::new("climb");
        s.touch("backend/run_pipeline.py");
        std::fs::create_dir_all(s.0.join("overlay/src")).expect("mkdir");
        assert_eq!(walk_up_for_backend(s.0.join("overlay/src")), Some(s.0.join("backend")));
    }

    #[test]
    fn a_checkout_venv_is_found_inside_the_backend() {
        let s = Scratch::new("devvenv");
        let backend = s.0.join("backend");
        s.touch("backend/run_pipeline.py");
        let python = s.touch("backend/.venv/bin/python");
        assert_eq!(venv_python(&backend), Some(python));
    }

    /// Packaged layout: the bundled `python/` sits beside the backend.
    #[test]
    fn the_bundled_interpreter_is_found_beside_the_backend() {
        let s = Scratch::new("pkgpython");
        let backend = s.0.join("backend");
        s.touch("backend/run_pipeline.py");
        s.touch("python/bin/python");
        assert_eq!(venv_python(&backend), Some(backend.join("../python/bin/python")));
    }

    /// The other sibling shape: an install that built a venv against the system Python.
    #[test]
    fn a_sibling_venv_is_found_too() {
        let s = Scratch::new("pkgvenv");
        let backend = s.0.join("backend");
        s.touch("backend/run_pipeline.py");
        s.touch("venv/bin/python");
        assert_eq!(venv_python(&backend), Some(backend.join("../venv/bin/python")));
    }

    #[test]
    fn the_checkout_venv_wins_when_both_exist() {
        let s = Scratch::new("bothvenv");
        let backend = s.0.join("backend");
        let dev = s.touch("backend/.venv/bin/python");
        s.touch("venv/bin/python");
        assert_eq!(venv_python(&backend), Some(dev));
    }

    #[test]
    fn no_venv_resolves_to_none_so_the_caller_falls_back_to_python3() {
        let s = Scratch::new("novenv");
        s.touch("backend/run_pipeline.py");
        assert_eq!(venv_python(&s.0.join("backend")), None);
    }
}

#[cfg(test)]
mod crash_tests {
    use super::give_up;
    use std::time::Duration;

    #[test]
    fn one_or_two_crashes_are_retried() {
        assert!(!give_up(1, Duration::from_secs(1)));
        assert!(!give_up(2, Duration::from_secs(30)));
    }

    #[test]
    fn three_crashes_in_the_window_stop_the_loop() {
        // The engine that segfaulted every 7s: slow enough to dodge an "instant crash" rule.
        assert!(give_up(3, Duration::from_secs(21)));
        assert!(give_up(4, Duration::from_secs(59)));
    }

    #[test]
    fn crashes_spread_beyond_the_window_keep_retrying() {
        assert!(!give_up(3, Duration::from_secs(61)));
    }
}

#[cfg(test)]
mod link_tests {
    use super::link_allowed;

    #[test]
    fn allows_the_donation_and_feedback_links() {
        assert!(link_allowed("https://github.com/sponsors/PSYEONE"));
        assert!(link_allowed("https://github.com/PSYEONE/interviewr-ai/issues/new?title=Feedback%3A+"));
        assert!(link_allowed("https://ko-fi.com/someone"));
    }

    #[test]
    fn rejects_other_sites_and_look_alikes() {
        assert!(!link_allowed("https://github.com.evil/PSYEONE/interviewr-ai/"));
        assert!(!link_allowed("https://github.com/sponsors/PSYEONEevil"));
        assert!(!link_allowed("https://github.com/PSYEONE/other-repo"));
        assert!(!link_allowed("http://ko-fi.com/someone"));
        assert!(!link_allowed("file:///etc/passwd"));
    }

    #[test]
    fn rejects_whitespace_that_could_smuggle_arguments() {
        assert!(!link_allowed("https://ko-fi.com/a --new-window"));
        assert!(!link_allowed("https://ko-fi.com/a\nb"));
    }
}

#[cfg(all(test, target_os = "linux"))]
mod proc_tests {
    use super::ppid_from_stat;

    #[test]
    fn reads_the_parent_pid() {
        assert_eq!(ppid_from_stat("2273 (python) S 2214 2273 2273 0 -1"), Some(2214));
    }

    #[test]
    fn a_command_name_with_spaces_and_brackets_does_not_shift_the_fields() {
        assert_eq!(ppid_from_stat("99 (odd name) (x)) R 42 99 99 0"), Some(42));
    }

    #[test]
    fn garbage_is_none() {
        assert_eq!(ppid_from_stat("no brackets here"), None);
    }
}

#[cfg(all(test, target_os = "linux"))]
mod pin_tests {
    use super::pin_supported_from;

    #[test]
    fn kde_can_pin_because_kwin_takes_a_window_rule() {
        assert!(pin_supported_from(Some((6, 6)), Some("wayland")));
    }

    #[test]
    fn x11_can_pin_without_kwin() {
        assert!(pin_supported_from(None, Some("x11")));
    }

    #[test]
    fn other_wayland_compositors_cannot() {
        // GNOME Wayland: no KWin rule, and the toolkit call is ignored.
        assert!(!pin_supported_from(None, Some("wayland")));
    }

    #[test]
    fn an_unset_session_type_is_assumed_to_allow_it() {
        assert!(pin_supported_from(None, None));
    }
}
