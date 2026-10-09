<script lang="ts">
  import { onDestroy, onMount } from "svelte";

  import { getHealth } from "../lib/api";
  import { session } from "../lib/session.svelte";
  import { windowControls } from "../lib/windowControls.svelte";
  import Bar from "./Bar.svelte";
  import ChatLog from "./ChatLog.svelte";
  import Controls from "./Controls.svelte";

  let { onBack }: { onBack?: () => void } = $props();

  // The speech model's state lives on the session store, so the log's one line can report it
  // rather than a banner contradicting the "Listening" text underneath.
  let poll: ReturnType<typeof setInterval> | null = null;

  async function checkStt(): Promise<void> {
    try {
      const health = await getHealth();
      session.sttReady = health.stt_ready;
      session.sttPercent = health.stt_progress;
      // Fixed for the engine's lifetime, so one read is enough to decide on the reasoning toggle.
      session.canThink = health.llm_can_think;
    } catch {
      /* engine busy or restarting; try again on the next tick */
    }
    if (session.sttReady && poll !== null) {
      clearInterval(poll);
      poll = null;
    }
  }

  // Audio capture runs only inside an interview, so resume on enter and pause on leave. The WS
  // stays open (owned by the dashboard); leaving also finalizes the conversation via resetView.
  onMount(() => {
    session.beginInterview();
    session.send("resume");
    void checkStt();
    poll = setInterval(() => void checkStt(), 2000);
  });
  onDestroy(() => {
    if (poll !== null) clearInterval(poll);
    windowControls.resetCollapse(); // never leave the dashboard stranded at strip height
    session.resetView();
    session.send("pause");
  });
</script>

<Bar {onBack} />
<ChatLog />
<Controls />
