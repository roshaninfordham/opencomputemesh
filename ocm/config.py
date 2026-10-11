import os
from dataclasses import dataclass, fields


@dataclass
class Settings:
    """Every field is overridable by env var OCM_<FIELD_NAME_UPPER>."""

    env: str = "development"
    host: str = "127.0.0.1"
    port: int = 8000
    database_path: str = "./data/opencomputemesh.db"
    data_dir: str = "./data"
    admin_token: str = ""
    join_code: str = ""  # empty -> generated once and persisted to data_dir/join_code
    network_name: str = "OpenComputeMesh Demo"
    demo_mode: bool = True
    demo_grant_occ: int = 100
    node_heartbeat_ttl_seconds: int = 60
    node_heartbeat_interval_seconds: int = 20
    node_offline_grace_seconds: int = 10
    enrollment_token_ttl_seconds: int = 600
    max_prompt_chars: int = 12000
    default_max_output_tokens: int = 512
    max_output_tokens: int = 2048
    job_timeout_seconds: int = 180
    queue_timeout_seconds: int = 30
    max_queued_jobs: int = 32
    max_active_jobs_per_member: int = 2
    max_concurrent_jobs_per_node: int = 1
    # Demo policy defaults, not market prices.
    default_input_occ_per_1k: int = 1
    default_output_occ_per_1k: int = 3
    min_job_charge_micro_occ: int = 0
    allowed_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    web_dist: str = "./web/dist"

    @classmethod
    def from_env(cls, **overrides) -> "Settings":
        s = cls(**overrides)
        for f in fields(cls):
            raw = os.environ.get(f"OCM_{f.name.upper()}")
            if raw is None or f.name in overrides:
                continue
            if f.type is bool or f.type == "bool":
                setattr(s, f.name, raw.strip().lower() in ("1", "true", "yes", "on"))
            elif f.type is int or f.type == "int":
                setattr(s, f.name, int(raw))
            else:
                setattr(s, f.name, raw)
        s.validate()
        return s

    @property
    def production(self) -> bool:
        return self.env != "development"

    @property
    def grant_micro(self) -> int:
        return self.demo_grant_occ * 1_000_000 if self.demo_mode else 0

    def validate(self) -> None:
        if self.production and self.admin_token and len(self.admin_token) < 32:
            raise ValueError("OCM_ADMIN_TOKEN must be at least 32 characters outside development")
        if self.max_output_tokens < self.default_max_output_tokens:
            raise ValueError("OCM_MAX_OUTPUT_TOKENS must be >= OCM_DEFAULT_MAX_OUTPUT_TOKENS")
