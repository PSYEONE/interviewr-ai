// Shared pin / stealth window state so the bar buttons and global hotkeys stay in sync. Wraps the
// Rust commands and holds the live toggle state in one place (a Svelte 5 runes store).

import { tick } from "svelte";

import {
  getCaptureSupported,
  getInnerSize,
  getPin,
  getStealth,
  setInnerSize,
  setMinInnerSize,
  setPin,
  setStealth,
} from "./tauri";

// Combined height of the two interview bars (logical px), measured after content is hidden,
// plus the 1px body border top+bottom. The collapsed strip resizes to exactly this.
function barsHeight(): number {
  const bar = document.getElementById("bar");
  const controls = document.getElementById("controls");
  return (bar?.offsetHeight ?? 0) + (controls?.offsetHeight ?? 0) + 2;
}

class WindowControls {
  pinned = $state(false);
  stealth = $state(false);
  captureSupported = $state(true);
  collapsed = $state(false);
  #restoreSize: { width: number; height: number } | null = null;
  #inited = false;

  async init(): Promise<void> {
    if (this.#inited) return;
    this.#inited = true;
    try {
      this.pinned = await getPin();
      // The persisted KWin keep-above rule should auto-apply at window creation, but on Wayland a
      // fresh window does not always pick it up; re-assert it so a pinned preference actually pins.
      if (this.pinned) await setPin(true);
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

  // Collapse the overlay to just its two bars (hide transcript + answer, shrink the window),
  // or expand back to the remembered size. Only meaningful in the interview view, which owns
  // the #controls bar; a hotkey pressed elsewhere is a no-op.
  async toggleCollapse(): Promise<void> {
    if (!document.getElementById("controls")) return; // interview view only
    try {
      if (!this.collapsed) {
        this.#restoreSize = await getInnerSize();
        this.collapsed = true;
        document.body.classList.add("collapsed");
        await tick(); // let the content hide so barsHeight measures only the bars
        await setMinInnerSize(360, 1); // drop the min floor so the height can shrink to the bars
        await setInnerSize(this.#restoreSize.width, barsHeight());
      } else {
        this.collapsed = false;
        document.body.classList.remove("collapsed");
        if (this.#restoreSize) {
          await setInnerSize(this.#restoreSize.width, this.#restoreSize.height);
        }
        await setMinInnerSize(360, 280); // restore the normal min floor
      }
    } catch {
      /* compositor/window may reject; state still reflects intent */
    }
  }

  // Leaving the interview while collapsed must restore the window, or the dashboard is stranded
  // at strip height. Restores from the saved size without measuring (the bars may be gone).
  resetCollapse(): void {
    if (!this.collapsed) return;
    this.collapsed = false;
    document.body.classList.remove("collapsed");
    if (this.#restoreSize) void setInnerSize(this.#restoreSize.width, this.#restoreSize.height);
    void setMinInnerSize(360, 280); // restore the normal min floor
  }
}

export const windowControls = new WindowControls();
