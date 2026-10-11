# Sponsor integrations

OpenComputeMesh uses three sponsor technologies from AI Engineer NYC 2026 (MLH): the Gemini API, Tiger Data (TimescaleDB) and Sentry. Each one is optional, off by default, and sits behind an interface the project already had, so turning it off leaves the rest of the system unchanged. This page explains each integration, shows the real code, and states exactly what was verified.

| Sponsor | Where | Enable | Verified |
|---|---|---|---|
| Gemini API | `ocm/runtime.py` (`GeminiAdapter`), `ocm/agent.py` | `ocm-agent --runtime gemini` with `GEMINI_API_KEY` | 12 unit tests with a mocked HTTP transport. Not run against the live API (no key). |
| Tiger Data | `ocm/timeseries.py`, `ocm/app.py`, `web/src/pages.tsx` | `uv sync --extra tiger`, `OCM_TIMESCALE_URL` | 4 tests against a real local TimescaleDB 2.30.2 (PostgreSQL 17). Not run against Tiger Cloud. |
| Sentry | `ocm/observability.py`, `ocm/cli.py` | `uv sync --extra sentry`, `SENTRY_DSN` | 5 tests, one through the real `sentry-sdk` with an in-memory transport. Not delivered to a live project (no DSN). |

Test counts are from `uv run pytest -q` on 2026-10-11: 67 passed, 0 skipped, with both extras installed, Ollama running and TimescaleDB on `127.0.0.1:55432`.

## Gemini API

### What it does

A node owner can share Gemini models with the group by running the Python agent with `--runtime gemini`. To the coordinator a Gemini node looks like any other node: it advertises models, receives `job.start`, streams `job.token` frames and finishes with `job.completed` and `final_stats`. Routing, receipts and credit settlement are unchanged.

### Why it fits

The coordinator never sees runtime-specific JSON. Agents talk to runtimes through the `RuntimeAdapter` protocol in `ocm/runtime.py` (`health`, `list_models`, `generate_stream`) and report Ollama-shaped `final_stats`. Gemini is a second implementation of that protocol. No routing, ledger, database or wire-protocol code changed to add it. It also lets someone without a GPU contribute capacity to the group.

### Request path

```mermaid
sequenceDiagram
  participant R as Requester (browser or /v1)
  participant C as Coordinator
  participant A as ocm-agent --runtime gemini
  participant G as Gemini API
  R->>C: prompt for gemini-2.5-flash
  C->>C: reserve credits, pick an eligible node
  C->>A: job.start (messages, max_tokens, temperature, top_p)
  A->>A: gemini_body(): system to systemInstruction, assistant to model
  A->>G: POST /v1beta/models/{model}:streamGenerateContent?alt=sse<br/>header x-goog-api-key
  loop server-sent events
    G-->>A: data: {candidates[].content.parts[], usageMetadata}
    A->>A: SSEDecoder, skip parts with thought=true
    A->>C: job.token (text, seq)
    C-->>R: request.token
  end
  A->>A: gemini_final_stats(usageMetadata, finishReason)
  A->>C: job.completed final_stats {prompt_eval_count, eval_count, thoughts_count, done_reason}
  C->>C: normalize, settle min(measured, reserved), store done_reason
  C-->>R: request.completed + receipt
```

### Code

Message conversion (`ocm/runtime.py`, `gemini_body`):

```python
system = [m["content"] for m in req.messages if m.get("role") == "system"]
contents = [{"role": "model" if m.get("role") == "assistant" else "user", "parts": [{"text": m["content"]}]}
            for m in req.messages if m.get("role") != "system"]
config = {"maxOutputTokens": req.max_tokens}
```

Usage mapping (`gemini_final_stats`). Only what the API reports is passed on:

```python
if "promptTokenCount" in usage:
    stats["prompt_eval_count"] = usage["promptTokenCount"]
if "candidatesTokenCount" in usage:
    stats["eval_count"] = usage["candidatesTokenCount"]
if "thoughtsTokenCount" in usage:
    stats["thoughts_count"] = usage["thoughtsTokenCount"]
if finish:
    stats["done_reason"] = GEMINI_FINISH.get(finish, finish.lower())
```

