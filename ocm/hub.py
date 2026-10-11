"""Live coordinator state: connected nodes and browsers, scheduling, and the job lifecycle.

The SQLite database is the source of truth for everything durable. This module only keeps what
cannot be persisted (open sockets, in-flight token streams, prompts) in memory. Run exactly one
coordinator process: socket ownership lives here.
"""

import asyncio
import json
import logging
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import timedelta

from fastapi import WebSocket

from . import ledger
from .config import Settings
from .db import dumps, insert, new_id, now, parse_ts, row, rows, sha256, update
from .metrics import normalize_ollama
from .protocol import PROTOCOL_VERSION, GenerationParams

log = logging.getLogger("ocm.hub")

TERMINAL = {"COMPLETED", "FAILED", "CANCELLED", "EXPIRED"}
TRANSITIONS = {
    "CREATED": {"QUEUED", "FAILED"},
    "QUEUED": {"DISPATCHED", "FAILED", "CANCELLED", "EXPIRED"},
    "DISPATCHED": {"RUNNING", "QUEUED", "FAILED", "CANCEL_REQUESTED", "EXPIRED"},
    "RUNNING": {"COMPLETED", "FAILED", "CANCEL_REQUESTED", "EXPIRED"},
    "CANCEL_REQUESTED": {"CANCELLED", "COMPLETED", "FAILED", "EXPIRED"},
}
CANCEL_ACK_SECONDS = 10


class IllegalTransition(Exception):
    pass


class ServiceError(Exception):
    def __init__(self, code: str, message: str, status: int = 400, details: dict | None = None):
        super().__init__(message)
        self.code, self.message, self.status, self.details = code, message, status, details


def transition(conn: sqlite3.Connection, job_id: str, new: str, **fields) -> bool:
    """Apply a legal state change inside the caller's transaction. Re-entering a terminal state is a no-op."""
    cur = row(conn, "SELECT status FROM jobs WHERE id = ?", job_id)["status"]
    if cur in TERMINAL:
        if cur == new:
            return False
        raise IllegalTransition(f"{cur} -> {new}")
    if new not in TRANSITIONS[cur]:
        raise IllegalTransition(f"{cur} -> {new}")
    update(conn, "jobs", job_id, {"status": new, **fields})
    return True


def log_event(conn, job_id: str, event_type: str, duration_ms: int | None = None, **meta) -> None:
    insert(conn, "job_events", {"job_id": job_id, "event_type": event_type, "occurred_at": now(),
                                "duration_ms": duration_ms, "safe_metadata_json": dumps(meta)})


def ms_since(t0: float) -> int:
    return round((time.monotonic() - t0) * 1000)


@dataclass(eq=False)
class Conn:
    ws: WebSocket
    member_id: str
    network_id: str
    node_id: str | None = None
    active: set = field(default_factory=set)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def send(self, type_: str, **payload) -> bool:
        msg = {"type": type_, "protocol_version": PROTOCOL_VERSION, "sent_at": now(), **payload}
        try:
            async with self.lock:
                await self.ws.send_text(json.dumps(msg))
            return True
        except Exception:  # closed socket: the disconnect handler cleans up
            return False


@dataclass
class Outcome:
    kind: str  # completed | failed | lost | timeout | cancelled
    started: bool = False
    payload: dict = field(default_factory=dict)


