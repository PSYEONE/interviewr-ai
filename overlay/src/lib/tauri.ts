// Typed wrappers for the overlay's Rust commands (src-tauri/src/lib.rs) and window controls.

import { invoke } from "@tauri-apps/api/core";
import { listen, type UnlistenFn } from "@tauri-apps/api/event";
import { getCurrentWindow, LogicalSize } from "@tauri-apps/api/window";

import type { Connection } from "./types";

export const getConnection = (): Promise<Connection> => invoke<Connection>("get_connection");
export const getPin = (): Promise<boolean> => invoke<boolean>("get_pin");
export const getPinSupported = (): Promise<boolean> => invoke<boolean>("get_pin_supported");
export const setPin = (on: boolean): Promise<boolean> => invoke<boolean>("set_pin", { on });
export const getStealth = (): Promise<boolean> => invoke<boolean>("get_stealth");
export const setStealth = (on: boolean): Promise<boolean> => invoke<boolean>("set_stealth", { on });
export const getCaptureSupported = (): Promise<boolean> =>
  invoke<boolean>("get_capture_supported");
export const quit = (): Promise<void> => invoke("quit");
// Tray icon state: blue while the engine is listening or thinking, grey otherwise.
export const setLive = (live: boolean): Promise<void> => invoke("set_live", { live });
// Opens a donation or feedback page in the browser; the Rust side only allows known links.
export const openLink = (url: string): Promise<void> => invoke("open_link", { url });

// Saves text through a native Save dialog (webview downloads are unreliable on WebKitGTK); false if cancelled.
export const saveText = (name: string, contents: string): Promise<boolean> =>
  invoke<boolean>("save_text", { name, contents });
export const startDragging = (): Promise<void> => getCurrentWindow().startDragging();
export const minimizeWindow = (): Promise<void> => getCurrentWindow().minimize();

// Window size in logical pixels; used to remember and restore the size when collapsing.
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

// Rejects with "not_managed" when the engine was started by hand, so the UI can ask for a manual restart.
export const restartEngine = (): Promise<void> => invoke("restart_engine");
export const engineManaged = (): Promise<boolean> => invoke<boolean>("engine_managed");

// Last fatal engine error, for a failure that happened before the UI started listening.
export const engineError = (): Promise<string | null> =>
  invoke<string | null>("engine_error");

// Fires when the engine fails to start or keeps crashing; the payload is its recent error output.
export function onEngineFailed(cb: (message: string) => void): Promise<UnlistenFn> {
  return listen<{ message: string }>("engine://failed", (e) => cb(e.payload.message));
}
