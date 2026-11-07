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

---

# Plan 2 — v0.2.5 edge config and sharing

Branch: `feat/edge-config` (from `release/plan1`). Spec
`docs/superpowers/specs/2026-11-07-edge-config-and-sharing.md`, plan
`docs/superpowers/plans/2026-11-07-v0.2-plan-2-config.md`.

## Status

| P | Task | State | Local gate |
|---|---|---|---|
| P0 | 1. `ocm-config` language | **done** | PASS |
| P1 | 2. include resolution | not started | - |
| P2 | 3. share URLs | not started | - |
| P3 | 4. agent tokens | not started | - |
| P4 | 5. free OSS models | not started | - |
| P5 | 6. hot reload | not started | - |
| P6 | 7. config over env | not started | - |

## Gate evidence (local, Rust 1.99.0)

- `cargo fmt --all --check`: clean
- `cargo clippy --workspace --all-targets -- -D warnings`: 0 errors
- `cargo test --workspace`: config 26 (parse 11, validate 15), frame 7, body 4,
  sign 4, score 6 = **47 passed**, 0 failed
- `cargo check --manifest-path fuzz/Cargo.toml --bins`: clean (both targets)

## Defects found and fixed during review

1. **`grant 100;` was rejected as malformed** — the share parser read `grant`
   through the one-argument path while validation expected two. Rewritten as an
   explicit two-argument branch before the one-argument `match`.
2. **`lex` was 133 lines in one function** — clippy `too_many_lines`. Split into
   a `Lexer` cursor with `word`/`quoted`/`punct`/`advance` helpers.
3. **`parse_network` returned `Option` it never produced** — `unnecessary_wraps`.
4. **`match` used where `if let`/`if` reads better** — `single_match_else` and
   `match_single_binding` on `parse_pool`, `parse_route`, `parse_share`.
5. **`assigning_clones`** on `allow_models` and `route.path` — `clone_from` /
   `clone_into`.
6. **EOF error pointed at the last argument** — a missing `;` reported column 8
   of `listen 8080` instead of the directive's own column 1. Now anchored to the
   directive start.
7. **Doc drift caught while writing the spec**: `docs/ARCHITECTURE.md` showed a
   `pool` nested inside a `route` and claimed `include` was expanded in P0. Both
   corrected (include is parsed and retained only).
8. **`allow_models;` degraded to allow-all** — an empty directive produced both
   an `ArgCount` error *and* the documented "all models" default, so a typo could
   widen a key's reach. `TokenCfg::allow_models` is now `Option<Vec<String>>`:
   `None` is allow-all, an empty directive is an error and never a wildcard.
9. **Undefined pool references were reported at `ocm.conf:1:1`** — the
   cross-reference pass ran after the node was dropped, so it had no location to
   report. Route references now carry their own source, line and column.
10. **`include` without a path, and `include ... { }`, were silently accepted** —
    now `ArgCount` and `BadValue` respectively.
11. **`expires 0s` and `max_nodes 0` were accepted** — a link that can never be
    used. Both are errors now; "unlimited" is expressed by omitting the
    directive. This rule caught the fixture itself, which used `max_nodes 0` to
    mean unlimited: the ambiguous spelling is gone rather than special-cased.
12. **The text parser had no fuzz coverage** — added the `parse_config` target
    and a second CI step, because `include` will soon feed it arbitrary file
    contents.

## Review focus

- [x] Errors never echo config contents (only directive names and shapes)
- [x] `validate()` returns every problem, not the first (fixture asserts >= 3)
- [x] Depth limit rejects >32 nested blocks without a stack overflow
- [x] Duplicate `network` is rejected; duplicate pools/tokens/shares/routes too
- [x] `route.pool` must resolve to a defined pool
- [x] `examples/ocm.conf` is the acceptance fixture for `ocm -t`
- [ ] Include resolution (P1) — deliberately deferred, documented as such
