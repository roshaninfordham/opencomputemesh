# ocm-tinfoil-gateway

P1 of the confidential tier: an OpenAI-compatible gateway that verifies a Tinfoil
enclave **before** serving, then forwards request bodies it never parses.

```bash
set -a; . ../../.env; set +a
cargo run          # listens on 127.0.0.1:8787
```

| Variable | Default | Meaning |
|---|---|---|
| `TINFOIL_API_KEY` | required | Upstream credential. Never sent to a client. |
| `TINFOIL_UPSTREAM` | `inference.tinfoil.sh` | The one enclave host that will be trusted. |
| `TINFOIL_ROUTER_REPO` | `tinfoilsh/confidential-model-router` | Code measurement to verify against. |
| `GATEWAY_BIND` | `127.0.0.1:8787` | Listen address. Keep it loopback unless a coordinator fronts it. |

Routes: `POST /v1/{path}` (and `GET` for `/v1/models`), plus `GET /healthz`.

## The design decision that matters

The gateway does **not** allowlist `*.tinfoil.sh`. It verifies exactly one origin at
startup and pins TLS to it, then refuses any request whose
`X-Tinfoil-Enclave-Url` names a different host. A wildcard allowlist would let a
caller direct our key at any Tinfoil-hosted enclave; a single pin cannot be steered.

If verification fails, the process exits. There is no flag to continue unverified,
because a downgrade path would be the vulnerability this exists to prevent.

## What it does and does not protect

- **Does**: keep the upstream key server-side, refuse cross-origin redirection,
  forward bodies byte-for-byte so an EHBP client's plaintext only exists inside the
  enclave, and request usage metrics for blind billing.
- **Does not**: authenticate callers, rate-limit, or hold budgets. Those belong to
  the coordinator, which will host this code once it exists in Rust.
- **Does not** verify per-request attestation: verification happens at connect time
  and pins the TLS certificate, which is the SDK's model.

## Known gaps (deliberate, tracked)

1. **No `/attestation` endpoint yet.** Rust and Go clients verify the enclave
   directly and do not need it; JavaScript, Python and Swift do. Adding it needs a
   second, unpinned HTTP client for the ATC, which is a separate decision.
2. **Response trailers are not forwarded.** Streaming usage arrives as an HTTP
   trailer, so blind billing of streams is P2 work; non-streaming usage is forwarded
   in `x-tinfoil-usage-metrics`.
3. **No tests against a live enclave.** Unit tests cover origin pinning and header
   hygiene; the end-to-end check is a manual smoke run.

## Why standalone

The runtime workspace has no HTTP server yet, and adding tokio + axum to it before
the Rust coordinator exists would fix the dependency graph around a component that
is still being designed. This crate is excluded from the workspace (`Cargo.toml`),
keeps its own lockfile, and moves in when the coordinator lands.
