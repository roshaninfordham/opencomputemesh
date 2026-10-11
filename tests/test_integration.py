"""Coordinator end-to-end over real sockets with scripted fake nodes (no Ollama needed).

Synthetic tokens exist only here; the product never fabricates them.
"""

import asyncio
import json
import socket
import threading
import time
import uuid

import httpx
import pytest
import uvicorn
import websockets

from ocm.app import create_app
from ocm.config import Settings
from ocm import ledger

FINAL = {"prompt_eval_count": 20, "eval_count": 3, "total_duration": 9_000_000, "eval_duration": 3_000_000,
         "load_duration": 1_000_000, "prompt_eval_duration": 2_000_000, "done_reason": "stop"}


@pytest.fixture
def server(tmp_path):
    port = socket.socket()
    port.bind(("127.0.0.1", 0))
    p = port.getsockname()[1]
    port.close()
    s = Settings(database_path=str(tmp_path / "db.sqlite"), data_dir=str(tmp_path), join_code="code1",
                 queue_timeout_seconds=2, job_timeout_seconds=5, node_offline_grace_seconds=0, port=p,
                 admin_token="a" * 32)
    app = create_app(s)
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=p, log_level="warning"))
    t = threading.Thread(target=srv.run, daemon=True)
    t.start()
    while not srv.started:
        time.sleep(0.02)
    yield f"127.0.0.1:{p}", app
    srv.should_exit = True
    t.join(5)


def member(base: str, nick: str) -> httpx.Client:
    c = httpx.Client(base_url=f"http://{base}", headers={"Origin": f"http://{base}"})
    r = c.post("/api/v1/session", json={"nickname": nick, "join_code": "code1"})
    assert r.status_code == 200, r.text
    return c


class FakeNode:
    """Speaks the node protocol. `behavior` decides what happens to each job."""

    def __init__(self, base, client, models, behavior="ok", name="fake"):
        self.base, self.client, self.models, self.behavior, self.name = base, client, models, behavior, name
        self.jobs: list[str] = []
        self.cancelled: list[str] = []
        self.node_id = self.credential = None
        self.final = FINAL

    async def start(self):
        token = self.client.post("/api/v1/nodes/enrollment-tokens").json()["enrollment_token"]
        self.ws = await websockets.connect(f"ws://{self.base}/ws/node")
        await self.ws.send(json.dumps({"type": "node.enroll", "enrollment_token": token, "capabilities": {
            "display_name": self.name, "runtime_healthy": True, "runtime_version": "fake",
            "models": [{"model_id": m, "digest": "d-" + m} for m in self.models]}}))
        reg = json.loads(await self.ws.recv())
        self.node_id, self.credential = reg["node_id"], reg["credential"]
        self.task = asyncio.create_task(self.loop())
        return self

    async def send(self, **m):
        await self.ws.send(json.dumps(m))

    async def loop(self):
        try:
            async for raw in self.ws:
                msg = json.loads(raw)
                if msg["type"] == "job.start":
                    self.jobs.append(msg["job_id"])
                    asyncio.create_task(self.run(msg["job_id"]))
                elif msg["type"] == "job.cancel":
                    self.cancelled.append(msg["job_id"])
                    if self.behavior not in ("hang_no_ack", "late_start"):
                        await self.send(type="job.failed", job_id=msg["job_id"], cancelled=True, started=True)
        except websockets.ConnectionClosed:
            pass

    async def run(self, job_id):
        b = self.behavior
        if b == "disconnect_before_start":
            await self.ws.close()
            return
        if b == "late_start":  # job.started crosses the coordinator's job.cancel on the wire
            while job_id not in self.cancelled:
                await asyncio.sleep(0.02)
            await self.send(type="job.started", job_id=job_id)
            await self.send(type="job.failed", job_id=job_id, cancelled=True, started=True)
            return
        await self.send(type="job.started", job_id=job_id)
        if b.startswith("hang"):
            return
        for tok in ("Hel", "lo", "!"):
            await self.send(type="job.token", job_id=job_id, text=tok)
        if b == "error":
            await self.send(type="job.failed", job_id=job_id, code="RUNTIME_ERROR", started=True)
            return
        await self.send(type="job.completed", job_id=job_id, final_stats=self.final, worker_elapsed_ms=12)

    async def close(self):
        await self.ws.close()


