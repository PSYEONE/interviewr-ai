// Typed wrappers over the overlay's Rust commands (overlay/src-tauri/src/lib.rs) and the
// window drag.

import { invoke } from "@tauri-apps/api/core";
import { listen, type UnlistenFn } from "@tauri-apps/api/event";
import { getCurrentWindow, LogicalSize } from "@tauri-apps/api/window";

import type { Connection } from "./types";

export const getConnection = (): Promise<Connection> => invoke<Connection>("get_connection");
export const getPin = (): Promise<boolean> => invoke<boolean>("get_pin");
export const setPin = (on: boolean): Promise<boolean> => invoke<boolean>("set_pin", { on });
export const getStealth = (): Promise<boolean> => invoke<boolean>("get_stealth");
export const setStealth = (on: boolean): Promise<boolean> => invoke<boolean>("set_stealth", { on });
export const getCaptureSupported = (): Promise<boolean> =>
  invoke<boolean>("get_capture_supported");
export const quit = (): Promise<void> => invoke("quit");

// Save text to a user-chosen file via a native dialog (the webview's own download is unreliable
// on WebKitGTK). Resolves false if the user cancels.
export const saveText = (name: string, contents: string): Promise<boolean> =>
  invoke<boolean>("save_text", { name, contents });
export const startDragging = (): Promise<void> => getCurrentWindow().startDragging();
export const minimizeWindow = (): Promise<void> => getCurrentWindow().minimize();

// Window size in logical px (innerSize is physical; divide by the scale factor). Used by the
// collapse-to-bar toggle to remember and restore the overlay's size.
export async function getInnerSize(): Promise<{ width: number; height: number }> {
  const win = getCurrentWindow();
  const [size, scale] = await Promise.all([win.innerSize(), win.scaleFactor()]);
  return { width: size.width / scale, height: size.height / scale };
}
export const setInnerSize = (width: number, height: number): Promise<void> =>
  getCurrentWindow().setSize(new LogicalSize(width, height));
// Lower the window's minimum size at runtime so a config minHeight can't clamp the collapse.
export const setMinInnerSize = (width: number, height: number): Promise<void> =>
  getCurrentWindow().setMinSize(new LogicalSize(width, height));

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
