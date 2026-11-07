# Publishing the gateway as a Tinfoil Container

The point: an OpenAI-compatible endpoint anyone can call with `curl`, running inside
an enclave, with no install step for the caller.

Be precise about what that buys us. The container attests **our gateway process**, not
the GPUs behind it. A caller learns that the code proxying their request is the code in
this repo, and that the operator's upstream key is not sitting on a box we can read.
That is a real guarantee and it is also a narrow one: it says nothing about the
community tier, where contributors still run `ocm node` on hardware nobody attested.
Do not blur the two in public copy.

## Already in this repo

| Piece | Why |
|---|---|
| `Dockerfile` | Stateless, non-root, distroless. No volume, so read-only root is fine. |
| `tinfoil-config.yml` | Digest-pinned image, `egress: allowlist`, secret names only. |
| `--healthcheck` | Distroless has no shell or curl; the binary probes itself. |
| `GATEWAY_TOKEN` gate | Required for any non-loopback bind, or the process exits. |

## Steps that need your accounts

### 1. Push the image and pin its digest

```bash
docker buildx build --platform linux/amd64 \
  -t ghcr.io/opencomputemesh/tinfoil-gateway:0.1.0 --push tools/tinfoil-gateway

docker buildx imagetools inspect ghcr.io/opencomputemesh/tinfoil-gateway:0.1.0 \
  --format '{{.Manifest.Digest}}'
```

Put that digest into `tinfoil-config.yml` under `containers[0].image`, replacing
`sha256:PIN_ME`. `--platform linux/amd64` is not optional: enclaves are amd64, and an
arm64 image built on a laptop will not boot.

### 2. Create the public config repository

Open [tinfoil-containers-template](https://github.com/tinfoilsh/tinfoil-containers-template)
→ **Use this template** → create a **public** repository. Copy this directory's
`tinfoil-config.yml` to its root. Keep both workflows:
`.github/workflows/tinfoil-release.yml` and `tinfoil-release-publish.yml`.

The config must be public so clients can verify the measurement independently. Never
put a secret value in it.

### 3. Create the two secrets in the same Tinfoil organization

```bash
# The operator's upstream credential.
printf '%s' "$TINFOIL_API_KEY" | tinfoil secret create TINFOIL_API_KEY --value-file -

# The caller credential. 32 random bytes, hex.
openssl rand -hex 32 | tr -d '\n' | tinfoil secret create GATEWAY_TOKEN --value-file -
```

Save the `GATEWAY_TOKEN` value somewhere you can hand it to callers; it is not
recoverable from the enclave.

### 4. Release, then create the instance

```bash
tinfoil repo build run <owner>/ocm-gateway-config --version v0.1.0
tinfoil repo build status <owner>/ocm-gateway-config --version v0.1.0
```

Wait for **both** workflow phases and a published GitHub release. Queued is not
deployable; a tag alone is not a release.

```bash
tinfoil container create ocm-gateway \
  --repo <owner>/ocm-gateway-config --tag v0.1.0
tinfoil container get ocm-gateway
```

### 5. Verify before announcing

```bash
# Attested request: expect verified:true plus the gateway's fingerprint.
tinfoil http get -e <instance-domain> \
  -r <owner>/ocm-gateway-config@v0.1.0 \
  https://<instance-domain>/healthz

# The gate is on: expect 401.
curl -s -o /dev/null -w '%{http_code}\n' https://<instance-domain>/v1/models

# The gate lets the right caller through: expect 200 and a model list.
curl -s https://<instance-domain>/v1/models \
  -H "Authorization: Bearer $GATEWAY_TOKEN"
```

If `/v1/models` answers 200 **without** a token, stop: a secret failed to arrive and the
container is spending the upstream key for anyone.

## Limits worth designing around

- **2 CPU / 8 GiB minimum**, 10 instances per organization.
- **The allowlist is the only outbound path.** If Tinfoil adds a hostname to the
  attestation flow, boot fails closed instead of silently degrading, and
  `kds-proxy`/`atc`/`github-proxy` names in `tinfoil-config.yml` need updating.
- **One shared token = one shared budget.** Every bearer spends the operator's key.
  Per-caller tokens and budgets belong to the Rust coordinator; until those exist, this
  is a private endpoint, not a public service.

## Order of operations

1. Private instance and your own token — what this document describes.
2. Per-caller credentials and budgets in the Rust coordinator.
3. Publish the URL, and only then describe it as a service.
