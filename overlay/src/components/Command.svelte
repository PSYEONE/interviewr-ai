<script lang="ts">
  // A command for the user to run: selectable text plus a Copy button that reports failures (WebKitGTK's clipboard is unreliable).
  let {
    command,
    note,
    warn = false,
  }: { command: string; note?: string; warn?: boolean } = $props();

  let copied = $state(false);
  let failed = $state(false);
  let timer: ReturnType<typeof setTimeout> | undefined;

  async function copy(): Promise<void> {
    clearTimeout(timer);
    try {
      await navigator.clipboard.writeText(command);
      copied = true;
      failed = false;
    } catch {
      copied = false;
      failed = true; // fall back to "select it yourself", which the CSS allows
    }
    timer = setTimeout(() => ((copied = false), (failed = false)), 2000);
  }
</script>

<!-- The note lives inside the row so the button centres against the whole block, not just the
     command line. `warn` marks a command the user must run to fix something; otherwise it is a
     plain section divider matching .wiz-head and .wiz-foot. -->
<div class="flex items-center gap-3 {warn ? 'card-warn' : 'border-t border-white/10 pt-3'}">
  <span class="min-w-0 flex-1">
    {#if note}
      <span class="mb-1 block text-[11px] {warn ? 'text-warn' : 'text-dim'}">{note}</span>
    {/if}
    <code class="cmd block">{command}</code>
  </span>
  <button class="shrink-0" onclick={copy}>
    {copied ? "Copied" : failed ? "Select it" : "Copy"}
  </button>
</div>