async def browser(base, client: httpx.Client):
    ticket = client.post("/api/v1/ws-ticket").json()["ticket"]
    return await websockets.connect(f"ws://{base}/ws/client?ticket={ticket}", origin=f"http://{base}")


async def until(ws, *types, timeout=8):
    seen = []
    async with asyncio.timeout(timeout):
        while True:
            m = json.loads(await ws.recv())
            seen.append(m)
            if m["type"] in types:
                return m, seen


def start_msg(model="m1", key=None, **kw):
    return json.dumps({"type": "request.start", "idempotency_key": key or str(uuid.uuid4()), "model": model,
                       "messages": [{"role": "user", "content": "hi"}], "max_tokens": 50, **kw})


def balances(app, *clients):
    return [ledger.balance(app.state.db, c.get("/api/v1/session").json()["member"]["id"]) for c in clients]


async def test_routing_streaming_and_settlement(server):
    base, app = server
    prov, req = member(base, "prov"), member(base, "req")
    a = await FakeNode(base, prov, ["m1"], name="A").start()
    b = await FakeNode(base, prov, ["m2"], name="B").start()
    ws = await browser(base, req)
    await ws.send(start_msg("m2"))
    done, seen = await until(ws, "request.completed")
    assert b.jobs and not a.jobs  # only the m2 host was eligible
    assert "".join(m["text"] for m in seen if m["type"] == "request.token") == "Hello!"
    assert [m["node_name"] for m in seen if m["type"] == "request.routed"] == ["B"]
    r = done["receipt"]
    assert r["tokens"] == {"input": 20, "output": 3, "total": 23, "source": "runtime_reported"}
    assert r["credits"]["settled_micro_occ"] == 29_000 and r["credits"]["settlement_status"] == "settled"
    assert r["credits"]["capped"] is False and r["credits"]["measured_micro_occ"] == 29_000
    assert balances(app, req, prov) == [100_000_000 - 29_000, 100_000_000 + 29_000]
    assert ledger.consistency_report(app.state.db)["ok"]
    # Unknown model -> clear error with real alternatives.
    await ws.send(start_msg("nope"))
    fail, _ = await until(ws, "request.failed")
    assert fail["code"] == "MODEL_UNAVAILABLE" and fail["details"]["available_models"] == ["m1", "m2"]
    await ws.close(); await a.close(); await b.close()


async def test_idempotent_replay_runs_once(server):
    base, app = server
    prov, req = member(base, "prov"), member(base, "req")
    node = await FakeNode(base, prov, ["m1"]).start()
    ws = await browser(base, req)
    key = str(uuid.uuid4())
    await ws.send(start_msg(key=key))
    await until(ws, "request.completed")
    await ws.send(start_msg(key=key))
    state, _ = await until(ws, "request.state")
    assert state["receipt"]["status"] == "COMPLETED" and len(node.jobs) == 1
    debits = app.state.db.execute("SELECT COUNT(*) FROM ledger WHERE entry_type='job_debit'").fetchone()[0]
    assert debits == 1
    await ws.close(); await node.close()


async def test_worker_error_releases_and_pays_nothing(server):
    base, app = server
    prov, req = member(base, "prov"), member(base, "req")
    node = await FakeNode(base, prov, ["m1"], behavior="error").start()
    ws = await browser(base, req)
    await ws.send(start_msg())
    fail, _ = await until(ws, "request.failed")
    assert fail["code"] == "RUNTIME_ERROR" and fail["receipt"]["retry_count"] == 0  # started work is never retried
    assert balances(app, req, prov) == [100_000_000, 100_000_000]
    assert ledger.consistency_report(app.state.db)["ok"]
    await ws.close(); await node.close()


