<script lang="ts">
  import { minimizeWindow, quit } from "../lib/tauri";
  import { windowControls as wc } from "../lib/windowControls.svelte";

  // Window controls shared by the live panel bar and the dashboard bar. Pin/Stealth state lives in
  // the windowControls store so global hotkeys and these buttons stay in sync. init() runs once in
  // App.svelte on mount.
</script>

<button class:on={wc.pinned} title="Toggle always-on-top" onclick={() => void wc.togglePin()}>
  Pin
</button>
<button
  class:on={wc.stealth}
  disabled={!wc.captureSupported}
  title={wc.captureSupported
    ? "Hide from screen recordings"
    : "Hide from screen recordings (needs KDE Plasma 6.6+)"}
  onclick={() => void wc.toggleStealth()}
>
  Stealth
</button>
<button class="icon ml-3" title="Minimize" onclick={() => void minimizeWindow()}>&#8211;</button>
<button class="icon" title="Quit" onclick={() => void quit()}>&#10005;</button>
