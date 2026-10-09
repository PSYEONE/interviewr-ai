# Senior Backend Engineer, Real-Time Platform

**Meridian Audio Labs** - Remote within the EU - permanent, full time

Meridian Audio Labs builds accessibility tooling for live events: real-time captions,
translation and transcripts for conferences, lectures and broadcast. Around 40 people, 11 of
them engineers. Our customers are universities and conference organisers, so the work is judged
on whether a caption appears before the speaker has moved on.

## The role

You would own the services that sit between audio arriving and text reaching a viewer's screen.
That means the transcription pipeline, the event delivery layer that fans results out to
however many clients are watching, and the latency budget across both. It is a small team, so
the person in this role is expected to make architectural calls and then live with them on call.

## What you would be doing

- Designing and running the real-time transcription pipeline: ingest, segmentation, model
  inference, and delivery to clients.
- Keeping end-to-end latency under 1.5 seconds at the 95th percentile while session counts grow.
- Building out the event delivery layer, currently WebSockets, so a single session can be
  watched by one viewer or a thousand without a redesign.
- Choosing where inference runs. We use both self-hosted GPU models and third-party APIs, and
  the trade-off between cost, accuracy and latency is an ongoing decision rather than a
  settled one.
- Making the system debuggable: tracing a single utterance from microphone to screen is
  something we expect to be able to do in production.
- Sharing the on-call rotation, one week in five.

## What we are looking for

- Five or more years building backend services in Python, with real asyncio experience rather
  than just familiarity.
- You have worked on something where latency mattered and can talk about how you measured and
  reduced it.
- Solid PostgreSQL: schema design, query plans, and migrations that run against live traffic.
- Comfortable on Linux, with containers and infrastructure as code.
- You write tests for systems with timing and concurrency in them, and you have opinions about
  how to do that without flakiness.

## Nice to have

- Audio or media pipeline experience: PipeWire, GStreamer, ffmpeg, codecs, resampling.
- Speech-to-text in production, whether self-hosted (whisper, Kaldi) or via an API.
- Retrieval or embedding work, particularly anything latency-sensitive.
- Rust, which a couple of our hot paths are moving to.
- Accessibility work, or any background in the standards around it.

## How we work

Remote-first with two weeks of the year together in person. Trunk-based, reviewed PRs, deploys
whenever CI is green rather than on a schedule. No take-home exercise: the technical interview
is a conversation about a system you have built, followed by a pairing session on our codebase.

## Interview process

1. Intro call, 30 minutes, with the engineering manager.
2. Systems conversation, 60 minutes, about the real-time pipeline you would own.
3. Pairing session, 90 minutes, on a real issue from our backlog.
4. Final call with the founders, 30 minutes.
