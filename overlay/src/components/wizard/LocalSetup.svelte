<script lang="ts">
  import { getSystemInfo, pullModel } from "../../lib/api";
  import type { SystemReport } from "../../lib/types";

  let {
    report,
    complete,
    back,
  }: {
    report: SystemReport;
    complete: (delta: Record<string, unknown>) => void;
    back: () => void;
  } = $props();

  const EMBED_MODEL = "nomic-embed-text";

  // `report` is set once by the Wizard before this screen mounts, so seeding from it is correct.
  // svelte-ignore state_referenced_locally
  let info = $state<SystemReport>(report);
  // svelte-ignore state_referenced_locally
  let selected = $state<string>(report.recommendation.suggested_models[0] ?? "");
  let pulling = $state(false);
  let pullPct = $state(0);
  let pullStatus = $state("");
  let error = $state<string | null>(null);

  // Suggested models first, then any other already-installed ones, de-duplicated.
  let candidates = $derived.by(() => {
    const names = new Set(report.recommendation.suggested_models);
    for (const m of info.ollama.models) {
      if (m.name !== EMBED_MODEL && !m.name.startsWith(`${EMBED_MODEL}:`)) names.add(m.name);
    }
    return [...names];
  });

  function installed(name: string): boolean {
    return info.ollama.models.some((m) => m.name === name || m.name === `${name}:latest`);
  }
  let embedInstalled = $derived(
    info.ollama.models.some((m) => m.name === EMBED_MODEL || m.name === `${EMBED_MODEL}:latest`),
  );
  let canContinue = $derived(!pulling && !!selected && installed(selected));

  async function refresh(): Promise<void> {
    try {
      info = await getSystemInfo(true);
    } catch (e) {
      error = e instanceof Error ? e.message : String(e);
    }
  }

  async function download(model: string): Promise<void> {
    if (pulling) return;
    error = null;
    pulling = true;
    pullPct = 0;
    pullStatus = "starting…";
    try {
      await pullModel(model, (p) => {
        pullStatus = p.status;
        if (p.percent !== null) pullPct = p.percent;
      });
      await refresh();
    } catch (e) {
      error = e instanceof Error ? e.message : String(e);
    } finally {
      pulling = false;
    }
  }

  function finish(): void {
    complete({
      llm_provider: "ollama",
      llm_model: selected,
      // Pin the discrete GPU for whisper on iGPU+dGPU machines.
      whisper_gpu_device: report.recommendation.discrete_gpu_index ?? -1,
    });
  }
</script>

<div class="wiz">
  <h1>Local model</h1>

  {#if !info.ollama.installed}
    <div class="card">
      <strong>Ollama isn't installed</strong>
      <p class="text-xs">Run this in a terminal, then re-check:</p>
      <pre class="card font-mono text-xs whitespace-pre-wrap break-all"><code
        >{info.ollama.install_command}</code></pre>
      <div class="flex items-center justify-end gap-2">
        <button onclick={refresh}>Re-check</button>
      </div>
    </div>
  {:else}
    {#if !info.ollama.running}
      <p class="text-warn">Ollama is installed but not running. Start it, then re-check.</p>
      <div class="flex items-center gap-2"><button onclick={refresh}>Re-check</button></div>
    {/if}

    <p class="text-xs">Pick a model to run locally:</p>
    {#each candidates as model (model)}
      <label class="card flex cursor-pointer items-center gap-2" class:selected={selected === model}>
        <input type="radio" name="model" value={model} bind:group={selected} />
        <span class="flex-1">{model}</span>
        {#if installed(model)}
          <span class="badge">installed</span>
        {:else}
          <button disabled={pulling} onclick={() => download(model)}>Download</button>
        {/if}
      </label>
    {/each}

    {#if pulling}
      <div class="bar-track"><div class="bar-fill" style="width:{pullPct}%"></div></div>
      <p class="text-xs">{pullStatus} {pullPct.toFixed(0)}%</p>
    {/if}

    <div class="card">
      <div class="flex items-center gap-2">
        <span class="flex-1">RAG (resume / job-description grounding)</span>
        {#if embedInstalled}
          <span class="badge">ready</span>
        {:else}
          <button disabled={pulling} onclick={() => download(EMBED_MODEL)}>
            Download {EMBED_MODEL}
          </button>
        {/if}
      </div>
    </div>
  {/if}

  {#if error}<p class="text-warn">{error}</p>{/if}

  <div class="flex-1"></div>
  <div class="flex items-center justify-end gap-2">
    <button class="mr-auto" onclick={back}>Back</button>
    <button class="primary" disabled={!canContinue} onclick={finish}>Continue</button>
  </div>
</div>
