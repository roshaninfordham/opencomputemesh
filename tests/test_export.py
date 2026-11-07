"""Decision-export units: in-memory database, no coordinator, no network."""

import json

from ocm import decisions
from ocm.db import connect


def seed():
    db = connect(":memory:")
    db.execute("INSERT INTO networks (id, name, join_code_hash, created_at) VALUES ('n1', 't', 'h', 't')")
    db.execute("INSERT INTO members (id, network_id, nickname, created_at) VALUES ('m1', 'n1', 'dev', 't')")
    db.execute("""INSERT INTO nodes (id, network_id, owner_member_id, credential_hash, display_name,
                  runtime_name, runtime_scope, created_at)
                  VALUES ('node1', 'n1', 'm1', 'c', 'gpu-box', 'ollama', 'local', 't')""")
    jobs = [
        ("j1", "COMPLETED", None, 4, 2, 900, 120, "node1", "2026-01-01T00:00:00Z"),
        ("j2", "FAILED", "WORKER_TIMEOUT", None, None, None, None, "node1", "2026-01-02T00:00:00Z"),
        ("j3", "QUEUED", None, None, None, None, None, None, "2026-01-03T00:00:00Z"),
    ]
    for jid, status, code, it, ot, el, ttft, node, ts in jobs:
        db.execute("""INSERT INTO jobs (id, network_id, requester_member_id, idempotency_key, model_id,
                      status, failure_code, input_tokens, output_tokens, worker_elapsed_ms,
                      time_to_first_token_ms, selected_node_id, created_at)
                      VALUES (?, 'n1', 'm1', ?, 'qwen2.5:1.5b', ?, ?, ?, ?, ?, ?, ?, ?)""",
                   (jid, f"key-{jid}", status, code, it, ot, el, ttft, node, ts))
    db.commit()
    return db


def test_export_covers_terminal_jobs_only():
    out = decisions.export_decisions(seed())
    assert [r["job_id"] for r in out] == ["j2", "j1"]  # newest first, QUEUED excluded


def test_rows_carry_the_decision_and_its_outcome():
    (failed,) = [r for r in decisions.export_decisions(seed()) if r["job_id"] == "j2"]
    assert failed["model_id"] == "qwen2.5:1.5b"
    assert failed["node_id"] == "node1" and failed["node_runtime"] == "ollama"
    assert failed["node_runtime_scope"] == "local"
    assert failed["status"] == "FAILED" and failed["failure_code"] == "WORKER_TIMEOUT"
    assert set(failed) == {"job_id", "model_id", "model_digest", "route_reason", "node_id",
                           "node_runtime", "node_runtime_scope", "status", "failure_code",
                           "input_tokens", "output_tokens", "worker_elapsed_ms",
                           "time_to_first_token_ms", "settlement_capped", "created_at"}


def test_export_contains_no_text_anywhere():
    blob = json.dumps(decisions.export_decisions(seed()))
    for banned in ("prompt", "message", "content", "output_text", "ping", "hello"):
        assert banned not in blob


def test_since_and_limit_bound_the_export():
    db = seed()
    assert [r["job_id"] for r in decisions.export_decisions(db, since="2026-01-02T00:00:00Z")] == ["j2"]
    assert [r["job_id"] for r in decisions.export_decisions(db, limit=1)] == ["j2"]
