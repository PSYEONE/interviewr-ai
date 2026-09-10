// Customizable global shortcuts. Actions start unbound; the user assigns accelerators in Settings,
// persisted locally (localStorage, an overlay concern, not engine .env) and registered with the
// Tauri global-shortcut plugin. Callbacks dispatch to the session / window-control stores.

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
// Portal path: whether the user has opted in. A fresh session's ListShortcuts reports nothing
// until that session has bound, so it cannot tell us about a previous grant; this can.
const PORTAL_OPT_IN_KEY = "interviewr.shortcuts.enabled";

// Which mechanism can actually deliver a shortcut. "portal" = the compositor owns the binding
// (Wayland), "plugin" = we grab it ourselves (X11/Windows/macOS), "none" = nothing can.
export type Backend = "portal" | "plugin" | "none";

// Shown by the desktop's own shortcut dialog on the portal path, so it has to read as a sentence
// to someone who has never seen this app. No "interviewr:" prefix: the desktop files these under
// the app's own component now that it can identify us, and repeats the app name itself.
const PORTAL_DESCRIPTIONS: Record<HotkeyAction, string> = {
  answer: "Answer the current question",
  clear: "Clear the pending transcript",
  reset: "Start a fresh conversation",
  pin: "Pin the overlay on top",
  stealth: "Hide the overlay from screen capture",
  answer_style: "Switch between script and bullet answers",
  collapse: "Collapse the overlay to its bars",
};

/** Turn the one portal error a user can act on into a sentence that says what to do.
 *  "An app id is required" means the desktop could not identify us; the Rust side knows which
 *  half is missing, and only it can tell them apart. */
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
  // Null until init() has asked the Rust side. The Settings UI branches on this: the portal path
  // has no per-action Record button, because the compositor owns the binding.
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
    // The grabs live in the Rust process while #inited is webview state, so a dev reload would
    // otherwise re-register over live grabs and every call would fail.
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
    // Previously swallowed, so Settings showed a dead binding as though it were live.
    if (failed.length > 0) {
      this.error = `Not active, the combination is already taken: ${failed.join(", ")}`;
    }
  }

  /** Portal path: the compositor holds the bindings, so there is nothing in localStorage to
   *  restore and nothing for us to grab. Listen always; bind only once the user has opted in,
   *  since binding is what raises the desktop's permission dialog. */
  async #initPortal(): Promise<void> {
    await listen<string>("shortcut://activated", (e) => {
      if (HOTKEY_ACTIONS.includes(e.payload as HotkeyAction)) {
        dispatch(e.payload as HotkeyAction);
      }
    });
    // The desktop owns the keys and tells us when one changes, which is the only way to follow an
    // edit made in its own editor: ConfigureShortcuts returns when the editor opens, not when the
    // user is done with it.
    await listen<[string, string][]>("shortcut://changed", (e) => this.#applyTriggers(e.payload));
    if (localStorage.getItem(PORTAL_OPT_IN_KEY) !== "1") return;

    // Ask before binding, so a permission dialog can never appear at startup. A desktop that still
    // holds our grant answers with the actions it stored, and re-binding those is silent; one that
    // has forgotten us answers with nothing, and binding would prompt for a permission the user
    // did not ask for right now.
    //
    // Verified on KDE, whose portal backend loads the stored component when the session is created.
    // A backend that stays empty until something has been bound would instead cost one click per
    // launch. That is worse, but it is visible rather than surprising, and KDE is the only one
    // testable here.
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

  /** Drop back to the un-enabled state, which is the only honest one when the desktop is not
   *  holding our shortcuts: Settings then offers Enable, and the dialog follows that click. */
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
      // An empty string is kept, not dropped: "registered with no key" and "not registered at
      // all" are different states and the UI says so. KDE separates multiple keys for one
      // action with a tab, which would otherwise render as a run of whitespace.
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
      // Nothing is bound, so nothing can be configured: stay on the Enable action rather than
      // offering an editor with nothing behind it.
      const raw = e instanceof Error ? e.message : String(e);
      this.#forget((await explain(raw)) ?? `Couldn't register shortcuts with the desktop: ${raw}`);
    }
  }

  /** Ask the compositor what it has bound. Its answer is the only truth: it owns the triggers
   *  and can change them without telling us. */
  async refreshTriggers(): Promise<void> {
    try {
      this.#applyTriggers(await invoke<[string, string][]>("shortcuts_list"));
    } catch {
      /* leave the last known triggers; this is display only */
    }
  }

  /** Portal path: hand binding to the desktop's own editor, the only thing that can set a
   *  trigger on Wayland. */
  async configure(): Promise<void> {
    this.error = null;
    try {
      await invoke("shortcuts_configure");
      // The dialog is where the keys change, so re-read rather than wait for a restart.
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
    this.error = null; // or a warning about this binding outlives the binding itself
    const accel = this.bindings[action];
    if (accel) await unregister(accel).catch(() => {});
    this.bindings[action] = null;
    this.#save();
  }
}

export const hotkeys = new Hotkeys();
