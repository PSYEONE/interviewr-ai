<script lang="ts">
  import { minimizeWindow, quit } from "../lib/tauri";
  import { windowControls as wc } from "../lib/windowControls.svelte";

  // Window buttons for the interview and dashboard bars; the settings button only appears when onSettings is passed.
  let { onSettings }: { onSettings?: () => void } = $props();
</script>

<button
  class:on={wc.pinned}
  disabled={!wc.pinSupported}
  title={wc.pinSupported
    ? "Toggle always-on-top"
    : "Always-on-top (needs KDE Plasma or an X11 session)"}
  onclick={() => void wc.togglePin()}
>
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
<!-- Collapse-to-bar belongs to the interview view (it shrinks to the two bars). On the dashboard
     the slot is kept but rendered invisible + inert, so Pin/Stealth hold a fixed position and the
     chevron simply appears in place when you enter an interview, with no reflow. This collapse
     button carries the single ml-3 separation from Pin/Stealth in both views. -->
<button
  class="icon chevron ml-3"
  class:up={!wc.collapsed}
  style:visibility={onSettings ? "hidden" : "visible"}
  disabled={!!onSettings}
  aria-hidden={!!onSettings}
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
<button
  class="icon"
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