`GEMINI_FINISH = {"STOP": "stop", "MAX_TOKENS": "length"}`. Other reasons are lowercased (for example `SAFETY` becomes `safety`). The coordinator stores `done_reason` on the job and shows it on the receipt. `/v1/chat/completions` returns `finish_reason: "length"` when `done_reason` is `length` and `"stop"` otherwise.

### What the receipt shows for a Gemini job

- Input and output tokens from `usageMetadata`, with `tokens.source = runtime_reported` when both are present. The job is charged only in that case, like any other job.
- `worker_elapsed` and `time_to_first_token` are measured by the agent and the coordinator.
- `runtime_total`, `runtime_load`, `prompt_eval`, `generation` and tokens per second are `null`, because Gemini reports no durations. They are not estimated.
- Thinking tokens are kept out of `eval_count`, so they are not charged. The agent sends them as `thoughts_count`, but the coordinator does not currently store them.

### Key handling and errors

- The key is read from `GEMINI_API_KEY` or `GOOGLE_API_KEY` with `os.environ.pop`, so child processes never inherit it.
- It is set once as the `x-goog-api-key` header on the `httpx.AsyncClient`. It is never put in a URL, a log line or a coordinator frame.
- HTTP errors are reported as `runtime HTTP <status>: <API error message>` (truncated to 200 characters), never with the prompt. A `promptFeedback.blockReason` fails the job as a refused prompt. A stream that ends without a `finishReason` fails the job instead of settling partial output.
- Cancelling closes the HTTP stream.
- At startup the agent prints that prompts sent to this node are forwarded to Google's Gemini API.

### Enable

```bash
GEMINI_API_KEY=<key> uv run ocm-agent --runtime gemini --models gemini-2.5-flash \
  --coordinator http://127.0.0.1:8000 --name gemini-node --state-dir ~/.ocm-gemini
```

No extra dependency is needed (it uses `httpx`). `OCM_RUNTIME=gemini` is the same as `--runtime gemini`. `--models` must name models the API lists for your key with `generateContent` support, with the `models/` prefix removed. Use a separate `--state-dir` if the same machine also runs an Ollama node. The Rust agent (`ocm-node`) supports Ollama only.

### Verification

`tests/test_gemini.py` (12 tests) replaces the network with an `httpx.MockTransport` that returns the documented response shapes. It checks:

- message conversion, including system instructions and the `assistant` to `model` role
- an SSE stream fed one character at a time (including a multi-line `data:` payload and a malformed event, which is counted and skipped)
- streamed text and usage with the response delivered in 1-, 7- and 10,000-byte reads; thinking tokens reported apart from `eval_count`; no durations invented; the request path, `alt=sse` and `systemInstruction`
- `MAX_TOKENS` to `length` and other reasons lowercased
- an API error returns the API's message and not the prompt
- a stream without a finish reason fails
- cancellation closes the stream
- model listing keeps only `generateContent` models and strips `models/`, health, and the key travels only in the header (never as a `key` query parameter)
- `--runtime gemini` without a key exits with a clear message, and with a key it skips the Ollama loopback check, announces the cloud runtime, never prints the key, and removes it from the environment

**Status:** not run against the live Gemini API, because no API key was available. The request format follows Google's published REST documentation; a live run is the remaining check.

## Tiger Data (TimescaleDB)

### What it does

When `OCM_TIMESCALE_URL` is set, the coordinator copies one metrics row for every terminal job (completed, failed, cancelled or expired) into a TimescaleDB hypertable. A continuous aggregate rolls those rows up per minute, network and model. `GET /api/v1/network/throughput` reads the aggregate and the Overview page draws it as "Throughput (last hour)".

### Why it fits

Job metrics are append-only, time-stamped and read by time window. That is what hypertables (time-partitioned chunks), continuous aggregates (incrementally maintained rollups) and native compression are for. SQLite stays the source of truth for jobs and the ledger. TimescaleDB holds a derived copy that is safe to lose, so it can be best effort and never slow down a job.

### Data path

```mermaid
flowchart LR
  J["Job reaches a terminal state<br/>(ocm/hub.py)"] -->|"Sink.record(job)<br/>never awaits"| BUF["In-memory deque<br/>max 10,000 rows"]
  BUF -->|"background writer<br/>batches of 500<br/>ON CONFLICT DO NOTHING"| HT[("hypertable<br/>ocm_job_metrics<br/>1-day chunks,<br/>compressed after 7 days")]
  HT -->|"refresh policy every minute<br/>+ real-time aggregation"| CA[("continuous aggregate<br/>ocm_job_metrics_1m")]
  CA -->|"SELECT ... WHERE network_id<br/>AND bucket >= now() - minutes"| EP["GET /api/v1/network/throughput"]
  EP --> UI["Overview page<br/>Throughput (last hour)"]
```

