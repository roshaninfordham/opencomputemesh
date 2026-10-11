#!/usr/bin/env bash
# Coordinator on :8000 plus the Vite dev server on :5173 (which proxies /api, /v1, /healthz and /ws to :8000).
set -euo pipefail
cd "$(dirname "$0")/.."
[[ -d web/node_modules ]] || (cd web && npm install)

pids=()
cleanup() { kill "${pids[@]}" 2>/dev/null || true; wait 2>/dev/null || true; }
trap cleanup EXIT INT TERM

OCM_PORT=8000 uv run ocm serve & pids+=($!)
(cd web && exec npm run dev -- --port 5173 --strictPort) & pids+=($!)

echo "Coordinator: http://127.0.0.1:8000   Web (dev): http://localhost:5173   Ctrl-C stops both."
# Exit (and clean up) as soon as either child dies.
while kill -0 "${pids[0]}" 2>/dev/null && kill -0 "${pids[1]}" 2>/dev/null; do sleep 1; done
