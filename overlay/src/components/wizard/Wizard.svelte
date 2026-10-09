<script lang="ts">
  import { onMount } from "svelte";

  import { minimizeWindow, quit, startDragging } from "../../lib/tauri";
  import type { SystemReport } from "../../lib/types";
  import CloudSetup from "./CloudSetup.svelte";
  import Context from "./Context.svelte";
  import Done from "./Done.svelte";
  import GpuCheck from "./GpuCheck.svelte";
  import LocalSetup from "./LocalSetup.svelte";
  import PullStatus from "../PullStatus.svelte";
  import Welcome from "./Welcome.svelte";
  import { windowControls } from "../../lib/windowControls.svelte";

  let { recheck }: { recheck: () => void } = $props();

  onMount(() => {
    void windowControls.enterWizard();
    return () => windowControls.leaveWizard();
  });

  type Step = "welcome" | "gpu" | "local" | "cloud" | "context" | "done";
  let step = $state<Step>("welcome");
  let report = $state<SystemReport | null>(null);
  let updates = $state<Record<string, unknown>>({});
  // Kept typed as well as in `updates`, because the next screen sizes chat models against it.
  let speechModel = $state("");
  // Remember which model path was taken so Context's Back returns to the right screen.
  let setupStep = $state<"local" | "cloud">("local");

  function chooseLocal(r: SystemReport, whisperModel: string): void {
    report = r;
    speechModel = whisperModel;
    updates = { ...updates, whisper_model: whisperModel };
    setupStep = "local";
    step = "local";
  }
  function chooseCloud(r: SystemReport, whisperModel: string): void {
    report = r;
    // Speech is always transcribed locally, so the choice applies on the cloud path too.
    speechModel = whisperModel;
    updates = { ...updates, whisper_model: whisperModel };
    setupStep = "cloud";
    step = "cloud";
  }
  function complete(delta: Record<string, unknown>): void {
    updates = { ...updates, ...delta };
    step = "context";
  }

  function onBarMouseDown(e: MouseEvent): void {
    if (e.button !== 0 || (e.target as HTMLElement).closest("button")) return;
    void startDragging();
  }
</script>

<div class="wizard-shell">
  <div id="wizbar" role="toolbar" tabindex="-1" onmousedown={onBarMouseDown}>
    <span id="brand">interviewr · setup</span>
    <span class="flex-1"></span>
    <button class="icon" title="Minimize" onclick={() => void minimizeWindow()}>&#8211;</button>
    <button class="icon" title="Close" onclick={() => void quit()}>&#10005;</button>
  </div>

    <PullStatus />

  {#if step === "welcome"}
    <Welcome next={() => (step = "gpu")} />
  {:else if step === "gpu"}
    <GpuCheck goLocal={chooseLocal} goCloud={chooseCloud} back={() => (step = "welcome")} />
  {:else if step === "local" && report}
    <LocalSetup
      {report}
      whisperModel={speechModel}
      {complete}
      back={() => (step = "gpu")}
    />
  {:else if step === "cloud"}
    <CloudSetup {complete} back={() => (step = "gpu")} />
  {:else if step === "context"}
    <Context next={() => (step = "done")} back={() => (step = setupStep)} />
  {:else if step === "done"}
    <Done {updates} {report} {recheck} back={() => (step = "context")} />
  {/if}
</div>
