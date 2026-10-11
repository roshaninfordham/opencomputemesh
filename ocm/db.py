import hashlib
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS networks (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, join_code_hash TEXT NOT NULL,
  created_at TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active', policy_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS members (
  id TEXT PRIMARY KEY, network_id TEXT NOT NULL REFERENCES networks(id), nickname TEXT NOT NULL,
  created_at TEXT NOT NULL, session_kind TEXT NOT NULL DEFAULT 'anonymous_demo',
  status TEXT NOT NULL DEFAULT 'active', session_hash TEXT UNIQUE, api_key_hash TEXT UNIQUE
);
CREATE TABLE IF NOT EXISTS enrollment_tokens (
  token_hash TEXT PRIMARY KEY, network_id TEXT NOT NULL, member_id TEXT NOT NULL,
  created_at TEXT NOT NULL, expires_at TEXT NOT NULL, used_at TEXT
);
CREATE TABLE IF NOT EXISTS nodes (
  id TEXT PRIMARY KEY, network_id TEXT NOT NULL, owner_member_id TEXT NOT NULL REFERENCES members(id),
  credential_hash TEXT NOT NULL, display_name TEXT NOT NULL, platform TEXT, architecture TEXT,
  cpu_model TEXT, logical_cpu_count INTEGER, memory_total_bytes INTEGER, memory_available_bytes INTEGER,
  gpu_json TEXT NOT NULL DEFAULT '[]', runtime_name TEXT, runtime_version TEXT,
  models_json TEXT NOT NULL DEFAULT '[]', sharing_enabled INTEGER NOT NULL DEFAULT 1,
  max_concurrent_jobs INTEGER NOT NULL DEFAULT 1, last_heartbeat_at TEXT,
  status TEXT NOT NULL DEFAULT 'PENDING', created_at TEXT NOT NULL, revoked_at TEXT
);
CREATE TABLE IF NOT EXISTS jobs (
  id TEXT PRIMARY KEY, network_id TEXT NOT NULL, requester_member_id TEXT NOT NULL,
  idempotency_key TEXT NOT NULL, kind TEXT NOT NULL DEFAULT 'request',
  selected_node_id TEXT, provider_member_id TEXT, route_reason TEXT,
  model_id TEXT NOT NULL, model_digest TEXT, status TEXT NOT NULL,
  created_at TEXT NOT NULL, queued_at TEXT, dispatched_at TEXT, first_token_at TEXT,
  completed_at TEXT, cancelled_at TEXT,
  input_tokens INTEGER, output_tokens INTEGER, token_count_source TEXT,
  queue_wait_ms INTEGER, end_to_end_ms INTEGER, worker_elapsed_ms INTEGER, runtime_total_ms INTEGER,
  runtime_load_ms INTEGER, prompt_eval_ms INTEGER, generation_ms INTEGER,
  time_to_first_token_ms INTEGER, tokens_per_second REAL, retry_count INTEGER NOT NULL DEFAULT 0,
  failure_code TEXT, tariff_version TEXT, reserved_credits INTEGER NOT NULL DEFAULT 0,
  settled_credits INTEGER, settlement_status TEXT NOT NULL DEFAULT 'none', cancel_effect TEXT,
  UNIQUE (requester_member_id, idempotency_key)
);
CREATE TABLE IF NOT EXISTS job_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL REFERENCES jobs(id),
  event_type TEXT NOT NULL, occurred_at TEXT NOT NULL, duration_ms INTEGER,
  safe_metadata_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS job_events_job ON job_events(job_id);
CREATE TABLE IF NOT EXISTS ledger (
  id INTEGER PRIMARY KEY AUTOINCREMENT, network_id TEXT NOT NULL, member_id TEXT NOT NULL,
  job_id TEXT, entry_type TEXT NOT NULL, amount_micro_occ INTEGER NOT NULL,
  idempotency_key TEXT NOT NULL UNIQUE, tariff_version TEXT, created_at TEXT NOT NULL,
  reversal_of INTEGER REFERENCES ledger(id), metadata_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS ledger_member ON ledger(member_id);
CREATE TRIGGER IF NOT EXISTS ledger_no_update BEFORE UPDATE ON ledger
  BEGIN SELECT RAISE(ABORT, 'ledger is append-only'); END;
CREATE TRIGGER IF NOT EXISTS ledger_no_delete BEFORE DELETE ON ledger
  BEGIN SELECT RAISE(ABORT, 'ledger is append-only'); END;
CREATE TABLE IF NOT EXISTS tariffs (
  version TEXT PRIMARY KEY, network_id TEXT NOT NULL, input_micro_per_1k INTEGER NOT NULL,
  output_micro_per_1k INTEGER NOT NULL, min_charge_micro INTEGER NOT NULL, active_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS pricing_refs (
  id TEXT PRIMARY KEY, local_model_id TEXT NOT NULL, provider TEXT NOT NULL,
  provider_model_id TEXT NOT NULL, input_usd_per_million_tokens TEXT NOT NULL,
  output_usd_per_million_tokens TEXT NOT NULL, source_url TEXT NOT NULL,
  source_checked_at TEXT NOT NULL, effective_at TEXT NOT NULL, currency TEXT NOT NULL DEFAULT 'USD',
  notes TEXT, enabled INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS benchmarks (
  id TEXT PRIMARY KEY, requester_member_id TEXT NOT NULL, local_job_id TEXT, remote_job_id TEXT,
  local_node_id TEXT NOT NULL, remote_node_id TEXT, model_id TEXT NOT NULL, model_digest TEXT,
  sampling_config_hash TEXT NOT NULL, local_metrics_json TEXT, remote_metrics_json TEXT,
  comparison_status TEXT NOT NULL, created_at TEXT NOT NULL
);
"""


# measured_credits: uncapped tariff cost of the reported usage; settlement_capped: 1 if the debit was cut to the hold;
# done_reason: the runtime's reported stop reason ("stop", "length", ...), node-reported.
ADDED_COLUMNS = {"jobs": {"measured_credits": "INTEGER", "settlement_capped": "INTEGER", "done_reason": "TEXT"}}


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def parse_ts(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def new_id() -> str:
    return str(uuid.uuid4())


def sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def connect(path: str) -> sqlite3.Connection:
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    for table, cols in ADDED_COLUMNS.items():  # idempotent upgrade of databases created before the column existed
        have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        for name, decl in cols.items():
            if name not in have:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
    conn.execute("PRAGMA user_version=1")
    return conn


def row(conn: sqlite3.Connection, sql: str, *args) -> dict | None:
    r = conn.execute(sql, args).fetchone()
    return dict(r) if r else None


def rows(conn: sqlite3.Connection, sql: str, *args) -> list[dict]:
    return [dict(r) for r in conn.execute(sql, args).fetchall()]


def insert(conn: sqlite3.Connection, table: str, values: dict) -> None:
    cols = ", ".join(values)
    marks = ", ".join("?" for _ in values)
    conn.execute(f"INSERT INTO {table} ({cols}) VALUES ({marks})", tuple(values.values()))


def update(conn: sqlite3.Connection, table: str, key: str, values: dict) -> None:
    sets = ", ".join(f"{k} = ?" for k in values)
    conn.execute(f"UPDATE {table} SET {sets} WHERE id = ?", (*values.values(), key))


def dumps(value) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)
