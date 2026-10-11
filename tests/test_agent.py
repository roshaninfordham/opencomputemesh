"""Python node agent units (no network, no Ollama)."""

import pytest

from ocm.agent import build_hello, prompt_token
from ocm.runtime import OllamaAdapter


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
