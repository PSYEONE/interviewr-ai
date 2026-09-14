// Global shortcuts: saved locally and registered with the Tauri plugin, or bound through the desktop portal on Wayland.

import { register, unregister, unregisterAll } from "@tauri-apps/plugin-global-shortcut";
import { listen } from "@tauri-apps/api/event";
import { invoke } from "@tauri-apps/api/core";

import { session } from "./session.svelte";
import { windowControls } from "./windowControls.svelte";

export type HotkeyAction =
  | "answer"
  | "clear"
  | "reset"
  | "pin"
  | "stealth"
  | "answer_style"
  | "collapse";
export const HOTKEY_ACTIONS: HotkeyAction[] = [
  "answer",
  "clear",
  "reset",
  "pin",
  "stealth",
  "answer_style",
  "collapse",
];
export const HOTKEY_LABELS: Record<HotkeyAction, string> = {
  answer: "Answer",
  clear: "Clear",
  reset: "Reset",
  pin: "Pin",
  stealth: "Stealth",
  answer_style: "Toggle answer style",
  collapse: "Collapse",
};

const STORAGE_KEY = "interviewr.hotkeys";
// Portal path: remembers that the user turned shortcuts on, which a new portal session can't report.
const PORTAL_OPT_IN_KEY = "interviewr.shortcuts.enabled";

// "portal" = the desktop owns the keys (Wayland), "plugin" = we register them (X11/Windows/macOS), "none" = unavailable.
export type Backend = "portal" | "plugin" | "none";

// Shown in the desktop's own shortcut dialog, so each one should make sense to someone new to the app.
const PORTAL_DESCRIPTIONS: Record<HotkeyAction, string> = {
  answer: "Answer the current question",
  clear: "Clear the pending transcript",
  reset: "Start a fresh conversation",
  pin: "Pin the overlay on top",
  stealth: "Hide the overlay from screen capture",
  answer_style: "Switch between script and bullet answers",
  collapse: "Collapse the overlay to its bars",
};

/** Turns the portal's "An app id is required" error into advice the user can act on; null for other errors. */
async function explain(raw: string): Promise<string | null> {
  if (!raw.includes("An app id is required")) return null;
  const problem = await invoke<string | null>("shortcuts_app_id_problem").catch(() => null);
  return problem ?? null;
}

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
    case "answer_style":
      session.toggleAnswerStyle();
      break;
    case "collapse":
      void windowControls.toggleCollapse();
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
    answer_style: null,
    collapse: null,
  });
  error = $state<string | null>(null);
  // Null until init() has checked which backend works here; Settings shows different controls for each.
  backend = $state<Backend | null>(null);
  // Portal path only: what the compositor actually has bound, keyed by action.
  triggers = $state<Partial<Record<HotkeyAction, string>>>({});
  enabled = $state(false);
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
    this.backend = await invoke<Backend>("shortcuts_backend").catch(() => "plugin" as Backend);

    if (this.backend === "portal") {
      await this.#initPortal();
      return;
    }
    if (this.backend === "none") {
      this.error =
        "This desktop has no way to register a global shortcut, so hotkeys are unavailable.";
      return;
    }

    this.#load();
    // Clear existing registrations first, so a dev reload doesn't fail registering them again.
    await unregisterAll().catch(() => {});
    const failed: string[] = [];
    for (const a of HOTKEY_ACTIONS) {
      const accel = this.bindings[a];
      if (!accel) continue;
      try {
        await this.#register(a, accel);
      } catch {
        failed.push(`${HOTKEY_LABELS[a]} (${accel})`);
      }
    }
    // Report shortcuts that failed to register, so Settings doesn't show them as working.
    if (failed.length > 0) {
      this.error = `Not active, the combination is already taken: ${failed.join(", ")}`;
    }
  }

  /** Portal path: listens for shortcut events, and re-binds only if the user enabled shortcuts before. */
  async #initPortal(): Promise<void> {
    await listen<string>("shortcut://activated", (e) => {
      if (HOTKEY_ACTIONS.includes(e.payload as HotkeyAction)) {
        dispatch(e.payload as HotkeyAction);
      }
    });
    // Picks up key changes the user makes in the desktop's own shortcut editor.
    await listen<[string, string][]>("shortcut://changed", (e) => this.#applyTriggers(e.payload));
    if (localStorage.getItem(PORTAL_OPT_IN_KEY) !== "1") return;

    // Check what the desktop still has stored before binding, so no permission dialog pops up at startup (verified on KDE).
    let listed: [string, string][];
    try {
      listed = await invoke<[string, string][]>("shortcuts_list");
    } catch (e) {
      const raw = e instanceof Error ? e.message : String(e);
      this.#forget((await explain(raw)) ?? `Couldn't reach your desktop's shortcuts: ${raw}`);
      return;
    }
    this.#applyTriggers(listed);
    if (Object.keys(this.triggers).length === 0) {
      this.#forget("Your desktop no longer has these registered. Enable them again to restore them.");
      return;
    }
    await this.enablePortal();
  }

  /** Resets to "not enabled" and shows `message`, so Settings offers the Enable button again. */
  #forget(message: string | null): void {
    this.enabled = false;
    localStorage.removeItem(PORTAL_OPT_IN_KEY);
    this.triggers = {};
    this.error = message;
  }

  #applyTriggers(listed: [string, string][]): void {
    const next: Partial<Record<HotkeyAction, string>> = {};
    for (const [id, trigger] of listed) {
      if (!HOTKEY_ACTIONS.includes(id as HotkeyAction)) continue;
      // Empty triggers are kept (registered but no key set); KDE separates multiple keys with tabs, so join them with commas.
      next[id as HotkeyAction] = trigger.split(/[\t\n]+/).filter(Boolean).join(", ");
    }
    this.triggers = next;
  }

  /** Bind with the compositor. First call raises the permission dialog; later ones do not. */
  async enablePortal(): Promise<void> {
    this.error = null;
    try {
      await invoke("shortcuts_bind", {
        shortcuts: HOTKEY_ACTIONS.map((a) => [a, PORTAL_DESCRIPTIONS[a]]),
      });
      this.enabled = true;
      localStorage.setItem(PORTAL_OPT_IN_KEY, "1");
      await this.refreshTriggers();
    } catch (e) {
      // Binding failed, so go back to offering the Enable button.
      const raw = e instanceof Error ? e.message : String(e);
      this.#forget((await explain(raw)) ?? `Couldn't register shortcuts with the desktop: ${raw}`);
    }
  }

  /** Re-reads the keys the desktop has bound, since the user can change them outside the app. */
  async refreshTriggers(): Promise<void> {
    try {
      this.#applyTriggers(await invoke<[string, string][]>("shortcuts_list"));
    } catch {
      /* leave the last known triggers; this is display only */
    }
  }

  /** Portal path: opens the desktop's own shortcut editor, the only way to set keys on Wayland. */
  async configure(): Promise<void> {
    this.error = null;
    try {
      await invoke("shortcuts_configure");
      // Refresh the displayed keys.
      await this.refreshTriggers();
    } catch (e) {
      this.error = e instanceof Error ? e.message : String(e);
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
    this.error = null; // clear any warning about the binding being removed
    const accel = this.bindings[action];
    if (accel) await unregister(accel).catch(() => {});
    this.bindings[action] = null;
    this.#save();
  }
}

export const hotkeys = new Hotkeys();
