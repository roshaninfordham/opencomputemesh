"""Gemini runtime adapter (httpx.MockTransport, no network).

Every response body below is a SYNTHETIC example of the documented Gemini REST format
(models.list, models.streamGenerateContent?alt=sse, google.rpc error envelope), not a captured response."""

import asyncio
import json

import httpx
import pytest

from ocm import agent
from ocm.runtime import GeminiAdapter, InferenceRequest, SSEDecoder, gemini_body

KEY = "test-key-not-real"


def sse(*events: dict) -> str:
    return "".join(f"data: {json.dumps(e)}\r\n\r\n" for e in events)


def chunk(text: str | None = None, finish: str | None = None, usage: dict | None = None) -> dict:
    cand = {"content": {"role": "model", "parts": [{"text": text}] if text else []}, "index": 0}
    if finish:
        cand["finishReason"] = finish
    ev = {"candidates": [cand], "modelVersion": "gemini-2.5-flash"}
    if usage:
        ev["usageMetadata"] = usage
    return ev


def adapter(handler) -> GeminiAdapter:
    return GeminiAdapter(KEY, transport=httpx.MockTransport(handler))


def run(a: GeminiAdapter, req: InferenceRequest | None = None, cancel: asyncio.Event | None = None):
    tokens: list[str] = []

    async def on_token(t):
        tokens.append(t)

    req = req or InferenceRequest(model_id="gemini-2.5-flash", messages=[{"role": "user", "content": "hi"}], max_tokens=64)
    res = asyncio.run(a.generate_stream(req, on_token, cancel or asyncio.Event()))
    return res, tokens


class SplitStream(httpx.AsyncByteStream):
    """Yields the body in fixed-size slices so events and lines are split mid-way."""

    def __init__(self, body: bytes, size: int):
        self.body, self.size = body, size

    async def __aiter__(self):
        for i in range(0, len(self.body), self.size):
            yield self.body[i:i + self.size]


def test_message_conversion():
    req = InferenceRequest(model_id="m", max_tokens=32, temperature=0.2, top_p=0.9, messages=[
        {"role": "system", "content": "Be brief."}, {"role": "user", "content": "Q1"},
        {"role": "assistant", "content": "A1"}, {"role": "user", "content": "Q2"}])
    assert gemini_body(req) == {
        "systemInstruction": {"parts": [{"text": "Be brief."}]},
        "contents": [{"role": "user", "parts": [{"text": "Q1"}]}, {"role": "model", "parts": [{"text": "A1"}]},
                     {"role": "user", "parts": [{"text": "Q2"}]}],
        "generationConfig": {"maxOutputTokens": 32, "temperature": 0.2, "topP": 0.9}}
    bare = gemini_body(InferenceRequest(model_id="m", max_tokens=8, messages=[{"role": "user", "content": "x"}]))
    assert "systemInstruction" not in bare and bare["generationConfig"] == {"maxOutputTokens": 8}


def test_sse_decoder_split_mid_line():
    body = sse({"a": 1}, {"b": "x\ny"}) + "data: {not json}\n\n"
    d = SSEDecoder()
    out = []
    for i in range(len(body)):
        out += d.feed(body[i])
    assert out == [{"a": 1}, {"b": "x\ny"}] and d.malformed == 1


@pytest.mark.parametrize("size", [1, 7, 10_000])
def test_stream_tokens_usage_and_thoughts(size):
    seen = {}
    usage = {"promptTokenCount": 9, "candidatesTokenCount": 4, "thoughtsTokenCount": 30, "totalTokenCount": 43}
    body = sse(chunk("Hel"), chunk("lo "), chunk("world", "STOP", usage)).encode()

    def handler(request: httpx.Request):
        seen["req"] = request
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=SplitStream(body, size))

    res, tokens = run(adapter(handler), InferenceRequest(
        model_id="gemini-2.5-flash", max_tokens=64,
        messages=[{"role": "system", "content": "S"}, {"role": "user", "content": "secret prompt"}]))
    assert res.ok and "".join(tokens) == "Hello world"
    # Only reported fields; no durations invented; thinking tokens kept apart from eval_count.
    assert res.final_stats == {"prompt_eval_count": 9, "eval_count": 4, "thoughts_count": 30, "done_reason": "stop"}
    assert res.worker_elapsed_ms is not None and res.worker_ttft_ms is not None
    r = seen["req"]
    assert r.url.path == "/v1beta/models/gemini-2.5-flash:streamGenerateContent"
    assert dict(r.url.params) == {"alt": "sse"}  # key is never in the query string
    assert r.headers["x-goog-api-key"] == KEY and KEY not in str(r.url)
    assert json.loads(r.content)["systemInstruction"] == {"parts": [{"text": "S"}]}


