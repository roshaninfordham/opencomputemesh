# Releasing

`.github/workflows/release.yml` publishes everything a release contains. It runs on
native runners, because macOS and Windows binaries cannot be cross-compiled from Linux —
and a Tauri desktop build cannot either.

## Cut a release

```bash
git tag -a v0.1.3 -m "v0.1.3"
git push origin v0.1.3
```

The tag is the release. Pushing it runs four jobs, then publishes them together with
`SHA256SUMS.txt` and generated release notes. To re-publish an existing tag without
moving it, run the workflow manually with that tag as input
(`workflow_dispatch`).

## If you get stuck

**`fatal: tag 'v0.1.3' already exists`** — you do not need to create it again; that tag is
already on the commit you want. Skip straight to the push. Check first:

```bash
git rev-list -n1 v0.1.3        # the commit the tag points at
git rev-parse --short main     # the commit you mean to release
```

If they differ, move the tag: `git tag -d v0.1.3 && git tag -a v0.1.3 -m "v0.1.3" <commit>`.
If they match, `git push origin v0.1.3` is the entire remaining step.

**`Username for 'https://github.com'`** — git has no credential helper, so it is asking you
every time. Log in once with the GitHub CLI and it configures git for you:

```bash
gh auth login          # GitHub.com -> HTTPS -> yes to "Authenticate Git" -> browser
git push origin v0.1.3
```

No GitHub CLI? Either `gh auth setup-git` after installing it, or use SSH
(`ssh-keygen -t ed25519`, add the key to GitHub, then
`git remote set-url origin git@github.com:OWNER/opencomputemesh.git`).

**Nothing happens after the push** — confirm the tag reached GitHub with
`git ls-remote --tags origin`, then watch the run with `gh run watch`. Tags are not
branches: `git push` alone never sends them, and a pushed branch named `v0.1.3` would not
trigger this workflow.

## What a release contains

| Artifact | Who it is for |
|---|---|
| `ocm-node-<version>-aarch64-apple-darwin` | Apple silicon Mac, sharing compute |
| `ocm-node-<version>-x86_64-apple-darwin` | Intel Mac, sharing compute |
| `ocm-node-<version>-x86_64-pc-windows-msvc.exe` | Windows x64, sharing compute |
| `ocm-node-<version>-x86_64-unknown-linux-musl` | Linux x64, static, no glibc version problem |
| `opencomputemesh-<version>-py3-none-any.whl` | Coordinator (`ocm serve`) and Python agent (`ocm-agent`) on macOS, Windows and Linux |
| `opencomputemesh-web-<version>.zip` | The built web UI, for a coordinator that serves it |
| `SHA256SUMS.txt` | Verify before you run anything |

Not in a release, deliberately: `tools/tinfoil-gateway` (its image is published to a
container registry, not GitHub Releases) and the desktop shell (not built yet — see
"Desktop app" below).

## Install, per OS

**Coordinator (any OS, needs Python 3.12+):**

```bash
uv tool install opencomputemesh==0.1.3     # or: pipx install opencomputemesh==0.1.3
ocm serve
```

**Web UI:** the coordinator serves `./web/dist` by default. Unzip
`opencomputemesh-web-<version>.zip` there (or set `OCM_WEB_DIST` to the folder you
unzipped). Without it, the API works and the UI answers 404.

**Node (sharing compute):**

```bash
# macOS / Linux
chmod +x ocm-node-0.1.3-aarch64-apple-darwin
./ocm-node-0.1.3-aarch64-apple-darwin --help
```

The Windows `.exe` runs directly. It still needs a model runtime on the same machine
(Ollama), because the node talks to it over loopback.

## Unsigned binaries: say this out loud

These artifacts are not code-signed yet.

- **macOS:** Gatekeeper blocks the first launch. Right-click → Open, or
  `xattr -d com.apple.quarantine <binary>`. Signing and notarisation need an Apple
  Developer certificate; until then, give the SHA-256 and say plainly that the binary
  is unsigned.
- **Windows:** SmartScreen shows "Windows protected your PC". "More info" → "Run
  anyway".

Do not describe these as one-click installers. They are checksummed downloads.

## Version to bump before tagging

A version lives in five places and all five must agree, or a `--locked` install fails in CI:

1. `pyproject.toml` → `[project].version`
2. `uv.lock` → the `opencomputemesh` package entry (`uv lock` updates it)
3. `crates/ocm-node/Cargo.toml` → `[package].version`
4. `crates/ocm-node/Cargo.lock` → the `ocm-node` package entry
5. `web/package.json` and `web/package-lock.json` → both version fields

Also add the `CHANGELOG.md` section named exactly `## [<version>] - <date>`.

## Desktop app (Tauri): planned, not built

The roadmap puts a Tauri 2 shell in Phase 1.5 (tray, pause, bundled coordinator). It is
not in this repository yet: there is no `src-tauri` directory and no `tauri` dependency.
When it lands it adds `.dmg`/`.app` and `.msi`/`.exe` installers here, and it must be
built on the same native runners — Tauri cannot cross-compile a macOS bundle from Linux
either.

Today the web bundle is what covers "every device": it runs in any browser, including
phones and tablets, against a coordinator URL. That is the widest reach we have, and it
needs no installation at all.
