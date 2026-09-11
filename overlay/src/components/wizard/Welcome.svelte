<script lang="ts">
  let { next }: { next: () => void } = $props();
</script>

<!-- Split shell so Get started sits where every later step's primary sits. No .wiz-head: the hero
     is a centred landing title and a ruled header bar under it would look wrong, so it stays in
     the body and only the footer is pinned. -->
<div class="wiz split">
  <div class="wiz-body gap-4">
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
  </div>

  <div class="wiz-foot">
    <button class="primary ml-auto" onclick={next}>Get started</button>
  </div>
</div>
