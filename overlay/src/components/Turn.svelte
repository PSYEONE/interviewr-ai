<script lang="ts">
  import { markdown } from "../lib/markdown";

  // One question and its answer. Shared by the live interview log and the history conversation
  // view so a past conversation is styled exactly like the interview that produced it.
  let { question, answer }: { question: string; answer: string } = $props();
</script>

<section class="turn">
  {#if question}
    <!-- Sticky: scrolling back through turns, each question pins to the top until the next one
         displaces it. Opaque background so text passes under rather than through it. -->
    <h2 class="turn-q">{question}</h2>
  {/if}
  <div class="turn-a" use:markdown={answer}></div>
</section>
