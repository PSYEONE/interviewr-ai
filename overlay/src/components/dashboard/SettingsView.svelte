<script lang="ts">
  import { onMount } from "svelte";

  import { getSettings, getSystemInfo, postSettings, reloadSettings } from "../../lib/api";
  import { applyAndRestart } from "../../lib/apply";
  import {
    HOTKEY_ACTIONS,
    HOTKEY_LABELS,
    hotkeys,
    type HotkeyAction,
  } from "../../lib/hotkeys.svelte";
  import type { ConfigView, GpuInfo } from "../../lib/types";

  let { back }: { back: () => void } = $props();

  interface Editable {
    stt_provider: string;
    whisper_model: string;
    whisper_use_gpu: boolean;
    whisper_gpu_device: number;
    rag_enabled: boolean;
    rag_top_k: number;
    embed_provider: string;
    embed_model: string;
    history_enabled: boolean;
    ollama_base_url: string;
    llm_keep_alive: string;
  }
  const KEYS: (keyof Editable)[] = [
    "stt_provider",
    "whisper_model",
    "whisper_use_gpu",
    "whisper_gpu_device",
    "rag_enabled",
    "rag_top_k",
    "embed_provider",
    "embed_model",
    "history_enabled",
    "ollama_base_url",
    "llm_keep_alive",
  ];
  // Fields the engine can apply live (no restart); everything else rebuilds at startup.
  const CHEAP = new Set<keyof Editable>(["rag_top_k", "history_enabled", "llm_keep_alive"]);

  function pick(c: ConfigView): Editable {
    return {
      stt_provider: c.stt_provider,
      whisper_model: c.whisper_model,
      whisper_use_gpu: c.whisper_use_gpu,
      whisper_gpu_device: c.whisper_gpu_device,
      rag_enabled: c.rag_enabled,
      rag_top_k: c.rag_top_k,
      embed_provider: c.embed_provider,
      embed_model: c.embed_model,
      history_enabled: c.history_enabled,
      ollama_base_url: c.ollama_base_url,
      llm_keep_alive: c.llm_keep_alive,
    };
  }

  let original = $state<Editable | null>(null);
  let form = $state<Editable | null>(null);
  let gpus = $state<GpuInfo[]>([]);
  let loadError = $state<string | null>(null);
  let saving = $state(false);
  let status = $state("");
  let error = $state<string | null>(null);

  function msg(e: unknown): string {
    return e instanceof Error ? e.message : String(e);
  }

  onMount(async () => {
    try {
      const cfg = await getSettings();
      original = pick(cfg);
      form = pick(cfg);
    } catch (e) {
      loadError = msg(e);
    }
    try {
      gpus = (await getSystemInfo()).gpus;
    } catch {
      gpus = []; // GPU list is a convenience; Auto still works
    }
  });

  // --- Global hotkey capture (applies immediately, independent of Save) ---
  let recording = $state<HotkeyAction | null>(null);

  function codeToKey(code: string): string | null {
    if (code.startsWith("Key")) return code.slice(3); // KeyA -> A
    if (code.startsWith("Digit")) return code.slice(5); // Digit1 -> 1
    if (/^F\d{1,2}$/.test(code)) return code; // F1..F12
    if (code === "Space") return "Space";
    const map: Record<string, string> = {
      ArrowUp: "Up",
      ArrowDown: "Down",
      ArrowLeft: "Left",
      ArrowRight: "Right",
      Enter: "Enter",
    };
    return map[code] ?? null;
  }

  function toAccelerator(e: KeyboardEvent): string | null {
    const mods: string[] = [];
    if (e.ctrlKey || e.metaKey) mods.push("CmdOrCtrl");
    if (e.altKey) mods.push("Alt");
    if (e.shiftKey) mods.push("Shift");
    if (mods.length === 0) return null; // require a modifier so it can't clash with typing
    const key = codeToKey(e.code);
    return key ? [...mods, key].join("+") : null;
  }

  function onKeydown(e: KeyboardEvent): void {
    if (!recording) return;
    e.preventDefault();
    if (e.code === "Escape") {
      recording = null;
      return;
    }
    const accel = toAccelerator(e);
    if (!accel) return; // wait for a modifier + a real key
    const action = recording;
    recording = null;
    void hotkeys.setBinding(action, accel);
  }

  async function save(): Promise<void> {
    if (!form || !original) return;
    const updates: Record<string, unknown> = {};
    for (const k of KEYS) if (form[k] !== original[k]) updates[k] = form[k];
    const changed = Object.keys(updates) as (keyof Editable)[];
    if (changed.length === 0) {
      back(); // nothing changed -> no restart, no reload
      return;
    }
    const heavy = changed.some((k) => !CHEAP.has(k));
    saving = true;
    error = null;
    try {
      if (heavy) {
        const res = await applyAndRestart(updates, (m) => (status = m));
        if (res === "manual") {
          error = "Saved. The engine isn't app-managed here; restart it manually to apply.";
          return;
        }
      } else {
        status = "Applying…";
        await postSettings(updates);
        await reloadSettings(); // cheap fields take effect with no restart
      }
      back();
    } catch (e) {
      error = msg(e);
    } finally {
      saving = false;
    }
  }
