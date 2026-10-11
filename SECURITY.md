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
- Leaks of prompts, outputs, session cookies, enrollment tokens, node credentials or the admin token

## Known and documented, not vulnerabilities

- Node owners can see the prompts their machine processes
- A compromised worker can return wrong output or misreport usage. Settlement is capped at the
  pre-agreed hold, and the cap is shown on the receipt.
- Quick Tunnel URLs are public to anyone who has them; the join code is the only gate
