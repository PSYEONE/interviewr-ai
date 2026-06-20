<script lang="ts">
  import { onMount } from "svelte";

  import { getSettings } from "../../lib/api";
  import { applyAndRestart } from "../../lib/apply";

  let { back }: { back: () => void } = $props();

  type Stage = "edit" | "applying" | "error";
  let stage = $state<Stage>("edit");
  let prompt = $state("");
  let loaded = $state(false);
  let status = $state("");
  let error = $state<string | null>(null);

  function msg(e: unknown): string {
    return e instanceof Error ? e.message : String(e);
  }

  async function load(): Promise<void> {
    try {
      prompt = (await getSettings()).system_prompt; // resolved: override or built-in default
      loaded = true;
    } catch (e) {
      error = msg(e);
      stage = "error";
    }
  }

  // thenBack=true after Save (return home); false after Reset (refill with the default and stay).
  async function apply(value: string, thenBack: boolean): Promise<void> {
    stage = "applying";
    error = null;
    try {
      const res = await applyAndRestart({ system_prompt: value }, (m) => (status = m));
      if (res === "manual") {
        error = "Saved. The engine isn't app-managed here; restart it manually to apply.";
        stage = "error";
        return;
      }
      if (thenBack) {
        back();
      } else {
        await load();
        stage = "edit";
      }
    } catch (e) {
      error = msg(e);
      stage = "error";
    }
  }

  onMount(load);
</script>

{#if stage === "applying"}
  <div class="splash">{status || "Applying…"}</div>
{:else}
  <div class="wiz">
    <h1>System prompt</h1>
    <p class="text-xs">
      Controls how the model answers.
    </p>
    {#if error}<p class="text-warn">{error}</p>{/if}

    {#if loaded}
      <textarea bind:value={prompt} rows="12"></textarea>
    {:else}
      <p class="text-dim text-sm">Loading…</p>
    {/if}

    <div class="flex-1"></div>
    <div class="flex items-center justify-end gap-2">
      <button class="mr-auto" onclick={back}>Back</button>
      <button onclick={() => apply("", false)} disabled={!loaded}>Reset to default</button>
      <button class="primary" onclick={() => apply(prompt, true)} disabled={!loaded || !prompt.trim()}>
        Save
      </button>
    </div>
  </div>
{/if}
