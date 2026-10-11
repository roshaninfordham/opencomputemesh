import asyncio
import json
import random
import sqlite3
from pathlib import Path

import pytest

from ocm import ledger
from ocm.db import connect, insert, now
from ocm.app import RateLimiter
from ocm.config import Settings
from ocm.hub import Hub, IllegalTransition, transition
from ocm.metrics import normalize_ollama
from ocm.runtime import NDJSONDecoder

FIXTURE = Path(__file__).parent / "fixtures" / "ollama_chat_stream.ndjson"


def test_ndjson_split_chunks_and_malformed():
    raw = FIXTURE.read_text()
    d = NDJSONDecoder()
    chunks = []
    for i in range(0, len(raw), 7):  # split mid-line everywhere
        chunks += d.feed(raw[i:i + 7])
    chunks += d.flush()
    assert chunks[-1]["done"] is True and chunks[-1]["eval_count"] == 10
    assert "".join(c["message"]["content"] for c in chunks).strip()
    d2 = NDJSONDecoder()
    assert d2.feed('{"a":1}\nnot json\n{"b"') == [{"a": 1}]
    assert d2.malformed == 1 and d2.flush() == [] and d2.malformed == 2


def test_normalize_metrics():
    final = json.loads(FIXTURE.read_text().splitlines()[-1])
    m = normalize_ollama(final)
    assert m["input_tokens"] == 30 and m["output_tokens"] == 10
    assert m["token_count_source"] == "runtime_reported"
    assert m["runtime_load_ms"] == 27417 and m["generation_ms"] == 146
    assert m["tokens_per_second"] == round(10 / 146465918 * 1e9, 2)
    empty = normalize_ollama({"eval_count": 5, "eval_duration": 0})
    assert empty["tokens_per_second"] is None and empty["input_tokens"] is None
    assert empty["token_count_source"] == "partial_runtime_reported"
    assert normalize_ollama({})["token_count_source"] == "unavailable"


TARIFF = {"input_micro_per_1k": 1_000_000, "output_micro_per_1k": 3_000_000, "min_charge_micro": 0}


def test_tariff_rounding():
    assert ledger.cost_micro(TARIFF, 35, 11) == 68_000
    assert ledger.cost_micro({**TARIFF, "input_micro_per_1k": 1}, 1, 0) == 1  # rounds up, never down to 0
    assert ledger.cost_micro({**TARIFF, "min_charge_micro": 500_000}, 1, 1) == 500_000
    assert ledger.format_occ(68_000) == "0.068" and ledger.format_occ(-100_000_000) == "-100"


@pytest.fixture
def db():
    c = connect(":memory:")
    with c:
        insert(c, "networks", {"id": "n", "name": "n", "join_code_hash": "x", "created_at": now()})
        for m in ("req", "prov"):
            insert(c, "members", {"id": m, "network_id": "n", "nickname": m, "created_at": now()})
        insert(c, "tariffs", {"version": "t", "network_id": "n", "input_micro_per_1k": 1, "output_micro_per_1k": 1,
                              "min_charge_micro": 0, "active_at": now()})
        ledger.grant(c, "n", "req", 1_000_000, "demo_join")
    return c


def make_job(c, jid="j1", status="CREATED"):
    job = {"id": jid, "network_id": "n", "requester_member_id": "req", "idempotency_key": jid, "model_id": "m",
           "status": status, "created_at": now(), "tariff_version": "t"}
    with c:
        insert(c, "jobs", job)
    return job


def test_ledger_reserve_settle_idempotent(db):
    job = make_job(db)
    with db:
        ledger.reserve(db, job, 400_000)
    assert ledger.balance(db, "req") == 600_000
    with db:
        assert ledger.settle(db, job, "prov", 100_000) is True
    with db:
        assert ledger.settle(db, job, "prov", 100_000) is False  # duplicate settlement is a no-op
    assert ledger.balance(db, "req") == 900_000 and ledger.balance(db, "prov") == 100_000
    db.execute("UPDATE jobs SET status='COMPLETED'")
    assert ledger.consistency_report(db)["ok"]


def test_ledger_insufficient_and_release(db):
    job = make_job(db)
    with pytest.raises(ledger.InsufficientCredits):
        with db:
            ledger.reserve(db, job, 2_000_000)
    with db:
        ledger.reserve(db, job, 500_000)
        ledger.release(db, job)
        ledger.release(db, job)
    assert ledger.balance(db, "req") == 1_000_000 and ledger.balance(db, "prov") == 0


