//! Puts the process in a named systemd scope so the GlobalShortcuts portal can identify the app.

use std::path::{Path, PathBuf};
use std::process::{id as pid, Command, Stdio};

/// App id we claim; matches the `.desktop` file tauri-bundler installs.
pub const APP_ID: &str = "interviewr-overlay";

/// APP_ID escaped for a unit name (a bare `-` would be read as a launcher prefix).
const UNIT_APP_ID: &str = r"interviewr\x2doverlay";

/// Moves this process into an `app-<APP_ID>-<pid>.scope` unless it already has our id; best effort.
pub fn ensure_scope() {
    if resolved_app_id().as_deref() == Some(APP_ID) {
        return;
    }
    // Sandboxed builds get their id from the sandbox.
    if std::path::Path::new("/.flatpak-info").exists() || std::env::var_os("SNAP").is_some() {
        return;
    }
    if let Err(e) = start_scope() {
        eprintln!("[overlay] could not claim an app scope ({e}); global shortcuts may be refused");
    }
}

/// App id the portal would resolve for us (mirrors xdg-desktop-portal's `get_app_from_pid`).
pub fn resolved_app_id() -> Option<String> {
    let id = parse_unit_name(&current_unit()?)?;
    if id.is_empty() {
        None
    } else {
        Some(id)
    }
}

/// Marks a desktop entry the app wrote for a checkout build.
const DEV_MARKER: &str = "X-Interviewr-Dev=true";

fn entry_file() -> String {
    format!("{APP_ID}.desktop")
}

/// Per-user data folder (`$XDG_DATA_HOME`, else `~/.local/share`).
fn user_data_dir() -> Option<PathBuf> {
    std::env::var_os("XDG_DATA_HOME")
        .map(PathBuf::from)
        .or_else(|| std::env::var_os("HOME").map(|h| PathBuf::from(h).join(".local/share")))
}

/// System data folders from `$XDG_DATA_DIRS`, with the spec's default.
fn system_data_dirs() -> Vec<PathBuf> {
    std::env::var("XDG_DATA_DIRS")
        .unwrap_or_else(|_| "/usr/local/share:/usr/share".to_string())
        .split(':')
        .filter(|d| !d.is_empty())
        .map(PathBuf::from)
        .collect()
}

/// Whether `<APP_ID>.desktop` is installed; the portal rejects the id without it.
pub fn desktop_entry_present() -> bool {
    let file = entry_file();
    user_data_dir()
        .into_iter()
        .chain(system_data_dirs())
        .any(|d| d.join("applications").join(&file).exists())
}

#[derive(Debug, PartialEq, Eq)]
enum EntryAction {
    Keep,
    Write,
    Remove,
}

/// Whether a user-level entry was made for a checkout build, by the app or the old install script.
fn is_dev_entry(contents: &str) -> bool {
    contents
        .lines()
        .map(str::trim)
        .any(|l| l == DEV_MARKER || (l.starts_with("Exec=") && l.contains("/src-tauri/target/")))
}

/// What to do with the user-level entry: a dev entry must never hide the package's own entry.
fn entry_action(installed: bool, user_entry: Option<&str>, system_entry: bool, wanted: &str) -> EntryAction {
    match user_entry {
        Some(e) if is_dev_entry(e) && (installed || system_entry) => EntryAction::Remove,
        Some(e) if is_dev_entry(e) && e != wanted => EntryAction::Write,
        None if !installed && !system_entry => EntryAction::Write,
        _ => EntryAction::Keep,
    }
}

/// Desktop entry for a checkout build, so global shortcuts work when running from source.
fn dev_entry(exe: &Path, icon: &Path) -> String {
    format!(
        "[Desktop Entry]\nType=Application\nName=interviewr (dev)\n\
         Comment=Real-time interview and meeting copilot\nExec=\"{}\"\nIcon={}\nTerminal=false\n\
         Categories=Utility;\nStartupWMClass={APP_ID}\n{DEV_MARKER}\n",
        exe.display(),
        icon.display()
    )
}

/// Writes, refreshes or removes the per-user dev desktop entry to suit how the app is running; best effort.
pub fn sync_desktop_entry(installed: bool) {
    if !cfg!(target_os = "linux") {
        return;
    }
    let (Some(data), Ok(exe)) = (user_data_dir(), std::env::current_exe()) else {
        return;
    };
    let dir = data.join("applications");
    let path = dir.join(entry_file());
    let system_entry = system_data_dirs().iter().any(|d| d.join("applications").join(entry_file()).exists());
    let icon = Path::new(env!("CARGO_MANIFEST_DIR")).join("icons/128x128.png");
    let wanted = dev_entry(&exe, &icon);
    let current = std::fs::read_to_string(&path).ok();
    let changed = match entry_action(installed, current.as_deref(), system_entry, &wanted) {
        EntryAction::Keep => false,
        EntryAction::Remove => std::fs::remove_file(&path).is_ok(),
        EntryAction::Write => std::fs::create_dir_all(&dir).is_ok() && std::fs::write(&path, &wanted).is_ok(),
    };
    if changed {
        // Refresh the desktop database so the portal sees the change without a re-login.
        let _ = Command::new("update-desktop-database").arg(&dir).stdout(Stdio::null()).stderr(Stdio::null()).status();
    }
}

