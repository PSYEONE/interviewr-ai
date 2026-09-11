<script lang="ts">
  import { deleteOllamaModel, getDisk, getSystemInfo } from "../../lib/api";
  import { pull } from "../../lib/pull.svelte";
  import Command from "../Command.svelte";
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
  let error = $state<string | null>(null);
  // Failures from the "Pull any model" field show inline beside it: a mistyped tag is about that
  // input, so the message belongs next to it rather than in the strip above the footer.
  let customError = $state<string | null>(null);
  // Progress lives in the shared store so it survives leaving this screen; PullStatus renders it.
  let pulling = $derived(pull.busy);

  type Row = {
    name: string;
    label: string;
    params: string | null;
    quant: string | null;
    downloadMb: number | null;
    vramMb: number | null;
    fit: string | null;
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
      quant: m.quant,
      downloadMb: m.download_mb,
      vramMb: m.vram_mb,
      fit: m.fit,
    }));
    for (const m of info.ollama.models) {
      if (isEmbed(m.name) || inCatalog.has(m.name)) continue;
      out.push({
        name: m.name,
        label: m.name,
        params: m.parameter_size,
        quant: m.quantization,
        downloadMb: null,
        vramMb: null,
        fit: null,
      });
    }
    // The configured model may have been removed from Ollama since. Keep a row for it so "current"
    // always has somewhere to sit and the missing download is obvious.
    if (currentModel && !out.some((r) => sameTag(r.name, currentModel))) {
      out.unshift({
        name: currentModel,
        label: currentModel,
        params: null,
        quant: null,
        downloadMb: null,
        vramMb: null,
        fit: null,
      });
    }
    return out;
  });

  let embedInstalled = $derived(installed(EMBED_MODEL));
  let canContinue = $derived(!pulling && !!selected && installed(selected));

  function gbOf(mb: number): string {
    return `${(mb / 1024).toFixed(mb < 10240 ? 1 : 0)} GB`;
  }

  // ModelInfo.size is raw bytes, unlike the megabytes used elsewhere in the report. This is what
  // Ollama reports for the tag; tags that share a blob each report the full size, so deleting one
  // of them frees nothing. Stated as "on disk", never as "frees N GB".
  function diskSize(name: string): string {
    const bytes = info.ollama.models.find((m) => sameTag(m.name, name))?.size ?? 0;
    return bytes > 0 ? `${(bytes / 1024 ** 3).toFixed(1)} GB on disk` : "";
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
      // Interrupted downloads. Ollama keeps these to resume from and never prunes them, and no
      // manifest references them, so nothing else in any UI can show that the space is in use.
      partial: d.partial_mb,
      reclaim: `sudo rm -f '${d.path}/blobs/'*-partial*`,
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

  // Reclaiming residue happens outside the app (a sudo rm in a terminal), so the only moment the
  // answer can have changed is when the window comes back. Guarded on there being residue at all,
  // so once it is cleared this stops firing and costs nothing.
  let recheckingDisk = false;
  async function recheckDisk(): Promise<void> {
    if (recheckingDisk || !info.disk || info.disk.partial_mb <= 0) return;
    recheckingDisk = true;
    try {
      info = { ...info, disk: await getDisk() };
    } catch {
      /* leave the last known figures in place */
    } finally {
      recheckingDisk = false;
    }
  }

  async function refresh(): Promise<void> {
    try {
      info = await getSystemInfo(true);
    } catch (e) {
      error = e instanceof Error ? e.message : String(e);
    }
  }

  // A pull outlives the screen that started it, so the refresh cannot belong to the caller alone:
  // leaving mid-download and coming back leaves this instance showing Download for a model that
  // has since landed. Plain `let`, so the effect depends on pull.settled and nothing else.
  let handled = pull.settled;
  $effect(() => {
    if (pull.settled === handled) return;
    handled = pull.settled;
    void refresh();
  });

  async function download(model: string): Promise<void> {
    if (pulling || !model) return;
    error = null;
    try {
      // A cancel is a normal outcome, not a failure: it must never paint the red error line.
      // Either way the disk figures moved, so refresh on both paths.
      await pull.run(model);
    } catch (e) {
      error = e instanceof Error ? e.message : String(e);
    }
    // Claim the completion before awaiting, so the effect above skips a pull we are refreshing for
    // anyway. /system/info re-probes the GPUs and Ollama, so doing it twice is worth avoiding.
    handled = pull.settled;
    await refresh();
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
    customError = null;
    try {
      await pull.run(tag);
    } catch (e) {
      // Inside the field there is only room for a verdict; the tag it refers to is right there.
      const status = (e as { status?: number }).status;
      customError =
        status === 404 ? "Model not found" : e instanceof Error ? e.message : String(e);
    }
    handled = pull.settled; // as in download(): this refresh is the one the effect would have run
    await refresh(); // installed() reads `info`, so the check below needs the fresh report
    if (!installed(tag)) return;
    // Pulling a model by name is an explicit choice, so select it once it lands. Re-pulling the
    // one already in use is a repair, not a choice, and must not change the selection.
    if (!sameTag(tag, currentModel)) selected = tag;
    customModel = "";
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

<!-- Both events: WebKitGTK's focus behaviour is unverified, and whichever arrives first makes the
     other a no-op via the in-flight guard. -->
<svelte:window onfocus={recheckDisk} />
<svelte:document onvisibilitychange={() => !document.hidden && recheckDisk()} />

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
        <Command command={info.ollama.install_command ?? ""} />
        <div class="flex items-center justify-end gap-2">
          <button onclick={refresh}>Re-check</button>
        </div>
      </div>
    {:else}
      {#if !info.ollama.running}
        <p class="text-warn">Ollama is installed but not running. Start it, then re-check.</p>
        <div class="flex items-center gap-2"><button onclick={refresh}>Re-check</button></div>
      {/if}

      <div class="card card-row flex items-center gap-2">
        <span class="text-dim shrink-0 text-xs">Pull any model:</span>
        <!-- The verdict sits inside the field, at the far end: it is about what was typed there.
             pointer-events-none so it never blocks a click into the input, and the input gains
             right padding while it shows so a long tag can't run underneath it. -->
        <span class="relative min-w-0 flex-1">
          <input
            class="w-full"
            class:pr-28={!!customError}
            type="text"
            placeholder="e.g. gemma3:4b"
            bind:value={customModel}
            oninput={() => (customError = null)}
            onkeydown={(e) => e.key === "Enter" && (e.preventDefault(), pullCustom())}
          />
          {#if customError}
            <span
              class="text-bad pointer-events-none absolute top-1/2 right-2 -translate-y-1/2 text-xs"
            >
              {customError}
            </span>
          {/if}
        </span>
        <button disabled={pulling || !customModel.trim()} onclick={pullCustom}>Download</button>
      </div>

      {#each rows as m (m.name)}
        {@const isCurrent = sameTag(m.name, currentModel)}
        <label
          class="card card-row flex items-center gap-2"
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
          <span class="min-w-0 flex-1">
            <span>{m.label}</span>
            <!-- Measured facts only, under the name so the top line stays scannable. The size is
                 exact (no tilde); the VRAM figure is an estimate and is marked as one. -->
            <span class="text-dim block text-[11px]">
              {#if m.params}<span>{m.params}</span>{/if}
              {#if m.quant}<span> · {m.quant}</span>{/if}
              {#if installed(m.name)}
                <span> · {diskSize(m.name)}</span>
              {:else if m.downloadMb}
                <span> · {gbOf(m.downloadMb)} download</span>
              {/if}
              {#if m.vramMb}
                <span> · ~{gbOf(m.vramMb)} VRAM</span>
              {/if}
            </span>
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

      <div class="card card-row">
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

  <!-- Not while a pull is running. Ollama streams a download into blobs/*-partial*, which is the
       same glob the backend measures, so mid-download this would name the file being written and
       tell the user to delete it. -->
  {#if error || (disk && disk.partial > 0 && !pulling)}
    <div class="wiz-status">
      {#if error}<p class="text-warn text-xs">{error}</p>{/if}
      {#if disk && disk.partial > 0 && !pulling}
        <Command
          warn
          note={`${gb(disk.partial)} in interrupted downloads. Run this to reclaim it:`}
          command={disk.reclaim}
        />
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
