<script lang="ts">
  import { onMount } from "svelte";

  import {
    getCaptureSupported,
    getPin,
    getStealth,
    minimizeWindow,
    quit,
    setPin,
    setStealth,
  } from "../lib/tauri";

  // Window-level controls shared by the live panel bar and the dashboard bar: pin (always-on-top)
  // and stealth (hide from screen capture) toggles, then a gap, then minimize + quit.
  let pinned = $state(false);
  let stealth = $state(false);
  let captureSupported = $state(true);

  async function togglePin(): Promise<void> {
    pinned = !pinned;
    try {
      await setPin(pinned);
    } catch {
      /* compositor may reject; the toggle reflects intent */
    }
  }

  async function toggleStealth(): Promise<void> {
    stealth = !stealth;
    try {
      await setStealth(stealth);
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
        stealth = await getStealth();
        // On KWin 6.6 the runtime property is lost on restart; re-apply the stored choice now.
        if (stealth) await setStealth(true);
      }
    } catch {
      captureSupported = false;
    }
  });
</script>

<button class:on={pinned} title="Toggle always-on-top" onclick={togglePin}>Pin</button>
<button
  class:on={stealth}
  disabled={!captureSupported}
  title={captureSupported
    ? "Stealth: keep the overlay out of screen recordings (KDE Plasma 6.6+)"
    : "Stealth: keep out of screen recordings (requires KDE Plasma 6.6+)"}
  onclick={toggleStealth}
>
  Stealth
</button>
<button class="icon ml-3" title="Minimize" onclick={() => void minimizeWindow()}>&#8211;</button>
<button class="icon" title="Quit" onclick={() => void quit()}>&#10005;</button>
