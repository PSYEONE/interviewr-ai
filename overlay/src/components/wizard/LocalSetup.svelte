<script lang="ts">
  import { deleteOllamaModel, getSystemInfo, pullModel } from "../../lib/api";
  import type { ConfigView, RankedModel, SystemReport } from "../../lib/types";

  let {
    report,
    complete,
    back,
    current = null,
    manage = false,
  }: {
    report: SystemReport;
    complete: (delta: Record<string, unknown>) => void;
    back: () => void;
    current?: ConfigView | null;
    // Only the dashboard's manage screen offers deletes; the wizard stays a linear setup path.
    manage?: boolean;
  } = $props();

  const EMBED_MODEL = "nomic-embed-text";

  // Ollama reports a bare tag as `name:latest` while .env stores it either way, so compare on the
  // normalized tag everywhere a config value meets an /api/tags value.
  function baseTag(name: string): string {
    return name.endsWith(":latest") ? name.slice(0, -":latest".length) : name;
  }
  function sameTag(a: string, b: string | null): boolean {
    return b !== null && baseTag(a) === baseTag(b);
  }

  // The model the engine is actually answering with, when it's running locally. Null in the wizard.
  let currentModel = $derived(
    current?.llm_provider === "ollama" && current.llm_model ? current.llm_model : null,
  );

  // `report` is set once by the Wizard before this screen mounts, so seeding from it is correct.
  // svelte-ignore state_referenced_locally
  let info = $state<SystemReport>(report);
  // Nothing is pre-picked when a model is already running: that row isn't selectable, so Continue
  // stays disabled until a genuinely different model is chosen. First run keeps the recommendation.
  // svelte-ignore state_referenced_locally
  let selected = $state<string>(
    current?.llm_provider === "ollama" && current.llm_model
      ? ""
      : (report.recommendation.suggested_models[0] ?? ""),
  );
  let customModel = $state("");
  // Model whose Delete is armed (click again to confirm), same shape as the history list.
  let armed = $state<string | null>(null);
  let deleting = $state(false);
  let pulling = $state(false);
  let pullPct = $state(0);
  let pullStatus = $state("");
  let error = $state<string | null>(null);

  type Row = {
    name: string;
    label: string;
    params: string | null;
    minVramMb: number | null;
    fit: string | null;
    blurb: string | null;
  };

  // Hardware-fit badge styling per fit label (backend RankedModel.fit).
  const FIT: Record<string, { text: string; cls: string }> = {
    fits: { text: "fits", cls: "badge" },
    tight: { text: "tight", cls: "badge-warn" },
    too_big: { text: "too big", cls: "badge-bad" },
    cpu: { text: "cpu", cls: "badge-muted" },
  };

  function isEmbed(name: string): boolean {
    return name === EMBED_MODEL || name.startsWith(`${EMBED_MODEL}:`);
  }
  function installed(name: string): boolean {
    return info.ollama.models.some((m) => sameTag(m.name, name));
  }

  // Curated catalog (ranked + fit-labelled) first, then any other installed model not in it, so a
  // previously-pulled or custom tag stays selectable. The embed model is excluded (it's not an LLM).
  let rows = $derived.by<Row[]>(() => {
    const catalog = info.recommendation.catalog;
    const inCatalog = new Set(catalog.map((m) => m.name));
    const out: Row[] = catalog.map((m: RankedModel) => ({
      name: m.name,
      label: m.label,
      params: m.params,
      minVramMb: m.min_vram_mb,
      fit: m.fit,
      blurb: m.blurb,
    }));
    for (const m of info.ollama.models) {
      if (isEmbed(m.name) || inCatalog.has(m.name)) continue;
      out.push({
        name: m.name,
        label: m.name,
        params: m.parameter_size,
        minVramMb: null,
        fit: null,
        blurb: null,
      });
    }
    // The configured model may have been removed from Ollama since. Keep a row for it so "current"
    // always has somewhere to sit and the missing download is obvious.
    if (currentModel && !out.some((r) => sameTag(r.name, currentModel))) {
      out.unshift({
        name: currentModel,
        label: currentModel,
        params: null,
        minVramMb: null,
        fit: null,
        blurb: null,
      });
    }
    return out;
  });

  let embedInstalled = $derived(installed(EMBED_MODEL));
  let canContinue = $derived(!pulling && !!selected && installed(selected));

  function vramGb(mb: number | null): string {
    return mb === null ? "" : `~${(mb / 1000).toFixed(mb >= 10000 ? 0 : 1)}GB`;
  }

  // ModelInfo.size is raw bytes, unlike the megabytes used elsewhere in the report. This is what
  // Ollama reports for the tag; tags that share a blob each report the full size, so deleting one
  // of them frees nothing. Stated as "on disk", never as "frees N GB".
  function diskSize(name: string): string {
    const bytes = info.ollama.models.find((m) => sameTag(m.name, name))?.size ?? 0;
    return bytes > 0 ? `${(bytes / 1e9).toFixed(1)} GB on disk` : "";
  }

  function gb(mb: number): string {
    return `${(mb / 1024).toFixed(mb < 10240 ? 1 : 0)} GB`;
  }

  // Disk split into "your models" and "everything else"; the rest of the track reads as free.
  let disk = $derived.by(() => {
    const d = info.disk;
    if (!d || d.total_mb <= 0) return null;
    const models = d.models_mb ?? 0;
    const other = Math.max(0, d.total_mb - d.free_mb - models);
    return {
      models,
      hasModels: d.models_mb !== null,
      free: d.free_mb,
      total: d.total_mb,
      modelsPct: (models / d.total_mb) * 100,
      otherPct: (other / d.total_mb) * 100,
    };
  });

  async function remove(model: string): Promise<void> {
    armed = null;
    deleting = true;
    error = null;
    try {
      await deleteOllamaModel(model);
      await refresh();
      // Continue would otherwise dead-end on a model that no longer exists.
      if (sameTag(selected, model)) selected = "";
    } catch (e) {
      error = e instanceof Error ? e.message : String(e);
    } finally {
      deleting = false;
    }
  }

  async function refresh(): Promise<void> {
    try {
      info = await getSystemInfo(true);
    } catch (e) {
      error = e instanceof Error ? e.message : String(e);
    }
  }

  async function download(model: string): Promise<void> {
    if (pulling || !model) return;
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

  // Pulling an answering model is an explicit choice, so select it once it lands. Re-pulling the
  // model already in use is a repair, not a choice, so that row uses plain download().
  async function pullAndSelect(model: string): Promise<void> {
    await download(model);
    if (installed(model)) selected = model;
  }

  async function pullCustom(): Promise<void> {
    const tag = customModel.trim();
    if (!tag) return;
    if (sameTag(tag, currentModel)) {
      await download(tag);
      return;
    }
    await pullAndSelect(tag);
    if (installed(tag)) customModel = "";
  }

  function finish(): void {
    complete({
      llm_provider: "ollama",
      llm_model: selected,
      // Pin the discrete GPU for whisper on iGPU+dGPU machines.
      whisper_gpu_device: info.recommendation.discrete_gpu_index ?? -1,
    });
  }
</script>

{#snippet rowAction(text: string)}
  <!-- Every row button reserves the width of the longest label ("Download") in a shared grid
       cell, so Delete/Confirm/Download all match and arming swaps text without moving anything. -->
  <span class="grid">
    <span class="invisible col-start-1 row-start-1">Download</span>
    <span class="col-start-1 row-start-1">{text}</span>
  </span>
{/snippet}

<div class="wiz split">
  <div class="wiz-head">
    <div class="flex items-baseline justify-between gap-3">
      <h1 class="shrink-0">Local model</h1>
      <!-- Title size, but the label stays light and dim so only the number reads as a headline.
           Truncates rather than pushing the title off on a narrow window. -->
      <span class="min-w-0 truncate text-2xl font-normal">
        {#if info.recommendation.vram_mb}
          <span class="text-dim">Available VRAM:</span>
          <span class="font-semibold">{(info.recommendation.vram_mb / 1000).toFixed(0)}GB</span>
        {:else}
          <span class="text-dim">No GPU detected; CPU only</span>
        {/if}
      </span>
    </div>
    {#if info.ollama.installed}
      <p class="text-dim text-xs">
        {currentModel ? "Pick a different model to run locally" : "Pick a model to run locally"}
      </p>
    {/if}
  </div>

  <div class="wiz-body">
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

      <div class="card flex items-center gap-2">
        <span class="text-dim shrink-0 text-xs">Pull any model:</span>
        <input
          class="flex-1"
          type="text"
          placeholder="e.g. gemma3:4b"
          bind:value={customModel}
          onkeydown={(e) => e.key === "Enter" && (e.preventDefault(), pullCustom())}
        />
        <button disabled={pulling || !customModel.trim()} onclick={pullCustom}>Download</button>
      </div>

      {#each rows as m (m.name)}
        {@const isCurrent = sameTag(m.name, currentModel)}
        <label
          class="card flex items-center gap-2"
          class:cursor-pointer={!isCurrent}
          class:selected={selected === m.name}
        >
          {#if isCurrent}
            <!-- Its own radio group: a disabled radio sharing name="model" would be unchecked the
                 moment another row is picked, and this one is only here to hold the column. -->
            <input type="radio" name="model-current" checked disabled />
          {:else}
            <input type="radio" name="model" value={m.name} bind:group={selected} />
          {/if}
          <span class="flex-1">
            <span>{m.label}</span>
            {#if m.params}<span class="text-dim text-xs"> · {m.params}</span>{/if}
            {#if installed(m.name)}
              <span class="text-dim text-xs"> · {diskSize(m.name)}</span>
            {:else if m.minVramMb}
              <span class="text-dim text-xs"> · {vramGb(m.minVramMb)}</span>
            {/if}
            {#if m.blurb}<span class="text-dim block text-[11px]">{m.blurb}</span>{/if}
          </span>
          {#if isCurrent}
            <span class="badge">current</span>
          {/if}
          {#if m.fit && FIT[m.fit]}
            <span class={FIT[m.fit].cls}>{FIT[m.fit].text}</span>
          {/if}
          {#if installed(m.name)}
            <span class="badge">installed</span>
            {#if manage && !isCurrent}
              <!-- preventDefault: this button sits inside the row's <label>, which would
                   otherwise also select the radio on the way past. -->
              <button
                class:armed={armed === m.name}
                disabled={pulling || deleting}
                title={armed === m.name ? "Click again to delete" : "Delete from disk"}
                onclick={(e) => {
                  e.preventDefault();
                  if (armed === m.name) void remove(m.name);
                  else armed = m.name;
                }}
                onmouseleave={() => {
                  if (armed === m.name) armed = null;
                }}
              >
                {@render rowAction(armed === m.name ? "Confirm" : "Delete")}
              </button>
            {/if}
          {:else}
            <button
              disabled={pulling}
              onclick={(e) => {
                e.preventDefault();
                void (isCurrent ? download(m.name) : pullAndSelect(m.name));
              }}
            >
              {@render rowAction("Download")}
            </button>
          {/if}
        </label>
      {/each}

      <div class="card">
        <div class="flex items-center gap-2">
          <span class="flex-1">
            RAG (resume / job-description context)
            {#if embedInstalled}
              <span class="text-dim text-xs"> · {diskSize(EMBED_MODEL)}</span>
            {/if}
          </span>
          {#if embedInstalled}
            <span class="badge">ready</span>
            <!-- Only offer to reclaim it once RAG is off; the backend refuses (409) either way. -->
            {#if manage && current && !current.rag_enabled}
              <button
                class:armed={armed === EMBED_MODEL}
                disabled={pulling || deleting}
                title={armed === EMBED_MODEL ? "Click again to delete" : "Delete from disk"}
                onclick={() => {
                  if (armed === EMBED_MODEL) void remove(EMBED_MODEL);
                  else armed = EMBED_MODEL;
                }}
                onmouseleave={() => {
                  if (armed === EMBED_MODEL) armed = null;
                }}
              >
                {@render rowAction(armed === EMBED_MODEL ? "Confirm" : "Delete")}
              </button>
            {/if}
          {:else}
            <button disabled={pulling} onclick={() => download(EMBED_MODEL)}>
              Download {EMBED_MODEL}
            </button>
          {/if}
        </div>
      </div>

    {/if}
  </div>

  {#if error || pulling}
    <div class="wiz-status">
      {#if error}<p class="text-warn text-xs">{error}</p>{/if}
      {#if pulling}
        <div class="bar-track"><div class="bar-fill" style="width:{pullPct}%"></div></div>
        <p class="text-xs">{pullStatus} {pullPct.toFixed(0)}%</p>
      {/if}
    </div>
  {/if}

  <div class="wiz-foot">
    <button onclick={back}>Back</button>
    {#if disk}
      <div class="flex min-w-0 flex-1 flex-col gap-1">
        <div class="bar-track flex">
          <div class="h-full bg-white/25" style="width:{disk.otherPct}%"></div>
          <div class="bar-fill" style="width:{disk.modelsPct}%"></div>
        </div>
        <p class="text-dim truncate text-[11px]">
          {#if disk.hasModels}<span class="text-accent">&#9632;</span> {gb(disk.models)} of models ·
          {/if}
          {gb(disk.free)} free of {gb(disk.total)}
        </p>
      </div>
    {:else}
      <div class="flex-1"></div>
    {/if}
    <button class="primary" disabled={!canContinue} onclick={finish}>Continue</button>
  </div>
</div>
