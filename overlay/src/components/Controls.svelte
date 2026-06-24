<script lang="ts">
  import { session } from "../lib/session.svelte";

  // New finalizes the conversation; gate it until there is content to save so it can't spam
  // empty/duplicate conversations. resetView() clears these, so it greys out again afterwards.
  const canFinalize = $derived(!!session.committed || !!session.answer);
</script>

<div id="controls">
  <button title="Answer from current transcript" onclick={() => session.send("answer")}>
    Answer
  </button>
  <button title="Clear pending buffer" onclick={() => session.send("clear")}>Clear</button>
  <span class="text-dim text-sm ml-10">Style:</span>
  <button
    title={`Toggle between a script or bullet points`}
    onclick={() => session.toggleAnswerStyle()}
  >
    {session.answerStyle === "bullets" ? "Bullet" : "Script"}
  </button>
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
