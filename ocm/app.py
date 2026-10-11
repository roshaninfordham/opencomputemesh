"""Coordinator HTTP + WebSocket API. One process, one SQLite database."""

import asyncio
import hmac
import json
import logging
import os
import secrets
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, Query, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field, ValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import ledger
from .config import Settings
from .db import connect, dumps, insert, new_id, now, parse_ts, row, rows, sha256, update
from .hub import Conn, Hub, ServiceError
from .metrics import int_or_none
from .protocol import BenchmarkStart, ChatMessage, GenerationParams, NodeHello, RequestRef, RequestStart

log = logging.getLogger("ocm")
SESSION_COOKIE = "ocm_session"
NETWORK_ID = "demo"
MAX_OFFSET = 1_000_000
# connect-src 'self' covers same-origin ws:/wss: (CSP3); bare ws:/wss: would allow any host.
SECURITY_HEADERS = {
    "x-content-type-options": "nosniff", "referrer-policy": "no-referrer", "x-frame-options": "DENY",
    "content-security-policy": "default-src 'self'; img-src 'self' data:; font-src 'self' data:; connect-src 'self'; "
                               "object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'",
}


# ---------- small helpers ----------

class RateLimiter:
    # ponytail: in-process sliding window; move to a shared store if the coordinator ever scales out.
    def __init__(self):
        self.hits: dict[str, deque] = defaultdict(deque)
        self.max_window, self.swept = 0.0, time.monotonic()

    def allow(self, key: str, limit: int, window: float) -> bool:
        t = time.monotonic()
        self.max_window = max(self.max_window, window)
        if t - self.swept > 60:  # drop keys (e.g. one per client IP) with no hit inside any window
            self.swept = t
            for k in [k for k, q in self.hits.items() if not q or q[-1] <= t - self.max_window]:
                del self.hits[k]
        q = self.hits[key]
        while q and q[0] <= t - window:
            q.popleft()
        if len(q) >= limit:
            return False
        q.append(t)
        return True


def client_ip(scope_client, headers) -> str:
    peer = scope_client.host if scope_client else "unknown"
    # Only trust Cloudflare's header when the tunnel (on loopback) is the peer.
    if peer in ("127.0.0.1", "::1") and headers.get("cf-connecting-ip"):
        return headers["cf-connecting-ip"]
    return peer


def origin_ok(headers, settings: Settings) -> bool:
    origin = headers.get("origin")
    if not origin:
        return False
    if origin in [o.strip() for o in settings.allowed_origins.split(",") if o.strip()]:
        return True
    netloc = urlparse(origin).netloc
    return netloc in {headers.get("host"), headers.get("x-forwarded-host")} - {None}


def err(code: str, message: str, status: int, details: dict | None = None):
    raise ServiceError(code, message, status, details)


def resolve_join_code(s: Settings) -> str:
    if s.join_code:
        return s.join_code
    p = Path(s.data_dir) / "join_code"
    if p.exists():
        return p.read_text().strip()
    p.parent.mkdir(parents=True, exist_ok=True)
    code = secrets.token_urlsafe(6).replace("-", "x").replace("_", "y")
    p.write_text(code)
    os.chmod(p, 0o600)
    return code


# ---------- request bodies ----------

class SessionIn(BaseModel):
    nickname: str = Field(min_length=1, max_length=32)
    join_code: str = Field(min_length=1, max_length=64)


class JobIn(GenerationParams):
    idempotency_key: str = Field(min_length=8, max_length=64)
    node_id: str | None = None


class BenchmarkIn(GenerationParams):
    idempotency_key: str = Field(min_length=8, max_length=64)


class PricingIn(BaseModel):
    local_model_id: str
    provider: str = Field(max_length=64)
    provider_model_id: str = Field(max_length=128)
    input_usd_per_million_tokens: str
    output_usd_per_million_tokens: str
    source_url: str = Field(pattern=r"^https://")
    source_checked_at: str = Field(min_length=10)
    notes: str | None = None


class ChatCompletionIn(BaseModel):
    model: str
    messages: list[ChatMessage]
    temperature: float | None = None
    top_p: float | None = None
    max_tokens: int | None = None
    max_completion_tokens: int | None = None
    stream: bool = False


