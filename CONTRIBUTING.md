# Contributing

1. Set up: `uv sync`, then `scripts/build_web.sh` (or `make install web`).
2. Develop: `scripts/dev.sh` runs the coordinator on :8000 and the Vite dev server on :5173.
3. Test: `uv run pytest -q` must pass. The real-Ollama test skips when Ollama or `qwen2.5:1.5b` is missing; run it locally when you touch `ocm/runtime.py` or `ocm/metrics.py`.
4. Keep changes small and match the existing style. New behaviour needs a test in `tests/`.
5. Metrics and accounting must stay honest: never estimate a value the runtime did not report, and never edit ledger rows (append a reversal instead).
6. Do not log or persist prompts or outputs.
7. Update `README.md` and `docs/` when you change endpoints, settings, wire messages or the job state machine.

By contributing you agree that your contributions are licensed under the Apache License 2.0.
