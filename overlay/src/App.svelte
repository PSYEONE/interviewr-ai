<script lang="ts">
  import { onMount } from "svelte";

  import Panel from "./components/Panel.svelte";
  import Wizard from "./components/wizard/Wizard.svelte";
  import { getHealth } from "./lib/api";
  import { setPin } from "./lib/tauri";

  type View = "loading" | "wizard" | "panel" | "offline";
  let view = $state<View>("loading");

  // First-run is backend truth: configured=false (no model set) -> wizard, else the panel.
  // The engine being unreachable just means "waiting" (manual start in dev; the 3c sidecar
  // will start it for us later).
  async function decide(): Promise<void> {
    try {
      const health = await getHealth();
      view = health.configured ? "panel" : "wizard";
      // The wizard has no pin control, so make sure it isn't stuck always-on-top.
      if (view === "wizard") void setPin(false);
    } catch {
      view = "offline";
      setTimeout(() => void decide(), 1500);
    }
  }

  onMount(decide);
</script>

{#if view === "loading"}
  <div class="splash">Starting…</div>
{:else if view === "offline"}
  <div class="splash">Waiting for the engine…</div>
{:else if view === "wizard"}
  <Wizard recheck={decide} />
{:else}
  <Panel />
{/if}
