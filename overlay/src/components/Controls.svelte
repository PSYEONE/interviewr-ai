<script lang="ts">
  import { session } from "../lib/session.svelte";

  // New finalizes the conversation; gate it until there is content to save so it can't spam
  // empty/duplicate conversations. resetView() clears these, so it greys out again afterwards.
  const canFinalize = $derived(session.turns.length > 0 || !!session.pending);
</script>

<div id="controls">
  <button title="Answer from current transcript" onclick={() => session.requestAnswer()}>
    Answer
  </button>
  <button title="Clear pending buffer" onclick={() => session.clearPending()}>Clear</button>
  <span class="text-dim text-sm ml-10">Style:</span>
  <button
    title={`Toggle between a script or bullet points`}
    onclick={() => session.toggleAnswerStyle()}
  >
    {session.answerStyle === "bullets" ? "Bullet" : "Script"}
  </button>
  {#if session.canThink}
    <span class="text-dim text-sm ml-10">Reasoning:</span>
    <button
      class:on={session.reasoning}
      title="Let the model think first. Better answers, but it can take a minute to start talking."
      onclick={() => session.toggleReasoning()}
    >
      {session.reasoning ? "On" : "Off"}
    </button>
  {/if}
  <span class="flex-1"></span>
  <button
    disabled={!canFinalize}
    title={canFinalize
      ? "Finalize this conversation and start a fresh topic"
      : "Nothing to finalize yet"}
    onclick={() => session.resetView()}
  >
    New
  </button>
</div>
