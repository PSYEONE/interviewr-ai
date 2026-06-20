// Shared pin / stealth window state so the bar buttons and global hotkeys stay in sync. Wraps the
// Rust commands and holds the live toggle state in one place (a Svelte 5 runes store).

import { getCaptureSupported, getPin, getStealth, setPin, setStealth } from "./tauri";

class WindowControls {
  pinned = $state(false);
  stealth = $state(false);
  captureSupported = $state(true);
  #inited = false;

  async init(): Promise<void> {
    if (this.#inited) return;
    this.#inited = true;
    try {
      this.pinned = await getPin();
    } catch {
      /* keep default */
    }
    try {
      this.captureSupported = await getCaptureSupported();
      if (this.captureSupported) {
        this.stealth = await getStealth();
        // On KWin 6.6 the runtime property is lost on restart; re-apply the stored choice now.
        if (this.stealth) await setStealth(true);
      }
    } catch {
      this.captureSupported = false;
    }
  }

  async togglePin(): Promise<void> {
    this.pinned = !this.pinned;
    try {
      await setPin(this.pinned);
    } catch {
      /* compositor may reject; the toggle reflects intent */
    }
  }

  async toggleStealth(): Promise<void> {
    if (!this.captureSupported) return;
    this.stealth = !this.stealth;
    try {
      await setStealth(this.stealth);
    } catch {
      /* ignore */
    }
  }
}

export const windowControls = new WindowControls();
