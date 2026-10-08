<script lang="ts">
  import { onMount } from "svelte";

  import { FEEDBACK_URL, openSupportLink, support, supportLinks } from "../lib/support";

  // After-interview pop-up: donation links if it helped, a feedback link if it didn't.
  // `preview` opens it from Settings in a dev build without counting towards the lifetime cap.
  let { close, preview = false }: { close: () => void; preview?: boolean } = $props();

  type Step = "ask" | "helpful" | "unhelpful";
  let step = $state<Step>("ask");
  let dialog: HTMLDivElement;

  onMount(() => {
    if (!preview) support.markShown();
    dialog.focus();
  });

  function donated(): void {
    if (!preview) support.markSupported();
    close();
  }

  // Keeps Tab inside the dialog, and treats Escape as "Maybe later".
  function onKeydown(e: KeyboardEvent): void {
    if (e.key === "Escape") {
      e.preventDefault();
      close();
      return;
    }
    if (e.key !== "Tab") return;
    const buttons = [...dialog.querySelectorAll<HTMLButtonElement>("button:not(:disabled)")];
    if (buttons.length === 0) return;
    const first = buttons[0];
    const last = buttons[buttons.length - 1];
    const active = document.activeElement;
    if (e.shiftKey && (active === first || active === dialog)) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && active === last) {
      e.preventDefault();
      first.focus();
    }
  }
</script>

<!-- Clicking the dimmed backdrop does nothing; the answer buttons and Escape are the ways out. -->
<div class="backdrop">
  <div
    bind:this={dialog}
    class="dialog"
    role="dialog"
    aria-modal="true"
    aria-labelledby="support-title"
    tabindex="-1"
    onkeydown={onKeydown}
  >
    {#if step === "ask"}
      <h2 id="support-title">How did the interview go?</h2>
      <div class="actions">
        <button class="primary" onclick={() => (step = "helpful")}>Helpful</button>
        <button onclick={() => (step = "unhelpful")}>Not helpful</button>
      </div>
    {:else if step === "helpful"}
      <svg class="heart" viewBox="0 0 24 24" aria-hidden="true">
        <path
          d="M12 21.35l-1.45-1.32C5.4 15.36 2 12.28 2 8.5 2 5.42 4.42 3 7.5 3c1.74 0 3.41.81 4.5 2.09C13.09 3.81 14.76 3 16.5 3 19.58 3 22 5.42 22 8.5c0 3.78-3.4 6.86-8.55 11.54L12 21.35z"
        />
      </svg>
      <h2 id="support-title">Glad it helped!</h2>
      <p>
        interviewr is free, with no subscription or tracking. If you'd like to support it, a donation
        would be greatly appreciated &lt;3
      </p>
      <div class="actions">
        {#each supportLinks as link (link.url)}
          <button class="primary" onclick={() => openSupportLink(link.url)}>{link.label}</button>
        {/each}
      </div>
      <div class="actions">
        <button onclick={donated}>I've donated</button>
        <button onclick={close}>Maybe later</button>
      </div>
    {:else}
      <h2 id="support-title">Let us know what went wrong</h2>
      <div class="actions">
        <button class="primary" onclick={() => openSupportLink(FEEDBACK_URL)}>Suggest a fix on GitHub</button>
        <button onclick={close}>Close</button>
      </div>
    {/if}
  </div>
</div>

<style>
  .backdrop {
    position: fixed;
    inset: 0;
    z-index: 50;
    display: grid;
    place-items: center;
    padding: 1rem;
    background: rgba(0, 0, 0, 0.6);
    backdrop-filter: blur(4px);
  }
  .dialog {
    display: flex;
    flex-direction: column;
    gap: 0.9rem;
    width: min(26rem, 100%);
    padding: 1.5rem;
    border: 1px solid rgba(255, 255, 255, 0.1);
    border-radius: 0.75rem;
    background: var(--solid);
    color: var(--color-fg);
    text-align: center;
    outline: none;
  }
  h2 {
    font-size: 1.15rem;
    font-weight: 600;
  }
  p {
    color: var(--color-fg);
    font-size: 0.9rem;
  }
  .actions {
    display: flex;
    flex-wrap: wrap;
    justify-content: center;
    gap: 0.5rem;
  }
  .heart {
    width: 3.5rem;
    height: 3.5rem;
    margin: 0 auto;
    fill: var(--color-bad);
    animation: heart-pop 0.6s cubic-bezier(0.2, 1.6, 0.4, 1) both;
  }
  @keyframes heart-pop {
    0% {
      transform: scale(0);
      opacity: 0;
    }
    60% {
      transform: scale(1.2);
      opacity: 1;
    }
    100% {
      transform: scale(1);
    }
  }
  @media (prefers-reduced-motion: reduce) {
    .heart {
      animation: none;
    }
  }
</style>
