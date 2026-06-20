<script lang="ts">
  import { getSystemInfo } from "../../lib/api";
  import { applyAndRestart } from "../../lib/apply";
  import type { SystemReport } from "../../lib/types";
  import CloudSetup from "../wizard/CloudSetup.svelte";
  import LocalSetup from "../wizard/LocalSetup.svelte";

  let { back }: { back: () => void } = $props();

  type Stage = "choose" | "local" | "cloud" | "applying" | "error";
  let stage = $state<Stage>("choose");
  let status = $state("");
  let error = $state<string | null>(null);
  let report = $state<SystemReport | null>(null);

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
    <h1>Change model</h1>
    <p class="text-xs">Switch the answering model. The engine restarts to apply.</p>
    <div class="flex gap-2">
      <button class="flex-1" onclick={chooseLocal}>Local model (Ollama)</button>
      <button class="flex-1" onclick={() => (stage = "cloud")}>
        Cloud model (bring your own key)
      </button>
    </div>
    <div class="flex-1"></div>
    <div class="flex"><button onclick={back}>Back</button></div>
  </div>
{:else if stage === "local" && report}
  <LocalSetup {report} complete={apply} back={() => (stage = "choose")} />
{:else if stage === "cloud"}
  <CloudSetup complete={apply} back={() => (stage = "choose")} />
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
