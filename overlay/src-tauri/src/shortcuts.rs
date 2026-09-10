//! Global shortcuts on Wayland, via the XDG GlobalShortcuts portal.
//!
//! `tauri-plugin-global-shortcut` sits on `global-hotkey`, which is X11-only: every linux build
//! routes to an `XGrabKey` backend with no Wayland path. Under a Wayland session XWayland is
//! usually running, so the grab *succeeds* and the plugin reports success, but the compositor
//! never routes keys to XWayland's root and the shortcut is inert. A silent success is worse than
//! a failure, so on Wayland we use the portal instead and let the compositor own the binding.
//!
//! The plugin keeps the X11/Windows/macOS path, where it genuinely works.

use std::sync::Arc;

use ashpd::desktop::global_shortcuts::{
    BindShortcutsOptions, ConfigureShortcutsOptions, GlobalShortcuts, ListShortcutsOptions,
    NewShortcut,
};
use ashpd::desktop::{CreateSessionOptions, Session};
use futures_util::StreamExt;
use tauri::{AppHandle, Emitter, Manager};
use tokio::sync::Mutex;

/// Emitted to the webview with the activated shortcut id as payload.
pub const ACTIVATED_EVENT: &str = "shortcut://activated";

/// Which mechanism can actually deliver a global shortcut here.
pub fn backend() -> &'static str {
    decide(is_wayland(), portal_present)
}

/// The decision itself, separated from the probing so it can be tested without a session bus.
/// `portal` is lazy: on X11 the answer never depends on it, so don't pay for the probe.
fn decide(wayland: bool, portal: impl FnOnce() -> bool) -> &'static str {
    if !wayland {
        // X11, including the app forced onto XWayland: the plugin's grabs work.
        "plugin"
    } else if portal() {
        "portal"
    } else {
        // Wayland without the portal: nothing can bind a global key. Say so rather than
        // registering something that will never fire.
        "none"
    }
}

fn is_wayland() -> bool {
    std::env::var("XDG_SESSION_TYPE")
        .map(|v| v.eq_ignore_ascii_case("wayland"))
        .unwrap_or(false)
        || std::env::var("WAYLAND_DISPLAY").is_ok_and(|v| !v.is_empty())
}

/// Cheap synchronous probe: the interface either resolves on the session bus or it does not.
fn portal_present() -> bool {
    std::process::Command::new("busctl")
        .args([
            "--user",
            "introspect",
            "org.freedesktop.portal.Desktop",
            "/org/freedesktop/portal/desktop",
            "org.freedesktop.portal.GlobalShortcuts",
        ])
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .status()
        .map(|s| s.success())
        .unwrap_or(false)
}

type ShortcutSession = Session<GlobalShortcuts>;

#[derive(Default)]
pub struct PortalState {
    inner: Arc<Mutex<Option<ShortcutSession>>>,
}

/// Bind `shortcuts` (id, human description) with the compositor.
///
/// The description is what the desktop shows in its own confirmation dialog, so it has to read
/// as a sentence to someone who has never seen this app. The compositor picks the final trigger;
/// we do not get to insist on one.
pub async fn bind(app: &AppHandle, shortcuts: Vec<(String, String)>) -> Result<(), String> {
    let state = app.state::<PortalState>();
    let proxy = GlobalShortcuts::new().await.map_err(err)?;

    let session = proxy
        .create_session(CreateSessionOptions::default())
        .await
        .map_err(err)?;
    let new: Vec<NewShortcut> = shortcuts
        .into_iter()
        .map(|(id, description)| NewShortcut::new(id, description))
        .collect();
    proxy
        .bind_shortcuts(&session, &new, None, BindShortcutsOptions::default())
        .await
        .map_err(err)?
        .response()
        .map_err(err)?;

    // The stream is tied to the session, so both have to outlive this call: a dropped session
    // silently unbinds everything.
    let activated = proxy.receive_activated().await.map_err(err)?;
    let handle = app.clone();
    tauri::async_runtime::spawn(async move {
        futures_util::pin_mut!(activated);
        while let Some(signal) = activated.next().await {
            let _ = handle.emit(ACTIVATED_EVENT, signal.shortcut_id().to_string());
        }
    });

    *state.inner.lock().await = Some(session);
    Ok(())
}

/// What the compositor currently has bound, as (action id, human-readable trigger).
///
/// `trigger_description` is the portal's own rendering of the key, which is the only correct
/// thing to show: the compositor picks the trigger and can change it behind our back.
pub async fn list(app: &AppHandle) -> Result<Vec<(String, String)>, String> {
    let state = app.state::<PortalState>();
    let guard = state.inner.lock().await;
    let Some(session) = guard.as_ref() else {
        // Not bound yet, which is a normal first-run state, not a failure.
        return Ok(Vec::new());
    };
    let proxy = GlobalShortcuts::new().await.map_err(err)?;
    let listed = proxy
        .list_shortcuts(session, ListShortcutsOptions::default())
        .await
        .map_err(err)?
        .response()
        .map_err(err)?;
    Ok(listed
        .shortcuts()
        .iter()
        .map(|s| (s.id().to_string(), s.trigger_description().to_string()))
        .collect())
}

/// Open the desktop's own shortcut editor. On Wayland the compositor owns the binding, so this
/// replaces the in-app key-capture widget rather than supplementing it.
pub async fn configure(app: &AppHandle) -> Result<(), String> {
    let state = app.state::<PortalState>();
    let guard = state.inner.lock().await;
    let session = guard.as_ref().ok_or("no shortcut session; bind first")?;
    let proxy = GlobalShortcuts::new().await.map_err(err)?;
    proxy
        .configure_shortcuts(session, None, ConfigureShortcutsOptions::default())
        .await
        .map_err(err)
}

fn err<E: std::fmt::Display>(e: E) -> String {
    e.to_string()
}

#[cfg(test)]
mod tests {
    use super::decide;

    #[test]
    fn x11_uses_the_plugin_and_never_probes_the_portal() {
        // The probe shells out to busctl, so an X11 session must not pay for it.
        let answer = decide(false, || panic!("portal probed on X11"));
        assert_eq!(answer, "plugin");
    }

    #[test]
    fn wayland_with_the_portal_uses_it() {
        assert_eq!(decide(true, || true), "portal");
    }

    #[test]
    fn wayland_without_the_portal_admits_it_cannot() {
        // The bug this replaces: the plugin would report success here and never fire.
        assert_eq!(decide(true, || false), "none");
    }
}