/// Why the portal would refuse us, phrased for the user, or None when it should work.
pub fn diagnose() -> Option<String> {
    match (resolved_app_id(), desktop_entry_present()) {
        (Some(_), true) => None,
        (_, false) => Some(format!(
            "Your desktop can't identify this app, because {APP_ID}.desktop is not installed \
             and couldn't be created. Restart the overlay to try again, or install the package."
        )),
        (None, true) => Some(
            "Your desktop can't identify this app: it is not running in an application scope. \
             Launch the overlay from your applications menu, or restart it."
                .to_string(),
        ),
    }
}

/// The systemd user unit this process is in, read the way `sd_pid_get_user_unit` would.
fn current_unit() -> Option<String> {
    let cgroup = std::fs::read_to_string("/proc/self/cgroup").ok()?;
    // cgroup v2: a single "0::<path>" line. The unit is the deepest path segment that names one.
    let path = cgroup.lines().find_map(|l| l.strip_prefix("0::"))?;
    unit_from_cgroup(path)
}

fn unit_from_cgroup(path: &str) -> Option<String> {
    path.trim_end().rsplit('/').find(|s| is_unit(s)).map(str::to_string)
}

/// Whether a cgroup segment is a real unit name (skips Konsole's `tab(N).scope`).
fn is_unit(segment: &str) -> bool {
    (segment.ends_with(".scope") || segment.ends_with(".service") || segment.ends_with(".slice"))
        && segment
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b":-_.\\@".contains(&b))
}

/// The portal's two regexes, hand-rolled. Returns the unescaped app id, "" when nothing matches.
fn parse_unit_name(unit: &str) -> Option<String> {
    let rest = unit.strip_prefix("app-")?;
    let matched = if let Some(body) = rest.strip_suffix(".scope").or(rest.strip_suffix(".slice")) {
        // ^app-(?:[[:alnum:]]+\-)?(.+?)(?:\-[[:alnum:]]*)(?:\.scope|\.slice)$
        lazy_match(body, |tail, i| {
            tail[i..].starts_with('-') && tail[i + 1..].bytes().all(|b| b.is_ascii_alphanumeric())
        })
    } else if let Some(body) = rest.strip_suffix(".service") {
        // ^app-(?:[[:alnum:]]+\-)?(.+?)(?:@[[:alnum:]]*|\-autostart)?\.service$
        lazy_match(body, |tail, i| {
            let r = &tail[i..];
            r.is_empty()
                || r == "-autostart"
                || (r.starts_with('@') && r[1..].bytes().all(|b| b.is_ascii_alphanumeric()))
        })
    } else {
        None
    };
    Some(unescape(matched.unwrap_or("")))
}

/// Lazy regex match: tries with the optional launcher prefix, then without.
fn lazy_match(body: &str, ends: impl Fn(&str, usize) -> bool) -> Option<&str> {
    let launcher = body
        .find('-')
        .filter(|&i| i > 0 && body[..i].bytes().all(|b| b.is_ascii_alphanumeric()))
        .map(|i| i + 1);
    for start in launcher.into_iter().chain(std::iter::once(0)) {
        let tail = &body[start..];
        // The capture is `.+?`, so at least one character.
        for i in 1..=tail.len() {
            if tail.is_char_boundary(i) && ends(tail, i) {
                return Some(&tail[..i]);
            }
        }
    }
    None
}

/// Undo systemd's unit-name escaping, the `\xNN` subset that unit names actually use.
fn unescape(s: &str) -> String {
    let b = s.as_bytes();
    let mut out = String::with_capacity(s.len());
    let mut i = 0;
    while i < b.len() {
        let hex = (b.len() - i >= 4 && b[i] == b'\\' && b[i + 1] == b'x')
            .then(|| std::str::from_utf8(&b[i + 2..i + 4]).ok())
            .flatten()
            .and_then(|h| u8::from_str_radix(h, 16).ok());
        match hex {
            Some(c) => {
                out.push(c as char);
                i += 4;
            }
            None => {
                out.push(b[i] as char);
                i += 1;
            }
        }
    }
    out
}

