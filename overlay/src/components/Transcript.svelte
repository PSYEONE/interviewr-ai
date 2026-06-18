<script lang="ts">
  import { session } from "../lib/session.svelte";

  let el: HTMLDivElement | undefined;
  let stick = true;

  function onScroll(): void {
    if (el) stick = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
  }

  // Keep pinned to the bottom as new text lands, unless the user has scrolled up.
  $effect(() => {
    void session.committed;
    void session.live;
    if (stick && el) el.scrollTop = el.scrollHeight;
  });
</script>

<div id="transcript" bind:this={el} onscroll={onScroll}>
  <span id="t-done">{session.committed}</span><span id="t-live">{session.live}</span>
</div>
