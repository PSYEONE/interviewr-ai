// Typed wrappers over the overlay's Rust commands (overlay/src-tauri/src/lib.rs) and the
// window drag.

import { invoke } from "@tauri-apps/api/core";
import { listen, type UnlistenFn } from "@tauri-apps/api/event";
import { getCurrentWindow } from "@tauri-apps/api/window";

import type { Connection } from "./types";

export const getConnection = (): Promise<Connection> => invoke<Connection>("get_connection");
export const getPin = (): Promise<boolean> => invoke<boolean>("get_pin");
export const setPin = (on: boolean): Promise<boolean> => invoke<boolean>("set_pin", { on });
export const getCaptureHidden = (): Promise<boolean> => invoke<boolean>("get_capture_hidden");
export const setCaptureHidden = (on: boolean): Promise<boolean> =>
  invoke<boolean>("set_capture_hidden", { on });
export const getCaptureSupported = (): Promise<boolean> =>
  invoke<boolean>("get_capture_supported");
export const quit = (): Promise<void> => invoke("quit");

// Save text to a user-chosen file via a native dialog (the webview's own download is unreliable
// on WebKitGTK). Resolves false if the user cancels.
export const saveText = (name: string, contents: string): Promise<boolean> =>
  invoke<boolean>("save_text", { name, contents });
export const startDragging = (): Promise<void> => getCurrentWindow().startDragging();
export const minimizeWindow = (): Promise<void> => getCurrentWindow().minimize();

// Engine sidecar. restartEngine rejects with "not_managed" when an external/manual backend
// owns the port (dev), so callers can fall back to a manual-restart prompt.
export const restartEngine = (): Promise<void> => invoke("restart_engine");
export const engineManaged = (): Promise<boolean> => invoke<boolean>("engine_managed");

// Last fatal engine failure, or null. Polled in the offline path to recover a failure that was
// emitted (engine://failed) before this webview attached its listener (e.g. an initial-spawn
// error at startup).
export const engineError = (): Promise<string | null> =>
  invoke<string | null>("engine_error");

// Fired by Rust when the engine gives up (crash-loop) or fails to start; payload is the
// stderr tail so the UI can tell the user what actually went wrong.
export function onEngineFailed(cb: (message: string) => void): Promise<UnlistenFn> {
  return listen<{ message: string }>("engine://failed", (e) => cb(e.payload.message));
}
