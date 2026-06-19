// Typed wrappers over the overlay's Rust commands (overlay/src-tauri/src/lib.rs) and the
// window drag. No new Rust commands are added in this phase.

import { invoke } from "@tauri-apps/api/core";
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
export const startDragging = (): Promise<void> => getCurrentWindow().startDragging();
export const minimizeWindow = (): Promise<void> => getCurrentWindow().minimize();
