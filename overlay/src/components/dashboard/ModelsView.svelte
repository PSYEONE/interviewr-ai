<script lang="ts">
  import { onMount } from "svelte";

  import { getSettings, getSystemInfo } from "../../lib/api";
  import { applyAndRestart } from "../../lib/apply";
  import type { ConfigView, SystemReport } from "../../lib/types";
  import CloudSetup from "../wizard/CloudSetup.svelte";
  import LocalSetup from "../wizard/LocalSetup.svelte";

  let { back }: { back: () => void } = $props();

  type Stage = "choose" | "local" | "cloud" | "applying" | "error";
  let stage = $state<Stage>("choose");
  let status = $state("");
  let error = $state<string | null>(null);
  let report = $state<SystemReport | null>(null);
  // The running config, so the setup screens can mark what's already in use. A failed load leaves
  // it null, which degrades them to their first-run behaviour rather than blocking the screen.
  let config = $state<ConfigView | null>(null);

  let localIsCurrent = $derived(config?.llm_provider === "ollama");

  onMount(async () => {
    try {
      config = await getSettings();
    } catch {
      config = null;
    }
  });

  function msg(e: unknown): string {
    return e instanceof Error ? e.message : String(e);
  }

  async function chooseLocal(): Promise<void> {
    error = null;
    try {
      report = await getSystemInfo(true);
      stage = "local";
    } catch (e) {
      error = msg(e);
      stage = "error";
    }
  }

  // The wizard setup components call complete(delta) with the right {llm_provider, llm_model, ...}.
  async function apply(delta: Record<string, unknown>): Promise<void> {
    stage = "applying";
    error = null;
    try {
      const res = await applyAndRestart(delta, (m) => (status = m));
      if (res === "manual") {
        error = "Saved. The engine isn't app-managed here; restart it manually to apply.";
        stage = "error";
        return;
      }
      back(); // applied and ready -> back to the dashboard home
    } catch (e) {
      error = msg(e);
      stage = "error";
    }
  }
</script>

{#if stage === "choose"}
  <div class="wiz">
    <h1>Manage models</h1>
    <p class="text-xs">Switch, download or remove local models.</p>
    <div class="flex flex-wrap items-stretch gap-3">
      <button class="choice" onclick={chooseLocal}>
        <span class="flex w-full items-center gap-2">
          <span class="choice-title flex-1">Local model (Ollama)</span>
          {#if config && localIsCurrent}<span class="badge">current</span>{/if}
        </span>
        <span class="choice-desc">
          Run on your own GPU. Private, offline, no API key or usage cost.
        </span>
      </button>

      <button class="choice" onclick={() => (stage = "cloud")}>
        <span class="flex w-full items-center gap-2">
          <span class="choice-title flex-1">Cloud model</span>
          {#if config && !localIsCurrent}<span class="badge">current</span>{/if}
        </span>
        <span class="choice-desc">
          Bring your own key for Claude, GPT, Gemini or Groq. Works on any hardware.
        </span>
      </button>
    </div>
    <div class="flex-1"></div>
    <div class="flex"><button onclick={back}>Back</button></div>
  </div>
{:else if stage === "local" && report}
  <LocalSetup {report} current={config} manage complete={apply} back={() => (stage = "choose")} />
{:else if stage === "cloud"}
  <CloudSetup current={config} complete={apply} back={() => (stage = "choose")} />
{:else if stage === "applying"}
  <div class="splash">{status || "Applying…"}</div>
{:else}
  <div class="wiz">
    {#if error}<p class="text-warn">{error}</p>{/if}
    <div class="flex-1"></div>
    <div class="flex items-center gap-2">
      <button class="mr-auto" onclick={back}>Back</button>
      <button onclick={() => (stage = "choose")}>Try again</button>
    </div>
  </div>
{/if}
