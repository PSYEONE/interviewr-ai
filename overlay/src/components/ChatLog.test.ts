// The empty log is the one place that says what the engine is doing, so it must never claim to be
// listening while the speech model is still loading.

import { flushSync, mount, unmount } from "svelte";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { session } from "../lib/session.svelte";
import ChatLog from "./ChatLog.svelte";

let target: HTMLDivElement;
let app: Record<string, unknown> | null = null;

async function render(): Promise<void> {
  app = mount(ChatLog, { target }) as Record<string, unknown>;
  for (let i = 0; i < 20; i++) await Promise.resolve();
  flushSync();
}

function text(): string {
  return (target.textContent ?? "").replace(/\s+/g, " ").trim();
}

beforeEach(() => {
  session.turns = [];
  session.pending = "";
  session.live = "";
  session.sttReady = true;
  session.sttPercent = 0;
  target = document.createElement("div");
  document.body.appendChild(target);
});

afterEach(() => {
  if (app) void unmount(app);
  app = null;
  target.remove();
});

describe("the empty log", () => {
  it("invites a question once the model is ready", async () => {
    await render();
    expect(text()).toBe("Listening. Ask a question, then hit Answer.");
  });

  it("says the model is starting instead of claiming to listen", async () => {
    // The fault this replaced: a banner saying "Preparing the speech model" sat directly above
    // "Listening. Ask a question", and only one of them was true.
    session.sttReady = false;
    await render();
    expect(text()).toBe("Starting the speech model.");
    expect(text()).not.toContain("Listening");
  });

  it("reports progress while the model is downloading", async () => {
    session.sttReady = false;
    session.sttPercent = 42;
    await render();
    expect(text()).toContain("Downloading the speech model (42%)");
  });

  it("says nothing once there is a turn to read", async () => {
    session.turns = [
      { id: "a1", question: "q", answer: "a", done: true, reasoning: "", thoughtMs: null },
    ];
    session.sttReady = false;
    await render();
    expect(text()).not.toContain("Listening");
    expect(text()).not.toContain("speech model");
  });
});
