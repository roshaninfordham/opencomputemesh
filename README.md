<p align="center">
  <img src="docs/images/banner.svg" alt="OpenComputeMesh: share compute, run models, see every token" width="100%">
</p>

<p align="center">
  <a href="https://github.com/roshaninfordham/opencomputemesh/actions/workflows/ci.yml"><img src="https://github.com/roshaninfordham/opencomputemesh/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache--2.0-blue.svg" alt="License: Apache-2.0"></a>
  <img src="https://img.shields.io/badge/python-3.12%2B-2457d6.svg" alt="Python 3.12+">
  <img src="https://img.shields.io/badge/rust-node%20agent-15201d.svg" alt="Rust node agent">
</p>

# OpenComputeMesh

<p align="center"><a href="https://www.youtube.com/watch?v=pevKYZLvLoE"><img src="docs/images/demo-preview.gif" alt="OpenComputeMesh demo preview: a prompt streams from a node, the receipt, the sponsor stack and what it saves. Click to watch the full 2-minute video." width="80%"></a><br><a href="https://www.youtube.com/watch?v=pevKYZLvLoE"><b>▶ Watch the 2-minute demo on YouTube</b></a>: real captures of the running software, how we used Gemini API, Tiger Data and Sentry, and what it saves.<br><a href="https://opencomputemesh.vercel.app"><b>▶ Watch the demo with the embedded player: https://opencomputemesh.vercel.app</b></a></p>

