<script lang="ts">
  import { session } from "../lib/session.svelte";

  let el: HTMLDivElement | undefined;
  let stick = true;

  function onScroll(): void {
    if (el) stick = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
  }

  $effect(() => {
    void session.answer;
    if (stick && el) el.scrollTop = el.scrollHeight;
  });
</script>

<div id="answer" bind:this={el} onscroll={onScroll}>{session.answer}</div>
