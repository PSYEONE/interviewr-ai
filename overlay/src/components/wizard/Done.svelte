<script lang="ts">
  import { postSettings, waitForHealth } from "../../lib/api";
  import { engineManaged, restartEngine } from "../../lib/tauri";
  import type { SystemReport } from "../../lib/types";

  let {
    updates,
    report,
    recheck,
    back,
  }: {
    updates: Record<string, unknown>;
    report: SystemReport | null;
    recheck: () => void;
    back: () => void;
  } = $props();

  let saving = $state(false);
  let saved = $state(false); // manual-restart fallback (dev / external engine)
  let statusMsg = $state("");
  let error = $state<string | null>(null);

  const LABELS: Record<string, string> = {
    llm_provider: "LLM provider",
    llm_model: "Model",
    whisper_gpu_device: "Speech-to-text GPU",
    ollama_base_url: "Ollama URL",
    anthropic_api_key: "Anthropic API key",
    openai_api_key: "OpenAI API key",
    gemini_api_key: "Gemini API key",
    groq_api_key: "Groq API key",
  };

  const PROVIDERS: Record<string, string> = {
    ollama: "Ollama (local)",
    anthropic: "Anthropic (Claude)",
    openai: "OpenAI",
    gemini: "Google Gemini",
    groq: "Groq",
  };

  function label(key: string): string {
    return LABELS[key] ?? key.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
  }

  function display(key: string, value: unknown): string {
    if (key.endsWith("_api_key")) return "••••••";
    if (key === "llm_provider") return PROVIDERS[String(value)] ?? String(value);
    if (key === "whisper_gpu_device") {
      const idx = Number(value);
      if (idx < 0) return "Auto";
      const gpu = report?.gpus.find((g) => g.index === idx);
      return gpu ? gpu.name : `Device ${idx}`;
    }
    return String(value);
  }

  let summary = $derived(
    Object.entries(updates).map(([key, value]) => ({
      key,
      label: label(key),
      value: display(key, value),
    })),
  );

  function msgOf(e: unknown): string {
    return e instanceof Error ? e.message : String(e);
  }

  async function save(): Promise<void> {
    saving = true;
    error = null;
    try {
      statusMsg = "Saving…";
      await postSettings(updates);

      // If we don't own the engine (dev / a manually-run backend), we can't restart it; show
      // the manual fallback instead.
      const managed = await engineManaged().catch(() => false);
      if (!managed) {
        saved = true;
        return;
      }

      statusMsg = "Restarting engine…";
      try {
        await restartEngine();
      } catch (e) {
        if (msgOf(e).includes("not_managed")) {
          saved = true;
          return;
        }
        throw e;
      }

      statusMsg = "Waiting for the engine…";
      await waitForHealth((h) => h.configured && h.ready);
      recheck(); // engine is up with the new config: advance to the panel
    } catch (e) {
      error = msgOf(e);
    } finally {
      saving = false;
    }
  }
</script>

<div class="wiz split">
  <div class="wiz-head">
    <h1>Review &amp; save</h1>
  </div>
  <div class="wiz-body gap-4">

  {#each summary as row (row.key)}
    <div class="card flex items-center gap-2">
      <span class="text-dim text-xs flex-1">{row.label}</span>
      <span>{row.value}</span>
    </div>
  {/each}

  {#if error}<p class="text-warn">{error}</p>{/if}

  {#if saving && statusMsg}
    <p class="text-dim text-xs">{statusMsg}</p>
  {/if}

  {#if saved}
    <div class="card selected">
      <strong>Saved.</strong>
      <p class="text-xs">Restart the engine to apply, then continue.</p>
    </div>
  {/if}

  </div>

  <div class="wiz-foot">
    <button onclick={back} disabled={saving}>Back</button>
    {#if !saved}
      <button class="primary ml-auto" onclick={save} disabled={saving}>
        {saving ? statusMsg || "Saving…" : "Save"}
      </button>
    {:else}
      <button class="primary ml-auto" onclick={recheck}>I've restarted, continue</button>
    {/if}
  </div>
</div>
