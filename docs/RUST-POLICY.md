# Rust-only runtime policy (approved)

Rust is the only backend/systems language. Python/Node exist only as
build-time tools, never as runtime.

## What this means

- Runtime: `ocm-coord`, `ocm-node`, `ocm-proto`, `ocm-sched`, `ocm-runtime`,
  `ocm-ledger` — all Rust. No `ocm/*.py` at runtime after v0.2.
- Python agent + coordinator are v0.1 legacy. Deleted when the Rust e2e suite
  passes every v0.1 behaviour (spec acceptance #9).
- Integrations in Rust: TimescaleDB via `tokio-postgres` (feature `tiger`),
  Sentry via `sentry` crate with the same scrubbers, Ollama/Gemini/OpenAI-compat
  via `reqwest` + rustls. No `psycopg`, no `sentry-sdk`, no `httpx` at runtime.
- CLI via `clap` in Rust (`ocm serve`, `ocm join-code`, `ocm check-ledger`).
- Desktop via Tauri 2 (Rust core). No Python sidecar.
- Scripts: `cargo xtask` for mesh tasks. Keep `scripts/demo_tunnel.sh` etc.
  only for demo ergonomics until replaced.

## What stays non-Rust

- Web UI stays React + Vite + TypeScript, but embedded into the `ocm-coord`
  binary (already the v0.2 plan). No separate Node server in production.
- CI YAML stays, but every gate is cargo-based plus `npm ci/build` for the embed.
- Mobile (v0.3) uses native shells + embedded Rust core, not Python.

## Why not rewrite web in Rust/WASM

Cost (weeks) outweighs benefit (zero mesh gain). Leptos/Dioxus would lose
Biome/Playwright maturity. Revisit only after v0.2 ships. Rust serves and
guards the UI — that satisfies the policy.

## Enforcement

- New runtime deps must be Rust, `default-features = false` where possible,
  justified in `docs/DEPENDENCIES.md`.
- Staleness check fails CI if `ocm/*.py`, `tests/test_*.py`, `pyproject.toml`
  runtime deps, or Python `ocm-agent` references remain after the cutover.
- See `docs/DRIFT-GUARD.md` for the check procedure.
