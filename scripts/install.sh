#!/bin/sh
# Install ocm-node from OpenComputeMesh GitHub releases.
#
#   curl -fsSL https://raw.githubusercontent.com/roshaninfordham/opencomputemesh/main/scripts/install.sh | sh
#
# Picks the build for this machine, verifies it against the release's SHA256SUMS.txt, and puts
# the binary in ~/.local/bin (or OCM_INSTALL_DIR). No sudo, no shell-config edits.
set -eu

REPO="${OCM_REPO:-roshaninfordham/opencomputemesh}"
VERSION="${OCM_VERSION:-latest}"
INSTALL_DIR="${OCM_INSTALL_DIR:-$HOME/.local/bin}"
OS="${OCM_OS:-}"
ARCH="${OCM_ARCH:-}"
PRINT_ASSET=0

die() {
  printf 'install: %s\n' "$*" >&2
  exit 1
}

usage() {
  cat <<'EOF'
Install ocm-node from the latest (or a pinned) OpenComputeMesh release.

Usage: sh install.sh [options]

  --version <tag>   install a specific release, e.g. --version v0.1.3 (default: latest)
  --dir <path>      install here (default: ~/.local/bin, or $OCM_INSTALL_DIR)
  --print-asset     print the release-asset suffix for this machine and exit (no network)
  -h, --help        show this help

Environment: OCM_VERSION, OCM_INSTALL_DIR, OCM_REPO, OCM_OS, OCM_ARCH
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --version) [ $# -ge 2 ] || die "--version needs a tag such as v0.1.3"; VERSION="$2"; shift 2 ;;
    --version=*) VERSION="${1#*=}"; shift ;;
    --dir) [ $# -ge 2 ] || die "--dir needs a path"; INSTALL_DIR="$2"; shift 2 ;;
    --dir=*) INSTALL_DIR="${1#*=}"; shift ;;
    --print-asset) PRINT_ASSET=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown option: $1 (try --help)" ;;
  esac
done

if [ -z "$OS" ]; then OS="$(uname -s)"; OS_DETECTED=1; else OS_DETECTED=0; fi
if [ -z "$ARCH" ]; then ARCH="$(uname -m)"; fi

# Prints the asset suffix for this machine, or explains why there is none.
map_asset() {
  case "$OS" in
    Darwin)
      case "$ARCH" in
        arm64|aarch64) printf 'macos-apple-silicon\n' ;;
        x86_64|amd64)
          # An Intel shell under Rosetta says x86_64; install the native build anyway.
          if [ "$OS_DETECTED" = 1 ] && [ "$(sysctl -n hw.optional.arm64 2>/dev/null || echo 0)" = 1 ]; then
            printf 'macos-apple-silicon\n'
          else
            printf 'macos-intel\n'
          fi ;;
        *) die "unsupported Mac architecture: $ARCH (arm64 and x86_64 have builds)" ;;
      esac ;;
    Linux)
      case "$ARCH" in
        x86_64|amd64) printf 'linux-x64\n' ;;
        aarch64|arm64) die "no Linux arm64 build yet. Build one from source (crates/ocm-node) or use the Python agent: uv tool install opencomputemesh" ;;
        *) die "unsupported Linux architecture: $ARCH (only x86_64 has a build)" ;;
      esac ;;
    MINGW*|MSYS*|CYGWIN*)
      die "this is the macOS/Linux installer. On Windows run install.ps1 in PowerShell: irm https://raw.githubusercontent.com/roshaninfordham/opencomputemesh/main/scripts/install.ps1 | iex" ;;
    *)
      die "unsupported operating system: $OS (macOS, Linux and Windows are supported)" ;;
  esac
}

ASSET_SUFFIX="$(map_asset)"

if [ "$PRINT_ASSET" = 1 ]; then
  printf '%s\n' "$ASSET_SUFFIX"
  exit 0
fi

command -v curl >/dev/null 2>&1 || die "curl is required to download the release"

if [ "$VERSION" = latest ]; then
  LATEST_URL="https://github.com/$REPO/releases/latest"
  EFFECTIVE="$(curl -fsSIL --proto '=https' --tlsv1.2 -o /dev/null -w '%{url_effective}' "$LATEST_URL")" \
    || die "could not read the latest release from $LATEST_URL (set OCM_VERSION=vX.Y.Z to install a known tag)"
  TAG="${EFFECTIVE##*/}"
else
  TAG="$VERSION"
  case "$TAG" in v*) ;; *) TAG="v$TAG" ;; esac
fi

