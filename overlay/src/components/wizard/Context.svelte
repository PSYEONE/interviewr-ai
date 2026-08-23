<script lang="ts">
  import { uploadDocument } from "../../lib/api";

  let { next, back }: { next: () => void; back: () => void } = $props();

  interface Slot {
    name: string | null;
    chunks: number | null;
    error: string | null;
    busy: boolean;
  }
  const resume = $state<Slot>({ name: null, chunks: null, error: null, busy: false });
  const jd = $state<Slot>({ name: null, chunks: null, error: null, busy: false });

  function ragHint(e: unknown): string {
    const msg = e instanceof Error ? e.message : String(e);
    if (msg.includes("503")) {
      return "Needs a local embed model (nomic-embed-text). Add it in the model step, finish setup and restart, then upload here.";
    }
    return msg;
  }

  async function upload(kind: "resume" | "job_description", slot: Slot, file?: File): Promise<void> {
    if (!file) return;
    slot.name = file.name;
    slot.error = null;
    slot.chunks = null;
    slot.busy = true;
    try {
      const rec = await uploadDocument(kind, file);
      slot.chunks = rec.chunk_count;
    } catch (e) {
      slot.error = ragHint(e);
    } finally {
      slot.busy = false;
    }
  }
</script>

<div class="wiz">
  <h1>Add context (optional)</h1>
  <p class="text-xs">
    Give answers context from your background. Your resume and the job description stay on this
    machine.
  </p>

  {#snippet slotCard(title: string, kind: "resume" | "job_description", slot: Slot)}
    <div class="card">
      <div class="flex items-center gap-2">
        <strong class="flex-1">{title}</strong>
        {#if slot.busy}
          <span class="text-dim text-xs">uploading…</span>
        {:else if slot.chunks !== null}
          <span class="badge">{slot.chunks} chunks</span>
        {/if}
      </div>
      <label class="filebtn">
        <input
          type="file"
          accept=".txt,.md,.pdf,.docx"
          onchange={(e) => upload(kind, slot, e.currentTarget.files?.[0])}
        />
        {slot.name ?? "Choose a file (txt, md, pdf, docx)"}
      </label>
      {#if slot.error}<p class="text-warn">{slot.error}</p>{/if}
    </div>
  {/snippet}

  {@render slotCard("Resume / CV", "resume", resume)}
  {@render slotCard("Job description", "job_description", jd)}

  <div class="flex-1"></div>
  <div class="flex items-center justify-end gap-2">
    <button class="mr-auto" onclick={back}>Back</button>
    <button class="primary" onclick={next}>Continue</button>
  </div>
</div>
