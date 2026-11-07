// Settings screen: the dropdowns show the saved values, and Save sends only what changed.

import { flushSync, mount, unmount } from "svelte";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ConfigView, WhisperOption } from "../../lib/types";
import SettingsView from "./SettingsView.svelte";

const { getSettings, getAudioApps, getSystemInfo, postSettings, reloadSettings, applyAndRestart } =
  vi.hoisted(() => ({
    getSettings: vi.fn(),
    getAudioApps: vi.fn(),
    getSystemInfo: vi.fn(),
    postSettings: vi.fn(),
    reloadSettings: vi.fn(),
    applyAndRestart: vi.fn(),
  }));

vi.mock("../../lib/api", () => ({ getSettings, getAudioApps, getSystemInfo, postSettings, reloadSettings }));
vi.mock("../../lib/apply", () => ({ applyAndRestart }));

const CONFIG: ConfigView = {
  stt_provider: "whisper_cpp",
  whisper_model: "base.en",
  whisper_language: "auto",
  whisper_gpu_device: -1,
  whisper_use_gpu: true,
  llm_provider: "ollama",
  llm_model: "llama3.2:3b",
  ollama_base_url: "http://127.0.0.1:11434",
  llm_keep_alive: "30m",
  embed_provider: "ollama",
  embed_model: "nomic-embed-text",
  rag_enabled: true,
  rag_top_k: 4,
  history_enabled: true,
  audio_capture_app: "Brave",
  system_prompt: "be terse",
  answer_style_script: "script directive",
  answer_style_bullets: "bullet directive",
  auth_token_set: false,
  anthropic_api_key_set: false,
  openai_api_key_set: false,
  gemini_api_key_set: false,
  groq_api_key_set: false,
};

function model(name: string, live_wer: number | null = null): WhisperOption {
  return {
    name,
    english_only: name.endsWith(".en"),
    download_mb: 142,
    vram_mb: 390,
    live_wer,
    speed: null,
    fit: "fits",
  };
}

const WHISPER = {
  suggested: "small.en",
  models: [model("base.en", 0.06), model("small.en", 0.048), model("small"), model("large-v3-turbo-q5_0")],
  languages: ["en", "bg", "de"],
};

let target: HTMLElement;
let app: Record<string, unknown> | null = null;

/** Mount and let onMount's awaits settle, so the form is populated before asserting. */
async function render(): Promise<void> {
  app = mount(SettingsView, { target, props: { back: () => {} } });
  for (let i = 0; i < 20; i++) await Promise.resolve();
  flushSync();
}

function select(id: string): HTMLSelectElement {
  const el = target.querySelector<HTMLSelectElement>(`#${id}`);
  if (!el) throw new Error(`${id} select not rendered`);
  return el;
}

const capture = (): HTMLSelectElement => select("capapp");

function choose(el: HTMLSelectElement, value: string): void {
  el.value = value;
  el.dispatchEvent(new Event("change"));
  flushSync();
}

async function settle(): Promise<void> {
  for (let i = 0; i < 20; i++) await Promise.resolve();
}

function clickSave(): void {
  const save = [...target.querySelectorAll("button")].find((b) => b.textContent?.trim() === "Save");
  if (!save) throw new Error("Save button not rendered");
  save.click();
}

beforeEach(() => {
  vi.clearAllMocks();
  getSettings.mockResolvedValue({ ...CONFIG });
  getAudioApps.mockResolvedValue(["Brave", "Firefox", "mpv"]);
  getSystemInfo.mockResolvedValue({ gpus: [], whisper: WHISPER });
  postSettings.mockResolvedValue(undefined);
  reloadSettings.mockResolvedValue(undefined);
  applyAndRestart.mockResolvedValue("applied");
  target = document.createElement("div");
  document.body.appendChild(target);
});

afterEach(() => {
  if (app) void unmount(app);
  app = null;
  target.remove();
});

describe("the capture dropdown", () => {
  it("shows the saved application", async () => {
    // Regression: .env said Brave but the dropdown showed Everything.
    await render();
    expect(capture().value).toBe("Brave");
  });

  it("shows it even when the app list arrives after the settings do", async () => {
    let release: (v: string[]) => void = () => {};
    getAudioApps.mockReturnValue(new Promise<string[]>((r) => (release = r)));
    await render();
    expect(capture().value).toBe("Brave");

    release(["Brave", "Firefox"]);
    for (let i = 0; i < 20; i++) await Promise.resolve();
    flushSync();
    expect(capture().value).toBe("Brave");
  });

  it("shows it even when the app list is empty", async () => {
    // If /audio/apps fails, the saved app must still be selectable so saving doesn't replace it.
    getAudioApps.mockResolvedValue([]);
    await render();
    expect(capture().value).toBe("Brave");
  });

  it("offers Everything as the empty value", async () => {
    getSettings.mockResolvedValue({ ...CONFIG, audio_capture_app: "" });
    await render();
    expect(capture().value).toBe("");
    expect(capture().selectedOptions[0]?.textContent?.trim()).toBe("Everything (system audio)");
  });
});

