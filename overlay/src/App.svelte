<script lang="ts">
  import { onMount } from "svelte";

  import Dashboard from "./components/Dashboard.svelte";
  import Wizard from "./components/wizard/Wizard.svelte";
  import { getHealth } from "./lib/api";
  import { hotkeys } from "./lib/hotkeys.svelte";
  import { session } from "./lib/session.svelte";
  import { engineError, onEngineFailed, restartEngine, setLive } from "./lib/tauri";
  import { windowControls } from "./lib/windowControls.svelte";

  type View = "loading" | "wizard" | "dashboard" | "offline" | "error";
  let view = $state<View>("loading");
  let errorMsg = $state("");

  // Shows the wizard until a model is configured, otherwise the dashboard; keeps retrying while the engine starts.
  async function decide(): Promise<void> {
    if (view === "error") return; // a hard engine failure takes precedence over polling
    try {
      const health = await getHealth();
      // Only checks "configured", so an engine with a broken model still opens the dashboard where it can be fixed.
      view = health.configured ? "dashboard" : "wizard";
    } catch {
      // Engine unreachable: check for a startup failure reported before we were listening, otherwise keep waiting.
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

  // Restarts the engine after it failed to start or kept crashing.
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

  // Tray icon follows the session: blue while listening or thinking, grey otherwise.
  $effect(() => {
    const live = session.status === "listening" || session.status === "thinking";
    setLive(live).catch(() => {});
  });

  onMount(() => {
    void decide();
    // Window controls + global hotkeys are app-wide; init once regardless of the current view.
    void windowControls.init();
    void hotkeys.init();
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
  <Dashboard />
{/if}
