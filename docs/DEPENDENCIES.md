# Dependencies

Every dependency must earn its place. Default features are disabled unless noted.

| Crate | Used by | Why |
|---|---|---|
| serde, serde_json | ocm-proto | JSON wire format with derive; the protocol is JSON so browsers and tools can read it |
| ed25519-dalek | ocm-proto | Node identity and result-frame signatures (audited, pure Rust) |
| getrandom | ocm-proto | OS randomness for generating node keys, without pulling a full RNG stack |
| proptest (dev) | ocm-proto, ocm-sched | Property tests for protocol and scheduler invariants |
