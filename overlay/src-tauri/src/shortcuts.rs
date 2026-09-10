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
    NewShortcut, Shortcut,
};
use ashpd::desktop::{CreateSessionOptions, Session};
use futures_util::StreamExt;
use tauri::{AppHandle, Emitter, Manager};
use tokio::sync::Mutex;

/// Emitted to the webview with the activated shortcut id as payload.
pub const ACTIVATED_EVENT: &str = "shortcut://activated";

/// Emitted when the desktop changes a trigger, with the same payload as [`list`]. The compositor
/// owns the keys, so this is the only way to know a key changed without asking repeatedly.
pub const CHANGED_EVENT: &str = "shortcut://changed";

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

/// The proxy is kept beside the session on purpose. Dropping either silently unbinds everything,
/// and the `Activated` stream is only useful while both are alive.
struct Portal {
    proxy: GlobalShortcuts,
    session: ShortcutSession,
}

#[derive(Default)]
pub struct PortalState {
    inner: Arc<Mutex<Option<Portal>>>,
}

/// The session, created on first use. Creating one raises no dialog and, on KDE, loads whatever
/// the desktop has already stored for us, which is what lets [`list`] answer before any bind.
async fn ensure<'a>(app: &AppHandle, slot: &'a mut Option<Portal>) -> Result<&'a Portal, String> {
    if slot.is_none() {
        let proxy = GlobalShortcuts::new().await.map_err(err)?;
        let session = proxy
            .create_session(CreateSessionOptions::default())
            .await
            .map_err(err)?;
        let activated = proxy.receive_activated().await.map_err(err)?;
        let handle = app.clone();
        tauri::async_runtime::spawn(async move {
            futures_util::pin_mut!(activated);
            while let Some(signal) = activated.next().await {
                let _ = handle.emit(ACTIVATED_EVENT, signal.shortcut_id().to_string());
            }
        });

        let changed = proxy.receive_shortcuts_changed().await.map_err(err)?;
        let handle = app.clone();
        tauri::async_runtime::spawn(async move {
            futures_util::pin_mut!(changed);
            while let Some(signal) = changed.next().await {
                let _ = handle.emit(CHANGED_EVENT, pairs(signal.shortcuts()));
            }
        });

        *slot = Some(Portal { proxy, session });
    }
    Ok(slot.as_ref().expect("just filled"))
}

/// Bind `shortcuts` (id, human description) with the compositor.
///
/// The description is what the desktop shows in its own confirmation dialog, so it has to read
/// as a sentence to someone who has never seen this app. The compositor picks the final trigger;
/// we do not get to insist on one.
pub async fn bind(app: &AppHandle, shortcuts: Vec<(String, String)>) -> Result<(), String> {
    let state = app.state::<PortalState>().inner.clone();
    let mut slot = state.lock().await;
    let portal = ensure(app, &mut slot).await?;
    let new: Vec<NewShortcut> = shortcuts
        .into_iter()
        .map(|(id, description)| NewShortcut::new(id, description))
        .collect();
    portal
        .proxy
        .bind_shortcuts(&portal.session, &new, None, BindShortcutsOptions::default())
        .await
        .map_err(err)?
        .response()
        .map_err(err)?;
    Ok(())
}

/// What the compositor currently has for us, as (action id, human-readable trigger).
///
/// `trigger_description` is the portal's own rendering of the key, which is the only correct
/// thing to show: the compositor picks the trigger and can change it behind our back.
///
/// This answers before anything is bound, which is the point: a non-empty answer means the desktop
/// still holds our permission, so re-binding is silent. See `#initPortal` in hotkeys.svelte.ts.
pub async fn list(app: &AppHandle) -> Result<Vec<(String, String)>, String> {
    let state = app.state::<PortalState>().inner.clone();
    let mut slot = state.lock().await;
    let portal = ensure(app, &mut slot).await?;
    let listed = portal
        .proxy
        .list_shortcuts(&portal.session, ListShortcutsOptions::default())
        .await
        .map_err(err)?
        .response()
        .map_err(err)?;
    Ok(pairs(listed.shortcuts()))
}

/// The wire shape the webview reads, shared by [`list`] and [`CHANGED_EVENT`] so the two cannot
/// drift apart.
fn pairs(shortcuts: &[Shortcut]) -> Vec<(String, String)> {
    shortcuts
        .iter()
        .map(|s| (s.id().to_string(), s.trigger_description().to_string()))
        .collect()
}

/// Open the desktop's own shortcut editor. On Wayland the compositor owns the binding, so this
/// replaces the in-app key-capture widget rather than supplementing it.
pub async fn configure(app: &AppHandle) -> Result<(), String> {
    let state = app.state::<PortalState>().inner.clone();
    let mut slot = state.lock().await;
    let portal = ensure(app, &mut slot).await?;
    portal
        .proxy
        .configure_shortcuts(&portal.session, None, ConfigureShortcutsOptions::default())
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
