# Security policy

OpenComputeMesh v0.1 is a demo for trusted networks. Read the security model and limitations in the
[README](README.md#security-model) before running it anywhere public.

## Reporting a vulnerability

Please report vulnerabilities privately through GitHub's
[private vulnerability reporting](https://github.com/roshaninfordham/opencomputemesh/security/advisories/new)
rather than a public issue. Include the affected component (coordinator, Python agent, Rust agent, web app),
steps to reproduce, and the impact you expect.

## In scope

- Ways for a browser or node to act outside its own network, member, node or job
- Ledger manipulation: double settlement, unbalanced transfers, edited history
- Reaching a participant's Ollama runtime from anywhere but their own loopback agent
- Leaks of a node's Gemini API key (it must travel only in the `x-goog-api-key` header to Google)
- Prompts, outputs, cookies, tokens or join codes reaching Sentry or the TimescaleDB metrics sink. Sentry events are scrubbed in `ocm/observability.py` (`send_default_pii=False`, no request bodies, local variables, cookies or query strings, sensitive headers and token shapes redacted); the sink stores metric columns only.
- Leaks of prompts, outputs, session cookies, enrollment tokens, node credentials or the admin token

## Known and documented, not vulnerabilities

- Node owners can see the prompts their machine processes
- A compromised worker can return wrong output or misreport usage. Settlement is capped at the
  pre-agreed hold, and the cap is shown on the receipt.
- Quick Tunnel URLs are public to anyone who has them; the join code is the only gate
- A node started with `--runtime gemini` sends the prompts routed to it to Google's Gemini API. This is opt-in, announced by the agent at startup, and disclosed in the README
- Sentry and TimescaleDB are off unless `SENTRY_DSN` or `OCM_TIMESCALE_URL` is set; when on, their operators can see the scrubbed error data and job metrics (ids, models, token counts, latencies)
