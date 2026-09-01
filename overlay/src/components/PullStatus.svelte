<script lang="ts">
  import { pull } from "../lib/pull.svelte";

  // Mounted in both shells so a download stays visible and cancellable from any screen. Leaving
  // the model picker no longer hides a pull that is still running.
</script>

{#if pull.active}
  <div id="pullbar">
    <span class="min-w-0 flex-1">
      <span class="truncate font-semibold">{pull.active.model}</span>
      <span class="text-dim text-xs"> · {pull.active.status} {pull.active.pct.toFixed(0)}%</span>
      <span class="bar-track mt-1 block">
        <span class="bar-fill block" style="width:{pull.active.pct}%"></span>
      </span>
    </span>
    <button disabled={pull.cancelling} onclick={() => pull.cancel()}>
      {pull.cancelling ? "Cancelling…" : "Cancel"}
    </button>
  </div>
{/if}
