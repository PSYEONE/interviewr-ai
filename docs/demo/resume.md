# Jordan Avery

Backend engineer, Remote (EU) - jordan.avery@example.com - github.com/example

Backend engineer with eight years on Python services, most of it on systems where latency is
the product: real-time audio pipelines, streaming APIs and the observability needed to prove
they hold up. Comfortable owning a service from the schema to the on-call rota.

## Experience

### Senior Backend Engineer, Harbourline Systems (2022 - present)

- Rebuilt the live captioning pipeline around a streaming transcription model, cutting the
  median delay between speech and displayed text from 4.1s to 900ms.
- Replaced a polling REST endpoint with an authenticated WebSocket event bus, which let the
  mobile and web clients share one backend instead of maintaining two.
- Took the ingest service from 400 to 3,500 concurrent sessions on the same three nodes by
  moving audio decoding off the request path into a worker pool.
- Introduced structured logging and traces across six services; mean time to diagnose a
  production incident dropped from around two hours to under twenty minutes.
- Ran the migration from a single Postgres instance to a primary with read replicas, with no
  planned downtime and a tested rollback.

### Backend Engineer, Quayside Analytics (2019 - 2022)

- Built the data export service used by every enterprise customer, handling CSV, Parquet and
  streamed JSON, with resumable downloads for multi-gigabyte extracts.
- Owned the job scheduler: Celery to start with, then a custom asyncio runner once per-task
  cancellation and progress reporting became requirements the queue could not meet.
- Cut the nightly aggregation window from 6 hours to 40 minutes by rewriting the hot query
  path and adding the partial indexes the planner was missing.
- Mentored two juniors through their first on-call rotations and wrote the runbooks they used.

### Software Engineer, Tessellate Media (2017 - 2019)

- Maintained a Django monolith serving 2M monthly users, with a focus on the media upload and
  transcoding path.
- Added an ffmpeg-based transcoding tier that removed a per-minute SaaS bill of about
  4,000 EUR a month.

## Technical

**Languages:** Python (expert), SQL, Rust (working), Bash, TypeScript
**Backend:** FastAPI, Django, asyncio, WebSockets, Celery, gRPC
**Data:** PostgreSQL, Redis, SQLite, Parquet, vector search with pgvector
**Audio and ML:** PipeWire, ffmpeg, whisper.cpp, Ollama, retrieval over embeddings
**Infrastructure:** Linux, Docker, Kubernetes, Terraform, GitHub Actions, Prometheus, Grafana
**Practice:** typed Python under mypy strict, property-based testing, trunk-based development

## Education

BSc Computer Science, University of Example (2017)

## Projects

**opencaption** - open-source live captioning overlay for Linux, 1.2k stars. Captures system
audio through PipeWire and transcribes on the GPU through Vulkan, so it runs on AMD and NVIDIA
without a vendor-specific toolkit.

**pgqueue** - a small Postgres-backed job queue for teams who do not want to run Redis.