OpenComputeMesh v0.1 lets a small group pool the language models they already run locally with [Ollama](https://ollama.com). One person runs a coordinator. Others join it in a browser, and anyone who wants to contribute compute runs a small node agent next to their own Ollama (or, opted in, in front of the Gemini API). A prompt sent from the web app (or the OpenAI-compatible API) is routed to one online worker that has the exact model. The tokens stream back, and each request gets a receipt that shows runtime-reported token counts, timings, and the OpenCompute Credits (OCC) moved between requester and provider.

Opening the coordinator URL does not share anyone's compute. A browser that joins can only send requests. Compute is shared only when someone runs the node agent next to a runtime that has at least one model (for Ollama, a model already pulled), and approves which of those models to share. The agent connects out to the coordinator over a WebSocket and opens no inbound ports. With the default Ollama runtime it talks to Ollama on loopback only.

Built for AI Engineer NYC 2026 (MLH). Sponsor tracks entered: Best Overall, Best Use of Gemini API, Best Use of Tiger Data, Best Use of Sentry. Details: [Sponsor technologies](#sponsor-technologies), the deep dive in [docs/SPONSORS.md](docs/SPONSORS.md), and the [judging criteria map](#judging-criteria-map).

## Sponsor technologies

All three integrations are optional and off by default. With none of them enabled the coordinator, agents and tests behave exactly as before. Status is stated as it is, not as it might be.

| Sponsor | Status |
|---|---|
| Gemini API | Built. Unit-tested against the documented REST/SSE format with a mocked transport (`tests/test_gemini.py`, 12 tests). **Not run live**: no API key was available. |
| Tiger Data (TimescaleDB) | Built. **Verified live** against a real local TimescaleDB 2.30.2 on PostgreSQL 17 (Docker image `timescale/timescaledb:latest-pg17`), through the real coordinator (`tests/test_timeseries.py`, 4 tests). Not yet run against a hosted Tiger Cloud service. |
| Sentry | Built. Scrubbers unit-tested, and the full path tested through the real `sentry-sdk` with an in-memory transport (`tests/test_observability.py`, 5 tests). **Not delivered to a live Sentry project**: no DSN was available. |

### Gemini API: a cloud runtime behind the same adapter as Ollama

- **What it does.** A node owner can run `ocm-agent --runtime gemini` and share Gemini models with the group. The coordinator routes, meters and settles those jobs exactly like Ollama jobs. Receipts carry Gemini's own `usageMetadata` token counts.
- **Why it fits.** The coordinator only sees normalized shapes from the `RuntimeAdapter` protocol in `ocm/runtime.py`. Gemini is a second implementation of that protocol, so no routing, ledger or wire-protocol code changed. It lets a member with no local GPU contribute capacity.
- **How it works.** `GeminiAdapter` calls `POST /v1beta/models/{model}:streamGenerateContent?alt=sse`, decodes server-sent events (`SSEDecoder`), streams text parts (skipping `thought` parts), and maps `usageMetadata.promptTokenCount` / `candidatesTokenCount` to the receipt's input and output tokens. `finishReason` `STOP` becomes `stop`, `MAX_TOKENS` becomes `length`, anything else is lowercased (for example `safety`). That value is stored as `done_reason`, and `/v1/chat/completions` reports `finish_reason: "length"` for `length` and `"stop"` otherwise. Gemini reports no durations, so runtime timings and tokens per second stay empty rather than estimated. `thoughtsTokenCount` is sent as `thoughts_count` in `final_stats`, kept out of the output count, and not currently stored or charged by the coordinator.
- **Key handling.** The key comes from `GEMINI_API_KEY` (or `GOOGLE_API_KEY`), is removed from the agent's environment once read, and travels only in the `x-goog-api-key` header: never in a URL, log line or coordinator frame (`tests/test_gemini.py` asserts all three).
- **Files.** `ocm/runtime.py` (`SSEDecoder`, `gemini_body`, `gemini_final_stats`, `GeminiAdapter`), `ocm/agent.py` (`--runtime`, `OCM_RUNTIME`), `tests/test_gemini.py`.
- **Enable.** No extra dependency (uses `httpx`). `GEMINI_API_KEY=... uv run ocm-agent --runtime gemini --models gemini-2.5-flash --coordinator http://127.0.0.1:8000`. The model ID must match one the API lists for your key (with the `models/` prefix removed); `gemini-2.5-flash` is the ID used in the tests, not one verified live.
- **Trade-off.** A Gemini node sends the prompts routed to it to Google. The agent prints this at startup. The Rust agent supports Ollama only.

### Tiger Data: job metrics as a TimescaleDB hypertable

- **What it does.** When `OCM_TIMESCALE_URL` is set, every terminal job (completed, failed, cancelled, expired) is copied as one metrics row into the hypertable `ocm_job_metrics`. A continuous aggregate `ocm_job_metrics_1m` rolls it up per minute and model. `GET /api/v1/network/throughput` reads the aggregate, and the web Overview page draws it as the "Throughput (last hour)" panel.
- **Why it fits.** Job metrics are append-only, time-stamped and queried by time window: the workload hypertables, continuous aggregates and compression are built for. SQLite stays the source of truth for jobs and the ledger; TimescaleDB holds a derived copy for time-series queries.
- **How it works.** `ocm/timeseries.py` creates the schema idempotently under a Postgres advisory lock (safe when two coordinators start at once): a 1-day-chunk hypertable, compression segmented by `model_id` with a 7-day compression policy, and a 1-minute continuous aggregate with `materialized_only = false` (real-time aggregation, so fresh rows show before the next refresh) and a refresh policy every minute. The job path never waits on Postgres: `record()` appends to a bounded in-memory buffer (10,000 rows) and a background writer inserts in batches of 500, with `ON CONFLICT DO NOTHING` so retries are idempotent and exponential backoff up to 30 s on failure. Only metric columns are written; there is no column that can hold a prompt or output.
- **Files.** `ocm/timeseries.py`, `ocm/config.py` (`timescale_url`), `ocm/hub.py` (`Sink.record` on every terminal job), `ocm/app.py` (`/api/v1/network/throughput`), `web/src/pages.tsx` and `web/src/components.tsx` (`ThroughputChart`), `tests/test_timeseries.py`.
- **Enable.** `uv sync --extra tiger` (installs `psycopg[binary]`), then `OCM_TIMESCALE_URL=postgresql://user:pass@host:port/db uv run ocm serve`. The database must already have the `timescaledb` extension (Tiger Cloud services and the TimescaleDB Docker image do). See the [runbook](docs/DEMO_RUNBOOK.md#optional-sponsor-integrations).
- **Verified.** `tests/test_timeseries.py` runs against a real TimescaleDB (default `postgresql://postgres:ocmlocal@127.0.0.1:55432/ocm`, override with `OCM_TEST_TIMESCALE_URL`) and checks: the schema is idempotent; completed and failed jobs land in the hypertable with exact runtime-reported values; the continuous aggregate returns them before any refresh; the endpoint returns them; a Postgres that accepts TCP but never answers neither fails nor delays a job, and the endpoint answers `503 TIMESERIES_UNAVAILABLE` instead of inventing data.

### Sentry: opt-in error monitoring that cannot leak prompts

- **What it does.** When `SENTRY_DSN` is set, `ocm serve` initializes the Sentry SDK with the FastAPI and Starlette integrations, so unhandled coordinator errors are reported with release `opencomputemesh@<version>` and environment `OCM_ENV`.
- **Why it fits.** The coordinator handles prompts, join codes, session cookies, API keys, enrollment tokens and node credentials. Error monitoring is only acceptable if none of those can leave the machine, so the integration is built around scrubbing.
- **How it works.** `init_sentry()` sets `send_default_pii=False`, `include_local_variables=False`, `max_request_body_size="never"` and `traces_sample_rate` from `SENTRY_TRACES_SAMPLE_RATE` (default 0, so no performance traces). `before_send`, `before_send_transaction` and `before_breadcrumb` run pure scrubbers that drop request bodies, cookies, query strings and stack-frame variables; redact `Authorization`, `Cookie`, `Set-Cookie` and `X-Api-Key` headers; strip query strings from URLs; replace `/join/<code>` with `/join/[redacted]`; and redact `ocm_`/`ocme_`/`ocmn_` tokens and any run of 32 or more token characters.
- **Files.** `ocm/observability.py`, `ocm/cli.py` (called only for `ocm serve`), `tests/test_observability.py`.
- **Enable.** `uv sync --extra sentry` (installs `sentry-sdk`), then `SENTRY_DSN=https://...ingest.sentry.io/... uv run ocm serve`. To enable Tiger Data and Sentry together use `uv sync --extra tiger --extra sentry`; `uv sync` removes extras that are not named.
- **Verified.** Unit tests feed events, transactions and breadcrumbs holding a prompt, a join code, a session cookie, a WebSocket ticket, an API key, an enrollment token, a node credential and a long secret, and assert none survive. `test_end_to_end_real_sdk` initializes the real SDK with an in-memory transport, captures an exception and a message, and asserts the same on the serialized envelopes.
- **Trade-off.** The 32-character rule also redacts UUIDs, so job and node IDs appear as `[redacted]` in Sentry events.

## What it looks like

These screenshots come from real runs against `qwen2.5:1.5b` on Ollama 0.17.7. Every number is measured.

| A receipt after a run | Local node vs shared network | Phone |
|---|---|---|
| <img src="docs/images/run-receipt.png" alt="Run page showing streamed output, a latency waterfall, runtime-reported token counts and the credit transfer" width="420"> | <img src="docs/images/local-vs-mesh.png" alt="Comparison of one run on the requester's own node and one on another worker with the same model digest" width="420"> | <img src="docs/images/mobile-run.png" alt="The run page at phone width" width="180"> |

The comparison shown ran both nodes on the same Mac, so it demonstrates the mechanics, not a hardware difference.

## Architecture at a glance

<p align="center"><img src="docs/images/architecture.svg" alt="Architecture: browsers and API clients talk to one coordinator; node agents dial out and run models through pluggable runtime adapters; SQLite ledger, Tiger Data metrics and Sentry monitoring" width="100%"></p>

Each layer is a slot with one interface. The box borders show what is built and verified, what is built but needs credentials to verify live, and what is only an extension point.

| Layer | Default | Also built | Status |
|---|---|---|---|
| Model runtime | Ollama on `127.0.0.1` (`ocm/runtime.py` `OllamaAdapter`) | Gemini API (`GeminiAdapter`, `ocm-agent --runtime gemini`) | Ollama verified end to end. Gemini is unit-tested against the documented API format; a live run needs `GEMINI_API_KEY`. |
| Node agent | Python `ocm-agent` | Rust `ocm-node` (single binary) | Both verified against the coordinator, including over a public `wss://` tunnel. |
| Jobs and ledger | SQLite in WAL mode | n/a (Postgres is an extension point) | Verified. |
| Time-series metrics | Off | Tiger Data / TimescaleDB sink (`ocm/timeseries.py`, `OCM_TIMESCALE_URL`): hypertable `ocm_job_metrics`, continuous aggregate `ocm_job_metrics_1m`, compression policy, live throughput chart | Verified against TimescaleDB 2.30 locally; not yet against a hosted Tiger Cloud service. |
| Error monitoring | Logs | Sentry (`ocm/observability.py`, `SENTRY_DSN`), with scrubbers that drop prompts, outputs, cookies, tokens and join codes | Scrubbing verified through the real SDK with an in-memory transport; live delivery needs a DSN. |

Snowflake and Nimble are not integrated.

## Components

| Part | Code | What it does |
|---|---|---|
| Coordinator | `ocm/app.py`, `ocm/hub.py`, `ocm/ledger.py`, `ocm/db.py`, `ocm/config.py`, `ocm/cli.py` | FastAPI app (one process). Serves REST, WebSockets, the OpenAI-compatible subset and the built web app. Stores everything durable in SQLite (WAL mode). |
| Node agent | `ocm/agent.py`, `ocm/runtime.py` (CLI `ocm-agent`) | Outbound-only WebSocket client. Enrolls with a one-time token, then runs jobs against local Ollama (default) or the Gemini API (`--runtime gemini`) and streams tokens back. |
| Metrics sink (optional) | `ocm/timeseries.py` | Copies terminal-job metrics to Tiger Data / TimescaleDB when `OCM_TIMESCALE_URL` is set. |
| Error monitoring (optional) | `ocm/observability.py` | Sentry with privacy scrubbers when `SENTRY_DSN` is set. |
| Web app | `web/` (React + Vite) | Built to `web/dist` and served by the coordinator from the same origin. Join, run a model, view receipts, nodes, usage, the ledger and (when enabled) the throughput chart. |
| Rust agent (alternative) | `crates/ocm-node` | A Rust node agent, binary `ocm-node`, with the same Ollama flags, environment variables and `<state-dir>/node.json` state file as `ocm-agent`. It supports the Ollama runtime only (no `--runtime`). It pins the rustls `ring` crypto provider at startup, which fixed a panic on the first `wss://` handshake. Build with `cargo build --release --manifest-path crates/ocm-node/Cargo.toml`. Covered by its own `cargo test` suite (9 tests) and verified end to end against the coordinator, including over a public `wss://` tunnel. |

## Quick start

Requirements: Python 3.12 or newer (the repo pins 3.13), [uv](https://docs.astral.sh/uv/), Node.js with npm, and Ollama on every machine that will share compute.

From a clean checkout:

```bash
uv sync                      # Python dependencies (including dev group)
scripts/build_web.sh         # npm ci + npm run build in web/ -> web/dist
uv run ocm serve             # coordinator on http://127.0.0.1:8000
```

`ocm serve` prints `Join path: /join/<code>`. You can print the code again at any time with:

```bash
uv run ocm join-code
```

Open `http://127.0.0.1:8000/join/<code>` in a browser and pick a nickname. In demo mode each new member gets a 100 OCC grant.

To share compute from the same machine:

```bash
ollama serve                 # if it is not already running
ollama pull qwen2.5:1.5b     # any model you choose; qwen2.5:1.5b is the recommended demo model
```

In the web app go to Nodes, open "Share my compute" and create a one-time enrollment token. Then:

```bash
uv run ocm-agent --coordinator http://127.0.0.1:8000 --models qwen2.5:1.5b --name my-laptop
```

The agent asks for the token with a hidden prompt. For non-interactive runs set `OCM_ENROLLMENT_TOKEN` in the environment instead. After enrollment the agent saves a node credential to `~/.opencomputemesh/node.json` (mode 0600). Later runs reuse it and need no token. Use `--state-dir DIR` to keep a separate credential per node, for example when simulating two nodes on one machine.

Other agent flags: `--runtime` (`ollama` or `gemini`, default from `OCM_RUNTIME`, else `ollama`), `--ollama` (default `http://127.0.0.1:11434`, loopback only), `--max-jobs` (default 1), `--heartbeat` (seconds, default 20). `--coordinator` can also come from `OCM_COORDINATOR` or from the saved state.

Optional sponsor integrations (each is independent; see [Sponsor technologies](#sponsor-technologies)):

```bash
uv sync --extra tiger --extra sentry          # psycopg and sentry-sdk
OCM_TIMESCALE_URL=postgresql://postgres:ocmlocal@127.0.0.1:55432/ocm \
SENTRY_DSN=https://<key>@<org>.ingest.sentry.io/<project> \
  uv run ocm serve                            # coordinator with Tiger Data metrics and Sentry
GEMINI_API_KEY=<key> uv run ocm-agent --runtime gemini --models gemini-2.5-flash \
  --coordinator http://127.0.0.1:8000 --name gemini-node --state-dir ~/.ocm-gemini
```

To let people on other networks join, publish the coordinator through a tunnel. See [docs/DEMO_RUNBOOK.md](docs/DEMO_RUNBOOK.md) and `scripts/demo_tunnel.sh`.

Make targets wrap these steps: `make install`, `make web`, `make serve`, `make dev`, `make test`, `make preflight`, `make tunnel`, `make check-ledger`, `make rust-agent`.

For frontend work, `scripts/dev.sh` runs the coordinator on port 8000 and the Vite dev server on port 5173. Vite proxies `/api`, `/v1`, `/healthz` and `/ws` to the coordinator, and `http://localhost:5173` and `http://127.0.0.1:5173` are in the default `OCM_ALLOWED_ORIGINS`.

## Architecture

```mermaid
flowchart LR
  B[Browser web app<br/>phone or laptop] -->|HTTPS + /ws/client| C
  API[API client] -->|/v1/chat/completions| C
  C[Coordinator<br/>FastAPI, one process] --> DB[(SQLite WAL<br/>jobs + append-only ledger)]
  C -->|every finished job| TS[(Tiger Data / TimescaleDB<br/>hypertable ocm_job_metrics)]
  TS --> CA[continuous aggregate<br/>ocm_job_metrics_1m] --> CH[/api/v1/network/throughput<br/>live chart in the app/]
  C -->|errors, after scrubbing<br/>prompts and secrets| SE[Sentry]
  PA[Python node agent<br/>ocm-agent] -->|outbound wss /ws/node| C
  RA[Rust node agent<br/>ocm-node] -->|outbound wss /ws/node| C
  PA --> AD{{RuntimeAdapter}}
  RA --> OL
  AD -->|127.0.0.1 only| OL[Ollama<br/>local models]
  AD -->|streamGenerateContent<br/>key stays on node| GE[Gemini API]
```

The database is the source of truth for durable state. TimescaleDB, when enabled, holds a derived copy of job metrics only. Live sockets, in-flight token streams and prompts exist only in the coordinator's memory, so exactly one coordinator process must run (uvicorn is started with a single worker). The job state machine, wire messages, scheduling, and retry, timeout and cancel rules are in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## How metrics are defined

Every number on a receipt comes from a named source. When a value is not reported it stays empty (`null`). The coordinator never estimates it.

Token counts come from Ollama's final stream chunk: `prompt_eval_count` (input) and `eval_count` (output). Each receipt carries `tokens.source`:

- `runtime_reported`: both counts were present.
- `partial_runtime_reported`: only one was present.
- `unavailable`: neither was present.

Only `runtime_reported` jobs are charged (see accounting below). The input estimate used for the credit hold is never shown as a token count.

For Gemini nodes the adapter maps `usageMetadata.promptTokenCount` to `prompt_eval_count` and `candidatesTokenCount` to `eval_count`, so the same rules apply. Gemini reports no durations: `runtime_total`, `runtime_load`, `prompt_eval`, `generation` and tokens per second stay `null`, while `worker_elapsed` and `time_to_first_token` are still measured. Thinking tokens (`thoughtsTokenCount`) are not added to the output count and are not charged.

Ollama reports durations in nanoseconds. `ocm/metrics.py` converts them to milliseconds with `round(ns / 1_000_000)`:

| Receipt field (`timings_ms`) | Source | Measured by |
|---|---|---|
| `runtime_total` | `total_duration` | Ollama |
| `runtime_load` | `load_duration` | Ollama |
| `prompt_eval` | `prompt_eval_duration` | Ollama |
| `generation` | `eval_duration` | Ollama |
| `worker_elapsed` | Monotonic clock from the agent's `POST /api/chat` to the final chunk | Node agent |
| `time_to_first_token` | From the coordinator sending `job.start` to the first non-empty token reaching the coordinator | Coordinator |
| `queue_wait` | From the job entering `QUEUED` to dispatch (last attempt, if retried) | Coordinator |
| `coordinator_end_to_end` | From the coordinator starting the job to its terminal state | Coordinator |

Tokens per second is `eval_count / eval_duration * 1e9`, rounded to two decimals. It is runtime generation speed only, with load, prompt evaluation and network time excluded. It is `null` if either value is missing.

The web app also measures browser-side times with `performance.now()`: end-to-end from sending the request to receiving `request.completed`, and time to the first token received. It shows these next to the coordinator numbers, labelled as seen by this browser. They include the network path between the browser and the coordinator, so they are always larger than the coordinator's times and are not stored.

Per-job CPU time and GPU active time are not measured. Nodes report hardware inventory only (CPU model, logical CPU count, memory, GPUs from `nvidia-smi` or `system_profiler`, labelled with their source).

## Accounting

OpenCompute Credits (OCC) are non-cash demo accounting units. All amounts are stored as integer micro-OCC (1 OCC = 1,000,000 micro-OCC) in an append-only `ledger` table. SQLite triggers reject `UPDATE` and `DELETE` on it, and corrections are compensating `reversal` entries.

- **Grants.** With `OCM_DEMO_MODE=true` (the default), each new member gets `OCM_DEMO_GRANT_OCC` (default 100) as a `demo_grant` entry. With demo mode off, members get no grant.
- **Tariff.** `OCM_DEFAULT_INPUT_OCC_PER_1K=1` and `OCM_DEFAULT_OUTPUT_OCC_PER_1K=3` are demo policy defaults, not market prices. On startup the coordinator records a tariff version `t-<in>-<out>-<min>`. Each job is priced at the tariff version that was active when it was created.
- **Cost.** `cost = max(ceil((input_tokens * input_micro_per_1k + output_tokens * output_micro_per_1k) / 1000), OCM_MIN_JOB_CHARGE_MICRO_OCC)` (`ledger.cost_micro`).
- **Reservation.** When a job is accepted, a worst-case hold is written as a negative `reservation` entry: `cost_micro(tariff, estimate_input_tokens(messages), max_tokens)`. Here `estimate_input_tokens = sum(ceil(len(content) / 2) + 16 for each message) + 64`, a deliberately high estimate of about 2 characters per token (the +64 covers chat-template and model default system-prompt tokens), and `max_tokens` is the request value or `OCM_DEFAULT_MAX_OUTPUT_TOKENS`. If the balance is below the hold, the job fails with `INSUFFICIENT_CREDITS`. Example: the prompt "hi" with 512 output tokens holds 81 x 0.001 + 512 x 0.003 = 1.617 OCC.
- **Settlement.** When a job completes with `runtime_reported` usage, one transaction releases the hold (`reservation_release`), debits the requester the measured cost (`job_debit`), and credits the node owner the same amount (`provider_reward`). Usage is reported by the node, so the charge is capped at the reservation: `min(cost, reserved)`. A requester is never charged more than the hold they agreed to. If the input estimate were ever too low, the provider would be underpaid; the job then records the uncapped `measured_credits` and `settlement_capped = 1`, and the receipt (`credits.capped`, `credits.measured_micro_occ`) and the `credit_settled` event show both amounts. Every entry has an idempotency key, so settlement can't happen twice. If a member's request runs on their own node, the debit and reward cancel out.
- **Unsettled.** If a job completes without both token counts, the hold is released, nobody is charged or paid, and the job is marked `unsettled_missing_usage`.
- **Failures and cancels.** Failed, cancelled and expired jobs release the hold and charge nothing.
- **Check.** `uv run ocm check-ledger` verifies three things: every job's debit and reward cancel out, no terminal job still holds a reservation, and total supply equals grants plus reversals plus currently held reservations. It exits non-zero if any check fails.

## Security model

- **Loopback-only runtime.** `ocm-agent` and `ocm-node` refuse an `--ollama` URL whose host is not `127.0.0.1`, `localhost` or `[::1]`. The URL is parsed (`httpx.URL` in Python), not string-matched, so `http://127.0.0.1@evil.com` is rejected, and the Ollama client ignores `HTTP(S)_PROXY` (`trust_env=False`). The agent opens no listening sockets.
- **Cloud runtime is explicit.** `--runtime gemini` is the one deliberate exception to loopback-only: prompts routed to that node go to Google's Gemini API, and the agent says so at startup. The API key is read from `GEMINI_API_KEY` or `GOOGLE_API_KEY`, removed from the process environment, and sent only in the `x-goog-api-key` header.
- **Optional telemetry.** The TimescaleDB sink writes metric columns only (no prompt or output column exists) and never logs its URL. Sentry is off without `SENTRY_DSN` and scrubs every event, transaction and breadcrumb before sending (see [Sentry](#sentry-opt-in-error-monitoring-that-cannot-leak-prompts)). It connects out to `/ws/node` (`wss://` when the coordinator URL is `https://`).
- **Explicit model approval.** A node shares only the installed models named with `--models` (or chosen at the interactive prompt). Jobs for any other model are refused with `MODEL_NOT_SHARED`. The agent never pulls models.
- **Enrollment tokens.** Tokens are created per member from the web app. They are single-use (claimed with a conditional `UPDATE ... WHERE used_at IS NULL`), expire after `OCM_ENROLLMENT_TOKEN_TTL_SECONDS` (default 600), and are limited to 5 per member per 10 minutes. The agent reads them from a hidden prompt or `OCM_ENROLLMENT_TOKEN`, never from a command-line argument.
- **Hashed credentials.** The database stores only SHA-256 hashes of the join code, session tokens, API keys, enrollment tokens and node credentials. Sessions, API keys and enrollment tokens are looked up by hash. The join code, node credentials and the admin token are compared with `hmac.compare_digest`. A revoked node's credential hash is overwritten, and its socket is closed with code 4003.
- **Plaintext on disk.** A generated join code is also kept in plaintext in `$OCM_DATA_DIR/join_code` (mode 0600), so `ocm join-code` can print it. The agent keeps its node credential in plaintext in `<state-dir>/node.json` (mode 0600).
- **Sessions.** Browser sessions use an `HttpOnly`, `SameSite=Lax` cookie. The cookie is marked `Secure` when the request scheme is `https`. Behind a tunnel this works because uvicorn runs with `proxy_headers=True` and `forwarded_allow_ips="127.0.0.1"`, so it trusts `X-Forwarded-Proto` only from a local proxy.
- **Origin and CSRF check.** Cookie-authenticated writes, `POST /api/v1/session`, and `/ws/client` handshakes must carry an `Origin` that is in `OCM_ALLOWED_ORIGINS` or matches the request's `Host` or `X-Forwarded-Host`. Otherwise REST returns `403 BAD_ORIGIN` and WebSocket handshakes are closed with code 4403. Browser WebSockets also need a single-use ticket from `POST /api/v1/ws-ticket`, valid for 60 seconds.
- **Security headers.** Every HTTP response carries `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, `X-Frame-Options: DENY` and a restrictive `Content-Security-Policy` (`default-src 'self'`, `connect-src 'self'`, `frame-ancestors 'none'`).
- **Rate limits** (in-process sliding windows):
  - joins: 10 per minute per client IP. `CF-Connecting-IP` is trusted only when the peer is loopback.
  - requests: 20 per minute per member, shared by REST, WebSocket and `/v1/chat/completions`.
  - benchmarks: 5 per 5 minutes per member.
  - node WebSocket connects: 30 per minute per IP.
  - node messages: 2000 per 10 seconds per node. Excess messages are dropped.
- **Job limits:**
  - at most `OCM_MAX_ACTIVE_JOBS_PER_MEMBER` active jobs per member
  - at most `OCM_MAX_QUEUED_JOBS` queued jobs network-wide
  - total prompt length capped at `OCM_MAX_PROMPT_CHARS`
  - output tokens capped at `OCM_MAX_OUTPUT_TOKENS`
- **No prompt storage or logging.** The coordinator does not write prompts or outputs to the database or its logs. They exist in memory while a job runs and are returned to the requester. `job_events` records only event types, durations and safe metadata (node id, failure code, amounts).
- **Node isolation.** A node can report events only for jobs dispatched to it. Other reports are ignored.
- **Admin.** Admin routes need `Authorization: Bearer $OCM_ADMIN_TOKEN` and are disabled when the token is empty. Outside `OCM_ENV=development` the coordinator refuses to start if the admin token is set and shorter than 32 characters.

Trust assumptions: everyone trusts the coordinator operator, who controls the database, the ledger and all routing. Requesters trust that a node runs the model it advertises and reports honest token counts. The coordinator routes on the model name (and, for benchmarks, the digest) that the node reports, but it cannot verify what actually ran. Node owners accept that prompts routed to them are visible on their machine.

## API

All JSON errors use the envelope `{"error": {"code", "message", "request_id", "details?"}}`.

Health:

| Method | Path | Notes |
|---|---|---|
| GET | `/healthz` | Liveness, `{"status": "ok"}` |
| GET | `/readyz` | Database reachable; returns `online_nodes` and `time` |

Session and network (cookie auth unless noted):

| Method | Path | Notes |
|---|---|---|
| POST | `/api/v1/session` | Join with `{nickname, join_code}`; sets the session cookie. Origin required. |
| GET | `/api/v1/session` | Current member or `null` |
| POST | `/api/v1/session/leave` | Ends the session |
| POST | `/api/v1/session/api-key` | Creates or replaces the member's API key (shown once) |
| GET | `/api/v1/network` | Network snapshot. Without a session, only counts and model names. |
| GET | `/api/v1/network/throughput?minutes=` | Per-minute, per-model job counts, token sums, average and p95 TTFT and end-to-end latency, and average tokens per second from the TimescaleDB continuous aggregate. `minutes` 1 to 1440, default 60. Returns `{"enabled": false}` when `OCM_TIMESCALE_URL` is unset, and `503 TIMESERIES_UNAVAILABLE` when it is set but unreachable. Includes `buffered_rows` and `dropped_rows`. |
| GET | `/api/v1/network/join-info` | Join code and path |
| POST | `/api/v1/ws-ticket` | 60-second single-use ticket for `/ws/client` |

Nodes:

| Method | Path | Notes |
|---|---|---|
| POST | `/api/v1/nodes/enrollment-tokens` | One-time enrollment token |
| GET | `/api/v1/nodes` | All nodes in the network |
| GET | `/api/v1/nodes/{id}` | Node plus job statistics |
| POST | `/api/v1/nodes/{id}/pause` | Owner only |
| POST | `/api/v1/nodes/{id}/resume` | Owner only |
| DELETE | `/api/v1/nodes/{id}` | Revoke (owner only) |
| POST | `/api/v1/nodes/{id}/refresh-models` | Asks the connected agent to resend its model inventory |

Models and jobs:

| Method | Path | Notes |
|---|---|---|
| GET | `/api/v1/models` | Models served by online, sharing nodes |
| POST | `/api/v1/jobs` | Blocking request: `{model, messages, max_tokens?, temperature?, top_p?, idempotency_key, node_id?}`. Returns `{receipt, output}`. |
| GET | `/api/v1/jobs?limit=&offset=` | Receipts for jobs you requested or served |
| GET | `/api/v1/jobs/{id}` | Receipt plus event timeline |
| GET | `/api/v1/jobs/{id}/receipt` | Receipt |
| POST | `/api/v1/jobs/{id}/cancel` | Requester only |

Benchmarks:

| Method | Path | Notes |
|---|---|---|
| GET | `/api/v1/benchmarks/eligibility?model=` | Whether a local and remote comparison is possible |
| POST | `/api/v1/benchmarks` | Runs the same prompt on your node and on another node with the same digest |
| GET | `/api/v1/benchmarks` | Your last 20 benchmarks |

Accounting:

| Method | Path | Notes |
|---|---|---|
| GET | `/api/v1/me/usage` | Consumed and contributed totals by model |
| GET | `/api/v1/me/ledger?limit=&offset=` | Balance, totals by entry type, entries, tariff |

Admin (`Authorization: Bearer $OCM_ADMIN_TOKEN`):

| Method | Path | Notes |
|---|---|---|
| GET | `/api/v1/admin/metrics` | Job and node counts, ledger consistency report |
| POST | `/api/v1/admin/pricing` | Adds a sourced hosted-API price reference (see below) |
| POST | `/api/v1/admin/invites` | Returns the join path |

OpenAI-compatible (non-streaming subset). Authenticate with `Authorization: Bearer <api key>`; create the key in Settings.

| Method | Path | Notes |
|---|---|---|
| GET | `/v1/models` | Models with `worker_count` |
| POST | `/v1/chat/completions` | `model`, `messages`, `temperature`, `top_p`, `max_tokens` or `max_completion_tokens`. `stream: true` returns `400 STREAMING_UNSUPPORTED`. An optional `Idempotency-Key` header makes retries safe. The response includes `usage` (only when token counts are known) and the full receipt under `opencomputemesh`. |

```bash
curl -s http://127.0.0.1:8000/v1/chat/completions \
  -H "Authorization: Bearer $OCM_API_KEY" -H 'Content-Type: application/json' \
  -d '{"model":"qwen2.5:1.5b","messages":[{"role":"user","content":"Say hi in five words."}]}'
```

WebSockets:

| Path | Notes |
|---|---|
| `/ws/client?ticket=` | Browser stream of snapshots, request events and tokens |
| `/ws/node` | Node agents |

Message types are listed in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

Any other path serves the web app from `OCM_WEB_DIST`. If the web app is not built, it returns `503 WEB_NOT_BUILT`.

### Hosted API equivalent

Receipts show a hypothetical hosted-API cost only after an admin has recorded a sourced, dated price for that local model. Without one they show "Hosted API equivalent not configured".

```bash
curl -s -X POST http://127.0.0.1:8000/api/v1/admin/pricing \
  -H "Authorization: Bearer $OCM_ADMIN_TOKEN" -H 'Content-Type: application/json' \
  -d '{"local_model_id":"qwen2.5:1.5b","provider":"<provider>","provider_model_id":"<model>",
       "input_usd_per_million_tokens":"<decimal>","output_usd_per_million_tokens":"<decimal>",
       "source_url":"https://<pricing page>","source_checked_at":"<YYYY-MM-DD>"}'
```

## Configuration

Every field of `Settings` in `ocm/config.py` can be set with an environment variable `OCM_<FIELD>`. Booleans accept `1/true/yes/on`. `.env.example` lists them all. The coordinator does not load `.env` files itself; export the variables or source the file.

| Variable | Default | Meaning |
|---|---|---|
| `OCM_ENV` | `development` | Any other value enables production validation (admin token length) |
| `OCM_HOST` | `127.0.0.1` | Bind address |
| `OCM_PORT` | `8000` | Bind port |
| `OCM_DATABASE_PATH` | `./data/opencomputemesh.db` | SQLite file path (not a URL) |
| `OCM_DATA_DIR` | `./data` | Where the generated join code is stored |
| `OCM_ADMIN_TOKEN` | empty | Enables admin routes; must be 32 or more characters outside development |
| `OCM_JOIN_CODE` | empty | Fixed join code. If empty, one is generated once and saved to `$OCM_DATA_DIR/join_code`. |
| `OCM_NETWORK_NAME` | `OpenComputeMesh Demo` | Display name |
| `OCM_DEMO_MODE` | `true` | Grant credits on join |
| `OCM_DEMO_GRANT_OCC` | `100` | Grant per new member |
| `OCM_NODE_HEARTBEAT_TTL_SECONDS` | `60` | A node with no heartbeat for this long is ineligible and swept offline |
| `OCM_NODE_HEARTBEAT_INTERVAL_SECONDS` | `20` | Read by `ocm-agent` as its default `--heartbeat`; not used by the coordinator |
| `OCM_NODE_OFFLINE_GRACE_SECONDS` | `10` | Delay before a disconnected node is shown `OFFLINE` |
| `OCM_ENROLLMENT_TOKEN_TTL_SECONDS` | `600` | Enrollment token lifetime |
| `OCM_MAX_PROMPT_CHARS` | `12000` | Total characters across messages |
| `OCM_DEFAULT_MAX_OUTPUT_TOKENS` | `512` | Used when a request has no `max_tokens` |
| `OCM_MAX_OUTPUT_TOKENS` | `2048` | Upper bound for `max_tokens` |
| `OCM_JOB_TIMEOUT_SECONDS` | `180` | Deadline from dispatch to completion |
| `OCM_QUEUE_TIMEOUT_SECONDS` | `30` | How long a job waits for a free worker |
| `OCM_MAX_QUEUED_JOBS` | `32` | Network-wide queue limit |
| `OCM_MAX_ACTIVE_JOBS_PER_MEMBER` | `2` | Concurrent jobs per requester |
| `OCM_MAX_CONCURRENT_JOBS_PER_NODE` | `1` | Cap on the agent's `--max-jobs` |
| `OCM_DEFAULT_INPUT_OCC_PER_1K` | `1` | Demo tariff, whole OCC per 1k input tokens |
| `OCM_DEFAULT_OUTPUT_OCC_PER_1K` | `3` | Demo tariff, whole OCC per 1k output tokens |
| `OCM_MIN_JOB_CHARGE_MICRO_OCC` | `0` | Minimum charge per settled job |
| `OCM_ALLOWED_ORIGINS` | `http://localhost:5173,http://127.0.0.1:5173` | Extra origins accepted besides the request host |
| `OCM_WEB_DIST` | `./web/dist` | Built web app directory |
| `OCM_TIMESCALE_URL` | empty | Optional Tiger Data / TimescaleDB connection URL for the job-metrics sink. Empty disables it. Needs `uv sync --extra tiger`. |

Coordinator variables outside `Settings` (read by `init_sentry()` in `ocm/observability.py`, only under `ocm serve`; need `uv sync --extra sentry`):

| Variable | Default | Meaning |
|---|---|---|
| `SENTRY_DSN` | empty | Enables Sentry error monitoring. Empty disables it. |
| `SENTRY_TRACES_SAMPLE_RATE` | `0` | Fraction of requests sent as performance traces. `0` sends none. |

Agent-side variables:

| Variable | Default | Meaning |
|---|---|---|
| `OCM_COORDINATOR` | saved state | Coordinator URL (`--coordinator`) |
| `OCM_ENROLLMENT_TOKEN` | empty | One-time enrollment token for non-interactive runs |
| `OCM_AGENT_STATE_DIR` | `~/.opencomputemesh` | Where `node.json` is kept (`--state-dir`) |
| `OCM_RUNTIME` | `ollama` | `ollama` or `gemini` (`--runtime`). Python agent only. |
| `OLLAMA_BASE_URL` | `http://127.0.0.1:11434` | Ollama URL (`--ollama`); must be loopback |
| `GEMINI_API_KEY` (or `GOOGLE_API_KEY`) | empty | Required with `--runtime gemini`. Removed from the environment once read; sent only in the `x-goog-api-key` header. |
| `OCM_NODE_HEARTBEAT_INTERVAL_SECONDS` | `20` | Default `--heartbeat` |

## Testing

```bash
uv run pytest -q                                              # Python: coordinator, agents, sponsor integrations
cargo test --manifest-path crates/ocm-node/Cargo.toml         # Rust agent
```

Current counts (run on 2026-10-11 on the development Mac, with the `tiger` and `sentry` extras installed, Ollama 0.17.7 with `qwen2.5:1.5b`, and TimescaleDB 2.30.2 on `127.0.0.1:55432`): **67 passed, 0 skipped** in Python and **9 passed** in Rust.

| File | Tests | Covers |
|---|---|---|
| `tests/test_units.py` | 14 | NDJSON decoding, metric normalization, tariff rounding, ledger reserve, settle, release and reversal, append-only triggers, the state machine |
| `tests/test_integration.py` | 15 | A real coordinator with fake WebSocket nodes: routing, streaming and settlement, idempotent replay, worker errors, retry on another worker after a disconnect, timeout and cancel, insufficient credits, pause and authorization, rejection of reports on foreign jobs |
| `tests/test_agent.py` | 16 | Python node agent: loopback-only Ollama URL parsing (including userinfo tricks), enrollment-token reuse after a failed attempt, proxy settings ignored for runtime traffic |
| `tests/test_gemini.py` | 12 | Gemini adapter with a mocked transport: message conversion, SSE split mid-line, tokens, usage and thinking tokens, finish-reason mapping, API errors without the prompt, missing finish reason, cancellation, model listing, key only in the header, `--runtime gemini` requiring a key and announcing the cloud runtime |
| `tests/test_timeseries.py` | 4 | Tiger Data sink against a real TimescaleDB: idempotent schema, hypertable rows, continuous aggregate with real-time aggregation, throughput endpoint, outage that neither fails nor delays jobs, disabled endpoint |
| `tests/test_observability.py` | 5 | Sentry scrubbers on events, breadcrumbs and transactions, no-op without a DSN, end to end through the real SDK with an in-memory transport |
| `tests/test_real_ollama.py` | 1 | Streams from a local Ollama and checks the runtime counters |

Some tests skip themselves rather than pass when their dependency is missing: `test_timeseries.py` (no `psycopg` or no reachable TimescaleDB with the extension; set `OCM_TEST_TIMESCALE_URL`), the real-SDK Sentry test (no `sentry-sdk`), and `test_real_ollama.py` (no Ollama or no `OCM_SMOKE_MODEL`, default `qwen2.5:1.5b`). CI (`.github/workflows/ci.yml`) runs `uv sync --locked` without extras and has no Ollama or TimescaleDB, so those tests skip there. The live TimescaleDB verification was done locally.

`scripts/demo_preflight.sh` runs the checks a demo host needs (see the runbook).

What has been verified by hand:

- A real prompt was routed through the coordinator to an Ollama node. It returned runtime-reported token counts and a balanced ledger transfer.
- The full flow was run through a Cloudflare Quick Tunnel, and re-run with the current security headers (CSP included) in Chromium. The SPA, same-origin `wss://` streaming and the QR code all worked with no console errors. Safari has not been tested:
  - The `Secure` cookie was set via `X-Forwarded-Proto`.
  - Cross-origin POSTs were rejected.
  - Browser WebSocket streaming worked.
  - Agent enrollment over `wss://` worked, including the Rust agent after the rustls provider fix.

Not verified live: a Gemini node against the real Gemini API (no API key) and Sentry delivery to a real project (no DSN).

Known runtime quirk: with Ollama 0.17.7, `qwen3:4b` ignores `think: false` and emits its reasoning text in normal content. Use `qwen2.5:1.5b` for demos.

## Limitations

- **Single trust boundary.** There is one coordinator process and one SQLite file. Whoever runs the coordinator controls membership, routing and the ledger. There is no replication, no federation and no horizontal scaling. Rate limits and WebSocket tickets are in-process.
- **A URL alone shares no compute.** Joining in a browser only lets you send requests. Capacity exists only while someone runs the node agent with Ollama and approved models.
- **Two runtimes.** Ollama (loopback) and the Gemini API are implemented behind `RuntimeAdapter`. The Gemini adapter has not been run against the live API. A Gemini node sends prompts to Google, and the Rust agent supports Ollama only. llama.cpp, vLLM and MLX are not implemented.
- **One worker per request.** Each request runs entirely on one node. There is no model sharding, tensor or pipeline parallelism, or splitting a request across machines.
- **No per-job GPU or CPU telemetry.** The system records worker wall time and runtime-reported durations and tokens, not per-job CPU or GPU active time. Usage reports show these as `null`.
- **Token counts depend on the runtime.** They are whatever Ollama or Gemini reports, with their provenance recorded. Different runtimes or model templates count differently. Jobs without both counts are not charged.
- **OCC is not money.** Credits are non-cash demo accounting units. They cannot be redeemed, transferred out or exchanged, and the default tariff is demo policy, not a market price.
- **The hosted API equivalent is hypothetical.** It is shown only after an admin has added a sourced, dated price with `POST /api/v1/admin/pricing`. It is an illustrative comparison, not a quote or a saving.
- **Benchmarks are single runs.** A comparison runs one prompt once on your node and once on another node. It is offered only when both serve the same model digest. The result is illustrative and not statistically valid.
- **Node owners can see prompts.** A prompt is processed in plain text by the runtime on the node that serves it. The owner of that machine can observe it, and on a Gemini node it is also sent to Google. Don't send anything you would not show the node owners.
- **Time-series metrics are best effort.** The TimescaleDB copy is buffered in memory (10,000 rows). A long outage drops the oldest rows, and a coordinator restart loses what is buffered. SQLite remains authoritative. Verified on a local TimescaleDB 2.30.2, not on Tiger Cloud.
- **Sentry is unverified against a live project** and redacts UUIDs (job and node IDs) along with secrets. Gemini thinking tokens are reported by the agent but not stored or charged.
- **Quick Tunnels are temporary.** Cloudflare Quick Tunnel URLs are random, change on every restart, and are meant for demos, not production. When the URL changes, restart agents with the new `--coordinator`; they reuse their saved credential.

## License

Apache-2.0. See [LICENSE](LICENSE).
