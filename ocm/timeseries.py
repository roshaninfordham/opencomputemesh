"""Optional Tiger Data (TimescaleDB) sink for per-job metrics. Enabled only by OCM_TIMESCALE_URL.

SQLite stays the source of truth; this is a derived, best-effort copy for time-series queries.
Only metric columns are written, never prompts or outputs. `record()` never awaits: the job path
appends to an in-memory buffer and a background task owns the Postgres connection.
"""

import asyncio
import collections
import logging

from .db import parse_ts

try:
    import psycopg
    from psycopg.rows import dict_row
except ImportError:  # the `tiger` extra is not installed: the sink stays a no-op
    psycopg = None

log = logging.getLogger("ocm.timeseries")

SOURCE = "timescaledb continuous aggregate ocm_job_metrics_1m"
BUFFER_ROWS = 10_000
BATCH_ROWS = 500
CONNECT_TIMEOUT_S = 5
READ_TIMEOUT_S = 5

# Column -> jobs-table field. Nothing else leaves the coordinator.
COLUMNS = {
    "job_id": "id", "network_id": "network_id", "kind": "kind", "model_id": "model_id", "model_digest": "model_digest",
    "node_id": "selected_node_id", "status": "status", "failure_code": "failure_code", "input_tokens": "input_tokens",
    "output_tokens": "output_tokens", "token_source": "token_count_source", "queue_wait_ms": "queue_wait_ms",
    "ttft_ms": "time_to_first_token_ms", "worker_elapsed_ms": "worker_elapsed_ms", "runtime_total_ms": "runtime_total_ms",
    "runtime_load_ms": "runtime_load_ms", "generation_ms": "generation_ms", "tokens_per_second": "tokens_per_second",
    "end_to_end_ms": "end_to_end_ms", "settled_micro_occ": "settled_credits",
}
INSERT = (f"INSERT INTO ocm_job_metrics (time, {', '.join(COLUMNS)}) VALUES (%s{', %s' * len(COLUMNS)}) "
          "ON CONFLICT (job_id, time) DO NOTHING")  # retried batches are idempotent: time is the job's completed_at

# Each statement is idempotent; run one at a time in autocommit (continuous aggregate DDL refuses transactions).
SCHEMA = [
    """CREATE TABLE IF NOT EXISTS ocm_job_metrics (
         time TIMESTAMPTZ NOT NULL, job_id TEXT NOT NULL, network_id TEXT, kind TEXT, model_id TEXT, model_digest TEXT,
         node_id TEXT, status TEXT NOT NULL, failure_code TEXT, input_tokens INTEGER, output_tokens INTEGER,
         token_source TEXT, queue_wait_ms INTEGER, ttft_ms INTEGER, worker_elapsed_ms INTEGER, runtime_total_ms INTEGER,
         runtime_load_ms INTEGER, generation_ms INTEGER, tokens_per_second DOUBLE PRECISION, end_to_end_ms INTEGER,
         settled_micro_occ BIGINT, UNIQUE (job_id, time))""",
    "SELECT create_hypertable('ocm_job_metrics', by_range('time', INTERVAL '1 day'), if_not_exists => TRUE)",
    # Changing compression settings once chunks are compressed errors, so only enable it the first time.
    """DO $$ BEGIN
         IF NOT (SELECT compression_enabled FROM timescaledb_information.hypertables
                 WHERE hypertable_name = 'ocm_job_metrics' AND hypertable_schema = current_schema()) THEN
           ALTER TABLE ocm_job_metrics SET (timescaledb.compress, timescaledb.compress_segmentby = 'model_id',
                                            timescaledb.compress_orderby = 'time DESC');
         END IF;
       END $$""",
    "SELECT add_compression_policy('ocm_job_metrics', INTERVAL '7 days', if_not_exists => TRUE)",
    # Token sums count only exact runtime-reported usage; NULL means no exact usage in that bucket.
    """CREATE MATERIALIZED VIEW IF NOT EXISTS ocm_job_metrics_1m
       WITH (timescaledb.continuous, timescaledb.materialized_only = false) AS
       SELECT time_bucket(INTERVAL '1 minute', time) AS bucket, network_id, model_id,
              count(*) AS jobs, count(*) FILTER (WHERE status = 'COMPLETED') AS completed,
              sum(input_tokens) FILTER (WHERE token_source = 'runtime_reported') AS input_tokens,
              sum(output_tokens) FILTER (WHERE token_source = 'runtime_reported') AS output_tokens,
              avg(ttft_ms)::float8 AS avg_ttft_ms,
              percentile_cont(0.95) WITHIN GROUP (ORDER BY ttft_ms) AS p95_ttft_ms,
              avg(end_to_end_ms)::float8 AS avg_e2e_ms,
              percentile_cont(0.95) WITHIN GROUP (ORDER BY end_to_end_ms) AS p95_e2e_ms,
              avg(tokens_per_second) AS avg_tokens_per_second
       FROM ocm_job_metrics GROUP BY bucket, network_id, model_id WITH NO DATA""",
    """SELECT add_continuous_aggregate_policy('ocm_job_metrics_1m', start_offset => INTERVAL '1 day',
         end_offset => INTERVAL '1 minute', schedule_interval => INTERVAL '1 minute', if_not_exists => TRUE)""",
]