def test_ledger_append_only_and_reversal(db):
    with pytest.raises(sqlite3.DatabaseError):
        db.execute("UPDATE ledger SET amount_micro_occ = 5")
    with pytest.raises(sqlite3.DatabaseError):
        db.execute("DELETE FROM ledger")
    with db:
        assert ledger.reverse(db, 1, "test") and not ledger.reverse(db, 1, "test")
    assert ledger.balance(db, "req") == 0 and ledger.consistency_report(db)["ok"]


def test_state_machine(db):
    make_job(db)
    with db:
        transition(db, "j1", "QUEUED")
        transition(db, "j1", "DISPATCHED")
        with pytest.raises(IllegalTransition):
            transition(db, "j1", "CANCELLED")  # running work must go through CANCEL_REQUESTED
        transition(db, "j1", "RUNNING")
        transition(db, "j1", "COMPLETED")
        assert transition(db, "j1", "COMPLETED") is False  # terminal is idempotent
        with pytest.raises(IllegalTransition):
            transition(db, "j1", "FAILED")


async def test_wait_for_node_drains_stale_events_before_cancel():
    hub = Hub(connect(":memory:"), Settings(queue_timeout_seconds=0))
    q = asyncio.Queue()
    q.put_nowait({"type": "job.token"})  # left over from a failed first dispatch
    q.put_nowait({"type": "_cancel"})
    assert await hub._wait_for_node({"network_id": "n", "model_id": "m"}, q, None, set(), None) == "cancelled"
    assert hub.queued == 0


def test_rate_limiter_prunes_idle_keys(monkeypatch):
    t = [1000.0]
    monkeypatch.setattr("ocm.app.time.monotonic", lambda: t[0])
    rl = RateLimiter()
    for i in range(100):
        assert rl.allow(f"join:10.0.0.{i}", 1, 60)
    assert not rl.allow("join:10.0.0.1", 1, 60)
    t[0] += 61
    assert rl.allow("join:fresh", 1, 60)
    assert list(rl.hits) == ["join:fresh"]


def test_recover_after_restart_releases_once(db):
    job = make_job(db, status="RUNNING")
    with db:
        ledger.reserve(db, job, 300_000)
    hub = Hub(db, Settings())
    hub.recover_after_restart()
    hub.recover_after_restart()
    assert ledger.balance(db, "req") == 1_000_000
    assert db.execute("SELECT status, failure_code FROM jobs").fetchone()[:] == ("FAILED", "COORDINATOR_RESTART")
    assert db.execute("SELECT COUNT(*) FROM ledger WHERE entry_type = 'reservation_release'").fetchone()[0] == 1
    assert ledger.consistency_report(db)["ok"]


def test_ledger_property_random_ops(db):
    rnd = random.Random(1234)
    with db:
        ledger.grant(db, "n", "prov", 1_000_000, "demo_join")
    jobs = []
    for step in range(400):
        op = rnd.choice(["new", "settle", "release", "dup"])
        if op == "new" or not jobs:
            j = make_job(db, f"j{step}", status="RUNNING")
            j["requester_member_id"] = rnd.choice(["req", "prov"])
            db.execute("UPDATE jobs SET requester_member_id = ? WHERE id = ?", (j["requester_member_id"], j["id"]))
            try:
                with db:
                    ledger.reserve(db, j, rnd.randint(0, 200_000))
            except ledger.InsufficientCredits:
                pass
            jobs.append(j)
        else:
            j = rnd.choice(jobs)
            with db:
                if op == "release":
                    ledger.release(db, j)
                else:  # settle, or a duplicate settle; provider may be the requester (own node)
                    ledger.settle(db, j, rnd.choice(["req", "prov"]), rnd.randint(0, 50_000))
                db.execute("UPDATE jobs SET status = 'COMPLETED' WHERE id = ?", (j["id"],))
        rep = ledger.consistency_report(db)
        assert rep["ok"], (step, rep)
        for m in ("req", "prov"):
            assert ledger.balance(db, m) == sum(r[0] for r in db.execute(
                "SELECT amount_micro_occ FROM ledger WHERE member_id = ?", (m,)))
    total = db.execute("SELECT SUM(amount_micro_occ) FROM ledger").fetchone()[0]
    assert total == 2_000_000 + db.execute(
        "SELECT COALESCE(SUM(amount_micro_occ),0) FROM ledger WHERE entry_type IN ('reservation','reservation_release')").fetchone()[0]
