<script lang="ts">
  import { onMount } from "svelte";

  import {
    getCaptureHidden,
    getCaptureSupported,
    getPin,
    minimizeWindow,
    quit,
    setCaptureHidden,
    setPin,
  } from "../lib/tauri";

  // Window-level controls shared by the live panel bar and the dashboard bar: pin (always-on-top)
  // and hide-from-capture toggles, then a gap, then minimize + quit.
  let pinned = $state(false);
  let hidden = $state(false);
  let captureSupported = $state(true);

  async function togglePin(): Promise<void> {
    pinned = !pinned;
    try {
      await setPin(pinned);
    } catch {
      /* compositor may reject; the toggle reflects intent */
    }
  }

  async function toggleHide(): Promise<void> {
    hidden = !hidden;
    try {
      await setCaptureHidden(hidden);
    } catch {
      /* ignore */
    }
  }

  onMount(async () => {
    // Initialize toggles from the real persisted state so they never lie after a restart.
    try {
      pinned = await getPin();
    } catch {
      /* keep default */
    }
    try {
      captureSupported = await getCaptureSupported();
      if (captureSupported) {
        hidden = await getCaptureHidden();
        // On KWin 6.6 the runtime property is lost on restart; re-apply the stored choice now.
        if (hidden) await setCaptureHidden(true);
      }
    } catch {
      captureSupported = false;
    }
  });
</script>

<button class:on={pinned} title="Toggle always-on-top" onclick={togglePin}>Pin</button>
<button
  class:on={hidden}
  disabled={!captureSupported}
  title={captureSupported
    ? "Hide overlay from screen recording (KDE Plasma 6.6+)"
    : "Hide from screen recording (requires KDE Plasma 6.6+)"}
  onclick={toggleHide}
>
  Hide
</button>
<button class="icon ml-3" title="Minimize" onclick={() => void minimizeWindow()}>&#8211;</button>
<button class="icon" title="Quit" onclick={() => void quit()}>&#10005;</button>
