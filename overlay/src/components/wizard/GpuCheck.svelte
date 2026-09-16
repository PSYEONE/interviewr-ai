<script lang="ts">
  import { onMount } from "svelte";

  import { getSystemInfo } from "../../lib/api";
  import type { SystemReport, WhisperOption } from "../../lib/types";

  let {
    goLocal,
    goCloud,
    back,
  }: {
    goLocal: (r: SystemReport, whisperModel: string) => void;
    goCloud: (r: SystemReport, whisperModel: string) => void;
    back: () => void;
  } = $props();

  let report = $state<SystemReport | null>(null);
  // Speech model, preselected from the hardware; every size stays available.
  let speechModel = $state("");
  let error = $state<string | null>(null);
  let refreshing = $state(false);

  function vram(mb: number | null): string {
    return mb ? `${(mb / 1024).toFixed(1)} GB` : "shared";
  }

  // "142 MB, 6.0% word errors", plus a warning when the model is too big for the GPU found.
  function speechLabel(m: WhisperOption): string {
    const size = m.download_mb >= 1024 ? `${(m.download_mb / 1024).toFixed(1)} GB` : `${m.download_mb} MB`;
    const accuracy = m.live_wer === null ? "fastest, least accurate" : `${(m.live_wer * 100).toFixed(1)}% word errors`;
    const warn = m.fit === "too_big" ? ", may not fit this GPU" : "";
    return `${m.name} · ${size} · ${accuracy}${warn}`;
  }

  // Cached by default (instant on back-navigation); force re-probes the hardware.
  async function load(force = false): Promise<void> {
    error = null;
    refreshing = true;
    try {
      report = await getSystemInfo(force);
      if (!speechModel) speechModel = report.whisper.suggested;
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
      <label class="shrink-0 text-xs" for="speech">Speech model</label>
      <select id="speech" class="min-w-0 flex-1" bind:value={speechModel}>
        {#each report.whisper.models as m (m.name)}
          <option value={m.name}>{speechLabel(m)}</option>
        {/each}
      </select>
    </div>
    <p class="text-dim text-xs">
      Transcribes what you hear, on this machine. Bigger is more accurate but slower, and it downloads once.
    </p>

    <div class="flex flex-wrap items-stretch gap-3">
      <button class="choice" onclick={() => report && goLocal(report, speechModel)}>
        <span class="flex w-full items-center gap-2">
          <span class="choice-title flex-1">Local</span>
          {#if report.recommendation.can_run_local}<span class="badge">Recommended</span>{/if}
        </span>
        <span class="choice-desc">Run on your own GPU. Private, offline, no API key or usage cost (just your electricity bill).</span>
      </button>

      <button class="choice" onclick={() => report && goCloud(report, speechModel)}>
        <span class="flex w-full items-center gap-2">
          <span class="choice-title flex-1">Cloud / API keys</span>
          {#if !report.recommendation.can_run_local}<span class="badge">Recommended</span>{/if}
        </span>
        <span class="choice-desc">Bring your own key for Claude, GPT, Gemini or Groq. Works on any hardware.</span>
      </button>

      <button class="choice" disabled>
        <span class="flex w-full items-center gap-2">
          <span class="choice-title flex-1">Rent a GPU</span>
          <span class="badge-muted">soon</span>
        </span>
        <span class="choice-desc">Managed GPU in the cloud. Frontier speed, your data stays private.</span>
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
