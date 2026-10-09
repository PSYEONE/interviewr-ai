// Ollama refuses a pull mid-stream on a 200 response, so pullModel has to turn that line into a failure.

import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("./connection", () => ({
  httpBase: async () => "http://engine",
  authHeaders: async () => ({}),
}));
vi.mock("./tauri", () => ({ saveText: vi.fn() }));
const { pullModel } = await import("./api");

function ndjson(...lines: object[]): Response {
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      const text = lines.map((l) => JSON.stringify(l)).join("\n") + "\n";
      controller.enqueue(new TextEncoder().encode(text));
      controller.close();
    },
  });
  return new Response(body, { status: 200 });
}

beforeEach(() => {
  vi.restoreAllMocks();
});

describe("pullModel", () => {
  it("reports each progress line", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        ndjson(
          { status: "pulling manifest", percent: null },
          { status: "pulling abc", percent: 50 },
          { status: "success", percent: 100 },
        ),
      ),
    );
    const seen: string[] = [];
    await pullModel("gemma3:4b", (p) => seen.push(p.status));
    expect(seen).toEqual(["pulling manifest", "pulling abc", "success"]);
  });

  it("throws when the stream carries an error instead of ending quietly", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        ndjson(
          { status: "pulling manifest" },
          { status: "error", error: "pull model manifest: 412: requires a newer version of Ollama" },
        ),
      ),
    );
    await expect(pullModel("gemma3:4b", () => {})).rejects.toThrow(/newer version of Ollama/);
  });

  it("keeps the http status on a rejected request, so 404 stays distinguishable", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("no such model", { status: 404 })));
    await expect(pullModel("nope:1b", () => {})).rejects.toMatchObject({ status: 404 });
  });
});
