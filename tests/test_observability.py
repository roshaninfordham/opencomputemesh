import json

import pytest

from ocm import observability as ob

ENROLL = "ocme_" + "A1b2C3d4E5f6G7h8"
NODE = "ocmn_" + "Zz9Yy8Xx7Ww6Vv5U"
APIKEY = "ocm_" + "k3yK3yK3yK3yK3y1"
LONG = "Qm9yZ0xvbmdTZWNyZXRfVmFsdWUtMTIzNDU2Nzg5MA"
PROMPT = "my private diagnosis prompt text"
JOIN = "joinsecret123"
SECRETS = [ENROLL, NODE, APIKEY, LONG, PROMPT, JOIN, "sessioncookieval", "ticketval99"]


def test_exception_and_request_scrubbed():
    ev = {
        "exception": {"values": [{"type": "ValueError", "value": f"bad token {ENROLL} and {NODE}",
                                  "stacktrace": {"frames": [{"function": "f", "vars": {"messages": PROMPT}}]}}]},
        "request": {"url": "https://h/ws/client?ticket=ticketval99", "query_string": "ticket=ticketval99",
                    "data": {"messages": [{"role": "user", "content": PROMPT}]}, "cookies": {"ocm_session": "sessioncookieval"},
                    "headers": {"Authorization": f"Bearer {APIKEY}", "Cookie": "ocm_session=sessioncookieval",
                                "X-Api-Key": LONG, "User-Agent": "curl"}},
        "logentry": {"message": f"key {APIKEY}"},
        "tags": {"t": LONG},
    }
    out = ob.scrub_event(ev)
    s = json.dumps(out)
    for x in SECRETS:
        assert x not in s
    assert "data" not in out["request"] and "cookies" not in out["request"]
    assert out["request"]["query_string"] == "" and "?" not in out["request"]["url"]
    assert out["request"]["headers"]["User-Agent"] == "curl"
    assert "vars" not in out["exception"]["values"][0]["stacktrace"]["frames"][0]


def test_breadcrumb_ticket_url():
    b = ob.scrub_breadcrumb({"category": "httplib", "message": f"GET /ws/client?ticket=ticketval99 {ENROLL}",
                             "data": {"url": f"http://h/join/{JOIN}?x=1", "http.query": "ticket=ticketval99"}})
    s = json.dumps(b)
    assert "ticketval99" not in s and JOIN not in s and ENROLL not in s
    assert b["data"]["url"] == "http://h/join/[redacted]"


def test_transaction_join_path():
    t = ob.scrub_event({"type": "transaction", "transaction": f"/join/{JOIN}",
                        "request": {"url": f"https://h/join/{JOIN}"}})
    assert t["transaction"] == "/join/[redacted]"
    assert t["request"]["url"] == "https://h/join/[redacted]"


def test_init_noop_without_dsn(monkeypatch):
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    assert ob.init_sentry() is False


def test_end_to_end_real_sdk(monkeypatch):
    sdk = pytest.importorskip("sentry_sdk")
    from sentry_sdk.transport import Transport

    sent = []

    class Mem(Transport):
        def capture_envelope(self, envelope):
            sent.append(json.dumps([envelope.headers, [i.payload.json for i in envelope.items]], default=str))

    monkeypatch.setenv("SENTRY_DSN", "https://pub@o0.ingest.sentry.io/1")
    orig = sdk.init
    monkeypatch.setattr(sdk, "init", lambda **kw: orig(transport=Mem(), **kw))
    try:
        assert ob.init_sentry() is True
        sdk.add_breadcrumb(message=f"GET /ws/client?ticket=ticketval99 {NODE}", data={"url": f"/join/{JOIN}"})
        sdk.set_tag("k", APIKEY)
        prompt_local = PROMPT  # noqa: F841 - must not leak via frame vars
        try:
            raise RuntimeError(f"enroll failed {ENROLL}; messages={LONG}")
        except RuntimeError as e:
            sdk.capture_exception(e)
        sdk.capture_message(f"hello {APIKEY} at /join/{JOIN}")
        sdk.flush()
    finally:
        sdk.get_client().close()
        sdk.get_global_scope().clear()
        sdk.get_isolation_scope().clear()
        sdk.get_current_scope().clear()
    assert len(sent) >= 2
    blob = "\n".join(sent)
    for x in SECRETS:
        assert x not in blob
    assert "[redacted]" in blob
