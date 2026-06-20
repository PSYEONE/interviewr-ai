<script lang="ts">
  import { onMount } from "svelte";

  import { deleteDocument, listDocuments, uploadDocument, type DocumentRecord } from "../../lib/api";

  let { back }: { back: () => void } = $props();

  let docs = $state<DocumentRecord[]>([]);
  let error = $state<string | null>(null);
  let busy = $state(false);

  const KINDS: { kind: string; label: string }[] = [
    { kind: "resume", label: "Resume / CV" },
    { kind: "job_description", label: "Job description" },
    { kind: "other", label: "Other notes" },
  ];

  function kindLabel(kind: string): string {
    return KINDS.find((k) => k.kind === kind)?.label ?? kind;
  }

  // RAG returns 503 when no embed model is available; point the user at the fix.
  function hint(e: unknown): string {
    const m = e instanceof Error ? e.message : String(e);
    return m.includes("503")
      ? "Needs a local embed model (nomic-embed-text). Add it under Change models, then retry."
      : m;
  }

  async function load(): Promise<void> {
    try {
      docs = await listDocuments();
    } catch (e) {
      error = e instanceof Error ? e.message : String(e);
    }
  }

  async function add(kind: string, file?: File): Promise<void> {
    if (!file) return;
    busy = true;
    error = null;
    try {
      await uploadDocument(kind, file);
      await load();
    } catch (e) {
      error = hint(e);
    } finally {
      busy = false;
    }
  }

  async function remove(id: number): Promise<void> {
    error = null;
    try {
      await deleteDocument(id);
      await load();
    } catch (e) {
      error = e instanceof Error ? e.message : String(e);
    }
  }

  onMount(load);
</script>

<div class="wiz">
  <h1>Context</h1>
  <p class="text-xs">
    Ground answers in your background. Files stay on this machine; changes apply live (no restart).
  </p>

  {#if error}<p class="text-warn">{error}</p>{/if}

  <h2>Current documents</h2>
  {#if docs.length === 0}
    <p class="text-dim text-sm">No documents yet.</p>
  {:else}
    {#each docs as d (d.id)}
      <div class="card flex items-center gap-2">
        <span class="flex-1 truncate">{d.name}</span>
        <span class="badge-muted">{kindLabel(d.kind)}</span>
        <span class="text-dim text-xs">{d.chunk_count} chunks</span>
        <button class="icon" title="Delete" onclick={() => void remove(d.id)}>&#10005;</button>
      </div>
    {/each}
  {/if}

  <h2>Add a document</h2>
  {#each KINDS as k (k.kind)}
    <label class="filebtn">
      <input
        type="file"
        accept=".txt,.md,.pdf,.docx"
        disabled={busy}
        onchange={(e) => add(k.kind, e.currentTarget.files?.[0])}
      />
      {k.label} (txt, md, pdf, docx)
    </label>
  {/each}
  {#if busy}<p class="text-dim text-xs">uploading…</p>{/if}

  <div class="flex-1"></div>
  <div class="flex"><button onclick={back}>Back</button></div>
</div>
