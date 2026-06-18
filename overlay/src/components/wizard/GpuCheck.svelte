<script lang="ts">
  import { onMount } from "svelte";

  import { getSystemInfo } from "../../lib/api";
  import type { SystemReport } from "../../lib/types";

  let {
    goLocal,
    goCloud,
    back,
  }: {
    goLocal: (r: SystemReport) => void;
    goCloud: (r: SystemReport) => void;
    back: () => void;
  } = $props();

  let report = $state<SystemReport | null>(null);
  let error = $state<string | null>(null);
  let refreshing = $state(false);

  function vram(mb: number | null): string {
    return mb ? `${(mb / 1024).toFixed(1)} GB` : "shared";
  }

  // Cached by default (instant on back-navigation); force re-probes the hardware.
  async function load(force = false): Promise<void> {
    error = null;
    refreshing = true;
    try {
      report = await getSystemInfo(force);
    } catch (e) {
      error = e instanceof Error ? e.message : String(e);
    } finally {
      refreshing = false;
    }
  }

  onMount(() => load(false));
</script>

<div class="wiz">
  <div class="flex items-center gap-2">
    <h1>Hardware check</h1>
    <span class="flex-1"></span>
    <button class="icon" title="Re-detect hardware" disabled={refreshing} onclick={() => load(true)}>
      {refreshing ? "…" : "↻"}
    </button>
  </div>

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

    <div class="flex flex-wrap items-stretch gap-3">
      <button class="choice" onclick={() => report && goLocal(report)}>
        <span class="flex w-full items-center gap-2">
          <span class="choice-title flex-1">Local</span>
          {#if report.recommendation.can_run_local}<span class="badge">Recommended</span>{/if}
        </span>
        <span class="choice-desc">Run on your own GPU. Private, offline, no API key or usage cost (just your electricity bill).</span>
      </button>

      <button class="choice" onclick={() => report && goCloud(report)}>
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

    <div class="flex-1"></div>
    <div class="flex items-center justify-end gap-2">
      <button class="mr-auto" onclick={back}>Back</button>
    </div>
  {/if}
</div>
