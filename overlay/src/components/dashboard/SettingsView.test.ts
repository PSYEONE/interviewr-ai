// The Settings screen's two answerable-by-running questions: does the capture dropdown show the
// value it loaded, and does Save send exactly what changed. Both were guessed at for several
// rounds because nothing here could be executed.

import { flushSync, mount, unmount } from "svelte";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ConfigView } from "../../lib/types";
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

let target: HTMLElement;
let app: Record<string, unknown> | null = null;

/** Mount and let onMount's awaits settle, so the form is populated before asserting. */
async function render(): Promise<void> {
  app = mount(SettingsView, { target, props: { back: () => {} } });
  for (let i = 0; i < 20; i++) await Promise.resolve();
  flushSync();
}

const capture = (): HTMLSelectElement =>
  target.querySelector<HTMLSelectElement>("#capapp") ?? (() => {
    throw new Error("capture select not rendered");
  })();

function clickSave(): void {
  const save = [...target.querySelectorAll("button")].find((b) => b.textContent?.trim() === "Save");
  if (!save) throw new Error("Save button not rendered");
  save.click();
}

beforeEach(() => {
  vi.clearAllMocks();
  getSettings.mockResolvedValue({ ...CONFIG });
  getAudioApps.mockResolvedValue(["Brave", "Firefox", "mpv"]);
  getSystemInfo.mockResolvedValue({ gpus: [] });
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
    // The assertion that could not be answered for several rounds: .env said Brave and the
    // dropdown showed Everything.
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
    // What happens when /audio/apps fails: the saved value must still be selectable, or saving
    // from this screen would quietly replace it with Everything.
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
