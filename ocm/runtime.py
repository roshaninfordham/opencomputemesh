"""Runtime adapters. Routing logic only sees these normalized shapes, never Ollama JSON."""

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Protocol

import httpx


@dataclass
class RuntimeModel:
    model_id: str
    digest: str | None
    runtime: str = "ollama"
    family: str | None = None
    parameter_size: str | None = None
    quantization: str | None = None
    size_bytes: int | None = None


@dataclass
class InferenceRequest:
    model_id: str
    messages: list[dict]
    max_tokens: int
    temperature: float | None = None
    top_p: float | None = None


@dataclass
class RuntimeResult:
    ok: bool
    final_stats: dict = field(default_factory=dict)
    worker_elapsed_ms: int | None = None
    worker_ttft_ms: int | None = None
    error: str | None = None
    cancelled: bool = False


class RuntimeAdapter(Protocol):
    name: str

    async def health(self) -> str | None: ...  # version string, or None if unreachable
    async def list_models(self) -> list[RuntimeModel]: ...
    async def generate_stream(self, req: InferenceRequest, on_token: Callable[[str], Awaitable[None]],
                              cancel: asyncio.Event) -> RuntimeResult: ...


class NDJSONDecoder:
    """Incremental newline-delimited JSON. Malformed lines are counted and skipped, not fatal."""

    def __init__(self):
        self.buf = ""
        self.malformed = 0

    def feed(self, chunk: str) -> list[dict]:
        self.buf += chunk
        *lines, self.buf = self.buf.split("\n")
        out = []
        for line in lines:
            if not line.strip():
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                self.malformed += 1
        return out

    def flush(self) -> list[dict]:
        return self.feed("\n")


FINAL_STAT_KEYS = ("total_duration", "load_duration", "prompt_eval_count", "prompt_eval_duration",
                   "eval_count", "eval_duration", "done_reason")


class OllamaAdapter:
    name = "ollama"

    def __init__(self, base_url: str = "http://127.0.0.1:11434", timeout: float = 180):
        # trust_env=False: never route loopback runtime traffic (prompts, outputs) through HTTP(S)_PROXY.
        self.client = httpx.AsyncClient(base_url=base_url, timeout=httpx.Timeout(timeout, connect=5), trust_env=False)

    async def health(self) -> str | None:
        try:
            r = await self.client.get("/api/version")
            r.raise_for_status()
            return r.json().get("version")
        except (httpx.HTTPError, ValueError):
            return None

    async def list_models(self) -> list[RuntimeModel]:
        r = await self.client.get("/api/tags")
        r.raise_for_status()
        out = []
        for m in r.json().get("models", []):
            d = m.get("details") or {}
            out.append(RuntimeModel(model_id=m["name"], digest=m.get("digest"), family=d.get("family"),
                                    parameter_size=d.get("parameter_size"),
                                    quantization=d.get("quantization_level"), size_bytes=m.get("size")))
        return out

    async def generate_stream(self, req, on_token, cancel) -> RuntimeResult:
        options = {"num_predict": req.max_tokens}
        if req.temperature is not None:
            options["temperature"] = req.temperature
        if req.top_p is not None:
            options["top_p"] = req.top_p
        # think=False keeps reasoning models (qwen3, ...) from spending the budget on a hidden phase;
        # it is accepted and ignored by non-thinking models.
        body = {"model": req.model_id, "messages": req.messages, "stream": True, "think": False,
                "options": options}
        start = time.monotonic()
        ttft = None
        decoder = NDJSONDecoder()
        try:
            async with self.client.stream("POST", "/api/chat", json=body) as resp:
                if resp.status_code != 200:
                    detail = (await resp.aread()).decode(errors="replace")[:200]
                    return RuntimeResult(ok=False, error=f"runtime HTTP {resp.status_code}: {detail}")
                async for text in resp.aiter_text():
                    if cancel.is_set():
                        # Leaving the context closes the connection; Ollama aborts on client disconnect.
                        return RuntimeResult(ok=False, cancelled=True, error="cancelled",
                                             worker_elapsed_ms=round((time.monotonic() - start) * 1000))
                    for chunk in decoder.feed(text):
                        result = await self._handle(chunk, on_token, start, ttft)
                        if isinstance(result, RuntimeResult):
                            result.worker_ttft_ms = ttft
                            return result
                        ttft = result
                for chunk in decoder.flush():
                    result = await self._handle(chunk, on_token, start, ttft)
                    if isinstance(result, RuntimeResult):
                        result.worker_ttft_ms = ttft
                        return result
        except httpx.HTTPError as e:
            return RuntimeResult(ok=False, error=f"runtime connection error: {type(e).__name__}")
        return RuntimeResult(ok=False, error="runtime stream ended without final statistics")

    @staticmethod
    async def _handle(chunk, on_token, start, ttft):
        if "error" in chunk:
            return RuntimeResult(ok=False, error=f"runtime error: {str(chunk['error'])[:200]}")
        content = (chunk.get("message") or {}).get("content") or ""
        if content:
            if ttft is None:
                ttft = round((time.monotonic() - start) * 1000)
            await on_token(content)
        if chunk.get("done"):
            return RuntimeResult(ok=True, final_stats={k: chunk[k] for k in FINAL_STAT_KEYS if k in chunk},
                                 worker_elapsed_ms=round((time.monotonic() - start) * 1000))
        return ttft
