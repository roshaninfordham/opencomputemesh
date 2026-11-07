"""OpenAI-compatible runtime adapter (httpx.MockTransport, no network).

The bodies below are synthetic examples of the documented chat-completions stream format, not captured
responses. SIE, vLLM, LM Studio, llama.cpp's server and TGI all speak this shape, which is the point.
"""

import asyncio
import json

import httpx
import pytest

from ocm.runtime import InferenceRequest, OpenAICompatAdapter, openai_body

KEY = "test-key-not-real"


def event(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"


def chunk(text: str | None = None, finish: str | None = None, usage: dict | None = None) -> dict:
    body = {"id": "chatcmpl-1", "object": "chat.completion.chunk",
            "choices": [{"index": 0, "delta": ({"content": text} if text else {}), "finish_reason": finish}]}
    if usage:
        body["usage"] = usage
        body["choices"] = []  # the usage chunk arrives with no choices
    return body


DONE = "data: [DONE]\n\n"


def adapter(handler, **kw) -> OpenAICompatAdapter:
    return OpenAICompatAdapter(transport=httpx.MockTransport(handler), **kw)


def run(a: OpenAICompatAdapter, req: InferenceRequest | None = None, cancel: asyncio.Event | None = None):
    tokens: list[str] = []

    async def on_token(t):
        tokens.append(t)

    req = req or InferenceRequest(model_id="qwen3.6-27b", messages=[{"role": "user", "content": "hi"}], max_tokens=64)
    return asyncio.run(a.generate_stream(req, on_token, cancel or asyncio.Event())), tokens


class SplitStream(httpx.AsyncByteStream):
    def __init__(self, body: bytes, size: int):
        self.body, self.size = body, size

    async def __aiter__(self):
        for i in range(0, len(self.body), self.size):
            yield self.body[i:i + self.size]


def test_body_shape():
    req = InferenceRequest(model_id="m", max_tokens=32, temperature=0.2, top_p=0.9,
                           messages=[{"role": "user", "content": "x"}])
    assert openai_body(req) == {"model": "m", "messages": [{"role": "user", "content": "x"}], "stream": True,
                                "max_tokens": 32, "temperature": 0.2, "top_p": 0.9,
                                "stream_options": {"include_usage": True}}
    bare = openai_body(InferenceRequest(model_id="m", max_tokens=8, messages=[]), stream_options=False)
    assert "stream_options" not in bare and "temperature" not in bare


@pytest.mark.parametrize("size", [1, 5, 10_000])
def test_stream_tokens_usage_and_finish(size):
    usage = {"prompt_tokens": 11, "completion_tokens": 3, "total_tokens": 14}
    body = (event(chunk("Hel")) + event(chunk("lo")) + event(chunk(" world", "stop")) + event(chunk(usage=usage))
            + DONE).encode()

    def handler(request):
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=SplitStream(body, size))

    res, tokens = run(adapter(handler))
    assert res.ok and "".join(tokens) == "Hello world"
    assert res.final_stats == {"prompt_eval_count": 11, "eval_count": 3, "done_reason": "stop"}
    assert "eval_duration" not in res.final_stats  # no duration is invented
    assert res.worker_elapsed_ms is not None and res.worker_ttft_ms is not None


def test_stream_options_rejection_is_retried_once_without_it():
    seen = []

    def handler(request):
        body = json.loads(request.content)
        seen.append(body)
        if "stream_options" in body:
            return httpx.Response(400, json={"error": {"message": "unknown field `stream_options`"}})
        return httpx.Response(200, text=event(chunk("ok", "stop")) + DONE)

    res, tokens = run(adapter(handler))
    assert res.ok and tokens == ["ok"]
    assert len(seen) == 2 and "stream_options" not in seen[1]
    # the retry keeps everything else identical
    assert {k: v for k, v in seen[0].items() if k != "stream_options"} == seen[1]


def test_other_400_is_not_retried():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(400, json={"error": {"message": "model not loaded"}})

    res, tokens = run(adapter(handler))
    assert not res.ok and not tokens and len(calls) == 1
    assert res.error == "runtime HTTP 400: model not loaded"


