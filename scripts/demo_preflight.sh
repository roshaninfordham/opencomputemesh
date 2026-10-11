#!/usr/bin/env bash
# Pre-demo checks. Starts nothing permanent: expects a coordinator already running on $OCM_PORT
# with the same OCM_* environment (notably OCM_DATABASE_PATH) as this shell.
set -euo pipefail
cd "$(dirname "$0")/.."

PORT="${OCM_PORT:-8000}"
BASE="http://127.0.0.1:${PORT}"
OLLAMA="${OLLAMA_BASE_URL:-http://127.0.0.1:11434}"
failures=0

step() {  # step "<name>" <command...>
  local name=$1; shift
  echo "==> ${name}"
  if "$@"; then echo "PASS  ${name}"; else echo "FAIL  ${name}"; failures=$((failures + 1)); fi
  echo
}

ollama_version() { curl -fsS --max-time 3 "${OLLAMA}/api/version" && echo; }
ollama_models() {
  local tags; tags=$(curl -fsS --max-time 3 "${OLLAMA}/api/tags") || return 1
  uv run python -c 'import json,sys; m=[x["name"] for x in json.load(sys.stdin).get("models",[])]; print("\n".join(m) or "(no models installed)"); sys.exit(0 if m else 1)' <<<"$tags"
}
web_built() { [[ -f web/dist/index.html ]] || scripts/build_web.sh; }
healthz() { curl -fsS --max-time 3 "${BASE}/healthz" && echo; }
readyz() { curl -fsS --max-time 3 "${BASE}/readyz" && echo; }

step "Ollama reachable (${OLLAMA}/api/version)" ollama_version
step "Ollama models installed" ollama_models
step "uv sync" uv sync
step "web/dist built" web_built
step "Coordinator /healthz (${BASE})" healthz
step "Coordinator /readyz (${BASE})" readyz
step "Test suite (uv run pytest -q)" uv run pytest -q
step "Ledger invariants (uv run ocm check-ledger)" uv run ocm check-ledger

if (( failures )); then echo "Preflight: ${failures} step(s) FAILED"; exit 1; fi
echo "Preflight: all steps PASSED"