def test_max_tokens_maps_to_length_and_other_reasons_lowercase():
    def make(finish):
        return lambda req: httpx.Response(200, text=sse(chunk("x", finish, {"promptTokenCount": 1, "candidatesTokenCount": 1})))
    assert run(adapter(make("MAX_TOKENS")))[0].final_stats["done_reason"] == "length"
    assert run(adapter(make("SAFETY")))[0].final_stats["done_reason"] == "safety"


def test_http_error_returns_api_message_not_prompt():
    err = {"error": {"code": 400, "message": "API key not valid. Please pass a valid API key.", "status": "INVALID_ARGUMENT"}}
    res, tokens = run(adapter(lambda req: httpx.Response(400, json=err)), InferenceRequest(
        model_id="gemini-2.5-flash", max_tokens=8, messages=[{"role": "user", "content": "do-not-echo-me"}]))
    assert not res.ok and not tokens
    assert res.error == "runtime HTTP 400: API key not valid. Please pass a valid API key."
    assert "do-not-echo-me" not in res.error and KEY not in res.error
    long = run(adapter(lambda req: httpx.Response(500, json={"error": {"message": "z" * 1000}})))[0]
    assert len(long.error) < 240


def test_stream_without_finish_reason_fails():
    res, _ = run(adapter(lambda req: httpx.Response(200, text=sse(chunk("partial")))))
    assert not res.ok and "finish reason" in res.error


def test_cancellation_closes_stream():
    closed = asyncio.Event()

    class Endless(httpx.AsyncByteStream):
        async def __aiter__(self):
            while True:
                yield sse(chunk("t")).encode()
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
        a = adapter(lambda req: httpx.Response(200, stream=Endless()))
        req = InferenceRequest(model_id="gemini-2.5-flash", messages=[{"role": "user", "content": "hi"}], max_tokens=8)
        return await asyncio.wait_for(a.generate_stream(req, on_token, cancel), 5)

    res = asyncio.run(go())
    assert res.cancelled and not res.ok and len(tokens) == 3 and closed.is_set()


def test_list_models_and_health():
    models_page = {"models": [
        {"name": "models/gemini-2.5-flash", "version": "001", "supportedGenerationMethods": ["generateContent", "countTokens"]},
        {"name": "models/text-embedding-004", "version": "004", "supportedGenerationMethods": ["embedContent"]},
        {"name": "models/gemini-2.5-pro", "supportedGenerationMethods": ["generateContent"]}]}
    seen = []

    def handler(req):
        seen.append(req)
        return httpx.Response(200, json=models_page)

    a = adapter(handler)
    models = asyncio.run(a.list_models())
    assert [(m.model_id, m.digest, m.runtime, m.family) for m in models] == [
        ("gemini-2.5-flash", "001", "gemini", "gemini"), ("gemini-2.5-pro", None, "gemini", "gemini")]
    assert asyncio.run(a.health()) == "gemini-api-v1beta"
    assert all(r.headers["x-goog-api-key"] == KEY and "key" not in r.url.params for r in seen)
    assert asyncio.run(adapter(lambda req: httpx.Response(403, json={})).health()) is None


def test_agent_gemini_requires_key(monkeypatch, tmp_path):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    with pytest.raises(SystemExit) as e:
        agent.main(["--runtime", "gemini", "--coordinator", "http://localhost:1", "--state-dir", str(tmp_path)])
    assert "GEMINI_API_KEY" in str(e.value)


def test_agent_gemini_skips_loopback_check_and_announces_cloud(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("GEMINI_API_KEY", KEY)
    ran = {}

    async def fake_run_forever(self):
        ran["runtime"] = self.runtime

    monkeypatch.setattr(agent.Agent, "run_forever", fake_run_forever)
    agent.main(["--runtime", "gemini", "--ollama", "http://10.0.0.5:11434", "--coordinator", "http://localhost:1",
                "--state-dir", str(tmp_path)])
    assert isinstance(ran["runtime"], GeminiAdapter)
    out = capsys.readouterr().out
    assert "forwarded to Google's Gemini API" in out and KEY not in out
    assert "GEMINI_API_KEY" not in agent.os.environ  # popped: child processes never inherit it
