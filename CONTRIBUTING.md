# Contributing

1. Set up: `uv sync`, then `scripts/build_web.sh` (or `make install web`). To work on the optional integrations, install their extras together: `uv sync --extra tiger --extra sentry` (`uv sync` drops extras you do not name).
2. Develop: `scripts/dev.sh` runs the coordinator on :8000 and the Vite dev server on :5173.
3. Test: `uv run pytest -q` must pass. The real-Ollama test skips when Ollama or `qwen2.5:1.5b` is missing; run it locally when you touch `ocm/runtime.py` or `ocm/metrics.py`. `tests/test_timeseries.py` skips without `psycopg` or a reachable TimescaleDB; run it against one (default `postgresql://postgres:ocmlocal@127.0.0.1:55432/ocm`, see the runbook, or set `OCM_TEST_TIMESCALE_URL`) when you touch `ocm/timeseries.py`. The Rust agent: `cargo test --manifest-path crates/ocm-node/Cargo.toml`.
4. Keep changes small and match the existing style. New behaviour needs a test in `tests/`.
5. Metrics and accounting must stay honest: never estimate a value the runtime did not report, and never edit ledger rows (append a reversal instead).
6. Do not log or persist prompts or outputs. That includes the optional integrations: the TimescaleDB sink writes only the metric columns in `COLUMNS` (`ocm/timeseries.py`), and anything that could reach Sentry must pass the scrubbers in `ocm/observability.py` (add a canary to `tests/test_observability.py` for any new secret shape). Runtime adapters must report only what the runtime reports, and API keys (such as the Gemini key) must stay in headers, never in URLs, logs or coordinator frames.
7. Update `README.md` and `docs/` when you change endpoints, settings, wire messages or the job state machine.

By contributing you agree that your contributions are licensed under the Apache License 2.0.
