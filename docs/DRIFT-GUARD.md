# Drift guard — how we stay aligned

Purpose: prevent spec/plan/code/docs from drifting as parallel work lands.

## Single sources of truth

- Product direction: `docs/ROADMAP.md`
- v0.2 design: `docs/superpowers/specs/2026-10-11-v0.2-native-core-design.md`
  + `docs/RUST-POLICY.md` amendment (Rust-only runtime)
- Task breakdown: `docs/superpowers/plans/2026-10-11-v0.2-plan-1-foundation.md`
  (Plans 2-6 follow the same checkbox style)
- Progress: `CHANGELOG.md` + `STACK-TRACKING.md` on stack branches

## Checks (run before every PR merge)

1. Scope: does this PR map to exactly one plan Task? If not, split it.
2. Spec match: protocol v2 (`v:2`, 1 MiB cap, deny_unknown_fields, Ed25519 on
   result frames), scheduler (eligible -> expected_ms -> 2-choices -> bounded
   queue -> breaker -> single retry), ledger invariants (micro-OCC, append-only,
   idempotent, cap min(measured,reserved) disclosed).
3. Rust-only: no new Python runtime code, no new npm runtime deps beyond web embed.
4. Metrics honesty: unknown stays null/Unavailable, never estimated. Receipts
   carry provenance.
5. Privacy: no prompts/outputs in DB, logs, TimescaleDB, or Sentry (run scrub tests).
6. Docs: README + ARCHITECTURE + config table + `.env.example` + CLI help updated
   in the same PR if behavior changed. Plan checkbox ticked in the same branch.
7. Config surface: a new directive needs its parser case, its validation rule,
   a fixture in `crates/ocm-config/tests/fixtures/`, and a row in the directive
   reference. `ocm -t` must accept `examples/ocm.conf` at all times.

## Staleness sweep (weekly or pre-release)

- `README`, `docs/ARCHITECTURE.md`, `docs/ROADMAP.md`, CLI `--help`,
  `.env.example` match code; no removed component still referenced.
- `CHANGELOG.md` has an entry per merged feature.
- `docs/ROADMAP.md` Outcomes flipped from planned to measured with numbers.

## Stack discipline

- One branch per Task, stacked: `stack/01-frame` -> `02-bodies` -> `03-sign` ->
  `04-fuzz` -> `05-score` (-> `06-pick` -> `07-breaker`).
- PR targets parent, not main. Merge bottom-up only when CI green + checklist ticked.
- Never force-push a parent after a child is based on it; rebase children forward.
