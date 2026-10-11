"""Runs against a real local Ollama; skipped (not passed) when unavailable."""

import asyncio
import os

import httpx
import pytest

from ocm.metrics import normalize_ollama
from ocm.runtime import InferenceRequest, OllamaAdapter

MODEL = os.environ.get("OCM_SMOKE_MODEL", "qwen2.5:1.5b")


def _ollama_has_model() -> str | None:
    try:
        tags = httpx.get("http://127.0.0.1:11434/api/tags", timeout=2).json()
    except Exception:
        return "Ollama not reachable on 127.0.0.1:11434"
    return None if any(m["name"] == MODEL for m in tags.get("models", [])) else f"model {MODEL} not installed"


@pytest.mark.skipif(_ollama_has_model() is not None, reason=_ollama_has_model() or "")
async def test_real_stream_has_runtime_counters():
    a = OllamaAdapter()
    assert await a.health()
    assert MODEL in [m.model_id for m in await a.list_models()]
    tokens = []

    async def on_token(t):
        tokens.append(t)
    res = await a.generate_stream(InferenceRequest(MODEL, [{"role": "user", "content": "Count to three."}], 24),
                                  on_token, asyncio.Event())
    assert res.ok and "".join(tokens).strip()
    m = normalize_ollama(res.final_stats)
    assert m["token_count_source"] == "runtime_reported" and m["output_tokens"] <= 24
    assert m["tokens_per_second"] and res.worker_ttft_ms is not None
