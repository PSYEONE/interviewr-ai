<script lang="ts">
  import { onMount } from "svelte";

  import {
    getAudioApps,
    getSettings,
    getSystemInfo,
    postSettings,
    reloadSettings,
  } from "../../lib/api";
  import { applyAndRestart } from "../../lib/apply";
  import {
    HOTKEY_ACTIONS,
    HOTKEY_LABELS,
    hotkeys,
    type HotkeyAction,
  } from "../../lib/hotkeys.svelte";
  import type { ConfigView, GpuInfo, WhisperOption } from "../../lib/types";
  import ConfirmBack from "../ConfirmBack.svelte";
  import Info from "../Info.svelte";

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
    audio_capture_app: string;
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
    "audio_capture_app",
  ];
  // Settings the engine can apply without a restart; changing anything else restarts it.
  const CHEAP = new Set<keyof Editable>([
    "rag_top_k",
    "history_enabled",
    "llm_keep_alive",
    "audio_capture_app",
  ]);

  // A model set by hand, or picked before the list loaded, so the dropdown can still show it.
  let customWhisper = $derived.by(() => {
    const name = form?.whisper_model;
    return name && !speechModels.some((m) => m.name === name) ? name : null;
  });

  // "small.en · 466 MB · 4.8% word errors"
  function speechLabel(m: WhisperOption): string {
    const size = m.download_mb >= 1024 ? `${(m.download_mb / 1024).toFixed(1)} GB` : `${m.download_mb} MB`;
    const accuracy = m.live_wer === null ? "fastest, least accurate" : `${(m.live_wer * 100).toFixed(1)}% word errors`;
    return `${m.name} · ${size} · ${accuracy}`;
  }

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
      audio_capture_app: c.audio_capture_app,
    };
  }

  let original = $state<Editable | null>(null);
  let form = $state<Editable | null>(null);
  let gpus = $state<GpuInfo[]>([]);
  let speechModels = $state<WhisperOption[]>([]);
  let audioApps = $state<string[]>([]);
  let loadingApps = $state(false);
  // Saved app captured at load; building the options from the select's own bound value would stop it showing reliably.
  let savedApp = $state("");
  const captureChoices = $derived(
    savedApp && !audioApps.includes(savedApp) ? [...audioApps, savedApp] : audioApps,
  );
  let loadError = $state<string | null>(null);
  let saving = $state(false);
  let status = $state("");
  let error = $state<string | null>(null);
  // Same diff save() uses to decide what to send, reused so Back can refuse to discard it.
  const dirty = $derived(
    form !== null && original !== null && KEYS.some((k) => form![k] !== original![k]),
  );

  function msg(e: unknown): string {
    return e instanceof Error ? e.message : String(e);
  }

  async function loadAudioApps(): Promise<void> {
    loadingApps = true;
    try {
      audioApps = await getAudioApps();
    } catch {
      audioApps = []; // the picker still offers "Everything", which is the working default
    } finally {
      loadingApps = false;
    }
  }

  onMount(async () => {
    void loadAudioApps();
    try {
      const cfg = await getSettings();
      original = pick(cfg);
      form = pick(cfg);
      savedApp = cfg.audio_capture_app;
    } catch (e) {
      loadError = msg(e);
    }
    try {
      const info = await getSystemInfo();
      gpus = info.gpus;
      speechModels = info.whisper.models;
    } catch {
      gpus = []; // GPU list is a convenience; Auto still works
    }
  });

  // Global hotkey recording (applies immediately, separate from Save).
  let recording = $state<HotkeyAction | null>(null);
  let recordError = $state<string | null>(null);
  let recordTimer: ReturnType<typeof setTimeout> | undefined;

  function startRecording(action: HotkeyAction): void {
    recordError = null;
    recording = action;
    // Time out, so a combination the desktop swallows doesn't leave "Press keys..." showing forever.
    clearTimeout(recordTimer);
    recordTimer = setTimeout(() => {
      if (recording === action) {
        recording = null;
        recordError = "No key detected. Your desktop may already use that combination.";
      }
    }, 5000);
  }

  function stopRecording(): void {
    clearTimeout(recordTimer);
    recording = null;
  }

  // Modifier keys on their own, meaning the user is still reaching for the real key.
  const MODIFIER_CODES = /^(Control|Shift|Alt|Meta|OS)(Left|Right)?$/;

  function codeToKey(code: string): string | null {
    if (code.startsWith("Key")) return code.slice(3); // KeyA -> A
    if (code.startsWith("Digit")) return code.slice(5); // Digit1 -> 1
    if (code.startsWith("Numpad")) return code; // Numpad0, NumpadAdd, ...
    if (/^F\d{1,2}$/.test(code)) return code; // F1..F12
    if (code === "Space") return "Space";
    const map: Record<string, string> = {
      ArrowUp: "Up",
      ArrowDown: "Down",
      ArrowLeft: "Left",
      ArrowRight: "Right",
      Enter: "Enter",
      Tab: "Tab",
      Backspace: "Backspace",
      Delete: "Delete",
      Insert: "Insert",
      Home: "Home",
      End: "End",
      PageUp: "PageUp",
      PageDown: "PageDown",
      Minus: "Minus",
      Equal: "Equal",
      BracketLeft: "BracketLeft",
      BracketRight: "BracketRight",
      Backslash: "Backslash",
      Semicolon: "Semicolon",
      Quote: "Quote",
      Backquote: "Backquote",
      Comma: "Comma",
      Period: "Period",
      Slash: "Slash",
    };
    return map[code] ?? null;
  }

  function toAccelerator(e: KeyboardEvent): string | null {
    const mods: string[] = [];
    if (e.ctrlKey) mods.push("CmdOrCtrl");
    // Super is its own modifier, kept separate so Super+A isn't registered as Ctrl+A.
    if (e.metaKey) mods.push("Super");
    if (e.altKey) mods.push("Alt");
    if (e.shiftKey) mods.push("Shift");
    const key = codeToKey(e.code);
    if (!key) return null;
    // F-keys stand alone; everything else needs a modifier or it would clash with typing.
    if (mods.length === 0 && !/^F\d{1,2}$/.test(key)) return null;
    return [...mods, key].join("+");
  }

  function onKeydown(e: KeyboardEvent): void {
    if (!recording) return;
    e.preventDefault();
    if (e.code === "Escape") {
      stopRecording();
      return;
    }
    // Still holding modifiers down, waiting for the actual key. Not a rejection.
    if (MODIFIER_CODES.test(e.code)) return;
    const accel = toAccelerator(e);
    if (!accel) {
      // Tell the user this key can't be used, rather than silently ignoring it.
      recordError = "That key can't be bound. Try a letter, digit or F-key, with a modifier.";
      return;
    }
    const action = recording;
    stopRecording();
    void hotkeys.setBinding(action, accel);
  }

  async function save(): Promise<void> {
    if (!form || !original) return;
    const updates: Record<string, unknown> = {};
    // Skip undefined, which a select writes when it has no matching option, so it can't blank a saved value.
    for (const k of KEYS) {
      if (form[k] !== original[k] && form[k] !== undefined) updates[k] = form[k];
    }
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

  // Re-reads shortcuts when the window regains focus, for desktops that don't send change events.
  function refreshShortcuts(): void {
    if (hotkeys.backend !== "portal" || !hotkeys.enabled) return;
    void hotkeys.refreshTriggers();
  }
</script>

<svelte:window onkeydown={onKeydown} onfocus={refreshShortcuts} />
<svelte:document onvisibilitychange={() => !document.hidden && refreshShortcuts()} />

<div class="wiz split">
  <div class="wiz-head">
    <h1>Settings</h1>
  </div>

  <!-- Extra spacing, since this form is denser than a wizard step. -->
  <div class="wiz-body gap-4">
    {#if loadError}
      <p class="text-warn">{loadError}</p>
    {:else if !form}
      <p class="text-dim text-sm">Loading…</p>
    {:else}
      <h2>Speech-to-text</h2>
      <div>
        <label for="stt">
          Provider
          <Info text="Engine that transcribes audio. whisper.cpp runs locally on your GPU; Groq and Deepgram are cloud services (need an API key)." />
        </label>
        <select id="stt" bind:value={form.stt_provider}>
          <option value="whisper_cpp">whisper.cpp (local)</option>
          <option value="groq">Groq (cloud)</option>
          <option value="deepgram">Deepgram (cloud)</option>
        </select>
      </div>
      <div>
        <label for="wmodel">
          Whisper model
          <Info text="Speech model size. Bigger is more accurate but slower and uses more VRAM. Changing it downloads the new model once." />
        </label>
        <select id="wmodel" bind:value={form.whisper_model}>
          {#each speechModels as m (m.name)}
            <option value={m.name}>{speechLabel(m)}</option>
          {/each}
          {#if customWhisper}
            <option value={customWhisper}>{customWhisper}</option>
          {/if}
        </select>
      </div>
      <label class="flex items-center gap-2">
        <input type="checkbox" bind:checked={form.whisper_use_gpu} /> Use GPU for transcription
        <Info text="Transcribe on the GPU (Vulkan) instead of the CPU. Much faster; turn off only if the GPU build misbehaves." />
      </label>
      <div>
        <label for="gpu">
          Transcription GPU
          <Info text="Which GPU transcribes. Auto picks the best discrete card; set this on multi-GPU machines to pin the dGPU." />
        </label>
        <select id="gpu" bind:value={form.whisper_gpu_device}>
          <option value={-1}>Auto</option>
          {#each gpus as g (g.index)}
            <option value={g.index}>{g.name}</option>
          {/each}
        </select>
      </div>

      <hr class="sep" />
      <h2>Retrieval (RAG)</h2>
      <label class="flex items-center gap-2">
        <input type="checkbox" bind:checked={form.rag_enabled} /> Enable RAG context
        <Info text="Use your uploaded resume and job description as context for answers (added under Change context)." />
      </label>
      <div>
        <label for="topk">
          Chunks per answer (top-k)
          <Info text="How many of the most relevant document chunks to feed the model with each question. Higher = more context, more tokens." />
        </label>
        <input id="topk" type="number" min="0" max="20" bind:value={form.rag_top_k} />
      </div>
      <div>
        <label for="eprov">
          Embedding provider
          <Info text="Model that turns documents and questions into vectors for retrieval. Ollama is local; OpenAI/Gemini are cloud (need a key)." />
        </label>
        <select id="eprov" bind:value={form.embed_provider}>
          <option value="ollama">Ollama (local)</option>
          <option value="openai">OpenAI</option>
          <option value="gemini">Gemini</option>
        </select>
      </div>
      <div>
        <label for="emodel">
          Embedding model
          <Info text="The embedding model name for the chosen provider (e.g. nomic-embed-text for Ollama). Changing it means re-uploading documents." />
        </label>
        <input id="emodel" type="text" bind:value={form.embed_model} />
      </div>

      <hr class="sep" />
      <h2>Audio</h2>
      <div>
        <label for="capapp">
          Capture from
          <Info text="Transcribe all system audio, or just one app." />
        </label>
        <div class="flex items-center gap-2">
          <select id="capapp" class="flex-1" bind:value={form.audio_capture_app}>
            <option value="">Everything (system audio)</option>
            {#each captureChoices as a (a)}
              <option value={a}>{a}</option>
            {/each}
          </select>
          <button onclick={loadAudioApps} disabled={loadingApps}>
            {loadingApps ? "…" : "Refresh"}
          </button>
        </div>
      </div>

      <hr class="sep" />
      <h2>History</h2>
      <label class="flex items-center gap-2">
        <input type="checkbox" bind:checked={form.history_enabled} /> Save conversation history
        <Info text="Save each answered question and its answer to a local history file you can review and export. Off = nothing is written." />
      </label>

      <hr class="sep" />
      <h2>Connection</h2>
      <div>
        <label for="ourl">
          Ollama URL
          <Info text="Where the Ollama server is reachable. Default http://127.0.0.1:11434 for a local install." />
        </label>
        <input id="ourl" type="text" bind:value={form.ollama_base_url} />
      </div>
      <div>
        <label for="keep">
          Model keep-alive
          <Info text="How long Ollama keeps the model loaded in VRAM after the last request (e.g. 30m, 1h, 0 = unload now, -1 = forever). Ollama only." />
        </label>
        <input id="keep" type="text" bind:value={form.llm_keep_alive} />
      </div>
    {/if}

    <hr class="sep" />
    <h2>Shortcuts</h2>
    <p class="text-xs">
      Global hotkeys, active even when the overlay isn't focused.
    </p>
    {#if hotkeys.error}<p class="text-warn">{hotkeys.error}</p>{/if}
    {#if recordError}<p class="text-bad text-xs">{recordError}</p>{/if}

    {#if hotkeys.backend === "portal"}
      <!-- Wayland: keys are set in the desktop's own shortcut editor, so there's no in-app recorder here. -->
      {#if !hotkeys.enabled}
        <div class="flex items-center gap-2">
          <button onclick={() => void hotkeys.enablePortal()}>Enable shortcuts</button>
        </div>
      {:else}
        <div class="flex items-center gap-2">
          <button onclick={() => void hotkeys.configure()}>Configure shortcuts</button>
        </div>
        {#each HOTKEY_ACTIONS as a (a)}
          <div class="card card-row flex items-center gap-2">
            <span class="flex-1">{HOTKEY_LABELS[a]}</span>
            <span class="text-dim text-sm">
              {#if hotkeys.triggers[a]}
                {hotkeys.triggers[a]}
              {:else if a in hotkeys.triggers}
                No key set
              {:else}
                Not registered
              {/if}
            </span>
          </div>
        {/each}
      {/if}
    {:else if hotkeys.backend === "none"}
      <p class="text-dim text-xs">Not available on this desktop.</p>
    {:else}
      {#each HOTKEY_ACTIONS as a (a)}
        <div class="card flex items-center gap-2">
          <span class="flex-1">{HOTKEY_LABELS[a]}</span>
          <span class="text-dim text-sm">
            {recording === a ? "Press keys…" : (hotkeys.bindings[a] ?? "Unbound")}
          </span>
          <button onclick={() => startRecording(a)}>{recording === a ? "…" : "Record"}</button>
          {#if hotkeys.bindings[a]}
            <button onclick={() => void hotkeys.clearBinding(a)}>Clear</button>
          {/if}
        </div>
      {/each}
    {/if}
  </div>

  <!-- Errors and save progress sit above the footer, outside the scroll area, so they're always visible. -->
  {#if error || (saving && status)}
    <div class="wiz-status">
      {#if error}<p class="text-warn">{error}</p>{/if}
      {#if saving && status}<p class="text-dim text-xs">{status}</p>{/if}
    </div>
  {/if}

  <div class="wiz-foot">
    <ConfirmBack {dirty} {back} disabled={saving} />
    <button class="primary ml-auto" onclick={save} disabled={saving || !form}>
      {saving ? "Saving…" : "Save"}
    </button>
  </div>
</div>
