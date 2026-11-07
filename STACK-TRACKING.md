# Stack tracking — Plan 1 Tasks 1-5 (accountability)

Base: `docs/roadmap` (2e6e7a8). Verified locally on Rust 1.99.0 per `rust-toolchain.toml`
plus CI (`workspace` + `deny` + `fuzz` jobs).

## Stack (merge bottom-up, PR to parent)

| # | Branch | Task | Local gate |
|---|---|---|---|
| 1 | `stack/01-frame` | workspace, quality gates, frame envelope | PASS |
| 2 | `stack/02-bodies` | protocol v2 message bodies | PASS |
| 3 | `stack/03-sign` | Ed25519 keys + result-frame signatures | PASS |
| 4 | `stack/04-fuzz` | fuzz `decode_frame` in CI | PASS |
| 5 | `stack/05-score` | EWMA scoring + eligibility | PASS |
| - | `docs/rust-only` | Rust-only policy + drift guard | docs only |

## Gate evidence (local, Rust 1.99.0)

- `cargo fmt --all --check`: clean
- `cargo clippy --workspace --all-targets -- -D warnings`: 0 errors
- `cargo test --workspace`: frame 7, body 4, sign 4, score 6 (incl. 2 proptests) = 21 passed, 0 failed

## Defects found and fixed during review

1. **`Cargo.lock` was missing** — CI's `--locked` would have failed. Now committed at `stack/01-frame`.
2. **`cargo fmt --check` failed** — plan's snippets exceeded `max_width = 110`. Formatted.
3. **`ed25519-dalek` 3.0.0 has no `std` feature** — plan's contingency applied: `fast` + `zeroize`, recorded in `docs/DEPENDENCIES.md`.
4. **Pedantic lints**: `missing_docs` on test crates and `ocm-sched`; `doc_markdown` on `OpenComputeMesh`/`Ed25519`/`SplitMix64`/`Lemire`; `assert!` vs `assert_eq!` in `test result_bodies_require_signatures`.
5. **`Rng` ambiguous in `score.rs` proptest** — glob conflict, qualified as `ocm_sched::Rng`.

## Review focus (plan §Review Focus) — re-checked

- [x] Task 1: decode errors never echo payload (`errors_never_echo_payload`)
- [x] Task 1: 10k deep nesting → `Malformed`, no stack overflow (`deep_nesting_is_malformed_not_a_crash`)
- [ ] Task 5: NaN/zero/negative throughput → prior (covers `expected_ms`; NaN/inf/-1 covered by `ewma_tracks_and_ignores_garbage`)
- [ ] Task 6: duplicate ids / single eligible among thousands — needs `pick` (not in this stack)
- [ ] Task 7: backwards clock — needs `Breaker` (not in this stack)

## Next

Tasks 6 (`pick` + 50µs budget) and 7 (`Breaker` + `Admission`) remain before the full Plan 1 gate.
