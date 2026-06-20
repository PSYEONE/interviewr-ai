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
  let confirmId = $state<number | null>(null); // row pending a delete confirmation

  function label(c: { title: string | null; preview: string; id: number }): string {
    return c.title || c.preview || `Conversation ${c.id}`;
  }
  function when(iso: string): string {
    const d = new Date(iso);
    return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
  }
  function turns(n: number): string {
    return `${n} turn${n === 1 ? "" : "s"}`;
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
    confirmId = null;
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
    confirmId = null;
    void loadHistory();
  }

  function onBarMouseDown(e: MouseEvent): void {
    if (e.button !== 0 || (e.target as HTMLElement).closest("button")) return;
    void startDragging();
  }

  onMount(loadHistory);
</script>

{#snippet downloadIcon()}
  <svg
    viewBox="0 0 24 24"
    width="15"
    height="15"
    fill="none"
    stroke="currentColor"
    stroke-width="2"
    stroke-linecap="round"
    stroke-linejoin="round"
    aria-hidden="true"
  >
    <path d="M12 3v12" />
    <path d="M7 11l5 5 5-5" />
    <path d="M5 21h14" />
  </svg>
{/snippet}

{#if view === "interview"}
  <Panel onBack={goHome} />
{:else}
  <div class="wizard-shell">
    <div id="wizbar" role="toolbar" tabindex="-1" onmousedown={onBarMouseDown}>
      <span id="brand">interviewr</span>
      <span class="flex-1"></span>
      <button class="icon" title="Minimize" onclick={() => void minimizeWindow()}>&#8211;</button>
      <button class="icon" title="Quit" onclick={() => void quit()}>&#10005;</button>
    </div>

    {#if view === "home"}
      <div class="dash">
        <div class="dash-head">
          <button class="primary" onclick={() => (view = "interview")}>Start interview</button>
          <button onclick={() => (view = "context")}>Change context</button>
          <button onclick={() => (view = "models")}>Change models</button>
          <button onclick={() => (view = "advanced")}>Advanced</button>
          <h2>History</h2>
        </div>
        <div class="dash-body">
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
                  <span class="text-dim text-xs">{when(c.started_at)} · {turns(c.turn_count)}</span>
                </button>
                {#if confirmId === c.id}
                  <span class="text-warn text-xs">Delete?</span>
                  <button class="icon" onclick={() => void remove(c.id)}>Yes</button>
                  <button class="icon" onclick={() => (confirmId = null)}>No</button>
                {:else}
                  <button class="icon" title="Download (.md)" onclick={() => download(c.id)}>
                    {@render downloadIcon()}
                  </button>
                  <button class="icon" title="Delete" onclick={() => (confirmId = c.id)}>
                    &#10005;
                  </button>
                {/if}
              </div>
            {/each}
          {/if}
        </div>
      </div>
    {:else if view === "conversation" && selected}
      <div class="dash">
        <div class="dash-head flex-row items-center gap-2">
          <button onclick={goHome}>Back</button>
          <span class="flex-1 truncate font-semibold">{label({ ...selected, preview: "" })}</span>
          <button class="icon" title="Download (.md)" onclick={() => download(selected!.id)}>
            {@render downloadIcon()}
          </button>
        </div>
        <div class="dash-body">
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