</script>

<svelte:window onkeydown={onKeydown} />

<div class="dash">
  <div class="flex items-center gap-2 border-b border-white/10 p-4">
    <button onclick={back} disabled={saving}>Back</button>
    <h1 class="flex-1 text-lg font-semibold">Settings</h1>
    <button class="primary" onclick={save} disabled={saving || !form}>
      {saving ? status || "Saving…" : "Save"}
    </button>
  </div>

  <div class="wiz">
    {#if loadError}
      <p class="text-warn">{loadError}</p>
    {:else if !form}
      <p class="text-dim text-sm">Loading…</p>
    {:else}
      {#if error}<p class="text-warn">{error}</p>{/if}

      <h2>Speech-to-text</h2>
      <div>
        <label for="stt">Provider</label>
        <select id="stt" bind:value={form.stt_provider}>
          <option value="whisper_cpp">whisper.cpp (local)</option>
          <option value="groq">Groq (cloud)</option>
          <option value="deepgram">Deepgram (cloud)</option>
        </select>
      </div>
      <div>
        <label for="wmodel">Whisper model</label>
        <input id="wmodel" type="text" bind:value={form.whisper_model} />
      </div>
      <label class="flex items-center gap-2">
        <input type="checkbox" bind:checked={form.whisper_use_gpu} /> Use GPU for transcription
      </label>
      <div>
        <label for="gpu">Transcription GPU</label>
        <select id="gpu" bind:value={form.whisper_gpu_device}>
          <option value={-1}>Auto</option>
          {#each gpus as g (g.index)}
            <option value={g.index}>{g.name}</option>
          {/each}
        </select>
      </div>

      <h2>Retrieval (RAG)</h2>
      <label class="flex items-center gap-2">
        <input type="checkbox" bind:checked={form.rag_enabled} /> Enable RAG grounding
      </label>
      <div>
        <label for="topk">Chunks per answer (top-k)</label>
        <input id="topk" type="number" min="0" max="20" bind:value={form.rag_top_k} />
      </div>
      <div>
        <label for="eprov">Embedding provider</label>
        <select id="eprov" bind:value={form.embed_provider}>
          <option value="ollama">Ollama (local)</option>
          <option value="openai">OpenAI</option>
          <option value="gemini">Gemini</option>
        </select>
      </div>
      <div>
        <label for="emodel">Embedding model</label>
        <input id="emodel" type="text" bind:value={form.embed_model} />
      </div>

      <h2>History</h2>
      <label class="flex items-center gap-2">
        <input type="checkbox" bind:checked={form.history_enabled} /> Save conversation history
      </label>

      <h2>Connection</h2>
      <div>
        <label for="ourl">Ollama URL</label>
        <input id="ourl" type="text" bind:value={form.ollama_base_url} />
      </div>
      <div>
        <label for="keep">Model keep-alive</label>
        <input id="keep" type="text" bind:value={form.llm_keep_alive} />
      </div>
    {/if}

    <h2>Shortcuts</h2>
    <p class="text-xs">
      Global hotkeys, active even when the overlay isn't focused. Unbound by default; changes apply
      immediately (no Save needed).
    </p>
    {#if hotkeys.error}<p class="text-warn">{hotkeys.error}</p>{/if}
    {#each HOTKEY_ACTIONS as a (a)}
      <div class="card flex items-center gap-2">
        <span class="flex-1">{HOTKEY_LABELS[a]}</span>
        <span class="text-dim text-sm">
          {recording === a ? "Press keys…" : (hotkeys.bindings[a] ?? "Unbound")}
        </span>
        <button onclick={() => (recording = a)}>{recording === a ? "…" : "Record"}</button>
        {#if hotkeys.bindings[a]}
          <button onclick={() => void hotkeys.clearBinding(a)}>Clear</button>
        {/if}
      </div>
    {/each}
  </div>
</div>
