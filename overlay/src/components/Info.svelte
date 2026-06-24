<script lang="ts">
  // Small "i" info badge that pops a styled dark tooltip on hover (native title is light/laggy).
  let { text }: { text: string } = $props();
</script>

<span class="info" data-tip={text} role="note" aria-label={text}>i</span>
