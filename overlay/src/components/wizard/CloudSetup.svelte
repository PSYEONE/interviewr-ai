<script lang="ts">
  import type { ConfigView } from "../../lib/types";

  let {
    complete,
    back,
    current = null,
  }: {
    complete: (delta: Record<string, unknown>) => void;
    back: () => void;
    current?: ConfigView | null;
  } = $props();

  type Provider = "anthropic" | "openai" | "gemini" | "groq";

  const PROVIDERS: { id: Provider; label: string; defaultModel: string; keyUrl: string }[] = [
    { id: "anthropic", label: "Anthropic (Claude)", defaultModel: "claude-opus-4-8", keyUrl: "console.anthropic.com" },
    { id: "openai", label: "OpenAI", defaultModel: "gpt-4o", keyUrl: "platform.openai.com" },
    { id: "gemini", label: "Google Gemini (free tier)", defaultModel: "gemini-2.5-flash", keyUrl: "aistudio.google.com" },
    { id: "groq", label: "Groq (free tier)", defaultModel: "llama-3.3-70b-versatile", keyUrl: "console.groq.com" },
  ];

  // The configured provider, when it's one of ours. Null in the wizard (nothing configured yet) and
  // when the engine is on a local provider, in which case this screen starts from its own defaults.
  function seedProvider(c: ConfigView | null): Provider | null {
    return PROVIDERS.find((x) => x.id === c?.llm_provider)?.id ?? null;
  }

  // The running model, but only when it belongs to `p`; otherwise that provider's default.
  function seedModel(c: ConfigView | null, p: Provider): string {
    if (c?.llm_provider === p && c.llm_model) return c.llm_model;
    return PROVIDERS.find((x) => x.id === p)?.defaultModel ?? "";
  }

  // svelte-ignore state_referenced_locally
  const seeded = seedProvider(current) ?? PROVIDERS[0].id;

  let provider = $state<Provider>(seeded);
  // svelte-ignore state_referenced_locally
  let model = $state<string>(seedModel(current, seeded));
  let apiKey = $state("");

  let active = $derived(PROVIDERS.find((p) => p.id === provider) ?? PROVIDERS[0]);

  // The backend reports only whether a key exists, never the key itself.
  function keySet(p: Provider): boolean {
    if (!current) return false;
    const flags: Record<Provider, boolean> = {
      anthropic: current.anthropic_api_key_set,
      openai: current.openai_api_key_set,
      gemini: current.gemini_api_key_set,
      groq: current.groq_api_key_set,
    };
    return flags[p];
  }

  let keySaved = $derived(keySet(provider));

  // A saved key means the field can stay blank, so switching model within a provider doesn't force
  // the user to dig their key back out. Nothing to apply if provider, model and key are all as-is.
  let changed = $derived(
    !current ||
      provider !== current.llm_provider ||
      model.trim() !== current.llm_model ||
      !!apiKey.trim(),
  );
  let canContinue = $derived(!!model.trim() && (keySaved || !!apiKey.trim()) && changed);

  function pick(p: Provider): void {
    provider = p;
    // Re-picking the provider that's running restores its model rather than the generic default.
    model = seedModel(current, p);
  }

  function finish(): void {
    const delta: Record<string, unknown> = {
      llm_provider: provider,
      llm_model: model.trim(),
    };
    // Only write a key when one was actually typed, so a blank field leaves .env untouched.
    const key = apiKey.trim();
    if (key) delta[`${provider}_api_key`] = key;
    complete(delta);
  }
</script>

<div class="wiz">
  <h1>Cloud model</h1>
  <p class="text-xs">Bring your own API key. It's stored locally in .env, never sent anywhere but the provider.</p>

  {#each PROVIDERS as p (p.id)}
    <label class="card flex cursor-pointer items-center gap-2" class:selected={provider === p.id}>
      <input type="radio" name="provider" checked={provider === p.id} onchange={() => pick(p.id)} />
      <span class="flex-1">{p.label}</span>
      {#if current?.llm_provider === p.id}<span class="badge">current</span>{/if}
      {#if keySet(p.id)}<span class="badge-muted">key saved</span>{/if}
      <span class="text-dim text-xs">{p.keyUrl}</span>
    </label>
  {/each}

  <div>
    <label for="model">Model</label>
    <input id="model" type="text" bind:value={model} />
  </div>
  <div>
    <label for="key">{active.label} API key</label>
    <input
      id="key"
      type="password"
      bind:value={apiKey}
      placeholder={keySaved ? "saved, leave blank to keep it" : "paste your key"}
    />
  </div>

  <div class="flex-1"></div>
  <div class="flex items-center justify-end gap-2">
    <button class="mr-auto" onclick={back}>Back</button>
    <button class="primary" disabled={!canContinue} onclick={finish}>Continue</button>
  </div>
</div>
