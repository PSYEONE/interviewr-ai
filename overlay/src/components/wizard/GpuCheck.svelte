<script lang="ts">
  import { onMount } from "svelte";

  import { getSystemInfo } from "../../lib/api";
  import {
    AUTO_LANGUAGE,
    englishOnlyMismatch,
    languageOptions,
    speechLabel,
  } from "../../lib/speech";
  import type { SystemReport } from "../../lib/types";

  let {
    goLocal,
    goCloud,
    back,
  }: {
    goLocal: (r: SystemReport, whisperModel: string, whisperLanguage: string) => void;
    goCloud: (r: SystemReport, whisperModel: string, whisperLanguage: string) => void;
    back: () => void;
  } = $props();

  let report = $state<SystemReport | null>(null);
  // Speech model, preselected from the hardware and language; every size stays available.
  let speechModel = $state("");
  // Once the user picks a model by hand, a language change no longer replaces it.
  let modelTouched = $state(false);
  let speechLanguage = $state(AUTO_LANGUAGE);
  let error = $state<string | null>(null);
  let refreshing = $state(false);

  const languages = $derived(languageOptions(report?.whisper.languages ?? []));
  const mismatch = $derived(
    englishOnlyMismatch(speechLanguage, report?.whisper.models.find((m) => m.name === speechModel)),
  );

  function vram(mb: number | null): string {
    return mb ? `${(mb / 1024).toFixed(1)} GB` : "shared";
  }

  // Cached by default (instant on back-navigation); force re-probes the hardware.
  async function load(force = false): Promise<void> {
    error = null;
    refreshing = true;
    try {
      // The saved chat model is about to be replaced on the next screen, so size speech against
      // the one we are about to recommend instead. The language changes which model is suggested;
      // auto-detect is left out of the query so the default report stays cached.
      report = await getSystemInfo(force, {
        assumeRecommendedLlm: true,
        ...(speechLanguage !== AUTO_LANGUAGE ? { whisperLanguage: speechLanguage } : {}),
      });
      if (!modelTouched) speechModel = report.whisper.suggested;
    } catch (e) {
      error = e instanceof Error ? e.message : String(e);
    } finally {
      refreshing = false;
    }
  }

  onMount(() => load(false));
</script>

<div class="wiz split">
  <div class="wiz-head">
    <div class="flex items-center gap-2">
      <h1>Hardware check</h1>
      <span class="flex-1"></span>
      <button
        class="icon"
        title="Re-detect hardware"
        disabled={refreshing}
        onclick={() => load(true)}
      >
        {refreshing ? "…" : "↻"}
      </button>
    </div>
  </div>

  <div class="wiz-body gap-4">

  {#if error}
    <p class="text-warn">Couldn't reach the engine: {error}</p>
    <div class="flex items-center justify-end gap-2"><button onclick={() => load(true)}>Retry</button></div>
  {:else if !report}
    <p class="text-xs">Detecting GPUs…</p>
  {:else}
    {#each report.gpus.filter((g) => g.kind !== "cpu") as gpu (gpu.index)}
      <div class="card flex items-center gap-2">
        <span class="flex-1">{gpu.name}</span>
        <span class="text-dim text-xs">{gpu.kind} · {vram(gpu.vram_mb)}</span>
      </div>
    {/each}
    <p class="text-xs">{report.cpu_count} CPU cores · {(report.ram_mb / 1024).toFixed(1)} GB RAM</p>
    <p class="text-xs">{report.recommendation.reason}</p>

    <div class="card flex items-center gap-2">
      <label class="shrink-0 text-xs" for="speech-lang">Spoken language</label>
      <select
        id="speech-lang"
        class="min-w-0 flex-1"
        bind:value={speechLanguage}
        onchange={() => void load(false)}
      >
        {#each languages as l (l.code)}
          <option value={l.code}>{l.label}</option>
        {/each}
      </select>
    </div>
    <div class="card flex items-center gap-2">
      <label class="shrink-0 text-xs" for="speech">Speech model</label>
      <select
        id="speech"
        class="min-w-0 flex-1"
        bind:value={speechModel}
        onchange={() => (modelTouched = true)}
      >
        {#each report.whisper.models as m (m.name)}
          <option value={m.name}>{speechLabel(m, true)}</option>
        {/each}
      </select>
    </div>
    {#if mismatch}
      <p class="text-warn text-xs">This model only transcribes English. Pick a multilingual one for that language.</p>
    {/if}
    <p class="text-dim text-xs">
      Transcribes what you hear, on this machine. Bigger is more accurate but slower, and it downloads
      once. Auto-detect picks the language per sentence, so a conversation can switch languages; .en
      models only understand English. Sizes are judged against the GPU memory left over once your
      local model is loaded.
    </p>

    <div class="flex flex-wrap items-stretch gap-3">
      <button class="choice" onclick={() => report && goLocal(report, speechModel, speechLanguage)}>
        <span class="flex w-full items-center gap-2">
          <span class="choice-title flex-1">Local</span>
          {#if report.recommendation.can_run_local}<span class="badge">Recommended</span>{/if}
        </span>
        <span class="choice-desc">Run on your own GPU. Private, offline, no API key or usage cost (just your electricity bill).</span>
      </button>

      <button class="choice" onclick={() => report && goCloud(report, speechModel, speechLanguage)}>
        <span class="flex w-full items-center gap-2">
          <span class="choice-title flex-1">Cloud / API keys</span>
          {#if !report.recommendation.can_run_local}<span class="badge">Recommended</span>{/if}
        </span>
        <span class="choice-desc">Bring your own key for Claude, GPT, Gemini or Groq. Works on any hardware.</span>
      </button>
    </div>

  {/if}
  </div>

  <!-- Outside the {#if}: Back used to render only once a report existed, so an unreachable engine
       left Retry as the only control and no way out of the screen. -->
  <div class="wiz-foot">
    <button onclick={back}>Back</button>
  </div>
</div>
