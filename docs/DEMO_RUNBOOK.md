# Demo runbook

This runbook is for the person hosting a live demo. The demo host machine runs the coordinator, the tunnel and one node agent. Audience members join from their phones or laptops through the tunnel URL, and optionally run their own agent.

Use `qwen2.5:1.5b`. With Ollama 0.17.7, `qwen3:4b` ignores `think: false` and prints its reasoning in the answer.

## 1. Host preflight

Run these in the repository root about 30 minutes before the demo.

**Ollama is running and has the demo model.**

```bash
curl -s http://127.0.0.1:11434/api/version
```

Expected output: `{"version":"0.17.7"}` (or your version). If the connection is refused, run `ollama serve`.

```bash
ollama list
```

Expected output: `qwen2.5:1.5b` appears in the list. If it doesn't, run `ollama pull qwen2.5:1.5b`.

**Dependencies are installed and the web app is built.**

```bash
uv sync
scripts/build_web.sh
```

Expected output: `Built .../web/dist` at the end.

**Start the coordinator** (terminal 1). Leave it running.

```bash
uv run ocm serve
```

Expected output: a line `Join path: /join/<code>`, then `Uvicorn running on http://127.0.0.1:8000`.

The default data directory is `./data`. For a fresh ledger, point `OCM_DATA_DIR` and `OCM_DATABASE_PATH` at a new directory. Use the same variables in every terminal that runs `ocm` commands.

**Run the preflight script** (terminal 2):

```bash
scripts/demo_preflight.sh
```

Expected output: one `PASS` or `FAIL` line per step, then `Preflight: all steps PASSED`. The steps are:

1. Ollama version
2. installed models
3. `uv sync`
4. web build
5. `/healthz`
6. `/readyz`
7. `uv run pytest -q`
8. `uv run ocm check-ledger`

The script starts nothing permanent. It needs the coordinator from the previous step running on `$OCM_PORT` (default 8000). The real-Ollama test shows as skipped, not failed, if the model is missing.

**Join and enroll the host node.**

A browser session belongs to one origin. Joining at `http://127.0.0.1:8000` and again at the tunnel URL creates two different members, and only the member that created the enrollment token owns the node. Only the owner can pause, resume, revoke or refresh the node, see its contribution under "My usage", or run benchmarks against it. Pick one origin for the host and use it for the whole demo:

- **Simplest:** do all host actions in a tab at `http://127.0.0.1:8000`, including pause/resume, usage and benchmarks. Use a second tab at the tunnel URL only to show the audience's view.
- **Alternative:** start the tunnel first (section 2), then join and create the token on the tunnel origin. The agent can still use `--coordinator http://127.0.0.1:8000`. The token ties the node to the member that created it, whichever URL the agent dials.

The steps below use the simplest option.

1. Open `http://127.0.0.1:8000/join/<code>` and join as `host`.
2. Go to Nodes, open "Share my compute", and click "Create token".
3. In terminal 3, run:

   ```bash
   uv run ocm-agent --coordinator http://127.0.0.1:8000 --models qwen2.5:1.5b --name host-mac
   ```

4. Paste the token at the hidden prompt.

Expected output:

```
[ocm-agent] enrolled as node <uuid>; credential saved to ~/.opencomputemesh/node.json (0600)
[ocm-agent] online; sharing qwen2.5:1.5b
```

**Run one prompt as a smoke test.** Go to "Run a model", choose `qwen2.5:1.5b`, and send "Say hi in five words." Check that the receipt shows runtime-reported tokens and a settled transfer. Then run:

```bash
uv run ocm check-ledger
```

Expected output: JSON ending in `"ok": true`, and exit code 0.

## 2. Start the tunnel and get the URL

Terminal 4:

```bash
scripts/demo_tunnel.sh
```

The script does the following:

1. Checks that the local coordinator answers `/healthz`.
2. Starts `cloudflared tunnel --url http://localhost:${OCM_PORT:-8000}`.
3. Reads the `https://<random>.trycloudflare.com` URL from cloudflared's output.
4. Waits for `$URL/healthz` to answer. A new tunnel's DNS can take a few seconds.
5. Prints the join URL (`$URL/join/<code>`), the agent command, and a QR code in the terminal.

It keeps running until you press Ctrl-C, which stops cloudflared.

To do the same by hand:

```bash
cloudflared tunnel --url http://localhost:8000
# look for a line like:  |  https://some-random-words.trycloudflare.com  |
curl -s https://some-random-words.trycloudflare.com/healthz    # {"status":"ok"}
uv run ocm join-code
```

The tunnel URL is public while it runs. Anyone with the join link can join as a demo member.

## 3. Show the QR code

Show the terminal QR from `demo_tunnel.sh` on the projector, or display it full-screen. To print it again:

```bash
JOIN="https://<tunnel>.trycloudflare.com/join/$(uv run ocm join-code)"
uv run python -c "import segno,sys; segno.make(sys.argv[1]).terminal(compact=True)" "$JOIN"
```

You can also open the tunnel URL in a second host tab to show the audience's view. That tab is a separate member. Do owner actions (pause, resume, usage, benchmarks) in the tab where you enrolled the node.