printf '%s\n' "$TAG" | grep -Eq '^v[0-9]+\.[0-9]+\.[0-9]+$' \
  || die "not a release tag: $VERSION (expected vX.Y.Z)"

VERSION_NUM="${TAG#v}"
ASSET="ocm-node-${VERSION_NUM}-${ASSET_SUFFIX}"
BASE="https://github.com/$REPO/releases/download/$TAG"

TMP="$(mktemp -d "${TMPDIR:-/tmp}/ocm-install.XXXXXX")" || die "could not create a temporary directory"
cleanup() { rm -rf "$TMP"; }
trap cleanup EXIT HUP INT TERM

printf 'downloading %s (%s)\n' "$ASSET" "$TAG"
curl -fsSL --proto '=https' --tlsv1.2 --retry 3 --retry-delay 1 -o "$TMP/$ASSET" "$BASE/$ASSET" \
  || die "download failed: $BASE/$ASSET"
curl -fsSL --proto '=https' --tlsv1.2 --retry 3 --retry-delay 1 -o "$TMP/SHA256SUMS.txt" "$BASE/SHA256SUMS.txt" \
  || die "download failed: $BASE/SHA256SUMS.txt"

EXPECTED="$(awk -v want="$ASSET" '$2 == want { print $1; exit }' "$TMP/SHA256SUMS.txt")"
[ -n "$EXPECTED" ] || die "SHA256SUMS.txt has no entry for $ASSET"

if command -v sha256sum >/dev/null 2>&1; then
  ACTUAL="$(sha256sum "$TMP/$ASSET" | awk '{ print $1 }')"
elif command -v shasum >/dev/null 2>&1; then
  ACTUAL="$(shasum -a 256 "$TMP/$ASSET" | awk '{ print $1 }')"
else
  die "need sha256sum or shasum to verify the download; neither was found"
fi

if [ "$ACTUAL" != "$EXPECTED" ]; then
  die "checksum mismatch for $ASSET
  expected $EXPECTED
  got      $ACTUAL
Nothing was installed."
fi

mkdir -p "$INSTALL_DIR" 2>/dev/null \
  || die "cannot create $INSTALL_DIR (set OCM_INSTALL_DIR to a writable path)"
TARGET="$INSTALL_DIR/ocm-node"
OLD=""
if [ -x "$TARGET" ]; then
  OLD="$("$TARGET" --version 2>/dev/null | head -n 1 || true)"
fi
STAGE="$INSTALL_DIR/.ocm-node.$$"
cp "$TMP/$ASSET" "$STAGE" || die "cannot write to $INSTALL_DIR"
chmod 0755 "$STAGE"
mv -f "$STAGE" "$TARGET" || { rm -f "$STAGE"; die "cannot replace $TARGET"; }

if [ "$OS" = Darwin ] && command -v xattr >/dev/null 2>&1; then
  xattr -d com.apple.quarantine "$TARGET" 2>/dev/null || true
fi

NEW="$("$TARGET" --version 2>/dev/null | head -n 1 || true)"
[ -n "$NEW" ] || NEW="ocm-node $VERSION_NUM"

printf '\n%s -> %s\n' "$NEW" "$TARGET"
if [ -n "$OLD" ] && [ "$OLD" != "$NEW" ]; then
  printf 'replaced %s\n' "$OLD"
fi
printf 'sha256 verified against SHA256SUMS.txt (%s)\n' "$TAG"

ON_PATH=1
case ":$PATH:" in
  *":$INSTALL_DIR:"*) ;;
  *) ON_PATH=0 ;;
esac

if [ "$ON_PATH" = 0 ]; then
  printf '\n%s is not on PATH. Add it:\n' "$INSTALL_DIR"
  case "${SHELL:-}" in
    */fish)
      printf '  fish_add_path %s\n' "$INSTALL_DIR"
      RC="$HOME/.config/fish/config.fish" ;;
    *)
      printf '  export PATH="%s:$PATH"\n' "$INSTALL_DIR"
      RC="$HOME/.profile"
      case "${SHELL:-}" in
        */zsh) RC="$HOME/.zshrc" ;;
        */bash) RC="$HOME/.bashrc" ;;
      esac ;;
  esac
  printf 'Keep that line in %s to use ocm-node in new shells.\n' "$RC"
fi

BIN="ocm-node"
if [ "$ON_PATH" = 0 ]; then BIN="$TARGET"; fi
printf '\nnext:\n  %s --coordinator https://your-coordinator --name "%s"\n' "$BIN" "$(hostname 2>/dev/null || echo my-laptop)"
printf 'It asks for the one-time token from Machines -> Share my compute.\n'
