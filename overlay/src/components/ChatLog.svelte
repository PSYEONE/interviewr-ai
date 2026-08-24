<script lang="ts">
  import { session } from "../lib/session.svelte";
  import Turn from "./Turn.svelte";

  let el: HTMLDivElement | undefined;
  let stick = true;

  function onScroll(): void {
    if (el) stick = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
  }

  // One stick-to-bottom for the whole log (the old split panes each had their own). Reading the
  // state is what registers the dependency, so a new token or a new turn re-pins.
  $effect(() => {
    void session.turns;
    void session.pending;
    void session.live;
    if (stick && el) el.scrollTop = el.scrollHeight;
  });
</script>

<div id="chat" bind:this={el} onscroll={onScroll}>
  <div class="log">
    {#each session.turns as turn (turn.id)}
      <Turn question={turn.question} answer={turn.answer} />
    {/each}

    {#if session.pending || session.live}
      <section class="turn">
        <p class="turn-pending">
          <span>{session.pending}</span><span id="t-live">{session.live}</span>
        </p>
      </section>
    {/if}

    {#if session.turns.length === 0 && !session.pending && !session.live}
      <p class="turn-empty">Listening. Ask a question, then hit Answer.</p>
    {/if}
  </div>
</div>
