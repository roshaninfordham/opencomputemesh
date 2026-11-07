# OpenComputeMesh — Product Roadmap

Vision: anyone can pool compute from devices they already own into a mesh
that serves AI models, with every token measured and every credit accounted for.

North star: one person self-hosts with a single coordinator binary/container.
Others join from any major OS/device with one install, share what they choose,
run prompts from web or API, and trust receipts, costs and contributions —
among friends first, among strangers eventually.

Principles: explicit sharing only; Rust-only runtime (see docs/RUST-POLICY.md); no fake numbers; outbound-only nodes;
prompts are toxic (never stored/logged); one request = one node (no sharding);
credits are accounting, not money (until explicitly changed).

Personas: Maya (requester), Dev (contributor), Sam (host/operator),
Priya (app builder on /v1 API), Alex (stranger).

## Phase 0 — v0.1 Trusted small group [DONE]

Stories delivered:
- As Maya, I join with link+nickname, pick a model, stream tokens, open a
  receipt with token counts, timings and payment — so I trust the result.
- As Dev, I run one agent next to Ollama, approve exact models, see My Usage.
- As Sam, I run `ocm serve`, share a QR, see members/nodes/jobs, pause bad
  nodes, run `check-ledger` (debits == rewards).
- As Priya, I call `/v1/chat/completions` with API key, get usage + receipt.

Outcomes: real routed/streamed/settled prompt with runtime_reported tokens;
67 Python + 9 Rust tests; Tiger Data verified on TimescaleDB 2.30.2;
Gemini/Sentry built, not live-verified. Demo via Quick Tunnel in Chromium.

Limits: single coordinator process + SQLite; in-memory sockets/limits;
no node identity beyond credential hash; no reputation; simple scheduler
(fewest-active); desktop+Ollama only; no federation/mobile/container.

## Phase 1 — v0.2 Native core [IN PROGRESS]

Spec: docs/superpowers/specs/2026-10-11-v0.2-native-core-design.md
Plan 1: docs/superpowers/plans/2026-10-11-v0.2-plan-1-foundation.md
Streams: (a) proto+sched (b) runtimes (c) ledger+coord (d) node+discovery
(e) web+desktop (f) packaging/CI/docs.

### 1.1 Workspace, protocol v2, scheduler
- As Dev, I download one `ocm-node` binary, no Python/Node, and contribute
  in 2 minutes.
- As Sam, forged result frames are rejected by signature without logging content.
- As Maya, my prompt goes to the node expected to finish first, p95 stays low.

Enablers: Cargo workspace (ocm-proto, ocm-sched pure, no I/O); JSON v:2,
1 MiB cap, deny_unknown_fields; Ed25519-signed job_completed/job_failed;
EWMA + expected_ms + power-of-two-choices + breaker + bounded per-model
queue with RETRY_LATER.

Outcomes: fmt/clippy/deny/test green on 3 OSes; scheduler <50us at 10k nodes;
60s fuzz in CI; decode errors never echo payload; property tests pass.

### 1.2 Runtimes as adapters
- As Dev with no GPU, I share gemini-2.5-flash via --runtime gemini.
- As Dev with llama.cpp/vLLM, I point at any OpenAI-compatible loopback server.

Enablers: RuntimeAdapter {health, list_models, generate_stream}; ollama
(loopback only), openai_compat (loopback default), gemini (opt-in, key only
in x-goog-api-key). Provenance preserved; missing durations stay null.

Outcome: same receipt shapes for all runtimes; Gemini live smoke test.

### 1.3 Rust coordinator + ledger
- As Sam, I run one `ocm-coord` binary/container, restarts do not corrupt ledger.
- As Priya, all v0.1 endpoints still work, so no rewrite.

Enablers: ocm-ledger (SQLite micro-OCC, append-only, idempotent, cap
min(measured,reserved) disclosed); ocm-coord (tokio+axum+rustls);
TimescaleDB+Sentry behind features, off by default.

Outcomes: v0.1 behaviours ported to Rust e2e (2 nodes + fake runtime);
1000 concurrent streams; relay <1ms/token; container <30MB non-root /readyz.

