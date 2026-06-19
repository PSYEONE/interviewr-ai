<script lang="ts">
  import { onDestroy, onMount } from "svelte";

  import { session } from "../lib/session.svelte";
  import Answer from "./Answer.svelte";
  import Bar from "./Bar.svelte";
  import Transcript from "./Transcript.svelte";

  onMount(() => {
    session.start();
  });
  onDestroy(() => {
    session.stop();
  });
</script>

<Bar />
<Transcript />
<Answer />
