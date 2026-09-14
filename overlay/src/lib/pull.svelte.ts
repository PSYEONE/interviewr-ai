// The current model download, kept outside the screens so it can be shown and cancelled from anywhere.

import { pullModel } from "./api";

export type ActivePull = { model: string; pct: number; status: string };

class Pull {
  active = $state<ActivePull | null>(null);
  // Tag being resolved before any download starts. Kept separate from `active` so an unknown
  // model is rejected without ever flashing a progress bar.
  checking = $state<string | null>(null);
  // Set briefly between the user hitting Cancel and the request actually unwinding.
  cancelling = $state(false);
  // Increases whenever a download ends, however it ends, so any open screen can refresh its model list.
  settled = $state(0);

  #controller: AbortController | null = null;

  get busy(): boolean {
    return this.active !== null || this.checking !== null;
  }

  /** Run a pull to completion. Resolves "done", "cancelled", or throws a real failure.
   *  Cancellation is a normal outcome, not an error: callers must not surface it as one. */
  async run(model: string): Promise<"done" | "cancelled"> {
    if (this.busy || !model) return "cancelled"; // one pull at a time, process-wide
    const controller = new AbortController();
    this.#controller = controller;
    this.cancelling = false;
    this.checking = model; // the tag is being resolved; no progress bar yet
    try {
      await pullModel(
        model,
        (p) => {
          if (this.active?.model !== model) return;
          this.active = {
            model,
            status: p.status,
            pct: p.percent ?? this.active.pct,
          };
        },
        controller.signal,
        () => {
          this.checking = null;
          this.active = { model, pct: 0, status: "starting…" };
        },
      );
      return "done";
    } catch (e) {
      if (controller.signal.aborted || (e instanceof DOMException && e.name === "AbortError")) {
        return "cancelled";
      }
      throw e;
    } finally {
      this.active = null;
      this.checking = null;
      this.cancelling = false;
      this.#controller = null;
      this.settled += 1; // last, so a listener that reads busy sees the pull already finished
    }
  }

  cancel(): void {
    if (!this.#controller) return;
    this.cancelling = true;
    if (this.active) this.active = { ...this.active, status: "cancelling…" };
    this.#controller.abort();
  }
}

export const pull = new Pull();
