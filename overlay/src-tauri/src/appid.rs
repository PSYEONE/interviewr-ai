//! Puts the process in a named systemd scope so the GlobalShortcuts portal can identify the app.

use std::process::id as pid;

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

/// Whether `<APP_ID>.desktop` is installed; the portal rejects the id without it.
pub fn desktop_entry_present() -> bool {
    let file = format!("{APP_ID}.desktop");
    let home = std::env::var_os("XDG_DATA_HOME").map(std::path::PathBuf::from).or_else(|| {
        std::env::var_os("HOME").map(|h| std::path::PathBuf::from(h).join(".local/share"))
    });
    let dirs = std::env::var("XDG_DATA_DIRS")
        .unwrap_or_else(|_| "/usr/local/share:/usr/share".to_string());
    home.into_iter()
        .chain(dirs.split(':').filter(|d| !d.is_empty()).map(std::path::PathBuf::from))
        .any(|d| d.join("applications").join(&file).exists())
}

/// Why the portal would refuse us, phrased for the user, or None when it should work.
pub fn diagnose() -> Option<String> {
    match (resolved_app_id(), desktop_entry_present()) {
        (Some(_), true) => None,
        (_, false) => Some(format!(
            "Your desktop can't identify this app, because {APP_ID}.desktop is not installed. \
             Run overlay/scripts/install-desktop-entry.sh, then restart the overlay."
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
    use super::{parse_unit_name, APP_ID};

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
