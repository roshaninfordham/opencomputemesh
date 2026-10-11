#!/usr/bin/env bash
# Build the React app into web/dist, which the coordinator serves same-origin.
set -euo pipefail
cd "$(dirname "$0")/../web"
if [[ -f package-lock.json ]]; then npm ci; else npm install; fi
npm run build
echo "Built $(pwd)/dist"