def create_app(settings: Settings | None = None) -> FastAPI:
    s = settings or Settings.from_env()
    db = connect(s.database_path)
    hub = Hub(db, s)
    limiter = RateLimiter()
    tickets: dict[str, tuple[str, float]] = {}
    join_code = resolve_join_code(s)

    with db:
        if not row(db, "SELECT id FROM networks WHERE id = ?", NETWORK_ID):
            insert(db, "networks", {"id": NETWORK_ID, "name": s.network_name, "join_code_hash": sha256(join_code),
                                    "created_at": now(), "policy_json": dumps({"members_may_use_all_nodes": True})})
        update(db, "networks", NETWORK_ID, {"join_code_hash": sha256(join_code), "name": s.network_name})
        version = f"t-{s.default_input_occ_per_1k}-{s.default_output_occ_per_1k}-{s.min_job_charge_micro_occ}"
        if not row(db, "SELECT version FROM tariffs WHERE version = ?", version):
            insert(db, "tariffs", {"version": version, "network_id": NETWORK_ID,
                                   "input_micro_per_1k": s.default_input_occ_per_1k * ledger.MICRO,
                                   "output_micro_per_1k": s.default_output_occ_per_1k * ledger.MICRO,
                                   "min_charge_micro": s.min_job_charge_micro_occ, "active_at": now()})
    hub.recover_after_restart()

    @asynccontextmanager
    async def lifespan(app):
        sweeper = asyncio.create_task(hub.sweep_forever())
        log.info("coordinator ready; join path /join/<code> (code in %s/join_code or OCM_JOIN_CODE)", s.data_dir)
        yield
        sweeper.cancel()

    app = FastAPI(title="OpenComputeMesh Coordinator", version="0.1.0", lifespan=lifespan)
    app.state.hub, app.state.db, app.state.settings, app.state.join_code = hub, db, s, join_code

    # ---------- errors & request ids ----------

    @app.middleware("http")
    async def request_id_mw(request: Request, call_next):
        request.state.request_id = request.headers.get("x-request-id", "")[:64] or new_id()
        try:
            resp = await call_next(request)
        except Exception:
            log.exception("unhandled error on %s %s", request.method, request.url.path)
            resp = JSONResponse({"error": {"code": "INTERNAL_ERROR", "message": "Internal server error.",
                                           "request_id": request.state.request_id}}, status_code=500)
        resp.headers["x-request-id"] = request.state.request_id
        resp.headers.update(SECURITY_HEADERS)
        return resp

    def envelope(request: Request, code, message, status, details=None):
        body = {"error": {"code": code, "message": message, "request_id": getattr(request.state, "request_id", None)}}
        if details:
            body["error"]["details"] = details
        return JSONResponse(body, status_code=status)

    @app.exception_handler(ServiceError)
    async def service_error(request: Request, e: ServiceError):
        return envelope(request, e.code, e.message, e.status, e.details)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, e: RequestValidationError):
        details = [{"loc": list(x["loc"]), "msg": x["msg"]} for x in e.errors()]
        return envelope(request, "INVALID_REQUEST", "Request validation failed.", 422, {"errors": details})

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, e: StarletteHTTPException):
        return envelope(request, "NOT_FOUND" if e.status_code == 404 else "HTTP_ERROR", str(e.detail), e.status_code)

    # ---------- auth dependencies ----------

    def member_from_token(token: str | None) -> dict | None:
        if not token:
            return None
        return row(db, "SELECT * FROM members WHERE session_hash = ? AND status = 'active'", sha256(token))

    async def current_member(request: Request) -> dict:
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            m = row(db, "SELECT * FROM members WHERE api_key_hash = ? AND status = 'active'", sha256(auth[7:].strip()))
            if not m:
                err("UNAUTHENTICATED", "Invalid API key.", 401)
            return m
        m = member_from_token(request.cookies.get(SESSION_COOKIE))
        if not m:
            err("UNAUTHENTICATED", "Join the network first.", 401)
        # CSRF: cookie-authenticated writes must come from our own origin.
        if request.method not in ("GET", "HEAD", "OPTIONS") and not origin_ok(request.headers, s):
            err("BAD_ORIGIN", "Cross-origin request rejected.", 403)
        return m

    def require_admin(request: Request) -> None:
        auth = request.headers.get("authorization", "")
        if not s.admin_token or not hmac.compare_digest(auth.encode(), f"Bearer {s.admin_token}".encode()):
            err("FORBIDDEN", "Admin token required.", 403)

    def limit(key: str, n: int, window: float):
        if not limiter.allow(key, n, window):
            err("RATE_LIMITED", "Too many requests. Slow down.", 429)

    def my_node(member: dict, node_id: str) -> dict:
        n = row(db, "SELECT * FROM nodes WHERE id = ? AND network_id = ?", node_id, member["network_id"])
        if not n:
            err("NOT_FOUND", "Node not found.", 404)
        if n["owner_member_id"] != member["id"]:
            err("FORBIDDEN", "Only the node owner can do that.", 403)
        return n

    def visible_job(member: dict, job_id: str) -> dict:
        j = row(db, "SELECT * FROM jobs WHERE id = ? AND network_id = ?", job_id, member["network_id"])
        if not j or member["id"] not in (j["requester_member_id"], j["provider_member_id"]):
            err("NOT_FOUND", "Request not found.", 404)
        return j

    def member_view(m: dict) -> dict:
        return {"id": m["id"], "nickname": m["nickname"], "network_id": m["network_id"], "created_at": m["created_at"],
                "session_kind": m["session_kind"], "balance_micro_occ": ledger.balance(db, m["id"]),
                "has_api_key": bool(m["api_key_hash"])}

    # ---------- health ----------

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    @app.get("/readyz")
    async def readyz():
        db.execute("SELECT 1 FROM networks LIMIT 1").fetchone()
        return {"status": "ready", "online_nodes": len(hub.nodes), "time": now()}

    # ---------- session / network ----------

    @app.post("/api/v1/session")
    async def create_session(body: SessionIn, request: Request, response: Response):
        if not origin_ok(request.headers, s):
            err("BAD_ORIGIN", "Cross-origin request rejected.", 403)
        limit(f"join:{client_ip(request.client, request.headers)}", 10, 60)
        net = row(db, "SELECT * FROM networks WHERE id = ?", NETWORK_ID)
        if not hmac.compare_digest(sha256(body.join_code.strip()), net["join_code_hash"]):
            err("BAD_JOIN_CODE", "That join code is not valid for this network.", 403)
        nickname = " ".join(body.nickname.split())
        if not nickname or not nickname.isprintable():
            err("INVALID_NICKNAME", "Choose a nickname with visible characters.", 422)
        existing = member_from_token(request.cookies.get(SESSION_COOKIE))
        if existing and existing["network_id"] == NETWORK_ID:
            return {"member": member_view(existing), "restored": True}
        token = secrets.token_urlsafe(32)
        m = {"id": new_id(), "network_id": NETWORK_ID, "nickname": nickname, "created_at": now(), "session_hash": sha256(token)}
        with db:
            insert(db, "members", m)
            if s.grant_micro:
                ledger.grant(db, NETWORK_ID, m["id"], s.grant_micro, "demo_join")
        response.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="lax", max_age=7 * 86400,
                            secure=request.url.scheme == "https", path="/")
        return {"member": member_view(row(db, "SELECT * FROM members WHERE id = ?", m["id"])), "restored": False}

    @app.get("/api/v1/session")
    async def get_session(request: Request):
        m = member_from_token(request.cookies.get(SESSION_COOKIE))
        return {"member": member_view(m) if m else None}

    @app.post("/api/v1/session/leave")
    async def leave(response: Response, member=Depends(current_member)):
        with db:
            update(db, "members", member["id"], {"session_hash": None})
        response.delete_cookie(SESSION_COOKIE, path="/")
        return {"ok": True}

    @app.post("/api/v1/session/api-key")
    async def rotate_api_key(member=Depends(current_member)):
        key = "ocm_" + secrets.token_urlsafe(32)
        with db:
            update(db, "members", member["id"], {"api_key_hash": sha256(key)})
        return {"api_key": key, "note": "Shown once. Use as `Authorization: Bearer <key>` for /v1/chat/completions."}

    @app.get("/api/v1/network")
    async def network(request: Request):
        m = member_from_token(request.cookies.get(SESSION_COOKIE))
        snap = hub.network_snapshot(NETWORK_ID, m["id"] if m else None)
        if not m:  # public landing view: counts and model names only
            return {k: snap[k] for k in ("name", "online_node_count", "member_count", "demo_mode")} | {
                "models": [{"model_id": x["model_id"], "worker_count": x["worker_count"]} for x in snap["models"]]}
        return snap

    @app.get("/api/v1/network/join-info")
    async def join_info(member=Depends(current_member)):
        return {"join_code": join_code, "join_path": f"/join/{join_code}",
                "note": "Anyone with this link can join as a demo member. Share it only with the audience."}

    @app.post("/api/v1/ws-ticket")
    async def ws_ticket(member=Depends(current_member)):
        t = secrets.token_urlsafe(24)
        tickets[t] = (member["id"], time.monotonic() + 60)
        for k, (_, exp) in list(tickets.items()):
            if exp < time.monotonic():
                tickets.pop(k, None)
        return {"ticket": t, "expires_in_seconds": 60}

    # ---------- nodes ----------

    @app.post("/api/v1/nodes/enrollment-tokens")
    async def enrollment_token(request: Request, member=Depends(current_member)):
        limit(f"enroll:{member['id']}", 5, 600)
        token = "ocme_" + secrets.token_urlsafe(24)
        exp = parse_ts(now()).timestamp() + s.enrollment_token_ttl_seconds
        from datetime import datetime, timezone
        expires_at = datetime.fromtimestamp(exp, timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        with db:
            insert(db, "enrollment_tokens", {"token_hash": sha256(token), "network_id": member["network_id"],
                                             "member_id": member["id"], "created_at": now(), "expires_at": expires_at})
        return {"enrollment_token": token, "expires_at": expires_at, "single_use": True}

    @app.get("/api/v1/nodes")
    async def list_nodes(member=Depends(current_member)):
        return {"nodes": [hub.public_node(n, member["id"]) for n in
                          rows(db, "SELECT * FROM nodes WHERE network_id = ? ORDER BY created_at", member["network_id"])]}

    @app.get("/api/v1/nodes/{node_id}")
    async def get_node(node_id: str, member=Depends(current_member)):
        n = row(db, "SELECT * FROM nodes WHERE id = ? AND network_id = ?", node_id, member["network_id"])
        if not n:
            err("NOT_FOUND", "Node not found.", 404)
        stats = row(db, """SELECT COUNT(*) AS jobs, SUM(status='COMPLETED') AS completed,
                           SUM(COALESCE(worker_elapsed_ms,0)) AS worker_ms, SUM(COALESCE(input_tokens,0)) AS input_tokens,
                           SUM(COALESCE(output_tokens,0)) AS output_tokens FROM jobs WHERE selected_node_id = ?""", node_id)
        return {"node": hub.public_node(n, member["id"]), "stats": stats}

    async def set_sharing(member, node_id: str, enabled: bool):
        n = my_node(member, node_id)
        if n["revoked_at"]:
            err("REVOKED", "This node has been revoked.", 409)
        with db:
            update(db, "nodes", node_id, {"sharing_enabled": int(enabled)})
            n = row(db, "SELECT * FROM nodes WHERE id = ?", node_id)
            if n["status"] in ("ONLINE", "PAUSED"):
                update(db, "nodes", node_id, {"status": "ONLINE" if enabled else "PAUSED"})
        conn = hub.nodes.get(node_id)
        if conn:
            await conn.send("node.sharing", enabled=enabled)
        await hub.notify_capacity()
        await hub.broadcast_snapshot(member["network_id"])
        return {"node": hub.public_node(row(db, "SELECT * FROM nodes WHERE id = ?", node_id), member["id"])}

    @app.post("/api/v1/nodes/{node_id}/pause")
    async def pause(node_id: str, member=Depends(current_member)):
        return await set_sharing(member, node_id, False)

    @app.post("/api/v1/nodes/{node_id}/resume")
    async def resume(node_id: str, member=Depends(current_member)):
        return await set_sharing(member, node_id, True)

    @app.delete("/api/v1/nodes/{node_id}")
    async def revoke(node_id: str, member=Depends(current_member)):
        my_node(member, node_id)
        with db:
            update(db, "nodes", node_id, {"revoked_at": now(), "status": "REVOKED", "sharing_enabled": 0,
                                          "credential_hash": "revoked:" + secrets.token_hex(8)})
        conn = hub.nodes.get(node_id)
        if conn:
            await conn.send("node.revoked")
            await conn.ws.close(code=4003, reason="revoked")
        await hub.broadcast_snapshot(member["network_id"])
        return {"ok": True}

    @app.post("/api/v1/nodes/{node_id}/refresh-models")
    async def refresh_models(node_id: str, member=Depends(current_member)):
        my_node(member, node_id)
        conn = hub.nodes.get(node_id)
        if not conn:
            err("NODE_OFFLINE", "Node is not connected.", 409)
        await conn.send("model.refresh")
        return {"ok": True}

    # ---------- models / jobs ----------

    @app.get("/api/v1/models")
    async def models(member=Depends(current_member)):
        return {"models": hub.available_models(member["network_id"])}

    async def run_blocking(member, params: GenerationParams, key: str, node_id=None):
        job, task = await hub.submit(member, params, key, pin_node=node_id, stream_to_member=False)
        result = await task if task else job
        return result

    @app.post("/api/v1/jobs")
    async def create_job(body: JobIn, request: Request, member=Depends(current_member)):
        limit(f"req:{member['id']}", 20, 60)
        result = await run_blocking(member, body, body.idempotency_key, body.node_id)
        return {"receipt": hub.receipt(result["id"]), "output": result.get("_output")}

    @app.get("/api/v1/jobs")
    async def list_jobs(member=Depends(current_member), limit_: int = Query(25, alias="limit"), offset: int = Query(0, ge=0, le=MAX_OFFSET)):
        limit_ = max(1, min(limit_, 100))
        js = rows(db, """SELECT id FROM jobs WHERE requester_member_id = ? OR provider_member_id = ?
                         ORDER BY created_at DESC LIMIT ? OFFSET ?""", member["id"], member["id"], limit_, offset)
        return {"jobs": [hub.receipt(j["id"]) for j in js], "limit": limit_, "offset": offset}

    @app.get("/api/v1/jobs/{job_id}")
    async def get_job(job_id: str, member=Depends(current_member)):
        visible_job(member, job_id)
        events = rows(db, "SELECT event_type, occurred_at, duration_ms, safe_metadata_json FROM job_events WHERE job_id = ? ORDER BY id", job_id)
        return {"receipt": hub.receipt(job_id), "events": events}

    @app.get("/api/v1/jobs/{job_id}/receipt")
    async def get_receipt(job_id: str, member=Depends(current_member)):
        visible_job(member, job_id)
        return hub.receipt(job_id)

    @app.post("/api/v1/jobs/{job_id}/cancel")
    async def cancel_job(job_id: str, member=Depends(current_member)):
        await hub.cancel(member["id"], job_id)
        return {"ok": True, "status": row(db, "SELECT status FROM jobs WHERE id = ?", job_id)["status"]}

    @app.get("/api/v1/benchmarks/eligibility")
    async def benchmark_eligibility(model: str, member=Depends(current_member)):
        r = hub.benchmark_blocker(member, model)
        if isinstance(r, str):
            return {"available": False, "reason": r}
        local, digest = r
        return {"available": True, "local_node": local["display_name"], "model_digest": digest}

    @app.post("/api/v1/benchmarks")
    async def run_benchmark(body: BenchmarkIn, member=Depends(current_member)):
        limit(f"bench:{member['id']}", 5, 300)
        return await hub.benchmark(member, body, body.idempotency_key)

    @app.get("/api/v1/benchmarks")
    async def list_benchmarks(member=Depends(current_member)):
        bs = rows(db, "SELECT * FROM benchmarks WHERE requester_member_id = ? ORDER BY created_at DESC LIMIT 20", member["id"])
        return {"benchmarks": [hub.benchmark_view(b) for b in bs]}

    # ---------- accounting ----------

    @app.get("/api/v1/me/usage")
    async def usage(member=Depends(current_member)):
        q = """SELECT model_id, COUNT(*) AS jobs, SUM(status='COMPLETED') AS completed,
                      SUM(COALESCE(input_tokens,0)) AS input_tokens, SUM(COALESCE(output_tokens,0)) AS output_tokens,
                      SUM(COALESCE(worker_elapsed_ms,0)) AS worker_ms, SUM(COALESCE(settled_credits,0)) AS settled_micro_occ
               FROM jobs WHERE {col} = ? AND status != 'CREATED' GROUP BY model_id ORDER BY jobs DESC"""
        consumed = rows(db, q.format(col="requester_member_id"), member["id"])
        contributed = rows(db, q.format(col="provider_member_id"), member["id"])

        def total(xs):
            keys = ("jobs", "completed", "input_tokens", "output_tokens", "worker_ms", "settled_micro_occ")
            return {k: sum(x[k] or 0 for x in xs) for k in keys}
        return {"consumed": {"by_model": consumed, "total": total(consumed)},
                "contributed": {"by_model": contributed, "total": total(contributed),
                                "cpu_core_seconds": None, "gpu_active_seconds": None,
                                "note": "Per-job CPU/GPU active time is not measured in v0.1; worker wall time and runtime-reported tokens are."}}

    @app.get("/api/v1/me/ledger")
    async def my_ledger(member=Depends(current_member), limit_: int = Query(50, alias="limit"), offset: int = Query(0, ge=0, le=MAX_OFFSET)):
        limit_ = max(1, min(limit_, 200))
        entries = rows(db, """SELECT l.id, l.job_id, l.entry_type, l.amount_micro_occ, l.tariff_version, l.created_at,
                                     l.reversal_of, l.metadata_json, j.model_id
                              FROM ledger l LEFT JOIN jobs j ON j.id = l.job_id
                              WHERE l.member_id = ? ORDER BY l.id DESC LIMIT ? OFFSET ?""", member["id"], limit_, offset)
        sums = {r["entry_type"]: r["total"] for r in rows(db, "SELECT entry_type, SUM(amount_micro_occ) AS total FROM ledger WHERE member_id = ? GROUP BY entry_type", member["id"])}
        tariff = hub.active_tariff(member["network_id"])
        return {"balance_micro_occ": ledger.balance(db, member["id"]), "totals_by_type": sums, "entries": entries,
                "explanation": ("Balance = sum of your append-only ledger entries. Each request holds a worst-case reservation, "
                                "then settles: the hold is released, you are debited the measured cost (capped at the hold), and the worker's owner "
                                "is rewarded the same amount. OCC are non-cash demo accounting units."),
                "tariff": {"version": tariff["version"], "input_occ_per_1k": ledger.format_occ(tariff["input_micro_per_1k"]),
                           "output_occ_per_1k": ledger.format_occ(tariff["output_micro_per_1k"]),
                           "label": "demo policy default, not a market price"},
                "limit": limit_, "offset": offset}

    # ---------- admin ----------

    @app.get("/api/v1/admin/metrics", dependencies=[Depends(require_admin)])
    async def admin_metrics():
        return {"jobs_by_status": rows(db, "SELECT status, COUNT(*) AS n FROM jobs GROUP BY status"),
                "members": db.execute("SELECT COUNT(*) FROM members").fetchone()[0],
                "nodes_by_status": rows(db, "SELECT status, COUNT(*) AS n FROM nodes GROUP BY status"),
                "connected_nodes": len(hub.nodes), "ledger_consistency": ledger.consistency_report(db)}

    @app.post("/api/v1/admin/pricing", dependencies=[Depends(require_admin)])
    async def add_pricing(body: PricingIn):
        from decimal import Decimal, InvalidOperation
        try:
            Decimal(body.input_usd_per_million_tokens), Decimal(body.output_usd_per_million_tokens)
        except InvalidOperation:
            err("INVALID_PRICE", "Prices must be decimal strings.", 422)
        ref = {"id": new_id(), **body.model_dump(), "effective_at": now()}
        with db:
            insert(db, "pricing_refs", ref)
        return ref

    @app.post("/api/v1/admin/invites", dependencies=[Depends(require_admin)])
    async def admin_invite():
        return {"join_path": f"/join/{join_code}", "join_code": join_code}

    # ---------- OpenAI-compatible (non-streaming subset) ----------

    @app.get("/v1/models")
    async def oai_models(member=Depends(current_member)):
        return {"object": "list", "data": [{"id": m["model_id"], "object": "model", "owned_by": "opencomputemesh",
                                            "worker_count": m["worker_count"]} for m in hub.available_models(member["network_id"])]}

    @app.post("/v1/chat/completions")
    async def chat_completions(body: ChatCompletionIn, request: Request, member=Depends(current_member)):
        if body.stream:
            err("STREAMING_UNSUPPORTED", "stream=true is not supported on this endpoint in v0.1; use the web UI WebSocket.", 400)
        limit(f"req:{member['id']}", 20, 60)
        try:
            params = GenerationParams(model=body.model, messages=body.messages, temperature=body.temperature,
                                      top_p=body.top_p, max_tokens=body.max_completion_tokens or body.max_tokens)
        except ValidationError as e:
            err("INVALID_REQUEST", "Invalid chat completion request.", 422, {"errors": [x["msg"] for x in e.errors()]})
        key = request.headers.get("idempotency-key") or new_id()
        result = await run_blocking(member, params, key[:64])
        r = hub.receipt(result["id"])
        if result["status"] != "COMPLETED":
            err(result["failure_code"] or "FAILED", "Request did not complete.", 502, {"request_id": result["id"]})
        usage_ = None
        if r["tokens"]["total"] is not None:
            usage_ = {"prompt_tokens": r["tokens"]["input"], "completion_tokens": r["tokens"]["output"],
                      "total_tokens": r["tokens"]["total"]}
        return {"id": f"chatcmpl-{result['id']}", "object": "chat.completion",
                "created": int(parse_ts(result["created_at"]).timestamp()), "model": result["model_id"],
                "choices": [{"index": 0, "message": {"role": "assistant", "content": result.get("_output", "")},
                             "finish_reason": "length" if r["done_reason"] == "length" else "stop"}],
                "usage": usage_, "opencomputemesh": r}

    # ---------- WebSockets ----------

    @app.websocket("/ws/client")
    async def ws_client(ws: WebSocket, ticket: str = ""):
        if not origin_ok(ws.headers, s):
            await ws.close(code=4403)
            return
        member_id, exp = tickets.pop(ticket, (None, 0))
        member = row(db, "SELECT * FROM members WHERE id = ? AND status = 'active'", member_id) if member_id and exp > time.monotonic() else None
        if not member:
            await ws.close(code=4401)
            return
        await ws.accept()
        conn = Conn(ws=ws, member_id=member["id"], network_id=member["network_id"])
        hub.add_client(conn)
        await conn.send("network.snapshot", network=hub.network_snapshot(member["network_id"], member["id"]))
        try:
            while True:
                raw = await ws.receive_text()
                if len(raw) > 200_000:
                    await conn.send("error", code="MESSAGE_TOO_LARGE", message="Message too large.")
                    continue
                try:
                    msg = json.loads(raw)
                    if not isinstance(msg, dict):
                        raise ValueError("not an object")
                    await handle_client_message(conn, member, msg)
                except ServiceError as e:
                    await conn.send("request.failed" if e.code != "BENCHMARK_UNAVAILABLE" else "benchmark.failed",
                                    code=e.code, message=e.message, details=e.details,
                                    request_id=(e.details or {}).get("request_id"), client_ref=msg.get("idempotency_key") if isinstance(msg, dict) else None)
                except (ValidationError, ValueError) as e:
                    await conn.send("error", code="INVALID_MESSAGE", message="Invalid message.",
                                    details=[x["msg"] for x in e.errors()] if isinstance(e, ValidationError) else None)
        except WebSocketDisconnect:
            pass
        finally:
            hub.remove_client(conn)

    async def handle_client_message(conn: Conn, member: dict, msg: dict):
        t = msg.get("type")
        if t == "client.ping":
            await conn.send("client.pong")
        elif t == "request.start":
            m = RequestStart.model_validate(msg)
            limit_ok = limiter.allow(f"req:{member['id']}", 20, 60)
            if not limit_ok:
                raise ServiceError("RATE_LIMITED", "Too many requests. Slow down.", 429)
            job, task = await hub.submit(member, m, str(m.idempotency_key), pin_node=m.node_id)
            if not task:  # idempotent replay of a finished request
                await conn.send("request.state", request_id=job["id"], client_ref=str(m.idempotency_key),
                                receipt=hub.receipt(job["id"]))
            else:
                await conn.send("request.created", request_id=job["id"], client_ref=str(m.idempotency_key))
        elif t in ("request.cancel", "request.status"):
            m = RequestRef.model_validate(msg)
            if t == "request.cancel":
                await hub.cancel(member["id"], m.request_id)
            else:
                visible_job(member, m.request_id)
                await conn.send("request.state", request_id=m.request_id, receipt=hub.receipt(m.request_id))
        elif t == "benchmark.start":
            m = BenchmarkStart.model_validate(msg)
            if not limiter.allow(f"bench:{member['id']}", 5, 300):
                raise ServiceError("RATE_LIMITED", "Too many benchmarks. Slow down.", 429)

            async def go():
                try:
                    await hub.benchmark(member, m, str(m.idempotency_key))
                except ServiceError as e:
                    await conn.send("benchmark.failed", code=e.code, message=e.message)
            asyncio.create_task(go())
        else:
            await conn.send("error", code="UNKNOWN_TYPE", message="Unknown message type.")

    @app.websocket("/ws/node")
    async def ws_node(ws: WebSocket):
        ip = client_ip(ws.client, ws.headers)
        if not limiter.allow(f"nodews:{ip}", 30, 60):
            await ws.close(code=4429)
            return
        await ws.accept()
        try:
            hello = NodeHello.model_validate_json(await asyncio.wait_for(ws.receive_text(), timeout=15))
        except (TimeoutError, ValidationError, WebSocketDisconnect, ValueError):
            await ws.close(code=4400, reason="bad hello")
            return
        caps = hello.capabilities
        cap_fields = {"display_name": caps.display_name, "platform": caps.platform, "architecture": caps.architecture,
                      "cpu_model": caps.cpu_model, "logical_cpu_count": caps.logical_cpu_count,
                      "memory_total_bytes": caps.memory_total_bytes, "memory_available_bytes": caps.memory_available_bytes,
                      "gpu_json": dumps([g.model_dump() for g in caps.gpus]), "runtime_name": caps.runtime_name,
                      "runtime_version": caps.runtime_version, "models_json": dumps([m.model_dump() for m in caps.models]),
                      "max_concurrent_jobs": min(caps.max_concurrent_jobs, s.max_concurrent_jobs_per_node)}
        if hello.type == "node.enroll":
            tok = row(db, "SELECT * FROM enrollment_tokens WHERE token_hash = ?", sha256(hello.enrollment_token or ""))
            if not tok or tok["used_at"] or parse_ts(tok["expires_at"]) < parse_ts(now()):
                await ws.close(code=4401, reason="invalid or expired enrollment token")
                return
            node_id, credential = new_id(), "ocmn_" + secrets.token_urlsafe(32)
            with db:
                claimed = db.execute("UPDATE enrollment_tokens SET used_at = ? WHERE token_hash = ? AND used_at IS NULL",
                                     (now(), tok["token_hash"])).rowcount
                if claimed:
                    insert(db, "nodes", {"id": node_id, "network_id": tok["network_id"], "owner_member_id": tok["member_id"],
                                         "credential_hash": sha256(credential), "created_at": now(), **cap_fields})
            if not claimed:  # single use is enforced by the UPDATE, not the earlier read
                await ws.close(code=4401, reason="invalid or expired enrollment token")
                return
            await ws.send_text(json.dumps({"type": "node.registered", "node_id": node_id, "credential": credential,
                                           "protocol_version": 1, "sent_at": now()}))
        else:
            n = row(db, "SELECT * FROM nodes WHERE id = ?", hello.node_id or "")
            if not n or n["revoked_at"] or not hmac.compare_digest(n["credential_hash"], sha256(hello.credential or "")):
                await ws.close(code=4401, reason="invalid node credential")
                return
            node_id = n["id"]
            with db:
                update(db, "nodes", node_id, cap_fields)
            await ws.send_text(json.dumps({"type": "node.registered", "node_id": node_id, "protocol_version": 1, "sent_at": now()}))
        n = row(db, "SELECT * FROM nodes WHERE id = ?", node_id)
        conn = Conn(ws=ws, member_id=n["owner_member_id"], network_id=n["network_id"], node_id=node_id)
        await hub.node_connected(node_id, conn, caps.runtime_healthy)
        log.info("node %s connected (%d models)", node_id[:8], len(caps.models))
        try:
            while True:
                raw = await ws.receive_text()
                if len(raw) > 1_000_000 or not limiter.allow(f"nodemsg:{node_id}", 2000, 10):
                    continue
                try:
                    msg = json.loads(raw)
                except ValueError:
                    continue
                if not isinstance(msg, dict):
                    continue
                t = msg.get("type")
                if t == "node.heartbeat":
                    await hub.node_heartbeat(node_id, msg.get("runtime_healthy") is True,
                                             int_or_none(msg.get("memory_available_bytes")))
                elif t == "node.capabilities":
                    from .protocol import Capabilities
                    try:
                        c = Capabilities.model_validate(msg.get("capabilities"))
                    except ValidationError:
                        continue
                    with db:
                        update(db, "nodes", node_id, {"models_json": dumps([m.model_dump() for m in c.models]),
                                                      "runtime_version": c.runtime_version})
                    await hub.node_heartbeat(node_id, c.runtime_healthy, c.memory_available_bytes)
                    await hub.broadcast_snapshot(n["network_id"])
                elif t in ("job.started", "job.token", "job.completed", "job.failed"):
                    hub.route_node_event(node_id, msg)
        except WebSocketDisconnect:
            pass
        finally:
            log.info("node %s disconnected", node_id[:8])
            await hub.node_disconnected(node_id, conn)

    # ---------- SPA (registered last) ----------

    dist = Path(s.web_dist).resolve()

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        if path.startswith(("api/", "v1/", "ws/")):
            err("NOT_FOUND", "Not found.", 404)
        f = (dist / path).resolve()
        if path and f.is_file() and f.is_relative_to(dist):
            return FileResponse(f)
        index = dist / "index.html"
        if not index.exists():
            return JSONResponse({"error": {"code": "WEB_NOT_BUILT", "message": "Run scripts/build_web.sh"}}, status_code=503)
        return FileResponse(index, headers={"cache-control": "no-cache"})

    return app