### Code

The hypertable and compression (`ocm/timeseries.py`, `SCHEMA`):

```sql
SELECT create_hypertable('ocm_job_metrics', by_range('time', INTERVAL '1 day'), if_not_exists => TRUE)
SELECT add_compression_policy('ocm_job_metrics', INTERVAL '7 days', if_not_exists => TRUE)
```

Compression is segmented by `model_id` and ordered by `time DESC`. It is enabled only if it is not already on, because changing compression settings after chunks are compressed is an error.

The continuous aggregate (abridged):

```sql
CREATE MATERIALIZED VIEW IF NOT EXISTS ocm_job_metrics_1m
WITH (timescaledb.continuous, timescaledb.materialized_only = false) AS
SELECT time_bucket(INTERVAL '1 minute', time) AS bucket, network_id, model_id,
       count(*) AS jobs, count(*) FILTER (WHERE status = 'COMPLETED') AS completed,
       sum(output_tokens) FILTER (WHERE token_source = 'runtime_reported') AS output_tokens,
       percentile_cont(0.95) WITHIN GROUP (ORDER BY ttft_ms) AS p95_ttft_ms, ...
FROM ocm_job_metrics GROUP BY bucket, network_id, model_id WITH NO DATA
```

Token sums count only exact runtime-reported usage. A refresh policy (`start_offset` 1 day, `end_offset` 1 minute, every minute) keeps it materialized, and `materialized_only = false` makes a job finished seconds ago visible before the next refresh.

Schema creation runs one statement at a time in autocommit (continuous aggregate DDL refuses transactions) and under `pg_advisory_lock(hashtext('ocm_job_metrics'))`, because `IF NOT EXISTS` is not race-safe when two coordinators start against the same database.

### Guarantees

- **Never blocks a job.** `record()` only appends to a deque. The background writer owns the connection (5-second connect and read timeouts) and retries with exponential backoff from 1 to 30 seconds.
- **Idempotent writes.** Rows are keyed on `(job_id, time)`, where `time` is the job's `completed_at`, so a retried batch inserts nothing twice.
- **Never stores prompts.** The `COLUMNS` map is the only data that leaves the coordinator: ids, model, node, status, failure code, token counts and source, latencies, tokens per second and settled micro-OCC. No column can hold a prompt or output, and a test asserts this against `information_schema`.
- **Never logs secrets.** Write failures log only the exception type and the buffer size, never the URL (which holds the password) or row content.
- **Never fakes data.** If the sink is configured but unreachable, the endpoint returns `503 TIMESERIES_UNAVAILABLE`. If it is not configured, it returns `{"enabled": false}` and the panel is hidden.
- **Bounded loss.** The buffer holds at most 10,000 rows. On a long outage the oldest rows are dropped and counted (`dropped_rows` in the endpoint response), and a restart loses what is buffered. The code marks this as a known ceiling.

### Enable

```bash
uv sync --extra tiger                     # installs psycopg[binary]
OCM_TIMESCALE_URL=postgresql://<user>:<password>@<host>:<port>/<db> uv run ocm serve
```

