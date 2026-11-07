# ocm-tinfoil-probe

**Not part of the runtime.** A manual check that the Tinfoil router enclave is
running the code published for a pinned repository, printed as JSON so the claim
in `tools/tinfoil-gateway/README.md` can be
reproduced by anyone holding a key.

```bash
set -a; . ../../.env; set +a
cargo run -q
```

```json
{"host":"inference.tinfoil.sh","repo":"tinfoilsh/confidential-model-router","verified":true,
 "enclave_fingerprint":"f5bcc2…","code_fingerprint":"f5bcc2…"}
```

A fingerprint means the hardware measured a launch configuration, and the SDK
matched it against the Sigstore-published measurement for that repository. If the
two disagree, the SDK fails instead of connecting — which is the entire point.

## Why a separate crate

The `tinfoil` SDK pins `async-openai` and `reqwest` and is fetched from git. The
runtime workspace keeps a small, deterministic dependency set (`--locked` in CI,
plus `cargo-deny`), so this crate is excluded from the workspace and is not built
by `cargo test --workspace`. `Cargo.lock` here pins the SDK revision.

P1 of the confidential tier moves this verification into `crates/ocm-tinfoil`
inside the workspace, behind the coordinator's attested backend; this crate then
either disappears or becomes a diagnostic.

## Reading the two fingerprints

For the hosted router the two values are identical: the router enclave and the
model enclaves it admits are chained, so the client's verification covers the
router's measured code. Treat a *changed* value across days as expected (enclaves
reboot and are re-measured); treat a value that does not match the pinned repo's
published measurement as a hard failure, never as a warning.
