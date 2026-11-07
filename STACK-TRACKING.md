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

---

# Phase 1.6 — v0.3 confidential tier (Tinfoil)

Spec: `docs/superpowers/specs/2026-11-07-confidential-tier-tinfoil.md`.

## Status

| P | Work | State |
|---|---|---|
| P0 | `backend tinfoil` + mandatory `repo` in `ocm-config` | **done** |
| P1 | Attested backend: verify, EHBP byte forwarding, enclave allowlist | **done** (`tools/tinfoil-gateway`) |
| P2 | Blind metering: reserve-then-reconcile, trailer settlement | not started |
| P3 | Receipt `execution` block, `community` labelling | not started |
| P4 | Coordinator as a Tinfoil Container | deferred, blockers documented |

## Evidence (real, this machine)

- `GET /v1/models` on `inference.tinfoil.sh`: 20 models, including `gpt-oss-120b`,
  `llama3-3-70b`, `gemma4-31b`, `glm-5-3`, `kimi-k3`, `deepseek-v4-1-flash`.
- Chat completion `gpt-oss-120b`: content `enclave reachable`, `finish_reason:
  stop`, usage 73 prompt + 24 completion tokens. (A first attempt with
  `max_tokens: 32` returned `content: null` — reasoning consumed the cap. Budget
  policy must account for reasoning tokens before content.)
- Response header on `/v1/models`: `tinfoil-pt: https://tinfoil.sh/predicate/sev-snp-guest/v2`
  — SEV-SNP guest predicate, i.e. this really is an SNP-guest attestation, not a TLS claim.
- `tools/tinfoil-probe` (official Rust SDK) verifies and prints:
  `verified: true`, `enclave_fingerprint = code_fingerprint = f5bcc220a94d5c34…`
  for `tinfoilsh/confidential-model-router`.
- `tools/tinfoil-gateway` (P1), live smoke run: 2 unit tests pass, clippy clean;
  `/healthz` returns `verified: true` with the pinned host and fingerprint; a real
  completion returned `"gateway reachable"` (73 + 21 tokens); a malformed JSON body
  was rejected by the **upstream** (`invalid_json`), proving the gateway never
  parses bodies; `X-Tinfoil-Enclave-Url: https://evil.example` was refused with
  `400`; the key appeared in zero log lines.
- Upstream usage header reports `cost_usd` alongside token counts
  (`cost_usd=0.00002355` for a 73+21 request). This is the honest settlement basis
  for the tier, and it is recorded in the spec.

## Gate

- `cargo fmt --all --check`: clean
- `cargo clippy --workspace --all-targets -- -D warnings`: 0 errors
- `cargo test --workspace`: config 30 (parse 11, validate 19), frame 7, body 4,
  sign 4, score 6 = **51 passed**, 0 failed
- `tools/tinfoil-probe`: builds and verifies against the live service

## Notes and risks

1. **The upstream key is server-side only.** It lives in untracked `.env`
   (`.gitignore:7`), never in `ocm.conf`, a receipt, or a log. `.env.example`
   documents `TINFOIL_API_KEY` and `TINFOIL_ROUTER_REPO` with empty values.
2. **Contributor hardware cannot be attested.** The confidential tier covers the
   coordinator-as-router and upstream models, not a stranger's laptop. The spec
   states this rather than implying otherwise.
3. **Attestation fingerprints change across enclave reboots**, so they are recorded
   per request, never cached as a permanent claim.
4. **Streaming usage arrives after the body**, so budgets must reserve and then
   reconcile; a disconnect still bills.
5. Coordinator-in-enclave is blocked on the Rust coordinator and on persistent
   storage (the container has no disk by default), not on Tinfoil.

---

# Confidential tier — P1 container packaging

Artifacts: `tools/tinfoil-gateway/{Dockerfile,.dockerignore,tinfoil-config.yml,PUBLISHING.md}`.

| Check | Result |
|---|---|
| `cargo fmt --check` (tool crate) | clean |
| `cargo clippy --all-targets -- -D warnings` | 0 errors |
| `cargo test` | 4 passed, 0 failed |
| public bind without `GATEWAY_TOKEN` | exit 1, refusal message |
| `/v1/models` with no / wrong / right token | 401 / 401 / 200 |
| `--healthcheck` | exit 0 |
| upstream key occurrences in logs | 0 |
| `tinfoil-config.yml` parses; allowlist, shim paths, secrets | verified |

Not run: `docker build` and a container run. No Docker daemon is reachable in this
environment (socket is root-only and `no_new_privs` blocks sudo). Base-image tags
`rust:1.99-slim` and `gcr.io/distroless/cc-debian12:nonroot` were confirmed to exist
with `docker manifest inspect`, which needs no daemon.
