"""Wire schemas shared by coordinator and node agent (protocol_version 1)."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

PROTOCOL_VERSION = 1
MODEL_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:/\-]{0,127}$"


class ModelInfo(BaseModel):
    model_id: str = Field(pattern=MODEL_ID_PATTERN)
    digest: str | None = Field(default=None, max_length=128)
    runtime: str = Field(default="ollama", max_length=32)
    family: str | None = Field(default=None, max_length=64)
    parameter_size: str | None = Field(default=None, max_length=32)
    quantization: str | None = Field(default=None, max_length=32)
    size_bytes: int | None = None


class GpuInfo(BaseModel):
    name: str = Field(max_length=128)
    memory_total_bytes: int | None = None
    source: str = Field(max_length=64)  # where this came from, e.g. "nvidia-smi", "system_profiler"


class Capabilities(BaseModel):
    display_name: str = Field(min_length=1, max_length=48)
    platform: str | None = Field(default=None, max_length=64)
    architecture: str | None = Field(default=None, max_length=32)
    cpu_model: str | None = Field(default=None, max_length=128)
    logical_cpu_count: int | None = None
    memory_total_bytes: int | None = None
    memory_available_bytes: int | None = None
    gpus: list[GpuInfo] = Field(default_factory=list, max_length=16)
    runtime_name: str = Field(default="ollama", max_length=32)
    runtime_version: str | None = Field(default=None, max_length=32)
    runtime_healthy: bool = False
    models: list[ModelInfo] = Field(default_factory=list, max_length=200)
    max_concurrent_jobs: int = Field(default=1, ge=1, le=8)


class NodeHello(BaseModel):
    type: Literal["node.enroll", "node.auth"]
    protocol_version: int = PROTOCOL_VERSION
    enrollment_token: str | None = Field(default=None, max_length=128)
    node_id: str | None = Field(default=None, max_length=64)
    credential: str | None = Field(default=None, max_length=128)
    capabilities: Capabilities


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str = Field(max_length=100_000)


class GenerationParams(BaseModel):
    model: str = Field(pattern=MODEL_ID_PATTERN)
    messages: list[ChatMessage] = Field(min_length=1, max_length=50)
    max_tokens: int | None = Field(default=None, ge=1)
    temperature: float | None = Field(default=None, ge=0, le=2)
    top_p: float | None = Field(default=None, gt=0, le=1)

    @field_validator("messages")
    @classmethod
    def last_is_user(cls, v):
        if v[-1].role != "user":
            raise ValueError("last message must be from the user")
        return v


class RequestStart(GenerationParams):
    type: Literal["request.start"]
    idempotency_key: UUID
    node_id: str | None = Field(default=None, max_length=64)  # optional explicit worker


class BenchmarkStart(GenerationParams):
    type: Literal["benchmark.start"]
    idempotency_key: UUID


class RequestRef(BaseModel):
    type: Literal["request.cancel", "request.status"]
    request_id: str = Field(max_length=64)