## 4. Three-minute demo script

| Time | Action | Say |
|---|---|---|
| 0:00 | Show the Overview page and the QR code. | "This is a coordinator on my laptop, published through a temporary tunnel. Scan to join; no account is needed. You get 100 demo credits, which are not money." |
| 0:30 | Point at the member count going up. Open Nodes. | "Joining doesn't share your computer. Compute is shared only by running this agent next to Ollama. It connects out, opens no ports, and talks to Ollama on localhost." Show the host node with its CPU, memory, GPU source and the `qwen2.5:1.5b` digest. |
| 1:00 | Go to "Run a model". Choose `qwen2.5:1.5b` and send a short prompt. | "The coordinator picks one online worker that has this exact model and streams the tokens back as they are generated." |
| 1:30 | Open the receipt. | "Token counts come from Ollama itself and are labelled as such. Tokens per second is eval count over eval duration. These are runtime, worker and coordinator timings, plus what this browser saw. Nothing here is estimated." |
| 2:00 | Show the credits section and "My usage". | "A worst-case hold was taken first. After the run, I'm debited the measured cost and the node owner is credited exactly the same amount. The ledger is append-only." Optionally run `uv run ocm check-ledger` on screen. |
| 2:30 | If an audience member has enrolled a node, send another prompt and show it routed to their machine. Otherwise pause the host node (from the `127.0.0.1` tab that owns it) and show `MODEL_UNAVAILABLE`, then resume. | "One request runs on one machine. There is no sharding. The person whose machine runs my prompt can see it, so don't send secrets." |
| 3:00 | End. | Point at the Limitations section of the README. |

## 5. Troubleshooting

### Node shows offline

- Is the agent process still running? It prints `cannot reach coordinator ... retrying` while it backs off, for up to 60 seconds.
- Is Ollama up? Check with `curl -s http://127.0.0.1:11434/api/version`. If Ollama is unhealthy, the node shows `DEGRADED` instead of `ONLINE`.
- Was the node paused? Click Resume on the Nodes page.
- The node is shown `OFFLINE` 10 seconds (`OCM_NODE_OFFLINE_GRACE_SECONDS`) after its socket closes.
- The node is also swept offline if it sends no heartbeat for 60 seconds (`OCM_NODE_HEARTBEAT_TTL_SECONDS`), for example when the laptop sleeps.
- If the agent exits with "credential or enrollment token rejected" or "node revoked by owner", its saved credential has been deleted. Create a new token and enroll again.

### Model missing

The agent exits at startup in these cases:

- `Ollama has no models installed`: run `ollama pull qwen2.5:1.5b` on that machine, then start the agent again.
- `Not installed locally: <model>`: run `ollama pull <model>` on that machine, then start the agent again.

If you pull a model while the agent is running, click "Refresh models" for the node. The agent shares only models that are in its approved list. That list is saved in the state file the first time `--models` is passed, so pass `--models` again to change it.

### `MODEL_UNAVAILABLE`

No node that is `ONLINE`, sharing and connected advertises that exact model ID. The message lists the available models. Check the following:

- the model name is exact, including the tag (`qwen2.5:1.5b`, not `qwen2.5`)
- the node is not paused or degraded
- the agent was started with that model in `--models`

### `INSUFFICIENT_CREDITS`

The worst-case hold is larger than the balance. The hold is `ceil(chars/2)+16` per message plus 16 input tokens at 1 OCC per 1k, plus `max_tokens` at 3 OCC per 1k. Lower `max_tokens`, or start the next demo with a fresh database. There is no top-up endpoint. Grants are given once, on join.

### `BAD_ORIGIN`

A cookie-authenticated write came from an origin that is neither the request host nor in `OCM_ALLOWED_ORIGINS`. Usual causes:

- The page was opened at one address (for example `http://localhost:8000`) while requests went to another.
- A proxy changed the Host header and did not send `X-Forwarded-Host`.
- A dev server on a port that is not in `OCM_ALLOWED_ORIGINS`.

Open the app at the same URL it is served from: the tunnel URL for the audience, `http://127.0.0.1:8000` locally. Browser WebSockets rejected for the same reason close with code 4403.

### Tunnel URL changed

Quick Tunnel URLs change every time cloudflared restarts.

1. Rerun `scripts/demo_tunnel.sh` and share the new join URL and QR code.
2. Browsers must open the new URL and join again, because cookies are per origin.
3. Restart agents with the new `--coordinator`:

   ```bash
   uv run ocm-agent --coordinator https://<new>.trycloudflare.com
   ```

Rejoining creates a new member with a new grant. Nodes stay owned by the member that enrolled them on the old origin. After the change their rewards go to a balance that the owner can no longer see, and the owner can no longer pause or revoke them from the new session. For a long demo, keep the tunnel running. Otherwise, enroll the host node from a `127.0.0.1` session, which survives tunnel restarts.

The agent prints `coordinator URL changed; re-using saved node credential (tunnel URLs change on restart)` and reconnects as the same node. It needs no new token, and the approved models come from the saved state. Agents connected to `http://127.0.0.1:8000` on the host are not affected.
