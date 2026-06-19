<script lang="ts">
  let {
    complete,
    back,
  }: { complete: (delta: Record<string, unknown>) => void; back: () => void } = $props();

  type Provider = "anthropic" | "openai" | "gemini" | "groq";

  const PROVIDERS: { id: Provider; label: string; defaultModel: string; keyUrl: string }[] = [
    { id: "anthropic", label: "Anthropic (Claude)", defaultModel: "claude-opus-4-8", keyUrl: "console.anthropic.com" },
    { id: "openai", label: "OpenAI", defaultModel: "gpt-4o", keyUrl: "platform.openai.com" },
    { id: "gemini", label: "Google Gemini (free tier)", defaultModel: "gemini-2.5-flash", keyUrl: "aistudio.google.com" },
    { id: "groq", label: "Groq (free tier)", defaultModel: "llama-3.3-70b-versatile", keyUrl: "console.groq.com" },
  ];

  let provider = $state<Provider>("anthropic");
  let model = $state<string>(PROVIDERS[0].defaultModel);
  let apiKey = $state("");

  let current = $derived(PROVIDERS.find((p) => p.id === provider) ?? PROVIDERS[0]);
  let canContinue = $derived(!!model.trim() && !!apiKey.trim());

  function pick(p: Provider): void {
    provider = p;
    model = PROVIDERS.find((x) => x.id === p)?.defaultModel ?? "";
  }

  function finish(): void {
    complete({
      llm_provider: provider,
      llm_model: model.trim(),
      [`${provider}_api_key`]: apiKey.trim(),
    });
  }
</script>

<div class="wiz">
  <h1>Cloud model</h1>
  <p class="text-xs">Bring your own API key. It's stored locally in .env, never sent anywhere but the provider.</p>

  {#each PROVIDERS as p (p.id)}
    <label class="card flex cursor-pointer items-center gap-2" class:selected={provider === p.id}>
      <input type="radio" name="provider" checked={provider === p.id} onchange={() => pick(p.id)} />
      <span class="flex-1">{p.label}</span>
      <span class="text-dim text-xs">{p.keyUrl}</span>
    </label>
  {/each}

  <div>
    <label for="model">Model</label>
    <input id="model" type="text" bind:value={model} />
  </div>
  <div>
    <label for="key">{current.label} API key</label>
    <input id="key" type="password" bind:value={apiKey} placeholder="paste your key" />
  </div>

  <div class="flex-1"></div>
  <div class="flex items-center justify-end gap-2">
    <button class="mr-auto" onclick={back}>Back</button>
    <button class="primary" disabled={!canContinue} onclick={finish}>Continue</button>
  </div>
</div>