def test_error_message_never_echoes_prompt():
    def handler(request):
        return httpx.Response(500, json={"error": {"message": "boom " + "z" * 500}})

    res, _ = run(adapter(handler), InferenceRequest(
        model_id="m", max_tokens=8, messages=[{"role": "user", "content": "do-not-echo-me"}]))
    assert not res.ok and "do-not-echo-me" not in res.error and len(res.error) < 240


def test_stream_without_finish_reason_fails():
    res, _ = run(adapter(lambda request: httpx.Response(200, text=event(chunk("partial")) + DONE)))
    assert not res.ok and "finish reason" in res.error


def test_cancellation_closes_stream():
    closed = asyncio.Event()

    class Endless(httpx.AsyncByteStream):
        async def __aiter__(self):
            while True:
                yield event(chunk("t")).encode()
                await asyncio.sleep(0)

        async def aclose(self):
            closed.set()

    cancel = asyncio.Event()
    tokens: list[str] = []

    async def on_token(t):
        tokens.append(t)
        if len(tokens) == 3:
            cancel.set()

    async def go():
        a = adapter(lambda request: httpx.Response(200, stream=Endless()))
        req = InferenceRequest(model_id="m", messages=[{"role": "user", "content": "hi"}], max_tokens=8)
        return await asyncio.wait_for(a.generate_stream(req, on_token, cancel), 5)

    res = asyncio.run(go())
    assert res.cancelled and not res.ok and len(tokens) == 3 and closed.is_set()


def test_list_models_and_health():
    page = {"object": "list", "data": [{"id": "qwen3.6-27b", "object": "model", "owned_by": "Qwen"},
                                       {"id": "BAAI/bge-m3", "owned_by": "BAAI"}]}
    a = adapter(lambda request: httpx.Response(200, json=page))
    models = asyncio.run(a.list_models())
    assert [(m.model_id, m.digest, m.runtime, m.family) for m in models] == [
        ("qwen3.6-27b", None, "openai", "Qwen"), ("BAAI/bge-m3", None, "openai", "BAAI")]
    assert asyncio.run(a.health()) == "openai-compatible"
    assert asyncio.run(adapter(lambda request: httpx.Response(503)).health()) is None


def test_api_key_travels_only_in_the_header():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"data": []})

    a = adapter(handler, api_key=KEY)
    asyncio.run(a.list_models())
    assert seen[0].headers["authorization"] == f"Bearer {KEY}"
    assert KEY not in str(seen[0].url)


def test_no_key_means_no_authorization_header():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"data": []})

    asyncio.run(adapter(handler).list_models())
    assert "authorization" not in seen[0].headers


def test_agent_builds_openai_adapter_without_a_key(monkeypatch, tmp_path):
    from ocm import agent as agent_mod

    ran = {}

    async def fake_run_forever(self):
        ran["runtime"] = self.runtime
        ran["scope"] = self.runtime_scope

    monkeypatch.setattr(agent_mod.Agent, "run_forever", fake_run_forever)
    monkeypatch.delenv("OCM_OPENAI_API_KEY", raising=False)
    agent_mod.main(["--runtime", "openai", "--base-url", "http://127.0.0.1:8080",
                    "--coordinator", "http://localhost:1", "--state-dir", str(tmp_path)])
    assert isinstance(ran["runtime"], OpenAICompatAdapter)
    assert ran["runtime"].name == "openai" and ran["scope"] == "local"


def test_agent_refuses_a_non_loopback_openai_server_without_opt_in(monkeypatch, tmp_path):
    from ocm import agent as agent_mod

    monkeypatch.delenv("OCM_ALLOW_REMOTE_RUNTIME", raising=False)
    with pytest.raises(SystemExit) as e:
        agent_mod.main(["--runtime", "openai", "--base-url", "http://10.0.0.5:8080",
                        "--coordinator", "http://localhost:1", "--state-dir", str(tmp_path)])
    assert "allow-remote-runtime" in str(e.value)