async def test_disconnect_before_start_retries_on_other_worker(server):
    base, app = server
    prov, req = member(base, "prov"), member(base, "req")
    flaky = await FakeNode(base, prov, ["m1"], behavior="disconnect_before_start", name="flaky").start()
    ws = await browser(base, req)
    good = None
    await ws.send(start_msg())
    # Bring a healthy worker online while the first one is being tried.
    good = await FakeNode(base, prov, ["m1"], name="good").start()
    done, seen = await until(ws, "request.completed")
    assert done["receipt"]["retry_count"] == 1 and done["receipt"]["node"]["display_name"] == "good"
    assert len(good.jobs) == 1
    await ws.close(); await good.close()


async def test_timeout_and_cancel(server):
    base, app = server
    prov, req = member(base, "prov"), member(base, "req")
    node = await FakeNode(base, prov, ["m1"], behavior="hang").start()
    ws = await browser(base, req)
    await ws.send(start_msg())
    created, _ = await until(ws, "request.started")
    await ws.send(json.dumps({"type": "request.cancel", "request_id": created["request_id"]}))
    c, _ = await until(ws, "request.cancelled")
    assert c["receipt"]["status"] == "CANCELLED" and c["receipt"]["cancel_effect"] == "runtime_stream_closed"
    assert node.cancelled == [created["request_id"]]
    # A worker that hangs past the deadline expires; reservation is returned.
    node.behavior = "hang_no_ack"
    await ws.send(start_msg())
    e, _ = await until(ws, "request.failed", timeout=12)
    assert e["code"] == "JOB_TIMEOUT"
    assert balances(app, req, prov) == [100_000_000, 100_000_000]
    await ws.close(); await node.close()


async def test_insufficient_credits(server):
    base, app = server
    prov, req = member(base, "prov"), member(base, "req")
    node = await FakeNode(base, prov, ["m1"]).start()
    ws = await browser(base, req)
    db = app.state.db
    mid = req.get("/api/v1/session").json()["member"]["id"]
    with db:
        ledger._entry(db, network_id="demo", member_id=mid, entry_type="adjustment", amount=-ledger.balance(db, mid),
                      key="drain")
    await ws.send(start_msg())
    f, _ = await until(ws, "request.failed")
    assert f["code"] == "INSUFFICIENT_CREDITS" and not node.jobs
    await ws.close(); await node.close()


async def test_pause_and_authorization(server):
    base, app = server
    prov, req = member(base, "prov"), member(base, "req")
    node = await FakeNode(base, prov, ["m1"]).start()
    assert req.post(f"/api/v1/nodes/{node.node_id}/pause").status_code == 403  # not the owner
    assert prov.post(f"/api/v1/nodes/{node.node_id}/pause").json()["node"]["status"] == "PAUSED"
    ws = await browser(base, req)
    await ws.send(start_msg())
    f, _ = await until(ws, "request.failed")
    assert f["code"] == "MODEL_UNAVAILABLE" and not node.jobs
    prov.post(f"/api/v1/nodes/{node.node_id}/resume")
    await ws.send(start_msg())
    done, _ = await until(ws, "request.completed")
    jid = done["request_id"]
    other = member(base, "snoop")
    assert other.get(f"/api/v1/jobs/{jid}").status_code == 404
    assert prov.get(f"/api/v1/jobs/{jid}/receipt").status_code == 200  # provider sees the receipt
    # Cross-origin writes are rejected; admin needs the admin token, not a session.
    assert httpx.post(f"http://{base}/api/v1/ws-ticket", cookies=req.cookies,
                      headers={"Origin": "https://evil.example"}).status_code == 403
    assert req.get("/api/v1/admin/metrics").status_code == 403
    adm = httpx.get(f"http://{base}/api/v1/admin/metrics", headers={"Authorization": "Bearer " + "a" * 32}).json()
    assert adm["ledger_consistency"]["ok"]
    # Revoked credentials can no longer connect.
    assert prov.delete(f"/api/v1/nodes/{node.node_id}").status_code == 200
    await ws.close()


