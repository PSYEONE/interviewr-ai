//! Global shortcuts: the XDG portal on Wayland, the Tauri plugin on X11/Windows/macOS.

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

/// Emitted when the desktop changes a trigger, with the same payload as [`list`].
pub const CHANGED_EVENT: &str = "shortcut://changed";

/// Which mechanism can actually deliver a global shortcut here.
pub fn backend() -> &'static str {
    decide(is_wayland(), portal_present)
}

/// Picks the backend; `portal` is only probed on Wayland.
fn decide(wayland: bool, portal: impl FnOnce() -> bool) -> &'static str {
    if !wayland {
        // X11, including the app forced onto XWayland: the plugin's grabs work.
        "plugin"
    } else if portal() {
        "portal"
    } else {
        // Wayland without the portal: nothing can bind a global key.
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

/// Portal proxy and session; dropping either unbinds every shortcut.
struct Portal {
    proxy: GlobalShortcuts,
    session: ShortcutSession,
}

#[derive(Default)]
pub struct PortalState {
    inner: Arc<Mutex<Option<Portal>>>,
}

/// Creates the portal session on first use and forwards activation and change signals to the webview.
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

/// Binds (id, description) pairs; the compositor picks the actual keys.
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

/// Current bindings as (action id, trigger description); works before anything is bound.
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

/// Shared wire format for [`list`] and [`CHANGED_EVENT`].
fn pairs(shortcuts: &[Shortcut]) -> Vec<(String, String)> {
    shortcuts
        .iter()
        .map(|s| (s.id().to_string(), s.trigger_description().to_string()))
        .collect()
}

/// Opens the desktop's own shortcut editor.
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
        // The plugin would report success here and never fire.
        assert_eq!(decide(true, || false), "none");
    }
}
