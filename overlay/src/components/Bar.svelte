<script lang="ts">
  import { session } from "../lib/session.svelte";
  import { startDragging } from "../lib/tauri";
  import WindowButtons from "./WindowButtons.svelte";

  let { onBack }: { onBack?: () => void } = $props();

  // data-tauri-drag-region does not fire on WebKitGTK; drive the drag from the title bar.
  function onBarMouseDown(e: MouseEvent): void {
    if (e.button !== 0 || (e.target as HTMLElement).closest("button")) return;
    void startDragging();
  }
</script>

<div id="bar" role="toolbar" tabindex="-1" onmousedown={onBarMouseDown}>
  {#if onBack}
    <button class="icon" title="Finalize and return to dashboard" onclick={onBack}>&#8592;</button>
  {/if}
  <span id="dot" data-state={session.status} title={session.status}></span>
  <span id="brand">interviewr</span>
  {#if session.captureSource}
    <span class="min-w-0 truncate">
      <span class="text-dim">Listening to:</span>
      {session.captureSource}
    </span>
  {/if}
  <span class="flex-1"></span>
  <WindowButtons />
</div>