### 1.4 Node daemon + LAN discovery
- As Dev+Maya on same WiFi, devices discover with zero config, no URL typed.

Enablers: ocm-node daemon, mDNS (mdns-sd) + federated coordinators,
0600 key storage, backoff+jitter reconnect.

Outcome: 2 LAN nodes discover + serve with no URL configured.

### 1.5 Web + desktop app
- As Dev, I install Tauri app, see live contribution, pause from tray.

Enablers: Tauri 2 shell + shared web UI; strict TS + Biome + Playwright.

Outcome: install/enroll/share/pause works macOS+Windows+Linux.

### 1.6 Packaging, CI, docs hygiene
- As Sam, I `docker compose up` with signed checksums.

Outcomes: tagged releases + installers + SHA-256; staleness check passes;
Python removed; hackathon framing removed (neutral adapters stay).

## Phase 2 — v0.3 Beyond one room [PLANNED]

- As Sam with 200 users, I run Postgres primary, not SQLite.
- As Maya, my request can be served by another coordinator's nodes
  (cross-network federation), so capacity is global.
- As Alex on iPhone/Android, I share via embedded llama.cpp (small quant
  models on Metal/Vulkan/CPU).
- As Sam, per-model concurrency/queue/cost policies hold under 10x burst.

Outcomes: 2 coordinators federate one request e2e in CI; p95 queue-wait
under bound in burst test; mobile shares <=3B Q4 and completes a routed job.

Non-goals: real money, E2E prompts, cross-machine sharding.

## Phase 3 — v0.4 Safe among strangers [PLANNED]

- As Sam hosting publicly, Sybils/floods are rate-limited/attested/throttled.
- As Maya, I see which trust level served me (verified digest + spot history).
- As Dev, reputation follows my node key; honest-slow hardware not penalised.
- As Priya, I get moderation hooks + per-key budgets for a public bot.

Enablers: reputation v2 + spot-check majority + hardware-aware tolerance;
Sybil cost (PoW/invite/stake); classifier adapters; confidential-compute
node class; key budgets + audit exports.

Outcomes: red-team suite (forged/inflated/altered) earns ~0, honest nodes
unaffected; flood drills recover legit p95 in <2min.

## Phase 4 — v1.0 Boring to run [PLANNED]

- As Sam, upgrade = one binary + migrate --check, backup = one command,
  dashboards show jobs/tokens/credits/throughput/reputation.
- As Dev, updates auto-install, approved models auto-patch, exports to CSV.
- As auditor, I replay any receipt to ledger + provenance.

Outcomes: fresh VPS to public mesh <10min from docs; 30-day soak with
0 ledger inconsistencies + 0 prompt leaks (canary suite).

## Metrics (every phase)

- Time-to-first-contribution (target v0.2: <5min desktop).
- Routing quality: % on fastest-eligible; p50/p95 queue-wait, TTFT, tok/s.
- Trust: spot pass rate, breaker opens, forged rejected, Sybil cost.
- Accounting: check-ledger pass, runtime_reported %, capped-settlement rate.
- Reliability: uptime, recovery time, TimescaleDB lag/dropped rows.
- Adoption: WAU contributors/requesters, models served, tokens settled.

## Risks

1. Single-operator trust until federation+reputation — keep invite-only till v0.4.
2. Mobile battery/thermals may limit phones to tiny models — prototype first.
3. Prompts cross borders; Gemini discloses to Google; OCC non-cash till counsel.
4. Free grants invite farms — need invite-graph or cost-to-join before open enrol.

## Rust-only rule

Backend/systems are Rust only. Python coordinator/agent are v0.1 legacy,
deleted at v0.2 cutover. Web stays React+TS but embedded in the Rust binary.
Details: `docs/RUST-POLICY.md`. Drift checks: `docs/DRIFT-GUARD.md`.

## Tracking in this branch

- This file is the human roadmap. Progress lives in:
  docs/superpowers/specs/ + docs/superpowers/plans/ + CHANGELOG.md
- Conventions: spec approved -> plan per stream -> tasks with - [ ] checkboxes
  -> task review + whole-branch review (see v0.2 Plan 1 header).
- Update cadence: check a box + commit per task; update Outcomes when measured.