describe("the speech language", () => {
  it("offers auto-detect first, then whisper's languages by name", async () => {
    await render();
    const labels = [...select("wlang").options].map((o) => o.textContent?.trim());
    expect(labels[0]).toBe("Auto-detect");
    expect(labels).toContain("Bulgarian (bg)");
    expect(select("wlang").value).toBe("auto");
  });

  it("keeps a saved code selectable before the language list arrives", async () => {
    // Issue #12's reporter set bg by hand; a select without that option would save it as blank.
    getSettings.mockResolvedValue({ ...CONFIG, whisper_language: "bg" });
    getSystemInfo.mockRejectedValue(new Error("engine busy"));
    await render();
    expect(select("wlang").value).toBe("bg");
  });

  it("shows the model and language in use", async () => {
    getSettings.mockResolvedValue({ ...CONFIG, whisper_model: "small", whisper_language: "bg" });
    await render();
    const current = target.querySelector('[data-testid="speech-current"]')?.textContent ?? "";
    expect(current.replace(/\s+/g, " ")).toContain("Using small · Bulgarian (bg)");
  });

  it("tags English-only models so they are not picked for another language", async () => {
    await render();
    const labels = [...select("wmodel").options].map((o) => o.textContent ?? "");
    expect(labels.find((l) => l.startsWith("small.en"))).toContain("English only");
    expect(labels.find((l) => l.startsWith("small ·"))).toContain("multilingual");
    // The error-rate figure is jargon in a menu; the list is ordered by size instead.
    expect(labels.some((l) => l.includes("%"))).toBe(false);
  });

  it("warns when another language is paired with a .en model", async () => {
    await render();
    expect(target.textContent).not.toContain("only transcribes English");
    choose(select("wlang"), "bg");
    expect(target.textContent).toContain("only transcribes English");
    choose(select("wmodel"), "small");
    expect(target.textContent).not.toContain("only transcribes English");
  });

  it("applies a language change with a reload, not a restart", async () => {
    await render();
    choose(select("wlang"), "bg");
    clickSave();
    await settle();
    expect(postSettings).toHaveBeenCalledWith({ whisper_language: "bg" });
    expect(reloadSettings).toHaveBeenCalled();
    expect(applyAndRestart).not.toHaveBeenCalled();
  });

  it("still restarts for a model change", async () => {
    await render();
    choose(select("wmodel"), "small");
    clickSave();
    await settle();
    expect(applyAndRestart).toHaveBeenCalledWith({ whisper_model: "small" }, expect.any(Function));
  });
});

describe("saving", () => {
  it("sends only the changed field and reloads without restarting the engine", async () => {
    await render();
    const select = capture();
    select.value = "Firefox";
    select.dispatchEvent(new Event("change"));
    flushSync();

    clickSave();
    for (let i = 0; i < 20; i++) await Promise.resolve();

    expect(postSettings).toHaveBeenCalledWith({ audio_capture_app: "Firefox" });
    expect(reloadSettings).toHaveBeenCalled();
    // The capture target is read per interview, so changing it must not cost an engine restart.
    expect(applyAndRestart).not.toHaveBeenCalled();
  });

  it("restarts the engine for a field the engine only reads at startup", async () => {
    await render();
    const url = target.querySelector<HTMLInputElement>("#ourl");
    if (!url) throw new Error("ollama url input not rendered");
    url.value = "http://otherhost:11434";
    url.dispatchEvent(new Event("input"));
    flushSync();

    clickSave();
    for (let i = 0; i < 20; i++) await Promise.resolve();

    expect(applyAndRestart).toHaveBeenCalled();
    expect(reloadSettings).not.toHaveBeenCalled();
  });

  it("sends nothing at all when nothing was touched", async () => {
    // What protects a saved value from a control that failed to display it.
    await render();
    clickSave();
    for (let i = 0; i < 20; i++) await Promise.resolve();

    expect(postSettings).not.toHaveBeenCalled();
    expect(applyAndRestart).not.toHaveBeenCalled();
  });
});
