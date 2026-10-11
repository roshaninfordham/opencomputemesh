"""Tiger Data / TimescaleDB sink against a real TimescaleDB (skips only if none is reachable).

Uses its own schema (search_path) so it never touches rows a live coordinator writes to `public`.
"""

import asyncio
import os
import socket
import threading
import time
import uuid

import pytest
import uvicorn

from ocm.app import create_app
from ocm.config import Settings
from ocm.timeseries import ensure_schema

from test_integration import FakeNode, member

psycopg = pytest.importorskip("psycopg", reason="psycopg not installed (install the `tiger` extra)")

SCHEMA = "ocm_test"
BASE_URL = os.environ.get("OCM_TEST_TIMESCALE_URL", "postgresql://postgres:ocmlocal@127.0.0.1:55432/ocm")
URL = f"{BASE_URL}?options=-csearch_path%3D{SCHEMA},public"


def _reachable() -> str | None:
    try:
        with psycopg.connect(BASE_URL, connect_timeout=2) as c:
            if not c.execute("SELECT 1 FROM pg_extension WHERE extname = 'timescaledb'").fetchone():
                return "timescaledb extension not created in the test database"
    except Exception as e:
        return f"no TimescaleDB at {BASE_URL.split('@')[-1]} ({type(e).__name__}); set OCM_TEST_TIMESCALE_URL"
    return None


pytestmark = pytest.mark.skipif(bool(reason := _reachable()), reason=reason or "")


@pytest.fixture(scope="module", autouse=True)
def fresh_schema():
    with psycopg.connect(BASE_URL, autocommit=True) as c:
        c.execute(f"DROP MATERIALIZED VIEW IF EXISTS {SCHEMA}.ocm_job_metrics_1m")
        c.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
        c.execute(f"CREATE SCHEMA {SCHEMA}")


def pg(sql, *args):
    with psycopg.connect(URL) as c:
        return c.execute(sql, args).fetchall()


def run_server(tmp_path, url):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    p = sock.getsockname()[1]
    sock.close()
    s = Settings(database_path=str(tmp_path / "db.sqlite"), data_dir=str(tmp_path), join_code="code1",
                 queue_timeout_seconds=2, job_timeout_seconds=5, node_offline_grace_seconds=0, port=p, timescale_url=url)
    app = create_app(s)
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=p, log_level="warning"))
    t = threading.Thread(target=srv.run, daemon=True)
    t.start()
    while not srv.started:
        time.sleep(0.02)
    return f"127.0.0.1:{p}", app, srv, t


@pytest.fixture
def ts_server(tmp_path):
    base, app, srv, t = run_server(tmp_path, URL)
    yield base, app
    srv.should_exit = True
    t.join(10)


async def until_row(job_id, timeout=8):
    async with asyncio.timeout(timeout):
        while True:
            r = await asyncio.to_thread(pg, "SELECT status, failure_code, input_tokens, output_tokens, token_source, ttft_ms, "
                                            "tokens_per_second, node_id, model_digest FROM ocm_job_metrics WHERE job_id = %s", job_id)
            if r:
                return r
            await asyncio.sleep(0.1)


async def test_schema_is_idempotent(ts_server):
    async with await psycopg.AsyncConnection.connect(URL, autocommit=True) as c:
        await ensure_schema(c)  # the server already ran it once on startup
        await ensure_schema(c)
    assert pg("SELECT hypertable_name, compression_enabled FROM timescaledb_information.hypertables WHERE hypertable_schema = %s",
              SCHEMA) == [("ocm_job_metrics", True)]
    assert pg("SELECT materialized_only FROM timescaledb_information.continuous_aggregates WHERE view_schema = %s", SCHEMA) == [(False,)]
    jobs = pg("""SELECT proc_name FROM timescaledb_information.jobs j
                 LEFT JOIN timescaledb_information.continuous_aggregates a ON a.materialization_hypertable_name = j.hypertable_name
                 WHERE j.hypertable_schema = %s OR a.view_schema = %s ORDER BY proc_name""", SCHEMA, SCHEMA)
    assert [j[0] for j in jobs] == ["policy_compression", "policy_refresh_continuous_aggregate"]


