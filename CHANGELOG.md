# Changelog

All notable changes are documented here. The format follows Keep a Changelog and the project uses
Semantic Versioning.

## [Unreleased]

### Added
- Rust workspace with strict quality gates.
- `ocm-proto`: protocol v2 frames with Ed25519 signing of result frames.
- `ocm-sched`: load manager with expected-completion scoring and power-of-two-choices.
- `ocm-config`: nginx-style declarative configuration language with full validation (`ocm -t` semantics).
- `ocm-config`: `backend tinfoil` pools with a mandatory attestation `repo`; a config that claims
  attestation without naming what it verifies against is rejected.
- Confidential tier (v0.3) spec: a coordinator that routes and bills inference it cannot read.
- `tools/tinfoil-probe`: reproduces enclave attestation verification and prints the measurement.
- `tools/tinfoil-gateway`: P1 attested gateway that pins one verified enclave and forwards opaque
  bodies, refusing any other origin and any unverified start.
- `tools/tinfoil-gateway`: container packaging (`Dockerfile`, `tinfoil-config.yml`, publish path) so
  the gateway can run as an attested Tinfoil Container and callers install nothing.

### Changed
- `tools/tinfoil-gateway`: a non-loopback bind now requires `GATEWAY_TOKEN` and the process exits
  without it; `--healthcheck` lets a distroless container probe itself.

## [0.1.0] - 2026-10-11

### Added
- Python coordinator and agent, Rust agent, React web app, credit ledger and optional integrations.
