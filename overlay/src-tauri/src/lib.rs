use serde::Serialize;
use tauri::{Manager, WebviewWindow};

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

/// Toggle always-on-top. The toolkit call works on X11/Windows/macOS; on KDE/Wayland it is a
/// no-op, so we also drive a KWin "keep above" rule there.
#[tauri::command]
fn set_pin(window: WebviewWindow, on: bool) -> Result<bool, String> {
    let _ = window.set_always_on_top(on);
    #[cfg(target_os = "linux")]
    set_kwin_keep_above(on)?;
    Ok(on)
}

/// KWin rule identifiers, shared by the writer and the state reader so they never drift.
#[cfg(target_os = "linux")]
const KWIN_GROUP: &str = "b6a9f2c1-7e3d-4a8b-9c2e-0123456789ab";
#[cfg(target_os = "linux")]
const KWIN_FILE: &str = "kwinrulesrc";

/// Read the persisted keep-above value for our rule. None if the rule does not exist yet
/// (first run) or the KDE read tools are absent.
#[cfg(target_os = "linux")]
fn kwin_keep_above_state() -> Option<bool> {
    use std::process::Command;
    let val = ["kreadconfig6", "kreadconfig5"].iter().find_map(|bin| {
        Command::new(bin)
            .args(["--file", KWIN_FILE, "--group", KWIN_GROUP, "--key", "above"])
            .output()
            .ok()
            .filter(|o| o.status.success())
            .map(|o| String::from_utf8_lossy(&o.stdout).trim().to_string())
    })?;
    match val.as_str() {
        "true" => Some(true),
        "false" => Some(false),
        _ => None, // key absent (empty) -> rule not established yet
    }
}

/// Write/flip a KWin window rule that forces the overlay above other windows, then reload KWin.
/// Idempotent: re-running just updates the `above` flag. KDE only (silently does nothing if the
/// kwriteconfig/qdbus tools are absent).
#[cfg(target_os = "linux")]
fn set_kwin_keep_above(on: bool) -> Result<(), String> {
    use std::process::Command;
    const GROUP: &str = KWIN_GROUP;
    const FILE: &str = KWIN_FILE;

    let kwrite = |args: &[&str]| -> bool {
        ["kwriteconfig6", "kwriteconfig5"]
            .iter()
            .any(|bin| matches!(Command::new(bin).args(args).status(), Ok(s) if s.success()))
    };
    let kread = |args: &[&str]| -> Option<String> {
        ["kreadconfig6", "kreadconfig5"].iter().find_map(|bin| {
            Command::new(bin)
                .args(args)
                .output()
                .ok()
                .filter(|o| o.status.success())
                .map(|o| String::from_utf8_lossy(&o.stdout).trim().to_string())
        })
    };

    let set = |key: &str, val: &str| kwrite(&["--file", FILE, "--group", GROUP, "--key", key, val]);
    if !set("Description", "interviewr overlay - keep above") {
        return Err("kwriteconfig not found (KDE Plasma required for Wayland pinning)".into());
    }
    set("wmclass", "interviewr-overlay");
    set("wmclassmatch", "1");
    set("wmclasscomplete", "false");
    set("above", if on { "true" } else { "false" });
    set("aboverule", "2"); // Force

    let rules = kread(&["--file", FILE, "--group", "General", "--key", "rules"]).unwrap_or_default();
    if !rules.split(',').any(|r| r == GROUP) {
        let merged = if rules.is_empty() { GROUP.to_string() } else { format!("{rules},{GROUP}") };
        let count = merged.split(',').count().to_string();
        kwrite(&["--file", FILE, "--group", "General", "--key", "rules", &merged]);
        kwrite(&["--file", FILE, "--group", "General", "--key", "count", &count]);
    }

    let reconfigure = |bin: &str| {
        Command::new(bin)
            .args(["org.kde.KWin", "/KWin", "reconfigure"])
            .status()
            .map(|s| s.success())
            .unwrap_or(false)
    };
    let _ = reconfigure("qdbus6") || reconfigure("qdbus");
    Ok(())
}

/// Pin state the UI initializes its toggle from, so it never disagrees with the window.
/// On Linux this is the persisted KWin keep-above value (pinned on first run); elsewhere the
/// window is recreated on top each launch, so report pinned.
#[tauri::command]
fn get_pin() -> bool {
    #[cfg(target_os = "linux")]
    return kwin_keep_above_state().unwrap_or(true);
    #[cfg(not(target_os = "linux"))]
    return true;
}

pub fn run() {
    // Always-on-top is enforced by a KWin window rule, not the toolkit: on native Wayland
    // GTK ignores keep-above, and forcing XWayland to dodge that costs HiDPI scaling and
    // normal window management. See scripts/install-kwin-rule.sh.
    load_env();
    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![get_connection, set_pin, get_pin, quit])
        .setup(|app| {
            if let Some(win) = app.get_webview_window("main") {
                let _ = win.set_always_on_top(true); // honored on X11/Win/macOS; no-op on Wayland
            }
            // Pin by default only on first run; later launches honor the user's saved choice
            // (the KWin rule persists), so an unpinned overlay reopens unpinned.
            #[cfg(target_os = "linux")]
            if kwin_keep_above_state().is_none() {
                let _ = set_kwin_keep_above(true);
            }
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
