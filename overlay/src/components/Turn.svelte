<script lang="ts">
  import { markdown } from "../lib/markdown";

  // One question and its answer. Shared by the live interview log and the history conversation
  // view so a past conversation is styled exactly like the interview that produced it.
  let {
    question,
    answer,
    reasoning = "",
    thoughtMs = null,
  }: {
    question: string;
    answer: string;
    reasoning?: string;
    thoughtMs?: number | null;
  } = $props();

  let pane: HTMLDivElement | null = $state(null);
  // Capped at a few lines, so a minute of reasoning never pushes the conversation off screen.
  $effect(() => {
    if (pane && reasoning) pane.scrollTop = pane.scrollHeight;
  });

  const thoughtFor = $derived(
    thoughtMs === null ? "" : `Thought for ${Math.max(1, Math.round(thoughtMs / 1000))}s`,
  );
</script>

<section class="turn">
  {#if question}
    <!-- Sticky: scrolling back through turns, each question pins to the top until the next one
         displaces it. Opaque background so text passes under rather than through it. -->
    <h2 class="turn-q">{question}</h2>
  {/if}
  {#if reasoning}
    {#if answer}
      <details class="turn-thought">
        <summary>{thoughtFor || "Thinking"}</summary>
        <div class="turn-thought-all">{reasoning}</div>
      </details>
    {:else}
      <div class="turn-think" bind:this={pane}>{reasoning}</div>
    {/if}
  {/if}
  <div class="turn-a" use:markdown={answer}></div>
</section>
