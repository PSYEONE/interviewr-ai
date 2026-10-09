// Ollama refuses a pull mid-stream on a 200 response, so pullModel has to turn that line into a failure.

import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("./connection", () => ({
  httpBase: async () => "http://engine",
  authHeaders: async () => ({}),
}));
vi.mock("./tauri", () => ({ saveText: vi.fn() }));
const { getSystemInfo, pullModel } = await import("./api");

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

describe("getSystemInfo", () => {
  function reportStub(usable: number): Response {
    return new Response(JSON.stringify({ recommendation: { usable_vram_mb: usable } }), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
  }

  it("caches the saved-settings report", async () => {
    const fetchMock = vi.fn(async () => reportStub(6530));
    vi.stubGlobal("fetch", fetchMock);
    await getSystemInfo();
    await getSystemInfo();
    expect(fetchMock).toHaveBeenCalledTimes(1); // the hardware probe is slow
  });

  it("neither reads nor writes the cache when labels are overridden", async () => {
    // Sharing one cache key is how the wizard and the model manager came to disagree: an
    // overridden report describes a configuration that is not saved.
    const fetchMock = vi.fn(async (_url: RequestInfo | URL) => reportStub(5280));
    vi.stubGlobal("fetch", fetchMock);
    await getSystemInfo(); // seeds the cache
    fetchMock.mockClear();

    const overridden = await getSystemInfo(false, { whisperModel: "medium.en" });
    expect(fetchMock).toHaveBeenCalledTimes(1); // went to the network despite a warm cache
    expect(String(fetchMock.mock.calls[0]?.[0])).toContain("whisper_model=medium.en");
    expect(overridden.recommendation.usable_vram_mb).toBe(5280);

    fetchMock.mockClear();
    await getSystemInfo(); // the cached report must be untouched
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("sends the wizard's assume flag", async () => {
    const fetchMock = vi.fn(async (_url: RequestInfo | URL) => reportStub(6530));
    vi.stubGlobal("fetch", fetchMock);
    await getSystemInfo(true, { assumeRecommendedLlm: true });
    expect(String(fetchMock.mock.calls[0]?.[0])).toContain("assume_recommended_llm=true");
  });
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
