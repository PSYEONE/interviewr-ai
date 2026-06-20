// Customizable global shortcuts. Actions start unbound; the user assigns accelerators in Settings,
// persisted locally (localStorage, an overlay concern, not engine .env) and registered with the
// Tauri global-shortcut plugin. Callbacks dispatch to the session / window-control stores.

import { register, unregister } from "@tauri-apps/plugin-global-shortcut";

import { session } from "./session.svelte";
import { windowControls } from "./windowControls.svelte";

export type HotkeyAction = "answer" | "clear" | "reset" | "pin" | "stealth";
export const HOTKEY_ACTIONS: HotkeyAction[] = ["answer", "clear", "reset", "pin", "stealth"];
export const HOTKEY_LABELS: Record<HotkeyAction, string> = {
  answer: "Answer",
  clear: "Clear",
  reset: "Reset",
  pin: "Pin",
  stealth: "Stealth",
};

const STORAGE_KEY = "interviewr.hotkeys";

function dispatch(action: HotkeyAction): void {
  switch (action) {
    case "answer":
      session.send("answer");
      break;
    case "clear":
      session.send("clear");
      break;
    case "reset":
      session.resetView();
      break;
    case "pin":
      void windowControls.togglePin();
      break;
    case "stealth":
      void windowControls.toggleStealth();
      break;
  }
}

class Hotkeys {
  bindings = $state<Record<HotkeyAction, string | null>>({
    answer: null,
    clear: null,
    reset: null,
    pin: null,
    stealth: null,
  });
  error = $state<string | null>(null);
  #inited = false;

  #load(): void {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (!raw) return;
      const saved = JSON.parse(raw) as Partial<Record<HotkeyAction, string | null>>;
      for (const a of HOTKEY_ACTIONS) this.bindings[a] = saved[a] ?? null;
    } catch {
      /* ignore corrupt storage */
    }
  }

  #save(): void {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(this.bindings));
    } catch {
      /* non-fatal */
    }
  }

  async #register(action: HotkeyAction, accel: string): Promise<void> {
    await register(accel, (e) => {
      if (e.state === "Pressed") dispatch(action);
    });
  }

  async init(): Promise<void> {
    if (this.#inited) return;
    this.#inited = true;
    this.#load();
    for (const a of HOTKEY_ACTIONS) {
      const accel = this.bindings[a];
      if (accel) {
        try {
          await this.#register(a, accel);
        } catch {
          /* a saved combo may now be taken; leave it bound in the UI, just not active */
        }
      }
    }
  }

  async setBinding(action: HotkeyAction, accel: string): Promise<void> {
    this.error = null;
    const prev = this.bindings[action];
    try {
      if (prev) await unregister(prev).catch(() => {});
      // One accelerator per action: if another action already holds it, free it first.
      for (const a of HOTKEY_ACTIONS) {
        if (a !== action && this.bindings[a] === accel) {
          await unregister(accel).catch(() => {});
          this.bindings[a] = null;
        }
      }
      await this.#register(action, accel);
      this.bindings[action] = accel;
      this.#save();
    } catch (e) {
      this.error = `Couldn't bind ${accel}: ${e instanceof Error ? e.message : String(e)}`;
      if (prev) await this.#register(action, prev).catch(() => {}); // restore the old binding
    }
  }

  async clearBinding(action: HotkeyAction): Promise<void> {
    const accel = this.bindings[action];
    if (accel) await unregister(accel).catch(() => {});
    this.bindings[action] = null;
    this.#save();
  }
}

export const hotkeys = new Hotkeys();
