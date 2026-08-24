<script lang="ts">
  import { onDestroy, onMount } from "svelte";

  import {
    deleteConversation,
    downloadExport,
    getConversation,
    listHistory,
  } from "../lib/api";
  import { session } from "../lib/session.svelte";
  import { startDragging } from "../lib/tauri";
  import type { ConversationDetail, ConversationRecord } from "../lib/types";
  import ContextView from "./dashboard/ContextView.svelte";
  import ModelsView from "./dashboard/ModelsView.svelte";
  import SettingsView from "./dashboard/SettingsView.svelte";
  import SystemPromptView from "./dashboard/SystemPromptView.svelte";
  import Panel from "./Panel.svelte";
  import Turn from "./Turn.svelte";
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
  // The detail header used to blank the preview, which made an untitled conversation render as
  // "Conversation 25". Fall back to its first question, exactly like the list does.
  function firstQuestion(c: ConversationDetail): string {
    return c.turns[0]?.question ?? "";
  }
  function turns(n: number): string {
    return `${n} turn${n === 1 ? "" : "s"}`;
  }

  let historyReq = 0; // monotonic id so an out-of-order response can't clobber a newer load

  async function loadHistory(): Promise<void> {
    const req = ++historyReq;
    historyError = null;
    try {
      const data = await listHistory();
      if (req !== historyReq) return; // a newer load superseded this one
      conversations = data;
    } catch (e) {
      if (req !== historyReq) return;
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

  // Filesystem-safe slug so a saved export says what it contains, not just which row it was.
  // Non-ASCII characters drop out entirely; an empty result falls back to the bare id.
  function slug(text: string): string {
    return text
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+/, "")
      .slice(0, 60)
      .replace(/-+$/, "");
  }

  function download(c: { id: number; title: string | null; preview: string }): void {
    const name = slug(c.title || c.preview);
    const file = name ? `conversation-${c.id}-${name}.md` : `conversation-${c.id}.md`;
    void downloadExport(`/history/${c.id}/export`, file);
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

  // The WS subscription is owned here, at the dashboard level, so it stays open across both the
  // home screen and the interview view and keeps receiving broadcast events on either.
  onMount(() => {
    session.start();
    void loadHistory();
  });
  onDestroy(() => session.stop());

  // Titles are generated server-side after a conversation is finalized and arrive over the WS.
  // Reloading on each one reflects the new title and surfaces the now-finalized conversation.
  $effect(() => {
    if (session.lastTitled !== null) void loadHistory();
  });
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
      <WindowButtons onSettings={() => (view = "settings")} />
    </div>

    {#if view === "home"}
      <div class="dash">
        <div class="dash-head">
          <div class="flex gap-2">
            <button class="flex-1" onclick={() => (view = "context")}>Change context</button>
            <button class="flex-1" onclick={() => (view = "models")}>Manage models</button>
            <button class="flex-1" onclick={() => (view = "advanced")}>Change system prompt</button>
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
                  class="row min-w-0 flex-1 py-2.5 text-left"
                  onclick={() => void openConversation(c.id)}
                  title="Open conversation"
                >
                  <span class="block truncate text-base">{label(c)}</span>
                  <span class="text-dim text-sm">{when(c.started_at)} · {turns(c.turn_count)}</span>
                </button>
                <button class="row-icon" title="Download (.md)" onclick={() => download(c)}>
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
              <div class="truncate font-semibold">{label({ ...selected, preview: firstQuestion(selected) })}</div>
              <div class="text-dim text-sm">{when(selected.started_at)}</div>
            </div>
            <button class="row-icon" title="Download (.md)" onclick={() => download({ ...selected!, preview: firstQuestion(selected!) })}>
              {@render downloadIcon()}
            </button>
          </div>
        </div>
        <!-- p-0 so .log supplies the padding, matching the interview log exactly. -->
        <div class="dash-body !p-0">
          <div class="log">
            {#each selected.turns as t (t.id)}
              <Turn question={t.question} answer={t.answer} />
            {/each}
            {#if selected.turns.length === 0}
              <p class="text-dim text-sm">This conversation has no turns.</p>
            {/if}
          </div>
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
