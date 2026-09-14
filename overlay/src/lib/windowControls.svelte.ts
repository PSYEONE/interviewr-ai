// Shared pin, stealth and collapse state, so the bar buttons and global shortcuts stay in sync.

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

// Height of the two interview bars plus the 1px border top and bottom; the collapsed window uses this height.
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
      // Re-apply the pin at startup, since a new Wayland window doesn't always pick up the KWin rule.
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

  // Collapses the overlay to just its two bars, or expands it back to the saved size (interview view only).
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

  // Restores the window size when leaving the interview while collapsed.
  resetCollapse(): void {
    if (!this.collapsed) return;
    this.collapsed = false;
    document.body.classList.remove("collapsed");
    if (this.#restoreSize) void setInnerSize(this.#restoreSize.width, this.#restoreSize.height);
    void setMinInnerSize(360, 280); // restore the normal min floor
  }
}

export const windowControls = new WindowControls();
