// The reasoning toggle must stay hidden unless the model really has a reasoning mode, so nobody
// thinks their 3B model is thinking.

import { flushSync, mount, unmount } from "svelte";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { session } from "../lib/session.svelte";
import Controls from "./Controls.svelte";

let target: HTMLDivElement;
let app: Record<string, unknown> | null = null;

async function render(): Promise<void> {
  app = mount(Controls, { target }) as Record<string, unknown>;
  for (let i = 0; i < 20; i++) await Promise.resolve();
  flushSync();
}

function buttonLabels(): string[] {
  return [...target.querySelectorAll("button")].map((b) => (b.textContent ?? "").trim());
}

function reasoningButton(): HTMLButtonElement {
  const found = [...target.querySelectorAll<HTMLButtonElement>("button")].find((b) =>
    ["On", "Off"].includes((b.textContent ?? "").trim()),
  );
  if (!found) throw new Error("reasoning button not rendered");
  return found;
}

beforeEach(() => {
  session.canThink = false;
  session.setReasoning(false);
  target = document.createElement("div");
  document.body.appendChild(target);
});

afterEach(() => {
  if (app) void unmount(app);
  app = null;
  target.remove();
});

describe("the reasoning toggle", () => {
  it("is absent when the model has no reasoning mode", async () => {
    await render();
    expect(buttonLabels()).toContain("Answer"); // the rest of the bar still rendered
    expect(buttonLabels()).not.toContain("Off");
  });

  it("appears when the engine says the model can reason", async () => {
    session.canThink = true;
    await render();
    expect(reasoningButton().textContent?.trim()).toBe("Off");
  });

  it("flips the session setting when clicked", async () => {
    session.canThink = true;
    await render();
    reasoningButton().click();
    flushSync();
    expect(session.reasoning).toBe(true);
    expect(reasoningButton().textContent?.trim()).toBe("On");
    // The lit-up state is what tells the user it is costing them latency.
    expect(reasoningButton().classList.contains("on")).toBe(true);
  });
});
