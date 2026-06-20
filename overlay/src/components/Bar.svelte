<script lang="ts">
  import { onMount } from "svelte";

  import { session } from "../lib/session.svelte";
  import {
    getCaptureHidden,
    getCaptureSupported,
    getPin,
    minimizeWindow,
    quit,
    setCaptureHidden,
    setPin,
    startDragging,
  } from "../lib/tauri";

  let { onBack }: { onBack?: () => void } = $props();

  let pinned = $state(true);
  let hidden = $state(false);
  let captureSupported = $state(true);

  // data-tauri-drag-region does not fire on WebKitGTK; drive the drag from the title bar.
  function onBarMouseDown(e: MouseEvent): void {
    if (e.button !== 0 || (e.target as HTMLElement).closest("button")) return;
    void startDragging();
  }

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

<div id="bar" role="toolbar" tabindex="-1" onmousedown={onBarMouseDown}>
  {#if onBack}
    <button class="icon" title="Back to dashboard" onclick={onBack}>&#8592;</button>
  {/if}
  <span id="dot" data-state={session.status} title={session.status}></span>
  <span id="brand">interviewr</span>
  <span class="flex-1"></span>
  <button title="Answer from current transcript" onclick={() => session.send("answer")}>
    Answer
  </button>
  <button title="Clear pending buffer" onclick={() => session.send("clear")}>Clear</button>
  <button title="Reset session" onclick={() => session.resetView()}>Reset</button>
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
  <button class="icon" title="Minimize" onclick={() => void minimizeWindow()}>&#8211;</button>
  <button class="icon" title="Quit" onclick={() => void quit()}>&#10005;</button>
</div>
