<script lang="ts">
  // Back button that refuses to throw away unsaved edits on one click: while `dirty`, the first
  // click only arms it (red, "Discard?") and the second leaves. Same arm-then-confirm idiom as the
  // model picker's Delete, sharing its `button.armed` styling.
  let {
    dirty,
    back,
    disabled = false,
  }: { dirty: boolean; back: () => void; disabled?: boolean } = $props();

  let armed = $state(false);

  function click(): void {
    // A clean form is the common case and must stay a single click.
    if (!dirty || armed) {
      back();
      return;
    }
    armed = true;
  }
</script>

<button
  class:armed={armed && dirty}
  {disabled}
  title={armed && dirty ? "Click again to leave; your edits will be lost" : "Back"}
  onclick={click}
  onmouseleave={() => (armed = false)}
>
  <!-- The longest label reserves the width in a shared grid cell, so arming swaps the text
       without resizing the button and shifting the row it sits in. -->
  <span class="grid">
    <span class="invisible col-start-1 row-start-1">Discard?</span>
    <span class="col-start-1 row-start-1">{armed && dirty ? "Discard?" : "Back"}</span>
  </span>
</button>
