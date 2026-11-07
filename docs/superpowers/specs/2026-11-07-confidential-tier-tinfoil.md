# OpenComputeMesh v0.3: the confidential tier (Tinfoil)

Status: proposed, 2026-11-07. Depends on `ocm-config` (Phase 1.5, P0 shipped).
License: Apache-2.0. Platform: [Tinfoil](https://docs.tinfoil.sh).

## 1. The one-sentence product

**Your prompt is encrypted to a hardware enclave that we cannot read, we route and
bill it anyway, and the receipt names the exact code measurement that processed it.**

Everything below follows from that sentence.

## 2. Why this, and not something else

The mesh has three trust gaps. Only some of them are fixable, and pretending
otherwise is how privacy products lose credibility.

| Gap | Today | Can Tinfoil close it? |
|---|---|---|
| The **coordinator** sees every prompt in plaintext while routing and metering | A promise in the README ("prompts are toxic, never logged") | **Yes.** End-to-end encryption to the enclave makes the coordinator blind by construction (EHBP). |
| A **contributor's laptop** is a stranger's machine running unmeasured code | Trust, or don't share | **No.** Consumer hardware has no TDX/SEV-SNP measurement we can attest. Say this plainly. |
| A **receipt** proves accounting (tokens, price) but not execution | Nothing to verify | **Yes.** Attestation fingerprints can be signed into the receipt. |

Two gaps closed, one honestly out of reach. That split is the product: a
*community* tier for cheap capacity you trust socially, and a *confidential*
tier where the privacy claim is hardware-enforced and independently checkable.

The share-URL and agent-token work (Phase 1.5) is what makes strangers able to
transact at all. This tier is what makes it defensible to do so.

## 3. Architecture: the coordinator as a blind, metered router

Tinfoil's own proxy guidance describes a server that holds the API key, forwards
bodies it cannot decrypt, and meters from response headers. An OCM coordinator is
already that shape, so the confidential tier is an adapter rather than a rewrite.

```text
client (SDK)                     coordinator                     tinfoil
    |                                |                              |
    |-- GET /attestation ----------->|----------------------------->|
    |<------------- attestation bundle|<-----------------------------|
    |  verify measurements vs. pinned repo (client-side)             |
    |                                |                              |
    |-- POST /v1/chat/completions -->|                              |
    |   Ehbp-Encapsulated-Key        | authenticate OCM token        |
    |   X-Tinfoil-Enclave-Url        | model allowlist (header)      |
    |   X-Tinfoil-Model              | reserve budget                |
    |   (body = HPKE ciphertext)     |                              |
    |                                |-- forward + TINFOIL_API_KEY ->|
    |                                |   X-Tinfoil-Request-Usage-Metrics
    |                                |<-- ciphertext + usage -------|
    |<-- ciphertext + Ehbp-Response-Nonce                           |
    |  decrypt locally               | settle from usage metrics     |
```

What the coordinator can see: who called, which model, when, how many tokens,
and how much it cost. What it cannot see: the prompt or the completion. That is
the entire selling proposition, and it is enforced by the enclave's key, not by
our good intentions.

### Non-negotiable rules

1. **Fail closed.** If attestation verification fails, the request fails. There is
   no fallback to a plaintext upstream, because a silent downgrade is the attack
   this feature exists to prevent.
2. **The key never leaves the server.** `TINFOIL_API_KEY` is read from the
   environment and used only for upstream calls. It never appears in `ocm.conf`,
   a receipt, a log line, or a browser response.
3. **`X-Tinfoil-Enclave-Url` is untrusted input.** Forward only to approved
   `https://*.tinfoil.sh` origins, or a client can use us as an open proxy.
4. **Model allowlist is enforced on the header, then re-checked by Tinfoil
   against the decrypted body.** A mismatch is a `400` before inference runs.
5. **Attestation is verified by the client and observed by the coordinator.**
   These are different claims and the receipt must not blur them: the client's
   verification is the strong one; the coordinator's recorded fingerprints are a
   corroborating observation, signed by the coordinator, not by hardware.

## 4. Receipts carry execution evidence

Extend the result frame with an `execution` object:

```json
{
  "execution": {
    "backend": "tinfoil",
    "repo": "tinfoilsh/confidential-model-router",
    "enclave_fingerprint": "f5bcc220a94d5c34c5c80db9593dcc88d7d1a19b43796c05ec4b6cdcf921c8176a56cb3d0224650b1250ee892a0ec438",
    "verified_at": "2026-11-07T12:00:00Z",
    "verified_by": "coordinator",
    "client_verified": true
  }
}
```

- `backend: "community"` receipts omit fingerprints and still say so explicitly,
  so absence of evidence is never mistaken for evidence of privacy.
- The fingerprint is recorded per request, not cached forever: enclaves reboot and
  measurements change. A stale fingerprint is a lie with a timestamp.
- Reconciliation: the ledger settles from `X-Tinfoil-Usage-Metrics`, which the
  coordinator trusts for billing but marks as `reported_by: upstream`.

## 5. Configuration

```nginx
pool "confidential" {
    backend tinfoil;
    model gpt-oss-120b;
    repo tinfoilsh/confidential-model-router;
}

route "private" {
    path /private;
    pool confidential;
    auth token;
}
```

`repo` is **required** for `backend tinfoil` and **rejected** on `backend community`.
A pool that claims attestation without naming what it verifies against has no
guarantee at all, and a community pool carrying a repo is a copy-paste bug that
would imply one. Both are config errors, enforced by `ocm -t`.

## 6. Metering without reading the body

Budget enforcement has a real subtlety that must not be hand-waved:

- Non-streaming: usage arrives in `X-Tinfoil-Usage-Metrics`, so settle exactly.
- Streaming: usage arrives as an HTTP trailer **after** the body, so the coordinator
  must *reserve* `max_tokens` of budget up front and reconcile at the end. A client
  that disconnects mid-stream must still be charged for what the enclave produced.
- `tokens/sec` and `finish_reason` are likewise upstream-reported, never inferred.
- The usage header also carries `cost_usd`, the provider's own measured cost for the
  request. Settlement for this tier should reconcile against that number rather than
  inventing a tariff: we can charge a margin, but we should not fabricate a price
  for a cost we did not incur and cannot observe.

## 7. Deploying OCM itself as a Tinfoil Container (deferred, deliberately)

The symmetric move — run the coordinator in an enclave so users verify *our*
routing code, not just Tinfoil's — is the natural sequel and must not be promised
before it is buildable. Blockers, from their own docs:

- **No Rust coordinator yet.** v0.2 native core is mid-cutover; today the Python
  coordinator is what runs. Containerising a component being rewritten builds it twice.
- **No persistent disk by default.** The ledger is the product; it needs an
  encrypted persistent volume (beta) or the S3 sidecar, plus an unlock path.
- **No load balancing and no inbound private networking.** One enclave per instance;
  we would need our own front door and auth, which we have, but capacity planning
  is ours.
- **The config repo must be public**, because it is measured. Our `tinfoil-config.yml`
  and image digests become public artifacts; that is fine, and it is a commitment
  we should make deliberately.

Revisit once the Rust coordinator serves `/v1` end to end.

## 8. Milestones

| P | Work | Acceptance |
|---|---|---|
| P0 | `backend tinfoil` + `repo` invariant in `ocm-config` | `ocm -t` accepts the example; a tinfoil pool without `repo` is rejected; a community pool with `repo` is rejected |
| P1 | Attested backend: verify, then forward EHBP bytes | A real completion through the coordinator whose request body the coordinator cannot decrypt; enclave URL allowlist enforced |
| P2 | Blind metering: reserve then reconcile, per-key budget under encryption | Over-budget key stops mid-stream at the reserve, settles on trailer; disconnect still bills |
| P3 | Receipt `execution` block, client-verified flag | Receipt shows fingerprints; community receipts say `community` explicitly |
| P4 | Coordinator as a Tinfoil Container | Users verify the coordinator's own measurement |

### P1 status: shipped as `tools/tinfoil-gateway`

Standalone, because the coordinator that will eventually host it is still Python.
Verified against the live service:

- Startup fails closed: the gateway verifies the enclave and obtains the
  attestation-pinned client before it binds a socket.
- A real completion returned through the gateway (`gpt-oss-120b`, 73 prompt + 21
  completion tokens).
- A deliberately malformed JSON body was rejected **by the upstream** with
  `invalid_json`, which is the observable proof that the gateway forwards bytes it
  never parses. With an EHBP client those same bytes are ciphertext.
- A request naming `https://evil.example` was refused locally with `400`; the
  pinned origin is compared for equality, so a lookalike host cannot match.
- The upstream key appeared in zero log lines, and a client `Authorization` header
  is dropped rather than forwarded.

Deliberately not done yet: no `/attestation` endpoint (needed only by the
JavaScript, Python and Swift clients), no trailer forwarding (so streaming usage
is not yet billable), and no caller authentication — which is why it binds loopback
and must not be exposed publicly in this form.

## 9. Failure modes and responses

| Failure | Response |
|---|---|
| Attestation mismatch | Fail closed, `502`, no plaintext fallback, alert |
| Upstream 429 | Surface as `429`, refund the reserve |
| Trailer missing on a stream | Settle at the reserve, mark receipt `usage: estimated` |
| Enclave URL not on the allowlist | `400`, log the origin |
| `TINFOIL_API_KEY` unset | Confidential pools are unavailable and say so; no silent downgrade |

## 10. Out of scope

- Running our own enclaves or writing attestation verification ourselves. Use the
  SDK; hand-rolled attestation verification is how this class of product breaks.
- Attesting contributor hardware. It is not possible, so it is not claimed.
- Tinfoil-hosted features beyond chat completions until the tier is proven.
