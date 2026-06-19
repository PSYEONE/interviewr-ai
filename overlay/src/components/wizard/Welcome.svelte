<script lang="ts">
  let { next }: { next: () => void } = $props();
</script>

<div class="wiz">
  <h1 class="hero">interviewr</h1>
  <br>
  <p>A private, Linux-first copilot that listens, transcribes, and drafts answers on-device.</p>
  <p>Built for:</p>
  <ul>
    <li>live <strong>interview &amp; meeting prep</strong></li>
    <li>real-time captions</li>
    <li>privacy focused local model setup</li>
  </ul>
  <p>Lets check your hardware and set up a model.</p>
  <div class="flex-1"></div>
  <div class="flex items-center justify-end gap-2">
    <button class="primary" onclick={next}>Get started</button>
  </div>
</div>
