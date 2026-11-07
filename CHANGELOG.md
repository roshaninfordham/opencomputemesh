# Changelog

All notable changes are documented here. The format follows Keep a Changelog and the project uses
Semantic Versioning.

## [Unreleased]

### Changed
- Diagrams redrawn around role rather than vendor: colour now marks core, runtime, attested tier,
  memory and data, and each of SIE, Tinfoil and Cognee states what it is for, how it is reached,
  what was verified and what it cannot do. The banner drops "peer-to-peer" for what actually happens.
- Repository trimmed to what a reader needs: internal planning notes, stack tracking, the roadmap and
  the demo runbook are gone, along with the video build directory and the 4.9 MB preview GIF. The
  README (616 lines) and landing page now say the same things in a fraction of the space.

## [0.1.2] - 2026-11-07

### Added
- `ocm-agent --runtime openai`: one adapter for every server that speaks the OpenAI chat-completions
  protocol (SIE, vLLM, LM Studio, llama.cpp, TGI), sharing its models through the mesh unchanged. The
  loopback rule is identical to Ollama, `--allow-remote-runtime` covers a server on another machine, and a
  server that rejects `stream_options` is retried once without it. Token counts come from the server's
  `usage` block; durations stay empty because the protocol reports none.
- README install instructions for every role and OS (coordinator wheel plus web bundle, one
  `ocm-node` binary per platform, browser-only joining), a capabilities table that states what
  the software does not do, and a redrawn architecture diagram covering the attested tier,
  the opt-in remote runtime and the decision export.
- `docs/RELEASING.md` explains the two failures that stop a tag release: an existing local tag,
  and git prompting for a username with no credential helper.

### Changed
- `web/tsconfig.tsbuildinfo` is no longer tracked, so `npm run build` leaves the tree clean.
- Documentation test counts now match the release checkout (97 Python, 61 Rust) instead of the
  older 67/9 figures.

## [0.1.1] - 2026-11-07

First release with downloadable binaries for macOS (Apple silicon and Intel),
Windows x64 and Linux x64, a wheel for the coordinator and Python agent, and the
built web bundle. See `docs/RELEASING.md`.

### Added
- Rust workspace with strict quality gates.
- `ocm-proto`: protocol v2 frames with Ed25519 signing of result frames.
- `ocm-sched`: load manager with expected-completion scoring and power-of-two-choices.
- `ocm-config`: nginx-style declarative configuration language with full validation (`ocm -t` semantics).
- `ocm-config`: `backend tinfoil` pools with a mandatory attestation `repo`; a config that claims
  attestation without naming what it verifies against is rejected.
- Confidential tier (v0.3) spec: a coordinator that routes and bills inference it cannot read.
- `tools/tinfoil-probe`: reproduces enclave attestation verification and prints the measurement.
- `tools/tinfoil-gateway`: P1 attested gateway that pins one verified enclave and forwards opaque
  bodies, refusing any other origin and any unverified start.
- `tools/tinfoil-gateway`: container packaging (`Dockerfile`, `tinfoil-config.yml`, publish path) so
  the gateway can run as an attested Tinfoil Container and callers install nothing.
- `ocm-agent`: `--allow-remote-runtime` shares a runtime the owner hosts elsewhere, so a contributor
  can join without installing anything on the machine with the GPU. The node probes each shared  model before enrolling, and its receipts carry `runtime_scope` and `attested: false`.
- `ocm-node` matches it: `--allow-remote-runtime`, a startup probe per shared model, and `remote`
  plus `attested: false` on receipts.
- `ocm-agent`: reports NVIDIA compute capability and driver version alongside VRAM in the node
  inventory, so routing can prefer the hardware a model needs before dispatch, not during.
- `ocm export-decisions`: dumps terminal jobs as labelled JSONL rows — model, route reason, node and
  node runtime tier, status, failure code, tokens, timings. No prompt or output text exists in the
  rows, by construction. This is the dataset the house decision model trains on.

### Changed
- `tools/tinfoil-gateway`: a non-loopback bind now requires `GATEWAY_TOKEN` and the process exits
  without it; `--healthcheck` lets a distroless container probe itself.

## [0.1.0] - 2026-10-11

### Added
- Python coordinator and agent, Rust agent, React web app, credit ledger and optional integrations.
