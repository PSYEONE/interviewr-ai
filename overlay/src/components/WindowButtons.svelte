<script lang="ts">
  import { minimizeWindow, quit } from "../lib/tauri";
  import { windowControls as wc } from "../lib/windowControls.svelte";

  // Window controls shared by the live panel bar and the dashboard bar. Pin/Stealth state lives in
  // the windowControls store so global hotkeys and these buttons stay in sync. The settings cog is
  // optional (only the dashboard passes onSettings). init() runs once in App.svelte on mount.
  let { onSettings }: { onSettings?: () => void } = $props();
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
{#if !onSettings}
  <!-- Collapse-to-bar only exists in the interview view (it shrinks to the two bars); the
       dashboard has no #controls bar to collapse to, and shows the settings cog in this slot. -->
  <button
    class="icon chevron ml-3"
    class:up={!wc.collapsed}
    title={wc.collapsed ? "Expand" : "Collapse to bar"}
    aria-label={wc.collapsed ? "Expand" : "Collapse to bar"}
    onclick={() => void wc.toggleCollapse()}
  >
    <svg
      viewBox="0 0 24 24"
      width="15"
      height="15"
      fill="none"
      stroke="currentColor"
      stroke-width="2"
      stroke-linecap="round"
      stroke-linejoin="round"
      aria-hidden="true"
    >
      <path d="M6 9l6 6 6-6" />
    </svg>
  </button>
{/if}
<button
  class="icon ml-3"
  title={onSettings ? "Settings" : "Can't open during an interview"}
  disabled={!onSettings}
  onclick={onSettings}
  aria-label="Settings"
>
    <svg
      viewBox="0 0 24 24"
      width="15"
      height="15"
      fill="none"
      stroke="currentColor"
      stroke-width="2"
      stroke-linecap="round"
      stroke-linejoin="round"
      aria-hidden="true"
    >
      <circle cx="12" cy="12" r="3" />
      <path
        d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09a1.65 1.65 0 0 0-1-1.51 1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09a1.65 1.65 0 0 0 1.51-1 1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33h.09a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82v.09a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"
      />
    </svg>
</button>
<button
  class="icon"
  disabled={wc.stealth}
  title={wc.stealth
    ? "Unavailable while stealthed (no taskbar entry to restore from)"
    : "Minimize"}
  onclick={() => void minimizeWindow()}
>
  &#8211;
</button>
<button class="icon" title="Quit" onclick={() => void quit()}>&#10005;</button>
