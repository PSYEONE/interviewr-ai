<script lang="ts">
  import { onMount } from "svelte";

  import {
    deleteConversation,
    downloadExport,
    getConversation,
    listHistory,
  } from "../lib/api";
  import { startDragging } from "../lib/tauri";
  import type { ConversationDetail, ConversationRecord } from "../lib/types";
  import ContextView from "./dashboard/ContextView.svelte";
  import ModelsView from "./dashboard/ModelsView.svelte";
  import SettingsView from "./dashboard/SettingsView.svelte";
  import SystemPromptView from "./dashboard/SystemPromptView.svelte";
  import Panel from "./Panel.svelte";
  import WindowButtons from "./WindowButtons.svelte";

  type View =
    | "home"
    | "interview"
    | "conversation"
    | "models"
    | "context"
    | "advanced"
    | "settings";
  let view = $state<View>("home");

  let conversations = $state<ConversationRecord[]>([]);
  let historyError = $state<string | null>(null);
  let selected = $state<ConversationDetail | null>(null);
  let armedId = $state<number | null>(null); // row whose delete is "armed" (click again to confirm)

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
    armedId = null;
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
    armedId = null;
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
    width="24"
    height="24"
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
      <WindowButtons />
    </div>

    {#if view === "home"}
      <div class="dash">
        <div class="dash-head">
          <div class="flex gap-2">
            <button class="flex-1" onclick={() => (view = "context")}>Change context</button>
            <button class="flex-1" onclick={() => (view = "models")}>Change models</button>
            <button class="flex-1" onclick={() => (view = "advanced")}>Change system prompt</button>
            <button class="flex-1" onclick={() => (view = "settings")}>Settings</button>
            <button class="primary flex-1" onclick={() => (view = "interview")}>
              Start interview
            </button>
          </div>
        </div>
        <div class="dash-body !pt-0">
          <h2 class="dash-sticky">History</h2>
          {#if historyError}
            <p class="text-warn">{historyError}</p>
          {/if}
          {#if conversations.length === 0}
            <p class="text-dim text-sm">No conversations yet. Start an interview to build history.</p>
          {:else}
            {#each conversations as c (c.id)}
              <div class="flex gap-2">
                <button
                  class="row flex-1 py-2.5 text-left"
                  onclick={() => void openConversation(c.id)}
                  title="Open conversation"
                >
                  <span class="block truncate text-base">{label(c)}</span>
                  <span class="text-dim text-sm">{when(c.started_at)} · {turns(c.turn_count)}</span>
                </button>
                <button class="row-icon" title="Download (.md)" onclick={() => download(c.id)}>
                  {@render downloadIcon()}
                </button>
                <button
                  class="row-icon text-2xl"
                  class:armed={armedId === c.id}
                  title={armedId === c.id ? "Click again to delete" : "Delete"}
                  onclick={() => {
                    if (armedId === c.id) void remove(c.id);
                    else armedId = c.id;
                  }}
                  onmouseleave={() => {
                    if (armedId === c.id) armedId = null;
                  }}
                >
                  &#10005;
                </button>
              </div>
            {/each}
          {/if}
        </div>
      </div>
    {:else if view === "conversation" && selected}
      <div class="dash">
        <div class="dash-head">
          <div class="flex items-stretch gap-2">
            <button onclick={goHome}>Back</button>
            <div class="min-w-0 flex-1 self-center">
              <div class="truncate font-semibold">{label({ ...selected, preview: "" })}</div>
              <div class="text-dim text-sm">{when(selected.started_at)}</div>
            </div>
            <button class="row-icon" title="Download (.md)" onclick={() => download(selected!.id)}>
              {@render downloadIcon()}
            </button>
          </div>
        </div>
        <div class="dash-body">
          {#each selected.turns as t (t.id)}
            <div class="flex flex-col gap-1">
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
      <SystemPromptView back={goHome} />
    {:else if view === "settings"}
      <SettingsView back={goHome} />
    {/if}
  </div>
{/if}
