<script lang="ts">
  import { onMount } from "svelte";

  import { getSettings, postSettings, reloadSettings } from "../../lib/api";
  import { applyAndRestart } from "../../lib/apply";

  let { back }: { back: () => void } = $props();

  // Every field here is prompt text the model sees. Only system_prompt is baked into the LLM
  // adapter at build time, so only it needs a restart; the style directives are read per answer
  // and take effect on a reload.
  type Field = "system_prompt" | "answer_style_script" | "answer_style_bullets";
  const FIELDS: { key: Field; title: string; blurb: string }[] = [
    { key: "system_prompt", title: "System prompt", blurb: "Who the model is, on every answer." },
    { key: "answer_style_script", title: "Script answers", blurb: "Shape of a Script answer." },
    { key: "answer_style_bullets", title: "Bullet answers", blurb: "Shape of a Bullets answer." },
  ];
  const CHEAP = new Set<Field>(["answer_style_script", "answer_style_bullets"]);

  type Stage = "edit" | "applying" | "error";
  let stage = $state<Stage>("edit");
  let form = $state<Record<Field, string> | null>(null);
  let original = $state<Record<Field, string> | null>(null);
  let status = $state("");
  let error = $state<string | null>(null);
  // Back discards, so it arms first (click again to confirm), like the model picker's Delete.
  let armedBack = $state(false);
  const dirty = $derived(
    form !== null && original !== null && FIELDS.some(({ key }) => form![key] !== original![key]),
  );

  function msg(e: unknown): string {
    return e instanceof Error ? e.message : String(e);
  }

  async function load(): Promise<void> {
    try {
      const cfg = await getSettings(); // resolved: override or built-in default
      const next: Record<Field, string> = {
        system_prompt: cfg.system_prompt,
        answer_style_script: cfg.answer_style_script,
        answer_style_bullets: cfg.answer_style_bullets,
      };
      form = next;
      original = { ...next };
    } catch (e) {
      error = msg(e);
      stage = "error";
    }
  }

  /** Persist `updates`, restarting only if something in them needs it. */
  async function apply(updates: Partial<Record<Field, string>>): Promise<boolean> {
    const changed = Object.keys(updates) as Field[];
    if (changed.length === 0) return true;
    stage = "applying";
    error = null;
    try {
      if (changed.some((k) => !CHEAP.has(k))) {
        const res = await applyAndRestart(updates, (m) => (status = m));
        if (res === "manual") {
          error = "Saved. The engine isn't app-managed here; restart it manually to apply.";
          stage = "error";
          return false;
        }
      } else {
        status = "Applying…";
        await postSettings(updates);
        await reloadSettings(); // the running session picks these up; no restart
      }
      return true;
    } catch (e) {
      error = msg(e);
      stage = "error";
      return false;
    }
  }

  async function save(): Promise<void> {
    if (!form || !original) return;
    const updates: Partial<Record<Field, string>> = {};
    for (const { key } of FIELDS) if (form[key] !== original[key]) updates[key] = form[key];
    if (Object.keys(updates).length === 0) {
      back(); // nothing changed
      return;
    }
    if (await apply(updates)) back();
  }

  // Saving "" is what restores the built-in: the backend resolves an empty override to its
  // default, so reloading afterwards refills the box with it.
  async function reset(key: Field): Promise<void> {
    if (await apply({ [key]: "" })) {
      await load();
      stage = "edit";
    }
  }

  /** Leave, but not while unsaved edits would go with us: the first click only arms. */
  function leave(): void {
    if (!dirty || armedBack) {
      back();
      return;
    }
    armedBack = true;
  }

  onMount(load);
</script>

{#snippet backLabel(text: string)}
  <!-- The longest label reserves the width in a shared grid cell, so arming swaps the text
       without resizing the button and shifting the footer. -->
  <span class="grid">
    <span class="invisible col-start-1 row-start-1">Discard?</span>
    <span class="col-start-1 row-start-1">{text}</span>
  </span>
{/snippet}

{#if stage === "applying"}
  <div class="splash">{status || "Applying…"}</div>
{:else}
  <div class="wiz split">
    <div class="wiz-head">
      <h1>Prompts</h1>
    </div>

    <div class="wiz-body">
      {#if form}
        {#each FIELDS as f (f.key)}
          <div class="flex items-center gap-2">
            <span class="flex-1">{f.title}</span>
            <span class="text-dim text-xs">{f.blurb}</span>
            <button onclick={() => void reset(f.key)}>Reset</button>
          </div>
          <textarea bind:value={form[f.key]} rows="8"></textarea>
        {/each}
      {:else}
        <p class="text-dim text-sm">Loading…</p>
      {/if}
    </div>

    {#if error}
      <div class="wiz-status"><p class="text-warn">{error}</p></div>
    {/if}

    <div class="wiz-foot">
      <button
        class:armed={armedBack && dirty}
        title={armedBack && dirty ? "Click again to leave; your edits will be lost" : "Back"}
        onclick={leave}
        onmouseleave={() => (armedBack = false)}
      >
        {@render backLabel(armedBack && dirty ? "Discard?" : "Back")}
      </button>
      <button class="primary ml-auto" onclick={() => void save()} disabled={!form}>Save</button>
    </div>
  </div>
{/if}
