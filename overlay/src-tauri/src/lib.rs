use serde::Serialize;
use tauri::Manager;

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

pub fn run() {
    // Always-on-top is enforced by a KWin window rule, not the toolkit: on native Wayland
    // GTK ignores keep-above, and forcing XWayland to dodge that costs HiDPI scaling and
    // normal window management. See scripts/install-kwin-rule.sh.
    load_env();
    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![get_connection, quit])
        .setup(|app| {
            if let Some(win) = app.get_webview_window("main") {
                let _ = win.set_always_on_top(true); // honored on X11/Win/macOS; no-op on Wayland
            }
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