async def test_node_cannot_report_on_foreign_jobs(server):
    base, app = server
    prov, req = member(base, "prov"), member(base, "req")
    slow = await FakeNode(base, prov, ["m1"], behavior="hang", name="slow").start()
    rogue = await FakeNode(base, prov, ["m9"], name="rogue").start()
    ws = await browser(base, req)
    await ws.send(start_msg())
    started, _ = await until(ws, "request.started")
    await rogue.send(type="job.completed", job_id=started["request_id"], final_stats=FINAL)
    await asyncio.sleep(0.3)
    status = app.state.db.execute("SELECT status FROM jobs WHERE id = ?", (started["request_id"],)).fetchone()[0]
    assert status == "RUNNING"
    await ws.close(); await slow.close(); await rogue.close()


# ---------- hardening ----------

async def test_overreported_usage_is_capped_at_the_hold(server):
    base, app = server
    prov, req = member(base, "prov"), member(base, "req")
    node = await FakeNode(base, prov, ["m1"]).start()
    node.final = {**FINAL, "prompt_eval_count": 10**9, "eval_count": 10**9}
    ws = await browser(base, req)
    await ws.send(start_msg())
    done, _ = await until(ws, "request.completed")
    c = done["receipt"]["credits"]
    assert c["settled_micro_occ"] == c["reserved_micro_occ"] > 0
    assert c["capped"] is True and c["measured_micro_occ"] == ledger.cost_micro(
        app.state.hub.active_tariff("demo"), 10**9, 10**9) > c["reserved_micro_occ"]
    meta = app.state.db.execute("SELECT safe_metadata_json FROM job_events WHERE job_id = ? AND event_type = 'credit_settled'",
                                (done["request_id"],)).fetchone()[0]
    assert json.loads(meta) == {"amount_micro_occ": c["reserved_micro_occ"], "capped": True,
                                "measured_micro_occ": c["measured_micro_occ"]}
    assert balances(app, req, prov) == [100_000_000 - c["reserved_micro_occ"], 100_000_000 + c["reserved_micro_occ"]]
    assert ledger.consistency_report(app.state.db)["ok"]
    await ws.close(); await node.close()


async def test_node_reconnect_fails_jobs_of_replaced_socket(server):
    base, app = server
    prov, req = member(base, "prov"), member(base, "req")
    node = await FakeNode(base, prov, ["m1"], behavior="hang").start()
    ws = await browser(base, req)
    await ws.send(start_msg())
    await until(ws, "request.started")
    ws2 = await websockets.connect(f"ws://{base}/ws/node")
    await ws2.send(json.dumps({"type": "node.auth", "node_id": node.node_id, "credential": node.credential,
                               "capabilities": {"display_name": "fake", "runtime_healthy": True,
                                                "models": [{"model_id": "m1", "digest": "d-m1"}]}}))
    f, _ = await until(ws, "request.failed", timeout=3)  # not JOB_TIMEOUT after 5 s
    assert f["code"] == "WORKER_DISCONNECTED"
    assert balances(app, req, prov) == [100_000_000, 100_000_000]
    await ws.close(); await ws2.close()


async def test_malformed_ws_messages_do_not_kill_sockets(server):
    base, app = server
    prov, req = member(base, "prov"), member(base, "req")
    node = await FakeNode(base, prov, ["m1"]).start()
    ws = await browser(base, req)
    await ws.send("[1]")
    e, _ = await until(ws, "error")
    assert e["code"] == "INVALID_MESSAGE"
    await ws.send(json.dumps({"type": "client.ping"}))
    await until(ws, "client.pong")
    for raw in ("[1]", "null", json.dumps({"type": "job.token", "job_id": ["x"]})):
        await node.ws.send(raw)
    for mem in ("lots", {"a": 1}, 2**70, True):
        await node.send(type="node.heartbeat", runtime_healthy=True, memory_available_bytes=mem)
    await ws.send(start_msg())
    await until(ws, "request.completed")  # node socket survived
    v = app.state.db.execute("SELECT memory_available_bytes FROM nodes WHERE id = ?", (node.node_id,)).fetchone()[0]
    assert v is None
    await ws.close(); await node.close()


