import json
import sqlite3
from pathlib import Path

import pytest

from ocm import ledger
from ocm.db import connect, insert, now
from ocm.hub import IllegalTransition, transition
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