The target database needs the `timescaledb` extension. Tiger Cloud services and the `timescale/timescaledb` Docker image provide it. The coordinator creates the tables, policies and aggregate itself. For a local setup see [DEMO_RUNBOOK.md](DEMO_RUNBOOK.md#optional-sponsor-integrations).

### Verification

`tests/test_timeseries.py` (4 tests) runs against a real TimescaleDB, by default `postgresql://postgres:ocmlocal@127.0.0.1:55432/ocm` (override with `OCM_TEST_TIMESCALE_URL`), in its own `ocm_test` schema so it never touches a live coordinator's rows. It starts a real coordinator with fake WebSocket nodes and checks that:

1. running the schema twice is a no-op;
2. a completed job and a failed job land in the hypertable with the exact runtime-reported values; the continuous aggregate returns them before any refresh; the endpoint returns the same buckets; no column can hold prompt text; `minutes=0` is rejected;
3. with a "black hole" Postgres that accepts TCP but never answers, a job still completes in under 2 seconds (the connect timeout is 5), the row stays buffered for retry, and the endpoint answers 503;
4. with no URL the endpoint answers `{"enabled": false}`.

**Status:** verified live on 2026-10-11 against TimescaleDB 2.30.2 on PostgreSQL 17.11 (`timescale/timescaledb:latest-pg17` on `127.0.0.1:55432`). Not yet run against a hosted Tiger Cloud service. CI installs no extras and has no TimescaleDB, so these tests skip there.

## Sentry

### What it does

When `SENTRY_DSN` is set, `ocm serve` initializes the Sentry SDK with the Starlette and FastAPI integrations. Unhandled coordinator errors are reported with release `opencomputemesh@<package version>` and environment `OCM_ENV`. Performance tracing is off unless `SENTRY_TRACES_SAMPLE_RATE` is above 0. Node agents never initialize Sentry.

### Why it fits

A coordinator that crashes in front of an audience needs error monitoring. But it also handles prompts, join codes, session cookies, API keys, enrollment tokens and node credentials, and its own rule is never to log or persist prompts. The integration is built so that monitoring cannot become a leak.

### Event path

```mermaid
flowchart LR
  E["Unhandled exception, message,<br/>transaction or breadcrumb"] --> SDK["sentry_sdk<br/>send_default_pii=False<br/>include_local_variables=False<br/>max_request_body_size=never"]
  SDK --> H{"before_send /<br/>before_send_transaction /<br/>before_breadcrumb"}
  H --> W["_walk(): drop request body, cookies,<br/>frame vars; blank query strings;<br/>redact sensitive headers"]
  W --> R["scrub_str(): /join/[redacted],<br/>ticket= token= key= redacted,<br/>ocm_ / ocme_ / ocmn_ and 32+ char runs redacted"]
  R --> S["Sentry project"]
```

### Code

Initialization (`ocm/observability.py`, `init_sentry`):

```python
sentry_sdk.init(
    dsn=dsn,
    send_default_pii=False,
    include_local_variables=False,
    max_request_body_size="never",
    traces_sample_rate=float(os.environ.get("SENTRY_TRACES_SAMPLE_RATE", "0") or 0),
    ...
    before_send=scrub_event,
    before_send_transaction=scrub_event,
    before_breadcrumb=scrub_breadcrumb,
)
```

The string scrubber:

```python
_SECRET = re.compile(r"\bocm[en]?_[A-Za-z0-9_\-]+|(?<![A-Za-z0-9_\-])[A-Za-z0-9_\-]{32,}(?![A-Za-z0-9_\-])")
_JOIN = re.compile(r"(/join/)[^/?#\s\"']+")
_QS_SECRET = re.compile(r"(?i)\b(ticket|token|code|key|api_key|secret)=[^&\s\"']+")
```

The scrubbers are pure functions, applied recursively to every string, list, tuple and dict in an event. `init_sentry()` is a no-op that returns `False` when `SENTRY_DSN` is empty or `sentry-sdk` is not installed.

### Enable

```bash
uv sync --extra sentry                    # installs sentry-sdk
SENTRY_DSN=https://<key>@<org>.ingest.sentry.io/<project> uv run ocm serve
# optional: SENTRY_TRACES_SAMPLE_RATE=0.1
```

With Tiger Data as well, run `uv sync --extra tiger --extra sentry`. `uv sync` removes extras you do not name.

### Verification

`tests/test_observability.py` (5 tests) uses a prompt, a join code, a session cookie, a WebSocket ticket, an API key, an enrollment token, a node credential and a 42-character secret as canaries:

- an exception event with a request body, cookies, headers, frame variables, log entry and tags: no canary survives, `User-Agent` is kept, and the request body, cookies, frame vars and query string are gone;
- a breadcrumb with a ticket URL and join path;
- a transaction named `/join/<code>`;
- `init_sentry()` without a DSN returns `False`;
- end to end: the real SDK is initialized with an in-memory transport, a breadcrumb, a tag, an exception (with the prompt in a local variable) and a message are captured, and the serialized envelopes contain no canary.

**Status:** not delivered to a live Sentry project, because no DSN was available. Everything up to the transport is exercised by the real SDK.

**Known side effect:** the 32-character rule also matches UUIDs, so job and node IDs appear as `[redacted]` in Sentry events.
