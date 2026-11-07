# Stack tracking — Plan 1 Tasks 1-5 (accountability)

Base: `docs/roadmap` (2e6e7a8) — roadmap + v0.1 + v0.2 plan.
No local cargo/rustc in this VM, so verification is via CI (`workspace` job).
Do NOT claim green until CI passes on each PR.

Stack (each branch stacked on previous, PR to previous, final to main):
1. stack/01-frame — Task 1: workspace, quality gates, frame envelope + tests/frame.rs
2. stack/02-bodies — Task 2: Body variants + tests/body.rs
3. stack/03-sign — Task 3: NodeKey + verify + tests/sign.rs
4. stack/04-fuzz — Task 4: fuzz/ decode_frame + CI fuzz 60s
5. stack/05-score — Task 5: Rng + Ewma + eligible/expected_ms + tests/score.rs

Accountability checklist (update per PR):
- [ ] 01 CI: fmt, clippy -D warnings, cargo test --workspace pass
- [ ] 02 CI: body roundtrips, deny_unknown_fields holds
- [ ] 03 CI: sign roundtrip, tamper rejected, secret roundtrip
- [ ] 04 CI: fuzz target compiles (nightly), 60s run passes
- [ ] 05 CI: score + proptest pass, clippy clean
- [ ] Review focus re-checked: no payload echo, deep nesting Malformed, NaN/0/neg -> prior, dup ids/single-eligible, backwards clock safe

Parallel-agent protocol used here:
- Sequential scaffold by one builder (this session) because this env has no parallel code agents and no background exec.
- Next: assign one owner per stack branch (01-05) to take it to green in parallel, rebasing only onto its parent.
- No direct push to main. Merge bottom-up: 01 -> 02 -> 03 -> 04 -> 05.

How to take a branch to green:
git checkout stack/0X-xxx
# fix, then:
cargo fmt --all && cargo clippy --workspace --all-targets -- -D warnings && cargo test --workspace --locked
git commit + push (via integration), update checkbox above in next stack commit.