class Hub:
    def __init__(self, conn: sqlite3.Connection, settings: Settings):
        self.db = conn
        self.s = settings
        self.nodes: dict[str, Conn] = {}
        self.clients: dict[str, set[Conn]] = {}
        self.job_queues: dict[str, asyncio.Queue] = {}
        self.job_tasks: dict[str, asyncio.Task] = {}
        self.capacity = asyncio.Condition()
        self.offline_timers: dict[str, asyncio.Task] = {}
        self.queued = 0

    # ---------- startup / housekeeping ----------

    def recover_after_restart(self) -> None:
        """Nodes must re-authenticate; in-flight jobs cannot be resumed, so they fail visibly."""
        with self.db:
            self.db.execute("UPDATE nodes SET status = 'OFFLINE' WHERE status != 'REVOKED'")
            for job in rows(self.db, "SELECT * FROM jobs WHERE status NOT IN ('COMPLETED','FAILED','CANCELLED','EXPIRED')"):
                update(self.db, "jobs", job["id"], {"status": "FAILED", "failure_code": "COORDINATOR_RESTART",
                                                     "completed_at": now(), "settlement_status": "released"})
                ledger.release(self.db, job)
                log_event(self.db, job["id"], "failed", failure_code="COORDINATOR_RESTART")
                log_event(self.db, job["id"], "credit_released")

    async def sweep_forever(self) -> None:
        while True:
            await asyncio.sleep(5)
            try:
                await self.sweep_stale_nodes()
            except Exception:
                log.exception("sweeper failed")

    async def sweep_stale_nodes(self) -> None:
        cutoff = parse_ts(now()) - timedelta(seconds=self.s.node_heartbeat_ttl_seconds)
        stale = [n for n in rows(self.db, "SELECT id, last_heartbeat_at FROM nodes WHERE status IN ('ONLINE','PAUSED','DEGRADED')")
                 if not n["last_heartbeat_at"] or parse_ts(n["last_heartbeat_at"]) < cutoff]
        for n in stale:
            conn = self.nodes.get(n["id"])
            if conn:
                await conn.ws.close(code=4008, reason="heartbeat expired")
            await self.node_disconnected(n["id"], conn, grace=False)

    # ---------- node presence ----------

    def node_status_for(self, node: dict, healthy: bool) -> str:
        if node["revoked_at"]:
            return "REVOKED"
        if not healthy or not json.loads(node["models_json"]):
            return "DEGRADED"
        return "ONLINE" if node["sharing_enabled"] else "PAUSED"

    async def node_connected(self, node_id: str, conn: Conn, healthy: bool) -> None:
        old = self.nodes.get(node_id)
        if old and old is not conn:
            await old.ws.close(code=4009, reason="replaced by newer connection")
        timer = self.offline_timers.pop(node_id, None)
        if timer:
            timer.cancel()
        self.nodes[node_id] = conn
        node = row(self.db, "SELECT * FROM nodes WHERE id = ?", node_id)
        with self.db:
            update(self.db, "nodes", node_id, {"status": self.node_status_for(node, healthy), "last_heartbeat_at": now()})
        await self.notify_capacity()
        await self.broadcast_snapshot(conn.network_id)

    async def node_heartbeat(self, node_id: str, healthy: bool, memory_available: int | None) -> None:
        node = row(self.db, "SELECT * FROM nodes WHERE id = ?", node_id)
        status = self.node_status_for(node, healthy)
        with self.db:
            update(self.db, "nodes", node_id, {"last_heartbeat_at": now(), "status": status,
                                               "memory_available_bytes": memory_available})
        if status != node["status"]:
            await self.notify_capacity()
            await self.broadcast_snapshot(node["network_id"])

    async def node_disconnected(self, node_id: str, conn: Conn | None, grace: bool = True) -> None:
        if conn is not None and self.nodes.get(node_id) is not conn:
            return  # an older socket closing after replacement
        self.nodes.pop(node_id, None)
        for job_id in list(conn.active if conn else ()):
            q = self.job_queues.get(job_id)
            if q:
                q.put_nowait({"type": "_lost"})

        async def go_offline():
            if grace:
                await asyncio.sleep(self.s.node_offline_grace_seconds)
            if node_id in self.nodes:
                return
            node = row(self.db, "SELECT network_id, status FROM nodes WHERE id = ?", node_id)
            if node and node["status"] != "REVOKED":
                with self.db:
                    update(self.db, "nodes", node_id, {"status": "OFFLINE"})
                await self.broadcast_snapshot(node["network_id"])
            self.offline_timers.pop(node_id, None)

        # Not eligible immediately (no socket), shown OFFLINE after the grace interval.
        self.offline_timers[node_id] = asyncio.create_task(go_offline())

    async def notify_capacity(self) -> None:
        async with self.capacity:
            self.capacity.notify_all()

    # ---------- browser presence ----------

    def add_client(self, conn: Conn) -> None:
        self.clients.setdefault(conn.member_id, set()).add(conn)

    def remove_client(self, conn: Conn) -> None:
        self.clients.get(conn.member_id, set()).discard(conn)

    async def to_member(self, member_id: str, type_: str, **payload) -> None:
        for c in list(self.clients.get(member_id, ())):
            await c.send(type_, **payload)

    async def broadcast_snapshot(self, network_id: str) -> None:
        for member_id, conns in list(self.clients.items()):
            mine = [c for c in conns if c.network_id == network_id]
            if mine:
                snap = self.network_snapshot(network_id, member_id)  # per viewer: carries is_mine
                for c in mine:
                    await c.send("network.snapshot", network=snap)

    # ---------- read models ----------

    def models_on_node(self, node: dict) -> list[dict]:
        return json.loads(node["models_json"])

    def eligible_nodes(self, network_id: str, model_id: str, digest: str | None = None,
                       exclude: set = frozenset(), only: str | None = None, ignore_capacity=False) -> list[dict]:
        cutoff = parse_ts(now()) - timedelta(seconds=self.s.node_heartbeat_ttl_seconds)
        out = []
        for n in rows(self.db, """SELECT * FROM nodes WHERE network_id = ? AND status = 'ONLINE'
                                  AND sharing_enabled = 1 AND revoked_at IS NULL""", network_id):
            conn = self.nodes.get(n["id"])
            if not conn or n["id"] in exclude or (only and n["id"] != only):
                continue
            if not n["last_heartbeat_at"] or parse_ts(n["last_heartbeat_at"]) < cutoff:
                continue
            m = next((m for m in self.models_on_node(n) if m["model_id"] == model_id), None)
            if not m or (digest and m.get("digest") != digest):
                continue
            if not ignore_capacity and len(conn.active) >= n["max_concurrent_jobs"]:
                continue
            n["_active"], n["_digest"] = len(conn.active), m.get("digest")
            out.append(n)
        return out

    def pick_node(self, network_id, model_id, digest=None, exclude=frozenset(), only=None) -> tuple[dict, str] | None:
        cands = self.eligible_nodes(network_id, model_id, digest, exclude, only)
        if not cands:
            return None
        # Fewest active jobs, then freshest heartbeat, then stable id.
        cands.sort(key=lambda n: (n["_active"], -parse_ts(n["last_heartbeat_at"]).timestamp(), n["id"]))
        n = cands[0]
        reason = (f"exact model present; online; {n['_active']} active job(s); "
                  f"within owner limit of {n['max_concurrent_jobs']}; {len(cands)} eligible worker(s)")
        return n, reason

    def available_models(self, network_id: str) -> list[dict]:
        by_model: dict[str, dict] = {}
        for n in rows(self.db, """SELECT * FROM nodes WHERE network_id = ? AND status = 'ONLINE'
                                  AND sharing_enabled = 1 AND revoked_at IS NULL""", network_id):
            if n["id"] not in self.nodes:
                continue
            for m in self.models_on_node(n):
                e = by_model.setdefault(m["model_id"], {"model_id": m["model_id"], "worker_count": 0, "digests": [],
                                                         "family": m.get("family"), "parameter_size": m.get("parameter_size"),
                                                         "quantization": m.get("quantization"), "node_ids": []})
                e["worker_count"] += 1
                e["node_ids"].append(n["id"])
                if m.get("digest") and m["digest"] not in e["digests"]:
                    e["digests"].append(m["digest"])
        return sorted(by_model.values(), key=lambda e: e["model_id"])

    def public_node(self, n: dict, viewer_member_id: str | None = None) -> dict:
        owner = row(self.db, "SELECT nickname FROM members WHERE id = ?", n["owner_member_id"])
        conn = self.nodes.get(n["id"])
        return {
            "id": n["id"], "display_name": n["display_name"], "owner_nickname": owner["nickname"] if owner else None,
            "is_mine": viewer_member_id == n["owner_member_id"], "status": n["status"],
            "connected": conn is not None, "platform": n["platform"], "architecture": n["architecture"],
            "cpu_model": n["cpu_model"], "logical_cpu_count": n["logical_cpu_count"],
            "memory_total_bytes": n["memory_total_bytes"], "memory_available_bytes": n["memory_available_bytes"],
            "gpus": json.loads(n["gpu_json"]), "runtime_name": n["runtime_name"], "runtime_version": n["runtime_version"],
            "models": self.models_on_node(n), "sharing_enabled": bool(n["sharing_enabled"]),
            "max_concurrent_jobs": n["max_concurrent_jobs"], "active_jobs": len(conn.active) if conn else 0,
            "last_heartbeat_at": n["last_heartbeat_at"], "created_at": n["created_at"], "revoked_at": n["revoked_at"],
            "telemetry": {"cpu_time_per_job": "unavailable", "gpu_active_time": "unavailable",
                          "worker_wall_time": "measured", "tokens": "runtime_reported"},
        }

    def network_snapshot(self, network_id: str, viewer_member_id: str | None = None) -> dict:
        net = row(self.db, "SELECT id, name, created_at FROM networks WHERE id = ?", network_id)
        nodes = rows(self.db, "SELECT * FROM nodes WHERE network_id = ? AND status != 'REVOKED' ORDER BY created_at", network_id)
        activity = rows(self.db, """
            SELECT e.occurred_at, e.event_type, e.job_id, j.model_id, j.kind, n.display_name AS node_name,
                   m.nickname AS requester_nickname
            FROM job_events e JOIN jobs j ON j.id = e.job_id
            LEFT JOIN nodes n ON n.id = j.selected_node_id LEFT JOIN members m ON m.id = j.requester_member_id
            WHERE j.network_id = ? AND e.event_type IN ('accepted','dispatched','first_token','completed','failed','cancelled','credit_settled')
            ORDER BY e.id DESC LIMIT 25""", network_id)
        return {
            "id": net["id"], "name": net["name"],
            "online_node_count": sum(1 for n in nodes if n["status"] == "ONLINE" and n["id"] in self.nodes),
            "node_count": len(nodes),
            "member_count": self.db.execute("SELECT COUNT(*) FROM members WHERE network_id = ?", (network_id,)).fetchone()[0],
            "models": self.available_models(network_id),
            "nodes": [self.public_node(n, viewer_member_id) for n in nodes],
            "activity": activity,
            "demo_mode": self.s.demo_mode,
        }

    def active_tariff(self, network_id: str) -> dict:
        return row(self.db, "SELECT * FROM tariffs WHERE network_id = ? ORDER BY active_at DESC LIMIT 1", network_id)

    # ---------- job lifecycle ----------

    async def submit(self, member: dict, params: GenerationParams, idempotency_key: str, *, kind="request",
                     pin_node: str | None = None, exclude: set = frozenset(), digest: str | None = None,
                     stream_to_member=True) -> tuple[dict, asyncio.Task | None]:
        """Create (or return the existing) job for this key and start it. Never runs a key twice."""
        existing = row(self.db, "SELECT * FROM jobs WHERE requester_member_id = ? AND idempotency_key = ?",
                       member["id"], idempotency_key)
        if existing:
            return existing, self.job_tasks.get(existing["id"])
        prompt_chars = sum(len(m.content) for m in params.messages)
        if prompt_chars > self.s.max_prompt_chars:
            raise ServiceError("PROMPT_TOO_LARGE", f"Prompt exceeds {self.s.max_prompt_chars} characters.", 413)
        max_tokens = params.max_tokens or self.s.default_max_output_tokens
        if max_tokens > self.s.max_output_tokens:
            raise ServiceError("MAX_TOKENS_TOO_LARGE", f"max_tokens must be <= {self.s.max_output_tokens}.")
        active = sum(1 for j in self.job_tasks if row(self.db, "SELECT requester_member_id FROM jobs WHERE id = ?", j)["requester_member_id"] == member["id"])
        if active >= self.s.max_active_jobs_per_member:
            raise ServiceError("TOO_MANY_ACTIVE_JOBS", "Wait for your running requests to finish.", 429)
        hosts = self.eligible_nodes(member["network_id"], params.model, digest, exclude, pin_node, ignore_capacity=True)
        if not hosts:
            raise ServiceError("MODEL_UNAVAILABLE", f"No eligible online worker serves '{params.model}'.", 409,
                               {"available_models": [m["model_id"] for m in self.available_models(member["network_id"])]})
        if self.queued >= self.s.max_queued_jobs:
            raise ServiceError("QUEUE_FULL", "The network queue is full. Try again shortly.", 503)

        tariff = self.active_tariff(member["network_id"])
        messages = [m.model_dump() for m in params.messages]
        reservation = ledger.cost_micro(tariff, ledger.estimate_input_tokens(messages), max_tokens)
        job = {"id": new_id(), "network_id": member["network_id"], "requester_member_id": member["id"],
               "idempotency_key": idempotency_key, "kind": kind, "model_id": params.model, "status": "CREATED",
               "created_at": now(), "tariff_version": tariff["version"], "reserved_credits": reservation}
        try:
            with self.db:
                insert(self.db, "jobs", job)
                log_event(self.db, job["id"], "accepted", kind=kind)
                ledger.reserve(self.db, job, reservation)
                log_event(self.db, job["id"], "credit_reserved", amount_micro_occ=reservation)
        except ledger.InsufficientCredits:
            with self.db:
                insert(self.db, "jobs", {**job, "reserved_credits": 0})
                transition(self.db, job["id"], "FAILED", failure_code="INSUFFICIENT_CREDITS", completed_at=now())
                log_event(self.db, job["id"], "failed", failure_code="INSUFFICIENT_CREDITS")
            raise ServiceError("INSUFFICIENT_CREDITS",
                               f"This request needs a hold of {ledger.format_occ(reservation)} OCC "
                               f"(worst case for {max_tokens} output tokens); your balance is "
                               f"{ledger.format_occ(ledger.balance(self.db, member['id']))} OCC.", 402,
                               {"request_id": job["id"]})
        self.job_queues[job["id"]] = asyncio.Queue()
        task = asyncio.create_task(self._run(job["id"], member, messages, max_tokens, params, pin_node,
                                             set(exclude), digest, stream_to_member))
        self.job_tasks[job["id"]] = task
        task.add_done_callback(lambda _t, jid=job["id"]: (self.job_tasks.pop(jid, None), self.job_queues.pop(jid, None)))
        return row(self.db, "SELECT * FROM jobs WHERE id = ?", job["id"]), task

    async def cancel(self, member_id: str, job_id: str) -> dict:
        job = row(self.db, "SELECT * FROM jobs WHERE id = ? AND requester_member_id = ?", job_id, member_id)
        if not job:
            raise ServiceError("NOT_FOUND", "Request not found.", 404)
        q = self.job_queues.get(job_id)
        if q and job["status"] not in TERMINAL:
            q.put_nowait({"type": "_cancel"})
        return job

    async def _run(self, job_id, member, messages, max_tokens, params, pin_node, exclude, digest, stream) -> dict:
        t0 = time.monotonic()
        out: list[str] = []
        ref = row(self.db, "SELECT idempotency_key FROM jobs WHERE id = ?", job_id)["idempotency_key"]
        emit = (lambda t, **p: self.to_member(member["id"], t, request_id=job_id, client_ref=ref, **p)) if stream else (lambda t, **p: asyncio.sleep(0))
        q = self.job_queues[job_id]
        try:
            await emit("request.accepted", model=params.model)
            while True:
                job = row(self.db, "SELECT * FROM jobs WHERE id = ?", job_id)
                with self.db:
                    transition(self.db, job_id, "QUEUED", queued_at=now())
                    log_event(self.db, job_id, "queued")
                await emit("request.queued")
                tq = time.monotonic()
                picked = await self._wait_for_node(job, q, digest, exclude, pin_node)
                if picked == "cancelled":
                    return await self._finish(job_id, "CANCELLED", "CANCELLED_BY_USER", emit, t0, cancel_effect="cancelled_before_dispatch")
                if picked is None:
                    return await self._finish(job_id, "EXPIRED", "QUEUE_TIMEOUT", emit, t0)
                node, reason = picked
                conn = self.nodes[node["id"]]
                conn.active.add(job_id)
                try:
                    with self.db:
                        transition(self.db, job_id, "DISPATCHED", selected_node_id=node["id"],
                                   provider_member_id=node["owner_member_id"], route_reason=reason,
                                   model_digest=node["_digest"], dispatched_at=now(), queue_wait_ms=ms_since(tq))
                        log_event(self.db, job_id, "dispatched", node_id=node["id"], reason=reason)
                    await emit("request.routed", node_id=node["id"], node_name=node["display_name"], reason=reason,
                               model_digest=node["_digest"])
                    td = time.monotonic()
                    sent = await conn.send("job.start", job_id=job_id, model=params.model, messages=messages,
                                           options={"max_tokens": max_tokens, "temperature": params.temperature,
                                                    "top_p": params.top_p},
                                           deadline_seconds=self.s.job_timeout_seconds)
                    outcome = await self._consume(job_id, conn, q, td, out, emit) if sent else Outcome("lost")
                finally:
                    conn.active.discard(job_id)
                    await self.notify_capacity()
                job = row(self.db, "SELECT * FROM jobs WHERE id = ?", job_id)
                if outcome.kind in ("failed", "lost") and not outcome.started and job["retry_count"] < 1 and job["status"] == "DISPATCHED":
                    exclude.add(node["id"])
                    with self.db:
                        update(self.db, "jobs", job_id, {"retry_count": job["retry_count"] + 1})
                        log_event(self.db, job_id, "retry", reason=outcome.kind, failed_node_id=node["id"])
                    continue  # back to the queue; expires with QUEUE_TIMEOUT if no other worker appears
                return await self._settle_outcome(job_id, outcome, emit, t0, "".join(out))
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("job %s crashed", job_id)
            return await self._finish(job_id, "FAILED", "INTERNAL_ERROR", emit, t0)

    @staticmethod
    def _failure_code(o: Outcome) -> str:
        return {"lost": "WORKER_DISCONNECTED", "timeout": "JOB_TIMEOUT"}.get(o.kind, o.payload.get("code") or "WORKER_ERROR")

    async def _wait_for_node(self, job, q, digest, exclude, pin_node):
        deadline = time.monotonic() + self.s.queue_timeout_seconds
        self.queued += 1
        try:
            while True:
                if not q.empty() and q.get_nowait()["type"] == "_cancel":
                    return "cancelled"
                picked = self.pick_node(job["network_id"], job["model_id"], digest, exclude, pin_node)
                if picked:
                    return picked
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                try:
                    async with self.capacity:
                        await asyncio.wait_for(self.capacity.wait(), timeout=min(remaining, 1.0))
                except TimeoutError:
                    pass
        finally:
            self.queued -= 1

    async def _consume(self, job_id, conn: Conn, q: asyncio.Queue, td: float, out: list, emit) -> Outcome:
        started = False
        first = False
        deadline = time.monotonic() + self.s.job_timeout_seconds
        cancel_deadline = None
        while True:
            limit = cancel_deadline or deadline
            try:
                ev = await asyncio.wait_for(q.get(), timeout=max(0.01, limit - time.monotonic()))
            except TimeoutError:
                await conn.send("job.cancel", job_id=job_id)
                return Outcome("cancelled" if cancel_deadline else "timeout", started,
                               {"cancel_effect": "no_ack_from_worker"} if cancel_deadline else {})
            t = ev["type"]
            if t == "job.started" and not started:
                started = True
                with self.db:
                    transition(self.db, job_id, "RUNNING")
                    log_event(self.db, job_id, "worker_started")
                await emit("request.started")
            elif t == "job.token":
                text = ev.get("text") or ""
                if not text:
                    continue
                if not first:
                    first = True
                    ttft = ms_since(td)
                    with self.db:
                        update(self.db, "jobs", job_id, {"first_token_at": now(), "time_to_first_token_ms": ttft})
                        log_event(self.db, job_id, "first_token", duration_ms=ttft)
                out.append(text)
                await emit("request.token", text=text, seq=ev.get("seq"))
            elif t == "job.completed":
                return Outcome("completed", True, ev)
            elif t == "job.failed":
                if cancel_deadline:
                    return Outcome("cancelled", started, {**ev, "cancel_effect": "runtime_stream_closed"})
                return Outcome("failed", started or bool(ev.get("started")), ev)
            elif t == "_lost":
                return Outcome("lost", started)
            elif t == "_cancel" and not cancel_deadline:
                with self.db:
                    transition(self.db, job_id, "CANCEL_REQUESTED", cancelled_at=now())
                    log_event(self.db, job_id, "cancel_requested")
                await conn.send("job.cancel", job_id=job_id)
                cancel_deadline = time.monotonic() + CANCEL_ACK_SECONDS

    async def _settle_outcome(self, job_id, o: Outcome, emit, t0, output: str) -> dict:
        if o.kind == "completed":
            stats = o.payload.get("final_stats") or {}
            m = normalize_ollama(stats)
            job = row(self.db, "SELECT * FROM jobs WHERE id = ?", job_id)
            fields = {**m, "worker_elapsed_ms": o.payload.get("worker_elapsed_ms"), "completed_at": now(),
                      "end_to_end_ms": ms_since(t0)}
            with self.db:
                if job["status"] == "DISPATCHED":
                    transition(self.db, job_id, "RUNNING")
                if m["token_count_source"] == "runtime_reported":
                    tariff = row(self.db, "SELECT * FROM tariffs WHERE version = ?", job["tariff_version"])
                    amount = ledger.cost_micro(tariff, m["input_tokens"], m["output_tokens"])
                    ledger.settle(self.db, job, job["provider_member_id"], amount)
                    fields.update(settled_credits=amount, settlement_status="settled")
                    log_event(self.db, job_id, "credit_settled", amount_micro_occ=amount)
                else:
                    # No exact usage: hold returned, provider not paid, job shown as unsettled.
                    ledger.release(self.db, job)
                    fields.update(settlement_status="unsettled_missing_usage")
                    log_event(self.db, job_id, "credit_released", reason="usage_unavailable")
                transition(self.db, job_id, "COMPLETED", **fields)
                log_event(self.db, job_id, "worker_metric", duration_ms=fields["worker_elapsed_ms"],
                          done_reason=stats.get("done_reason"))
                log_event(self.db, job_id, "completed", duration_ms=fields["end_to_end_ms"])
            await emit("request.completed", receipt=self.receipt(job_id), output_chars=len(output))
            await self.broadcast_snapshot(job["network_id"])
            return {**row(self.db, "SELECT * FROM jobs WHERE id = ?", job_id), "_output": output}
        if o.kind == "cancelled":
            return await self._finish(job_id, "CANCELLED", "CANCELLED_BY_USER", emit, t0,
                                      cancel_effect=o.payload.get("cancel_effect"),
                                      worker_elapsed_ms=o.payload.get("worker_elapsed_ms"))
        if o.kind == "timeout":
            return await self._finish(job_id, "EXPIRED", "JOB_TIMEOUT", emit, t0)
        return await self._finish(job_id, "FAILED", self._failure_code(o), emit, t0,
                                  worker_elapsed_ms=o.payload.get("worker_elapsed_ms"))

    async def _finish(self, job_id, status, code, emit, t0, **extra) -> dict:
        job = row(self.db, "SELECT * FROM jobs WHERE id = ?", job_id)
        fields = {"failure_code": code, "completed_at": now(), "end_to_end_ms": ms_since(t0),
                  "settlement_status": "released", **{k: v for k, v in extra.items() if v is not None}}
        with self.db:
            transition(self.db, job_id, status, **fields)
            ledger.release(self.db, job)
            log_event(self.db, job_id, "cancelled" if status == "CANCELLED" else "failed", failure_code=code)
            log_event(self.db, job_id, "credit_released")
        msg = {"QUEUE_TIMEOUT": "No eligible worker became free before the queue deadline.",
               "WORKER_DISCONNECTED": "The worker disconnected before finishing.",
               "JOB_TIMEOUT": "The worker did not finish before the job deadline.",
               "CANCELLED_BY_USER": "Request cancelled.",
               "INTERNAL_ERROR": "The coordinator hit an internal error."}.get(code, "The worker reported an error.")
        await emit("request.cancelled" if status == "CANCELLED" else "request.failed", code=code, message=msg,
                   receipt=self.receipt(job_id))
        await self.broadcast_snapshot(job["network_id"])
        return row(self.db, "SELECT * FROM jobs WHERE id = ?", job_id)

    def route_node_event(self, node_id: str, ev: dict) -> None:
        """A node may only report on jobs dispatched to it."""
        job_id = ev.get("job_id")
        conn = self.nodes.get(node_id)
        q = self.job_queues.get(job_id or "")
        if q and conn and job_id in conn.active:
            q.put_nowait(ev)

    # ---------- receipts ----------

    def receipt(self, job_id: str) -> dict:
        j = row(self.db, "SELECT * FROM jobs WHERE id = ?", job_id)
        node = row(self.db, "SELECT id, display_name, owner_member_id, runtime_name, runtime_version FROM nodes WHERE id = ?",
                   j["selected_node_id"]) if j["selected_node_id"] else None
        provider = row(self.db, "SELECT nickname FROM members WHERE id = ?", j["provider_member_id"]) if j["provider_member_id"] else None
        requester = row(self.db, "SELECT nickname FROM members WHERE id = ?", j["requester_member_id"])
        tariff = row(self.db, "SELECT * FROM tariffs WHERE version = ?", j["tariff_version"]) if j["tariff_version"] else None
        entries = rows(self.db, """SELECT l.id, l.entry_type, l.amount_micro_occ, l.created_at, m.nickname
                                   FROM ledger l JOIN members m ON m.id = l.member_id WHERE l.job_id = ? ORDER BY l.id""", job_id)
        known = j["input_tokens"] is not None and j["output_tokens"] is not None
        return {
            "request_id": j["id"], "kind": j["kind"], "status": j["status"], "failure_code": j["failure_code"],
            "model_id": j["model_id"], "model_digest": j["model_digest"],
            "node": {"id": node["id"], "display_name": node["display_name"], "owner_nickname": provider and provider["nickname"],
                     "runtime": f"{node['runtime_name']} {node['runtime_version'] or ''}".strip()} if node else None,
            "requester_nickname": requester["nickname"], "route_reason": j["route_reason"],
            "tokens": {"input": j["input_tokens"], "output": j["output_tokens"],
                       "total": j["input_tokens"] + j["output_tokens"] if known else None, "source": j["token_count_source"]},
            "timings_ms": {
                "coordinator_end_to_end": j["end_to_end_ms"], "queue_wait": j["queue_wait_ms"],
                "time_to_first_token": j["time_to_first_token_ms"], "worker_elapsed": j["worker_elapsed_ms"],
                "runtime_total": j["runtime_total_ms"], "runtime_load": j["runtime_load_ms"],
                "prompt_eval": j["prompt_eval_ms"], "generation": j["generation_ms"]},
            "tokens_per_second": j["tokens_per_second"], "retry_count": j["retry_count"],
            "cancel_effect": j["cancel_effect"],
            "timestamps": {k: j[k] for k in ("created_at", "queued_at", "dispatched_at", "first_token_at", "completed_at")},
            "credits": {
                "tariff": tariff and {"version": tariff["version"],
                                      "input_occ_per_1k": ledger.format_occ(tariff["input_micro_per_1k"]),
                                      "output_occ_per_1k": ledger.format_occ(tariff["output_micro_per_1k"]),
                                      "min_charge_occ": ledger.format_occ(tariff["min_charge_micro"])},
                "reserved_micro_occ": j["reserved_credits"], "settled_micro_occ": j["settled_credits"],
                "settlement_status": j["settlement_status"],
                "entries": entries,
            },
            "hosted_api_equivalent": self.hosted_equivalent(j),
        }

    def hosted_equivalent(self, j: dict) -> dict:
        ref = row(self.db, "SELECT * FROM pricing_refs WHERE local_model_id = ? AND enabled = 1 ORDER BY effective_at DESC LIMIT 1",
                  j["model_id"])
        if not ref:
            return {"configured": False, "message": "Hosted API equivalent not configured."}
        if j["input_tokens"] is None or j["output_tokens"] is None:
            return {"configured": True, "available": False, "message": "Token usage unavailable."}
        from decimal import Decimal
        usd = (j["input_tokens"] * Decimal(ref["input_usd_per_million_tokens"]) +
               j["output_tokens"] * Decimal(ref["output_usd_per_million_tokens"])) / Decimal(1_000_000)
        return {"configured": True, "available": True, "label": "hypothetical hosted API equivalent",
                "usd": str(usd.quantize(Decimal("0.000001"))), "provider": ref["provider"],
                "provider_model_id": ref["provider_model_id"], "source_url": ref["source_url"],
                "source_checked_at": ref["source_checked_at"], "notes": ref["notes"]}

    # ---------- benchmark ----------

    async def benchmark(self, member: dict, params: GenerationParams, key: str) -> dict:
        reason = self.benchmark_blocker(member, params.model)
        if isinstance(reason, str):
            raise ServiceError("BENCHMARK_UNAVAILABLE", reason, 409)
        local, digest = reason
        sampling = dumps({"model": params.model, "max_tokens": params.max_tokens or self.s.default_max_output_tokens,
                          "temperature": params.temperature, "top_p": params.top_p, "think": False})
        await self.to_member(member["id"], "benchmark.progress", stage="local", node_name=local["display_name"])
        lj, lt = await self.submit(member, params, f"{key}:local", kind="benchmark_local", pin_node=local["id"],
                                   digest=digest)
        lres = await lt if lt else lj
        await self.to_member(member["id"], "benchmark.progress", stage="remote")
        rj, rt = await self.submit(member, params, f"{key}:remote", kind="benchmark_remote",
                                   exclude={local["id"]}, digest=digest)
        rres = await rt if rt else rj

        def metrics(j):
            return {k: j[k] for k in ("id", "status", "selected_node_id", "input_tokens", "output_tokens", "token_count_source",
                                      "end_to_end_ms", "queue_wait_ms", "time_to_first_token_ms", "runtime_total_ms",
                                      "runtime_load_ms", "tokens_per_second", "settled_credits")}
        lm, rm = metrics(lres), metrics(rres)
        comparable = (lres["status"] == rres["status"] == "COMPLETED"
                      and lres["token_count_source"] == rres["token_count_source"] == "runtime_reported"
                      and lres["model_digest"] == rres["model_digest"] == digest
                      and lres["selected_node_id"] != rres["selected_node_id"])
        bench = {"id": new_id(), "requester_member_id": member["id"], "local_job_id": lres["id"], "remote_job_id": rres["id"],
                 "local_node_id": local["id"], "remote_node_id": rres["selected_node_id"], "model_id": params.model,
                 "model_digest": digest, "sampling_config_hash": sha256(sampling), "local_metrics_json": dumps(lm),
                 "remote_metrics_json": dumps(rm), "comparison_status": "comparable_single_run" if comparable else "non_comparable",
                 "created_at": now()}
        with self.db:
            insert(self.db, "benchmarks", bench)
        result = self.benchmark_view(bench)
        await self.to_member(member["id"], "benchmark.completed", benchmark=result)
        return result

    def benchmark_view(self, b: dict) -> dict:
        def node_name(nid):
            n = row(self.db, "SELECT display_name FROM nodes WHERE id = ?", nid) if nid else None
            return n and n["display_name"]
        lm, rm = json.loads(b["local_metrics_json"]), json.loads(b["remote_metrics_json"])
        diff = None
        if b["comparison_status"] == "comparable_single_run" and lm["end_to_end_ms"] and rm["end_to_end_ms"]:
            d = rm["end_to_end_ms"] - lm["end_to_end_ms"]
            diff = {"end_to_end_ms": d, "percent": round(d / lm["end_to_end_ms"] * 100, 1)}
        return {"id": b["id"], "model_id": b["model_id"], "model_digest": b["model_digest"],
                "comparison_status": b["comparison_status"], "created_at": b["created_at"],
                "local": {**lm, "node_name": node_name(b["local_node_id"]), "receipt": self.receipt(lm["id"])},
                "remote": {**rm, "node_name": node_name(rm["selected_node_id"]), "receipt": self.receipt(rm["id"])},
                "difference": diff,
                "caveat": "Single-run observed comparison on different devices, load and network paths; illustrative, not statistically valid."}

    def benchmark_blocker(self, member: dict, model_id: str):
        """Return a human reason the comparison is unavailable, or (local_node, digest)."""
        mine = [n for n in self.eligible_nodes(member["network_id"], model_id, ignore_capacity=True)
                if n["owner_member_id"] == member["id"]]
        if not mine:
            owned = rows(self.db, "SELECT id FROM nodes WHERE owner_member_id = ? AND revoked_at IS NULL", member["id"])
            if not owned:
                return "You have no connected node. Share your compute first to get a local baseline."
            return f"None of your nodes is online, sharing, and serving '{model_id}'."
        for local in mine:
            if not local["_digest"]:
                continue
            others = self.eligible_nodes(member["network_id"], model_id, local["_digest"], {local["id"]}, ignore_capacity=True)
            if others:
                return local, local["_digest"]
        return f"No other online worker serves '{model_id}' with the same model digest as your node."
