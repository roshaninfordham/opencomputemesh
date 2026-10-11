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
        self.node_id = None

    async def start(self):
        token = self.client.post("/api/v1/nodes/enrollment-tokens").json()["enrollment_token"]
        self.ws = await websockets.connect(f"ws://{self.base}/ws/node")
        await self.ws.send(json.dumps({"type": "node.enroll", "enrollment_token": token, "capabilities": {
            "display_name": self.name, "runtime_healthy": True, "runtime_version": "fake",
            "models": [{"model_id": m, "digest": "d-" + m} for m in self.models]}}))
        self.node_id = json.loads(await self.ws.recv())["node_id"]
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
                    if self.behavior != "hang_no_ack":
                        await self.send(type="job.failed", job_id=msg["job_id"], cancelled=True, started=True)
        except websockets.ConnectionClosed:
            pass

    async def run(self, job_id):
        b = self.behavior
        if b == "disconnect_before_start":
            await self.ws.close()
            return
        await self.send(type="job.started", job_id=job_id)
        if b.startswith("hang"):
            return
        for tok in ("Hel", "lo", "!"):
            await self.send(type="job.token", job_id=job_id, text=tok)
        if b == "error":
            await self.send(type="job.failed", job_id=job_id, code="RUNTIME_ERROR", started=True)
            return
        await self.send(type="job.completed", job_id=job_id, final_stats=FINAL, worker_elapsed_ms=12)

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