async def test_terminal_jobs_land_in_hypertable_aggregate_and_endpoint(ts_server):
    base, app = ts_server
    model, bad = f"ts-{uuid.uuid4().hex[:8]}", f"ts-{uuid.uuid4().hex[:8]}"
    prov, req = member(base, "prov"), member(base, "req")
    node = await FakeNode(base, prov, [model]).start()
    broken = await FakeNode(base, prov, [bad], behavior="error", name="broken").start()
    body = {"model": model, "messages": [{"role": "user", "content": "secret prompt"}], "max_tokens": 50}
    ok = (await asyncio.to_thread(req.post, "/api/v1/jobs", json={**body, "idempotency_key": str(uuid.uuid4())})).json()
    fail = (await asyncio.to_thread(req.post, "/api/v1/jobs", json={**body, "model": bad, "idempotency_key": str(uuid.uuid4())})).json()
    assert ok["receipt"]["status"] == "COMPLETED" and fail["receipt"]["status"] == "FAILED"

    rows = await until_row(ok["receipt"]["request_id"])
    assert rows == [("COMPLETED", None, 20, 3, "runtime_reported", rows[0][5], 1000.0, node.node_id, "d-" + model)]
    assert rows[0][5] is not None  # ttft measured
    assert (await until_row(fail["receipt"]["request_id"]))[0][:2] == ("FAILED", "RUNTIME_ERROR")
    # Never prompts or outputs: no column can hold them.
    cols = {c[0] for c in pg("SELECT column_name FROM information_schema.columns WHERE table_schema = %s AND table_name = 'ocm_job_metrics'", SCHEMA)}
    assert not cols & {"prompt", "messages", "output", "content", "text"}

    agg = pg("SELECT jobs, completed, input_tokens, output_tokens, avg_tokens_per_second FROM ocm_job_metrics_1m WHERE model_id = %s", model)
    assert agg == [(1, 1, 20, 3, 1000.0)]  # real-time aggregation: fresh rows appear before any refresh

    r = (await asyncio.to_thread(req.get, "/api/v1/network/throughput?minutes=60")).json()
    assert r["enabled"] is True and r["source"] == "timescaledb continuous aggregate ocm_job_metrics_1m"
    mine = {b["model_id"]: b for b in r["buckets"]}
    assert (mine[model]["completed"], mine[model]["output_tokens"]) == (1, 3)
    assert (mine[bad]["jobs"], mine[bad]["completed"]) == (1, 0)
    assert r["buffered_rows"] == 0 and r["dropped_rows"] == 0
    assert (await asyncio.to_thread(req.get, "/api/v1/network/throughput?minutes=0")).status_code == 422
    await node.close()
    await broken.close()


async def test_sink_outage_neither_fails_nor_delays_jobs(tmp_path):
    # A listener that accepts TCP but never speaks Postgres: connect hangs until connect_timeout (5 s).
    blackhole = socket.socket()
    blackhole.bind(("127.0.0.1", 0))
    blackhole.listen(16)
    url = f"postgresql://postgres:x@127.0.0.1:{blackhole.getsockname()[1]}/ocm"
    base, app, srv, t = run_server(tmp_path, url)
    try:
        model = f"ts-{uuid.uuid4().hex[:8]}"
        prov, req = member(base, "prov"), member(base, "req")
        node = await FakeNode(base, prov, [model]).start()
        t0 = time.monotonic()
        r = (await asyncio.to_thread(req.post, "/api/v1/jobs", json={"model": model, "idempotency_key": str(uuid.uuid4()),
                                                                       "messages": [{"role": "user", "content": "hi"}]})).json()
        assert r["receipt"]["status"] == "COMPLETED"
        assert time.monotonic() - t0 < 2  # far below the sink's 5 s connect timeout
        assert len(app.state.hub.timeseries.buffer) == 1  # kept for retry, not lost
        resp = await asyncio.to_thread(req.get, "/api/v1/network/throughput", timeout=15)
        assert resp.status_code == 503 and resp.json()["error"]["code"] == "TIMESERIES_UNAVAILABLE"  # never faked
        await node.close()
    finally:
        srv.should_exit = True
        t.join(15)
        blackhole.close()


async def test_disabled_endpoint_says_so(tmp_path):
    base, app, srv, t = run_server(tmp_path, "")
    try:
        assert member(base, "a").get("/api/v1/network/throughput").json() == {"enabled": False}
    finally:
        srv.should_exit = True
        t.join(5)
