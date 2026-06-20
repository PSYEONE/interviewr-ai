<script lang="ts">
  import { onMount } from "svelte";

  import {
    deleteConversation,
    downloadExport,
    getConversation,
    listHistory,
  } from "../lib/api";
  import { minimizeWindow, quit, startDragging } from "../lib/tauri";
  import type { ConversationDetail, ConversationRecord } from "../lib/types";
  import AdvancedView from "./dashboard/AdvancedView.svelte";
  import ContextView from "./dashboard/ContextView.svelte";
  import ModelsView from "./dashboard/ModelsView.svelte";
  import Panel from "./Panel.svelte";

  type View = "home" | "interview" | "conversation" | "models" | "context" | "advanced";
  let view = $state<View>("home");

  let conversations = $state<ConversationRecord[]>([]);
  let historyError = $state<string | null>(null);
  let selected = $state<ConversationDetail | null>(null);

  function label(c: { title: string | null; preview: string; id: number }): string {
    return c.title || c.preview || `Conversation ${c.id}`;
  }
  function when(iso: string): string {
    const d = new Date(iso);
    return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
  }

  async function loadHistory(): Promise<void> {
    historyError = null;
    try {
      conversations = await listHistory();
    } catch (e) {
      historyError = e instanceof Error ? e.message : String(e);
    }
  }

  async function openConversation(id: number): Promise<void> {
    try {
      selected = await getConversation(id);
      view = "conversation";
    } catch (e) {
      historyError = e instanceof Error ? e.message : String(e);
    }
  }

  async function remove(id: number): Promise<void> {
    try {
      await deleteConversation(id);
      await loadHistory();
    } catch (e) {
      historyError = e instanceof Error ? e.message : String(e);
    }
  }

  function download(id: number): void {
    void downloadExport(`/history/${id}/export`, `conversation-${id}.md`);
  }

  // Returning from a live interview: turns/titles may have changed, so refresh.
  function goHome(): void {
    view = "home";
    void loadHistory();
  }

  function onBarMouseDown(e: MouseEvent): void {
    if (e.button !== 0 || (e.target as HTMLElement).closest("button")) return;
    void startDragging();
  }

  onMount(loadHistory);
</script>

{#if view === "interview"}
  <Panel onBack={goHome} />
{:else}
  <div class="wizard-shell">
    <div id="wizbar" role="toolbar" tabindex="-1" onmousedown={onBarMouseDown}>
      {#if view !== "home"}
        <button class="icon" title="Back" onclick={goHome}>&#8592;</button>
      {/if}
      <span id="brand">interviewr</span>
      <span class="flex-1"></span>
      <button class="icon" title="Minimize" onclick={() => void minimizeWindow()}>&#8211;</button>
      <button class="icon" title="Quit" onclick={() => void quit()}>&#10005;</button>
    </div>

    {#if view === "home"}
      <div class="wiz">
        <button class="primary" onclick={() => (view = "interview")}>Start interview</button>
        <button onclick={() => (view = "context")}>Change context</button>
        <button onclick={() => (view = "models")}>Change models</button>
        <button onclick={() => (view = "advanced")}>Advanced</button>

        <h2>History</h2>
        {#if historyError}
          <p class="text-warn">{historyError}</p>
        {/if}
        {#if conversations.length === 0}
          <p class="text-dim text-sm">No conversations yet. Start an interview to build history.</p>
        {:else}
          {#each conversations as c (c.id)}
            <div class="card flex items-center gap-2">
              <button
                class="row flex-1 text-left"
                onclick={() => void openConversation(c.id)}
                title="Open conversation"
              >
                <span class="block truncate">{label(c)}</span>
                <span class="text-dim text-xs">{when(c.started_at)} · {c.turn_count} turns</span>
              </button>
              <button class="icon" title="Download (.md)" onclick={() => download(c.id)}>&#8595;</button>
              <button class="icon" title="Delete" onclick={() => void remove(c.id)}>&#10005;</button>
            </div>
          {/each}
        {/if}
      </div>
    {:else if view === "conversation" && selected}
      <div class="wiz">
        <div class="flex items-center gap-2">
          <h1 class="flex-1 truncate">{label({ ...selected, preview: "" })}</h1>
          <button onclick={() => download(selected!.id)}>Download</button>
        </div>
        <p class="text-dim text-xs">{when(selected.started_at)}</p>
        {#each selected.turns as t (t.id)}
          <div class="card flex flex-col gap-1">
            <strong>Q: {t.question}</strong>
            <p class="text-ok whitespace-pre-wrap">{t.answer}</p>
          </div>
        {/each}
        {#if selected.turns.length === 0}
          <p class="text-dim text-sm">This conversation has no turns.</p>
        {/if}
      </div>
    {:else if view === "models"}
      <ModelsView back={goHome} />
    {:else if view === "context"}
      <ContextView back={goHome} />
    {:else if view === "advanced"}
      <AdvancedView back={goHome} />
    {/if}
  </div>
{/if}
