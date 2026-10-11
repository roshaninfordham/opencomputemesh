#!/usr/bin/env bash
# Publish the local coordinator through a temporary Cloudflare Quick Tunnel and print the join URL + QR.
# The URL is public while this runs. Ctrl-C stops the tunnel.
set -euo pipefail
cd "$(dirname "$0")/.."

PORT="${OCM_PORT:-8000}"
command -v cloudflared >/dev/null || { echo "cloudflared not found (brew install cloudflared)"; exit 1; }
curl -fsS --max-time 3 "http://127.0.0.1:${PORT}/healthz" >/dev/null \
  || { echo "No coordinator on http://127.0.0.1:${PORT}. Start it first: uv run ocm serve"; exit 1; }

log=$(mktemp -t ocm-tunnel.XXXXXX)
cloudflared tunnel --url "http://localhost:${PORT}" >"$log" 2>&1 &
pid=$!
trap 'kill "$pid" 2>/dev/null || true; wait "$pid" 2>/dev/null || true; rm -f "$log"' EXIT INT TERM

URL=""
for _ in $(seq 1 60); do
  URL=$(grep -Eo 'https://[a-z0-9-]+\.trycloudflare\.com' "$log" | head -n1 || true)
  [[ -n "$URL" ]] && break
  kill -0 "$pid" 2>/dev/null || { cat "$log"; echo "cloudflared exited"; exit 1; }
  sleep 1
done
[[ -n "$URL" ]] || { cat "$log"; echo "No trycloudflare.com URL after 60s"; exit 1; }
echo "Tunnel URL: $URL"

# DNS for a new Quick Tunnel can take a few seconds to propagate.
ok=""
for _ in $(seq 1 30); do
  if curl -fsS --max-time 5 "$URL/healthz" >/dev/null 2>&1; then ok=1; break; fi
  sleep 2
done
[[ -n "$ok" ]] && echo "Health check: PASS ($URL/healthz)" || echo "Health check: FAIL ($URL/healthz not reachable yet; keep waiting or check the log: $log)"

JOIN="$URL/join/$(uv run ocm join-code)"
echo
echo "Join URL: $JOIN"
echo "Agent:    uv run ocm-agent --coordinator $URL --models qwen2.5:1.5b --name <name>"
echo
uv run python -c "import segno,sys; segno.make(sys.argv[1]).terminal(compact=True)" "$JOIN"
echo
echo "Tunnel running (cloudflared pid $pid). Ctrl-C to stop."
wait "$pid"
