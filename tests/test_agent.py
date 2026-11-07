"""Python node agent units (no network, no Ollama)."""

import asyncio

import ocm.agent

import pytest

from ocm.agent import Agent, build_hello, is_loopback_url, prompt_token, runtime_scope
from ocm.runtime import OllamaAdapter, RuntimeResult


def no_prompt():
    raise AssertionError("must not prompt")


def test_enrollment_token_survives_failed_attempt():
    hello, token = build_hello({}, "ocme_t", no_prompt)
    assert hello == {"type": "node.enroll", "enrollment_token": "ocme_t"} and token == "ocme_t"
    # The connect failed before node.registered: the next attempt reuses the same token without prompting.
    assert build_hello({}, token, no_prompt)[0]["enrollment_token"] == "ocme_t"
    # A prompted token is remembered for the next attempt too.
    hello, token = build_hello({}, None, lambda: " typed ")
    assert hello["enrollment_token"] == "typed" and build_hello({}, token, no_prompt)[0]["enrollment_token"] == "typed"
    with pytest.raises(SystemExit):  # no token, no tty: the real prompt refuses
        build_hello({}, None, prompt_token)
    # A saved credential wins over any token.
    assert build_hello({"node_id": "n", "credential": "c"}, "ocme_t", no_prompt)[0] == {
        "type": "node.auth", "node_id": "n", "credential": "c"}


def test_ollama_client_ignores_proxy_env(monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", "http://10.9.9.9:1")
    monkeypatch.setenv("HTTPS_PROXY", "http://10.9.9.9:1")
    client = OllamaAdapter().client
    assert client.trust_env is False and not client._mounts  # no proxy transport mounted


@pytest.mark.parametrize("url,ok", [
    ("http://127.0.0.1:11434", True), ("http://localhost:11434", True), ("http://[::1]:11434", True),
    ("https://localhost", True), ("http://LOCALHOST:11434/", True),
    ("http://127.0.0.1:11434@evil.com", False), ("http://u:p@localhost:11434", False),
    ("http://127.0.0.1.evil.com:11434", False), ("http://localhost.evil.com", False), ("http://10.0.0.5:11434", False),
    ("ftp://localhost", False), ("127.0.0.1:11434", False), ("http://[::1", False), ("", False),
])
def test_loopback_url(url, ok):
    assert is_loopback_url(url) is ok


def test_a_remote_runtime_needs_an_explicit_opt_in():
    # The default stays what it always was: the runtime is on this machine.
    assert runtime_scope("http://127.0.0.1:11434", False) == "local"
    assert runtime_scope("http://[::1]:11434", True) == "local"
    with pytest.raises(SystemExit):
        runtime_scope("http://192.168.1.50:11434", False)
    # Opted in: still only a plain http(s) URL with no embedded credentials.
    assert runtime_scope("http://192.168.1.50:11434", True) == "remote"
    for bad in ("http://127.0.0.1@evil.com:11434", "http://user:pw@box:11434", "ftp://box:21", "not a url"):
        with pytest.raises(SystemExit):
            runtime_scope(bad, True)


class FakeRuntime:
    """Stands in for a remote Ollama: it answers or it does not."""
    name = "ollama"

    def __init__(self, ok: bool = True):
        self.ok, self.calls = ok, 0

    async def health(self):
        return "0.0.test"

    async def list_models(self):
        return []

    async def generate_stream(self, req, on_token, cancel):
        self.calls += 1
        return RuntimeResult(ok=self.ok, final_stats={"eval_count": 1}, error=None if self.ok else "refused")


def agent_with(tmp_path, runtime, scope="remote") -> Agent:
    a = Agent("http://127.0.0.1:1", tmp_path, runtime, None, None, 1, 20, runtime_scope=scope)
    a.state["approved_models"] = ["qwen2.5:1.5b"]
    return a


def test_probe_asks_the_remote_runtime_to_generate_once(tmp_path):
    rt = FakeRuntime()
    asyncio.run(agent_with(tmp_path, rt).probe())
    assert rt.calls == 1


def test_probe_refuses_a_runtime_that_is_up_but_serves_nothing(tmp_path):
    rt = FakeRuntime(ok=False)
    with pytest.raises(SystemExit):
        asyncio.run(agent_with(tmp_path, rt).probe())
    assert rt.calls == 1


def test_capabilities_announce_the_scope(tmp_path):
    caps = asyncio.run(agent_with(tmp_path, FakeRuntime()).capabilities())
    assert caps["runtime_scope"] == "remote"
    assert asyncio.run(agent_with(tmp_path, FakeRuntime(), scope="local").capabilities())["runtime_scope"] == "local"


def test_an_older_agent_that_sends_no_scope_is_treated_as_local():
    from ocm.protocol import Capabilities

    assert Capabilities(display_name="node").runtime_scope == "local"


def test_gpu_inventory_reports_cuda_details(monkeypatch):
    rows = "NVIDIA GeForce RTX 4090, 24564, 8.9, 550.54.15\n"
    monkeypatch.setattr(ocm.agent, "_run", lambda cmd: rows if cmd[0] == "nvidia-smi" else None)
    (gpu,) = ocm.agent.gpu_inventory()
    assert gpu["name"] == "NVIDIA GeForce RTX 4090"
    assert gpu["memory_total_bytes"] == 24564 * 1024 * 1024
    assert gpu["cuda_compute_cap"] == "8.9"
    assert gpu["driver_version"] == "550.54.15"
    assert gpu["source"] == "nvidia-smi"


def test_gpu_inventory_survives_short_rows(monkeypatch):
    monkeypatch.setattr(ocm.agent, "_run", lambda cmd: "Some Card, 8192\n" if cmd[0] == "nvidia-smi" else None)
    (gpu,) = ocm.agent.gpu_inventory()
    assert gpu["memory_total_bytes"] == 8192 * 1024 * 1024
    assert gpu["cuda_compute_cap"] is None and gpu["driver_version"] is None
