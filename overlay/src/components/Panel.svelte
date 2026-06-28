<script lang="ts">
  import { onDestroy, onMount } from "svelte";

  import { session } from "../lib/session.svelte";
  import { windowControls } from "../lib/windowControls.svelte";
  import Answer from "./Answer.svelte";
  import Bar from "./Bar.svelte";
  import Controls from "./Controls.svelte";
  import Transcript from "./Transcript.svelte";

  let { onBack }: { onBack?: () => void } = $props();

  // Audio capture runs only inside an interview, so resume on enter and pause on leave. The WS
  // stays open (owned by the dashboard); leaving also finalizes the conversation via resetView.
  onMount(() => session.send("resume"));
  onDestroy(() => {
    windowControls.resetCollapse(); // never leave the dashboard stranded at strip height
    session.resetView();
    session.send("pause");
  });
</script>

<Bar {onBack} />
<Transcript />
<Answer />
<Controls />
