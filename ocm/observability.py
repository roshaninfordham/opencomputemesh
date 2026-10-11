"""Optional Sentry error monitoring with privacy-first scrubbing.

Off unless SENTRY_DSN is set. Prompts, outputs, tokens, cookies, join codes and credentials must
never leave the machine: request bodies/cookies/query strings/frame vars are dropped, and known
secret shapes are redacted from every string in an event. The scrubbers are pure functions.
"""
import os
import re
from importlib.metadata import PackageNotFoundError, version

R = "[redacted]"
_SECRET = re.compile(r"\bocm[en]?_[A-Za-z0-9_\-]+|(?<![A-Za-z0-9_\-])[A-Za-z0-9_\-]{32,}(?![A-Za-z0-9_\-])")
_JOIN = re.compile(r"(/join/)[^/?#\s\"']+")
_QS_SECRET = re.compile(r"(?i)\b(ticket|token|code|key|api_key|secret)=[^&\s\"']+")
_SENSITIVE_HEADERS = {"authorization", "cookie", "set-cookie", "x-api-key"}
_DROP_KEYS = {"vars", "cookies", "data", "body"}  # "data": request bodies; breadcrumb data is walked separately
_QS_KEYS = {"query_string", "http.query"}


def scrub_str(s: str) -> str:
    s = _JOIN.sub(r"\1[redacted]", s)
    s = _QS_SECRET.sub(lambda m: f"{m.group(1)}={R}", s)
    return _SECRET.sub(R, s)


def _walk(o, parent: str = ""):
    if isinstance(o, str):
        return scrub_str(o)
    if isinstance(o, list):
        return [_walk(x, parent) for x in o]
    if isinstance(o, tuple):
        return tuple(_walk(x, parent) for x in o)
    if not isinstance(o, dict):
        return o
    out = {}
    for k, v in o.items():
        if k == "vars" or k == "cookies" or (k in ("data", "body") and parent == "request"):
            continue
        if k in _QS_KEYS:
            out[k] = ""
        elif k == "headers":
            out[k] = _headers(v)
        elif k == "url" and isinstance(v, str):
            out[k] = scrub_str(v.split("?", 1)[0].split("#", 1)[0])
        else:
            out[k] = _walk(v, k)
    return out


def _headers(h):
    pairs = h.items() if isinstance(h, dict) else h
    clean = [(k, R if str(k).lower() in _SENSITIVE_HEADERS else _walk(v)) for k, v in pairs]
    return dict(clean) if isinstance(h, dict) else [list(p) for p in clean]


def scrub_event(event, hint=None):
    return _walk(event)


def scrub_breadcrumb(crumb, hint=None):
    return _walk(crumb)


def init_sentry() -> bool:
    """Initialise Sentry iff SENTRY_DSN is set and sentry_sdk is importable. Returns whether enabled."""
    dsn = os.environ.get("SENTRY_DSN", "").strip()
    if not dsn:
        return False
    try:
        import sentry_sdk
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        from sentry_sdk.integrations.starlette import StarletteIntegration
    except ImportError:
        return False
    try:
        pkg = version("opencomputemesh")
    except PackageNotFoundError:
        pkg = "unknown"
    sentry_sdk.init(
        dsn=dsn,
        send_default_pii=False,
        include_local_variables=False,
        max_request_body_size="never",
        traces_sample_rate=float(os.environ.get("SENTRY_TRACES_SAMPLE_RATE", "0") or 0),
        environment=os.environ.get("OCM_ENV") or None,
        release=f"opencomputemesh@{pkg}",
        integrations=[StarletteIntegration(), FastApiIntegration()],
        before_send=scrub_event,
        before_send_transaction=scrub_event,
        before_breadcrumb=scrub_breadcrumb,
    )
    return True
