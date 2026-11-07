# Dependencies

Every dependency must earn its place. Default features are disabled unless noted.

| Crate | Used by | Why |
|---|---|---|
| serde, serde_json | ocm-proto | JSON wire format with derive; the protocol is JSON so browsers and tools can read it |
| ed25519-dalek | ocm-proto | Node identity and result-frame signatures (audited, pure Rust) |
| getrandom | ocm-proto | OS randomness for generating node keys, without pulling a full RNG stack |
| proptest (dev) | ocm-proto, ocm-sched | Property tests for protocol and scheduler invariants |
| ed25519-dalek | ocm-proto | 3.0.0 has no `std` feature; signing and verification use `fast` + `zeroize` (no `alloc` needed) |
| libfuzzer-sys (fuzz only) | fuzz/ | Coverage-guided fuzzing of the frame decoder in CI |
| tinfoil (probe only) | tools/tinfoil-probe | Official SDK for enclave attestation; fetched from git and kept out of the workspace so the runtime set stays deterministic |
| axum, tokio, reqwest (gateway only) | tools/tinfoil-gateway | HTTP server and upstream client for the attested gateway; kept out of the workspace until the Rust coordinator owns an HTTP surface |
| (none) | ocm-config | Deliberately dependency-free: the config language needs only `std` |