/// Asks the systemd user manager to start a transient scope containing this process.
fn start_scope() -> Result<(), String> {
    let name = format!("app-{UNIT_APP_ID}-{}.scope", pid());
    let rt = tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()
        .map_err(|e| e.to_string())?;
    rt.block_on(async {
        let conn = zbus::Connection::session().await.map_err(|e| e.to_string())?;
        let props: Vec<(&str, zbus::zvariant::Value<'_>)> = vec![
            ("Description", "interviewr overlay".into()),
            ("PIDs", vec![pid()].into()),
        ];
        let aux: Vec<(&str, Vec<(&str, zbus::zvariant::Value<'_>)>)> = Vec::new();
        conn.call_method(
            Some("org.freedesktop.systemd1"),
            "/org/freedesktop/systemd1",
            Some("org.freedesktop.systemd1.Manager"),
            "StartTransientUnit",
            &(name.as_str(), "fail", props, aux),
        )
        .await
        .map(|_| ())
        .map_err(|e| e.to_string())
    })
}

#[cfg(test)]
mod tests {
    use super::{entry_action, parse_unit_name, EntryAction, APP_ID, DEV_MARKER};

    const WANTED: &str = "[Desktop Entry]\nExec=\"/src/overlay/src-tauri/target/debug/interviewr-overlay\"\nX-Interviewr-Dev=true\n";
    const OLD_SCRIPT: &str = "[Desktop Entry]\nExec=/home/u/interviewr/overlay/src-tauri/target/release/interviewr-overlay\n";
    const USERS_OWN: &str = "[Desktop Entry]\nExec=/usr/bin/interviewr-overlay --custom\n";

    #[test]
    fn installed_app_removes_a_dev_entry_that_would_hide_its_own() {
        assert_eq!(entry_action(true, Some(WANTED), true, WANTED), EntryAction::Remove);
        assert_eq!(entry_action(true, Some(OLD_SCRIPT), true, WANTED), EntryAction::Remove);
    }

    #[test]
    fn installed_app_leaves_an_entry_the_user_wrote() {
        assert_eq!(entry_action(true, Some(USERS_OWN), true, WANTED), EntryAction::Keep);
        assert_eq!(entry_action(true, None, true, WANTED), EntryAction::Keep);
    }

    #[test]
    fn checkout_writes_an_entry_when_there_is_none_anywhere() {
        assert_eq!(entry_action(false, None, false, WANTED), EntryAction::Write);
    }

    #[test]
    fn checkout_relies_on_the_package_entry_when_it_exists() {
        assert_eq!(entry_action(false, None, true, WANTED), EntryAction::Keep);
        assert_eq!(entry_action(false, Some(WANTED), true, WANTED), EntryAction::Remove);
    }

    #[test]
    fn checkout_refreshes_a_stale_dev_entry_and_keeps_a_current_one() {
        assert_eq!(entry_action(false, Some(OLD_SCRIPT), false, WANTED), EntryAction::Write);
        assert_eq!(entry_action(false, Some(WANTED), false, WANTED), EntryAction::Keep);
    }

    #[test]
    fn checkout_leaves_an_entry_the_user_wrote() {
        assert_eq!(entry_action(false, Some(USERS_OWN), false, WANTED), EntryAction::Keep);
    }

    #[test]
    fn the_written_entry_carries_the_marker() {
        let entry = super::dev_entry(std::path::Path::new("/tmp/interviewr-overlay"), std::path::Path::new("/tmp/icon.png"));
        assert!(entry.contains(DEV_MARKER));
        assert!(entry.contains("Name=interviewr (dev)"));
        assert!(entry.contains(&format!("StartupWMClass={APP_ID}")));
    }

    fn id(unit: &str) -> String {
        parse_unit_name(unit).expect("app- prefix")
    }

    #[test]
    fn a_scope_without_the_random_suffix_resolves_to_nothing() {
        // No random suffix: the portal answered "An app id is required".
        assert_eq!(id("app-dev.interviewr.overlay.scope"), "");
    }

    #[test]
    fn a_terminal_launch_resolves_to_the_terminal() {
        // Binds, but into Konsole's shortcuts.
        assert_eq!(id("app-org.kde.konsole-3544.scope"), "org.kde.konsole");
    }

    #[test]
    fn our_escaped_scope_resolves_to_our_app_id() {
        assert_eq!(id(&format!("app-{}-99.scope", super::UNIT_APP_ID)), APP_ID);
    }

    #[test]
    fn an_unescaped_dash_is_read_as_a_launcher_prefix() {
        // Why UNIT_APP_ID escapes the dash.
        assert_eq!(id("app-interviewr-overlay-99.scope"), "overlay");
    }

    #[test]
    fn service_units_and_slices_are_understood_too() {
        assert_eq!(id("app-gnome-org.gnome.Evince-12345.scope"), "org.gnome.Evince");
        assert_eq!(id("app-org.kde.amarok.service"), "org.kde.amarok");
        assert_eq!(id("app-flatpak-org.telegram.desktop@12345.service"), "org.telegram.desktop");
        assert_eq!(id("app-org.gnome.Evince-12345.slice"), "org.gnome.Evince");
    }

    #[test]
    fn units_that_are_not_apps_are_not_ours_to_read() {
        assert!(parse_unit_name("session.slice").is_none());
    }

    #[test]
    fn a_nested_non_unit_cgroup_is_looked_through() {
        // Konsole's per-tab directory isn't a unit, so the scope above it is used.
        let path = "/user.slice/user-1000.slice/user@1000.service/app.slice\
                    /app-org.kde.konsole-3544.scope/tab(3564).scope";
        assert_eq!(
            super::unit_from_cgroup(path).as_deref(),
            Some("app-org.kde.konsole-3544.scope")
        );
    }
}