async def test_cancel_races_and_queued_cancel(server):
    base, app = server
    prov, req = member(base, "prov"), member(base, "req")
    node = await FakeNode(base, prov, ["m1"], behavior="late_start").start()
    ws = await browser(base, req)
    # job.started arriving after the cancel was sent must not crash the job.
    await ws.send(start_msg())
    routed, _ = await until(ws, "request.routed")
    await ws.send(json.dumps({"type": "request.cancel", "request_id": routed["request_id"]}))
    c, _ = await until(ws, "request.cancelled", "request.failed")
    assert c["type"] == "request.cancelled" and c["receipt"]["status"] == "CANCELLED"
    # Worker vanishing while a cancel is pending ends CANCELLED, without waiting for the ack timeout.
    node.behavior = "hang_no_ack"
    await ws.send(start_msg())
    started, _ = await until(ws, "request.started")
    await ws.send(json.dumps({"type": "request.cancel", "request_id": started["request_id"]}))
    while not node.cancelled[1:]:
        await asyncio.sleep(0.02)
    await node.close()
    c, _ = await until(ws, "request.cancelled", "request.failed", timeout=3)
    assert c["type"] == "request.cancelled" and c["receipt"]["cancel_effect"] == "worker_disconnected"
    # A queued job (worker busy) cancels before dispatch and returns its hold; cancelling it again is a no-op.
    busy = await FakeNode(base, prov, ["m1"], behavior="hang").start()
    await ws.send(start_msg())
    await until(ws, "request.started")
    await ws.send(start_msg())
    created, _ = await until(ws, "request.created")
    await until(ws, "request.queued")
    await ws.send(json.dumps({"type": "request.cancel", "request_id": created["request_id"]}))
    c, _ = await until(ws, "request.cancelled")
    assert c["request_id"] == created["request_id"] and c["receipt"]["cancel_effect"] == "cancelled_before_dispatch"
    assert c["receipt"]["node"] is None and len(busy.jobs) == 1
    assert req.post(f"/api/v1/jobs/{created['request_id']}/cancel").json() == {"ok": True, "status": "CANCELLED"}
    assert ledger.consistency_report(app.state.db)["ok"]
    await ws.close(); await busy.close()


async def test_enrollment_token_claim_is_atomic(server, monkeypatch):
    base, app = server
    prov = member(base, "prov")
    token = prov.post("/api/v1/nodes/enrollment-tokens").json()["enrollment_token"]
    db, real = app.state.db, __import__("ocm.app", fromlist=["new_id"]).new_id

    def racing_new_id():  # a concurrent enroll claims the token between the read and the UPDATE
        with db:
            db.execute("UPDATE enrollment_tokens SET used_at = 'x'")
        return real()
    monkeypatch.setattr("ocm.app.new_id", racing_new_id)
    ws = await websockets.connect(f"ws://{base}/ws/node")
    await ws.send(json.dumps({"type": "node.enroll", "enrollment_token": token,
                              "capabilities": {"display_name": "x", "models": []}}))
    with pytest.raises(websockets.ConnectionClosed) as e:
        await ws.recv()
    assert e.value.rcvd.code == 4401
    assert db.execute("SELECT COUNT(*) FROM nodes").fetchone()[0] == 0


def test_http_hardening(server):
    base, app = server
    req = member(base, "req")
    for path in ("/", "/api/v1/session", "/api/nope"):
        r = req.get(path)
        assert r.headers["x-content-type-options"] == "nosniff" and r.headers["referrer-policy"] == "no-referrer"
        assert r.headers["x-frame-options"] == "DENY" and "frame-ancestors 'none'" in r.headers["content-security-policy"]
    r = req.get("/api/nope")
    assert r.status_code == 404 and r.json()["error"]["code"] == "NOT_FOUND"
    assert req.get("/api/v1/jobs?offset=-1").status_code == 422
    assert req.get(f"/api/v1/me/ledger?offset={10**20}").status_code == 422  # was a 500 (sqlite OverflowError)
    assert req.get("/api/v1/jobs?limit=5").json()["limit"] == 5
    r = req.post("/api/v1/jobs", json={"model": "m1", "idempotency_key": "k" * 8,
                                       "messages": [{"role": "bogus", "content": "SECRET-PROMPT-123"}]})
    assert r.status_code == 422 and "SECRET-PROMPT-123" not in r.text
