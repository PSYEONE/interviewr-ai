<script lang="ts">
  import { onMount } from "svelte";

  import Panel from "./components/Panel.svelte";
  import Wizard from "./components/wizard/Wizard.svelte";
  import { getHealth } from "./lib/api";
  import { engineError, onEngineFailed, restartEngine, setPin } from "./lib/tauri";

  type View = "loading" | "wizard" | "panel" | "offline" | "error";
  let view = $state<View>("loading");
  let errorMsg = $state("");

  // First-run is backend truth: configured=false (no model set) -> wizard, else the panel.
  // The engine being unreachable just means "waiting" while the sidecar boots it.
  async function decide(): Promise<void> {
    if (view === "error") return; // a hard engine failure takes precedence over polling
    try {
      const health = await getHealth();
      view = health.configured ? "panel" : "wizard";
      // The wizard has no pin control, so make sure it isn't stuck always-on-top.
      if (view === "wizard") void setPin(false);
    } catch {
      // Engine unreachable. Recover a failure whose engine://failed event we may have missed
      // (e.g. a spawn error before this listener attached); otherwise keep waiting for boot.
      const err = await engineError().catch(() => null);
      if (err) {
        errorMsg = err;
        view = "error";
        return;
      }
      view = "offline";
      setTimeout(() => void decide(), 1500);
    }
  }

  // The sidecar gave up on the engine (crash-loop or failed start). Show what went wrong
  // instead of an endless "waiting" spinner.
  async function retry(): Promise<void> {
    view = "loading";
    errorMsg = "";
    try {
      await restartEngine();
    } catch {
      /* not managed, or restart failed; decide() will fall back to offline polling */
    }
    void decide();
  }

  onMount(() => {
    void decide();
    const unlisten = onEngineFailed((message) => {
      errorMsg = message;
      view = "error";
    });
    return () => void unlisten.then((fn) => fn());
  });
</script>

{#if view === "loading"}
  <div class="splash">Starting…</div>
{:else if view === "offline"}
  <div class="splash">Waiting for the engine…</div>
{:else if view === "error"}
  <div class="splash error">
    <strong>The engine failed to start</strong>
    <pre class="msg">{errorMsg}</pre>
    <button class="primary" onclick={retry}>Retry</button>
  </div>
{:else if view === "wizard"}
  <Wizard recheck={decide} />
{:else}
  <Panel />
{/if}