THROUGHPUT = """SELECT bucket, model_id, jobs, completed, input_tokens, output_tokens, avg_ttft_ms, p95_ttft_ms,
                       avg_e2e_ms, p95_e2e_ms, avg_tokens_per_second
                FROM ocm_job_metrics_1m WHERE network_id = %s AND bucket >= now() - make_interval(mins => %s)
                ORDER BY bucket, model_id"""


async def ensure_schema(conn) -> None:
    # IF NOT EXISTS is not race-safe: serialize coordinators starting at once against the same database.
    await conn.execute("SELECT pg_advisory_lock(hashtext('ocm_job_metrics'))")
    try:
        for stmt in SCHEMA:
            await conn.execute(stmt)
    finally:
        await conn.execute("SELECT pg_advisory_unlock(hashtext('ocm_job_metrics'))")


class Sink:
    def __init__(self, url: str):
        self.url = url
        self.enabled = bool(url) and psycopg is not None
        # ponytail: in-memory buffer, BUFFER_ROWS ceiling; on a long outage the oldest rows drop and a restart loses
        # what is buffered. Use a SQLite outbox table if metrics must survive both.
        self.buffer: collections.deque = collections.deque(maxlen=BUFFER_ROWS)
        self.dropped = 0
        self.conn = None
        self._lock = asyncio.Lock()
        self._wake = asyncio.Event()
        self._task: asyncio.Task | None = None
        if url and psycopg is None:
            log.warning("OCM_TIMESCALE_URL is set but psycopg is not installed (pip install 'opencomputemesh[tiger]'); "
                        "time-series sink disabled")

    def record(self, job: dict) -> None:
        """Queue one terminal job's metrics. Never blocks, never raises into the job path."""
        if not self.enabled:
            return
        try:
            row = (parse_ts(job["completed_at"]), *(job.get(f) for f in COLUMNS.values()))
        except Exception as e:
            log.warning("timeseries: could not build row for job %s (%s)", job.get("id"), type(e).__name__)
            return
        if len(self.buffer) == BUFFER_ROWS:
            self.dropped += 1
        self.buffer.append(row)
        self._wake.set()

    async def start(self) -> None:
        """Start the writer; it connects and ensures the schema right away without delaying startup."""
        if self.enabled:
            self._task = asyncio.create_task(self._writer())
            self._wake.set()

    async def close(self) -> None:
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        if self.buffer and self.conn:
            try:
                await asyncio.wait_for(self._flush(), 2)
            except Exception:
                pass
        if self.buffer:
            log.warning("timeseries: %d buffered rows not written at shutdown", len(self.buffer))
        await self._reset()

    async def _connect(self):
        async with self._lock:
            if self.conn is None or self.conn.closed:
                self.conn = await psycopg.AsyncConnection.connect(self.url, autocommit=True,
                                                                  connect_timeout=CONNECT_TIMEOUT_S)
                await ensure_schema(self.conn)
                log.info("timeseries: connected to TimescaleDB, schema ready")
            return self.conn

    async def _reset(self) -> None:
        conn, self.conn = self.conn, None
        if conn is not None:
            try:
                await asyncio.wait_for(conn.close(), 2)
            except Exception:
                pass

    async def _flush(self) -> None:
        while self.buffer:
            conn = await self._connect()
            batch = [self.buffer.popleft() for _ in range(min(len(self.buffer), BATCH_ROWS))]
            try:
                async with conn.cursor() as cur:
                    await cur.executemany(INSERT, batch)
            except BaseException:
                self.dropped += max(0, len(self.buffer) + len(batch) - BUFFER_ROWS)
                self.buffer.extendleft(reversed(batch))
                raise

    async def _writer(self) -> None:
        delay = 1.0
        while True:
            await self._wake.wait()
            self._wake.clear()
            try:
                await self._connect()  # also (re)creates the schema
                await self._flush()
                delay = 1.0
            except asyncio.CancelledError:
                raise
            except Exception as e:  # never log row content or the URL (it holds the password)
                log.warning("timeseries: write failed (%s); %d rows buffered, retry in %.0fs",
                            type(e).__name__, len(self.buffer), delay)
                await self._reset()
                await asyncio.sleep(delay)
                delay = min(delay * 2, 30.0)
                self._wake.set()

    async def throughput(self, network_id: str, minutes: int) -> list[dict]:
        async def q():
            conn = await self._connect()
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(THROUGHPUT, (network_id, minutes))
                return await cur.fetchall()
        try:
            out = await asyncio.wait_for(q(), READ_TIMEOUT_S)
        except BaseException:
            await self._reset()
            raise
        for r in out:
            r["bucket"] = r["bucket"].isoformat()
        return out
