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
/// via a transient KWin script — the same thing the "Hide from Screencast" menu action does — so
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
/// On Linux this is the persisted KWin keep-above value (pinned on first run); elsewhere the
/// window is recreated on top each launch, so report pinned.
#[tauri::command]
fn get_pin() -> bool {
    #[cfg(target_os = "linux")]
    return kwin_rule_bool_state("above").unwrap_or(true);
    #[cfg(not(target_os = "linux"))]
    return true;
}

/// Toggle "hide from screen capture" (KWin 6.6+; a no-op elsewhere). Persists the intent in the
/// `excludefromcapture` rule (remembered on 6.6, honored natively on 6.7+) and applies it now via
/// the runtime script for 6.6. Only PipeWire-portal capturers honor it; a privacy/accessibility
/// convenience, not an undetectability guarantee.
#[tauri::command]
fn set_capture_hidden(on: bool) -> Result<bool, String> {
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

/// Persisted capture-hidden preference for the UI to initialize from. Default OFF (opt-in). On 6.6
/// the frontend re-runs the script at startup to re-apply this across restarts.
#[tauri::command]
fn get_capture_hidden() -> bool {
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

pub fn run() {
    // Always-on-top is enforced by a KWin window rule, not the toolkit: on native Wayland
    // GTK ignores keep-above, and forcing XWayland to dodge that costs HiDPI scaling and
    // normal window management. See scripts/install-kwin-rule.sh.
    load_env();
    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![
            get_connection,
            set_pin,
            get_pin,
            set_capture_hidden,
            get_capture_hidden,
            get_capture_supported,
            quit
        ])
        .setup(|app| {
            if let Some(win) = app.get_webview_window("main") {
                let _ = win.set_always_on_top(true); // honored on X11/Win/macOS; no-op on Wayland
            }
            // Pin by default only on first run; later launches honor the user's saved choice
            // (the KWin rule persists), so an unpinned overlay reopens unpinned. Hide-from-capture
            // is opt-in, so it is never auto-enabled here.
            #[cfg(target_os = "linux")]
            if kwin_rule_bool_state("above").is_none() {
                let _ = set_kwin_rule_bool("above", true);
            }
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
