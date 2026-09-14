// pull.settled must go up however a download ends, because that's how the model picker knows to refresh.

import { beforeEach, describe, expect, it, vi } from "vitest";

import { pull } from "./pull.svelte";

vi.mock("./api", () => ({ pullModel: vi.fn() }));
const { pullModel } = await import("./api");
const mocked = vi.mocked(pullModel);

beforeEach(() => {
  pull.settled = 0;
  pull.lastDone = null;
  mocked.mockReset();
});

describe("pull.settled", () => {
  it("bumps once when a pull completes", async () => {
    mocked.mockImplementation(async (_m, _p, _s, onStart) => {
      onStart?.();
    });

    await expect(pull.run("gemma3:4b")).resolves.toBe("done");
    expect(pull.settled).toBe(1);
  });

  it("bumps when a pull is cancelled", async () => {
    // Cancel resolves "cancelled" rather than throwing, so a finally-less implementation would
    // return through the happy path and never bump.
    mocked.mockImplementation(async (_m, _p, _s, onStart) => {
      onStart?.();
      pull.cancel();
      throw new DOMException("aborted", "AbortError");
    });

    await expect(pull.run("gemma3:4b")).resolves.toBe("cancelled");
    expect(pull.settled).toBe(1);
  });

  it("bumps when a pull fails", async () => {
    mocked.mockRejectedValue(new Error("pull gemma3:4b -> 500"));

    await expect(pull.run("gemma3:4b")).rejects.toThrow("500");
    expect(pull.settled).toBe(1);
  });

  it("leaves the store idle afterwards, so the next pull is allowed", async () => {
    mocked.mockRejectedValue(new Error("boom"));
    await expect(pull.run("a")).rejects.toThrow("boom");

    expect(pull.busy).toBe(false);
    expect(pull.active).toBeNull();
    expect(pull.cancelling).toBe(false);
  });

  it("does not bump for a call refused because another pull is running", async () => {
    // The one-at-a-time guard returns before any work, so nothing settled.
    let release = (): void => {};
    mocked.mockImplementation(
      async (_m, _p, _s, onStart) =>
        new Promise<void>((resolve) => {
          onStart?.();
          release = resolve;
        }),
    );

    const first = pull.run("a");
    await expect(pull.run("b")).resolves.toBe("cancelled");
    expect(pull.settled).toBe(0);

    release();
    await first;
    expect(pull.settled).toBe(1);
  });
});

describe("pull.lastDone", () => {
  it("names the model after a successful pull", async () => {
    mocked.mockImplementation(async (_m, _p, _s, onStart) => {
      onStart?.();
    });

    await pull.run("gemma3:4b");
    expect(pull.lastDone).toBe("gemma3:4b");
  });

  it("is cleared when the next pull is cancelled", async () => {
    mocked.mockResolvedValueOnce(undefined);
    await pull.run("gemma3:4b");

    mocked.mockImplementation(async (_m, _p, _s, onStart) => {
      onStart?.();
      pull.cancel();
      throw new DOMException("aborted", "AbortError");
    });
    await pull.run("qwen3:8b");
    expect(pull.lastDone).toBeNull();
  });

  it("stays null when a pull fails", async () => {
    mocked.mockRejectedValue(new Error("pull gemma3:4b -> 500"));

    await expect(pull.run("gemma3:4b")).rejects.toThrow("500");
    expect(pull.lastDone).toBeNull();
  });
});
